"""Entity extraction: maps article text to companies/tickers.

Uses a curated alias table (fast, no false positives from common words) plus
`$TICKER` cashtag detection for anything outside the table. Extend COMPANIES
or load a bigger list (e.g. from SEC company_tickers.json) for wider coverage.
"""
import re

# ticker -> (display name, [aliases matched case-insensitively as whole words])
COMPANIES: dict[str, tuple[str, list[str]]] = {
    "AAPL": ("Apple", ["Apple", "iPhone", "Tim Cook"]),
    "MSFT": ("Microsoft", ["Microsoft", "Azure", "Satya Nadella"]),
    "GOOGL": ("Alphabet/Google", ["Alphabet", "Google", "YouTube"]),
    "AMZN": ("Amazon", ["Amazon", "AWS", "Andy Jassy"]),
    "META": ("Meta Platforms", ["Meta Platforms", "Facebook", "Instagram", "Zuckerberg"]),
    "NVDA": ("Nvidia", ["Nvidia", "Jensen Huang"]),
    "TSLA": ("Tesla", ["Tesla", "Elon Musk"]),
    "AMD": ("AMD", ["AMD", "Advanced Micro Devices"]),
    "INTC": ("Intel", ["Intel"]),
    "TSM": ("TSMC", ["TSMC", "Taiwan Semiconductor"]),
    "ASML": ("ASML", ["ASML"]),
    "AVGO": ("Broadcom", ["Broadcom"]),
    "ORCL": ("Oracle", ["Oracle"]),
    "NFLX": ("Netflix", ["Netflix"]),
    "JPM": ("JPMorgan", ["JPMorgan", "JP Morgan", "Jamie Dimon"]),
    "GS": ("Goldman Sachs", ["Goldman Sachs", "Goldman"]),
    "MS": ("Morgan Stanley", ["Morgan Stanley"]),
    "BAC": ("Bank of America", ["Bank of America"]),
    "BRK.B": ("Berkshire Hathaway", ["Berkshire Hathaway", "Warren Buffett"]),
    "V": ("Visa", ["Visa Inc"]),
    "WMT": ("Walmart", ["Walmart"]),
    "XOM": ("Exxon Mobil", ["Exxon"]),
    "CVX": ("Chevron", ["Chevron"]),
    "PFE": ("Pfizer", ["Pfizer"]),
    "LLY": ("Eli Lilly", ["Eli Lilly", "Lilly"]),
    "NVO": ("Novo Nordisk", ["Novo Nordisk"]),
    "BA": ("Boeing", ["Boeing"]),
    "DIS": ("Disney", ["Disney"]),
    "COIN": ("Coinbase", ["Coinbase"]),
    "BTC": ("Bitcoin", ["Bitcoin"]),
    "ETH": ("Ethereum", ["Ethereum"]),
    "BABA": ("Alibaba", ["Alibaba"]),
    "TCEHY": ("Tencent", ["Tencent"]),
    "BYDDY": ("BYD", ["BYD"]),
    "9988.HK": ("Alibaba HK", ["Alibaba Group Holding"]),
    "7203.T": ("Toyota", ["Toyota"]),
    "6758.T": ("Sony", ["Sony"]),
    "005930.KS": ("Samsung", ["Samsung"]),
    "RELIANCE.NS": ("Reliance Industries", ["Reliance Industries", "Mukesh Ambani"]),
    "TCS.NS": ("Tata Consultancy", ["Tata Consultancy", "TCS"]),
    "SAP": ("SAP", ["SAP SE"]),
    "SHEL": ("Shell", ["Shell plc", "Shell PLC"]),
    "HSBC": ("HSBC", ["HSBC"]),
    "BP": ("BP", ["BP plc", "BP PLC"]),
    "AZN": ("AstraZeneca", ["AstraZeneca"]),
    "LVMUY": ("LVMH", ["LVMH"]),
    "NSRGY": ("Nestlé", ["Nestle", "Nestlé"]),
    "OPENAI": ("OpenAI", ["OpenAI", "ChatGPT", "Sam Altman"]),
}

CASHTAG_RE = re.compile(r"\$([A-Z]{1,5})\b")


def _compile() -> list[tuple[str, re.Pattern]]:
    out = []
    for ticker, (_, aliases) in COMPANIES.items():
        # Case-sensitive for short all-caps aliases (avoids "amd"/"bp" noise), else insensitive.
        parts = []
        for a in aliases:
            flag = "" if (a.isupper() and len(a) <= 4) else "(?i:"
            parts.append(f"{flag}\\b{re.escape(a)}\\b{')' if flag else ''}")
        out.append((ticker, re.compile("|".join(parts))))
    return out


_PATTERNS = _compile()


def extract_entities(text: str) -> set[str]:
    """Return a set of tickers mentioned in `text`."""
    found = {t for t, p in _PATTERNS if p.search(text)}
    found.update(CASHTAG_RE.findall(text))
    return found


def display_name(ticker: str) -> str:
    return COMPANIES.get(ticker, (ticker, []))[0]
