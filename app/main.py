import asyncio
import contextlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db, scheduler, scraper, trends
from .sources import SOURCES

MARKETS = scheduler.parse_markets(os.getenv("SCHEDULE_MARKETS", "US"))
STATIC = Path(__file__).parent / "static"


def _last_run() -> float | None:
    with db.session() as conn:
        return conn.execute("SELECT MAX(finished_at) FROM runs").fetchone()[0]


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if not os.getenv("DISABLE_SCHEDULER"):
        task = asyncio.create_task(scheduler.run_forever(MARKETS, scraper.scrape_all, _last_run))
    yield
    if task:
        task.cancel()


app = FastAPI(title="FinTrend", lifespan=lifespan)


def _schedule(n: int = 6) -> list[dict]:
    return [dict(at=r.at.timestamp(), label=r.label, market=r.market, phase=r.phase)
            for r in scheduler.upcoming(datetime.now(timezone.utc), MARKETS, n)]


@app.get("/api/trends")
def api_trends(hours: int = Query(24, ge=1, le=168), limit: int = Query(20, ge=1, le=100),
               region: str | None = None):
    with db.session() as conn:
        return trends.top_trends(conn, hours, limit, region)


@app.get("/api/feed")
def api_feed(kind: str | None = None, region: str | None = None, q: str | None = Query(None, max_length=100),
             hours: int = Query(48, ge=1, le=168), limit: int = Query(30, ge=1, le=200)):
    with db.session() as conn:
        return trends.feed(conn, kind, region, q, hours, limit)


@app.get("/api/announcements")
def api_announcements(limit: int = Query(30, ge=1, le=100)):
    with db.session() as conn:
        return trends.latest_announcements(conn, limit)


@app.get("/api/company/{ticker}")
def api_company(ticker: str, hours: int = Query(72, ge=6, le=168)):
    with db.session() as conn:
        return trends.company_detail(conn, ticker.upper(), hours)


@app.get("/api/markets")
def api_markets(hours: int = Query(24, ge=1, le=168)):
    with db.session() as conn:
        return trends.markets(conn, hours)


@app.get("/api/overview")
def api_overview():
    with db.session() as conn:
        return trends.overview(conn) | {"next": _schedule(1)}


@app.get("/api/regions")
def api_regions():
    return sorted({s.region for s in SOURCES})


@app.get("/api/status")
def api_status():
    with db.session() as conn:
        health = {r["source"]: dict(r) for r in conn.execute("SELECT * FROM source_health")}
        runs = [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY finished_at DESC LIMIT 15")]
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    sources = [dict(name=s.name, region=s.region, kind=s.kind, url=s.url,
                    **{k: health.get(s.name, {}).get(k) for k in ("last_attempt", "last_ok", "ok", "last_new")})
               for s in SOURCES]
    return dict(schedule=_schedule(6), markets=[dict(key=k, **_market(k)) for k in MARKETS],
                runs=runs, sources=sources, total_articles=total, now=time.time())


def _market(key: str) -> dict:
    m = scheduler.MARKETS[key]
    return dict(name=m.name, tz=m.tz, open=m.open.strftime("%H:%M"), close=m.close.strftime("%H:%M"))


@app.post("/api/refresh")
async def api_refresh():
    return await scraper.scrape_all("manual")


app.mount("/static", StaticFiles(directory=STATIC), name="static")

PAGES = {"/": "index.html", "/markets": "markets.html", "/feed": "feed.html", "/status": "status.html"}
for _path, _file in PAGES.items():
    app.get(_path, include_in_schema=False)(lambda f=_file: FileResponse(STATIC / f))


@app.get("/company/{ticker}", include_in_schema=False)
def company_page(ticker: str):
    return FileResponse(STATIC / "company.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)
