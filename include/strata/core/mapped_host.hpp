#pragma once

#include "strata/core/pin_budget.hpp"

#include <cstring>

namespace strata::core {

/// Allocates mapped-pinned host memory, zeroed unless `clear` is false.  On a device-alias failure, releases the host
/// allocation and leaves both outputs unchanged, so callers may retry without losing ownership of the earlier attempt.
inline bool alloc_mapped_pinned(size_t bytes, void** host, void** device, unsigned extra_flags = 0,
                                bool clear = true) {
    void* allocated = nullptr;
    const unsigned flags = cudaHostAllocMapped | extra_flags;
    if (host_alloc_guarded(&allocated, bytes, flags) != cudaSuccess) return false;
    if (clear) std::memset(allocated, 0, bytes);

    void* alias = nullptr;
    if (cudaHostGetDevicePointer(&alias, allocated, 0) != cudaSuccess) {
        host_free_guarded(allocated);
        return false;
    }
    *host = allocated;
    *device = alias;
    return true;
}

} // namespace strata::core
