"""Ticker universe (SEC company_tickers.json): powers search, CIK→ticker for EDGAR filings,
and company names for tickers outside the curated list."""
import os
import re
import sqlite3
import time

import httpx

from . import db
from .entities import COMPANIES

URL = "https://www.sec.gov/files/company_tickers.json"
TTL = 7 * 86400
SEC_UA = os.getenv("SEC_USER_AGENT", "FinTrend/0.1 (personal project; set SEC_USER_AGENT to your contact email)")
SAFE_Q = re.compile(r"[^A-Za-z0-9 .&'\-]")
TICKER_RE = re.compile(r"^[A-Za-z0-9.\-^=]{1,12}$")


def parse_universe(js: dict) -> list[dict]:
    rows, seen = [], set()
    for i, v in enumerate(js.values()):
        t = str(v.get("ticker", "")).upper()
        if t and t not in seen:
            seen.add(t)
            rows.append(dict(ticker=t, name=v.get("title", t), cik=int(v["cik_str"]), rank=i))
    return rows


def store(rows: list[dict]) -> None:
    with db.session() as conn:
        conn.executemany("INSERT INTO companies(ticker,name,cik,rank) VALUES(:ticker,:name,:cik,:rank) "
                         "ON CONFLICT(ticker) DO UPDATE SET name=excluded.name, cik=excluded.cik, rank=excluded.rank", rows)
    db.cache_put("universe", {"count": len(rows)})


async def refresh(client: httpx.AsyncClient, force: bool = False) -> int:
    meta, age = db.cache_get("universe")
    if meta and age < TTL and not force:
        return meta["count"]
    r = await client.get(URL, headers={"User-Agent": SEC_UA})
    r.raise_for_status()
    rows = parse_universe(r.json())
    store(rows)
    return len(rows)


def cik_map(conn: sqlite3.Connection) -> dict[int, str]:
    """cik -> primary ticker (best-ranked share class)."""
    out: dict[int, str] = {}
    for r in conn.execute("SELECT cik, ticker FROM companies WHERE cik IS NOT NULL ORDER BY rank DESC"):
        out[r["cik"]] = r["ticker"]  # DESC so the lowest rank wins last
    return out


def names(conn: sqlite3.Connection, tickers) -> dict[str, str]:
    tickers = list(tickers)
    out = {t: COMPANIES[t][0] for t in tickers if t in COMPANIES}
    rest = [t for t in tickers if t not in out]
    for i in range(0, len(rest), 500):
        chunk = rest[i:i + 500]
        for r in conn.execute(f"SELECT ticker, name FROM companies WHERE ticker IN ({','.join('?' * len(chunk))})", chunk):
            out[r["ticker"]] = r["name"].title() if r["name"].isupper() else r["name"]
    return {t: out.get(t, t) for t in tickers}


def search(conn: sqlite3.Connection, q: str, limit: int = 8) -> list[dict]:
    q = SAFE_Q.sub("", q).strip()
    if not q:
        return []
    ql, qu = q.lower(), q.upper()
    cands: dict[str, tuple] = {}

    def consider(ticker, name, rank):
        tl, nl = ticker.lower(), name.lower()
        cls = 0 if tl == ql else 1 if tl.startswith(ql) else 2 if nl.startswith(ql) else 3 if ql in nl else None
        if cls is not None and (ticker not in cands or cands[ticker][0] > cls):
            cands[ticker] = (cls, rank, ticker, name)

    for t, (name, aliases) in COMPANIES.items():
        consider(t, name, -1)
        for a in aliases:
            consider(t, a, -1)
    like = ql.replace("%", "").replace("_", "")
    for r in conn.execute("SELECT ticker,name,rank FROM companies WHERE lower(ticker) LIKE ? OR lower(name) LIKE ? "
                          "ORDER BY rank LIMIT 60", (like + "%", "%" + like + "%")):
        consider(r["ticker"], r["name"], r["rank"])
    ordered = sorted(cands.values())[:limit]
    nm = names(conn, [c[2] for c in ordered])
    out = [dict(ticker=c[2], name=nm[c[2]]) for c in ordered]
    if TICKER_RE.match(q) and not any(c[0] <= 1 for c in ordered):  # no exact/prefix ticker match
        out.append(dict(ticker=qu, name=f"Look up {qu}", raw=True))  # any Yahoo-style symbol, e.g. 0700.HK
    return out
