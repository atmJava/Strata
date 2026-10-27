# AI reference

This file gives source-backed facts for the retained server distribution. Exact measurements and deployment hashes
live in [STATE.md](STATE.md), [HANDOVER.md](HANDOVER.md) and [EVIDENCE_LEDGER.md](EVIDENCE_LEDGER.md).

## Product surface

The retained surfaces are:

- the `strata` command line in `strata.py`, `strata_cli/` and `strata_control/`
- the Python server in `serve/`
- the CUDA engine sources in `src/`, `include/`, `cmake/` and retained CUDA-specific third-party sources
- DFlash2 kernels, server wiring, pack tools and tests

Not retained in this distribution:

- desktop and Tauri/Node hosts
- installers, updater feeds and release packaging
- model marketplace or downloader commands
- HIP, SYCL and Metal implementations
- engines and run paths for model families other than Qwen3.8-Flash-Next

Older files remain in Git history, not in the current tree.

## Hardware and build

The fixed target is Linux x86_64, CUDA compute capability 7.5, 2 x RTX 2080 Ti 22 GB. CMake forces
`CMAKE_CUDA_ARCHITECTURES=75` and copies DFlash2 into the engine build:

- `src/kernels/cuda/dflash2.cu`
- `src/core/dflash2.cpp`
- DFlash2 parity and benchmark targets behind `EXCLUDE_FROM_ALL`

The local refactor workstation has no `nvcc` and no NVIDIA GPU. No claim is made that this edited tree compiled,
loaded, or ran on the target host.

## Server

The Python server exposes OpenAI- and Anthropic-compatible endpoints, including:

- `POST /v1/chat/completions`
- `POST /v1/responses`
- `POST /v1/messages`
- `GET /v1/models`
- `GET /props`, `/health`, `/slots`, `/metrics`
- `GET /v1/telemetry`

The production target is 6 slots, 262,144-token context, INT8 KV cache, `kv_resident=32768`, two batch groups and
backend `cuda`.

`strata check-production` validates those effective values and the 12 critical environment switches. It is static.
It does not compile CUDA, identify the running binary, load a model or test stability.

## DFlash2

DFlash2 mode values are `auto`, `mtp`, `dflash2` and `off`.

- `auto` currently resolves to MTP.
- `dflash2` requires a Qwen3.8-Flash-Next model and a ready pack.
- The expected pack is `<data-dir>/dflash2/qwen3.8-flash-next-dflash2.q8.pack`.
- A missing or incompatible pack falls back to MTP with a reason.
- The server emits `--draft dflash2 --draft-model <pack>` only when the pack is ready.

DFlash2 is not the production default. The current host lacks the drafter pack, and the tree has no complete
same-boot `sm_75` prompt-class sweep proving it faster in every case.

## Measured production state

As recorded on the target host:

- production engine version `0.1.51`
- 6 x 256K profile after changing `max_context` from `192000` to `262144`
- `batch_slots=6`, `kv_resident=32768`, `expert_slots=17248`
- expert cache `28222 MiB`
- startup free VRAM `1005/1073 MiB`
- health 200 and `loaded=true` after the switch
- two needle requests passed at 222,322 tokens and 253,633 tokens
- a 6-way short concurrency check passed
- the engine's earlier one-hour mixed soak used the 192K profile

The 256K mixed soak has not run. Do not describe the 192K soak as 256K evidence.

## Known limits

- a full 262,144-token request was not tested; the largest needle was 253,633 tokens
- simultaneous long reads, cancellation and real multi-tenant traffic are not covered
- long reads remain about 37 to 42 seconds in the measured earlier mixed workload
- full-slot admission latency remains a user-visible limit
- GPU availability, driver state and actual running hashes must be checked on the host
- API key values must never be printed or committed

## Connection defaults

The server should remain on `127.0.0.1` unless the operator explicitly exposes it. Remote monitoring requires
HTTPS. Keep inference and telemetry credentials distinct when possible, and pass them by environment-variable
reference rather than literal command arguments.
