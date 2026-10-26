// A failed doorbell initialization owns and frees every region it has acquired, then leaves the caller's
// existing state untouched.  Link wrapping keeps the test independent of a CUDA driver.
#include "strata/core/layer.hpp"

#include <cuda_runtime.h>

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace {

int host_allocs = 0;
int host_frees = 0;
int mappings = 0;
int fail_alloc_at = 0;
int fail_mapping_at = 0;

void check(bool condition, const char* message) {
    if (condition) return;
    std::fprintf(stderr, "doorbell_transaction_test: %s\n", message);
    std::exit(1);
}

void reset_counts() {
    host_allocs = host_frees = mappings = 0;
    fail_alloc_at = fail_mapping_at = 0;
}

bool empty(const strata::core::Doorbell& db) {
    return db.n_embd == 0 && db.k == 0 &&
           db.h_x_f == nullptr && db.h_ids == nullptr && db.h_weights == nullptr &&
           db.h_seq == nullptr && db.h_flag == nullptr &&
           db.d_x_f == nullptr && db.d_ids == nullptr && db.d_weights == nullptr &&
           db.d_seq == nullptr && db.d_flag == nullptr;
}

} // namespace

extern "C" cudaError_t __wrap_cudaHostAlloc(void** pointer, size_t bytes, unsigned flags) {
    ++host_allocs;
    if (flags != cudaHostAllocMapped || (fail_alloc_at > 0 && host_allocs == fail_alloc_at)) {
        *pointer = nullptr;
        return cudaErrorMemoryAllocation;
    }
    *pointer = std::calloc(1, bytes);
    return *pointer ? cudaSuccess : cudaErrorMemoryAllocation;
}

extern "C" cudaError_t __wrap_cudaFreeHost(void* pointer) {
    check(pointer != nullptr, "a null host allocation reached cudaFreeHost");
    ++host_frees;
    std::free(pointer);
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaHostGetDevicePointer(void** device, void* host, unsigned) {
    ++mappings;
    check(host != nullptr, "a null host allocation reached cudaHostGetDevicePointer");
    if (fail_mapping_at > 0 && mappings == fail_mapping_at) {
        *device = nullptr;
        return cudaErrorInvalidValue;
    }
    *device = (uint8_t*) host + 0x1000;
    return cudaSuccess;
}

int main() {
    using strata::core::Doorbell;
    using strata::core::ModelGeometry;

    constexpr int64_t kEmbeddingWidth = 256;
    constexpr int64_t kTokens = 5;
    constexpr uint64_t expected_bytes =
        (uint64_t) kEmbeddingWidth * 4 + (uint64_t) kTokens * 4 * 2 + 4 * 2;
    ModelGeometry geometry;
    geometry.n_embd = kEmbeddingWidth;

    for (int fail_at = 1; fail_at <= 5; ++fail_at) {
        Doorbell db;
        reset_counts();
        fail_alloc_at = fail_at;
        check(strata::core::doorbell_init(geometry, kTokens, db) == 0,
              "a failed host allocation was reported as success");
        check(empty(db), "a host-allocation failure published partial state");
        check(host_allocs == fail_at, "host-allocation failure count is wrong");
        check(host_frees == fail_at - 1, "a host-allocation failure retained an earlier region");
    }

    for (int fail_at = 1; fail_at <= 5; ++fail_at) {
        Doorbell db;
        reset_counts();
        fail_mapping_at = fail_at;
        check(strata::core::doorbell_init(geometry, kTokens, db) == 0,
              "a failed device mapping was reported as success");
        check(empty(db), "a mapping failure published partial state");
        check(host_allocs == fail_at, "mapping failure count is wrong");
        check(mappings == fail_at, "mapping failure did not reach the requested region");
        check(host_frees == fail_at, "a mapping failure retained the current or an earlier region");
    }

    Doorbell db;
    reset_counts();
    check(strata::core::doorbell_init(geometry, kTokens, db) == expected_bytes,
          "successful initialization returned the wrong byte count");
    check(host_allocs == 5 && host_frees == 0 && mappings == 5,
          "successful initialization did not acquire all five regions");
    check(db.n_embd == geometry.n_embd && db.k == kTokens, "successful initialization lost dimensions");
    check(db.h_x_f && db.h_ids && db.h_weights && db.h_seq && db.h_flag,
          "successful initialization lost a host pointer");
    check(db.d_x_f && db.d_ids && db.d_weights && db.d_seq && db.d_flag,
          "successful initialization lost a device pointer");
    check((uint8_t*) db.d_x_f == (uint8_t*) db.h_x_f + 0x1000 &&
              (uint8_t*) db.d_ids == (uint8_t*) db.h_ids + 0x1000 &&
              (uint8_t*) db.d_weights == (uint8_t*) db.h_weights + 0x1000 &&
              (uint8_t*) db.d_seq == (uint8_t*) db.h_seq + 0x1000 &&
              (uint8_t*) db.d_flag == (uint8_t*) db.h_flag + 0x1000,
          "a host pointer was paired with the wrong device mapping");
    check(db.h_x_f[0] == 0.0f && db.h_ids[0] == 0 && db.h_weights[0] == 0.0f &&
              *db.h_seq == 0 && *db.h_flag == 0,
          "a published region was not zero-initialized");

    Doorbell before = db;
    reset_counts();
    check(strata::core::doorbell_init(geometry, kTokens, db) == 0,
          "a second initialization replaced the existing owner");
    check(host_allocs == 0 && host_frees == 0 && mappings == 0,
          "a refused reinitialization touched resources");
    check(std::memcmp(&before, &db, sizeof db) == 0, "a refused reinitialization changed the doorbell");

    strata::core::doorbell_free(db);
    check(host_frees == 5 && empty(db), "normal release retained or published a region");

    reset_counts();
    geometry.n_embd = 0;
    check(strata::core::doorbell_init(geometry, kTokens, db) == 0,
          "a zero embedding width was accepted");
    geometry.n_embd = 1;
    check(strata::core::doorbell_init(geometry, 0, db) == 0, "a zero token capacity was accepted");
    check(host_allocs == 0 && mappings == 0, "an invalid request started allocating");

    std::printf("doorbell_transaction_test: failed initialization releases every acquired region\n");
    return 0;
}
