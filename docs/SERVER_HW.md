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
| `hw/s1-concurrent-read` | One commit: a concurrent read no longer turns itself off beside decoding slots when the source is the io-prefetch/mapped-experts shape this machine uses |
| `hw/tp-solo` *(not created)* | The solo-window tensor-parallel patch (about +3% decode, +150 MiB per card). Parked: written against 0.2.0-beta, it needs a port to 0.2.2's `verify.cpp` / `generate.cpp` and a bit-by-bit equivalence check first |

Each branch is one change, based on `main`, so it can be rebased on its own when a new release lands.

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
| Prefill, cold 13.2K prompt | 1470 → 1552 tok/s with `--prefill-pipe 1` | 2026-10-22 |

## Not a lever (ruled out, with a measurement)

Clock locking (`-lgc`) is monotonically slower below 1890 MHz (−4.8% at 1700, −8.8% at 1600, no change above).
`CUDA_DEVICE_MAX_CONNECTIONS=1` is +0.17%, inside the noise. A driver change is refused for the reason above.
Cross-card staging is 0.46% of the window, so the link is not the limit. All three were tested as A-B-A or
interleaved, not as one run each.

## Not tested

1. A 127K cold read on the final 0.2.2 build (it was run on an older build only).
2. The 512-token bitwise check under the configuration upstream requires for it (`STRATA_IQ_MT_MIN=1`,
   `--pcie-frac 0`, a fixed adaptive, `--no-prefill-borrow`). Without that, on and off differ by design.
3. A one-hour soak.
4. Multi-stream cold-start stalls: 5–10 s were seen on an older build with 3–4 joins, and did not come back on a
   warm machine. Cause unknown, not reproduced on the final build.
5. Eight slots, long-context (≥128K) concurrency, vision under load, tool calls under load.
6. The engine's own test suite (it was switched off for the release build).

## What comes next

1. Rebuild from `hw/server-adapt` and confirm it matches the binary that is serving now.
2. Close the unmeasured items above that need no new code: the 127K read, the bitwise check, the soak.
3. Only then touch placement, if the numbers ask for it: the split planner and the RAM tier on 22 GB cards.
4. Port the tensor-parallel patch to 0.2.2 on its own branch, or drop it and record why.

## Rules

- Upstream's rules apply here too: [CONTRIBUTING.md](../CONTRIBUTING.md) and [AGENTS.md](../AGENTS.md). The default
  path stays byte-identical to the release; anything new is opt-in and can be turned off.
- No claim without a measurement, and say what it was measured on. A reading taken from the code is marked as
  unconfirmed static analysis, as above.
- One branch, one change. A change that helps only this box stays in this fork; a change that helps any box with
  these cards is worth offering upstream.
