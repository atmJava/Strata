// qfuse_hc_bench.cpp - the production Q8 hyper-connection read with and without a fused q8_1 image.
//
//     qfuse_hc_bench parity [iters]
//     qfuse_hc_bench A|B [iters] [rounds]
//
// A is the current path: gr_up_q8_kernel followed by native_quantize_q8_1. B sets q8_mixed, so
// gr_up_q8_q32_kernel owns the complete q8_1 group and no separate quantization runs. parity compares
// every output buffer and the complete q8_1 image for T=1..8. Performance output is one line per T.
#include "strata/kernels/fused_gr.hpp"
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
constexpr int HC = 4;
constexpr int D = N * HC;
constexpr int LR = 320;
constexpr int TM = K::kFusedGrMaxT;
constexpr int Q81_BYTES = 36;

void check(cudaError_t e, const char* what) {
    if (e != cudaSuccess) {
        std::fprintf(stderr, "qfuse_hc_bench: %s: %s\n", what, cudaGetErrorString(e));
        std::exit(2);
    }
}

uint16_t bf16(float x) {
    uint32_t u = 0;
    std::memcpy(&u, &x, sizeof(u));
    u += 0x7fffu + ((u >> 16) & 1u);
    return (uint16_t) (u >> 16);
}

struct Q8 {
    std::vector<uint8_t> codes;   // Q8_0-style 34-byte blocks; the kernels read the first half and int8 data
    std::vector<float> scales;    // the same block scale as fp32, as STRATA_HC_Q8 passes separately
};

Q8 q8_of_bf16(const std::vector<uint16_t>& w) {
    Q8 out;
    const size_t blocks = w.size() / 32;
    out.codes.resize(blocks * 34);
    out.scales.resize(blocks);
    for (size_t b = 0; b < blocks; ++b) {
        float v[32], amax = 0.f;
        for (int i = 0; i < 32; ++i) {
            const uint32_t bits = (uint32_t) w[b * 32 + i] << 16;
            std::memcpy(&v[i], &bits, sizeof(float));
            amax = std::fmax(amax, std::fabs(v[i]));
        }
        const float d = amax / 127.f;
        const float id = d > 0.f ? 1.f / d : 0.f;
        const uint32_t x = [&] {
            uint32_t u = 0;
            std::memcpy(&u, &d, sizeof(u));
            return u;
        }();
        const uint32_t sign = (x >> 16) & 0x8000u;
        int32_t e = (int32_t) ((x >> 23) & 0xffu) - 127 + 15;
        const uint32_t m = x & 0x7fffffu;
        uint16_t h = 0;
        if (e <= 0) h = (uint16_t) sign;
        else if (e >= 31) h = (uint16_t) (sign | 0x7c00u);
        else h = (uint16_t) (sign | ((uint32_t) e << 10) | (m >> 13));
        uint8_t* block = out.codes.data() + b * 34;
        std::memcpy(block, &h, sizeof(h));
        for (int i = 0; i < 32; ++i) block[2 + i] = (uint8_t) (int8_t) std::nearbyint(v[i] * id);
        out.scales[b] = d;
    }
    return out;
}

struct Buffers {
    float *R = nullptr, *bo = nullptr, *inj = nullptr, *wn = nullptr, *xn = nullptr;
    float *lo = nullptr, *rs = nullptr, *inject = nullptr, *mixed = nullptr;
    uint16_t *wd = nullptr, *wu = nullptr, *wi = nullptr;
    uint8_t *qd = nullptr, *qu = nullptr, *qm = nullptr, *qout = nullptr;
    float *sd = nullptr, *su = nullptr;
    unsigned* cnt = nullptr;
};

struct Host {
    std::vector<float> R, bo, inj, wn;
    std::vector<uint16_t> wd, wu, wi;
    Q8 qd, qu;
};

struct Snapshot {
    std::vector<float> values;
    std::vector<uint8_t> q8;
};

Host make_host() {
    Host h;
    constexpr int seed = 731;
    std::mt19937 rng(seed);
    std::normal_distribution<float> nd(0.f, 1.f);
    h.R.resize((size_t) TM * D);
    h.bo.resize((size_t) TM * N);
    h.inj.resize((size_t) TM * HC);
    h.wn.resize(D);
    h.wd.resize((size_t) LR * D);
    h.wu.resize((size_t) D * LR);
    h.wi.resize((size_t) HC * D);
    for (auto& x : h.R) x = nd(rng);
    for (auto& x : h.bo) x = 0.5f * nd(rng);
    for (auto& x : h.inj) x = nd(rng);
    for (auto& x : h.wn) x = 1.f + 0.3f * nd(rng);
    for (auto& x : h.wd) x = bf16(0.02f * nd(rng));
    for (auto& x : h.wu) x = bf16(0.05f * nd(rng));
    for (auto& x : h.wi) x = bf16(0.02f * nd(rng));
    h.qd = q8_of_bf16(h.wd);
    h.qu = q8_of_bf16(h.wu);
    return h;
}

void* alloc(size_t bytes, const char* what) {
    void* p = nullptr;
    check(cudaMalloc(&p, bytes), what);
    return p;
}

void upload(void* dst, const void* src, size_t bytes, const char* what) {
    check(cudaMemcpy(dst, src, bytes, cudaMemcpyHostToDevice), what);
}

Buffers allocate_buffers(const Host& h) {
    Buffers b;
    b.R = (float*) alloc((size_t) TM * D * sizeof(float), "R");
    b.bo = (float*) alloc((size_t) TM * N * sizeof(float), "bo");
    b.inj = (float*) alloc((size_t) TM * HC * sizeof(float), "inj");
    b.wn = (float*) alloc((size_t) D * sizeof(float), "wn");
    b.xn = (float*) alloc((size_t) TM * D * sizeof(float), "xn");
    b.lo = (float*) alloc((size_t) TM * LR * sizeof(float), "lo");
    b.rs = (float*) alloc((size_t) TM * HC * sizeof(float), "rs");
    b.inject = (float*) alloc((size_t) TM * HC * sizeof(float), "inject");
    b.mixed = (float*) alloc((size_t) TM * N * sizeof(float), "mixed");
    b.wd = (uint16_t*) alloc(h.wd.size() * sizeof(uint16_t), "wd");
    b.wu = (uint16_t*) alloc(h.wu.size() * sizeof(uint16_t), "wu");
    b.wi = (uint16_t*) alloc(h.wi.size() * sizeof(uint16_t), "wi");
    b.qd = (uint8_t*) alloc(h.qd.codes.size(), "qd");
    b.qu = (uint8_t*) alloc(h.qu.codes.size(), "qu");
    b.qm = (uint8_t*) alloc((size_t) TM * (N / 32) * Q81_BYTES, "qm");
    b.qout = (uint8_t*) alloc((size_t) TM * (N / 32) * Q81_BYTES, "qout");
    b.sd = (float*) alloc(h.qd.scales.size() * sizeof(float), "sd");
    b.su = (float*) alloc(h.qu.scales.size() * sizeof(float), "su");
    b.cnt = (unsigned*) alloc((N / 32) * sizeof(unsigned), "cnt");
    upload(b.bo, h.bo.data(), h.bo.size() * sizeof(float), "bo upload");
    upload(b.inj, h.inj.data(), h.inj.size() * sizeof(float), "inj upload");
    upload(b.wn, h.wn.data(), h.wn.size() * sizeof(float), "wn upload");
    upload(b.wd, h.wd.data(), h.wd.size() * sizeof(uint16_t), "wd upload");
    upload(b.wu, h.wu.data(), h.wu.size() * sizeof(uint16_t), "wu upload");
    upload(b.wi, h.wi.data(), h.wi.size() * sizeof(uint16_t), "wi upload");
    upload(b.qd, h.qd.codes.data(), h.qd.codes.size(), "qd upload");
    upload(b.qu, h.qu.codes.data(), h.qu.codes.size(), "qu upload");
    upload(b.sd, h.qd.scales.data(), h.qd.scales.size() * sizeof(float), "sd upload");
    upload(b.su, h.qu.scales.data(), h.qu.scales.size() * sizeof(float), "su upload");
    return b;
}

void make_args(const Buffers& b, K::FusedGrArgs (&a)[TM], int T, bool fused_q8, cudaStream_t s) {
    check(cudaMemsetAsync(b.cnt, 0, (N / 32) * sizeof(unsigned), s), "counter reset");
    for (int t = 0; t < T; ++t) {
        a[t] = K::FusedGrArgs();
        a[t].R = b.R + (size_t) t * D;
        a[t].R_out = b.R + (size_t) t * D;
        a[t].apply = true;
        a[t].bo_prev = b.bo + (size_t) t * N;
        a[t].inj_prev = b.inj + (size_t) t * HC;
        a[t].w_norm = b.wn;
        a[t].w_down = b.wd;
        a[t].w_up = b.wu;
        a[t].w_inject = b.wi;
        a[t].q8_down = b.qd;
        a[t].q8_up = b.qu;
        a[t].s_down = b.sd;
        a[t].s_up = b.su;
        a[t].lo = b.lo + (size_t) t * LR;
        a[t].rs = b.rs + (size_t) t * HC;
        a[t].inject_out = b.inject + (size_t) t * HC;
        a[t].mixed = b.mixed + (size_t) t * N;
        if (fused_q8) {
            a[t].q8_mixed = b.qm + (size_t) t * (N / 32) * Q81_BYTES;
            a[t].q8_cnt = b.cnt;
        }
    }
}

void reset_inputs(const Host& h, const Buffers& b, cudaStream_t s) {
    check(cudaMemcpyAsync(b.R, h.R.data(), (size_t) TM * D * sizeof(float),
                          cudaMemcpyHostToDevice, s), "R reset");
    check(cudaMemsetAsync(b.lo, 0xff, (size_t) TM * LR * sizeof(float), s), "lo poison");
    check(cudaMemsetAsync(b.rs, 0xff, (size_t) TM * HC * sizeof(float), s), "rs poison");
    check(cudaMemsetAsync(b.inject, 0xff, (size_t) TM * HC * sizeof(float), s), "inject poison");
    check(cudaMemsetAsync(b.mixed, 0xff, (size_t) TM * N * sizeof(float), s), "mixed poison");
    check(cudaMemsetAsync(b.qm, 0x5a, (size_t) TM * (N / 32) * Q81_BYTES, s), "fused q8 poison");
    check(cudaMemsetAsync(b.qout, 0x5a, (size_t) TM * (N / 32) * Q81_BYTES, s), "q8 poison");
}

void run_arm(const Host& h, const Buffers& b, int T, bool fused_q8, cudaStream_t s) {
    K::FusedGrArgs a[TM];
    make_args(b, a, T, fused_q8, s);
    reset_inputs(h, b, s);
    const bool wrote = K::fused_gr_read_multi(a, T, b.xn, s);
    if (fused_q8) {
        if (!wrote) {
            std::fprintf(stderr, "qfuse_hc_bench: fused arm did not report its q8_1 image\n");
            std::exit(3);
        }
    } else {
        K::native_quantize_q8_1(b.mixed, b.qout, N, T, s);
    }
    check(cudaStreamSynchronize(s), "arm sync");
}

Snapshot snapshot(const Buffers& b, int T, bool fused_q8, cudaStream_t s) {
    Snapshot out;
    const size_t nf = (size_t) T * (D + LR + HC + HC + N);
    out.values.resize(nf);
    float* p = out.values.data();
    check(cudaMemcpyAsync(p, b.R, (size_t) T * D * sizeof(float), cudaMemcpyDeviceToHost, s), "R read");
    p += (size_t) T * D;
    check(cudaMemcpyAsync(p, b.lo, (size_t) T * LR * sizeof(float), cudaMemcpyDeviceToHost, s), "lo read");
    p += (size_t) T * LR;
    check(cudaMemcpyAsync(p, b.rs, (size_t) T * HC * sizeof(float), cudaMemcpyDeviceToHost, s), "rs read");
    p += (size_t) T * HC;
    check(cudaMemcpyAsync(p, b.inject, (size_t) T * HC * sizeof(float), cudaMemcpyDeviceToHost, s), "inject read");
    p += (size_t) T * HC;
    check(cudaMemcpyAsync(p, b.mixed, (size_t) T * N * sizeof(float), cudaMemcpyDeviceToHost, s), "mixed read");
    out.q8.resize((size_t) T * (N / 32) * Q81_BYTES);
    const uint8_t* src = fused_q8 ? b.qm : b.qout;
    check(cudaMemcpyAsync(out.q8.data(), src, out.q8.size(), cudaMemcpyDeviceToHost, s), "q8 read");
    check(cudaStreamSynchronize(s), "snapshot sync");
    return out;
}

Snapshot snapshot_arm(const Host& h, const Buffers& b, int T, bool fused_q8, cudaStream_t s) {
    run_arm(h, b, T, fused_q8, s);
    return snapshot(b, T, fused_q8, s);
}

unsigned long long fnv(const void* data, size_t bytes) {
    unsigned long long h = 1469598103934665603ull;
    const auto* p = (const unsigned char*) data;
    for (size_t i = 0; i < bytes; ++i) h = (h ^ p[i]) * 1099511628211ull;
    return h;
}

double median(std::vector<double> v) {
    std::sort(v.begin(), v.end());
    const size_t n = v.size();
    return n & 1 ? v[n / 2] : 0.5 * (v[n / 2 - 1] + v[n / 2]);
}

int parity(int iters) {
    const Host h = make_host();
    const Buffers b = allocate_buffers(h);
    cudaStream_t s;
    check(cudaStreamCreateWithFlags(&s, cudaStreamNonBlocking), "stream");
    int bad = 0;
    for (int T = 1; T <= TM; ++T) {
        const Snapshot a = snapshot_arm(h, b, T, false, s);
        const Snapshot f = snapshot_arm(h, b, T, true, s);
        const bool values = a.values.size() == f.values.size() &&
                            std::memcmp(a.values.data(), f.values.data(),
                                        a.values.size() * sizeof(float)) == 0;
        const bool q8 = a.q8.size() == f.q8.size() &&
                        std::memcmp(a.q8.data(), f.q8.data(), a.q8.size()) == 0;
        std::printf("PARITY T=%d values=%s q8=%s q8_shaish=%016llx\n", T,
                    values ? "equal" : "DIFF", q8 ? "equal" : "DIFF",
                    fnv(f.q8.data(), f.q8.size()));
        if (!values || !q8) ++bad;
    }
    std::printf("PARITY total T=1..%d %s\n", TM, bad ? "FAIL" : "pass");
    (void) iters;
    check(cudaStreamDestroy(s), "stream destroy");
    return bad ? 1 : 0;
}

int bench(const std::string& arm, int iters, int rounds) {
    const bool fused_q8 = arm == "B";
    if (!fused_q8 && arm != "A") {
        std::fprintf(stderr, "qfuse_hc_bench: arm must be A, B or parity\n");
        return 2;
    }
    const Host h = make_host();
    const Buffers b = allocate_buffers(h);
    cudaStream_t s = nullptr;
    check(cudaStreamCreateWithFlags(&s, cudaStreamNonBlocking), "stream");
    cudaEvent_t e0 = nullptr, e1 = nullptr;
    check(cudaEventCreate(&e0), "event start");
    check(cudaEventCreate(&e1), "event stop");
    int dev = 0;
    check(cudaGetDevice(&dev), "device");

    for (int T = 1; T <= TM; ++T) {
        K::FusedGrArgs a[TM];
        make_args(b, a, T, fused_q8, s);
        for (int i = 0; i < std::max(50, iters); ++i) {
            reset_inputs(h, b, s);
            K::fused_gr_read_multi(a, T, b.xn, s);
            if (!fused_q8) K::native_quantize_q8_1(b.mixed, b.qout, N, T, s);
        }
        check(cudaStreamSynchronize(s), "warm-up");
        std::vector<double> us;
        for (int r = 0; r < rounds; ++r) {
            check(cudaEventRecord(e0, s), "event start record");
            for (int i = 0; i < iters; ++i) {
                K::fused_gr_read_multi(a, T, b.xn, s);
                if (!fused_q8) K::native_quantize_q8_1(b.mixed, b.qout, N, T, s);
            }
            check(cudaEventRecord(e1, s), "event stop record");
            check(cudaEventSynchronize(e1), "event sync");
            float ms = 0.f;
            check(cudaEventElapsedTime(&ms, e0, e1), "event elapsed");
            us.push_back(1e3 * (double) ms / (double) iters);
        }
        const double med = median(us);
        run_arm(h, b, T, fused_q8, s);
        const Snapshot out = snapshot(b, T, fused_q8, s);
        const uint64_t vh = fnv(out.values.data(), out.values.size() * sizeof(float));
        const uint64_t qh = fnv(out.q8.data(), out.q8.size());
        std::printf("QPATH arm=%s gpu=%d T=%d us=%.3f hash=%016llx q8=%016llx values=%zu qbytes=%zu\n",
                    arm.c_str(), dev, T, med, (unsigned long long) vh, (unsigned long long) qh,
                    out.values.size(), out.q8.size());
        std::fflush(stdout);
    }
    check(cudaEventDestroy(e0), "event start destroy");
    check(cudaEventDestroy(e1), "event stop destroy");
    check(cudaStreamDestroy(s), "stream destroy");
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);
    const std::string mode = argc > 1 ? argv[1] : "parity";
    const int iters = argc > 2 ? std::atoi(argv[2]) : 200;
    const int rounds = argc > 3 ? std::atoi(argv[3]) : 5;
    if (mode == "parity") return parity(iters);
    return bench(mode, iters, rounds);
}
