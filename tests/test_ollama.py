import asyncio
import json

import httpx
import pytest

from app import chat, chat_ollama as O, chat_tools, db, demo, quotes


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(quotes, "DEMO", True)
    demo.main()


def nd(*objs):
    return ("\n".join(json.dumps(o) for o in objs) + "\n").encode()


def run_stream(handler, text="what is trending?"):
    async def go():
        t = httpx.MockTransport(handler)
        return [json.loads(x[6:]) async for x in O.stream([{"role": "user", "content": text}], chat.SYSTEM, transport=t)]
    return asyncio.run(go())


def test_check_reachable_missing_model_and_down(monkeypatch):
    monkeypatch.setattr(O, "MODEL", "llama3.1")
    ok = lambda names: httpx.MockTransport(lambda r: httpx.Response(200, json={"models": [{"name": n} for n in names]}))
    assert asyncio.run(O.check(True, ok(["llama3.1:latest", "qwen2.5:7b"]))) == (True, "")
    ok_, why = asyncio.run(O.check(True, ok(["qwen2.5:7b"])))
    assert not ok_ and "isn’t installed" in why and "ollama pull llama3.1" in why
    def down(request):
        raise httpx.ConnectError("refused")
    ok_, why = asyncio.run(O.check(True, httpx.MockTransport(down)))
    assert not ok_ and "Couldn’t reach Ollama" in why
    monkeypatch.setattr(O, "MODEL", "qwen2.5:7b")
    assert asyncio.run(O.check(True, ok(["qwen2.5:7b"])))[0] and not asyncio.run(O.check(True, ok(["qwen2.5:3b"])))[0]


def test_tool_call_then_answer(seeded):
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        if len(seen) == 1:
            return httpx.Response(200, content=nd(
                {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_trending_companies", "arguments": {"limit": 2}}}]}, "done": False},
                {"message": {"role": "assistant", "content": ""}, "done": True, "prompt_eval_count": 100, "eval_count": 10}))
        return httpx.Response(200, content=nd(
            {"message": {"role": "assistant", "content": "$NVDA "}, "done": False}, {"message": {"role": "assistant", "content": "leads."}, "done": False},
            {"message": {"role": "assistant", "content": ""}, "done": True, "prompt_eval_count": 200, "eval_count": 20}))
    ev = run_stream(handler)
    assert [e["type"] for e in ev] == ["tool_start", "tool_done", "delta", "delta", "done"]
    assert ev[0]["label"] == "Checking trending companies" and ev[1]["ok"] is True
    assert "".join(e.get("text", "") for e in ev if e["type"] == "delta") == "$NVDA leads."
    assert ev[-1]["usage"] == dict(input=300, output=30, cache_read=0, rounds=2)
    # request shape: native tool format, system prompt first, stream on
    first = seen[0]
    assert first["stream"] is True and first["model"] == O.MODEL and first["messages"][0]["role"] == "system"
    assert [t["function"]["name"] for t in first["tools"]] == [t["name"] for t in chat_tools.TOOLS]
    assert first["tools"][0]["type"] == "function" and "parameters" in first["tools"][0]["function"]
    # second request carries the assistant tool call and a role=tool result with real data
    msgs = seen[1]["messages"]
    assert msgs[-2]["tool_calls"][0]["function"]["name"] == "get_trending_companies"
    assert msgs[-1]["role"] == "tool" and json.loads(msgs[-1]["content"])[0]["ticker"]


def test_argument_forms_and_bad_tools(seeded):
    calls = iter([
        [{"function": {"name": "find_ticker", "arguments": '{"query": "nvid"}'}},                 # arguments as a JSON string
         {"function": {"name": "does_not_exist", "arguments": {}}},                                 # hallucinated tool
         {"function": {"name": "search_news", "arguments": "not json"}}],                           # unparseable arguments
        None])

    def handler(req):
        c = next(calls)
        msg = {"role": "assistant", "content": "done" if c is None else "", **({"tool_calls": c} if c else {})}
        return httpx.Response(200, content=nd({"message": msg, "done": True}))
    ev = run_stream(handler)
    assert [e["ok"] for e in ev if e["type"] == "tool_done"] == [True, False, False]               # failures reported, not raised
    assert ev[-1]["type"] == "done" and any(e["type"] == "delta" for e in ev)


def test_round_cap_and_http_errors(seeded):
    loop = lambda req: httpx.Response(200, content=nd({"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "find_ticker", "arguments": {"query": "a"}}}]}, "done": True}))
    ev = run_stream(loop)
    assert sum(e["type"] == "tool_start" for e in ev) == O.MAX_ITERATIONS and ev[-2]["type"] == "notice" and ev[-1]["type"] == "done"
    ev = run_stream(lambda req: httpx.Response(404, content=b'{"error":"model not found"}'))
    assert ev[-1]["type"] == "error" and "404" in ev[-1]["message"]
    def refused(req):
        raise httpx.ConnectError("gone")
    ev = run_stream(refused)
    assert ev[-1]["type"] == "error" and "Lost connection" in ev[-1]["message"]
    def slow(req):
        raise httpx.ReadTimeout("slow")
    assert "too long" in run_stream(slow)[-1]["message"]
