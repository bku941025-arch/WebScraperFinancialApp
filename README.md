# FinTrend

Collects financial news and press releases from feeds around the world and surfaces the
companies/tickers that are trending right now.

## Run
```
pip install -r requirements.txt
python -m uvicorn app.main:app --reload     # dashboard at http://localhost:8000
python -m app.scraper                       # one-off fetch from the CLI
pytest
```

## Data sources (all free, no key required)
| Data | Source | Notes |
|---|---|---|
| News / press releases | RSS feeds (`app/sources.py`) | per-feed failures are skipped and shown on Status |
| 8-K filings | SEC EDGAR "current events" Atom feed | **Set `SEC_USER_AGENT="YourApp you@example.com"`** — the SEC requires a contact in the User-Agent |
| Ticker universe / search | SEC `company_tickers.json` | refreshed weekly; also maps filings (CIK) to tickers |
| Earnings calendar | Nasdaq public calendar (unofficial) — or **Finnhub** if `FINNHUB_API_KEY` is set (free key, official) | refreshed on every scheduled scrape, next 21 days |
| Prices & charts | Yahoo Finance chart endpoint (unofficial) | cached 3–30 min; stale copy served if a refresh fails |

The Nasdaq and Yahoo endpoints are unofficial and can change or rate-limit. Anything unavailable degrades to an
empty state rather than an error; the Status page shows what loaded.

### Try the UI without live feeds
```
FINTREND_DB=demo.db python -m app.demo                                   # synthetic data
FINTREND_DB=demo.db FINTREND_DEMO=1 DISABLE_SCHEDULER=1 python -m uvicorn app.main:app
```

## Pages
| Path | What it shows |
|---|---|
| `/` | Dashboard: top-3 spotlight, ranked trends with sparklines, expandable headlines, rising-fast, latest press releases, ticker tape |
| `/earnings` | Quarterly earnings calendar: grouped by day, before-open/after-close, EPS estimate, "trending" flag, day jump chips |
| `/markets` | Trend "bubble cloud" + per-region breakdown |
| `/feed` | Searchable feed of news, press releases and SEC 8-K filings with region and time filters |
| `/company/{ticker}` | Price chart (line/candles, 1D–1Y), stock summary, next earnings, mentions-over-time, sources, regions, related companies |
| `/status` | Next scheduled runs (live countdown), per-source health, run history, "Run now" |

Click any trending row on the dashboard to expand it into a price chart. The search box in the top bar (press `/`) finds
any ticker or company and previews its price, ranges, volume, news count and next earnings; Enter opens the full page.
Light/dark theme toggle (top right) remembers your choice.

## Schedule: 3 scrapes per trading day
Instead of constant polling, feeds are collected **just after the open (+5 min), at mid-session,
and shortly before the close (-15 min)**, in the exchange's own timezone (DST-aware, weekends
skipped; exchange holidays are not modelled). Default is the US session (09:35 / 12:45 / 15:45
New York = exactly 3 runs a day). To also cover other regions:
```
SCHEDULE_MARKETS=US,EU,ASIA python -m uvicorn app.main:app   # 9 runs/day
```
On startup the server also scrapes once if the last run is more than 6 hours old, so a fresh
install isn't empty. The scheduler only runs while the server process is up.

## How it works
- `app/sources.py` – feed registry (news + press-release wires, tagged by region). Add feeds here.
- `app/scraper.py` – concurrent fetch → parse → dedupe by URL → SQLite.
- `app/entities.py` – maps text to tickers via an alias table + `$CASHTAGS`. Extend `COMPANIES`.
- `app/trends.py` – score = recency-decayed mentions (12h half-life), press releases ×1.5,
  boosted by number of distinct sources; `momentum` = last-6h vs prior-6h mentions.
- `app/scheduler.py` – open / midday / close run times per market.
- `app/sentiment.py` – tiny keyword headline-tone signal (colour only, not advice).
- `app/main.py` + `app/static/*` – JSON API and the pages.

## Notes
Uses public RSS/Atom feeds rather than HTML scraping: stable, and respects site terms.
Some feed URLs may change or require adjustments; failures are logged per-feed and skipped.

## Ideas for next steps
Better sentiment, non-US exchange announcement feeds, news markers on price charts, alerts (email/Telegram) on spikes.
