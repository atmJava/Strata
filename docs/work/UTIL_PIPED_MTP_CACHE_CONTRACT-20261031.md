# UTIL-PIPED-MTP-CACHE contract

Status: rejected at startup. `--expert-cache-device1` is a helper-cache option and cannot cap the
second stage of a two-GPU layer split; see
[`UTIL_PIPED_MTP_CACHE_RESULT-20261031.md`](UTIL_PIPED_MTP_CACHE_RESULT-20261031.md).

## Evidence and selected problem

The six-slot `STRATA_PIPED_MTP=1` run improved two-stream aggregate throughput by `19.92%` with
five of six paired wins, but failed the adoption gate:

- four streams: `-3.82%`;
- six streams: `-9.79%`;
- fixed greedy output was different from A and not repeatable within B.

The losing arm also had a different expert cache. A held `10065 + 7209 = 17274` slots. B held
`10064 + 7040 = 17104`; CUDA1 lost 169 slots to the six slot drafters. The engine documentation
says text equality between arms requires the same expert cache and that changed CPU/GPU expert
residency can alter the last digits. This is a documented confound, not yet a proven cause of the
observed text difference.

The next experiment therefore asks one question: **is the parity failure caused by the smaller
expert cache, and does restoring both cards' cache counts also recover the four/six-stream
regression?** It does not scan draft depth, slot count or group count.

## Candidate

Copy `b-piped-mtp.json` to a new file. Keep `--batch-mtp`, `STRATA_PIPED_MTP=1`,
`STRATA_BATCH_SELF_COMMIT=0`, `--batch 6`, `--batch-groups 2` and every other setting unchanged.
Replace the cache sizing with the A counts:

```text
--expert-cache 10065
--expert-cache-device1 7209
```

The startup must report:

- exact primary cache `10065` slots and CUDA1 cache `7209` slots, total `17274`;
- `--batch-mtp: 6 slot drafters`;
- `STRATA_PIPED_MTP: 2 ... groups ... 3 slots ... 1 MTP proposal`;
- `batch_slots=6`, `batch_groups=2`, health loaded;
- engine sha256
  `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`.

If an explicit cache does not fit, requires changing a reserve, loses a slot, silently falls back,
or differs from the requested counts, reject immediately and restore A. Do not scan cache sizes.

## Frozen parity

Run the same three fixed greedy probes twice as in
`UTIL_PIPED_MTP_RESULT-20261031.md`, after a fresh candidate start:

1. exact text `STRATA_PARITY_ALPHA`;
2. the arithmetic probe;
3. the speculative-decoding explanation at `max_tokens=96`.

Candidate success requires:

- both candidate trials are byte-identical for each probe;
- every candidate probe equals the A text with the same finish reason;
- all requests succeed.

If any probe differs, **reject the cache-neutral candidate without spending a performance A-B-A**.
This is a correctness gate; do not use a semantic similarity score to waive it.

## Frozen throughput

If parity passes, use `tools/server/bench-strata.py` with `BENCH_N=2/4/6`, three repetitions each.
The pre-candidate A baseline is the restored A2 from the preceding run or a fresh A3 immediately
before the candidate. After the candidate, restore A and run A3 with the same workload.

Keep gate:

1. two-stream candidate aggregate is at least `1%` above the A2/A3 combined median and wins at
   least `5/6` paired bursts;
2. four- and six-stream medians are no more than `1%` below A2/A3;
3. zero request errors, zero unexpected finish reasons and all six slots remain available;
4. RAM, VRAM and swap stay within the current safety envelope;
5. production systemd and its files remain unchanged.

Passing permits one longer mixed-traffic soak and only then adoption of the exact candidate
configuration. A sub-one-percent gain is rejected.

## Reject gate

Restore A and record a rejection if any of these occurs:

```text
explicit cache counts do not match A
startup loses a slot or needs a reserve change
any greedy probe differs or is not repeatable
two-stream improvement is below 1%
four- or six-stream median regresses by more than 1%
health, resource or request-integrity check fails
```

After failure, do not scan nearby cache sizes. If exact cache restores parity but six streams still
regress, the next target may test concurrency-aware gating of the piped drafts; that is a separate
code contract.

## Restart cost

The candidate needs one A-to-candidate start and one candidate-to-A restore, about 3–4 minutes
each on the current box. The experiment is justified because the existing candidate already
demonstrates a `19.92%` two-stream opportunity and the cache mismatch is the documented
prerequisite for output equality.
