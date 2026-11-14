# NVIDIA Tesla V100 / Titan V (Volta, sm_70)

This page keeps measurements from a card this build does not target: it builds sm_75 only. Read it as history.

This page is for people running Strata on a V100 or Titan V. Volta uses the experimental CUDA 12 engine, which current releases
provide as an engine component through the app installer or the Terminal ([INSTALL.md](INSTALL.md)). CUDA 13 cannot run Volta.
How setup selects the CUDA 12 engine and the driver requirements are in [OLDER_GPUS.md](OLDER_GPUS.md). Speeds below were
reported on the named machines. They are not a guarantee for another V100 setup.

> **On this page:** [Quick start](#quick-start) · [What runs on Volta](#what-runs-on-volta) · [Opt-in switches](#opt-in-switches) ·
> [Checking the kernels](#checking-and-benchmarking-the-volta-kernels) · [Limits](#limits) · [History](#history--older-measurements)

## Quick start

1. Install the app or Terminal for your system ([INSTALL.md](INSTALL.md)). Setup picks the CUDA 12 engine when the model runs on
   a Volta card.
2. To choose it by hand from a source checkout: `START-HERE.bat --setup --gpu 0 --cuda 12` (Linux: `./setup.sh`).

### Build from source

CUDA 13 dropped Volta, so the build needs a CUDA 12.x toolkit (12.8 was used) and a host GCC that toolkit accepts.

```sh
cmake -S . -B build -DSTRATA_ENABLE_CUDA=ON -DSTRATA_BUILD_TESTS=OFF \
      -DCMAKE_CUDA_ARCHITECTURES=70 -DSTRATA_EXPERIMENTAL_SM60=ON
cmake --build build --target strata -j
```

A source checkout can fetch or build its CUDA 12 engine; `--build` requests compilation with the toolkit (see
[OLDER_GPUS.md](OLDER_GPUS.md)). For a PC with a V100 and a newer card, build for both architectures
(`-DCMAKE_CUDA_ARCHITECTURES="70;86"`). A sm_70-only build has no code for a card such as a Quadro RTX 4000 (sm_75); hide it with
`CUDA_VISIBLE_DEVICES`.

## What runs on Volta

The flag lowers the floor (CMake and the runtime device check) and replaces two intrinsics Volta lacks. Which kernel runs is
decided per architecture when the engine is built.

| Part of the prompt path | On sm_70 |
| --- | --- |
| MoE experts | MMQ by default. When every visible card is compute capability 7 or newer, the FP16 tensor-core path (`gemm_iq_f16_grouped`, a group of experts per launch) ran instead: FP16 activations rather than MMQ's q8_1, so answers differ. |
| Dense projections (dequantized weights) | FP16 tensor-core GEMMs (cuBLAS / CUTLASS `s884`) |
| BF16 projections (hyper-connection, router, indexer) | converted to FP16 and run on the FP16 tensor cores |
| QSA attention for decode and verify windows | the #540 kernel, fewer shuffles, bit-exact |
| QSA prompt attention, int8 / FP16 / K8V4 KV | `prompt_attn_v70_kernel` on `mma.m8n8k4` (the alternative was the decode kernel, one query at a time) |
| QSA prompt attention, Q4_0 KV | the decode kernel |
| QSA block scores | the warp kernel (the tensor-core scorer needs sm_80) |

| Part of a decode or verify window | On sm_70 |
| --- | --- |
| Routed experts in VRAM | expert mode 8 (codebook grid and the group's activations in shared memory, SwiGLU fused). Opt-in, see below. |
| Dense 2-4 column GEMVs and the head | the interleaved `native_mmvq_il` with a Volta rows table. Opt-in, see below. |
| Hyper-connection read, up projection | the latency-hidden up kernel. Opt-in, see below. |

## Opt-in switches

The switches this page once listed are gone. In the upstream CUDA 12 build each kernel is chosen for the card's
architecture; this checkout builds sm_75 only, so none of these kernels is in it. The rows in the tables above record
what the code did on Volta; the switches they name are no longer read, and the numbers in the history section were
measured with them.

Volta's tensor cores take FP16 only. A model's BF16 weights are converted, and a weight outside FP16's range would saturate (none
did in the check in the history section).

## Checking and benchmarking the Volta kernels

```sh
cmake -S . -B build -DSTRATA_PARITY_PROMPT_ATTN=ON ... && cmake --build build --target qsa_prompt_attn_parity
CUDA_VISIBLE_DEVICES=0 ./build/qsa_prompt_attn_parity 32768 2048 5     # synthetic, no model; exit 0 = PASS
```

It compares the tensor-core attention with the FP32 kernel and an FP64 host reference and times both. The Q4_0 cases are skipped
below sm_80.

## Limits

- A layer split over two V100 was not measured with this kernel. The cards work in turn on a single request, so a split adds
  expert-cache room, not prompt speed.
- Pascal (sm_60) is covered by the same flag. Community runs exist for it; this page's own kernel measurements are V100 only.

## History / older measurements

**Prompt attention kernel.** Measured on: one V100-PCIE-32GB, i9-7960X, Unsloth UD-IQ4_XS, int8 KV, MTP `--spec 2`, random-word
prompts, prompt read speed in tok/s with and without the Volta kernel (a switch that is gone). Decode was 38-51 tok/s in
every run and is not affected. Report: [bench/results/2026-10-03-v100-prompt-attn](../bench/results/2026-10-03-v100-prompt-attn/README.md).

| Prompt tokens | old attention path | `prompt_attn_v70_kernel` |
| --- | ---: | ---: |
| 7,194 | 1,010 | 1,164 |
| 28,650 | 1,063 | 1,251 |
| 114,338 | 973 | 1,123 |

Against an FP64 reference the kernel's error is about 2-3e-6 (the FP32 kernel it replaces: 2e-6). 15 of 15 needles were found.
Greedy output was identical on the 28,650- and 114,338-token prompts and differed late in the answer on the 7,194-token one.

**Other measurements**

| Measured on | Result |
| --- | --- |
| V100-SXM2, contributor's measurement | decode kernels: GPU work per verify window 22.8 -> 21.2 ms ([report](../bench/results/2026-10-07-v100-decode-kernels/README.md)) |
| V100, FP16 tensor-core prompt experts | prompt +10% against MMQ ([report](../bench/results/2026-10-07-v100-prompt-experts/README.md)) |
| V100, second prompt-expert kernel (IQ2_S gate/up, IQ4_NL down) | prompt +2.5-4.3% |
| V100, multi-block greedy pick / register-split norm | decode +0.7-0.8% / +0.5-0.6% |
| V100, 8-wide int8 KV gather off | +11% on the gather kernel |
| V100, chain-split DeltaNet recurrence | +6-9% on the recurrence |
| V100, decode GEMV / MoE fusions | 4-16 us per token saved, not visible end to end |
| Tesla P100 alone and with an RTX 2070 SUPER as expert helper (#1069) | 30.5 tok/s alone, 35.8-38.4 with the helper |
| Two Tesla P40, layer split (#1028) | [report](../bench/results/2026-10-05-community-2x-p40/README.md) |
| V100 + P100 pair (#1079) | +6-11% decode from engine 0.1.39 to 0.1.40 with the P100 as helper cache |
