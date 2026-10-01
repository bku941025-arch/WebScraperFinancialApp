"""Trend scoring.

Score for a ticker = sum over its articles in the window of
    recency_weight * kind_weight
where recency_weight halves every HALF_LIFE_H hours. A source-diversity
multiplier rewards stories covered by many outlets, and `momentum` compares the
last 6h of mentions to the prior 6h to flag what's accelerating.
"""
import math
import sqlite3
import time

from .entities import display_name

HALF_LIFE_H = 12.0
KIND_WEIGHT = {"press_release": 1.5, "news": 1.0}


def top_trends(conn: sqlite3.Connection, hours: int = 24, limit: int = 20,
               region: str | None = None, now: float | None = None) -> list[dict]:
    now = now or time.time()
    since = now - hours * 3600
    q = ("SELECT m.ticker, a.id, a.title, a.url, a.source, a.kind, a.region, a.published_at "
         "FROM mentions m JOIN articles a ON a.id = m.article_id WHERE a.published_at >= ?")
    args: list = [since]
    if region:
        q += " AND a.region = ?"
        args.append(region)
    by_ticker: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute(q, args):
        by_ticker.setdefault(r["ticker"], []).append(r)

    out = []
    for ticker, rows in by_ticker.items():
        score = sum(
            0.5 ** (((now - r["published_at"]) / 3600) / HALF_LIFE_H) * KIND_WEIGHT.get(r["kind"], 1.0)
            for r in rows)
        sources = {r["source"] for r in rows}
        score *= 1 + 0.25 * math.log2(len(sources))
        recent = sum(1 for r in rows if now - r["published_at"] <= 6 * 3600)
        prior = sum(1 for r in rows if 6 * 3600 < now - r["published_at"] <= 12 * 3600)
        rows.sort(key=lambda r: r["published_at"], reverse=True)
        out.append(dict(
            ticker=ticker, name=display_name(ticker), score=round(score, 2),
            mentions=len(rows), sources=len(sources),
            press_releases=sum(1 for r in rows if r["kind"] == "press_release"),
            momentum=recent - prior,
            headlines=[dict(title=r["title"], url=r["url"], source=r["source"], kind=r["kind"],
                            published_at=r["published_at"]) for r in rows[:5]],
        ))
    out.sort(key=lambda t: t["score"], reverse=True)
    return out[:limit]


def latest_announcements(conn: sqlite3.Connection, limit: int = 30, hours: int = 48,
                         now: float | None = None) -> list[dict]:
    now = now or time.time()
    rows = conn.execute(
        "SELECT a.title, a.url, a.source, a.region, a.published_at, "
        "(SELECT group_concat(ticker) FROM mentions WHERE article_id = a.id) AS tickers "
        "FROM articles a WHERE a.kind='press_release' AND a.published_at >= ? "
        "ORDER BY a.published_at DESC LIMIT ?", (now - hours * 3600, limit))
    return [dict(r, tickers=(r["tickers"] or "").split(",") if r["tickers"] else []) for r in rows]
