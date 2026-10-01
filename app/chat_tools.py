"""Tools the finance assistant can call. Each reads this app's own data (news, filings, earnings)
or the price provider, and returns compact JSON — the model never sees raw candles or HTML."""
import json
import math
import time
from datetime import datetime, timezone

from . import db, earnings, quotes, trends, universe

MAX_RESULT_CHARS = 9000

TOOLS = [
    {"name": "get_trending_companies",
     "description": "Ranked list of companies/tickers currently trending in the financial news, press-release and SEC-filing "
                    "feeds this app collects from sources worldwide. Each entry has a trend score, mention/source counts, "
                    "momentum (mentions in the last 6h minus the prior 6h), a rough headline-tone score (-1 bearish .. +1 bullish) "
                    "and top headlines. Use for questions about what is hot, bullish, bearish or moving in the news.",
     "input_schema": {"type": "object", "properties": {
         "hours": {"type": "integer", "description": "Lookback window in hours, 1-168. Default 24."},
         "region": {"type": "string", "description": "Optional source region filter: US, UK, EU, Asia, India, AU or Global."},
         "limit": {"type": "integer", "description": "Number of companies, 1-15. Default 8."}}}},
    {"name": "get_stock_snapshot",
     "description": "Price action for one ticker: last price, daily change, day and 52-week range, volume, returns over 1M/3M/6M/1Y, "
                    "position versus the 50- and 200-day moving averages, RSI(14) and annualised volatility, plus this app's news "
                    "mention count and next earnings date. Prices come from Yahoo Finance and may be delayed. Use the Yahoo-style "
                    "symbol (e.g. AAPL, 7203.T, 0700.HK, BTC-USD).",
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string", "description": "Ticker symbol."}}, "required": ["symbol"]}},
    {"name": "get_company_news",
     "description": "Recent articles, press releases and SEC 8-K filings mentioning a ticker, newest first, with source and time.",
     "input_schema": {"type": "object", "properties": {
         "ticker": {"type": "string"}, "hours": {"type": "integer", "description": "1-168, default 72."},
         "limit": {"type": "integer", "description": "1-20, default 10."}}, "required": ["ticker"]}},
    {"name": "search_news",
     "description": "Keyword search over the headlines and summaries collected by this app. Use for themes, sectors or events "
                    "(e.g. 'rate cut', 'AI chips', 'acquisition').",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"},
         "kind": {"type": "string", "enum": ["news", "press_release", "filing"], "description": "Optional source type filter."},
         "hours": {"type": "integer", "description": "1-168, default 72."}, "limit": {"type": "integer", "description": "1-20, default 10."}},
         "required": ["query"]}},
    {"name": "get_upcoming_earnings",
     "description": "Scheduled quarterly earnings reports: date, before-open/after-close, EPS estimate, fiscal quarter, market cap, "
                    "and whether the company is currently in the news.",
     "input_schema": {"type": "object", "properties": {
         "days": {"type": "integer", "description": "Days ahead, 1-30. Default 7."},
         "only_trending": {"type": "boolean", "description": "Only companies currently in the news."},
         "limit": {"type": "integer", "description": "1-30, default 15. Biggest/most newsworthy first."}}}},
    {"name": "find_ticker",
     "description": "Resolve a company name or partial ticker to ticker symbols. Use before other tools when unsure of a symbol.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "get_market_overview",
     "description": "Coverage statistics and the most-mentioned companies per region over a window, plus counts of articles, press "
                    "releases and filings. Use for broad 'what is happening across markets' questions.",
     "input_schema": {"type": "object", "properties": {"hours": {"type": "integer", "description": "1-168, default 24."}}}},
]

LABELS = {
    "get_trending_companies": "Checking trending companies",
    "get_stock_snapshot": "Pulling price action",
    "get_company_news": "Reading company news",
    "search_news": "Searching headlines",
    "get_upcoming_earnings": "Checking the earnings calendar",
    "find_ticker": "Looking up ticker",
    "get_market_overview": "Scanning markets",
}


def _int(v, default, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _str(v, n=80):
    return v.strip()[:n] if isinstance(v, str) else ""


def technicals(candles: list[list]) -> dict:
    """Return/trend/momentum/volatility figures from daily candles [t,o,h,l,c,v]."""
    closes = [c[4] for c in candles]
    n = len(closes)
    if n < 2:
        return {}

    def ret(days):
        return round((closes[-1] / closes[-1 - days] - 1) * 100, 2) if n > days else None

    def sma(k):
        return sum(closes[-k:]) / k if n >= k else None

    out = dict(ret_1m_pct=ret(21), ret_3m_pct=ret(63), ret_6m_pct=ret(126), ret_1y_pct=ret(n - 1) if n > 200 else None)
    for k in (50, 200):
        m = sma(k)
        out[f"vs_sma{k}_pct"] = round((closes[-1] / m - 1) * 100, 2) if m else None
    if n > 15:  # Wilder RSI(14)
        gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, n)]
        losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, n)]
        ag, al = sum(gains[:14]) / 14, sum(losses[:14]) / 14
        for g, l in zip(gains[14:], losses[14:]):
            ag, al = (ag * 13 + g) / 14, (al * 13 + l) / 14
        out["rsi14"] = round(100 - 100 / (1 + ag / al), 1) if al else 100.0
    rets = [closes[i] / closes[i - 1] - 1 for i in range(max(1, n - 63), n)]
    if len(rets) > 5:
        mu = sum(rets) / len(rets)
        out["volatility_ann_pct"] = round(math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252) * 100, 1)
    out["from_52w_high_pct"] = round((closes[-1] / max(c[2] for c in candles) - 1) * 100, 2)
    return out


def _headline(h: dict) -> dict:
    return dict(title=h["title"], source=h["source"], kind=h.get("kind"),
                age_hours=round((time.time() - h["published_at"]) / 3600, 1), url=h.get("url"))


async def get_trending_companies(hours=24, region=None, limit=8):
    with db.session() as conn:
        rows = trends.top_trends(conn, _int(hours, 24, 1, 168), _int(limit, 8, 1, 15), _str(region, 12) or None)
    return [dict(ticker=r["ticker"], name=r["name"], trend_score=r["score"], mentions=r["mentions"], sources=r["sources"],
                 press_releases=r["press_releases"], sec_filings=r["filings"], momentum=r["momentum"], tone=r["tone"],
                 headlines=[_headline(h) for h in r["headlines"][:3]]) for r in rows]


async def get_stock_snapshot(symbol=""):
    sym = _str(symbol, 12).upper()
    if not quotes.SYMBOL_RE.match(sym):
        return {"error": "invalid symbol"}
    chart = await quotes.get_chart(sym, "1Y")
    quote = quotes.summarize(chart)
    with db.session() as conn:
        name = universe.names(conn, [sym])[sym]
        mentions = conn.execute("SELECT COUNT(*) FROM mentions m JOIN articles a ON a.id = m.article_id "
                                "WHERE m.ticker = ? AND a.published_at >= ?", (sym, time.time() - 86400)).fetchone()[0]
        nxt = earnings.next_for(conn, sym)
    out = dict(symbol=sym, name=name, news_mentions_24h=mentions,
               next_earnings=dict(date=nxt["date"], when=nxt["time_label"], eps_estimate=nxt["eps_est"]) if nxt else None)
    if not quote:
        return out | {"price_error": chart.get("error") or "no price data"}
    q = {k: quote[k] for k in ("price", "change", "change_pct", "day_low", "day_high", "year_low", "year_high",
                               "volume", "avg_volume", "ytd_pct", "currency", "exchange")}
    return out | dict(quote=q, technicals=technicals(chart["candles"]), price_source=chart.get("source"),
                      price_as_of=datetime.fromtimestamp(quote["as_of"], timezone.utc).strftime("%Y-%m-%d"),
                      stale=chart.get("stale", False))


async def get_company_news(ticker="", hours=72, limit=10):
    t = _str(ticker, 12).upper()
    with db.session() as conn:
        d = trends.company_detail(conn, t, _int(hours, 72, 6, 168))
    return dict(ticker=t, name=d["name"], mentions=d["mentions"], press_releases=d["press_releases"], tone=d["tone"],
                articles=[_headline(a) for a in d["articles"][:_int(limit, 10, 1, 20)]])


async def search_news(query="", kind=None, hours=72, limit=10):
    q = _str(query, 100)
    if not q:
        return {"error": "empty query"}
    with db.session() as conn:
        rows = trends.feed(conn, kind if kind in ("news", "press_release", "filing") else None, None, q,
                           _int(hours, 72, 1, 168), _int(limit, 10, 1, 20))
    return dict(query=q, count=len(rows), results=[dict(title=r["title"], source=r["source"], kind=r["kind"], tickers=r["tickers"],
                                                        age_hours=round((time.time() - r["published_at"]) / 3600, 1), url=r["url"]) for r in rows])


async def get_upcoming_earnings(days=7, only_trending=False, limit=15):
    with db.session() as conn:
        rows = earnings.upcoming(conn, _int(days, 7, 1, 30), bool(only_trending), None, _int(limit, 15, 1, 30))
    return [dict(ticker=r["ticker"], name=r["name"], date=r["date"], when=r["time_label"], eps_estimate=r["eps_est"], fiscal=r["fiscal"],
                 market_cap=r["market_cap"], in_the_news=r["trending"], mentions_72h=r["mentions"]) for r in rows]


async def find_ticker(query=""):
    with db.session() as conn:
        return [r for r in universe.search(conn, _str(query), 6) if not r.get("raw")]


async def get_market_overview(hours=24):
    with db.session() as conn:
        h = _int(hours, 24, 1, 168)
        m = trends.markets(conn, h)
        ov = trends.overview(conn)
    return dict(window_hours=h, last_24h=dict(articles=ov["articles_24h"], press_releases=ov["press_24h"], sec_filings=ov["filings_24h"],
                                              companies_mentioned=ov["companies_24h"]),
                regions=[dict(region=r["region"], articles=r["articles"], press_releases=r["press_releases"],
                              top_companies=[dict(ticker=t["ticker"], name=t["name"], mentions=t["n"]) for t in r["top"]]) for r in m["regions"]])


IMPLS = {f.__name__: f for f in (get_trending_companies, get_stock_snapshot, get_company_news, search_news,
                                 get_upcoming_earnings, find_ticker, get_market_overview)}


async def run_tool(name: str, args) -> tuple[str, bool]:
    """-> (json text for the tool_result, is_error). Never raises."""
    fn = IMPLS.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool {name}"}), True
    try:
        res = await fn(**(args if isinstance(args, dict) else {}))
        text = json.dumps(res, default=str, ensure_ascii=False)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + '…[truncated]"'
        return text, bool(isinstance(res, dict) and res.get("error"))
    except TypeError as exc:  # unexpected argument names from the model
        return json.dumps({"error": f"bad arguments: {exc}"}), True
    except Exception as exc:  # a tool failure must not break the conversation
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"}), True
