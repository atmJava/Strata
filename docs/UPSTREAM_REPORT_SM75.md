# Upstream report: the Turing kernel series on an RTX 2080 Ti

Draft for an upstream issue (a change of default needs scope agreement first, [CONTRIBUTING.md](../CONTRIBUTING.md)
§Before starting 3). Not posted: this fork has no network route to GitHub from the machine that prepared it.

**Proposed title:** `[Engine] sm_75: the Turing kernel series is bitwise and +7% prefill here — turn it on by default?`

---

Problem: `STRATA_SM75_PERF` is off by default because nobody on the team has an `sm_75` card, and every 7.5 card
therefore runs the older kernels even though the newer ones are bitwise equal and faster. This fork has two of them
(2 × RTX 2080 Ti 22 GB) and measured the series end to end.

## Environment

- Hardware: 2 × RTX 2080 Ti **22 GB** (TU102, compute capability 7.5), NVLink, one layer split (24/24); Xeon E5-2680 v4
  (14 cores, no AVX-512), 62 GiB RAM; Linux 7.0.0-38, driver 595.71.05, CUDA 13.0.
- Build: source at upstream `011c9e60` (0.2.2) plus one local change to the concurrent read (the fork's
  `hw/s1-concurrent-read`); engine 0.1.47, built for `sm_75` only, Release, tests off.
- Model: a native IQ3_XXS pack, `--layer-split 24 --batch 4 --batch-groups 2`, INT8 K/V, 32,768 resident cells,
  the profile-filled expert cache (18,801 slots over both cards).

## Reproduce

```sh
# arm without the series: the production config, no STRATA_SM75_PERF
# arm with it: add STRATA_SM75_PERF=1 to the run config's "env" and restart (~150 s to ready)
# each arm: 3 interleaved rounds, medians; A-B-A (base, series, base again)
```

## Measurements (2026-10-23, one boot per arm)

| Workload | base-A | `STRATA_SM75_PERF=1` | base-B (reverted) | series again |
|---|---:|---:|---:|---:|
| 84K-token cold prefill (tok/s) | 2,375.6 | **2,552.0** | 2,381.3 | 2,566.5 |
| cold prefill, 43–50K prompt (tok/s) | 2,108 | **2,236** | 2,094 | 2,258 |
| four sessions at once, total (tok/s) | 111.3 | **119.7** | 112.5 | 117.1 |
| decode after 84K (tok/s) | 67.8 | 73.5 | 71.2 | 72.3 |

- Prefill +7.2 to +7.6% at 84K, +6.0 to +7.8% at 43–50K, four sessions +4.1 to +7.5%.
- Decode reads 67.9–78.0 with the series against 67.8–71.2 without: the direction is up but the spread is wider than
  the effect, so **no decode win is claimed**. The draft acceptance rate is what moves it (275/347 and 255/325 with,
  259/328 and 280/359 without).
- Restart-to-restart noise on this box is ±2% and the draft acceptance rate moves decode by about 0.6–0.7 tok/s per
  point, so the A-B-A shape above was used rather than a single arm.

## Exactness

In the configuration `docs/BATCHING.md` asks for (`STRATA_IQ_MT_MIN=1`, `--pcie-frac 0`, `--adapt-every 1000000`,
`--no-prefill-borrow`, one slot) both arms are stable and produced the **same 512 greedy tokens**
(`sha256 aac0a04d…`): four runs warm, plus three after a second restart.

One exception, reported as observed: the first request after one particular restart gave different text
(`00281f84…`) and did not come back on a repeat restart. I did not explain it. Outside that configuration the text
differs run to run anyway (two runs of the same prompt in one arm gave different hashes), so the special
configuration is the only place this check means anything.

## Proposal

Default the bitwise series on for compute capability 7.5 — the same shape as `arch_default_env()`'s gfx1151 table —
with every per-kernel variable still able to override it and `STRATA_SM75_PERF=0` restoring today's behaviour.
Alternatively, take these numbers into `docs/DETAILS.md` and leave the switch opt-in; either way the card stops
being unmeasured.

## A comment that says more than the code does

Reading these files for a different reason, we found two comments a reader would take as "an `sm_75` card cannot have
the fused prompt experts": `include/strata/prefill/moe_fused.hpp:2` ("NVIDIA sm_80 and newer; sm_75 and HIP: MMQ")
and `:31`, which describes `available()` as "the current device is sm_80 or newer". The code is not that.
`src/prefill/moe_fused.cu:652` accepts a 7.5 card when `STRATA_PF_FUSED_SM75=1`, and `:675` is
`dev_info().cc >= 75`. `src/prefill/moe_fused_iq.cu:1298-1307` goes further: it has a Turing branch that sets the
kernels up for `T_Q2_0`, and its comment — "the codebook formats do not fit 64 KB at 4 warps a row" — is the reason
this fork closed its own attempt at the IQ3_XXS pack without running anything.

The arithmetic in that same file says the comment is wrong for `IQ3_XXS`: its shared memory at four warps and two
stages is 60,672 B (`:110-113` with `:106-109`), 1,024 B more than the `Q2_0` format the branch does set up, against
a 64 KiB limit. Only `IQ2_S` is over at that shape (67,840 B). The branch returns at `:1307`, five lines above the
`setup_one` calls at `:1309-1311` that ask the driver whether it fits, so the claim has never been tested on any
card. **This paragraph is reading, not measurement — we have not run it.** We thought it worth reporting either way:
a comment that decides what a maintainer tries costs more than a comment that is merely wrong.

## What I did not test

- Only one card model: RTX 2080 Ti (22 GB and stock 11 GB share the TU102 die). No 2060/2070/2080, no Super variants,
  no CMP 50HX, no Windows.
- No HIP or SYCL build (this box has one backend); the series is CUDA-only code, but the change touches a header that
  both backends compile.
- The engine's own test suite was off in the release build, so the parity binaries were not run.
- The `--batch-groups 2` pipelined path, vision, and tool calls were exercised only through the serving config, not as
  separate correctness runs.
- The fused int8 prompt experts (`STRATA_PF_FUSED_SM75=1`) were not measured on this card at all. The paragraph above
  says only why they are worth measuring here.

## Reproduction status

Measured on the machine, A-B-A, with a boot named for every number. The exactness result is measured in the special
configuration; the one unexplained cold-run difference is reported as observed, not explained.
