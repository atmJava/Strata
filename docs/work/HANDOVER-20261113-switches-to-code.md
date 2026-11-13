# Handover, 2026-11-13: the switches became code, and the build did not deploy

## What runs on 192.168.9.100 now

| part | identity |
|---|---|
| engine | `build-guard-prod/strata`, md5 `950829a6` (the deployment's own) |
| server | `/opt/ai/src/strata-0.2.7-exp`, content of `exp/swift-iq3s-trial`; `serve/server.py` md5 `349ed71c` |
| config | byte-identical to the backup in `/opt/ai/scratch/rollback-1027/` |

Rollback: that directory holds the binary, config, run script, unit text and the four touched Python files; three
`cp`s and a restart.

The deployment's tree equals `exp/swift-iq3s-trial` (13ac39da2), not this branch's base. They differ in four lines of
`src/program/generate.cpp`: the PLE probe's buffer. This branch already carries the fixed form.

## Measured today

- `kv_bytes_per_token = 14220` in INT8 over 12 QSA layers; with 12,288 MiB that is **5.5 conversations** at 160K.
- Five sessions, two turns (`tools/sim_client.py --sessions 5 --turns 2 --context-mix 60000,40000,20000,15000,10000`):

| session | turn 1 read | wall | turn 2 reused / read |
|---|---|---|---|
| 0 | 15,179 | 47.9 s | 22,854 / 37 |
| 1 | 15,288 | 36.1 s | 15,283 / 37 |
| 2 | 7,808 | 17.3 s | 7,803 / 37 |
| 3 | 5,823 | 10.5 s | 5,818 / 37 |
| 4 | 3,894 | 4.9 s | **0** / 3,926 |

- Turn-2 reuse comes from the conversation cache's restore (`restored 22854 tokens (checkpoint)`), not from a slot
  prefix: `prefix_hits = 1` against `requests_reused = 5`.
- Session 4 has no history to restore: 3,894 is below `--conversation-cache-min-tokens 4096`, so it was never parked.
- The wait still dominates: session 0 read 11.7 s and took 47.9 s of wall time.

## The deployment that failed

Builds from this tree faulted the engine on the first concurrent batch request: `mtp draft: an illegal memory
access`. Ruled out one at a time:

- The config's `env` map still fed the engine `STRATA_BATCH_SELF_COMMIT` (`verify.cpp`, `generate.cpp`); unset, the
  commit graph faults on this box. Both reads are constants now, and the fault remained.
- `STRATA_ROUTE_TAIL_SKIP` defaults to 0 when unset (the deployment's value); `STRATA_EXP_MODE` is a bench.
- `verify-r4` was not a copy of HEAD (three files had other hashes), which produced three mixed builds. Rebuilt with
  `git archive HEAD` into `verify-r5`: same fault, so the tree was not the cause.
- All 27 env values match the folded constants; every startup size (draft head over 106,299 tokens, borrowed expert
  slots, 8-token window) is identical between the two binaries.
- **The build differs.** `build-guard-prod`'s cache has `CMAKE_CUDA_FLAGS_RELEASE` and its three siblings **empty**;
  a fresh configure gives `-O3 -DNDEBUG`. `NDEBUG` drops the two arena asserts at `src/core/session.cpp:184` and
  `:204`; `:204` is the one whose comment warns of "one table short and a silent overflow of whatever follows the
  arena".

In flight: `/opt/ai/scratch/verify-r5/build-r5b`, this tree with those four flags empty (asserts live). Passing means
asserting builds are what this box runs; failing prints the broken invariant.

## Next

1. Deploy `build-r5b` and run the same five-session load.
2. Park from 2,048 (the park-gain threshold) instead of 4,096, and prove it with session 4.
3. Rank admission by `plen - reused` instead of `plen`; `serve/test_admit_order.py` can hold it.
4. Remove the no-op `--trim-stage-weights` (help text, launcher, `DETAILS.md`).
5. Reconcile `DETAILS.md`'s switch table rows that still name dead variables.
6. Do not delete the config's 27 `env` keys or the cache arguments until a build with the constants is the binary on
   disk: today's binary still reads `STRATA_BATCH_SELF_COMMIT`, and without it the engine faults.

## Two things worth keeping

- A verification tree is trustworthy only when it came from `git archive HEAD`.
- The engine's CMake cache is part of the engine: the four empty `CMAKE_CUDA_FLAGS_*` entries keep its asserts.
