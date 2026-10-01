import asyncio
import json

import pytest

from app import chat, chat_rules as R, db, demo, quotes


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(quotes, "DEMO", True)
    demo.main()


def ask(*msgs):
    """msgs: alternating user/assistant strings ending with the user's question -> (text, events)."""
    hist = [{"role": "user" if i % 2 == 0 else "assistant", "content": m} for i, m in enumerate(msgs)]

    async def go():
        return [json.loads(x[6:]) async for x in R.stream(hist)]
    ev = asyncio.run(go())
    return "".join(e.get("text", "") for e in ev if e["type"] == "delta"), ev


def tickers(q):
    with db.session() as c:
        return R.extract_tickers(q, c)


def test_ticker_extraction(seeded):
    assert tickers("Compare AAPL and Microsoft") == ["AAPL", "MSFT"]               # symbol + curated name, in order
    assert tickers("how is $nvda doing") == ["NVDA"]                                # cashtag, any case
    assert tickers("thoughts on $OPENAI?") == ["OPENAI"]                            # six-letter cashtag
    assert tickers("thoughts on apple?") == ["AAPL"]                                # lower-case curated alias
    assert tickers("Tell me about Palantir") == ["PLTR"]                            # Capitalised SEC-name first word
    assert tickers("when does 7203.T report") == ["7203.T"] and tickers("0700.HK news") == ["0700.HK"]
    assert tickers("WHAT IS THE RSI NOW AI CEO") == []                              # shouting + common acronyms are not tickers
    assert tickers("what is trending this week in the market") == []
    assert tickers("Which Global Financial group is best") == []                    # generic first-words of company names


@pytest.mark.parametrize("q,tk,intent", [
    ("hi", [], "help"), ("What can you do?", [], "help"),
    ("what is RSI?", [], "glossary"), ("explain the 200-day moving average", [], "glossary"), ("What's an 8-K?", [], "glossary"),
    ("what is NVDA's RSI?", ["NVDA"], "read"),
    ("Compare AAPL and MSFT", ["AAPL", "MSFT"], "compare"), ("AAPL vs MSFT", ["AAPL", "MSFT"], "compare"),
    ("Who reports earnings this week?", [], "earnings"), ("when does Tesla report", ["TSLA"], "earnings"),
    ("Any 8-K filings today?", [], "filings"), ("latest news on Nvidia", ["NVDA"], "news"),
    ("Should I buy TSLA?", ["TSLA"], "advice"), ("what stock should I buy", [], "advice"),
    ("give me a full read on $NVDA", ["NVDA"], "read"), ("what looks bullish right now", [], "trending"),
    ("anything bearish in Asia?", [], "trending"), ("how are markets doing around the world", [], "overview"),
    ("what is happening with oil prices", [], "news"), ("random gibberish xyzzy", [], "fallback"),
])
def test_intents(seeded, q, tk, intent):
    assert R.classify(q, tk) == intent


def test_parsers():
    assert R.parse_window("what's hot this week") == (168, "the past week") and R.parse_window("right now")[0] == 24
    assert R.parse_region("bullish in Asia") == "Asia" and R.parse_region("US stocks") == "US" and R.parse_region("tell us more") is None
    assert R.parse_region("wall street movers") == "US" and R.parse_region("India or UK") is None and R.parse_region("Asia and the US") is None
    assert [R.earnings_days(x) for x in ("today", "tomorrow", "this week", "next week", "this month")] == [1, 2, 7, 14, 30]


def test_scorecard_math():
    snap = dict(quote=dict(price=90, year_high=100), technicals=dict(vs_sma200_pct=4.0, vs_sma50_pct=-2.0, ret_3m_pct=12.0, rsi14=75))
    rows, pos, neg = R.scorecard(snap, dict(mentions=9, tone=0.4))
    assert dict((r[0], r[1]) for r in rows) == {"Price vs 200-day average": 1, "Price vs 50-day average": -1, "3-month return": 1,
                                                 "RSI(14)": -1, "Distance from 52-week high": 1, "News tone": 1}
    assert (pos, neg) == (4, 2)
    assert R.scorecard({}, {}) == ([], 0, 0)


def test_glossary_answers(seeded):
    t, ev = ask("what is RSI?")
    assert "Relative Strength Index" in t and "overbought" in t and [e["type"] for e in ev if e["type"] != "delta"] == ["done"]
    assert "2.02" in ask("What's an 8-K?")[0] and "Item" in ask("explain 8-K filings")[0]
    assert "50-day" in ask("explain the 200-day moving average")[0]
    assert "Trend score" in ask("what does the trend score mean?")[0]


def test_trending_modes(seeded):
    t, ev = ask("What looks bullish right now?")
    assert "| Ticker |" in t and "$NVDA" in t and "screen, not a signal" in t and "not personalised advice" in t
    assert [e["type"] for e in ev if e["type"] != "delta"] == ["tool_start", "tool_done", "done"]
    t, _ = ask("anything bearish this week?")
    assert "negative" in t or "lowest tone" in t.lower()                      # honest when nothing is clearly negative
    assert "**Asia**" in ask("what's trending in Asia?")[0]


def test_company_read_and_scorecard(seeded):
    t, ev = ask("Give me a full read on $NVDA")
    assert t.startswith("### $NVDA — Nvidia") and "RSI(14)" in t and "In the news" in t and "Next earnings" in t and "synthetic" in t
    assert [e["name"] for e in ev if e["type"] == "tool_start"] == ["get_stock_snapshot", "get_company_news"]
    t, _ = ask("Should I buy NVDA?")
    assert "Signal scorecard" in t and "What would change this" in t and "not personalised advice" in t and ("🟢" in t or "🔴" in t)


def test_compare_earnings_filings_news_overview(seeded):
    t, _ = ask("Compare AAPL and MSFT")
    assert "**$AAPL**" in t and "**$MSFT**" in t and "vs 200-day avg" in t and "Where each leads" in t
    assert "Earnings in the next 7 days" in ask("who reports earnings this week?")[0]
    assert "reports on **20" in ask("when does Tesla report?")[0]
    assert "8-K" in ask("any notable 8-K filings today?")[0]
    assert "Latest on **$NVDA**" in ask("latest news on nvidia")[0]
    assert "Market coverage" in ask("how are markets doing around the world?")[0]
    t, _ = ask("hello")
    assert "built-in assistant" in t and "Compare" in t


def test_follow_up_uses_previous_ticker(seeded):
    t, _ = ask("Give me a read on $AMZN", "### $AMZN — Amazon", "what about its earnings?")
    assert t.startswith("**$AMZN**") and "reports on" in t
    t, _ = ask("what's trending?", "table of $NVDA and $TSLA", "what about earnings?")
    assert "Earnings in the next" in t or "reports on" in t                    # no pronoun -> generic list, no guessing


def test_unknown_and_empty_cases(seeded, tmp_path, monkeypatch):
    t, _ = ask("tell me about pizza recipes")
    assert "built-in assistant" in t and "didn’t recognise" in t
    t, _ = ask("give me a read on $ZZZZ")
    assert "ZZZZ" in t                                                          # explains instead of crashing
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "empty.db")                   # brand-new install: no data at all
    for q in ("what's trending?", "who reports earnings", "any 8-K filings?", "market overview", "latest news on Nvidia"):
        t, ev = ask(q)
        assert t and ev[-1]["type"] == "done"
    assert "don’t have data" in ask("what's trending?")[0]


def test_tool_failure_does_not_break_answer(seeded, monkeypatch):
    async def boom(**kw):
        raise RuntimeError("db down")
    monkeypatch.setitem(__import__("app.chat_tools", fromlist=["IMPLS"]).IMPLS, "get_trending_companies", boom)
    t, ev = ask("what's trending?")
    assert "don’t have data" in t and [e["ok"] for e in ev if e["type"] == "tool_done"] == [False] and ev[-1]["type"] == "done"


def test_markdown_safety_in_links():
    assert R._link("a [b] c", "https://x.com/p") == "[a (b) c](https://x.com/p)"
    assert R._link("t", "javascript:alert(1)") == "t" and R._link("t", "https://x.com/a)b") == "t" and R._link("t", None) == "t"


def test_dispatch_through_chat_stream(seeded, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("FINTREND_CHAT", raising=False)

    async def go():
        return [json.loads(x[6:]) async for x in chat.stream_chat([{"role": "user", "content": "what is RSI?"}])]
    ev = asyncio.run(go())
    assert ev[0]["type"] == "delta" and ev[-1]["type"] == "done"


# ---------- the question bank (dashboard menu) must stay in sync with the built-in assistant
def _bank_cases():
    from app.question_bank import BANK
    return [(c["id"], qq) for c in BANK for qq in c["questions"]]


def _fill(text):
    return text.replace("{A}", "$NVDA").replace("{B}", "$AAPL")


@pytest.mark.parametrize("cat,qq", _bank_cases(), ids=lambda v: v if isinstance(v, str) else v["text"][:40])
def test_every_bank_question_is_understood(seeded, cat, qq):
    text = _fill(qq["text"])
    assert R.classify(text, tickers(text)) == qq["expect"], text
    answer, ev = ask(text)
    assert len(answer) > 60 and ev[-1]["type"] == "done" and not any(e["type"] == "error" for e in ev)
    assert "didn’t recognise" not in answer and "don’t have data" not in answer


def test_bank_shape_and_endpoint(seeded):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.question_bank import BANK
    c = TestClient(app)
    data = c.get("/api/chat/questions").json()
    assert [x["id"] for x in data] == ["pulse", "company", "calendar", "learn"]
    allq = [qq for x in data for qq in x["questions"]]
    assert len(allq) == len({qq["text"] for qq in allq}) >= 20                       # no duplicates, a decent menu
    assert all(qq["slots"] == [s for s in "AB" if "{" + s + "}" in qq["text"]] for qq in allq)
    assert sum(qq["featured"] for qq in allq) == 3 and not any(qq["slots"] for qq in allq if qq["featured"])
    assert max(len(qq["text"]) for qq in allq) < 80 and BANK[1]["questions"][-1]["slots"] == ["A", "B"]
    assert all(x["blurb"] and x["icon"] and x["title"] for x in data)                   # topic cards need a name, icon and blurb
    slotted = [qq for qq in allq if qq["slots"]]
    assert slotted and all(qq["label"] and qq["icon"] and qq["hint"] for qq in slotted)  # company action tiles need labels
    assert len({qq["label"] for qq in slotted}) == len(slotted)
    r = c.get("/chat", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/#ask"                 # old page moved to the dashboard
