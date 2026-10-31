# UTIL-QFUSE-HC screen contract

## Question

Can the production `STRATA_HC_Q8=1` hyper-connection read write its consumer's q8_1 image
inside the up kernel and thereby replace both the existing up kernel and the following
`native_quantize_q8_1` launch with a gain of at least 1%?

This screen is separate from the rejected `STRATA_QFUSE` GDN recurrence path. It does not
change token selection, model weights, scheduling, groups, slots or the server.

## Source-backed reach

The production verifier calls `gr_read_group()` in both stages. With `STRATA_HC_Q8=1`, each
group's read has Q8 down/up weights and `s_down` / `s_up` scales. The current verifier does
not allocate `qcnt_` or set `q8_mixed`, because `g_qfuse_hc()` is hard-coded false. The current
critical path is therefore:

```text
gr_down_q8_kernel -> gr_up_q8_kernel -> native_quantize_q8_1
```

Setting `q8_mixed` instead reaches:

```text
gr_down_q8_kernel -> gr_up_q8_q32_kernel
```

The second kernel owns a complete 32-column q8_1 group and writes the q8_1 bytes without a
counter or fence. This source reach is already present, but the existing `gr_parity` QFUSE
check exercises the BF16 up path rather than the production Q8 up path.

## Frozen A and harness

Use the existing A2 server as an untouched baseline. Build the standalone screening harness
against the existing frozen `build75/libstrata_kernels.a`; do not modify the remote frozen
source tree, production binary, production config or systemd state.

For each token count `T=1..8`, compare:

- A: Q8 read with no `q8_mixed`, followed by `native_quantize_q8_1(mixed, ref, T)`.
- B: the same Q8 read with `q8_mixed` set, using `gr_up_q8_q32_kernel`; no separate
  quantization.

Both arms use identical generated BF16/Q8 weights, inputs, Q8 scales, pending state and
buffer poisoning.

## Gates

### Exactness

A/B must compare byte for byte for every `T=1..8` over:

- `R`, `lo`, `rs`, `inject_out`, and `mixed`;
- the complete q8_1 image.

Any difference stops the screen before timing is considered.

### Stage screen

Run seven alternating A/B processes on each GPU. Every process covers `T=1..8`; timing is
the median of five event-timed batches, with a warm-up before the timed batches. The timed
path includes both kernels and the following quantization in A, and only both kernels in B.

The screen passes only if at least one production-reachable window size has:

1. a paired median gain of at least 1%;
2. at least five wins in seven paired processes;
3. no token count regresses by more than 1%;
4. no CUDA error, unexpected output, or foreign process.

The server's production windows use one to four rows per group, but `T=1..8` is measured to
show the whole shape and to expose regressions.

## Decision

If the stage screen fails, record `UTIL-QFUSE-HC` as rejected and do not build, deploy or
restart the server. If it passes, preregister the source opt-in and server A1-B-A2 separately.
The microbenchmark alone is not permission to change the server.

The existing global 1% rule still applies after the screen: the measured per-read saving must
be extrapolated over the real 24-layer, two-hyper-connection-read stage. A candidate whose
optimistic stage or window ceiling is below 1% is rejected without consuming a restart.

No API key, token or private prompt is written to the repository, logs or conversation.

## Outcome

**Rejected at the production projection gate.** Both cards passed exactness for every `T=1..8`.
The complete `read + quantize` path passed 1% and 5/7 at production-reachable row widths, but
the absolute saving is only `0.44-0.79 us` per read. A 24-layer stage has 48 such reads; even
summing both card stages and using the fastest profiled stage as the denominator gives an
optimistic ceiling below 0.5%. The candidate cannot reach the global 1% threshold without
overlapping or non-additive effects that are not evidenced. No source opt-in, server build or
restart was made. See
[`UTIL_QFUSE_HC_RESULT-20261031.md`](UTIL_QFUSE_HC_RESULT-20261031.md).
