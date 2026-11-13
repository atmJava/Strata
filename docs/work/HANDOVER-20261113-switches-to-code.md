# Handover, 2026-11-13: the switches become code, and a build the deployment does not have

This round took the deployment's performance switches out of its run config and put them in the code, then tried to
deploy the result. The code part is done and tested. The deployment did not pass and the service runs the previous
binary again. What follows is what is true now, what was measured, and exactly where the next session starts.

## What is running on 192.168.9.100 right now

| part | identity |
|---|---|
| engine | `/opt/ai/src/strata-batch-prefill-guard-20261101/build-guard-prod/strata`, md5 `950829a69415d29bc2f16f8b797a4bfe` |
| server | `/opt/ai/src/strata-0.2.7-exp` at content `exp/swift-iq3s-trial` (13ac39da2); `serve/server.py` md5 `349ed71c`, i.e. the deployment's own |
| config | `/opt/ai/config/strata-swift-iq3s-trial.json`, byte-identical to `/opt/ai/scratch/rollback-1027/` |
| unit | `strata.service`, `Environment=STRATA_ADMIT_SHORT_FIRST=2048` drop-in removed, `daemon-reload` still pending (harmless) |

Rollback material: `/opt/ai/scratch/rollback-1027/` holds the engine binary, the config, the run script, the unit
text and a tar of the four `serve/` + `strata_control/` files the round touched. Restoring it is three `cp`s and a
restart.

The deployment's tree is **content-identical to `exp/swift-iq3s-trial` (13ac39da2)**, not to this branch's base
`e167dbbc0`. The two differ in exactly four lines of `src/program/generate.cpp`: the PLE probe's buffer
(`ple_direct_rows_per_s`) is `PLE_N_HEADS * PLE_HEAD_DIM` floats a token in theirs and a fixed 2560 in ours. That is a
heap overflow of 24 KB at hidden 4096; this branch already carries the fixed form, so nothing had to be ported.

## What was measured today, on this deployment

The engine reports **`kv_bytes_per_token = 14220`** over the 12 QSA layers in INT8, which is exactly the static
estimate (1,056 B of K/V + 1 page-table byte + 128-byte pooled indexer key). With `--conversation-cache-mib 12288`
that is **5.5 conversations** at the 160K compaction point, so capacity was never the lever. This number is in
`AGENTS.md` now.

A fixed 5-session load (`tools/sim_client.py --sessions 5 --turns 2 --context-mix 60000,40000,20000,15000,10000`)
against the deployment's own binary:

| session | turn 1 prompt / read | wall | turn 2 reused / read |
|---|---|---|---|
| 0 | 22,859 / 15,179 | 47.9 s | 22,854 / **37** |
| 1 | 15,288 / 15,288 | 36.1 s | 15,283 / **37** |
| 2 | 7,808 / 7,808 | 17.3 s | 7,803 / **37** |
| 3 | 5,823 / 5,823 | 10.5 s | 5,818 / **37** |
| 4 | 3,894 / 3,894 | 4.9 s | **0** / 3,926 |

Three findings, all measured rather than read:

1. **Turn 2's reuse comes from the conversation cache, not a slot prefix.**
   `/metrics` said `admission.prefix_hits = 1` but `conversation_cache.requests_reused = 5`, and the log says
   `restored 22854 tokens (checkpoint) in 59.9 ms`. `pick_slot`'s affinity is therefore not the lever; the park and
   restore path is.
2. **A conversation below `--conversation-cache-min-tokens 4096` is never parked**, so when its slot is taken it has
   no history left at all: session 4 re-read 3,926 tokens. That is the one reuse hole in the table and it is a policy
   number, not a bug. Parking 3,894 tokens of INT8 K/V costs about 55 MB.
3. **The wait still dominates.** Session 0's read took 11.7 s and its wall time was 47.9 s. The counters also showed
   `admission.all_busy = 12627` for ten requests - that was counting poll iterations, not requests, and is renamed
   `no_free_slot_polls` in the server with a comment saying so.

## The deployment that did not pass

Four builds were deployed in turn. Three crashed the engine on the first concurrent batch request with
`mtp draft: an illegal memory access was encountered`; the fourth (the clean one) crashed too. The service was rolled
back each time and is healthy now.

What the search established, in order:

- Dropping the config's `env` map silently turned **`STRATA_BATCH_SELF_COMMIT`** off: the engine still read it in
  `src/core/verify.cpp` and `src/program/generate.cpp`. Both reads are now the constant `true` (commit below). This
  was a real defect of the round, but not the crash: folding it changed nothing.
- `STRATA_ROUTE_TAIL_SKIP` and `STRATA_EXP_MODE` were the only other keys still read; the first defaults to 0 when
  unset (the deployment's value), the second is a bench.
- The verification tree on the box (`/opt/ai/scratch/verify-r4`) **was not a copy of HEAD**: `generate.cpp`,
  `verify.cpp` and `serve/server.py` all had different hashes from this branch. That defect is real and is why the
  first three builds were mixtures. It is **not** the crash: `/opt/ai/scratch/verify-r5`, made with
  `git archive HEAD` and built in a fresh directory, crashes the same way.
- The deployment's own binary survives the same load untouched (the crash counter stays at 6 across its run), so the
  fault is on our side of the fence.
- Every one of the 27 `env` values matches the constant the code now folds; every startup number that would move if a
  pool were sized differently (draft head over 106,299 tokens, the borrowed expert slots, the 8-token verify window)
  is identical between the two binaries. So the settings are not it.
- **What does differ is the build.** `/opt/ai/src/strata-batch-prefill-guard-20261101/build-guard-prod/CMakeCache.txt`
  has `CMAKE_CUDA_FLAGS_RELEASE`, `_RELWITHDEBINFO`, `_DEBUG` and `_MINSIZEREL` **empty**; a fresh configure fills
  them with `-O3 -DNDEBUG`. `NDEBUG` removes the only two release invariants that guard the arenas
  (`src/core/session.cpp:184` and `:204`); `:204` asserts that what `session_init` carved fits what the caller was
  told to allocate, and its own comment says the dangerous direction is "one table short and a silent overflow of
  whatever follows the arena". A silent overflow there surfaces later as an illegal device access in whatever runs
  next - here, the MTP draft.

The last build in flight, `/opt/ai/scratch/verify-r5/build-r5b`, is this branch built with the deployment's flags
(the four CUDA flags empty, so the asserts are live). If it passes the same load, the crash was a build-configuration
difference and asserting builds are what this box runs. If it still crashes, the assert now fires and names the
invariant instead of leaving a device fault.

## What is in this round's commits

- The five server switches became defaults (`ADMIT_SHORT_FIRST=2048`, defer-title, pin-DSH-tool-prefix,
  `ptc_tool_rewrite`, keep-DSH-compaction-tools), with `tools/sim_client.py` and its tests.
- The engine reports `kv_bytes_per_token`; the cache view carries `capacity_tokens`/`capacity_conversations`.
- `auto_conversation_cache_mib` sizes from the working set (5 conversations x 163,840 tokens) instead of a round
  number.
- Eleven engine switches became code, then twelve more, then the branches behind all of them were deleted rather than
  left dead; the last commit removes the last five helpers and the 31-line serial checkpoint save.
- `tools/run_tests.py` runs every suite from one command; `serve/test_deployment_policy.py` asserts the defaults in
  one place; `AGENTS.md` gained "How work is ordered here" and "How a round is closed".
- `pick_slot` now counts why it chose a slot (`prefix_hits`, `prefix_tokens`, `no_prefix`, `no_free_slot_polls`,
  `group_fill`) and `/metrics` exposes them under `admission`.
- Uncommitted at the time of writing, committed with this document: `STRATA_BATCH_SELF_COMMIT` as a constant (with
  the crash it caused and why), `tail_skip_rank()` returning 0 without an environment read, and the poll counter's
  rename.

## Where the next session starts

1. `/opt/ai/scratch/verify-r5/build-r5b`: wait for the link, deploy it the way `rollback-1027` says, and run
   `tools/sim_client.py` with the mix above. A pass means "asserting build" is the requirement - record it in
   `AGENTS.md` and make it the build command of record. A crash now prints the broken invariant.
2. **Park threshold.** `--conversation-cache-min-tokens 4096` leaves a 3,894-token conversation with nothing to
   restore. Park from the park-gain threshold (2,048) instead, or from a fraction of a slot, and prove it with
   session 4 of the same load.
3. **Admission by the bytes that still have to be read**, not by prompt length: a 22,859-token turn that reuses
   22,854 reads 37 tokens and is ranked as a long request today. `ADMIT_SHORT_FIRST = 2048` compares `plen`;
   comparing `plen - reused` is the same policy applied to the real cost. Unit-testable in `serve/test_admit_order.py`.
4. **`--trim-stage-weights`** is now a no-op flag: it still parses, and the `--help` text, the launcher and
   `docs/DETAILS.md` still name it.
5. `docs/DETAILS.md`'s switch table still teaches `STRATA_PREFILL_CPU_SHARE=0` and `STRATA_ROUTE_TAIL_SKIP=0` in a
   few rows; the table is only partly reconciled.
6. The run config still carries the 27 `env` keys and eight `--conversation-cache-*`/`--prompt-cache-*` arguments.
   They are inert once a build with the constants runs, but **do not delete them until that build is the one on
   disk**: the binary in `build-guard-prod` today still reads `STRATA_BATCH_SELF_COMMIT`, and without it the engine
   faults on the first batch request.

## Rules this round confirmed

- Compiling, building and running suites on the box is fine; restarting the service is asked for separately. The
  service was restarted for the four deployment attempts, each time rolled back to a working binary before stopping.
- A verification tree is only trustworthy if it came from `git archive HEAD`. `verify-r4` was edited file by file and
  produced three binaries that were not HEAD; `verify-r5` is the pattern to repeat.
- The engine's build flags are part of the engine. A cache with the four `CMAKE_CUDA_FLAGS_*` entries empty compiles
  the same source into a binary with live asserts; that difference is not cosmetic on this box.
