import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data.db"

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
