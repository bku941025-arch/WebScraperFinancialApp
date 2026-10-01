import asyncio
import json
from types import SimpleNamespace as NS

import anthropic
import httpx
import pytest
from fastapi.testclient import TestClient

from app import chat, chat_tools, db, demo, quotes


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(quotes, "DEMO", True)
    demo.main()


def run(coro):
    return asyncio.run(coro)


def collect(agen):
    async def go():
        return [json.loads(x[6:]) async for x in agen]
    return asyncio.run(go())


# ---------- scripted fake of the SDK stream (records every request it receives)
class FakeClient:
    def __init__(self, turns, raises=None):
        self.turns, self.calls, self.raises = list(turns), [], raises
        self.beta = NS(messages=NS(stream=self._stream))

    def _stream(self, **kw):
        if self.raises:
            raise self.raises
        self.calls.append(kw)
        return _FakeStream(self.turns.pop(0))


class _FakeStream:
    def __init__(self, turn):
        self.turn = turn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def __aiter__(self):
        for b in self.turn["content"]:
            if b.type in ("tool_use", "server_tool_use", "web_search_tool_result"):
                yield NS(type="content_block_start", content_block=b)
            elif b.type == "text":
                yield NS(type="text", text=b.text)

    async def get_final_message(self):
        t = self.turn
        return NS(content=t["content"], stop_reason=t["stop"], usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=3))


def text(s):
    return NS(type="text", text=s)


def tool(name, args, id="tu1"):
    return NS(type="tool_use", id=id, name=name, input=args)


def test_technicals_math():
    up = [[i, 0, c + 1, c - 1, c, 100] for i, c in enumerate(range(100, 340))]       # steady uptrend, 240 bars
    t = chat_tools.technicals(up)
    assert t["ret_1m_pct"] > 0 and t["vs_sma50_pct"] > 0 and t["vs_sma200_pct"] > 0 and t["rsi14"] > 95
    assert t["from_52w_high_pct"] <= 0 and chat_tools.technicals([[1, 1, 1, 1, 1, 1]]) == {}
    flat = [[i, 1, 1, 1, 1, 1] for i in range(40)]
    assert chat_tools.technicals(flat)["rsi14"] == 100.0 and chat_tools.technicals(flat)["ret_3m_pct"] is None


def test_tools_return_compact_grounded_data(seeded):
    trending = json.loads(run(chat_tools.run_tool("get_trending_companies", {"limit": 3, "hours": "24"}))[0])
    assert len(trending) == 3 and {"ticker", "trend_score", "tone", "headlines"} <= trending[0].keys()
    snap = json.loads(run(chat_tools.run_tool("get_stock_snapshot", {"symbol": "nvda"}))[0])
    assert snap["symbol"] == "NVDA" and snap["quote"]["price"] > 0 and snap["technicals"]["rsi14"] is not None
    assert snap["price_source"] == "demo"
    assert json.loads(run(chat_tools.run_tool("get_company_news", {"ticker": "NVDA", "limit": 3}))[0])["articles"]
    assert json.loads(run(chat_tools.run_tool("search_news", {"query": "earnings", "kind": "bogus"}))[0])["query"] == "earnings"
    assert json.loads(run(chat_tools.run_tool("get_upcoming_earnings", {"days": 30}))[0])
    assert json.loads(run(chat_tools.run_tool("find_ticker", {"query": "nvid"}))[0])[0]["ticker"] == "NVDA"
    assert json.loads(run(chat_tools.run_tool("get_market_overview", {}))[0])["regions"]


def test_tool_errors_never_raise(seeded):
    for name, args in [("nope", {}), ("get_stock_snapshot", {"symbol": "bad sym!"}), ("get_stock_snapshot", {"wrong": 1}),
                       ("get_trending_companies", "not-a-dict"), ("search_news", {"query": ""}), ("get_stock_snapshot", {"symbol": "OPENAI"})]:
        out, _ = run(chat_tools.run_tool(name, args))
        json.loads(out)  # always valid JSON
    assert run(chat_tools.run_tool("nope", {}))[1] is True
    assert run(chat_tools.run_tool("get_trending_companies", {"limit": 10**9, "hours": "x"}))[1] is False  # inputs clamped


def test_loop_runs_tools_then_answers(seeded):
    fc = FakeClient([dict(content=[text("Checking. "), tool("get_trending_companies", {"limit": 2})], stop="tool_use"),
                     dict(content=[text("$NVDA leads.")], stop="end_turn")])
    ev = collect(chat.stream_chat([{"role": "user", "content": "what is bullish?"}], fc))
    kinds = [e["type"] for e in ev]
    assert kinds == ["delta", "tool_start", "tool_done", "delta", "done"]
    assert ev[1]["label"] == "Checking trending companies" and ev[2]["ok"] is True
    assert ev[-1]["usage"] == dict(input=20, output=10, cache_read=6, rounds=2)
    # second request carried the assistant tool_use turn + a matching tool_result with real data
    msgs = fc.calls[1]["messages"]
    assert msgs[-2]["role"] == "assistant" and msgs[-1]["content"][0]["tool_use_id"] == "tu1"
    assert json.loads(msgs[-1]["content"][0]["content"])[0]["ticker"]


def test_request_shape_for_opus_and_other_models(monkeypatch):
    kw = chat._request_kwargs([{"role": "user", "content": "hi"}])
    assert kw["model"] == "claude-opus-5-5" and kw["output_config"] == {"effort": "medium"}
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert "thinking" not in kw and "tool_choice" not in kw and "temperature" not in kw     # not allowed on this model
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"} and "Today is" in kw["system"][1]["text"]
    assert {t["name"] for t in kw["tools"]} == set(chat_tools.IMPLS)
    monkeypatch.setattr(chat, "MODEL", "claude-haiku-4-5")
    old = chat._request_kwargs([])
    assert not {"output_config", "betas", "fallbacks"} & old.keys()                          # unsupported on older models


def test_refusal_max_tokens_and_loop_cap(seeded):
    ev = collect(chat.stream_chat([{"role": "user", "content": "x"}], FakeClient([dict(content=[], stop="refusal")])))
    assert ev[-1]["type"] == "error" and "can’t help" in ev[-1]["message"]
    ev = collect(chat.stream_chat([{"role": "user", "content": "x"}], FakeClient([dict(content=[text("part")], stop="max_tokens")])))
    assert [e["type"] for e in ev] == ["delta", "notice", "done"]
    loop = FakeClient([dict(content=[tool("find_ticker", {"query": "a"}, f"t{i}")], stop="tool_use") for i in range(20)])
    ev = collect(chat.stream_chat([{"role": "user", "content": "x"}], loop))
    assert len(loop.calls) == chat.MAX_ITERATIONS and ev[-2]["type"] == "notice" and ev[-1]["type"] == "done"


def test_fallback_echo_drops_pre_boundary_blocks():
    blocks = [NS(type="thinking"), text("kept partial"), tool("a", {}, "x"), NS(type="fallback"), NS(type="thinking"), tool("b", {}, "y")]
    out = chat._echo(blocks)
    assert [b.type for b in out] == ["text", "thinking", "tool_use"] and out[2].id == "y"
    assert chat._echo([text("a"), tool("a", {})]) and len(chat._echo([text("a"), tool("a", {})])) == 2


@pytest.mark.parametrize("exc,needle", [
    (anthropic.AuthenticationError("x", response=httpx.Response(401, request=httpx.Request("POST", "http://x")), body=None), "key"),
    (anthropic.RateLimitError("x", response=httpx.Response(429, request=httpx.Request("POST", "http://x")), body=None), "rate-limited"),
    (anthropic.APIConnectionError(request=httpx.Request("POST", "http://x")), "reach"),
    (RuntimeError("boom"), "went wrong"),
])
def test_error_mapping(exc, needle):
    ev = collect(chat.stream_chat([{"role": "user", "content": "x"}], FakeClient([], raises=exc)))
    assert ev[-1]["type"] == "error" and needle in ev[-1]["message"] and "boom" not in ev[-1]["message"]


def test_history_cleaning_and_rate_limit():
    h = [{"role": "assistant", "content": "stray"}, {"role": "user", "content": " hi "}, {"role": "assistant", "content": "  "},
         {"role": "user", "content": "q"}]
    assert chat.clean_history(h) == [{"role": "user", "content": "hi"}, {"role": "user", "content": "q"}]
    assert len(chat.clean_history([{"role": "user", "content": str(i)} for i in range(100)])) == chat.MAX_HISTORY
    assert collect(chat.stream_chat([{"role": "assistant", "content": "x"}], FakeClient([])))[0]["type"] == "error"
    rl = chat.RateLimiter(2, 60)
    assert [rl.allow("a", 100), rl.allow("a", 101), rl.allow("a", 102), rl.allow("b", 102), rl.allow("a", 161)] == [True, True, False, True, True]


def test_api_endpoint_demo_mode_end_to_end(seeded, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "FINTREND_CHAT_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    from app.main import app
    c = TestClient(app)
    assert c.get("/chat").status_code == 200
    assert c.get("/api/chat/status").json() == dict(configured=True, demo=True, model="demo (scripted)", web_search=False)
    r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "What is trending and bullish right now?"}]})
    assert r.headers["content-type"].startswith("text/event-stream")
    ev = [json.loads(l[6:]) for l in r.text.split("\n\n") if l.startswith("data: ")]
    body = "".join(e.get("text", "") for e in ev if e["type"] == "delta")
    assert [e["type"] for e in ev if e["type"] != "delta"] == ["tool_start", "tool_done", "done"]
    assert "Demo mode" in body and "| Ticker |" in body and "$" in body
    assert c.post("/api/chat", json={"messages": [{"role": "robot", "content": "x"}]}).status_code == 422
    assert c.post("/api/chat", json={"messages": []}).status_code == 422
    chat.limiter.hits.clear()


def test_endpoint_requires_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(quotes, "DEMO", False)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "FINTREND_CHAT_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    from app.main import app
    c = TestClient(app)
    assert c.get("/api/chat/status").json()["configured"] is False
    assert c.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 503
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert c.get("/api/chat/status").json() == dict(configured=True, demo=False, model="claude-opus-5-5", web_search=False)


# ---------- web search (opt-in server tool)
def srv(id="srv1", q="nvidia news"):
    return NS(type="server_tool_use", id=id, name="web_search", input={"query": q})


def srv_result(id="srv1", urls=("https://www.reuters.com/a", "https://example.org/b")):
    return NS(type="web_search_tool_result", tool_use_id=id, content=[NS(type="web_search_result", url=u, title="T " + u) for u in urls])


def test_web_tool_request_shape(monkeypatch):
    base = [{"role": "user", "content": "x"}]
    off = chat._request_kwargs(base)
    assert all(t.get("name") != "web_search" for t in off["tools"]) and "web_search" not in off["system"][1]["text"]
    on = chat._request_kwargs(base, web=True)
    ws = [t for t in on["tools"] if t.get("name") == "web_search"]
    assert ws == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 4}]       # dynamic-filtering version
    assert len(on["tools"]) == len(chat_tools.TOOLS) + 1 and "code_execution" not in str(on["tools"])
    assert "web_search" in on["system"][1]["text"] and on["system"][0] == off["system"][0]    # cached prefix block unchanged
    monkeypatch.setattr(chat, "MODEL", "claude-haiku-4-5")
    assert chat._web_tool()["type"] == "web_search_20250305"
    monkeypatch.setattr(chat, "MODEL", "claude-sonnet-5-5")
    assert chat._web_tool()["type"] == "web_search_20260209"


def test_web_search_events_and_sources(seeded):
    cited = NS(type="text", text="Reuters says so.", citations=[NS(url="https://www.reuters.com/a", title="Reuters story")])
    fc = FakeClient([dict(content=[srv(), srv_result(), cited], stop="end_turn")])
    ev = collect(chat.stream_chat([{"role": "user", "content": "latest on nvidia?"}], fc, web=True))
    assert [e["type"] for e in ev] == ["tool_start", "tool_done", "delta", "sources", "done"]
    assert ev[0]["label"] == "Searching the web" and ev[1] == dict(type="tool_done", id="srv1", name="web_search", ok=True)
    assert ev[3]["items"] == [dict(title="Reuters story", url="https://www.reuters.com/a", site="reuters.com", cited=True)]
    assert any(t.get("name") == "web_search" for t in fc.calls[0]["tools"])
    # no citations -> fall back to the results that were searched, flagged as uncited; unsafe URLs dropped
    fc = FakeClient([dict(content=[srv(), srv_result(urls=("https://a.com/x", "javascript:alert(1)")), text("ok")], stop="end_turn")])
    items = [e for e in collect(chat.stream_chat([{"role": "user", "content": "q"}], fc, web=True)) if e["type"] == "sources"][0]["items"]
    assert [i["url"] for i in items] == ["https://a.com/x"] and items[0]["cited"] is False


def test_web_search_error_result_is_not_ok(seeded):
    err = NS(type="web_search_tool_result", tool_use_id="srv1", content=NS(type="web_search_tool_result_error", error_code="max_uses_exceeded"))
    ev = collect(chat.stream_chat([{"role": "user", "content": "q"}], FakeClient([dict(content=[srv(), err, text("fallback answer")], stop="end_turn")]), web=True))
    assert ev[1]["ok"] is False and not any(e["type"] == "sources" for e in ev) and ev[-1]["type"] == "done"


def test_pause_turn_resumes_without_extra_user_message(seeded):
    paused = [srv(), srv_result()]
    fc = FakeClient([dict(content=paused, stop="pause_turn"), dict(content=[text("done")], stop="end_turn")])
    ev = collect(chat.stream_chat([{"role": "user", "content": "q"}], fc, web=True))
    assert ev[-1]["type"] == "done" and any(e["type"] == "delta" for e in ev)
    msgs = fc.calls[1]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[-1]["content"] == paused   # server blocks echoed untouched


def test_web_mixes_with_client_tools(seeded):
    fc = FakeClient([dict(content=[srv(), srv_result(), tool("find_ticker", {"query": "nvid"}, "c1")], stop="tool_use"),
                     dict(content=[text("answer")], stop="end_turn")])
    ev = collect(chat.stream_chat([{"role": "user", "content": "q"}], fc, web=True))
    assert [e["type"] for e in ev if e["type"].startswith("tool")] == ["tool_start", "tool_done", "tool_start", "tool_done"]
    last = fc.calls[1]["messages"]
    assert [b.type for b in last[-2]["content"]] == ["server_tool_use", "web_search_tool_result", "tool_use"]
    assert last[-1]["content"][0]["tool_use_id"] == "c1"


def test_api_web_flag_needs_operator_opt_in(seeded, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "FINTREND_CHAT_ENABLED", "FINTREND_WEB_SEARCH"):
        monkeypatch.delenv(k, raising=False)
    from app.main import app
    c = TestClient(app)
    seen = []
    orig = chat.stream_chat
    monkeypatch.setattr("app.main.chat.stream_chat", lambda h, client=None, web=False: (seen.append(web), orig(h, client, web))[1])
    body = {"messages": [{"role": "user", "content": "latest web news on markets"}], "web": True}
    c.post("/api/chat", json=body)
    assert seen == [False]                                           # server hasn't enabled it -> ignored
    monkeypatch.setenv("FINTREND_WEB_SEARCH", "1")
    assert c.get("/api/chat/status").json()["web_search"] is True
    r = c.post("/api/chat", json=body)
    ev = [json.loads(l[6:]) for l in r.text.split("\n\n") if l.startswith("data: ")]
    assert seen == [False, True] and "sources" in [e["type"] for e in ev]          # demo client simulated a (fake) search
    assert "placeholders" in "".join(e.get("text", "") for e in ev)
    chat.limiter.hits.clear()
