# UTIL-WEIGHT-RO contract

Status: pre-registered before implementation.

## Evidence and selected problem

UTIL-NCU measured `17.50%` long-scoreboard stalls in
`native_gu_fused_kernel<18>`. The three rejected shared-memory candidates changed activation
loads, source-level weight reuse and codebook bank placement; none reduced machine-level load
counts or improved time. The latest padded four-copy layout was `2.86%` slower.

The current sm_75 SASS contains weight-side 16-bit loads as
`LD.E.U16.SYS`, while the kernel's read-only arguments use
`LDG.E.CONSTANT.SYS`. The IQ3 weight blocks are read-only for the kernel lifetime. This candidate
tests one different mechanism: route the IQ3 weight `qs`, auxiliary/sign word and scale through
NVIDIA's read-only load path, without changing shared-memory layout or arithmetic.

The hypothesis is specifically that the read-only cache path lowers long-scoreboard latency and
improves fused gate/up time. It does not claim that all `LD.E` instructions are avoidable.

## Candidate

Add a default-off `STRATA_IQ3_WEIGHT_RO` path to `DotG<18>` only in
`src/kernels/cuda/iq_kernels.cu`.

- keep the existing 256-word shared codebook and all bank addressing;
- keep the existing q8_1 activation reads, index extraction, signs, dp4a, scaling and stores;
- use explicit read-only 16-bit loads for the IQ3 weight `qs` and auxiliary word where their
  alignment requires 16-bit access;
- use an explicit read-only load for the IQ3 block scale;
- leave every other format's `DotG` and every non-fused kernel unchanged;
- unset or `0` must compile and run the existing path.

Before timing, inspect the candidate SASS. The intended target loads must change from
`LD.E.U16.SYS` to a `LDG...CONSTANT` form. If the machine code does not change, reject immediately
rather than treating a source-only edit as an experiment.

## Frozen workload

```text
native_expert_bench SHARD1 36 64 6 8 8 1
```

- IQ3_XXS gate/up and Q2_0 down, layer 36, 64 expert blobs, 6 entries per group;
- one idle GPU with `CUDA_VISIBLE_DEVICES=0`;
- seven alternating baseline/candidate processes, reversing arm order each pair;
- every arm must print output hash `9a055a5a94e15c87` and `0 differing`.

## Keep gate

All conditions must pass:

1. Every baseline and candidate process has output hash `9a055a5a94e15c87`;
2. candidate median gate/up time is at least `1.0%` lower than the equal-count baseline median;
3. at least `5/7` pairs favor the candidate and candidate p75 is below the baseline median;
4. candidate SASS proves that at least the principal IQ3 weight loads use the read-only path;
5. a bounded NCU pass reports long-scoreboard stall at or below `16.5%`, global-load instruction
   count no greater than `296,960`, registers at or below `85`, active warps at least `69%`, and
   no spill or launch failure;
6. default-path parity tests pass and both `STRATA_IQ3_WEIGHT_RO=0` and unset retain the old path.

Passing permits one controlled engine A/B on the fixed cold long prompt. The candidate must improve
prompt wall by at least `1%`, preserve the reference answer/needle, keep VRAM/RAM within existing
safety limits, and leave production systemd untouched.

## Reject gate

Reject and restore the baseline if any of these occurs:

- output changes;
- generated SASS does not use the read-only path;
- median speedup is below `1%`, results are tied, or one outlier carries the result;
- long-scoreboard stall remains above `16.5%` or global-load instructions increase;
- occupancy falls below `69%`, registers exceed `85`, spilling appears or launch fails;
- the engine arm does not improve wall by at least `1%` with quality and resources intact.

Do not scan load-cache operators, row-pair counts, NTC chunk sizes or nearby geometry after a
failed read-only cache path. A different mechanism needs new counters and a new contract.

## Baseline

- source sha256 `47009da533393ff4bcb62e6831427204237da3a1f9679ad4a691d4ddc897971b`;
- engine sha256
  `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
- process gate/up median from UTIL-GRID-PAD: `475.7 us`;
- NCU long-scoreboard stall `17.50%`, global-load instructions `296,960`;
- registers `73`, active warps `71.59%`.

## Cost and restart policy

Build and SASS inspection do not require stopping the candidate. Stop it once only before the
isolated GPU measurements. A restart costs about 2.5–3 minutes on this model shape; if the process
timing gate fails, do not run NCU or an engine A/B and restore/restart immediately.
