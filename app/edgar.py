"""SEC EDGAR "current events" Atom feed → article items for 8-K filings.

Entries look like:  title   "8-K - Apple Inc. (0000320193) (Filer)"
                    summary "<b>Filed:</b> 2026-10-01 <b>AccNo:</b> ... <br>Item 2.02: Results of ..."
Filings whose CIK can't be mapped to a ticker (funds, trusts, shells) are skipped.
"""
import calendar
import re
import time

import feedparser

ITEM_LABELS = {
    "1.01": "Material agreement", "1.02": "Agreement terminated", "1.03": "Bankruptcy",
    "2.01": "Acquisition / disposal completed", "2.02": "Earnings results", "2.03": "New debt obligation",
    "2.05": "Restructuring costs", "2.06": "Material impairment", "3.01": "Delisting notice",
    "3.02": "Unregistered share sale", "4.01": "Auditor change", "4.02": "Financials non-reliance",
    "5.01": "Change in control", "5.02": "Executive / director change", "5.03": "Bylaws amended",
    "5.07": "Shareholder vote", "7.01": "Reg FD disclosure", "8.01": "Other events", "9.01": "Exhibits",
}
TITLE_RE = re.compile(r"^(?P<form>.+?) - (?P<name>.+?) \((?P<cik>\d{6,10})\) \((?P<role>[^)]*)\)\s*$")
ITEM_RE = re.compile(r"Item\s+(\d\.\d{2})")
SKIP_ITEMS = {"9.01"}  # exhibits-only isn't a headline


def parse_edgar(content: bytes, cikmap: dict[int, str], now: float | None = None) -> list[dict]:
    now = now or time.time()
    items = []
    for e in feedparser.parse(content).entries:
        m = TITLE_RE.match((e.get("title") or "").strip())
        url = e.get("link")
        if not m or not url:
            continue
        ticker = cikmap.get(int(m["cik"]))
        if not ticker:
            continue
        codes = [c for c in dict.fromkeys(ITEM_RE.findall(e.get("summary") or "")) if c not in SKIP_ITEMS]
        what = ", ".join(ITEM_LABELS.get(c, f"Item {c}") for c in codes[:3])
        title = f"{m['name'].title() if m['name'].isupper() else m['name']} filed {m['form']}" + (f": {what}" if what else "")
        ts = e.get("published_parsed") or e.get("updated_parsed")
        items.append(dict(
            url=url, title=title,
            summary=("Items: " + ", ".join(f"{c} {ITEM_LABELS.get(c, '')}".strip() for c in codes)) if codes else "",
            published_at=min(float(calendar.timegm(ts)), now) if ts else now, tickers=[ticker], codes=codes))
    return items
