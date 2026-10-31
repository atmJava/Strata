# UTIL-POLL-TURNOVER contract

Status: rejected on 2026-10-31. The opt-in fast path passed correctness but failed the
end-to-end non-regression gate. All candidate source changes were removed; see
[`UTIL_POLL_TURNOVER_RESULT-20261031.md`](UTIL_POLL_TURNOVER_RESULT-20261031.md).

## Source-backed problem

The single-window pipeline's `Verifier::service` already returns before any CUDA
call when no layer has rung and the 2 ms liveness or 20 s timeout probe is not
due. Its source history records why: the pipeline can call service millions of
times per second while a window waits, and every call used to switch devices
(`cudaGetDevice` plus up to two `cudaSetDevice` calls).

The batch pipeline has the same wait shape but `Verifier::batch_poll` enters
`OnDevice` before it knows whether a layer has rung. In a two-GPU split, the
second stage therefore performs a device switch and switch-back on every pump
iteration while waiting for its next doorbell. The ring counter is in mapped
memory and is already read by `batch_serve`; it can answer the same question
without a driver call.

This is not a launch or window aggregation experiment. It removes idle host
driver traffic and preserves the existing doorbell, timeout, polling,
completion and commit ordering.

## Candidate

Add an opt-in `STRATA_POLL_FAST=1` fast path:

1. It applies only when `ext_serve_ == false`, `ar_on() == false`,
   `b_k_ < b_steps_`, and `h_seq_ < b_k_ + 1`.
2. For the first 2 ms after a ring, or within 2 ms of the previous liveness
   probe, return without constructing `OnDevice`.
3. When the 2 ms probe is due, use the unchanged path: switch device, let
   `batch_serve` inspect the ring, call `cudaStreamQuery`, detect a dead graph,
   and enforce the 20 s timeout.
4. Once a ring is visible, or every layer has been served, use the unchanged
   batch service and completion path.
5. External concurrent-read servicing and the all-resident zero-doorbell graph
   keep their current code paths.

No output row, launch, event, commit, sampling or finish handling changes. The
only changed observable is that an idle waiting tick avoids CUDA driver calls.

## Gates

### Startup and path

- Candidate starts with six slots, two groups, both GPUs, health loaded and
  `STRATA_POLL_FAST=1` visible in the runtime environment.
- The production systemd unit, config and files are unchanged.
- A startup marker proves the fast batch-poll path was selected.
- Cold-start and resource output stays inside the current safety envelope.

### Correctness

Three frozen greedy probes run twice in B and twice in A2. B must be
byte-identical within itself, equal to A, have the same finish reason, and not
truncate or append tokens. A2 repeats the same check.

### Performance

Run same-boot A1-B-A2 with fixed 400-token prompts at `N=2/4/6/8`, three waves
per arm. The A arm has no `STRATA_POLL_FAST`; B adds only `STRATA_POLL_FAST=1`.
Require:

1. at least one concurrency level improves at least 1% against the combined
   A1/A2 median and wins at least 5/6 paired waves;
2. every other level regresses by no more than 1%;
3. A1-to-A2 drift is at most 3%;
4. zero HTTP or execution errors, unexpected finishes or foreign requests;
5. health, six slots, two groups, RAM, VRAM and swap remain safe.

The user accepts a measured improvement of 1% or more. A warm-up outlier is
not discarded from any arm.

## Restart cost and rollback

The candidate requires one A-to-B and one B-to-A load, about 9 seconds per stop
plus 130-160 seconds per load. The source gap and prior device-switch stall
make one controlled test worthwhile; do not repeat it if the end-to-end gate
fails.

Keep `strata.service` inactive/dead. Preserve the exact A engine and config. If
any gate fails, restore A and verify its hash, health, PID, slots, groups and
GPU state. Never write an API key or token to the repository, logs or
conversation.

## Result

The first candidate benchmark used a build that omitted performance switches present in A,
so B1 is not comparable. A same-option rebuild (B2) passed startup, path-hit and greedy
parity. Against adjacent A2/A3, B2 improved 2 streams by `9.129%` and 4 streams by `7.885%`,
but regressed 6 streams by `3.241%` and 8 streams by `6.122%`. Paired wins were
`4/6, 5/6, 1/6, 0/6`; no level met both the 1% gain and 5/6 gates, and 6/8 violated the
non-regression limit. A3 restored the original binary and config.
