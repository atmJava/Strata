// include/strata/prefill/moe_fused_iq.hpp - #136: the fused int8 prompt experts (moe_fused.hpp) for the native GGUF
// packs (tools/iq_pack.py: an expert is its raw GGUF slices [gate rows | up rows | down rows], formats per layer in
// expert_layout().fmt), opt-in with the same STRATA_PF_FUSED=1 (NVIDIA sm_80 and newer; HIP: MMQ).  These kernels
// are set up by default on a compute capability 7.5 (Turing) card (their own switch, STRATA_PF_FUSED_SM75 - not the
// STRATA_SM75_PERF series, whose kernels live in sm75_series.hpp): dev_info sets them up per kernel and prints one
// banner, so the default launch path is still MMQ - the fused pairs need STRATA_PF_FUSED=1 like every other covered
// format.  With it, the production pack's pair (gate/up IQ3_XXS, down IQ4_NL) runs where its
// shapes fit the card's shared memory, and the Q2_0/Q2_0 pair runs the fused kernels on a 7.5 card too (before this it
// needed STRATA_PF_FUSED_SM75=1 there - the series default is what changed, not the pair)
// (checked per kernel in moe_fused_iq.cu; the IQ pair runs at 2 warps a row when the 4-warp shape does not fit);
// STRATA_PF_FUSED_SM75=0 keeps the MMQ path there in every case.
//
// The grouping on the GPU (fused::group), the per-expert pointers (fused::Batch: a cache slot or a ring slot, no
// gather) and the launch shape are the Q2_0 path's.  What differs is the load stage: the i-quant blocks are decoded to
// int8 in shared memory before the mma (codebook and sign lookups, as llama.cpp's MMQ load_tiles does), with the
// block's scale per 32 (or per 16: IQ2_XS, IQ2_S, multiplied at m16n8k16) beside them.  The activations and H are
// int8 per 32 values in their natural order here (quantize_act_native), not the Q2_0 path's permuted one.
//
// Covered: gate/up IQ2_XXS, IQ2_XS, IQ2_S, IQ3_XXS, IQ3_S, IQ4_XS; down Q2_0, IQ4_NL - every layer of the IQ2_XS,
// IQ3_XXS and IQ3_S packs but the IQ1_M ones (which MMQ does not cover either).  Other layers keep MMQ.
#pragma once

#include "strata/prefill/moe_fused.hpp"

#include <cstddef>
#include <cstdint>

namespace strata::prefill::fused {

/// One layer's native expert geometry (cpu::NativeFmt's fields the kernels read; n_embd 2560, n_ff 640).
struct NativeGeom {
    int gu_type = -1, d_type = -1;    ///< ggml types
    size_t gu_row = 0, d_row = 0;     ///< bytes per gate/up row and per down row
    size_t up_off = 0, down_off = 0;  ///< inside the blob
};

/// enabled() (STRATA_PF_FUSED=1 on sm_80+), and the kernels cover this gate/up and down pair on this device.
bool native_supported(int gu_type, int d_type);

/// x [rows][cols] FP32 -> `xa` (act_bytes(rows, cols)): int8 per 32 values in natural order, the native kernels' form.
void quantize_act_native(const float* x, int64_t rows, int64_t cols, void* xa, void* stream);

/// experts() for native blobs: b.blob[e - e0] is the native blob of expert e (gate at 0, up at g.up_off, down at
/// g.down_off; 2-byte aligned).  `xa` from quantize_act_native; H in `ha` (act_bytes(n, 640)); down into `dm` at the
/// rows of `group`.  `dm` 16-byte aligned (gfx12 stores float4; checked there - the prompt path's buffers are 256-aligned
/// through Alloc's 256-byte takes from a 256-aligned cache slot or cudaMalloc).
void experts_native(const Batch& b, const NativeGeom& g, int n_expert, int64_t n, const void* scratch, const void* xa,
                    const int32_t* src, void* ha, float* dm, void* stream);

}  // namespace strata::prefill::fused
