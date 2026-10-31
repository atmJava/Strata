# UTIL-MTP-LOWCONC contract

Date: 2026-10-31

## Question

The active engine measures about `87.9 tok/s` for one 512-token decode. Two
concurrent 512-token decodes aggregate to only about `87.5–88.6 tok/s`, because
the pipelined batch path verifies one ordinary row per slot and has no
per-slot draft K/V without `--batch-mtp`.

Can serial batch-MTP restore speculative decoding and improve the user's
normal two-stream Agent path without regressing the four-stream ceiling?

This is not a rerun of `UTIL-PIPED-MTP`: that candidate used
`STRATA_PIPED_MTP=1` with pipelined groups, reached about `+20%` at two
streams, but regressed four/six streams and did not repeat a fixed greedy
output. This contract tests the existing serial batch-MTP path instead.

## Arms

All arms use the same engine, model, context, KV type, expert cache, batch
slots, prompt, sampling settings and 512-token output limit.

- A: the active two-group pipelined configuration, no `--batch-mtp`.
- B: identical except that `--batch-groups 2` becomes `--batch-groups 1`, plus
  `--batch-mtp`. The explicit one group is required: the current config also
  gives `--batch-groups 2`, and the `STRATA_BATCH_MTP_SERIAL` shortcut applies
  only when no group count was given.
- A2: restore A.

The startup log must show one group, state that `--batch-mtp` is active and
report how many slot drafters it created.

## Workload

Use `tools/server/probe-decode-concurrency.py` with a deterministic long
prompt, a full 512-token warmup, and levels `1,2,4`. Every request must finish
with `length` and produce 512 completion tokens. The tool stores metadata and
output hashes, not prompt or response text. Concurrent aggregate throughput
uses total output tokens divided by the longest overlapping `decode_ms`, not
the sum of overlapping decode times.

Start with three waves per level. If B improves the accepted level by at
least `5%` and wins all three waves, that is enough to proceed to the
four-stream gate. If the result is between `1%` and `5%`, repeat six waves
and require five of six wins; do not accept a sub-5% difference from three
waves alone.

## Correctness and safety gates

1. Startup is healthy with six slots, the same model/context, and no CUDA or
   execution error.
2. Each level's two repeated candidate waves have one output hash, and B's
   hash equals A's hash for the same deterministic request.
3. No unexpected `stop`, non-finite value, crash, timeout or foreign request.
4. The existing offline preflight and focused batch-group tests pass before
   the restart.

## Performance gate

1. At one or two streams, B's median aggregate decode throughput improves by
   at least `1%` over the adjacent A1/A2 median.
2. Four streams do not regress by more than `1%`.
3. A1/A2 drift is at most `3%` at every measured level.
4. A >=5% result wins all three screening waves; a 1–5% result must then win
   at least five of six paired waves.
5. Six/eight streams are a safety smoke only if the four-stream gate passes;
   they are not a reason to reject a valid low-concurrency result.

## Stop rules

- Source/path proof that serial batch-MTP cannot run on this layer split stops
  before a restart.
- A startup or correctness failure restores A immediately and does not run
  the performance matrix.
- A performance miss rejects the candidate; do not scan draft depth,
  `spec-min-p`, group counts or nearby MTP settings.
- Any accepted candidate must be opt-in at source/config level and have an
  exact rollback file.
