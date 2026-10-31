# UTIL-LFUSE-SHARED contract

## Objective

Determine whether the existing exact shared-expert launch fusion can improve the production
two-stage decode window on two RTX 2080 Ti cards by at least 1%, without regressing another
concurrency level by more than 1%.

The candidate is environment-only:

```text
STRATA_LFUSE=1
STRATA_MMVF_ROWS=1
```

No source, engine, config argument or production service change is part of the candidate.

## Source-backed reach

The live A process uses `--native` and `--resident-experts`, so all of these are true:

- `shared_expert_native_bf16_enabled()`;
- `native_moe_combine_enabled()`;
- `dec_batch`;
- `ar_on()` (100% of routed experts resident in VRAM);
- the shared expert runs on its forked CUDA stream.

`Verifier::record_window` enables the existing `lfuse_on(n)` branch only when all of the above
hold and the current group has two to eight rows. At eight requests the two groups have four
rows each; at six they have three each; at four they have two each. At two requests each group
has one row, so this candidate must leave two-stream behavior unchanged.

With `STRATA_MMVF_ROWS=1`, the multi-row BF16 router GEMV accepts a non-null auxiliary weight:

1. the shared expert's scalar gate row is computed in the same launch as the routed-expert
   router GEMV;
2. `shared_expert_multi` defers the per-row sigmoid and scale;
3. the resident-hit combine computes `sigmoid(shared_gate)`, multiplies the unscaled shared row
   by it, then adds the result with the same rounding as the former separate scale launch.

The projected ceiling is the measured `shared+quant` cost in the current window profile:
`0.57 ms` on stage 1 and `0.60 ms` on stage 2, or `1.17 ms` of a `10.96 + 12.68 = 23.64 ms`
two-stage window. A 1% threshold is `0.236 ms`, so removing roughly one fifth of this stage is
enough. The shared branch is forked, so a positive end-to-end result is not assumed from this
ceiling alone.

## Pair branch is not counted

`native_mmvq_pair` supports Q8_0 only. The real shared-expert gate/up types are IQ3_S, IQ4_XS,
Q4_K, Q5_K and Q6_K, and the two projections are often different types. The pair branch
therefore falls back to the ordinary two native MMVQ calls. Its launch count is not credited
to this candidate.

The source comments claim bitwise equality, but that claim is not accepted as a measurement.
The candidate must still pass the independent row/aux parity harnesses and frozen server text
parity before throughput counts.

## Frozen baseline

Remote source and build:

```text
/opt/ai/src/strata-freeze-throughput-20261029T0812Z
build75
```

A engine sha256:

```text
105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3
```

A config sha256:

```text
88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f
```

A runtime:

```text
server/engine/vision 593069/593112/593073
health loaded, 6 slots, groups 0,1
```

The production systemd unit remains inactive/dead throughout. The candidate config is a deep
copy whose diff must contain only the two environment keys above.

## Gates

### Independent correctness

Run `mmvf_rows_parity` and `fusions_parity` on each card with the candidate flags. Every checked
value must be bitwise equal. No model, server or production file is changed by these harnesses.

### Startup and path

B must start on both cards with six slots, two groups, health `loaded=true`, the exact candidate
engine/config hashes, and both candidate environment keys visible in the engine process.
`STRATA_LFUSE_GATE` and `STRATA_LFUSE_PAIR` stay at their default-on values. The source reach
above identifies the selected 4/6/8-stream branch; no unexecuted pair fusion is claimed.

### Correctness

Run the same three frozen greedy probes used by the preceding server experiments, twice in B
and twice in A2. B must be byte-identical within itself and to A, with the same finish reason.
A2 repeats the same check.

### Performance

Run same-boot A1-B-A2 with fixed 400-token prompts at `N=2/4/6/8`, three waves per arm. Require:

1. at least one of 4/6/8 streams improves at least 1% against the combined A1/A2 median and
   wins at least 5/6 paired waves;
2. every other level regresses by no more than 1%;
3. two streams remains within that same non-regression limit, as its one-row groups do not take
   the LFUSE branch;
4. A1-to-A2 drift is at most 3%;
5. zero HTTP or execution errors, no unexpected finish reason and no foreign request;
6. health, six slots, two groups, RAM, VRAM and swap remain safe.

A user-accepted 1% gain is sufficient. No warm-up outlier is discarded.

## Restart cost and rollback

The test needs one A-to-B and one B-to-A load, about 9 seconds of stop time plus 130-160 seconds
of model loading per transition. The microbenchmarks cost no restart. If any gate fails, restore
A and verify its hashes, health, PID, slots, groups, runtime environment and resources.

Do not write an API key, token or private prompt to the repository, logs or conversation.

## Outcome

**Rejected.** Both independent parity harnesses and the frozen B/A2 probes passed, and the candidate
started with six slots / two groups and the exact expected binary. Throughput did not pass the
registered gates:

| streams | A1/A2 median | B median | B delta | paired wins | A drift |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 77.3185 | 74.602 | **-3.513%** | 1/6 | **-15.536%** |
| 4 | 110.5865 | 100.875 | **-8.782%** | 1/6 | **-6.256%** |
| 6 | 122.3185 | 119.019 | **-2.697%** | 2/6 | -1.494% |
| 8 | 109.8805 | 114.674 | **+4.362%** | 4/6 | **+4.053%** |

Only eight streams reached the nominal gain, and it still failed the 5/6 pair-win and 3% drift gates.
Two, four and six streams exceeded the one-percent non-regression limit. The candidate was not
retained. A2 was restored from the exact baseline config and binary, with no LFUSE/MMVF environment
variables in the engine; full evidence is in
[`UTIL_LFUSE_SHARED_RESULT-20261031.md`](UTIL_LFUSE_SHARED_RESULT-20261031.md).
