# UTIL-MTP-LOWCONC result

Run: 2026-10-31 on the two-RTX-2080-Ti candidate, CUDA 13.0, engine
`/opt/ai/src/strata-freeze-throughput-20261029T0812Z/build75/strata`.

## Decision

**Rejected by the four-stream non-regression gate.** Serial batch-MTP improved
the two-stream aggregate by `3.25%`, but reduced four-stream aggregate
throughput by `28.99%`.

The candidate was output-preserving for this fixed greedy workload, had zero
request errors, and used the same six slots. Its high-concurrency regression is
large enough that no additional waves, thresholds, group counts or draft
depths were measured.

## Arms

A used the restored two-group pipelined config:

- `/opt/ai/logs/agent-mtp-lowconc-20261031T0745Z/a-config.json`
- SHA-256 `7b7f75ef46a6431a1c3c72372effec210acff4d15ca6df0d29a92ef2ac83e31b`
- `--batch-groups 2`, no `--batch-mtp`.

B copied A and changed only the serial batch-MTP controls:

- `--batch-groups 2` became `--batch-groups 1`;
- appended `--batch-mtp`;
- config SHA-256
  `6681affd409208835f992ed717ea414511a6a91030d62134cf0a5e95a350c916`.

The startup log confirmed six slot drafters on CUDA1, so the candidate reached
the intended path rather than silently falling back.

## Measurement

`tools/server/probe-decode-concurrency.py` used one fixed deterministic prompt,
512 requested output tokens, one full warmup and three waves at each of 1, 2
and 4 streams. Aggregate decode throughput divides total output tokens by the
longest overlapping decode interval.

| Streams | A1 tok/s | B tok/s | Change |
| ---: | ---: | ---: | ---: |
| 1 | 104.20 | 103.73 | -0.46% |
| 2 | 88.50 | 91.38 | **+3.25%** |
| 4 | 136.01 | 96.58 | **-28.99%** |

Every request produced 512 tokens with `finish=length`. All outputs at a level
had one hash, and B's hashes equalled A's. There were no HTTP, CUDA, timeout or
foreign-request errors.

## Restored A2

A2 reproduced the same path after the candidate:

| Streams | A1 tok/s | A2 tok/s | Drift |
| ---: | ---: | ---: | ---: |
| 1 | 104.20 | 102.75 | -1.39% |
| 2 | 88.50 | 87.85 | -0.74% |
| 4 | 136.01 | 135.81 | -0.15% |

All three levels stayed within the pre-registered 3% drift gate. A2 had six
slots, two groups, health loaded and `1005 MiB` free VRAM at the final check.

## Interpretation

The two-stream gain is real for this exact workload but not adoptable. The
single-group path loses the two-stage overlap that keeps four streams useful.
The next experiment therefore changes only the low-concurrency slot layout and
leaves all three-or-more-stream slot sets unchanged.

## Evidence

- `/opt/ai/logs/agent-mtp-lowconc-20261031T0745Z/a1.json`
  (SHA-256 `61b0c5ec4a57021737d08d99d33ebfb16dbc53f409423cbbb12c10fbba69e12c`)
- `/opt/ai/logs/agent-mtp-lowconc-20261031T0745Z/b.json`
  (SHA-256 `93ec118733f2c45ad22a22fe59fadc75f2b7c98b43ba7edfe282658c12c16a22`)
- `/opt/ai/logs/agent-mtp-lowconc-20261031T0745Z/b-server.log`
  (SHA-256 `42c729635819d6d06f4a4982ec4aa10944c41577575683e96f403b1eb27fe89c`)
- `/opt/ai/logs/agent-mtp-lowconc-20261031T0745Z/a-config.json`
- `/opt/ai/logs/agent-mtp-lowconc-20261031T0745Z/b-config.json`
- `/opt/ai/logs/agent-mtp-lowconc-20261031T0745Z/a2.json`
  (SHA-256 `3dc06335a9c5c23cd694e6e339adec59cc9f7b45edb228dd19129aab26f16d8f`)

## Not measured

No sampling, cancellation, mixed prompt, six/eight-stream, second model or AMD
arm was run.
