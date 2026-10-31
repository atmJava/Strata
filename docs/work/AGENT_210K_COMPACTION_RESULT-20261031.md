# AGENT-210K-COMPACTION result

Date: 2026-10-31

Status: measurement and test-coverage audit complete. The engine and server
were not changed; the long-compaction probe, its tests and this document were
extended.

## Decision

Compaction prefix reuse remains effective at the user's normal long-context
point. Three completed 210K compaction requests cached
`208,527 / 208,554 = 99.987%` of the prompt.

The 210K compaction prompt read is solved in the two runs that did not trigger
recovery: it took `248–283 ms` and read 27 tokens. The remaining normal cost is
summary decode, not a repeated history read. The recovery run reported
`prompt_ms=75.474 s`; that span includes the roughly 75 seconds with no new
token and must not be folded into the normal prompt range.

The separate `9.605–9.784 s` history-turn result was previously labelled as a
hot restore. The logs do not support that label. That turn reused `196,608`
tokens and reread `11,924`; `prompt_ms` was `9.314–9.486 s`. The visible engine
progress began at `204,800` only because it reports after the next 8,192-token
chunk; it does not mean reuse began there.

After compaction, the retained long-prompt checkpoint list was
`[8192, 32768, 81920, 131072, 172032, 180224, 196608, 208549]`. The branch that
repeats the `208,532`-token history could no longer use the earlier `208,527`
point and fell back to `196,608`.

An exact `strata_prefix {"tokens": 208528}` pin closed the mechanism test. In
the same conversation, history converted to an exact-pinned turn, compaction
then resumed at `208528 + 27`, and the post-compaction history reused
`208528 / 208533` tokens. The branch took `0.274 s`, versus `9.016–9.523 s`
without an effective pin. This proves the checkpoint mechanism but not its
production frequency.

The full local rollout scan found 14,183 requests and 109 compaction events.
None of the first requests after compaction returned to the pre-compaction
history; the median first-post-compaction input was about `5.2%` of the prior
input. The branch is therefore a low-frequency diagnostic, not the user's
180K workload.

The other large tail is generation:

- completed compaction runs produced 347/532/734 output tokens and took
  `5.853/8.845/85.017 s`;
- one run triggered `reasoning_loop_recovery` at 512 tokens and made no visible
  progress for about 75 seconds before completing.

The 85-second outlier must not be reported as a normal median, and the single
occurrence does not establish a user-facing trigger rate.

## Results

The deterministic history turn produced `208,532` input tokens. A cold read
took `75.295 s`. Later branch turns reused `196,608` tokens, reread `11,924`,
and took `9.605–9.784 s`; the engine log resumed visible prefill at `204,800`,
not at the reused boundary.

| Completed compaction | Input | Cached | Output | Wall | Result |
| --- | ---: | ---: | ---: | ---: | --- |
| 1 | 208,554 | 208,527 | 347 | 5.853 s | completed |
| 2 | 208,554 | 208,527 | 532 | 8.845 s | completed |
| 3 | 208,554 | 208,527 | 734 | 85.017 s | completed |

The third run logged:

```text
repeated reasoning detected at 512 tokens (coverage=1.000)
resuming the same output with the low-effort instruction (reasoning_loop_recovery)
```

The progress counter then stayed at 512 for about 75 seconds before the answer
grew to 734 tokens. The response was complete and the cache still reported
99.987% reuse.

## Test coverage

The CPU-only regression set for these tools and the recovery path is:

```text
24 passed in 0.88s
```

It covers deterministic history and compaction request construction, compaction
metadata and `tools: []`, cache/output field extraction, long-context runner
failure accounting, and recovery prompt/sampling/cancel behavior.
The focused long-compaction and session-trace run is `11 passed`.

The live GPU measurements below are explicit probes rather than ordinary unit
tests. They require the 2×2080 Ti server and are therefore not run by the
offline suite:

- 180K history and compaction;
- 94K/189K warm continuation;
- 208K cold turn and three 210K compactions;
- repeat-history branch after compaction.

The exact-pin branch result is a manual live probe, not an automated GPU
regression. It is recorded as the completed low-frequency
`AGENT-LONG-CHECKPOINT-BRANCH` check. It must not be used as evidence for aged
session recovery.

## Other long-prefix measurements

The same-conversation tool probe produced:

| Reported prompt | Warm cached | Reuse | Prompt | Wall |
| ---: | ---: | ---: | ---: | ---: |
| 94,885 | 94,282 | 99.36% | 1.942 s | 2.086 s |
| 189,093 | 188,490 | 99.68% | 2.243 s | 2.430 s |
| 208,532 | 196,608 | 94.27% | 9.406 s median of 3 | 9.620 s median of 3 |

The 64K/128K requests were deterministic tool-style turns with 16 output
tokens. The actual tokenizer produced 94K/189K prompts respectively.

## Evidence

Remote results:

- `/opt/ai/logs/agent-cost-map-65536-20261031.json`
- `/opt/ai/logs/agent-cost-map-131072-20261031.json`
- `/opt/ai/logs/agent-compaction-210k-20261031.json`
- `/opt/ai/logs/agent-compaction-210k-complete-20261031.json`
- `/opt/ai/logs/agent-compaction-210k-complete-2-20261031.json`
- `/opt/ai/logs/agent-compaction-210k-complete-3-20261031.json`
- `/opt/ai/logs/agent-long-branch-pin-exact-20261031T1300Z.json`
- `/opt/ai/logs/agent-long-branch-pin-20261031T1255Z.json`
- `/opt/ai/logs/agent-long-branch-pin-20261031T1250Z.json`

The 180K cold-turn/compaction result remains
`/opt/ai/logs/agent-compaction-long-20261031T0530Z/result.json`.

## Limits

- One deterministic history shape, one conversation and three completed
  compaction runs do not estimate real production frequency.
- The user's "180K sessions that reread history" means reopening a conversation
  last used more than one day ago. The 180K cold turn above is a new synthetic
  conversation on the same server; it does not cover cache expiry, eviction or
  a process restart across that age boundary.
- The first two probes' initial rounds retained a shared prefix, so they are not
  pure cold-read baselines.
- The compaction prompt is deterministic but its output length varied under
  existing sampling and speculative decoding.
- No prompt or response text was saved.
- No restart, engine build, systemd change or engine/server source edit was
  made for these measurements; only the probe tool and its tests changed.
- Scan of available server logs found only three
  `repeated reasoning detected` lines, including two older entries and this one.
  The logs do not provide a reliable request/compaction denominator, so this is
  not a production trigger rate.
