// A failed device alias must release the mapped-pinned host allocation; a failed host allocation must not publish or
// release anything.  Link wrapping keeps the ownership path independent of a CUDA driver.
#include "strata/core/mapped_host.hpp"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace {

int host_allocs = 0;
int host_frees = 0;
int device_aliases = 0;
bool fail_host_alloc = false;
bool fail_device_alias = false;
unsigned last_flags = 0;

void check(bool condition, const char* message) {
    if (condition) return;
    std::fprintf(stderr, "mapped_host_test: %s\n", message);
    std::exit(1);
}

void reset_counts() {
    host_allocs = host_frees = device_aliases = 0;
    fail_host_alloc = fail_device_alias = false;
    last_flags = 0;
}

} // namespace

extern "C" cudaError_t __wrap_cudaHostAlloc(void** host, std::size_t bytes, unsigned flags) {
    ++host_allocs;
    last_flags = flags;
    if (fail_host_alloc) return cudaErrorMemoryAllocation;
    *host = std::malloc(bytes);
    return *host != nullptr || bytes == 0 ? cudaSuccess : cudaErrorMemoryAllocation;
}

extern "C" cudaError_t __wrap_cudaHostGetDevicePointer(void** device, void* host, unsigned) {
    ++device_aliases;
    if (fail_device_alias) return cudaErrorInvalidValue;
    *device = (void*) ((std::uintptr_t) host + 0x1000);
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaFreeHost(void* host) {
    ++host_frees;
    std::free(host);
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaGetLastError() {
    return cudaSuccess;
}

int main() {
    using strata::core::alloc_mapped_pinned;
#if defined(_WIN32)
    _putenv_s("STRATA_TEST_PIN_BUDGET_MIB", "1");
#else
    setenv("STRATA_TEST_PIN_BUDGET_MIB", "1", 1);
#endif
    auto& pin = strata::core::pin_detail::st();
    void* old_host = (void*) 0x1000;
    void* old_device = (void*) 0x2000;
    void* host = old_host;
    void* device = old_device;

    reset_counts();
    check(alloc_mapped_pinned(64, &host, &device, cudaHostAllocPortable),
          "a successful mapped allocation was rejected");
    check(host != old_host && device != nullptr, "a successful mapped allocation was not published");
    check(last_flags == (cudaHostAllocMapped | cudaHostAllocPortable),
          "the mapped allocation lost its flags");
    check(((unsigned char*) host)[0] == 0 && ((unsigned char*) host)[63] == 0,
          "the mapped allocation was not zeroed");
    check(pin.sim_used == 64, "the mapped allocation was not charged to the pin budget");
    strata::core::host_free_guarded(host);
    check(host_frees == 1, "the successful allocation did not free exactly once");
    check(pin.sim_used == 0, "the successful allocation did not return its pin budget");

    reset_counts();
    host = old_host;
    device = old_device;
    check(alloc_mapped_pinned(8, &host, &device, 0, false),
          "an allocation that skips initialization was rejected");
    std::memset(host, 0x5a, 8);
    check(static_cast<unsigned char*>(host)[7] == 0x5a,
          "an allocation that skips initialization was overwritten");
    strata::core::host_free_guarded(host);
    check(host_frees == 1, "the uninitialized allocation did not free exactly once");
    check(pin.sim_used == 0, "the uninitialized allocation did not return its pin budget");

    reset_counts();
    fail_device_alias = true;
    host = old_host;
    device = old_device;
    check(!alloc_mapped_pinned(64, &host, &device),
          "a failed device alias was accepted");
    check(host == old_host && device == old_device,
          "a failed device alias changed the caller's outputs");
    check(host_allocs == 1 && device_aliases == 1 && host_frees == 1,
          "a failed device alias retained its host allocation");

    reset_counts();
    fail_host_alloc = true;
    host = old_host;
    device = old_device;
    check(!alloc_mapped_pinned(64, &host, &device),
          "a failed host allocation was accepted");
    check(host == old_host && device == old_device,
          "a failed host allocation changed the caller's outputs");
    check(host_allocs == 1 && device_aliases == 0 && host_frees == 0,
          "a failed host allocation touched a resource it never acquired");

    std::puts("mapped_host_test: mapped host ownership and failure rollback pass");
    return 0;
}
