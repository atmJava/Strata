# UTIL-SIDE-MIXER screen contract

Status: rejected before the server gate on 2026-10-31. The existing
`STRATA_SIDE_MIXER=2` path is exact and reduces the isolated projection pair at useful row
widths, but it is unreachable in production batch windows. See
[`UTIL_SIDE_MIXER_RESULT-20261031.md`](UTIL_SIDE_MIXER_RESULT-20261031.md).

## Question

The production GDN layer pays for the qkv projection and the gate projection (`z`) in
sequence. Can `STRATA_SIDE_MIXER=2` overlap the real IQ4_XS qkv matrix with the real IQ3_S
gate matrix on the same RTX 2080 Ti and save at least 1% of an `18.27 ms` stage
(`0.183 ms`) without changing either output?

## Source reach

`record_window()` reads the opt-in `STRATA_SIDE_MIXER` mode. Mode 2 creates a non-blocking
side stream and events in `Verifier::init`, then:

1. quantizes the layer input;
2. forks the side stream;
3. launches qkv on the main stream and the gate projection on the side stream;
4. runs the qkv-dependent conv and a/b gates on the main stream;
5. joins before the recurrence reads `z`.

The path is inside every GDN layer and is not a rejected head, group, checkpoint, QSA or
HC-q8 mechanism. It is marked exact in the source and `docs/DETAILS.md`.

The reach check later found that this description applied only to non-batch windows. Production
decode enters `capture_batch()`, which sets `batch_rec_`, and the source disables both projection
and indexer branches in that mode.

## Frozen measurements

The real tensors from the production artifact are:

```text
blk.*.attn_qkv.weight   IQ4_XS  2560 -> 10240
blk.*.attn_gate.weight  IQ3_S   2560 -> 6144
```

The current profile reports about `0.84 ms` in `q8+qkv/q-idx gemv` and `1.03 ms` in `z`
for a GDN layer. The overlap's optimistic ceiling is `min(qkv, gate)`, not their sum.

Use a standalone GPU harness against the frozen `build75/libstrata_kernels.a`. It compares:

- sequential qkv then gate on one stream;
- the same two calls after a side-stream fork, joined on the main stream.

Both arms use identical synthetic block weights, Q8_1 activation, one cold-sized matrix
copy and `T=1..8`.

## Gates

For every `T=1..8`, qkv and gate outputs must be byte-identical. Run seven independent
processes on each card; each reports the median of five event-timed batches. The route may
proceed only if:

1. at least one production width (`T=1..4`) saves at least `0.183 ms`;
2. that width wins at least 5/7 paired processes;
3. no other production width regresses by more than 1%;
4. there are no CUDA errors.

If it passes, preregister an env-only server candidate with `STRATA_SIDE_MIXER=2`, frozen
greedy parity, startup reach and the existing A1-B-A2 throughput gates. If it fails, reject
without building or restarting the server.

## Safety

The test is read-only with respect to the remote source tree, production binary, config,
service and user files. It uses temporary GPU allocations and leaves A2 running. No API key,
token or private prompt is written to the repository or logs.
