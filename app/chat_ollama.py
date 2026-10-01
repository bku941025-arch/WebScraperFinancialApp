"""Local-model provider via Ollama (https://ollama.com): free per question, nothing leaves your machine.

Uses Ollama's native /api/chat with streaming and tool calling. Pick a model that supports tools,
e.g. `ollama pull llama3.1` or `qwen2.5`. Config: OLLAMA_HOST (default http://localhost:11434),
FINTREND_OLLAMA_MODEL (default llama3.1).
"""
import json
import logging
import os
import time

import httpx

from . import chat_tools
from .chat_common import sse

log = logging.getLogger("chat.ollama")

HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
if not HOST.startswith("http"):
    HOST = "http://" + HOST
MODEL = os.getenv("FINTREND_OLLAMA_MODEL", "llama3.1")
MAX_ITERATIONS = 6
CHECK_TTL = 15.0
_cache: dict = {"at": 0.0, "val": (False, "")}

LOCAL_NOTE = """
Answer style for this session: be concise. Call at most two or three tools, then answer — do not keep calling tools. \\
Use only numbers that appear in tool results. Write in Markdown."""


def _tools() -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
            for t in chat_tools.TOOLS]


def _has_model(names: list[str], model: str) -> bool:
    base = model.split(":")[0]
    return any(n == model or (":" not in model and n.split(":")[0] == base) for n in names)


async def check(force: bool = False, transport: httpx.AsyncBaseTransport | None = None) -> tuple[bool, str]:
    """Is Ollama reachable and is the model installed? Cached briefly so each question doesn't pay for it."""
    if not force and time.time() - _cache["at"] < CHECK_TTL:
        return _cache["val"]
    try:
        async with httpx.AsyncClient(timeout=1.5, trust_env=False, transport=transport) as c:
            r = await c.get(f"{HOST}/api/tags")
            r.raise_for_status()
            names = [m.get("name", "") for m in r.json().get("models", [])]
        val = (True, "") if _has_model(names, MODEL) else (False, f"Ollama is running but the model “{MODEL}” isn’t installed (run: ollama pull {MODEL}).")
    except Exception:
        val = (False, f"Couldn’t reach Ollama at {HOST}.")
    _cache.update(at=time.time(), val=val)
    return val


def _args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


async def stream(history: list[dict], system: str, transport: httpx.AsyncBaseTransport | None = None):
    """Async generator of SSE strings. `transport` lets tests inject a mock server."""
    messages = [{"role": "system", "content": system + LOCAL_NOTE}] + history
    usage = dict(input=0, output=0, cache_read=0, rounds=0)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=5), trust_env=False, transport=transport) as client:
            for rnd in range(MAX_ITERATIONS):
                text, calls = "", []
                body = {"model": MODEL, "messages": messages, "tools": _tools(), "stream": True, "options": {"temperature": 0.3}}
                async with client.stream("POST", f"{HOST}/api/chat", json=body) as r:
                    if r.status_code != 200:
                        detail = (await r.aread()).decode("utf-8", "replace")[:200]
                        yield sse(dict(type="error", message=f"Ollama returned {r.status_code}: {detail or 'error'}"))
                        return
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        obj = json.loads(line)
                        msg = obj.get("message") or {}
                        if msg.get("content"):
                            text += msg["content"]
                            yield sse(dict(type="delta", text=msg["content"]))
                        for tc in msg.get("tool_calls") or []:
                            calls.append(tc.get("function") or {})
                        if obj.get("done"):
                            usage["input"] += obj.get("prompt_eval_count", 0) or 0
                            usage["output"] += obj.get("eval_count", 0) or 0
                usage["rounds"] += 1
                if not calls:
                    break
                messages.append({"role": "assistant", "content": text, "tool_calls": [{"function": f} for f in calls]})
                for i, f in enumerate(calls):
                    tid, name = f"ollama-{rnd}-{i}", f.get("name", "")
                    yield sse(dict(type="tool_start", id=tid, name=name, label=chat_tools.LABELS.get(name, "Working")))
                    result, is_err = await chat_tools.run_tool(name, _args(f.get("arguments")))
                    yield sse(dict(type="tool_done", id=tid, name=name, ok=not is_err))
                    messages.append({"role": "tool", "tool_name": name, "content": result})
            else:
                yield sse(dict(type="notice", text="I stopped after several lookups — ask a narrower question to go deeper."))
        yield sse(dict(type="done", usage=usage))
    except httpx.ConnectError:
        _cache["at"] = 0.0
        yield sse(dict(type="error", message=f"Lost connection to Ollama at {HOST}. Is it still running?"))
    except httpx.TimeoutException:
        yield sse(dict(type="error", message="The local model took too long to respond. Try a smaller model or a shorter question."))
    except Exception:
        log.exception("ollama chat failed")
        yield sse(dict(type="error", message="Something went wrong with the local model. Check the server log."))
