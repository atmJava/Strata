# UTIL-BATCH-HEAD result

## Decision

**Rejected at the source gate on 2026-10-31.** No candidate binary was built and no
Strata restart was used.

The profile's `1.04 ms/window` head phase and the duplicate per-group launch are real.
The proposed merge cannot preserve the existing two-group pipeline without a larger
bubble than the work it removes.

## Source control flow

With two stages, the pipelined batch path has two `PGroup` entries but only two
verifiers. Each verifier represents one stage, not one group:

- `generate.cpp` creates `pg` for the logical groups and `stage_group[k]` for the
  stages.
- `pump()` launches at most one group on each stage and stores its group index in
  `stage_group[k]`.
- `Verifier::batch_poll()` returns the stage's picks only after that group's whole
  final-stage graph has completed.
- `pump()` then appends the accepted tokens, marks that group not in flight, and
  releases the final stage for another group.

`record_window()` owns one `head_mixed_`, one `head_logits_`, one `m_out_`, and one
`arg_scratch_` per verifier. The last stage is therefore a strict serial resource:
group A runs layers, mixer, output projection and sampling; only then can group B
start its last-stage layers.

## Why delaying A is not free

A merged head needs A's mixed rows and picks to remain unsettled until B reaches the
same point. During that wait:

1. A cannot start its next stage-0 window because its next token has not been sampled.
2. B must finish its complete final-stage window before the merged projection can run.
3. The opposite stage is consequently unable to swap in A's next window.

The minimal bubble is therefore one stage window, measured here as about
`16.9–19.2 ms/window`, while removing only one roughly `1.04 ms` head call per merge.
Double-buffering A's mixed rows and outputs protects ownership but does not remove the
verdict dependency or the bubble.

## Claim boundary

This result rejects the direct merge in the current scheduler. It does not claim that
output-projection weight reads are irrelevant. A design that computes independent
vocabulary partitions concurrently, without delaying a group's verdict, remains a
different candidate.

## Evidence

- `src/program/generate.cpp`: `PGroup`, `stage_group`, `pump()`, and final-stage
  release after `batch_poll()`.
- `src/core/verify.cpp`: `record_window()` head buffers and `batch_poll()` output
  publication.
- `docs/work/UTIL_BATCH_HEAD_CONTRACT-20261031.md`.
- `docs/work/UTIL_WINDOW_PROFILE_RESULT-20261031.md`.

Not measured: candidate build, parity, throughput, memory, or restart behavior. There
was no candidate to measure.

## Next candidate

`UTIL-HEAD-SPLIT`: partition `output.weight` by vocabulary rows across the two GPUs and
compute the halves concurrently on private head streams. The design keeps the existing
group scheduler and verdict timing. The next gate is a GPU harness, not a server
restart.
