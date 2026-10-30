# UTIL-SLOT-CKPT-MOVE contract

Status: rejected on 2026-10-31 by the throughput gate. See
[`UTIL_CKPT_MOVE_RESULT-20261031.md`](UTIL_CKPT_MOVE_RESULT-20261031.md).

## Evidence and selected problem

The restored A config already enables `STRATA_BATCH_SELF_COMMIT=1`,
`STRATA_SLOT_COPY_DIRECT=1`, `STRATA_SLOT_COPY_BATCHSYNC=1`, `STRATA_CKPT_POOL=1` and
`STRATA_CKPT_THREADS=1`. Those are not new candidates.

The deployed source has one separate opt-in, `STRATA_SLOT_CKPT_MOVE=1`, which is absent from A.
When a request is admitted into a batch slot, it moves the request's turn checkpoint into the slot
instead of copying about 112 MiB of host vectors. The source comments say the same bytes are later
restored from another container, so token selection and model arithmetic should be unchanged. The
change is most relevant to 8 external streams because prompt admissions and slot turnover expose
the copy cost repeatedly.

This experiment asks one question: **does moving, rather than copying, the admission checkpoint
improve throughput by at least 1% without changing greedy output?** It does not scan checkpoint
cache sizes, slot counts, group counts, MTP depth, lead-in timing or kernel arithmetic.

## Candidate

Copy A to a new candidate config and add exactly one engine environment entry:

```text
STRATA_SLOT_CKPT_MOVE=1
```

Keep every argument and every other environment entry unchanged. Startup must report six slots,
two piped groups, the same engine SHA-256, health loaded, and an expert-cache shape within the
normal one-slot loading tolerance. The production systemd unit, production config and source files
must remain unchanged.

Reject before parity if startup fails, loses a slot, silently falls back, changes another variable,
reports the wrong engine hash, or leaves unsafe RAM/VRAM/swap.

## Frozen parity

Run three fixed greedy probes twice against A and twice against the candidate:

1. reply with exactly `STRATA_PARITY_ALPHA`;
2. compute `59 * 16` and reply with only `944`;
3. return exactly `The quick brown fox jumps over the lazy dog.` and nothing else.

The initial proposed free-form speculative-decoding sentence was rejected as a fixture before
candidate startup: two unrecorded A trials already produced different valid sentences with the
same broad meaning. A frozen quality probe cannot be waived because the baseline itself is
unstable. The third probe is now an exact-copy instruction, which repeated byte-for-byte on A
alongside the two existing probes.

Warm each probe once without recording it, then run two recorded trials. Each request uses
`temperature=0`, `max_tokens=96`, the same chat template and the same server route. Candidate
success requires both trials of every probe to be byte-identical, equal to A, and to have the same
finish reason. Any different answer, repeatability failure, request error or unexpected finish
rejects before performance work.

## Frozen throughput

Use `tools/server/bench-strata.py` at `BENCH_N=2/4/6/8`, three repetitions and the existing fixed
400-token prompts. Run A3 immediately before candidate startup and A4 immediately after candidate
restore. Use the same boot and record aggregate tok/s, wall, TTFT, slot wait, decode rate, draft
acceptance, expected/foreign requests and resource samples.

Adoption requires all of:

1. at least one measured stream level improves by `>=1%` against the A3/A4 combined median and
   wins at least `5/6` paired repetitions at that level;
2. every other 2/4/6/8 level regresses by no more than `1%`;
3. there are zero HTTP/execution errors, zero unexpected finish reasons and zero foreign requests;
4. six slots, health, RAM, VRAM and swap remain inside the current safety envelope;
5. the A-B-A drift is no more than 3%; and
6. production systemd, config and source files remain unchanged.

If only 8 streams improves by at least 1% while 2/4/6 stay within 1%, keep the exact env-only
candidate as the result. Do not scan nearby checkpoint or copy settings.

## Reject gate

Restore A and record rejection if startup or parity fails, no level reaches 1%, a paired-win gate
fails, another level regresses above 1%, A-B-A drift exceeds 3%, or request/resource/production
integrity fails.

## Restart cost

One A-to-candidate start and one candidate-to-A restore should take about 3-7 minutes each including
loading. The candidate is justified because it is an existing, one-variable, arithmetic-neutral
copy-elision path and the 8-stream tail repeatedly exercises admission and slot turnover.
