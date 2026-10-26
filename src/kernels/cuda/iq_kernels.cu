// src/kernels/cuda/iq_kernels.cu - see include/strata/kernels/iq_kernels.hpp.
//
// The dot products (vec_dot_*_q8_1), the dequantizers and the q8_1 quantizer are transcribed from llama.cpp
// (ggml/src/ggml-cuda/vecdotq.cuh, dequantize.cuh, quantize.cu at the commit in third_party/ggml/VERSION.txt;
// MIT license, third_party/ggml/LICENSE).  The block structs and codebook grids come from its ggml-common.h,
// included unchanged.
#include "strata/kernels/iq_kernels.hpp"
#include "strata/kernels/dp4a.hpp"
#include "strata/kernels/q8_1_finite.hpp"

#include <cuda_fp16.h>
#include <cuda_runtime.h>

#define GGML_COMMON_DECL_CUDA
#define GGML_COMMON_IMPL_CUDA
#include "ggml-common.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <type_traits>

namespace strata::kernels {
namespace {

void check(const char* what) {
    const cudaError_t e = cudaGetLastError();
    if (e != cudaSuccess) { std::fprintf(stderr, "%s: %s\n", what, cudaGetErrorString(e)); std::exit(1); }
}

// ---------------------------------------------------------------- llama.cpp helpers (vecdotq.cuh)
__device__ __forceinline__ int get_int_b2(const void* x, const int& i32) {
    const uint16_t* x16 = (const uint16_t*) x;
    int x32 = x16[2 * i32 + 0] << 0;
    x32 |= x16[2 * i32 + 1] << 16;
    return x32;
}
__device__ __forceinline__ int get_int_b4(const void* x, const int& i32) { return ((const int*) x)[i32]; }
__device__ __forceinline__ uint32_t unpack_ksigns(const uint8_t v) {
    const uint32_t p = __popc(v) & 1;
    const uint32_t s = v ^ p << 7;
    return s * 0x01010101;
}
__device__ __forceinline__ int2 get_int_from_table_16(const int& q4, const int8_t* table) {
    const uint32_t* table32 = (const uint32_t*) table;
    uint32_t tmp[2];
    const uint32_t low_high_selection_indices = (0x32103210 | ((q4 & 0x88888888) >> 1));
#pragma unroll
    for (uint32_t i = 0; i < 2; ++i) {
        const uint32_t shift = 16 * i;
        const uint32_t low = __byte_perm(table32[0], table32[1], q4 >> shift);
        const uint32_t high = __byte_perm(table32[2], table32[3], q4 >> shift);
        tmp[i] = __byte_perm(low, high, low_high_selection_indices >> shift);
    }
    return make_int2(__byte_perm(tmp[0], tmp[1], 0x6420), __byte_perm(tmp[0], tmp[1], 0x7531));
}
#define ggml_cuda_dp4a(a, b, c) STRATA_DP4A((a), (b), (c))

// ---------------------------------------------------------------- the dot products (vecdotq.cuh)
__device__ __forceinline__ float vec_dot_q2_0_q8_1(const void* __restrict__ vbq, const block_q8_1* __restrict__ bq8_1,
                                                   const int& kbx, const int& iqs) {
    const block_q2_0* bq2_0 = (const block_q2_0*) vbq + kbx;
    const float d2 = bq2_0->d;
    const int16_t* qs = (const int16_t*) bq2_0->qs + iqs * 4;
    const block_q8_1* bq8_1_chunk = bq8_1 + iqs;
    int sumi = 0;
#pragma unroll
    for (int j = 0; j < 4; ++j) {
        const int q = qs[j];
        const int u = get_int_b4(bq8_1_chunk->qs, j * 2 + 0);
        const int v = get_int_b4(bq8_1_chunk->qs, j * 2 + 1);
        const int qe = __byte_perm(0x020100FF, 0x020100FF, q >> 0);
        const int qo = __byte_perm(0x020100FF, 0x020100FF, q >> 2);
        const int qx = __byte_perm(qe, qo, 0x5140);
        const int qy = __byte_perm(qe, qo, 0x7362);
        sumi = ggml_cuda_dp4a(u, qx, sumi);
        sumi = ggml_cuda_dp4a(v, qy, sumi);
    }
    const float d8 = __low2float(bq8_1_chunk->ds);
    return d2 * d8 * sumi;
}

__device__ __forceinline__ float vec_dot_iq3_xxs_q8_1(const void* __restrict__ vbq, const block_q8_1* __restrict__ bq8_1,
                                                      const int& kbx, const int& iqs) {
    const block_iq3_xxs* bq3 = (const block_iq3_xxs*) vbq + kbx;
    const int2 q3_packed = make_int2(get_int_b2(bq3->qs, iqs), get_int_b2(bq3->qs, iqs + 1));
    const uint8_t* q3 = (const uint8_t*) &q3_packed;
    const uint32_t aux32 = get_int_b2(bq3->qs, QK_K / 16 + iqs / 2);
    int sumi = 0;
#pragma unroll
    for (int l0 = 0; l0 < 8; l0 += 2) {
        const int2 grid_pos = make_int2(iq3xxs_grid[q3[l0 + 0]], iq3xxs_grid[q3[l0 + 1]]);
        const uint32_t signs = unpack_ksigns(aux32 >> (7 * l0 / 2));
        const int signs0 = __vcmpne4(signs & 0x08040201, 0);
        const int grid_l = __vsub4(grid_pos.x ^ signs0, signs0);
        const int u0 = get_int_b4(bq8_1[iqs / 2].qs, l0 + 0);
        const int signs1 = __vcmpne4(signs & 0x80402010, 0);
        const int grid_h = __vsub4(grid_pos.y ^ signs1, signs1);
        const int u1 = get_int_b4(bq8_1[iqs / 2].qs, l0 + 1);
        sumi = ggml_cuda_dp4a(grid_l, u0, sumi);
        sumi = ggml_cuda_dp4a(grid_h, u1, sumi);
    }
    const int ls = aux32 >> 28;
    sumi = (ls * sumi + sumi / 2) / 2;
    const float d = __half2float(bq3->d) * __low2float(bq8_1[iqs / 2].ds);
    return d * sumi;
}

__device__ __forceinline__ float vec_dot_iq4_nl_q8_1(const void* __restrict__ vbq, const block_q8_1* __restrict__ bq8_1,
                                                     const int& kbx, const int& iqs) {
    const block_iq4_nl* bq4 = (const block_iq4_nl*) vbq + kbx;
    const int* q8 = (const int*) bq8_1->qs + iqs;
    int sumi = 0;
#pragma unroll
    for (int l = 0; l < 2; ++l) {
        const int aux_q4 = get_int_b2(bq4->qs, iqs + l);
        const int2 v = get_int_from_table_16(aux_q4, kvalues_iq4nl);
        sumi = ggml_cuda_dp4a(v.x, q8[l + 0], sumi);
        sumi = ggml_cuda_dp4a(v.y, q8[l + 4], sumi);
    }
    const float d = __half2float(bq4->d) * __low2float(bq8_1->ds);
    return d * sumi;
}

constexpr int VDR_Q8_0 = 2;

__device__ __forceinline__ float vec_dot_q8_0_q8_1(const void* __restrict__ vbq, const block_q8_1* __restrict__ bq8_1,
                                                   const int& kbx, const int& iqs) {
    const block_q8_0* bq8_0 = (const block_q8_0*) vbq + kbx;
    int sumi = 0;
#pragma unroll
    for (int i = 0; i < VDR_Q8_0; ++i)
        sumi = ggml_cuda_dp4a(get_int_b2(bq8_0->qs, iqs + i), get_int_b4(bq8_1->qs, iqs + i), sumi);
    const float d8_0 = __half2float(bq8_0->d), d8_1 = __low2float(bq8_1->ds);
    return d8_0 * d8_1 * ((float) sumi);
}
// ---------------------------------------------------------------- the formats
// qk = values per block, ipb = dot calls per block (qi / vdr), step = the iqs stride between calls.
template<int TY> struct Fmt;
template<> struct Fmt<18> { static constexpr int qk = 256, ipb = 8, step = 2;
    __device__ static float dot(const void* v, const block_q8_1* y, int kbx, int iqs) { return vec_dot_iq3_xxs_q8_1(v, y, kbx, iqs); } };
template<> struct Fmt<20> { static constexpr int qk = 32, ipb = 2, step = 2;
    __device__ static float dot(const void* v, const block_q8_1* y, int kbx, int iqs) { return vec_dot_iq4_nl_q8_1(v, y, kbx, iqs); } };
template<> struct Fmt<42> { static constexpr int qk = 64, ipb = 2, step = 1;
    __device__ static float dot(const void* v, const block_q8_1* y, int kbx, int iqs) { return vec_dot_q2_0_q8_1(v, y, kbx, iqs); } };
template<> struct Fmt<8> { static constexpr int qk = 32, ipb = QI8_0 / VDR_Q8_0, step = VDR_Q8_0;
    __device__ static float dot(const void* v, const block_q8_1* y, int kbx, int iqs) { return vec_dot_q8_0_q8_1(v, y, kbx, iqs); } };

// The formats of each role, one list each so a type cannot be in one switch and missing from another.  Every
// entry is a kernel template for each CUDA architecture of the build, hence two lists rather than one.
#define STRATA_GU_FMTS(X) X(18) X(8)
#define STRATA_D_FMTS(X) X(20) X(42) X(8)
#define STRATA_MMVQ_FMTS(X) X(18) X(20) X(42) X(8)

__device__ __forceinline__ float warp_sum(float v) {
#pragma unroll
    for (int o = 16; o > 0; o >>= 1) v += __shfl_xor_sync(0xffffffffu, v, o);
    return v;
}

// One row against one q8_1 activation, the whole warp: call k = (block, part) is lane-strided.
template<int TY>
__device__ __forceinline__ float row_dot(const uint8_t* row, const block_q8_1* x, int nb, int lane) {
    using F = Fmt<TY>;
    float s = 0.0f;
    for (int k = lane; k < nb * F::ipb; k += 32) {
        const int kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
        s += F::dot(row, x + kbx * (F::qk / 32), kbx, iqs);
    }
    return warp_sum(s);
}

template<int TY>
__global__ void __launch_bounds__(128) mmvq_kernel(const uint8_t* __restrict__ w, size_t row_bytes,
                                                   const block_q8_1* __restrict__ x, float* __restrict__ y, int n_in,
                                                   int n_out, int ncols) {
    const int row = blockIdx.x * 4 + threadIdx.y;
    if (row >= n_out) return;
    const int lane = threadIdx.x;
    const int nb = n_in / Fmt<TY>::qk;
    const uint8_t* wr = w + (size_t) row * row_bytes;
    for (int c = 0; c < ncols; ++c) {
        const float s = row_dot<TY>(wr, x + (size_t) c * (n_in / 32), nb, lane);
        if (lane == 0) y[(size_t) c * n_out + row] = s;
    }
}

// ---------------------------------------------------------------- decode once, apply to every column
// The kernels above call Fmt<TY>::dot once per (call, column): each column re-reads the weight words and redoes
// the grid lookups and sign unpacking.  Here each dot is split, as native_mmvq.cu's multi-column traits are, into
// `load` (everything that depends only on the weight: the signed grid words, the integer scales, the fp16 block
// scale as a float) and `apply` (the activation loads, the dp4a chain in the same order, the same integer scale
// step and the same float expression).  `apply(load(...))` does the dot's integer and float operations in the same
// order on the same values, so a column of the kernels below is BITWISE equal to the same column of mmvq_kernel /
// native_gu_kernel / native_down_kernel (iq_multi_parity checks it; STRATA_OLD_IQ_MMVQ=1 keeps the old kernels).
template<int TY> struct Split;
// Formats with a Split below take the decode-once kernels.  Every supported matrix format has one.
template<int TY> inline constexpr bool kSplit = false;
template<int TY> inline constexpr bool kStageIqGrid = (TY == 18);
template<> inline constexpr bool kSplit<18> = true;
template<> struct Split<18> {   // IQ3_XXS
    struct W { int g[8]; int ls; float dw; };
    template<bool STAGE_GRID = false>
    __device__ static W load(const void* __restrict__ vbq, int kbx, int iqs, const uint32_t* __restrict__ s_grid = nullptr) {
        const block_iq3_xxs* bq3 = (const block_iq3_xxs*) vbq + kbx;
        const int2 q3_packed = make_int2(get_int_b2(bq3->qs, iqs), get_int_b2(bq3->qs, iqs + 1));
        const uint8_t* q3 = (const uint8_t*) &q3_packed;
        const uint32_t aux32 = get_int_b2(bq3->qs, QK_K / 16 + iqs / 2);
        const uint32_t* grid = STAGE_GRID ? s_grid : iq3xxs_grid;
        W r;
#pragma unroll
        for (int l0 = 0; l0 < 8; l0 += 2) {
            const int2 grid_pos = make_int2(grid[q3[l0 + 0]], grid[q3[l0 + 1]]);
            if constexpr (STAGE_GRID) {
                // The staged tables hold one mask per 7-bit sign field; bit 7 of the field is the next group's
                // first sign, which unpack_ksigns cancels, so masking it off is the same value.
                const uint32_t v = (aux32 >> (7 * l0 / 2)) & 0x7f;
                const uint32_t ml = s_grid[256 + v], mh = s_grid[384 + v];
                r.g[l0 + 0] = (int) ((grid_pos.x ^ ml) + (ml & 0x01010101u));
                r.g[l0 + 1] = (int) ((grid_pos.y ^ mh) + (mh & 0x01010101u));
            } else {
                const uint32_t signs = unpack_ksigns(aux32 >> (7 * l0 / 2));
                const int signs0 = __vcmpne4(signs & 0x08040201, 0);
                r.g[l0 + 0] = __vsub4(grid_pos.x ^ signs0, signs0);
                const int signs1 = __vcmpne4(signs & 0x80402010, 0);
                r.g[l0 + 1] = __vsub4(grid_pos.y ^ signs1, signs1);
            }
        }
        r.ls = aux32 >> 28;
        r.dw = __half2float(bq3->d);
        return r;
    }
    __device__ static float apply(const W& r, const block_q8_1* __restrict__ bq8_1, int iqs) {
        int sumi = 0;
#pragma unroll
        for (int j = 0; j < 8; ++j) sumi = ggml_cuda_dp4a(r.g[j], get_int_b4(bq8_1[iqs / 2].qs, j), sumi);
        sumi = (r.ls * sumi + sumi / 2) / 2;
        const float d = r.dw * __low2float(bq8_1[iqs / 2].ds);
        return d * sumi;
    }
    __device__ static float apply_u(const W& r, const int (&u)[8], float d8) {
        int sumi = 0;
#pragma unroll
        for (int j = 0; j < 8; ++j) sumi = ggml_cuda_dp4a(r.g[j], u[j], sumi);
        sumi = (r.ls * sumi + sumi / 2) / 2;
        const float d = r.dw * d8;
        return d * sumi;
    }
};
template<> inline constexpr bool kSplit<20> = true;
template<> struct Split<20> {   // IQ4_NL
    struct W { int2 v[2]; float dw; };
    template<bool STAGE_GRID = false>
    __device__ static W load(const void* __restrict__ vbq, int kbx, int iqs, const uint32_t* __restrict__ = nullptr) {
        const block_iq4_nl* bq4 = (const block_iq4_nl*) vbq + kbx;
        W r;
#pragma unroll
        for (int l = 0; l < 2; ++l) r.v[l] = get_int_from_table_16(get_int_b2(bq4->qs, iqs + l), kvalues_iq4nl);
        r.dw = __half2float(bq4->d);
        return r;
    }
    __device__ static float apply(const W& r, const block_q8_1* __restrict__ bq8_1, int iqs) {
        const int* q8 = (const int*) bq8_1->qs + iqs;
        int sumi = 0;
#pragma unroll
        for (int l = 0; l < 2; ++l) {
            sumi = ggml_cuda_dp4a(r.v[l].x, q8[l + 0], sumi);
            sumi = ggml_cuda_dp4a(r.v[l].y, q8[l + 4], sumi);
        }
        const float d = r.dw * __low2float(bq8_1->ds);
        return d * sumi;
    }
    // `apply` with the block's words in registers: p0 = words iqs, iqs + 1; p1 = words iqs + 4, iqs + 5; d8 = __low2float(ds)
    __device__ static float apply_u(const W& r, int2 p0, int2 p1, float d8) {
        int sumi = 0;
        sumi = ggml_cuda_dp4a(r.v[0].x, p0.x, sumi);
        sumi = ggml_cuda_dp4a(r.v[0].y, p1.x, sumi);
        sumi = ggml_cuda_dp4a(r.v[1].x, p0.y, sumi);
        sumi = ggml_cuda_dp4a(r.v[1].y, p1.y, sumi);
        const float d = r.dw * d8;
        return d * sumi;
    }
};
template<> inline constexpr bool kSplit<42> = true;
template<> struct Split<42> {   // Q2_0
    struct W { int qx[4], qy[4]; float d2; };
    template<bool STAGE_GRID = false>
    __device__ static W load(const void* __restrict__ vbq, int kbx, int iqs, const uint32_t* __restrict__ = nullptr) {
        const block_q2_0* bq2_0 = (const block_q2_0*) vbq + kbx;
        W r;
        r.d2 = bq2_0->d;
        const int16_t* qs = (const int16_t*) bq2_0->qs + iqs * 4;
#pragma unroll
        for (int j = 0; j < 4; ++j) {
            const int q = qs[j];
            const int qe = __byte_perm(0x020100FF, 0x020100FF, q >> 0);
            const int qo = __byte_perm(0x020100FF, 0x020100FF, q >> 2);
            r.qx[j] = __byte_perm(qe, qo, 0x5140);
            r.qy[j] = __byte_perm(qe, qo, 0x7362);
        }
        return r;
    }
    __device__ static float apply(const W& r, const block_q8_1* __restrict__ bq8_1, int iqs) {
        const block_q8_1* bq8_1_chunk = bq8_1 + iqs;
        int sumi = 0;
#pragma unroll
        for (int j = 0; j < 4; ++j) {
            const int u = get_int_b4(bq8_1_chunk->qs, j * 2 + 0);
            const int v = get_int_b4(bq8_1_chunk->qs, j * 2 + 1);
            sumi = ggml_cuda_dp4a(u, r.qx[j], sumi);
            sumi = ggml_cuda_dp4a(v, r.qy[j], sumi);
        }
        const float d8 = __low2float(bq8_1_chunk->ds);
        return r.d2 * d8 * sumi;
    }
    // `apply` with the q8_1 block (2 kbx + iqs) in registers: u = its 8 words, d8 = __low2float(ds)
    __device__ static float apply_u(const W& r, const int (&u)[8], float d8) {
        int sumi = 0;
#pragma unroll
        for (int j = 0; j < 4; ++j) {
            sumi = ggml_cuda_dp4a(u[j * 2 + 0], r.qx[j], sumi);
            sumi = ggml_cuda_dp4a(u[j * 2 + 1], r.qy[j], sumi);
        }
        return r.d2 * d8 * sumi;
    }
};
template<int TY> struct S27;
template<> struct S27<8> {   // Q8_0
    struct W { int q[VDR_Q8_0]; float d; };
    __device__ static W load(const void* __restrict__ vbq, int kbx, int iqs) {
        const block_q8_0* b = (const block_q8_0*) vbq + kbx;
        W r;
#pragma unroll
        for (int i = 0; i < VDR_Q8_0; ++i) r.q[i] = get_int_b2(b->qs, iqs + i);
        r.d = __half2float(b->d);
        return r;
    }
    __device__ static float apply(const W& r, const block_q8_1* __restrict__ bq8_1, int iqs) {
        int sumi = 0;
#pragma unroll
        for (int i = 0; i < VDR_Q8_0; ++i) sumi = ggml_cuda_dp4a(r.q[i], get_int_b4(bq8_1->qs, iqs + i), sumi);
        const float d8_1 = __low2float(bq8_1->ds);
        return r.d * d8_1 * ((float) sumi);
    }
};
// Q8_0's decode-once trait, shared by DFlash2 dense matrices and the Q8_0 expert paths.
template<int TY> struct SplitK : S27<TY> {
    template<bool STAGE_GRID = false>
    __device__ static typename S27<TY>::W load(const void* __restrict__ vbq, int kbx, int iqs, const uint32_t* __restrict__ = nullptr) {
        return S27<TY>::load(vbq, kbx, iqs);
    }
};
template<> inline constexpr bool kSplit<8> = true;
template<> struct Split<8> : SplitK<8> {};

// One row against the n <= NC activations x + off[0..n) (n >= 1, warp-uniform; offsets in q8_1 blocks, 32-bit to
// spare registers), the whole warp.  Per activation this is row_dot: the same calls k, lane-strided the same way,
// summed in the same order, then the same warp_sum.  Only the weight side moves out of the per-activation loop.
template<int TY, int NC, bool EXACT_N = false, bool STAGE_GRID = false>
__device__ __forceinline__ void row_dot_multi(const uint8_t* row, const block_q8_1* x, const int (&off)[NC], int n,
                                              int nb, int lane, float (&s)[NC], const uint32_t* __restrict__ s_grid = nullptr) {
    using F = Fmt<TY>;
    using S = Split<TY>;
#pragma unroll
    for (int c = 0; c < NC; ++c) s[c] = 0.0f;
    for (int k = lane; k < nb * F::ipb; k += 32) {
        const int kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
        const typename S::W w = S::template load<STAGE_GRID>(row, kbx, iqs, s_grid);
#pragma unroll
        for (int c = 0; c < NC; ++c)
            if (EXACT_N || c < n) s[c] += S::apply(w, x + off[c] + kbx * (F::qk / 32), iqs);
    }
#pragma unroll
    for (int c = 0; c < NC; ++c)
        if (EXACT_N || c < n) s[c] = warp_sum(s[c]);
}

// 8-lane sub-warp row dot for K = nb * ipb == 40 (TD == 20, IQ4_NL with n_ff == 640):
// 4 rows per warp (32 rows per 256-thread block), 100% active lanes, bitwise identical addition tree to row_dot_multi.
template<int TY, int NC, bool EXACT_N = false, bool STAGE_GRID = false>
__device__ __forceinline__ void row_dot_40_sub8(const uint8_t* row, const block_q8_1* x, const int (&off)[NC], int n,
                                                int t, float (&s)[NC], const uint32_t* __restrict__ s_grid = nullptr) {
    using F = Fmt<TY>;
    using S = Split<TY>;
#pragma unroll
    for (int c = 0; c < NC; ++c) s[c] = 0.0f;
    for (int k = t; k < 40; k += 32) {
        const int kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
        const typename S::W w = S::template load<STAGE_GRID>(row, kbx, iqs, s_grid);
#pragma unroll
        for (int c = 0; c < NC; ++c)
            if (EXACT_N || c < n) s[c] += S::apply(w, x + off[c] + kbx * (F::qk / 32), iqs);
    }
    {
        const int k = t + 16, kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
        const typename S::W w = S::template load<STAGE_GRID>(row, kbx, iqs, s_grid);
#pragma unroll
        for (int c = 0; c < NC; ++c) {
            if (EXACT_N || c < n) {
                float s16 = 0.0f;
                s16 += S::apply(w, x + off[c] + kbx * (F::qk / 32), iqs);
                s[c] = __fadd_rn(s[c], s16);
            }
        }
    }
    float s8[NC];
#pragma unroll
    for (int c = 0; c < NC; ++c) s8[c] = 0.0f;
    {
        const int k = t + 8, kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
        const typename S::W w = S::template load<STAGE_GRID>(row, kbx, iqs, s_grid);
#pragma unroll
        for (int c = 0; c < NC; ++c)
            if (EXACT_N || c < n) s8[c] += S::apply(w, x + off[c] + kbx * (F::qk / 32), iqs);
    }
    {
        const int k = t + 24, kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
        const typename S::W w = S::template load<STAGE_GRID>(row, kbx, iqs, s_grid);
#pragma unroll
        for (int c = 0; c < NC; ++c) {
            if (EXACT_N || c < n) {
                float s24 = 0.0f;
                s24 += S::apply(w, x + off[c] + kbx * (F::qk / 32), iqs);
                s[c] = __fadd_rn(s[c], __fadd_rn(s8[c], s24));
            }
        }
    }
#pragma unroll
    for (int c = 0; c < NC; ++c) {
        if (EXACT_N || c < n) {
#pragma unroll
            for (int o = 4; o > 0; o >>= 1) s[c] += __shfl_xor_sync(0xffffffffu, s[c], o);
        }
    }
}

// 16-lane sub-warp row dot for K = nb * ipb == 20 (TD == 42, Q2_0, with n_ff == 640):
// 2 rows per warp (16 rows per 256-thread block), bitwise identical addition tree to row_dot_multi.
template<int TY, int NC, bool EXACT_N = false>
__device__ __forceinline__ void row_dot_20_sub16(const uint8_t* row, const block_q8_1* x, const int (&off)[NC], int n,
                                                 int t, float (&s)[NC]) {
    using F = Fmt<TY>;
    using S = Split<TY>;
#pragma unroll
    for (int c = 0; c < NC; ++c) s[c] = 0.0f;
    {
        const int k = t, kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
        const typename S::W w = S::load(row, kbx, iqs);
#pragma unroll
        for (int c = 0; c < NC; ++c)
            if (EXACT_N || c < n) s[c] += S::apply(w, x + off[c] + kbx * (F::qk / 32), iqs);
    }
    float s16[NC];
#pragma unroll
    for (int c = 0; c < NC; ++c) s16[c] = 0.0f;
    if (t < 4) {
        const int k = t + 16, kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
        const typename S::W w = S::load(row, kbx, iqs);
#pragma unroll
        for (int c = 0; c < NC; ++c)
            if (EXACT_N || c < n) s16[c] += S::apply(w, x + off[c] + kbx * (F::qk / 32), iqs);
    }
#pragma unroll
    for (int c = 0; c < NC; ++c) {
        if (EXACT_N || c < n) {
            s[c] = __fadd_rn(s[c], s16[c]);
#pragma unroll
            for (int o = 8; o > 0; o >>= 1) s[c] += __shfl_xor_sync(0xffffffffu, s[c], o);
        }
    }
}

// 16-lane sub-warp row dot for K = nb * ipb == 80 (all kSplit gate/up formats with n_embd == 2560):
// 2 rows per warp (16 rows per 256-thread block), 100% active lanes, bitwise identical addition tree to row_dot_multi.
template<int TY, int NC, bool EXACT_N = false, bool STAGE_GRID = false>
__device__ __forceinline__ void row_dot_80_sub16(const uint8_t* row, const block_q8_1* x, const int (&off)[NC], int n,
                                                 int t, float (&s)[NC], const uint32_t* __restrict__ s_grid = nullptr) {
    using F = Fmt<TY>;
    using S = Split<TY>;
#pragma unroll
    for (int c = 0; c < NC; ++c) s[c] = 0.0f;
    for (int k = t; k < 80; k += 32) {
        const int kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
        const typename S::W w = S::template load<STAGE_GRID>(row, kbx, iqs, s_grid);
#pragma unroll
        for (int c = 0; c < NC; ++c)
            if (EXACT_N || c < n) s[c] += S::apply(w, x + off[c] + kbx * (F::qk / 32), iqs);
    }
    float s16[NC];
#pragma unroll
    for (int c = 0; c < NC; ++c) s16[c] = 0.0f;
    for (int k = t + 16; k < 64; k += 32) {
        const int kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
        const typename S::W w = S::template load<STAGE_GRID>(row, kbx, iqs, s_grid);
#pragma unroll
        for (int c = 0; c < NC; ++c)
            if (EXACT_N || c < n) s16[c] += S::apply(w, x + off[c] + kbx * (F::qk / 32), iqs);
    }
#pragma unroll
    for (int c = 0; c < NC; ++c) {
        if (EXACT_N || c < n) {
            s[c] = __fadd_rn(s[c], s16[c]);
#pragma unroll
            for (int o = 8; o > 0; o >>= 1) s[c] += __shfl_xor_sync(0xffffffffu, s[c], o);
        }
    }
}

// 147-verify: row_dot_80_sub16 for two rows (a gate row and its up row) at once, both rows' weight loads of a step issued
// before either is applied; each row's sums are row_dot_80_sub16's, in its order (the same bits).
template<int TY, int NC, bool EXACT_N = false, bool STAGE_GRID = false>
__device__ __forceinline__ void row_dot2_80_sub16(const uint8_t* ra, const uint8_t* rb, const block_q8_1* x, const int (&off)[NC],
                                                  int n, int t, float (&sa)[NC], float (&sb)[NC],
                                                  const uint32_t* __restrict__ s_grid = nullptr) {
    using F = Fmt<TY>;
    using S = Split<TY>;
#pragma unroll
    for (int c = 0; c < NC; ++c) { sa[c] = 0.0f; sb[c] = 0.0f; }
    for (int k = t; k < 80; k += 32) {
        const int kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
        const typename S::W wa = S::template load<STAGE_GRID>(ra, kbx, iqs, s_grid);
        const typename S::W wb = S::template load<STAGE_GRID>(rb, kbx, iqs, s_grid);
#pragma unroll
        for (int c = 0; c < NC; ++c)
            if (EXACT_N || c < n) {
                sa[c] += S::apply(wa, x + off[c] + kbx * (F::qk / 32), iqs);
                sb[c] += S::apply(wb, x + off[c] + kbx * (F::qk / 32), iqs);
            }
    }
    float a16[NC], b16[NC];
#pragma unroll
    for (int c = 0; c < NC; ++c) { a16[c] = 0.0f; b16[c] = 0.0f; }
    for (int k = t + 16; k < 64; k += 32) {
        const int kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
        const typename S::W wa = S::template load<STAGE_GRID>(ra, kbx, iqs, s_grid);
        const typename S::W wb = S::template load<STAGE_GRID>(rb, kbx, iqs, s_grid);
#pragma unroll
        for (int c = 0; c < NC; ++c)
            if (EXACT_N || c < n) {
                a16[c] += S::apply(wa, x + off[c] + kbx * (F::qk / 32), iqs);
                b16[c] += S::apply(wb, x + off[c] + kbx * (F::qk / 32), iqs);
            }
    }
#pragma unroll
    for (int c = 0; c < NC; ++c) {
        if (EXACT_N || c < n) {
            sa[c] = __fadd_rn(sa[c], a16[c]);
            sb[c] = __fadd_rn(sb[c], b16[c]);
#pragma unroll
            for (int o = 8; o > 0; o >>= 1) {
                sa[c] += __shfl_xor_sync(0xffffffffu, sa[c], o);
                sb[c] += __shfl_xor_sync(0xffffffffu, sb[c], o);
            }
        }
    }
}

// The sign-mask tables that follow the codebook words in a staged IQ3_XXS table (see stage_iq_grid): the byte masks
// of every 7-bit sign field, two 128-word tables at +256.
__device__ __forceinline__ void stage_iq3xxs_masks(uint32_t* s_buf, int tid, int nthreads) {
    for (int u = tid; u < 128; u += nthreads) {
        const uint32_t s = (uint32_t) u | ((__popc((uint32_t) u) & 1u) << 7);   // 8th sign = parity of the 7
        uint32_t ml = 0, mh = 0;
#pragma unroll
        for (int i = 0; i < 4; ++i) {
            ml |= ((s >> i) & 1u) * (0xffu << (8 * i));
            mh |= ((s >> (4 + i)) & 1u) * (0xffu << (8 * i));
        }
        s_buf[256 + u] = ml;
        s_buf[384 + u] = mh;
    }
}
template<int TY, bool STAGE_GRID = kStageIqGrid<TY>>
__device__ __forceinline__ const uint32_t* stage_iq_grid(uint32_t* s_buf, int tid, int nthreads) {
    if constexpr (!STAGE_GRID) {
        return nullptr;
    } else {
        static_assert(TY == 18, "only IQ3_XXS stages a codebook");
        // 256 codebook words, then two 128-word tables of the per-byte sign masks (signs 0-3, signs 4-7).  A
        // mask depends only on the 7 bits it is unpacked from, and iq3xxs_grid's bytes are all >= 4, so the
        // consumer applies it as (g ^ mask) + (mask & 0x01010101): a negate per set byte, with no carry into
        // the next.  One pass per block replaces the per-lookup unpack_ksigns / __vcmpne4 / __vsub4 chain.
        // Ported from shinbunbun/llama-cpp-p100-patches (MIT), patches 29 `mmvq-iq3xxs-grid-smem` and 30
        // `mmvq-ksigns-smem`, measured there on a Tesla P100 (sm_60).
        for (int i = tid; i < 256; i += nthreads) s_buf[i] = iq3xxs_grid[i];
        stage_iq3xxs_masks(s_buf, tid, nthreads);
        __syncthreads();
        return s_buf;
    }
}

template<int TY, bool STAGE_GRID = kStageIqGrid<TY>>
struct IqGridWords {
    static constexpr int value = STAGE_GRID && TY == 18 ? 512 : 4;
};

// mmvq_kernel with the columns taken NC at a time.  After warp_sum every lane holds the same sum, so lane c stores
// column c.
// #778: `__shared__ alignas(16)` does not compile for gfx1030 (HIP); the attribute after the declarator does, but nvcc
// with MSVC as host does not take it.  Same layout either way.
#define STRATA_SHARED_ALIGN16_U32(name, ...) __shared__ alignas(16) uint32_t name[__VA_ARGS__]
template<int TY, int NC, bool STAGE_GRID = kStageIqGrid<TY>, bool EXACT_N = false>
__global__ void __launch_bounds__(128) mmvq_multi_kernel(const uint8_t* __restrict__ w, size_t row_bytes,
                                                         const block_q8_1* __restrict__ x, float* __restrict__ y,
                                                         int n_in, int n_out, int ncols) {
    STRATA_SHARED_ALIGN16_U32(s_grid_buf, IqGridWords<TY, STAGE_GRID>::value);
    const uint32_t* s_grid = stage_iq_grid<TY, STAGE_GRID>(s_grid_buf, threadIdx.y * 32 + threadIdx.x, 128);
    const int row = blockIdx.x * 4 + threadIdx.y;
    if (row >= n_out) return;
    const int lane = threadIdx.x;
    const int nb = n_in / Fmt<TY>::qk, xb = n_in / 32;
    const uint8_t* wr = w + (size_t) row * row_bytes;
    if constexpr (EXACT_N) {
        int off[NC];
#pragma unroll
        for (int c = 0; c < NC; ++c) off[c] = c * xb;
        float s[NC];
        row_dot_multi<TY, NC, true, STAGE_GRID>(wr, x, off, NC, nb, lane, s, s_grid);
#pragma unroll
        for (int c = 0; c < NC; ++c)
            if (lane == c) y[(size_t) c * n_out + row] = s[c];
    } else {
        for (int c0 = 0; c0 < ncols; c0 += NC) {
            const int n = min(NC, ncols - c0);
            int off[NC];
#pragma unroll
            for (int c = 0; c < NC; ++c) off[c] = (c0 + min(c, n - 1)) * xb;
            float s[NC];
            row_dot_multi<TY, NC, false, STAGE_GRID>(wr, x, off, n, nb, lane, s, s_grid);
#pragma unroll
            for (int c = 0; c < NC; ++c)
                if (c < n && lane == c) y[(size_t) (c0 + c) * n_out + row] = s[c];
        }
    }
}

// ---------------------------------------------------------------- grouped native experts
constexpr int GU_ROWS = 8;     // rows per block (one warp each)

// Group g is computed by block row g % gridDim.y: rows y, y + gridDim.y, ... < *n_groups.  The callers size the
// launch for the most groups a call can have (cap: every entry its own expert), but the count is only known on the
// device, and a verify window's PCIe call usually has no group at all: with one block row per possible group every
// unused one was cap x (2 n_ff / 8 + n_embd / 8) blocks that started only to return.  A smaller gridDim.y strides
// instead.  Each group's rows are computed by the same warp code in the same order either way, so the results do
// not depend on gridDim.y (gridDim.y = cap_groups is the one-row-per-group launch).
template<int TG>
__global__ void __launch_bounds__(256) native_gu_kernel(const unsigned long long* __restrict__ grp_ptr,
                                                        const int32_t* __restrict__ grp_start,
                                                        const int32_t* __restrict__ n_groups,
                                                        const int32_t* __restrict__ ent_tok,
                                                        const block_q8_1* __restrict__ xq, NativeExpertLayout L,
                                                        float* __restrict__ gate, float* __restrict__ up) {
    const int warp = threadIdx.x >> 5, lane = threadIdx.x & 31;
    const int row = blockIdx.x * GU_ROWS + warp;             // 0 .. 2*n_ff
    if (row >= 2 * L.n_ff) return;
    const bool is_up = row >= L.n_ff;
    const int r = is_up ? row - (int) L.n_ff : row;
    const size_t off = (is_up ? L.up_off : 0) + (size_t) r * L.gu_row;
    const int nb = (int) (L.n_embd / Fmt<TG>::qk), xb = (int) (L.n_embd / 32);
    const int ng = *n_groups;
    for (int g = blockIdx.y; g < ng; g += gridDim.y) {
        const uint8_t* blob = (const uint8_t*) grp_ptr[g];
        const uint8_t* wr = blob + off;
        const int e0 = grp_start[g], e1 = grp_start[g + 1];
        for (int e = e0; e < e1; ++e) {
            const float s = row_dot<TG>(wr, xq + (size_t) ent_tok[e] * xb, nb, lane);
            if (lane == 0) (is_up ? up : gate)[(size_t) e * L.n_ff + r] = s;
        }
    }
}

// native_gu_kernel with the group's entries taken GRP_NC at a time, each weight part decoded once per pass.
// A group has at most one entry per token of the window (kVerifyMaxT = 8; setup writes --spec 4, and a split window
// has halves of <= 4), so 4 takes such windows in one pass; 8 would take ~64-80 registers against ~48 (ptxas -v).
constexpr int GRP_NC = 4;

template<int TG, bool STAGE_GRID = kStageIqGrid<TG>, bool SUB16 = true>
__global__ void __launch_bounds__(256) native_gu_multi_kernel(const unsigned long long* __restrict__ grp_ptr,
                                                              const int32_t* __restrict__ grp_start,
                                                              const int32_t* __restrict__ n_groups,
                                                              const int32_t* __restrict__ ent_tok,
                                                              const block_q8_1* __restrict__ xq, NativeExpertLayout L,
                                                              float* __restrict__ gate, float* __restrict__ up) {
    const int ng = *n_groups;
    if (blockIdx.y >= ng) return;
    STRATA_SHARED_ALIGN16_U32(s_grid_buf, IqGridWords<TG, STAGE_GRID>::value);
    const uint32_t* s_grid = stage_iq_grid<TG, STAGE_GRID>(s_grid_buf, threadIdx.x, 256);
    const int nb = (int) (L.n_embd / Fmt<TG>::qk), xb = (int) (L.n_embd / 32);
    if constexpr (SUB16) {
        if (xb == 80 && nb * Fmt<TG>::ipb == 80) {
            const int subwarp = threadIdx.x >> 4, t = threadIdx.x & 15;
            const int row = blockIdx.x * 16 + subwarp;            // 0 .. 2*n_ff
            if (row >= 2 * L.n_ff) return;
            const bool is_up = row >= L.n_ff;
            const int r = is_up ? row - (int) L.n_ff : row;
            const size_t w_off = (is_up ? L.up_off : 0) + (size_t) r * L.gu_row;
            float* dst = is_up ? up : gate;
            for (int g = blockIdx.y; g < ng; g += gridDim.y) {
                const uint8_t* wr = (const uint8_t*) grp_ptr[g] + w_off;
                constexpr float sc = 1.0f;
                const int e0 = grp_start[g], e1 = grp_start[g + 1];
                for (int e = e0; e < e1; e += GRP_NC) {
                    const int n = min(GRP_NC, e1 - e);
                    if (n == 1) {
                        const int off1[1] = { ent_tok[e] * 80 };
                        float s1[1];
                        row_dot_80_sub16<TG, 1, true, STAGE_GRID>(wr, xq, off1, 1, t, s1, s_grid);
                        if (t == 0) dst[(size_t) e * L.n_ff + r] = s1[0] * sc;
                    } else if (n == 2) {
                        const int off2[2] = { ent_tok[e] * 80, ent_tok[e + 1] * 80 };
                        float s2[2];
                        row_dot_80_sub16<TG, 2, true, STAGE_GRID>(wr, xq, off2, 2, t, s2, s_grid);
                        if (t < 2) dst[(size_t) (e + t) * L.n_ff + r] = s2[t] * sc;
                    } else if (n == 3) {
                        const int off3[3] = { ent_tok[e] * 80, ent_tok[e + 1] * 80, ent_tok[e + 2] * 80 };
                        float s3[3];
                        row_dot_80_sub16<TG, 3, true, STAGE_GRID>(wr, xq, off3, 3, t, s3, s_grid);
                        if (t < 3) dst[(size_t) (e + t) * L.n_ff + r] = s3[t] * sc;
                    } else {
                        const int off4[4] = { ent_tok[e] * 80, ent_tok[e + 1] * 80, ent_tok[e + 2] * 80, ent_tok[e + 3] * 80 };
                        float s4[4];
                        row_dot_80_sub16<TG, 4, true, STAGE_GRID>(wr, xq, off4, 4, t, s4, s_grid);
                        if (t < 4) dst[(size_t) (e + t) * L.n_ff + r] = s4[t] * sc;
                    }
                }
            }
            return;
        }
    }
    const int warp = threadIdx.x >> 5, lane = threadIdx.x & 31;
    for (int rc = blockIdx.x; ; rc += gridDim.x) {
    const int row = rc * GU_ROWS + warp;             // 0 .. 2*n_ff
    if (row >= 2 * L.n_ff) break;
    const bool is_up = row >= L.n_ff;
    const int r = is_up ? row - (int) L.n_ff : row;
    const size_t w_off = (is_up ? L.up_off : 0) + (size_t) r * L.gu_row;
    float* dst = is_up ? up : gate;
    for (int g = blockIdx.y; g < ng; g += gridDim.y) {   // the group stride, as native_gu_kernel
        const uint8_t* wr = (const uint8_t*) grp_ptr[g] + w_off;
        constexpr float sc = 1.0f;
        const int e0 = grp_start[g], e1 = grp_start[g + 1];
        for (int e = e0; e < e1; e += GRP_NC) {
            const int n = min(GRP_NC, e1 - e);
            if (n == 1) {
                const int off1[1] = { ent_tok[e] * xb };
                float s1[1];
                row_dot_multi<TG, 1, true, STAGE_GRID>(wr, xq, off1, 1, nb, lane, s1, s_grid);
                if (lane == 0) dst[(size_t) e * L.n_ff + r] = s1[0] * sc;
            } else if (n == 2) {
                const int off2[2] = { ent_tok[e] * xb, ent_tok[e + 1] * xb };
                float s2[2];
                row_dot_multi<TG, 2, true, STAGE_GRID>(wr, xq, off2, 2, nb, lane, s2, s_grid);
                if (lane < 2) dst[(size_t) (e + lane) * L.n_ff + r] = s2[lane] * sc;
            } else if (n == 3) {
                const int off3[3] = { ent_tok[e] * xb, ent_tok[e + 1] * xb, ent_tok[e + 2] * xb };
                float s3[3];
                row_dot_multi<TG, 3, true, STAGE_GRID>(wr, xq, off3, 3, nb, lane, s3, s_grid);
                if (lane < 3) dst[(size_t) (e + lane) * L.n_ff + r] = s3[lane] * sc;
            } else {
                const int off4[4] = { ent_tok[e] * xb, ent_tok[e + 1] * xb, ent_tok[e + 2] * xb, ent_tok[e + 3] * xb };
                float s4[4];
                row_dot_multi<TG, 4, true, STAGE_GRID>(wr, xq, off4, 4, nb, lane, s4, s_grid);
                if (lane < 4) dst[(size_t) (e + lane) * L.n_ff + r] = s4[lane] * sc;
            }
        }
    }
    break;
    }
}

__global__ void swiglu_entries_kernel(const float* __restrict__ gate, const float* __restrict__ up, float* __restrict__ h,
                                      long long n) {
    const long long i = (long long) blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float g = gate[i];
    h[i] = (g / (1.0f + __expf(-g))) * up[i];
}

template<int TD>
__global__ void __launch_bounds__(256) native_down_kernel(const unsigned long long* __restrict__ grp_ptr,
                                                          const int32_t* __restrict__ grp_start,
                                                          const int32_t* __restrict__ n_groups,
                                                          const int32_t* __restrict__ ent_dst,
                                                          const block_q8_1* __restrict__ hq, NativeExpertLayout L,
                                                          float* __restrict__ out) {
    const int warp = threadIdx.x >> 5, lane = threadIdx.x & 31;
    const int r = blockIdx.x * 8 + warp;
    if (r >= L.n_embd) return;
    const size_t off = L.down_off + (size_t) r * L.d_row;
    const int nb = (int) (L.n_ff / Fmt<TD>::qk), hb = (int) (L.n_ff / 32);
    const int ng = *n_groups;
    for (int g = blockIdx.y; g < ng; g += gridDim.y) {   // as native_gu_kernel
        const uint8_t* blob = (const uint8_t*) grp_ptr[g];
        const uint8_t* wr = blob + off;
        constexpr float sd = 1.0f;
        const int e0 = grp_start[g], e1 = grp_start[g + 1];
        for (int e = e0; e < e1; ++e) {
            const float s = row_dot<TD>(wr, hq + (size_t) e * hb, nb, lane);
            if (lane == 0) out[(size_t) ent_dst[e] * L.n_embd + r] = s * sd;
        }
    }
}

// the 8-lane IQ4_NL down rows: K = 40 dots.
template<int TD, int NC, bool EXACT_N = false, bool STAGE_GRID = false>
__device__ __forceinline__ void row_dot_sub8(const uint8_t* row, const block_q8_1* x, const int (&off)[NC], int n, int t,
                                             float (&s)[NC], const uint32_t* __restrict__ s_grid = nullptr) {
    row_dot_40_sub8<TD, NC, EXACT_N, STAGE_GRID>(row, x, off, n, t, s, s_grid);
}

// native_down_kernel with the entries taken GRP_NC at a time
template<int TD, bool SUB_DOWN = true>
__global__ void __launch_bounds__(256) native_down_multi_kernel(const unsigned long long* __restrict__ grp_ptr,
                                                                const int32_t* __restrict__ grp_start,
                                                                const int32_t* __restrict__ n_groups,
                                                                const int32_t* __restrict__ ent_dst,
                                                                const block_q8_1* __restrict__ hq, NativeExpertLayout L,
                                                                float* __restrict__ out) {
    const int ng = *n_groups;
    if (blockIdx.y >= ng) return;
    __shared__ uint32_t s_hq_buf[GRP_NC * 20 * 9];
    const int nb = (int) (L.n_ff / Fmt<TD>::qk), hb = (int) (L.n_ff / 32);
    if constexpr (SUB_DOWN && TD == 20) {
        if (hb == 20) {
            const int r = blockIdx.x * 32 + (threadIdx.x >> 3);
            const int t = threadIdx.x & 7;
            const bool valid_r = (r < L.n_embd);
            const size_t w_off = L.down_off + (size_t) (valid_r ? r : 0) * L.d_row;
            const block_q8_1* s_hq = reinterpret_cast<const block_q8_1*>(s_hq_buf);
            for (int g = blockIdx.y; g < ng; g += gridDim.y) {
                const uint8_t* wr = (const uint8_t*) grp_ptr[g] + w_off;
                constexpr float sd = 1.0f;
                const int e0 = grp_start[g], e1 = grp_start[g + 1];
                for (int e = e0; e < e1; e += GRP_NC) {
                    const int n = min(GRP_NC, e1 - e);
                    const uint32_t* src = reinterpret_cast<const uint32_t*>(hq + (size_t) e * 20);
                    const int words = n * (20 * 9);
                    __syncthreads();
                    for (int i = threadIdx.x; i < words; i += 256) s_hq_buf[i] = src[i];
                    __syncthreads();
                    if (valid_r) {
                        if (n == 1) {
                            const int off1[1] = { 0 };
                            float s1[1];
                            row_dot_sub8<TD, 1, true>(wr, s_hq, off1, 1, t, s1);
                            if (t == 0) out[(size_t) ent_dst[e] * L.n_embd + r] = s1[0] * sd;
                        } else if (n == 2) {
                            const int off2[2] = { 0, 20 };
                            float s2[2];
                            row_dot_sub8<TD, 2, true>(wr, s_hq, off2, 2, t, s2);
                            if (t < 2) out[(size_t) ent_dst[e + t] * L.n_embd + r] = s2[t] * sd;
                        } else if (n == 3) {
                            const int off3[3] = { 0, 20, 40 };
                            float s3[3];
                            row_dot_sub8<TD, 3, true>(wr, s_hq, off3, 3, t, s3);
                            if (t < 3) out[(size_t) ent_dst[e + t] * L.n_embd + r] = s3[t] * sd;
                        } else {
                            const int off4[4] = { 0, 20, 40, 60 };
                            float s4[4];
                            row_dot_sub8<TD, 4, true>(wr, s_hq, off4, 4, t, s4);
                            if (t < 4) out[(size_t) ent_dst[e + t] * L.n_embd + r] = s4[t] * sd;
                        }
                    }
                }
            }
            return;
        }
    } else if constexpr (SUB_DOWN && TD == 42) {
        if (hb == 20) {
            const int r = blockIdx.x * 16 + (threadIdx.x >> 4);
            const int t = threadIdx.x & 15;
            const bool valid_r = (r < L.n_embd);
            const size_t w_off = L.down_off + (size_t) (valid_r ? r : 0) * L.d_row;
            const block_q8_1* s_hq = reinterpret_cast<const block_q8_1*>(s_hq_buf);
            for (int g = blockIdx.y; g < ng; g += gridDim.y) {
                const uint8_t* wr = (const uint8_t*) grp_ptr[g] + w_off;
                constexpr float sd = 1.0f;
                const int e0 = grp_start[g], e1 = grp_start[g + 1];
                for (int e = e0; e < e1; e += GRP_NC) {
                    const int n = min(GRP_NC, e1 - e);
                    const uint32_t* src = reinterpret_cast<const uint32_t*>(hq + (size_t) e * 20);
                    const int words = n * (20 * 9);
                    __syncthreads();
                    for (int i = threadIdx.x; i < words; i += 256) s_hq_buf[i] = src[i];
                    __syncthreads();
                    if (valid_r) {
                        if (n == 1) {
                            const int off1[1] = { 0 };
                            float s1[1];
                            row_dot_20_sub16<TD, 1, true>(wr, s_hq, off1, 1, t, s1);
                            if (t == 0) out[(size_t) ent_dst[e] * L.n_embd + r] = s1[0] * sd;
                        } else if (n == 2) {
                            const int off2[2] = { 0, 20 };
                            float s2[2];
                            row_dot_20_sub16<TD, 2, true>(wr, s_hq, off2, 2, t, s2);
                            if (t < 2) out[(size_t) ent_dst[e + t] * L.n_embd + r] = s2[t] * sd;
                        } else if (n == 3) {
                            const int off3[3] = { 0, 20, 40 };
                            float s3[3];
                            row_dot_20_sub16<TD, 3, true>(wr, s_hq, off3, 3, t, s3);
                            if (t < 3) out[(size_t) ent_dst[e + t] * L.n_embd + r] = s3[t] * sd;
                        } else {
                            const int off4[4] = { 0, 20, 40, 60 };
                            float s4[4];
                            row_dot_20_sub16<TD, 4, true>(wr, s_hq, off4, 4, t, s4);
                            if (t < 4) out[(size_t) ent_dst[e + t] * L.n_embd + r] = s4[t] * sd;
                        }
                    }
                }
            }
            return;
        }
    }
    const int warp = threadIdx.x >> 5, lane = threadIdx.x & 31;
    const int r = blockIdx.x * 8 + warp;
    const bool valid_r = (r < L.n_embd);
    const size_t w_off = L.down_off + (size_t) (valid_r ? r : 0) * L.d_row;
    for (int g = blockIdx.y; g < ng; g += gridDim.y) {   // the group stride, as native_gu_kernel
        const uint8_t* wr = (const uint8_t*) grp_ptr[g] + w_off;
        constexpr float sd = 1.0f;
        const int e0 = grp_start[g], e1 = grp_start[g + 1];
        if (hb == 20) {
            const block_q8_1* s_hq = reinterpret_cast<const block_q8_1*>(s_hq_buf);
            for (int e = e0; e < e1; e += GRP_NC) {
                const int n = min(GRP_NC, e1 - e);
                const uint32_t* src = reinterpret_cast<const uint32_t*>(hq + (size_t) e * 20);
                const int words = n * (20 * 9);
                __syncthreads();
                for (int i = threadIdx.x; i < words; i += 256) s_hq_buf[i] = src[i];
                __syncthreads();
                if (valid_r) {
                    if (n == 1) {
                        const int off1[1] = { 0 };
                        float s1[1];
                        row_dot_multi<TD, 1, true>(wr, s_hq, off1, 1, nb, lane, s1);
                        if (lane == 0) out[(size_t) ent_dst[e] * L.n_embd + r] = s1[0] * sd;
                    } else if (n == 2) {
                        const int off2[2] = { 0, 20 };
                        float s2[2];
                        row_dot_multi<TD, 2, true>(wr, s_hq, off2, 2, nb, lane, s2);
                        if (lane < 2) out[(size_t) ent_dst[e + lane] * L.n_embd + r] = s2[lane] * sd;
                    } else {
                        int off[GRP_NC];
#pragma unroll
                        for (int c = 0; c < GRP_NC; ++c) off[c] = min(c, n - 1) * 20;
                        float s[GRP_NC];
                        row_dot_multi<TD, GRP_NC, false>(wr, s_hq, off, n, nb, lane, s);
#pragma unroll
                        for (int c = 0; c < GRP_NC; ++c)
                            if (c < n && lane == c) out[(size_t) ent_dst[e + c] * L.n_embd + r] = s[c] * sd;
                    }
                }
            }
        } else if (valid_r) {
            for (int e = e0; e < e1; e += GRP_NC) {
                const int n = min(GRP_NC, e1 - e);
                int off[GRP_NC];
#pragma unroll
                for (int c = 0; c < GRP_NC; ++c) off[c] = (e + min(c, n - 1)) * hb;
                float s[GRP_NC];
                row_dot_multi<TD, GRP_NC, false>(wr, hq, off, n, nb, lane, s);
#pragma unroll
                for (int c = 0; c < GRP_NC; ++c)
                    if (c < n && lane == c) out[(size_t) ent_dst[e + c] * L.n_embd + r] = s[c] * sd;
            }
        }
    }
}

// ---------------------------------------------------------------- EXPDEC (STRATA_EXPDEC=1): the group's activations
// in shared memory, R rows per lane
//
// native_gu_multi_kernel / native_down_multi_kernel decode each weight part once and apply it to every entry of the
// group, but the gate/up apply re-reads the entry's q8_1 block from global memory with 9 scattered 4-byte loads
// (36-byte blocks: a warp's load touches 32 sectors), so an entry costs about 60% of a whole expert (nsys, 2080 Ti,
// L02 at 131k: ~2.6 us per expert and ~1.6 us per entry and layer) although it reads nothing from DRAM.  Here a
// block stages its group's entries (ED_NC at a time, as the default) in shared memory laid out so that a call's
// words are two 16-byte loads (gate/up, Q2_0 down: the block's two halves in separate planes, conflict-free) or two
// 8-byte loads (IQ4_NL down), the fp16 block scale already a float; every lane computes R rows at once, so the
// activation words are loaded once per R rows; the IQ3_XXS codebook and sign masks are staged too, and a block takes
// `rb` rows (auto: the most that keep ~STRATA_EXPDEC_MINBLK blocks in flight) instead of 16.
// Each (row, entry) sum is the default kernel's sum tree (row_dot_80_sub16 / row_dot_40_sub8 / row_dot_20_sub16: the
// same lane does the same calls in the same order with the same operations, `apply_u` = `apply` on the block's
// words), so the output is bitwise the same (expdec_bench checks it; docs/EXPERTDEC.md).
#ifndef STRATA_EXPDEC_MINB
#define STRATA_EXPDEC_MINB 4   // gate/up __launch_bounds__ min blocks per SM by default (STRATA_EXPDEC_MINB at runtime): 4 = up to 64
                               // registers, as the default kernels (bench: equal on the 2080 Ti, 0.87-0.95 on the 3060); 2 = up to 128
#endif
constexpr int ED_NC = GRP_NC;   // entries staged at once
constexpr int ED_XB = 80;       // q8_1 blocks of a gate/up row (n_embd 2560)
constexpr int ED_HB = 20;       // q8_1 blocks of a down row (n_ff 640)

// the codebook of a gate/up format in shared memory: words, and the copy (every thread of the block; sync after)
template<int TG> struct EdGrid;
// IQ3_XXS: the codebook and, behind it, the sign-mask tables Split<18>::load<true> reads (stage_iq_grid's layout).
template<> struct EdGrid<18> { static constexpr int words = 512;
    __device__ static void copy(uint32_t* s, int tid) {
        for (int i = tid; i < 256; i += 256) s[i] = iq3xxs_grid[i];
        stage_iq3xxs_masks(s, tid, 256);
    } };

// One entry's q8_1 row (XB blocks of 36 bytes, 4-byte aligned) into shared memory as entry c: the 8 words of block k
// in planes (HALVES: s_u[(2 c + h) XB + k] = words 4h..4h+3, two 16-byte loads per call, conflict-free over 16
// consecutive k; else s_u[(c XB + k) 2 + h], the block's 32 bytes together, for the IQ4_NL pairs) and the fp16 scale
// as s_ds[c XB + k] = __low2float(ds), the value `apply` computes.  All 256 threads.
template<int XB, bool HALVES>
__device__ __forceinline__ void ed_stage_entry(int4* __restrict__ s_u, float* __restrict__ s_ds, int c,
                                               const block_q8_1* __restrict__ src, int tid) {
    int* su = (int*) s_u;
    const int* w32 = (const int*) src;
    for (int w = tid; w < XB * 9; w += 256) {
        const int k = w / 9, j = w - k * 9;
        const int v = w32[w];
        if (j == 0) {
            const half2 h = *reinterpret_cast<const half2*>(&v);
            s_ds[c * XB + k] = __low2float(h);
        } else {
            const int q = j - 1;
            if constexpr (HALVES) su[((c * 2 + (q >> 2)) * XB + k) * 4 + (q & 3)] = v;
            else su[(c * XB + k) * 8 + q] = v;
        }
    }
}
template<int XB>
__device__ __forceinline__ void ed_load_u(const int4* __restrict__ s_u, int c, int k, int (&u)[8]) {
    const int4 a = s_u[(c * 2 + 0) * XB + k], b = s_u[(c * 2 + 1) * XB + k];
    u[0] = a.x; u[1] = a.y; u[2] = a.z; u[3] = a.w;
    u[4] = b.x; u[5] = b.y; u[6] = b.z; u[7] = b.w;
}

// R gate/up rows against the NC staged entries, lane t of a 16-lane sub-warp: row_dot_80_sub16's tree (calls t,
// t+32, t+64 into s; t+16, t+48 into s16; s = __fadd_rn(s, s16); xor 8-4-2-1), every call's weight part decoded once
// per row and the entry's block loaded once per NC x R products.
template<int TG, int R, int NC>
__device__ __forceinline__ void ed_gu_rows(const uint8_t* const* wr, const int4* __restrict__ s_u,
                                           const float* __restrict__ s_ds, const uint32_t* __restrict__ s_grid, int t,
                                           float (&s)[R][NC]) {
    using F = Fmt<TG>;
    using S = Split<TG>;
    float s16[R][NC];
#pragma unroll
    for (int r = 0; r < R; ++r)
#pragma unroll
        for (int c = 0; c < NC; ++c) { s[r][c] = 0.0f; s16[r][c] = 0.0f; }
#pragma unroll
    for (int j = 0; j < 3; ++j) {
        const int k = t + 32 * j;
        const int kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
        typename S::W w[R];
#pragma unroll
        for (int r = 0; r < R; ++r) w[r] = S::template load<true>(wr[r], kbx, iqs, s_grid);
#pragma unroll
        for (int c = 0; c < NC; ++c) {
            int u[8];
            ed_load_u<ED_XB>(s_u, c, k, u);
            const float d8 = s_ds[c * ED_XB + k];
#pragma unroll
            for (int r = 0; r < R; ++r) s[r][c] += S::apply_u(w[r], u, d8);
        }
    }
#pragma unroll
    for (int j = 0; j < 2; ++j) {
        const int k = t + 16 + 32 * j;
        const int kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
        typename S::W w[R];
#pragma unroll
        for (int r = 0; r < R; ++r) w[r] = S::template load<true>(wr[r], kbx, iqs, s_grid);
#pragma unroll
        for (int c = 0; c < NC; ++c) {
            int u[8];
            ed_load_u<ED_XB>(s_u, c, k, u);
            const float d8 = s_ds[c * ED_XB + k];
#pragma unroll
            for (int r = 0; r < R; ++r) s16[r][c] += S::apply_u(w[r], u, d8);
        }
    }
#pragma unroll
    for (int r = 0; r < R; ++r)
#pragma unroll
        for (int c = 0; c < NC; ++c) {
            s[r][c] = __fadd_rn(s[r][c], s16[r][c]);
#pragma unroll
            for (int o = 8; o > 0; o >>= 1) s[r][c] += __shfl_xor_sync(0xffffffffu, s[r][c], o);
        }
}

// gate/up: 16 sub-warps of 16 lanes, R rows each = 16 R rows per pass, `rb` rows per block (a multiple of 16 R; 2 n_ff
// is one too, so a pass is whole or absent for the entire block).  Group stride as native_gu_multi_kernel.
template<int TG, int R, int MINB>
__global__ void __launch_bounds__(256, MINB) expdec_gu_kernel(const unsigned long long* __restrict__ grp_ptr,
                                                                            const int32_t* __restrict__ grp_start,
                                                                            const int32_t* __restrict__ n_groups,
                                                                            const int32_t* __restrict__ ent_tok,
                                                                            const block_q8_1* __restrict__ xq, NativeExpertLayout L,
                                                                            float* __restrict__ gate, float* __restrict__ up, int rb) {
    const int ng = *n_groups;
    if (blockIdx.y >= ng) return;
    __shared__ alignas(16) int4 s_u[ED_NC * 2 * ED_XB];
    __shared__ float s_ds[ED_NC * ED_XB];
    STRATA_SHARED_ALIGN16_U32(s_grid_buf, EdGrid<TG>::words);
    const int tid = threadIdx.x;
    EdGrid<TG>::copy(s_grid_buf, tid);   // visible after the staging's __syncthreads
    const int sw = tid >> 4, t = tid & 15;
    const int nrow = 2 * (int) L.n_ff, n_ff = (int) L.n_ff;
    const int row_base = blockIdx.x * rb;
    for (int g = blockIdx.y; g < ng; g += gridDim.y) {
        const uint8_t* blob = (const uint8_t*) grp_ptr[g];
        const int e0 = grp_start[g], e1 = grp_start[g + 1];
        for (int e = e0; e < e1; e += ED_NC) {
            const int n = min(ED_NC, e1 - e);
            __syncthreads();
            for (int c = 0; c < n; ++c) ed_stage_entry<ED_XB, true>(s_u, s_ds, c, xq + (size_t) ent_tok[e + c] * ED_XB, tid);
            __syncthreads();
            for (int p = 0; p * 16 * R < rb; ++p) {
                const int row0 = row_base + p * 16 * R + sw * R;
                if (row0 >= nrow) break;   // uniform over the block
                const bool is_up = row0 >= n_ff;
                const int r0 = is_up ? row0 - n_ff : row0;
                const uint8_t* wr[R];
#pragma unroll
                for (int r = 0; r < R; ++r) wr[r] = blob + (is_up ? L.up_off : 0) + (size_t) (r0 + r) * L.gu_row;
                float* dst = is_up ? up : gate;
                auto run = [&](auto nc) {
                    constexpr int NC = decltype(nc)::value;
                    float s[R][NC];
                    ed_gu_rows<TG, R, NC>(wr, s_u, s_ds, s_grid_buf, t, s);
#pragma unroll
                    for (int c = 0; c < NC; ++c)
                        if (t == c)
#pragma unroll
                            for (int r = 0; r < R; ++r) dst[(size_t) (e + c) * n_ff + r0 + r] = s[r][c];
                };
                switch (n) {
                    case 1: run(std::integral_constant<int, 1>{}); break;
                    case 2: run(std::integral_constant<int, 2>{}); break;
                    case 3: run(std::integral_constant<int, 3>{}); break;
                    default: run(std::integral_constant<int, 4>{}); break;
                }
            }
        }
    }
}

// R IQ4_NL down rows, lane t of an 8-lane sub-warp: row_dot_40_sub8's tree.  s_p: the block's 8 words together
// (ed_stage_entry<ED_HB, false>), read as the int2 pairs (iqs, iqs + 1) and (iqs + 4, iqs + 5).
template<int R, int NC>
__device__ __forceinline__ void ed_d20_rows(const uint8_t* const* wr, const int2* __restrict__ s_p,
                                            const float* __restrict__ s_ds, int t, float (&s)[R][NC]) {
    using F = Fmt<20>;
    using S = Split<20>;
    auto term = [&](int k, int r, int c, const typename S::W& w) -> float {
        const int kbx = k / F::ipb, par = k & 1;
        const int2 p0 = s_p[(c * ED_HB + kbx) * 4 + par], p1 = s_p[(c * ED_HB + kbx) * 4 + 2 + par];
        return S::apply_u(w, p0, p1, s_ds[c * ED_HB + kbx]);
    };
    auto loadw = [&](int k, typename S::W (&w)[R]) {
        const int kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
#pragma unroll
        for (int r = 0; r < R; ++r) w[r] = S::load(wr[r], kbx, iqs);
    };
#pragma unroll
    for (int r = 0; r < R; ++r)
#pragma unroll
        for (int c = 0; c < NC; ++c) s[r][c] = 0.0f;
#pragma unroll
    for (int j = 0; j < 2; ++j) {   // k = t, t + 32
        const int k = t + 32 * j;
        typename S::W w[R];
        loadw(k, w);
#pragma unroll
        for (int c = 0; c < NC; ++c)
#pragma unroll
            for (int r = 0; r < R; ++r) s[r][c] += term(k, r, c, w[r]);
    }
    {
        const int k = t + 16;
        typename S::W w[R];
        loadw(k, w);
#pragma unroll
        for (int c = 0; c < NC; ++c)
#pragma unroll
            for (int r = 0; r < R; ++r) {
                float s16 = 0.0f;
                s16 += term(k, r, c, w[r]);
                s[r][c] = __fadd_rn(s[r][c], s16);
            }
    }
    float s8[R][NC];
#pragma unroll
    for (int r = 0; r < R; ++r)
#pragma unroll
        for (int c = 0; c < NC; ++c) s8[r][c] = 0.0f;
    {
        const int k = t + 8;
        typename S::W w[R];
        loadw(k, w);
#pragma unroll
        for (int c = 0; c < NC; ++c)
#pragma unroll
            for (int r = 0; r < R; ++r) s8[r][c] += term(k, r, c, w[r]);
    }
    {
        const int k = t + 24;
        typename S::W w[R];
        loadw(k, w);
#pragma unroll
        for (int c = 0; c < NC; ++c)
#pragma unroll
            for (int r = 0; r < R; ++r) {
                float s24 = 0.0f;
                s24 += term(k, r, c, w[r]);
                s[r][c] = __fadd_rn(s[r][c], __fadd_rn(s8[r][c], s24));
            }
    }
#pragma unroll
    for (int r = 0; r < R; ++r)
#pragma unroll
        for (int c = 0; c < NC; ++c)
#pragma unroll
            for (int o = 4; o > 0; o >>= 1) s[r][c] += __shfl_xor_sync(0xffffffffu, s[r][c], o);
}

// R Q2_0 down rows, lane t of a 16-lane sub-warp: row_dot_20_sub16's tree (call t; call t + 16 for t < 4 into s16).
template<int R, int NC>
__device__ __forceinline__ void ed_d42_rows(const uint8_t* const* wr, const int4* __restrict__ s_u,
                                            const float* __restrict__ s_ds, int t, float (&s)[R][NC]) {
    using F = Fmt<42>;
    using S = Split<42>;
    {
        const int k = t, kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
        typename S::W w[R];
#pragma unroll
        for (int r = 0; r < R; ++r) w[r] = S::load(wr[r], kbx, iqs);
#pragma unroll
        for (int c = 0; c < NC; ++c) {
            int u[8];
            ed_load_u<ED_HB>(s_u, c, k, u);
            const float d8 = s_ds[c * ED_HB + k];
#pragma unroll
            for (int r = 0; r < R; ++r) { s[r][c] = 0.0f; s[r][c] += S::apply_u(w[r], u, d8); }
        }
    }
    float s16[R][NC];
#pragma unroll
    for (int r = 0; r < R; ++r)
#pragma unroll
        for (int c = 0; c < NC; ++c) s16[r][c] = 0.0f;
    if (t < 4) {
        const int k = t + 16, kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
        typename S::W w[R];
#pragma unroll
        for (int r = 0; r < R; ++r) w[r] = S::load(wr[r], kbx, iqs);
#pragma unroll
        for (int c = 0; c < NC; ++c) {
            int u[8];
            ed_load_u<ED_HB>(s_u, c, k, u);
            const float d8 = s_ds[c * ED_HB + k];
#pragma unroll
            for (int r = 0; r < R; ++r) s16[r][c] += S::apply_u(w[r], u, d8);
        }
    }
#pragma unroll
    for (int r = 0; r < R; ++r)
#pragma unroll
        for (int c = 0; c < NC; ++c) {
            s[r][c] = __fadd_rn(s[r][c], s16[r][c]);
#pragma unroll
            for (int o = 8; o > 0; o >>= 1) s[r][c] += __shfl_xor_sync(0xffffffffu, s[r][c], o);
        }
}

// down (IQ4_NL: 32 sub-warps of 8 lanes; Q2_0: 16 of 16), R rows per lane, `rb` rows per block (a multiple of the
// rows per pass; n_embd too).  The entries' h rows are contiguous in hq (entry e at e * ED_HB).
template<int TD, int R>
__global__ void __launch_bounds__(256, STRATA_EXPDEC_MINB) expdec_down_kernel(const unsigned long long* __restrict__ grp_ptr,
                                                                              const int32_t* __restrict__ grp_start,
                                                                              const int32_t* __restrict__ n_groups,
                                                                              const int32_t* __restrict__ ent_dst,
                                                                              const block_q8_1* __restrict__ hq, NativeExpertLayout L,
                                                                              float* __restrict__ out, int rb) {
    const int ng = *n_groups;
    if (blockIdx.y >= ng) return;
    constexpr int LANES = TD == 20 ? 8 : 16;
    constexpr int RPP = (256 / LANES) * R;   // rows per pass
    __shared__ alignas(16) int4 s_u[ED_NC * 2 * ED_HB];
    __shared__ float s_ds[ED_NC * ED_HB];
    const int tid = threadIdx.x;
    const int sw = tid / LANES, t = tid % LANES;
    const int nrow = (int) L.n_embd;
    const int row_base = blockIdx.x * rb;
    for (int g = blockIdx.y; g < ng; g += gridDim.y) {
        const uint8_t* blob = (const uint8_t*) grp_ptr[g] + L.down_off;
        const int e0 = grp_start[g], e1 = grp_start[g + 1];
        for (int e = e0; e < e1; e += ED_NC) {
            const int n = min(ED_NC, e1 - e);
            __syncthreads();
            for (int c = 0; c < n; ++c) ed_stage_entry<ED_HB, TD == 42>(s_u, s_ds, c, hq + (size_t) (e + c) * ED_HB, tid);
            __syncthreads();
            for (int p = 0; p * RPP < rb; ++p) {
                const int row0 = row_base + p * RPP + sw * R;
                if (row0 >= nrow) break;   // uniform over the block
                const uint8_t* wr[R];
#pragma unroll
                for (int r = 0; r < R; ++r) wr[r] = blob + (size_t) (row0 + r) * L.d_row;
                auto run = [&](auto nc) {
                    constexpr int NC = decltype(nc)::value;
                    float s[R][NC];
                    if constexpr (TD == 20) ed_d20_rows<R, NC>(wr, (const int2*) s_u, s_ds, t, s);
                    else ed_d42_rows<R, NC>(wr, s_u, s_ds, t, s);
#pragma unroll
                    for (int c = 0; c < NC; ++c)
                        if (t == c) {
                            const size_t o = (size_t) ent_dst[e + c] * L.n_embd + row0;
#pragma unroll
                            for (int r = 0; r < R; ++r) out[o + r] = s[r][c];
                        }
                };
                switch (n) {
                    case 1: run(std::integral_constant<int, 1>{}); break;
                    case 2: run(std::integral_constant<int, 2>{}); break;
                    case 3: run(std::integral_constant<int, 3>{}); break;
                    default: run(std::integral_constant<int, 4>{}); break;
                }
            }
        }
    }
}

// ---------------------------------------------------------------- q8_1 (quantize.cu)
// value i of a q8_1 row set (the warp holds block i / 32, lane = i % 32)
__device__ __forceinline__ void q8_1_store(const float xi, block_q8_1* __restrict__ y, const long long i) {
    float amax = fabsf(xi), sum = xi;
#pragma unroll
    for (int o = 16; o > 0; o >>= 1) {
        amax = fmaxf(amax, __shfl_xor_sync(0xffffffffu, amax, o));
        sum += __shfl_xor_sync(0xffffffffu, sum, o);
    }
    const float d = q8_1_finite(amax / 127.0f);   // #606: q8_1_finite.hpp - the same bits for every finite block
    const int8_t q = q8_1_quant(xi, d, amax);
    const long long ib = i / 32, iqs = i % 32;
    y[ib].qs[iqs] = q;
    if (iqs == 0) y[ib].ds = q8_1_ds(d, sum);
}

__global__ void quantize_q8_1_kernel(const float* __restrict__ x, block_q8_1* __restrict__ y, long long n) {
    const long long i = (long long) blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    q8_1_store(x[i], y, i);
}

// swiglu_entries_kernel and quantize_q8_1_kernel in one pass, over the call's own entries only:
// [grp_start[0], grp_start[*n_groups]) (a call's entries are contiguous; the verify window's PCIe call starts after
// its VRAM call) instead of all cap_entries twice - nothing reads the others: the down kernel reads its groups'.
// n_ff is a multiple of 32, so a warp is one q8_1 block and the bounds are warp-uniform.  The values are the two
// kernels': the SwiGLU product is rounded on its own - it must not contract into the first add of q8_1_store's block
// sum, as it could not when it went through memory (CUDA: __fmul_rn; HIP's __fmul_rn is a plain product, so there
// the product is written here under contract(off)) - then q8_1_store unchanged: the same blocks, bit for bit.
__global__ void __launch_bounds__(256) swiglu_q8_1_entries_kernel(const float* __restrict__ gate,
                                                                  const float* __restrict__ up,
                                                                  const int32_t* __restrict__ grp_start,
                                                                  const int32_t* __restrict__ n_groups, int n_ff,
                                                                  block_q8_1* __restrict__ hq) {
    const long long lo = (long long) grp_start[0] * n_ff, hi = (long long) grp_start[*n_groups] * n_ff;
    for (long long i = lo + (long long) blockIdx.x * blockDim.x + threadIdx.x; i < hi;
         i += (long long) gridDim.x * blockDim.x) {
        const float g = gate[i];
        const float h = __fmul_rn(g / (1.0f + __expf(-g)), up[i]);
        q8_1_store(h, hq, i);
    }
}

// 147-verify (STRATA_GU_SWIGLU=1): native_gu_multi_kernel's 16-lane gate and up rows, SwiGLU and the q8_1 blocks in ONE
// launch.  A block owns 32 consecutive hidden columns r of a group: its 32 sub-warps each compute gate row r and up row
// r (the same row_dot_80_sub16 calls, entries GRP_NC at a time), so the product
// h = silu(gate) * up is formed in the block and warp k quantizes entry k's 32 values with q8_1_store - the q8_1 block
// of those columns.  Every value is the separate kernels': the dots are the same calls, gate and up are the same floats
// (written to memory before), the product is rounded on its own as in swiglu_q8_1_entries_kernel, and q8_1_store is the
// same.  It saves the gate/up round trip through memory and one launch per layer.
template<int TG, bool STAGE_GRID>
__global__ void __launch_bounds__(512) native_gu_swiglu_kernel(const unsigned long long* __restrict__ grp_ptr,
                                                               const int32_t* __restrict__ grp_start,
                                                               const int32_t* __restrict__ n_groups,
                                                               const int32_t* __restrict__ ent_tok,
                                                               const block_q8_1* __restrict__ xq, NativeExpertLayout L,
                                                               block_q8_1* __restrict__ hq) {
    const int ng = *n_groups;
    if ((int) blockIdx.y >= ng) return;
    STRATA_SHARED_ALIGN16_U32(s_grid_buf, IqGridWords<TG, STAGE_GRID>::value);
    const uint32_t* s_grid = stage_iq_grid<TG, STAGE_GRID>(s_grid_buf, threadIdx.x, 512);
    __shared__ float sh[GRP_NC][32];
    const int sw = threadIdx.x >> 4, t = threadIdx.x & 15, warp = threadIdx.x >> 5, lane = threadIdx.x & 31;
    const int r = blockIdx.x * 32 + sw;                       // the hidden column (gate row r, up row r)
    const size_t g_off = (size_t) r * L.gu_row, u_off = L.up_off + (size_t) r * L.gu_row;
    for (int g = blockIdx.y; g < ng; g += gridDim.y) {
        const uint8_t* blob = (const uint8_t*) grp_ptr[g];
        constexpr float sg = 1.0f;
        constexpr float su = 1.0f;
        const int e0 = grp_start[g], e1 = grp_start[g + 1];
        for (int e = e0; e < e1; e += GRP_NC) {
            const int n = min(GRP_NC, e1 - e);
            float gv[GRP_NC], uv[GRP_NC];
            if (n == 1) {
                const int off1[1] = { ent_tok[e] * 80 };
                float a1[1], b1[1];
                row_dot2_80_sub16<TG, 1, true, STAGE_GRID>(blob + g_off, blob + u_off, xq, off1, 1, t, a1, b1, s_grid);
                gv[0] = a1[0]; uv[0] = b1[0];
            } else if (n == 2) {
                const int off2[2] = { ent_tok[e] * 80, ent_tok[e + 1] * 80 };
                float a2[2], b2[2];
                row_dot2_80_sub16<TG, 2, true, STAGE_GRID>(blob + g_off, blob + u_off, xq, off2, 2, t, a2, b2, s_grid);
#pragma unroll
                for (int c = 0; c < 2; ++c) { gv[c] = a2[c]; uv[c] = b2[c]; }
            } else if (n == 3) {
                const int off3[3] = { ent_tok[e] * 80, ent_tok[e + 1] * 80, ent_tok[e + 2] * 80 };
                float a3[3], b3[3];
                row_dot2_80_sub16<TG, 3, true, STAGE_GRID>(blob + g_off, blob + u_off, xq, off3, 3, t, a3, b3, s_grid);
#pragma unroll
                for (int c = 0; c < 3; ++c) { gv[c] = a3[c]; uv[c] = b3[c]; }
            } else {
                const int off4[4] = { ent_tok[e] * 80, ent_tok[e + 1] * 80, ent_tok[e + 2] * 80, ent_tok[e + 3] * 80 };
                float a4[4], b4[4];
                row_dot2_80_sub16<TG, 4, true, STAGE_GRID>(blob + g_off, blob + u_off, xq, off4, 4, t, a4, b4, s_grid);
#pragma unroll
                for (int c = 0; c < 4; ++c) { gv[c] = a4[c]; uv[c] = b4[c]; }
            }
            // sub-lane c holds entry c's sums (as the separate kernel's store): h of column r, entry c
#pragma unroll
            for (int c = 0; c < GRP_NC; ++c) {
                if (c < n && t == c) {
                    const float gg = gv[c] * sg, uu = uv[c] * su;
                    sh[c][sw] = __fmul_rn(gg / (1.0f + __expf(-gg)), uu);
                }
            }
            __syncthreads();
            if (warp < n) q8_1_store(sh[warp][lane], hq, (long long) (e + warp) * L.n_ff + (long long) blockIdx.x * 32 + lane);
            __syncthreads();
        }
    }
}

// ---------------------------------------------------------------- dequant (dequantize.cuh)
template<typename dst_t> __device__ __forceinline__ dst_t cvt(float v);
template<> __device__ __forceinline__ float cvt<float>(float v) { return v; }
template<> __device__ __forceinline__ __half cvt<__half>(float v) { return __float2half(v); }

template<typename dst_t>
__device__ void dq_iq3_xxs(const void* vx, int64_t ibs, dst_t* yy, int tid) {
    const block_iq3_xxs* x = (const block_iq3_xxs*) vx;
    const int64_t il = tid / 8, ib = tid % 8;
    dst_t* y = yy + 32 * ib + 8 * il;
    const uint8_t* q3 = x[ibs].qs + 8 * ib;
    const uint16_t* gas = (const uint16_t*) (x[ibs].qs + QK_K / 4) + 2 * ib;
    const uint8_t* grid1 = (const uint8_t*) (iq3xxs_grid + q3[2 * il + 0]);
    const uint8_t* grid2 = (const uint8_t*) (iq3xxs_grid + q3[2 * il + 1]);
    const uint32_t aux32 = gas[0] | (gas[1] << 16);
    const float d = (float) x[ibs].d * (0.5f + (aux32 >> 28)) * 0.5f;
    const uint8_t signs = ksigns_iq2xs[(aux32 >> 7 * il) & 127];
    for (int j = 0; j < 4; ++j) {
        y[j + 0] = cvt<dst_t>(d * grid1[j] * (signs & kmask_iq2xs[j + 0] ? -1.f : 1.f));
        y[j + 4] = cvt<dst_t>(d * grid2[j] * (signs & kmask_iq2xs[j + 4] ? -1.f : 1.f));
    }
}
template<typename dst_t>
__device__ void dq_iq4_nl(const void* vx, int64_t ibs, dst_t* yy, int tid) {
    const block_iq4_nl* x = (const block_iq4_nl*) vx + ibs * (QK_K / QK4_NL);
    const int64_t il = tid / 8, ib = tid % 8;
    dst_t* y = yy + 32 * ib + 4 * il;
    const uint8_t* q4 = x[ib].qs + 4 * il;
    const float d = (float) x[ib].d;
    for (int j = 0; j < 4; ++j) {
        y[j + 0] = cvt<dst_t>(d * kvalues_iq4nl[q4[j] & 0xf]);
        y[j + 16] = cvt<dst_t>(d * kvalues_iq4nl[q4[j] >> 4]);
    }
}
template<typename dst_t>
__device__ void dq_q2_0(const void* vx, int64_t ibs, dst_t* yy, int tid) {
    // one "superblock" = 256 values = 4 blocks of 64; thread tid writes 8 values
    const block_q2_0* x = (const block_q2_0*) vx + ibs * 4;
    const int b = tid / 8, part = tid % 8;          // block 0..3, 8 values each
    const float d = (float) x[b].d;
    for (int j = 0; j < 8; ++j) {
        const int i = part * 8 + j;
        const int code = (x[b].qs[i / 4] >> ((i % 4) * 2)) & 3;
        yy[b * 64 + i] = cvt<dst_t>(d * (float) (code - 1));
    }
}

__device__ void dq_q8_0(const void* vx, int64_t ibs, dst_t* yy, int tid) {
    const block_q8_0* x = (const block_q8_0*) vx + ibs * (QK_K / QK8_0);
    const int ib = tid % 8, il = tid / 8;
    const float d = __half2float(x[ib].d);
    dst_t* y = yy + 32 * ib + 8 * il;
    for (int j = 0; j < 8; ++j) y[j] = cvt<dst_t>((float) x[ib].qs[8 * il + j] * d);
}

// BF16 (the token embedding as the checkpoint ships it, tools/embd_bf16_pack.py): 8 values per thread.
template<typename dst_t>
__device__ void dq_bf16(const void* vx, int64_t ibs, dst_t* yy, int tid) {
    const uint16_t* x = (const uint16_t*) vx + ibs * 256 + tid * 8;
    for (int j = 0; j < 8; ++j) yy[tid * 8 + j] = cvt<dst_t>(__uint_as_float((uint32_t) x[j] << 16));
}

// Every type below must also be in is_iq() (BF16: embed_type_supported): the host entry points refuse the others,
// so the default is unreachable.
template<typename dst_t>
__device__ __forceinline__ void dq_dispatch(int ty, const void* vx, int64_t ibs, dst_t* y, int tid) {
    switch (ty) {
        case 18: dq_iq3_xxs(vx, ibs, y, tid); break;
        case 20: dq_iq4_nl(vx, ibs, y, tid); break;
        case 42: dq_q2_0(vx, ibs, y, tid); break;
        case 8: dq_q8_0(vx, ibs, y, tid); break;
        case 30: dq_bf16(vx, ibs, y, tid); break;
        default: break;
    }
}

// flat: superblock i -> y + 256 i
template<typename dst_t>
__global__ void dequant_flat_kernel(int ty, const void* __restrict__ vx, dst_t* __restrict__ y) {
    const int64_t i = blockIdx.x;
    dq_dispatch<dst_t>(ty, vx, i, y + i * QK_K, threadIdx.x);
}
// gate/up: superblock i of a role matrix (n_embd/256 per row) -> interleaved row 2r + parity
__global__ void dequant_gu_kernel(int ty, const void* __restrict__ gate, const void* __restrict__ up, int64_t per_row,
                                  __half* __restrict__ y) {
    const int64_t i = blockIdx.x;
    const int parity = blockIdx.y;
    const int64_t r = i / per_row, c = i % per_row;
    __half* dst = y + ((2 * r + parity) * per_row + c) * QK_K;
    dq_dispatch<__half>(ty, parity ? up : gate, i, dst, threadIdx.x);
}

// the types dq_dispatch dequantizes
bool is_iq(int t) {
    return t == 18 || t == 20 || t == 42 || t == 8;
}
// values per block of the types the grouped expert kernels take (0 = none)
int gu_qk(int t) {
    switch (t) {
#define STRATA_QK(T) case T: return Fmt<T>::qk;
        STRATA_GU_FMTS(STRATA_QK)
#undef STRATA_QK
        default: return 0;
    }
}
int d_qk(int t) {
    switch (t) {
#define STRATA_QK(T) case T: return Fmt<T>::qk;
        STRATA_D_FMTS(STRATA_QK)
#undef STRATA_QK
        default: return 0;
    }
}
bool gu_split(int t) {
    switch (t) {
#define STRATA_SP(T) case T: return kSplit<T>;
        STRATA_GU_FMTS(STRATA_SP)
#undef STRATA_SP
        default: return false;
    }
}

bool env_on(const char* name) {
    const char* v = std::getenv(name);
    return v != nullptr && v[0] != '\0' && v[0] != '0';
}
// STRATA_OLD_IQ_MMVQ=1 keeps the per-column kernels (bitwise equal to the new ones; kept for A/B timing)
bool g_old_kernels = env_on("STRATA_OLD_IQ_MMVQ");
bool g_no_sub16_gu = env_on("STRATA_NO_SUB16_GU");
// STRATA_IQ_STAGE_GRID=0 disables staging 64-bit i-quant codebook tables into shared memory
bool g_stage_grid = [] {
    const char* v = std::getenv("STRATA_IQ_STAGE_GRID");
    return v == nullptr || v[0] == '\0' || v[0] != '0';
}();
// The single-matrix mmvq (128-thread blocks, one per 4 rows) pays the 2-8 KB staging per block, while the grouped
// expert kernels gain. So mmvq stages only with STRATA_IQ_STAGE_GRID_MMVQ=1 (bitwise the same either way).
bool g_stage_grid_mmvq = g_stage_grid && [] {
    const char* v = std::getenv("STRATA_IQ_STAGE_GRID_MMVQ");
    return v != nullptr && v[0] == '1';
}();
// IQ3_XXS's codebook and sign-mask tables staged in shared memory by the grouped expert kernels (the
// single-matrix mmvq does not): bitwise the same either way.  OPT-IN: STRATA_IQ_STAGE_TABLES=1.  The PR (#1424) made it
// the default on compute capability 6.0 (GP100: no __dp4a, these kernels decode-bound at 70-140 GB/s); on a Tesla P100
// the decode was 0.97-1.03x of the unstaged kernels, noise, so it is not on by default.
bool stage_tables_on() {
    if (!g_stage_grid) return false;
    static const bool on = [] {
        const char* v = std::getenv("STRATA_IQ_STAGE_TABLES");
        return v && v[0] && v[0] != '0';
    }();
    return on;
}

template<int TY, bool STAGE_GRID = kStageIqGrid<TY>>
void launch_mmvq_multi_nc(dim3 grid, dim3 block, cudaStream_t s, const uint8_t* W, size_t rb, const block_q8_1* X,
                          float* y, int n_in, int n_out, int ncols) {
    switch (ncols) {
        case 1: mmvq_multi_kernel<TY, 1, STAGE_GRID, true><<<grid, block, 0, s>>>(W, rb, X, y, n_in, n_out, 1); break;
        case 2: mmvq_multi_kernel<TY, 2, STAGE_GRID, true><<<grid, block, 0, s>>>(W, rb, X, y, n_in, n_out, 2); break;
        case 3: mmvq_multi_kernel<TY, 3, STAGE_GRID, true><<<grid, block, 0, s>>>(W, rb, X, y, n_in, n_out, 3); break;
        case 4: mmvq_multi_kernel<TY, 4, STAGE_GRID, true><<<grid, block, 0, s>>>(W, rb, X, y, n_in, n_out, 4); break;
        case 5: mmvq_multi_kernel<TY, 5, STAGE_GRID, true><<<grid, block, 0, s>>>(W, rb, X, y, n_in, n_out, 5); break;
        case 6: mmvq_multi_kernel<TY, 6, STAGE_GRID, true><<<grid, block, 0, s>>>(W, rb, X, y, n_in, n_out, 6); break;
        default:
            if (ncols <= 1) mmvq_multi_kernel<TY, 1, STAGE_GRID, false><<<grid, block, 0, s>>>(W, rb, X, y, n_in, n_out, ncols);
            else mmvq_multi_kernel<TY, 8, STAGE_GRID, false><<<grid, block, 0, s>>>(W, rb, X, y, n_in, n_out, ncols);
            break;
    }
}

template<int TY>
void launch_mmvq(const uint8_t* W, size_t rb, const block_q8_1* X, float* y, int n_in, int n_out, int ncols,
                 cudaStream_t s) {
    const dim3 grid((unsigned) ((n_out + 3) / 4)), block(32, 4);
    if constexpr (!kSplit<TY>) mmvq_kernel<TY><<<grid, block, 0, s>>>(W, rb, X, y, n_in, n_out, ncols);
    else if (g_old_kernels) mmvq_kernel<TY><<<grid, block, 0, s>>>(W, rb, X, y, n_in, n_out, ncols);
    else if constexpr (kStageIqGrid<TY>) {
        if (!g_stage_grid_mmvq) {
            launch_mmvq_multi_nc<TY, false>(grid, block, s, W, rb, X, y, n_in, n_out, ncols);
            return;
        }
        launch_mmvq_multi_nc<TY, true>(grid, block, s, W, rb, X, y, n_in, n_out, ncols);
    } else {
        launch_mmvq_multi_nc<TY, false>(grid, block, s, W, rb, X, y, n_in, n_out, ncols);
    }
}


// ---- EXPDEC launch: knobs, shapes, rows per block
int env_int_or(const char* name, int dflt) {
    const char* v = std::getenv(name);
    return v != nullptr && v[0] != '\0' ? std::atoi(v) : dflt;
}
// read at first use (not at load), so a per-architecture default set at start (nv_defaults.hpp) is seen; native_expert_set_expdec overrides
int g_expdec = -1;
int expdec_mode() { if (g_expdec < 0) g_expdec = env_on("STRATA_EXPDEC") ? 1 : 0; return g_expdec; }
int g_expdec_r = env_int_or("STRATA_EXPDEC_R", 1);          // gate/up rows per lane: 1, 2 (bench: R=1 wins on the 2080 Ti)
int g_expdec_dr = env_int_or("STRATA_EXPDEC_DR", 4);        // down rows per lane: 1, 2, 4 (Q2_0: R=4 wins on both cards)
int g_expdec_minb = env_int_or("STRATA_EXPDEC_MINB", STRATA_EXPDEC_MINB);   // gate/up __launch_bounds__ min blocks: 2 (<= 128 regs) or 4 (<= 64)
int g_expdec_d20 = env_int_or("STRATA_EXPDEC_D20", 0);      // 1: EXPDEC also for the IQ4_NL down (bench: no gain, default kernel)
// 1: EXPDEC for the down too (Q2_0; IQ4_NL with _D20).  Default 0: in the engine (nsys of a 131k decode) the Q2_0 down
// with EXPDEC was ~10% slower than the default kernel although the bench alone had it at 0.78, so by default EXPDEC
// takes the gate/up only (-20/-23% at T >= 2 on an RTX 2080 Ti in the engine).
int g_expdec_down = env_int_or("STRATA_EXPDEC_DOWN", 0);
int g_expdec_rb = env_int_or("STRATA_EXPDEC_RB", 0);        // gate/up rows per block (0 = auto)
int g_expdec_drb = env_int_or("STRATA_EXPDEC_DRB", 0);      // down rows per block (0 = auto)
int g_expdec_minblk = env_int_or("STRATA_EXPDEC_MINBLK", 800);   // auto: the most rows per block that keep this many blocks
int expdec_r() { return g_expdec_r == 2 ? 2 : 1; }
int expdec_dr() { return g_expdec_dr == 1 || g_expdec_dr == 2 ? g_expdec_dr : 4; }

bool expdec_gu_ok(const NativeExpertLayout& L) {
    return L.gu_type == 18 && L.n_embd == 32 * ED_XB && L.n_ff % 64 == 0 && L.n_ff > 0;
}
bool expdec_down_ok(const NativeExpertLayout& L) {
    return g_expdec_down && ((L.d_type == 20 && g_expdec_d20) || L.d_type == 42) && L.n_ff == 32 * ED_HB && L.n_embd % 128 == 0 &&
           L.n_embd > 0;
}
// rows per block: `knob` rounded up to the rows per pass, or (0) doubled from one pass while ceil(nrow / rb) x
// cap_groups blocks stay >= g_expdec_minblk (cap_groups: the launch's group rows; every block row g >= n_groups returns)
int expdec_rows_per_block(int nrow, int rpp, int64_t cap_groups, int knob) {
    if (knob > 0) {
        int rb = (knob + rpp - 1) / rpp * rpp;
        return rb > nrow ? nrow : rb;
    }
    int rb = rpp;
    const int64_t cap = cap_groups > 0 ? cap_groups : 1;
    while (rb * 2 <= nrow && ((int64_t) ((nrow + 2 * rb - 1) / (2 * rb))) * cap >= g_expdec_minblk) rb *= 2;
    return rb;
}

template<int TG, int R, int MINB>
void expdec_gu_r(int64_t gy, int64_t cap_groups, cudaStream_t s, const unsigned long long* grp_ptr, const int32_t* grp_start,
                 const int32_t* n_groups, const int32_t* ent_tok, const block_q8_1* X, const NativeExpertLayout& L,
                 float* gate, float* up) {
    const int nrow = 2 * (int) L.n_ff;
    const int rb = expdec_rows_per_block(nrow, 16 * R, cap_groups, g_expdec_rb);
    const dim3 grid((unsigned) ((nrow + rb - 1) / rb), (unsigned) gy);
    expdec_gu_kernel<TG, R, MINB><<<grid, 256, 0, s>>>(grp_ptr, grp_start, n_groups, ent_tok, X, L, gate, up, rb);
}
template<int TG>
void expdec_gu_t(int64_t gy, int64_t cap_groups, cudaStream_t s, const unsigned long long* grp_ptr, const int32_t* grp_start,
                 const int32_t* n_groups, const int32_t* ent_tok, const block_q8_1* X, const NativeExpertLayout& L,
                 float* gate, float* up) {
    const bool b4 = g_expdec_minb >= 4;
    if (expdec_r() == 2) {
        if (b4) expdec_gu_r<TG, 2, 4>(gy, cap_groups, s, grp_ptr, grp_start, n_groups, ent_tok, X, L, gate, up);
        else expdec_gu_r<TG, 2, 2>(gy, cap_groups, s, grp_ptr, grp_start, n_groups, ent_tok, X, L, gate, up);
    } else {
        if (b4) expdec_gu_r<TG, 1, 4>(gy, cap_groups, s, grp_ptr, grp_start, n_groups, ent_tok, X, L, gate, up);
        else expdec_gu_r<TG, 1, 2>(gy, cap_groups, s, grp_ptr, grp_start, n_groups, ent_tok, X, L, gate, up);
    }
}
// One line on stderr per (role, format, taken or not), the first time STRATA_EXPDEC=1 meets it: the proof in the
// engine's log that the EXPDEC kernels are the ones launched (or captured into the window's graph), with the
// choices in force.
void expdec_note(const NativeExpertLayout& L, bool gu, bool taken, cudaStream_t s, int64_t gy, int64_t cap_groups) {
    static bool seen[2][64][2] = {};
    const int ty = gu ? L.gu_type : L.d_type;
    if (ty < 0 || ty >= 64 || seen[gu ? 0 : 1][ty][taken ? 1 : 0]) return;
    seen[gu ? 0 : 1][ty][taken ? 1 : 0] = true;
    cudaStreamCaptureStatus cs = cudaStreamCaptureStatusNone;
    cudaStreamIsCapturing(s, &cs);
    int dev = -1;
    cudaGetDevice(&dev);
    if (!taken) {
        std::fprintf(stderr, "strata expdec: CUDA%d %s type %d (n_embd %lld, n_ff %lld) not covered: the default kernel runs\n", dev,
                     gu ? "gate/up" : "down", ty, (long long) L.n_embd, (long long) L.n_ff);
        return;
    }
    if (gu) {
        const int R = expdec_r(), rb = expdec_rows_per_block(2 * (int) L.n_ff, 16 * R, cap_groups, g_expdec_rb);
        std::fprintf(stderr, "strata expdec: CUDA%d gate/up type %d: expdec_gu_kernel R %d MINB %d, %d rows per block, grid %d x %lld "
                             "(cap %lld groups)%s\n", dev, ty, R, g_expdec_minb >= 4 ? 4 : 2, rb, (2 * (int) L.n_ff + rb - 1) / rb,
                     (long long) gy, (long long) cap_groups, cs != cudaStreamCaptureStatusNone ? ", captured into a graph" : "");
    } else {
        const int R = expdec_dr(), lanes = ty == 20 ? 8 : 16;
        const int rb = expdec_rows_per_block((int) L.n_embd, (256 / lanes) * R, cap_groups, g_expdec_drb);
        std::fprintf(stderr, "strata expdec: CUDA%d down type %d: expdec_down_kernel R %d, %d rows per block, grid %d x %lld "
                             "(cap %lld groups)%s\n", dev, ty, R, rb, ((int) L.n_embd + rb - 1) / rb, (long long) gy,
                     (long long) cap_groups, cs != cudaStreamCaptureStatusNone ? ", captured into a graph" : "");
    }
}

// false: EXPDEC is off or does not take this layout (the caller launches the default kernels)
bool expdec_gu(const NativeExpertLayout& L, int64_t gy, int64_t cap_groups, cudaStream_t s, const unsigned long long* grp_ptr,
               const int32_t* grp_start, const int32_t* n_groups, const int32_t* ent_tok, const block_q8_1* X, float* gate,
               float* up) {
    if (!expdec_mode() || g_old_kernels) return false;
    if (!expdec_gu_ok(L)) { expdec_note(L, true, false, s, gy, cap_groups); return false; }
    expdec_note(L, true, true, s, gy, cap_groups);
    expdec_gu_t<18>(gy, cap_groups, s, grp_ptr, grp_start, n_groups, ent_tok, X, L, gate, up);
    return true;
}
template<int TD, int R>
void expdec_down_r(int64_t gy, int64_t cap_groups, cudaStream_t s, const unsigned long long* grp_ptr, const int32_t* grp_start,
                   const int32_t* n_groups, const int32_t* ent_dst, const block_q8_1* hq, const NativeExpertLayout& L, float* out) {
    constexpr int LANES = TD == 20 ? 8 : 16;
    const int nrow = (int) L.n_embd;
    const int rb = expdec_rows_per_block(nrow, (256 / LANES) * R, cap_groups, g_expdec_drb);
    const dim3 grid((unsigned) ((nrow + rb - 1) / rb), (unsigned) gy);
    expdec_down_kernel<TD, R><<<grid, 256, 0, s>>>(grp_ptr, grp_start, n_groups, ent_dst, hq, L, out, rb);
}
template<int TD>
void expdec_down_t(int64_t gy, int64_t cap_groups, cudaStream_t s, const unsigned long long* grp_ptr, const int32_t* grp_start,
                   const int32_t* n_groups, const int32_t* ent_dst, const block_q8_1* hq, const NativeExpertLayout& L, float* out) {
    switch (expdec_dr()) {
        case 1: expdec_down_r<TD, 1>(gy, cap_groups, s, grp_ptr, grp_start, n_groups, ent_dst, hq, L, out); break;
        case 2: expdec_down_r<TD, 2>(gy, cap_groups, s, grp_ptr, grp_start, n_groups, ent_dst, hq, L, out); break;
        default: expdec_down_r<TD, 4>(gy, cap_groups, s, grp_ptr, grp_start, n_groups, ent_dst, hq, L, out); break;
    }
}
bool expdec_down(const NativeExpertLayout& L, int64_t gy, int64_t cap_groups, cudaStream_t s, const unsigned long long* grp_ptr,
                 const int32_t* grp_start, const int32_t* n_groups, const int32_t* ent_dst, const block_q8_1* hq, float* out) {
    if (!expdec_mode() || g_old_kernels) return false;
    if (!expdec_down_ok(L)) { expdec_note(L, false, false, s, gy, cap_groups); return false; }
    expdec_note(L, false, true, s, gy, cap_groups);
    if (L.d_type == 20) expdec_down_t<20>(gy, cap_groups, s, grp_ptr, grp_start, n_groups, ent_dst, hq, L, out);
    else expdec_down_t<42>(gy, cap_groups, s, grp_ptr, grp_start, n_groups, ent_dst, hq, L, out);
    return true;
}

template<int TG>
void launch_gu(dim3 grid, cudaStream_t s, const unsigned long long* grp_ptr, const int32_t* grp_start,
               const int32_t* n_groups, const int32_t* ent_tok, const block_q8_1* X, const NativeExpertLayout& L,
               float* gate, float* up) {
    // the sub-16 rows (row_dot_80_sub16) assume 80 dots per row: (n_embd / qk) * ipb == 80.
    const bool g_no_sub16_gu = ::strata::kernels::g_no_sub16_gu || (L.n_embd / Fmt<TG>::qk) * Fmt<TG>::ipb != 80;
    if constexpr (!kSplit<TG>) native_gu_kernel<TG><<<grid, 256, 0, s>>>(grp_ptr, grp_start, n_groups, ent_tok, X, L, gate, up);
    else if (g_old_kernels) native_gu_kernel<TG><<<grid, 256, 0, s>>>(grp_ptr, grp_start, n_groups, ent_tok, X, L, gate, up);
    else if constexpr (kStageIqGrid<TG>) {
        if (!g_stage_grid || (TG == 18 && !stage_tables_on())) {
            if (g_no_sub16_gu) native_gu_multi_kernel<TG, false, false><<<grid, 256, 0, s>>>(grp_ptr, grp_start, n_groups, ent_tok, X, L, gate, up);
            else native_gu_multi_kernel<TG, false, true><<<grid, 256, 0, s>>>(grp_ptr, grp_start, n_groups, ent_tok, X, L, gate, up);
            return;
        }
        if (g_no_sub16_gu) native_gu_multi_kernel<TG, true, false><<<grid, 256, 0, s>>>(grp_ptr, grp_start, n_groups, ent_tok, X, L, gate, up);
        else native_gu_multi_kernel<TG, true, true><<<grid, 256, 0, s>>>(grp_ptr, grp_start, n_groups, ent_tok, X, L, gate, up);
    } else {
        if (g_no_sub16_gu) native_gu_multi_kernel<TG, false, false><<<grid, 256, 0, s>>>(grp_ptr, grp_start, n_groups, ent_tok, X, L, gate, up);
        else native_gu_multi_kernel<TG, false, true><<<grid, 256, 0, s>>>(grp_ptr, grp_start, n_groups, ent_tok, X, L, gate, up);
    }
}

// STRATA_GU_SWIGLU=1 runs gate/up, SwiGLU and the q8_1 blocks as native_gu_swiglu_kernel, where launch_gu would
// run native_gu_multi_kernel's 16-lane path (n_embd 2560, the split formats); bitwise the same hq.
bool gu_swiglu_on() {
    static const bool on = [] { const char* v = std::getenv("STRATA_GU_SWIGLU"); return v != nullptr && std::atoi(v) != 0; }();
    return on;
}
template<int TG>
bool launch_gu_swiglu(const NativeExpertLayout& L, int64_t gy, cudaStream_t s, const unsigned long long* grp_ptr,
                      const int32_t* grp_start, const int32_t* n_groups, const int32_t* ent_tok, const block_q8_1* X,
                      block_q8_1* hq) {
    if constexpr (!kSplit<TG>) {
        return false;
    } else {
        if (g_old_kernels || g_no_sub16_gu || L.n_embd != 2560 || L.n_ff % 32 != 0 || L.n_ff <= 0) return false;
        // The 16-lane rows assume 80 dots a row, as launch_gu does.
        if ((L.n_embd / Fmt<TG>::qk) * Fmt<TG>::ipb != 80) return false;
        const dim3 grid((unsigned) (L.n_ff / 32), (unsigned) gy);
        bool stage = false;
        if constexpr (kStageIqGrid<TG>) stage = g_stage_grid && !(TG == 18 && !stage_tables_on());
        if constexpr (kStageIqGrid<TG>) {
            if (stage) native_gu_swiglu_kernel<TG, true><<<grid, 512, 0, s>>>(grp_ptr, grp_start, n_groups, ent_tok, X, L, hq);
            else native_gu_swiglu_kernel<TG, false><<<grid, 512, 0, s>>>(grp_ptr, grp_start, n_groups, ent_tok, X, L, hq);
        } else {
            native_gu_swiglu_kernel<TG, false><<<grid, 512, 0, s>>>(grp_ptr, grp_start, n_groups, ent_tok, X, L, hq);
        }
        return true;
    }
}

template<int TD>
void launch_down(dim3 grid, cudaStream_t s, const unsigned long long* grp_ptr, const int32_t* grp_start,
                 const int32_t* n_groups, const int32_t* ent_dst, const block_q8_1* hq, const NativeExpertLayout& L,
                 float* out) {
    if constexpr (!kSplit<TD>) native_down_kernel<TD><<<grid, 256, 0, s>>>(grp_ptr, grp_start, n_groups, ent_dst, hq, L, out);
    else if (g_old_kernels) native_down_kernel<TD><<<grid, 256, 0, s>>>(grp_ptr, grp_start, n_groups, ent_dst, hq, L, out);
    else if (g_no_sub16_gu) native_down_multi_kernel<TD, false><<<grid, 256, 0, s>>>(grp_ptr, grp_start, n_groups, ent_dst, hq, L, out);
    else native_down_multi_kernel<TD, true><<<grid, 256, 0, s>>>(grp_ptr, grp_start, n_groups, ent_dst, hq, L, out);
}

}  // namespace

void iq_set_old_kernels(bool old) { g_old_kernels = old; }
bool iq_old_kernels() { return g_old_kernels; }

bool iq_supported(int t) noexcept { return is_iq(t); }
// BF16 is the one embedding-only format; every other accepted value is a production matrix format.
bool embed_type_supported(int t) noexcept { return is_iq(t) || t == 30; }

size_t iq_row_bytes(int t, int64_t n) noexcept {
    switch (t) {
        case 18: return (size_t) (n / 256) * sizeof(block_iq3_xxs);
        case 20: return (size_t) (n / 32) * sizeof(block_iq4_nl);
        case 42: return (size_t) (n / 64) * sizeof(block_q2_0);
        case 8: return (size_t) (n / 32) * sizeof(block_q8_0);
        case 30: return (size_t) n * 2;   // BF16: the token embedding only (iq_embed_rows, iq_dequant_f32)
        default: return 0;
    }
}

void quantize_q8_1_rows(const float* x, int64_t n_rows, int64_t n_cols, void* y, void* stream) {
    const long long n = (long long) n_rows * n_cols;
    if (n <= 0) return;
    quantize_q8_1_kernel<<<(unsigned) ((n + 255) / 256), 256, 0, (cudaStream_t) stream>>>(x, (block_q8_1*) y, n);
    check("quantize_q8_1_rows");
}

void iq_mmvq(int t, const void* w, const void* x_q8_1, float* y, int n_in, int n_out, int ncols, void* stream) {
    const size_t rb = iq_row_bytes(t, n_in);
    cudaStream_t s = (cudaStream_t) stream;
    const auto* W = (const uint8_t*) w;
    const auto* X = (const block_q8_1*) x_q8_1;
    switch (t) {
#define STRATA_MMVQ(T) case T: launch_mmvq<T>(W, rb, X, y, n_in, n_out, ncols, s); break;
        STRATA_MMVQ_FMTS(STRATA_MMVQ)
#undef STRATA_MMVQ
        default: std::fprintf(stderr, "iq_mmvq: type %d is not supported\n", t); std::exit(1);
    }
    check("iq_mmvq");
}

void iq_dequant_f16(int t, const void* src, int64_t n, uint16_t* dst, void* stream) {
    if (n % 256 != 0 || !is_iq(t)) { std::fprintf(stderr, "iq_dequant_f16: bad arguments\n"); std::exit(1); }
    dequant_flat_kernel<__half><<<(unsigned) (n / 256), 32, 0, (cudaStream_t) stream>>>(t, src, (__half*) dst);
    check("iq_dequant_f16");
}

namespace {
__global__ void embed_rows_kernel(int ty, const uint8_t* __restrict__ table, size_t row_bytes,
                                  const int32_t* __restrict__ tokens, int64_t n_embd, float* __restrict__ y) {
    const int t = blockIdx.y;
    const int64_t b = blockIdx.x;
    const uint8_t* row = table + (size_t) tokens[t] * row_bytes;
    dq_dispatch<float>(ty, row, b, y + (size_t) t * n_embd + b * QK_K, threadIdx.x);
}
}  // namespace

void iq_embed_rows(int t, const void* table, size_t row_bytes, const int32_t* tokens, int64_t n_tok, int64_t n_embd,
                   float* out, void* stream) {
    if (n_tok <= 0) return;
    if (n_embd % 256 != 0 || !embed_type_supported(t)) { std::fprintf(stderr, "iq_embed_rows: bad arguments\n"); std::exit(1); }
    embed_rows_kernel<<<dim3((unsigned) (n_embd / 256), (unsigned) n_tok), 32, 0, (cudaStream_t) stream>>>(
        t, (const uint8_t*) table, row_bytes, tokens, n_embd, out);
    check("iq_embed_rows");
}

void iq_dequant_f32(int t, const void* src, int64_t n, float* dst, void* stream) {
    if (n % 256 != 0 || !embed_type_supported(t)) { std::fprintf(stderr, "iq_dequant_f32: bad arguments\n"); std::exit(1); }
    dequant_flat_kernel<float><<<(unsigned) (n / 256), 32, 0, (cudaStream_t) stream>>>(t, src, dst);
    check("iq_dequant_f32");
}

void iq_dequant_gu_f16(int t, const void* gate, const void* up, int64_t n_ff, int64_t n_embd, uint16_t* dst, void* stream) {
    // checked like the other entry points: an unknown type used to leave `dst` unwritten, a wrong prompt and no error
    if (n_embd % 256 != 0 || !is_iq(t)) { std::fprintf(stderr, "iq_dequant_gu_f16: type %d / %lld\n", t, (long long) n_embd); std::exit(1); }
    const int64_t per_row = n_embd / 256;
    dequant_gu_kernel<<<dim3((unsigned) (n_ff * per_row), 2), 32, 0, (cudaStream_t) stream>>>(t, gate, up, per_row,
                                                                                           (__half*) dst);
    check("iq_dequant_gu_f16");
}

bool native_expert_supported(int gu_type, int d_type, int64_t n_embd, int64_t n_ff) noexcept {
    const bool roles_ok = (gu_type == 18 || gu_type == 8) && (d_type == 20 || d_type == 42 || d_type == 8);
    const int qg = gu_qk(gu_type), qd = d_qk(d_type);
    // a type without row bytes would make every row (and the up/down offsets) 0: refused, not run wrong
    return roles_ok && qg > 0 && qd > 0 && iq_row_bytes(gu_type, n_embd) > 0 && iq_row_bytes(d_type, n_ff) > 0 &&
           is_iq(gu_type) && is_iq(d_type) && n_embd % qg == 0 && n_ff % qd == 0 &&
           n_embd % 256 == 0 && (n_ff * n_embd) % 256 == 0;
}

NativeExpertLayout native_expert_layout(int gu_type, int d_type, int64_t n_embd, int64_t n_ff) {
    NativeExpertLayout L;
    L.gu_type = gu_type;
    L.d_type = d_type;
    L.n_embd = n_embd;
    L.n_ff = n_ff;
    L.gu_row = iq_row_bytes(gu_type, n_embd);
    L.d_row = iq_row_bytes(d_type, n_ff);
    L.up_off = (size_t) n_ff * L.gu_row;
    L.down_off = 2 * L.up_off;
    L.bytes = L.down_off + (size_t) n_embd * L.d_row;
    return L;
}

size_t native_expert_scratch_bytes(int64_t cap, int64_t n_ff) {
    const size_t f = (size_t) cap * (size_t) n_ff * sizeof(float);
    return 3 * ((f + 255) & ~(size_t) 255) + (((size_t) cap * (size_t) (n_ff / 32) * sizeof(block_q8_1) + 255) & ~(size_t) 255);
}


// ---- 5 (LDS): IQ3_XXS gate/up with the codebook grid and the group's q8_1
// activations in LDS.  The bench showed gate/up compute-bound, not bandwidth-bound (T = 2 costs ~1.8x T = 1 on the
// same weights, 110-130 GB/s): each call issues its grid lookups and eight activation loads through the vector
// memory path after the weight load.  Same calls, same lane-strided k, same R2 row pairs, same warp_sum: the
// output is bitwise the mode-2 one.
template<int TY> struct GridOf;
template<> struct GridOf<18> { using T = uint32_t; static constexpr int N = 256; __device__ static const T* src() { return iq3xxs_grid; } };

// SG = 1: the signs without byte-SIMD ops.  m = the sign nibble as 0x00/0xff bytes; (g ^ m) is g or -g-1 per byte, so
// dp4a(g ^ m, u) - dp4a(m, u) = sum(s g u) exactly - the same integers, the same floats.
__device__ __forceinline__ int nib_mask(uint32_t n) {
    const uint32_t b = __umul24(n & 0xFu, 0x204081u) & 0x01010101u;
    return (int) ((b << 8) - b);
}
template<int TY, int SG> struct DotG;
template<int SG> struct DotG<18, SG> { __device__ static __forceinline__ float f(const void* vbq, const block_q8_1* bq8_1, int kbx, int iqs,
                                                      const uint32_t* grid) {
    const block_iq3_xxs* bq3 = (const block_iq3_xxs*) vbq + kbx;
    const int2 q3_packed = make_int2(get_int_b2(bq3->qs, iqs), get_int_b2(bq3->qs, iqs + 1));
    const uint8_t* q3 = (const uint8_t*) &q3_packed;
    const uint32_t aux32 = get_int_b2(bq3->qs, QK_K / 16 + iqs / 2);
    int sumi = 0;
#pragma unroll
    for (int l0 = 0; l0 < 8; l0 += 2) {
        const int2 grid_pos = make_int2(grid[q3[l0 + 0]], grid[q3[l0 + 1]]);
        const uint32_t signs = unpack_ksigns(aux32 >> (7 * l0 / 2));
        const int u0 = get_int_b4(bq8_1[iqs / 2].qs, l0 + 0);
        const int u1 = get_int_b4(bq8_1[iqs / 2].qs, l0 + 1);
        if constexpr (SG == 1) {
            const int m0 = nib_mask(signs), m1 = nib_mask(signs >> 4);
            sumi = ggml_cuda_dp4a(grid_pos.x ^ m0, u0, sumi);
            sumi = ggml_cuda_dp4a(grid_pos.y ^ m1, u1, sumi);
            sumi -= ggml_cuda_dp4a(m1, u1, ggml_cuda_dp4a(m0, u0, 0));
        } else {
            const int signs0 = __vcmpne4(signs & 0x08040201, 0);
            const int grid_l = __vsub4(grid_pos.x ^ signs0, signs0);
            const int signs1 = __vcmpne4(signs & 0x80402010, 0);
            const int grid_h = __vsub4(grid_pos.y ^ signs1, signs1);
            sumi = ggml_cuda_dp4a(grid_l, u0, sumi);
            sumi = ggml_cuda_dp4a(grid_h, u1, sumi);
        }
    }
    const int ls = aux32 >> 28;
    sumi = (ls * sumi + sumi / 2) / 2;
    const float d = __half2float(bq3->d) * __low2float(bq8_1[iqs / 2].ds);
    return d * sumi;
} };



constexpr int LDS_RB = 64;    // gate/up rows per block (4 R2 passes of 16)
constexpr int LDS_NT = 4;     // entries whose activations sit in LDS at once

template<int TG, int SG, bool TI = false>
__global__ void __launch_bounds__(256) native_gu_lds_kernel(const unsigned long long* __restrict__ grp_ptr,
                                                            const int32_t* __restrict__ grp_start,
                                                            const int32_t* __restrict__ n_groups,
                                                            const int32_t* __restrict__ ent_tok,
                                                            const block_q8_1* __restrict__ xq, NativeExpertLayout L,
                                                            float* __restrict__ gate, float* __restrict__ up) {
    using GT = typename GridOf<TG>::T;
    using F = Fmt<TG>;
    __shared__ GT sgrid[GridOf<TG>::N];
    extern __shared__ int sx_raw[];   // LDS_NT tokens x xb blocks of q8_1 (36 bytes = 9 ints each)
    const int g = blockIdx.y;
    if (g >= *n_groups) return;       // uniform over the block
    const int tid = threadIdx.x, lane = tid & 31, warp = tid >> 5;
    const GT* gsrc = GridOf<TG>::src();
    for (int i = tid; i < GridOf<TG>::N; i += 256) sgrid[i] = gsrc[i];
    const uint8_t* blob = (const uint8_t*) grp_ptr[g];
    const int nb = (int) (L.n_embd / F::qk), xb = (int) (L.n_embd / 32);
    const int e0 = grp_start[g], e1 = grp_start[g + 1];
    const int nrow = 2 * (int) L.n_ff;
    const block_q8_1* sx = (const block_q8_1*) sx_raw;
    auto wrow = [&](int row) -> const uint8_t* {
        const bool is_up = row >= L.n_ff;
        return blob + (is_up ? L.up_off : 0) + (size_t) (is_up ? row - (int) L.n_ff : row) * L.gu_row;
    };
    auto put = [&](int row, int e, float v) {
        const bool is_up = row >= L.n_ff;
        (is_up ? up : gate)[(size_t) e * L.n_ff + (is_up ? row - (int) L.n_ff : row)] = v;
    };
    for (int c0 = e0; c0 < e1; c0 += LDS_NT) {
        const int cn = min(LDS_NT, e1 - c0);
        __syncthreads();
        for (int j = 0; j < cn; ++j) {
            const int* src = (const int*) (xq + (size_t) ent_tok[c0 + j] * xb);
            for (int i = tid; i < xb * 9; i += 256) sx_raw[j * xb * 9 + i] = src[i];
        }
        __syncthreads();
        for (int p = 0; p < LDS_RB / 16; ++p) {
            const int row0 = blockIdx.x * LDS_RB + p * 16 + warp, row1 = row0 + 8;
            if (row0 >= nrow) break;
            const bool two = row1 < nrow;
            const uint8_t* w0 = wrow(row0);
            const uint8_t* w1 = wrow(two ? row1 : row0);
            if constexpr (TI) {   // mode 7: the tokens inside the k loop - one weight load per k for all of them
                auto pass = [&](auto ntc) {
                    constexpr int NTC = decltype(ntc)::value;
                    float s0[NTC], s1[NTC];
#pragma unroll
                    for (int j = 0; j < NTC; ++j) { s0[j] = 0.0f; s1[j] = 0.0f; }
                    for (int k = lane; k < nb * F::ipb; k += 32) {
                        const int kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
#pragma unroll
                        for (int j = 0; j < NTC; ++j) {
                            const block_q8_1* xk = sx + (size_t) j * xb + kbx * (F::qk / 32);
                            const float a = DotG<TG, SG>::f(w0, xk, kbx, iqs, sgrid);
                            const float b = DotG<TG, SG>::f(w1, xk, kbx, iqs, sgrid);
                            s0[j] += a;
                            s1[j] += b;
                        }
                    }
#pragma unroll
                    for (int j = 0; j < NTC; ++j) {
                        const float a = warp_sum(s0[j]), b = warp_sum(s1[j]);
                        if (lane == 0) { put(row0, c0 + j, a); if (two) put(row1, c0 + j, b); }
                    }
                };
                switch (cn) {
                    case 1: pass(std::integral_constant<int, 1>{}); break;
                    case 2: pass(std::integral_constant<int, 2>{}); break;
                    case 3: pass(std::integral_constant<int, 3>{}); break;
                    default: pass(std::integral_constant<int, 4>{}); break;
                }
                continue;
            }
            for (int j = 0; j < cn; ++j) {
                const block_q8_1* x = sx + (size_t) j * xb;
                float s0 = 0.0f, s1 = 0.0f;
                for (int k = lane; k < nb * F::ipb; k += 32) {
                    const int kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
                    const block_q8_1* xk = x + kbx * (F::qk / 32);
                    const float a = DotG<TG, SG>::f(w0, xk, kbx, iqs, sgrid);
                    const float b = DotG<TG, SG>::f(w1, xk, kbx, iqs, sgrid);
                    s0 += a;
                    s1 += b;
                }
                s0 = warp_sum(s0);
                s1 = warp_sum(s1);
                if (lane == 0) { put(row0, c0 + j, s0); if (two) put(row1, c0 + j, s1); }
            }
        }
    }
}

// ---- 7 (down): the group's q8_1 h rows in LDS, the tokens inside the k loop, the R2 row pairs of mode 2 -
// the same per-(row, entry) sums in the same order: bitwise the mode-2 output.
template<int TD>
__global__ void __launch_bounds__(256) native_down_lds_kernel(const unsigned long long* __restrict__ grp_ptr,
                                                              const int32_t* __restrict__ grp_start,
                                                              const int32_t* __restrict__ n_groups,
                                                              const int32_t* __restrict__ ent_dst,
                                                              const block_q8_1* __restrict__ hq, NativeExpertLayout L,
                                                              float* __restrict__ out) {
    using F = Fmt<TD>;
    extern __shared__ int sh_raw[];   // LDS_NT entries x hb blocks of q8_1
    const int g = blockIdx.y;
    if (g >= *n_groups) return;
    const int tid = threadIdx.x, lane = tid & 31, warp = tid >> 5;
    const uint8_t* blob = (const uint8_t*) grp_ptr[g];
    const int nb = (int) (L.n_ff / F::qk), hb = (int) (L.n_ff / 32);
    const int e0 = grp_start[g], e1 = grp_start[g + 1];
    const int nrow = (int) L.n_embd;
    const block_q8_1* sh = (const block_q8_1*) sh_raw;
    for (int c0 = e0; c0 < e1; c0 += LDS_NT) {
        const int cn = min(LDS_NT, e1 - c0);
        __syncthreads();
        {
            const int* src = (const int*) (hq + (size_t) c0 * hb);   // the chunk's entries are contiguous
            for (int i = tid; i < cn * hb * 9; i += 256) sh_raw[i] = src[i];
        }
        __syncthreads();
        for (int p = 0; p < LDS_RB / 16; ++p) {
            const int r0 = blockIdx.x * LDS_RB + p * 16 + warp, r1 = r0 + 8;
            if (r0 >= nrow) break;
            const bool two = r1 < nrow;
            const uint8_t* w0 = blob + L.down_off + (size_t) r0 * L.d_row;
            const uint8_t* w1 = blob + L.down_off + (size_t) (two ? r1 : r0) * L.d_row;
            auto pass = [&](auto ntc) {
                constexpr int NTC = decltype(ntc)::value;
                float s0[NTC], s1[NTC];
#pragma unroll
                for (int j = 0; j < NTC; ++j) { s0[j] = 0.0f; s1[j] = 0.0f; }
                for (int k = lane; k < nb * F::ipb; k += 32) {
                    const int kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
#pragma unroll
                    for (int j = 0; j < NTC; ++j) {
                        const block_q8_1* xk = sh + (size_t) j * hb + kbx * (F::qk / 32);
                        const float a = F::dot(w0, xk, kbx, iqs);
                        const float b = F::dot(w1, xk, kbx, iqs);
                        s0[j] += a;
                        s1[j] += b;
                    }
                }
#pragma unroll
                for (int j = 0; j < NTC; ++j) {
                    const float a = warp_sum(s0[j]), b = warp_sum(s1[j]);
                    if (lane == 0) {
                        out[(size_t) ent_dst[c0 + j] * L.n_embd + r0] = a;
                        if (two) out[(size_t) ent_dst[c0 + j] * L.n_embd + r1] = b;
                    }
                }
            };
            switch (cn) {
                case 1: pass(std::integral_constant<int, 1>{}); break;
                case 2: pass(std::integral_constant<int, 2>{}); break;
                case 3: pass(std::integral_constant<int, 3>{}); break;
                default: pass(std::integral_constant<int, 4>{}); break;
            }
        }
    }
}

// ---- 8: mode 7's gate/up with SwiGLU and the q8_1 quantization in the epilogue.  A block takes h rows
// [r0, r0 + 32): gate rows r0.. and up rows r0.. (64 weight rows, the same per-(row, entry) sums as mode 7), so
// one 32-lane warp per entry holds exactly one q8_1 block of h and runs quantize_q8_1_kernel's own code on it -
// bitwise the separate kernels' hq, two launches fewer per call.
template<int TG>
__global__ void __launch_bounds__(256) native_gu_fused_kernel(const unsigned long long* __restrict__ grp_ptr,
                                                              const int32_t* __restrict__ grp_start,
                                                              const int32_t* __restrict__ n_groups,
                                                              const int32_t* __restrict__ ent_tok,
                                                              const block_q8_1* __restrict__ xq, NativeExpertLayout L,
                                                              block_q8_1* __restrict__ hq) {
    using GT = typename GridOf<TG>::T;
    using F = Fmt<TG>;
    __shared__ GT sgrid[GridOf<TG>::N];
    __shared__ float res[LDS_NT][64];
    extern __shared__ int sx_raw[];
    const int g = blockIdx.y;
    if (g >= *n_groups) return;
    const int tid = threadIdx.x, lane = tid & 31, warp = tid >> 5;
    const GT* gsrc = GridOf<TG>::src();
    for (int i = tid; i < GridOf<TG>::N; i += 256) sgrid[i] = gsrc[i];
    const uint8_t* blob = (const uint8_t*) grp_ptr[g];
    const int nb = (int) (L.n_embd / F::qk), xb = (int) (L.n_embd / 32), hb = (int) (L.n_ff / 32);
    const int e0 = grp_start[g], e1 = grp_start[g + 1];
    const int r0 = blockIdx.x * 32;
    const block_q8_1* sx = (const block_q8_1*) sx_raw;
    auto wrow = [&](int i) -> const uint8_t* {   // i < 32: gate row r0 + i, else up row r0 + i - 32
        return blob + (i < 32 ? (size_t) 0 : L.up_off) + (size_t) (r0 + (i & 31)) * L.gu_row;
    };
    for (int c0 = e0; c0 < e1; c0 += LDS_NT) {
        const int cn = min(LDS_NT, e1 - c0);
        __syncthreads();
        for (int j = 0; j < cn; ++j) {
            const int* src = (const int*) (xq + (size_t) ent_tok[c0 + j] * xb);
            for (int i = tid; i < xb * 9; i += 256) sx_raw[j * xb * 9 + i] = src[i];
        }
        __syncthreads();
        for (int p = 0; p < 4; ++p) {
            const int i0 = p * 16 + warp, i1 = i0 + 8;
            const uint8_t* w0 = wrow(i0);
            const uint8_t* w1 = wrow(i1);
            auto pass = [&](auto ntc) {
                constexpr int NTC = decltype(ntc)::value;
                float s0[NTC], s1[NTC];
#pragma unroll
                for (int j = 0; j < NTC; ++j) { s0[j] = 0.0f; s1[j] = 0.0f; }
                for (int k = lane; k < nb * F::ipb; k += 32) {
                    const int kbx = k / F::ipb, iqs = F::step * (k % F::ipb);
#pragma unroll
                    for (int j = 0; j < NTC; ++j) {
                        const block_q8_1* xk = sx + (size_t) j * xb + kbx * (F::qk / 32);
                        const float a = DotG<TG, 1>::f(w0, xk, kbx, iqs, sgrid);
                        const float b = DotG<TG, 1>::f(w1, xk, kbx, iqs, sgrid);
                        s0[j] += a;
                        s1[j] += b;
                    }
                }
#pragma unroll
                for (int j = 0; j < NTC; ++j) {
                    const float a = warp_sum(s0[j]), b = warp_sum(s1[j]);
                    if (lane == 0) { res[j][i0] = a; res[j][i1] = b; }
                }
            };
            switch (cn) {
                case 1: pass(std::integral_constant<int, 1>{}); break;
                case 2: pass(std::integral_constant<int, 2>{}); break;
                case 3: pass(std::integral_constant<int, 3>{}); break;
                default: pass(std::integral_constant<int, 4>{}); break;
            }
        }
        __syncthreads();
        if (warp < cn) {   // swiglu_entries_kernel + quantize_q8_1_kernel, one block of 32 h values per entry
            const float gg = res[warp][lane], uu = res[warp][32 + lane];
            const float xi = (gg / (1.0f + __expf(-gg))) * uu;
            float amax = fabsf(xi), sum = xi;
#pragma unroll
            for (int o = 16; o > 0; o >>= 1) {
                amax = fmaxf(amax, __shfl_xor_sync(0xffffffffu, amax, o));
                sum += __shfl_xor_sync(0xffffffffu, sum, o);
            }
            const float d = q8_1_finite(amax / 127.0f);   // #606, as q8_1_store: finite blocks bit for bit
            const int8_t q = q8_1_quant(xi, d, amax);
            block_q8_1* y = hq + (size_t) (c0 + warp) * hb + blockIdx.x;
            y->qs[lane] = q;
            if (lane == 0) y->ds = q8_1_ds(d, sum);
        }
    }
}

constexpr int kExpMode = 8;
int exp_mode() { return kExpMode; }
int g_exp_phase = 0;   // the bench: 0 all, 1 gate/up + swiglu + quantize only, 2 down only

void native_expert_set_mode(int mode, int phase) {
    (void) mode;   // retained for the benchmark ABI; production is fixed to STRATA_EXP_MODE=8
    g_exp_phase = phase;
}

namespace {
bool g_grouped_v1 = env_on("STRATA_GROUPED_V1");
}  // namespace

void native_grouped_set_v1(bool v1) { g_grouped_v1 = v1; }

void native_expert_set_expdec(int on) { g_expdec = on ? 1 : 0; }
int native_expert_expdec() { return expdec_mode(); }
void native_expert_set_expdec_params(const NativeExpertExpdecParams& p) {
    if (p.gu_rows_per_lane > 0) g_expdec_r = p.gu_rows_per_lane;
    if (p.down_rows_per_lane > 0) g_expdec_dr = p.down_rows_per_lane;
    if (p.gu_min_blocks_per_sm > 0) g_expdec_minb = p.gu_min_blocks_per_sm;
    if (p.down_iq4_nl >= 0) g_expdec_d20 = p.down_iq4_nl;
    if (p.down >= 0) g_expdec_down = p.down;
    if (p.gu_rows_per_block >= 0) g_expdec_rb = p.gu_rows_per_block;
    if (p.down_rows_per_block >= 0) g_expdec_drb = p.down_rows_per_block;
    if (p.min_blocks > 0) g_expdec_minblk = p.min_blocks;
}
NativeExpertExpdecParams native_expert_expdec_params() {
    NativeExpertExpdecParams p;
    p.gu_rows_per_lane = expdec_r();
    p.down_rows_per_lane = expdec_dr();
    p.gu_min_blocks_per_sm = g_expdec_minb >= 4 ? 4 : 2;
    p.down_iq4_nl = g_expdec_d20 ? 1 : 0;
    p.down = g_expdec_down ? 1 : 0;
    p.gu_rows_per_block = g_expdec_rb;
    p.down_rows_per_block = g_expdec_drb;
    p.min_blocks = g_expdec_minblk;
    return p;
}
bool native_expert_expdec_takes(const NativeExpertLayout& L, bool& gu, bool& down) {
    gu = expdec_gu_ok(L);
    down = expdec_down_ok(L);
    return gu || down;
}

void native_expert_grouped(const NativeExpertLayout& L, const unsigned long long* grp_ptr, const int32_t* grp_start,
                           const int32_t* n_groups, const int32_t* ent_dst, const int32_t* ent_tok, int64_t cap_groups,
                           int64_t cap_entries, const void* x_q8_1, void* scratch, float* out, void* stream,
                           int64_t grid_groups) {
    if (cap_groups <= 0 || cap_entries <= 0) return;
    if (L.n_ff % 32 != 0) { std::fprintf(stderr, "native_expert_grouped: n_ff %lld\n", (long long) L.n_ff); std::exit(1); }
    cudaStream_t s = (cudaStream_t) stream;
    const size_t f = (size_t) cap_entries * (size_t) L.n_ff * sizeof(float), fa = (f + 255) & ~(size_t) 255;
    float* gate = (float*) scratch;
    float* up = (float*) ((uint8_t*) scratch + fa);
    float* h = (float*) ((uint8_t*) scratch + 2 * fa);
    block_q8_1* hq = (block_q8_1*) ((uint8_t*) scratch + 3 * fa);
    const auto* X = (const block_q8_1*) x_q8_1;
    const bool v1 = g_grouped_v1;
    const int64_t gy = (v1 || grid_groups <= 0 || grid_groups > cap_groups) ? cap_groups : grid_groups;
    const int gu_rows = (!g_old_kernels && !g_no_sub16_gu && L.n_embd == 2560 && gu_split(L.gu_type)) ? 16 : GU_ROWS;
    const dim3 ggu((unsigned) ((2 * L.n_ff + gu_rows - 1) / gu_rows), (unsigned) gy);
    const int em0 = (grid_groups > 0 && grid_groups < cap_groups) ? 0 : exp_mode();
    bool gu_done = false;
    const bool fused_gu = em0 == 8 && L.gu_type == 18 && L.n_ff % 32 == 0;
    if (fused_gu && g_exp_phase != 2) {
        const dim3 gl((unsigned) (L.n_ff / 32), (unsigned) cap_groups);
        const size_t sh = (size_t) LDS_NT * (size_t) (L.n_embd / 32) * sizeof(block_q8_1);
        native_gu_fused_kernel<18><<<gl, 256, sh, s>>>(grp_ptr, grp_start, n_groups, ent_tok, X, L, hq);
        check("native_expert_grouped/gu fused");
        gu_done = true;
    }
    // 147-verify STRATA_GU_SWIGLU=1: gate/up + SwiGLU + q8_1 in one launch where the CUDA layout's 16-lane kernel would run
    const bool gs_layout = !fused_gu && em0 == 0;
    if (gs_layout && g_exp_phase == 0 && !v1 && gu_swiglu_on() && !(expdec_mode() && !g_old_kernels && expdec_gu_ok(L))) {
        bool gs_done = false;
        switch (L.gu_type) {
#define STRATA_GS(T) case T: gs_done = launch_gu_swiglu<T>(L, gy, s, grp_ptr, grp_start, n_groups, ent_tok, X, hq); break;
            STRATA_GU_FMTS(STRATA_GS)
#undef STRATA_GS
            default: break;
        }
        gu_done = gs_done;
        if (gs_done) check("native_expert_grouped/gu+swiglu");
    }
    if (g_exp_phase != 2 && !gu_done) {
    const bool lds_gu = em0 == 8 && L.gu_type == 18;
    if (lds_gu) {
        const dim3 gl((unsigned) ((2 * L.n_ff + LDS_RB - 1) / LDS_RB), (unsigned) cap_groups);
        const size_t sh = (size_t) LDS_NT * (size_t) (L.n_embd / 32) * sizeof(block_q8_1);
        native_gu_lds_kernel<18, 1, true><<<gl, 256, sh, s>>>(grp_ptr, grp_start, n_groups, ent_tok, X, L, gate, up);
    } else if (!expdec_gu(L, gy, cap_groups, s, grp_ptr, grp_start, n_groups, ent_tok, X, gate, up)) {   // STRATA_EXPDEC=1
        switch (L.gu_type) {
#define STRATA_GU(T) case T: launch_gu<T>(ggu, s, grp_ptr, grp_start, n_groups, ent_tok, X, L, gate, up); break;
            STRATA_GU_FMTS(STRATA_GU)
#undef STRATA_GU
            default: std::fprintf(stderr, "native_expert_grouped: gate/up type %d\n", L.gu_type); std::exit(1);
        }
    }
    }
    check("native_expert_grouped/gu");
    const long long nh = (long long) cap_entries * L.n_ff;
    const bool sw_v1 = v1;
    if (sw_v1) {
        swiglu_entries_kernel<<<(unsigned) ((nh + 255) / 256), 256, 0, s>>>(gate, up, h, nh);
        quantize_q8_1_kernel<<<(unsigned) ((nh + 255) / 256), 256, 0, s>>>(h, hq, nh);
    } else {
        swiglu_q8_1_entries_kernel<<<(unsigned) ((nh + 255) / 256), 256, 0, s>>>(gate, up, grp_start, n_groups,
                                                                                (int) L.n_ff, hq);
    }
    check("native_expert_grouped/swiglu");
    }
    if (g_exp_phase == 1) return;
    const int d_rows = (!g_old_kernels && !g_no_sub16_gu && L.n_ff == 640) ? (L.d_type == 20 ? 32 : (L.d_type == 42 ? 16 : 8)) : 8;
    const dim3 gd((unsigned) ((L.n_embd + d_rows - 1) / d_rows), (unsigned) gy);
    const bool lds_down_ok = em0 == 8 && (L.d_type == 42 || L.d_type == 20);
    if (lds_down_ok) {
        const dim3 gl((unsigned) ((L.n_embd + LDS_RB - 1) / LDS_RB), (unsigned) cap_groups);
        const size_t sh = (size_t) LDS_NT * (size_t) (L.n_ff / 32) * sizeof(block_q8_1);
        if (L.d_type == 20) native_down_lds_kernel<20><<<gl, 256, sh, s>>>(grp_ptr, grp_start, n_groups, ent_dst, hq, L, out);
        else native_down_lds_kernel<42><<<gl, 256, sh, s>>>(grp_ptr, grp_start, n_groups, ent_dst, hq, L, out);
        check("native_expert_grouped/down");
        return;
    }
    if (!expdec_down(L, gy, cap_groups, s, grp_ptr, grp_start, n_groups, ent_dst, hq, out))   // STRATA_EXPDEC=1
    switch (L.d_type) {
#define STRATA_DOWN(T) case T: launch_down<T>(gd, s, grp_ptr, grp_start, n_groups, ent_dst, hq, L, out); break;
        STRATA_D_FMTS(STRATA_DOWN)
#undef STRATA_DOWN
        default: std::fprintf(stderr, "native_expert_grouped: down type %d\n", L.d_type); std::exit(1);
    }
    check("native_expert_grouped/down");
}

}  // namespace strata::kernels
