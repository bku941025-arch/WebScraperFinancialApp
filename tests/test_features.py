from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import db, demo, scheduler, scraper, sentiment, trends

UTC = timezone.utc


def test_three_runs_per_us_trading_day():
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)  # Thursday, EDT
    runs = scheduler.upcoming(now, ["US"], 3)
    assert [r.phase for r in runs] == ["open", "midday", "close"]
    # 09:35, 12:45, 15:45 New York time (UTC-4)
    assert [r.at.strftime("%H:%M") for r in runs] == ["13:35", "16:45", "19:45"]


def test_skips_weekend_and_handles_dst():
    fri_night = datetime(2026, 10, 2, 23, 0, tzinfo=UTC)
    nxt = scheduler.upcoming(fri_night, ["US"], 1)[0]
    assert nxt.at.date().isoformat() == "2026-10-05" and nxt.phase == "open"   # Monday
    winter = scheduler.upcoming(datetime(2026, 12, 1, 0, 0, tzinfo=UTC), ["US"], 1)[0]
    assert winter.at.strftime("%H:%M") == "14:35"  # EST = UTC-5 after DST ends


def test_multi_market_and_parse():
    assert scheduler.parse_markets("us, asia, junk") == ["US", "ASIA"]
    assert scheduler.parse_markets("junk") == ["US"]
    runs = scheduler.upcoming(datetime(2026, 10, 1, 0, 0, tzinfo=UTC), ["US", "EU", "ASIA"], 9)
    assert {r.market for r in runs} == {"US", "EU", "ASIA"}
    assert runs == sorted(runs, key=lambda r: r.at)


def test_sentiment():
    assert sentiment.score("Shares surge after record profit") == 1.0
    assert sentiment.score("Stock plunges on probe") == -1.0
    assert sentiment.score("Company holds annual meeting") == 0.0


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    demo.main()
    return tmp_path


def test_queries(seeded):
    with db.session() as c:
        t = trends.top_trends(c)
        assert t and len(t[0]["spark"]) == trends.BINS and sum(t[0]["spark"]) == t[0]["mentions"]
        co = trends.company_detail(c, t[0]["ticker"])
        assert co["mentions"] > 0 and len(co["timeline"]) == 36 and sum(b["n"] for b in co["timeline"]) == co["mentions"]
        assert trends.company_detail(c, "NOPE")["mentions"] == 0
        assert all(x["kind"] == "press_release" for x in trends.feed(c, kind="press_release"))
        assert all("nvidia" in (x["title"] + x["summary"]).lower() for x in trends.feed(c, q="nvidia"))
        assert trends.feed(c, q="100%_") == []  # LIKE wildcards are escaped
        assert trends.markets(c)["regions"] and trends.overview(c)["articles_24h"] > 0


def test_api_and_pages(seeded):
    client = TestClient(__import__("app.main", fromlist=["app"]).app)
    for path in ["/", "/markets", "/feed", "/status", "/company/NVDA"]:
        assert client.get(path).status_code == 200
    assert client.get("/api/trends?hours=24").json()[0]["ticker"]
    assert client.get("/api/company/nvda").json()["ticker"] == "NVDA"
    s = client.get("/api/status").json()
    assert len(s["schedule"]) == 6 and len(s["sources"]) == 16 and s["runs"]
    assert client.get("/api/feed?hours=0").status_code == 422


def test_run_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    scraper.record_run("US open", 1.0, {"A": 3, "B": -1})
    scraper.record_run("US midday", 2.0, {"A": 1, "B": -1})
    with db.session() as c:
        assert c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
        b = c.execute("SELECT ok, last_ok FROM source_health WHERE source='B'").fetchone()
        assert b["ok"] == 0 and b["last_ok"] is None
        assert c.execute("SELECT last_new FROM source_health WHERE source='A'").fetchone()[0] == 1
