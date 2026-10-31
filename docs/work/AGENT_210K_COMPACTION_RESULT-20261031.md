# AGENT-210K-COMPACTION result

Date: 2026-10-31

Status: measurement complete. No source or service change.

## Decision

Compaction prefix reuse remains effective at the user's normal long-context
point. Three completed 210K compaction requests cached
`208,527 / 208,554 = 99.987%` of the prompt.

The remaining cost is not the long prompt read. It is:

- restoring a parked 208K conversation before a new turn (`9.605–9.784 s`,
  three runs);
- generating the compaction summary (347/532/734 output tokens in these runs);
- one reasoning-loop recovery tail in which no new token arrived for about
  75 seconds before the reply completed.

The 85-second outlier must not be reported as a normal median, and the single
occurrence does not establish a user-facing trigger rate.

## Results

The deterministic history turn produced `208,532` input tokens. A cold read
took `75.295 s`. Later identical turns reported `196,608` cached tokens and
`9.605–9.784 s` wall; the engine log resumed visible prefill at `204,800`.

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

## Other long-prefix measurements

The same-conversation tool probe produced:

| Reported prompt | Warm cached | Reuse | Prompt | Wall |
| ---: | ---: | ---: | ---: | ---: |
| 94,885 | 94,282 | 99.36% | 1.942 s | 2.086 s |
| 189,093 | 188,490 | 99.68% | 2.243 s | 2.430 s |
| 208,532 | 196,608 | 94.27% | restore + short read | 9.620 s median of 3 |

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

The 180K cold-turn/compaction result remains
`/opt/ai/logs/agent-compaction-long-20261031T0530Z/result.json`.

## Limits

- One deterministic history shape, one conversation and three completed
  compaction runs do not estimate real production frequency.
- The first two probes' initial rounds retained a shared prefix, so they are not
  pure cold-read baselines.
- The compaction prompt is deterministic but its output length varied under
  existing sampling and speculative decoding.
- No prompt or response text was saved.
- No restart, engine build, systemd change or source edit was made for these
  measurements.
