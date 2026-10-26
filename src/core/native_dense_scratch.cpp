#include "strata/core/native_dense_scratch.hpp"

#include "strata/core/on_device.hpp"

#include <limits>

namespace strata::core {

std::shared_ptr<NativeDenseScratch> NativeDenseScratch::create(int device, uint64_t elements, std::string& err) {
    if (device < 0 || elements == 0 ||
        elements > (std::numeric_limits<size_t>::max)() / sizeof(uint16_t)) {
        err = "native dense scratch: invalid device or matrix size";
        return {};
    }

    const OnDevice on_device(device);
    int current = -1;
    if (cudaGetDevice(&current) != cudaSuccess || current != device) {
        err = "native dense scratch: cannot enter the owning device";
        return {};
    }

    auto scratch = std::shared_ptr<NativeDenseScratch>(new NativeDenseScratch());
    scratch->device_ = device;
    const size_t bytes = (size_t) elements * sizeof(uint16_t);
    for (auto& cls : scratch->buffers_) {
        for (void*& buffer : cls) {
            if (cudaMalloc(&buffer, bytes) != cudaSuccess) {
                err = "native dense scratch: device allocation failed";
                scratch.reset();
                return {};
            }
        }
    }
    for (cudaEvent_t& event : scratch->released_) {
        event = nullptr;
        if (cudaEventCreateWithFlags(&event, cudaEventDisableTiming) != cudaSuccess) {
            err = "native dense scratch: event creation failed";
            scratch.reset();
            return {};
        }
    }
    return scratch;
}

NativeDenseScratch::~NativeDenseScratch() {
    release();
}

void NativeDenseScratch::release() {
    const OnDevice on_device(device_);
    if (!on_device.ok) return;   // retain the buffers and events until their owning device is available again
    if (device_ >= 0) (void) cudaDeviceSynchronize();
    for (cudaEvent_t& event : released_) {
        if (event) (void) cudaEventDestroy(event);
        event = nullptr;
    }
    for (auto& cls : buffers_) {
        for (void*& buffer : cls) {
            if (buffer) (void) cudaFree(buffer);
            buffer = nullptr;
        }
    }
    for (bool& set : event_set_) set = false;
    device_ = -1;
    (void) cudaGetLastError();
}

uint16_t* NativeDenseScratch::buffer(int cls, int slot) const {
    if (cls < 0 || cls > 1 || slot < 0 || slot > 1 || buffers_[cls][slot] == nullptr) return nullptr;
    return static_cast<uint16_t*>(buffers_[cls][slot]);
}

cudaError_t NativeDenseScratch::wait_before_reuse(int slot, cudaStream_t stream) const {
    if (slot < 0 || slot > 1) return cudaErrorInvalidValue;
    if (!event_set_[slot]) return cudaSuccess;
    return cudaStreamWaitEvent(stream, released_[slot], 0);
}

cudaError_t NativeDenseScratch::mark_released(int slot, cudaStream_t stream) {
    if (slot < 0 || slot > 1 || released_[slot] == nullptr) return cudaErrorInvalidValue;
    const cudaError_t status = cudaEventRecord(released_[slot], stream);
    if (status == cudaSuccess) event_set_[slot] = true;
    return status;
}

} // namespace strata::core
