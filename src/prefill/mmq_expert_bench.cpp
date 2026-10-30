// src/prefill/mmq_expert_bench.cpp - the prompt path's routed-expert products (MMQ) timed at the engine's shapes.
//
//     mmq_expert_bench [rows per expert ...]
//
// One MMQ group as the prompt path launches it: normally 16 experts, R rows each, in one activation array in expert
// order.  MMQ_BENCH_EXPERTS=N overrides the group size for the launch-amortization experiment; it changes only this
// synthetic benchmark, not the production grouping or buffers.
// The sm_75 production pack uses IQ2_XXS, IQ2_XS, IQ3_XXS, IQ3_S or IQ2_S gate/up [1280 x 2560], and IQ4_NL or
// Q2_0 down [2560 x 640].  Q8_0 is kept for the DFlash2/dense fallback shape.
// Synthetic weights of the right format (valid scales), random activations of row-varying magnitude.  Per product:
// the median of 25 runs, and the rate in int8-equivalent TOPS (2 x rows x out x in).  Default R: 12 (a 600-token
// chunk: 600 x 10 / 512), 160 (8K), 626 (32K).
#include "strata/prefill/moe_mmq.hpp"

#include "ggml.h"

#include <cuda_runtime.h>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <vector>

namespace mmq = strata::prefill::mmq;

namespace {

void ck(cudaError_t e, const char* what) {
    if (e != cudaSuccess) { std::fprintf(stderr, "%s: %s\n", what, cudaGetErrorString(e)); std::exit(1); }
}

// Valid synthetic weights: the production i-quants and Q8_0 each start with a finite f16 scale.  Random bytes follow,
// with that scale overwritten by a small positive value in the model's range.
std::vector<uint8_t> weights(ggml_type t, int64_t rows, int64_t cols, int experts, std::mt19937& rng) {
    const size_t per = mmq::matrix_bytes(t, rows, cols);
    std::vector<uint8_t> w((size_t) experts * per + 4096);
    std::uniform_int_distribution<int> byte(0, 255);
    for (auto& b : w) b = (uint8_t) byte(rng);
    const size_t bs = ggml_type_size(t), nblk = (size_t) experts * per / bs;
    for (size_t i = 0; i < nblk; ++i) {
        uint8_t* blk = w.data() + i * bs;
        const uint16_t d = 0x2000 + (uint16_t) byte(rng);             // f16 ~ 0.0078..0.016
        std::memcpy(blk, &d, 2);
    }
    return w;
}

struct Case {
    const char* name;
    ggml_type t;
    int64_t rows, cols;
};

double run_case(const Case& c, int R, int E, std::mt19937& rng, float* ms_out) {
    const int T = E * R;
    const size_t per = mmq::matrix_bytes(c.t, c.rows, c.cols);
    std::vector<uint8_t> w = weights(c.t, c.rows, c.cols, E, rng);
    std::vector<float> x((size_t) T * c.cols);
    std::normal_distribution<float> nd(0.f, 1.f);
    std::uniform_real_distribution<float> mag(0.1f, 10.f);
    for (int t = 0; t < T; ++t) { const float m = mag(rng); for (int64_t k = 0; k < c.cols; ++k) x[(size_t) t * c.cols + k] = nd(rng) * m; }
    std::vector<int32_t> bounds(E + 1);
    for (int e = 0; e <= E; ++e) bounds[e] = e * R;

    uint8_t* dw; float *dx, *dd; void* dq; int32_t *dids, *db;
    ck(cudaMalloc(&dw, w.size()), "w"); ck(cudaMemcpy(dw, w.data(), w.size(), cudaMemcpyHostToDevice), "w");
    ck(cudaMalloc(&dx, x.size() * 4), "x"); ck(cudaMemcpy(dx, x.data(), x.size() * 4, cudaMemcpyHostToDevice), "x");
    ck(cudaMalloc(&dq, mmq::q8_bytes(T, c.cols)), "xq");
    ck(cudaMalloc(&dd, (size_t) T * c.rows * 4), "dst");
    ck(cudaMalloc(&dids, (size_t) T * 4), "ids");
    ck(cudaMalloc(&db, (E + 1) * 4), "bounds");
    ck(cudaMemcpy(db, bounds.data(), (E + 1) * 4, cudaMemcpyHostToDevice), "bounds");
    mmq::iota(dids, T, nullptr);
    mmq::quantize(dx, nullptr, dq, c.t, c.cols, c.cols, T, nullptr);
    mmq::Context ctx;
    mmq::Product p;
    p.w = dw; p.type = c.t; p.w_rows = c.rows; p.w_cols = c.cols; p.expert_bytes = per; p.n = E;
    p.xq = dq; p.bounds = db; p.ids = dids; p.total_rows = T; p.max_rows = R; p.dst = dd; p.ld_dst = c.rows;
    cudaEvent_t e0, e1;
    cudaEventCreate(&e0); cudaEventCreate(&e1);
    std::vector<float> ts;
    for (int i = 0; i < 28; ++i) {
        cudaEventRecord(e0);
        ctx.run(p, nullptr);
        cudaEventRecord(e1);
        ck(cudaEventSynchronize(e1), "run");
        float ms; cudaEventElapsedTime(&ms, e0, e1);
        if (i >= 3) ts.push_back(ms);
    }
    std::sort(ts.begin(), ts.end());
    const float ms = ts[ts.size() / 2];
    if (ms_out) *ms_out = ms;
    cudaFree(dw); cudaFree(dx); cudaFree(dq); cudaFree(dd); cudaFree(dids); cudaFree(db);
    cudaEventDestroy(e0); cudaEventDestroy(e1);
    return 2.0 * T * (double) c.rows * c.cols / (ms * 1e9);
}

// 吞吐优化微基准：同一份 GU 上比较 SwiGLU 写 H + q8_1 量化，与直接 SwiGLU -> q8_1。
// 输入只用于计时，所有形状的 q8_1 字节等价性由 mmq_expert_parity 覆盖。
void bench_hidden_quant() {
    const int rows_cases[] = {4000, 8000, 16000, 40000, 400000};
    std::printf("hidden quantize: SwiGLU + q8_1, medians of 25\n");
    for (int rows : rows_cases) {
        float* gu = nullptr;
        float* h = nullptr;
        void* q_old = nullptr;
        void* q_new = nullptr;
        const size_t gu_bytes = (size_t) rows * 1280 * sizeof(float);
        const size_t h_bytes = (size_t) rows * 640 * sizeof(float);
        const size_t q_bytes = mmq::q8_bytes(rows, 640);
        ck(cudaMalloc(&gu, gu_bytes), "hquant gu");
        ck(cudaMalloc(&h, h_bytes), "hquant h");
        ck(cudaMalloc(&q_old, q_bytes), "hquant q old");
        ck(cudaMalloc(&q_new, q_bytes), "hquant q new");
        // 0x3f3f3f3f is a finite positive float; it avoids an expensive host fill while keeping all arithmetic active.
        ck(cudaMemset(gu, 0x3f, gu_bytes), "hquant init");

        cudaEvent_t begin, end;
        ck(cudaEventCreate(&begin), "hquant event");
        ck(cudaEventCreate(&end), "hquant event");
        std::vector<float> old_ms, new_ms;
        for (int i = 0; i < 28; ++i) {
            ck(cudaEventRecord(begin), "hquant record");
            mmq::swiglu(gu, h, rows, 640, false, nullptr);
            mmq::quantize(h, nullptr, q_old, GGML_TYPE_IQ4_NL, 640, 640, rows, nullptr);
            ck(cudaEventRecord(end), "hquant record");
            ck(cudaEventSynchronize(end), "hquant sync");
            float ms = 0;
            ck(cudaEventElapsedTime(&ms, begin, end), "hquant elapsed");
            if (i >= 3) old_ms.push_back(ms);

            ck(cudaEventRecord(begin), "hquant record");
            mmq::swiglu_quantize(gu, q_new, rows, 640, false, GGML_TYPE_IQ4_NL, nullptr);
            ck(cudaEventRecord(end), "hquant record");
            ck(cudaEventSynchronize(end), "hquant sync");
            ck(cudaEventElapsedTime(&ms, begin, end), "hquant elapsed");
            if (i >= 3) new_ms.push_back(ms);
        }
        std::sort(old_ms.begin(), old_ms.end());
        std::sort(new_ms.begin(), new_ms.end());
        const float t_old = old_ms[old_ms.size() / 2], t_new = new_ms[new_ms.size() / 2];
        std::printf("  rows %6d  two-kernel %8.3f ms  fused %8.3f ms  %.2fx\n", rows, t_old, t_new,
                    t_old / t_new);
        cudaEventDestroy(begin);
        cudaEventDestroy(end);
        cudaFree(gu);
        cudaFree(h);
        cudaFree(q_old);
        cudaFree(q_new);
    }
}

}  // namespace

int main(int argc, char** argv) {
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--hquant-bench") == 0) {
            bench_hidden_quant();
            return 0;
        }
    }
    std::vector<int> Rs;
    for (int i = 1; i < argc; ++i) Rs.push_back(std::atoi(argv[i]));
    if (Rs.empty()) Rs = {12, 160, 626};
    // Comparing one launch over 16/32/64 experts separates fixed launch cost from the per-expert MMQ work.  This hook
    // only allocates synthetic contiguous weights at the requested size; production keeps the measured 16-expert group.
    const int experts = [] {
        const char* value = std::getenv("MMQ_BENCH_EXPERTS");
        const int parsed = value ? std::atoi(value) : 0;
        return parsed > 0 ? parsed : 16;
    }();
    std::printf("mmq_expert_bench: sm_75 production formats, %d experts a launch, medians of 25\n", experts);
    const Case cases[] = {
                          {"iq2_xxs gate/up", GGML_TYPE_IQ2_XXS, 1280, 2560},
                          {"iq2_xs gate/up", GGML_TYPE_IQ2_XS, 1280, 2560},
                          {"iq3_xxs gate/up", GGML_TYPE_IQ3_XXS, 1280, 2560},
                          {"iq3_s gate/up", GGML_TYPE_IQ3_S, 1280, 2560},
                          {"iq2_s gate/up", GGML_TYPE_IQ2_S, 1280, 2560},
                          {"iq4_nl down", GGML_TYPE_IQ4_NL, 2560, 640},
                          {"q2_0 down", GGML_TYPE_Q2_0, 2560, 640},
                          {"q8_0 fallback", GGML_TYPE_Q8_0, 2560, 640}};
    std::mt19937 rng(7);
    for (int R : Rs)
        for (const Case& c : cases) {
            float ms = 0;
            const double tops = run_case(c, R, experts, rng, &ms);
            // the weight bytes one launch reads, per ms: the bandwidth the product needs at residency
            const double gbs = (double) experts * mmq::matrix_bytes(c.t, c.rows, c.cols) / (ms * 1e6);
            std::printf("  R %4d  %-14s %8.3f ms  %7.1f TOPS  %7.1f GB/s weights\n", R, c.name, ms, tops, gbs);
        }
    return 0;
}
