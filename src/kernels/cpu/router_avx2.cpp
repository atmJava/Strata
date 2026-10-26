// src/kernels/cpu/router_avx2.cpp - AVX2 BF16 dots for the router look-ahead.
#include "strata/kernels/cpu/router_avx2.hpp"

#include <immintrin.h>

#include <cstring>

namespace strata::kernels::cpu {
namespace {

inline float hsum_float_8(const __m256 x) {
    __m128 res = _mm256_extractf128_ps(x, 1);
    res = _mm_add_ps(res, _mm256_castps256_ps128(x));
    res = _mm_add_ps(res, _mm_movehl_ps(res, res));
    res = _mm_add_ss(res, _mm_movehdup_ps(res));
    return _mm_cvtss_f32(res);
}

}  // namespace

void bf16_rows_dot_multi(const uint16_t* w, int rows, int cols, const float* x, int nt, float* out) {
    for (int t0 = 0; t0 < nt; t0 += 4) {
        const int m = nt - t0 < 4 ? nt - t0 : 4;
        const float* x0 = x + (size_t) t0 * cols;
        const float* x1 = x0 + cols;
        const float* x2 = x1 + cols;
        const float* x3 = x2 + cols;
        for (int r = 0; r < rows; ++r) {
            const uint16_t* wr = w + (size_t) r * (size_t) cols;
            __m256 a0 = _mm256_setzero_ps(), a1 = _mm256_setzero_ps(), a2 = _mm256_setzero_ps(),
                   a3 = _mm256_setzero_ps();
            for (int c = 0; c + 8 <= cols; c += 8) {
                const __m256 wf = _mm256_castsi256_ps(
                    _mm256_slli_epi32(_mm256_cvtepu16_epi32(_mm_loadu_si128((const __m128i*) (wr + c))), 16));
                a0 = _mm256_fmadd_ps(wf, _mm256_loadu_ps(x0 + c), a0);
                if (m > 1) a1 = _mm256_fmadd_ps(wf, _mm256_loadu_ps(x1 + c), a1);
                if (m > 2) a2 = _mm256_fmadd_ps(wf, _mm256_loadu_ps(x2 + c), a2);
                if (m > 3) a3 = _mm256_fmadd_ps(wf, _mm256_loadu_ps(x3 + c), a3);
            }
            out[(size_t) t0 * rows + r] = hsum_float_8(a0);
            if (m > 1) out[(size_t) (t0 + 1) * rows + r] = hsum_float_8(a1);
            if (m > 2) out[(size_t) (t0 + 2) * rows + r] = hsum_float_8(a2);
            if (m > 3) out[(size_t) (t0 + 3) * rows + r] = hsum_float_8(a3);
        }
    }
}

void bf16_rows_dot(const uint16_t* w, int rows, int cols, const float* x, float* out) {
    for (int r = 0; r < rows; ++r) {
        const uint16_t* wr = w + (size_t) r * (size_t) cols;
        __m256 acc = _mm256_setzero_ps();
        int c = 0;
        for (; c + 8 <= cols; c += 8) {
            const __m256i h = _mm256_cvtepu16_epi32(_mm_loadu_si128((const __m128i*) (wr + c)));
            acc = _mm256_fmadd_ps(_mm256_castsi256_ps(_mm256_slli_epi32(h, 16)), _mm256_loadu_ps(x + c), acc);
        }
        float s = hsum_float_8(acc);
        for (; c < cols; ++c) {
            const uint32_t bits = (uint32_t) wr[c] << 16;
            float f;
            std::memcpy(&f, &bits, 4);
            s += f * x[c];
        }
        out[r] = s;
    }
}

}  // namespace strata::kernels::cpu
