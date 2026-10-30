# UTIL-HEAD-Q8-FUSE contract

Status: rejected on 2026-10-31 by the independent GPU gate. See
[`UTIL_HEAD_Q8_FUSE_RESULT-20261031.md`](UTIL_HEAD_Q8_FUSE_RESULT-20261031.md).
Source and independent GPU gates precede any server candidate or restart.

## Source-backed problem

The production engine runs with `STRATA_HC_Q8=1`. In the final verifier stage,
`Verifier::record_window` currently performs:

1. `gr_write_multi` (or `gr_write` for one row) to write
   `R' = R + block_out * 2*sigmoid(inject/hc)`;
2. `fused_gr_read_multi` with the Q8 down/up projections and `apply=false`, which
   reads `R'` again for the final mixer.

The Q8 kernel already implements `apply=true`: the down projection forms `R'` from
the old `R`, block output and injection, and the up projection writes the same `R'`
while producing `mixed`. Fusing therefore removes the separate write launch and
the extra read of `R'`; the final applied write remains part of the mixer.

Trigger conditions are the existing final-stage conditions:

- `STRATA_FUSE_HEAD_GR=1`;
- the final model layer on the head stage;
- a loaded plain native head;
- no conversation-cache override (`!cvec().cover(last layer)`);
- the GGUF Q8 hyper-connection projections are present.

This covers every captured main-model verifier window (single and batch, all
valid row counts and both split groups) when the production `STRATA_HC_Q8=1`
prerequisites hold. It does not cover MTP's BF16 draft mixer.

## Exactness hypothesis

The native `gr_write` post-op computes its gate with a reciprocal-multiply form,
`expf`, and explicitly rounded FMA operations. The current Q8 fusion uses a
division form and `__expf`. A bitwise candidate must reproduce the native
post-op expression for the final mixer while leaving the already production Q8
path for intermediate layers unchanged.

The harness compares the candidate against the current sequence:

```text
copy R
gr_write_multi(R, block_out, inject, R)
Q8 fused_gr_read_multi(apply=false, R)
```

and the candidate:

```text
copy R
Q8 fused_gr_read_multi(apply=true, R, block_out, inject)
```

For `T=1..8`, the following must be byte-identical:

- final `R`;
- `lo`;
- `rs`;
- `mixed`;
- the quantized `mixed` Q8_1 image used by the output projection.

Captured graph replay with changed inputs must also remain byte-identical.

## GPU source gate

Use the production geometry:

```text
n_embd=2560, hc=4, hc_lr=320
```

The independent harness must run at least five alternating processes. It measures
the current two-operation sequence and the fused operation with native post-op
arithmetic, including both kernel time and a graph containing each sequence.

The fastest observed stage-2 window is `18.27 ms`; the user accepts an improvement
of 1% or more. The minimum useful savings is therefore about `182.7 us` per such
window.

The source/GPU gate passes only if:

1. every byte comparison above passes for `T=1..8`;
2. changed-input graph replay matches the direct candidate;
3. at least one row count saves at least `182.7 us` against the fastest window,
   or an equivalently measured graph critical path demonstrates at least 1%;
4. no measured row count regresses by more than 1%;
5. the saving is attributable to the removed write/read and is not an artifact of
   stale inputs or skipped work.

If the maximum measured saving is below the threshold, reject without building a
server candidate or restarting Strata.

## Server gate

Only after the GPU gate passes, run same-boot A1-B-A2 with only:

```text
STRATA_FUSE_HEAD_GR=1
```

added to A. Require:

1. three frozen greedy probes twice per arm, byte-identical and equal between A
   and B, with the same finish reason;
2. three waves at `N=2/4/6/8`;
3. at least one level improves at least 1% against the A1/A2 combined median and
   wins at least 5/6 paired waves;
4. every other level regresses by no more than 1%;
5. A1-to-A2 drift is at most 3%;
6. zero HTTP/execution errors, unexpected finishes or foreign requests;
7. six slots, two groups, health, RAM, VRAM and swap stay in the existing safety
   envelope;
8. logs prove the fused Q8 path is active;
9. production systemd, config and runtime files remain unchanged.

## Restart cost and rollback

The harness runs without a Strata restart. A server candidate costs one A-to-B
load and one B-to-A restore, about `9 s` per stop plus `130–160 s` per load.
Do not spend that time unless the GPU gate has a credible 1% critical-path saving.

Keep `strata.service` inactive/dead and preserve the A engine/config. If any
correctness, throughput, drift, request, health or resource gate fails, restore
the exact A engine/config and verify hashes, health, slots, groups and GPU state.
Never write an API key or token to the repository, logs or conversation.
