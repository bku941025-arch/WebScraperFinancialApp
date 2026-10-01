"""The assistant's question bank — the menu users pick from on the dashboard.

Single source of truth for the UI (served at /api/chat/questions) and for tests, which check that every
question is understood by the built-in assistant (so the menu and the matcher can't drift apart).

`text` may contain slots {A} / {B} — the UI renders them as company dropdowns and substitutes `$TICKER`.
`expect` is the intent the built-in assistant should route the question to.
"""


def q(text: str, expect: str, featured: bool = False) -> dict:
    slots = [s for s in ("A", "B") if "{" + s + "}" in text]
    return dict(text=text, expect=expect, slots=slots, featured=featured)


BANK = [
    dict(id="pulse", icon="🔥", title="Market pulse", questions=[
        q("What looks bullish right now?", "trending", True),
        q("Anything bearish or concerning right now?", "trending", True),
        q("What's trending in the US today?", "trending"),
        q("What's trending in Asia this week?", "trending"),
        q("What's trending in Europe today?", "trending"),
        q("How are markets doing around the world?", "overview"),
    ]),
    dict(id="company", icon="🔍", title="Companies", questions=[
        q("Give me a full read on {A}", "read"),
        q("Should I buy {A}?", "advice"),
        q("What's the latest news on {A}?", "news"),
        q("When does {A} report earnings?", "earnings"),
        q("Any recent SEC filings from {A}?", "filings"),
        q("Compare {A} with {B}", "compare"),
    ]),
    dict(id="calendar", icon="📅", title="Earnings & filings", questions=[
        q("Who reports earnings this week?", "earnings", True),
        q("Which earnings are worth watching?", "earnings"),
        q("Who reports earnings tomorrow?", "earnings"),
        q("Any notable 8-K filings today?", "filings"),
        q("What does an 8-K Item 2.02 mean?", "glossary"),
    ]),
    dict(id="learn", icon="🎓", title="Learn", questions=[
        q("What is RSI?", "glossary"),
        q("What is the 200-day moving average?", "glossary"),
        q("What does P/E mean?", "glossary"),
        q("What is market cap?", "glossary"),
        q("What is a short squeeze?", "glossary"),
        q("How do interest rates affect stocks?", "glossary"),
        q("What is the difference between a bull and bear market?", "glossary"),
        q("What does the trend score mean?", "glossary"),
    ]),
]
