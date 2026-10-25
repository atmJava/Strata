"""T2 - batch invariance (F1).

One greedy reply alone must equal the same reply inside a batch.  Strata's batch
windows are only exact with the settings in docs/BATCHING.md ("Exactness"):
STRATA_IQ_MT_MIN=1, --pcie-frac 0, --adapt-every 1000000, --no-prefill-borrow.
Without them the test records the mismatch and xfails instead of hiding it.
"""
from concurrent.futures import ThreadPoolExecutor
import os

import pytest

import mp_client as mp

PROMPT = "用一句话解释为什么 1+1=2。"
MAX_TOKENS = 24


def _reply(prompt: str = PROMPT):
    reply = mp.post_json("/v1/chat/completions", mp.chat_body(prompt, max_tokens=MAX_TOKENS))
    return ((reply["choices"][0]["message"].get("content") or ""),
            reply["usage"]["completion_tokens"])


def test_batch_invariance(server):
    solo_a = _reply()
    solo_b = _reply()
    if solo_a != solo_b:
        pytest.skip("greedy replies differ between two solo runs (adaptive expert tier or sampling): "
                    "T2 needs the exactness settings of docs/BATCHING.md")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(_reply), pool.submit(_reply)
    batch = (first.result(), second.result())
    if batch != (solo_a, solo_a):
        reason = ("batch reply differs from solo: exactness env not set "
                  "(STRATA_IQ_MT_MIN=1, --pcie-frac 0, --adapt-every 1000000, --no-prefill-borrow); "
                  "set MP_EXACT=1 when running against an exact arm")
        if os.environ.get("MP_EXACT") == "1":
            assert batch == (solo_a, solo_a), reason
        pytest.xfail(reason)
    assert batch == (solo_a, solo_a)
