// Host-only ownership and failure tests for opt-in CUDA timing diagnostics.  Link wrapping replaces the CUDA
// runtime, so these checks exercise the exact production owner without opening a device.
#include "strata/platform/gpu_event_pairs.hpp"

#include <cstdint>
#include <cstdio>
#include <cstdlib>

namespace {

int current_device = 0;
int creates = 0;
int destroys = 0;
int fail_create_at = 0;
int fail_set_device = -1;
int fail_restore_at = -1;
int set_device_calls = 0;

void check(bool condition, const char* message) {
    if (condition) return;
    std::fprintf(stderr, "gpu_event_pairs_test: %s\n", message);
    std::exit(1);
}

} // namespace

extern "C" cudaError_t __wrap_cudaGetDevice(int* device) {
    *device = current_device;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaSetDevice(int device) {
    ++set_device_calls;
    if (device == fail_set_device || set_device_calls == fail_restore_at)
        return cudaErrorInvalidValue;
    current_device = device;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaEventCreateWithFlags(cudaEvent_t* event, unsigned flags) {
    ++creates;
    if (flags != cudaEventDefault) {
        *event = nullptr;
        return cudaErrorInvalidValue;
    }
    if (fail_create_at > 0 && creates == fail_create_at) {
        *event = nullptr;
        return cudaErrorMemoryAllocation;
    }
    *event = reinterpret_cast<cudaEvent_t>(static_cast<uintptr_t>(creates));
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaEventDestroy(cudaEvent_t event) {
    if (!event) return cudaErrorInvalidValue;
    ++destroys;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaEventRecord(cudaEvent_t event, cudaStream_t) {
    return event ? cudaSuccess : cudaErrorInvalidValue;
}

extern "C" cudaError_t __wrap_cudaEventSynchronize(cudaEvent_t event) {
    return event ? cudaSuccess : cudaErrorInvalidValue;
}

extern "C" cudaError_t __wrap_cudaEventElapsedTime(float* milliseconds, cudaEvent_t start, cudaEvent_t end) {
    if (!start || !end) return cudaErrorInvalidValue;
    *milliseconds = 1.25f;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaGetLastError() {
    return cudaSuccess;
}

int main() {
    using strata::platform::GpuEventPairs;

    {
        GpuEventPairs events;
        check(events.ensure(2, 3), "event group creation failed");
        check(events.ready(2, 3), "event group is not ready");
        check(current_device == 0, "creation did not restore the caller's device");
        check(creates == 6, "the event group did not create start/end pairs");
        check(events.record_start(1, nullptr) == cudaSuccess, "start event record failed");
        check(events.record_end(1, nullptr) == cudaSuccess, "end event record failed");
        float milliseconds = 0;
        check(events.elapsed_ms(1, milliseconds) == cudaSuccess && milliseconds == 1.25f,
              "elapsed time did not use the requested pair");
        check(events.ensure(2, 3), "reusing a ready group failed");
        check(creates == 6, "a ready group was recreated");
        check(!events.ensure(2, 0), "an invalid pair count was accepted");
        check(events.ready(2, 3), "an invalid request destroyed a ready group");
        events.release();
        check(destroys == 6, "release did not destroy every event");
        check(!events.ready(2, 3), "a released group still reports ready");
    }

    creates = destroys = 0;
    {
        GpuEventPairs events;
        check(events.ensure(1, 2), "destructor fixture creation failed");
    }
    check(destroys == 4, "the destructor did not destroy its event pairs");
    check(current_device == 0, "destruction did not restore the caller's device");

    creates = destroys = 0;
    fail_create_at = 4;
    GpuEventPairs retry;
    check(!retry.ensure(1, 3), "partial event creation was not reported");
    check(destroys == 3, "partial event creation retained earlier events");
    check(!retry.ready(1, 3), "a failed group reports ready");
    fail_create_at = 0;
    const int before = creates;
    check(retry.ensure(1, 3), "retry after a creation failure did not succeed");
    check(creates - before == 6, "retry did not create a complete event group");
    retry.release();

    creates = destroys = 0;
    set_device_calls = 0;
    fail_restore_at = 2;   // creation switches to device 1; restoration switches back to device 0
    GpuEventPairs unrecovered;
    check(!unrecovered.ensure(1, 2), "a failed device restore was not reported");
    check(destroys == 4, "a failed device restore retained its event group");
    check(!unrecovered.ready(1, 2), "a group with a failed restore reports ready");
    fail_restore_at = -1;
    current_device = 0;

    std::printf("gpu_event_pairs_test: transactional creation and device-owned release passed\n");
    return 0;
}
