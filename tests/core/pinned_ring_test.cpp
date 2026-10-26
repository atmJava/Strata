// A PinnedRing belongs to the device current at its first use.  Release must switch back to that device before destroying
// events or freeing the pinned slots, then restore the caller's device.
#include "strata/core/pinned_ring.hpp"

#include <cuda_runtime.h>

#include <cstdio>
#include <cstdlib>

namespace {

struct Event {
    int device = -1;
};

int current_device = 0;
int host_allocs = 0;
int host_frees = 0;
int event_destroys = 0;
unsigned last_flags = 0;

void check(bool condition, const char* message) {
    if (condition) return;
    std::fprintf(stderr, "pinned_ring_test: %s\n", message);
    std::exit(1);
}

} // namespace

extern "C" cudaError_t __wrap_cudaGetDevice(int* device) {
    *device = current_device;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaSetDevice(int device) {
    current_device = device;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaHostAlloc(void** pointer, std::size_t bytes, unsigned flags) {
    ++host_allocs;
    last_flags = flags;
    *pointer = std::malloc(bytes);
    return *pointer != nullptr || bytes == 0 ? cudaSuccess : cudaErrorMemoryAllocation;
}

extern "C" cudaError_t __wrap_cudaFreeHost(void* pointer) {
    ++host_frees;
    std::free(pointer);
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaEventCreateWithFlags(cudaEvent_t* event, unsigned) {
    Event* made = new Event{current_device};
    *event = reinterpret_cast<cudaEvent_t>(made);
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaEventDestroy(cudaEvent_t event) {
    Event* made = reinterpret_cast<Event*>(event);
    check(made != nullptr && made->device == current_device,
          "an event was destroyed outside the device that created it");
    ++event_destroys;
    delete made;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaEventSynchronize(cudaEvent_t) { return cudaSuccess; }
extern "C" cudaError_t __wrap_cudaEventRecord(cudaEvent_t, cudaStream_t) { return cudaSuccess; }
extern "C" cudaError_t __wrap_cudaMemcpyAsync(void*, const void*, std::size_t, cudaMemcpyKind, cudaStream_t) {
    return cudaSuccess;
}
extern "C" cudaError_t __wrap_cudaStreamSynchronize(cudaStream_t) { return cudaSuccess; }
extern "C" cudaError_t __wrap_cudaGetLastError() { return cudaSuccess; }
extern "C" const char* __wrap_cudaGetErrorString(cudaError_t) { return "stub"; }

int main() {
    strata::core::PinnedRing ring;
    current_device = 1;
    void* stage = ring.acquire(64);
    check(stage != nullptr, "a pinned slot could not be acquired");
    check(host_allocs == 1 && last_flags == cudaHostAllocPortable,
          "the pinned slot lost its allocation or portable flag");

    current_device = 0;
    ring.release();
    check(host_frees == 1 && event_destroys == 1,
          "release did not free the pinned slot and its event exactly once");
    check(current_device == 0, "release did not restore the caller's device");

    ring.release();
    check(host_frees == 1 && event_destroys == 1, "a second release freed the same slot again");
    std::puts("pinned_ring_test: release runs on the ring's device and restores the caller");
    return 0;
}
