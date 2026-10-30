# UTIL-GDN-GROUPS contract

Status: rejected on 2026-10-31 by the throughput and A-B-A drift gates. See
[`UTIL_GDN_GROUPS_RESULT-20261031.md`](UTIL_GDN_GROUPS_RESULT-20261031.md).

## Evidence and selected problem

A is the restored six-slot, two-group instance at
`6f221167e534fa2d4967ae4f266b2e31874de316`. It already enables
`STRATA_BATCH_SELF_COMMIT=1`; this is not a new candidate. The real engine log also shows
`KV streaming: 32768 of 262144 cells`, so `kv_mode=1` and the wider
`STRATA_BATCH_ATTN_ROWS=1` path is unreachable in this instance.

The reachable, default-off `STRATA_BATCH_GDN_GROUPS=1` path combines the GDN recurrence of all
one-row slot groups into one launch per GDN layer. On a six-row window this replaces up to six
recurrence launches with one while preserving each slot's independent state and row. The source
had an interaction defect: the grouped window call always passed `commit=false`, so under the
enabled self-commit policy it produced outputs but did not retain the new recurrent state. The
existing parity harness covered a non-committing window and a separate commit path, but not the
combined self-commit window used by A.

This experiment asks one question: **can a corrected grouped GDN recurrence save enough launch
cost to improve at least one of 2/4/6/8 streams by 1% without changing outputs, state or finish
semantics?** It does not scan group counts, MTP depth, lead-in timing, KV residency, checkpoint
copying or QSA scheduling.

## Candidate

The candidate retains A's exact config and adds only:

```text
STRATA_BATCH_GDN_GROUPS=1
```

The source change is opt-in and leaves the default path unchanged:

- each grouped window entry carries the existing one-row keep pointer when the window is eligible
  for self-commit;
- the grouped GDN kernel writes the final recurrent state after producing all row outputs when a
  keep pointer is present;
- a normal verify window still has no keep pointer and leaves state untouched.

The production systemd unit, production config and runtime files must remain unchanged. Startup
must show six slots, two piped groups, `loaded=true`, the expected model geometry and safe
RAM/VRAM/swap. Reject before parity if the switch is absent from the engine process, group launch
selection is not reached, startup falls back to another schedule, or resources fail the existing
safety envelope.

## Exactness gate

The synthetic GPU harness must compare `memcmp`, not tolerances:

1. existing per-row reference behavior for 1/4/8 independent groups;
2. all row outputs for a self-committing grouped window against the corresponding per-slot calls;
3. every slot's final GDN state against the per-slot calls; and
4. the non-committing window still leaving state byte-identical.

Then run the frozen greedy probes twice against A and twice against the candidate:

1. reply with exactly `STRATA_PARITY_ALPHA`;
2. compute `59 * 16` and reply with only `944`;
3. return exactly `The quick brown fox jumps over the lazy dog.` and nothing else.

Each candidate trial must be byte-identical to itself and to A, with the same finish reason. Any
output, state, repeatability, execution or finish failure rejects before throughput work.

## Frozen throughput

Use `tools/server/bench-strata.py` at `BENCH_N=2/4/6/8`, three repetitions and the existing fixed
400-token prompts. Run A3 immediately before candidate startup and A4 immediately after candidate
restore, in the same boot. Record aggregate tok/s, wall, TTFT, slot wait, decode rate, draft
acceptance, expected/foreign requests and resource samples.

Adoption requires all of:

1. at least one stream level improves by at least 1% against the A3/A4 combined median and wins at
   least 5/6 paired repetitions;
2. every other level regresses by no more than 1%;
3. there are zero HTTP/execution errors, zero unexpected finish reasons and zero foreign requests;
4. six slots, health, RAM, VRAM and swap remain inside the current safety envelope;
5. A3-to-A4 drift is no more than 3%; and
6. production systemd, config and source files remain unchanged.

If the candidate fails, restore A's original binary and source behavior and record the result. If
it passes, keep the source opt-in and record the enabling environment entry as the measured
candidate; do not scan adjacent GDN settings.

## Restart cost

One A-to-candidate start and one candidate-to-A restore are expected to take about 3-7 minutes
each. Restarting is justified because the recurrence is in every decode window and the candidate
removes five of six launches at the six-row level; one synthetic-kernel and one process A/B cycle
can resolve the 1% question. The existing A binary will be preserved before rebuilding so that
A4 uses the identical baseline inode.
