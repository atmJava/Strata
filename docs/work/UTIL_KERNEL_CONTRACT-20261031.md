# UTIL-KERNEL contract

Status: rejected; see [`UTIL_KERNEL_RESULT-20261031.md`](UTIL_KERNEL_RESULT-20261031.md). This file
records the only candidate allowed by the original UTIL-NCU evidence.

## Selected problem

Target:

```text
strata::kernels::native_gu_fused_kernel<18>
```

File: `src/kernels/cuda/iq_kernels.cu`.

NCU shows 29.75% MIO throttle, 71.59% achieved active warps, and 58.07% SM throughput. The
codebook lookup path is shared-memory heavy. The candidate will replicate the 256-entry
IQ3_XXS codebook four times in shared memory with a bank rotation and choose a replica by lane.
This changes where an identical codebook value is read; it does not change indices, products,
sum trees, SwiGLU, quantization, or stores.

The path is opt-in. Unset `STRATA_IQ3_GRID_REPL` must leave the existing kernel selected and its
machine behavior unchanged.

## Frozen workload

```text
native_expert_bench SHARD1 36 64 6 8 8 1
```

- candidate binary sha256
  `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
- layer 36, IQ3_XXS/Q2_0, 64 expert blobs, 6 entries per group;
- candidate launches `native_gu_fused_kernel<18>` on the first arm;
- GPU clocks and caches stay uncontrolled, as in the profiling run;
- process arms alternate and run only while both GPUs are otherwise idle.

Baseline output hash: `9a055a5a94e15c87`.

## Keep gate

All conditions must pass:

1. Candidate and baseline output hashes are identical for the frozen workload.
2. Seven alternating process runs have equal sample counts; candidate median gate/up time is at
   least 1.0% lower than baseline.
3. The candidate median is not carried by one outlier: at least five of seven paired runs favor
   the candidate, and the candidate 75th percentile is below the baseline median.
4. A bounded NCU repeat reports MIO throttle at or below 28.75%, down at least one percentage
   point from 29.75%.
5. Achieved active warps remain at least 69%, with no register spill and no launch failure.
6. Default-path unit/parity tests pass, and setting `STRATA_IQ3_GRID_REPL=0` still selects the
   unchanged path.

The first bounded kernel gate is a screen. Passing it permits one A/B of the engine on the same
fixed cold prompt, not an immediate default change. The engine arm must also improve prompt wall
by at least 1%, preserve the reference answer/needle, stay inside the existing VRAM/RAM limits,
and leave production systemd untouched.

## Reject gate

Reject and restore the baseline if any of these occurs:

- output hash changes;
- kernel median is slower, tied, or less than 1% faster;
- the MIO stall does not fall below 29.75%;
- adding replicas lowers active warps below 69% or spills registers;
- the result is explained by one outlier or by a profile-only artifact;
- a full engine arm does not improve wall by at least 1% with quality and resources intact.

Do not scan replica counts 2/8/16 after a failed 4-way test in the same goal. A different
mechanism needs new evidence.

## Baseline measurements

- uninstrumented gate/up: about 0.4741 ms;
- NCU kernel duration: 653.18 us, counters as listed above;
- output hash: `9a055a5a94e15c87`;
- report:
  `/opt/ai/logs/ncu-micro-gu-metrics-20261030T180641Z.ncu-rep`;
- decision:
  `/opt/ai/logs/utilization-decision-20261031-UTIL-NCU.json`.
