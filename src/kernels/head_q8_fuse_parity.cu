// UTIL-HEAD-Q8-FUSE source/GPU gate.
//
// The production STRATA_HC_Q8=1 verifier currently writes the last layer's applied
// residual with gr_write_multi and then runs the final Q8 mixer with apply=false.
// This harness compares that sequence with one Q8 mixer call using apply=true and
// measures the saved critical path on the production hyper-connection geometry.

#include "strata/kernels/fused_gr.hpp"
#include "strata/kernels/gr.hpp"

#include <cuda_runtime.h>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <string>
#include <vector>

namespace k = strata::kernels;

namespace {

constexpr int kN = 2560;
constexpr int kHC = 4;
constexpr int kD = kN * kHC;
constexpr int kLR = 320;
constexpr int kMaxT = k::kFusedGrMaxT;
constexpr double kFastestWindowMs = 18.27;

void check(cudaError_t e, const char* what) {
    if (e != cudaSuccess) {
        std::fprintf(stderr, "%s: %s\n", what, cudaGetErrorString(e));
        std::exit(2);
    }
}

template <typename T>
T* upload(const std::vector<T>& h) {
    void* p = nullptr;
    check(cudaMalloc(&p, h.size() * sizeof(T)), "cudaMalloc upload");
    check(cudaMemcpy(p, h.data(), h.size() * sizeof(T), cudaMemcpyHostToDevice), "cudaMemcpy upload");
    return static_cast<T*>(p);
}

template <typename T>
T* device(size_t n) {
    void* p = nullptr;
    check(cudaMalloc(&p, n * sizeof(T)), "cudaMalloc device");
    check(cudaMemset(p, 0, n * sizeof(T)), "cudaMemset device");
    return static_cast<T*>(p);
}

template <typename T>
std::vector<T> download(const T* d, size_t n) {
    std::vector<T> h(n);
    check(cudaMemcpy(h.data(), d, n * sizeof(T), cudaMemcpyDeviceToHost), "cudaMemcpy download");
    return h;
}

std::vector<uint8_t> make_q8(size_t rows, size_t cols, uint32_t seed) {
    std::mt19937 rng(seed);
    std::uniform_int_distribution<int> q(-8, 8);
    std::vector<uint8_t> out(rows * (cols / 32) * 34);
    for (size_t b = 0; b < rows * (cols / 32); ++b) {
        // 2^-7 is exactly representable in fp16; each decoded value is q / 128.
        out[b * 34] = 0x00;
        out[b * 34 + 1] = 0x20;
        for (int i = 0; i < 32; ++i)
            out[b * 34 + 2 + i] = static_cast<uint8_t>(static_cast<int8_t>(q(rng)));
    }
    return out;
}

struct MixerPath {
    float* r = nullptr;
    float* lo = nullptr;
    float* rs = nullptr;
    float* mixed = nullptr;
    uint8_t* q8_mixed = nullptr;
    unsigned* q8_cnt = nullptr;
    k::FusedGrArgs args[kMaxT];
    cudaGraph_t graph = nullptr;
    cudaGraphExec_t exec = nullptr;
};

void bind_args(MixerPath& p, const float* w_norm, const uint8_t* q8_down, const uint8_t* q8_up,
               const float* bo, const float* inj, int T, bool apply) {
    for (int t = 0; t < T; ++t) {
        k::FusedGrArgs& a = p.args[t];
        a = k::FusedGrArgs();
        a.R = p.r + (size_t) t * kD;
        a.R_out = p.r + (size_t) t * kD;
        a.apply = apply;
        a.bo_prev = bo + (size_t) t * kN;
        a.inj_prev = inj + (size_t) t * kHC;
        a.w_norm = w_norm;
        a.w_down = nullptr;
        a.w_up = nullptr;
        a.q8_down = q8_down;
        a.q8_up = q8_up;
        a.w_inject = nullptr;
        a.eps = 1e-6f;
        a.lo = p.lo + (size_t) t * kLR;
        a.rs = p.rs + (size_t) t * kHC;
        a.mixed = p.mixed + (size_t) t * kN;
        a.q8_mixed = p.q8_mixed + (size_t) t * (kN / 32) * 36;
        a.q8_cnt = p.q8_cnt;
    }
}

MixerPath make_path(const float* w_norm, const uint8_t* q8_down, const uint8_t* q8_up,
                    const float* bo, const float* inj, int T, bool fused) {
    MixerPath p;
    p.r = device<float>((size_t) kMaxT * kD);
    p.lo = device<float>((size_t) kMaxT * kLR);
    p.rs = device<float>((size_t) kMaxT * kHC);
    p.mixed = device<float>((size_t) kMaxT * kN);
    void* qraw = nullptr;
    check(cudaMalloc(&qraw, (size_t) kMaxT * (kN / 32) * 36), "q8 mixed");
    p.q8_mixed = static_cast<uint8_t*>(qraw);
    p.q8_cnt = device<unsigned>(kN / 32);
    bind_args(p, w_norm, q8_down, q8_up, bo, inj, T, fused);
    return p;
}

void release_path(MixerPath& p) {
    if (p.exec) cudaGraphExecDestroy(p.exec);
    if (p.graph) cudaGraphDestroy(p.graph);
    cudaFree(p.r);
    cudaFree(p.lo);
    cudaFree(p.rs);
    cudaFree(p.mixed);
    cudaFree(p.q8_mixed);
    cudaFree(p.q8_cnt);
    p = MixerPath{};
}

void launch_path(MixerPath& p, int T, bool fused, float* xn, cudaStream_t stream) {
    if (!fused) k::gr_write_multi(p.r, p.args[0].bo_prev, p.args[0].inj_prev,
                                  k::GrShapes{kN, kHC, kLR}, p.r, T, stream);
    k::fused_gr_read_multi(p.args, T, xn, stream);
}

void reset_path(MixerPath& p, const float* base, int T, cudaStream_t stream) {
    check(cudaMemcpyAsync(p.r, base, (size_t) T * kD * sizeof(float),
                          cudaMemcpyDeviceToDevice, stream), "reset R");
    check(cudaMemsetAsync(p.q8_cnt, 0, (kN / 32) * sizeof(unsigned), stream), "reset q8 counters");
}

bool same_path(const MixerPath& a, const MixerPath& b, int T) {
    const auto ar = download(a.r, (size_t) T * kD);
    const auto br = download(b.r, (size_t) T * kD);
    const auto alo = download(a.lo, (size_t) T * kLR);
    const auto blo = download(b.lo, (size_t) T * kLR);
    const auto ars = download(a.rs, (size_t) T * kHC);
    const auto brs = download(b.rs, (size_t) T * kHC);
    const auto am = download(a.mixed, (size_t) T * kN);
    const auto bm = download(b.mixed, (size_t) T * kN);
    const size_t qb = (size_t) T * (kN / 32) * 36;
    const auto aq = download(a.q8_mixed, qb);
    const auto bq = download(b.q8_mixed, qb);
    return std::memcmp(ar.data(), br.data(), ar.size() * sizeof(float)) == 0 &&
           std::memcmp(alo.data(), blo.data(), alo.size() * sizeof(float)) == 0 &&
           std::memcmp(ars.data(), brs.data(), ars.size() * sizeof(float)) == 0 &&
           std::memcmp(am.data(), bm.data(), am.size() * sizeof(float)) == 0 &&
           std::memcmp(aq.data(), bq.data(), aq.size()) == 0;
}

float median(std::vector<float> values) {
    std::sort(values.begin(), values.end());
    return values[values.size() / 2];
}

double time_direct(MixerPath& p, int T, bool fused, float* xn, cudaStream_t stream,
                   cudaEvent_t begin, cudaEvent_t end, int iters) {
    for (int i = 0; i < 10; ++i) launch_path(p, T, fused, xn, stream);
    check(cudaStreamSynchronize(stream), "warm direct");
    check(cudaEventRecord(begin, stream), "event begin");
    for (int i = 0; i < iters; ++i) launch_path(p, T, fused, xn, stream);
    check(cudaEventRecord(end, stream), "event end");
    check(cudaEventSynchronize(end), "event sync");
    float ms = 0;
    check(cudaEventElapsedTime(&ms, begin, end), "event elapsed");
    return 1000.0 * ms / iters;
}

double time_graph(MixerPath& p, cudaStream_t stream, cudaEvent_t begin, cudaEvent_t end, int iters) {
    for (int i = 0; i < 10; ++i) check(cudaGraphLaunch(p.exec, stream), "warm graph");
    check(cudaStreamSynchronize(stream), "warm graph sync");
    check(cudaEventRecord(begin, stream), "graph begin");
    for (int i = 0; i < iters; ++i) check(cudaGraphLaunch(p.exec, stream), "graph launch");
    check(cudaEventRecord(end, stream), "graph end");
    check(cudaEventSynchronize(end), "graph sync");
    float ms = 0;
    check(cudaEventElapsedTime(&ms, begin, end), "graph elapsed");
    return 1000.0 * ms / iters;
}

}  // namespace

int main(int argc, char** argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);
    const int iters = argc > 1 ? std::atoi(argv[1]) : 200;
    const int rep = argc > 2 ? std::atoi(argv[2]) : 5;
    int cuda_device = 0;
    check(cudaGetDevice(&cuda_device), "cudaGetDevice");
    k::gr_set_native_mmvf(true);

    std::mt19937 rng(20261031);
    std::normal_distribution<float> normal(0.0f, 1.0f);
    std::vector<float> w_norm(kD), base((size_t) kMaxT * kD);
    std::vector<float> bo((size_t) kMaxT * kN), inj((size_t) kMaxT * kHC);
    for (auto& x : w_norm) x = 1.0f + 0.1f * normal(rng);
    for (auto& x : base) x = normal(rng);
    for (auto& x : bo) x = 0.5f * normal(rng);
    for (auto& x : inj) x = 3.0f * normal(rng);
    const auto q_down = make_q8(kLR, kD, 11);
    const auto q_up = make_q8(kD, kLR, 29);

    const float* d_norm = upload(w_norm);
    const float* d_base = upload(base);
    const float* d_bo = upload(bo);
    const float* d_inj = upload(inj);
    const uint8_t* d_qd = upload(q_down);
    const uint8_t* d_qu = upload(q_up);
    float* d_xn = device<float>((size_t) kMaxT * kD);
    cudaStream_t stream;
    check(cudaStreamCreateWithFlags(&stream, cudaStreamNonBlocking), "stream");
    cudaEvent_t begin, end;
    check(cudaEventCreate(&begin), "event begin");
    check(cudaEventCreate(&end), "event end");

    int failures = 0;
    int below_threshold = 0;
    int regressions = 0;
    double best_saved_us = 0;
    std::printf("UTIL-HEAD-Q8-FUSE device=%d geometry=%d/%d/%d fastest_window_ms=%.2f\n",
                cuda_device, kN, kHC, kLR, kFastestWindowMs);
    for (int T = 1; T <= kMaxT; ++T) {
        MixerPath separate = make_path(d_norm, d_qd, d_qu, d_bo, d_inj, T, false);
        MixerPath fused = make_path(d_norm, d_qd, d_qu, d_bo, d_inj, T, true);

        reset_path(separate, d_base, T, stream);
        launch_path(separate, T, false, d_xn, stream);
        reset_path(fused, d_base, T, stream);
        launch_path(fused, T, true, d_xn, stream);
        check(cudaStreamSynchronize(stream), "direct sync");
        const bool direct_same = same_path(separate, fused, T);
        if (!direct_same) {
            std::printf("T=%d direct-byte-compare=FAIL\n", T);
            ++failures;
        }

        for (MixerPath* p : {&separate, &fused}) {
            check(cudaStreamBeginCapture(stream, cudaStreamCaptureModeThreadLocal), "capture begin");
            launch_path(*p, T, p == &fused, d_xn, stream);
            check(cudaStreamEndCapture(stream, &p->graph), "capture end");
            check(cudaGraphInstantiate(&p->exec, p->graph, nullptr, nullptr, 0), "graph instantiate");
        }
        reset_path(separate, d_base, T, stream);
        check(cudaGraphLaunch(separate.exec, stream), "separate graph launch");
        reset_path(fused, d_base, T, stream);
        check(cudaGraphLaunch(fused.exec, stream), "fused graph launch");
        check(cudaStreamSynchronize(stream), "graph parity sync");
        const bool graph_same = same_path(separate, fused, T);
        if (!graph_same) {
            std::printf("T=%d graph-byte-compare=FAIL\n", T);
            ++failures;
        }

        std::vector<float> direct_sep, direct_fused, graph_sep, graph_fused;
        for (int r = 0; r < rep; ++r) {
            direct_sep.push_back((float) time_direct(separate, T, false, d_xn, stream, begin, end, iters));
            direct_fused.push_back((float) time_direct(fused, T, true, d_xn, stream, begin, end, iters));
            graph_sep.push_back((float) time_graph(separate, stream, begin, end, iters));
            graph_fused.push_back((float) time_graph(fused, stream, begin, end, iters));
        }
        const double ds = median(direct_sep), df = median(direct_fused);
        const double gs = median(graph_sep), gf = median(graph_fused);
        const double direct_saved = ds - df;
        const double graph_saved = gs - gf;
        const double direct_pct = 100.0 * direct_saved / (kFastestWindowMs * 1000.0);
        const double graph_pct = 100.0 * graph_saved / (kFastestWindowMs * 1000.0);
        best_saved_us = std::max(best_saved_us, std::max(direct_saved, graph_saved));
        const double best_pct = std::max(direct_pct, graph_pct);
        if (best_pct < 1.0) ++below_threshold;
        if (best_pct < -1.0) ++regressions;
        std::printf("T=%d direct=%.3f/%.3f us saved=%.3f (%.3f%% window) graph=%.3f/%.3f us saved=%.3f (%.3f%%) byte=%s\n",
                    T, ds, df, direct_saved, direct_pct, gs, gf, graph_saved, graph_pct,
                    direct_same && graph_same ? "PASS" : "FAIL");
        release_path(separate);
        release_path(fused);
    }
    std::printf("SUMMARY failures=%d best_saved_us=%.3f threshold_us=%.3f below_threshold_rows=%d "
                "regressions=%d result=%s\n",
                failures, best_saved_us, 0.01 * kFastestWindowMs * 1000.0, below_threshold,
                regressions, failures == 0 && below_threshold <= kMaxT - 1 && regressions == 0 ? "PASS" : "REJECT");

    check(cudaEventDestroy(begin), "event destroy begin");
    check(cudaEventDestroy(end), "event destroy end");
    check(cudaStreamDestroy(stream), "stream destroy");
    cudaFree(d_xn);
    cudaFree((void*) d_norm);
    cudaFree((void*) d_base);
    cudaFree((void*) d_bo);
    cudaFree((void*) d_inj);
    cudaFree((void*) d_qd);
    cudaFree((void*) d_qu);
    return failures == 0 && below_threshold <= kMaxT - 1 && regressions == 0 ? 0 : 1;
}
