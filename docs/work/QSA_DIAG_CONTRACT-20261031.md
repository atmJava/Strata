# QSA-DIAG pre-registered contract

Registered before the first diagnostic request on 2026-10-31.

## Question

On one real cold prompt near 150K tokens, is either of these QSA-selection routes worth one minimal
implementation under the user's 1% end-to-end threshold?

1. Reuse candidate cells shared by adjacent queries.
2. Score indexer keys in FP16 where the selection remains equivalent or close enough for a quality gate.

This run decides whether to implement a candidate. It does not adopt either route.

## Fixed inputs and instrumentation

- Candidate server on port 8080; production systemd, config and runtime directories are not touched.
- The baseline engine binary and source hashes are recorded before the restart.
- One clean candidate restart enables `STRATA_SEL_OVERLAP=1`, `STRATA_IDX_FP16_CHECK=1` and
  `STRATA_PREFILL_TIMING=1`.
- One deterministic, unique request targets 150,000 prompt tokens. Its prompt must be cold
  (`cached_tokens=0`), return no HTTP/engine error, finish without an unexpected `length`, and answer
  the needle.
- The diagnostic timing boundary sits after the normal score and top-k calls. Therefore `qsa select`
  excludes the overlap and FP16 diagnostics; `qsa attn` is not used for any conclusion.
- `qsa_select_bench 150000 256 10 262144` with `STRATA_SELECT_SIMT=1` measures the current
  production scorer/top-k split at the same geometry. It does not replace the real request.
- Record per-chunk and full-request `qsa select`, tail `T`, active blocks, selection width, selected
  union ratio, FP16/FP32 selected-cell agreement, differing cells, runtime env, source/binary hashes,
  RAM/swap and both GPUs' VRAM.
- One restart restores the unmodified A binary/config after the request. Budget about three minutes
  for each restart. A failed or interrupted diagnostic is not reused as partial evidence.

## Decision equations

Let:

- `S` be full-request GPU timeline share of clean `qsa select`;
- `F` be the microbench scorer share, `score_ms / (score_ms + topk_ms)`;
- `r` be the median or pooled 16-query union ratio `union / sum(width)`.

The candidate-sharing upper bound is `S * F * (1 - r)`. Candidate sharing can only avoid scoring
work; exact per-query top-k remains unless a proposed candidate set is proven to contain every
top-k result.

The FP16 experiment headroom assumes a conservative 2x scorer speedup: `S * F * 0.5`.

## Pre-registered gates

### QSA-CANDIDATES

Proceed to at most one minimal exact candidate-sharing implementation only if all hold:

- the diagnostic request passes the correctness/resource checks above;
- `r <= 0.75`;
- the optimistic end-to-end upper bound `S * F * (1 - r) >= 1.25%`;
- the proposed candidate set can be shown to contain the same top-k cells, or an approximate
  candidate path is explicitly treated as a quality experiment.

The 1.25% bound leaves 25% headroom above the user's 1% adoption threshold. If the upper bound is
below 1.25%, reject this route without implementing it.

### QSA-FP16

Proceed to at most one minimal FP16-scorer implementation only if all hold:

- the diagnostic request passes the correctness/resource checks above;
- FP16-rounded keys reproduce at least 99.9% of full sparse selections exactly and share at least
  99.99% of sparse selected cells;
- the conservative end-to-end headroom `S * F * 0.5 >= 1.0%`.

Passing this gate permits an experiment, not adoption. The implementation must then match or justify
its quality change and pass A-B-A with stable, attributable end-to-end and `qsa select` improvement
of at least 1%.

If neither route passes its gate, reject both and move to a different mechanism. Thresholds and
denominators are fixed here and may not be changed after seeing the result.
