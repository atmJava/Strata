# UTIL-BATCH-GROUP-FIT result

Run: 2026-10-31 on the 2×RTX 2080 Ti candidate, CUDA 13.0, compute capability 7.5.

## Decision

**UTIL-BATCH-GROUP-FIT is rejected by the frozen greedy-parity gate.** The candidate started
correctly with six slots in one group, but the natural-language probe repeated a 94-token answer
ending in `finish=stop`, while A repeated a different 96-token answer ending in `finish=length`.
Candidate throughput was not run after the failed gate.

The result also overturns the contract's assumption that changing only the group count leaves the
model arithmetic unchanged. The one-group path is internally repeatable, but it is not bitwise
compatible with the two-group path. A path that changes generated text does not qualify as a
configuration-only throughput improvement.

## A3 eight-stream baseline

The restored A config used six slots and two groups, config SHA-256
`88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f`, with engine SHA-256
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`.

| Streams | Aggregate median tok/s | Wall median s | TTFT median ms |
| ---: | ---: | ---: | ---: |
| 2 | 81.0 | 9.9 | 142.0 |
| 4 | 116.1 | 13.6 | 293.0 |
| 6 | 133.2 | 17.8 | 967.5 |
| 8 | 104.4 | 30.1 | 1926.0 |

The eight-stream repetitions were `103.4/115.4/104.4 tok/s`. Draft acceptance was `75.48%`
across 27 requests. There were no foreign requests; median `slot_wait` was `1468.5 ms`, median
decode time was `8567 ms`, and median decode rate was `21.9 tok/s`. Two requests stopped
legitimately at `363/375` tokens with `finish=stop`; the others ended by length.

This confirms a real concurrency plateau: eight streams are slower than six even though more work
is admitted. It does not justify selecting one group.

## Candidate isolation

The candidate was copied from A to
`/opt/ai/logs/qsa-diag-20261031T1653Z/e-batch-groups-1.json` (SHA-256
`0437977f454de71f3f5423598b501f50671fc590d5cd81fe4d7a2a5f06cdc700`). Only the
`--batch-groups` argument changed from the JSON string `"2"` to the number `1`. Restoring `"2"`
in a deep copy reproduced A exactly.

B started with six slots, one group, `expert_slots=17274`, `expert_cache_mib=28267`, correct
engine hash and health `loaded=true`. The A-to-B shutdown took 8 seconds and startup to loaded
took 206 seconds.

## Greedy parity

Both B trials produced the same 94-token answer:

```text
Because the target model accepts only tokens that match its own greedy argmax choice, so the accepted sequence is identical to standard greedy decoding.
```

A produced a different 96-token answer:

```text
Because the target model accepts only tokens that match its own greedy argmax choice, so verification filters out any incorrect proposals and leaves the output identical to standard
```

Probe 0 (`STRATA_PARITY_ALPHA`) and the arithmetic probe (`944`) matched A. The failure is stable
and specific to the natural-language sequence rather than a transient startup or request error.

## Restore and resources

A was restored with server/engine/vision PIDs `529088/529112/529097`, health `loaded=true`, six
slots, two groups, `expert_slots=17274`, `expert_cache_mib=28267`, and the expected engine and
config hashes. B-to-A shutdown took 9 seconds and startup to loaded took 180 seconds, for a total
interruption of `3:10`.

The production `strata.service` remains `inactive/dead`, `MainPID=0`, `NRestarts=23`. At the final
check RAM had `14.1 GiB` available, swap used `12 MiB`, the GPUs were idle, and VRAM free was
`1006 MiB` per card.

## Evidence

- `/opt/ai/logs/util-batch-group-fit-a3-n8-20261030T195241Z.log`
  (sha256 `76b3c96cd01262f0cbb2e7b42f0a90077e67810dfc2677de377cd423e164fcff`)
- `/opt/ai/logs/util-batch-group-fit-a3-n8-final-metrics-20261030T195241Z.json`
  (sha256 `78b78e61ec83a60f6cc2b3fe6a5e69d27ea46c49377996d8481b0bd2257a5877`)
- `/opt/ai/logs/util-batch-group-fit-a3-n8-resources-20261030T195241Z.txt`
  (sha256 `c6ef66f92703501e3146674a8965efddfe31d16b1dcadc5dc434e7544b477ece`)
- `/opt/ai/logs/util-batch-group-fit-parity-B-20261030T2001Z.json`
  (sha256 `6221d00e17d3a123af5bade9688d08f61396fccf1c8f2489dc0d9aacc249fd0b`)
- `/opt/ai/logs/util-piped-mtp-parity-A-20261031T1908Z.json`
  (sha256 `2ab2dcfc6dfed18e287b308ca5c560f34194164468aa58776fc4c54c30d94003`)
- `/opt/ai/logs/util-batch-group-fit-candidate-20261030T195643Z.out`
  (sha256 `a747c19c51d73abf313d116ca1445ded36be0fb36678b465c94231a904821282`)
- `/opt/ai/logs/util-batch-group-fit-switch-A-to-B-20261030T195643Z.txt`
  (sha256 `014ed4ce9f27ca08f7de82be4db0a203b0364e23e653dc2d7e565ca88adc171a`)
- `/opt/ai/logs/util-batch-group-fit-B-final-metrics-20261030T200045Z.json`
  (sha256 `a239f0fb5139078d5ba65e5270531d1dae87227fe3e693be6f6f73d51323c42e`)
- `/opt/ai/logs/util-batch-group-fit-B-final-resources-20261030T200045Z.txt`
  (sha256 `9c14dc260378ac0d135e02d16a461c340de9ed55f0d518127623d2b23b290e71`)
- `/opt/ai/logs/util-batch-group-fit-restore-A-20261030T200045Z.out`
  (sha256 `7055aa71115c2c0265ac6ebe0d5ba25564f04769340c960d8272fd3758e8c2d0`)
- `/opt/ai/logs/util-batch-group-fit-switch-B-to-A-20261030T200045Z.txt`
  (sha256 `3871c51ada33749084278de8ffad016114a0b8efdb8c5ca27c33bc86ff390de6`)

## Not measured

The candidate did not run the 2/4/6/8-stream performance arms because parity failed first. The
historical one-group regression at four streams remains context, not a substitute for a current
candidate measurement. Any later concurrency-aware group selection must first explain and pass
the parity difference; scanning other fixed group counts is out of scope.
