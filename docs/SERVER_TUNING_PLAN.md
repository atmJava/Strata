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

**Bitwise: checked on 2026-10-23, and it holds after warm-up.** The production configuration is not reproducible run
to run — the same prompt twice gave different `text_sha256` values and different draft counts — so the check has to
use the configuration upstream asks for: `STRATA_IQ_MT_MIN=1`, `--pcie-frac 0`, `--adapt-every 1000000`,
`--no-prefill-borrow`, one slot. In that configuration both arms are stable, and `STRATA_SM75_PERF=1` produced the
**same 512 greedy tokens** as `=0` (`aac0a04d…`), four runs warm plus three after a second restart. One first run
right after a restart differed once (`00281f84…`) and did not come back on the repeat restart; recorded as a cold-run
difference, not explained.

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

### P3 — `--ple-io`: measured, not a bottleneck any more

The record had the numbers: a brand-new 8.2K prompt taking 6.9–7.2 s, of which the host PLE bucket was 5.98–6.58 s,
and `--ple-inflight 1024` doing nothing. The read path is mapped in [SERVER_PLE.md](SERVER_PLE.md), including the
finding that the `PLE` field is a running total, not a per-request figure.

Measured on 2026-10-23 with the delta of that counter, on real prose (a different slice each time, because the
reader's row cache answers repeated text):

| Prompt | read | PLE, first after a start | PLE, later |
|---|---|---|---|
| 6.2–6.7K tokens | 4.9–6.6 s (1,179–1,255 tok/s) | 1,015 ms | 0–1 ms |
| 24.1K tokens | 12.2–12.4 s (1,935–1,967 tok/s) | 203 ms | 0–62 ms |

So the record's premise no longer holds on 0.2.2 with the concurrent read: the gather runs on the prompt's own thread
while the GPU works and hides. What is left is the first request after an engine start, about a second, once.

**Decision:** no PLE change. `--ple-io ram` is out for RAM, the smaller read unit is a pack change, and the read is
already issued a chunk ahead. The shape the numbers do show is the one every agent request has: the same text reads
at 1,179–1,255 tok/s at 6K and at 1,935–1,967 tok/s at 24K, so a small prompt pays a fixed cost per token. W3, the
`sm_75` prompt-expert path, was the next candidate for that shape and is **closed as not applicable** here (the pack
is native IQ3_XXS, the Turing kernels are set up for `T_Q2_0` only; see *Not tried, and why*). The one lever left for
it is `--prefill 4096`, parked until the traffic mix is known — one line in the run config.

### The tensor-parallel patch: closed, not ported

The parked patch lives in `/opt/ai/src/strata-0.2.0-tp-20261020` (base `strata-0.2.0-beta`). Its footprint, from a
file-level diff: new `include/strata/core/tp_dense.hpp`, `src/core/tp_dense.cpp`, `src/kernels/cuda/native_mmvq_split.cu`;
changed `CMakeLists.txt`, `include/strata/core/verify.hpp`, `src/core/verify.cpp`, `include/strata/core/weights.hpp`,
`src/core/weights.cpp`, `include/strata/kernels/native_mmvq.hpp`, `include/strata/kernels/verify_kernels.hpp`,
`serve/server.py`, `serve/replicas.py`, and two server tests. The switch is `STRATA_TP_GDN=1`.

It is not ported, for three reasons that are about this box rather than the patch:

1. **It needs the mode this box does not use.** Per `strata-optimized-default-20261018.md` its gate is `--batch 0`,
   no `--pipeline-windows`, no `--spec-split`, no `--batch-mtp` — one stream. This server was moved to
   `parallel 4` + `--batch-groups 2` on measurement (4 sessions +63% over the serial group), and a port would trade
   that for a single-stream gain.
2. **The measured gain is single-stream.** +3.8% on a projection benchmark and +7.5% weighted on real single-session
   use. There is no measurement of it under the pipelined 4-slot mode, and the patch's own document says it does not
   combine with it.
3. **VRAM margin.** The record measured about 293 MiB free on CUDA1 after the TP split, against a box that already
   starts at 689 / 1039 MiB free with vision loaded.

Porting it would mean rebasing the seven changed files onto 0.2.2's `verify.cpp` and weight code, redoing the
`serve/` side, and then a bit-by-bit check on top of the 300 MiB margin — for a mode the serving config does not use.

**Closed.** The tree is left in place and the decision is recorded, not forgotten. It re-opens if the load becomes
single-session dominant, or if a solo-mode endpoint (no slots) is wanted for a batch client.

### Not tried, and why

- The C track (lookup-chain, suffix-draft, spec window): pipelined slots run no per-slot drafts by design, and this
  box serves four slots. It only moves a single stream.
- `STRATA_PF_FUSED_SM75=1` (fused int8 prompt experts): **reopened 2026-10-23 — the reason it was closed was a
  comment, not a measurement.** It was closed because the Turing branch of `fused::dev_info()` sets up the `T_Q2_0`
  kernels only (`src/prefill/moe_fused_iq.cu:1302-1304`) and the comment there says the codebook formats "do not fit
  64 KB at 4 warps a row". That branch returns at `:1307`, five lines above the `setup_one` calls at `:1309-1311`
  which ask the driver for exactly this (`cudaFuncSetAttribute` plus an occupancy query, `:1213-1219`), so no card
  ever answered it. The kernel's own shared-memory formula (`:110-113`, with the constants at `:58-67` and the
  codebook sizes at `:106-109`) says the comment is wrong for this format. Arithmetic on constants read from the
  file, **unconfirmed static analysis — nothing here was run on hardware**:

  | gate/up format | codebook | shared memory, 4 warps and 2 stages | against 64 KiB |
  |---|---:|---:|---|
  | `Q2_0`, `IQ4_NL`, `IQ4_XS` | 0 | 59,648 B | fits — the only formats Turing sets up |
  | **`IQ3_XXS`** | 1,024 | **60,672 B** | **fits, 4,864 B spare** |
  | `IQ2_XXS`, `IQ3_S` | 2,048 | 61,696 B | fits |
  | `IQ2_XS` | 4,096 | 63,744 B | fits 64 KiB; not if 1 KiB a block is reserved |
  | `IQ2_S` | 8,192 | 67,840 B | does not fit |

  The distance between the format that is set up and this box's pack is 1,024 bytes: the `iq3xxs_grid`. The same
  formula says the author's *other* shared-memory claim is right — four stages at four warps is 70,888 B, which
  does not fit, and that is why Turing runs two stages (`:69-71`) — so the configuration is not what blocks this
  format. The pack is also a pair the kernels already cover: its 48 expert layers are IQ3_XXS gate/up with IQ4_NL
  down ([ORCA.md](ORCA.md), and [INTEL.md](INTEL.md) says the same), and `gu_covered` (`:1333`) and `d_covered`
  (`:1344`) accept that pair with no Turing condition on either. What stands in the way is three lines: the Turing
  branch sets up `Q2_0` only (`:1304`), `native_supported()` refuses every other pair on such a card (`:1425`), and
  `requested()` wants `STRATA_PF_FUSED=1` beside `STRATA_PF_FUSED_SM75=1` (`:1426-1427`, and
  `src/prefill/moe_fused.cu:652,675,690-696`). No new kernel and no new dispatch: the body is compiled for
  `__CUDA_ARCH__ >= 750` (`:482`) and the launch switch already routes `T_IQ3_XXS` gate/up and `Q2_0` / `IQ4_NL`
  down (`:1534,1539-1540`).

  Three things are open, and only this box settles them. **(a) Does the driver grant 60,672 bytes?** Read
  `cudaDevAttrMaxSharedMemoryPerBlockOptin` and `cudaDevAttrReservedSharedMemoryPerBlock`, the way
  `src/prefill/gdn_rec_parity.cu:1127-1129` does, and compare them with the number above. 60,672 fits 65,536 with
  room and fits 64 KiB less a 1 KiB reservation with 2,816 B to spare, but this checkout does not record the card's
  values. **(b) Does the kernel stay under 128 registers a thread** at 512 threads and one block a multiprocessor
  (`__launch_bounds__(512, 1)`, `:479`)? The same `setup_one` answers it, because it requires one resident block.
  **(c) Is it faster here than the MMQ int8 path that serves this pack today** (`src/prefill/moe_mmq.cu:273-274`,
  dispatched at `:455`)? MMQ's tile check (`:294-320`) runs against the pinned llama.cpp tree, so only the start-up
  log answers it: when a type has no tile the engine prints the line at `src/prefill/moe_mmq.cu:312-314` once per
  type, and its absence means MMQ is what runs. `STRATA_PREFILL_MMQ=0` asks the cheaper question — whether MMQ beats
  the FP16 path on this card at all — with no rebuild.

  It changes answers: int8 activations and int8 H, another sum order. So the gate is KL and top-1, not bitwise. The
  only quality numbers for this pack are from another card, and they are not in the win's favour: on an R9700 the
  IQ3_XXS pack measured first-token KL 0.00340 with MMQ against 0.00388 fused over 24 prompts, and
  [AMD_HIP.md](AMD_HIP.md) records that the flag stays opt-in **because that pool is not at or below MMQ's**;
  against the FP16 prompt path fused is at MMQ's level (0.00613 against 0.00683, [DETAILS.md](DETAILS.md)). The
  speed wins are on gfx12 (IQ3_XXS +15.7%, +19-20% with the attention work) and on sm_120. The only fused-against-MMQ
  engine numbers for a *native* pack are in `src/prefill/prefill.cpp:256` — IQ2_XS on an RTX 5070, +8% at 4K and 0%
  at 32K. Nothing has ever been measured for this pack on `sm_75`.

  Two cautions for the round that tries it. Turning it on is not swapping a kernel: `fused_ring()` demands that
  **every** layer be covered (`src/prefill/prefill.cpp:263-272`), the MoE buffers shrink to the fused path's sizes,
  the ring's byte budget goes from 384 blobs' worth to 1024 (`:280-283`) while a native pack's slot cap stays 512
  (`:276`), and with them the auto chunk and the borrowed cache slots move — so it is measured as a whole engine
  (T5 and T4), not as a kernel bench. `--peer-device` keeps the MMQ path and its buffer sizes (`:258`). And the
  fused path and `STRATA_PREFILL_HELP` are alternatives, not two arms: the help path needs the MMQ buffers
  ([MULTI_GPU.md](MULTI_GPU.md)), so both cannot be left on while the other is tested. The instrument for (b) and
  for the parity gate already exists: `tests/cuda/prefill_fused_iq_test.cpp:693-699` skips a pair only while
  `native_supported()` refuses it, so the moment those three lines are relaxed the same test runs the IQ3_XXS pair
  on this card — parity and timing, with no change to the test.
- `--prefill 4096` (smaller chunks): measured, parked. A 6.5K prompt reads 5.24/5.35 s against 5.61/5.86 s at 8192
  (+8%, the two stages overlap once the prompt is more than one chunk), while a 22.7K prompt reads 1,830 against
  ~1,950 tok/s (−6%). The chunk is tied to the ring sizing, so it is a restart option, not a per-request one. Parked
  until the traffic mix is known; flipping it is one line in the run config.
- A `sm_75` default for the Turing series: it would change upstream's default for every 7.5 card. Fine as a proposal
  to upstream once this fork has a bitwise check behind it; not as a local patch. The proposal text is in
  [UPSTREAM_REPORT_SM75.md](UPSTREAM_REPORT_SM75.md).

## Bottlenecks, after the measurements

| Rank | Item | Evidence | Status |
|---|---|---|---|
| 1 | Prefill expert GEMM on `sm_75` | `gemm gate/up` 18–26% of the prefill timeline; the same text reads at 1,179–1,255 tok/s at 6K and 1,935–1,967 at 24K, so a small prompt pays a fixed cost per token. The one candidate lever is the fused int8 path, whose closure was reopened 2026-10-23 | the only open lever, unmeasured |
| 2 | PLE read on new content | 0–62 ms per request at 6–24K tokens, ~1 s on the first request after a start ([SERVER_PLE.md](SERVER_PLE.md)) | measured, closed |
| 3 | Long-context KV from RAM | 40+ GiB per 184K request; the overlap switch gave nothing here | measured, closed |
| 4 | Decode single stream | ~79 tok/s; the ceiling is the serial layer split, not a kernel | closed by the record |
| 5 | CPU share (AVX-2) | the engine's own policy; prefill is 93.6% in `libcuda.so`, so the CPU is not the limit | closed by the record |

Ruled out with a measurement, again: NVLink (47.05 GB/s, 0.46% of the window), clock locking, `-lgc`,
`CUDA_DEVICE_MAX_CONNECTIONS=1`, the driver.

## Goals

| # | Goal | Status |
|---|---|---|
| G1 | Know what the default path does, repeatably | done: the A-B-A harness and the baseline above |
| G2 | Take the bitwise Turing kernels if they hold up | **done**: +7% prefill, adopted, and the same 512 tokens as `=0` in the upstream configuration (W1.1) |
| G3 | Overlap the long-context KV upload | **closed as failed**: +0.6%, dropped |
| G4 | Make the PLE read a measured choice | **done**: it is a measured choice and the answer is "leave it" (W2.2) |
| G5 | Change code only where the numbers ask | **open again on one leg**: the PLE row read was measured out (W2.2) and the tensor-parallel patch is closed on its own terms, but the `sm_75` prompt experts were closed on a comment the arithmetic refutes (W3.1, reopened 2026-10-23). Nothing has been changed since |

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

**P3 — PLE.** Measured, closed. The per-request cost is 0–62 ms at 6–24K tokens and about a second on the first
request after an engine start ([SERVER_PLE.md](SERVER_PLE.md)); no change is worth making. The shape that is left —
a small prompt reading at two-thirds the rate of a large one — is not PLE's to take. Whether W3's lever reaches it is
open again as of 2026-10-23 (see *Not tried, and why*); the one lever measured for it is the parked `--prefill 4096`,
waiting on the traffic mix.

**P4 — code.** Nothing has been changed, and one candidate is queued. W2 showed nothing worth removing. W3's lever
was believed not to reach this pack; on 2026-10-23 that belief was shown to rest on a comment, and the queued work is
the three-line opt-in described under *Not tried, and why* — unmeasured, and the test that would measure it is already
in the tree. W4 is closed — the upstream report is written
([UPSTREAM_REPORT_SM75.md](UPSTREAM_REPORT_SM75.md)) and not posted, for lack of a network route, and the
tensor-parallel patch is closed rather than ported — and W5.2 is done on the soak sample.

**P5 — the record.** The three unmeasured items in [SERVER_HW.md](SERVER_HW.md) are closed on 2026-10-23 except the
full hour: 127K measured on and off, bitwise in the upstream configuration, soak 47 of 60 minutes. Folding the open
items of the server's own 24 documents into one place was not done; `/opt/ai/docs/README.md` is still their index.

## Open questions

1. ~~What does the `PLE` field in `strata prefill timing` measure?~~ Answered in [SERVER_PLE.md](SERVER_PLE.md): a
   running total since the engine started, not a per-request figure.
2. Is 22 GB reported as 22 GB? `nvidia-smi` says 22528 MiB; the engine prints 21.49 GiB free and sizes the cache
   from it.
3. Why did the KV overlap gain nothing here when it gained 5% on two 3080s? Layer split, resident cells, or S1.
4. ~~Does `STRATA_SM75_PERF=1` hold bitwise under the T2 configuration?~~ Answered on 2026-10-23 (W1.1): in the
   upstream configuration the two arms gave the same 512 greedy tokens in 9 of 10 runs; one first run after a restart
   differed once and did not come back. The correctness is no longer borrowed.
5. What is the traffic mix, small prompts against large? `--prefill 4096` wins +8% at 6.5K and loses 6% at 22.7K, so
   the choice is the mix and not the code, and nothing on the box records it.
