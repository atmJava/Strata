# UTIL-PIPED-MTP-ORDER contract

Status: pre-registered before implementation.

## Evidence and selected problem

The cache-neutral experiment cannot start: `--expert-cache-device1` is a helper-cache flag and
requires a GPU that runs no layer-split stage, while this box has only the two GPUs used by both
stages. The original cache mismatch remains a documented confound.

The engine already has `--expert-cache-cpu-order`, an opt-in GPU expert path that matches the
CPU's floating-point accumulation order. If the parity drift came from GPU/CPU expert location and
rounding rather than from piped MTP itself, this switch should remove the drift without changing the
expert cache or the two-GPU shape.

This experiment asks one question: **can CPU-order GPU expert arithmetic make the unchanged
piped-MTP candidate byte-identical to A?** It does not scan cache sizes, draft depth, slots or groups.

## Candidate

Copy `b-piped-mtp.json` to a new file and append exactly one engine argument:

```text
--expert-cache-cpu-order
```

Keep every other argument and environment entry unchanged, including:

- `--batch-mtp`;
- `STRATA_PIPED_MTP=1`;
- `STRATA_BATCH_SELF_COMMIT=0`;
- `--expert-cache auto`, `parallel=6`, `--batch-groups 2`;
- context, KV, reserve and all experimental environment settings.

Startup must report the same six slot drafters and two piped groups, health must be loaded,
`/metrics` must report `batch_slots=6` and `batch_groups=2`, and the engine sha256 must remain
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`.

If startup loses a slot, silently falls back, changes another argument, or fails health, reject and
restore A immediately.

## Frozen parity

Run the same three fixed greedy probes twice as in
`UTIL_PIPED_MTP_RESULT-20261031.md`:

1. exact text `STRATA_PARITY_ALPHA`;
2. the arithmetic probe;
3. the speculative-decoding explanation at `max_tokens=96`.

Candidate success requires:

- both candidate trials are byte-identical for each probe;
- every candidate text equals the recorded A text with the same finish reason;
- all requests succeed.

If any probe differs, reject without a performance A-B-A, restore A, and do not scan more arithmetic
or cache switches. A semantic similarity score cannot waive this gate.

## Frozen throughput

If parity passes, use `tools/server/bench-strata.py` with `BENCH_N=2/4/6`, three repetitions each.
Use a fresh A3 run immediately before the candidate as the pre-candidate baseline and A4 after it as
the restored baseline.

Keep gate:

1. two-stream candidate aggregate is at least `1%` above the A3/A4 combined median and wins at least
   `5/6` paired bursts;
2. four- and six-stream medians are no more than `1%` below A3/A4;
3. zero request errors, zero unexpected finish reasons and all six slots remain available;
4. RAM, VRAM and swap stay within the current safety envelope;
5. production systemd and its files remain unchanged; and
6. a sub-one-percent two-stream gain is rejected.

Passing permits one longer mixed-traffic soak and only then adoption of the exact candidate
configuration.

## Reject gate

Restore A and record a rejection if any of these occurs:

```text
startup loses a slot or silently falls back
any greedy probe differs or is not repeatable
two-stream improvement is below 1%
four- or six-stream median regresses by more than 1%
health, resource or request-integrity check fails
```

After failure, do not scan nearby cache sizes, other order switches, draft depth, slots or groups.

## Restart cost

The experiment needs one A-to-candidate start and one candidate-to-A restore, about 3-4 minutes
each. It is justified because the candidate already showed a `19.92%` two-stream opportunity and
this is the existing mechanism intended to remove GPU/CPU expert-location rounding. The pre-candidate
A baseline can run without another restart on the already restored A.

