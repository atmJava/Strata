#include "conversation_copy.hpp"

#include <cstddef>
#include <vector>

namespace strata::core::conversation_detail {
namespace {

struct PrivateDevice {
    cudaStream_t stream = nullptr;
    cudaEvent_t ordered = nullptr;

    cudaError_t ensure_stream() {
        if (stream) return cudaSuccess;
        stream = nullptr;
        const cudaError_t status =
            cudaStreamCreateWithFlags(&stream, cudaStreamNonBlocking);
        if (status != cudaSuccess) stream = nullptr;
        return status;
    }

    cudaError_t ensure_event() {
        if (ordered) return cudaSuccess;
        ordered = nullptr;
        const cudaError_t status =
            cudaEventCreateWithFlags(&ordered, cudaEventDisableTiming);
        if (status != cudaSuccess) ordered = nullptr;
        return status;
    }
};

/// One thread owns at most one stream and one ordering event per CUDA device.  A thread-local destructor makes
/// that ownership end with the thread, including short-lived checkpoint and graph-launch helpers.
struct PrivateResources {
    std::vector<PrivateDevice> devices;

    ~PrivateResources() {
        int previous = 0;
        const bool restore = cudaGetDevice(&previous) == cudaSuccess;
        for (size_t device = 0; device < devices.size(); ++device) {
            PrivateDevice& resources = devices[device];
            if (!resources.stream && !resources.ordered) continue;
            if (cudaSetDevice((int) device) != cudaSuccess) {
                (void) cudaGetLastError();
                continue;
            }
            if (resources.stream) (void) cudaStreamSynchronize(resources.stream);
            if (resources.ordered) (void) cudaEventDestroy(resources.ordered);
            if (resources.stream) (void) cudaStreamDestroy(resources.stream);
            resources = {};
            (void) cudaGetLastError();
        }
        if (restore) (void) cudaSetDevice(previous);
        (void) cudaGetLastError();
    }
};

PrivateDevice& device_resources(int device) {
    thread_local PrivateResources resources;
    if (device < 0) device = 0;
    if ((size_t) device >= resources.devices.size())
        resources.devices.resize((size_t) device + 1);
    return resources.devices[(size_t) device];
}

} // namespace

cudaError_t private_stream(cudaStream_t& stream) {
    int device = 0;
    cudaError_t status = cudaGetDevice(&device);
    if (status != cudaSuccess) return status;
    PrivateDevice& resources = device_resources(device);
    status = resources.ensure_stream();
    if (status != cudaSuccess) return status;
    stream = resources.stream;
    return cudaSuccess;
}

cudaError_t copy_nonblocking(void* dst, const void* src, size_t bytes, bool queued) {
    cudaStream_t stream = nullptr;
    cudaError_t status = private_stream(stream);
    if (status != cudaSuccess) return status;
    status = cudaMemcpyAsync(dst, src, bytes, cudaMemcpyDefault, stream);
    if (status != cudaSuccess || queued) return status;
    status = cudaStreamSynchronize(stream);
    core::progress_beat();   // #2049: each completed parked-conversation copy is progress
    return status;
}

cudaError_t order_after_queued() {
    cudaStream_t stream = nullptr;
    cudaError_t status = private_stream(stream);
    if (status != cudaSuccess) return status;

    int device = 0;
    status = cudaGetDevice(&device);
    if (status != cudaSuccess) return status;
    PrivateDevice& resources = device_resources(device);
    status = resources.ensure_event();
    if (status != cudaSuccess) return status;
    status = cudaEventRecord(resources.ordered, stream);
    if (status != cudaSuccess) return status;
    return cudaStreamWaitEvent(nullptr, resources.ordered, 0);
}

} // namespace strata::core::conversation_detail
