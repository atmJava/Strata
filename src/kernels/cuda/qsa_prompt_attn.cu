// src/kernels/cuda/qsa_prompt_attn.cu - see include/strata/kernels/qsa_prompt_attn.hpp.
#include "strata/core/emulate.hpp"
#include "strata/core/sm75_series.hpp"
#include "strata/core/geometry.hpp"
#include "strata/kernels/qsa_prompt_attn.hpp"
#include "strata/kernels/kv_q8.hpp"
#include "strata/kernels/kv_q4.hpp"

#include <cuda_fp16.h>
#include <cuda_runtime.h>
#include <algorithm>
#include <cmath>

#include <cstdio>
#include <cstdlib>
#include <type_traits>

namespace strata::kernels {
namespace {

constexpr int HD = 256;           // head_dim
constexpr int G = 12;             // query heads per KV head
static_assert(HD == strata::core::kCompiledGeometry.head_dim && G == strata::core::kCompiledGeometry.n_head / strata::core::kCompiledGeometry.n_head_kv, "qsa_prompt_attn is compiled for core/geometry.hpp");
#ifndef D1_CH
#define D1_CH 32
#endif
constexpr int CH = D1_CH;         // cells per chunk
constexpr int THREADS = 128;      // 4 warps: scores by cell (8 each), p.v by dimension (64 each = one int8 scale group)
constexpr int QS = HD + 8;        // q row stride in halves (bank-conflict-free fragment loads)

// m16n8k16 with f16 inputs needs sm_80.  Turing (sm_75) has m16n8k8 with the SAME A/B/C register mapping, so the
// k=16 step is two k=8 steps on the fragments as they are already laid out: a[0]/a[1] are rows gid/gid+8 at k columns
// 2*tig..2*tig+1 (b[0]'s k rows), a[2]/a[3] the same rows at k columns 2*tig+8..2*tig+9 (b[1]'s k rows).  The
// products then add into the same FP32 C registers in the order hi-part-0, hi-part-1, which is the order the k16
// instruction accumulates in as well - but the sum now rounds twice, so the two paths do not agree bit for bit.
__device__ __forceinline__ void mma16816(float* c, const uint32_t* a, const uint32_t* b) {
    asm volatile("mma.sync.aligned.m16n8k8.row.col.f32.f16.f16.f32 {%0,%1,%2,%3}, {%4,%5}, {%6}, {%0,%1,%2,%3};\n"
                 : "+f"(c[0]), "+f"(c[1]), "+f"(c[2]), "+f"(c[3])
                 : "r"(a[0]), "r"(a[1]), "r"(b[0]));
    asm volatile("mma.sync.aligned.m16n8k8.row.col.f32.f16.f16.f32 {%0,%1,%2,%3}, {%4,%5}, {%6}, {%0,%1,%2,%3};\n"
                 : "+f"(c[0]), "+f"(c[1]), "+f"(c[2]), "+f"(c[3])
                 : "r"(a[2]), "r"(a[3]), "r"(b[1]));
}

// Two int8 codes (low byte first) as an exact half2: 1024 + (c + 128) built in the mantissa, minus 1152.
__device__ __forceinline__ uint32_t i8x2_to_h2(uint32_t x) {
    uint32_t y = ((x & 0xffu) | ((x & 0xff00u) << 8)) ^ 0x00800080u;
    y |= 0x64006400u;
    __half2 h = *reinterpret_cast<__half2*>(&y);
    h = __hsub2(h, __halves2half2(__float2half(1152.f), __float2half(1152.f)));
    return *reinterpret_cast<uint32_t*>(&h);
}

__device__ __forceinline__ uint32_t pack_h2(float lo_k, float hi_k) {   // element k in the low half
    __half2 h = __floats2half2_rn(lo_k, hi_k);
    return *reinterpret_cast<uint32_t*>(&h);
}

// KV_MODE 1: int8 codes + fp16 scale per 64 values. KV_MODE 0: fp16 values (scales 1).
// KV_MODE 3 (hybrid K8V4): K as mode 1, V as mode 0 - the row's q4_0 blocks are dequantized to fp16 at
// gather, so everything downstream of the load is the mode-0 V path; the caller un-rotates the output.
// KV_MODE 4 (Q4_0 K and V, `--kv q4_0`): mode 1 with a scale per 32 values - each q4_0 block's codes enter as exact
// int8 (code - 8) and its fp16 scale is applied in FP32, as mode 1's are; the caller rotates q and un-rotates the
// output (kv_q4.hpp).
template <int KV_MODE, bool PF = false>
struct Smem {
    using KElem = typename std::conditional<KV_MODE == 0, __half, int8_t>::type;
    using VElem = typename std::conditional<KV_MODE == 1 || KV_MODE == 4, int8_t, __half>::type;
    static constexpr int KROW = KV_MODE == 0 ? HD + 8 : HD + 16;   // elements; 16-byte aligned rows, banks spread
    static constexpr int VROW = (KV_MODE == 1 || KV_MODE == 4) ? HD + 16 : HD + 8;
    static constexpr int NG = KV_MODE == 4 ? HD / QK4_0 : 4;      // scale groups per row (q4_0: 8 of 32, else 4 of 64)
    __half qh[16][QS];
    __half ql[16][QS];
    // int8 K/V (mode 1): K is read only by the scores and V only by p.v, so they share one buffer - V is stored over K
    // after the scores (prompt_attn_kernel).  37.9 -> 29.2 KB: two blocks fit an SM's 64 KB on Turing instead of one.
    struct Rows { KElem k[CH][KROW]; VElem v[CH][VROW]; };
    struct SharedRows { union { KElem k[CH][KROW]; VElem v[CH][VROW]; }; };
    typename std::conditional<KV_MODE == 1 && PF, SharedRows, Rows>::type kv;
    float ks[CH][NG];
    float vs[CH][NG];
    float s[16][CH + 1];
    float qmax[THREADS / 32];
    float alpha[16];
    float lsum[16];
    float mrow[16];
    long long row[CH];
};

template <int KV_MODE, bool PF = false>
__global__ void __launch_bounds__(THREADS) prompt_attn_kernel(const float* __restrict__ q, QsaAttnPools p,
                                                              const int32_t* __restrict__ ids,
                                                              const int32_t* __restrict__ steps, int n_kv_heads,
                                                              int page_size, float scale_log2, float* __restrict__ attn,
                                                              int cap) {
    extern __shared__ __align__(16) unsigned char smem_raw[];
    Smem<KV_MODE, PF>& S = *reinterpret_cast<Smem<KV_MODE, PF>*>(smem_raw);
    const int qi = blockIdx.x, kvh = blockIdx.y;
    const int n_head = n_kv_heads * G;
    q += (size_t) qi * n_head * HD + (size_t) kvh * G * HD;
    attn += (size_t) qi * n_head * HD + (size_t) kvh * G * HD;
    ids += (size_t) qi * cap;
    const int n = __ldg(steps + (size_t) qi * kStepCount + kStepWidth);
    const int t = threadIdx.x, lane = t & 31, warp = t >> 5;
    const int gid = lane >> 2, tig = lane & 3;

    // q: 12 heads + 4 zero rows, scaled by a power of two that puts its largest value near 2^14 (exact, and the
    // lo halves stay out of FP16's subnormal range), then split into hi + lo halves
    float qm = 0.0f;
    for (int i = t; i < G * HD; i += THREADS) qm = fmaxf(qm, fabsf(q[i]));
#pragma unroll
    for (int o = 16; o > 0; o >>= 1) qm = fmaxf(qm, __shfl_xor_sync(0xffffffffu, qm, o));
    if (lane == 0) S.qmax[warp] = qm;
    __syncthreads();
    qm = fmaxf(fmaxf(S.qmax[0], S.qmax[1]), fmaxf(S.qmax[2], S.qmax[3]));
    int qe = 0;
    if (qm > 0.0f) frexpf(qm, &qe);                 // qm < 2^qe
    const float qup = ldexpf(1.0f, 14 - qe), qdown = ldexpf(scale_log2, qe - 14);
    for (int i = t; i < 16 * HD; i += THREADS) {
        const int h = i / HD, d = i % HD;
        const float x = h < G ? q[(size_t) h * HD + d] * qup : 0.0f;
        const __half hi = __float2half_rn(x);
        S.qh[h][d] = hi;
        S.ql[h][d] = __float2half_rn(x - __half2float(hi));
    }
    if (t < 16) { S.mrow[t] = -INFINITY; S.lsum[t] = 0.0f; }

    float acc[8][4];
#pragma unroll
    for (int j = 0; j < 8; ++j) acc[j][0] = acc[j][1] = acc[j][2] = acc[j][3] = 0.0f;

    // int8 K/V: the next chunk's rows are loaded into registers while this chunk computes (Turing has no cp.async, and
    // at 1 block per SM the gather's latency was the kernel's largest stall), then stored to shared memory exactly as
    // the gather below stores them: the same bytes in the same places, the same arithmetic after.
    constexpr bool PREFETCH = KV_MODE == 1 && PF;
    constexpr int KP = HD / 16;                               // 16-byte pieces per int8 row
    constexpr int PPT = PREFETCH ? CH * KP / THREADS : 1;     // pieces per thread
    static_assert(!PREFETCH || (CH * KP) % THREADS == 0 && CH * 4 == THREADS, "prefetch layout");
    uint4 pk[PPT], pv[PPT];
    float pa = 0.0f, pb = 0.0f;
    auto row_of = [&](int c0, int c) -> long long {          // as S.row below; -1 past the chunk's cells
        if (c >= min(CH, n - c0)) return -1;
        const int cell = ids[c0 + c];
        const long long page = (long long) p.page_table[cell / page_size];
        return (page * n_kv_heads + kvh) * page_size + (cell % page_size);
    };
    auto fetch = [&](int c0) {
        if constexpr (PREFETCH) {
#pragma unroll
            for (int j = 0; j < PPT; ++j) {
                const int i = t + j * THREADS, c = i / KP, pc = i % KP;
                const long long r = row_of(c0, c);
                pk[j] = r >= 0 ? __ldg(reinterpret_cast<const uint4*>(p.k_q + r * HD) + pc) : make_uint4(0, 0, 0, 0);
                pv[j] = r >= 0 ? __ldg(reinterpret_cast<const uint4*>(p.v_q + r * HD) + pc) : make_uint4(0, 0, 0, 0);
            }
            const int c = t / 4, g = t % 4;
            const long long r = row_of(c0, c);
            pa = r >= 0 ? __half2float(__ushort_as_half(p.k_scale[r * (HD / KV_Q8_GROUP) + g])) : 0.0f;
            pb = r >= 0 ? __half2float(__ushort_as_half(p.v_scale[r * (HD / KV_Q8_GROUP) + g])) : 0.0f;
        }
    };
    if constexpr (PREFETCH) fetch(0);

    for (int c0 = 0; c0 < n; c0 += CH) {
        const int nh = min(CH, n - c0);
        if constexpr (PREFETCH) {
            __syncthreads();   // the previous chunk's p.v is done with k, v, s
#pragma unroll
            for (int j = 0; j < PPT; ++j) {
                const int i = t + j * THREADS, c = i / KP, pc = i % KP;
                *reinterpret_cast<uint4*>(reinterpret_cast<unsigned char*>(&S.kv.k[c][0]) + pc * 16) = pk[j];
            }
            S.ks[t / 4][t % 4] = pa;
            S.vs[t / 4][t % 4] = pb;
            __syncthreads();
        } else {
        if (t < CH) {
            long long r = -1;
            if (t < nh) {
                const int cell = ids[c0 + t];
                const long long page = (long long) p.page_table[cell / page_size];
                r = (page * n_kv_heads + kvh) * page_size + (cell % page_size);
            }
            S.row[t] = r;
        }
        __syncthreads();   // rows ready; the previous chunk's p.v is done with k, v, s
        // gather the chunk's K and V rows (16-byte pieces; K8V4's V as q4_0 blocks dequantized to fp16)
        // and their scales
        if constexpr (KV_MODE == 4) {
            // q4_0 K and V: one (K or V, cell, block) per item - the block's 16 code bytes (2-byte aligned: eight
            // 16-bit loads) become 32 exact int8 codes (element j in the low nibble of byte j, j + 16 in the high
            // one), its fp16 scale goes to ks/vs
            constexpr int BLKS = HD / QK4_0;
            constexpr int BYTES = BLKS * (int) sizeof(block_q4_0);
            for (int i = t; i < 2 * CH * BLKS; i += THREADS) {
                const int kv = i / (CH * BLKS), rem = i % (CH * BLKS), c = rem / BLKS, b = rem % BLKS;
                const long long r = S.row[c];
                uint4 lo = make_uint4(0, 0, 0, 0), hi = make_uint4(0, 0, 0, 0);
                float d = 0.0f;
                if (r >= 0) {
                    const block_q4_0* blk = reinterpret_cast<const block_q4_0*>((kv == 0 ? p.k_q4 : p.v_q4) + r * BYTES) + b;
                    d = __half2float(__ushort_as_half(__ldg(&blk->d)));
                    const uint16_t* q16 = reinterpret_cast<const uint16_t*>(blk->qs);
                    uint32_t w[4];
#pragma unroll
                    for (int j = 0; j < 4; ++j)
                        w[j] = (uint32_t) __ldg(q16 + 2 * j) | ((uint32_t) __ldg(q16 + 2 * j + 1) << 16);
                    // per byte: the low nibble minus 8 is element j, the high one minus 8 element j + 16
                    lo = make_uint4(__vsub4(w[0] & 0x0F0F0F0Fu, 0x08080808u), __vsub4(w[1] & 0x0F0F0F0Fu, 0x08080808u),
                                    __vsub4(w[2] & 0x0F0F0F0Fu, 0x08080808u), __vsub4(w[3] & 0x0F0F0F0Fu, 0x08080808u));
                    hi = make_uint4(__vsub4((w[0] >> 4) & 0x0F0F0F0Fu, 0x08080808u),
                                    __vsub4((w[1] >> 4) & 0x0F0F0F0Fu, 0x08080808u),
                                    __vsub4((w[2] >> 4) & 0x0F0F0F0Fu, 0x08080808u),
                                    __vsub4((w[3] >> 4) & 0x0F0F0F0Fu, 0x08080808u));
                }
                int8_t* dst = kv == 0 ? &S.kv.k[c][b * QK4_0] : &S.kv.v[c][b * QK4_0];
                reinterpret_cast<uint4*>(dst)[0] = lo;
                reinterpret_cast<uint4*>(dst)[1] = hi;
                (kv == 0 ? S.ks : S.vs)[c][b] = d;
            }
        } else {
            constexpr int KPIECES = HD * (int) sizeof(typename Smem<KV_MODE>::KElem) / 16;   // per K row
            for (int i = t; i < CH * KPIECES; i += THREADS) {
                const int c = i / KPIECES, pc = i % KPIECES;
                const long long r = S.row[c];
                uint4 kx = make_uint4(0, 0, 0, 0);
                if (r >= 0) {
                    if constexpr (KV_MODE == 0)
                        kx = __ldg(reinterpret_cast<const uint4*>(p.k_pool + r * HD) + pc);
                    else   // modes 1 and 3: the K side is INT8
                        kx = __ldg(reinterpret_cast<const uint4*>(p.k_q + r * HD) + pc);
                }
                *reinterpret_cast<uint4*>(reinterpret_cast<unsigned char*>(&S.kv.k[c][0]) + pc * 16) = kx;
            }
            if constexpr (KV_MODE == 3) {   // V: dequantize the row's q4_0 blocks straight into the fp16 V row
                constexpr int BLKS = HD / QK4_0;
                constexpr int BYTES = BLKS * (int) sizeof(block_q4_0);
                for (int i = t; i < CH * BLKS; i += THREADS) {
                    const int c = i / BLKS, b = i % BLKS;
                    const long long r = S.row[c];
#pragma unroll
                    for (int j = 0; j < QK4_0; ++j) S.kv.v[c][b * QK4_0 + j] = __half(0);
                    if (r >= 0) {
                        const block_q4_0* blk = reinterpret_cast<const block_q4_0*>(p.v_q4 + r * BYTES) + b;
                        const float d = __half2float(__ushort_as_half(blk->d));
#pragma unroll
                        for (int j = 0; j < QK4_0 / 2; ++j) {
                            S.kv.v[c][b * QK4_0 + j] = __float2half_rn((float) ((int)(blk->qs[j] & 0x0F) - 8) * d);
                            S.kv.v[c][b * QK4_0 + j + QK4_0 / 2] =
                                __float2half_rn((float) ((int)(blk->qs[j] >> 4) - 8) * d);
                        }
                    }
                }
            } else {
                constexpr int VPIECES = HD * (int) sizeof(typename Smem<KV_MODE>::VElem) / 16;   // per V row
                for (int i = t; i < CH * VPIECES; i += THREADS) {
                    const int c = i / VPIECES, pc = i % VPIECES;
                    const long long r = S.row[c];
                    uint4 vx = make_uint4(0, 0, 0, 0);
                    if (r >= 0) {
                        if constexpr (KV_MODE == 1)
                            vx = __ldg(reinterpret_cast<const uint4*>(p.v_q + r * HD) + pc);
                        else
                            vx = __ldg(reinterpret_cast<const uint4*>(p.v_pool + r * HD) + pc);
                    }
                    *reinterpret_cast<uint4*>(reinterpret_cast<unsigned char*>(&S.kv.v[c][0]) + pc * 16) = vx;
                }
            }
            for (int i = t; i < CH * 4; i += THREADS) {
                const int c = i / 4, g = i % 4;
                const long long r = S.row[c];
                float a = 0.0f, b = 0.0f;
                if (r >= 0) {
                    if constexpr (KV_MODE == 1) {
                        a = __half2float(__ushort_as_half(p.k_scale[r * (HD / KV_Q8_GROUP) + g]));
                        b = __half2float(__ushort_as_half(p.v_scale[r * (HD / KV_Q8_GROUP) + g]));
                    } else if constexpr (KV_MODE == 3) {   // K as int8, V dequantized to fp16 (scale 1)
                        a = __half2float(__ushort_as_half(p.k_scale[r * (HD / KV_Q8_GROUP) + g]));
                        b = 1.0f;
                    } else {
                        a = b = 1.0f;
                    }
                }
                S.ks[c][g] = a;
                S.vs[c][g] = b;
            }
        }
        __syncthreads();
        }
        // scores: warp w takes cells 8w..8w+7 (one n-tile) over all 256 dims, per scale group (64 dims; q4_0's 32)
        constexpr int NG = Smem<KV_MODE>::NG, KPG = HD / 16 / NG;   // groups per row, 16-dim MMA steps per group
#pragma unroll
        for (int nt = 0; nt < CH / 32; ++nt) {
            const int cb = (warp + 4 * nt) * 8;
            float sc[4] = {0.f, 0.f, 0.f, 0.f};
#pragma unroll
            for (int g = 0; g < NG; ++g) {
                float tg[4] = {0.f, 0.f, 0.f, 0.f};
#pragma unroll
                for (int kk = 0; kk < KPG; ++kk) {
                    const int k0 = (g * KPG + kk) * 16;
                    uint32_t ah[4], al[4], b[2];
                    ah[0] = *reinterpret_cast<const uint32_t*>(&S.qh[gid][k0 + 2 * tig]);
                    ah[1] = *reinterpret_cast<const uint32_t*>(&S.qh[gid + 8][k0 + 2 * tig]);
                    ah[2] = *reinterpret_cast<const uint32_t*>(&S.qh[gid][k0 + 2 * tig + 8]);
                    ah[3] = *reinterpret_cast<const uint32_t*>(&S.qh[gid + 8][k0 + 2 * tig + 8]);
                    al[0] = *reinterpret_cast<const uint32_t*>(&S.ql[gid][k0 + 2 * tig]);
                    al[1] = *reinterpret_cast<const uint32_t*>(&S.ql[gid + 8][k0 + 2 * tig]);
                    al[2] = *reinterpret_cast<const uint32_t*>(&S.ql[gid][k0 + 2 * tig + 8]);
                    al[3] = *reinterpret_cast<const uint32_t*>(&S.ql[gid + 8][k0 + 2 * tig + 8]);
                    if constexpr (KV_MODE != 0) {   // modes 1 and 3: the K side is INT8 codes
                        b[0] = i8x2_to_h2(*reinterpret_cast<const uint16_t*>(&S.kv.k[cb + gid][k0 + 2 * tig]));
                        b[1] = i8x2_to_h2(*reinterpret_cast<const uint16_t*>(&S.kv.k[cb + gid][k0 + 2 * tig + 8]));
                    } else {
                        b[0] = *reinterpret_cast<const uint32_t*>(&S.kv.k[cb + gid][k0 + 2 * tig]);
                        b[1] = *reinterpret_cast<const uint32_t*>(&S.kv.k[cb + gid][k0 + 2 * tig + 8]);
                    }
                    mma16816(tg, ah, b);
#ifndef D1_NO_QLO
                    mma16816(tg, al, b);
#endif
                }
                const float s0 = S.ks[cb + 2 * tig][g], s1 = S.ks[cb + 2 * tig + 1][g];
                sc[0] = fmaf(tg[0], s0, sc[0]);
                sc[1] = fmaf(tg[1], s1, sc[1]);
                sc[2] = fmaf(tg[2], s0, sc[2]);
                sc[3] = fmaf(tg[3], s1, sc[3]);
            }
            const int c = cb + 2 * tig;
            S.s[gid][c] = c < nh ? sc[0] * qdown : -INFINITY;
            S.s[gid][c + 1] = c + 1 < nh ? sc[1] * qdown : -INFINITY;
            S.s[gid + 8][c] = c < nh ? sc[2] * qdown : -INFINITY;
            S.s[gid + 8][c + 1] = c + 1 < nh ? sc[3] * qdown : -INFINITY;
        }
        __syncthreads();
        if constexpr (PREFETCH) {   // the scores are done with K: V goes over it (read after the softmax's barrier)
#pragma unroll
            for (int j = 0; j < PPT; ++j) {
                const int i = t + j * THREADS, c = i / KP, pc = i % KP;
                *reinterpret_cast<uint4*>(reinterpret_cast<unsigned char*>(&S.kv.v[c][0]) + pc * 16) = pv[j];
            }
            if (c0 + CH < n) fetch(c0 + CH);   // the next chunk's rows, in flight while this one finishes
        }
        // online softmax: row t/8, 4 cells per thread, 8 threads per row (lanes 8r..8r+7 of a warp)
        {
            constexpr int PER = CH / 8;
            const int r = t >> 3, sub = t & 7;
            float x[PER], mx = -INFINITY;
#pragma unroll
            for (int j = 0; j < PER; ++j) { x[j] = S.s[r][sub * PER + j]; mx = fmaxf(mx, x[j]); }
#pragma unroll
            for (int o = 1; o < 8; o <<= 1) mx = fmaxf(mx, __shfl_xor_sync(0xffffffffu, mx, o));
            const float m_old = S.mrow[r];
            const float m_new = fmaxf(m_old, mx);
            float sum = 0.0f;
#pragma unroll
            for (int j = 0; j < PER; ++j) {
                const float e = x[j] == -INFINITY ? 0.0f : exp2f(x[j] - m_new);
                S.s[r][sub * PER + j] = e;
                sum += e;
            }
#pragma unroll
            for (int o = 1; o < 8; o <<= 1) sum += __shfl_xor_sync(0xffffffffu, sum, o);
            __syncwarp();
            if (sub == 0) {
                const float a = m_old == -INFINITY ? 0.0f : exp2f(m_old - m_new);
                S.alpha[r] = a;
                S.lsum[r] = fmaf(S.lsum[r], a, sum);
                S.mrow[r] = m_new;
            }
        }
        __syncthreads();
        // p.v: warp w owns dims [64w, 64w+64), which is int8 scale group w (q4_0: groups 2w and 2w+1, four n-tiles
        // each). A group's scale is folded into p relative to the chunk's largest magnitude (q4_0's scales are signed:
        // ggml's d = max / -8), times 2^14 (|p'| <= 2^14: inside FP16's range, its lo half out of the subnormals); the
        // chunk's sum is then added to the running one in FP32 with the factor taken back out
        {
            constexpr int GPW = NG / 4, JPG = 8 / GPW;   // scale groups per warp, n-tiles per group
            float vdown_g[GPW];
            float tmp[8][4];
#pragma unroll
            for (int j = 0; j < 8; ++j) tmp[j][0] = tmp[j][1] = tmp[j][2] = tmp[j][3] = 0.0f;
#pragma unroll
            for (int gi = 0; gi < GPW; ++gi) {
            const int vg = warp * GPW + gi;
            float vmax = 0.0f;
#pragma unroll
            for (int c = lane; c < CH; c += 32) vmax = fmaxf(vmax, fabsf(S.vs[c][vg]));
#pragma unroll
            for (int o = 16; o > 0; o >>= 1) vmax = fmaxf(vmax, __shfl_xor_sync(0xffffffffu, vmax, o));
            const float vup = vmax > 0.0f ? 16384.0f / vmax : 0.0f;
            vdown_g[gi] = vmax * (1.0f / 16384.0f);
#pragma unroll
            for (int ks = 0; ks < CH / 16; ++ks) {
                const int cA = ks * 16 + 2 * tig, cB = cA + 8;
                const float w0 = S.vs[cA][vg] * vup, w1 = S.vs[cA + 1][vg] * vup, w2 = S.vs[cB][vg] * vup,
                            w3 = S.vs[cB + 1][vg] * vup;
                const float p00 = S.s[gid][cA] * w0, p01 = S.s[gid][cA + 1] * w1;
                const float p10 = S.s[gid + 8][cA] * w0, p11 = S.s[gid + 8][cA + 1] * w1;
                const float p02 = S.s[gid][cB] * w2, p03 = S.s[gid][cB + 1] * w3;
                const float p12 = S.s[gid + 8][cB] * w2, p13 = S.s[gid + 8][cB + 1] * w3;
                uint32_t ah[4], al[4];
                ah[0] = pack_h2(p00, p01);
                ah[1] = pack_h2(p10, p11);
                ah[2] = pack_h2(p02, p03);
                ah[3] = pack_h2(p12, p13);
                {
                    const __half2* h = reinterpret_cast<const __half2*>(ah);
                    float2 f;
                    f = __half22float2(h[0]); al[0] = pack_h2(p00 - f.x, p01 - f.y);
                    f = __half22float2(h[1]); al[1] = pack_h2(p10 - f.x, p11 - f.y);
                    f = __half22float2(h[2]); al[2] = pack_h2(p02 - f.x, p03 - f.y);
                    f = __half22float2(h[3]); al[3] = pack_h2(p12 - f.x, p13 - f.y);
                }
#pragma unroll
                for (int jj = 0; jj < JPG; ++jj) {
                    const int j = gi * JPG + jj;
                    const int d = warp * 64 + j * 8 + gid;
                    uint32_t b[2];
                    if constexpr (KV_MODE == 1 || KV_MODE == 4) {
                        const uint32_t x0 = (uint8_t) S.kv.v[cA][d] | ((uint32_t) (uint8_t) S.kv.v[cA + 1][d] << 8);
                        const uint32_t x1 = (uint8_t) S.kv.v[cB][d] | ((uint32_t) (uint8_t) S.kv.v[cB + 1][d] << 8);
                        b[0] = i8x2_to_h2(x0);
                        b[1] = i8x2_to_h2(x1);
                    } else {
                        const __half2 h0 = __halves2half2(S.kv.v[cA][d], S.kv.v[cA + 1][d]);
                        const __half2 h1 = __halves2half2(S.kv.v[cB][d], S.kv.v[cB + 1][d]);
                        b[0] = *reinterpret_cast<const uint32_t*>(&h0);
                        b[1] = *reinterpret_cast<const uint32_t*>(&h1);
                    }
                    mma16816(tmp[j], ah, b);
#ifndef D1_NO_PLO
                    mma16816(tmp[j], al, b);
#endif
                }
            }
            }
            const float a0 = S.alpha[gid], a1 = S.alpha[gid + 8];
#pragma unroll
            for (int j = 0; j < 8; ++j) {
                const float vdown = vdown_g[j / JPG];
                acc[j][0] = fmaf(acc[j][0], a0, tmp[j][0] * vdown);
                acc[j][1] = fmaf(acc[j][1], a0, tmp[j][1] * vdown);
                acc[j][2] = fmaf(acc[j][2], a1, tmp[j][2] * vdown);
                acc[j][3] = fmaf(acc[j][3], a1, tmp[j][3] * vdown);
            }
        }
    }
    __syncthreads();
    const float l0 = S.lsum[gid], l1 = S.lsum[gid + 8];
    const float i0 = l0 > 0.0f ? 1.0f / l0 : 0.0f, i1 = l1 > 0.0f ? 1.0f / l1 : 0.0f;
#pragma unroll
    for (int j = 0; j < 8; ++j) {
        const int d = warp * 64 + j * 8 + 2 * tig;
        *reinterpret_cast<float2*>(attn + (size_t) gid * HD + d) = make_float2(acc[j][0] * i0, acc[j][1] * i0);
        if (gid + 8 < G)
            *reinterpret_cast<float2*>(attn + (size_t) (gid + 8) * HD + d) = make_float2(acc[j][2] * i1, acc[j][3] * i1);
    }
}

// ---- int8 KV on sm_75: warp w owns dims [64w, 64w+64) for both q.k and p.v, which is also int8 scale group w. It
// gathers only its own 64-byte slice of each K and V row, stages the next chunk in registers (Turing has no
// cp.async), and keeps q in registers. Only the q.k partial sums cross warps, in a fixed order.
constexpr int CH2 = 32;

// Turing (register staging): one stage - the next chunk's pieces wait in registers until this chunk's p.v is done -
// and the q.k partials of cell group nt (8 cells) in the 512 bytes of the warp's own K slice that held those cells
// (read by then), so a block takes ~19.7 KB and three fit a Turing SM (one did, at 45.6 KB: four warps on four
// schedulers, every latency exposed)
struct Smem2T {
    int8_t kv[1][4][2][CH2][64];
    float sc[1][4][2][CH2];
    float p[16][CH2 + 1];
    float qmax[4];
    float alpha[16];
    float lsum[16];
    float mrow[16];
};

__device__ __forceinline__ int swz(int cell, int byte) {   // byte offset of (cell, byte) in a stage slice
    return cell * 64 + ((((byte >> 4) ^ (cell >> 1)) & 3) << 4) + (byte & 15);
}

__global__ void __launch_bounds__(THREADS) prompt_attn_i8_kernel(const float* __restrict__ q, QsaAttnPools p,
                                                                 const int32_t* __restrict__ ids,
                                                                 const int32_t* __restrict__ steps, int n_kv_heads,
                                                                 int page_size, float scale_log2,
                                                                 float* __restrict__ attn, int cap) {
    extern __shared__ __align__(16) unsigned char smem_raw[];
    Smem2T& S = *reinterpret_cast<Smem2T*>(smem_raw);
    // the partial of (row r, cell c) of warp w: cell group c / 8's 512 bytes of w's K slice, [row][cell % 8]
#define PA2_PART(w, r, c) reinterpret_cast<const float*>(&S.kv[0][w][0][0][0])[((c) >> 3) * 128 + (r) * 8 + ((c) & 7)]
    const int qi = blockIdx.x, kvh = blockIdx.y;
    const int n_head = n_kv_heads * G;
    q += (size_t) qi * n_head * HD + (size_t) kvh * G * HD;
    attn += (size_t) qi * n_head * HD + (size_t) kvh * G * HD;
    ids += (size_t) qi * cap;
    const int n = __ldg(steps + (size_t) qi * kStepCount + kStepWidth);
    const int t = threadIdx.x, lane = t & 31, warp = t >> 5;
    const int gid = lane >> 2, tig = lane & 3;
    const int dim0 = warp * 64;

    // q: the power-of-two prescale over all 12 heads (as v1), then this warp's 64 dims as hi/lo A fragments
    float qm = 0.0f;
    for (int i = t; i < G * HD; i += THREADS) qm = fmaxf(qm, fabsf(q[i]));
#pragma unroll
    for (int o = 16; o > 0; o >>= 1) qm = fmaxf(qm, __shfl_xor_sync(0xffffffffu, qm, o));
    if (lane == 0) S.qmax[warp] = qm;
    if (t < 16) { S.mrow[t] = -INFINITY; S.lsum[t] = 0.0f; }
    __syncthreads();
    qm = fmaxf(fmaxf(S.qmax[0], S.qmax[1]), fmaxf(S.qmax[2], S.qmax[3]));
    int qe = 0;
    if (qm > 0.0f) frexpf(qm, &qe);
    const float qup = ldexpf(1.0f, 14 - qe), qdown = ldexpf(scale_log2, qe - 14);
    uint32_t qh[4][4], ql[4][4];
#pragma unroll
    for (int kk = 0; kk < 4; ++kk) {
#pragma unroll
        for (int r = 0; r < 4; ++r) {
            const int row = gid + (r & 1) * 8, col = dim0 + kk * 16 + 2 * tig + (r >> 1) * 8;
            float2 x = make_float2(0.f, 0.f);
            if (row < G) x = *reinterpret_cast<const float2*>(q + (size_t) row * HD + col);
            x.x *= qup;
            x.y *= qup;
            const __half2 hi = __floats2half2_rn(x.x, x.y);
            const float2 hf = __half22float2(hi);
            const __half2 lo = __floats2half2_rn(x.x - hf.x, x.y - hf.y);
            qh[kk][r] = *reinterpret_cast<const uint32_t*>(&hi);
            ql[kk][r] = *reinterpret_cast<const uint32_t*>(&lo);
        }
    }

    // the chunk pipeline: cells two chunks ahead, their pool rows one chunk ahead, the data in registers one ahead
    const int n_chunks = (n + CH2 - 1) / CH2;
    auto cell_of = [&](int c) -> int { return c < n ? __ldg(ids + c) : -1; };
    auto row_of = [&](int cell) -> long long {
        if (cell < 0) return -1;
        const long long page = (long long) __ldg(p.page_table + cell / page_size);
        return (page * n_kv_heads + kvh) * page_size + (cell % page_size);
    };
    // The warp's next pieces wait in registers and are stored into shared memory after this chunk's p.v.
    uint4 rk[4], rv[4];
    auto store_stage = [&](int st) {
#pragma unroll
        for (int j = 0; j < 4; ++j) {
            const int idx = lane + 32 * j, cell = idx >> 2, pc = idx & 3;
            *reinterpret_cast<uint4*>(&S.kv[st][warp][0][0][0] + swz(cell, pc * 16)) = rk[j];
            *reinterpret_cast<uint4*>(&S.kv[st][warp][1][0][0] + swz(cell, pc * 16)) = rv[j];
        }
    };
    auto issue = [&](long long r, float& ksr, float& vsr) {
#pragma unroll
        for (int j = 0; j < 4; ++j) {
            const int idx = lane + 32 * j, cell = idx >> 2, pc = idx & 3;
            const long long rr = __shfl_sync(0xffffffffu, r, cell);
            const bool ok = rr >= 0;
            const size_t off = ok ? (size_t) rr * HD + dim0 + pc * 16 : 0;
            rk[j] = ok ? __ldg(reinterpret_cast<const uint4*>(p.k_q + off)) : make_uint4(0, 0, 0, 0);
            rv[j] = ok ? __ldg(reinterpret_cast<const uint4*>(p.v_q + off)) : make_uint4(0, 0, 0, 0);
        }
        ksr = r >= 0 ? __half2float(__ushort_as_half(__ldg(p.k_scale + r * (HD / KV_Q8_GROUP) + warp))) : 0.0f;
        vsr = r >= 0 ? __half2float(__ushort_as_half(__ldg(p.v_scale + r * (HD / KV_Q8_GROUP) + warp))) : 0.0f;
    };
    float ksn, vsn;
    issue(row_of(cell_of(lane)), ksn, vsn);
    store_stage(0);
    S.sc[0][warp][0][lane] = ksn;
    S.sc[0][warp][1][lane] = vsn;
    long long r_next = row_of(cell_of(CH2 + lane));
    int cell_next2 = cell_of(2 * CH2 + lane);

    float acc[8][4];
#pragma unroll
    for (int j = 0; j < 8; ++j) acc[j][0] = acc[j][1] = acc[j][2] = acc[j][3] = 0.0f;

    for (int ci = 0; ci < n_chunks; ++ci) {
        const int st = 0, c0 = ci * CH2;
        const bool more = ci + 1 < n_chunks;
        if (more) issue(r_next, ksn, vsn);
        r_next = row_of(cell_next2);
        cell_next2 = cell_of((ci + 3) * CH2 + lane);
        __syncwarp();
        const int8_t* K = &S.kv[st][warp][0][0][0];
        const int8_t* V = &S.kv[st][warp][1][0][0];
        // q.k over this warp's 64 dims, times the cell's K scale for this group
#pragma unroll
        for (int nt = 0; nt < CH2 / 8; ++nt) {
            float tg[4] = {0.f, 0.f, 0.f, 0.f};
            const int cell = nt * 8 + gid;
#pragma unroll
            for (int kk = 0; kk < 4; ++kk) {
                uint32_t b[2];
                b[0] = i8x2_to_h2(*reinterpret_cast<const uint16_t*>(K + swz(cell, kk * 16 + 2 * tig)));
                b[1] = i8x2_to_h2(*reinterpret_cast<const uint16_t*>(K + swz(cell, kk * 16 + 2 * tig + 8)));
                mma16816(tg, qh[kk], b);
                mma16816(tg, ql[kk], b);
            }
            const int c = nt * 8 + 2 * tig;
            const float s0 = S.sc[st][warp][0][c], s1 = S.sc[st][warp][0][c + 1];
            __syncwarp();   // every lane has read the group's cells: their 512 bytes take its partials
            float* pp = reinterpret_cast<float*>(&S.kv[0][warp][0][0][0]) + nt * 128;
            pp[gid * 8 + 2 * tig] = tg[0] * s0;
            pp[gid * 8 + 2 * tig + 1] = tg[1] * s1;
            pp[(gid + 8) * 8 + 2 * tig] = tg[2] * s0;
            pp[(gid + 8) * 8 + 2 * tig + 1] = tg[3] * s1;
        }
        __syncthreads();
        // online softmax over the four groups' sum (fixed order): row t/8, 4 cells per thread
        {
            const int r = t >> 3, sub = t & 7;
            float x[4], mx = -INFINITY;
#pragma unroll
            for (int j = 0; j < 4; ++j) {
                const int c = sub * 4 + j;
                x[j] = c0 + c < n ? (((PA2_PART(0, r, c) + PA2_PART(1, r, c)) + PA2_PART(2, r, c)) + PA2_PART(3, r, c)) * qdown
                                  : -INFINITY;
                mx = fmaxf(mx, x[j]);
            }
#pragma unroll
            for (int o = 1; o < 8; o <<= 1) mx = fmaxf(mx, __shfl_xor_sync(0xffffffffu, mx, o));
            const float m_old = S.mrow[r];
            const float m_new = fmaxf(m_old, mx);
            float sum = 0.0f;
#pragma unroll
            for (int j = 0; j < 4; ++j) {
                const float e = x[j] == -INFINITY ? 0.0f : exp2f(x[j] - m_new);
                S.p[r][sub * 4 + j] = e;
                sum += e;
            }
#pragma unroll
            for (int o = 1; o < 8; o <<= 1) sum += __shfl_xor_sync(0xffffffffu, sum, o);
            __syncwarp();
            if (sub == 0) {
                const float a = m_old == -INFINITY ? 0.0f : exp2f(m_old - m_new);
                S.alpha[r] = a;
                S.lsum[r] = fmaf(S.lsum[r], a, sum);
                S.mrow[r] = m_new;
            }
        }
        __syncthreads();
        // p.v over this warp's 64 dims (as v1)
        {
            float vmax = S.sc[st][warp][1][lane];
#pragma unroll
            for (int o = 16; o > 0; o >>= 1) vmax = fmaxf(vmax, __shfl_xor_sync(0xffffffffu, vmax, o));
            const float vup = vmax > 0.0f ? 16384.0f / vmax : 0.0f, vdown = vmax * (1.0f / 16384.0f);
            float tmp[8][4];
#pragma unroll
            for (int j = 0; j < 8; ++j) tmp[j][0] = tmp[j][1] = tmp[j][2] = tmp[j][3] = 0.0f;
#pragma unroll
            for (int ks = 0; ks < CH2 / 16; ++ks) {
                const int cA = ks * 16 + 2 * tig, cB = cA + 8;
                const float w0 = S.sc[st][warp][1][cA] * vup, w1 = S.sc[st][warp][1][cA + 1] * vup,
                            w2 = S.sc[st][warp][1][cB] * vup, w3 = S.sc[st][warp][1][cB + 1] * vup;
                const float p00 = S.p[gid][cA] * w0, p01 = S.p[gid][cA + 1] * w1;
                const float p10 = S.p[gid + 8][cA] * w0, p11 = S.p[gid + 8][cA + 1] * w1;
                const float p02 = S.p[gid][cB] * w2, p03 = S.p[gid][cB + 1] * w3;
                const float p12 = S.p[gid + 8][cB] * w2, p13 = S.p[gid + 8][cB + 1] * w3;
                uint32_t ah[4], al[4];
                ah[0] = pack_h2(p00, p01);
                ah[1] = pack_h2(p10, p11);
                ah[2] = pack_h2(p02, p03);
                ah[3] = pack_h2(p12, p13);
                {
                    const __half2* h = reinterpret_cast<const __half2*>(ah);
                    float2 f;
                    f = __half22float2(h[0]); al[0] = pack_h2(p00 - f.x, p01 - f.y);
                    f = __half22float2(h[1]); al[1] = pack_h2(p10 - f.x, p11 - f.y);
                    f = __half22float2(h[2]); al[2] = pack_h2(p02 - f.x, p03 - f.y);
                    f = __half22float2(h[3]); al[3] = pack_h2(p12 - f.x, p13 - f.y);
                }
#pragma unroll
                for (int j = 0; j < 8; ++j) {
                    const int d = j * 8 + gid;
                    const uint32_t x0 = (uint8_t) V[swz(cA, d)] | ((uint32_t) (uint8_t) V[swz(cA + 1, d)] << 8);
                    const uint32_t x1 = (uint8_t) V[swz(cB, d)] | ((uint32_t) (uint8_t) V[swz(cB + 1, d)] << 8);
                    uint32_t b[2];
                    b[0] = i8x2_to_h2(x0);
                    b[1] = i8x2_to_h2(x1);
                    mma16816(tmp[j], ah, b);
                    mma16816(tmp[j], al, b);
                }
            }
            const float a0 = S.alpha[gid], a1 = S.alpha[gid + 8];
#pragma unroll
            for (int j = 0; j < 8; ++j) {
                acc[j][0] = fmaf(acc[j][0], a0, tmp[j][0] * vdown);
                acc[j][1] = fmaf(acc[j][1], a0, tmp[j][1] * vdown);
                acc[j][2] = fmaf(acc[j][2], a1, tmp[j][2] * vdown);
                acc[j][3] = fmaf(acc[j][3], a1, tmp[j][3] * vdown);
            }
        }
        if (more) {
            S.sc[0][warp][0][lane] = ksn;
            S.sc[0][warp][1][lane] = vsn;
            store_stage(0);
        }
        __syncwarp();
    }
    __syncthreads();
    const float l0 = S.lsum[gid], l1 = S.lsum[gid + 8];
    const float i0 = l0 > 0.0f ? 1.0f / l0 : 0.0f, i1 = l1 > 0.0f ? 1.0f / l1 : 0.0f;
#pragma unroll
    for (int j = 0; j < 8; ++j) {
        const int d = dim0 + j * 8 + 2 * tig;
        *reinterpret_cast<float2*>(attn + (size_t) gid * HD + d) = make_float2(acc[j][0] * i0, acc[j][1] * i0);
        if (gid + 8 < G)
            *reinterpret_cast<float2*>(attn + (size_t) (gid + 8) * HD + d) = make_float2(acc[j][2] * i1, acc[j][3] * i1);
    }
}

#undef PA2_PART

bool launch_i8(const float* q, const QsaAttnPools& pools, const int32_t* ids, const int32_t* steps, int64_t cap,
               const QsaShapes& s, float* attn, int64_t n_q, cudaStream_t st) {
    static bool attr[64] = {};   // the shared-memory opt-in is per device (a layer split runs this on several)
    int dev = 0;
    cudaGetDevice(&dev);
    if (dev < 0 || dev >= 64) return false;
    const int bytes = (int) sizeof(Smem2T);
    if (!attr[dev]) {
        if (cudaFuncSetAttribute(prompt_attn_i8_kernel, cudaFuncAttributeMaxDynamicSharedMemorySize, bytes) !=
            cudaSuccess) {
            cudaGetLastError();
            return false;
        }
        attr[dev] = true;
    }
    const float scale_log2 = 1.4426950408889634f / sqrtf((float) HD);
    for (int64_t q0 = 0; q0 < n_q; q0 += 65535) {
        const int64_t nb = n_q - q0 < 65535 ? n_q - q0 : 65535;
        prompt_attn_i8_kernel<<<dim3((unsigned) nb, (unsigned) s.n_head_kv), THREADS, bytes, st>>>(
            q + q0 * s.n_head * HD, pools, ids + q0 * cap, steps + q0 * kStepCount, (int) s.n_head_kv,
            (int) s.page_size, scale_log2, attn + q0 * s.n_head * HD, (int) cap);
    }
    const cudaError_t e = cudaGetLastError();
    if (e != cudaSuccess) {
        std::fprintf(stderr, "qsa_prompt_attn_batch: %s\n", cudaGetErrorString(e));
        std::exit(1);
    }
    return true;
}

template <int KV_MODE, bool PF = false>
bool launch(const float* q, const QsaAttnPools& pools, const int32_t* ids, const int32_t* steps, int64_t cap,
            const QsaShapes& s, float* attn, int64_t n_q, cudaStream_t st) {
    static bool attr[64] = {};   // per device, as above
    int dev = 0;
    cudaGetDevice(&dev);
    const int bytes = (int) sizeof(Smem<KV_MODE, PF>);
    if (dev < 0 || dev >= 64) return false;
    if (!attr[dev]) {
        if (cudaFuncSetAttribute(prompt_attn_kernel<KV_MODE, PF>, cudaFuncAttributeMaxDynamicSharedMemorySize, bytes) !=
            cudaSuccess) {
            cudaGetLastError();
            return false;
        }
        attr[dev] = true;
    }
    const float scale_log2 = 1.4426950408889634f / sqrtf((float) HD);
    for (int64_t q0 = 0; q0 < n_q; q0 += 65535) {
        const int64_t nb = n_q - q0 < 65535 ? n_q - q0 : 65535;
        prompt_attn_kernel<KV_MODE, PF><<<dim3((unsigned) nb, (unsigned) s.n_head_kv), THREADS, bytes, st>>>(
            q + q0 * s.n_head * HD, pools, ids + q0 * cap, steps + q0 * kStepCount, (int) s.n_head_kv,
            (int) s.page_size, scale_log2, attn + q0 * s.n_head * HD, (int) cap);
    }
    const cudaError_t e = cudaGetLastError();
    if (e != cudaSuccess) {
        std::fprintf(stderr, "qsa_prompt_attn_batch: %s\n", cudaGetErrorString(e));
        std::exit(1);
    }
    return true;
}


bool qsa_prompt_attn_batch(const float* q, const QsaAttnPools& pools, const int32_t* ids, const int32_t* steps,
                           int64_t cap, const QsaShapes& s, float* attn, int64_t n_q, void* stream) {
    if (n_q <= 0) return true;
    // This source is built for sm_75 only. Cache the device check because a layer split calls this on several GPUs.
    static int sm75[64] = {};
    int dev = 0;
    if (cudaGetDevice(&dev) != cudaSuccess || dev < 0 || dev >= 64) { cudaGetLastError(); return false; }
    if (sm75[dev] == 0) {
        int major = 0, minor = 0;
        if (cudaDeviceGetAttribute(&major, cudaDevAttrComputeCapabilityMajor, dev) != cudaSuccess ||
            cudaDeviceGetAttribute(&minor, cudaDevAttrComputeCapabilityMinor, dev) != cudaSuccess) {
            cudaGetLastError();
            return false;
        }
        sm75[dev] = strata::is_sm75(major, minor) ? 1 : 2;   // STRATA_EMULATE_CC=75 counts for tests
    }
    if (sm75[dev] != 1) return false;
    if (s.head_dim != HD || s.n_head != (int64_t) G * s.n_head_kv || cap <= 0 || !ids || !steps || !pools.page_table)
        return false;
    cudaStream_t st = (cudaStream_t) stream;
    if (pools.k_q4 != nullptr) {   // Q4_0 K and V (--kv q4_0): mode 4.  STRATA_PROMPT_ATTN_Q4=0: the old kernel (A/B)
        static const bool q4_off = [] {
            const char* v = std::getenv("STRATA_PROMPT_ATTN_Q4");
            return v != nullptr && v[0] == '0';
        }();
        // Mode 4 uses pairs of m16n8k8 MMAs (another summation order than the FP32 kernel it replaces). It remains a
        // manual opt-in on Turing: STRATA_PROMPT_ATTN_Q4_TC=1.
        static const bool q4_tc = [] {
            const char* v = std::getenv("STRATA_PROMPT_ATTN_Q4_TC");
            return v != nullptr && v[0] == '1';
        }();
        if (q4_off || !q4_tc || pools.v_q4 == nullptr) return false;
        return launch<4>(q, pools, ids, steps, cap, s, attn, n_q, st);
    }
    if (pools.k_q != nullptr && pools.v_q4 != nullptr) {   // hybrid K8V4: int8 K + dequantized-q4 V
        if (!pools.k_scale) return false;
        return launch<3>(q, pools, ids, steps, cap, s, attn, n_q, st);
    }
    if (pools.k_q != nullptr) {
        if (!pools.v_q || !pools.k_scale || !pools.v_scale) return false;
        // STRATA_PROMPT_ATTN_V1=1 (debug) selects the first layout; v2's register staging is opt-in with
        // STRATA_PA_V2T=1 until a measured sm_75 sweep supports changing the default.
        static const bool v1 = std::getenv("STRATA_PROMPT_ATTN_V1") != nullptr;
        static const bool v2t = [] { const char* e = std::getenv("STRATA_PA_V2T"); return e != nullptr && e[0] == '1'; }();
        if (v1 || !v2t) {
            // #1821 (xrip): v1 with the next chunk's rows prefetched in registers and K/V sharing one buffer (two blocks an
            // SM), the same bits. STRATA_PA_V1_PREFETCH=0 / 1 forces it off / on; unset follows STRATA_SM75_PERF.
            static const int pf_env = [] { const char* e = std::getenv("STRATA_PA_V1_PREFETCH"); return e ? (std::atoi(e) != 0 ? 1 : 0) : -1; }();
            if (pf_env == 1 || (pf_env < 0 && strata::sm75_series()))
                return launch<1, true>(q, pools, ids, steps, cap, s, attn, n_q, st);
            return launch<1>(q, pools, ids, steps, cap, s, attn, n_q, st);
        }
        return launch_i8(q, pools, ids, steps, cap, s, attn, n_q, st);
    }
    if (!pools.k_pool || !pools.v_pool) return false;
    return launch<0>(q, pools, ids, steps, cap, s, attn, n_q, st);
}

}  // namespace strata::kernels
