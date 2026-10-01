import asyncio
import contextlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import chat, db, earnings, quotes, scheduler, scraper, trends, universe
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


@app.get("/api/search")
def api_search(q: str = Query("", max_length=60)):
    with db.session() as conn:
        return universe.search(conn, q)


def _symbol(symbol: str) -> str:
    if not quotes.SYMBOL_RE.match(symbol):
        raise HTTPException(422, "invalid symbol")
    return symbol.upper()


@app.get("/api/stock/{symbol}/chart")
async def api_stock_chart(symbol: str, range: str = Query("1M", pattern="^(1D|5D|1M|6M|1Y)$")):
    return await quotes.get_chart(_symbol(symbol), range)


@app.get("/api/stock/{symbol}/summary")
async def api_stock_summary(symbol: str):
    sym = _symbol(symbol)
    chart = await quotes.get_chart(sym, "1Y")
    quote = quotes.summarize(chart)
    with db.session() as conn:
        name = universe.names(conn, [sym])[sym]
        mentions = conn.execute("SELECT COUNT(*) FROM mentions m JOIN articles a ON a.id = m.article_id "
                                "WHERE m.ticker = ? AND a.published_at >= ?", (sym, time.time() - 86400)).fetchone()[0]
        filing = conn.execute("SELECT a.title, a.url, a.published_at FROM mentions m JOIN articles a ON a.id = m.article_id "
                              "WHERE m.ticker = ? AND a.kind = 'filing' ORDER BY a.published_at DESC LIMIT 1", (sym,)).fetchone()
        nxt = earnings.next_for(conn, sym)
    return dict(symbol=sym, name=name if name != sym else (quote or {}).get("name") or sym, quote=quote,
                error=chart.get("error"), source=chart.get("source"), stale=chart.get("stale", False),
                mentions_24h=mentions, latest_filing=dict(filing) if filing else None, next_earnings=nxt)


@app.get("/api/earnings")
def api_earnings(days: int = Query(14, ge=1, le=60), only_trending: bool = False, q: str | None = Query(None, max_length=40),
                 top: int | None = Query(None, ge=1, le=100), limit: int = Query(300, ge=1, le=1000)):
    with db.session() as conn:
        return earnings.upcoming(conn, days, only_trending, q, top, limit)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=6000)


class ChatIn(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=60)
    web: bool = False


@app.get("/api/chat/status")
async def api_chat_status():
    return await chat.status()


@app.post("/api/chat")
async def api_chat(body: ChatIn, request: Request):
    if not chat.limiter.allow(request.client.host if request.client else "?"):
        raise HTTPException(429, "Too many questions — please wait a few minutes.")
    resolved = await chat.resolve()
    return StreamingResponse(chat.stream_chat([m.model_dump() for m in body.messages], web=body.web and resolved.web, resolved=resolved),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
                runs=runs, sources=sources, total_articles=total, now=time.time(), datasets=_datasets())


def _datasets() -> dict:
    uni, uni_age = db.cache_get("universe")
    ern, ern_age = db.cache_get("earnings_meta")
    with db.session() as conn:
        upcoming = conn.execute("SELECT COUNT(*) FROM earnings WHERE date >= ?", (earnings.today_et().isoformat(),)).fetchone()[0]
    return dict(universe=dict(count=(uni or {}).get("count"), age=uni_age),
                earnings=dict(provider=(ern or {}).get("provider"), upcoming=upcoming, age=ern_age),
                prices="demo (synthetic)" if quotes.DEMO else "Yahoo Finance (unofficial)")


def _market(key: str) -> dict:
    m = scheduler.MARKETS[key]
    return dict(name=m.name, tz=m.tz, open=m.open.strftime("%H:%M"), close=m.close.strftime("%H:%M"))


@app.post("/api/refresh")
async def api_refresh():
    return await scraper.scrape_all("manual")


app.mount("/static", StaticFiles(directory=STATIC), name="static")

PAGES = {"/": "index.html", "/markets": "markets.html", "/feed": "feed.html", "/earnings": "earnings.html", "/chat": "chat.html", "/status": "status.html"}
for _path, _file in PAGES.items():
    app.get(_path, include_in_schema=False)(lambda f=_file: FileResponse(STATIC / f))


@app.get("/company/{ticker}", include_in_schema=False)
def company_page(ticker: str):
    return FileResponse(STATIC / "company.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)
