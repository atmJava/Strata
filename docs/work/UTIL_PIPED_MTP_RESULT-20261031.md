# UTIL-PIPED-MTP result

Run: 2026-10-31 on the candidate snapshot, two RTX 2080 Ti, CUDA 13.0, engine
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`.

## Decision

**UTIL-PIPED-MTP is rejected by the pre-registered quality and high-concurrency gates.**

The candidate did keep six slots and two pipelined groups, and its startup logs proved that all six
slot drafters and the two-group piped MTP path were active. At two streams it reached `93.9 tok/s`
against a combined A1/A2 median of `78.3 tok/s`, a `19.92%` improvement with five of six paired
wins. That speed result is real, but it cannot be adopted:

- four streams regressed from the combined A median `108.55` to `104.4 tok/s` (`-3.82%`);
- six streams regressed from `125.7` to `113.4 tok/s` (`-9.79%`);
- the fixed greedy parity probe was not output-preserving: B produced different text in two
  identical runs, while A produced the same text twice.

The result therefore rejects this exact six-slot, two-group configuration. It does not reject the
pipelined MTP mechanism itself, and it does not justify scanning draft depths, slot counts or group
counts.

## Startup proof

Candidate config:
`/opt/ai/logs/qsa-diag-20261031T1653Z/b-piped-mtp.json`
(sha256 `e22fb10cdfcc277eac739008b100feb3eab1ee9f35d73f89e9e96eea64637bc7`).

The only semantic changes from A were:

```text
append --batch-mtp
STRATA_PIPED_MTP=1
STRATA_BATCH_SELF_COMMIT=0
```

The engine log printed:

```text
strata generate: STRATA_PIPED_MTP: 2 pipelined groups of 3 slots draft up to 1 MTP proposal per slot each window
strata generate: --batch-mtp: 6 slot drafters on CUDA1 (51 MiB of private K/V state and buffers each, sharing the solo drafter's weights); 14.24 GiB free there
```

`/metrics` reported `batch_slots=6`, `batch_groups=2`, `pool_workers=13`, health `loaded=true`,
and the requested engine hash. The candidate consumed `964 MiB` free VRAM instead of A's
`1006 MiB`; both cards remained healthy.

## Frozen workload

`tools/server/bench-strata.py` used the same fixed prompts and `max_tokens=400` as the
pre-registered run:

```text
BENCH_N=2, 4, 6; three repetitions each
A1 -> B -> A2
```

All 135 benchmark requests completed. There were no HTTP or execution errors and no foreign
requests in any measurement window. `finish_reason=length` values were expected because the
benchmark asks for 400 tokens.

| Streams | A1 median | A2 median | combined A median | B median | B vs A | B wins |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 89.5 | 75.4 | **78.3** | **93.9** | **+19.92%** | 5/6 |
| 4 | 104.6 | 108.8 | **108.55** | 104.4 | **-3.82%** | 1/6 |
| 6 | 125.3 | 128.5 | **125.7** | 113.4 | **-9.79%** | 0/6 |

All values are output tokens per second. A1/A2 can be one of the original repetitions:

```text
2 streams A: 76.6, 89.5, 90.7, 74.2, 80.0, 75.4
4 streams A: 116.1, 102.1, 104.6, 108.3, 113.9, 108.8
6 streams A: 117.7, 125.3, 126.1, 128.5, 123.6, 128.9

2 streams B: 93.9, 86.9, 103.6
4 streams B: 104.4, 106.3, 98.2
6 streams B: 112.8, 118.7, 113.4
```

Concurrent TTFT medians were:

| Streams | A1 | B | A2 |
| ---: | ---: | ---: | ---: |
| 2 | 156.5 ms | 174.0 ms | 157.0 ms |
| 4 | 476.0 ms | 487.5 ms | 279.5 ms |
| 6 | 765.0 ms | 821.0 ms | 1146.0 ms |

At six streams B also reduced each request's median decode rate from A1's `22.1` to `20.5 tok/s`.
The extra speculative rows did not pay back at full concurrency.

## Greedy parity failure

Three fixed greedy probes were run twice on A and twice on B. The exact-output and arithmetic
probes matched across all arms. The third probe did not:

```text
A trial 0/1: identical, finish=length after 96 tokens
B trial 0:   "Because the target model accepts only tokens that match its own greedy argmax choice,
              so verification rejects any mismatch and preserves the original greedy sequence."
B trial 1:   "Speculative decoding preserves greedy output because the target model verifies each
              proposed token and accepts only those matching its own greedy argmax choice."
```

The B texts are coherent, but they are neither identical to A nor repeatable within B. The
pre-registered gate required fixed greedy parity; this run did not pass it.

The likely contributor, not yet proven causally, is expert residency. B reported
`expert_slots=17104` (`primary=10064`, CUDA1=`7040`) and `expert_cache=27947 MiB`; A reported
`17274` (`primary=10065`, CUDA1=`7209`) and `28267 MiB`. CUDA1 lost 169 slots because the slot
drafters allocate there. The engine documentation explicitly requires the same expert cache on
both arms for text equality, and says a different cache can change the last digits through
CPU/GPU rounding. A cache-neutral follow-up is therefore the next test, rather than a draft-depth
or group-count scan.

## Restart cost and restoration

The A-to-B transition was necessary because both the argument and environment are fixed at process
start.

- A stop took 8 seconds; B was health-loaded after 181 seconds in total (`3:01`).
- B stop took 9 seconds; A2 was health-loaded within 232 seconds of launch and 241 seconds of stop
  start (`4:01`).

A2 is restored with:

- server PID `520066`, engine PID `520075`, vision PID `520070`;
- engine sha256 `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
- config sha256 `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f`;
- `batch_slots=6`, `batch_groups=2`, `vram_free_mib=1006`, health `loaded=true`;
- no active `STRATA_PIPED_MTP`; `STRATA_BATCH_SELF_COMMIT=1`;
- production `strata.service` unchanged at `inactive/dead`, `MainPID=0`, `NRestarts=23`.

Final resource state remained safe: `13.5 GiB` RAM available, swap used `12 MiB`, both GPUs idle
after the workload. No production service or configuration file was changed.

## Evidence

- `/opt/ai/logs/util-piped-mtp-summary-20261031T1923Z.json`
  (sha256 `bf0c1cbcc2f671d41e4567130546d5ffcfbd8480965d6d5b2913b0acd8b297f2`)
- `/opt/ai/logs/util-piped-mtp-a1-20261031T1905Z.log`
  (sha256 `fab6a00cab9e2e22097024f6ce5c24c99debc93fa1729bb7bde2b4dc4aeedf2a`)
- `/opt/ai/logs/util-piped-mtp-B-diag-20261031T1914Z.log`
  (sha256 `0511c233767909433e1a5666f8eacaf66d5802a854892be37775a19fd949bfbb`)
- `/opt/ai/logs/util-piped-mtp-a2-20261031T1920Z.log`
  (sha256 `e3846bb52946717a1bd70abe783e2956158c605c9030733b62052e4fb7f709c0`)
- `/opt/ai/logs/util-piped-mtp-parity-A-20261031T1908Z.json`
  (sha256 `2ab2dcfc6dfed18e287b308ca5c560f34194164468aa58776fc4c54c30d94003`)
- `/opt/ai/logs/util-piped-mtp-parity-B-20261031T1912Z.json`
  (sha256 `145f697b105d28b75e519d27b68b15a5be4db41f0fe05c425cc32e5a48695fb6`)
- `/opt/ai/logs/util-piped-mtp-A2-final-metrics-20261031T1923Z.json`
  (sha256 `f5ae2401ef4ce3ec885636f68cc365eef5c1b01f3e1d0d353c7acb6b5d53196c`)
- `/opt/ai/logs/util-piped-mtp-switch-A-to-B-20261030T190729Z.txt`
- `/opt/ai/logs/util-piped-mtp-switch-B-to-A-20261030T191519Z.txt`

## Not measured

This run does not prove that pipelined MTP is inherently output-changing when both arms use the same
expert cache. It does not test a longer mixed soak, sampling, cancellation, 1/8 streams, AMD, or a
second model. The next contract isolates the cache difference before any further performance claim.
