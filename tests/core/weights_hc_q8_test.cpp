// STRATA_HC_REQ8 publishes a BF16 tensor only after all of them upload. A late read failure must not leave a
// WeightRef pointing into the allocation owned by WeightTable, and the temporary device allocation must be released.
#include "strata/core/weights.hpp"

#include <cuda_runtime.h>

#include <chrono>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <set>
#include <string>
#include <vector>

namespace fs = std::filesystem;
using strata::core::WeightTable;

namespace {
int g_fail = 0;
void check(bool ok, const char* what) {
    std::printf("  %-72s %s\n", what, ok ? "ok" : "FAIL");
    if (!ok) ++g_fail;
}

struct TempDir {
    fs::path path;
    TempDir() {
        path = fs::temp_directory_path() /
               ("strata-hc-q8-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        fs::create_directories(path);
    }
    ~TempDir() {
        std::error_code ignored;
        fs::remove_all(path, ignored);
    }
};

const char* kNames[] = {"blk.0.hc_attn_down.weight", "blk.0.hc_attn_up.weight"};

void write_pack(const fs::path& pack, size_t dense_bytes) {
    fs::create_directories(pack);
    std::ofstream idx(pack / "index.txt", std::ios::binary);
    idx << "# align 256 pool 1024 tensors 2\n";
    for (int i = 0; i < 2; ++i) {
        const unsigned off = (unsigned) (i * 512);
        idx << kNames[i] << " 0 4 " << off << " 512 " << off
            << " 512 256 1 0 0 0 0 0 0 0 0 0 0\n";
    }
    std::vector<unsigned char> dense(dense_bytes);
    for (size_t i = 0; i < dense.size(); ++i) dense[i] = (unsigned char) ((i * 37 + 11) & 0xff);
    std::ofstream(pack / "dense.bin", std::ios::binary).write((const char*) dense.data(), (std::streamsize) dense.size());
}

struct Loaded {
    WeightTable wt;
    void* arena = nullptr;
    std::set<std::string> skip{kNames[0], kNames[1]};
    ~Loaded() {
        if (arena) cudaFree(arena);
    }
    bool load(const fs::path& pack, std::string& err) {
        if (cudaMalloc(&arena, 256) != cudaSuccess) {
            err = "cudaMalloc";
            return false;
        }
        return wt.load(pack.string(), arena, 256, err, &skip);
    }
};
}  // namespace

int main() {
    int devices = 0;
    if (cudaGetDeviceCount(&devices) != cudaSuccess || devices == 0) {
        std::puts("weights_hc_q8_test: no CUDA device, skipped");
        return 77;
    }
    TempDir tmp;

    std::printf("1. successful upload publishes both tensors\n");
    {
        const fs::path pack = tmp.path / "ok";
        write_pack(pack, 1024);
        Loaded l;
        std::string err;
        check(l.load(pack, err), "the skipped pack loads");
        uint64_t bytes = 0;
        check(l.wt.load_hc_q8(pack.string(), bytes, err), "the HC tensors upload");
        check(bytes == 1024, "the upload owns the aligned total");
        for (const char* name : kNames) {
            const auto* w = l.wt.find(name);
            check(w && w->resident && w->data && w->q8_scale, "the tensor is published after upload");
        }
        check(!l.wt.load_hc_q8(pack.string(), bytes, err), "a second HC upload is refused");
    }

    std::printf("2. a late read failure publishes neither tensor\n");
    {
        const fs::path pack = tmp.path / "short";
        write_pack(pack, 512);
        Loaded l;
        std::string err;
        check(l.load(pack, err), "the skipped pack loads");
        uint64_t bytes = 123;
        check(!l.wt.load_hc_q8(pack.string(), bytes, err), "the truncated second tensor fails");
        check(bytes == 0, "a failed upload reports no bytes");
        for (const char* name : kNames) {
            const auto* w = l.wt.find(name);
            check(w && !w->resident && !w->data && !w->q8_scale, "the failed upload publishes nothing");
        }
    }

    std::printf(g_fail ? "weights_hc_q8_test: %d FAILED\n" : "weights_hc_q8_test: all passed\n", g_fail);
    return g_fail ? 1 : 0;
}
