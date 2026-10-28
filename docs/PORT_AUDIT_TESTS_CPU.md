# CPU tests for the session's RoPE share, the MTP logits width and the card-class constants

Added 2026-10-25 for the sm_75 port audit. Two test executables, both registered in CTest, both intended to run on a
machine with no GPU and no model weights:

> **状态：历史审计记录（2026-10-25）。** 本页记录当时为移植审计新增的两个 CPU 测试；
> 契约的现行状态看代码与 CTest，不看本页。

| target | file | contracts |
|---|---|---|
| `session_rope_share_test` | `tests/core/session_rope_share_test.cpp` | `session_bytes(..., borrow_rope)` is smaller by exactly ONE RoPE table `max_cells*(n_rot/2)*4*2` (48 MiB at 196608 cells, 64 MiB at 262144), for 3 layer ranges x 3 cell counts x 3 k, independent of k, linear in `max_cells`, and unaffected by alignment; a `[0,48)` range holds 12 QSA layers but still saves one table, not 12; `session_rope_owner`'s borrow chain (real-engine mode). |
| `mtp_logits_width_test` | `tests/core/mtp_logits_width_test.cpp` | the draft logits are `max_t * n_logits * 4`, full vocabulary minus subset = `max_t*(n_vocab-n_subset)*4`; `bind_bytes` returns 0 once `head_logits_cap >= max_t*n_logits` and exactly 4 for one float short; no subset (no file, short/odd file, `STRATA_MTP_FULL_HEAD`, `n_dvocab >= n_vocab`) prices the whole vocabulary; `dflash2_auto_cap` returns 0 for Ampere/Ada (cc 8) and Turing (cc 7), 6 for sm_120, 5 for UMA; the Turing series is 7.5-only and on by default there. |

Both print every failed assertion as `FAIL <file>:<line>: <expression> [context]` and exit non-zero; a pass prints
`ok (<n> checks)`.

## Two modes, one set of assertions

`session_bytes` is pure arithmetic, but it lives in `src/core/session.cpp`, and `MtpDrafter::bind_bytes` in
`src/core/mtp.cpp`; both are CUDA translation units and both are part of `strata_engine`, which CMake creates only
under `if(STRATA_ENABLE_CUDA OR STRATA_ENABLE_HIP)` (`CMakeLists.txt:335`, `:583`). A CPU-only build therefore has
no object that provides either symbol (and no CUDA headers to compile them with).

The tests handle this without changing `src/`:

* `STRATA_TEST_REAL_ENGINE` - set by `CMakeLists.txt` when `TARGET strata_engine` exists. The tests call the real
  `strata::core::session_bytes`, `strata::core::session_rope_owner` and `MtpDrafter::bind_bytes`.
* otherwise - the functions are transcribed in the test, one source line per line with `src/core/...:NN` on each.
  The transcription is also the oracle the real mode compares against.

Notes on the real mode:

* The tested functions touch no device. `session_bytes` / `session_rope_owner` only read structs; `bind_bytes` only
  compares pointers and reads a file's size. Both tests are registered with `CUDA_VISIBLE_DEVICES=-1`.
* `bind_bytes` reads private width fields (`max_t_`, `n_dvocab_`, `head_logits_`, `head_logits_cap_`,
  `owns_draft_head_`, `dvocab_path_`) whose only public setter, `load()`, needs a device and the real model files.
  The test includes `mtp.hpp` with a test-only `#define private public`; it changes no layout and `mtp.cpp` is
  compiled normally. If that is unwanted, a public width accessor on `MtpDrafter` would replace it (a source
  change, not made here).

## How the CPU-only tests were built and run

```
cmake -S /home/kali/IdeaProjects/strata -B /tmp/strata-build-cpu \
      -DSTRATA_ENABLE_CUDA=OFF -DSTRATA_BUILD_TESTS=ON        # EXIT 0
cmake --build /tmp/strata-build-cpu -j4 --target session_rope_share_test mtp_logits_width_test   # EXIT 0
/tmp/strata-build-cpu/session_rope_share_test                  # EXIT 0
/tmp/strata-build-cpu/mtp_logits_width_test                    # EXIT 0
ctest -R "session_rope_share_test|mtp_logits_width_test"       # 2/2 passed
```

Output:

```
session_rope_share_test: mode=transcription (CPU-only build: session.cpp is not compiled)
geometry: n_layers=48 qsa_interval=4 n_qsa_layers=12 n_rot=64
one table: 4096 cells = 1048576 B, 196608 = 50331648 B (48 MiB), 262144 = 67108864 B (64 MiB)
session_rope_share_test: ok (215 checks)

mtp_logits_width_test: mode=transcription (CPU-only build: mtp.cpp is not compiled)
logits: max_t=8 full = 7946240 B, subset(40000) = 1280000 B, difference = 6666240 B (= 8*(248320-40000)*4)
mtp_logits_width_test: ok (116 checks)
```

`-DSTRATA_WITH_CUDA=OFF` is not a CMake option here (the option is `STRATA_ENABLE_CUDA`, `CMakeLists.txt:69`); it
is stored as an unused `STRATA_WITH_CUDA:UNINITIALIZED` cache entry, prints no warning and changes nothing - the
build was already CPU-only by default. Also `tests/CMakeLists.txt` is absent, so `STRATA_BUILD_TESTS` defaults to
OFF (`CMakeLists.txt:119-125`); `-DSTRATA_BUILD_TESTS=ON` is required, and `cmake --build --target help | grep test`
then lists the CPU test targets.

## The real functions, executed

### sm_75 server build (`build75`)

The two files were copied to `/opt/ai/src/strata-port-20261025/tests/core/` and built by hand against that tree's
`build75` archives, with the link line of a target that also links `strata_engine` (`file_expert_source_test`):

```
cd /opt/ai/src/strata-port-20261025/build75
/usr/bin/c++ -O3 -DNDEBUG -std=c++20 -Wall -Wextra \
  -DSTRATA_HAVE_DFLASH2=1 -DSTRATA_NATIVE_EXPERTS=1 -DSTRATA_Q6K_EXPERTS=1 -DSTRATA_TEST_REAL_ENGINE=1 \
  -I/opt/ai/src/strata-port-20261025/include -isystem /usr/local/cuda-13.0/targets/x86_64-linux/include \
  ../tests/core/session_rope_share_test.cpp -o /tmp/session_rope_share_test_real \
  -L/usr/local/cuda-13.0/targets/x86_64-linux/lib/stubs -L/usr/local/cuda-13.0/targets/x86_64-linux/lib \
  libstrata_engine.a libstrata_kernels.a libstrata_core.a libstrata_kernels_cpu.a \
  ggml/src/libggml-cpu.a ggml/src/libggml-base.a \
  -lm /usr/local/cuda-13.0/targets/x86_64-linux/lib/libcudart.so -lcudadevrt -lcudart_static -lrt -lpthread -ldl
```

(the same line with `mtp_logits_width_test.cpp` -> `/tmp/mtp_logits_width_test_real`.) Both link, and both pass with
`CUDA_VISIBLE_DEVICES=-1` and with `CUDA_VISIBLE_DEVICES=1`; output is identical, which confirms the tested paths
open no context and allocate no VRAM:

```
session_rope_share_test: mode=real (strata_engine)
geometry: n_layers=48 qsa_interval=4 n_qsa_layers=12 n_rot=64
one table: 4096 cells = 1048576 B, 196608 = 50331648 B (48 MiB), 262144 = 67108864 B (64 MiB)
session_rope_share_test: ok (312 checks)                      # EXIT 0

mtp_logits_width_test: mode=real (strata_engine)
logits: max_t=8 full = 7946240 B, subset(40000) = 1280000 B, difference = 6666240 B (= 8*(248320-40000)*4)
mtp_logits_width_test: ok (183 checks)                        # EXIT 0
```

This is the real nvcc/CUDA-13.0 build of `src/core/session.cpp` and `src/core/mtp.cpp`; no stubs and no shim were
used. `build75` has no CMake target for these two tests (that tree is the port baseline and its `CMakeLists.txt` was
not edited); the targets below are registered by this workspace's `CMakeLists.txt`, which is what `ctest -R` uses
on a CPU-only build.

### The same mode locally, without a CUDA toolkit

As an extra check the real-mode tests were also built here against the real `src/core/session.cpp` and
`src/core/mtp.cpp` by compiling them with the repo's Metal CUDA-compat shim (`metal/include/metal_compat`, with its
arm64-only `immintrin.h` removed so the x86 one is used) plus stubs for symbols unrelated to the tested arithmetic:
`gr_workspace_init`, `qsa_decode_attn_scratch_floats`, `shared_expert_scratch_bytes`, `VmmRange::release` (only the
non-RoPE session terms) and the `strata::gpu` destroy/free wrappers + `native_q6_k_unpack` (only the `MtpDrafter`
destructor's null-guarded frees). With `-ffunction-sections -Wl,--gc-sections` only the tested functions and their
callees survive. Result: `session_rope_share_test: ok (312 checks)` and `mtp_logits_width_test: ok (183 checks)`,
exit 0 - the same counts as `build75`.

## Can these become CI-able real-function tests?

Yes, with a GPU toolkit - no GPU and no weights needed at run time:

* CI job with `-DSTRATA_BUILD_TESTS=ON -DSTRATA_ENABLE_CUDA=ON` (nvcc present). `TARGET strata_engine` then exists,
  both tests link it, define `STRATA_TEST_REAL_ENGINE`, and call the real functions. `CUDA_VISIBLE_DEVICES=-1` is
  already set on the tests; **verified on `build75`: both pass with the real CUDA build and no device visible**, so
  such a CI job needs no GPU and no weights at run time - only the toolkit to compile and link.
* CI job without nvcc keeps running the transcription mode - fast and GPU-free, but it pins the documented
  arithmetic, not the compiled function: a refactor of `session_bytes`/`bind_bytes` that keeps the transcription's
  documented contract is only caught by the real-engine job.
* To get the real function in a CPU-only build without a CUDA toolkit, the sizing arithmetic would have to move out
  of the CUDA translation units (e.g. a header-only width helper shared by `session.cpp` and `mtp.cpp`). That is a
  source change and was out of scope here.

## Not tested, and why

* **`session_init` / `session_init(..., share_rope_from)`** - carves the arena and builds the table with the rope
  kernels: `cudaMalloc`, `cudaPointerGetAttributes`, a device check. Needs a device and a real geometry. The sizing
  side (`session_bytes`) is what this test covers; the header's "incompatible donor returns 0" checks
  (`session.cpp:112-140`) are device-dependent and unverified here.
* **`MtpDrafter::load` / `draft` / `prefill`** - read `rt/` files and allocate ~0.9 GB of VRAM.
* **Environment-cached switches**: `full_head_env()` (`mtp.cpp:514-517`), `coupled_draft_env()`
  (`coupled_draft.hpp:77-79`), `sm75_series()` (`sm75_series.hpp:31-41`) and `emulated_cc()` (`emulate.hpp:22-30`)
  read their variable once per process. One process can test one setting; the tests unset the variables at the
  start and test the documented default path (`STRATA_MTP_FULL_HEAD` off, `STRATA_SPEC_*` off, `STRATA_SM75_PERF`
  unset, `STRATA_EMULATE_CC` off). The opposite setting of `STRATA_SM75_PERF` / `STRATA_EMULATE_CC` needs a second
  process and is not covered.
* **`nv_default_env` / `apply_nv_defaults`** (`src/core/nv_defaults.cpp`) - CPU-compilable and untested in the tree,
  but outside this audit's brief; the Ampere/Ada and Turing claims covered here are `dflash2_auto_cap` and the
  sm_75 predicate.
* **`session_zero`, `session_token`, graphs** - all device calls.

## Suspicious contracts found while writing these tests

1. **The audit brief's formula was wrong, not the code.** The brief asked for a borrow saving of
   `q_n * max_cells*(n_rot/2)*4*2` with `q_n` the QSA layers in the range. The source shares ONE table inside the
   session: `src/core/session.cpp:75-77` sizes `qsa_state_bytes(..., true) + (q_n-1)*qsa_state_bytes(..., false)`
   when the session owns the table and `q_n * qsa_state_bytes(..., false)` when it borrows (cross-session), and
   `src/core/session.cpp:184-193` asserts one owner. The header says "saves exactly one table"
   (`include/strata/core/session.hpp:111-118`). The tests assert one table; `q_n * table` is asserted to hold only
   where `q_n == 1` (range `[24,28)`), and for `q_n == 12` the test records that the saving is not `q_n` tables.
   The brief's formula is the error here.
2. **`-DSTRATA_WITH_CUDA=OFF` is not a CMake option.** The option is `STRATA_ENABLE_CUDA` (`CMakeLists.txt:69`);
   the misspelt variable is stored as an unused cache entry and changes nothing. `STRATA_BUILD_TESTS` also has to
   be passed explicitly because `tests/CMakeLists.txt` is absent (`CMakeLists.txt:119-125`).
3. **`bind_bytes` reads the subset width from the file again when `dhead_ == nullptr`, instead of from
   `n_dvocab_`** (`src/core/mtp.cpp:549-560` vs `:759`). After `bind()` the authoritative width is `n_dvocab_`
   (`= n_vocab_` when there is no subset). A `draft_vocab.bin` that appears between `bind()` and a later
   `bind_bytes()` therefore prices a subset where `bind()` built none: the logits term is 0 anyway (`bind()` sized
   the wider full vocabulary), but the head-row term (`:566-567`) over-reserves rows the drafter will never
   allocate. A file that disappears is harmless: `dhead_ != nullptr` then takes the width from `n_dvocab_`. Low
   risk (static reading, not observed), and only this direction can differ.
4. **`bind_bytes` on a shared slot drafter before its `bind()`** (`src/core/mtp.cpp:566`) would reserve the subset's
   head rows (`owns_draft_head_` is still true and `dhead_` is still null before `bind(..., shared)`), although the
   shared head rows are not allocated by that drafter. Static reading only; no call site was found that does this
   before `bind()`.
