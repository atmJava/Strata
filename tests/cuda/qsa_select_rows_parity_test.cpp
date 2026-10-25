// tests/cuda/qsa_select_rows_parity_test.cpp - the QSA block-score selection's two paths, bit for bit, on one input.
//
//   default dispatch            qsa_block_scores (no active-block count, <= 8 queries): on Turing
//                               block_scores_halve_kernel (STRATA_SM75_PERF unset), elsewhere block_scores_multi_kernel
//   STRATA_QSA_ROWS=1           block_scores_rows_kernel (QSAROWS, docs/QSAROWS.md), calls of up to 4 queries
//
// Both must produce the same scores bytes and the same top-k ids bytes (the ids the decoder actually consumes), and
// every score must be finite.  The same synthetic input is generated on the device from a fixed splitmix64 seed:
// pooled indexer keys, the spare `dead` key, the indexer queries and the per-query step records, for T = 1..4 at
// three (context, capacity) pairs - 4096/65536, 32768/262144 (capacity twice the context, as verify.cpp reserves it)
// and 2047/8192 (n_kv <= idx_top_k: the identity selection, plus a partial tail block that takes the +1e9 rule).
//
// WHY TWO PROCESSES.  qsa_select.cu reads STRATA_QSA_ROWS, STRATA_SCORES_HALVE and STRATA_SCORES_MULTI once into
// function-local statics (`static const bool rows_env = [] ...`), so one process can never run both dispatch arms:
// whichever call comes first freezes the choice.  The driver therefore re-executes itself twice, once with the
// variable unset and once with STRATA_QSA_ROWS=1, each child dumps its scores/ids to a file, and the driver compares
// the files.  The driver itself never calls the selector, so a variable in the caller's environment cannot bias it.
//
// A host-only shape defense needs no GPU and runs first: qsa_decode_attn_scratch_floats' n_chunks arithmetic at the
// caps 2051 / 131072 / 262144 (+ chunk boundaries), and the rows entry point's documented refusal (nq > 4, another
// indexer geometry) which is what makes the dispatcher's fallback safe.
//
//   qsa_select_rows_parity_test [--device N] [--keep]
//   exit 0 pass, 1 a mismatch / validation failure, 2 setup or CUDA error, 77 no CUDA device (the usual skip code;
//   the host-only shape part has already run and printed by then).
//
// Budgets: each child allocates ~33 MiB of device buffers (the 262144-cell capacity's pooled keys dominate), well
// under 800 MiB; the two children run one after the other, so their peaks do not add.  A full run is a few seconds,
// checked against 60 s by the driver.
#include "strata/kernels/qsa.hpp"
#include "strata/kernels/qsa_decode_attn.hpp"
#include "strata/kernels/qsa_select.hpp"

#include <cuda_runtime.h>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#if defined(__linux__) || defined(__APPLE__)
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace k = strata::kernels;

namespace {

constexpr int kMaxT = 4;                       // qsa_block_scores_rows' query limit
constexpr int64_t kIdxDim = 128;               // qsa_select.cu's IDX_DIM
constexpr int64_t kIdxHeads = 4;               //                      IDX_HEADS
constexpr int64_t kBlock = 4;                  //                      R
constexpr int64_t kMaxCapCells = 262144;       // the largest capacity any case reserves
constexpr int64_t kMaxBlocks = kMaxCapCells / kBlock + 2;   // verify.cpp's max_blocks_ formula
constexpr int64_t kIdxTopK = 2048;             // QsaShapes::idx_top_k, qsa_real_shapes()
constexpr int64_t kTopkMaxCells = 32768;       // qsa.hpp::kTopkMaxCells: the top-k kernel's largest context
constexpr uint64_t kMemLimitMiB = 800;         // the single-process budget this test is written to
constexpr double kWallLimitS = 60.0;
constexpr const char* kDumpMagic = "QSAPAR1";
static_assert(k::kTopkMaxCells == kTopkMaxCells, "qsa.hpp's top-k context bound moved; update this test");
static_assert(k::kStepCount == 4, "the step record is pos, n_kv, n_bid, width");

// ---------------------------------------------------------------------------------------------------------------
// host-only shape defense

bool shape_defense() {
    std::puts("-- shape defense (host only, no kernel)");
    bool ok = true;
    const k::QsaShapes s = k::qsa_real_shapes();
    const int64_t want_cap = s.idx_top_k + s.idx_block - 1;
    if (s.n_head != 24 || s.n_head_kv != 2 || s.head_dim != 256 || s.n_rot != 64 || s.idx_n_head != kIdxHeads ||
        s.idx_dim != kIdxDim || s.idx_block != kBlock || s.idx_top_k != kIdxTopK || s.page_size != 4) {
        std::fprintf(stderr, "FAIL shape: qsa_real_shapes() is not the artifact geometry this test is written for "
                             "(n_head %lld, n_head_kv %lld, head_dim %lld, idx_n_head %lld, idx_dim %lld, idx_block "
                             "%lld, idx_top_k %lld, page_size %lld)\n",
                     (long long) s.n_head, (long long) s.n_head_kv, (long long) s.head_dim, (long long) s.idx_n_head,
                     (long long) s.idx_dim, (long long) s.idx_block, (long long) s.idx_top_k, (long long) s.page_size);
        ok = false;
    }
    // The contexts the task names all select at most idx_top_k + idx_block - 1 cells, so their attention scratch is the
    // same as the 2051-cap scratch; this is the link between a context and the cap the scratch function takes.
    for (const int64_t ctx : {int64_t{2051}, int64_t{131072}, int64_t{262144}}) {
        const int64_t w = k::qsa_selection_width(ctx, s);
        if (w != want_cap) {
            std::fprintf(stderr, "FAIL shape: qsa_selection_width(%lld) = %lld, want %lld\n", (long long) ctx,
                         (long long) w, (long long) want_cap);
            ok = false;
        }
    }
    // qsa_decode_attn_scratch_floats: chunks = ceil(cap / 64), layout [acc: chunks*n_head*HD][m][l] + 64 floats of
    // slack, n_head = 24, HD = 256.  The function is pure host arithmetic (no kernel, no device).
    struct CapCase { int64_t cap; const char* why; };
    const CapCase caps[] = {
        {2051, "the 2051 selection cap (4K..262K contexts)"},
        {131072, "a 131072-cell capacity used as the cap"},
        {262144, "a 262144-cell capacity used as the cap"},
        {32768, "the drafter's dense 32768-cell window"},
        {64, "exactly one chunk"},
        {65, "one chunk plus one cell"},
        {1, "one cell"},
    };
    for (const CapCase& c : caps) {
        const int64_t chunks = (c.cap + 63) / 64;
        const uint64_t part_m = (uint64_t) chunks * (uint64_t) s.n_head * (uint64_t) s.head_dim;
        const uint64_t part_l = part_m + (uint64_t) chunks * (uint64_t) s.n_head;
        const uint64_t want = part_l + (uint64_t) chunks * (uint64_t) s.n_head + 64;
        const uint64_t got = k::qsa_decode_attn_scratch_floats(c.cap, s);
        if (got != want || got != (uint64_t) chunks * (uint64_t) s.n_head * (uint64_t) (s.head_dim + 2) + 64) {
            std::fprintf(stderr, "FAIL shape: scratch_floats(cap=%lld) = %llu, want %llu (chunks %lld, part_m %llu, "
                                 "part_l %llu)\n",
                         (long long) c.cap, (unsigned long long) got, (unsigned long long) want, (long long) chunks,
                         (unsigned long long) part_m, (unsigned long long) part_l);
            ok = false;
        } else {
            std::printf("   cap %-7lld -> %6lld chunks, %8llu floats (%6.3f MiB)  %s\n", (long long) c.cap,
                        (long long) chunks, (unsigned long long) got, (double) got * 4.0 / (1024.0 * 1024.0), c.why);
        }
    }
    // What makes the env dispatch arm safe: the rows entry point refuses geometries it cannot do, without launching,
    // and the caller then falls through to the halve/multi kernel.  These calls never reach CUDA.
    if (!k::qsa_block_scores_rows(nullptr, nullptr, nullptr, nullptr, 0, 1024, s, nullptr, nullptr)) {
        std::fprintf(stderr, "FAIL shape: qsa_block_scores_rows(nq=0) should succeed as a no-op\n");
        ok = false;
    }
    if (k::qsa_block_scores_rows(nullptr, nullptr, nullptr, nullptr, kMaxT + 1, 1024, s, nullptr, nullptr)) {
        std::fprintf(stderr, "FAIL shape: qsa_block_scores_rows accepted nq=%d (its limit is %d)\n", kMaxT + 1, kMaxT);
        ok = false;
    }
    k::QsaShapes bad = s;
    bad.idx_dim = 64;
    if (k::qsa_block_scores_rows(nullptr, nullptr, nullptr, nullptr, 1, 1024, bad, nullptr, nullptr)) {
        std::fprintf(stderr, "FAIL shape: qsa_block_scores_rows accepted idx_dim=64\n");
        ok = false;
    }
    bad = s;
    bad.idx_block = 8;
    if (k::qsa_block_scores_rows(nullptr, nullptr, nullptr, nullptr, 1, 1024, bad, nullptr, nullptr)) {
        std::fprintf(stderr, "FAIL shape: qsa_block_scores_rows accepted idx_block=8\n");
        ok = false;
    }
    std::printf("   rows refusal: nq>%d, idx_dim 64, idx_block 8 all refused without a launch %s\n", kMaxT,
                ok ? "" : "(FAIL)");
    return ok;
}

// ---------------------------------------------------------------------------------------------------------------
// device input generation (deterministic, no curand)

__host__ __device__ __forceinline__ unsigned long long splitmix(unsigned long long x) {
    x += 0x9E3779B97F4A7C15ull;
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ull;
    x = (x ^ (x >> 27)) * 0x94D049BB133111EBull;
    return x ^ (x >> 31);
}

__global__ void fill_uniform_kernel(float* p, unsigned long long n, unsigned long long seed, float scale) {
    const unsigned long long stride = (unsigned long long) gridDim.x * blockDim.x;
    for (unsigned long long i = (unsigned long long) blockIdx.x * blockDim.x + threadIdx.x; i < n; i += stride) {
        const unsigned long long h = splitmix(seed ^ (i * 0xD6E8FEB86659FD93ull));
        // 24 bits -> [0, 1) -> [-1, 1), rounded once in float: the same bytes in both children
        const float u = (float) ((h >> 40) & 0xFFFFFFull) * (1.0f / 16777216.0f);
        p[i] = (u * 2.0f - 1.0f) * scale;
    }
}

// qsa_step_fill's arithmetic, on the device: pos = ctx + t, n_kv = pos + 1, n_bid = n_kv / idx_block,
// width = qsa_selection_width(n_kv, s).
__global__ void make_steps_kernel(int32_t* steps, int nq, long long ctx, int idx_block, int idx_top_k) {
    if (blockIdx.x != 0 || threadIdx.x != 0) return;
    for (int t = 0; t < nq; ++t) {
        const long long pos = ctx + t, n_kv = pos + 1;
        const long long width = n_kv < (long long) idx_top_k + idx_block - 1 ? n_kv : (long long) idx_top_k + idx_block - 1;
        int32_t* st = steps + (size_t) t * k::kStepCount;
        st[k::kStepPos] = (int32_t) pos;
        st[k::kStepNKv] = (int32_t) n_kv;
        st[k::kStepNBid] = (int32_t) (n_kv / idx_block);
        st[k::kStepWidth] = (int32_t) width;
    }
}

// A 64-bit digest of a byte range, independent of thread order: XOR of splitmix(seed, index, value) over every byte.
__global__ void digest_kernel(const unsigned char* p, unsigned long long n, unsigned long long seed,
                              unsigned long long* out) {
    const unsigned long long stride = (unsigned long long) gridDim.x * blockDim.x;
    unsigned long long h = 0;
    for (unsigned long long i = (unsigned long long) blockIdx.x * blockDim.x + threadIdx.x; i < n; i += stride)
        h ^= splitmix(seed ^ (i * 0x9E3779B97F4A7C15ull) ^ (unsigned long long) p[i]);
    if (h != 0) atomicXor(out, h);
}

void ck(cudaError_t e, const char* what) {
    if (e != cudaSuccess) {
        std::fprintf(stderr, "qsa_select_rows_parity: %s: %s\n", what, cudaGetErrorString(e));
        std::exit(2);
    }
}

// ---------------------------------------------------------------------------------------------------------------
// dump format

struct DumpHeader {
    char magic[8];
    uint32_t version;
    uint32_t rows_path;
    int32_t device;
    int32_t cc_major;
    int32_t cc_minor;
    int32_t n_cases;
    char gpu[256];
    char dispatch[160];
    double wall_s;
    uint64_t peak_alloc_bytes;
    uint64_t peak_delta_bytes;
};

struct DumpCase {
    int64_t ctx;
    int64_t cap_ctx;
    int64_t max_blocks;
    int64_t cap_width;
    int64_t scores_floats;
    int64_t ids_ints;
    double ms;
    uint64_t digest_pooled;
    uint64_t digest_dead;
    uint64_t digest_q;
    uint64_t digest_steps;
};

struct Dump {
    DumpHeader h{};
    std::vector<DumpCase> cases;
    std::vector<std::vector<float>> scores;
    std::vector<std::vector<int32_t>> ids;
};

constexpr uint32_t kDumpVersion = 1;

bool write_bytes(std::FILE* f, const void* p, size_t n) {
    if (n == 0) return true;
    return std::fwrite(p, 1, n, f) == n;
}

bool read_bytes(std::FILE* f, void* p, size_t n) {
    if (n == 0) return true;
    return std::fread(p, 1, n, f) == n;
}

// ---------------------------------------------------------------------------------------------------------------
// the child: generate one path's outputs and dump them

struct Geo {
    const char* name;
    int64_t ctx;
    int64_t cap_ctx;   // the reserved capacity, deliberately not equal to ctx
};

const Geo kGeos[] = {
    {"ctx 4096 / capacity 65536", 4096, 65536},
    {"ctx 32768 / capacity 262144", 32768, 262144},
    {"ctx 2047 / capacity 8192", 2047, 8192},
};

int64_t max_blocks_for(int64_t cap_ctx) { return cap_ctx / kBlock + 2; }

int64_t widths_for(int64_t ctx, int nq, const k::QsaShapes& s);

const char* env_state(const char* name, char* buf, size_t n) {
    const char* v = std::getenv(name);
    if (v == nullptr) {
        std::snprintf(buf, n, "%s unset", name);
    } else {
        std::snprintf(buf, n, "%s=%s", name, v);
    }
    return buf;
}

int run_child(bool rows_path, const std::string& out_path, int device) {
#if !defined(__linux__) && !defined(__APPLE__)
    (void) rows_path; (void) out_path; (void) device;
    std::puts("SKIP: this test's two-process dispatch check uses fork/exec (POSIX only)");
    return 77;
#else
    // Before any qsa_select call: the dispatch and the two Turing switches are read once per process.
    //
    // STRATA_SCORES_MULTI=0 would take the rows branch out of play entirely (it sits inside `scores_multi_on()`), and
    // then the "rows" child would quietly run the plain per-query kernel: the comparison would pass without ever
    // comparing the kernel under test.  Clear it; the multi scorer's default is on.
    unsetenv("STRATA_SCORES_MULTI");
    // STRATA_SCORES_HALVE=0 would make the "default" arm the multi kernel even on Turing.  A regression test of the
    // default must not be redirected by the A/B switch it is checking around: clear it, so the card's own default
    // (halve on Turing, multi elsewhere) runs.
    unsetenv("STRATA_SCORES_HALVE");
    if (rows_path) {
        setenv("STRATA_QSA_ROWS", "1", 1);
    } else {
        unsetenv("STRATA_QSA_ROWS");
    }
    int devices = 0;
    const cudaError_t count_err = cudaGetDeviceCount(&devices);
    if (count_err != cudaSuccess || devices == 0) {
        cudaGetLastError();
        std::printf("SKIP: no CUDA device (%s)\n", cudaGetErrorString(count_err));
        return 77;
    }
    if (device < 0 || device >= devices) {
        std::fprintf(stderr, "qsa_select_rows_parity child: --device %d out of range (%d devices)\n", device, devices);
        return 2;
    }
    ck(cudaSetDevice(device), "cudaSetDevice");
    cudaDeviceProp prop{};
    ck(cudaGetDeviceProperties(&prop, device), "cudaGetDeviceProperties");
    ck(cudaFree(0), "cudaFree(0) (create the context)");
    size_t free_before = 0, total = 0;
    ck(cudaMemGetInfo(&free_before, &total), "cudaMemGetInfo (before)");

    const k::QsaShapes s = k::qsa_real_shapes();
    const int64_t cap_width = k::qsa_selection_width(kTopkMaxCells, s);   // 2051 for the artifact geometry
    char dispatch[160];
    if (rows_path) {
        std::snprintf(dispatch, sizeof(dispatch), "STRATA_QSA_ROWS=1 -> block_scores_rows_kernel (QSAROWS)");
    } else {
        const char* perf = std::getenv("STRATA_SM75_PERF");
        const char* halve = std::getenv("STRATA_SCORES_HALVE");
        const bool turing = prop.major == 7 && prop.minor == 5;
        const bool halve_on = halve != nullptr ? std::atoi(halve) != 0
                                              : (turing && !(perf != nullptr && std::atoi(perf) == 0));
        std::snprintf(dispatch, sizeof(dispatch), "default dispatch -> %s",
                      halve_on ? "block_scores_halve_kernel (Turing)" : "block_scores_multi_kernel / block_scores_kernel");
    }
    char e1[80], e2[80], e3[80];
    std::printf("[child:%s] device %d (%s, %d.%d), %s; %s, %s, %s (STRATA_SCORES_MULTI and "
                "STRATA_SCORES_HALVE cleared)\n", rows_path ? "rows" : "default", device, prop.name, prop.major,
                prop.minor, dispatch, env_state("STRATA_QSA_ROWS", e1, sizeof(e1)),
                env_state("STRATA_SCORES_HALVE", e2, sizeof(e2)), env_state("STRATA_SM75_PERF", e3, sizeof(e3)));
    std::fflush(stdout);

    // One allocation of the largest case's buffers, reused for every case.  ~32 MiB of pooled keys dominates.
    const size_t pooled_n = (size_t) kMaxBlocks * (size_t) kIdxDim;
    const size_t q_n = (size_t) kMaxT * kIdxHeads * kIdxDim;
    const size_t scores_n = (size_t) kMaxT * (size_t) kMaxBlocks;
    const size_t ids_n = (size_t) kMaxT * (size_t) cap_width;
    float* d_pooled = nullptr;
    float* d_dead = nullptr;
    float* d_q = nullptr;
    int32_t* d_steps = nullptr;
    float* d_scores = nullptr;
    int32_t* d_ids = nullptr;
    unsigned long long* d_dig = nullptr;
    ck(cudaMalloc((void**) &d_pooled, pooled_n * sizeof(float)), "cudaMalloc pooled");
    ck(cudaMalloc((void**) &d_dead, (size_t) kIdxDim * sizeof(float)), "cudaMalloc dead");
    ck(cudaMalloc((void**) &d_q, q_n * sizeof(float)), "cudaMalloc q_idx");
    ck(cudaMalloc((void**) &d_steps, (size_t) kMaxT * k::kStepCount * sizeof(int32_t)), "cudaMalloc steps");
    ck(cudaMalloc((void**) &d_scores, scores_n * sizeof(float)), "cudaMalloc scores");
    ck(cudaMalloc((void**) &d_ids, ids_n * sizeof(int32_t)), "cudaMalloc ids");
    ck(cudaMalloc((void**) &d_dig, 4 * sizeof(unsigned long long)), "cudaMalloc digest");
    const uint64_t peak_alloc = (uint64_t) pooled_n * sizeof(float) + (uint64_t) kIdxDim * sizeof(float) +
                                (uint64_t) q_n * sizeof(float) + (uint64_t) kMaxT * k::kStepCount * sizeof(int32_t) +
                                (uint64_t) scores_n * sizeof(float) + (uint64_t) ids_n * sizeof(int32_t) +
                                4 * sizeof(unsigned long long);

    std::FILE* f = std::fopen(out_path.c_str(), "wb");
    if (f == nullptr) {
        std::fprintf(stderr, "qsa_select_rows_parity child: cannot write %s: %s\n", out_path.c_str(),
                     std::strerror(errno));
        return 2;
    }
    DumpHeader h{};
    std::memcpy(h.magic, kDumpMagic, 8);
    h.version = kDumpVersion;
    h.rows_path = rows_path ? 1 : 0;
    h.device = device;
    h.cc_major = prop.major;
    h.cc_minor = prop.minor;
    h.n_cases = (int32_t) ((int64_t) (sizeof(kGeos) / sizeof(kGeos[0])) * kMaxT);
    std::snprintf(h.gpu, sizeof(h.gpu), "%s", prop.name);
    std::snprintf(h.dispatch, sizeof(h.dispatch), "%s", dispatch);
    if (!write_bytes(f, &h, sizeof(h))) {
        std::fprintf(stderr, "qsa_select_rows_parity child: write header failed\n");
        return 2;
    }

    const auto wall0 = std::chrono::steady_clock::now();
    uint64_t peak_delta = 0;
    int64_t case_index = 0;
    for (const Geo& g : kGeos) {
        const int64_t mb = max_blocks_for(g.cap_ctx);
        if (mb > kMaxBlocks || g.ctx + kMaxT > g.cap_ctx) {
            std::fprintf(stderr, "qsa_select_rows_parity child: bad case geometry\n");
            return 2;
        }
        for (int nq = 1; nq <= kMaxT; ++nq, ++case_index) {
            const uint64_t seed = splitmix(0x515341ULL ^ (uint64_t) case_index * 0x9E3779B97F4A7C15ull ^
                                           (uint64_t) g.ctx ^ ((uint64_t) g.cap_ctx << 20));
            const uint64_t seed_pool = seed ^ 0x1111111111111111ull;
            const uint64_t seed_dead = seed ^ 0x2222222222222222ull;
            const uint64_t seed_q = seed ^ 0x3333333333333333ull;
            const uint64_t seed_steps = seed ^ 0x4444444444444444ull;
            // pooled keys and the spare are ~N(0, 0.09)-sized in qsa_rows_bench; the queries are ~N(0, 1).  Uniform
            // values keep both signs of the dot product, so the per-head relu and the +1e9 tail rule are exercised.
            fill_uniform_kernel<<<256, 256>>>(d_pooled, (unsigned long long) (mb * kIdxDim), seed_pool, 0.09f);
            fill_uniform_kernel<<<1, 128>>>(d_dead, (unsigned long long) kIdxDim, seed_dead, 0.09f);
            fill_uniform_kernel<<<1, 256>>>(d_q, (unsigned long long) (nq * kIdxHeads * kIdxDim), seed_q, 1.0f);
            make_steps_kernel<<<1, 1>>>(d_steps, nq, (long long) g.ctx, (int) s.idx_block, (int) s.idx_top_k);
            unsigned long long dig[4] = {0, 0, 0, 0};
            const struct {
                const void* p;
                size_t n;
                uint64_t seed;
            } parts[4] = {
                {d_pooled, (size_t) mb * (size_t) kIdxDim * sizeof(float), seed_pool},
                {d_dead, (size_t) kIdxDim * sizeof(float), seed_dead},
                {d_q, (size_t) nq * (size_t) kIdxHeads * (size_t) kIdxDim * sizeof(float), seed_q},
                {d_steps, (size_t) nq * k::kStepCount * sizeof(int32_t), seed_steps},
            };
            for (int pi = 0; pi < 4; ++pi) {
                ck(cudaMemset(d_dig, 0, 4 * sizeof(unsigned long long)), "cudaMemset digest");
                digest_kernel<<<128, 256>>>((const unsigned char*) parts[pi].p, (unsigned long long) parts[pi].n,
                                            parts[pi].seed, d_dig);
                ck(cudaMemcpy(&dig[pi], d_dig, sizeof(unsigned long long), cudaMemcpyDeviceToHost), "copy digest");
            }
            // Sentinel fill: bytes no block writes must stay identical on both paths (and a write past a query's
            // n_bid shows up as a difference instead of being hidden by stale bytes).
            const size_t scores_bytes = (size_t) nq * (size_t) mb * sizeof(float);
            const size_t ids_bytes = (size_t) nq * (size_t) cap_width * sizeof(int32_t);
            ck(cudaMemset(d_scores, 0xFF, scores_bytes), "cudaMemset scores");
            ck(cudaMemset(d_ids, 0x7F, ids_bytes), "cudaMemset ids");
            cudaEvent_t ev0 = nullptr, ev1 = nullptr;
            ck(cudaEventCreate(&ev0), "event create");
            ck(cudaEventCreate(&ev1), "event create");
            ck(cudaEventRecord(ev0), "event record");
            k::qsa_block_scores(d_pooled, d_dead, d_q, d_steps, nq, mb, s, d_scores, nullptr);
            k::qsa_block_topk(d_scores, d_steps, nq, mb, cap_width, s, d_ids, nullptr);
            ck(cudaEventRecord(ev1), "event record");
            ck(cudaEventSynchronize(ev1), "event sync");
            float ms = 0.0f;
            ck(cudaEventElapsedTime(&ms, ev0, ev1), "event elapsed");
            ck(cudaEventDestroy(ev0), "event destroy");
            ck(cudaEventDestroy(ev1), "event destroy");
            size_t free_now = 0;
            if (cudaMemGetInfo(&free_now, &total) == cudaSuccess && free_now <= free_before)
                peak_delta = std::max<uint64_t>(peak_delta, (uint64_t) (free_before - free_now));

            DumpCase c{};
            c.ctx = g.ctx;
            c.cap_ctx = g.cap_ctx;
            c.max_blocks = mb;
            c.cap_width = cap_width;
            c.scores_floats = (int64_t) nq * mb;
            c.ids_ints = (int64_t) nq * cap_width;
            c.ms = (double) ms;
            c.digest_pooled = (uint64_t) dig[0];
            c.digest_dead = (uint64_t) dig[1];
            c.digest_q = (uint64_t) dig[2];
            c.digest_steps = (uint64_t) dig[3];
            std::vector<float> hs(scores_bytes / sizeof(float));
            std::vector<int32_t> hi(ids_bytes / sizeof(int32_t));
            ck(cudaMemcpy(hs.data(), d_scores, scores_bytes, cudaMemcpyDeviceToHost), "copy scores");
            ck(cudaMemcpy(hi.data(), d_ids, ids_bytes, cudaMemcpyDeviceToHost), "copy ids");
            if (!write_bytes(f, &c, sizeof(c)) || !write_bytes(f, hs.data(), scores_bytes) ||
                !write_bytes(f, hi.data(), ids_bytes)) {
                std::fprintf(stderr, "qsa_select_rows_parity child: write case %lld failed: %s\n", (long long) case_index,
                             std::strerror(errno));
                return 2;
            }
            std::printf("[child:%s] %-28s T=%d max_blocks=%lld width=%lld  scores %lld ids %lld  %.3f ms\n",
                        rows_path ? "rows" : "default", g.name, nq, (long long) mb,
                        (long long) widths_for(g.ctx, nq, s), (long long) c.scores_floats, (long long) c.ids_ints,
                        c.ms);
            std::fflush(stdout);
        }
    }
    const auto wall1 = std::chrono::steady_clock::now();
    h.wall_s = std::chrono::duration<double>(wall1 - wall0).count();
    h.peak_alloc_bytes = peak_alloc;
    h.peak_delta_bytes = peak_delta;
    if (std::fseek(f, 0, SEEK_SET) != 0 || !write_bytes(f, &h, sizeof(h)) || std::fclose(f) != 0) {
        std::fprintf(stderr, "qsa_select_rows_parity child: final header write failed\n");
        return 2;
    }
    std::printf("[child:%s] device buffers %.2f MiB, cudaMemGetInfo delta %.2f MiB, %.3f s\n",
                rows_path ? "rows" : "default", (double) peak_alloc / (1024.0 * 1024.0),
                (double) peak_delta / (1024.0 * 1024.0), h.wall_s);
    (void) cudaFree(d_pooled); (void) cudaFree(d_dead); (void) cudaFree(d_q); (void) cudaFree(d_steps);
    (void) cudaFree(d_scores); (void) cudaFree(d_ids); (void) cudaFree(d_dig);
    return 0;
#endif
}

int64_t widths_for(int64_t ctx, int nq, const k::QsaShapes& s) {
    const int64_t pos = ctx + nq - 1;   // the last of the case's T queries has the largest n_kv
    return k::qsa_selection_width(pos + 1, s);
}

// ---------------------------------------------------------------------------------------------------------------
// the driver: read both dumps and compare

bool read_dump(const std::string& path, Dump& d, std::string& err) {
    std::FILE* f = std::fopen(path.c_str(), "rb");
    if (f == nullptr) {
        err = "cannot open " + path + ": " + std::strerror(errno);
        return false;
    }
    if (!read_bytes(f, &d.h, sizeof(d.h)) || std::memcmp(d.h.magic, kDumpMagic, 8) != 0 ||
        d.h.version != kDumpVersion || d.h.n_cases < 0 || d.h.n_cases > 1024) {
        err = "bad header in " + path;
        std::fclose(f);
        return false;
    }
    d.cases.resize((size_t) d.h.n_cases);
    d.scores.resize((size_t) d.h.n_cases);
    d.ids.resize((size_t) d.h.n_cases);
    for (int32_t i = 0; i < d.h.n_cases; ++i) {
        if (!read_bytes(f, &d.cases[(size_t) i], sizeof(DumpCase))) {
            err = "short case header in " + path;
            std::fclose(f);
            return false;
        }
        const DumpCase& c = d.cases[(size_t) i];
        if (c.scores_floats <= 0 || c.ids_ints <= 0 || c.scores_floats > (int64_t) 1 << 28 ||
            c.ids_ints > (int64_t) 1 << 24) {
            err = "implausible case sizes in " + path;
            std::fclose(f);
            return false;
        }
        d.scores[(size_t) i].resize((size_t) c.scores_floats);
        d.ids[(size_t) i].resize((size_t) c.ids_ints);
        if (!read_bytes(f, d.scores[(size_t) i].data(), (size_t) c.scores_floats * sizeof(float)) ||
            !read_bytes(f, d.ids[(size_t) i].data(), (size_t) c.ids_ints * sizeof(int32_t))) {
            err = "short case data in " + path;
            std::fclose(f);
            return false;
        }
    }
    std::fclose(f);
    return true;
}

void print_score_diffs(const std::vector<float>& a, const std::vector<float>& b, int64_t max_blocks, size_t max_show) {
    size_t shown = 0, total = 0, first = a.size();
    for (size_t i = 0; i < a.size(); ++i) {
        uint32_t ba = 0, bb = 0;
        std::memcpy(&ba, &a[i], 4);
        std::memcpy(&bb, &b[i], 4);
        if (ba != bb) {
            if (first == a.size()) first = i;
            if (shown < max_show) {
                std::printf("     scores[%zu] (query %zu, block %zu): default %.9g 0x%08x | rows %.9g 0x%08x\n", i,
                            i / (size_t) max_blocks, i % (size_t) max_blocks, (double) a[i], ba, (double) b[i], bb);
                ++shown;
            }
            ++total;
        }
    }
    std::printf("     %zu of %zu score floats differ; first at index %zu (query %zu, block %zu)\n", total, a.size(),
                first, first / (size_t) max_blocks, first % (size_t) max_blocks);
}

void print_id_diffs(const std::vector<int32_t>& a, const std::vector<int32_t>& b, int64_t cap_width, size_t max_show) {
    size_t shown = 0, total = 0, first = a.size();
    for (size_t i = 0; i < a.size(); ++i) {
        if (a[i] != b[i]) {
            if (first == a.size()) first = i;
            if (shown < max_show) {
                std::printf("     ids[%zu] (query %zu, rank %zu): default %d | rows %d\n", i, i / (size_t) cap_width,
                            i % (size_t) cap_width, a[i], b[i]);
                ++shown;
            }
            ++total;
        }
    }
    std::printf("     %zu of %zu id ints differ; first at index %zu (query %zu, rank %zu)\n", total, a.size(), first,
                first / (size_t) cap_width, first % (size_t) cap_width);
}

// Blocks 0..n_bid are the blocks a query writes (n_bid = n_kv / 4; the last one may be empty but still scores the
// `dead` key).  Bytes past that are the 0xFF sentinel: identical by construction and part of the memcmp, but not
// scores, so the finiteness check skips them.
int64_t written_score_floats(const DumpCase& c) {
    const int64_t nq = c.scores_floats / c.max_blocks;
    int64_t n = 0;
    for (int64_t qi = 0; qi < nq; ++qi) n += (c.ctx + qi + 1) / kBlock + 1;
    return n;
}

bool check_scores_finite(const std::vector<float>& s, const DumpCase& c, size_t& bad, size_t& first, float& first_v) {
    const int64_t nq = c.scores_floats / c.max_blocks;
    bad = 0;
    first = s.size();
    for (int64_t qi = 0; qi < nq; ++qi) {
        const int64_t n_bid = (c.ctx + qi + 1) / kBlock;
        for (int64_t b = 0; b <= n_bid; ++b) {
            const size_t i = (size_t) (qi * c.max_blocks + b);
            if (!std::isfinite(s[i])) {
                if (first == s.size()) {
                    first = i;
                    first_v = s[i];
                }
                ++bad;
            }
        }
    }
    return bad == 0;
}

// The selection contract: ids ascending, unique, inside [0, n_kv), `width` of them.
bool check_ids(const Dump& d, size_t ci, std::string& why) {
    const DumpCase& c = d.cases[ci];
    const int64_t nq = c.scores_floats / c.max_blocks;
    const int64_t cap = c.cap_width;
    if (c.ids_ints != nq * cap) {
        why = "ids count does not match nq*cap";
        return false;
    }
    for (int64_t qi = 0; qi < nq; ++qi) {
        const int64_t n_kv = c.ctx + qi + 1;
        const int64_t width = std::min<int64_t>(n_kv, kIdxTopK + kBlock - 1);
        const int32_t* ids = d.ids[ci].data() + qi * cap;
        for (int64_t j = 0; j < width; ++j) {
            if (ids[j] < 0 || ids[j] >= n_kv) {
                why = "id " + std::to_string(ids[j]) + " at query " + std::to_string(qi) + " rank " +
                      std::to_string(j) + " is outside [0, " + std::to_string(n_kv) + ")";
                return false;
            }
            if (j > 0 && ids[j] <= ids[j - 1]) {
                why = "ids not strictly ascending at query " + std::to_string(qi) + " rank " + std::to_string(j);
                return false;
            }
        }
    }
    return true;
}

int run_driver(int device, bool keep, const char* argv0) {
    std::puts("qsa_select_rows_parity: default dispatch against STRATA_QSA_ROWS=1, scores and top-k ids bit for bit");
    bool ok = shape_defense();

    int devices = 0;
    const cudaError_t count_err = cudaGetDeviceCount(&devices);
    if (count_err != cudaSuccess || devices == 0) {
        cudaGetLastError();
        std::printf("SKIP: no CUDA device (%s); the shape defense above %s\n", cudaGetErrorString(count_err),
                    ok ? "passed" : "FAILED");
        return ok ? 77 : 1;
    }
    if (device < 0 || device >= devices) {
        std::fprintf(stderr, "FAIL: --device %d out of range (%d devices)\n", device, devices);
        return 2;
    }
    if (!ok) {
        std::fprintf(stderr, "qsa_select_rows_parity: shape defense failed\n");
        return 1;
    }

#if !defined(__linux__) && !defined(__APPLE__)
    std::puts("SKIP: the two-process dispatch check needs fork/exec (POSIX only)");
    return 77;
#else
    std::string self = argv0 != nullptr ? argv0 : "";
    char buf[4096];
    const ssize_t n = readlink("/proc/self/exe", buf, sizeof(buf) - 1);
    if (n > 0) {
        buf[n] = '\0';
        self = buf;
    } else if (self.empty() || self.find('/') == std::string::npos) {
        char* real = realpath(self.c_str(), nullptr);
        if (real != nullptr) {
            self = real;
            std::free(real);
        }
    }
    if (self.find('/') == std::string::npos) {
        std::fprintf(stderr, "FAIL: cannot find this executable's path (use an absolute path)\n");
        return 2;
    }

    char tmpl[] = "/tmp/qsa_select_rows_parity.XXXXXX";
    char* dir = mkdtemp(tmpl);
    if (dir == nullptr) {
        std::fprintf(stderr, "FAIL: mkdtemp: %s\n", std::strerror(errno));
        return 2;
    }
    const std::string dir_s = dir;
    const std::string dump_default = dir_s + "/default.bin";
    const std::string dump_rows = dir_s + "/rows.bin";
    const auto wall0 = std::chrono::steady_clock::now();

    auto spawn = [&](const char* mode, const std::string& out) -> int {
        std::fflush(stdout);
        std::fflush(stderr);
        const pid_t pid = fork();
        if (pid < 0) {
            std::fprintf(stderr, "FAIL: fork: %s\n", std::strerror(errno));
            return -1;
        }
        if (pid == 0) {
            char dev[32];
            std::snprintf(dev, sizeof(dev), "--device=%d", device);
            const std::string m = std::string("--child=") + mode;
            const std::string o = "--out=" + out;
            char* const args[] = {const_cast<char*>(self.c_str()), const_cast<char*>(m.c_str()),
                                  const_cast<char*>(o.c_str()), dev, nullptr};
            execv(self.c_str(), args);
            std::fprintf(stderr, "FAIL: execv(%s): %s\n", self.c_str(), std::strerror(errno));
            _exit(127);
        }
        int status = 0;
        if (waitpid(pid, &status, 0) != pid) {
            std::fprintf(stderr, "FAIL: waitpid: %s\n", std::strerror(errno));
            return -1;
        }
        if (WIFEXITED(status)) return WEXITSTATUS(status);
        std::fprintf(stderr, "FAIL: child %s died with signal %d\n", mode, WTERMSIG(status));
        return -1;
    };

    std::string why;
    int rc = spawn("default", dump_default);
    if (rc != 0) {
        std::fprintf(stderr, "FAIL: the default-dispatch child exited %d\n", rc);
        (void) rmdir(dir);
        return rc == 77 ? 77 : 2;
    }
    rc = spawn("rows", dump_rows);
    if (rc != 0) {
        std::fprintf(stderr, "FAIL: the STRATA_QSA_ROWS=1 child exited %d\n", rc);
        (void) rmdir(dir);
        return rc == 77 ? 77 : 2;
    }
    const double total_s = std::chrono::duration<double>(std::chrono::steady_clock::now() - wall0).count();

    Dump a, b;
    if (!read_dump(dump_default, a, why) || !read_dump(dump_rows, b, why)) {
        std::fprintf(stderr, "FAIL: %s\n", why.c_str());
        return 2;
    }
    if (a.h.rows_path != 0 || b.h.rows_path != 1) {
        std::fprintf(stderr, "FAIL: the dumps are not one default and one rows run (rows_path %u / %u)\n",
                     a.h.rows_path, b.h.rows_path);
        ok = false;
    }
    if (a.h.cc_major != b.h.cc_major || a.h.cc_minor != b.h.cc_minor || a.h.device != b.h.device ||
        std::strcmp(a.h.gpu, b.h.gpu) != 0) {
        std::fprintf(stderr, "FAIL: the two children ran on different devices (%s %d.%d vs %s %d.%d)\n", a.h.gpu,
                     a.h.cc_major, a.h.cc_minor, b.h.gpu, b.h.cc_major, b.h.cc_minor);
        ok = false;
    }
    if (a.h.n_cases != b.h.n_cases) {
        std::fprintf(stderr, "FAIL: %d cases in the default dump, %d in the rows dump; dumps kept: %s %s\n",
                     a.h.n_cases, b.h.n_cases, dump_default.c_str(), dump_rows.c_str());
        return 1;
    }
    std::printf("-- %s (%d.%d): default child \"%s\"; rows child \"%s\"\n", a.h.gpu, a.h.cc_major, a.h.cc_minor,
                a.h.dispatch, b.h.dispatch);
    std::printf("-- %d cases, both dumps read (%.2f s end to end including both processes)\n", a.h.n_cases, total_s);

    uint64_t compared_scores = 0, compared_ids = 0, diff_scores = 0, diff_ids = 0;
    int bad_cases = 0, nonfinite = 0;
    for (size_t ci = 0; ci < a.cases.size(); ++ci) {
        const DumpCase& ca = a.cases[ci];
        const DumpCase& cb = b.cases[ci];
        const std::string tag = "ctx " + std::to_string(ca.ctx) + " cap " + std::to_string(ca.cap_ctx) + " T " +
                                std::to_string(ca.scores_floats / ca.max_blocks);
        if (ca.ctx != cb.ctx || ca.cap_ctx != cb.cap_ctx || ca.max_blocks != cb.max_blocks ||
            ca.cap_width != cb.cap_width || ca.scores_floats != cb.scores_floats || ca.ids_ints != cb.ids_ints) {
            std::fprintf(stderr, "FAIL %s: case metadata differs between the dumps\n", tag.c_str());
            ok = false;
            ++bad_cases;
            continue;
        }
        if (ca.digest_pooled != cb.digest_pooled || ca.digest_dead != cb.digest_dead || ca.digest_q != cb.digest_q ||
            ca.digest_steps != cb.digest_steps) {
            std::fprintf(stderr, "FAIL %s: the device-generated inputs differ between the two processes "
                                 "(digests %016llx/%016llx %016llx/%016llx %016llx/%016llx %016llx/%016llx)\n",
                         tag.c_str(), (unsigned long long) ca.digest_pooled, (unsigned long long) cb.digest_pooled,
                         (unsigned long long) ca.digest_dead, (unsigned long long) cb.digest_dead,
                         (unsigned long long) ca.digest_q, (unsigned long long) cb.digest_q,
                         (unsigned long long) ca.digest_steps, (unsigned long long) cb.digest_steps);
            ok = false;
            ++bad_cases;
            continue;
        }

        const std::vector<float>& sa = a.scores[ci];
        const std::vector<float>& sb = b.scores[ci];
        const std::vector<int32_t>& ia = a.ids[ci];
        const std::vector<int32_t>& ib = b.ids[ci];
        compared_scores += sa.size();
        compared_ids += ia.size();
        size_t case_sdiff = 0, case_idiff = 0;
        for (size_t i = 0; i < sa.size(); ++i) {
            uint32_t ua = 0, ub = 0;
            std::memcpy(&ua, &sa[i], 4);
            std::memcpy(&ub, &sb[i], 4);
            if (ua != ub) ++case_sdiff;
        }
        for (size_t i = 0; i < ia.size(); ++i)
            if (ia[i] != ib[i]) ++case_idiff;
        const bool scores_memcmp = std::memcmp(sa.data(), sb.data(), sa.size() * sizeof(float)) == 0;
        const bool ids_memcmp = std::memcmp(ia.data(), ib.data(), ia.size() * sizeof(int32_t)) == 0;
        const bool scores_same = scores_memcmp && case_sdiff == 0, ids_same = ids_memcmp && case_idiff == 0;
        diff_scores += case_sdiff;
        diff_ids += case_idiff;
        std::string id_why;
        const bool ids_valid = check_ids(a, ci, id_why) && check_ids(b, ci, id_why);
        size_t bad_a = 0, bad_b = 0, first_a = sa.size(), first_b = sb.size();
        float first_va = 0.0f, first_vb = 0.0f;
        const bool finite_a = check_scores_finite(sa, ca, bad_a, first_a, first_va);
        const bool finite_b = check_scores_finite(sb, ca, bad_b, first_b, first_vb);
        const size_t nonfinite_case = bad_a + bad_b;
        const int64_t written = written_score_floats(ca);
        nonfinite += nonfinite_case;
        const int64_t width = widths_for(ca.ctx, (int) (ca.scores_floats / ca.max_blocks), k::qsa_real_shapes());
        const bool pass = scores_same && ids_same && ids_valid && nonfinite_case == 0;
        std::printf("%s %-34s max_blocks %6lld width %5lld | scores %s (%lld floats) | ids %s (%lld ints) | "
                    "finite %s | %.3f/%.3f ms\n",
                    pass ? "PASS" : "FAIL", tag.c_str(), (long long) ca.max_blocks, (long long) width,
                    scores_same ? "same" : "DIFFER", (long long) ca.scores_floats, ids_same ? "same" : "DIFFER",
                    (long long) ca.ids_ints, nonfinite_case == 0 ? "yes" : "NO", ca.ms, cb.ms);
        if (nonfinite_case == 0 && written != ca.scores_floats)
            std::printf("     (%lld of %lld floats written, %lld sentinel floats also memcmp'd)\n", (long long) written,
                        (long long) ca.scores_floats, (long long) (ca.scores_floats - written));
        if (!scores_same) {
            std::printf("   score differences (%s):\n", tag.c_str());
            print_score_diffs(sa, sb, ca.max_blocks, 8);
        }
        if (!ids_same) {
            std::printf("   id differences (%s):\n", tag.c_str());
            print_id_diffs(ia, ib, ca.cap_width, 8);
        }
        if (!ids_valid) {
            std::fprintf(stderr, "   FAIL %s: id contract: %s\n", tag.c_str(), id_why.c_str());
        }
        if (nonfinite_case != 0) {
            if (!finite_a)
                std::fprintf(stderr, "   FAIL %s: default has %zu NaN/Inf scores in the written blocks, first [%zu] = %g\n",
                             tag.c_str(), bad_a, first_a, (double) first_va);
            if (!finite_b)
                std::fprintf(stderr, "   FAIL %s: rows has %zu NaN/Inf scores in the written blocks, first [%zu] = %g\n",
                             tag.c_str(), bad_b, first_b, (double) first_vb);
        }
        if (!pass) {
            ok = false;
            ++bad_cases;
        }
    }

    const uint64_t peak = std::max(a.h.peak_alloc_bytes, b.h.peak_alloc_bytes);
    const uint64_t peak_delta = std::max(a.h.peak_delta_bytes, b.h.peak_delta_bytes);
    const double limit_bytes = (double) kMemLimitMiB * 1024.0 * 1024.0;
    std::printf("-- compared %llu score floats and %llu id ints in %d cases; %llu score floats and %llu id ints differ; "
                "%d non-finite written scores\n",
                (unsigned long long) compared_scores, (unsigned long long) compared_ids, a.h.n_cases,
                (unsigned long long) diff_scores, (unsigned long long) diff_ids, nonfinite);
    std::printf("-- child wall: default %.3f s, rows %.3f s\n", a.h.wall_s, b.h.wall_s);
    std::printf("-- per-child peak device buffers %.2f MiB (cudaMemGetInfo delta %.2f MiB, limit %.0f MiB); "
                "wall %.3f s (limit %.0f s)\n",
                (double) peak / (1024.0 * 1024.0), (double) peak_delta / (1024.0 * 1024.0), (double) kMemLimitMiB,
                total_s, kWallLimitS);
    if ((double) peak >= limit_bytes) {
        std::fprintf(stderr, "FAIL: a child allocated %.2f MiB, over the %.0f MiB budget\n",
                     (double) peak / (1024.0 * 1024.0), (double) kMemLimitMiB);
        ok = false;
    }
    if (total_s >= kWallLimitS) {
        std::fprintf(stderr, "FAIL: the two children took %.2f s, over the %.0f s budget\n", total_s, kWallLimitS);
        ok = false;
    }
    if (!ok) {
        std::fprintf(stderr, "qsa_select_rows_parity: FAIL (%d case(s) failed; the FAIL lines above say which check); "
                             "dumps kept: %s %s\n", bad_cases, dump_default.c_str(), dump_rows.c_str());
        return 1;
    }
    std::printf("qsa_select_rows_parity: PASS - default and STRATA_QSA_ROWS=1 are bit-identical on %d cases "
                "(scores and top-k ids)\n", a.h.n_cases);
    if (!keep) {
        std::remove(dump_default.c_str());
        std::remove(dump_rows.c_str());
        if (rmdir(dir) != 0) {
            // Best effort: the directory and the (already removed) files are in /tmp.
        }
    } else {
        std::printf("dumps kept: %s %s\n", dump_default.c_str(), dump_rows.c_str());
    }
    return 0;
#endif
}

}  // namespace

int main(int argc, char** argv) {
    std::setvbuf(stdout, nullptr, _IOLBF, 0);   // a ctest log keeps the driver's lines in order with the children's
    int device = 0;
    bool keep = false;
    bool child = false, rows_path = false;
    std::string out_path;
    for (int i = 1; i < argc; ++i) {
        const char* a = argv[i];
        if (std::strncmp(a, "--device=", 9) == 0) device = std::atoi(a + 9);
        else if (std::strcmp(a, "--device") == 0 && i + 1 < argc) device = std::atoi(argv[++i]);
        else if (std::strncmp(a, "--child=", 8) == 0) {
            child = true;
            const char* mode = a + 8;
            if (std::strcmp(mode, "rows") == 0) rows_path = true;
            else if (std::strcmp(mode, "default") != 0) {
                std::fprintf(stderr, "bad --child mode %s\n", mode);
                return 2;
            }
        } else if (std::strncmp(a, "--out=", 6) == 0) out_path = a + 6;
        else if (std::strcmp(a, "--keep") == 0) keep = true;
        else if (std::strcmp(a, "--help") == 0 || std::strcmp(a, "-h") == 0) {
            std::printf("usage: %s [--device N] [--keep]\n"
                        "  default dispatch vs STRATA_QSA_ROWS=1: scores and top-k ids, memcmp'd.\n", argv[0]);
            return 0;
        } else {
            std::fprintf(stderr, "usage: %s [--device N] [--keep]\n", argv[0]);
            return 2;
        }
    }
    if (child) {
        if (out_path.empty()) {
            std::fprintf(stderr, "--child needs --out=FILE\n");
            return 2;
        }
        return run_child(rows_path, out_path, device);
    }
    return run_driver(device, keep, argv[0]);
}
