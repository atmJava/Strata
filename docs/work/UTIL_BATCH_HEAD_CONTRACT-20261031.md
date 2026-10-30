# UTIL-BATCH-HEAD contract

Status: rejected at the source gate on 2026-10-31. See
[`UTIL_BATCH_HEAD_RESULT-20261031.md`](UTIL_BATCH_HEAD_RESULT-20261031.md).

## Evidence and selected problem

`UTIL-WINDOW-PROFILE` found that six streams peak at `122.703 tok/s` (A combined median), while
eight streams fall to `107.214 tok/s` in the uninstrumented arms. At eight, BSTAT active rows per
window fall `9.07%` to `2.6837`; padding is only `0.0228` rows/window. Per active row, stage 2 costs
`4.54%` more (`6.5119` to `6.8078 ms`).

The final-stage profile pays the GDN `head` phase `1.06 ms/window` at six streams and
`1.04 ms/window` at eight. The pipelined path runs two slot groups of up to three rows, and each
group runs its own head. A pair of group windows therefore reads the output projection twice to
produce at most six rows. The relevant source path is the final stage of
`Verifier::record_window` plus the `PGroup` scheduler in `src/program/generate.cpp`; this is a
static starting point, not yet an implemented path.

This candidate asks one question: **can the final stage combine the head rows of two simultaneously
ready pipeline groups into one output-projection call, preserving every row's logits, sampling and
slot order, and improve at least one of 2/4/6/8 streams by 1%?**

It does not retry GDN grouping, checkpoint move, fixed group counts, QSA slot merge, piped MTP,
EXPDEC or KV-resolve changes.

## Candidate and trigger

Add an opt-in `STRATA_BATCH_HEAD_MERGE=1`, default off. In the piped batch path only, when the
final stage has rows from both groups ready before prediction:

1. run each group's final mixer as today and materialize its mixed rows in the common head input;
2. concatenate the ready rows in deterministic group/slot order;
3. call the existing native head once with the combined row count `T <= 8`;
4. apply the existing per-row sampler/argmax in original order; and
5. publish each group's verdict and commit without changing slot ownership or recurrent state.

If only one group is ready, or the combined path would delay the earlier group beyond the
pre-registered timing point, fall back to the current per-group head. The trigger must be logged or
available in a counter for the experiment.

The implementation must not change the default path. A layer split, batched multi-stream serving,
the native head and the piped group path are prerequisites; otherwise the switch is a no-op.

## Source gate

Before building a server candidate:

- identify every owner of each group's final residual/mixed buffer and the head logits buffer;
- prove the combined contiguous input can be formed without overwriting a live group;
- prove the group rows can be mapped back to slot order without changing sampling counters;
- keep all per-slot recurrent state and commit ordering independent of head batching;
- show that the expected saved critical path is at least `1%` using the measured `1.04 ms/window`
  head phase and no more than one extra synchronization; and
- add a CPU/GPU harness for separate-versus-merged head rows.

If any of these fail, reject before a server restart.

## Exactness gate

The harness must use `memcmp`/bitwise comparison, not tolerances:

1. native-head logits for two row sets run separately must equal the same rows run as one merged
   call;
2. greedy token ids and finish decisions must equal the separate-group path for every row;
3. fixed-seed sampling must produce the same token stream and draw counter for every row;
4. recurrent state and committed slot prefixes must be byte-identical; and
5. one-group fallback must remain byte-identical to A.

Then run the three frozen greedy probes twice against A and twice against the candidate. Each
candidate reply must repeat byte-for-byte, equal A and keep the same finish reason. Any difference
rejects before throughput.

## Frozen throughput

Use the fixed 400-token prompts in `tools/server/window-profile-sweep.py`, three waves at
`N=2/4/6/8`, on the same boot:

1. A1;
2. B with only `STRATA_BATCH_HEAD_MERGE=1` added; and
3. A2 after restoring the original binary/config.

Adoption requires all of:

1. at least one stream level improves by at least 1% against the A1/A2 combined median and wins at
   least 5/6 paired waves;
2. every other level regresses by no more than 1%;
3. A1→A2 drift is no more than 3% at each level;
4. zero HTTP/execution errors, zero unexpected finish reasons and zero foreign requests;
5. six slots, two groups, health, RAM, VRAM and swap stay inside the existing safety envelope;
6. the merged path is actually hit at six and eight streams, while two and four streams may use
   the one-group fallback; and
7. production systemd, config and runtime files remain unchanged.

The 1% user threshold is absolute: a directional result below 1% is recorded as rejected.

## Restart cost

An already-built candidate needs one A→B startup and one B→A restore, about `150 s` per load plus
about `9 s` per stop. Source/build and harness work come first; a restart happens only after the
source gate and parity pass.

## Safety and rollback

Preserve A's config and binary before building. Keep the production service inactive/dead and do
not modify its config. If the candidate fails any exactness, startup, throughput, drift or resource
gate, restore the exact A source behavior and binary, remove the new switch from the runtime
config, verify hashes/health/slots/resources, and record the result. No API key or token enters the
repository or logs.
