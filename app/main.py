import asyncio
import contextlib
import os
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, scraper, trends
from .sources import SOURCES

REFRESH_MINUTES = int(os.getenv("REFRESH_MINUTES", "15"))
STATIC = Path(__file__).parent / "static"


async def _loop():
    while True:
        await scraper.scrape_all()
        await asyncio.sleep(REFRESH_MINUTES * 60)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_loop())
    yield
    task.cancel()


app = FastAPI(title="FinTrend", lifespan=lifespan)


@app.get("/api/trends")
def api_trends(hours: int = Query(24, ge=1, le=168), limit: int = Query(20, ge=1, le=100),
               region: str | None = None):
    with db.session() as conn:
        return trends.top_trends(conn, hours, limit, region)


@app.get("/api/announcements")
def api_announcements(limit: int = Query(30, ge=1, le=100)):
    with db.session() as conn:
        return trends.latest_announcements(conn, limit)


@app.get("/api/regions")
def api_regions():
    return sorted({s.region for s in SOURCES})


@app.post("/api/refresh")
async def api_refresh():
    return await scraper.scrape_all()


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
