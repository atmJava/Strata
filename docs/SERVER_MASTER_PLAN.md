# Server work: the global plan

One document that says what is being done to this box, in what order, and when a thing is finished. It is updated
after every round; the status table at the bottom is the only status there is.

Scope: make Strata faster and more trustworthy on the machine in [SERVER_HW.md](SERVER_HW.md) — 2 × RTX 2080 Ti
22 GB (`sm_75`), one layer split, CUDA 13.0, a four-slot server. Not a new feature; a tuning and correctness
programme with a measurement behind every claim.

The detailed rules, acceptance and test ladder are in [SERVER_TUNING_PLAN.md](SERVER_TUNING_PLAN.md). This file
does not repeat them; it decides **what happens next**.

## Definition of done

The programme is finished when all of these are true:

1. Every workstream below is either **done with evidence** or **closed with a recorded reason**.
2. The three unmeasured items this plan took on are run: the 127K read, the bitwise check under the upstream
   configuration, and the soak. What else `SERVER_HW.md` lists as not tested — multi-stream stalls, 8 slots and
   vision under load, the engine's own test suite — was never in this plan's scope and stays open there.
3. The run config on the server and the documents agree: every `env` key and every non-default flag has a line
   saying why it is there and what it bought.
4. The numbers in `SERVER_HW.md` and `SERVER_TUNING_PLAN.md` are from this box and name the boot they came from.
5. Anything that would help another `sm_75` machine is written up so it can be offered upstream, and anything that
   only helps this box is marked as such.

## Definition of done: the check

Written on 2026-10-23, when every task had reached a state. Row 1 was true that morning; W3 was reopened the same
afternoon, so it is not any more.

| # | Item | State |
|---|---|---|
| 1 | Every workstream done or closed with a reason | **no, as of 2026-10-23**: 22 tasks, 12 done, 3 closed, 7 open. W3.1 and W3.2 were closed that morning and reopened that afternoon on a reason that turned out to be a comment, and the same reading added W3.0 and W6.1–W6.4 |
| 2 | The three items this plan took on are run: the 127K read, the bitwise check, the soak | **yes, with one caveat**: 127K measured on and off (W1.2), bitwise in the upstream configuration (W1.1), soak 47 of 60 minutes (W1.4, cut by an external restart that is now fixed). The rest of `SERVER_HW.md`'s list is out of scope by item 2 and stays open there |
| 3 | The run config and the documents agree | **yes**: 14 `env` keys, each explained in §7 of the server record; `--prefill auto`; no diagnostic switch left in |
| 4 | Every number names the box and the boot | **yes**: every row of the measured tables carries 2026-10-22 or 2026-10-23, and the A-B-A rows name the arm |
| 5 | Upstream text for anything that helps other `sm_75` machines | **yes**: [UPSTREAM_REPORT_SM75.md](UPSTREAM_REPORT_SM75.md), unposted for lack of a network route |

What the plan did not produce, and why: no performance code change. PLE was measured out (0–62 ms a request), the
tensor-parallel patch needs a mode this box does not serve, and the one configuration lever found (a 4096-token chunk)
wins 8% on small prompts and loses 6% on large ones with no traffic data to choose. The one change that shipped is a
configuration line (`STRATA_SM75_PERF=1`, +7% prefill), and the one operational defect found (`needrestart`
restarting the service nightly) is fixed.

W3 is the exception, and it is open: it was closed on 2026-10-23 morning on a comment in
`src/prefill/moe_fused_iq.cu:1302-1304` — that the codebook formats do not fit 64 KiB at four warps a row — helped
along by the header comment at `include/strata/prefill/moe_fused.hpp:2,31`, which says the fused prompt experts are
for `sm_80` and newer. Neither is a measurement. The file's own formula puts this box's format at 60,672 bytes,
inside the limit, and the branch that carries the comment returns five lines before the code that would have asked
the driver. The numbers and what would settle it are in
[SERVER_TUNING_PLAN.md](SERVER_TUNING_PLAN.md#not-tried-and-why). The rule this leaves behind: **a closure reason has
to name the run that produced it**, and where a comment is the only evidence, the check is usually a few lines away.

## Workstreams

Each task is one round of work. A task is "done" when its evidence exists and the documents say so; "closed" when
the answer is no and the reason is written down.

### W0 — Record and sync

| Task | What | Done looks like |
|---|---|---|
| W0.1 | Commit the documents (`SERVER_HW.md`, `SERVER_TUNING_PLAN.md`, this file) on `hw/server-adapt` | `git log` shows them, working tree clean |
| W0.2 | Keep `/opt/ai/docs/strata-tuning-plan-0.2.2-20261022.md` §7 in step with every config change | the server's own record names the current `env` |
| W0.3 | One short record per round in `/opt/ai/docs` (or a section added to §7) | the next person can read what changed and why |

### W1 — Correctness baseline

The adopted Turing series claims to be bitwise and this box has not checked it. Until W1.1 passes, the speed is
measured and the correctness is borrowed.

| Task | What | Done looks like |
|---|---|---|
| W1.1 | T2: the 512-token bitwise check, `STRATA_SM75_PERF=0` against `=1`, under the upstream configuration (`STRATA_IQ_MT_MIN=1`, `--pcie-frac 0`, a fixed adaptive, `--no-prefill-borrow`) | same token ids on both arms, or a written account of where they differ |
| W1.2 | The 127K cold read on the final build | one number, with the concurrent read on and off |
| W1.3 | T7: cancellation in the middle of a long read | request ends, nothing in flight, engine keeps serving |
| W1.4 | T8: one-hour soak | flat VRAM and RAM, no crash, no drift past the noise |

### W2 — PLE, the largest remaining item

A new agent session pays this on its first prompt: the record measured 6 of the 7 seconds of a new 8.2K prompt in
the host PLE bucket, and the engine still prints `--ple-io direct measures slow here`. Three candidates from the
record: the table in RAM (rejected, no RAM), a smaller read unit (the path reads 90-byte rows as 8 KiB), and issuing
the read earlier.

| Task | What | Done looks like |
|---|---|---|
| W2.1 | Read the source: what the `PLE` field times, where rows are read, whether the 8 KiB claim holds in 0.2.2 | a written note with file and line references |
| W2.2 | Decide between the candidates, with a predicted effect and the measurement that would confirm it | one choice, and the alternative recorded as not chosen |
| W2.3 | Implement on its own branch, in the file's style, with the resource and error rules from the tuning plan | a branch that builds, with the change explained in its comments |
| W2.4 | Accept or reject it on the ladder (T5 at 4K/32K/128K, T6, T2 if it touches numerics) | kept with numbers, or reverted with the reason |

### W3 — The `sm_75` prompt experts

`STRATA_PF_FUSED_SM75=1` is the int8 tensor-core prompt path written for this card, and it changes answers. It was
closed on 2026-10-23 morning as not applicable to this pack and reopened the same afternoon: the reason recorded for
it was a comment, and the file's own arithmetic puts this pack inside the limit it claimed to exceed. The reading, the
byte counts and what is still unknown are in [SERVER_TUNING_PLAN.md](SERVER_TUNING_PLAN.md#not-tried-and-why). W6.1
comes first, because whether MMQ even has a tile for this pack here decides what a fused win would be measured
against.

| Task | What | Done looks like |
|---|---|---|
| W3.0 | The three lines in `src/prefill/moe_fused_iq.cu` (`:1304` and `:1425`) behind `STRATA_PF_FUSED_SM75=1`, then `prefill_fused_iq_test` on this card | the test runs the `IQ3_XXS` pair at all: either it prints the parity and timing for it, or `setup_one` refuses it and that is the answer, written down |
| W3.1 | The KL gate: teacher-forced KL and top-1 against the default, on the box's model and prompts | numbers against the default's own run-to-run floor |
| W3.2 | The speed reading at 4K/32K/128K prefill, whole engine, with the ring and the loan re-checked (the buffers change) | kept or dropped with the numbers |

### W4 — What belongs upstream

| Task | What | Done looks like |
|---|---|---|
| W4.1 | A `sm_75` default for the Turing series, or a report that asks for one | an issue or PR text with the A-B-A numbers and the T2 result |
| W4.2 | The TP patch: port it or close it | a branch with T2 behind it, or a recorded decision not to |
| W4.3 | The prefill `Xid 13` crash of 2026-11-02: report it or close it | an issue text with the kernel log, the cgroup evidence and what would confirm the cause |

### W5 — The instrument

| Task | What | Done looks like |
|---|---|---|
| W5.1 | The arm harness wrapped as one command with a results file per arm | what P0 produced; keep it working |
| W5.2 | A memory-stability check after the config change (evictions, parked, `MemAvailable` low water) | one reading after a day of real traffic |

### W6 — What the reading round left open

Written 2026-10-23 from reading the source, with no run behind any of it. Each task is one thing the reading could
not settle from this machine, and each is cheap. The list they come from is
[SERVER_TUNING_PLAN.md](SERVER_TUNING_PLAN.md#which-switches-reach-this-box).

| Task | What | Done looks like |
|---|---|---|
| W6.1 | Read what the engine already prints and what the driver already knows: the `prompt kernels: … no tile …` line (`src/prefill/moe_mmq.cu:312-314`) and both cards' `MaxSharedMemoryPerBlockOptin` and `ReservedSharedMemoryPerBlock` | two values and a yes-or-no on which path serves the prompt experts, written into `SERVER_HW.md` |
| W6.2 | `STRATA_PREFILL_MMQ=0`, A-B-A at 32K and 128K | does MMQ beat the FP16 expert path here at all, with medians of three |
| W6.3 | The drop-one sweep of the switches that reach this box and were never measured here: the nine, plus `EXPDEC`, `ATTN_LANECELL` and `ARGMAX_ROWS_SM70` | one table, one arm per row, T2 behind the bitwise ones, anything under 3% written as no |
| W6.4 | What the run config actually holds against what the pipelined path needs: `STRATA_PIPED_CONCURRENT`, `STRATA_PIPED_READ_PIECE`, and what a control line does to the two cards (`include/strata/program/batch_read.hpp:24-45`, `src/program/generate.cpp:11784-11813`) | the two keys recorded as set or unset, and one measured reading of a prompt read beside four decoding slots |

## Order and dependencies

```
W0.1 ──┬─> W1.1 ──> W4.1
       ├─> W2.1 ──> W2.2 ──> W2.3 ──> W2.4 ──> W4.2
       ├─> W1.2 ──> W1.3 ──> W1.4
       └─> W6.1 ──┬─> W6.2
                  ├─> W6.3
                  ├─> W6.4
                  └─> W3.0 ──> W3.1 ──> W3.2
```

Rules of order:

- W1.1 comes before W4.1: a default cannot be proposed on an unverified claim.
- W2.1 (reading) comes before any PLE code. The tuning plan's rule 2 applies twice here: the read path has an I/O
  thread, a staging pool and an error path, and none of them may be guessed at.
- W1.2–W1.4 are cheap and close risk; they are interleaved with W2, not postponed to the end.
- W6.1 comes first among the open tasks. It costs one boot and one query, and every number in W3, W6.2 and W6.3
  means something different depending on what it says.
- W3.0 comes before W3.1: a KL gate run against a path that may not launch at all is a wasted round.
- A round runs one task. If a task turns out to be two, it is split in this table first.

## Iteration protocol

1. Pick the first unblocked task in the table, top to bottom.
2. Do it. Record the evidence (command, numbers, log path, boot time) in the round's record.
3. Update this table and the owning document. If the answer is no, write the reason.
4. Commit on `hw/server-adapt` with the area prefix, one change per commit.
5. If a task is blocked, write what blocks it and move to the next unblocked one. A blocker that persists across
   three rounds escalates to the human instead of being ground against.

Measurement rules are the tuning plan's, and they are not negotiable: same boot where possible, A-B-A where a
restart is needed, medians of three, nothing under 3% counts, and no claim without a boot named.

## Risk register

| Risk | Why it matters | Mitigation |
|---|---|---|
| A restart in the middle of real traffic | users are disconnected; the record notes this | synthetic work only; say so before a restart |
| VRAM is at 689 / 1039 MiB free at start | a second engine or a big allocation cannot fit | one engine at a time; check free VRAM before any experiment |
| The driver is pinned and fragile | 595.91.07 caused Xid 62/154 | never upgrade while measuring; the local repo has the restore script |
| 62 GiB RAM with 4 × 256K slots | `MemAvailable` has hit 4.1 GiB | watch the low water in every round; do not add slots |
| The record has been contradicted by re-testing before | trust the owned document, then re-test with a reason | read `/opt/ai/docs/README.md` first, every round |
| Servers and repos drift apart | the serving tree has S1 and the docs did not say so | W0.2: the server record is updated with the config |

## Status

Updated at the end of every round. `done`, `closed`, `open`, `blocked — <reason>`.

| Task | State | Evidence |
|---|---|---|
| W0.1 | done | the documents are committed on `hw/server-adapt` in the same change that added this file |
| W0.2 | done | §7.6, §7.7 and §7.8 of the server plan record the 2026-10-23 config change, the bitwise check and the PLE measurement |
| W0.3 | done | one section appended per round: §7.6 (T2), §7.7 (PLE field), §7.8 (PLE measured) |
| W1.1 | done | 2026-10-23: in the upstream configuration (`STRATA_IQ_MT_MIN=1`, `--pcie-frac 0`, `--adapt-every 1000000`, `--no-prefill-borrow`, one slot) both arms are stable and gave the same 512 greedy tokens (`aac0a04d…`), 4 warm runs + 3 after a second restart. One first run after a restart differed once (`00281f84…`), not reproduced |
| W1.2 | done | 2026-10-23 on the final build (126,050-token cold prompt): concurrent read on 58.2/58.5 s with the stream at 19.7–20.3 tok/s and a 0.9 s longest stall; off 49.0/49.6 s with the stream at 0.3–0.4 tok/s and a 48.7 s stall |
| W1.3 | done | 2026-10-23, three reps: a client leaves 10.2–10.3 s into a 55,035-token read; the engine stops at 39,680 tokens (16.5–16.7 s), logs `(cancelled)`, keeps serving (the next request completes, health 200). The next request waits 6.6–6.7 s behind the chunk in flight. No `keeps its conversation` line per cancel |
| W1.4 | done | 47 minutes (06:02–06:49 UTC): 3,995 requests, 0 errors, RSS +148 MB, MemAvailable 23.3–23.6 GiB, 0 cache evictions, GPU0 61→82 °C. Cut by `apt-daily-upgrade` restarting the service through `needrestart` (now exempted); the full hour is still worth re-running, and the soak script should back off when the server is down |
| W2.1 | done | [SERVER_PLE.md](SERVER_PLE.md): the `PLE` field is a running total (nothing in the serving path calls `Prefill::reset()`), reads are one 4 KiB page per 90-byte row (8 KiB on a straddle), the gather is one batched request per chunk on an aux-CPU thread |
| W2.2 | done | measured on 2026-10-23: PLE costs 0–62 ms per request at 6–24K tokens of real text, plus ~1 s on the first request after an engine start. No candidate is worth implementing — see [SERVER_PLE.md](SERVER_PLE.md#measured-2026-10-23-ple-is-not-the-bottleneck-any-more) |
| W2.3 | closed | the premise did not survive W2.2: there is no per-request PLE cost to remove |
| W2.4 | closed | same; re-opens only if a real load shows a cost that matters |
| W3.0 | open | The three lines have not been changed, built or run here. `tests/cuda/prefill_fused_iq_test.cpp:693-699` skips a pair only while `native_supported()` refuses it, so the existing test covers the `IQ3_XXS` pair the moment they are relaxed — no test change needed. Nothing measured |
| W3.1 | **open — reopened 2026-10-23** | Closed as "not applicable to this model" on `src/prefill/moe_fused_iq.cu:1302-1304` and on `native_supported()`'s Turing refusal (`:1425`). The refusal is real; its stated reason was never probed — the Turing branch returns at `:1307`, five lines above the `setup_one` calls at `:1309-1311` that ask the driver — and the kernel's own formula puts IQ3_XXS gate/up at 60,672 B of shared memory against a 64 KiB limit, 1,024 B more than the `Q2_0` format that *is* set up. The pack is IQ3_XXS gate/up with IQ4_NL down, a pair `gu_covered` and `d_covered` (`:1333,1344`) already accept. **Unconfirmed static analysis: arithmetic on constants read from the file, nothing run on this box.** What would settle it is in [SERVER_TUNING_PLAN.md](SERVER_TUNING_PLAN.md#not-tried-and-why) |
| W3.2 | **open — reopened with W3.1** | It assumed the measured gain needs a Q2_0 pack. The gains on record are gfx12 (IQ3_XXS +15.7%, +19-20% with the attention work) and sm_120; for a native pack the only engine numbers are IQ2_XS on an RTX 5070, +8% at 4K and 0% at 32K (`src/prefill/prefill.cpp:256`). For this pack the one quality measurement went the other way — first-token KL 0.00340 with MMQ against 0.00388 fused over 24 prompts on an R9700, which is why that flag stayed opt-in ([AMD_HIP.md](AMD_HIP.md)). So the gain here is unknown, not known-and-unavailable |
| W4.1 | done | [UPSTREAM_REPORT_SM75.md](UPSTREAM_REPORT_SM75.md): ready-to-post issue with the A-B-A table, the T2 exactness result and what was not tested. Not posted: no network route to GitHub from this machine |
| W4.2 | closed | not ported: `STRATA_TP_GDN` needs `--batch 0` (single stream), while this box was moved to `parallel 4` on measurement; the gain measured in the record is single-stream (+3.8% projection, +7.5% weighted), and the patch leaves ~293 MiB free on CUDA1. Footprint and re-open condition in [SERVER_TUNING_PLAN.md](SERVER_TUNING_PLAN.md#the-tensor-parallel-patch-closed-not-ported) |
| W4.3 | done | [UPSTREAM_REPORT_XID13.md](UPSTREAM_REPORT_XID13.md): ready-to-post crash report — 202 `Xid 13` and one `Xid 43` in one second on CUDA0, `prefill copy_i32` as the reporting site, no OOM and no throttle, the first occurrence in four boots. Not posted: the text waits for the issue form |
| W5.1 | done | `/opt/ai/bin/run-arm.sh`, `apply-arm.sh`, `cold-prefill.py` |
| W5.2 | done | across the soak and the cache counters: `evictions 0` over 1,120 parks / 1,119 restores, conversation-cache bytes flat at 589.8 MB, MemAvailable low water 23.3 GiB (historically 4.1 GiB), RSS +148 MB over 39 minutes. A day of real traffic is not available; this is the best sample there is |
| W6.1 | open | costs one start-up log read and one device query, and neither has been done: no log on the box has been read for the tile line, and the two shared-memory attributes are not recorded in [SERVER_HW.md](SERVER_HW.md) |
| W6.2 | open | not run |
| W6.3 | open | not run. The arms, what each one selects and what another card said about it are tabulated in [SERVER_TUNING_PLAN.md](SERVER_TUNING_PLAN.md#which-switches-reach-this-box) |
| W6.4 | open | the 14 `env` keys are recorded in §7 of the server record; whether `STRATA_PIPED_CONCURRENT` and `STRATA_PIPED_READ_PIECE` are among them has not been checked from this machine, and the drain behaviour has not been measured |

W3.0 and W6.1–W6.4 come from the same reading round as the W3 reopen, and nothing in W3 or W6 has been run on this
machine. What is established is that the reason W3 was closed was not a measurement — not that the switch wins. Every
byte count, percentage and "reaches this box" claim in W3 and W6 is unconfirmed static analysis until the machine is
used, and the byte counts come from arithmetic on constants read out of the source.

Already settled before this plan existed, so it is not a task: the Turing series is on (`STRATA_SM75_PERF=1`,
+7.2–7.6% prefill, A-B-A), and `STRATA_KV_PREFETCH` is off (+0.6%, inside the noise).
