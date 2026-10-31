# UTIL-2WAY-PACK contract (superseded)

Date: 2026-10-31

Status: **not deployed; superseded by `UTIL-TWO-STREAM-SERIAL`.** The measured
four-stream stage cost shows two rows inside one non-overlapped group would
likely be slower than the current two one-row pipelined groups. A same-boot
serial solo probe instead measured about `9.49%` lower two-request completion
and produced the stronger next hypothesis.

## Evidence and selected problem

The active six-slot, two-group configuration measured `104.20 tok/s` for one
512-token stream, `88.50 tok/s` aggregate for two streams and `136.01 tok/s` for
four streams. At two streams the server spreads requests to slots `{0, 3}`, one
per pipeline group; both groups then have a one-row window. At four streams the
final slots are `{0, 1, 3, 4}`, so each group has a two-row window.

Serial batch-MTP improved two streams by only `3.25%` and regressed four streams
by `28.99%`, so it was rejected. This candidate changes only how the first two
requests choose slots.

## Candidate

The opt-in environment variable `STRATA_BATCH_TWO_SAME_GROUP=1` changes the
six-slot, two-group order from:

```text
0, 3, 1, 4, 2, 5
```

to:

```text
0, 1, 3, 4, 2, 5
```

The server, engine binary, model, context, KV type, expert cache, six slots, two
groups, systemd state and default behavior remain otherwise unchanged. With the
variable absent or not `1`, the order is byte-for-byte the current default.

The candidate is limited to exactly two pipeline groups and at least two slots
per group. Other group counts keep the current order.

## Source and scheduling gates

1. Unit tests prove the opt-in order is `0,1,3,4,2,5` for six slots/two groups.
2. For 3, 4, 5 and 6 in-flight requests, the final occupied slot set equals the
   current order's set. Four streams therefore still form two two-row groups.
3. With the variable absent, every existing slot-allocation test and order stays
   unchanged.
4. A fake-engine concurrency run shows exactly two admissions use slots 0 and 1,
   and four admissions use slots 0, 1, 3 and 4.
5. The focused server suite and offline preflight pass before a restart.

## Workload and performance gate

Use `tools/server/probe-decode-concurrency.py` with one deterministic prompt,
512 requested output tokens, a full warmup and levels `1,2,4`. Run A1, then B,
then A2 with three waves. If the two-stream improvement is between 1% and 5%,
extend to six waves and require five paired wins.

Adoption requires all of:

1. Two-stream decode aggregate or the later-request completion wall improves by
   at least 1% over the adjacent A1/A2 median;
2. one-stream throughput does not fall by more than 1%;
3. four-stream aggregate does not fall by more than 1%;
4. A1/A2 drift is at most 3%;
5. all requests produce 512 tokens with `finish=length`, and candidate output
   hashes equal A for the deterministic workload;
6. zero HTTP, CUDA, timeout or foreign-request errors;
7. health is loaded with six slots and two groups, and RAM, VRAM and swap stay
   inside the current safety envelope;
8. production systemd and configuration files remain unchanged.

## Stop and rollback

A source or fake-engine failure stops before restarting. A startup, correctness
or four-stream regression stops the performance matrix. The candidate then
restores the exact pre-candidate server file and A config; it does not scan
other packing sizes or group counts.
