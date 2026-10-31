// mixer_overlap_bench.cpp - the GDN qkv and gate projections sequentially or on two streams.
//
//     mixer_overlap_bench [iters] [rounds]
//
// Real production shapes and formats: IQ4_XS qkv 2560 -> 10240 and IQ3_S gate 2560 -> 6144.
// A is qkv then gate on one stream. B forks a non-blocking side stream after activation
// quantization, runs qkv on the main stream and gate on the side, then joins. Outputs must be
// byte-identical. One line per T=1..8.
#include "strata/kernels/native_mmvq.hpp"

#include <cuda_runtime.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <string>
#include <vector>

namespace K = strata::kernels;

namespace {

constexpr int N = 2560;
constexpr int QKV = 10240;
constexpr int GATE = 6144;
constexpr int QSA_Q = 12288;
constexpr int KV = 512;
constexpr int MAX_T = 8;
constexpr int QKV_TYPE = 23;   // IQ4_XS
constexpr int GATE_TYPE = 21;  // IQ3_S
constexpr int QSA_Q_TYPE = 23; // IQ4_XS
constexpr int QSA_K_TYPE = 23; // IQ4_XS
constexpr int QSA_V_TYPE = 14; // Q6_K

void ck(cudaError_t e, const char* what) {
    if (e != cudaSuccess) {
        std::fprintf(stderr, "mixer_overlap_bench: %s: %s\n", what, cudaGetErrorString(e));
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
    float* x = nullptr;
    void* xq = nullptr;
    float* qkv_y[2] = {nullptr, nullptr};
    float* gate_y[2] = {nullptr, nullptr};
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
    d.qkv_w = (uint8_t*) alloc(qw.size(), "qkv weights");
    d.gate_w = (uint8_t*) alloc(gw.size(), "gate weights");
    upload(d.qkv_w, qw.data(), qw.size(), "qkv upload");
    upload(d.gate_w, gw.data(), gw.size(), "gate upload");
    std::vector<float> x((size_t) MAX_T * N);
    std::mt19937 rng(303);
    std::normal_distribution<float> nd(0.f, 1.f);
    for (auto& v : x) v = nd(rng);
    d.x = (float*) alloc(x.size() * sizeof(float), "activation");
    upload(d.x, x.data(), x.size() * sizeof(float), "activation upload");
    d.xq = alloc(K::native_q8_1_bytes(N, MAX_T), "q8 activation");
    d.qkv_y[0] = (float*) alloc((size_t) MAX_T * QKV * sizeof(float), "qkv A");
    d.qkv_y[1] = (float*) alloc((size_t) MAX_T * QKV * sizeof(float), "qkv B");
    d.gate_y[0] = (float*) alloc((size_t) MAX_T * GATE * sizeof(float), "gate A");
    d.gate_y[1] = (float*) alloc((size_t) MAX_T * GATE * sizeof(float), "gate B");
    ck(cudaStreamCreateWithFlags(&d.main, cudaStreamNonBlocking), "main stream");
    ck(cudaStreamCreateWithFlags(&d.side, cudaStreamNonBlocking), "side stream");
    ck(cudaEventCreateWithFlags(&d.fork, cudaEventDisableTiming), "fork event");
    ck(cudaEventCreateWithFlags(&d.join, cudaEventDisableTiming), "join event");
    ck(cudaEventCreate(&d.start), "start event");
    ck(cudaEventCreate(&d.stop), "stop event");
    return d;
}

void launch_a(Device& d, int T, int arm) {
    K::native_mmvq(QKV_TYPE, d.qkv_w, d.xq, d.qkv_y[arm], N, QKV, T, d.main);
    K::native_mmvq(GATE_TYPE, d.gate_w, d.xq, d.gate_y[arm], N, GATE, T, d.main);
}

void launch_b(Device& d, int T, int arm) {
    ck(cudaEventRecord(d.fork, d.main), "fork record");
    ck(cudaStreamWaitEvent(d.side, d.fork, 0), "side wait");
    K::native_mmvq(QKV_TYPE, d.qkv_w, d.xq, d.qkv_y[arm], N, QKV, T, d.main);
    K::native_mmvq(GATE_TYPE, d.gate_w, d.xq, d.gate_y[arm], N, GATE, T, d.side);
    ck(cudaEventRecord(d.join, d.side), "join record");
    ck(cudaStreamWaitEvent(d.main, d.join, 0), "main wait");
}

double time_arm(Device& d, int T, int arm, int iters, int rounds) {
    std::vector<double> us;
    for (int r = 0; r < rounds; ++r) {
        ck(cudaEventRecord(d.start, d.main), "start record");
        for (int i = 0; i < iters; ++i) {
            if (arm == 0) launch_a(d, T, 0);
            else launch_b(d, T, 1);
        }
        ck(cudaEventRecord(d.stop, d.main), "stop record");
        ck(cudaEventSynchronize(d.stop), "stop sync");
        float ms = 0.f;
        ck(cudaEventElapsedTime(&ms, d.start, d.stop), "elapsed");
        us.push_back(1e3 * (double) ms / (double) iters);
    }
    return median(us);
}

int run_qsa(Device& d, int iters, int rounds) {
    int dev = 0;
    ck(cudaGetDevice(&dev), "qsa device");
    const auto qw = weights(QSA_Q_TYPE, N, QSA_Q, 404u);
    const auto kw = weights(QSA_K_TYPE, N, KV, 505u);
    const auto vw = weights(QSA_V_TYPE, N, KV, 606u);
    uint8_t* q_w = (uint8_t*) alloc(qw.size(), "qsa q weights");
    uint8_t* k_w = (uint8_t*) alloc(kw.size(), "qsa k weights");
    uint8_t* v_w = (uint8_t*) alloc(vw.size(), "qsa v weights");
    upload(q_w, qw.data(), qw.size(), "qsa q upload");
    upload(k_w, kw.data(), kw.size(), "qsa k upload");
    upload(v_w, vw.data(), vw.size(), "qsa v upload");
    float* qy[2] = {
        (float*) alloc((size_t) MAX_T * QSA_Q * sizeof(float), "qsa q A"),
        (float*) alloc((size_t) MAX_T * QSA_Q * sizeof(float), "qsa q B")};
    float* ky[2] = {
        (float*) alloc((size_t) MAX_T * KV * sizeof(float), "qsa k A"),
        (float*) alloc((size_t) MAX_T * KV * sizeof(float), "qsa k B")};
    float* vy[2] = {
        (float*) alloc((size_t) MAX_T * KV * sizeof(float), "qsa v A"),
        (float*) alloc((size_t) MAX_T * KV * sizeof(float), "qsa v B")};
    auto launch_seq = [&](int T, int arm) {
        K::native_mmvq(QSA_Q_TYPE, q_w, d.xq, qy[arm], N, QSA_Q, T, d.main);
        K::native_mmvq(QSA_K_TYPE, k_w, d.xq, ky[arm], N, KV, T, d.main);
        K::native_mmvq(QSA_V_TYPE, v_w, d.xq, vy[arm], N, KV, T, d.main);
    };
    auto launch_side = [&](int T, int arm) {
        ck(cudaEventRecord(d.fork, d.main), "qsa fork record");
        ck(cudaStreamWaitEvent(d.side, d.fork, 0), "qsa side wait");
        K::native_mmvq(QSA_Q_TYPE, q_w, d.xq, qy[arm], N, QSA_Q, T, d.side);
        K::native_mmvq(QSA_K_TYPE, k_w, d.xq, ky[arm], N, KV, T, d.main);
        K::native_mmvq(QSA_V_TYPE, v_w, d.xq, vy[arm], N, KV, T, d.main);
        ck(cudaEventRecord(d.join, d.side), "qsa join record");
        ck(cudaStreamWaitEvent(d.main, d.join, 0), "qsa main wait");
    };
    auto time_seq = [&](int T, int arm) {
        std::vector<double> us;
        for (int r = 0; r < rounds; ++r) {
            ck(cudaEventRecord(d.start, d.main), "qsa start record");
            for (int i = 0; i < iters; ++i) {
                if (arm == 0) launch_seq(T, 0);
                else launch_side(T, 1);
            }
            ck(cudaEventRecord(d.stop, d.main), "qsa stop record");
            ck(cudaEventSynchronize(d.stop), "qsa stop sync");
            float ms = 0.f;
            ck(cudaEventElapsedTime(&ms, d.start, d.stop), "qsa elapsed");
            us.push_back(1e3 * (double) ms / (double) iters);
        }
        return median(us);
    };
    int bad = 0;
    for (int T = 1; T <= MAX_T; ++T) {
        K::native_quantize_q8_1(d.x, d.xq, N, T, d.main);
        ck(cudaStreamSynchronize(d.main), "qsa quantize sync");
        for (int i = 0; i < 50; ++i) {
            launch_seq(T, 0);
            launch_side(T, 1);
        }
        launch_seq(T, 0);
        launch_side(T, 1);
        ck(cudaStreamSynchronize(d.main), "qsa check");
        auto equal = [&](float* a, float* b, size_t count) {
            std::vector<float> ha(count), hb(count);
            ck(cudaMemcpy(ha.data(), a, count * 4, cudaMemcpyDeviceToHost), "qsa A read");
            ck(cudaMemcpy(hb.data(), b, count * 4, cudaMemcpyDeviceToHost), "qsa B read");
            return std::memcmp(ha.data(), hb.data(), count * 4) == 0;
        };
        const bool qe = equal(qy[0], qy[1], (size_t) T * QSA_Q);
        const bool ke = equal(ky[0], ky[1], (size_t) T * KV);
        const bool ve = equal(vy[0], vy[1], (size_t) T * KV);
        const double a = time_seq(T, 0);
        const double b = time_seq(T, 1);
        std::printf("QSAMIX gpu=%d T=%d A=%.4f B=%.4f save=%.4f pct=%+.4f q=%s k=%s v=%s\n",
                    dev, T, a, b, a - b, 100.0 * (a - b) / a, qe ? "equal" : "DIFF",
                    ke ? "equal" : "DIFF", ve ? "equal" : "DIFF");
        std::fflush(stdout);
        if (!qe || !ke || !ve) ++bad;
    }
    cudaFree(q_w); cudaFree(k_w); cudaFree(v_w);
    for (int i = 0; i < 2; ++i) { cudaFree(qy[i]); cudaFree(ky[i]); cudaFree(vy[i]); }
    return bad;
}

int run(int iters, int rounds) {
    int n_dev = 0;
    ck(cudaGetDeviceCount(&n_dev), "device count");
    int dev = 0;
    ck(cudaGetDevice(&dev), "device");
    Device d = make_device(dev);
    int bad = 0;
    for (int T = 1; T <= MAX_T; ++T) {
        K::native_quantize_q8_1(d.x, d.xq, N, T, d.main);
        ck(cudaStreamSynchronize(d.main), "quantize sync");
        launch_a(d, T, 0);
        launch_b(d, T, 1);
        for (int i = 0; i < 50; ++i) {
            launch_a(d, T, 0);
            launch_b(d, T, 1);
        }
        ck(cudaStreamSynchronize(d.main), "warm-up");
        launch_a(d, T, 0);
        launch_b(d, T, 1);
        ck(cudaStreamSynchronize(d.main), "check");
        std::vector<float> qa((size_t) T * QKV), qb(qa.size());
        std::vector<float> ga((size_t) T * GATE), gb(ga.size());
        ck(cudaMemcpy(qa.data(), d.qkv_y[0], qa.size() * 4, cudaMemcpyDeviceToHost), "qkv A read");
        ck(cudaMemcpy(qb.data(), d.qkv_y[1], qb.size() * 4, cudaMemcpyDeviceToHost), "qkv B read");
        ck(cudaMemcpy(ga.data(), d.gate_y[0], ga.size() * 4, cudaMemcpyDeviceToHost), "gate A read");
        ck(cudaMemcpy(gb.data(), d.gate_y[1], gb.size() * 4, cudaMemcpyDeviceToHost), "gate B read");
        const bool qeq = std::memcmp(qa.data(), qb.data(), qa.size() * 4) == 0;
        const bool geq = std::memcmp(ga.data(), gb.data(), ga.size() * 4) == 0;
        const double a = time_arm(d, T, 0, iters, rounds);
        const double b = time_arm(d, T, 1, iters, rounds);
        std::printf("SIDEMIX gpu=%d T=%d A=%.4f B=%.4f save=%.4f pct=%+.4f qkv=%s gate=%s\n",
                    dev, T, a, b, a - b, 100.0 * (a - b) / a, qeq ? "equal" : "DIFF",
                    geq ? "equal" : "DIFF");
        std::fflush(stdout);
        if (!qeq || !geq) ++bad;
    }
    bad += run_qsa(d, iters, rounds);
    return bad ? 1 : 0;
}

}  // namespace

int main(int argc, char** argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);
    const int iters = argc > 1 ? std::atoi(argv[1]) : 100;
    const int rounds = argc > 2 ? std::atoi(argv[2]) : 5;
    return run(iters, rounds);
}
