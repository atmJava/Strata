// src/kernels/cpu/iq_avx2.cpp - the i-quant expert rows in 256-bit lanes, several tokens at once.
//
// The AVX-512 kernels' (iq_avx512.cpp) multi-token scheme on the CPUs without AVX-512 (AMD Zen 2/3, Intel
// Core 12th-14th gen and Core Ultra): each 32-value chunk is decoded once - grid lookups, one sign vector,
// one scale vector - and every token of the verify window applies them with a load, a `sign`, a `maddubs`,
// a `madd` and an `add`.  ggml-cpu's own AVX2 dot products for these formats are single-token: every token
// re-does the codebook lookups and the sign expansion.  The sign vector is ggml's bit_selector pattern
// (ggml-cpu/arch/x86/quants.c): pshufb-broadcast of each sign byte, AND with the bit selector, CMPEQ, OR
// with one, `vpsignb`.  The arithmetic is ggml's (ggml-cpu/quants.c, the `_generic` references) - only the
// order of the float additions differs.
//
// The server's gate/up format is IQ3_XXS (18). It has a gathered decode (Fmt32<118>): the grid indices are built in
// one register and read with one vpgather, the same words into the same lanes, so the same bits. Where it is faster
// depends on the core, so STRATA_IQ256_GATHER picks it per thread (expert_layout.hpp, cpu_gather_fast_here).
//
// Where the CPU has AVX-VNNI (cpu_avxvnni_ok: Alder Lake, Sapphire Rapids and later) the row kernels take a second
// copy with vpdpwssd / vpdpbusd (iq_avx2_rows.inl), the same integer sums.
//
// This file is compiled for AVX2 and must not run anything before the engine's cpu_avx2_ok() check: no runtime
// initializer at namespace scope (#391, 016ea2e), the switches are read on first use.
#include "strata/kernels/cpu/iq_avx2.hpp"
#include "strata/kernels/cpu/expert_layout.hpp"

#define GGML_COMMON_DECL_CPP
#define GGML_COMMON_IMPL_CPP
#include "ggml-common.h"

#include <immintrin.h>

#include <cmath>
#include <cstdlib>
#include <cstring>

// STRATA_AVXVNNI (CMake: the compiler has the AVX-VNNI intrinsics): GCC and Clang compile them only inside functions
// with target("avxvnni") - the VNNI copy of iq_avx2_rows.inl.
#if !defined(STRATA_AVXVNNI)
#define STRATA_AVXVNNI 0
#endif
#if STRATA_AVXVNNI && (defined(__GNUC__) || defined(__clang__))
#define STRATA_AVXVNNI_FN __attribute__((target("avxvnni")))
#else
#define STRATA_AVXVNNI_FN
#endif

namespace strata::kernels::cpu {
namespace {

inline float h2f(uint16_t h) { return _mm_cvtss_f32(_mm_cvtph_ps(_mm_cvtsi32_si128((int) h))); }
inline uint32_t u32(const uint8_t* p) { uint32_t v; std::memcpy(&v, p, 4); return v; }
inline uint16_t u16(const uint8_t* p) { uint16_t v; std::memcpy(&v, p, 2); return v; }
// One scale for the whole 32-value half.
inline __m256i sc32(int s) { return _mm256_set1_epi16(s); }

// IQ3_XXS's even-parity sign masks (ggml's ksigns_iq2xs): one u64 per 7-bit sign index, byte k
// = 0xFF when bit k of ksigns_iq2xs[i] is set, 0x01 otherwise.  With this a whole 32-value sign vector
// is four scalar loads and a set_epi64x - no ksigns byte packing chain and no bit_selector expansion.
//
// Computed at COMPILE time (constexpr), so this file has no static constructor at all.  This TU is compiled
// for AVX2, and a runtime constructor here runs before main() on every CPU: MSVC turned its variable shift
// into BMI2 `shlx` (#391, demetree: strata.exe exited 0xC000001D before printing anything on a Sandy Bridge
// Xeon) and GCC vectorised the loop into AVX2 (`vpbroadcastb`, found on an AVX-only Xeon E5 by the
// Strata_Dirigo fork).  ksigns_iq2xs[i] is i's 7 bits plus an even-parity bit 7 (ggml-common.h); a const
// array is not usable in a constant expression, so the byte is derived the same way here, and
// native_expert_parity checks these kernels against ggml-cpu's.
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

inline float hsum8(__m256 v) {
    const __m128 lo = _mm256_castps256_ps128(v), hi = _mm256_extractf128_ps(v, 1);
    __m128 s = _mm_add_ps(lo, hi);
    s = _mm_hadd_ps(s, s);
    s = _mm_hadd_ps(s, s);
    return _mm_cvtss_f32(s);
}

// E-2 (iq_avx512.cpp) on the AVX-2 path: the pool streams the expert rows from DRAM at ~25 GB/s (4 KB pages
// when large pages are refused), so ask for the bytes a few blocks before the decode needs them - 2048 B is
// two gate/up rows ahead, a row is ~1 KB.  Same switch as the AVX-512 kernels: STRATA_IQ_PREFETCH is the
// distance in bytes, 0 = off, default 2048.  Measured on a Zen 3 5700X3D (no AVX-512) on IQ3_XXS decode: -4% on
// the gate/up phase, +1.0 GB/s over the rows, -1.3% ms/round end to end.  The non-temporal hint measured
// worse than T0 at the same distance, so this keeps T0.
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

// ---- IQ3_XXS: one 32-value half (values 64*j + 32*half .. +31) -> grid magnitudes, sign vector, scale
template <int TY> struct Fmt32;

template <> struct Fmt32<18> {   // IQ3_XXS: d, qs[64] grid bytes, 8 x u32 (4 x 7-bit sign index + 4-bit scale)
    static constexpr int bytes = 98;
    static constexpr float K = 0.25f;
    static inline void decode(const uint8_t* b, int j, int half, __m256i& g, __m256i& sgn, __m256i& sc) {
        const uint8_t* q = b + 2 + 16 * j + 8 * half;
        g = _mm256_set_epi32((int) iq3xxs_grid[q[7]], (int) iq3xxs_grid[q[6]], (int) iq3xxs_grid[q[5]], (int) iq3xxs_grid[q[4]],
                             (int) iq3xxs_grid[q[3]], (int) iq3xxs_grid[q[2]], (int) iq3xxs_grid[q[1]], (int) iq3xxs_grid[q[0]]);
        const uint32_t w = u32(b + 2 + 64 + 8 * j + 4 * half);
        sgn = _mm256_set_epi64x((long long) even_signs.v[(w >> 21) & 127], (long long) even_signs.v[(w >> 14) & 127],
                                (long long) even_signs.v[(w >> 7) & 127], (long long) even_signs.v[w & 127]);
        sc = sc32(2 * (int) (w >> 28) + 1);
    }
};

// ---- the gathered IQ3_XXS decode (STRATA_IQ256_GATHER, see the top of the file)
// The four 7-bit even-sign indices of a u32 (bits 0-6, 7-13, 14-20, 21-27) -> their four masks, one gather.
inline __m256i even_signs4(uint32_t w) {
    const __m128i idx = _mm_and_si128(_mm_srlv_epi32(_mm_set1_epi32((int) w), _mm_setr_epi32(0, 7, 14, 21)),
                                      _mm_set1_epi32(127));
    return _mm256_i32gather_epi64((const long long*) even_signs.v, idx, 8);
}

// IQ3_XXS: the grid entries and the four even-sign masks each by one gather.  P-core gate/up rows at three tokens:
// 0.26 -> 0.24 ms.
template <> struct Fmt32<118> {
    static constexpr int bytes = 98;
    static constexpr float K = 0.25f;
    static inline void decode(const uint8_t* b, int j, int half, __m256i& g, __m256i& sgn, __m256i& sc) {
        const uint8_t* q = b + 2 + 16 * j + 8 * half;
        g = _mm256_i32gather_epi32((const int*) iq3xxs_grid, _mm256_cvtepu8_epi32(_mm_loadl_epi64((const __m128i*) q)), 4);
        const uint32_t w = u32(b + 2 + 64 + 8 * j + 4 * half);
        sgn = even_signs4(w);
        sc = sc32(2 * (int) (w >> 28) + 1);
    }
};

// ---- the row kernels, twice: the AVX2 forms and the AVX-VNNI ones (iq_avx2_rows.inl)
namespace plain {
#define STRATA_ROWS_VNNI 0
#define STRATA_ROWS_FN
#include "iq_avx2_rows.inl"
}  // namespace plain
#if STRATA_AVXVNNI
namespace vnni {
#define STRATA_ROWS_VNNI 1
#define STRATA_ROWS_FN STRATA_AVXVNNI_FN
#include "iq_avx2_rows.inl"
}  // namespace vnni
#endif

bool vnni_on() { return STRATA_AVXVNNI && cpu_avxvnni_ok(); }

}  // namespace

// ggml's quantize_row_q8_K_ref (ggml-quants.c), which is also what ggml-cpu runs on x86 (no SIMD version there),
// in AVX-2: the same per-value arithmetic - one IEEE multiply by the same iscale, the same 1.5*2^23 rounding add,
// the same clamp - so the bytes are identical (q8k_quant_parity checks it).  The block's sign-carrying max is the
// FIRST value of the largest magnitude, as in the scalar loop; NaNs are skipped as there (`ax > amax` is false for
// them, and max_ps returns its second operand for a NaN first one).  A zero block leaves bsums as the reference
// does.  A verify window quantizes up to 6 tokens per layer on the host before the pool can start.
void q8k_quant_avx2(const float* x, void* vy, int64_t k) {
    block_q8_K* y = (block_q8_K*) vy;
    const int64_t nb = k / QK_K;
    const __m256 absm = _mm256_castsi256_ps(_mm256_set1_epi32(0x7fffffff));
    const __m256 magic = _mm256_set1_ps(12582912.f);
    const __m256i mant = _mm256_set1_epi32(0x007fffff), off = _mm256_set1_epi32(0x00400000);
    const __m256i c127 = _mm256_set1_epi32(127);
    const __m256i perm = _mm256_setr_epi32(0, 4, 1, 5, 2, 6, 3, 7);
    for (int64_t i = 0; i < nb; ++i, x += QK_K) {
        __m256 m = _mm256_setzero_ps();
        for (int j = 0; j < QK_K; j += 8) m = _mm256_max_ps(_mm256_and_ps(_mm256_loadu_ps(x + j), absm), m);
        __m128 h = _mm_max_ps(_mm256_castps256_ps128(m), _mm256_extractf128_ps(m, 1));
        h = _mm_max_ps(h, _mm_movehl_ps(h, h));
        h = _mm_max_ss(h, _mm_movehdup_ps(h));
        const float amax = _mm_cvtss_f32(h);
        if (!amax) {
            y[i].d = 0;
            std::memset(y[i].qs, 0, QK_K);
            continue;
        }
        float mx = 0.f;
        const __m256 va = _mm256_set1_ps(amax);
        for (int j = 0; j < QK_K; j += 8) {
            const int msk = _mm256_movemask_ps(_mm256_cmp_ps(_mm256_and_ps(_mm256_loadu_ps(x + j), absm), va, _CMP_EQ_OQ));
            if (msk) {
                int b = 0;
                while (!((msk >> b) & 1)) ++b;
                mx = x[j + b];
                break;
            }
        }
        const float iscale = -127.f / mx;
        const __m256 vis = _mm256_set1_ps(iscale);
        for (int j = 0; j < QK_K; j += 32) {
            __m256i v[4];
            for (int q = 0; q < 4; ++q) {
                __m256 p = _mm256_mul_ps(vis, _mm256_loadu_ps(x + j + 8 * q));
#if defined(__GNUC__) && !defined(__clang__)
                // GCC fuses a multiply and an add into an FMA by default (-ffp-contract=fast); the reference rounds
                // the product before the add, so the product must stay a separate value
                __asm__("" : "+x"(p));
#endif
                const __m256 t = _mm256_add_ps(p, magic);
                v[q] = _mm256_min_epi32(_mm256_sub_epi32(_mm256_and_si256(_mm256_castps_si256(t), mant), off), c127);
                // the reference stores MIN(127, v) into an int8 (keeps the low byte), and its bsums add those bytes:
                // the same here, so even a degenerate block (iscale overflowing to inf) gives the same bytes
                v[q] = _mm256_srai_epi32(_mm256_slli_epi32(v[q], 24), 24);
            }
            for (int q = 0; q < 2; ++q) {   // two 16-value sums
                const __m256i s = _mm256_add_epi32(v[2 * q], v[2 * q + 1]);
                __m128i s4 = _mm_add_epi32(_mm256_castsi256_si128(s), _mm256_extracti128_si256(s, 1));
                s4 = _mm_add_epi32(s4, _mm_shuffle_epi32(s4, 0x4E));
                s4 = _mm_add_epi32(s4, _mm_shuffle_epi32(s4, 0xB1));
                y[i].bsums[j / 16 + q] = (int16_t) _mm_cvtsi128_si32(s4);
            }
            const __m256i p16a = _mm256_packs_epi32(v[0], v[1]), p16b = _mm256_packs_epi32(v[2], v[3]);
            const __m256i p8 = _mm256_permutevar8x32_epi32(_mm256_packs_epi16(p16a, p16b), perm);
            _mm256_storeu_si256((__m256i*) (y[i].qs + j), p8);
        }
        y[i].d = 1 / iscale;
    }
}

void iq256_gu_rows_exact_one(int type, const uint8_t* blob, size_t gu_row, size_t up_off, int n,
                             const void* act, float* ff, int r0, int r1) {
    const auto* y = (const block_q8_K*) act;
#if STRATA_AVXVNNI
    if (vnni_on()) {
        if (type == 18) {
            if (iq256_variant() & kIq256Gather) vnni::gu_ggml_one<118>(blob, gu_row, up_off, n, y, ff, r0, r1);
            else vnni::gu_ggml_one<18>(blob, gu_row, up_off, n, y, ff, r0, r1);
        }
        return;
    }
#endif
    if (type == 18) {
        if (iq256_variant() & kIq256Gather) plain::gu_ggml_one<118>(blob, gu_row, up_off, n, y, ff, r0, r1);
        else plain::gu_ggml_one<18>(blob, gu_row, up_off, n, y, ff, r0, r1);
    }
}

int iq256_variant() noexcept {
    // STRATA_IQ256_GATHER=0/1 for every core; unset, where the calling thread's core gathers faster (per thread: a
    // hybrid CPU's pool runs on P- and E-cores).  AVX-VNNI where cpu_avxvnni_ok().  The same bits either way.
    const int s = iq256_gather_setting();
    return ((s >= 0 ? s == 1 : cpu_gather_fast_here()) ? kIq256Gather : 0) | (vnni_on() ? kIq256Vnni : 0);
}

int iq256_variants() noexcept { return kIq256Gather | (vnni_on() ? kIq256Vnni : 0); }

void iq256_gu_rows_v(int variant, int type, const uint8_t* blob, size_t gu_row, size_t up_off, int n,
                     const void* const* act, int nt, float* const* ff, int r0, int r1) {
    const bool g = (variant & kIq256Gather) != 0;
#if STRATA_AVXVNNI
    if (variant & kIq256Vnni) {
        if (g) vnni::gu_type<true>(type, nt, blob, gu_row, up_off, n, act, ff, r0, r1);
        else vnni::gu_type<false>(type, nt, blob, gu_row, up_off, n, act, ff, r0, r1);
        return;
    }
#endif
    if (g) plain::gu_type<true>(type, nt, blob, gu_row, up_off, n, act, ff, r0, r1);
    else plain::gu_type<false>(type, nt, blob, gu_row, up_off, n, act, ff, r0, r1);
}

void iq256_rows_v(int variant, int type, const uint8_t* w, size_t row_bytes, int n, const void* const* act, int nt,
                  float* const* out, int r0, int r1) {
    const bool g = (variant & kIq256Gather) != 0;
#if STRATA_AVXVNNI
    if (variant & kIq256Vnni) {
        if (g) vnni::dot_type<true>(type, nt, w, row_bytes, n, act, out, r0, r1);
        else vnni::dot_type<false>(type, nt, w, row_bytes, n, act, out, r0, r1);
        return;
    }
#endif
    if (g) plain::dot_type<true>(type, nt, w, row_bytes, n, act, out, r0, r1);
    else plain::dot_type<false>(type, nt, w, row_bytes, n, act, out, r0, r1);
}

void iq256_gu_rows(int type, const uint8_t* blob, size_t gu_row, size_t up_off, int n, const void* const* act, int nt,
                   float* const* ff, int r0, int r1) {
    iq256_gu_rows_v(iq256_variant(), type, blob, gu_row, up_off, n, act, nt, ff, r0, r1);
}

void iq256_rows(int type, const uint8_t* w, size_t row_bytes, int n, const void* const* act, int nt, float* const* out,
                int r0, int r1) {
    iq256_rows_v(iq256_variant(), type, w, row_bytes, n, act, nt, out, r0, r1);
}

void iq4nl256_down_rows_v(int variant, const uint8_t* w, size_t row_bytes, int n, const void* const* hq, int nt,
                          float* const* out, int r0, int r1) {
    const block_q8_0* y[8];
    for (int t = 0; t < nt; ++t) y[t] = (const block_q8_0*) hq[t];
#if STRATA_AVXVNNI
    if (variant & kIq256Vnni) {
        vnni::iq4nl_nt(w, row_bytes, n, y, nt, out, r0, r1);
        return;
    }
#endif
    (void) variant;
    plain::iq4nl_nt(w, row_bytes, n, y, nt, out, r0, r1);
}

void iq4nl256_down_rows(const uint8_t* w, size_t row_bytes, int n, const void* const* hq, int nt, float* const* out,
                        int r0, int r1) {
    iq4nl256_down_rows_v(vnni_on() ? kIq256Vnni : 0, w, row_bytes, n, hq, nt, out, r0, r1);
}

}  // namespace strata::kernels::cpu
