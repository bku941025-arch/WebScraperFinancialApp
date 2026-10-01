"""Trend scoring and the read-side queries behind the API.

Score for a ticker = sum over its articles in the window of
    recency_weight * kind_weight
where recency_weight halves every HALF_LIFE_H hours. A source-diversity
multiplier rewards stories covered by many outlets, and `momentum` compares the
last 6h of mentions to the prior 6h to flag what's accelerating.
"""
import math
import sqlite3
import time

from . import earnings, sentiment, universe

HALF_LIFE_H = 12.0
KIND_WEIGHT = {"press_release": 1.5, "filing": 1.25, "news": 1.0}
BINS = 24


def _bin(ts: float, since: float, span: float, bins: int) -> int:
    return min(max(int((ts - since) / span * bins), 0), bins - 1)


def top_trends(conn: sqlite3.Connection, hours: int = 24, limit: int = 20,
               region: str | None = None, now: float | None = None) -> list[dict]:
    now = now or time.time()
    since, span = now - hours * 3600, hours * 3600
    q = ("SELECT m.ticker, a.title, a.url, a.source, a.kind, a.region, a.published_at "
         "FROM mentions m JOIN articles a ON a.id = m.article_id WHERE a.published_at >= ?")
    args: list = [since]
    if region:
        q += " AND a.region = ?"
        args.append(region)
    by_ticker: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute(q, args):
        by_ticker.setdefault(r["ticker"], []).append(r)

    nm = universe.names(conn, by_ticker)
    out = []
    for ticker, rows in by_ticker.items():
        score = sum(
            0.5 ** (((now - r["published_at"]) / 3600) / HALF_LIFE_H) * KIND_WEIGHT.get(r["kind"], 1.0)
            for r in rows)
        sources = {r["source"] for r in rows}
        score *= 1 + 0.25 * math.log2(len(sources))
        recent = sum(1 for r in rows if now - r["published_at"] <= 6 * 3600)
        prior = sum(1 for r in rows if 6 * 3600 < now - r["published_at"] <= 12 * 3600)
        spark = [0] * BINS
        for r in rows:
            spark[_bin(r["published_at"], since, span, BINS)] += 1
        rows.sort(key=lambda r: r["published_at"], reverse=True)
        out.append(dict(
            ticker=ticker, name=nm[ticker], score=round(score, 2),
            mentions=len(rows), sources=len(sources),
            press_releases=sum(1 for r in rows if r["kind"] == "press_release"),
            filings=sum(1 for r in rows if r["kind"] == "filing"),
            momentum=recent - prior, spark=spark,
            tone=round(sum(sentiment.score(r["title"]) for r in rows) / len(rows), 2),
            headlines=[dict(title=r["title"], url=r["url"], source=r["source"], kind=r["kind"],
                            published_at=r["published_at"]) for r in rows[:5]],
        ))
    out.sort(key=lambda t: t["score"], reverse=True)
    return out[:limit]


def feed(conn: sqlite3.Connection, kind: str | None = None, region: str | None = None,
         q: str | None = None, hours: int = 48, limit: int = 30, now: float | None = None) -> list[dict]:
    now = now or time.time()
    sql = ("SELECT a.title, a.url, a.summary, a.source, a.region, a.kind, a.published_at, "
           "(SELECT group_concat(ticker) FROM mentions WHERE article_id = a.id) AS tickers "
           "FROM articles a WHERE a.published_at >= ?")
    args: list = [now - hours * 3600]
    if kind in ("press_release", "news", "filing"):
        sql += " AND a.kind = ?"
        args.append(kind)
    if region:
        sql += " AND a.region = ?"
        args.append(region)
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        sql += " AND (a.title LIKE ? ESCAPE '\\' OR a.summary LIKE ? ESCAPE '\\')"
        args += [like, like]
    sql += " ORDER BY a.published_at DESC LIMIT ?"
    args.append(limit)
    return [dict(r, tickers=r["tickers"].split(",") if r["tickers"] else [],
                 tone=sentiment.score(r["title"])) for r in conn.execute(sql, args)]


def latest_announcements(conn, limit: int = 30, hours: int = 48, now: float | None = None) -> list[dict]:
    return feed(conn, kind="press_release", hours=hours, limit=limit, now=now)


def company_detail(conn: sqlite3.Connection, ticker: str, hours: int = 72,
                   now: float | None = None) -> dict:
    now = now or time.time()
    since, span, bins = now - hours * 3600, hours * 3600, 36
    rows = conn.execute(
        "SELECT a.title, a.url, a.source, a.region, a.kind, a.published_at FROM mentions m "
        "JOIN articles a ON a.id = m.article_id WHERE m.ticker = ? AND a.published_at >= ? "
        "ORDER BY a.published_at DESC", (ticker, since)).fetchall()
    timeline = [dict(t=since + i * span / bins, n=0, pr=0) for i in range(bins)]
    by_source: dict[str, int] = {}
    by_region: dict[str, int] = {}
    for r in rows:
        b = timeline[_bin(r["published_at"], since, span, bins)]
        b["n"] += 1
        b["pr"] += r["kind"] == "press_release"
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1
        by_region[r["region"]] = by_region.get(r["region"], 0) + 1
    related = conn.execute(
        "SELECT m2.ticker AS ticker, COUNT(*) AS n FROM mentions m1 "
        "JOIN mentions m2 ON m1.article_id = m2.article_id AND m2.ticker != m1.ticker "
        "JOIN articles a ON a.id = m1.article_id WHERE m1.ticker = ? AND a.published_at >= ? "
        "GROUP BY m2.ticker ORDER BY n DESC LIMIT 8", (ticker, since)).fetchall()
    tone = sum(sentiment.score(r["title"]) for r in rows) / len(rows) if rows else 0.0
    return dict(
        ticker=ticker, name=universe.names(conn, [ticker])[ticker], hours=hours, mentions=len(rows),
        next_earnings=earnings.next_for(conn, ticker),
        press_releases=sum(r["kind"] == "press_release" for r in rows), tone=round(tone, 2),
        timeline=timeline,
        by_source=sorted(({"name": k, "n": v} for k, v in by_source.items()), key=lambda x: -x["n"]),
        by_region=sorted(({"name": k, "n": v} for k, v in by_region.items()), key=lambda x: -x["n"]),
        related=[dict(ticker=r["ticker"], name=universe.names(conn, [r["ticker"]])[r["ticker"]], n=r["n"]) for r in related],
        articles=[dict(r) | {"tone": sentiment.score(r["title"])} for r in rows[:40]],
    )


def markets(conn: sqlite3.Connection, hours: int = 24, now: float | None = None) -> dict:
    now = now or time.time()
    since = now - hours * 3600
    regions: dict[str, dict] = {}
    for r in conn.execute("SELECT region, COUNT(*) n, SUM(kind='press_release') pr FROM articles "
                          "WHERE published_at >= ? GROUP BY region", (since,)):
        regions[r["region"]] = dict(region=r["region"], articles=r["n"], press_releases=r["pr"] or 0, top=[])
    for r in conn.execute(
            "SELECT a.region, m.ticker, COUNT(*) n FROM mentions m JOIN articles a ON a.id = m.article_id "
            "WHERE a.published_at >= ? GROUP BY a.region, m.ticker ORDER BY n DESC", (since,)):
        reg = regions.get(r["region"])
        if reg and len(reg["top"]) < 5:
            reg["top"].append(dict(ticker=r["ticker"], name=universe.names(conn, [r["ticker"]])[r["ticker"]], n=r["n"]))
    return dict(regions=sorted(regions.values(), key=lambda x: -x["articles"]),
                cloud=[{k: t[k] for k in ("ticker", "name", "score", "tone", "mentions")}
                       for t in top_trends(conn, hours, 40, now=now)])


def overview(conn: sqlite3.Connection, now: float | None = None) -> dict:
    now = now or time.time()
    since = now - 24 * 3600
    a = conn.execute("SELECT COUNT(*) n, SUM(kind='press_release') pr, "
                     "SUM(kind='filing') fl FROM articles WHERE published_at >= ?", (since,)).fetchone()
    c = conn.execute("SELECT COUNT(DISTINCT m.ticker) FROM mentions m JOIN articles a ON a.id = m.article_id "
                     "WHERE a.published_at >= ?", (since,)).fetchone()[0]
    last = conn.execute("SELECT MAX(finished_at) FROM runs").fetchone()[0]
    return dict(articles_24h=a["n"], press_24h=a["pr"] or 0, filings_24h=a["fl"] or 0, companies_24h=c,
                sources=conn.execute("SELECT COUNT(*) FROM source_health WHERE ok=1").fetchone()[0],
                last_run=last)
