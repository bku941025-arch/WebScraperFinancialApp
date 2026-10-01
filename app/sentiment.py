"""Tiny keyword-based headline tone. Deliberately simple and transparent — a rough
colour signal for the UI, not a trading signal."""
import re

POS = {"surge", "surges", "soar", "soars", "jump", "jumps", "rally", "rallies", "beat", "beats",
       "record", "gain", "gains", "rise", "rises", "upgrade", "upgrades", "growth", "profit",
       "boost", "climbs", "outperform", "strong", "wins", "approval", "approved", "expands",
       "launches", "unveils", "partnership", "acquires", "buyback", "dividend"}
NEG = {"plunge", "plunges", "fall", "falls", "drop", "drops", "slump", "slumps", "miss", "misses",
       "cut", "cuts", "downgrade", "downgrades", "loss", "losses", "lawsuit", "probe", "recall",
       "layoffs", "fraud", "decline", "declines", "tumble", "tumbles", "weak", "warns", "warning",
       "bankruptcy", "investigation", "fine", "fined", "halts", "delays", "sinks"}
_WORD = re.compile(r"[a-z']+")


def score(text: str) -> float:
    """-1 (all negative words) .. +1 (all positive words); 0 if no signal."""
    words = _WORD.findall(text.lower())
    p = sum(w in POS for w in words)
    n = sum(w in NEG for w in words)
    return (p - n) / (p + n) if p + n else 0.0
