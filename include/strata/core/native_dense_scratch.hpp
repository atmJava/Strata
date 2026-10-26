#pragma once

#include <cuda_runtime.h>

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>

namespace strata::core {

/// Device-owned dequantization scratch shared by the WeightRefs of one NativeDense table.
///
/// There are two slots and two classes: class 1 is the ordered layer/session stream, class 0 is the prompt path,
/// whose streams are ordered by one event per slot before a slot is overwritten.  Creation is transactional.  The
/// destructor synchronizes the owning device, destroys both events and frees all four buffers in that device's
/// context.
class NativeDenseScratch {
public:
    static std::shared_ptr<NativeDenseScratch> create(int device, uint64_t elements, std::string& err);
    ~NativeDenseScratch();
    NativeDenseScratch(const NativeDenseScratch&) = delete;
    NativeDenseScratch& operator=(const NativeDenseScratch&) = delete;

    uint16_t* buffer(int cls, int slot) const;
    cudaError_t wait_before_reuse(int slot, cudaStream_t stream) const;
    cudaError_t mark_released(int slot, cudaStream_t stream);
    int device() const { return device_; }

private:
    NativeDenseScratch() = default;
    void release();

    int device_ = -1;
    void* buffers_[2][2] = {};
    cudaEvent_t released_[2] = {};
    bool event_set_[2] = {};
};

} // namespace strata::core
