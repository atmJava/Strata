# UTIL-GDN-GROUPS result

Run: 2026-10-31 on the 2×RTX 2080 Ti A instance, CUDA 13.0. A engine
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
candidate engine `d8795c15d5a1af45f1602b61ef872e6f6c993f7fedef920c67da90d0551ca374`.

## Decision

**UTIL-GDN-GROUPS is rejected.** The corrected grouped recurrence passed exact output and state
parity, but it did not deliver a stable release gain. At 2/4/6/8 streams its deltas against the
A3/A4 combined medians were `+0.777% / -8.011% / +0.913% / -1.386%`; paired wins were only
`2/6, 2/6, 3/6, 2/6`. A3-to-A4 drift was `-4.822% / -7.278% / -3.018% / +3.412%`.

No stream level reached both the 1% gain threshold and 5/6 paired wins. Four streams regressed
by more than 1%, and every level exceeded the 3% A-B-A drift limit. The candidate is not adopted.

## Path and source audit

The real engine log reports `KV streaming: 32768 of 262144 cells`, so `kv_mode=1`.
`STRATA_BATCH_ATTN_ROWS=1` is therefore unreachable in this instance and was rejected before a
candidate build.

The `STRATA_BATCH_GDN_GROUPS=1` recurrence path was reachable. Static audit found that its
self-commit interaction was incomplete: A enables `STRATA_BATCH_SELF_COMMIT=1`, while the grouped
window call discarded each row's keep pointer and passed `commit=false`. That computes outputs but
does not retain the new recurrent state. The candidate therefore carried only two functional
changes behind the existing opt-in:

- copy the self-commit keep pointer into every grouped window entry; and
- let the grouped kernel write state after all outputs when a keep pointer is present.

The default/non-self-commit path was unchanged. No group count, MTP depth, KV residency, QSA
schedule, checkpoint setting or lead-in value changed.

## Candidate isolation and parity

The candidate config is a deep copy of A plus only:

```text
STRATA_BATCH_GDN_GROUPS=1
```

It reports six slots, two groups, `expert_slots=17274`, `expert_cache_mib=28267`,
`vram_free_mib=1006`, health loaded, and the expected candidate engine hash. The production
systemd unit remained inactive/dead.

The full GPU harness ended `batch_rows_parity: ok` with zero OOM after A was stopped. New
self-committing window cases for 1/4/8 groups compared both row outputs and every slot's final GDN
state against per-slot calls with `memcmp`.

The three frozen greedy probes each ran twice against A and the candidate. Both candidate trials
were byte-identical to A:

- `STRATA_PARITY_ALPHA`;
- `944`;
- `The quick brown fox jumps over the lazy dog.`

All six requests finished with `stop`. Parity therefore passed.

## Throughput

Each cell is the aggregate median of three 400-token bursts. A3 ran immediately before candidate
startup; A4 ran after restoring the original A binary.

| Streams | A3 tok/s | Candidate tok/s | A4 tok/s | A combined median | Candidate delta | Wins | A3→A4 drift |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 78.8 | 77.8 | 75.0 | 77.2 | +0.777% | 2/6 | -4.822% |
| 4 | 111.3 | 102.2 | 103.2 | 111.1 | -8.011% | 2/6 | -7.278% |
| 6 | 122.6 | 121.6 | 118.9 | 120.5 | +0.913% | 3/6 | -3.018% |
| 8 | 105.5 | 106.7 | 109.1 | 108.2 | -1.386% | 2/6 | +3.412% |

Every benchmark window had zero foreign requests and no HTTP or execution errors. Candidate
draft acceptance stayed in the normal range at `79.78% / 79.35% / 79.77% / 79.16%`.

Eight streams still ran below six streams in both A and the candidate. This experiment did not
address the queued-tail mechanism; it only combined GDN recurrence launches inside each window.

## Restart cost and restoration

- A to candidate: stop about 9 seconds, startup to loaded `150` seconds.
- Candidate to A: stop about 9 seconds, startup to loaded `150` seconds.

The original A binary was preserved before rebuilding. The candidate source patch was reversed in
both trees after rejection. Final A is server/engine/vision PID `548696/548707/548699`, health
loaded, six slots, two groups, and engine/config SHA-256
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3` /
`88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f`.

Final RAM available was `13 GiB`, swap used `12 MiB`, and both GPUs were idle. Production
`strata.service` remained `inactive/dead`, `MainPID=0`, `NRestarts=23`.

## Evidence

- `/opt/ai/logs/qsa-diag-20261031T1653Z/f-gdn-groups.json`
  (sha256 `c033d81ffe07fc8819532499b7a170ad530c5f24ee76f99214bb4e80a5bbb5cb`)
- `/opt/ai/logs/util-gdn-groups-parity-kernel-freegpu-20261031T0025Z.log`
  (sha256 `0aab4ceb06430e55f0c6a25d36fdcf4335a16a3cecd431a7563ec945a1f78fbe`)
- `/opt/ai/logs/util-gdn-groups-parity-A-20261031T0010Z.json`
  (sha256 `32253bf35a25255c9ad3322086a76027184638fd2036760e8c2a3fa150b0cb93`)
- `/opt/ai/logs/util-gdn-groups-parity-B-20261031T0035Z.json`
  (sha256 `8c79eed8456f35e6d6ab09b1f10f9a9ccbe1e10fccf222418a611beb943af81b`)
- `/opt/ai/logs/util-gdn-groups-sameboot-A3-20261031T0015Z.log`
  (sha256 `a8aadf42ad9567721f4de293794c7c87e0ceb893fb26569f77286fd639ec252a`)
- `/opt/ai/logs/util-gdn-groups-sameboot-B-perf-20261031T0040Z.log`
  (sha256 `08c13be6da7885f5d24fa1f1c72dba0e464c1804a747214b3470a17a2c65fe03`)
- `/opt/ai/logs/util-gdn-groups-sameboot-A4-perf-20261031T0055Z.log`
  (sha256 `207763faeb443510dfb68dca281e187e49de47be94393df4fcdae643ec92fff8`)
- `/opt/ai/logs/util-gdn-groups-sameboot-summary-20261031T0105Z.json`
  (sha256 `974a855847544175faf9a847dd0148da60e3124d1e32d35f4ef9923aee975017`)

## Not measured

The throughput gate failed, so no long-context run, mixed soak or conversation checkpoint save
ran. The result does not establish whether another grouping of GDN conv, QSA resolve or scheduler
work can help.

## Next question

Move away from one-row launch aggregation. Profile the 2/4/6/8 decode windows with the existing
stage instrumentation and batch telemetry to separate active-row occupancy, stage hand-off,
streamed-KV work and slot-turnover cost before choosing the next source candidate.
