# Upstream backport, GPU order and one-hour soak result

Run: 2026-11-02 UTC on `ubuntu-server`, two RTX 2080 Ti 22 GB cards, CUDA 13.0, engine 0.1.51.
Evidence directory:
`/opt/ai/logs/gpu-order-backport-soak-20261102T132751Z`.

## What was deployed

The deployed engine was built from `strata-batch-prefill-guard-20261101`, not from the service
tree. Its SHA-256 was
`0e50583547aa355b12dc05583b4b3a18d8dc40c314ac35d425920a122fcaab0e`.

The two source backports in that build were:

- `08e8770c`: layer-major prefill asks the expert source to copy a blob when the mapping has no
  stable contiguous blob.
- `01d6f7b9`: a previous engine gets up to 120 seconds to release its memory before a new one
  starts, with a progress line every 10 seconds.

The installed `serve/server.py`, `strata_control/procs.py` and patched `prefill.cpp` matched the
local files. The service started with health `loaded=true`, six slots, two batch groups and no new
Xid.

## GPU order test

Each arm used the same binary, model, cache, 24/24 layer split, batch six, two groups, environment,
12K prompt and eight-token reply. Every arm was cold-started and given the same warm-up request.
The order test was A-B-A: `0,1 -> 1,0 -> 0,1`.

The `1,0` arm was checked in three places: the config, the engine command line and the server log.
The log said `cards in stage order: GPU 1, then GPU 0`.

Paired wall-clock results, candidate `1,0` against the median of the two `0,1` arms:

| Level | Turn | `0,1` first | `1,0` | `0,1` repeat | `1,0` change |
|---|---|---:|---:|---:|---:|
| 1 | short hot | 0.520834 s | 0.514187 s | 0.523688 s | 1.546% faster |
| 1 | 596-token hot | 1.399365 s | 1.386909 s | 1.394041 s | 0.701% faster |
| 1 | cold | 11.187603 s | 11.269965 s | 11.289748 s | 0.256% slower |
| 2 | short hot | 1.354500 s | 1.355681 s | 1.350710 s | 0.153% slower |
| 2 | 596-token hot | 2.627930 s | 2.650913 s | 2.601428 s | 1.641% slower |
| 2 | cold | 11.795421 s | 11.852299 s | 11.856552 s | 0.256% slower |

All 81 requests succeeded, all replies were expected, every finish was `stop`, and there were no
foreign requests. Control drift stayed between 0.28% and 1.46%.

`1,0` helped single-request hot turns by up to 1.55% wall time, but regressed the two-request hot-long
turn by 1.64%. Cold turns were effectively unchanged. Because production's important path is
concurrent long context, the service was returned to `0,1`.

This short comparison does not show that either order reduces the historical GPU 0 Xid risk.
GPU 0 remained at PCI `0000:03:00` in both orders.

## One-hour soak

The selected `0,1` order then ran the mixed 2/3/4-concurrent profile for 3600 seconds.

| Measure | Result |
|---|---:|
| Waves | 215 |
| Requests | 646 |
| HTTP or execution errors | 0 |
| Needle answer failures | 0 |
| `finish=length` | 0 |
| Engine restarts | 0 |
| New NVRM Xid lines | 0 |
| Samples | 358 |
| Max running requests | 4 |
| GPU 0 temperature | 56-79 C |
| GPU 1 temperature | 49-64 C |
| MemAvailable minimum | 2,555.2 MiB |
| MemAvailable at end | 4,352.8 MiB |

The long-context medians were 17.836 s TTFT for 32K, 25.402 s for 64K and 39.010 s for 96K.
The final health response was `loaded=true`; the engine PID, binary hash, config hash and systemd
restart count did not change during the soak.

The RAM minimum was close to the engine's 2,560 MiB floor. There was no OOM, swap use or failed
request, but 96K concurrent traffic leaves little spare RAM. A soak with more or longer contexts
should watch `MemAvailable`, not only request success.

The continuous sampler did not resolve the engine path in RSS because the remote copy of
`mixed-soak.py` searched for `build/strata --serve`; the deployed path is `build-guard-prod/strata`.
The soak's memory and GPU samples are still valid. Engine RSS after the run was 57,431,600 KiB.

## Rollback

Original files are in
`/opt/ai/backups/upstream-backport-20261102-gpu-order-soak/`. The old engine SHA-256 is
`e1208e5166b11b541fe110f3ed99c140da20718e981621c9849a5917b582e707`.
The pre-test config is also stored in the evidence directory as
`strata-production.before.json`.
