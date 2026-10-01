import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.getenv("FINTREND_DB") or Path(__file__).resolve().parent.parent / "data.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    source TEXT NOT NULL,
    region TEXT,
    kind TEXT NOT NULL,
    published_at REAL NOT NULL,   -- unix seconds, UTC
    fetched_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_pub ON articles(published_at);
CREATE TABLE IF NOT EXISTS mentions (
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    PRIMARY KEY (article_id, ticker)
);
CREATE INDEX IF NOT EXISTS idx_mentions_ticker ON mentions(ticker);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at REAL NOT NULL,
    finished_at REAL NOT NULL,
    trigger TEXT NOT NULL,        -- e.g. "US open", "manual", "startup"
    new_articles INTEGER NOT NULL,
    feeds_ok INTEGER NOT NULL,
    feeds_failed INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS companies (   -- ticker universe (SEC company_tickers.json)
    ticker TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    cik INTEGER,
    rank INTEGER NOT NULL           -- file order: larger companies first
);
CREATE INDEX IF NOT EXISTS idx_companies_cik ON companies(cik);
CREATE TABLE IF NOT EXISTS earnings (
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,             -- YYYY-MM-DD
    time TEXT NOT NULL DEFAULT '',  -- bmo | amc | dmh | ''
    name TEXT,
    eps_est REAL,
    fiscal TEXT,
    market_cap REAL,
    source TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_earnings_date ON earnings(date);
CREATE TABLE IF NOT EXISTS cache (       -- small key/value store for quotes + dataset metadata
    key TEXT PRIMARY KEY,
    fetched_at REAL NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_health (
    source TEXT PRIMARY KEY,
    last_attempt REAL NOT NULL,
    last_ok REAL,
    ok INTEGER NOT NULL,
    last_new INTEGER NOT NULL DEFAULT 0
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def session(path: Path | str | None = None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def cache_get(key: str) -> tuple[dict | None, float | None]:
    """Returns (payload, age_seconds) or (None, None)."""
    import json
    import time
    with session() as conn:
        r = conn.execute("SELECT fetched_at, payload FROM cache WHERE key = ?", (key,)).fetchone()
    return (json.loads(r["payload"]), time.time() - r["fetched_at"]) if r else (None, None)


def cache_put(key: str, payload: dict) -> None:
    import json
    import time
    with session() as conn:
        conn.execute("INSERT INTO cache(key,fetched_at,payload) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE "
                     "SET fetched_at=excluded.fetched_at, payload=excluded.payload",
                     (key, time.time(), json.dumps(payload)))
