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
2. `SERVER_HW.md`'s "Not tested" list is empty: the 127K read, the bitwise check under the upstream configuration,
   and the soak are run.
3. The run config on the server and the documents agree: every `env` key and every non-default flag has a line
   saying why it is there and what it bought.
4. The numbers in `SERVER_HW.md` and `SERVER_TUNING_PLAN.md` are from this box and name the boot they came from.
5. Anything that would help another `sm_75` machine is written up so it can be offered upstream, and anything that
   only helps this box is marked as such.

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

`STRATA_PF_FUSED_SM75=1` is the int8 tensor-core prompt path written for this card, and it changes answers. It is
the second performance candidate after PLE.

| Task | What | Done looks like |
|---|---|---|
| W3.1 | The KL gate: teacher-forced KL and top-1 against the default, on the box's model and prompts | numbers against the default's own run-to-run floor |
| W3.2 | The speed reading at 4K/32K/128K prefill | kept or dropped with the numbers |

### W4 — What belongs upstream

| Task | What | Done looks like |
|---|---|---|
| W4.1 | A `sm_75` default for the Turing series, or a report that asks for one | an issue or PR text with the A-B-A numbers and the T2 result |
| W4.2 | The TP patch: port it or close it | a branch with T2 behind it, or a recorded decision not to |

### W5 — The instrument

| Task | What | Done looks like |
|---|---|---|
| W5.1 | The arm harness wrapped as one command with a results file per arm | what P0 produced; keep it working |
| W5.2 | A memory-stability check after the config change (evictions, parked, `MemAvailable` low water) | one reading after a day of real traffic |

## Order and dependencies

```
W0.1 ──┬─> W1.1 ──> W4.1
       ├─> W2.1 ──> W2.2 ──> W2.3 ──> W2.4 ──> W4.2
       ├─> W1.2 ──> W1.3 ──> W1.4
       └─> W3.1 ──> W3.2
```

Rules of order:

- W1.1 comes before W4.1: a default cannot be proposed on an unverified claim.
- W2.1 (reading) comes before any PLE code. The tuning plan's rule 2 applies twice here: the read path has an I/O
  thread, a staging pool and an error path, and none of them may be guessed at.
- W1.2–W1.4 are cheap and close risk; they are interleaved with W2, not postponed to the end.
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
| W0.2 | open | §7 of the server plan already records the 2026-10-23 change |
| W0.3 | open | — |
| W1.1 | done | 2026-10-23: in the upstream configuration (`STRATA_IQ_MT_MIN=1`, `--pcie-frac 0`, `--adapt-every 1000000`, `--no-prefill-borrow`, one slot) both arms are stable and gave the same 512 greedy tokens (`aac0a04d…`), 4 warm runs + 3 after a second restart. One first run after a restart differed once (`00281f84…`), not reproduced |
| W1.2 | open | prior run was on an older build |
| W1.3 | open | — |
| W1.4 | open | `/tmp/soak.py` produced 0 bytes on the old build |
| W2.1 | done | [SERVER_PLE.md](SERVER_PLE.md): the `PLE` field is a running total (nothing in the serving path calls `Prefill::reset()`), reads are one 4 KiB page per 90-byte row (8 KiB on a straddle), the gather is one batched request per chunk on an aux-CPU thread |
| W2.2 | done | measured on 2026-10-23: PLE costs 0–62 ms per request at 6–24K tokens of real text, plus ~1 s on the first request after an engine start. No candidate is worth implementing — see [SERVER_PLE.md](SERVER_PLE.md#measured-2026-10-23-ple-is-not-the-bottleneck-any-more) |
| W2.3 | closed | the premise did not survive W2.2: there is no per-request PLE cost to remove |
| W2.4 | closed | same; re-opens only if a real load shows a cost that matters |
| W3.1 | open | — |
| W3.2 | open | — |
| W4.1 | open | — |
| W4.2 | open | TP parked since 0.2.0 |
| W5.1 | done | `/opt/ai/bin/run-arm.sh`, `apply-arm.sh`, `cold-prefill.py` |
| W5.2 | open | — |

Already settled before this plan existed, so it is not a task: the Turing series is on (`STRATA_SM75_PERF=1`,
+7.2–7.6% prefill, A-B-A), and `STRATA_KV_PREFETCH` is off (+0.6%, inside the noise).
