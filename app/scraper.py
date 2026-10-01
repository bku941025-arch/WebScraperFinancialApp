"""Fetches all feeds concurrently and stores new articles + entity mentions."""
import asyncio
import calendar
import logging
import re
import time

import feedparser
import httpx

from .db import session
from .entities import extract_entities
from .sources import SOURCES, Source

log = logging.getLogger("scraper")
UA = "Mozilla/5.0 (compatible; FinTrendBot/0.1; +personal research use)"
TAG_RE = re.compile(r"<[^>]+>")


def parse_feed(source: Source, content: bytes, now: float | None = None) -> list[dict]:
    now = now or time.time()
    parsed = feedparser.parse(content)
    items = []
    for e in parsed.entries:
        url, title = e.get("link"), (e.get("title") or "").strip()
        if not url or not title:
            continue
        ts = e.get("published_parsed") or e.get("updated_parsed")
        published = float(calendar.timegm(ts)) if ts else now
        published = min(published, now)  # clamp bogus future dates
        summary = TAG_RE.sub("", e.get("summary") or "").strip()[:500]
        items.append(dict(url=url, title=title, summary=summary, source=source.name,
                          region=source.region, kind=source.kind, published_at=published))
    return items


def store(items: list[dict], now: float | None = None) -> int:
    now = now or time.time()
    new = 0
    with session() as conn:
        for it in items:
            cur = conn.execute(
                "INSERT OR IGNORE INTO articles(url,title,summary,source,region,kind,published_at,fetched_at)"
                " VALUES(:url,:title,:summary,:source,:region,:kind,:published_at,:fetched_at)",
                {**it, "fetched_at": now})
            if cur.rowcount:
                new += 1
                for t in extract_entities(f"{it['title']}. {it['summary']}"):
                    conn.execute("INSERT OR IGNORE INTO mentions VALUES(?,?)", (cur.lastrowid, t))
    return new


async def _fetch(client: httpx.AsyncClient, src: Source) -> tuple[Source, bytes | None]:
    try:
        r = await client.get(src.url)
        r.raise_for_status()
        return src, r.content
    except Exception as exc:  # one dead feed must not break the run
        log.warning("feed failed: %s (%s)", src.name, exc)
        return src, None


def record_run(trigger: str, started: float, report: dict[str, int]) -> None:
    now = time.time()
    ok = sum(1 for n in report.values() if n >= 0)
    with session() as conn:
        conn.execute("INSERT INTO runs(started_at,finished_at,trigger,new_articles,feeds_ok,feeds_failed)"
                     " VALUES(?,?,?,?,?,?)",
                     (started, now, trigger, sum(n for n in report.values() if n > 0), ok, len(report) - ok))
        for name, n in report.items():
            conn.execute(
                "INSERT INTO source_health(source,last_attempt,last_ok,ok,last_new) VALUES(?,?,?,?,?)"
                " ON CONFLICT(source) DO UPDATE SET last_attempt=excluded.last_attempt,"
                " last_ok=COALESCE(excluded.last_ok, last_ok), ok=excluded.ok,"
                " last_new=CASE WHEN excluded.ok THEN excluded.last_new ELSE last_new END",
                (name, now, now if n >= 0 else None, int(n >= 0), max(n, 0)))


async def scrape_all(trigger: str = "manual") -> dict[str, int]:
    """Returns {source name: new article count} (-1 if the feed failed)."""
    started = time.time()
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=15, follow_redirects=True) as client:
        results = await asyncio.gather(*(_fetch(client, s) for s in SOURCES))
    report = {}
    for src, content in results:
        report[src.name] = -1 if content is None else store(parse_feed(src, content))
    record_run(trigger, started, report)
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for name, n in asyncio.run(scrape_all()).items():
        print(f"{name:30s} {'FAILED' if n < 0 else f'{n} new'}")
