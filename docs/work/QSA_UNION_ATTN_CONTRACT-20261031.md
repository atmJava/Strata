# QSA-UNION-ATTN pre-registered contract

Registered before the implementation on 2026-10-31.

## Question

Can co-resident prompt-attention work for three adjacent queries reuse selected K/V data that
separate CTAs would fetch again, and improve both end-to-end wall time and `qsa attn` by at least
1% on the same cold long prompt?

This is one exact implementation experiment. It does not change indexer scores or top-k, and it
does not adopt the grouped path unless every gate below passes.

## Source and resource audit

- The default int8 KV path is `prompt_attn_i8_kernel` in
  `src/kernels/cuda/qsa_prompt_attn.cu`: one 128-thread CTA per `(query, KV head)`, four warps
  with disjoint 64-dimension slices.
- `cuobjdump --dump-resource-usage` on the baseline `pa_bench` reports 168 registers per thread
  and 19,712 bytes of dynamic shared memory for that kernel.
- 168 registers x 128 threads = 21,504 registers, so three CTAs use 64,512 of the 65,536
  registers per SM. Three CTAs also use 59,136 of the 64 KiB shared memory per SM. The baseline
  therefore resides at exactly three CTAs, or 12 warps, per SM.
- A three-query CTA has 384 threads, 64,512 registers at the same 168-register target, and
  59,136 bytes of dynamic shared memory. It preserves the same 12 resident warps and the same
  per-query shared-memory layout. No query is combined into a larger numerical operation.

## Implementation boundary

The experiment is opt-in through `STRATA_PA_GROUP3=1` and applies only to the int8 K/V v2 path on
sm_75.

- Compute each adjacent query's selected cells, indexer scores, q.k values, online softmax and
  p.v output exactly as the default kernel does.
- Give each query its existing `Smem2T` region and its existing 128-thread arithmetic mapping.
- Schedule queries `3t`, `3t+1` and `3t+2` in one CTA so their same-chunk K/V requests reach the
  same SM cache. The groups stay phase-aligned at the existing score and softmax barriers.
- Do not form or sort a union of selected cells. Do not skip a load in software and do not use a
  union to mask arithmetic. Reuse is hardware-cache reuse of the unchanged loads.
- Handle the final partial tile and queries with different chunk counts without changing any
  valid query's arithmetic.
- Keep the existing single-query kernel and dispatch as the default. Leaving the environment
  variable unset must retain the old kernel, output, resource use and restart behavior.
- If the grouped CTA exceeds the 64 KiB shared-memory limit, exceeds the register budget, spills
  to local memory, fails to launch on either GPU or changes valid output bits, reject it without
  attempting a long-request performance claim.

## Fixed checks

Before the long request:

1. Build the affected sm_75 target and record compiler/SASS resource use.
2. Run `qsa_prompt_attn_parity` against the FP64 and old references in both default and grouped
   modes. The grouped output hash must equal the default output hash for each unchanged input.
3. Run the existing QSA CTest set and the Python server/terminal tests.
4. On a recorded or deterministic selection with the measured 28% adjacent-query sharing,
   compare default and grouped `pa_bench` medians. At least three alternating samples are
   required. A grouped median at or above the default is a stop condition for this mechanism.

## Long-request gate

Run the existing deterministic cold prompt near 150K tokens with `STRATA_PREFILL_TIMING=1`.
The same boot and binary must support an A-B-A switch:

- A1: `STRATA_PA_GROUP3` unset;
- B: `STRATA_PA_GROUP3=1`;
- A2: `STRATA_PA_GROUP3` unset.

Every sample must have `cached_tokens=0`, no HTTP or engine error, a normal stop rather than
unexpected `length`, and a correct needle answer. Record wall time, prompt time, the `qsa attn`
timeline and its share, minimum MemAvailable, SwapFree, both GPUs' peak memory, loaded status and
runtime environment.

Accept only if:

- both B-minus-neighbouring-A wall changes are positive and their median is at least 1%;
- both B-minus-neighbouring-A `qsa attn` changes are positive and their median is at least 1%;
- `qsa attn` has the same direction in both comparisons, so the end-to-end result is attributable
  to this path rather than unrelated layer timing;
- attention output parity, selection parity and needle checks pass;
- there is no OOM, swap use, engine restart, slot regression or production systemd change.

A single noisy win is not acceptance. The default path remains the fallback even if the opt-in
path is accepted. Rejection requires restoring the tested default engine byte-for-byte,
documenting the negative result, committing and pushing the evidence, and moving to UTIL-NCU.

## Restart budget

Restarts cost roughly 2.5-3 minutes and discard warm caches. The experiment allows at most:

- one candidate restart for a synthetic-selection microbenchmark if it cannot run in the current
  idle process;
- one candidate restart to stage the grouped and default binaries for the same-boot A-B-A;
- one recovery restart if an A-B-A request fails;
- one final restore/health restart if needed.

Any additional restart must have a concrete diagnostic reason recorded in the iteration log.
Production `strata.service`, its configuration and its runtime directories remain untouched.
