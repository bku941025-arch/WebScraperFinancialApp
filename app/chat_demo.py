"""Scripted stand-in for the model, used ONLY in FINTREND_DEMO mode with no API key configured.

It calls the real tools against the local (synthetic) data and formats the results — no AI model is
involved, which the reply says. It exists so the chat UI and the whole tool/stream pipeline can be
explored and tested offline. Mimics the slice of the SDK stream interface that app.chat uses.
"""
import asyncio
import json
import re
from types import SimpleNamespace as NS

NOTE = "*Demo mode — scripted reply composed from the local synthetic data; no AI model was called. Set `ANTHROPIC_API_KEY` for real answers.*\n\n"


def _pick(question: str) -> tuple[str, dict]:
    q = question.lower()
    m = re.search(r"\$?\b([A-Z]{2,5})\b", question)
    if "earning" in q:
        return "get_upcoming_earnings", {"days": 10}
    if m and m.group(1) not in {"AI", "THE", "ETF", "USD", "CEO", "IPO"} and not any(w in q for w in ("trend", "bullish", "hot")):
        return "get_stock_snapshot", {"symbol": m.group(1)}
    if any(w in q for w in ("trend", "bullish", "hot", "moving", "market")):
        return "get_trending_companies", {"limit": 5}
    return "search_news", {"query": question[:60], "limit": 5}


def _compose(tool: str, data) -> str:
    if isinstance(data, dict) and data.get("error"):
        return f"I couldn’t get that data: {data['error']}."
    if tool == "get_trending_companies":
        rows = "\n".join(f"| ${r['ticker']} | {r['name']} | {r['trend_score']} | {r['mentions']} | {r['momentum']:+d} | {r['tone']:+.2f} |" for r in data)
        return f"Here is what is trending in the collected news right now:\n\n| Ticker | Company | Score | Mentions | Momentum | Tone |\n|---|---|---|---|---|---|\n{rows}\n\n" \
               f"**Read:** ${data[0]['ticker']} leads on score; positive momentum with positive tone is the bullish combination to look for. " \
               "Headline tone is a crude keyword signal, so check price action before drawing conclusions."
    if tool == "get_stock_snapshot":
        q, t = data.get("quote"), data.get("technicals") or {}
        if not q:
            return f"No price data for ${data['symbol']} ({data.get('price_error')})."
        e = data.get("next_earnings")
        return (f"**${data['symbol']} — {data['name']}**\n\n- Price **{q['price']} {q['currency']}** ({q['change_pct']:+.2f}% on the day)\n"
                f"- 52-week range {q['year_low']} – {q['year_high']}; 1M return {t.get('ret_1m_pct')}%, RSI(14) {t.get('rsi14')}\n"
                f"- vs 50-day average: {t.get('vs_sma50_pct')}%, vs 200-day: {t.get('vs_sma200_pct')}%\n"
                f"- {data['news_mentions_24h']} news mentions in 24h" + (f"; next earnings {e['date']} ({e['when']})" if e else "") + ".")
    if tool == "get_upcoming_earnings":
        rows = "\n".join(f"- {r['date']} — **${r['ticker']}** ({r['when']}), EPS est. {r['eps_estimate']}" + (" · in the news" if r["in_the_news"] else "") for r in data[:8])
        return "Upcoming earnings:\n\n" + (rows or "Nothing scheduled in that window.")
    rows = "\n".join(f"- {r['title']} — *{r['source']}*" for r in data.get("results", []))
    return "Matching headlines:\n\n" + (rows or "No matches in the collected news.")


class _Stream:
    def __init__(self, messages):
        self.messages, self.final = messages, None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def __aiter__(self):
        last = self.messages[-1]
        usage = NS(input_tokens=0, output_tokens=0, cache_read_input_tokens=0)
        if isinstance(last["content"], str):  # fresh question -> call a tool
            name, args = _pick(last["content"])
            blk = NS(type="tool_use", id="toolu_demo", name=name, input=args)
            self.final = NS(content=[blk], stop_reason="tool_use", usage=usage)
            yield NS(type="content_block_start", content_block=blk)
            await asyncio.sleep(0.4)
            return
        res = last["content"][0]
        tool = next(b.name for b in self.messages[-2]["content"] if b.type == "tool_use")
        text = NOTE + _compose(tool, json.loads(res["content"]))
        for i in range(0, len(text), 14):
            yield NS(type="text", text=text[i:i + 14])
            await asyncio.sleep(0.012)
        self.final = NS(content=[NS(type="text", text=text)], stop_reason="end_turn", usage=usage)

    async def get_final_message(self):
        return self.final


class DemoClient:
    def __init__(self):
        self.beta = NS(messages=NS(stream=lambda **kw: _Stream(kw["messages"])))
