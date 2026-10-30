# UTIL-HEAD-SPLIT contract

Status: completed on 2026-10-31. GPU harness and server correctness passed; the
server throughput gate rejected the candidate and the default full-head path remains.

## Evidence and selected problem

`UTIL-WINDOW-PROFILE` measured stage 2 at `18.27–19.22 ms/window`, stage 1 at
`16.08–16.89 ms/window`, and the final head at `1.04–1.06 ms/window`. The
`UTIL-BATCH-HEAD` source gate showed that delaying one group's verdict to merge two
heads creates a larger pipeline bubble, so that design was rejected.

The output projection is independent over vocabulary rows. Splitting those rows
between the two GPUs can compute both halves concurrently without changing the group
scheduler: the stage that owns the head still performs the final mixer, starts one
half on its own stream and the other on the peer's private head stream, then combines
the two row ranges in vocabulary order before the existing sampler/argmax.

This is not a retry of grouped GDN, checkpoint move, fixed group counts, QSA slot
merge, piped MTP, EXPDEC, KV resolve, or delayed cross-group head batching.

## Candidate

Opt-in:

```text
STRATA_HEAD_SPLIT=1
```

Default off. The switch is a no-op without a layer split, two usable devices, a native
head, and a vocabulary divisible into two row ranges.

The design must:

1. replace the single full-head allocation with two disjoint vocabulary-row ranges;
2. keep total head weight VRAM no larger than the current full head;
3. copy or peer-transfer the final mixed rows without overwriting verifier scratch;
4. execute both halves on private streams with one event per side;
5. write each half at its vocabulary offset in the existing row-major logits layout;
6. keep argmax, NaN checks, fixed-seed sampling, penalties, and draw counters in the
   same logical row/order;
7. preserve the one-group fallback and the complete default path byte-for-byte.

No group's verdict may wait for another group. The stage that owns the head may wait
only for the two halves of its own head.

## Harness gate

The first implementation is a standalone CUDA harness; it does not start Strata.

It must load a full native head and two row partitions, run the same activation rows,
and prove:

1. every half's logits are `memcmp`-equal to the corresponding rows of the full call;
2. joining the halves is byte-identical to the full row-major logits buffer;
3. greedy ids and fixed-seed sampled ids/draw counters are identical;
4. one-group/one-device fallback remains byte-identical;
5. graph replay with changed activations produces changed output and still matches the
   full reference;
6. peak device memory for two halves is no more than the full head plus bounded
   merge/output scratch.

The timing harness must compare:

- full head on the last-stage device;
- sequential two-half execution;
- concurrent two-half execution on two devices, including transfer of the peer half.

The harness must be run on the two RTX 2080 Ti cards with at least seven alternating
processes. A server integration or restart is allowed only if the parity passes and
the measured critical path projects at least `1%` end-to-end at one of `2/4/6/8`
streams, with no other level projected to regress by more than `1%`.

## Server gate after harness

Use A1-B-A2 on the same boot, with only `STRATA_HEAD_SPLIT=1` added:

1. three frozen greedy probes twice per arm, byte-identical and same finish reason;
2. three waves at each of `N=2/4/6/8`;
3. at least one level improves by at least `1%` against the A1/A2 combined median and
   wins at least 5/6 paired waves;
4. every other level regresses by no more than `1%`;
5. A1-to-A2 drift is no more than `3%`;
6. zero HTTP/execution errors, unexpected finishes, or foreign requests;
7. six slots, two groups, health, RAM, VRAM, and swap remain in the existing safety
   envelope;
8. the split path is hit at the tested levels and the fallback is logged;
9. production systemd, config, and runtime files remain unchanged.

## Restart cost

Harness development and measurement need no Strata restart. A source-integrated
candidate requires one A-to-B load and one B-to-A restore, recently about `150 s` per
load plus about `9 s` per stop. That five-minute cost is deferred until both the
bitwise harness and projected 1% critical-path gates pass.

## Rollback and safety

Preserve A's runtime binary and config. Keep `strata.service` inactive/dead. Do not
modify production config or runtime files. If any parity, startup, throughput, drift,
memory, or request gate fails, remove the switch, restore the exact A binary/config,
and verify hashes, health, six slots, two groups, memory, and GPU state. Never write an
API key or token to the repository or logs.

## Final server decision

The candidate met startup, path-hit, greedy parity, health, slot count and resource
checks. It did not meet the throughput contract:

| N | A combined tok/s | B tok/s | change |
|---:|---:|---:|---:|
| 2 | 77.70 | 80.80 | +3.99% |
| 4 | 114.90 | 113.40 | -1.305% |
| 6 | 124.15 | 113.40 | -8.659% |
| 8 | 106.30 | 106.20 | -0.094% |

4 and 6 streams regress by more than 1%, so **UTIL-HEAD-SPLIT is rejected**. A2 was
restored with the full head and no split environment variable. The server evidence
is in `/opt/ai/logs/util-head-split-server-20261031T0800CST/`.
