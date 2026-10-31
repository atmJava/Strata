# UTIL-LFUSE-SHARED result

## Decision

**UTIL-LFUSE-SHARED is rejected at the server throughput gate.** The existing exact shared-expert
launch fusion was reachable and byte-identical, but only eight streams improved. Six and eight
streams did not satisfy the pair-win gate, and two, four and six streams regressed by more than the
allowed one percent.

No source, production config, service, routing or user file was changed. The two candidate
environment variables were present only in the temporary B config. A2 is running from the exact
baseline config and binary.

## Reach and correctness

The live A process satisfies the native shared-expert, resident-expert, static-batch and
asynchronous-resident conditions required by `lfuse_on(n)`. At two streams each group has one row,
so LFUSE is not reached there. At four, six and eight streams it is reached with two, three and
four rows per group.

The real shared gate/up tensor types are IQ3_S, IQ4_XS, Q4_K, Q5_K and Q6_K. The Q8_0-only pair
branch is not counted.

Independent checks passed on both cards:

```text
mmvf_rows_parity: 432 cases, 1660200 values, 0 mismatches
fusions_parity: 0 failures
```

The candidate started with:

- engine sha256 `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
- candidate config sha256 `2c40636bb6fa9f9a593a8d8bb9452f534951321b94aa8cd2cba6a47334f11dcc`;
- six slots in groups `0,1`, health `loaded=true`;
- `STRATA_LFUSE=1` and `STRATA_MMVF_ROWS=1` visible in the engine environment.

The config diff contained only those two environment additions. B and A2 each ran the three frozen
greedy probes twice. All six rows matched in each arm, B and A2 matched each other byte for byte,
and every probe finished with `stop`.

## Throughput

The registered run was A1-B-A2 on one boot, with three 400-token waves at each of 2/4/6/8 streams.
Rates are aggregate output tokens per second.

| streams | A1 | B | A2 | A1/A2 combined | B delta | paired wins | A drift |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 85.449 | 74.602 | 72.174 | 77.3185 | **-3.513%** | 1/6 | **-15.536%** |
| 4 | 117.720 | 100.875 | 110.355 | 110.5865 | **-8.782%** | 1/6 | **-6.256%** |
| 6 | 123.243 | 119.019 | 121.402 | 122.3185 | **-2.697%** | 2/6 | -1.494% |
| 8 | 107.109 | 114.674 | 111.450 | 109.8805 | **+4.362%** | 4/6 | **+4.053%** |

Eight streams met the nominal 1% gain threshold, but only won four of six paired waves and the
baseline drift exceeded 3%. No 4/6/8 level met both the gain and 5/6-win requirements. Two, four
and six streams also exceeded the one-percent non-regression limit. The candidate therefore fails
multiple hard gates and is not retained.

All 12 waves completed in every arm. There were no script errors, HTTP/execution errors or foreign
requests. Finish reasons were only `length` or `stop`: A1 `length=12, stop=7`; B
`length=12, stop=6`; A2 `length=12, stop=8`.

## Restart and restored state

The B load took about 155 seconds from launch to loaded. The B-to-A2 transition stopped B, took
about 9 seconds to release the service, and loaded A2 in about 198 seconds from launch to health
loaded. The measured shell restored A2 automatically after the sweep.

Restored A2:

| item | value |
| --- | --- |
| server / engine / vision PID | `604897 / 604926 / 604905` |
| engine sha256 | `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3` |
| config sha256 | `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f` |
| engine environment | no `STRATA_LFUSE` or `STRATA_MMVF_ROWS` |
| shapes | six slots, groups `0,1`, 262144 context |
| free VRAM | `803 / 904 MiB` |
| RAM | `49 GiB` used, `13 GiB` available |
| swap | `12.3 MiB` used of `4 GiB` |
| production systemd | `inactive/dead`, `MainPID=0`, `NRestarts=23` |

A2 health remained loaded after the full sweep.

## Evidence

Directory:

```text
/opt/ai/logs/util-lfuse-shared-20261031T0929CST/
```

The directory contains the A1/B/A2 sweeps, startup and resource snapshots, parity results, decision
summary and process/config observations.

`all-sha256.txt` SHA-256:

```text
330132790c59ecb3a4f01468ed520c3e86af208faa97f1089f068c8298e0753b
```

## Not measured

No long-context, mixed traffic, natural-language parity, other model or non-CUDA backend was run.
The rejection is for the existing LFUSE launch path on this two-2080-Ti production shape.
