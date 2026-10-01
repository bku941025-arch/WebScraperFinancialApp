"""Seed a *synthetic* database so the UI can be explored without live feeds.

    FINTREND_DB=demo.db python -m app.demo
    FINTREND_DB=demo.db FINTREND_DEMO=1 DISABLE_SCHEDULER=1 python -m uvicorn app.main:app

FINTREND_DEMO=1 also makes price charts synthetic (clearly labelled in the UI).

Headlines are made up for demonstration only.
"""
import random
import time
from datetime import timedelta

from . import earnings, scraper, universe
from . import db
from .db import session
from .sources import SOURCES

HOT = {  # ticker: (company text, weight) — weight skews how often it appears
    "NVDA": ("Nvidia", 10), "TSLA": ("Tesla", 8), "AAPL": ("Apple", 7), "MSFT": ("Microsoft", 6),
    "7203.T": ("Toyota", 4), "005930.KS": ("Samsung", 4), "BABA": ("Alibaba", 5), "SAP": ("SAP SE", 3),
    "AZN": ("AstraZeneca", 3), "JPM": ("JPMorgan", 5), "BTC": ("Bitcoin", 6), "OPENAI": ("OpenAI", 6),
    "AMZN": ("Amazon", 5), "META": ("Meta Platforms", 4), "NVO": ("Novo Nordisk", 4), "TSM": ("TSMC", 4),
    "RELIANCE.NS": ("Reliance Industries", 3), "SHEL": ("Shell plc", 2), "BA": ("Boeing", 3),
    "LLY": ("Eli Lilly", 3), "ASML": ("ASML", 3), "HSBC": ("HSBC", 2),
}
NEWS = ["{c} shares surge after analysts upgrade outlook", "{c} warns of weaker demand as costs rise",
        "{c} unveils new product line, investors cheer", "{c} faces probe over disclosures",
        "Why {c} is on every trader's watchlist today", "{c} beats earnings estimates on strong growth",
        "{c} announces partnership to expand in Asia", "{c} stock falls as rivals gain ground",
        "Options traders pile into {c} ahead of results", "{c} CEO says outlook remains strong"]
PR = ["{c} announces record quarterly revenue", "{c} to acquire AI startup in all-cash deal",
      "{c} declares quarterly dividend and new buyback", "{c} launches next-generation platform",
      "{c} announces leadership transition", "{c} files for approval of new product"]


def main(seed: int = 7) -> None:
    rnd, now = random.Random(seed), time.time()
    names = list(HOT)
    items = []
    for i in range(520):
        t = rnd.choices(names, [HOT[n][1] for n in names])[0]
        src = rnd.choice([x for x in SOURCES if x.fmt != "edgar"])
        # more mentions in recent hours, with a few "breaking" bursts
        age_h = min(71.9, rnd.expovariate(1 / 14)) if rnd.random() > 0.15 else rnd.uniform(0, 5)
        title = rnd.choice(PR if src.kind == "press_release" else NEWS).format(c=HOT[t][0])
        if rnd.random() < 0.25:  # co-mentions feed the "related companies" view
            title += f" while {HOT[rnd.choice(names)][0]} lags"
        items.append(dict(url=f"https://example.com/demo/{i}", title=title,
                          summary=f"Demo article about {HOT[t][0]}. Synthetic data for UI preview.",
                          source=src.name, region=src.region, kind=src.kind,
                          published_at=now - age_h * 3600))
    # SEC-style 8-K filings with item labels (tickers resolved directly)
    codes = [("2.02", "Earnings results"), ("5.02", "Executive / director change"), ("1.01", "Material agreement"),
             ("8.01", "Other events"), ("7.01", "Reg FD disclosure"), ("2.01", "Acquisition / disposal completed")]
    for i in range(70):
        t = rnd.choices(names, [HOT[n][1] for n in names])[0]
        code, label = rnd.choice(codes)
        items.append(dict(url=f"https://example.com/demo/8k/{i}", title=f"{HOT[t][0]} filed 8-K: {label}",
                          summary=f"Items: {code} {label}", source="SEC EDGAR 8-K", region="US", kind="filing",
                          published_at=now - rnd.uniform(0, 60) * 3600, tickers=[t]))
    scraper.store(items, now)
    # demo ticker universe (powers search) + earnings calendar
    extra = ["Palantir Technologies", "Snowflake Inc.", "Uber Technologies", "Airbnb, Inc.", "Shopify Inc.", "Salesforce, Inc.",
             "Adobe Inc.", "Intel Corporation", "Qualcomm Incorporated", "Micron Technology", "Cisco Systems", "Costco Wholesale"]
    tick = ["PLTR", "SNOW", "UBER", "ABNB", "SHOP", "CRM", "ADBE", "INTC", "QCOM", "MU", "CSCO", "COST"]
    universe.store([dict(ticker=t, name=n, cik=1000 + i, rank=i) for i, (t, n) in enumerate(zip(tick, extra))]
                   + [dict(ticker=t, name=HOT[t][0], cik=2000 + i, rank=20 + i) for i, t in enumerate(HOT) if "." not in t])
    today = earnings.today_et()
    rows, pool = [], list(HOT) + tick
    for i, t in enumerate(pool):
        d = today + timedelta(days=rnd.randint(0, 16))
        while d.weekday() >= 5:
            d += timedelta(days=1)
        rows.append(dict(ticker=t, date=d.isoformat(), name=HOT[t][0] if t in HOT else extra[tick.index(t)],
                         time=rnd.choice(["bmo", "amc", "amc", ""]), eps_est=round(rnd.uniform(.2, 4.5), 2),
                         fiscal=f"Q3 {today.year}", market_cap=rnd.uniform(5e9, 3e12)))
    earnings.replace_window(rows, today.isoformat(), (today + timedelta(days=21)).isoformat(), "demo")
    db.cache_put("earnings_meta", dict(provider="demo", rows=len(rows), failed_days=0))
    with session() as conn:
        for k, (label, n) in enumerate([("US open", 38), ("US midday", 21), ("US close", 17), ("manual", 9)]):
            fin = now - (k * 5.5 + 1) * 3600
            conn.execute("INSERT INTO runs(started_at,finished_at,trigger,new_articles,feeds_ok,feeds_failed)"
                         " VALUES(?,?,?,?,?,?)", (fin - 12, fin, label, n, len(SOURCES) - (k == 1), int(k == 1)))
        for j, s in enumerate(SOURCES):
            ok = j != 3
            conn.execute("INSERT OR REPLACE INTO source_health VALUES(?,?,?,?,?)",
                         (s.name, now - 3600, now - 3600 if ok else now - 86400, int(ok), rnd.randint(0, 14)))
    print(f"seeded {len(items)} synthetic articles, {len(rows)} earnings dates")


if __name__ == "__main__":
    main()
