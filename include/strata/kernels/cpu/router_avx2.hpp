// include/strata/kernels/cpu/router_avx2.hpp - AVX2 BF16 dots for the router look-ahead.
#pragma once

#include <cstdint>

namespace strata::kernels::cpu {

/// out[r] = w_r . x for BF16 rows (bits), fp32 x: the routing-aware prefetch's router (an estimate only).
void bf16_rows_dot(const uint16_t* w, int rows, int cols, const float* x, float* out);
/// The same for `nt` <= 8 tokens (x: nt rows of cols, cols % 8 == 0), out[t * rows + r].
void bf16_rows_dot_multi(const uint16_t* w, int rows, int cols, const float* x, int nt, float* out);

}  // namespace strata::kernels::cpu
