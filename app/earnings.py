"""Quarterly earnings calendar.

Provider: Finnhub when FINNHUB_API_KEY is set (official, free tier), otherwise Nasdaq's public
calendar endpoint (unofficial — may change or rate-limit). Both are normalised to the same rows.
"""
import asyncio
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from . import db

NASDAQ = "https://api.nasdaq.com/api/calendar/earnings?date={d}"
FINNHUB = "https://finnhub.io/api/v1/calendar/earnings?from={a}&to={b}&token={k}"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIME_LABEL = {"bmo": "Before open", "amc": "After close", "dmh": "During market", "": "Time TBA"}


def today_et() -> date:
    return datetime.now(ZoneInfo("America/New_York")).date()


def _num(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    neg = "(" in s or "-" in s
    digits = re.sub(r"[^\d.]", "", s)
    try:
        v = float(digits)
    except ValueError:
        return None
    return -v if neg else v


def parse_nasdaq(js: dict, day: str) -> list[dict]:
    rows = ((js or {}).get("data") or {}).get("rows") or []
    out = []
    for r in rows:
        sym = (r.get("symbol") or "").strip().upper()
        if not sym:
            continue
        t = (r.get("time") or "")
        out.append(dict(ticker=sym.replace(".", "-"), date=day, name=r.get("name"),
                        time="bmo" if "pre" in t else "amc" if "after" in t else "",
                        eps_est=_num(r.get("epsForecast")), fiscal=r.get("fiscalQuarterEnding"),
                        market_cap=_num(r.get("marketCap"))))
    return out


def parse_finnhub(js: dict) -> list[dict]:
    out = []
    for r in (js or {}).get("earningsCalendar") or []:
        sym = (r.get("symbol") or "").strip().upper()
        if not sym or not r.get("date"):
            continue
        q = f"Q{r['quarter']} {r['year']}" if r.get("quarter") and r.get("year") else None
        out.append(dict(ticker=sym.replace(".", "-"), date=r["date"], name=None, time=r.get("hour") or "",
                        eps_est=_num(r.get("epsEstimate")), fiscal=q, market_cap=None))
    return out


def replace_window(rows: list[dict], start: str, end: str, source: str) -> None:
    """Upsert rows and drop stale rows from the same provider inside the window (rescheduled dates)."""
    now = time.time()
    with db.session() as conn:
        keep = {(r["ticker"], r["date"]) for r in rows}
        for r in conn.execute("SELECT ticker, date FROM earnings WHERE source=? AND date BETWEEN ? AND ?",
                              (source, start, end)).fetchall():
            if (r["ticker"], r["date"]) not in keep:
                conn.execute("DELETE FROM earnings WHERE ticker=? AND date=?", (r["ticker"], r["date"]))
        conn.executemany(
            "INSERT INTO earnings(ticker,date,time,name,eps_est,fiscal,market_cap,source,updated_at) "
            "VALUES(:ticker,:date,:time,:name,:eps_est,:fiscal,:market_cap,:source,:now) "
            "ON CONFLICT(ticker,date) DO UPDATE SET time=excluded.time, name=COALESCE(excluded.name,name), "
            "eps_est=excluded.eps_est, fiscal=excluded.fiscal, market_cap=COALESCE(excluded.market_cap,market_cap), "
            "source=excluded.source, updated_at=excluded.updated_at",
            [dict(r, source=source, now=now) for r in rows])


async def refresh(client: httpx.AsyncClient, days: int = 21) -> dict:
    start = today_et()
    end = start + timedelta(days=days)
    key = os.getenv("FINNHUB_API_KEY")
    if key:
        r = await client.get(FINNHUB.format(a=start, b=end, k=key))
        r.raise_for_status()
        rows, source, failed = parse_finnhub(r.json()), "finnhub", 0
    else:
        sem = asyncio.Semaphore(3)
        weekdays = [start + timedelta(days=i) for i in range(days + 1) if (start + timedelta(days=i)).weekday() < 5]

        async def one(d: date):
            async with sem:
                try:
                    r = await client.get(NASDAQ.format(d=d), headers={"User-Agent": BROWSER_UA, "Accept": "application/json",
                                                                     "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"})
                    r.raise_for_status()
                    return parse_nasdaq(r.json(), d.isoformat())
                except Exception:
                    return None
        results = await asyncio.gather(*(one(d) for d in weekdays))
        failed = sum(r is None for r in results)
        if failed == len(results):
            raise RuntimeError("earnings calendar unreachable")
        rows, source = [x for r in results if r for x in r], "nasdaq"
    replace_window(rows, start.isoformat(), end.isoformat(), source)
    meta = dict(provider=source, rows=len(rows), failed_days=failed)
    db.cache_put("earnings_meta", meta)
    return meta


def upcoming(conn: sqlite3.Connection, days: int = 14, only_trending: bool = False, q: str | None = None,
             top: int | None = None, limit: int = 300, today: date | None = None, now: float | None = None) -> list[dict]:
    today = today or today_et()
    now = now or time.time()
    sql = ("SELECT e.*, (SELECT COUNT(*) FROM mentions m JOIN articles a ON a.id = m.article_id "
           "WHERE m.ticker = e.ticker AND a.published_at >= ?) AS mentions FROM earnings e "
           "WHERE e.date BETWEEN ? AND ?")
    args: list = [now - 72 * 3600, today.isoformat(), (today + timedelta(days=days)).isoformat()]
    if q:
        sql += " AND (e.ticker LIKE ? OR e.name LIKE ?)"
        like = "%" + re.sub(r"[^A-Za-z0-9 .\-]", "", q) + "%"
        args += [like, like]
    rows = [dict(r, time_label=TIME_LABEL.get(r["time"], "Time TBA"), trending=r["mentions"] > 0)
            for r in conn.execute(sql, args)]
    if only_trending:
        rows = [r for r in rows if r["trending"]]
    if top:  # most relevant N: trending first, then biggest companies
        rows = sorted(rows, key=lambda r: (-r["mentions"], -(r["market_cap"] or 0)))[:top]
    rows.sort(key=lambda r: (r["date"], r["time"] != "bmo", -(r["market_cap"] or 0), r["ticker"]))
    return rows[:limit]


def next_for(conn: sqlite3.Connection, ticker: str, today: date | None = None) -> dict | None:
    r = conn.execute("SELECT * FROM earnings WHERE ticker=? AND date>=? ORDER BY date LIMIT 1",
                     (ticker, (today or today_et()).isoformat())).fetchone()
    return dict(r, time_label=TIME_LABEL.get(r["time"], "Time TBA")) if r else None
