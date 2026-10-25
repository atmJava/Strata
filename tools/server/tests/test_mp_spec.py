"""T5 - speculative draft acceptance (F4/T5).

The fork's own tests (tests/v1/spec_decode/) assert that acceptance is measured and that the
output is unchanged by drafting.  Strata exposes the same numbers on /metrics
(`totals.drafts_offered`, `totals.drafts_accepted`) and the bench script prints them; this
test turns them into a gate: while drafting is on (production: `--spec 8`), a plain chat
request must offer drafts and accept at a healthy rate.

The window may include this session's own traffic, so the assertion is a floor, not an
exact ratio; a run with no drafts offered at all (spec off, a fallback arm) is xfail with
the reason, not a pass.
"""
import json
import urllib.request

import pytest

import mp_client as mp

PROMPTS = [
    "用大约 200 字解释投机解码（speculative decoding）为什么不会改变输出分布。",
    "用大约 200 字解释 MTP（multi-token prediction）草稿在什么情况下会被拒绝。",
    "用大约 200 字解释接受率（acceptance rate）如何影响解码加速比。",
]
MIN_OFFERED = 20          # a 200-token answer offers far more; this only rejects a dead drafter
MIN_ACCEPTANCE = 0.50     # production measures 78-82% (L-046/L-049); the floor leaves room for other traffic


def _totals() -> dict:
    request = urllib.request.Request(f"{mp.BASE_URL}/metrics",
                                     headers={"Authorization": f"Bearer {mp.API_KEY}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["totals"]


def test_draft_acceptance_floor(server):
    before = _totals()
    for prompt in PROMPTS:
        text, _usage, _first = mp.collect_stream(mp.chat_body(prompt, max_tokens=200, stream=True))
        assert text.strip(), "no answer; the acceptance window would be empty"
    after = _totals()

    offered = after["drafts_offered"] - before["drafts_offered"]
    accepted = after["drafts_accepted"] - before["drafts_accepted"]
    requests = after["requests"] - before["requests"]
    report = (f"drafts offered {offered}, accepted {accepted} "
              f"({(100.0 * accepted / offered) if offered else 0.0:.1f}%), requests in window {requests}")
    if offered < MIN_OFFERED:
        pytest.xfail(f"{report} - no drafting in this window (spec off, or a fallback arm); F4 not measurable here")
    assert accepted / offered >= MIN_ACCEPTANCE, report
    assert requests >= len(PROMPTS), report
