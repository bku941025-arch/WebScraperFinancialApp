"""The assistant's question bank — what users can ask from the dashboard.

Single source of truth for the UI (served at /api/chat/questions) and for tests, which check that every
question is understood by the built-in assistant (so the menu and the matcher can't drift apart).

`text` may contain slots {A} / {B}: the UI collects the company first, then substitutes `$TICKER`.
Slot questions also carry `label` / `icon` / `hint` — they become the action tiles on a company's page.
`expect` is the intent the built-in assistant should route the question to.
"""


def q(text: str, expect: str, featured: bool = False, label: str = "", icon: str = "", hint: str = "") -> dict:
    slots = [s for s in ("A", "B") if "{" + s + "}" in text]
    return dict(text=text, expect=expect, slots=slots, featured=featured, label=label, icon=icon, hint=hint)


BANK = [
    dict(id="pulse", icon="🔥", title="What's moving", blurb="Trending, bullish and bearish names", questions=[
        q("What looks bullish right now?", "trending", True),
        q("Anything bearish or concerning right now?", "trending", True),
        q("What's trending in the US today?", "trending"),
        q("What's trending in Asia this week?", "trending"),
        q("What's trending in Europe today?", "trending"),
        q("How are markets doing around the world?", "overview"),
    ]),
    dict(id="company", icon="🔍", title="Look up a company", blurb="Price action, news, earnings, filings", questions=[
        q("Give me a full read on {A}", "read", label="Full read", icon="📊", hint="Price, trend, RSI and news"),
        q("What's the latest news on {A}?", "news", label="Latest news", icon="📰", hint="Headlines and tone"),
        q("When does {A} report earnings?", "earnings", label="Next earnings", icon="📅", hint="Date and estimate"),
        q("Any recent SEC filings from {A}?", "filings", label="SEC filings", icon="🏛️", hint="Recent 8-K reports"),
        q("Should I buy {A}?", "advice", label="Buy signals", icon="🧮", hint="A scorecard, not advice"),
        q("Compare {A} with {B}", "compare", label="Compare", icon="⚖️", hint="Side by side with another company"),
    ]),
    dict(id="calendar", icon="📅", title="Earnings & filings", blurb="Who reports soon and notable 8-Ks", questions=[
        q("Who reports earnings this week?", "earnings", True),
        q("Which earnings are worth watching?", "earnings"),
        q("Who reports earnings tomorrow?", "earnings"),
        q("Any notable 8-K filings today?", "filings"),
        q("What does an 8-K Item 2.02 mean?", "glossary"),
    ]),
    dict(id="learn", icon="🎓", title="Learn the basics", blurb="RSI, moving averages, P/E and more", questions=[
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
