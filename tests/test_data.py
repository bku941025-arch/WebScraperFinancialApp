import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import db, demo, earnings, edgar, quotes, scraper, universe
from app.sources import Source

EDGAR_ATOM = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Latest Filings</title>
<entry><title>8-K - APPLE INC (0000320193) (Filer)</title>
<link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/320193/0000320193-26-000001-index.htm"/>
<summary type="html">&lt;b&gt;Filed:&lt;/b&gt; 2026-10-01 &lt;b&gt;AccNo:&lt;/b&gt; 0000320193-26-000001 &lt;br&gt;Item 2.02: Results of Operations&lt;br&gt;Item 9.01: Exhibits</summary>
<updated>2026-10-01T12:30:00-04:00</updated></entry>
<entry><title>8-K - Some Private Trust (0009999999) (Filer)</title>
<link rel="alternate" href="https://www.sec.gov/x/2"/><summary>nothing</summary><updated>2026-10-01T12:31:00-04:00</updated></entry>
<entry><title>garbage title</title><link rel="alternate" href="https://www.sec.gov/x/3"/><updated>2026-10-01T12:31:00-04:00</updated></entry>
</feed>"""
UNIVERSE = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."},
            "2": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
            "3": {"cik_str": 1067983, "ticker": "BRK-A", "title": "BERKSHIRE HATHAWAY INC"}}
NASDAQ = {"data": {"rows": [
    {"symbol": "AAPL", "name": "Apple Inc.", "time": "time-after-hours", "epsForecast": "$1.39",
     "fiscalQuarterEnding": "Sep/2026", "marketCap": "$3,100,000,000,000"},
    {"symbol": "XYZ", "name": "Loss Co", "time": "time-not-supplied", "epsForecast": "($0.12)", "marketCap": "N/A"}]}}
YAHOO = {"chart": {"result": [{"meta": {"currency": "USD", "longName": "Apple Inc.", "regularMarketPrice": 110.0,
                                         "fiftyTwoWeekHigh": 120.0, "fiftyTwoWeekLow": 80.0, "fullExchangeName": "NasdaqGS"},
                               "timestamp": [1790000000, 1790086400, 1790172800],
                               "indicators": {"quote": [{"open": [100, None, 104], "high": [102, None, 111], "low": [99, None, 103],
                                                         "close": [101, None, 110], "volume": [10, None, 30]}]}}], "error": None}}


@pytest.fixture()
def dbfile(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    return tmp_path


def test_universe_and_cikmap(dbfile):
    rows = universe.parse_universe(UNIVERSE)
    assert [r["ticker"] for r in rows] == ["AAPL", "TSLA", "BRK-B", "BRK-A"]
    universe.store(rows)
    with db.session() as c:
        assert universe.cik_map(c)[1067983] == "BRK-B"          # best-ranked share class wins
        assert universe.names(c, ["TSLA", "BRK-A", "ZZZ"]) == {"TSLA": "Tesla", "BRK-A": "Berkshire Hathaway Inc", "ZZZ": "ZZZ"}


def test_search(dbfile):
    universe.store(universe.parse_universe(UNIVERSE))
    with db.session() as c:
        assert universe.search(c, "aapl")[0]["ticker"] == "AAPL"
        assert universe.search(c, "tes")[0]["ticker"] == "TSLA"              # prefix of name/ticker
        assert universe.search(c, "nvidia")[0]["ticker"] == "NVDA"           # curated alias
        assert universe.search(c, "7203")[0]["ticker"] == "7203.T"           # non-US curated
        raw = universe.search(c, "0700.HK")
        assert raw[-1] == {"ticker": "0700.HK", "name": "Look up 0700.HK", "raw": True}
        assert not any(r.get("raw") for r in universe.search(c, "aap"))      # real prefix match -> no "look up" noise
        assert universe.search(c, "%%__;;") == [] and universe.search(c, "") == []


def test_edgar_parse_and_store(dbfile):
    ticks = {320193: "AAPL"}
    items = edgar.parse_edgar(EDGAR_ATOM, ticks, now=1790856000.0)
    assert len(items) == 1                                   # unmapped CIK + garbage skipped
    it = items[0]
    assert it["tickers"] == ["AAPL"] and it["codes"] == ["2.02"]   # 9.01 exhibits dropped
    assert it["title"] == "Apple Inc filed 8-K: Earnings results"
    assert "Earnings results" in it["title"] and "8-K" in it["title"]
    src = Source("SEC EDGAR 8-K", "u", "US", "filing", "edgar")
    parsed = scraper.parse_feed(src, EDGAR_ATOM, now=1790856000.0, cikmap=ticks)
    assert scraper.store(parsed, 1790856000.0) == 1
    with db.session() as c:
        assert c.execute("SELECT ticker FROM mentions").fetchall()[0][0] == "AAPL"
        assert c.execute("SELECT kind FROM articles").fetchone()[0] == "filing"


def test_earnings_parsers_and_queries(dbfile):
    rows = earnings.parse_nasdaq(NASDAQ, "2026-10-15")
    assert rows[0]["time"] == "amc" and rows[0]["eps_est"] == 1.39 and rows[0]["market_cap"] == 3.1e12
    assert rows[1]["eps_est"] == -0.12 and rows[1]["market_cap"] is None and rows[1]["time"] == ""
    fh = earnings.parse_finnhub({"earningsCalendar": [{"symbol": "MSFT", "date": "2026-10-20", "hour": "amc",
                                                       "epsEstimate": 3.1, "quarter": 1, "year": 2027}]})
    assert fh[0]["fiscal"] == "Q1 2027" and fh[0]["time"] == "amc"
    earnings.replace_window(rows, "2026-10-01", "2026-10-31", "nasdaq")
    today = date(2026, 10, 10)
    with db.session() as c:
        c.execute("INSERT INTO articles(url,title,summary,source,region,kind,published_at,fetched_at) VALUES('u','t','','s','US','news',?,?)", (1790856000.0, 1))
        c.execute("INSERT INTO mentions VALUES(1,'AAPL')")
        up = earnings.upcoming(c, 14, today=today, now=1790856000.0 + 3600)
        assert [r["ticker"] for r in up] == ["AAPL", "XYZ"] and up[0]["trending"] and not up[1]["trending"]
        assert [r["ticker"] for r in earnings.upcoming(c, 14, only_trending=True, today=today, now=1790856000.0 + 3600)] == ["AAPL"]
        assert earnings.upcoming(c, 3, today=today) == []                      # outside window
        assert earnings.next_for(c, "AAPL", today)["time_label"] == "After close"
    # rescheduled: AAPL moved -> old row removed, new inserted
    earnings.replace_window([dict(rows[0], date="2026-10-22")], "2026-10-01", "2026-10-31", "nasdaq")
    with db.session() as c:
        assert [r[0] for r in c.execute("SELECT date FROM earnings WHERE ticker='AAPL'")] == ["2026-10-22"]


def test_yahoo_parse_and_summary():
    d = quotes.parse_yahoo(YAHOO)
    assert len(d["candles"]) == 2 and d["candles"][1] == [1790172800, 104, 111, 103, 110, 30]   # null bar dropped
    s = quotes.summarize(d)
    assert s["price"] == 110.0 and s["prev_close"] == 101 and s["change"] == 9.0 and s["change_pct"] == 8.91
    assert (s["year_low"], s["year_high"], s["exchange"], s["name"]) == (80.0, 120.0, "NasdaqGS", "Apple Inc.")
    with pytest.raises(ValueError):
        quotes.parse_yahoo({"chart": {"result": None, "error": {"description": "Not Found"}}})
    assert quotes.yahoo_symbol("btc") == "BTC-USD" and quotes.yahoo_symbol("OPENAI") is None


def test_chart_slicing_cache_and_stale(dbfile, monkeypatch):
    monkeypatch.setattr(quotes, "DEMO", True)
    y, m = (asyncio.run(quotes.get_chart("AAPL", r)) for r in ("1Y", "1M"))
    assert y["source"] == "demo" and 240 <= len(y["candles"]) <= 252 and 15 <= len(m["candles"]) <= 23
    assert asyncio.run(quotes.get_chart("AAPL", "1Y"))["candles"] == y["candles"]            # served from cache
    assert len(asyncio.run(quotes.get_chart("AAPL", "1D"))["candles"]) == 78
    assert asyncio.run(quotes.get_chart("OPENAI", "1M"))["error"]
    # live mode failure with a stale cache entry -> stale data, no error
    monkeypatch.setattr(quotes, "DEMO", False)
    monkeypatch.setattr(quotes, "_download", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    db.cache_put("chart:ZZZ:1Y", dict(meta={}, candles=[[1, 1, 1, 1, 1, 1], [2, 1, 1, 1, 1, 1]], source="yahoo"))
    with db.session() as c:
        c.execute("UPDATE cache SET fetched_at = 0")
    r = asyncio.run(quotes.get_chart("ZZZ", "1Y"))
    assert r["stale"] and len(r["candles"]) == 2 and not r["error"]
    assert asyncio.run(quotes.get_chart("NEVER", "1Y"))["error"]


def test_api_stock_search_earnings(dbfile, monkeypatch):
    monkeypatch.setattr(quotes, "DEMO", True)
    demo.main()
    from app.main import app
    c = TestClient(app)
    assert c.get("/earnings").status_code == 200
    s = c.get("/api/stock/NVDA/summary").json()
    assert s["quote"]["price"] > 0 and s["name"] == "Nvidia" and s["mentions_24h"] > 0 and s["source"] == "demo"
    ch = c.get("/api/stock/NVDA/chart?range=6M").json()
    assert ch["candles"] and len(ch["candles"][0]) == 6
    assert c.get("/api/stock/NVDA/chart?range=10Y").status_code == 422
    assert c.get("/api/stock/bad%20sym!/summary").status_code in (404, 422)
    assert c.get("/api/search?q=nvd").json()[0]["ticker"] == "NVDA"
    e = c.get("/api/earnings?days=30").json()
    assert e and all("time_label" in r for r in e) and len(c.get("/api/earnings?days=30&top=5").json()) == 5
    assert c.get("/api/company/NVDA").json()["next_earnings"] is not None
    st = c.get("/api/status").json()
    assert st["datasets"]["earnings"]["provider"] == "demo" and st["datasets"]["universe"]["count"] > 20
    assert c.get("/api/feed?kind=filing").json()[0]["kind"] == "filing"
