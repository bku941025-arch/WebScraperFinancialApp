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
| `/chat` | **FinTrend AI** — streaming chat assistant grounded in the app's own data (trends, prices & technicals, news, filings, earnings) |
| `/status` | Next scheduled runs (live countdown), per-source health, run history, "Run now" |

Click any trending row on the dashboard to expand it into a price chart. The search box in the top bar (press `/`) finds
any ticker or company and previews its price, ranges, volume, news count and next earnings; Enter opens the full page.
Light/dark theme toggle (top right) remembers your choice.

## Assistant (`/chat`) — works with no AI and no key
Ask things like "what looks bullish right now?", "give me a full read on $NVDA", "compare $AAPL and $MSFT", "who reports
earnings this week?" or "what is RSI?". Answers stream in with tool-activity chips, Markdown tables and clickable `$TICKER`
links. Three interchangeable engines share the same tools (trending companies, price action + technicals, company news and
8-K filings, keyword search, earnings calendar, market overview) and the same UI — pick one with `FINTREND_CHAT`:

| `FINTREND_CHAT` | Engine | Cost | Best for |
|---|---|---|---|
| `rules` (default) | **Built-in, no AI.** Matches your question to a fixed set of intents (what's trending/bullish/bearish by region and window, a company read, comparisons, earnings, filings, news, market overview, a *"should I buy…?"* signal scorecard, ~20 glossary terms) and writes the answer from templates over live data | Free, instant, offline | Everyday questions; zero setup |
| `ollama` | A **local open-source model** via [Ollama](https://ollama.com) with tool calling. `ollama pull llama3.1`, then set `FINTREND_OLLAMA_MODEL` (default `llama3.1`) and optionally `OLLAMA_HOST` (default `http://localhost:11434`) | Free per question; needs a machine with roughly 8 GB+ free RAM | Free-form questions, nothing leaves your computer |
| `claude` | The **Claude API** (`ANTHROPIC_API_KEY`; `FINTREND_MODEL`, default `claude-opus-5-5`, or `claude-sonnet-5-5` for cheaper/faster) | API credits | Best answers; optional web search (below) |

If `FINTREND_CHAT` is unset it uses Claude when an Anthropic key is present, otherwise the built-in assistant. If the chosen engine
isn't usable (Ollama not running or model missing, no key), the answer falls back to the built-in assistant and the page shows why.
The badge next to the title shows which engine is active.

- **Built-in assistant limits:** it only understands the question types above (it says so, and lists them, when it doesn't);
  it never invents figures — everything comes from the tools or the fixed glossary. The "should I buy…?" scorecard counts
  simple signals (price vs 50/200-day averages, 3-month return, RSI, distance from the 52-week high, news tone) — it is a
  summary of data, not advice. Small local models are weaker than Claude at analysis and occasionally misuse tools.
- **Claude web search (opt-in, off by default):** set `FINTREND_WEB_SEARCH=1` and a **🌐 Web** toggle appears in the composer.
  When on for a question, Claude may use server-side web search for things the feeds don't cover and the answer shows a **Sources**
  row. Capped at `FINTREND_WEB_MAX_USES` searches (default 4); searches are billed per use on top of tokens. Web text is untrusted.
- **Safeguards (all engines):** tool results are treated as untrusted text, a per-IP rate limit (20 questions / 10 min), a cap on
  tool rounds per question, any API key stays on the server, and chat history lives only in your browser (localStorage; "New chat" clears it).
- **Claude only:** requests use `claude-opus-5-5`'s recommended settings (medium effort, server-side refusal fallback).

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
