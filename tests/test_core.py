import time

from app import db, scraper, trends
from app.entities import extract_entities
from app.sources import Source

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Nvidia unveils new chip as Tesla shares jump</title><link>http://x/1</link>
<description>&lt;b&gt;AI&lt;/b&gt; demand</description><pubDate>Wed, 01 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Nvidia beats estimates</title><link>http://x/2</link><pubDate>Wed, 01 Oct 2026 11:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_entities():
    assert extract_entities("Nvidia and Tesla rally; watch $PLTR") == {"NVDA", "TSLA", "PLTR"}
    assert "AMD" not in extract_entities("a tamd value")
    assert "BP" not in extract_entities("bp readings")


def test_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    now = 1790856000.0  # 2026-10-01 12:00 UTC
    pr = Source("Wire", "u", "Global", "press_release")
    news = Source("News", "u", "US")
    assert scraper.store(scraper.parse_feed(pr, RSS, now), now) == 2
    assert scraper.store(scraper.parse_feed(news, RSS, now), now) == 0  # dedup by url
    with db.session() as conn:
        t = trends.top_trends(conn, now=now)
        assert t[0]["ticker"] == "NVDA" and t[0]["mentions"] == 2 and t[0]["press_releases"] == 2
        assert {x["ticker"] for x in t} == {"NVDA", "TSLA"}
        assert len(trends.latest_announcements(conn, now=now)) == 2
