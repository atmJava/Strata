# UTIL-B6-ROWS contract

## Objective

Determine whether the existing, exact `STRATA_B6_MMVQ_ROWS=1` row layout can improve the
production six-slot, two-group decode window on two RTX 2080 Ti cards by at least 1% without
taking a server restart on weak evidence.

The candidate is not a parameter sweep. Only environment value `1` is tested: two output rows
per block. Values `2`, `4` and `8` are outside this contract.

## Source path and reach gate

The source contains two relevant layouts:

- default Turing production path: `native_mmvq_il`, selected by `STRATA_SM75_PERF=1`, for
  Q4_K/Q5_K/Q6_K/IQ4_XS and two to four columns when its measured table returns non-zero;
- B6 row layout: `b6_mmvq_rows_kernel`, entered through `native_mmvq`, for Q4_K/Q5_K/Q6_K and
  IQ4_XS when `STRATA_B6_MMVQ_ROWS=1`.

The live A3 process has `STRATA_SM75_PERF=1` and no `STRATA_B6_MMVQ_ROWS`, so the baseline takes
the IL path where available. With the current source, setting only `STRATA_B6_MMVQ_ROWS=1`
does **not** replace those IL calls. A server candidate is therefore forbidden unless B6 beats
the actual IL-selected kernel, not merely the older exact `native_mmvq` kernel.

Production plain pipelined groups have three slots each at six requests, and four each at eight
requests. The relevant group widths are therefore two through four; width one is the
single-column control. Widths five and eight remain stress/parity controls but cannot by
themselves justify a server test.

## Frozen inputs

Remote source root:

```text
/opt/ai/src/strata-freeze-throughput-20261029T0812Z
```

The remote worktree is intentionally dirty and belongs to the user. This contract does not
modify, reset, clean or check it out. The experiment reuses build directory `build75`, which
has Release, CUDA architecture 75, tests, `STRATA_MMQ_KQUANTS=ON`,
`STRATA_MMQ_TQ_OVERLAY=ON` and `STRATA_Q6K_EXPERTS=ON`.

Source hashes at pre-registration:

| file | sha256 |
| --- | --- |
| `src/kernels/b6_mmvq_bench.cpp` | `5363a5842616f2c9eeed1df780b09eadf7dc4c2f1966116a25fa408850c8c60e` |
| `src/kernels/mmvq_il_parity.cpp` | `cd61b11e77f163cc5af3b188e98416e739c9a6efb8ddb5412069da518f044547` |
| `src/kernels/mmvf_rows_parity.cpp` | `1ee1c71a01e306fc619958ea0a9e36517db248afadb30668f3931ce8ec2be225` |
| `src/kernels/cuda/native_mmvq.cu` | `2260db1bd24794596ba4c1dd01a2a754707ffbbef47b648bf38d3922c813fae8` |
| `include/strata/kernels/native_mmvq.hpp` | `0c284577e02c67c09376799c376e1c575c01e01ca42d56891e3b61460301ec92` |

The current A3 server remains running unless GPU memory or measured contention makes the
isolated microbench invalid. The microbench needs less than 1 GiB of transient allocation;
nvidia-smi reports about 1.3 GiB free on each 22 GiB card and zero compute utilization before
the run.

## Commands

Build only the existing lab targets in the A build directory:

```sh
cmake --build build75 --target b6_mmvq_bench mmvq_il_parity mmvf_rows_parity -j2
```

Initial exact-layout comparison:

```sh
STRATA_B6_MMVQ_ROWS=1 build75/b6_mmvq_bench 500 -1 1
```

Production-path comparison, where `--bench` times `native_mmvq` (B6 under the environment) and
each exact IL rows choice on the same Q8_1 bytes:

```sh
STRATA_B6_MMVQ_ROWS=1 build75/mmvq_il_parity --bench
```

Adjacent BF16 multi-row control:

```sh
STRATA_MMVF_ROWS=1 build75/mmvf_rows_parity
```

Each command runs on both cards. Raw stdout, stderr, hashes and environment are saved under
one timestamped `/opt/ai/logs/util-b6-rows-*CST/` directory.

## Gates

The first run is a screening run. It may reject immediately; it may not adopt.

1. Build and source gate: the three targets build with the frozen CMake options, source hashes
   stay unchanged, and no server process, config or runtime environment changes.
2. Correctness gate: every `b6_mmvq_bench` case/width reports B6 equals default, default equals
   independent one-column calls, and B6 equals one-column calls; no output is non-finite.
   `mmvq_il_parity` with B6 enabled must end `OK` with zero bit differences across every case,
   `T=1..8`, table choice and explicit IL rows 1/2/4. `mmvf_rows_parity` must report zero
   mismatches.
3. Reach gate: B6 must beat the IL rows choice actually selected by the sm_75 table for at
   least one production width two through four of at least one dense case. If B6 loses every
   such cell, there is no candidate to connect and the result is rejected.
4. Performance screening: a positive reach gate must also show a median B6 gain of at least 1%
   over that selected IL path, not over the slower default exact layout. A result below 1% is
   rejected without a restart.
5. Seven-process confirmation: only after screening passes, alternate seven independent
   processes on the idle cards. Require a conservative weighted median B6 gain of at least 1%
   over the actual IL-selected production paths, at least 5/7 paired wins, no production width
   regressing by more than 1%, and a conservative projected saving of at least 1% of the
   measured stage window.
6. Only a fully positive kernel gate may add a default-off source routing change from IL to
   B6. That source change must first pass parity again. Then one A1-B-A2 server matrix at
   2/4/6/8 streams, three waves per level and arm, must satisfy the existing gates: one level
   at least 1% with 5/6 paired wins, every other level no worse than 1%, A drift at most 3%,
   exact frozen greedy parity, six slots/two groups, no errors or foreign requests, and safe
   memory.

Failure at any gate stops the candidate. The A3 process is not restarted merely to test a
kernel path that cannot replace IL.

## Screening outcome

The screening run completed on 2026-10-31. All exactness checks passed, but the production-path
reach and performance gate failed: B6 won 0/22 paired width-two cells, its T=2 median was
`-5.868%` versus selected IL, and Q5_K/Q6_K cells regressed by 16-39%. The candidate is
rejected. No routing change, server build or restart followed. Full evidence is in
[`UTIL_B6_ROWS_RESULT-20261031.md`](UTIL_B6_ROWS_RESULT-20261031.md).

## Rollback

There is no server candidate in the screening phase. If a later source route is built, it is
default-off and stored in a separate build directory; A3 stays running until the candidate has
passed kernel parity and the restart cost is justified. A failed server candidate is stopped
and A3 is restarted from the frozen engine/config paths already recorded in `STATE.md`.

No API key, token or private prompt is written to evidence.
