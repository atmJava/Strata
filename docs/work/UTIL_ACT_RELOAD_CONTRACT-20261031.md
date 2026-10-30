# UTIL-ACT contract

Status: pre-registered before implementation.

## Evidence and selected problem

The rejected UTIL-KERNEL run showed that codebook replication does not lower bank conflicts on the
random IQ3_XXS index distribution. The follow-up counters instead found:

- `10,583,040` shared-load instructions;
- only `296,960` global-load instructions;
- `17,698,406` shared-load wavefronts from the one-copy baseline.

In `native_gu_fused_kernel<18>`, each k iteration calls the IQ3_XXS dot routine once for a gate
row and once for its paired up row. Both calls use the same q8_1 activation block and scale, but
the current routine reloads those shared-memory values for each row.

The candidate will explicitly load each q8_1 activation block into registers once per k iteration
and pass the same values to both row dots. The eight integer words, scale and every dp4a/sum
operation remain the same, and the two row results are added in the same order. The candidate is
opt-in with `STRATA_IQ3_ACT_RELOAD`; unset or `0` keeps the existing kernel path.

Change only `src/kernels/cuda/iq_kernels.cu`. Do not alter the codebook layout, codebook indices,
q8_1 quantization, SwiGLU, output stores, row pairing, number of warps or CTA geometry.

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
4. a one-launch NCU pass reports MIO throttle at or below `28.75%` and shared-load wavefronts below
   the `17,698,406` baseline;
5. achieved active warps remain at least `69%`, registers stay at or below 85 per thread, and
   there is no spill or launch failure;
6. default-path parity tests pass, and `STRATA_IQ3_ACT_RELOAD=0` retains the old path.

The microkernel gate is only a screen. Passing permits one controlled engine A/B on the same fixed
cold long prompt. The candidate must improve prompt wall by at least `1%`, preserve the reference
answer/needle, keep VRAM/RAM within the existing safety limits, and leave production systemd
untouched.

## Reject gate

Reject and restore the baseline if any of these occurs:

- output changes;
- median speedup is below `1%`, results are tied, or the result depends on an outlier;
- MIO does not fall below `29.75%` or shared-load wavefronts do not fall;
- occupancy falls below `69%`, registers exceed 85, spilling appears or launch fails;
- the engine arm does not improve wall by at least `1%` with quality and resources intact.

Do not retry the same activation-hoisting shape with another CTA/row geometry in this goal. A
different mechanism needs new counters and a new contract.

## Baseline

- source sha256 `47009da533393ff4bcb62e6831427204237da3a1f9679ad4a691d4ddc897971b`;
- restored engine sha256
  `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
- process A/B gate/up median `474.1 us`;
- NCU shared loads `10,583,040`, wavefronts `17,698,406`, bank conflicts `7,115,366`;
- NCU MIO throttle `29.75%`, active warps `71.59%`.
