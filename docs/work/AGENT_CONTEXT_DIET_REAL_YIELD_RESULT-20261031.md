# AGENT-CONTEXT-DIET-REAL-YIELD result

Date: 2026-10-31

Status: rejected by the live Codex coding-task gate. The read-only selection
gate passed, but the opt-in diet caused extra model and tool turns and was
substantially slower end to end.

Contract:
[`AGENT_CONTEXT_DIET_REAL_YIELD_CONTRACT-20261031.md`](AGENT_CONTEXT_DIET_REAL_YIELD_CONTRACT-20261031.md)

## Decision

The existing eager context diet has enough exposure in stored Codex sessions
to justify live validation. Thirty of 36 sessions contain at least one
eligible successful tool output, and 29 sessions have a conservative estimate
of at least 1% less model-visible input.

Live validation failed. Three paired Codex coding tasks were slower with the
diet by `17.365%`, `86.935%` and `41.276%`; the paired median was `41.276%`
slower. Two of three diet arms needed more model requests and every diet arm
needed at least two extra tool calls. The median input-token change was
`38.190%` more, not less.

Correctness did not fail: all six arms finished with passing repository tests,
`RESULT=PASS`, no HTTP errors and mode-0600 artifacts from which report markers
were recovered. The candidate is rejected because the real product path spent
more wall time and more model work.

## Method

`tools/server/probe-context-diet-yield.py` read the local Codex rollout JSONL
files without emitting prompt, assistant, reasoning or tool-body text. For
each output it retained only:

- output type and size class;
- whether the existing fail-open success classifier would fold it;
- the number of later requests that could see it before the next compaction;
- the stable reference length that would replace it;
- aggregate token estimates and session-level shares.

The selection estimate uses:

```text
sum((original output chars - stable reference chars)
    * later model-visible requests before compaction)
```

It then converts characters to tokens with a conservative `chars / 4`
estimate. Error, failure, panic, traceback, denial and timeout output remains
verbatim. Custom tool outputs and tool-search outputs are not folded.

## Real-session audit

Scan input:

```text
/home/kali/.codex/sessions
```

Aggregate result:

| field | result |
|---|---:|
| sessions / files | 36 |
| requests | 15,338 |
| input tokens | 3,136,590,299 |
| cached input | 97.890% |
| tool outputs | 22,250 |
| total tool-output chars | 83,592,095 |
| outputs >=4K chars | 5,533 |
| foldable large outputs | 2,332 |
| foldable original chars | 25,323,412 |
| later visible foldable requests | 165,524 |
| original repeated visible chars | 1,727,477,918 |
| stable-reference visible chars | 99,724,991 |
| estimated saved chars | 1,627,752,927 |
| estimated saved tokens | 406,938,232 |
| estimated saved input share | 12.9739% |
| compactions | 119 |

Coverage and distribution:

| field | result |
|---|---:|
| sessions with foldable output | 30 / 36 |
| sessions estimated at >=1% | 29 / 36 |
| per-session p50 | 14.8319% |
| per-session p75 | 25.8062% |
| per-session p90 | 65.3799% |
| per-session max | 70.6715% |

Exposure:

| later visible requests | outputs |
|---|---:|
| 1 | 59 |
| 2-4 | 98 |
| 5+ | 2,175 |

The classifier retained 3,187 large outputs because they looked like errors or
failures. It did not fold 2,228 custom-tool outputs or 30 tool-search outputs;
these are coverage gaps, not assumed savings.

The scan JSON is `/tmp/agent-context-diet-yield-20261031.json` with SHA-256
`fa3dfdaa7921a04b130c68662016cba747e9c16bb27283edc221b0d1ac45b45b`.

## Interpretation

The previous uncertainty was whether the diet only helped an unrealistic
synthetic wave. That uncertainty is reduced: large successful outputs are
common, and many are visible across at least five later requests.

The live result also shows why the aggregate estimate was insufficient:

- `97.890%` of aggregate input is already cached;
- the fixed prompt and proxy costs can dominate small uncached differences;
- a model may spend an extra turn rereading an artifact;
- real edit/test tasks can produce a different number of model turns;
- the opt-in proxy must be reachable from the actual Codex provider path.

The live gate used three adjacent control/diet pairs on identical Codex coding
fixtures. It required unchanged edit/test correctness, visible errors,
byte-exact artifact recovery, unchanged finish semantics, no extra turn or
retry that erased the gain, and a median wall improvement of at least 1%.

The implementation is rejected by that gate. The default direct server path
was never changed; no Strata restart was needed or performed.

## Live Codex pairs

The client was Codex CLI `0.164.0`, using its real Responses provider path:

```text
codex exec -> agent-shape-proxy -> SSH tunnel -> Strata B4
```

Both arms used the same model
`qwen3.8-flash-next-iq3_xxs-abliterated`, reasoning effort `low`, tool set,
sandbox, approval policy, 262144 context and fresh copies of the same fixture.
Each task first ran a successful 61,284-byte report without pipes or
redirection, then ran tests, edited implementation code and reran tests.

| Pair | Task | Control wall | Diet wall | Diet slowdown | Requests | Tool calls |
|---:|---|---:|---:|---:|---:|---:|
| 1 | simple add bug | 21.589 s | 25.338 s | 17.365% | 6 → 6 | 5 → 7 |
| 2 | 70-case failure | 96.806 s | 180.964 s | 86.935% | 8 → 19 | 7 → 18 |
| 3 | parser bug | 128.187 s | 181.097 s | 41.276% | 10 → 18 | 9 → 17 |
| Median |  |  |  | **41.276% slower** |  |  |

Input-token changes:

| Pair | Control | Diet | Diet vs control |
|---:|---:|---:|---:|
| 1 | 91,497 | 62,967 | 31.181% less |
| 2 | 169,813 | 305,520 | 79.916% more |
| 3 | 216,456 | 299,120 | 38.190% more |
| Median |  |  | **38.190% more** |

Across the three diet arms the proxy replaced 128 eligible outputs and removed
`1,027,637` characters from individual requests. That local reduction did not
survive the whole task: the model reread and reran work enough to add requests,
tool calls and total input.

All six arms had passing repository tests, final `RESULT=PASS`, return code
zero and zero proxy HTTP errors. Diet artifacts were mode `0600`; every diet
arm contained at least one exact report-marker file. The first discarded
attempt used a malformed fixture and is excluded from the table.

Evidence:

| Artifact | SHA-256 |
|---|---|
| aggregate JSON | `da0081a5a1e75d17ef6212870d3ce5f8a3ab023e499c1a3a51f56627efc7d933` |
| control shape log | `6b005ed78f54bfc0e020b4820581da194008d8c55a89ad03f1f3480ff7d0e4b2` |
| diet shape log | `3d34090bce2069d770e54cc02ac19b4a9ffbdd2e6e8de408baeaac4ba59e8a78` |

The aggregate is
`/tmp/context-diet-codex-live-20261031T105044Z-reanalyzed.json`. Private Codex
event streams and synthetic tool outputs remain under `/tmp` and were not
committed.

## Production decision

Do not expose the context diet as a recommended Codex path for this model and
workload. Leave the implementation and tests in the repository as an opt-in
research tool, with this failed result as the default reference.

The next priority is `AGENT-HISTORY-STATE-RESIDENCY` feasibility: the real
49-day, 223,114-token session still takes `113.014 s`, almost entirely before
the first token. That is a lower-frequency but much larger p99 user wait than
the rejected proxy overhead.

## User-priority check

The current plan now matches the repeated user constraints:

- usually one session, sometimes two, with at most one main and three
  subagents;
- an aged `180K` session means reopening a day-old session, while `210K` is
  the normal current-session compaction point;
- improvements of at least 1% count, but restart cost must be justified;
- correctness, tool-result visibility and recovery precede synthetic
  throughput;
- work must continue toward the largest user-visible bottleneck rather than
  scanning nearby local parameters;
- every adopted capability must have a real client path and a precise
  rollback.

The remaining largest local p99 item is the real 223K aged-session resume at
`113.014 s`. It becomes the current item after this rejection. The current
session-file API rejects the production layer split, batch and prompt-cache
shape, so the next step is read-only multi-stage state-residency feasibility,
not another context-diet parameter.

## Validation

Focused probe and runner tests:

```text
python -m pytest -q \
  tools/server/tests/test_probe_context_diet_yield.py \
  tools/server/tests/test_bench_context_diet_codex.py
6 passed
```

## Limits

The session set is one user's local history and is not a population sample.
The scanner treats request-level token usage as exposure and stops exposure at
the next recorded compaction. It does not reconstruct every exact serialized
prompt, classify semantic output value, support custom-tool payloads, or
measure wall time.
