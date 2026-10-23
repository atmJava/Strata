# Server hardware: this fork

This is `atmJava/Strata`, a fork of [Niko1221/Strata](https://github.com/Niko1221/Strata) taken at `011c9e60`
(0.2.2, engine 0.1.47). It exists for one machine: a two-card server that runs Strata as a service, where upstream's
desktop release is built for cards much newer than the ones in it.

The reason for a fork rather than a tarball and a patch file: the changes made on that machine used to live as
hand-applied patches in `/opt/ai/src/*`, so every release meant re-applying them by hand and hoping the context
still matched. Here each change is a commit on its own branch, rebased onto the next upstream release, and the
difference against upstream is a `git log` away.

## The machine

Facts below were checked on the machine, most on 2026-10-13 (NVLink and driver) and 2026-10-22 (build and service).

| | |
|---|---|
| Host | `ubuntu-server`, `atm@192.168.2.223`, reached over SSH, no display, Wi-Fi only (no wired port) |
| Cards | 2 × RTX 2080 Ti **22 GB** (TU102, `sm_75`); 11 GB is the stock size of this card. Both take a layer split |
| Link between the cards | NVLink `NV2`: two links per card, 25.781 GB/s each. P2P supported and passing, no replay. Measured 47.05 GB/s against a 51.56 GB/s ceiling (91%) |
| CPU | Xeon E5-2680 v4, 14 cores / 28 threads, one NUMA node |
| Memory | 62 GiB |
| Operating system and driver | kernel `7.0.0-38-generic`, driver `595.71.05` (pinned; see below) |
| CUDA | 13.0, `/usr/local/cuda-13.0` |
| Service | `strata.service` → `run-022.sh` → `serve/server.py`, port 8080, `Restart=on-failure`, starts at boot |
| Run config | `strata-022.json`: `parallel 4`, `--batch-groups 2`, and since 2026-10-23 `STRATA_SM75_PERF=1` in its `env` block |
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
| `main` | Upstream, untouched, at `011c9e60`. Fast-forward only |
| `hw/server-adapt` | This document, and the build settings that apply to this box |
| `hw/s1-concurrent-read` | One commit: a concurrent read no longer turns itself off beside decoding slots when the source is the io-prefetch/mapped-experts shape this machine uses. **This is the source the binary that serves now was built from** |
| `hw/tp-solo` *(not created)* | The solo-window tensor-parallel patch (about +3% decode, +150 MiB per card). Parked: written against 0.2.0-beta, it needs a port to 0.2.2's `verify.cpp` / `generate.cpp` and a bit-by-bit equivalence check first |

Each branch is one change, based on `main`, so it can be rebased on its own when a new release lands.

## The source on the server

`/opt/ai/src/strata-0.2.2` is an unpacked tree, not a git checkout. On 2026-10-23 it was compared with
`hw/server-adapt`, line by line and ignoring line endings. The only differences: the two files of the S1 change
(`include/strata/program/batch_read.hpp`, `src/program/generate.cpp`), whose md5s equal the `hw/s1-concurrent-read`
commit's, and one paragraph in `docs/BATCHING.md` that no branch has. That paragraph names
`STRATA_PIPED_CONCURRENT_ASSUME_COPY_ONLY`, an env switch the final S1 code does not have: it is left over from the
first, gated version of the change. The rest is listed in *Building on it*.

## Why `sm_75` is most of the work

Read in the engine, not measured (unconfirmed static analysis):

- `CMakeLists.txt` defaults `CMAKE_CUDA_ARCHITECTURES` to `120`. A build for these cards has to pass `75` by hand.
- Features gated at `sm_90+` (thread-block clusters, PDL) are compiled out here. That is expected, and it means
  upstream's numbers for those features say nothing about this box.
- Upstream's 0.2.2 speed work (Gyro, GPU roles, NUMA pool) mostly does not reach `sm_75`. This machine has one NUMA
  node, so the NUMA pool is off by design, and 0.2.2 was taken for the correctness and batch-path fixes, not speed.
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

### A rebuild is reproducible, with one caveat

Rebuilt on 2026-10-23 from the source on the box (see *The source on the server*), with the same command, into the
same `build/` path. The new and old `CMakeCache.txt` had the same md5, so the flags, the compiler and the paths were
the same; toolchain was cmake 3.28.3, nvcc 13.0, gcc 13.3, `BUILD_EXIT=0`.

| Check | Result |
|---|---|
| Size, `--version` | 79,076,000 bytes, `strata 0.1.47` — both the same |
| `.text`, `.rodata`, `.data`, `.data.rel.ro` | identical, byte for byte |
| `.nv_fatbin` (62 MB of CUDA) | identical, byte for byte |
| GNU build-id | identical (`09c8ffd3…`) |
| sha256 of the stripped binary | identical (`fb3da34e…`) |
| sha256 of the whole file | **differs**: 323 bytes |

All 323 differing bytes are inside `.strtab`, and every one of them sits in an nvcc temporary file name of the form
`tmpxft_<hex>_00000000-6_<file>.cudafe1.cpp` (130 names). That number is picked per compile, so it changes on every
build. Read the result as: the build is reproducible; compare two builds by build-id or by the stripped hash, not by
the file's sha256.

Build in the same directory to compare at all: ten strings in the binary carry `/opt/ai/src/strata-0.2.2/build`.

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
| 127K cold read, concurrent read on | 58.2 / 58.5 s, 19.7–20.3 tok/s beside it, longest stall 0.9 s, no gap over 2 s | 2026-10-23 |
| 127K cold read, concurrent read off | 49.0 / 49.6 s, 0.3–0.4 tok/s beside it, longest stall 48.7 s, 5–6% of gaps over 2 s | 2026-10-23 |
| Prefill, cold 13.2K prompt | 1470 → 1552 tok/s with `--prefill-pipe 1` | 2026-10-22 |
| Rebuild of the serving source | same build-id, same 79,076,000 bytes; 323 bytes of nvcc temp names differ in `.strtab` | 2026-10-23 |
| Turing kernel series on (`STRATA_SM75_PERF=1`), 84K cold prefill | 2375.6 / 2381.3 → 2552.0 / 2566.5 / 2556.9 tok/s (+7.2 to +7.6%) | 2026-10-23 |
| Turing series on, cold prefill 43–50K | 2108 / 2094 → 2236 / 2258 tok/s (+6.0 to +7.8%) | 2026-10-23 |
| Turing series on, four sessions | 111.3 / 112.5 → 119.7 / 117.1 tok/s (+4.1 to +7.5%) | 2026-10-23 |
| Turing series on, decode after 84K | 67.8–78.0 tok/s against 67.8–71.2: up, but inside the spread, not claimed | 2026-10-23 |
| `STRATA_KV_PREFETCH=1` on top of the above | +0.6% prefill, +1.0% cold read, −2.2% four sessions: no gain, removed | 2026-10-23 |
| Bitwise check, Turing series on against off, in the configuration upstream requires | the same 512 tokens in 9 of 10 runs; one first-run after a restart differed and did not come back | 2026-10-23 |
| Soak (47 minutes, cut by an external service restart) | 3,995 requests, 0 errors; RSS +148 MB; MemAvailable 23.3–23.6 GiB; 0 cache evictions; GPU0 61 → 82 °C, GPU1 53 → 65 °C | 2026-10-23 |
| `--prefill 4096` against `auto` (8192) | 6.5K-token read 5.24/5.35 s against 5.61/5.86 s (+8%); 22.7K read 1,830 against ~1,950 tok/s (−6%): kept `auto` | 2026-10-23 |

## Not a lever (ruled out, with a measurement)

Clock locking (`-lgc`) is monotonically slower below 1890 MHz (−4.8% at 1700, −8.8% at 1600, no change above).
`CUDA_DEVICE_MAX_CONNECTIONS=1` is +0.17%, inside the noise. A driver change is refused for the reason above.
Cross-card staging is 0.46% of the window, so the link is not the limit. All three were tested as A-B-A or
interleaved, not as one run each.

## Not tested

1. A full one-hour soak: 47 minutes were clean on 2026-10-23 before `apt-daily-upgrade` restarted the service
   (`needrestart`; now exempted in `/etc/needrestart/conf.d/50-strata.conf`). The next run can go the full hour.
2. Multi-stream cold-start stalls: 5–10 s were seen on an older build with 3–4 joins, and did not come back on a
   warm machine. Cause unknown, not reproduced on the final build.
3. Eight slots, long-context (≥128K) concurrency, vision under load, tool calls under load.
4. The engine's own test suite (it was switched off for the release build).
5. Whether llama.cpp's MMQ has a tile for this pack's IQ3_XXS experts on `sm_75`. The check runs against the pinned
   llama.cpp tree, so only the engine's own start-up log answers it — the engine prints one line per type when a type
   has no tile — and no log on this box has been read for that line. Until it is read, which path serves the prompt
   experts here is inferred from the build, not observed.
6. What these cards report for `MaxSharedMemoryPerBlockOptin` and `ReservedSharedMemoryPerBlock`. Several kernels
   choose their tile size against those two numbers, and the values are not recorded here.
7. The fused int8 prompt experts on this pack (`STRATA_PF_FUSED=1` with `STRATA_PF_FUSED_SM75=1`, after three lines
   in `src/prefill/moe_fused_iq.cu`): never built for this card and never run. Why it is worth a round, and what
   would settle it, is in [SERVER_TUNING_PLAN.md](SERVER_TUNING_PLAN.md#not-tried-and-why).

## What comes next

The tuning work — bottlenecks, goals, rules, acceptance and the test ladder — is in
[SERVER_TUNING_PLAN.md](SERVER_TUNING_PLAN.md). The order the work is done in, and the running status, is
[SERVER_MASTER_PLAN.md](SERVER_MASTER_PLAN.md). This list is the short version.

1. ~~Rebuild from `hw/server-adapt` and confirm it matches the binary that is serving now.~~ Done on 2026-10-23. The
   binary that serves was **not** built from `hw/server-adapt` but from `hw/s1-concurrent-read`, and rebuilding that
   source reproduced it (see *A rebuild is reproducible, with one caveat*). The next rebuild should start from
   `hw/s1-concurrent-read`.
2. ~~The Turing kernel series is on (`STRATA_SM75_PERF=1`, +7% prefill). Next is the PLE read: 6 of the 7 s of a new
   prompt, and the largest thing left for a server (see the plan).~~ Done, and the premise did not hold. Measured on
   2026-10-23 on real text: PLE costs 0–62 ms a request at 6–24K tokens, plus about 1 s on the first request after an
   engine start ([SERVER_PLE.md](SERVER_PLE.md)). There is nothing left there to take.
3. ~~Close the unmeasured items above that need no new code: the 127K read, the bitwise check under the configuration
   upstream requires, the soak.~~ Done on 2026-10-23, except the last: 127K measured on and off, bitwise in the
   upstream configuration, soak 47 of 60 minutes. The full hour is still to re-run.
4. Only then touch placement, if the numbers ask for it: the split planner and the RAM tier on 22 GB cards. Not
   reached — the plan closed with no code change, and no measurement asked for it.
5. ~~Port the tensor-parallel patch to 0.2.2 on its own branch, or drop it and record why.~~ Dropped, with the reason
   recorded: `STRATA_TP_GDN` needs `--batch 0` (one stream) and this box serves `parallel 4`, which measured +63% on
   four sessions over the serial group. Footprint and re-open condition in
   [SERVER_TUNING_PLAN.md](SERVER_TUNING_PLAN.md#the-tensor-parallel-patch-closed-not-ported).

## Rules

- Upstream's rules apply here too: [CONTRIBUTING.md](../CONTRIBUTING.md) and [AGENTS.md](../AGENTS.md). The default
  path stays byte-identical to the release; anything new is opt-in and can be turned off.
- No claim without a measurement, and say what it was measured on. A reading taken from the code is marked as
  unconfirmed static analysis, as above.
- One branch, one change. A change that helps only this box stays in this fork; a change that helps any box with
  these cards is worth offering upstream.
