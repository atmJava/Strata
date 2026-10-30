# UTIL-WEIGHT contract

Status: pre-registered before implementation.

## Evidence and selected problem

UTIL-ACT proved that the compiler already shared q8_1 activation loads across the paired gate/up
dots: candidate and baseline both executed `10,583,040` shared-load instructions. The remaining
large MIO source is weight-side work inside the NTC token loop.

For every k and each token in an NTC chunk, `native_gu_fused_kernel<18>` rereads the q3 bytes and
looks up eight IQ3_XXS codebook words for both the gate and up row. The same weight k is used by
up to four tokens in a chunk. The candidate will decode the weight-dependent values once per k:

- codebook words for the eight four-value groups;
- `aux32`, integer scale and fp16 block scale;

then apply the unchanged sign masks, dp4a operations, integer scale, float expression and row
accumulation to each token. A one-row working set keeps register growth bounded. The output order
for each `(row, token)` remains unchanged.

The path is opt-in with `STRATA_IQ3_WEIGHT_REUSE`; unset or `0` keeps the existing kernel selected.
Change only `src/kernels/cuda/iq_kernels.cu`. Do not change the row pair, CTA geometry, number of
warps, codebook layout, activation contents, SwiGLU, quantization or output stores.

## Frozen workload

```text
native_expert_bench SHARD1 36 64 6 8 8 1
```

- IQ3_XXS gate/up and Q2_0 down, layer 36, 64 expert blobs, 6 entries per group;
- one idle GPU with `CUDA_VISIBLE_DEVICES=0`;
- seven alternating baseline/candidate process runs;
- baseline output hash `9a055a5a94e15c87`.

With six entries, the kernel uses one four-token chunk and one two-token chunk. Expected codebook
lookups therefore drop by about 2.5x overall if the compiler does not already perform this reuse.

## Keep gate

All conditions must pass:

1. Every candidate run has output hash `9a055a5a94e15c87`;
2. candidate median gate/up time is at least `1.0%` lower than the equal-count baseline median;
3. at least `5/7` paired runs favor the candidate and candidate p75 is below the baseline median;
4. a bounded NCU pass reports MIO throttle at or below `28.75%` and shared-load wavefronts below
   `17,698,406`;
5. achieved active warps remain at least `69%`, registers stay at or below 85 per thread, and
   there is no spill or launch failure;
6. default-path parity tests pass, and `STRATA_IQ3_WEIGHT_REUSE=0` retains the old path.

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

Do not scan nearby NTC/chunk or row-pair geometries in this goal after a failed weight-reuse
implementation. A different mechanism needs new counters and a new contract.

## Baseline

- source sha256 `47009da533393ff4bcb62e6831427204237da3a1f9679ad4a691d4ddc897971b`;
- engine sha256
  `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
- process A/B gate/up median `475.1 us`;
- NCU shared-load instructions `10,583,040`, wavefronts `17,698,406`;
- NCU MIO throttle `29.75%`, active warps `71.59%`.
