# UTIL-WINDOW-PROFILE result

Run: 2026-10-31 on the 2×RTX 2080 Ti A instance, CUDA 13.0. Engine
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
base config `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f`.

## Decision

**UTIL-WINDOW-PROFILE is complete.** Three independent signals separate the six-stream peak
from the eight-stream tail:

1. live slot occupancy falls from `5.333` at six streams to `5.010` at eight;
2. median slowest-to-median wall ratio rises from `1.0026` to `1.1349`, and the median maximum
   slot wait rises from `1,859 ms` to `12,596 ms`;
3. BSTAT active rows per batch window falls from `2.9515` to `2.6837` while padding stays tiny
   (`0.0315` to `0.0228` rows/window).

The six-slot pool therefore creates a queued, underfilled tail; padding is not the cause.
Per active row, profiled stage 2 rises from `6.5119` to `6.8078 ms` (`+4.54%`) and stage 1 from
`5.7225` to `5.9917 ms` (`+4.70%`). The final-stage head is a fixed per-group-window cost:
`1.06 ms/window` at six streams and `1.04 ms/window` at eight, but fewer active rows per window
at eight raise its cost per active row by `7.9%`.

The measured streamed-KV resolve phase is effectively zero in this short-prompt run
(`kv-resolve 0.000` in both GDN and QSA stage reports). This round does not support another
KV-resolve candidate. `PSTAT` was enabled but the server did not expose a stage sample; stage
event reports supplied the stage evidence instead. No claim is made that PSTAT ran.

## Throughput and instrumentation cost

Each cell is three waves of 400 output tokens. P is A plus only
`STRATA_VERIFY_PROFILE=1` and `STRATA_BATCH_TELEMETRY=1`.

| Streams | A1 tok/s | P tok/s | A2 tok/s | A1/A2 combined | P vs combined |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 80.932 | 76.252 | 79.087 | 80.010 | `-4.696%` |
| 4 | 105.162 | 102.447 | 111.266 | 108.214 | `-5.329%` |
| 6 | 120.547 | 113.352 | 123.866 | 122.703 | `-7.621%` |
| 8 | 107.216 | 103.373 | 107.213 | 107.214 | `-3.583%` |

The instrument costs `3.6–7.6%`, consistent with the profiler disabling fused/side paths, so P's
absolute rate is not a production claim. A1→A2 drift was
`-2.280% / +5.804% / +2.753% / -0.003%`. Four streams remain too noisy to compare by absolute
cross-arm values; conclusions use P's within-run six/eight split.

## Window decomposition

| Metric | 6 streams | 8 streams | Change |
| --- | ---: | ---: | ---: |
| Mean occupied slots | 5.333 | 5.010 | `-6.06%` |
| Slowest / median request wall | 1.0026 | 1.1349 | `+13.2%` |
| Median maximum slot wait | 1,859 ms | 12,596 ms | `+577.6%` |
| BSTAT active rows/window | 2.9515 | 2.6837 | `-9.07%` |
| BSTAT processed rows/window | 2.9865 | 2.7256 | `-8.74%` |
| BSTAT padding rows/window | 0.0315 | 0.0228 | lower |
| Stage 1 total | 16.89 ms/window | 16.08 ms/window | `-4.80%` |
| Stage 2 total | 19.22 ms/window | 18.27 ms/window | `-4.94%` |
| Stage 1 per active row | 5.7225 ms | 5.9917 ms | `+4.70%` |
| Stage 2 per active row | 6.5119 ms | 6.8078 ms | `+4.54%` |

The absolute stage totals fall with fewer active rows; the per-row cost rises. That is the
underfilled-tail signature. At six streams, stage 2's GDN `head` phase is `1.06 ms/window` and
`waitA` is `1.63`; at eight they are `1.04` and `1.65`. The second pipeline stays occupied long
enough that two group windows pay the head's weight read separately.

Each P wave had exactly two stage reports and BSTAT records. All 12 P waves had no HTTP/execution
errors and no foreign requests. Finish counts were `length=12, stop=5`, versus
`length=12, stop=6` in A1 and A2. Since the profiler changes the fused/side path, this one
`stop`→`length` difference is a reminder that the instrumented arm is diagnostic only.

## Why the next candidate is different

One-row GDN recurrence, checkpoint movement and fixed grouping all tried to reduce a launch or
copy inside the existing window. The profile instead exposes duplicated final-stage work across
the two pipeline groups: every group runs its own head after its stage-2 layers. At six rows the
two groups each carry up to three rows, so merging their head work can read the output projection
once for up to six rows instead of once per group. This attacks the measured fixed cost and is
useful even when the queue is fully occupied.

## Restart and restoration

- A1 server/engine/vision PID `548696/548707/548699`.
- A→P stop about `9 s`; P loaded in `130 s`.
- P server/engine PID `553834/553841`.
- P→A2 stop about `9 s`; A2 loaded in `148 s`.
- A2 server/engine/vision PID `557432/557442/557436`, health loaded, six slots, two groups.
- A2 engine/config hashes are the original `105a4171...` / `88de996a...`; both profiling
  environment entries are absent.
- Production `strata.service` remained `inactive/dead`, `MainPID=0`, `NRestarts=23`.
- Final RAM available was `13 GiB`, swap used `12 MiB`, both GPUs were idle.

An incomplete P attempt used the server wrapper log instead of the engine stderr log and therefore
parsed no stage records. It was stopped and preserved as
`P-WINDOW-aborted-wrong-log.log`; the complete P run started from scratch with the correct engine
log.

## Evidence

- `docs/work/UTIL_WINDOW_PROFILE_CONTRACT-20261031.md`
- `/opt/ai/logs/util-window-profile-20261031T0545CST/UTIL-WINDOW-PROFILE-summary.json`
  (sha256 `8a35e4abcab25fa34d8ebf9db921d2f50368f994e1a1e8821097790404b7e221`)
- `/opt/ai/logs/util-window-profile-20261031T0545CST/A1-WINDOW.json`
  (sha256 `d13a5f9b845fbfd38166e1d2b40b966bec97010f3a6c0439291145dd543ad8e4`)
- `/opt/ai/logs/util-window-profile-20261031T0545CST/P-WINDOW.json`
  (sha256 `2119df0228a16f05e84bf7528cb70bd5110ff6659d684179aeaa1239e636366d`)
- `/opt/ai/logs/util-window-profile-20261031T0545CST/A2-WINDOW.json`
  (sha256 `458de9be7d789f3f43be14f4d2aa0e3dfb9504619992875c438c69854037bc67`)
- `/opt/ai/logs/util-window-profile-20261031T0545CST/P-WINDOW-metrics-all.json`
  (sha256 `09ac3aca5abf21a5dd31eff45fb124f293c60e3d6f9cd94797c4085571fbcaaa`)
- `/opt/ai/logs/util-window-profile-20261031T0545CST/p-window-profile.json`
  (sha256 `2317b2c7c4be0581057aee813535861aae7e93163e74fed5425e3c85e2ddb2f1`)
- `/opt/ai/bin/window-profile-sweep.py`
  (sha256 `02db9bfa014f8f436a0a4280d83db28dcae628c7076d7728876fa1160f214ddb`)
- `/home/atm/verify-20261027/strata-verify/strata-verify.log`
  (sha256 `e22ccfa9b1ac40e37e7610be60ae2711acde60ecf71e7bd6f060c4f7dcf28701`)

## Not measured

No long prompt, mixed prefill/decode traffic or longer soak ran. `PSTAT` was unavailable through
telemetry, so its separate host-stage claim is not established by this run. The profile does not
show which of two simultaneous pipeline groups has better head batching until the next candidate's
GPU harness measures merged versus separate head rows.

## Next question

Open `UTIL-BATCH-HEAD`: merge simultaneously ready final-stage head rows across the two pipeline
groups, read the output projection once for the combined row set, and then sample each row in its
original group/slot order. The contract is
[`UTIL_BATCH_HEAD_CONTRACT-20261031.md`](UTIL_BATCH_HEAD_CONTRACT-20261031.md).
