// The NativeDense dequantization scratch belongs to one table and one CUDA device.  Link wrapping observes
// transactional creation and teardown without opening a device.
#include "strata/core/native_dense_scratch.hpp"

#include <cstdint>
#include <cstdio>
#include <cstdlib>

namespace {

int current_device = 0;
int allocations = 0;
int frees = 0;
int event_creates = 0;
int event_destroys = 0;
int event_records = 0;
int stream_waits = 0;
int synchronizations = 0;
int fail_alloc_at = 0;
int fail_event_at = 0;
bool fail_set_device = false;

void check(bool condition, const char* message) {
    if (condition) return;
    std::fprintf(stderr, "native_dense_scratch_test: %s\n", message);
    std::exit(1);
}

void reset_counts() {
    allocations = frees = event_creates = event_destroys = 0;
    event_records = stream_waits = synchronizations = 0;
    fail_alloc_at = fail_event_at = 0;
    fail_set_device = false;
}

} // namespace

extern "C" cudaError_t __wrap_cudaGetDevice(int* device) {
    *device = current_device;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaSetDevice(int device) {
    if (fail_set_device) return cudaErrorInvalidValue;
    current_device = device;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaMalloc(void** pointer, size_t) {
    ++allocations;
    if (fail_alloc_at > 0 && allocations == fail_alloc_at) {
        *pointer = nullptr;
        return cudaErrorMemoryAllocation;
    }
    *pointer = reinterpret_cast<void*>(static_cast<uintptr_t>(0x1000 + allocations * 0x100));
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaFree(void* pointer) {
    check(pointer != nullptr, "null device allocation reached cudaFree");
    ++frees;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaEventCreateWithFlags(cudaEvent_t* event, unsigned flags) {
    ++event_creates;
    if (flags != cudaEventDisableTiming || (fail_event_at > 0 && event_creates == fail_event_at)) {
        *event = nullptr;
        return cudaErrorMemoryAllocation;
    }
    *event = reinterpret_cast<cudaEvent_t>(static_cast<uintptr_t>(0x9000 + event_creates * 0x100));
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaEventDestroy(cudaEvent_t event) {
    check(event != nullptr, "null event reached cudaEventDestroy");
    ++event_destroys;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaEventRecord(cudaEvent_t event, cudaStream_t) {
    check(event != nullptr, "null event reached cudaEventRecord");
    ++event_records;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaStreamWaitEvent(cudaStream_t, cudaEvent_t event, unsigned) {
    check(event != nullptr, "null event reached cudaStreamWaitEvent");
    ++stream_waits;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaDeviceSynchronize() {
    ++synchronizations;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaGetLastError() {
    return cudaSuccess;
}

int main() {
    using strata::core::NativeDenseScratch;
    std::string err;

    reset_counts();
    {
        auto scratch = NativeDenseScratch::create(2, 8, err);
        check(scratch != nullptr, "scratch creation failed");
        check(current_device == 0, "scratch creation did not restore the caller's device");
        check(allocations == 4, "scratch did not allocate four buffers");
        check(scratch->buffer(0, 0) && scratch->buffer(1, 1), "scratch buffers are unavailable");
        check(scratch->buffer(2, 0) == nullptr && scratch->buffer(0, 2) == nullptr,
              "an invalid scratch slot was accepted");
        check(scratch->wait_before_reuse(0, nullptr) == cudaSuccess && stream_waits == 0,
              "an unused slot waited unnecessarily");
        check(scratch->mark_released(0, nullptr) == cudaSuccess && event_records == 1,
              "the prompt reader was not recorded");
        check(scratch->wait_before_reuse(0, nullptr) == cudaSuccess && stream_waits == 1,
              "the next reader did not wait for the previous one");
        check(event_creates == 2, "scratch did not create two ordering events");
    }
    check(current_device == 0, "scratch destruction did not restore the caller's device");
    check(synchronizations == 1, "scratch destruction did not synchronize its device");
    check(event_destroys == 2, "scratch destruction retained an event");
    check(frees == 4, "scratch destruction retained a device buffer");

    reset_counts();
    err.clear();
    auto wrong_device_release = NativeDenseScratch::create(2, 8, err);
    check(wrong_device_release != nullptr, "wrong-device release fixture could not be created");
    current_device = 1;
    fail_set_device = true;
    wrong_device_release.reset();
    check(frees == 0 && event_destroys == 0 && synchronizations == 0,
          "destruction touched resources while its device was unavailable");
    check(current_device == 1, "failed destruction changed the caller's device");
    current_device = 0;

    reset_counts();
    fail_alloc_at = 3;
    err.clear();
    check(NativeDenseScratch::create(1, 8, err) == nullptr, "a partial allocation was reported as success");
    check(frees == 2, "a partial allocation retained its first buffers");
    check(event_creates == 0, "a partial allocation created an event");
    check(current_device == 0, "a failed allocation did not restore the caller's device");

    reset_counts();
    fail_event_at = 2;
    err.clear();
    check(NativeDenseScratch::create(1, 8, err) == nullptr, "a partial event creation was reported as success");
    check(frees == 4, "a partial event creation retained its buffers");
    check(event_destroys == 1, "a partial event creation retained its first event");
    check(current_device == 0, "a failed event creation did not restore the caller's device");

    reset_counts();
    err.clear();
    check(NativeDenseScratch::create(-1, 8, err) == nullptr, "an invalid device was accepted");
    check(NativeDenseScratch::create(0, 0, err) == nullptr, "an empty scratch was accepted");
    check(allocations == 0 && event_creates == 0, "an invalid scratch allocated resources");

    std::printf("native_dense_scratch_test: transactional creation and device-owned release passed\n");
    return 0;
}
