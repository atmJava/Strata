// QSA state owns pinned buffers outside the session arena.  Link wrapping observes their release without a
// CUDA driver; a real anonymous mapping covers the cudaHostRegister teardown path.
#include "strata/core/layer.hpp"

#include <cuda_runtime.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>

#include <sys/mman.h>

namespace {

int host_frees = 0;
int host_unregisters = 0;
int rope_releases = 0;

void check(bool condition, const char* message) {
    if (condition) return;
    std::fprintf(stderr, "qsa_state_resource_test: %s\n", message);
    std::exit(1);
}

void reset_counts() {
    host_frees = host_unregisters = rope_releases = 0;
}

} // namespace

extern "C" cudaError_t __wrap_cudaFreeHost(void* pointer) {
    check(pointer != nullptr, "a null pointer reached cudaFreeHost");
    ++host_frees;
    std::free(pointer);
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaHostUnregister(void* pointer) {
    check(pointer != nullptr, "a null pointer reached cudaHostUnregister");
    ++host_unregisters;
    return cudaSuccess;
}

namespace strata::kernels {
void rope_table_release(const float*) {
    ++rope_releases;
}
} // namespace strata::kernels

int main() {
    using strata::core::QsaState;

    reset_counts();
    QsaState registered;
    registered.host_step = (int32_t*) std::malloc(64);
    registered.host_pos = (int32_t*) std::malloc(64);
    registered.kv_host_allocation_bytes = 4096;
    registered.kv_host_allocation = (uint8_t*) mmap(
        nullptr, (size_t) registered.kv_host_allocation_bytes, PROT_READ | PROT_WRITE,
        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    check(registered.kv_host_allocation != MAP_FAILED, "the registered-path fixture mapping failed");
    registered.kv_host_registered = true;
    registered.host.k_pool = (uint16_t*) registered.kv_host_allocation;
    strata::core::qsa_kv_host_allocated(registered.kv_host_allocation_bytes);

    strata::core::qsa_state_release(registered);
    check(host_frees == 2 && host_unregisters == 1,
          "registered KV state did not release both pinned staging buffers and unregister the host copy");
    check(strata::core::qsa_kv_host_bytes() == 0, "registered KV bytes remained accounted after release");
    check(registered.host_step == nullptr && registered.host_pos == nullptr &&
              registered.kv_host_allocation == nullptr && !registered.host.present(),
          "release exposed a pointer that is no longer owned");

    strata::core::qsa_state_release(registered);
    check(host_frees == 2 && host_unregisters == 1, "a second release freed the same resources again");

    reset_counts();
    QsaState allocated;
    constexpr uint64_t kAllocatedBytes = 2048;
    allocated.kv_host_allocation = (uint8_t*) std::malloc((size_t) kAllocatedBytes);
    allocated.kv_host_allocation_bytes = kAllocatedBytes;
    strata::core::qsa_kv_host_allocated(kAllocatedBytes);
    strata::core::qsa_state_release(allocated);
    check(host_frees == 1 && host_unregisters == 0,
          "cudaHostAlloc-backed KV state used the registered teardown path");
    check(strata::core::qsa_kv_host_bytes() == 0, "allocated KV bytes remained accounted after release");

    reset_counts();
    QsaState borrowed_rope;
    borrowed_rope.cos_tab = (float*) 0x1000;
    borrowed_rope.sin_tab = (float*) 0x2000;
    borrowed_rope.owns_rope = false;
    strata::core::qsa_state_release(borrowed_rope);
    check(rope_releases == 0, "release freed a borrowed RoPE table");

    QsaState owned_rope;
    owned_rope.cos_tab = (float*) 0x1000;
    owned_rope.sin_tab = (float*) 0x2000;
    owned_rope.owns_rope = true;
    strata::core::qsa_state_release(owned_rope);
    check(rope_releases == 1 && !owned_rope.owns_rope, "an owned RoPE registry entry was not released");

    std::printf("qsa_state_resource_test: host and RoPE resources release exactly once\n");
    return 0;
}
