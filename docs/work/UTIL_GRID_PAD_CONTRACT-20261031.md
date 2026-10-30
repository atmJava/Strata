# UTIL-GRID-PAD contract

Status: pre-registered before implementation.

## Evidence and selected problem

The first four-copy codebook experiment stored a logical word at `4 * index + replica`. That maps
to bank `4 * (index mod 8) + replica`: each lane replica can reach only eight of the 32 shared
banks. The candidate therefore increased rather than reduced conflicts.

UTIL-WEIGHT subsequently proved that the compiler already reuses the activation and weight-side
loads it can see. The remaining measured MIO source is the random codebook load itself:
`10,583,040` shared-load instructions and `17,698,406` wavefronts in the baseline.

This candidate keeps four codebook copies, but gives every copy a `257`-word stride:

```text
physical(index, replica) = replica * 257 + index
bank(index, replica)     = index + replica (mod 32)
```

Each replica can therefore reach all 32 banks, and lane replica selection rotates identical
indices across four banks. Stage the same 256 codebook values into each copy. Choose replica
`lane & 3` for each lookup. Indices, sign handling, dp4a, scales, row sums, SwiGLU, q8_1
quantization and stores remain unchanged.

The path is opt-in with `STRATA_IQ3_GRID_PAD`; unset or `0` keeps the existing one-copy path.
Change only `src/kernels/cuda/iq_kernels.cu`.

## Frozen workload

```text
native_expert_bench SHARD1 36 64 6 8 8 1
```

- IQ3_XXS gate/up and Q2_0 down, layer 36, 64 expert blobs, 6 entries per group;
- one idle GPU with `CUDA_VISIBLE_DEVICES=0`;
- seven alternating baseline/candidate process runs;
- baseline output hash `9a055a5a94e15c87`.

## Keep gate

All conditions must pass:

1. Every candidate run has output hash `9a055a5a94e15c87`;
2. candidate median gate/up time is at least `1.0%` lower than the equal-count baseline median;
3. at least `5/7` paired runs favor the candidate and candidate p75 is below the baseline median;
4. a bounded NCU pass reports MIO throttle at or below `28.75%` and shared-load wavefronts below
   `17,698,406`;
5. achieved active warps remain at least `69%`, registers stay at or below 85 per thread, and
   there is no spill or launch failure;
6. default-path parity tests pass, and `STRATA_IQ3_GRID_PAD=0` retains the old path.

Passing permits one controlled engine A/B on the fixed cold long prompt. The candidate must improve
prompt wall by at least `1%`, preserve the reference answer/needle, keep VRAM/RAM within existing
safety limits, and leave production systemd untouched.

## Reject gate

Reject and restore the baseline if any of these occurs:

- output changes;
- median speedup is below `1%`, results are tied, or one outlier carries the result;
- MIO does not fall below `29.75%` or shared-load wavefronts do not fall;
- occupancy falls below `69%`, registers exceed 85, spilling appears or launch fails;
- the engine arm does not improve wall by at least `1%` with quality and resources intact.

Do not scan replica counts or nearby strides in this goal after a failed four-copy padded layout.
A different mechanism needs new counters and a new contract.

## Baseline

- source sha256 `47009da533393ff4bcb62e6831427204237da3a1f9679ad4a691d4ddc897971b`;
- engine sha256
  `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
- process A/B gate/up median `475.8 us` in UTIL-WEIGHT;
- NCU shared-load instructions `10,583,040`, wavefronts `17,698,406`;
- NCU MIO throttle `29.75%`, active warps `71.59%`.
