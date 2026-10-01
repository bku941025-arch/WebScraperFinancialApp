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
Feeds refresh every `REFRESH_MINUTES` (default 15) while the server runs.

## How it works
- `app/sources.py` – feed registry (news + press-release wires, tagged by region). Add feeds here.
- `app/scraper.py` – concurrent fetch → parse → dedupe by URL → SQLite.
- `app/entities.py` – maps text to tickers via an alias table + `$CASHTAGS`. Extend `COMPANIES`.
- `app/trends.py` – score = recency-decayed mentions (12h half-life), press releases ×1.5,
  boosted by number of distinct sources; `momentum` = last-6h vs prior-6h mentions.
- `app/main.py` + `app/static/index.html` – JSON API and dashboard.

## Notes
Uses public RSS/Atom feeds rather than HTML scraping: stable, and respects site terms.
Some feed URLs may change or require adjustments; failures are logged per-feed and skipped.

## Ideas for next steps
Sentiment scoring, SEC EDGAR 8-K / exchange announcement feeds, full ticker universe
(SEC `company_tickers.json`), price data overlay, alerts (email/Telegram) on spikes.
