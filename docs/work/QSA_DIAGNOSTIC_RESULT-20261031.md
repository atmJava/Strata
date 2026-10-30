# QSA-DIAG result

Run: 2026-10-31, candidate port 8080, 2×RTX 2080 Ti, candidate engine
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`.

## Result

One deterministic cold request read 150,069 prompt tokens with 0 reused tokens. It had no HTTP or
execution error, stopped normally, and answered its needle correctly. The diagnostic tools add large
per-layer synchronizations, so the request wall of 105.864 s is not a production benchmark.

The 19 full / partial chunk overlap measurements ranged from 13.0% to 28.4%, with a median of
28.0% and a mean of 26.84%. The final chunk had 7,214 tokens, 37,516 active blocks and selection
width 2,051. The 16-query selected-cell union therefore uses about 28% of the cells read one query
at a time.

FP16-rounded indexer keys were not equivalent. At the end of the request, 1,203,932 of 1,697,576
full selections were identical (70.9207%). For sparse selections, 3,429,943,099 of 3,431,249,164
cells were shared (99.9619%); 1,306,065 cells differed.

After moving the timing boundary ahead of the diagnostics, the production selection work took
3,929 ms of the 98,075 ms diagnostic GPU timeline (4.0%). The diagnostic's second scoring pass is
charged to `qsa attn`, so this share is conservative. At 150,000 tokens, `qsa_select_bench` measured
the production scorer at 2.020 ms and top-k at 0.899 ms, making the scorer 69.20% of the two.

## Decision

**QSA-FP16: rejected.** Full-selection agreement is 70.92%, far below the pre-registered 99.9%
requirement, and cell agreement is below 99.99%. The observed scorer/top-k split would otherwise
leave 1.386% headroom under the contract's conservative 2x assumption.

**QSA-CANDIDATES: passes the diagnostic gate and warrants one minimal implementation.** With
`S=0.040061`, `F=0.692018`, and the measured median `r=0.28`, the optimistic end-to-end upper bound
is `S*F*(1-r)=1.996%`, above the registered 1.25% gate. This does not prove a speedup. It justifies
one exact path that shares selected K/V loads across adjacent queries and preserves every query's
selection and attention arithmetic.

The next implementation must not approximate top-k with the union of final selections. Low overlap
is evidence for an attention-side union/cache loader, not for skipping indexer scores. It needs an
A-B-A on the same cold long prompt, at least 1% stable end-to-end wall and `qsa attn` improvement,
unchanged selection/attention output within the project parity gate, needle success, and no resource
regression. Reject it if those conditions fail.

## Resource and recovery evidence

- Diagnostic request: engine read 105,461.9 ms, wall 105.864 s, 14 generated tokens, one needle.
- Minimum MemAvailable 12,853.2 MiB; SwapFree remained 4,088.4 MiB.
- Peak GPU memory was about 21,228 / 21,126 MiB of 22,528 MiB.
- The first attempt exposed and fixed a real hook defect: FP16 buffers were process-wide, so the
  second GPU in a layer split received pointers allocated on the first. The failed attempt produced
  no accepted performance sample and is retained separately.
- The baseline binary was restored byte-for-byte. Candidate 8080 is healthy with engine
  `105a4171...`; diagnostic env is absent. Production `strata.service` remains `inactive/dead`,
  `MainPID=0`, `NRestarts=23`.

## Evidence

Directory: `/opt/ai/logs/qsa-diag-20261031T1653Z/`

- `contract.md` (sha256 `4945d3c327e1cc6273f707d93bd899c7a64bfe5df590e5e7b81ad3cbd2ede104`)
- `diag.json` (sha256 `86112d421fc39393259b0776145e7d99e04bf164fa85c3ba27c054da8b73dfaa`)
- `request-pass2.jsonl` (sha256 `f35f1660276668999d995a68ca770e381e6acabd8b457e45b7cf1543152e0dc2`)
- `summary-pass2.json` (sha256 `e8008ed4cbff60686362fcc2030d2ae09612359b1e70e090bd6e9db7a6543770`)
- `engine.log` (sha256 `ecc078ac7d5a6f439e05740d0cbc4c86dd9e540a1141922fc92f6d9b56e0b10b`)
- `diagnostics-extract.txt` (sha256 `401653992bcd5fd360b9e703409c491dbb67590de521027d67b50e8ab19c35ac`)
- `qsa-select-bench.txt` (sha256 `d8bdd81e3c493a36c2b236fb91d109a735009a8cf3f8e8985fcdf474d0ae122c`)
- `failed-request-cuda-device.jsonl` and `failed-summary-cuda-device.json` retain the rejected
  first attempt.

## Not measured

This is one synthetic 150K request on one sm_75 two-GPU host. It does not establish overlap for
natural-language distributions, concurrent traffic, other context lengths, cancellation, or other
backends. The overlap gate is an upper-bound screen; only a complete A-B-A can accept an
implementation.
