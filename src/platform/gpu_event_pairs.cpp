#include "strata/platform/gpu_event_pairs.hpp"

#include <limits>

namespace strata::platform {

bool GpuEventPairs::ready(int device, size_t pairs) const {
    if (device_ != device || pairs_ != pairs || events_.size() != pairs * 2) return false;
    for (cudaEvent_t event : events_)
        if (event == nullptr) return false;
    return true;
}

bool GpuEventPairs::ensure(int device, size_t pairs) {
    if (ready(device, pairs)) return true;
    if (device < 0 || pairs == 0 || pairs > std::numeric_limits<size_t>::max() / 2) return false;
    if (!release()) return false;

    int previous = -1;
    if (cudaGetDevice(&previous) != cudaSuccess) return false;
    if (cudaSetDevice(device) != cudaSuccess) {
        (void) cudaGetLastError();
        return false;
    }

    std::vector<cudaEvent_t> events(pairs * 2, nullptr);
    bool ok = true;
    for (cudaEvent_t& event : events) {
        // cudaEventElapsedTime needs a timing-enabled event.  This owner exists only for opt-in measurements.
        if (cudaEventCreateWithFlags(&event, cudaEventDefault) != cudaSuccess) {
            event = nullptr;
            ok = false;
            break;
        }
    }
    if (!ok) {
        for (cudaEvent_t event : events)
            if (event) (void) cudaEventDestroy(event);
        (void) cudaSetDevice(previous);
        (void) cudaGetLastError();
        return false;
    }

    if (cudaSetDevice(previous) != cudaSuccess) {
        for (cudaEvent_t event : events)
            if (event) (void) cudaEventDestroy(event);
        (void) cudaSetDevice(previous);
        (void) cudaGetLastError();
        return false;
    }

    device_ = device;
    pairs_ = pairs;
    events_.swap(events);
    return true;
}

bool GpuEventPairs::release() {
    if (!events_.empty() && device_ >= 0) {
        int previous = -1;
        if (cudaGetDevice(&previous) != cudaSuccess || cudaSetDevice(device_) != cudaSuccess) {
            (void) cudaGetLastError();
            return false;
        }
        bool destroyed = true;
        for (cudaEvent_t& event : events_) {
            if (event == nullptr) continue;
            if (cudaEventDestroy(event) == cudaSuccess) event = nullptr;
            else destroyed = false;
        }
        const bool restored = cudaSetDevice(previous) == cudaSuccess;
        if (!destroyed) {
            pairs_ = 0;
            (void) cudaGetLastError();
            return false;
        }
        events_.clear();
        pairs_ = 0;
        device_ = -1;
        (void) cudaGetLastError();
        return restored;
    }
    events_.clear();
    pairs_ = 0;
    device_ = -1;
    (void) cudaGetLastError();
    return true;
}

cudaError_t GpuEventPairs::record_start(size_t pair, cudaStream_t stream) const {
    if (pair >= pairs_) return cudaErrorInvalidValue;
    return cudaEventRecord(events_[pair * 2], stream);
}

cudaError_t GpuEventPairs::record_end(size_t pair, cudaStream_t stream) const {
    if (pair >= pairs_) return cudaErrorInvalidValue;
    return cudaEventRecord(events_[pair * 2 + 1], stream);
}

cudaError_t GpuEventPairs::synchronize_end(size_t pair) const {
    if (pair >= pairs_) return cudaErrorInvalidValue;
    return cudaEventSynchronize(events_[pair * 2 + 1]);
}

cudaError_t GpuEventPairs::elapsed_ms(size_t pair, float& milliseconds) const {
    if (pair >= pairs_) return cudaErrorInvalidValue;
    return cudaEventElapsedTime(&milliseconds, events_[pair * 2], events_[pair * 2 + 1]);
}

cudaError_t GpuEventPairs::elapsed_gap_ms(size_t earlier_pair, size_t later_pair, float& milliseconds) const {
    if (earlier_pair >= pairs_ || later_pair >= pairs_) return cudaErrorInvalidValue;
    return cudaEventElapsedTime(&milliseconds, events_[earlier_pair * 2 + 1], events_[later_pair * 2]);
}

} // namespace strata::platform
