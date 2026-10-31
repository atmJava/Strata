# AGENT-REAL-DECODE-SHAPE result

Date: 2026-10-31

Status: diagnostic complete. The decode replay found a larger,
production-hygiene issue: the live instance still has the timing and GPU
profiler environment variables enabled.

## Scope

The complete local Codex rollout scan covers 35 files, 152 roots and 14,768
requests. It contains 3,022,464,764 input tokens, 2,961,597,534 cached tokens,
10,343,967 output tokens and 3,347,547 reasoning tokens. These are client event
spans, not Strata `prompt_ms`, `decode_ms` or TTFT.

The trace file is `/tmp/agent-task-trace-all-20261031-v5.json`, SHA-256
`78ab01daf8d68eeb8f24a52e6b170f82638fbfe377d8a5534d7d26317056d054`.
It contains no prompt, assistant, reasoning or tool body.

## Shape result

| output tokens | requests | request share | model-span share |
|---|---:|---:|---:|
| 0-127 | 2,909 | 19.698% | 7.336% |
| 128-511 | 5,436 | 36.809% | 20.611% |
| 512-2047 | 5,646 | 38.231% | 49.225% |
| 2048+ | 777 | 5.261% | 22.828% |

Requests with at least 512 reasoning tokens are 12.006% of requests but
31.910% of model-span seconds. By following action, `exec` requests account for
63.541% of model time, edits 15.735%, polling 12.298% and compaction 4.580%.

Only `171.303 s` of model time overlapped two active requests; `69,064.894 s`
was at one active request. The earlier 26-file snapshot had `143.727 s` at
exactly two streams. Two-stream role scheduling is therefore not the largest
remaining target, and no further scheduling candidate is justified by this
trace.

## Live replay

The replay used the current B4 production shape: Qwen3.8 Flash Next IQ3_XXS,
engine `0.1.51`, MTP on, `spec=8`, `mtp-max=4`, six slots, two groups,
262144 context and `STRATA_BATCH_SERIAL_TWO=1`. It used one warm prompt,
temperature zero and reasoning effort `none`.

| requested tokens | one-stream span | two-stream span | second TTFT | second stream |
|---:|---:|---:|---:|---|
| 416 | 4.240 s | 8.199 s | about 4.18 s | length |
| 1,490 | 14.347 s | 28.919 s | about 14.5 s | length |
| 4,483 | 45.451 s | 85.701 s | 39.6-46.0 s | one early stop |

All 27 requests returned HTTP success and no execution error. The 416 and
1,490 runs all returned the requested token count with `finish=length`. In the
4,483 two-stream arm, the first wave ended one request at 3,892 tokens with
`finish=stop`, and the second wave did the same for the other request. The
remaining 4,483 responses reached `length`. This fails the strict all-`length`
acceptance gate and prevents a claim that long-output behaviour is fully
stable.

The two-stream completion span is approximately twice the solo span while the
second request waits for the first request to finish. Aggregate decode timers
overlap and show about twice the token rate, but that is not a user-visible
two-stream speedup. The completion span and second TTFT are the decision
fields.

Exact delayed-stop hashes:

- 4,483/length:
  `21bb8246022e929cac6edb2f7db7724280a3e95c913b2c4d2e9fffc465fafbac`
- 3,892/stop:
  `79bc59829a4cef5ea815124895c671edee73ec59a36ade154972b1bcb8847ab4`

At temperature zero, this does not guarantee byte-identical output across MTP,
cache or resumed states. The IQ one-token versus multi-token expert rounding
documented in `docs/DETAILS.md` can change an equally plausible continuation.
`STRATA_IQ_MT_MIN=1` can improve repeatability, but its historical one to three
percent decode cost makes it unsuitable as a performance candidate.

## Production instrumentation finding

The live engine environment contains both `STRATA_DECODE_TIMING=1` and
`STRATA_VERIFY_PROFILE=1`. The same instrumentation was therefore active for
this replay. The profiler is useful for attribution, but its intended use is
not as a production default.

Engine output from the replay shows about 1,055 windows, average window size
4.64, 4.25 accepted tokens per window and 42.94-43.69 ms per window. The
stage-0 and stage-1 GPU profiles each account for about 20 ms per window.
Resident-expert work is the largest named GPU stage, followed by the GDN state
and projection stages, shared-expert work, the QSA projections and the output
head. These percentages are only useful after removing the profiler's own
cost; they do not by themselves justify another kernel.

The historical same-host measurement in L-035 found `73.2 -> 68.5 tok/s`
single-stream and `120.2 -> 107.3 tok/s` four-stream when
`STRATA_VERIFY_PROFILE=1` was enabled, costs of 6.4% and 10.7%. The current
production configuration is a new instance, so that cost must be confirmed
with a same-shape A-B-A before changing the baseline.

## Decision

`AGENT-REAL-DECODE-SHAPE` is complete as a diagnostic, with the long-output
finish anomaly left open. The next item is `AGENT-PROFILE-HYGIENE`: measure the
live profiler overhead on the current model and configuration, remove the
instrumentation if it passes the pre-registered performance and quality gates,
and only then use an unprofiled decode attribution to select a code mechanism.

No production source, configuration or process was changed by this diagnostic.
The current server, vision and engine remained `50648`, `50651` and `50656`
while the replay ran.

## Raw evidence

- `/opt/ai/logs/agent-real-decode-shape-20261031T0840Z/decode-416.json`
  (SHA-256 `1e1d21e1a5e2ffe5e7854c23e2b1f6e02473dae6d5210f9d0ff7727593831c32`)
- `/opt/ai/logs/agent-real-decode-shape-20261031T0840Z/decode-1490.json`
  (SHA-256 `586409f3ddd8254ea63ebaeaf84ef4dda1f8917512282711e11cd2e36a0ddc9a`)
- `/opt/ai/logs/agent-real-decode-shape-20261031T0840Z/decode-4483.json`
  (SHA-256 `3df7488ddbaf41035a5fd4925513dff1aabe2ca3fd0daf12cde547b85dfd50c6`)
- `/home/atm/verify-20261027/strata-verify/strata-verify.log`
  (instrumented engine stderr; contains the decode timing and GPU-stage lines)

## Not measured

No four-stream replay, sampled decoding, second model, AMD backend, real
instrumentation-off baseline or automatic GPU regression was run. The prompt
class is one warm short prompt; proportions must not be generalized to all
vibe-coding requests.
