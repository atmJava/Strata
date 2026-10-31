# UTIL-Q5-HEAD-IL-GRID result

**Status: rejected at the performance-screening gate. Not built into the server and not
adopted.**

The exactness gate passed, but no capped persistent CTA grid came close to the pre-registered
18.5% head reduction. The best capped result was at `T=3`, where grid `2560` was only
`1.272%` faster than the plain kernel and `0.182` percentage points faster than the default
uncapped interleaved path. At `T=2`, every capped grid was slower than the default. A
seven-process confirmation and server A-B-A were therefore not warranted.

## Candidate and path

The bounded switch was:

```text
STRATA_MMVQ_IL_HEAD_GRID=<positive integer>
```

It capped the grid only for `n_out > 100000`, reaching the production
`2560 -> 248320` Q5_K output head. The existing `native_mmvq_il_kernel` row loop,
per-row arithmetic, reduction and dispatch were unchanged. The sm_75 rows-per-warp
selection was `R=4` at two columns and `R=2` at three and four columns.

The source change was removed after the screen. The runtime A2 environment does not
contain `STRATA_MMVQ_IL_HEAD_GRID`.

## Exactness and harness

The isolated build was:

```text
/opt/ai/src/strata-q5-head-grid-20261031T1115CST
```

Its `build75/mmvq_il_parity` run passed every case: `T=1..8`, output rows `1/2/4`, and
the real head table were bitwise equal to the current IL and plain-kernel references.
Malformed or absent values retained the original launch.

The timing logs named `screen2-gpu0-*` are the valid records. Earlier `screen-gpu0-*`
records used the wrong `MMVQ_IL_ONLY` filter and produced identical cells; they are not
evidence for this candidate.

## Performance screen

Each cell compares the selected interleaved kernel with the plain-kernel timing from the
same process. Absolute times drifted upward as the sequential grid scan progressed, so
cross-process absolute times are not compared.

| grid | T=2 selected rows | T=2 gain | T=3 selected rows | T=3 gain | T=4 selected rows | T=4 gain |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 160 | 4 | `+0.007%` | 2 | `-0.057%` | 2 | `+0.671%` |
| 320 | 4 | `+0.007%` | 2 | `-0.540%` | 2 | `0.000%` |
| 640 | 4 | `-0.006%` | 2 | `+0.298%` | 2 | `+0.005%` |
| 1280 | 4 | `+0.847%` | 2 | `0.000%` | 2 | `+1.377%` |
| 2560 | 4 | `0.000%` | 2 | `+1.272%` | 2 | `-0.390%` |
| 5120 | 4 | `+0.012%` | 2 | `0.000%` | 2 | `+0.128%` |
| off | 4 | `+1.017%` | 2 | `+1.090%` | 2 | `+0.010%` |

The required full-stage budget was about `0.183-0.192 ms`, or an 18.5% reduction in the
head. The best capped gain at `T=2` was `0.847%`; at `T=3`, grid `2560` had the best
capped gain of `1.272%`, still 15-17 percentage points below the gate. This is a
performance-screen rejection, not a claim that every possible launch geometry is
equivalent.

## State and evidence

The A2 server remains on the manual baseline:

| item | value |
| --- | --- |
| server / engine / vision PID | `13890 / 13906 / 13901` |
| health | `loaded=true` |
| slots / groups | `6 / 2` |
| engine sha256 | `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3` |
| config sha256 | `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f` |
| candidate env | absent |
| systemd | `inactive/dead`, `MainPID=0` |
| RAM / swap | about `14 GiB` available / `0 B` used |
| GPUs | idle; about `1006/1074 MiB` free |

Evidence:

```text
/opt/ai/logs/util-q5-head-il-grid-20261031T1120CST/
```

`UTIL-Q5-HEAD-IL-GRID-summary.json` sha256:

```text
71163c81167a730c5b44aaae4a535db8b4900273c8dac918f984636a55ae2d43
```

## Not measured

No candidate server build, restart, A1-B-A2 sweep, long-context traffic, other GPU model,
other backend or sampled-decoding run was made. This rejection covers only the fixed
persistent-CTA cap on the existing exact sm_75 Q5_K head kernel.
