// A failed dense-weight load must leave the table and its report unchanged.  Link wrapping supplies host-side
// CUDA memory and lets the test fail an allocation, an upload or the final device synchronization.
#include "strata/core/weights.hpp"

#include <cuda_runtime.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;
using strata::core::WeightTable;

namespace {

int host_allocs = 0;
int host_frees = 0;
int memcpys = 0;
int syncs = 0;
int fail_alloc_at = 0;
int fail_memcpy_at = 0;
int fail_sync_at = 0;

void check(bool condition, const char* message) {
    if (condition) return;
    std::fprintf(stderr, "weights_load_transaction_test: %s\n", message);
    std::exit(1);
}

struct TempDir {
    fs::path path;
    TempDir() {
        path = fs::temp_directory_path() /
               ("strata-weights-transaction-" +
                std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        fs::create_directories(path);
    }
    ~TempDir() {
        std::error_code ignored;
        fs::remove_all(path, ignored);
    }
};

void write_pack(const fs::path& pack, const char* name, const char* dense) {
    fs::create_directories(pack);
    std::ofstream index(pack / "index.txt", std::ios::binary);
    index << "# align 64 pool 64 tensors 1\n";
    index << name << " 0 4 0 64 0 64 16 1 0 0 0 0 0 0 0 0 0 0\n";
    std::vector<char> payload(64);
    for (size_t i = 0; i < payload.size(); ++i) payload[i] = dense[i % std::strlen(dense)];
    std::ofstream(pack / "dense.bin", std::ios::binary).write(payload.data(), payload.size());
}

void write_two_tensor_pack(const fs::path& pack, const char* first, const char* second, const char* dense) {
    fs::create_directories(pack);
    std::ofstream index(pack / "index.txt", std::ios::binary);
    index << "# align 64 pool 128 tensors 2\n";
    index << first << " 0 4 0 64 0 64 16 1 0 0 0 0 0 0 0 0 0 0\n";
    index << second << " 0 4 64 64 64 64 16 1 0 0 0 0 0 0 0 0 0 0\n";
    std::vector<char> payload(128);
    for (size_t i = 0; i < payload.size(); ++i) payload[i] = dense[i % std::strlen(dense)];
    std::ofstream(pack / "dense.bin", std::ios::binary).write(payload.data(), payload.size());
}

void reset_failures() {
    fail_alloc_at = fail_memcpy_at = fail_sync_at = 0;
}

} // namespace

extern "C" cudaError_t __wrap_cudaHostAlloc(void** pointer, size_t bytes, unsigned) {
    ++host_allocs;
    if (fail_alloc_at > 0 && host_allocs == fail_alloc_at) {
        *pointer = nullptr;
        return cudaErrorMemoryAllocation;
    }
    *pointer = std::malloc(bytes);
    return *pointer ? cudaSuccess : cudaErrorMemoryAllocation;
}

extern "C" cudaError_t __wrap_cudaFreeHost(void* pointer) {
    ++host_frees;
    std::free(pointer);
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaMemcpy(void* destination, const void* source, size_t bytes, cudaMemcpyKind) {
    ++memcpys;
    if (fail_memcpy_at > 0 && memcpys == fail_memcpy_at) return cudaErrorMemoryAllocation;
    std::memcpy(destination, source, bytes);
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaDeviceSynchronize() {
    ++syncs;
    return fail_sync_at > 0 && syncs == fail_sync_at ? cudaErrorMemoryAllocation : cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaGetDevice(int* device) {
    *device = 0;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaSetDevice(int) {
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaFree(void*) {
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaGetLastError() {
    return cudaSuccess;
}

int main() {
    TempDir temp;
    const fs::path first_pack = temp.path / "first";
    const fs::path second_pack = temp.path / "second";
    write_pack(first_pack, "blk.0.first.weight", "first tensor payload");
    write_two_tensor_pack(second_pack, "blk.0.partial.weight", "blk.0.second.weight",
                          "second tensor payload followed by third tensor payload");
    std::vector<unsigned char> first_arena(64), second_arena(128);
    WeightTable table;
    std::string err;

    host_allocs = host_frees = memcpys = syncs = 0;
    fail_memcpy_at = 2;
    check(!table.load(second_pack.string(), second_arena.data(), second_arena.size(), err),
          "a failed upload was reported as success");
    check(host_allocs == 2 && host_frees == 2, "failed-load staging was not released");
    check(table.find("blk.0.partial.weight") == nullptr, "a failed load published its first tensor");
    check(table.find("blk.0.second.weight") == nullptr, "a failed load published its tensor");
    check(table.report().tensors == 0, "a failed load changed the report");

    reset_failures();
    host_allocs = host_frees = memcpys = syncs = 0;
    fail_alloc_at = 2;
    check(!table.load(second_pack.string(), second_arena.data(), second_arena.size(), err),
          "a failed staging allocation was reported as success");
    check(host_allocs == 2 && host_frees == 1, "the first staging allocation leaked");
    check(table.find("blk.0.partial.weight") == nullptr, "an allocation failure changed the table");

    reset_failures();
    host_allocs = host_frees = memcpys = syncs = 0;
    fail_sync_at = 1;
    check(!table.load(second_pack.string(), second_arena.data(), second_arena.size(), err),
          "a failed final synchronization was reported as success");
    check(host_allocs == 2 && host_frees == 2, "final-sync failure staging was not released");
    check(table.find("blk.0.partial.weight") == nullptr, "a final-sync failure published its first tensor");
    check(table.find("blk.0.second.weight") == nullptr, "a final-sync failure published its tensor");
    check(table.report().tensors == 0, "a final-sync failure changed the report");

    reset_failures();
    check(table.load(first_pack.string(), first_arena.data(), first_arena.size(), err),
          "the table did not load after failed attempts");
    const auto* first = table.find("blk.0.first.weight");
    check(first != nullptr && first->data == first_arena.data(),
          "the first tensor was not published at its arena offset");
    check(table.report().tensors == 1, "the successful load report is incomplete");

    host_allocs = host_frees = memcpys = syncs = 0;
    check(!table.load(second_pack.string(), second_arena.data(), second_arena.size(), err),
          "a second load was accepted");
    check(host_allocs == 0, "an already loaded table began staging");
    check(table.find("blk.0.first.weight") == first, "a refused reload changed the published tensor");
    check(table.report().tensors == 1, "a refused reload changed the report");

    std::printf("weights_load_transaction_test: failure leaves the previous table intact\n");
    return 0;
}
