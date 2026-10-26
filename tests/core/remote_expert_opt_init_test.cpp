#include "strata/core/remote_expert_opt.hpp"
#include "strata/core/remote_experts.hpp"

#include <cuda_runtime.h>

#include <cstdlib>
#include <cstdio>
#include <new>
#include <string>
#include <type_traits>

namespace {

int host_allocs = 0;
int host_frees = 0;
int device_allocs = 0;
int device_frees = 0;
int device_alloc_attempts = 0;
int fail_malloc_at = -1;
bool fail_mapping = false;
int current_device = 0;

void check(bool condition, const char* message) {
    if (!condition) {
        std::fprintf(stderr, "remote_expert_opt_init_test: %s\n", message);
        std::exit(1);
    }
}

void reset_counters() {
    host_allocs = host_frees = device_allocs = device_frees = device_alloc_attempts = 0;
    fail_malloc_at = -1;
    fail_mapping = false;
    current_device = 0;
}

}  // namespace

extern "C" cudaError_t __wrap_cudaGetDevice(int* device) {
    *device = current_device;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaSetDevice(int device) {
    current_device = device;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaHostAlloc(void** pointer, std::size_t, unsigned) {
    *pointer = new int(1);
    ++host_allocs;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaHostGetDevicePointer(void** device, void* host, unsigned) {
    if (fail_mapping) {
        *device = nullptr;
        return cudaErrorInvalidValue;
    }
    *device = host;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaMalloc(void** pointer, std::size_t) {
    ++device_alloc_attempts;
    if (fail_malloc_at > 0 && device_alloc_attempts == fail_malloc_at) {
        *pointer = nullptr;
        return cudaErrorMemoryAllocation;
    }
    *pointer = new int(1);
    ++device_allocs;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaFree(void* pointer) {
    if (pointer != nullptr) {
        ++device_frees;
        delete static_cast<int*>(pointer);
    }
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaFreeHost(void* pointer) {
    if (pointer != nullptr) {
        ++host_frees;
        delete static_cast<int*>(pointer);
    }
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaStreamSynchronize(cudaStream_t) {
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaGetLastError() {
    return cudaSuccess;
}

extern "C" const char* __wrap_cudaGetErrorString(cudaError_t) {
    return "injected";
}

int main() {
    using Storage = std::aligned_storage_t<sizeof(strata::core::RemoteExperts),
                                           alignof(strata::core::RemoteExperts)>;
    reset_counters();
    Storage first_storage;
    Storage second_storage;
    auto* first = new (&first_storage) strata::core::RemoteExperts();
    auto* second = new (&second_storage) strata::core::RemoteExperts();

    {
        strata::core::RemoteExpertOpt opt;
        opt.attach(*first);

        fail_mapping = true;
        std::string err;
        check(!opt.init(err), "mapping failure must fail initialization");
        check(host_allocs == 1 && host_frees == 1, "mapping failure must roll back host staging");
        check(device_allocs == 0 && device_frees == 0, "mapping failure must not leave device sums");

        fail_mapping = false;
        fail_malloc_at = 2;
        opt.attach(*second);
        err.clear();
        check(!opt.init(err), "second peer allocation failure must fail initialization");
        check(host_allocs == 2 && host_frees == 2, "second failure must roll back host staging");
        check(device_allocs == 1 && device_frees == 1, "second failure must roll back the first peer sum");
        check(!err.empty(), "a failed initialization must report its cause");

        fail_malloc_at = -1;
        err.clear();
        check(opt.init(err), "initialization must be retryable after rollback");
        check(host_allocs == 3 && host_frees == 2, "successful retry must retain one host allocation");
        check(device_allocs == 3 && device_frees == 1, "successful retry must retain both peer sums");
        check(first->optimized_decode() && second->optimized_decode(), "attached peers must remain linked");
    }

    check(host_frees == 3, "destruction must free the retained host staging exactly once");
    check(device_frees == 3, "destruction must free both retained peer sums exactly once");
    check(!first->optimized_decode() && !second->optimized_decode(), "destruction must unlink every peer");
    std::puts("remote_expert_opt_init_test: initialization rollback and retry");
    return 0;
}
