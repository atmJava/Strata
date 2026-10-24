# Server hardware: this fork

> **状态：背景资料，部分条目已过期**（台账 L-003 记了五处与线上矛盾的说法）。机器与生产的现状以
> [`HANDOVER.md`](HANDOVER.md) §4/§6 为准。

This is `atmJava/Strata`, a fork of [Niko1221/Strata](https://github.com/Niko1221/Strata). `main` is a
fast-forward of upstream and currently sits at `7ddc17a3` (0.2.3.1); the fork was taken at `011c9e60` (0.2.2,
engine 0.1.47) and has been moved since. It exists for one machine: a two-card server that runs Strata as a
service. The reason for forking has narrowed with upstream: the 0.1.48 `linux-x86_64/cuda/cuda13` package lists
`sm_75` among its archs ([packaging/UPDATES.md](../packaging/UPDATES.md)), so "upstream builds only for newer
cards" is no longer the whole story. What the fork carries now is the machine-specific patches, and the record of
what has been measured on this box.

The reason for a fork rather than a tarball and a patch file: the changes made on that machine used to live as
hand-applied patches in `/opt/ai/src/*`, so every release meant re-applying them by hand and hoping the context
still matched. Here each change is a commit on its own branch, rebased onto the next upstream release, and the
difference against upstream is a `git log` away.

## The machine

Facts below were checked on the machine, most on 2026-10-13 (NVLink and driver) and 2026-10-22 (build and service).

| | |
|---|---|
| Host | `ubuntu-server`, `atm@192.168.3.14`, reached over SSH, no display, Wi-Fi only (no wired port). The box moved LAN address: older notes here and on the machine say `192.168.2.223`, which no longer answers |
| Cards | 2 × RTX 2080 Ti **22 GB** (TU102, `sm_75`); 11 GB is the stock size of this card. Both take a layer split |
| Link between the cards | NVLink `NV2`: two links per card, 25.781 GB/s each. P2P supported and passing, no replay. Measured 47.05 GB/s against a 51.56 GB/s ceiling (91%) |
| CPU | Xeon E5-2680 v4, 14 cores / 28 threads, one NUMA node |
| Memory | 62 GiB |
| Operating system and driver | kernel `7.0.0-38-generic`, driver `595.71.05` (pinned; see below) |
| CUDA | 13.0, `/usr/local/cuda-13.0` |
| Service | `strata.service` → `run-022.sh` → `serve/server.py`, port 8080, `Restart=on-failure`, starts at boot. Its `env` sets `STRATA_SM75_PERF=1`, `STRATA_PIPED_CONCURRENT=1` and eleven more switches (`/proc/<pid>/environ` is the truth, not the config file). A restart takes **~165 s** before `/health` answers `loaded: true`, and restarting it from an SSH session needs `sudo -S` |
| Source | `/opt/ai/src/strata-0.2.2`, ggml from the pinned `3cf03257` |

Two things about this machine decide most of what follows:

- **The 280 W power limit on each card is the real ceiling.** With both cards at 98–100% during prefill the clocks
  sit at 1425–1770 MHz. The driver and the link are not the limit.
- **The driver is pinned on purpose.** 595.71.05 is no longer in any repository, so it was rebuilt into a local
  `file://` repository with a restore script. The one alternative in `apt`, 595.91.07, is the version that produced
  Xid 62/154 on 2026-09-25. Do not upgrade it while chasing a speed number.

## Branches

| Branch | What it holds |
|---|---|
| `main` | Upstream, untouched, at `7ddc17a3` (0.2.3.1). Fast-forward only |
| `hw/server-adapt` | This document, and the build settings that apply to this box |
| `hw/server-adapt-0.2.3` | The same work rebased onto 0.2.3.1, plus the requirements page, the recovery runbook, the evidence ledger, the upstream comment drafts and the measurement harness (`tools/`). Pushed as the fork's working branch; `main` is untouched |
| `hw/s1-concurrent-read` | One commit: a concurrent read no longer turns itself off beside decoding slots when the source is the io-prefetch/mapped-experts shape this machine uses |
| `hw/tp-solo` *(not created)* | The solo-window tensor-parallel patch (about +3% decode, +150 MiB per card). Parked: written against 0.2.0-beta, it needs a port to 0.2.2's `verify.cpp` / `generate.cpp` and a bit-by-bit equivalence check first |

Each branch is one change, based on `main`, so it can be rebased on its own when a new release lands.

## Why `sm_75` is most of the work

Read in the engine, not measured (unconfirmed static analysis):

- `CMakeLists.txt` defaults `CMAKE_CUDA_ARCHITECTURES` to `120`. A build for these cards has to pass `75` by hand.
- Features gated at `sm_90+` (thread-block clusters, PDL) are compiled out here. That is expected, and it means
  upstream's numbers for those features say nothing about this box.
- Upstream's 0.2.2 speed work (Gyro, GPU roles, NUMA pool) mostly does not reach `sm_75`, and this machine has one
  NUMA node so the NUMA pool is off by design. But **the `sm_75` kernel series is the counterexample to "upstream's
  speed work cannot reach this card"**: `STRATA_SM75_PERF=1` measures +7.2 to +7.6% on a cold 84K prefill here
  (2,375.6 / 2,381.3 → 2,552.0 / 2,566.5 / 2,556.9 tok/s, plan §7.5) and +4.1 to +7.5% on four concurrent streams,
  and it is set in the service's `env` today. Upstream is being asked to make it the default on Turing in
  [#2197](https://github.com/Niko1221/Strata/issues/2197), where six other `sm_75` boxes have posted their numbers.
- 22 GB is not a stock size for this card. Whatever the engine decides from the reported memory is deciding it from
  a number the card was not sold with.
- This box is **not** on upstream's wanted-hardware list ([TEST_REQUESTS.md](TEST_REQUESTS.md)): that list asks for
  Pascal `sm_61`, unequal NVIDIA pairs and three or four cards. A two-card Turing report is still a report, but it
  does not answer one of their questions as it stands.

## Building on it

Verified on this machine on 2026-10-22: configure 10 s, `cmake --build build -j20` about 5 minutes, `BUILD_EXIT=0`,
`build/strata --version` = `strata 0.1.47`. Only CUDA/`sm_75` was built — no HIP, SYCL or Metal — and the engine's
own tests were off.

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=75 \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.0/bin/nvcc \
  -DSTRATA_ENABLE_CUDA=ON -DSTRATA_NATIVE_EXPERTS=ON -DSTRATA_MMQ_KQUANTS=ON \
  -DSTRATA_Q6K_EXPERTS=ON -DSTRATA_MMQ_TQ_OVERLAY=ON -DSTRATA_BUILD_TESTS=OFF \
  -DSTRATA_BUILD_CONVERSATION_TESTS=OFF -DSTRATA_GGML_DIR=/opt/ai/src/llama.cpp-3cf03257
cmake --build build -j20
```

A change to a file that also compiles as HIP or SYCL cannot be validated here: this box has one backend.

## What has been measured on it

All on this machine, engine 0.1.47 unless noted. Single-stream numbers move 76.7–81.6 tok/s between restarts
(about ±2%), so compare readings from one boot, not across restarts.

| Thing | Number | When |
|---|---|---|
| Single stream, decode | 79.1 tok/s (`parallel 4`, `--batch-groups 2`) | 2026-10-22 |
| Four sessions at once | 103–108 tok/s total, 28–31 each | 2026-10-22 |
| NVLink, peer copy and SM-side read | 47.05 and 45.74 GB/s | 2026-10-13 |
| 84K cold read, concurrent read on | 42.2 s, 21–22 tok/s decoding beside it, longest stall 0.8 s | 2026-10-22 |
| 84K cold read, concurrent read off | 35.7 s, 0.3 tok/s beside it, longest stall 35.5 s | 2026-10-22 |
| 127K cold read, concurrent read on / off | 58.2 / 58.5 s with the neighbour at 19.7 / 20.3 tok/s and a 0.9 s longest gap, against 49.0 / 49.6 s at 0.3 / 0.4 tok/s and a 48.7 / 49.3 s gap: the read costs 19% more, the neighbour keeps working | 2026-10-23, plan §7.9 |
| `STRATA_SM75_PERF` on vs off | cold 84K prefill +7.2 to +7.6%, cold 43–50K +6.0 to +7.8%, four streams +4.1 to +7.5%; single-stream decode inside the run-to-run band, so not claimed | 2026-10-23, plan §7.5 |
| `STRATA_SM75_PERF` bitwise gate | same 512-token answer (`aac0a04d109391fe`) on 9 of 10 runs in the config upstream asks for; the one exception was the first request after one restart and did not come back on the next restart | 2026-10-23, plan §7.6 |
| `--vram-reserve-mib 960 → 1280` | GPU0 free at load 504 → 804–810 MiB (the engine's own warning line sits at 512), expert cache 7,334 → 7,190 slots (−1.96%), and the expert-cache hit rate did not move: median 98.90% before the restart, 99.00% after (p10 97.80 → 97.50) over 88 and 470 completed requests | 2026-10-23/24, ledger L-001 |
| Thinking knobs, agent-shaped tasks | `reasoning_effort` is a prompt sentence, not an engine cap. `medium` costs nothing and saves 7% of completion tokens on reasoning tasks (28% on lookup-shaped ones). `none`/`off` saves 41% of tokens and 25% of wall clock but fails 3 to 4 of 14 reasoning tasks every rep - wrong arithmetic and one empty answer. A thinking budget saves ≤10%. | 2026-10-24, ledger L-009 / L-010 |
| `STRATA_EXP_MODE` 4 → 8 (now the production setting) | single-stream decode 67.2 / 67.7 (mode 4, same-boot bracket) against **73.5** (mode 8): **+8.5%**, with the bracket itself only 0.7% apart. Four-stream aggregate is not a result (its own bracket is 5.9% wide); cold prefill did not move. 24 of 24 bitwise runs returned the identical answer; the agent reasoning harness passed 14/14 in the median in both arms with tokens 2,230 against 2,178 (+2.4%, inside the noise line) and 14 tasks in 42.3 s against 44.5 s (−4.9%). | 2026-10-24, ledger L-011 |
| Prefill, cold 13.2K prompt | 1470 → 1552 tok/s with `--prefill-pipe 1` | 2026-10-22 |

One thing every number above sits inside: **in this box's production shape (4 slots, `--pcie-frac 0.31`,
adaptive) the output is not reproducible.** Two runs of the same self-scoring suite in the same boot, same config,
differ by 24.5% in per-task completion tokens and by 28% in total; pinning `temperature: 0` does not fix it (six
runs, six different answer hashes — ledger L-004). So a difference under ~3% is never a result here, and bitwise
claims can only be made in the single-slot gate config.

## Not a lever (ruled out, with a measurement)

Clock locking (`-lgc`) is monotonically slower below 1890 MHz (−4.8% at 1700, −8.8% at 1600, no change above).
`CUDA_DEVICE_MAX_CONNECTIONS=1` is +0.17%, inside the noise. A driver change is refused for the reason above.
Cross-card staging is 0.46% of the window, so the link is not the limit. All three were tested as A-B-A or
interleaved, not as one run each.

## Closed since this page was written

1. **A 127K cold read on the final build** — done, both arms, in plan §7.9 (the row is in the table above).
2. **The 512-token bitwise check in the configuration upstream requires** — done, plan §7.6: `STRATA_IQ_MT_MIN=1`,
   `--pcie-frac 0`, `--adapt-every 1000000`, `--no-prefill-borrow`, `parallel 1`. One practical trap: `--spec`
   cannot be turned off on a native pack (it wants `--spec T`, T ≥ 2), so what is gated is a spec window.
   The tool is now `tools/server/parity_window.py`; the box's copy used to live at `/tmp/parity.py`, `/tmp` was
   cleared, and the arm script kept reporting a completed run with the correctness gate missing.
3. **A soak** — 47 minutes on the production build with the concurrent-read gate open, no corruption seen; it was
   cut short by a restart from outside this work, so it is not a one-hour soak.

## Not tested

1. Multi-stream cold-start stalls: 5–10 s were seen on an older build with 3–4 joins, and did not come back on a
   warm machine. Cause unknown, not reproduced on the final build.
2. Eight slots, long-context (≥128K) concurrency, vision under load.
3. The engine's own test suite (it was switched off for the release build).
4. Long-context behaviour of `STRATA_EXP_MODE`. The mode-8 test itself is done (ledger L-011: +8.5%
   single-stream decode inside a same-boot bracket, 24 of 24 identical answer hashes, agent reasoning
   unchanged in the median - it is the production setting now). Not measured: mode 8 at 127K context, at eight
   slots, and against anything as heavy as the peers' 4,995-position teacher-forced comparison.
5. Tool calls under load have been measured for correctness only: a client-side tool loop comes back 6/6 and 8/8
   in every thinking-budget arm (ledger L-005), which says the budget is not a lever there, not that the tool path
   is fast.

## What comes next

1. Watch the change that is now live. Mode 8 is the production setting (ledger L-011), so the next reading is
   passive: the four-stream aggregate and any rise in empty or truncated answers in the service log, before
   spending another restart window on anything.
2. Rebuild from `hw/server-adapt` and confirm it matches the binary that is serving now.
3. Post the two upstream comment drafts in `docs/UPSTREAM_DRAFTS.md` (the #2275 one carries the 84K and 127K
   concurrent-read numbers and the instrumented answer to the recycled-buffer objection). They are written and
   unposted; posting is the owner's call.
4. Only then touch placement, if the numbers ask for it: the split planner and the RAM tier on 22 GB cards.
5. Port the tensor-parallel patch to current main on its own branch, or drop it and record why.

## Rules

- Upstream's rules apply here too: [CONTRIBUTING.md](../CONTRIBUTING.md) and [AGENTS.md](../AGENTS.md). The default
  path stays byte-identical to the release; anything new is opt-in and can be turned off.
- No claim without a measurement, and say what it was measured on. A reading taken from the code is marked as
  unconfirmed static analysis, as above.
- One branch, one change. A change that helps only this box stays in this fork; a change that helps any box with
  these cards is worth offering upstream.
