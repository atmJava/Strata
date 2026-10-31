# UTIL-BATCH-FAIR-SERVE contract

Status: rejected at the server throughput gates. Result:
[`UTIL_BATCH_FAIR_SERVE_RESULT-20261031.md`](UTIL_BATCH_FAIR_SERVE_RESULT-20261031.md).
This file was committed before the candidate source change or server measurement.

## Question

Can the two-stage batch pipeline reduce final-stage `waitA` by servicing at most one
already-ringed layer per stage on each `pump()` turn, instead of letting the first stage
drain every layer that rings while its callback runs?

## Source-backed problem

`Verifier::batch_serve()` loops until the stage's next mapped ring is not visible and then
returns. `pump()` visits stages in ascending order. With layer split `24`, stage 1 can ring
additional layers while its callback runs CPU expert work; `batch_serve()` consumes those
rings before `pump()` reaches stage 2. Stage 2 is then left waiting for its GPU plan even
though the host could have published that plan first.

The production window profile measured, per window:

| stage | measured cost |
| --- | ---: |
| stage 1 | `16.89 ms`, GDN `waitA 0.65`, QSA `waitA 0.09` |
| stage 2 | `19.22 ms`, GDN `waitA 1.63`, QSA `waitA 0.06` |

These stamps cover the GPU wait after the layer rings until the plan is copied. The pool's
plan calculation is only about `0.15 ms`, so the observed wait is dominated by host service
order and callback work rather than by plan construction. This is distinct from the rejected
`UTIL-POLL-TURNOVER`, which only removed CUDA device calls when no ring existed.

## Candidate

Add `STRATA_BATCH_FAIR_SERVE=1`, off by default. When enabled and a real ring is present:

1. service exactly one layer for that stage;
2. return `0` if another layer for that stage is still outstanding, allowing `pump()` to
   visit the other stage before servicing this stage again;
3. return `1` only when all layers for the current window have been served.

The single-window path, all-resident graph, external concurrent-read service, per-layer
callback order, row arithmetic, plan contents, GPU launches, events, commits, sampling and
finish handling remain unchanged. A startup marker records that the path was selected.

## Why the expected gain can exceed 1 percent

The profile's stage-2 `waitA` is `1.69 ms` of a `19.22 ms` stage, or `8.8%`. Stage 1 adds
another `0.74 ms` of waitA in `16.89 ms`. Recovering only one fifth of the stage-2 wait
would exceed 1% of that stage. The end-to-end gate, not this projection, decides the result.

## Numeric and correctness expectation

Scheduling changes only when a stage callback is visited; it does not change any callback's
inputs, row values, plan, launch order within a layer, commit or token choice. Bitwise output
and state parity are required, not merely equivalent greedy text.

## Gates

### Build and startup

- Build with exactly A's options: Release, CUDA architecture 75, Ninja, external ggml,
  `STRATA_MMQ_KQUANTS=ON`, `STRATA_MMQ_TQ_OVERLAY=ON`, `STRATA_Q6K_EXPERTS=ON`,
  non-portable, tests enabled.
- B starts with six slots, two groups, both GPUs and health loaded.
- The runtime environment contains `STRATA_BATCH_FAIR_SERVE=1`.
- Both stage startup markers appear, and production systemd/config files remain unchanged.

### Path and correctness

- A real pipelined batch decode reaches the fair-service branch; this is shown by the
  startup marker plus batch telemetry/batch-window records.
- Run the three frozen greedy probes twice in B and twice in A2. Every B result must be
  byte-identical within B, equal to A1, and have the same `stop` finish.
- No HTTP, execution, CUDA, never-rang or timeout error may appear.

### Performance

Run one same-boot A1-B-A2 matrix with three 400-token waves at each of `N=2/4/6/8`.
Require:

1. at least one level improves at least 1% against the A1/A2 combined median and wins at
   least 5/6 paired waves;
2. every other level regresses by no more than 1%;
3. A1-to-A2 drift is at most 3%;
4. zero HTTP or execution errors, unexpected finishes or foreign requests;
5. health, six slots, two groups, RAM, VRAM and swap remain safe.

## Restart cost and rollback

The candidate needs one A-to-B and one B-to-A load, about 9 seconds per stop and 130-160
seconds per load. A separate diagnostic instance is not justified unless the candidate
screen fails without explaining the branch. On any gate failure, restore the exact A engine
and config, then verify engine/config hashes, health, process environment, six slots, two
groups, systemd state and resources.

Never write an API key or token to the repository, logs or conversation.
