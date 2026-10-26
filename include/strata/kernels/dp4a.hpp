// include/strata/kernels/dp4a.hpp - byte dot products and the pause used by doorbell waits on CUDA sm_75.
#pragma once

#include <cstdint>

#define STRATA_DP4A(a, b, c) __dp4a((a), (b), (c))

/// Yield the thread while a doorbell flag is being polled.
__device__ __forceinline__ void strata_spin_pause() {
    __nanosleep(100);
}
