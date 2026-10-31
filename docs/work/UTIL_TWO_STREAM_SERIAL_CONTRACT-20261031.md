# UTIL-TWO-STREAM-SERIAL contract

Status: **adopted on 2026-10-31.** See
[`UTIL_TWO_STREAM_SERIAL_RESULT-20261031.md`](UTIL_TWO_STREAM_SERIAL_RESULT-20261031.md).

Date: 2026-10-31

## Evidence and selected problem

The active engine measures `104.20 tok/s` for one 512-token stream and
`88.50 tok/s` aggregate for two. Two streams use two one-row pipeline groups,
so their aggregate is below one stream rather than above it.

On the restored A2 boot, two identical 512-token requests run one after the
other completed in `10.659 s`. The concurrent two-stream wall median was
`11.777 s`. This same-boot diagnostic is not a candidate result, but its
`9.49%` total-completion advantage is large enough to justify a source-only,
opt-in scheduling candidate without rebuilding the engine.

## Candidate

`STRATA_BATCH_SERIAL_TWO=1` changes only the server's low-concurrency admission
decision:

- when no batch slot is busy and exactly two requests are in flight, the first
  remains on the solo path and the second waits for the control lines;
- the second then runs on the solo path, preserving the same deterministic
  single-request arithmetic;
- when a third request is waiting, or any slot is already busy, the existing
  batch admission runs unchanged;
- with the variable absent or not `1`, the current behavior is unchanged.

The engine binary, model, six slots, two groups, context, KV, expert cache,
sampling and production systemd files remain unchanged.

## Source gates

1. Existing slot and parallel tests pass with the variable absent.
2. A fake-engine two-request test observes two `GEN` solo calls and no `BGEN`.
3. A fake-engine three-request test observes promotion to a batch slot after
   the third request waits.
4. The focused server suite and offline preflight pass before restart.

## Workload and measurement

Use `tools/server/probe-decode-concurrency.py` with the same deterministic
prompt, 512 requested output tokens, one warmup, three waves and levels `1,2,4`.
Record:

- each request's start, finish and TTFT;
- two-request completion span from the first request's start to the later
  request's finish;
- four-request completion span and aggregate decode throughput;
- output hashes and finish reasons.

Serialized requests do not overlap, so their decode durations must not be
summed or divided by only the longest decode interval. The completion span is
the conservative low-concurrency metric.

## Adoption gate

1. Two-request completion span improves by at least 1% over adjacent A1/A2.
2. One-stream throughput does not regress by more than 1%.
3. Four-stream completion span and aggregate throughput do not regress by more
   than 1%; a third request must resume the production batch layout.
4. A1/A2 drift is at most 3%.
5. Candidate two-stream output equals the same fixed request's solo hash and
   repeats within the candidate; all requests finish with 512 tokens and
   `finish=length`.
6. Zero HTTP, CUDA, timeout or foreign-request errors.
7. Health is loaded with six slots and two groups; RAM, VRAM and swap remain
   safe.

If the gain is 1-5%, extend the paired run to six waves and require five wins.
If it is at least 5%, three winning screening waves are sufficient before the
four-stream gate.

A 64-token two-request regression check was added after adoption screening. The
candidate must also improve total completion for short output; it measured
`1.554 s` against restored A3's `1.692 s` (`8.18%` faster).

## Tradeoff and stop rule

The second request's TTFT becomes roughly the first request's remaining solo
time. This is acceptable only because the user normally runs one task and the
measured total completion falls; the candidate must not be applied to a
three-or-more-stream workload. Do not tune a delay, alter solo MTP, change group
counts or scan slot counts after a failed result. Restore the exact server file
and A config on failure.
