"""Feed registry. Add or remove sources here; each is a public RSS/Atom feed.

`kind` is "news", "press_release" or "filing" — press releases/announcements get a higher
weight in trend scoring because they're first-party signals.
`region` is a free-form label used for display and filtering.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    region: str
    kind: str = "news"
    fmt: str = "rss"  # "rss" (generic) or "edgar" (SEC current-filings Atom feed)


SOURCES: list[Source] = [
    # Global / US news
    Source("Yahoo Finance", "https://finance.yahoo.com/news/rssindex", "US"),
    Source("MarketWatch Top Stories", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "US"),
    Source("CNBC Markets", "https://www.cnbc.com/id/10000664/device/rss/rss.html", "US"),
    Source("Seeking Alpha Market News", "https://seekingalpha.com/market_currents.xml", "US"),
    Source("Investing.com News", "https://www.investing.com/rss/news.rss", "Global"),
    # Europe / UK
    Source("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", "UK"),
    Source("Guardian Business", "https://www.theguardian.com/uk/business/rss", "UK"),
    Source("Euronews Business", "https://www.euronews.com/rss?level=theme&name=business", "EU"),
    # Asia-Pacific
    Source("Nikkei Asia", "https://asia.nikkei.com/rss/feed/nar", "Asia"),
    Source("SCMP Business", "https://www.scmp.com/rss/92/feed", "Asia"),
    Source("Economic Times Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "India"),
    Source("ABC Australia Business", "https://www.abc.net.au/news/feed/51120/rss.xml", "AU"),
    # Press-release wires (first-party announcements)
    Source("PR Newswire", "https://www.prnewswire.com/rss/financial-services-latest-news/financial-services-latest-news-list.rss", "Global", "press_release"),
    Source("Business Wire", "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeEFpRWQ==", "Global", "press_release"),
    Source("GlobeNewswire", "https://www.globenewswire.com/RssFeed/subjectcode/27-Mergers%20And%20Acquisitions/feedTitle/GlobeNewswire%20-%20Mergers%20And%20Acquisitions", "Global", "press_release"),
    # Official regulatory filings. SEC requires a descriptive User-Agent: set SEC_USER_AGENT.
    Source("SEC EDGAR 8-K", "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&start=0&count=100&output=atom",
           "US", "filing", "edgar"),
]
