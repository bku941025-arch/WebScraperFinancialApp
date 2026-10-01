"""Built-in assistant — no AI model involved. Free, instant and offline.

It understands a fixed set of question types (what's trending/bullish/bearish, a read on a company, comparisons,
earnings, filings, news, market overview, "should I buy X" as a signal scorecard, and a glossary), calls the same
tools the AI providers use, and writes the answer from templates. Unrecognised questions fall back to a keyword
search of the collected news. Everything it says comes from tool data or the fixed glossary below.
"""
import asyncio
import re

from . import chat_tools, db, entities
from .chat_common import sse, typewriter

# ------------------------------------------------------------------ parsing
STOP_CAPS = {"AI", "CEO", "CFO", "ETF", "IPO", "USD", "GDP", "FED", "SEC", "EPS", "RSI", "PE", "SMA", "EMA", "US", "UK", "EU", "USA",
             "AND", "THE", "FOR", "NOT", "ALL", "ANY", "NEW", "TOP", "BUY", "SELL", "HOLD", "NOW", "YTD", "API", "FAQ", "OK", "CPI",
             "PPI", "ATH", "ROI", "ROE", "IT", "ARE", "IS", "WHAT", "WHO", "HOW", "WHY", "TELL", "SHOW", "GIVE", "BMO", "AMC", "OR", "VS"}
COMMON = set("""what which compare explain should today tomorrow this next earnings trending bullish bearish market markets stocks stock news
latest about from with when where does give tell show list best worst top more less week month year please thanks hello
global first american united national general international capital financial bank energy group trust health technology
technologies systems holdings partners resources fund income growth value digital blue green power real star west north
south east pacific atlantic bearish bullish right there their these those some many most other after before during
since while would could should might filings filing report reports reporting results price prices""".split())
CASH_RE = re.compile(r"\$([A-Za-z]{1,6}(?:[.-][A-Za-z]{1,2})?)\b")
INTL_RE = re.compile(r"\b(\d{4,6}\.[A-Z]{1,2}|[A-Z]{2,6}\.[A-Z]{1,2})\b")
UPPER_RE = re.compile(r"\b[A-Z]{2,5}(?:-[A-Z])?\b")
CAP_RE = re.compile(r"\b[A-Z][a-z]{3,}\b")
REGIONS = [(None, "US"), (r"\buk\b|britain|london", "UK"), (r"europe|\beu\b|euro zone|eurozone", "EU"),
           (r"asia|japan|china|hong kong|korea", "Asia"), (r"india", "India"), (r"australia", "AU"), (r"global|world", "Global")]


def extract_tickers(text: str, conn) -> list[str]:
    """Tickers mentioned, in order: $cashtags, curated company names, international symbols, verified UPPERCASE
    tickers, and Capitalised words matching the first word of an SEC-listed company name."""
    hits: list[tuple[int, str]] = [(m.start(), m.group(1).upper()) for m in CASH_RE.finditer(text)]
    for ticker, pat in entities._PATTERNS:
        m = pat.search(text)
        if m:
            hits.append((m.start(), ticker))
    hits += [(m.start(), m.group(1)) for m in INTL_RE.finditer(text)]
    for m in UPPER_RE.finditer(text):
        w = m.group(0)
        if w not in STOP_CAPS and (w in entities.COMPANIES or conn.execute("SELECT 1 FROM companies WHERE ticker = ?", (w,)).fetchone()):
            hits.append((m.start(), w))
    for m in CAP_RE.finditer(text):
        w = m.group(0).lower()
        if w in COMMON:
            continue
        row = conn.execute("SELECT ticker FROM companies WHERE lower(name) = ? OR lower(name) LIKE ? ORDER BY rank LIMIT 1", (w, w + " %")).fetchone()
        if row:
            hits.append((m.start(), row["ticker"]))
    out: list[str] = []
    for _, t in sorted(hits):
        if t not in out:
            out.append(t)
    return out[:4]


def parse_window(q: str) -> tuple[int, str]:
    q = q.lower()
    if re.search(r"last (hour|few hours)|past hour|this hour", q):
        return 6, "the last few hours"
    if re.search(r"this week|past week|last week|7 days|week", q):
        return 168, "the past week"
    if re.search(r"3 days|three days|few days|past days", q):
        return 72, "the last 3 days"
    return 24, "the last 24 hours"


def parse_region(q: str) -> str | None:
    """The one region the question names; None if it names none or several (then show all regions)."""
    ql = q.lower()
    found = {region for pat, region in REGIONS[1:] if re.search(pat, ql)}
    if re.search(r"\bUSA?\b|U\.S\.", q) or re.search(r"america|wall street", ql):
        found.add("US")
    return found.pop() if len(found) == 1 else None


def earnings_days(q: str) -> int:
    q = q.lower()
    return 1 if "today" in q or "tonight" in q else 2 if "tomorrow" in q else 14 if "next week" in q or "two weeks" in q else 30 if "month" in q else 7


# ------------------------------------------------------------------ formatting
def _p(v, d=1, plus=True):
    return "n/a" if v is None else f"{v:+.{d}f}%" if plus else f"{v:.{d}f}%"


def _n(v, d=2):
    return "n/a" if v is None else f"{v:,.{d}f}"


def _age(h) -> str:
    return "just now" if h < 1 else f"{int(h)}h ago" if h < 48 else f"{int(h / 24)}d ago"


def _link(title: str, url: str | None) -> str:
    t = title.replace("[", "(").replace("]", ")")
    return f"[{t}]({url})" if url and url.startswith(("http://", "https://")) and ")" not in url else t


def _headlines(items: list[dict], n: int = 3) -> str:
    return "\n".join(f"- {_link(h['title'], h.get('url'))} — *{h['source']}*, {_age(h['age_hours'])}" for h in items[:n])


def _tone(t: float) -> str:
    return "leaning positive" if t > .15 else "leaning negative" if t < -.15 else "mixed / neutral"


def _bad(d) -> bool:
    return not d or (isinstance(d, dict) and d.get("error"))


NO_DATA = ("I don’t have data for that yet. The feeds update at the scheduled scrapes (see the **Status** page, where you can also run one now).")
DISCLAIMER = "*This is a summary of data, not personalised advice — it can’t know your goals, time horizon or risk tolerance.*"

# ------------------------------------------------------------------ glossary
GLOSSARY: list[tuple[str, list[str], str]] = [
    ("RSI (Relative Strength Index)", ["rsi", "relative strength index"],
     "A 0–100 momentum gauge that compares recent average gains to average losses, usually over 14 periods. Above ~70 is often called *overbought* "
     "and below ~30 *oversold*, but in strong trends it can stay extreme for a long time — it flags stretched moves, not turning points."),
    ("Moving averages (50-day / 200-day)", ["moving average", "moving averages", "sma", "50-day", "200-day", "50 day", "200 day", "golden cross", "death cross"],
     "The average closing price over the last N days. The **50-day** reflects the medium-term trend and the **200-day** the long-term one. Price above both "
     "is generally read as an uptrend. A *golden cross* is the 50-day crossing above the 200-day; a *death cross* is the reverse. They lag the price, so they confirm trends rather than predict them."),
    ("P/E ratio", ["p/e", "pe ratio", "price to earnings", "price-to-earnings", "p/e ratio"],
     "Share price divided by earnings per share. A high P/E means investors pay a lot per dollar of profit, usually because they expect growth; a low one can mean "
     "cheapness or doubt. It only compares well within a sector. *This app doesn’t currently show P/E — it has no fundamentals feed.*"),
    ("EPS (earnings per share)", ["eps", "earnings per share"],
     "A company’s profit attributable to common shareholders divided by its share count. On the earnings calendar, **EPS est.** is the analyst consensus; "
     "whether the real figure beats or misses it — and the company’s guidance — typically drives the price reaction."),
    ("Market capitalisation", ["market cap", "market capitalization", "market capitalisation"],
     "Share price × shares outstanding: the market’s value of the whole company. Roughly: mega-cap above $200B, large-cap $10–200B, mid-cap $2–10B, small-cap below that (cut-offs vary by source)."),
    ("Volatility", ["volatility", "volatile"],
     "How much a price moves around. The app reports **annualised volatility** — the standard deviation of daily returns over about three months, scaled to a year. "
     "Higher means bigger swings in both directions."),
    ("Momentum", ["momentum"],
     "In markets, the tendency for recent winners to keep outperforming over short horizons. In this app, a company’s **momentum** is simply its news mentions in the last "
     "6 hours minus the 6 hours before — a measure of accelerating coverage, not of price."),
    ("8-K filing", ["8-k", "8k", "form 8-k", "current report"],
     "The SEC “current report” US public companies file within about four business days of a major event. The **Item** code says what happened: "
     "**2.02** results of operations (earnings), **5.02** executive or director changes, **1.01** a material agreement, **2.01** a completed acquisition or disposal, "
     "**7.01** Reg FD disclosure, **8.01** other events, **1.03** bankruptcy, **3.01** delisting notice, **4.02** financials should no longer be relied on."),
    ("Earnings report", ["earnings report", "earnings call", "bmo", "amc", "before open", "after close", "guidance"],
     "A company’s quarterly results. **BMO** = before the market opens, **AMC** = after it closes. Beyond the numbers, **guidance** — management’s forecast for coming quarters — "
     "often moves the stock more than the quarter itself."),
    ("Bull and bear markets", ["bull market", "bear market", "bull vs bear", "bulls and bears"],
     "By convention, a **bear market** is a fall of 20% or more from a recent peak and a **bull market** a rise of 20% or more from a low. A *bullish* view expects prices to rise; *bearish* expects them to fall."),
    ("Dividends and yield", ["dividend", "dividends", "dividend yield", "yield", "ex-dividend"],
     "A dividend is a share of profits paid to shareholders. **Yield** is the annual dividend divided by the share price. You must own the stock before the **ex-dividend date** to receive the next payment. "
     "An unusually high yield can signal a falling share price or an unsustainable payout."),
    ("ETF", ["etf", "exchange traded fund", "exchange-traded fund"],
     "An exchange-traded fund is a basket of assets (an index, a sector, bonds, commodities) that trades like a single stock — a low-cost way to diversify. Check its expense ratio and what it actually holds."),
    ("IPO", ["ipo", "initial public offering"],
     "An initial public offering: a private company first sells shares to the public and lists on an exchange. New listings are often volatile, and early-trading prices can differ sharply from the offer price."),
    ("Short selling and short squeezes", ["short selling", "short squeeze", "short interest", "shorting"],
     "Short selling means borrowing shares, selling them, and hoping to buy them back cheaper. Losses are theoretically unlimited because a price can rise without bound. "
     "A **short squeeze** is when a rising price forces short sellers to buy back, which pushes the price up further."),
    ("Beta", ["beta"],
     "A stock’s sensitivity to the overall market: a beta of 1 moves with it, above 1 swings more, below 1 less. *This app doesn’t currently show beta.*"),
    ("Stock splits and buybacks", ["stock split", "buyback", "buybacks", "share repurchase"],
     "A **split** divides each share into several (price falls proportionally, total value is unchanged). A **buyback** is a company repurchasing its own shares, which shrinks the share count and can lift per-share earnings."),
    ("Interest rates and stocks", ["interest rate", "interest rates", "rate cut", "rate hike", "fed rate"],
     "Higher rates raise borrowing costs and the discount applied to future profits, which tends to weigh most on growth stocks and richly valued sectors; lower rates usually do the opposite. "
     "Banks can benefit from wider lending margins, though effects vary and markets often move on *expectations* before a decision."),
    ("Diversification and dollar-cost averaging", ["diversification", "diversify", "dollar cost averaging", "dollar-cost averaging", "dca"],
     "**Diversification** spreads money across assets so one failure hurts less. **Dollar-cost averaging** invests a fixed amount at regular intervals, which smooths the entry price and removes the pressure to time the market."),
    ("Trend score (this app)", ["trend score", "score"],
     "A company’s score sums its recent news mentions, each weighted by how recent it is (halving every 12 hours). Press releases count ×1.5, SEC filings ×1.25, "
     "and stories carried by many different outlets get a boost. It measures *attention*, not whether the news is good."),
    ("Headline tone (this app)", ["tone", "headline tone", "bullish tone"],
     "A rough keyword signal from −1 (all negative words like *plunge, probe, misses*) to +1 (positive words like *surge, record, beats*) averaged over a company’s headlines. "
     "Treat it as colour, not a verdict — it can’t read context or sarcasm."),
]
GLOSS_RE = [(re.compile(r"(?<![\w-])(" + "|".join(re.escape(a) for a in al) + r")(?![\w-])", re.I), title, body) for title, al, body in GLOSSARY]
DEF_RE = re.compile(r"\b(what('s| is| are)|whats|define|definition|meaning of|explain|how (does|do|is|are)|difference between|tell me about|what does)\b", re.I)

HELP = """I’m the **built-in assistant** — no AI model, just your app’s own data, so I’m instant and free. I can help with:

- **What’s moving** — *“What looks bullish right now?”*, *“Anything bearish in Asia this week?”*
- **A company read** — *“Give me a full read on $NVDA”* (price action, trend, RSI, news, earnings date)
- **Comparisons** — *“Compare $AAPL and $MSFT”*
- **Earnings** — *“Who reports earnings this week?”*, *“When does Tesla report?”*
- **Filings & news** — *“Any notable 8-K filings today?”*, *“Latest news on Nvidia”*
- **Should I buy…?** — a signal scorecard from the data (not personal advice)
- **Concepts** — *“What is RSI?”*, *“Explain the 200-day moving average”*, *“What’s an 8-K Item 2.02?”*

For free-form questions, point the app at a local model (Ollama) or Claude — see the README."""


# ------------------------------------------------------------------ context + intent
class Ctx:
    """Runs tools and emits the same tool_start / tool_done events the AI providers do."""

    def __init__(self, queue: asyncio.Queue):
        self.q, self.n = queue, 0

    async def call(self, name: str, **args):
        self.n += 1
        tid = f"rules-{self.n}"
        await self.q.put(sse(dict(type="tool_start", id=tid, name=name, label=chat_tools.LABELS.get(name, "Working"))))
        try:
            data = await chat_tools.IMPLS[name](**args)
        except Exception as exc:
            data = {"error": str(exc)}
        await self.q.put(sse(dict(type="tool_done", id=tid, name=name, ok=not _bad(data))))
        return data


def classify(q: str, tickers: list[str]) -> str:
    ql = q.lower().strip()
    words = re.findall(r"[a-z']+", ql)
    if not words or (len(words) <= 3 and re.match(r"^(hi|hello|hey|help|thanks|thank you|yo|good (morning|afternoon|evening))\b", ql)) \
            or re.search(r"what can you (do|help)|how do(es)? (this|you) work|\bcapabilities\b", ql):
        return "help"
    if DEF_RE.search(ql) and not tickers and any(rx.search(ql) for rx, _, _ in GLOSS_RE):
        return "glossary"
    if len(tickers) >= 2 and re.search(r"compar|\bvs\.?\b|versus|better|which (one|is|stock)|\bor\b|difference|against|head to head", ql):
        return "compare"
    if re.search(r"earnings|\breports?\b|reporting|\beps\b|quarterly results", ql) and not re.search(r"what is|explain|define|meaning", ql):
        return "earnings"
    if re.search(r"8-?k|filings?\b|\bsec\b|form 8", ql):
        return "filings"
    if re.search(r"should i|\bbuy\b|\bsell\b|invest|good (stock|investment|buy|pick)|worth (buying|it)|safe\b|\bhold\b", ql):
        return "advice"
    if tickers and len(tickers) >= 2:
        return "compare"
    if tickers and not re.search(r"\bnews|headline|latest|what happened|updates?\b", ql):
        return "read"
    if re.search(r"news|headline|latest|what happened|updates?\b|happening", ql):
        return "news"
    if re.search(r"overview|around the world|how('s| is| are) (the )?(global )?(markets?|stocks)|market summary|regions?\b|across markets", ql):
        return "overview"
    if re.search(r"trend|bull|bear|\bhot\b|moving|movers|buzz|watch|best|top|strong|rall(y|ies)|gain|surg|plung|drop|worst|weak|opportunit|momentum|falling|rising|popular|interesting|what.?s up", ql):
        return "trending"
    if tickers:
        return "read"
    return "fallback"


def previous_tickers(history: list[dict], conn) -> list[str]:
    for m in reversed(history[:-1][-6:]):
        found = extract_tickers(m["content"], conn)
        if found:
            return found
    return []


# ------------------------------------------------------------------ handlers
async def h_help(ctx, q, tickers):
    return HELP


async def h_glossary(ctx, q, tickers):
    hits = [(title, body) for rx, title, body in GLOSS_RE if rx.search(q)]
    seen, out = set(), []
    for title, body in hits:
        if title not in seen:
            seen.add(title)
            out.append(f"**{title}** — {body}")
    return "\n\n".join(out[:2]) + "\n\n*Want to see it on a real company? Ask for a full read on any ticker.*"


async def h_trending(ctx, q, tickers, advice=False):
    ql = q.lower()
    hours, label = parse_window(q)
    region = parse_region(q)
    where = f" in **{region}** sources" if region else ""
    rows = await ctx.call("get_trending_companies", hours=hours, region=region, limit=15)
    if _bad(rows) or not rows:
        return NO_DATA if not region else f"Nothing in {region} sources for {label} yet. Try “all regions” or check the **Status** page."
    mode = "bear" if re.search(r"bear|plung|drop|fall|weak|worst|sell|risk|concern|trouble|negative|down\b", ql) else \
        "bull" if re.search(r"bull|rall|surg|gain|strong|best|buy|opportunit|momentum|rising|positive|\bup\b|top picks|invest", ql) else "all"
    note = ""
    if mode == "bull":
        pick = [r for r in rows if r["tone"] > .1 and r["momentum"] > 0][:6]
        title = f"Companies with **rising coverage and positive headline tone**{where} over {label}"
        if len(pick) < 3:
            pick = sorted(rows, key=lambda r: (r["tone"] > 0, r["momentum"] > 0, r["trend_score"]), reverse=True)[:6]
            note = "\n\n*Few names combine rising coverage with clearly positive tone right now, so these are the strongest overall.*"
    elif mode == "bear":
        pick = sorted([r for r in rows if r["tone"] < -.1], key=lambda r: r["tone"])[:6]
        title = f"Companies with **negative headline tone**{where} over {label}"
        if not pick:
            pick = sorted(rows, key=lambda r: r["tone"])[:3]
            title = f"No company in the top {len(rows)}{where} has clearly negative headlines over {label}. The lowest tone"
    else:
        pick, title = rows[:8], f"What’s trending{where} over {label}"
    tbl = "\n".join(f"| ${r['ticker']} | {r['name']} | {r['trend_score']} | {r['mentions']} | {r['momentum']:+d} | {r['tone']:+.2f} |" for r in pick)
    why = "\n".join(f"- **${r['ticker']}** — {_link(r['headlines'][0]['title'], r['headlines'][0].get('url'))} *({r['headlines'][0]['source']}, {_age(r['headlines'][0]['age_hours'])})*"
                    for r in pick[:3] if r["headlines"])
    foot = ("Rising coverage with positive tone is a **screen, not a signal** — check the price trend and what could go wrong before acting on any of these. "
            "Ask for a full read on one, e.g. *“Give me a full read on $" + pick[0]["ticker"] + "”*.")
    return (f"{title}:\n\n| Ticker | Company | Score | Mentions | Momentum | Tone |\n|---|---|---|---|---|---|\n{tbl}{note}\n\n**Why they’re in the news**\n{why}\n\n{foot}"
            + (f"\n\n{DISCLAIMER}" if advice or mode == "bull" else ""))


def _trend_state(s: dict, t: dict) -> str:
    a50, a200 = t.get("vs_sma50_pct"), t.get("vs_sma200_pct")
    if a50 is None or a200 is None:
        return "not enough price history for the long-term moving averages yet"
    if a50 > 0 and a200 > 0:
        return f"above both its 50-day ({_p(a50)}) and 200-day ({_p(a200)}) averages — an **uptrend**"
    if a50 < 0 and a200 < 0:
        return f"below both its 50-day ({_p(a50)}) and 200-day ({_p(a200)}) averages — a **downtrend**"
    if a200 > 0:
        return f"{_p(a200)} vs its 200-day average but {_p(a50)} vs its 50-day — **pulling back within a longer-term uptrend**"
    return f"{_p(a50)} vs its 50-day average but {_p(a200)} vs its 200-day — **bouncing, but still below its long-term trend**"


def _rsi_note(r) -> str:
    return "n/a" if r is None else f"{r:.0f} — " + ("stretched / overbought (>70)" if r > 70 else "oversold (<30)" if r < 30 else "healthy momentum" if r >= 50 else "soft momentum")


def scorecard(snap: dict, news: dict) -> tuple[list[tuple[str, int, str]], int, int]:
    t, q = snap.get("technicals") or {}, snap.get("quote") or {}
    rows: list[tuple[str, int, str]] = []
    for key, label in (("vs_sma200_pct", "Price vs 200-day average"), ("vs_sma50_pct", "Price vs 50-day average")):
        if t.get(key) is not None:
            rows.append((label, 1 if t[key] > 0 else -1, _p(t[key])))
    if t.get("ret_3m_pct") is not None:
        r = t["ret_3m_pct"]
        rows.append(("3-month return", 1 if r > 5 else -1 if r < -5 else 0, _p(r)))
    if t.get("rsi14") is not None:
        r = t["rsi14"]
        rows.append(("RSI(14)", -1 if r > 70 else 1 if r >= 50 else 0, f"{r:.0f}" + (" (stretched)" if r > 70 else " (oversold)" if r < 30 else "")))
    if q.get("year_high") and q.get("price"):
        off = (q["price"] / q["year_high"] - 1) * 100
        rows.append(("Distance from 52-week high", 1 if off > -10 else -1 if off < -30 else 0, _p(off)))
    if news.get("mentions"):
        tn = news.get("tone", 0)
        rows.append(("News tone", 1 if tn > .15 else -1 if tn < -.15 else 0, f"{tn:+.2f} over {news['mentions']} articles"))
    return rows, sum(1 for r in rows if r[1] > 0), sum(1 for r in rows if r[1] < 0)


async def _snapshot(ctx, ticker):
    snap, news = await asyncio.gather(ctx.call("get_stock_snapshot", symbol=ticker), ctx.call("get_company_news", ticker=ticker, hours=72, limit=6))
    return snap, news


def _read_text(ticker: str, snap: dict, news: dict, advice: bool = False) -> str:
    name = snap.get("name") or ticker
    q, t = snap.get("quote"), snap.get("technicals") or {}
    out = [f"### ${ticker} — {name}"]
    if q:
        out.append(f"**{_n(q['price'])} {q['currency']}** ({_p(q['change_pct'], 2)} on the day) · day range {_n(q['day_low'])}–{_n(q['day_high'])} · "
                   f"52-week {_n(q['year_low'])}–{_n(q['year_high'])} ({_p(t.get('from_52w_high_pct'))} from the high)")
        ret = " · ".join(f"{l} {_p(t.get(k))}" for k, l in (("ret_1m_pct", "1M"), ("ret_3m_pct", "3M"), ("ret_6m_pct", "6M"), ("ret_1y_pct", "1Y")) if t.get(k) is not None)
        out.append("**Trend & momentum**\n"
                   f"- Returns: {ret or 'n/a'}\n- The price is {_trend_state(snap, t)}.\n- RSI(14): {_rsi_note(t.get('rsi14'))}\n"
                   f"- Volatility: {_n(t.get('volatility_ann_pct'), 0)}% annualised (3-month)"
                   + ("\n- *Prices are from the demo data and are synthetic.*" if snap.get("price_source") == "demo" else ""))
    else:
        out.append(f"*No price data available* ({snap.get('price_error') or 'unknown reason'}).")
    if news.get("mentions"):
        out.append(f"**In the news (72h):** {news['mentions']} mentions, headline tone {news['tone']:+.2f} ({_tone(news['tone'])})"
                   + (f", including {news['press_releases']} press release(s)" if news.get("press_releases") else "") + "\n" + _headlines(news.get("articles", []), 3))
    else:
        out.append("**In the news:** nothing recent in the collected feeds.")
    e = snap.get("next_earnings")
    if e:
        out.append(f"**Next earnings:** {e['date']} ({e['when']})" + (f", EPS estimate {e['eps_estimate']}" if e.get("eps_estimate") is not None else ""))
    if advice and q:
        rows, pos, neg = scorecard(snap, news)
        sym = {1: "🟢", 0: "⚪", -1: "🔴"}
        tbl = "\n".join(f"| {sym[s]} | {lbl} | {val} |" for lbl, s, val in rows)
        out.append(f"**Signal scorecard**\n\n| | Signal | Reading |\n|---|---|---|\n{tbl}\n\n**{pos} positive, {neg} negative** of {len(rows)} signals. "
                   + ("Momentum and trend are broadly supportive" if pos - neg >= 2 else "Signals are broadly negative" if neg - pos >= 2 else "Signals are mixed")
                   + (", but an earnings report is coming up, which adds event risk." if e else ".")
                   + "\n\n**What would change this:** a break below the 200-day average, a miss or weak guidance on earnings, or the news tone turning negative."
                   + f"\n\n{DISCLAIMER}")
    return "\n\n".join(out)


async def h_read(ctx, q, tickers, advice=False):
    if not tickers:
        return "Which company? Give me a ticker or name, e.g. *“Give me a full read on $NVDA”*."
    t = tickers[0]
    snap, news = await _snapshot(ctx, t)
    if _bad(snap):
        return f"I couldn’t look up **{t}**: {(snap or {}).get('error', 'no data')}."
    return _read_text(t, snap, news if not _bad(news) else {}, advice) + ("" if advice else "\n\n*Want a verdict-style checklist? Ask “Should I buy $" + t + "?”*")


async def h_advice(ctx, q, tickers):
    if tickers:
        return await h_read(ctx, q, tickers, advice=True)
    return await h_trending(ctx, q if re.search(r"bull|best|top", q.lower()) else q + " best", [], advice=True)


async def h_compare(ctx, q, tickers):
    ts = tickers[:3]
    pairs = await asyncio.gather(*(_snapshot(ctx, t) for t in ts))
    ok = [(t, s, n) for t, (s, n) in zip(ts, pairs) if not _bad(s) and s.get("quote")]
    if len(ok) < 2:
        return "I couldn’t get price data for enough of those to compare. " + (NO_DATA if not ok else "")
    def row(label, f):
        return f"| {label} | " + " | ".join(f(s, n) for _, s, n in ok) + " |"
    tt = lambda s: s.get("technicals") or {}
    lines = ["| | " + " | ".join(f"**${t}**" for t, _, _ in ok) + " |", "|---|" + "---|" * len(ok),
             row("Price", lambda s, n: f"{_n(s['quote']['price'])} {s['quote']['currency']}"),
             row("Today", lambda s, n: _p(s["quote"]["change_pct"], 2)),
             row("1M / 3M return", lambda s, n: f"{_p(tt(s).get('ret_1m_pct'))} / {_p(tt(s).get('ret_3m_pct'))}"),
             row("6M / 1Y return", lambda s, n: f"{_p(tt(s).get('ret_6m_pct'))} / {_p(tt(s).get('ret_1y_pct'))}"),
             row("vs 200-day avg", lambda s, n: _p(tt(s).get("vs_sma200_pct"))),
             row("RSI(14)", lambda s, n: _n(tt(s).get("rsi14"), 0)),
             row("Volatility (ann.)", lambda s, n: f"{_n(tt(s).get('volatility_ann_pct'), 0)}%"),
             row("From 52-wk high", lambda s, n: _p(tt(s).get("from_52w_high_pct"))),
             row("News (72h)", lambda s, n: f"{n.get('mentions', 0)} · tone {n.get('tone', 0):+.2f}" if n and not _bad(n) else "–"),
             row("Next earnings", lambda s, n: (s.get("next_earnings") or {}).get("date", "–"))]
    def best(key, high=True):
        vals = [(tt(s).get(key), t) for t, s, _ in ok if tt(s).get(key) is not None]
        return (max if high else min)(vals)[1] if len(vals) > 1 else None
    edges = [f"- Strongest 3-month momentum: **${best('ret_3m_pct')}**" if best("ret_3m_pct") else "",
             f"- Furthest above its 200-day average: **${best('vs_sma200_pct')}**" if best("vs_sma200_pct") else "",
             f"- Lowest volatility: **${best('volatility_ann_pct', False)}**" if best("volatility_ann_pct", False) else ""]
    return ("**Side by side**\n\n" + "\n".join(lines) + "\n\n**Where each leads**\n" + "\n".join(e for e in edges if e)
            + "\n\nMomentum and volatility don’t say which is the better *investment* — valuation, business quality and your own horizon matter too.\n\n" + DISCLAIMER)


async def h_earnings(ctx, q, tickers):
    if tickers:
        snap = await ctx.call("get_stock_snapshot", symbol=tickers[0])
        e = None if _bad(snap) else snap.get("next_earnings")
        name = (snap or {}).get("name") or tickers[0]
        return (f"**${tickers[0]}** ({name}) reports on **{e['date']}** ({e['when']})" + (f", with an EPS estimate of {e['eps_estimate']}." if e.get("eps_estimate") is not None else ".")
                if e else f"I don’t see an upcoming earnings date for **${tickers[0]}** in the calendar yet (it covers the next ~3 weeks).")
    days = earnings_days(q)
    watch = bool(re.search(r"watch|worth|interest|in the news|trending|moving|hot|buzz", q.lower()))
    rows = await ctx.call("get_upcoming_earnings", days=days, only_trending=watch, limit=20)
    if _bad(rows) or not rows:
        return "No earnings found in that window" + (" for companies currently in the news" if watch else "") + ". The calendar fills in on the scheduled scrapes — check the **Status** page."
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r["date"], []).append(r)
    blocks = []
    for d, rs in by.items():
        items = "\n".join(f"- **${r['ticker']}** {r['name'] or ''} — {r['when']}" + (f", EPS est. {r['eps_estimate']}" if r.get("eps_estimate") is not None else "")
                          + (" · 🔥 in the news" if r.get("in_the_news") else "") for r in rs)
        blocks.append(f"**{d}**\n{items}")
    head = "Earnings in the next " + ("day" if days == 1 else f"{days} days") + (" for companies already in the news" if watch else "") + ":"
    return head + "\n\n" + "\n\n".join(blocks) + "\n\n🔥 marks companies with recent news coverage — they’re the likeliest to move. Open the **Earnings** page for the full calendar."


async def h_filings(ctx, q, tickers):
    if tickers:
        d = await ctx.call("get_company_news", ticker=tickers[0], hours=168, limit=20)
        items = [] if _bad(d) else [a for a in d["articles"] if a.get("kind") == "filing"]
        if not items:
            return f"No SEC filings for **${tickers[0]}** in the collected data for the past week."
        return f"Recent SEC filings for **${tickers[0]}**:\n\n" + _headlines(items, 6)
    d = await ctx.call("search_news", query="8-K", kind="filing", hours=48, limit=12)
    if _bad(d) or not d.get("results"):
        return "No 8-K filings collected in the last 48 hours. The EDGAR feed loads on scheduled scrapes — see the **Status** page."
    lines = "\n".join(f"- {_link(r['title'], r['url'])} — *{_age(r['age_hours'])}*" for r in d["results"][:10])
    return f"Latest **8-K filings** collected (last 48h):\n\n{lines}\n\n*Item 2.02 = earnings results, 5.02 = executive changes, 1.01 = material agreement. Ask “What is an 8-K?” for the full list.*"


STOP_WORDS = set("""the a an of on in for to and or is are was were be been what whats which who how why when where about any anything
latest news headline headlines update updates happening happened today this week recent recently me my tell show give please can you
could would with from at by it its stock stocks market markets company companies""".split())


async def h_news(ctx, q, tickers):
    if tickers:
        d = await ctx.call("get_company_news", ticker=tickers[0], hours=72, limit=8)
        if _bad(d) or not d.get("articles"):
            return f"Nothing recent about **${tickers[0]}** in the collected feeds (last 72 hours)."
        return f"Latest on **${tickers[0]}** ({d['name']}) — {d['mentions']} mentions, tone {d['tone']:+.2f} ({_tone(d['tone'])}):\n\n" + _headlines(d["articles"], 8)
    kw = " ".join(w for w in re.findall(r"[a-z0-9&.-]+", q.lower()) if w not in STOP_WORDS)[:80]
    if not kw:
        return await h_trending(ctx, q, [])
    return await _keyword_search(ctx, kw)


async def _keyword_search(ctx, kw: str):
    d = await ctx.call("search_news", query=kw, hours=168, limit=8)
    if _bad(d) or not d.get("results"):
        return None
    lines = "\n".join(f"- {_link(r['title'], r['url'])} — *{r['source']}*, {_age(r['age_hours'])}" + (f" · {', '.join('$' + t for t in r['tickers'][:3])}" if r["tickers"] else "") for r in d["results"])
    return f"Headlines matching “{kw}”:\n\n{lines}"


async def h_overview(ctx, q, tickers):
    hours, label = parse_window(q)
    d = await ctx.call("get_market_overview", hours=hours)
    if _bad(d) or not d.get("regions"):
        return NO_DATA
    t = d["last_24h"]
    rows = "\n".join(f"| {r['region']} | {r['articles']} | {r['press_releases']} | " + (", ".join(f"${c['ticker']} ({c['mentions']})" for c in r["top_companies"][:3]) or "–") + " |" for r in d["regions"])
    return (f"**Market coverage over {label}**\n\nIn the last 24h the app collected **{t['articles']}** articles, **{t['press_releases']}** press releases and **{t['sec_filings']}** SEC filings "
            f"mentioning **{t['companies_mentioned']}** companies.\n\n| Region | Articles | Press releases | Most-mentioned |\n|---|---|---|---|\n{rows}\n\n"
            "This shows where the news is, not how prices moved — ask for a read on any ticker for price action.")


async def h_fallback(ctx, q, tickers):
    kw = " ".join(w for w in re.findall(r"[a-z0-9&.-]+", q.lower()) if w not in STOP_WORDS)[:80]
    if kw:
        res = await _keyword_search(ctx, kw)
        if res:
            return res
    return ("I’m the built-in assistant, so I can only answer certain kinds of questions and I didn’t recognise that one.\n\n" + HELP.split("\n\n", 1)[1])


HANDLERS = {"help": h_help, "glossary": h_glossary, "trending": h_trending, "read": h_read, "advice": h_advice, "compare": h_compare, "earnings": h_earnings,
            "filings": h_filings, "news": h_news, "overview": h_overview, "fallback": h_fallback}


async def answer(history: list[dict], ctx: Ctx) -> str:
    q = history[-1]["content"]
    with db.session() as conn:
        tickers = extract_tickers(q, conn)
        if not tickers and re.search(r"\b(it|its|it's|their|them|that|this)\b", q.lower()):
            tickers = previous_tickers(history, conn)[:1]  # follow-up like "what about its earnings?"
    intent = classify(q, tickers)
    if intent == "read" and len(tickers) >= 2:
        intent = "compare"
    text = await HANDLERS[intent](ctx, q, tickers)
    return text or await h_fallback(ctx, q, tickers)


async def stream(history: list[dict]):
    """Async generator of SSE strings (same event protocol as the AI providers)."""
    queue: asyncio.Queue = asyncio.Queue()
    ctx = Ctx(queue)

    async def work():
        try:
            await queue.put(("text", await answer(history, ctx)))
        except Exception:
            await queue.put(("error", "Something went wrong while looking that up. Please retry."))
        await queue.put(None)

    task = asyncio.create_task(work())
    try:
        while (item := await queue.get()) is not None:
            if isinstance(item, str):
                yield item
            elif item[0] == "text":
                async for ev in typewriter(item[1]):
                    yield ev
            else:
                yield sse(dict(type="error", message=item[1]))
                return
        yield sse(dict(type="done", usage=dict(input=0, output=0, cache_read=0, rounds=ctx.n)))
    finally:
        task.cancel()
