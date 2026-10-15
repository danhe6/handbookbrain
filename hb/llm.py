"""LiteLLM wrapper: one place for model calls, embeddings, response caching and cost accounting.

Disk cache (HB_CACHE=1, default): an identical request (same model, messages, tools, params) is answered from
.cache/hb instead of the API. Because tool results and graph state are part of the messages, anything that
actually changed still misses the cache - so re-running the pipeline after a small code change only pays for
the parts that changed. HB_CACHE=0 disables it (use for the final, reported benchmark run).

We cache ourselves (diskcache) instead of using litellm.Cache: LiteLLM's cache-hit path imports its proxy
server modules (fastapi, orjson, ...), which a plain client install doesn't have.
"""
import hashlib
import json
import os
from types import SimpleNamespace as NS

# use the price table bundled with litellm instead of fetching it from GitHub at import (slow/timeouts offline)
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
import litellm  # noqa: E402

from .config import EMBED_MODEL, ROOT

litellm.suppress_debug_info = True
litellm.drop_params = True  # e.g. newer Claude models reject temperature=0
# flaky networks: retry with backoff instead of crashing a 20-minute run (not part of the cache key)
NET = {"num_retries": int(os.getenv("HB_RETRIES", "5")), "timeout": float(os.getenv("HB_TIMEOUT", "90"))}
USAGE = {"calls": 0, "cached": 0, "in": 0, "out": 0, "cost_usd": 0.0}

CACHE = None
if os.getenv("HB_CACHE", "1") != "0":
    try:
        import diskcache
        CACHE = diskcache.Cache(str(ROOT / ".cache" / "hb"), size_limit=2 ** 31)
    except Exception as e:  # diskcache missing etc. -> run uncached rather than fail
        print(f"[hb] LLM cache disabled ({type(e).__name__}: {e}); pip install diskcache to enable")


def _key(kind: str, payload: dict) -> str:
    return kind + ":" + hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _account(resp):
    u = getattr(resp, "usage", None)
    if u:
        USAGE["in"] += getattr(u, "prompt_tokens", 0) or 0
        USAGE["out"] += getattr(u, "completion_tokens", 0) or 0
    try:
        USAGE["cost_usd"] += litellm.completion_cost(completion_response=resp) or 0.0
    except Exception:
        pass


def _to_plain(resp) -> dict:
    """Keep only what callers read: choices[0].message.{content, tool_calls[].{id, function.{name, arguments}}}."""
    m = resp.choices[0].message
    calls = [{"id": c.id, "name": c.function.name, "arguments": c.function.arguments} for c in (m.tool_calls or [])]
    return {"content": m.content, "tool_calls": calls}


def _from_plain(d: dict):
    calls = [NS(id=c["id"], type="function", function=NS(name=c["name"], arguments=c["arguments"]))
             for c in d["tool_calls"]] or None
    return NS(choices=[NS(message=NS(content=d["content"], tool_calls=calls, role="assistant"))], usage=None)


def complete(model: str, messages: list[dict], **kw):
    if not model.startswith("anthropic/"):
        kw.setdefault("temperature", 0)
    USAGE["calls"] += 1
    key = _key("chat", {"model": model, "messages": messages, **kw}) if CACHE is not None else None
    if key and key in CACHE:
        USAGE["cached"] += 1
        return _from_plain(CACHE[key])
    resp = litellm.completion(model=model, messages=messages, **kw, **NET)
    _account(resp)
    plain = _to_plain(resp)
    if key:
        CACHE[key] = plain
    return _from_plain(plain)


def embed(texts: list[str]) -> list[list[float]]:
    texts = [t[:8000] for t in texts]
    out: list = [None] * len(texts)
    todo = []
    for i, t in enumerate(texts):
        key = _key("emb", {"model": EMBED_MODEL, "t": t}) if CACHE is not None else None
        if key and key in CACHE:
            out[i] = CACHE[key]
        else:
            todo.append((i, key))
    if len(todo) < len(texts):
        USAGE["calls"] += 1
        USAGE["cached"] += 1
    for j in range(0, len(todo), 96):
        batch = todo[j:j + 96]
        USAGE["calls"] += 1
        resp = litellm.embedding(model=EMBED_MODEL, input=[texts[i] for i, _ in batch], **NET)
        _account(resp)
        for (i, key), d in zip(batch, resp.data):
            v = d["embedding"] if isinstance(d, dict) else d.embedding
            out[i] = v
            if key:
                CACHE[key] = v
    return out


def usage_line() -> str:
    paid = USAGE["calls"] - USAGE["cached"]
    return (f"llm calls={USAGE['calls']} (cached {USAGE['cached']}, paid {paid}) in={USAGE['in']:,} "
            f"out={USAGE['out']:,} cost=${USAGE['cost_usd']:.4f}")
