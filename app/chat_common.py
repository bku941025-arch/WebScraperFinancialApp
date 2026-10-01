"""Pieces shared by the chat providers (rules / Ollama / Claude)."""
import asyncio
import json

MAX_HISTORY = 24  # messages kept from the browser's history


def sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def clean_history(raw: list[dict]) -> list[dict]:
    msgs = [dict(role=m["role"], content=m["content"].strip()) for m in raw if m["content"].strip()][-MAX_HISTORY:]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


async def typewriter(text: str, step: int = 18, delay: float = 0.008):
    """Yield `delta` events for already-complete text so rule-based answers stream like a model's."""
    for i in range(0, len(text), step):
        yield sse(dict(type="delta", text=text[i:i + step]))
        await asyncio.sleep(delay)
