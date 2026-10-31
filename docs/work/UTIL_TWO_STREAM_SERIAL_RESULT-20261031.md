# UTIL-TWO-STREAM-SERIAL result

Run: 2026-10-31 on two RTX 2080 Ti cards, CUDA 13.0, engine
`/opt/ai/src/strata-freeze-throughput-20261029T0812Z/build75/strata`.

## Decision

**Adopted as `STRATA_BATCH_SERIAL_TWO=1`.** With exactly two requests, the
first stays on the solo path and the second waits, then also runs solo. A third
request promotes the active stream to the production batch path.

The two-request completion span improved from `11.776 s` in A2 to `10.004 s`,
or `15.05%`. Against the A1/A2 combined median, the improvement is `14.83%`.
The fixed 64-token short workload also improved: `1.692 s` in A3 to `1.554 s`,
or `8.18%`.

## Arms and deployment

A used the original two-group config:

- config `/opt/ai/logs/agent-mtp-lowconc-20261031T0745Z/a-config.json`
- SHA-256 `7b7f75ef46a6431a1c3c72372effec210acff4d15ca6df0d29a92ef2ac83e31b`
- default server `serve/server.py` SHA-256
  `a38a6655e64f5b9d15af0bbf88b8a7dcfd2ffb3c587c98944e805b154ab3c594`

B used an isolated copy of the source tree plus only the server scheduling
change:

- candidate source `/opt/ai/src/strata-two-stream-serial-20261031`
- candidate `serve/server.py` SHA-256
  `10bd83181940472dd936e6ab795bbba4401ab1d750a1631fecd46194ac76a405`
- config SHA-256
  `7c90109272f6e2afb7c85d86224b886668259f81f739c737c9b7ad5b944d72dc`
- process environment `STRATA_BATCH_SERIAL_TWO=1`

The engine binary, model, six slots, two groups, 262144 context, KV mode,
expert cache and production `strata.service` were unchanged.

## Long-output measurement

The probe used one deterministic prompt, 512 requested output tokens, one full
warmup and three waves at 1, 2 and 4 streams.

| Streams | A1 | A2 | B | B vs adjacent median |
| ---: | ---: | ---: | ---: | ---: |
| 1, tok/s | 104.20 | 102.75 | 103.08 | -0.38% |
| 2, completion span | 11.716 s | 11.776 s | **10.004 s** | **-14.83%** |
| 4, tok/s | 136.01 | 135.81 | 135.96 | +0.04% |
| 4, completion span | 15.219 s | 15.237 s | 15.236 s | -0.01% |

All 27 candidate long-output requests succeeded with 512 tokens and
`finish=length`. The two-stream output hash was
`220155f501fc622223d644c110f1e1d0ce50d7fc487167b56c280a1e02aed2e1`,
equal to the same request's solo hash and identical in all three waves. The
four-stream hash remained A's
`3606b981af80669ee926b0b5b3104958f19b9c290b5e258c69552b9137a48598`.

The second request's TTFT was `5.073-5.105 s`, rather than about `0.1-0.4 s`
under concurrent admission. This is the deliberately accepted tradeoff: both
requests finish earlier in aggregate, but the second starts later.

## Short-output regression check

The same two-request shape with 64 output tokens was measured after restoring
the original server:

| Arm | Completion span | Aggregate decode |
| --- | ---: | ---: |
| A3, original batch | 1.692 s | 82.72 tok/s |
| B, serial two | **1.554 s** | 82.39 tok/s |

B improved total completion by `8.18%`; both arms had zero errors. This rules
out the concern that the policy only helps long generations.

## Six-stream safety

One six-stream, 256-token wave resumed the normal batch path:

- six requests, zero errors, all `finish=length`;
- one output hash across all six;
- decode aggregate `163.07 tok/s`;
- completion span `9.646 s`;
- six slots and two groups remained healthy.

## Startup, correctness and rollback

The fake-engine two-request test observed `GEN` then `GEN`, with no `BGEN`.
The three-request test observed promotion to `BGEN`. The default variable-off
path passed all existing slot tests, the focused parallel tests, the four probe
tests and offline preflight.

The service was verified after loading with:

- server PID `50648`;
- vision PID `50651`;
- engine PID `50656`;
- server source under
  `/opt/ai/src/strata-two-stream-serial-20261031`;
- config `/opt/ai/logs/agent-mtp-lowconc-20261031T0745Z/b-two-serial.json`;
- `STRATA_BATCH_SERIAL_TWO=1` in the server process environment;
- six slots, two groups and health loaded.

Rollback is exact: stop the candidate server and start
`/opt/ai/src/strata-freeze-throughput-20261029T0812Z/strata.py serve` with
`a-config.json`, without the environment variable. The original source file was
never modified.

## Evidence

- `/opt/ai/logs/agent-mtp-lowconc-20261031T0745Z/a1.json`
- `/opt/ai/logs/agent-mtp-lowconc-20261031T0745Z/a2.json`
- `/opt/ai/logs/agent-mtp-lowconc-20261031T0745Z/b-two-serial.json.out`
  (SHA-256 `2af3759e81d7134984265d55e6cd1f76434eccb9fd9b19eb14aa124003d37fb4`)
- `/opt/ai/logs/agent-mtp-lowconc-20261031T0745Z/b-two-serial-n6.json.out`
  (SHA-256 `e91a2d1ebe7c83724ae4495f0b875cfe018a629a82a0dca10cb5b3e4bf317ca4`)
- `/opt/ai/logs/agent-mtp-lowconc-20261031T0745Z/b-two-serial-n2-short.json.out`
- `/opt/ai/logs/agent-mtp-lowconc-20261031T0745Z/a3-n2-short.json.out`
- `/opt/ai/logs/agent-mtp-lowconc-20261031T0745Z/b-server.log`
- `/opt/ai/logs/agent-mtp-lowconc-20261031T0745Z/b2-server.log`

## Not measured

No high-concurrency soak, cancellation race, tool-call quality suite, sampled
decoding, second model, AMD backend or real multi-session trace replay was run.
The policy should be disabled if the second request's delayed TTFT is more
important than total completion for a deployment.
