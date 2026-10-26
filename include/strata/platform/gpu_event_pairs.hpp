#pragma once

#include <cuda_runtime.h>

#include <cstddef>
#include <vector>

namespace strata::platform {

/// A group of start/end CUDA event pairs used by opt-in timing diagnostics.
///
/// Events belong to the device that created them.  Creation is transactional: a failure destroys the events made by
/// this call and leaves the owner empty.  Release switches to the owning device, destroys every event, and restores
/// the caller's device.  The destructor calls release.
class GpuEventPairs {
public:
    GpuEventPairs() = default;
    ~GpuEventPairs() { release(); }
    GpuEventPairs(const GpuEventPairs&) = delete;
    GpuEventPairs& operator=(const GpuEventPairs&) = delete;

    bool ensure(int device, size_t pairs);
    bool ready(int device, size_t pairs) const;
    /// False on a device switch/destroy failure. Events are retained when they could not all be destroyed.
    bool release();

    cudaError_t record_start(size_t pair, cudaStream_t stream) const;
    cudaError_t record_end(size_t pair, cudaStream_t stream) const;
    cudaError_t synchronize_end(size_t pair) const;
    cudaError_t elapsed_ms(size_t pair, float& milliseconds) const;
    cudaError_t elapsed_gap_ms(size_t earlier_pair, size_t later_pair, float& milliseconds) const;

private:
    int device_ = -1;
    size_t pairs_ = 0;
    std::vector<cudaEvent_t> events_;   // start 0, end 0, start 1, end 1, ...
};

} // namespace strata::platform
