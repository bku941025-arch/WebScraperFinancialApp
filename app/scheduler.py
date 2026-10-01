"""Market-session scheduler: three scrapes per trading day per market —
shortly after the open, at mid-session, and shortly before the close.

Times are computed in each exchange's own timezone (so daylight saving is handled),
weekends are skipped. Exchange holidays are not modelled; a holiday run just finds
little new content. Select markets with SCHEDULE_MARKETS (default "US" = exactly
3 runs/day; "US,EU,ASIA" = 9).
"""
import asyncio
import logging
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("scheduler")


@dataclass(frozen=True)
class Market:
    key: str
    name: str
    tz: str
    open: time
    close: time


MARKETS = {
    "US": Market("US", "NYSE / Nasdaq", "America/New_York", time(9, 30), time(16, 0)),
    "EU": Market("EU", "London / Europe", "Europe/London", time(8, 0), time(16, 30)),
    "ASIA": Market("ASIA", "Tokyo", "Asia/Tokyo", time(9, 0), time(15, 0)),
}
OPEN_DELAY = timedelta(minutes=5)    # let opening headlines/press releases land
CLOSE_LEAD = timedelta(minutes=15)   # catch the pre-close wrap before the bell


@dataclass(frozen=True)
class Run:
    at: datetime  # UTC
    label: str    # "US open"
    market: str
    phase: str


def session_runs(m: Market, day: date) -> list[tuple[datetime, str]]:
    tz = ZoneInfo(m.tz)
    o, c = datetime.combine(day, m.open, tz), datetime.combine(day, m.close, tz)
    return [(o + OPEN_DELAY, "open"), (o + (c - o) / 2, "midday"), (c - CLOSE_LEAD, "close")]


def parse_markets(spec: str) -> list[str]:
    keys = [k.strip().upper() for k in spec.split(",") if k.strip().upper() in MARKETS]
    return keys or ["US"]


def upcoming(now: datetime, markets: list[str], n: int = 6) -> list[Run]:
    out: list[Run] = []
    for key in markets:
        m = MARKETS[key]
        today = now.astimezone(ZoneInfo(m.tz)).date()
        for d in range(-1, 8):
            day = today + timedelta(days=d)
            if day.weekday() >= 5:
                continue
            for at, phase in session_runs(m, day):
                at = at.astimezone(timezone.utc)
                if at > now:
                    out.append(Run(at, f"{key} {phase}", key, phase))
    out.sort(key=lambda r: r.at)
    return out[:n]


async def run_forever(markets: list[str], scrape, last_run_ts, catchup_hours: float = 6) -> None:
    """`scrape(trigger)` is awaited at each slot. On boot, scrape once if data is stale."""
    last = last_run_ts()
    if last is None or _time.time() - last > catchup_hours * 3600:
        await scrape("startup")
    while True:
        nxt = upcoming(datetime.now(timezone.utc), markets, 1)[0]
        await asyncio.sleep(max(0.0, (nxt.at - datetime.now(timezone.utc)).total_seconds()))
        log.info("scheduled scrape: %s", nxt.label)
        try:
            await scrape(nxt.label)
        except Exception:
            log.exception("scheduled scrape failed")
        await asyncio.sleep(1)  # guarantee we move past this slot
