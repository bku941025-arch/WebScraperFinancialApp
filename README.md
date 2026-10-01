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

### Try the UI without live feeds
```
FINTREND_DB=demo.db python -m app.demo                                   # synthetic data
FINTREND_DB=demo.db DISABLE_SCHEDULER=1 python -m uvicorn app.main:app
```

## Pages
| Path | What it shows |
|---|---|
| `/` | Dashboard: top-3 spotlight, ranked trends with sparklines, expandable headlines, rising-fast, latest press releases, ticker tape |
| `/markets` | Trend "bubble cloud" + per-region breakdown |
| `/feed` | Searchable news / press-release feed with region and time filters |
| `/company/{ticker}` | Mentions-over-time chart, sources, regions, co-mentioned companies, coverage |
| `/status` | Next scheduled runs (live countdown), per-source health, run history, "Run now" |

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
Better sentiment, SEC EDGAR 8-K / exchange announcement feeds, full ticker universe
(SEC `company_tickers.json`), price data overlay, alerts (email/Telegram) on spikes.
