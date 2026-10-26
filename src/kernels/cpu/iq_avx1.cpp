// src/kernels/cpu/iq_avx1.cpp - the i-quant expert rows for CPUs without AVX2 (STRATA_ISA_FLOOR=avx:
// Sandy/Ivy Bridge, Xeon E5 v1/v2, Bulldozer).  See iq_avx1.hpp.
//
// iq_avx2.cpp's multi-token scheme in 128-bit lanes: each 32-value chunk is decoded once per verify window
// (grid lookups, one sign vector, one scale vector) and every token applies it with a load, a sign, a
// maddubs, a madd and an add - all SSSE3/SSE2, all available since Core 2.  The 256-bit integer ops of the
// AVX2 kernel (pshufb/maddubs/sign_epi8 in __m256i, vpgather, vpdpbusd) have no AVX1 form and no FMA exists
// there, so the float accumulation is a plain SSE2 mul+add.  Without these kernels such a CPU runs every
// expert on ggml-cpu's scalar `_generic` dots (every x86 i-quant dot is #if defined(__AVX2__)).
//
// The arithmetic is ggml's (ggml-cpu/quants.c, the `_generic` references) - only the order of the float
// additions differs; the integer sums are exact.  iq_avx1_parity checks: rows vs ggml-cpu's dot rel <= 1e-5,
// and a multi-token row bit for bit the same kernel's single-token row for that token (#152).
//
// Formats: gate/up IQ3_XXS (18); down IQ4_NL (20) and Q2_0 (42).
//
// This file is compiled for SSE4.2 + AVX with AVX2/FMA/F16C explicitly off (CMakeLists.txt), and the engine
// calls it only behind cpu_avx1_ok().  No static constructors at namespace scope (#391): the tables below
// are constexpr, like iq_avx2.cpp's.
#include "strata/kernels/cpu/iq_avx1.hpp"

#include "ggml.h"

#define GGML_COMMON_DECL_CPP
#define GGML_COMMON_IMPL_CPP
#include "ggml-common.h"

#include <immintrin.h>

#include <cmath>
#include <cstdlib>
#include <cstring>

#if !defined(__AVX__)
#error "iq_avx1.cpp must be compiled with AVX enabled (see the per-source flags in CMakeLists.txt)"
#endif
#if defined(__FMA__) || defined(__F16C__)
#error "iq_avx1.cpp must be compiled without FMA/F16C: it runs on CPUs (Sandy/Ivy Bridge) that lack them"
#endif

namespace strata::kernels::cpu {
namespace {

// fp16 -> fp32 without F16C (expert.cpp's h2f: the conversion written out, subnormals included).
inline float h2f(uint16_t h) {
    const uint32_t sign = (uint32_t) (h >> 15) & 1u;
    uint32_t exp = (h >> 10) & 0x1Fu, man = h & 0x3FFu, f;
    if (exp == 0) {
        if (man == 0) {
            f = sign << 31;
        } else {
            exp = 127 - 15 + 1;
            while (!(man & 0x400u)) { man <<= 1; --exp; }
            man &= 0x3FFu;
            f = (sign << 31) | (exp << 23) | (man << 13);
        }
    } else if (exp == 31) {
        f = (sign << 31) | 0x7F800000u | (man << 13);
    } else {
        f = (sign << 31) | ((exp - 15 + 127) << 23) | (man << 13);
    }
    float out;
    std::memcpy(&out, &f, 4);
    return out;
}

inline uint32_t u32(const uint8_t* p) { uint32_t v; std::memcpy(&v, p, 4); return v; }
inline uint16_t u16(const uint8_t* p) { uint16_t v; std::memcpy(&v, p, 2); return v; }
inline uint64_t u64(const uint8_t* p) { uint64_t v; std::memcpy(&v, p, 8); return v; }

// keven_signs_q2xs as a constexpr table (iq_avx2.cpp's EvenSigns; byte k = 0xFF when bit k of
// ksigns_iq2xs[i] is set, 0x01 otherwise).  constexpr, not a runtime constructor (#391).
struct EvenSigns {
    uint64_t v[128];
    constexpr EvenSigns() : v{} {
        for (int i = 0; i < 128; ++i) {
            int par = 0;
            for (int k = 0; k < 7; ++k) par ^= (i >> k) & 1;
            const int s = i | (par << 7);          // == ksigns_iq2xs[i]
            uint64_t r = 0;
            for (int k = 0; k < 8; ++k) r |= (uint64_t) (((s >> k) & 1) ? 0xFF : 0x01) << (8 * k);
            v[i] = r;
        }
    }
};
static constexpr EvenSigns even_signs{};
static_assert(even_signs.v[0] == 0x0101010101010101ull && even_signs.v[1] == 0xFF010101010101FFull,
              "keven_signs_q2xs: byte k = 0xFF when bit k of ksigns_iq2xs[i] is set");

inline float hsum4(__m128 v) {
    __m128 s = _mm_add_ps(v, _mm_movehl_ps(v, v));
    s = _mm_add_ss(s, _mm_movehdup_ps(s));
    return _mm_cvtss_f32(s);
}

// E-2 prefetching, the same knob as the AVX-2/512 kernels: STRATA_IQ_PREFETCH is the distance in bytes.
int prefetch_distance() {
    static const int d = [] {
        const char* v = std::getenv("STRATA_IQ_PREFETCH");
        return v ? std::atoi(v) : 2048;
    }();
    return d;
}

inline void rows_ahead(const uint8_t* blk, int pf) {
    if (pf <= 0) return;
    _mm_prefetch((const char*) blk + pf, _MM_HINT_T0);
    _mm_prefetch((const char*) blk + pf + 64, _MM_HINT_T0);
}

// ---- per format: one 32-value half (values 64*j + 32*half .. +31) -> grid magnitudes, sign vector and
// scales as TWO 16-value halves (g0/g1, s0/s1, c0/c1).
template <int TY> struct Fmt128;

template <> struct Fmt128<18> {   // IQ3_XXS: d, qs[64] grid bytes, 8 x u32 (4 x 7-bit sign index + 4-bit scale)
    static constexpr int bytes = 98;
    static constexpr float K = 0.25f;
    static inline void decode(const uint8_t* b, int j, int half,
                              __m128i& g0, __m128i& g1, __m128i& s0, __m128i& s1, __m128i& c0, __m128i& c1) {
        const uint8_t* q = b + 2 + 16 * j + 8 * half;
        g0 = _mm_set_epi32((int) iq3xxs_grid[q[3]], (int) iq3xxs_grid[q[2]],
                           (int) iq3xxs_grid[q[1]], (int) iq3xxs_grid[q[0]]);
        g1 = _mm_set_epi32((int) iq3xxs_grid[q[7]], (int) iq3xxs_grid[q[6]],
                           (int) iq3xxs_grid[q[5]], (int) iq3xxs_grid[q[4]]);
        const uint32_t w = u32(b + 2 + 64 + 8 * j + 4 * half);
        s0 = _mm_set_epi64x((long long) even_signs.v[(w >> 7) & 127], (long long) even_signs.v[w & 127]);
        s1 = _mm_set_epi64x((long long) even_signs.v[(w >> 21) & 127], (long long) even_signs.v[(w >> 14) & 127]);
        c0 = c1 = _mm_set1_epi16((short) (2 * (int) (w >> 28) + 1));
    }
};

// acc + madd(maddubs(g, sign(y, sgn)), sc): the AVX2 kernel's per-token application in 128-bit.
inline __m128i madd_add(__m128i acc, __m128i g, __m128i sgn, __m128i yv, __m128i sc) {
    return _mm_add_epi32(acc, _mm_madd_epi16(_mm_maddubs_epi16(g, _mm_sign_epi8(yv, sgn)), sc));
}

// ---- the row kernels (iq_avx2_rows.inl's row_dot in 128-bit lanes)
template <int TY, int NT>
inline void row_dot(const uint8_t* row, int nblocks, const block_q8_K* const* y, float* res) {
    const int pf = prefetch_distance();
    __m128 accf[NT];
    for (int t = 0; t < NT; ++t) accf[t] = _mm_setzero_ps();
    for (int i = 0; i < nblocks; ++i) {
        const uint8_t* blk = row + (size_t) i * Fmt128<TY>::bytes;
        rows_ahead(blk, pf);
        __m128i acci[NT][2];
        for (int t = 0; t < NT; ++t) acci[t][0] = acci[t][1] = _mm_setzero_si128();
        for (int j = 0; j < 4; ++j) {
            for (int half = 0; half < 2; ++half) {
                __m128i g0, g1, s0, s1, c0, c1;
                Fmt128<TY>::decode(blk, j, half, g0, g1, s0, s1, c0, c1);
                const int off = 64 * j + 32 * half;
                for (int t = 0; t < NT; ++t) {
                    const uint8_t* q8 = (const uint8_t*) (y[t][i].qs + off);
                    acci[t][0] = madd_add(acci[t][0], g0, s0, _mm_loadu_si128((const __m128i*) q8), c0);
                    acci[t][1] = madd_add(acci[t][1], g1, s1, _mm_loadu_si128((const __m128i*) (q8 + 16)), c1);
                }
            }
        }
        const float dx = h2f(u16(blk)) * Fmt128<TY>::K;
        for (int t = 0; t < NT; ++t) {
            const __m128 f = _mm_add_ps(_mm_cvtepi32_ps(acci[t][0]), _mm_cvtepi32_ps(acci[t][1]));
            accf[t] = _mm_add_ps(accf[t], _mm_mul_ps(_mm_set1_ps(dx * y[t][i].d), f));
        }
    }
    for (int t = 0; t < NT; ++t) res[t] = hsum4(accf[t]);
}

template <int TY, int NT>
inline void gu_rows(const uint8_t* blob, size_t gu_row, size_t up_off, int n, const void* const* act,
                    float* const* ff, int r0, int r1) {
    const block_q8_K* y[NT];
    for (int t = 0; t < NT; ++t) y[t] = (const block_q8_K*) act[t];
    const int nb = n / QK_K;
    float g[NT], u[NT];
    for (int r = r0; r < r1; ++r) {
        row_dot<TY, NT>(blob + (size_t) r * gu_row, nb, y, g);
        row_dot<TY, NT>(blob + up_off + (size_t) r * gu_row, nb, y, u);
        for (int t = 0; t < NT; ++t) ff[t][r] = (g[t] / (1.f + std::exp(-g[t]))) * u[t];
    }
}

template <int TY, int NT>
inline void dot_rows(const uint8_t* w, size_t row_bytes, int n, const void* const* act, float* const* out,
                     int r0, int r1) {
    const block_q8_K* y[NT];
    for (int t = 0; t < NT; ++t) y[t] = (const block_q8_K*) act[t];
    float res[NT];
    for (int r = r0; r < r1; ++r) {
        row_dot<TY, NT>(w + (size_t) r * row_bytes, n / QK_K, y, res);
        for (int t = 0; t < NT; ++t) out[t][r] = res[t];
    }
}

template <int TY>
void gu_rows_nt(int nt, const uint8_t* blob, size_t gu_row, size_t up_off, int n, const void* const* act,
                float* const* ff, int r0, int r1) {
    switch (nt) {
        case 1: gu_rows<TY, 1>(blob, gu_row, up_off, n, act, ff, r0, r1); break;
        case 2: gu_rows<TY, 2>(blob, gu_row, up_off, n, act, ff, r0, r1); break;
        case 3: gu_rows<TY, 3>(blob, gu_row, up_off, n, act, ff, r0, r1); break;
        case 4: gu_rows<TY, 4>(blob, gu_row, up_off, n, act, ff, r0, r1); break;
        case 5: gu_rows<TY, 5>(blob, gu_row, up_off, n, act, ff, r0, r1); break;
        case 6: gu_rows<TY, 6>(blob, gu_row, up_off, n, act, ff, r0, r1); break;
        case 7: gu_rows<TY, 7>(blob, gu_row, up_off, n, act, ff, r0, r1); break;
        default: gu_rows<TY, 8>(blob, gu_row, up_off, n, act, ff, r0, r1); break;
    }
}

// ---- IQ4_NL (20) down rows against Q8_0: ggml's dot is single-token even where it is vectorized (and
// scalar here); decode the nibbles once, every token pays a sign, a maddubs, a madd and an fmadd-equivalent.
template <int NT>
void iq4nl_rows(const uint8_t* w, size_t row_bytes, int n, const block_q8_0* const* y, float* const* out,
                int r0, int r1) {
    const __m128i values = _mm_loadu_si128((const __m128i*) kvalues_iq4nl);
    const __m128i m4b = _mm_set1_epi8(0x0f);
    const __m128i ones = _mm_set1_epi16(1);
    const int nb = n / QK4_NL;
    const int pf = prefetch_distance();
    for (int r = r0; r < r1; ++r) {
        const uint8_t* row = w + (size_t) r * row_bytes;
        __m128 accf[NT];
        for (int t = 0; t < NT; ++t) accf[t] = _mm_setzero_ps();
        for (int ib = 0; ib < nb; ++ib) {
            const uint8_t* blk = row + (size_t) ib * sizeof(block_iq4_nl);
            rows_ahead(blk, pf);
            const __m128i bits = _mm_loadu_si128((const __m128i*) (blk + 2));
            const __m128i v0 = _mm_shuffle_epi8(values, _mm_and_si128(bits, m4b));
            const __m128i v1 = _mm_shuffle_epi8(values, _mm_and_si128(_mm_srli_epi16(bits, 4), m4b));
            const __m128i aq0 = _mm_sign_epi8(v0, v0);   // |w|: the unsigned maddubs operand
            const __m128i aq1 = _mm_sign_epi8(v1, v1);
            const float dx = h2f(u16(blk));
            for (int t = 0; t < NT; ++t) {
                const uint8_t* q8 = (const uint8_t*) y[t][ib].qs;
                const __m128i p0 = _mm_madd_epi16(_mm_maddubs_epi16(aq0, _mm_sign_epi8(_mm_loadu_si128((const __m128i*) q8), v0)), ones);
                const __m128i p1 = _mm_madd_epi16(_mm_maddubs_epi16(aq1, _mm_sign_epi8(_mm_loadu_si128((const __m128i*) (q8 + 16)), v1)), ones);
                const __m128 f = _mm_add_ps(_mm_cvtepi32_ps(p0), _mm_cvtepi32_ps(p1));
                accf[t] = _mm_add_ps(accf[t], _mm_mul_ps(_mm_set1_ps(dx * h2f(y[t][ib].d)), f));
            }
        }
        for (int t = 0; t < NT; ++t) out[t][r] = hsum4(accf[t]);
    }
}

// ---- Q2_0 (42) down rows against Q8_0: ggml-cpu has only the scalar generic dot for this type on x86.
// Codes {0,1,2,3} map to {c-1}: unpack to unsigned code bytes, dot them with maddubs, and subtract the
// activation's pair sums - all integer, so the per-block sum is EXACTLY the generic dot's sumi_block.
template <int NT>
void q2_0_rows(const uint8_t* w, size_t row_bytes, int n, const block_q8_0* const* y, float* const* out,
               int r0, int r1) {
    const __m128i m3 = _mm_set1_epi8(3);
    const __m128i ones = _mm_set1_epi16(1);
    const int nb = n / QK2_0;
    const int pf = prefetch_distance();
    for (int r = r0; r < r1; ++r) {
        const uint8_t* row = w + (size_t) r * row_bytes;
        __m128 accf[NT];
        for (int t = 0; t < NT; ++t) accf[t] = _mm_setzero_ps();
        for (int i = 0; i < nb; ++i) {
            const uint8_t* blk = row + (size_t) i * sizeof(block_q2_0);
            rows_ahead(blk, pf);
            const float dx = h2f(u16(blk));
            const uint8_t* qs = blk + 2;
            for (int k = 0; k < 2; ++k) {
                // 8 code bytes -> 32 unsigned codes in order (c0..c3 = the four 2-bit groups of each byte)
                const __m128i q = _mm_cvtsi64_si128((long long) u64(qs + 8 * k));
                const __m128i c0 = _mm_and_si128(q, m3);
                const __m128i c1 = _mm_and_si128(_mm_srli_epi16(q, 2), m3);
                const __m128i c2 = _mm_and_si128(_mm_srli_epi16(q, 4), m3);
                const __m128i c3 = _mm_and_si128(_mm_srli_epi32(q, 6), m3);
                const __m128i t01 = _mm_unpacklo_epi8(c0, c1);
                const __m128i t23 = _mm_unpacklo_epi8(c2, c3);
                const __m128i k0 = _mm_unpacklo_epi16(t01, t23);   // codes 0..15
                const __m128i k1 = _mm_unpackhi_epi16(t01, t23);   // codes 16..31
                for (int t = 0; t < NT; ++t) {
                    const uint8_t* q8 = (const uint8_t*) y[t][i * 2 + k].qs;
                    const __m128i y0 = _mm_loadu_si128((const __m128i*) q8);
                    const __m128i y1 = _mm_loadu_si128((const __m128i*) (q8 + 16));
                    // (c-1)*qy per pair: c*qy - qy, both at int16 level (|result| <= 508, no saturation)
                    const __m128i d0 = _mm_sub_epi16(_mm_maddubs_epi16(k0, y0), _mm_maddubs_epi16(_mm_set1_epi8(1), y0));
                    const __m128i d1 = _mm_sub_epi16(_mm_maddubs_epi16(k1, y1), _mm_maddubs_epi16(_mm_set1_epi8(1), y1));
                    const __m128 f = _mm_add_ps(_mm_cvtepi32_ps(_mm_madd_epi16(d0, ones)),
                                                _mm_cvtepi32_ps(_mm_madd_epi16(d1, ones)));
                    accf[t] = _mm_add_ps(accf[t], _mm_mul_ps(_mm_set1_ps(dx * h2f(y[t][i * 2 + k].d)), f));
                }
            }
        }
        for (int t = 0; t < NT; ++t) out[t][r] = hsum4(accf[t]);
    }
}

}  // namespace

bool iq128_supported(int type) noexcept {
    return type == 18;
}

bool iq128_down_supported(int type) noexcept {
    return type == 20 || type == 42;
}

void iq128_gu_rows(int type, const uint8_t* blob, size_t gu_row, size_t up_off, int n, const void* const* act,
                   int nt, float* const* ff, int r0, int r1) {
    switch (type) {
        case 18: gu_rows_nt<18>(nt, blob, gu_row, up_off, n, act, ff, r0, r1); break;
        default: break;
    }
}

void iq128_down_rows(int type, const uint8_t* w, size_t row_bytes, int n, const void* const* hq, int nt,
                     float* const* out, int r0, int r1) {
    const block_q8_0* y[8];
    for (int t = 0; t < nt && t < 8; ++t) y[t] = (const block_q8_0*) hq[t];
    if (type == 20) {
        for (int t0 = 0; t0 < nt; t0 += 4) {
            const int k = nt - t0 < 4 ? nt - t0 : 4;
            switch (k) {
                case 1: iq4nl_rows<1>(w, row_bytes, n, y + t0, out + t0, r0, r1); break;
                case 2: iq4nl_rows<2>(w, row_bytes, n, y + t0, out + t0, r0, r1); break;
                case 3: iq4nl_rows<3>(w, row_bytes, n, y + t0, out + t0, r0, r1); break;
                default: iq4nl_rows<4>(w, row_bytes, n, y + t0, out + t0, r0, r1); break;
            }
        }
        return;
    }
    // Q2_0: the same 4-at-a-time chunking (NT is capped at 8 by the pool; prefill chunks larger than 8
    // loop here, the per-token rows identical either way).
    for (int t0 = 0; t0 < nt; t0 += 4) {
        const int k = nt - t0 < 4 ? nt - t0 : 4;
        switch (k) {
            case 1: q2_0_rows<1>(w, row_bytes, n, y + t0, out + t0, r0, r1); break;
            case 2: q2_0_rows<2>(w, row_bytes, n, y + t0, out + t0, r0, r1); break;
            case 3: q2_0_rows<3>(w, row_bytes, n, y + t0, out + t0, r0, r1); break;
            default: q2_0_rows<4>(w, row_bytes, n, y + t0, out + t0, r0, r1); break;
        }
    }
}

// ggml's quantize_row_q8_K_ref in SSE4.2/AVX1: q8k_quant_avx2's arithmetic, 128-bit lanes (its packs need no
// lane-crossing permute at 128-bit).  The same per-value operations as the scalar reference - one IEEE multiply
// by the same iscale, the same 1.5*2^23 rounding add, the same clamp - so the bytes are identical
// (q8k_quant_parity checks it), including the NaN and degenerate-block behavior: `ax > amax` is false for a NaN
// and max_ps returns its second operand for a NaN first one, and a zero block leaves bsums as the reference does.
void q8k_quant_avx1(const float* x, void* vy, int64_t k) {
    block_q8_K* y = (block_q8_K*) vy;
    const int64_t nb = k / QK_K;
    const __m128 absm = _mm_castsi128_ps(_mm_set1_epi32(0x7fffffff));
    const __m128 magic = _mm_set1_ps(12582912.f);
    const __m128i mant = _mm_set1_epi32(0x007fffff), off = _mm_set1_epi32(0x00400000);
    const __m128i c127 = _mm_set1_epi32(127);
    for (int64_t i = 0; i < nb; ++i, x += QK_K) {
        __m128 m = _mm_setzero_ps();
        for (int j = 0; j < QK_K; j += 4) m = _mm_max_ps(_mm_and_ps(_mm_loadu_ps(x + j), absm), m);
        __m128 h = _mm_max_ps(m, _mm_movehl_ps(m, m));
        h = _mm_max_ss(h, _mm_movehdup_ps(h));
        const float amax = _mm_cvtss_f32(h);
        if (!amax) {
            y[i].d = 0;
            std::memset(y[i].qs, 0, QK_K);
            continue;
        }
        float mx = 0.f;
        const __m128 va = _mm_set1_ps(amax);
        for (int j = 0; j < QK_K; j += 4) {
            const int msk = _mm_movemask_ps(_mm_cmpeq_ps(_mm_and_ps(_mm_loadu_ps(x + j), absm), va));
            if (msk) {
                int b = 0;
                while (!((msk >> b) & 1)) ++b;
                mx = x[j + b];
                break;
            }
        }
        const float iscale = -127.f / mx;
        const __m128 vis = _mm_set1_ps(iscale);
        for (int j = 0; j < QK_K; j += 16) {
            __m128i v[4];
            for (int q = 0; q < 4; ++q) {
                __m128 p = _mm_mul_ps(vis, _mm_loadu_ps(x + j + 4 * q));
#if defined(__GNUC__) && !defined(__clang__)
                // GCC fuses a multiply and an add into an FMA by default (-ffp-contract=fast); the reference
                // rounds the product before the add, so the product must stay a separate value (no FMA under
                // -mavx, but the barrier keeps the contract if the file is ever built with a newer floor)
                __asm__("" : "+x"(p));
#endif
                const __m128 t = _mm_add_ps(p, magic);
                v[q] = _mm_min_epi32(_mm_sub_epi32(_mm_and_si128(_mm_castps_si128(t), mant), off), c127);
                // the reference stores MIN(127, v) into an int8 (keeps the low byte), and its bsums add those
                // bytes: the same here, so even a degenerate block (iscale overflowing to inf) gives the same bytes
                v[q] = _mm_srai_epi32(_mm_slli_epi32(v[q], 24), 24);
            }
            __m128i s = _mm_add_epi32(_mm_add_epi32(v[0], v[1]), _mm_add_epi32(v[2], v[3]));
            s = _mm_add_epi32(s, _mm_shuffle_epi32(s, 0x4E));
            s = _mm_add_epi32(s, _mm_shuffle_epi32(s, 0xB1));
            y[i].bsums[j / 16] = (int16_t) _mm_cvtsi128_si32(s);
            _mm_storeu_si128((__m128i*) (y[i].qs + j),
                             _mm_packs_epi16(_mm_packs_epi32(v[0], v[1]), _mm_packs_epi32(v[2], v[3])));
        }
        y[i].d = 1 / iscale;
    }
}

}  // namespace strata::kernels::cpu
