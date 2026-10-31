// batch_side_mixer_bench.cpp - production GDN/QSA projection overlap for batch rows.
//
//     batch_side_mixer_bench [direct_iters] [rounds]
//
// GDN qkv and QSA q use the production IL path. GDN gate and QSA k/v use native_mmvq.
// Both arms compare serial and two-stream production-shape projection sequences, directly
// and in captured graphs. Outputs must be byte-identical.
#include "strata/kernels/native_mmvq.hpp"

#include <cuda_runtime.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <vector>

namespace K = strata::kernels;

namespace {

constexpr int N = 2560;
constexpr int QKV = 10240;
constexpr int GATE = 6144;
constexpr int QSA_Q = 12288;
constexpr int KV = 512;
constexpr int MAX_T = 8;
constexpr int GDN_LAYERS = 18;
constexpr int QSA_LAYERS = 6;
constexpr int TOTAL_LAYERS = GDN_LAYERS + QSA_LAYERS;
constexpr int QSA_INTERVAL = 4;
constexpr double STAGE_US = 18270.0;
constexpr int QKV_TYPE = 23;   // IQ4_XS
constexpr int GATE_TYPE = 21;  // IQ3_S, not IL
constexpr int QSA_Q_TYPE = 23; // IQ4_XS
constexpr int QSA_K_TYPE = 23; // IQ4_XS, not IL at 512 outputs
constexpr int QSA_V_TYPE = 14; // Q6_K, not IL at 512 outputs

void ck(cudaError_t e, const char* what) {
    if (e != cudaSuccess) {
        std::fprintf(stderr, "batch_side_mixer_bench: %s: %s\n", what, cudaGetErrorString(e));
        std::exit(2);
    }
}

uint16_t sane_half(std::mt19937& rng) {
    const uint32_t r = rng();
    return (uint16_t) (((r >> 31) << 15) | ((5u + (r >> 10) % 5u) << 10) | (r & 0x3ffu));
}

void* alloc(size_t bytes, const char* what) {
    void* p = nullptr;
    ck(cudaMalloc(&p, bytes), what);
    return p;
}

void upload(void* dst, const void* src, size_t bytes, const char* what) {
    ck(cudaMemcpy(dst, src, bytes, cudaMemcpyHostToDevice), what);
}

std::vector<uint8_t> weights(int type, int n_in, int n_out, uint32_t seed) {
    const size_t bytes = K::native_mmvq_weight_bytes(type, n_in, n_out);
    std::vector<uint8_t> w(bytes);
    std::mt19937 rng(seed);
    for (auto& x : w) x = (uint8_t) (rng() & 0xffu);
    const int elems = 256;
    const int block_bytes = type == GATE_TYPE ? 110 : type == QSA_V_TYPE ? 210 : 136;
    const int scale_at = type == QSA_V_TYPE ? 208 : 0;
    const size_t blocks = (size_t) n_in * (size_t) n_out / (size_t) elems;
    for (size_t b = 0; b < blocks; ++b) {
        const uint16_t h = sane_half(rng);
        std::memcpy(w.data() + b * (size_t) block_bytes + (size_t) scale_at, &h, sizeof(h));
    }
    return w;
}

double median(std::vector<double> v) {
    std::sort(v.begin(), v.end());
    const size_t n = v.size();
    return n & 1 ? v[n / 2] : 0.5 * (v[n / 2 - 1] + v[n / 2]);
}

struct Device {
    uint8_t* qkv_w = nullptr;
    uint8_t* gate_w = nullptr;
    uint8_t* q_w = nullptr;
    uint8_t* k_w = nullptr;
    uint8_t* v_w = nullptr;
    float* x = nullptr;
    void* xq = nullptr;
    void* xil = nullptr;
    float* qkv_y[2] = {};
    float* gate_y[2] = {};
    float* q_y[2] = {};
    float* k_y[2] = {};
    float* v_y[2] = {};
    cudaStream_t main = nullptr;
    cudaStream_t side = nullptr;
    cudaEvent_t fork = nullptr;
    cudaEvent_t join = nullptr;
    cudaEvent_t start = nullptr;
    cudaEvent_t stop = nullptr;
};

Device make_device(int dev) {
    ck(cudaSetDevice(dev), "set device");
    Device d;
    const auto qw = weights(QKV_TYPE, N, QKV, 101u);
    const auto gw = weights(GATE_TYPE, N, GATE, 202u);
    const auto qa = weights(QSA_Q_TYPE, N, QSA_Q, 404u);
    const auto kw = weights(QSA_K_TYPE, N, KV, 505u);
    const auto vw = weights(QSA_V_TYPE, N, KV, 606u);
    d.qkv_w = (uint8_t*) alloc(qw.size(), "qkv weights");
    d.gate_w = (uint8_t*) alloc(gw.size(), "gate weights");
    d.q_w = (uint8_t*) alloc(qa.size(), "qsa q weights");
    d.k_w = (uint8_t*) alloc(kw.size(), "qsa k weights");
    d.v_w = (uint8_t*) alloc(vw.size(), "qsa v weights");
    upload(d.qkv_w, qw.data(), qw.size(), "qkv upload");
    upload(d.gate_w, gw.data(), gw.size(), "gate upload");
    upload(d.q_w, qa.data(), qa.size(), "qsa q upload");
    upload(d.k_w, kw.data(), kw.size(), "qsa k upload");
    upload(d.v_w, vw.data(), vw.size(), "qsa v upload");
    std::vector<float> x((size_t) MAX_T * N);
    std::mt19937 rng(303);
    std::normal_distribution<float> nd(0.f, 1.f);
    for (auto& v : x) v = nd(rng);
    d.x = (float*) alloc(x.size() * sizeof(float), "activation");
    upload(d.x, x.data(), x.size() * sizeof(float), "activation upload");
    d.xq = alloc(K::native_q8_1_bytes(N, MAX_T), "q8 activation");
    d.xil = alloc(K::native_q8_1_il_bytes(N, MAX_T), "interleaved q8 activation");
    d.qkv_y[0] = (float*) alloc((size_t) MAX_T * QKV * sizeof(float), "qkv A");
    d.qkv_y[1] = (float*) alloc((size_t) MAX_T * QKV * sizeof(float), "qkv B");
    d.gate_y[0] = (float*) alloc((size_t) MAX_T * GATE * sizeof(float), "gate A");
    d.gate_y[1] = (float*) alloc((size_t) MAX_T * GATE * sizeof(float), "gate B");
    d.q_y[0] = (float*) alloc((size_t) MAX_T * QSA_Q * sizeof(float), "q A");
    d.q_y[1] = (float*) alloc((size_t) MAX_T * QSA_Q * sizeof(float), "q B");
    d.k_y[0] = (float*) alloc((size_t) MAX_T * KV * sizeof(float), "k A");
    d.k_y[1] = (float*) alloc((size_t) MAX_T * KV * sizeof(float), "k B");
    d.v_y[0] = (float*) alloc((size_t) MAX_T * KV * sizeof(float), "v A");
    d.v_y[1] = (float*) alloc((size_t) MAX_T * KV * sizeof(float), "v B");
    ck(cudaStreamCreateWithFlags(&d.main, cudaStreamNonBlocking), "main stream");
    ck(cudaStreamCreateWithFlags(&d.side, cudaStreamNonBlocking), "side stream");
    ck(cudaEventCreateWithFlags(&d.fork, cudaEventDisableTiming), "fork event");
    ck(cudaEventCreateWithFlags(&d.join, cudaEventDisableTiming), "join event");
    ck(cudaEventCreate(&d.start), "start event");
    ck(cudaEventCreate(&d.stop), "stop event");
    return d;
}

void mm_il(int type, const void* w, Device& d, float* out, int n_in, int n_out, int T, cudaStream_t s) {
    K::native_mmvq_il(type, w, d.xq, d.xil, out, n_in, n_out, T, s);
}

void gdn_sequence(Device& d, int T, int arm, bool side) {
    // GDN: qkv IL on the main stream, IQ3_S gate beside it.
    if (side) {
        ck(cudaEventRecord(d.fork, d.main), "gdn fork record");
        ck(cudaStreamWaitEvent(d.side, d.fork, 0), "gdn side wait");
    }
    mm_il(QKV_TYPE, d.qkv_w, d, d.qkv_y[arm], N, QKV, T, d.main);
    K::native_mmvq(GATE_TYPE, d.gate_w, d.xq, d.gate_y[arm], N, GATE, T, side ? d.side : d.main);
    if (side) {
        ck(cudaEventRecord(d.join, d.side), "gdn join record");
        ck(cudaStreamWaitEvent(d.main, d.join, 0), "gdn main wait");
    }
}

void qsa_sequence(Device& d, int T, int arm, bool side) {
    // QSA: q IL on the side stream, K/V on the main stream.
    if (side) {
        ck(cudaEventRecord(d.fork, d.main), "qsa fork record");
        ck(cudaStreamWaitEvent(d.side, d.fork, 0), "qsa side wait");
    }
    mm_il(QSA_Q_TYPE, d.q_w, d, d.q_y[arm], N, QSA_Q, T, side ? d.side : d.main);
    K::native_mmvq(QSA_K_TYPE, d.k_w, d.xq, d.k_y[arm], N, KV, T, d.main);
    K::native_mmvq(QSA_V_TYPE, d.v_w, d.xq, d.v_y[arm], N, KV, T, d.main);
    if (side) {
        ck(cudaEventRecord(d.join, d.side), "qsa join record");
        ck(cudaStreamWaitEvent(d.main, d.join, 0), "qsa main wait");
    }
}

enum class Shape {
    Gdn18,
    Qsa6,
    Mixed24,
};

const char* shape_name(Shape shape) {
    switch (shape) {
        case Shape::Gdn18: return "GDN18";
        case Shape::Qsa6: return "QSA6";
        case Shape::Mixed24: return "MIXED24";
    }
    return "?";
}

int shape_layers(Shape shape) {
    switch (shape) {
        case Shape::Gdn18: return GDN_LAYERS;
        case Shape::Qsa6: return QSA_LAYERS;
        case Shape::Mixed24: return TOTAL_LAYERS;
    }
    return 0;
}

void shape_sequence(Device& d, int T, int arm, bool side, Shape shape) {
    if (shape == Shape::Gdn18) {
        for (int l = 0; l < GDN_LAYERS; ++l) gdn_sequence(d, T, arm, side);
        return;
    }
    if (shape == Shape::Qsa6) {
        for (int l = 0; l < QSA_LAYERS; ++l) qsa_sequence(d, T, arm, side);
        return;
    }
    // The model has one full-attention layer every fourth layer: 18 GDN + 6 QSA.
    for (int l = 0; l < TOTAL_LAYERS; ++l) {
        if (l % QSA_INTERVAL == QSA_INTERVAL - 1) qsa_sequence(d, T, arm, side);
        else gdn_sequence(d, T, arm, side);
    }
}

cudaGraph_t capture(Device& d, int T, int arm, Shape shape, bool side) {
    ck(cudaStreamBeginCapture(d.main, cudaStreamCaptureModeThreadLocal), "begin capture");
    shape_sequence(d, T, arm, side, shape);
    cudaGraph_t graph = nullptr;
    ck(cudaStreamEndCapture(d.main, &graph), "end capture");
    return graph;
}

double time_direct(Device& d, int T, int arm, bool side, int iters, int rounds) {
    std::vector<double> us;
    for (int r = 0; r < rounds; ++r) {
        ck(cudaEventRecord(d.start, d.main), "direct start");
        for (int i = 0; i < iters; ++i) shape_sequence(d, T, arm, side, Shape::Mixed24);
        ck(cudaEventRecord(d.stop, d.main), "direct stop");
        ck(cudaEventSynchronize(d.stop), "direct sync");
        float ms = 0.f;
        ck(cudaEventElapsedTime(&ms, d.start, d.stop), "direct elapsed");
        us.push_back(1e3 * (double) ms / (double) iters);
    }
    return median(us);
}

double time_graph(Device& d, cudaGraphExec_t ex, int iters, int rounds) {
    std::vector<double> us;
    for (int r = 0; r < rounds; ++r) {
        ck(cudaEventRecord(d.start, d.main), "graph start");
        for (int i = 0; i < iters; ++i) ck(cudaGraphLaunch(ex, d.main), "graph launch");
        ck(cudaEventRecord(d.stop, d.main), "graph stop");
        ck(cudaEventSynchronize(d.stop), "graph sync");
        float ms = 0.f;
        ck(cudaEventElapsedTime(&ms, d.start, d.stop), "graph elapsed");
        us.push_back(1e3 * (double) ms / (double) iters);
    }
    return median(us);
}

bool equal(float* a, float* b, size_t count) {
    std::vector<float> ha(count), hb(count);
    ck(cudaMemcpy(ha.data(), a, count * sizeof(float), cudaMemcpyDeviceToHost), "A read");
    ck(cudaMemcpy(hb.data(), b, count * sizeof(float), cudaMemcpyDeviceToHost), "B read");
    return std::memcmp(ha.data(), hb.data(), count * sizeof(float)) == 0;
}

int run(int direct_iters, int rounds) {
    ck(cudaSetDevice(0), "set device");
    Device d = make_device(0);
    int dev = 0;
    ck(cudaGetDevice(&dev), "device");
    int bad = 0;
    std::printf("batch_side_mixer_bench gpu=%d direct_iters=%d rounds=%d\n", dev, direct_iters, rounds);
    // The earlier T=1..8 screen found only T=2 with enough absolute headroom. The candidate
    // is restricted to T=2, so this benchmark measures only that production path.
    for (int T = 2; T <= 2; ++T) {
        if (T >= 2 && T <= 4) K::native_quantize_q8_1_il(d.x, d.xq, d.xil, N, T, d.main);
        else {
            K::native_quantize_q8_1(d.x, d.xq, N, T, d.main);
            ck(cudaMemsetAsync(d.xil, 0, K::native_q8_1_il_bytes(N, MAX_T), d.main), "xil poison");
        }
        ck(cudaStreamSynchronize(d.main), "quantize sync");
        const bool il_qkv = K::native_mmvq_il_supported(QKV_TYPE, T, QKV);
        const bool il_q = K::native_mmvq_il_supported(QSA_Q_TYPE, T, QSA_Q);
        // A wrong IL dispatch is not a candidate measurement.
        if (T >= 3 && T <= 4 && (!il_qkv || !il_q)) {
            std::printf("SIDE_PROD_SKIP gpu=%d T=%d il_qkv=%d il_q=%d\n", dev, T, (int) il_qkv, (int) il_q);
            ++bad;
            continue;
        }
        for (int i = 0; i < 50; ++i) {
            shape_sequence(d, T, 0, false, Shape::Mixed24);
            shape_sequence(d, T, 1, true, Shape::Mixed24);
        }
        shape_sequence(d, T, 0, false, Shape::Mixed24);
        shape_sequence(d, T, 1, true, Shape::Mixed24);
        ck(cudaStreamSynchronize(d.main), "warm checks");
        const bool equal_all = equal(d.qkv_y[0], d.qkv_y[1], (size_t) T * QKV) &&
                               equal(d.gate_y[0], d.gate_y[1], (size_t) T * GATE) &&
                               equal(d.q_y[0], d.q_y[1], (size_t) T * QSA_Q) &&
                               equal(d.k_y[0], d.k_y[1], (size_t) T * KV) &&
                               equal(d.v_y[0], d.v_y[1], (size_t) T * KV);
        const double a = time_direct(d, T, 0, false, direct_iters, rounds);
        const double b = time_direct(d, T, 1, true, direct_iters, rounds);
        std::printf("SIDE_PROD_DIRECT gpu=%d T=%d A=%.4f B=%.4f save=%.4f pct=%+.4f stage_pct=%+.4f "
                    "il_qkv=%d il_q=%d equal=%s\n",
                    dev, T, a, b, a - b, 100.0 * (a - b) / a, 100.0 * (a - b) / STAGE_US,
                    (int) il_qkv, (int) il_q,
                    equal_all ? "yes" : "NO");
        if (!equal_all) ++bad;

        cudaGraph_t ga = capture(d, T, 0, Shape::Mixed24, false);
        cudaGraph_t gb = capture(d, T, 1, Shape::Mixed24, true);
        cudaGraphExec_t ea = nullptr;
        cudaGraphExec_t eb = nullptr;
        ck(cudaGraphInstantiate(&ea, ga, nullptr, nullptr, 0), "instantiate direct A");
        ck(cudaGraphInstantiate(&eb, gb, nullptr, nullptr, 0), "instantiate direct B");
        ck(cudaGraphLaunch(ea, d.main), "launch direct A");
        ck(cudaGraphLaunch(eb, d.main), "launch direct B");
        ck(cudaStreamSynchronize(d.main), "sync direct graphs");
        const bool one_equal = equal(d.qkv_y[0], d.qkv_y[1], (size_t) T * QKV) &&
                               equal(d.gate_y[0], d.gate_y[1], (size_t) T * GATE) &&
                               equal(d.q_y[0], d.q_y[1], (size_t) T * QSA_Q) &&
                               equal(d.k_y[0], d.k_y[1], (size_t) T * KV) &&
                               equal(d.v_y[0], d.v_y[1], (size_t) T * KV);
        std::printf("SIDE_PROD_GRAPH_MIXED24 gpu=%d T=%d equal=%s\n", dev, T, one_equal ? "yes" : "NO");
        if (!one_equal) ++bad;
        cudaGraphExecDestroy(ea);
        cudaGraphExecDestroy(eb);
        cudaGraphDestroy(ga);
        cudaGraphDestroy(gb);

        const int graph_iters = std::max(1, direct_iters / 10);
        for (Shape shape : {Shape::Gdn18, Shape::Qsa6, Shape::Mixed24}) {
            cudaGraph_t gla = capture(d, T, 0, shape, false);
            cudaGraph_t glb = capture(d, T, 1, shape, true);
            cudaGraphExec_t ela = nullptr;
            cudaGraphExec_t elb = nullptr;
            ck(cudaGraphInstantiate(&ela, gla, nullptr, nullptr, 0), "instantiate shape A");
            ck(cudaGraphInstantiate(&elb, glb, nullptr, nullptr, 0), "instantiate shape B");
            const double ga_us = time_graph(d, ela, graph_iters, rounds);
            const double gb_us = time_graph(d, elb, graph_iters, rounds);
            ck(cudaGraphLaunch(ela, d.main), "launch shape A");
            ck(cudaGraphLaunch(elb, d.main), "launch shape B");
            ck(cudaStreamSynchronize(d.main), "sync shape graphs");
            const bool layers_equal = equal(d.qkv_y[0], d.qkv_y[1], (size_t) T * QKV) &&
                                      equal(d.gate_y[0], d.gate_y[1], (size_t) T * GATE) &&
                                      equal(d.q_y[0], d.q_y[1], (size_t) T * QSA_Q) &&
                                      equal(d.k_y[0], d.k_y[1], (size_t) T * KV) &&
                                      equal(d.v_y[0], d.v_y[1], (size_t) T * KV);
            std::printf("SIDE_PROD_GRAPH_%s gpu=%d T=%d layers=%d A=%.4f B=%.4f save=%.4f "
                        "pct=%+.4f stage_pct=%+.4f equal=%s\n",
                        shape_name(shape), dev, T, shape_layers(shape), ga_us, gb_us, ga_us - gb_us,
                        100.0 * (ga_us - gb_us) / ga_us, 100.0 * (ga_us - gb_us) / STAGE_US,
                        layers_equal ? "yes" : "NO");
            if (!layers_equal) ++bad;
            cudaGraphExecDestroy(ela);
            cudaGraphExecDestroy(elb);
            cudaGraphDestroy(gla);
            cudaGraphDestroy(glb);
        }
    }
    return bad ? 1 : 0;
}

}  // namespace

int main(int argc, char** argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);
    const int direct_iters = argc > 1 ? std::atoi(argv[1]) : 100;
    const int rounds = argc > 2 ? std::atoi(argv[2]) : 5;
    return run(direct_iters, rounds);
}
