# UTIL-BATCH-FAIR-SERVE result

**Status: rejected at the server throughput gates. Not adopted.**

The default-off candidate passed the build, startup, real batch-path and exactness gates. Its
one-layer-per-stage-turn scheduling improved two streams, but four and eight streams regressed
by more than 1%, and no level met both the 1% gain and 5/6 paired-win gates. The exact A engine
and config are restored.

## Candidate

`STRATA_BATCH_FAIR_SERVE=1` makes `Verifier::batch_serve()` service at most one already-rung
layer per stage and then return to `pump()`. This gives the other pipeline stage a chance to
have its CPU expert plan published before the first stage drains additional doorbells.

The branch does not change the single-window path, per-layer callback order, row arithmetic,
plan contents, GPU launches, events, commits, sampling or finish handling. It is off by
default.

## Build and tests

The candidate was built from the A freeze source with only the `verify.cpp` opt-in branch:

| item | value |
| --- | --- |
| source | `/opt/ai/src/strata-batch-fair-serve-20261031T0355UTC` |
| engine | `/opt/ai/src/strata-batch-fair-serve-20261031T0355UTC/build75/strata` |
| engine sha256 | `0099f11a08fa3e8a5a58783e0fdc1e2c4fdb60cebb288c6044d3b7e31b3002b7` |
| `verify.cpp` sha256 | `b5a9a170d899f7af2ca947ccd3831685d864abf22c5884434cfde5ca4b5dafce` |
| build | Release, CUDA 75, Ninja, external ggml, MMQ K-quants, TQ overlay, Q6_K experts |
| tests | `batch_read_test` and `batch_telemetry_test`: 2/2 passed |

The build completed without errors. Its compiler warnings are baseline warnings. No other
CTest targets were built or claimed as run.

## Start, route and exactness

B loaded in about `148 s`, with six slots, two groups and health loaded. The running environment
contained `STRATA_BATCH_FAIR_SERVE=1`, and the candidate engine hash matched the build above.
The engine log contained both stage startup markers.

A six-stream probe produced 12 captured batch windows: four one-row, four two-row and four
three-row windows. Eight multi-row captures show that the production batch path was reached.
The startup marker plus the multi-row captures distinguish this candidate from an env-only
configuration change.

A1 parity was run before stopping A. B parity ran twice, and A2 parity ran after rollback.
All six frozen greedy responses in each arm were repeatable and byte-identical across A1, B1,
B2 and A2. Every result had `finish=stop`; there were no HTTP, execution, CUDA, never-rang or
timeout errors.

## Same-boot A1-B-A2 throughput

A1 and A2 baseline sweeps were collected earlier in the same host boot, immediately before
the candidate start, with the same `window-profile-sweep` harness. B ran the identical
2/4/6/8-stream, three-wave, 400-token matrix.

Rates are aggregate output tokens per second. Paired wins compare each B repetition with the
same A1 and A2 repetition, giving six comparisons per level.

| streams | A1 | B | A2 | A1/A2 combined | B delta | paired wins | A drift |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 76.809 | 80.451 | 71.381 | 73.876 | **+8.901%** | 5/6 | **-7.067%** |
| 4 | 115.342 | 109.691 | 115.882 | 115.612 | **-5.121%** | 3/6 | +0.468% |
| 6 | 125.511 | 129.836 | 128.785 | 127.148 | +2.114% | 4/6 | +2.609% |
| 8 | 114.787 | 111.750 | 111.930 | 114.747 | **-2.612%** | 2/6 | -2.489% |

The candidate fails the non-regression gate at four and eight streams. No level meets both
the 1% gain and 5/6 paired-win gates: two streams has enough wins but fails the A1/A2 drift
gate, while six streams has a positive median but only four paired wins. The registered
throughput matrix therefore rejects the candidate.

All three arms completed 60 requests and 12 waves with zero request errors and zero foreign
requests. Finish reasons were only `length` or `stop`: A1 `55/5`, B `54/6`, A2 `53/7`.
Health and six-slot/two-group shape remained loaded throughout B.

## Restart cost and recovery

B-to-A2 port release took about `9 s`. A2 took about `198 s` to load, so this trial used about
`6 minutes` of stop/load cost including the earlier A-to-B load. A server reboot was not needed.

The restored state is:

| item | value |
| --- | --- |
| server / engine / vision PID | `26243 / 26252 / 26247` |
| engine sha256 | `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3` |
| config sha256 | `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f` |
| candidate environment | absent |
| shape | six slots, groups `0,1`, 262144 context |
| A2 parity | passed; exact text and `finish=stop` |
| RAM / swap | about `14 GiB` available / `0 B` used |
| production systemd | inactive/dead, `MainPID=0` |

## Evidence

Directory:

```text
/opt/ai/logs/util-batch-fair-serve-20261031T0355UTC/
```

The directory contains A1/B/A2 parity, B sweep, pre/post hashes, startup and recovery
observations, build and test logs, configs and `all-sha256.txt`.

- A1 parity `048d47f8fdce3b3660173af9e7afe6a9bc158ebd48a40f72d25448cdfd0cab97`
- B1 parity `ec2af304c3dfcf4069ca9b470cf58f8949308a2a797d10e3eaf57967147eed9d`
- B2 parity `1e8bab2c198db93180ca910807c649858a6c84f2b69fe0f0192924a971433ffb`
- A2 parity `b793c46f7892691e38d008734cd9a347944722da3f2938f9dedceadc1fce934d`
- B sweep `1f9939df89bb909ae155c2d7255a8bdc5c5397c46766037eb214521af822c85b`
- A/B summary `baceff8ae3840dda27a42309f0d2a0d723fe971cf8e92ad656b13abdb2dd1563`
- startup gates `19b8d4a6c3d33fc1f1a52eb28592acdab19ad6c4fd59b5e47718b9696b9dbc0b`
- candidate tests `17b6d360657f6bfc46e4d7d27ec7ac7830af80cf65bb6c749f3dea6fb27c34ee`

## Not measured

No long-context, mixed long-prefill traffic, natural-language quality suite, other model or
non-CUDA backend was run. The rejection applies to this cross-stage fair-service schedule on
the current two-2080-Ti production shape.
