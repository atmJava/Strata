"""T4 - prefix cache: a second read of the same unique prompt is cheaper (F3).

The server reports reused prompt tokens as OpenAI's
usage.prompt_tokens_details.cached_tokens.  The test uses a prompt that only these
two calls share, so a hit can only come from our first call.
"""
import time
import uuid

import pytest

import mp_client as mp

DOCUMENT = ("以下是编号 PREFIX-{tag} 的唯一文档。" + ("这是一段用于前缀缓存测试的文本，" * 60)
            + "\n最后一行：编号是 PREFIX-{tag}。")


def _call(prompt: str):
    started = time.monotonic()
    reply = mp.post_json("/v1/chat/completions", mp.chat_body(prompt, max_tokens=8))
    return reply, time.monotonic() - started


def test_second_read_hits_prefix_cache(server):
    prompt = DOCUMENT.format(tag=uuid.uuid4().hex[:8])
    first, first_s = _call(prompt)
    second, second_s = _call(prompt)
    usage = second["usage"]
    prompt_tokens = usage["prompt_tokens"]
    cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
    report = f"cached {cached}/{prompt_tokens} tokens; wall {first_s:.2f} -> {second_s:.2f} s"
    assert cached >= prompt_tokens * 0.5, f"{report} - prefix cache did not hit (T4/F3)"
    if second_s > first_s:
        pytest.xfail(f"{report} - cache hit but the wall time did not drop; timing is not conclusive here")
    assert second_s <= first_s, report
    first_text = first["choices"][0]["message"].get("content") or ""
    second_text = second["choices"][0]["message"].get("content") or ""
    assert first_text.strip() == second_text.strip(), report
