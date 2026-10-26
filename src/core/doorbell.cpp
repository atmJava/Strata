#include "strata/core/layer.hpp"

#include <cuda_runtime.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>

namespace strata::core {
namespace {

bool region_bytes(int64_t elements, size_t& bytes) {
    if (elements <= 0 || (uint64_t) elements > (uint64_t) (std::numeric_limits<size_t>::max)() / 4u)
        return false;
    bytes = (size_t) elements * 4u;
    return true;
}

bool total_bytes(size_t& total, size_t add) {
    if (add > (std::numeric_limits<size_t>::max)() - total) return false;
    total += add;
    return true;
}

bool owns_any(const Doorbell& db) {
    return db.h_x_f || db.h_ids || db.h_weights || db.h_seq || db.h_flag;
}

template <typename T>
bool allocate_region(size_t bytes, T*& host, T*& device, const char* name) {
    void* new_host = nullptr;
    void* new_device = nullptr;
    if (cudaHostAlloc(&new_host, bytes, cudaHostAllocMapped) != cudaSuccess) {
        std::fprintf(stderr, "doorbell_init: cudaHostAlloc(%s) failed\n", name);
        return false;
    }
    if (cudaHostGetDevicePointer(&new_device, new_host, 0) != cudaSuccess || new_device == nullptr) {
        std::fprintf(stderr, "doorbell_init: cudaHostGetDevicePointer(%s) failed\n", name);
        (void) cudaFreeHost(new_host);
        return false;
    }
    std::memset(new_host, 0, bytes);
    host = static_cast<T*>(new_host);
    device = static_cast<T*>(new_device);
    return true;
}

} // namespace

uint64_t doorbell_init(const ModelGeometry& g, int64_t k, Doorbell& db) {
    if (owns_any(db)) {
        std::fprintf(stderr, "doorbell_init: refusing to replace an initialized doorbell\n");
        return 0;
    }

    size_t x_bytes = 0;
    size_t ids_bytes = 0;
    size_t weights_bytes = 0;
    if (!region_bytes(g.n_embd, x_bytes) || !region_bytes(k, ids_bytes) ||
        !region_bytes(k, weights_bytes)) {
        std::fprintf(stderr, "doorbell_init: invalid embedding width or token capacity\n");
        return 0;
    }
    size_t total = 0;
    if (!total_bytes(total, x_bytes) || !total_bytes(total, ids_bytes) ||
        !total_bytes(total, weights_bytes) || !total_bytes(total, 4) || !total_bytes(total, 4)) {
        std::fprintf(stderr, "doorbell_init: doorbell byte count overflow\n");
        return 0;
    }

    Doorbell replacement;
    replacement.n_embd = g.n_embd;
    replacement.k = k;
    // ONE region per field, each MAPPED PINNED, so the device and the host have different pointers to the same
    // bytes and no copy is needed to publish them.  The replacement owns every region until this function's
    // final assignment, so a late failure cannot expose or leak a partially built doorbell.
    if (!allocate_region(x_bytes, replacement.h_x_f, replacement.d_x_f, "x_f") ||
        !allocate_region(ids_bytes, replacement.h_ids, replacement.d_ids, "ids") ||
        !allocate_region(weights_bytes, replacement.h_weights, replacement.d_weights, "weights") ||
        !allocate_region(4, replacement.h_seq, replacement.d_seq, "seq") ||
        !allocate_region(4, replacement.h_flag, replacement.d_flag, "flag")) {
        doorbell_free(replacement);
        return 0;
    }
    db = replacement;
    return (uint64_t) total;
}

void doorbell_free(Doorbell& db) {
    if (db.h_x_f) (void) cudaFreeHost(db.h_x_f);
    if (db.h_ids) (void) cudaFreeHost(db.h_ids);
    if (db.h_weights) (void) cudaFreeHost(db.h_weights);
    if (db.h_seq) (void) cudaFreeHost(db.h_seq);
    if (db.h_flag) (void) cudaFreeHost(db.h_flag);
    db = Doorbell{};
}

void doorbell_reset(const Doorbell& db) {
    if (db.h_seq) *db.h_seq = 0;
    if (db.h_flag) *(volatile uint32_t*) db.h_flag = 0;
}

} // namespace strata::core
