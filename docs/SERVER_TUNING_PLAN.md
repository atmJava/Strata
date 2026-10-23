# Server tuning: the plan

For the machine in [SERVER_HW.md](SERVER_HW.md): 2 × RTX 2080 Ti 22 GB (`sm_75`), NVLink, Xeon E5-2680 v4,
62 GiB RAM, CUDA 13.0, driver 595.71.05. This file holds the analysis, the measured results, the rules a change must
follow, the acceptance and the test ladder.

Read order: this file, then [SERVER_HW.md](SERVER_HW.md), then the server's own record (below) before proposing
anything, then [CONTRIBUTING.md](../CONTRIBUTING.md) and [AGENTS.md](../AGENTS.md). The global plan — what happens
next, in what order, and what "finished" means — is [SERVER_MASTER_PLAN.md](SERVER_MASTER_PLAN.md).

## The server's own record (read this before proposing anything)

The box has 24 tuning documents in `/opt/ai/docs`, indexed by `/opt/ai/docs/README.md`. That index opens with a
warning: a round of work in October re-tuned three things that the documents had already settled, and one of them had
been rejected before and was adopted again. The list below is the part that decides what is worth trying here.

| Already settled | Result | Where |
|---|---|---|
| `--pipeline-windows` on two cards | −41%, do not touch | `strata-server-tuning-20261012.md` |
| `--pool-workers 27` | −5% | `strata-64gb-tuning-20261011.md` |
| `--peer-device` | prefill −41% | `strata-agent-latency-20261016.md` |
| `--ple-io ram` (26.8 GiB table in RAM) | no gain; generation-time NVMe reads are 0.81 MiB/s | `strata-64gb-tuning-20261011.md` §5 |
| `--ple-inflight` 1024 | no gain, reverted | `strata-agent-latency-20261016.md` §12 |
| `--kv-resident` raised | upper bound ≤3% (29 MiB per request at 137K) | `strata-agent-latency-20261016.md` |
| `--kv-grow` | single GPU only; unusable on a layer split | `strata-tuning-plan-0.2.2-20261022.md` §4 |
| `--mtp-window` above 32K | no VRAM left (689 / 1039 MiB free) | same, §0.5 |
| Eight slots | ~34 GB pinned RAM; 62 GB is not enough | same, §E |
| Tensor-parallel patch | +3% single stream, parked | `strata-optimized-default-20261018.md` |
| `STRATA_BF16_TC=1` | adopted, prefill +18.2% | `strata-tuning-record-20261013.md` §10 |
| `STRATA_SELECT_SIMT=1` | adopted, prefill +4–14.5%, answers change | `strata-server-tuning-20261012.md` |
| `--spec-min-p 0.7`, `--prefill-pipe 1` | adopted (+1.9%, +5.6%) | `strata-tuning-plan-0.2.2-20261022.md` §3 |
| Conversation cache 12288 / 12 / min-tokens 4096 | adopted, evictions fell from 68–81 to single digits | same, §B |

Two facts about the shape of this box, from the same record, that limit what can be won:

- **The two cards are 280 W each and that is the ceiling.** During prefill both sit at 98–100% with the clocks at
  1425–1870 MHz; the driver and NVLink are not the limit.
- **A layer split is serial inside one request.** One card computes while the other waits, so a single stream cannot
  go much past 71–76 tok/s; concurrency, not single-stream speed, is where this box gains.

Measurement discipline the record earned the hard way: 15 of its 48 pitfalls are method errors, each of which once
produced the opposite conclusion. Decode moves with the draft acceptance rate (about +0.6–0.7 tok/s per point), the
same configuration can read 72.6 / 73.7 / 77.1 tok/s across three boots, and **a difference under 3% is not a win**.
Same-boot interleaving is preferred; a change that needs a restart is A-B-A or not a result.

## Where it stands

Engine 0.1.47, measured 2026-10-23 on this box, one boot per arm, A-B-A for the adopted change.

| Thing | Before | With `STRATA_SM75_PERF=1` |
|---|---|---|
| 84K cold prefill | 2375.6 / 2381.3 tok/s | 2552.0 / 2566.5 / 2556.9 tok/s |
| Cold prefill, 43–50K prompt | 2108 / 2094 tok/s | 2236 / 2258 tok/s |
| Four sessions at once | 111.3 / 112.5 tok/s total | 119.7 / 117.1 tok/s total |
| Decode, 512 tokens after 84K | 67.8 / 71.2 tok/s | 73.5 / 72.3 / 67.9 tok/s (and 75.6, 78.0 in repeats) |
| Expert cache | 18801 slots, hit rate 98.5–98.9% | same |
| KV streaming | 32768 of 256000 cells in VRAM, 96.8% of block reads hit VRAM | same |

The earlier record's numbers still describe the box: single-stream decode 79.1 tok/s with `--batch-groups 2`,
four sessions 103–108 tok/s, four sessions at 28–31 each, TTFT around 3.25 s on a real agent load.

What the engine prints at start, every time:

- `this CPU has no AVX-512: the expert kernels run on AVX-2`.
- `--ple-io direct measures slow here` (7018–8580 rows/s, 29–35 MB/s of random 4 KiB reads).
- `experts via mmap`, io prefetch with whole-blob preads on 8 I/O threads.
- 13 CPU pool workers on the 14 physical cores, host on core 0.

## What the parameters did, and what they did not

### P1 — the Turing kernel series: adopted

Upstream has a set of kernels for compute capability 7.5 only — PRs #2069–#2072, #2081, #2091–#2093, #2145–#2146,
#2220–#2223, #1821 — that are documented as bitwise equal to the kernels they replace. They were measured on one
RTX 2080 Ti (decode +5–6% at 4K–32K, prompts at depth up to +30%), but the team has no `sm_75` card to confirm them,
so `include/strata/core/sm75_series.hpp` leaves them off unless asked for. This box had never set it: the server's
parameters table lists the 724 `STRATA_*` variables and none of its tuning documents tried this one.

`STRATA_SM75_PERF=1` turns all of them on, on a 7.5 card only. Measured here:

| Step | 84K prefill | cold prefill (43–50K) | 4 sessions | decode |
|---|---|---|---|---|
| base-A | 2375.6 | 2108 | 111.3 | 67.8 |
| SM75 on | 2552.0 | 2236 | 119.7 | 73.5 |
| base-B (reverted) | 2381.3 | 2094 | 112.5 | 71.2 |
| SM75 on again (with the KV arm) | 2566.5 | 2258 | 117.1 | 72.3 |
| SM75 on, final state | 2556.9 | — | — | 67.9 |

- Prefill: **+7.2% to +7.6%** at 84K (three samples each side, no overlap).
- Cold prefill: **+6.0% to +7.8%** (two samples each side, no overlap).
- Four sessions: **+4.1% to +7.5%** (two each side).
- Decode: 67.9–78.0 on the new arm against 67.8–71.2 on the old one. The direction is up (about +5%) but the spread
  is wider than the effect, so it is **not claimed** as a decode win; the draft acceptance rate carries it
  (275/347 and 255/325 accepted, against 259/328 and 280/359 before).

**Bitwise was not verified, and the harness cannot verify it here.** The documented claim is that these kernels are
bitwise; the end-to-end text is not. The same prompt run twice inside one arm gave three different `text_sha256`
values and different draft counts, so this configuration's output is not reproducible run to run — the server's own
plan says the same: "default parameters, on/off `text_sha256` differ by design", and a bitwise check needs
`STRATA_IQ_MT_MIN=1`, `--pcie-frac 0`, a fixed adaptive and `--no-prefill-borrow`. That check is still open (T2).

Risk taken: the series changes nothing outside a 7.5 card, `=0` restores the old kernels, and a revert plus restart
is 150–180 s. The change is one line in the run config's `env` block.

### P2 — overlapping the long-context KV upload: no gain, dropped

`docs/KV_PREFETCH.md` describes `STRATA_KV_PREFETCH=1`: the next layer's KV upload runs on its own stream while the
current layer computes, reusing the staging pool. It was measured on two RTX 3080 20 GB cards at 5.3% (32K) and 4.1%
(64K) prefill.

Measured here, on top of the Turing series, with the same A-B-A shape:

| Arm | 84K prefill | cold prefill | 4 sessions |
|---|---|---|---|
| SM75 only | 2552.0 | 2236 | 119.7 |
| SM75 + KV prefetch | 2566.5 (+0.6%) | 2258 (+1.0%) | 117.1 (−2.2%) |

All three are inside the noise or below the 3% bar. The switch was removed. Why it does not reach this box is not
established; candidates are the layer split (each card owns half the layers, so half the KV upload per stage), the
32,768 resident cells already covering the first 32K, and the concurrent-read change (S1) already moving the read
off the decode's path. Recorded as measured, not explained.

### P3 — `--ple-io`: open, and the record has the numbers

The engine says `--ple-io direct` measures slow on this SSD and warns that a prompt can spend minutes in layer 1.
The record already quantified it: a brand-new 8.2K prompt takes 6.9–7.2 s, of which the host PLE bucket is
5.98–6.58 s; the same content a second time takes 0; `--ple-inflight 1024` did nothing; `--ple-io ram` is rejected
for RAM; the read path fetches 90-byte rows as 8 KiB. The prefill timing lines in the current log still show
`PLE 33xxx ms` on long contexts, and those numbers sit beside `wall 421 ms`, so what the field measures remains
unread.

This is the largest remaining item for a *server*: a new agent session pays it on its first prompt. The three
candidates the record lists are: the table in memory (no RAM), a 512-byte row read, and issuing the read earlier in
the engine. The third is a code change; the second is a code change; the first is decided.

### Not tried, and why

- The C track (lookup-chain, suffix-draft, spec window): pipelined slots run no per-slot drafts by design, and this
  box serves four slots. It only moves a single stream.
- `STRATA_PF_FUSED_SM75=1` (fused int8 prompt experts): it changes the bits and needs a quality gate, not just a
  reading. It is the next candidate after P3 if prefill is still the target, with the KL gate as the price.
- A `sm_75` default for the Turing series: it would change upstream's default for every 7.5 card. Fine as a proposal
  to upstream once this fork has a bitwise check behind it; not as a local patch.

## Bottlenecks, after the measurements

| Rank | Item | Evidence | Status |
|---|---|---|---|
| 1 | PLE read on new content | 6 of 7 s of a new 8K prompt; `direct` at 7–8.6K rows/s; the log's `PLE 33 s` | open, largest |
| 2 | Prefill expert GEMM on `sm_75` | `gemm gate/up` 18–26% of the prefill timeline; int8 fused prompt experts are opt-in and change bits | candidate |
| 3 | Long-context KV from RAM | 40+ GiB per 184K request; the overlap switch gave nothing here | measured, closed |
| 4 | Decode single stream | ~79 tok/s; the ceiling is the serial layer split, not a kernel | closed by the record |
| 5 | CPU share (AVX-2) | the engine's own policy; prefill is 93.6% in `libcuda.so`, so the CPU is not the limit | closed by the record |

Ruled out with a measurement, again: NVLink (47.05 GB/s, 0.46% of the window), clock locking, `-lgc`,
`CUDA_DEVICE_MAX_CONNECTIONS=1`, the driver.

## Goals

| # | Goal | Status |
|---|---|---|
| G1 | Know what the default path does, repeatably | done: the A-B-A harness and the baseline above |
| G2 | Take the bitwise Turing kernels if they hold up | **done for speed: +7% prefill, adopted.** Correctness (bitwise) still open |
| G3 | Overlap the long-context KV upload | **closed as failed**: +0.6%, dropped |
| G4 | Make the PLE read a measured choice | open; the record narrows it to row size or timing, not the table in RAM |
| G5 | Change code only where the numbers ask | open: PLE row read and the `sm_75` prompt experts are the two candidates |

## Non-goals

- A new quantization, model or cache design; the API surface.
- Anything that changes the default path for other machines. Opt-in and `=0` to restore, always.
- Topologies that are not this box (3–4 cards, unequal pairs).
- Re-testing what the record has already settled (the table at the top). If a new measurement is wanted, the reason
  has to be written first.

## Rules

On top of [CONTRIBUTING.md](../CONTRIBUTING.md) and [AGENTS.md](../AGENTS.md).

### Reading before writing

1. **The source on disk is the reference.** The serving tree is `/opt/ai/src/strata-0.2.2` and it carries S1; this
   checkout's `hw/server-adapt` does not. A code change starts from `hw/s1-concurrent-read` until S1 is merged.
2. Read the file, then every call site (`git grep -n`), then the test that covers it. Record the owner of every
   buffer involved before the edit. A branch is not opened on a guess.
3. Read `/opt/ai/docs/README.md` and the document that owns the question first. That is cheaper than a restart.
4. Mark a conclusion that comes from reading code as unconfirmed static analysis.

### The shape of the change

5. One branch, one change, based on `main` (or S1 while unmerged).
6. The default path stays byte-identical to the release; new behaviour is opt-in and `=0` restores the old path.
7. Reuse what is there: the staging pool, `ConcurrentReadSetup`, the timing fields, the log prefixes
   (`strata batch:`, `strata prefill timing:`, `strata generate:`).
8. Comments say why and what was measured, in the voice of the neighbouring lines.

### Resources

9. Every buffer names who allocates it, who reads it, who may recycle it, and on which stream or thread. S1 is the
   worked example: a source that recycles a buffer the reader still holds.
10. Release is part of the change: destructor, error path and cancellation path each drain their stream.
11. No new allocation on the decode or prefill hot path; take it from an existing pool.
12. Threads are named, pinned like the existing ones, and stopped where the others stop.

### Errors, probes, logs

13. Follow the file's error style: report and return, or fall back, never throw across a hot path.
14. A probe is off by default or free when off, and removed when the phase ends unless it earned a permanent line.
15. One log line per event, at the altitude of its neighbours; a warning names the flag that removes it.

### Style

16. Match the file: naming, comment voice, includes, `strata::`, the existing helpers. No new dependency, no
    reformatting of lines outside the change.

## Acceptance

1. Builds on this box with the [SERVER_HW.md](SERVER_HW.md) command, `BUILD_EXIT=0`.
2. A/B or A-B-A **inside one boot** where the change allows it; otherwise one arm per boot, interleaved where
   possible, medians of at least three rounds, and the difference is at least 3%.
3. An exactness gate: bitwise for a bitwise change (T2 configuration), or KL and top-1 for an answers-changing one.
4. A regression gate: the worst reading of the new arm is not below the best reading of the old arm on a workload the
   change does not target.
5. The engine log of a real request is attached; the start-up lines are unchanged and no new warning appears.
6. The commit says what was measured, on what, in which boot, and what was not measured.
7. The document that owns the claim is updated, not only the pull request.

## Test ladder

| Rung | What | Pass |
|---|---|---|
| T0 | build, `build/strata --version` | `strata 0.1.47`, `BUILD_EXIT=0` |
| T1 | start with the production config | the same start-up lines, no new warning |
| T2 | 512-token bitwise check under the upstream configuration (`STRATA_IQ_MT_MIN=1`, `--pcie-frac 0`, fixed adaptive, `--no-prefill-borrow`) | same token ids, both arms |
| T3 | decode, one stream | ≥3% better, or unchanged when not targeted |
| T4 | four sessions | same |
| T5 | cold prefill 4K / 32K / 128K | same |
| T6 | the S1 case: an 84K read beside decoding | read completes, the slot keeps 21–22 tok/s, longest stall under 2 s |
| T7 | cancellation mid-read | request ends, nothing left in flight, the engine keeps serving |
| T8 | one-hour soak | no crash, no drift, flat VRAM and RAM |

T2 matters more now: the production configuration is not reproducible run to run, so the bitwise gate has to be the
special configuration or it says nothing.

## Phases

**P0 — harness.** Done: `/opt/ai/bin/run-arm.sh` wraps the existing tools (`bench-strata.py` for one and four
sessions, `/tmp/parity.py` for a 512-token arm after an 84K prompt, `cold-prefill.py` for a cold read),
`/opt/ai/bin/apply-arm.sh` sets one `env` key and restarts, `/opt/ai/bin/set-env02.py` edits the config with a dated
backup. Results land in `/opt/ai/logs/arm-<label>.log` and the two jsonl files beside it.

**P1 — Turing series.** Done, adopted. `STRATA_SM75_PERF=1` is in the run config's `env`.

**P2 — KV prefetch.** Done, dropped. The switch is out of the config.

**P3 — PLE.** Next. Read the `PLE` field in the source first, then decide between the row-size change and doing the
read earlier. The gate is T5 at 4K / 32K / 128K plus T6, because PLE sits in the prefill path this box already
saturates.

**P4 — code.** Only what P3 leaves. Candidates: the PLE row read, `STRATA_PF_FUSED_SM75` with a KL gate, and a
`sm_75` default proposal upstream once T2 passes.

**P5 — the record.** Close the three unmeasured items in [SERVER_HW.md](SERVER_HW.md) (127K read, bitwise check,
soak) and fold the server's own documents' open items into one place.

## Open questions

1. What does the `PLE` field in `strata prefill timing` measure? `wall 421 ms` beside `PLE 33 674 ms` cannot be one
   span. Read `src/prefill/prefill.cpp` before drawing anything from it.
2. Is 22 GB reported as 22 GB? `nvidia-smi` says 22528 MiB; the engine prints 21.49 GiB free and sizes the cache
   from it.
3. Why did the KV overlap gain nothing here when it gained 5% on two 3080s? Layer split, resident cells, or S1.
4. Does `STRATA_SM75_PERF=1` hold bitwise under the T2 configuration? Until it does, the speed is measured and the
   correctness is borrowed from upstream's claim.
