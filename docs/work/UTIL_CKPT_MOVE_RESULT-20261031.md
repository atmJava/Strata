# UTIL-SLOT-CKPT-MOVE result

Run: 2026-10-31 on the 2×RTX 2080 Ti A instance, CUDA 13.0, engine
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`.

## Decision

**UTIL-SLOT-CKPT-MOVE is rejected.** Moving the admission checkpoint instead of copying it kept
the fixed greedy probes byte-identical, but it did not produce a robust throughput gain. At eight
streams the candidate median was `107.8 tok/s` against an A3/A4 combined median of `111.15 tok/s`,
a regression of `3.014%`, and the candidate won only `2/6` paired bursts. The two-stream nominal
`+3.099%` was not adoptable: it won only `4/6`, while 4 and 6 streams gained only
`2.259%` and `0.122%`.

The A3/A4 drift was also outside the contract at 2/4/6 streams. This is recorded as a failed
adoption, not as evidence that the switch improves any production level.

## Candidate isolation

A was copied to
`/opt/ai/logs/qsa-diag-20261031T1653Z/f-slot-ckpt-move.json` (sha256
`228aad6d40e8cf0cba3db3053f5a1e4651ae019f3c901c09f777cfae84e283d1`). Deleting the new
`STRATA_SLOT_CKPT_MOVE=1` entry reproduced A exactly. The candidate started with six slots,
two groups, `expert_slots=17274`, `expert_cache_mib=28267`, `vram_free=1006`, health loaded and the
expected engine hash.

## Parity

The initial free-form speculative-decoding parity sentence was rejected as a fixture before the
candidate started: two A trials already produced different valid sentences. The final frozen
probes were:

1. exact text `STRATA_PARITY_ALPHA`;
2. arithmetic result `944`;
3. exact copy of `The quick brown fox jumps over the lazy dog.`

Both candidate trials matched A byte-for-byte with the same `finish=stop` for every probe. Parity
therefore passed and did not cause the rejection.

## Throughput

Each cell is the aggregate median of three 400-token bursts. A3 ran immediately before candidate
startup; A4 ran after candidate restore. The table also shows the gate-relevant paired wins and
A3-to-A4 drift.

| Streams | A3 tok/s | Candidate tok/s | A4 tok/s | A3/A4 combined median | Candidate delta | Wins | A3→A4 drift |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 80.5 | 81.5 | 73.3 | 79.05 | +3.099% | 4/6 | −8.944% |
| 4 | 111.9 | 110.9 | 105.0 | 108.45 | +2.259% | 3/6 | −6.166% |
| 6 | 130.4 | 123.1 | 121.2 | 122.95 | +0.122% | 3/6 | −7.055% |
| 8 | 112.3 | 107.8 | 110.0 | 111.15 | −3.014% | 2/6 | −2.048% |

All benchmark windows had zero foreign requests. The 8-stream TTFT median was `1732.5 ms`,
slot-wait median `1281.5 ms`, decode rate median `22.5 tok/s`, and draft acceptance `78.42%`.
These values are not used to claim a gain because the adoption gate failed.

## Restart cost and restoration

- A to first candidate: stop `9 s`, startup to loaded about `100 s`.
- First candidate to A: restore to loaded about `205 s`.
- Same-cycle A3 to candidate: stop about `8 s`, startup to loaded `200 s`.
- Same-cycle candidate to final A4: stop about `8 s`, startup to loaded `160 s`.

Final A is loaded with server/engine/vision PID `541714/541773/541717`, six slots, two groups,
engine/config hashes unchanged, and no `STRATA_SLOT_CKPT_MOVE` in the engine environment.
Production `strata.service` remains `inactive/dead`, `MainPID=0`, `NRestarts=23`. Final RAM
available was `13.5 GiB`, swap used `12 MiB`, and both GPUs were idle.

## Evidence

- `/opt/ai/logs/util-slot-ckpt-move-parity-A-final-20261030T2018Z.json`
  (sha256 `b9101db0a5162edf895cdc13b5a14ab9206ef6c17ead200d2e44c750568ac542`)
- `/opt/ai/logs/util-slot-ckpt-move-sameboot-parity-B2-20261030T2047Z.json`
  (sha256 `4fd4ac03cc63c4e0aeef3402c4ee27e90f32147ce9053124ed66a1c914595867`)
- `/opt/ai/logs/util-slot-ckpt-move-sameboot-A3-20261030T2035Z.log`
  (sha256 `48979151041ddb023fcea6c9ed99a41cad347b64a3bd6c4f8c1e0f4706661d49`)
- `/opt/ai/logs/util-slot-ckpt-move-sameboot-B2-perf-20261030T2048Z.log`
  (sha256 `1239e078bde8fece8b9511e10f8c533364be4939ae4bc9398e1e04b915752468`)
- `/opt/ai/logs/util-slot-ckpt-move-sameboot-A4-perf-20261030T2058Z.log`
  (sha256 `8c38172b61a61e9d0c90b2712ec5184308926bc90ffa6f140c22b48e50438c53`)
- `/opt/ai/logs/util-slot-ckpt-move-sameboot-summary-20261030T2105Z.json`

## Not measured

No mixed long/short soak, long-context prefill or conversation checkpoint-save scenario ran after
the throughput gate failed. The switch is not adopted and does not need those follow-up checks.

## Next question

Return to `UTIL-MULTI` with the same A. The next source audit should target the one-row GDN batch
path and pipelined window state, not checkpoint/cache copying. Any candidate must use an untried
mechanism and preserve the same frozen parity and A-B-A gates.
