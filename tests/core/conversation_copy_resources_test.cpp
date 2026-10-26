// The private conversation-copy stream and ordering event belong to the thread that first needed them.
// Link wrapping supplies a CUDA backend that never initializes a real device, so this test can drive the exact
// production ownership path and observe thread-exit cleanup on a host without a GPU.
#include "conversation_copy.hpp"

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>

namespace {

struct Counters {
    int stream_creates = 0;
    int stream_syncs = 0;
    int stream_destroys = 0;
    int event_creates = 0;
    int event_records = 0;
    int event_waits = 0;
    int event_destroys = 0;
    int copies = 0;
};

Counters counts;

void check(bool condition, const char* message) {
    if (condition) return;
    std::fprintf(stderr, "conversation_copy_resources_test: %s\n", message);
    std::exit(1);
}

} // namespace

extern "C" cudaError_t __wrap_cudaGetDevice(int* device) {
    *device = 0;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaSetDevice(int) {
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaStreamCreateWithFlags(cudaStream_t* stream, unsigned) {
    ++counts.stream_creates;
    *stream = reinterpret_cast<cudaStream_t>(static_cast<uintptr_t>(1));
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaStreamSynchronize(cudaStream_t) {
    ++counts.stream_syncs;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaStreamDestroy(cudaStream_t) {
    ++counts.stream_destroys;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaStreamWaitEvent(cudaStream_t, cudaEvent_t, unsigned) {
    ++counts.event_waits;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaEventCreateWithFlags(cudaEvent_t* event, unsigned) {
    ++counts.event_creates;
    *event = reinterpret_cast<cudaEvent_t>(static_cast<uintptr_t>(2));
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaEventRecord(cudaEvent_t, cudaStream_t) {
    ++counts.event_records;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaEventDestroy(cudaEvent_t) {
    ++counts.event_destroys;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaMemcpyAsync(void* dst, const void* src, size_t bytes,
                                               cudaMemcpyKind, cudaStream_t) {
    ++counts.copies;
    std::memcpy(dst, src, bytes);
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaGetLastError() {
    return cudaSuccess;
}

int main() {
    cudaError_t immediate = cudaSuccess;
    cudaError_t queued = cudaSuccess;
    cudaError_t ordered = cudaSuccess;
    std::thread worker([&] {
        uint32_t source = 42, destination = 0, queued_destination = 0;
        immediate = strata::core::conversation_detail::copy_nonblocking(
            &destination, &source, sizeof source);
        queued = strata::core::conversation_detail::copy_nonblocking(
            &queued_destination, &source, sizeof source, true);
        ordered = strata::core::conversation_detail::order_after_queued();

        // Repeated calls reuse the same per-device stream and event.
        immediate = immediate == cudaSuccess
            ? strata::core::conversation_detail::copy_nonblocking(
                  &destination, &source, sizeof source)
            : immediate;
        ordered = ordered == cudaSuccess
            ? strata::core::conversation_detail::order_after_queued()
            : ordered;
    });
    worker.join();

    check(immediate == cudaSuccess && queued == cudaSuccess && ordered == cudaSuccess,
          "conversation copies failed");
    check(counts.stream_creates == 1, "the worker created more than one private stream");
    check(counts.stream_destroys == 1, "thread exit did not destroy the private stream");
    check(counts.event_creates == 1, "the worker created more than one ordering event");
    check(counts.event_destroys == 1, "thread exit did not destroy the ordering event");
    check(counts.stream_syncs >= 2, "copies and cleanup did not synchronize the private stream");
    check(counts.event_records == 2 && counts.event_waits == 2,
          "queued copies were not ordered on the legacy stream");
    check(counts.copies == 3, "the copy backend saw an unexpected number of transfers");

    std::printf("conversation_copy_resources_test: per-device resources released at thread exit\n");
    return 0;
}
