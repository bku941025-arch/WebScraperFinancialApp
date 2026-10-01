"""Price history + stock summary.

Source: Yahoo Finance's public chart endpoint (unofficial; no key, may rate-limit or change).
One 1y/daily download serves the 1M/6M/1Y charts and the summary; 1D (5m) and 5D (15m) are
separate intraday downloads. Responses are cached in SQLite, and stale data is served if a
refresh fails. With FINTREND_DEMO=1 deterministic *synthetic* prices are generated instead.
"""
import os
import random
import re
import time

import httpx

from . import db
from .entities import COMPANIES

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval={iv}&includePrePost=false"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
SYMBOL_RE = re.compile(r"^[A-Za-z0-9.\-^=]{1,12}$")
PLANS = {"1D": ("1d", "5m", 180), "5D": ("5d", "15m", 600), "1Y": ("1y", "1d", 1800)}  # range, interval, cache TTL s
SLICE_DAYS = {"1M": 31, "6M": 183, "1Y": 366}
YAHOO_SYMBOL = {"BTC": "BTC-USD", "ETH": "ETH-USD"}
NO_QUOTE = {"OPENAI"}  # private companies in the curated list
DEMO = bool(os.getenv("FINTREND_DEMO"))


def yahoo_symbol(t: str) -> str | None:
    t = t.upper()
    return None if t in NO_QUOTE else YAHOO_SYMBOL.get(t, t)


def parse_yahoo(js: dict) -> dict:
    """-> {meta, candles:[[t,o,h,l,c,v],...]}; raises ValueError on an error/empty response."""
    chart = (js or {}).get("chart") or {}
    res = (chart.get("result") or [None])[0]
    if not res:
        raise ValueError((chart.get("error") or {}).get("description") or "no data")
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    candles = []
    for i, t in enumerate(res.get("timestamp") or []):
        c = (q.get("close") or [])[i] if i < len(q.get("close") or []) else None
        if c is None:
            continue
        g = lambda k: (q.get(k) or [None] * (i + 1))[i]
        candles.append([t, g("open") or c, g("high") or c, g("low") or c, c, g("volume") or 0])
    if not candles:
        raise ValueError("no price data")
    m = res.get("meta") or {}
    keep = ("currency", "exchangeName", "fullExchangeName", "instrumentType", "longName", "shortName",
            "regularMarketPrice", "regularMarketDayHigh", "regularMarketDayLow", "regularMarketVolume",
            "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "chartPreviousClose")
    return dict(meta={k: m[k] for k in keep if k in m}, candles=candles)


def synthetic(symbol: str, plan: str, now: float | None = None) -> dict:
    now = now or time.time()
    rnd = random.Random(f"{symbol}:{plan}")
    price = 30 + sum(map(ord, symbol)) % 420 + rnd.random()
    rng, iv, _ = PLANS[plan]
    if plan == "1D":
        step, n, vol = 300, 78, 0.0016
    elif plan == "5D":
        step, n, vol = 900, 130, 0.0028
    else:
        step, n, vol = 86400, 252, 0.014
    drift = rnd.uniform(-0.0004, 0.0012) * (1 if plan == "1Y" else 0.2)
    candles, t = [], int(now // step * step) - (n - 1) * step
    for _ in range(n):
        if plan == "1Y":
            while time.gmtime(t).tm_wday >= 5:
                t += step
        o = price
        price *= 1 + rnd.gauss(drift, vol)
        hi, lo = max(o, price) * (1 + abs(rnd.gauss(0, vol / 2))), min(o, price) * (1 - abs(rnd.gauss(0, vol / 2)))
        candles.append([t, round(o, 2), round(hi, 2), round(lo, 2), round(price, 2), int(rnd.uniform(.4, 1.6) * 2e6)])
        t += step
    name = COMPANIES.get(symbol, (symbol,))[0]
    return dict(meta=dict(currency="USD", exchangeName="DEMO", longName=name), candles=candles)


async def _download(symbol: str, plan: str) -> dict:
    rng, iv, _ = PLANS[plan]
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=12, follow_redirects=True) as c:
        r = await c.get(YAHOO.format(sym=symbol, rng=rng, iv=iv))
        r.raise_for_status()
        return parse_yahoo(r.json())


async def _plan_data(symbol: str, plan: str) -> dict:
    key = f"chart:{'demo:' if DEMO else ''}{symbol}:{plan}"
    cached, age = db.cache_get(key)
    if cached and age < PLANS[plan][2]:
        return cached
    try:
        data = synthetic(symbol, plan) if DEMO else await _download(symbol, plan)
        data["source"] = "demo" if DEMO else "yahoo"
        db.cache_put(key, data)
        return data
    except Exception as exc:
        if cached:
            return dict(cached, stale=True)
        return dict(meta={}, candles=[], source="yahoo", error=f"Price data unavailable ({type(exc).__name__})")


async def get_chart(symbol: str, rng: str = "1M") -> dict:
    sym = yahoo_symbol(symbol)
    if sym is None:
        return dict(symbol=symbol, range=rng, meta={}, candles=[], error="No public price data (private company)")
    plan = rng if rng in ("1D", "5D") else "1Y"
    data = await _plan_data(sym, plan)
    candles = data["candles"]
    if rng in SLICE_DAYS and candles:
        cut = candles[-1][0] - SLICE_DAYS[rng] * 86400
        candles = [c for c in candles if c[0] >= cut]
    return dict(symbol=symbol, range=rng, interval=PLANS[plan][1], meta=data["meta"], candles=candles,
                source=data.get("source"), stale=data.get("stale", False), error=data.get("error"))


def summarize(year: dict) -> dict | None:
    """Summary stats from a 1Y/daily chart payload."""
    c, m = year.get("candles") or [], year.get("meta") or {}
    if not c:
        return None
    price = m.get("regularMarketPrice") or c[-1][4]
    prev = c[-2][4] if len(c) > 1 else m.get("chartPreviousClose") or price
    return dict(
        price=round(price, 2), prev_close=round(prev, 2), change=round(price - prev, 2),
        change_pct=round((price - prev) / prev * 100, 2) if prev else 0.0,
        day_low=m.get("regularMarketDayLow") or c[-1][3], day_high=m.get("regularMarketDayHigh") or c[-1][2],
        year_low=m.get("fiftyTwoWeekLow") or min(x[3] for x in c), year_high=m.get("fiftyTwoWeekHigh") or max(x[2] for x in c),
        volume=m.get("regularMarketVolume") or c[-1][5],
        avg_volume=int(sum(x[5] for x in c[-30:]) / len(c[-30:])),
        ytd_pct=round((price / next((x[4] for x in c if time.gmtime(x[0]).tm_year == time.gmtime(c[-1][0]).tm_year), c[0][4]) - 1) * 100, 2),
        currency=m.get("currency", "USD"), exchange=m.get("fullExchangeName") or m.get("exchangeName"),
        name=m.get("longName") or m.get("shortName"), as_of=c[-1][0])
