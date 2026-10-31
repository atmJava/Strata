# AGENT-DRAFT-BREAKDOWN contract

Date: 2026-10-31

Status: complete. Result:
[`AGENT_DRAFT_BREAKDOWN_RESULT-20261031.md`](AGENT_DRAFT_BREAKDOWN_RESULT-20261031.md).

## Question

Within the single-request decode window, how much of the 2.36 ms/window draft
phase is GPU graph work, host staging, successive graph launches, host decision
gaps, PLE prefetch, first-step wait and final synchronization?

The purpose is to choose one implementation candidate only. No production
default changes in this diagnostic.

## Evidence bound

The instrumented A2 profile reports `draft 2.36` against roughly
`42.7–45.5 ms/window`, or about 5.2–5.5% of decode. The real trace's model
union is 69,236 s against 184,604 s root-wall union, so eliminating the whole
draft phase is at most about 2.1% of root-task wall time. A credible mechanism
must remove at least half of the draft phase, or have an additional reduction
in redundant verification/tool rounds, to reach a 1% whole-task bound.

MTP draft cost is present in the real request class; the trace contains long
output and reasoning requests, and the production model runs with MTP enabled.
Changing draft depth, `spec-min-p`, groups or slot count is not a candidate.

## Controlled run

Use the current B2 source, model, config, six slots, two groups and warm prompt.
Temporarily add only:

```text
STRATA_DRAFT_TIMING=1
STRATA_MTP_PROFILE=1
```

Run 416, 1,490 and 4,483 token single-stream requests, three waves each.
Record the engine's:

- round graph time and per-step GPU time;
- host gaps between graph launches;
- staging, graph launch calls, PLE prefetch and waits;
- per-stage round/step profile names and totals;
- wall, prompt, decode, TTFT, output count, finish and output hash.

Restore the exact profiler-free B2 configuration after capturing the logs.

## Selection gate

A follow-up mechanism is eligible only if the timing split identifies at least
one removable component with:

1. a conservative whole-task bound of at least 1%, using the measured trigger
   frequency and the 37.5% model-share upper bound from the latest trace;
2. no change to draft tokens, verification, sampling or finish semantics;
3. an independent correctness path for greedy output and tool/finish handling;
4. an opt-in implementation with exact rollback;
5. expected savings greater than its build, deployment and restart cost.

If no single component reaches the bound, close MTP draft tuning and return to
the larger real-task map. Do not scan depth, thresholds, groups or cache knobs.

## Stop rules

Stop on a failed load, health failure, six-slot loss, HTTP or engine error,
unexpected instrumentation error, or a profiling overhead that makes the run
unusable. The diagnostic may consume at most two restarts: profiling on and
exact B2 restoration.
