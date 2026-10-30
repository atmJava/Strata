// src/kernels/head_split_parity.cu - UTIL-HEAD-SPLIT's independent GPU gate.
//
// The production native head is Q5_K with 2560 inputs and 248320 vocabulary rows.  This harness keeps the current
// full-head call as the reference, partitions only its vocabulary rows, and proves the partition is byte exact before
// any verifier/server integration.  It also measures the critical path of the three designs an integration could use:
//
//   full:        one Q5_K head call on the stage that owns the head
//   serial:      two half-row calls on that same device
//   concurrent:  one half on each RTX 2080 Ti, joined over peer access
//
// The input quantization and 2-4-column IL path match verify.cpp.  Weights and activations are synthetic, with finite
// block scales, so this is a kernel/graph/transport test rather than a model-quality benchmark.  The harness exits 77
// when the two-device peer setup it needs is unavailable.

#include "strata/kernels/native_mmvq.hpp"
#include "strata/kernels/sampler.hpp"

#include <cuda_runtime.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <random>
#include <string>
#include <vector>

namespace K = strata::kernels;

#define CK(call)                                                                                                        \
    do {                                                                                                                \
        const cudaError_t status_ = (call);                                                                             \
        if (status_ != cudaSuccess) {                                                                                   \
            std::fprintf(stderr, "%s:%d: %s: %s\n", __FILE__, __LINE__, #call, cudaGetErrorString(status_));           \
            std::exit(2);                                                                                               \
        }                                                                                                               \
    } while (0)

namespace {

constexpr int kHeadType = 13;       // Q5_K, the production native output head
constexpr int kNIn = 2560;
constexpr int kNOut = 248320;
constexpr int kN0 = kNOut / 2;
constexpr int kN1 = kNOut - kN0;
constexpr int kMaxT = 8;
constexpr int kBlockElems = 256;
constexpr int kBlockBytes = 176;
constexpr double kStage2WindowMs = 18.27;  // UTIL-WINDOW-PROFILE's fastest measured stage-2 window

struct DeviceMemory {
    float* mixed = nullptr;
    float* peer_mixed = nullptr;
    void* xq0 = nullptr;
    void* xq1 = nullptr;
    void* xil0 = nullptr;
    void* xil1 = nullptr;
    float* half0 = nullptr;
    float* half1 = nullptr;
    float* merged = nullptr;
    int* picks = nullptr;
};

struct FullMemory {
    void* weights = nullptr;
    float* mixed = nullptr;
    void* xq = nullptr;
    void* xil = nullptr;
    float* logits = nullptr;
    int* picks = nullptr;
};

struct Reference {
    int T = 0;
    std::vector<float> mixed;
    std::vector<float> logits;
    std::vector<float> changed_mixed;
    std::vector<float> changed_logits;
    std::vector<int> greedy;
    std::vector<int> sampled_c0;
    std::vector<int> sampled_c1;
};

uint16_t sane_half(std::mt19937& rng) {
    const uint32_t r = rng();
    return (uint16_t) (((r >> 31) << 15) | ((5u + (r >> 10) % 5u) << 10) | (r & 0x3ffu));
}

void fill_weights(std::vector<uint8_t>& weights) {
    uint64_t state = 0x9e3779b97f4a7c15ull;
    for (size_t i = 0; i < weights.size(); i += sizeof(state)) {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        const size_t n = std::min(sizeof(state), weights.size() - i);
        std::memcpy(weights.data() + i, &state, n);
    }
    std::mt19937 rng(77u + (unsigned) kHeadType * 31u + (unsigned) kNOut);
    const size_t n_blocks = (size_t) kNOut * (size_t) (kNIn / kBlockElems);
    for (size_t b = 0; b < n_blocks; ++b) {
        const uint16_t scale = sane_half(rng);
        const uint16_t min = sane_half(rng);
        std::memcpy(weights.data() + b * kBlockBytes, &scale, sizeof(scale));
        std::memcpy(weights.data() + b * kBlockBytes + 2, &min, sizeof(min));
    }
}

void fill_activations(std::vector<float>& values, uint32_t seed, float shift) {
    std::mt19937 rng(seed);
    std::normal_distribution<float> normal(0.0f, 1.0f);
    for (float& value : values) value = normal(rng) + shift;
}

bool same_bytes(const void* a, const void* b, size_t bytes) {
    return std::memcmp(a, b, bytes) == 0;
}

bool all_finite(const std::vector<float>& values) {
    for (const float value : values)
        if (!std::isfinite(value)) return false;
    return true;
}

bool half_matches(const std::vector<float>& full, const std::vector<float>& half, int T, int first, int width) {
    if (full.size() != (size_t) T * kNOut || half.size() != (size_t) T * (size_t) width) return false;
    for (int t = 0; t < T; ++t) {
        const float* want = full.data() + (size_t) t * kNOut + first;
        const float* got = half.data() + (size_t) t * width;
        if (!same_bytes(want, got, (size_t) width * sizeof(float))) return false;
    }
    return true;
}

K::SamplerParams sampler(uint64_t counter, bool greedy) {
    K::SamplerParams params;
    params.greedy = greedy;
    params.temperature = greedy ? 0.0f : 0.8f;
    params.top_k = 20;
    params.top_p = 0.95f;
    params.seed = 0x5eedc0deu;
    params.counter = counter;
    return params;
}

std::vector<int> sample_ids(const float* logits, int T, int* device_out, cudaStream_t stream, bool greedy,
                            uint64_t counter) {
    K::SamplerParams params = sampler(counter, greedy);
    K::sample_tokens(logits, T, kNOut, nullptr, 0, params, device_out, stream);
    CK(cudaStreamSynchronize(stream));
    std::vector<int> ids((size_t) T);
    CK(cudaMemcpy(ids.data(), device_out, ids.size() * sizeof(int), cudaMemcpyDeviceToHost));
    return ids;
}

bool use_il(int T, int n_out) {
    return T >= 2 && T <= 4 && K::native_mmvq_il_supported(kHeadType, T, n_out);
}

void launch_projection(const void* weights, const void* xq, void* xil, float* logits, int n_out, int T,
                       bool il, cudaStream_t stream) {
    if (il) {
        K::native_q8_1_interleave(xq, xil, kNIn, T, stream);
        K::native_mmvq_il(kHeadType, weights, xq, xil, logits, kNIn, n_out, T, stream);
    } else {
        K::native_mmvq(kHeadType, weights, xq, logits, kNIn, n_out, T, stream);
    }
}

__global__ void merge_halves(const float* half0, const float* half1, float* merged, int T, int n0, int n1) {
    const int width = n0 + n1;
    const size_t count = (size_t) T * width;
    for (size_t i = (size_t) blockIdx.x * blockDim.x + threadIdx.x; i < count;
         i += (size_t) blockDim.x * gridDim.x) {
        const int t = (int) (i / width);
        const int v = (int) (i % width);
        merged[i] = v < n0 ? half0[(size_t) t * n0 + v] : half1[(size_t) t * n1 + v - n0];
    }
}

void enqueue_merge(const float* half0, const float* half1, float* merged, int T, cudaStream_t stream) {
    const size_t count = (size_t) T * kNOut;
    const int block = 256;
    const int grid = (int) std::min<size_t>(4096, (count + block - 1) / block);
    merge_halves<<<grid, block, 0, stream>>>(half0, half1, merged, T, kN0, kN1);
    CK(cudaGetLastError());
}

template <class F>
double time_stream(cudaStream_t stream, int warmups, int reps, F&& launch) {
    for (int i = 0; i < warmups; ++i) launch();
    CK(cudaStreamSynchronize(stream));
    cudaEvent_t begin = nullptr, end = nullptr;
    CK(cudaEventCreate(&begin));
    CK(cudaEventCreate(&end));
    CK(cudaEventRecord(begin, stream));
    for (int i = 0; i < reps; ++i) launch();
    CK(cudaEventRecord(end, stream));
    CK(cudaEventSynchronize(end));
    float ms = 0.0f;
    CK(cudaEventElapsedTime(&ms, begin, end));
    CK(cudaEventDestroy(begin));
    CK(cudaEventDestroy(end));
    return 1000.0 * (double) ms / (double) reps;
}

cudaGraphExec_t capture_graph(cudaStream_t stream, int device, const std::function<void()>& enqueue) {
    const int previous = [] {
        int d = 0;
        CK(cudaGetDevice(&d));
        return d;
    }();
    CK(cudaSetDevice(device));
    cudaGraph_t graph = nullptr;
    cudaGraphExec_t executable = nullptr;
    CK(cudaStreamBeginCapture(stream, cudaStreamCaptureModeThreadLocal));
    enqueue();
    CK(cudaStreamEndCapture(stream, &graph));
    CK(cudaGraphInstantiate(&executable, graph, nullptr, nullptr, 0));
    CK(cudaGraphDestroy(graph));
    CK(cudaSetDevice(previous));
    return executable;
}

}  // namespace

int main(int argc, char** argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);
    int process_index = 0;
    int reps = 80;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--selftest") continue;
        if (arg == "--process" && i + 1 < argc) process_index = std::atoi(argv[++i]);
        else if (arg == "--reps" && i + 1 < argc) reps = std::max(1, std::atoi(argv[++i]));
        else {
            std::fprintf(stderr, "usage: %s [--selftest] [--process N] [--reps N]\n", argv[0]);
            return 2;
        }
    }

    int devices = 0;
    CK(cudaGetDeviceCount(&devices));
    if (devices < 2) {
        std::printf("SKIP: UTIL-HEAD-SPLIT needs two visible GPUs (%d found)\n", devices);
        return 77;
    }
    int can_01 = 0, can_10 = 0;
    CK(cudaDeviceCanAccessPeer(&can_01, 0, 1));
    CK(cudaDeviceCanAccessPeer(&can_10, 1, 0));
    if (!can_01 || !can_10) {
        std::printf("SKIP: peer access unavailable (0->1=%d, 1->0=%d)\n", can_01, can_10);
        return 77;
    }
    CK(cudaSetDevice(0));
    CK(cudaDeviceEnablePeerAccess(1, 0));
    CK(cudaSetDevice(1));
    CK(cudaDeviceEnablePeerAccess(0, 0));

    cudaStream_t stream0 = nullptr, stream1 = nullptr;
    cudaEvent_t ready = nullptr, peer_done = nullptr;
    CK(cudaSetDevice(0));
    CK(cudaStreamCreateWithFlags(&stream0, cudaStreamNonBlocking));
    CK(cudaEventCreateWithFlags(&ready, cudaEventDisableTiming));
    CK(cudaSetDevice(1));
    CK(cudaStreamCreateWithFlags(&stream1, cudaStreamNonBlocking));
    CK(cudaEventCreateWithFlags(&peer_done, cudaEventDisableTiming));

    const size_t weight_bytes = K::native_mmvq_weight_bytes(kHeadType, kNIn, kNOut);
    const size_t row_bytes = weight_bytes / (size_t) kNOut;
    const size_t weight0_bytes = (size_t) kN0 * row_bytes;
    const size_t weight1_bytes = weight_bytes - weight0_bytes;
    const size_t q8_bytes = K::native_q8_1_bytes(kNIn, kMaxT);
    const size_t il_bytes = K::native_q8_1_il_bytes(kNIn, kMaxT);
    const size_t full_logits_bytes = (size_t) kMaxT * kNOut * sizeof(float);
    const size_t peer_mixed_bytes = (size_t) kMaxT * kNIn * sizeof(float);

    std::printf("HEAD-SPLIT process=%d type=Q5_K n_in=%d n_out=%d n0=%d n1=%d weight_bytes=%zu "
                "stage2_window_us=%.1f\n",
                process_index, kNIn, kNOut, kN0, kN1, weight_bytes, kStage2WindowMs * 1000.0);

    std::vector<uint8_t> host_weights(weight_bytes);
    fill_weights(host_weights);
    std::vector<Reference> reference((size_t) kMaxT + 1);
    int failures = 0;

    // ---- one-device reference.  This is also the fallback required by the contract.
    size_t free0_before = 0, total0 = 0, free1_before = 0, total1 = 0;
    CK(cudaSetDevice(0));
    CK(cudaMemGetInfo(&free0_before, &total0));
    CK(cudaSetDevice(1));
    CK(cudaMemGetInfo(&free1_before, &total1));

    FullMemory full;
    CK(cudaSetDevice(0));
    CK(cudaMalloc(&full.weights, weight_bytes));
    CK(cudaMemcpy(full.weights, host_weights.data(), weight_bytes, cudaMemcpyHostToDevice));
    CK(cudaMalloc((void**) &full.mixed, peer_mixed_bytes));
    CK(cudaMalloc(&full.xq, q8_bytes));
    CK(cudaMalloc(&full.xil, il_bytes));
    CK(cudaMalloc((void**) &full.logits, full_logits_bytes));
    CK(cudaMalloc((void**) &full.picks, (size_t) kMaxT * sizeof(int)));

    std::vector<float> benchmark_order;
    for (int raw_t = 1; raw_t <= kMaxT; ++raw_t) {
        const int t = 1 + (raw_t - 1 + process_index) % kMaxT;
        Reference& ref = reference[(size_t) t];
        ref.T = t;
        ref.mixed.resize((size_t) t * kNIn);
        fill_activations(ref.mixed, 1000u + (uint32_t) t, 0.0f);
        CK(cudaMemcpyAsync(full.mixed, ref.mixed.data(), ref.mixed.size() * sizeof(float), cudaMemcpyHostToDevice,
                           stream0));
        K::native_quantize_q8_1(full.mixed, full.xq, kNIn, t, stream0);
        const bool il = use_il(t, kNOut);
        launch_projection(full.weights, full.xq, full.xil, full.logits, kNOut, t, il, stream0);
        CK(cudaStreamSynchronize(stream0));

        ref.logits.resize((size_t) t * kNOut);
        CK(cudaMemcpy(ref.logits.data(), full.logits, ref.logits.size() * sizeof(float), cudaMemcpyDeviceToHost));
        ref.greedy = sample_ids(full.logits, t, full.picks, stream0, true, 0);
        ref.sampled_c0 = sample_ids(full.logits, t, full.picks, stream0, false, 17);
        ref.sampled_c1 = sample_ids(full.logits, t, full.picks, stream0, false, 10017);

        // One-device fallback: poison and repeat the exact production call.
        CK(cudaMemsetAsync(full.logits, 0xa5, ref.logits.size() * sizeof(float), stream0));
        K::native_quantize_q8_1(full.mixed, full.xq, kNIn, t, stream0);
        launch_projection(full.weights, full.xq, full.xil, full.logits, kNOut, t, il, stream0);
        CK(cudaStreamSynchronize(stream0));
        std::vector<float> repeat(ref.logits.size());
        CK(cudaMemcpy(repeat.data(), full.logits, ref.logits.size() * sizeof(float), cudaMemcpyDeviceToHost));
        if (!same_bytes(repeat.data(), ref.logits.data(), ref.logits.size() * sizeof(float)) ||
            !all_finite(ref.logits)) {
            std::printf("FAIL T=%d one-device repeat\n", t);
            ++failures;
        } else {
            std::printf("PASS T=%d one-device-byte-stable finite il=%d\n", t, il ? 1 : 0);
        }

        // The graph must use the changed activation rather than replaying the captured values.
        cudaGraphExec_t full_graph = capture_graph(stream0, 0, [&] {
            K::native_quantize_q8_1(full.mixed, full.xq, kNIn, t, stream0);
            launch_projection(full.weights, full.xq, full.xil, full.logits, kNOut, t, il, stream0);
        });
        CK(cudaGraphLaunch(full_graph, stream0));
        CK(cudaStreamSynchronize(stream0));
        std::vector<float> graph_initial(ref.logits.size());
        CK(cudaMemcpy(graph_initial.data(), full.logits, graph_initial.size() * sizeof(float),
                      cudaMemcpyDeviceToHost));

        ref.changed_mixed.resize(ref.mixed.size());
        fill_activations(ref.changed_mixed, 9000u + (uint32_t) t, 0.25f);
        CK(cudaMemcpyAsync(full.mixed, ref.changed_mixed.data(), ref.changed_mixed.size() * sizeof(float),
                           cudaMemcpyHostToDevice, stream0));
        K::native_quantize_q8_1(full.mixed, full.xq, kNIn, t, stream0);
        launch_projection(full.weights, full.xq, full.xil, full.logits, kNOut, t, il, stream0);
        CK(cudaStreamSynchronize(stream0));
        ref.changed_logits.resize(ref.logits.size());
        CK(cudaMemcpy(ref.changed_logits.data(), full.logits, ref.changed_logits.size() * sizeof(float),
                      cudaMemcpyDeviceToHost));

        CK(cudaMemsetAsync(full.logits, 0xa5, ref.logits.size() * sizeof(float), stream0));
        CK(cudaGraphLaunch(full_graph, stream0));
        CK(cudaStreamSynchronize(stream0));
        std::vector<float> graph_changed(ref.logits.size());
        CK(cudaMemcpy(graph_changed.data(), full.logits, graph_changed.size() * sizeof(float),
                      cudaMemcpyDeviceToHost));
        const bool graph_ok =
            same_bytes(graph_initial.data(), ref.logits.data(), ref.logits.size() * sizeof(float)) &&
            same_bytes(graph_changed.data(), ref.changed_logits.data(), ref.changed_logits.size() * sizeof(float)) &&
            !same_bytes(graph_initial.data(), graph_changed.data(), graph_changed.size() * sizeof(float));
        std::printf("%s T=%d full-graph-changed-input\n", graph_ok ? "PASS" : "FAIL", t);
        if (!graph_ok) ++failures;
        CK(cudaGraphExecDestroy(full_graph));

        const double full_us = time_stream(stream0, 8, reps, [&] {
            K::native_quantize_q8_1(full.mixed, full.xq, kNIn, t, stream0);
            launch_projection(full.weights, full.xq, full.xil, full.logits, kNOut, t, il, stream0);
        });
        const double selection_us = time_stream(stream0, 8, reps, [&] {
            K::SamplerParams params = sampler(17, true);
            K::sample_tokens(full.logits, t, kNOut, nullptr, 0, params, full.picks, stream0);
        });
        benchmark_order.push_back((float) full_us);
        std::printf("BENCH process=%d T=%d full_us=%.3f selection_us=%.3f il=%d\n", process_index, t, full_us,
                    selection_us, il ? 1 : 0);
    }

    const size_t full_peak =
        weight_bytes + peer_mixed_bytes + q8_bytes + il_bytes + full_logits_bytes +
        (size_t) kMaxT * sizeof(int);
    std::printf("MEM process=%d full_total=%zu full_device0=%zu free0_before=%zu free1_before=%zu total0=%zu "
                "total1=%zu\n",
                process_index, full_peak, full_peak, free0_before, free1_before, total0, total1);

    CK(cudaFree(full.weights));
    CK(cudaFree(full.mixed));
    CK(cudaFree(full.xq));
    CK(cudaFree(full.xil));
    CK(cudaFree(full.logits));
    CK(cudaFree(full.picks));

    // ---- split weights and private head buffers.  Total weight bytes stay exactly the full matrix.
    DeviceMemory split;
    CK(cudaSetDevice(0));
    CK(cudaMalloc(&split.mixed, peer_mixed_bytes));
    CK(cudaMalloc(&split.xq0, q8_bytes));
    CK(cudaMalloc(&split.xil0, il_bytes));
    CK(cudaMalloc((void**) &split.half0, (size_t) kMaxT * kN0 * sizeof(float)));
    CK(cudaMalloc((void**) &split.merged, full_logits_bytes));
    CK(cudaMalloc((void**) &split.picks, (size_t) kMaxT * sizeof(int)));
    void* weights0 = nullptr;
    CK(cudaMalloc(&weights0, weight0_bytes));
    CK(cudaMemcpy(weights0, host_weights.data(), weight0_bytes, cudaMemcpyHostToDevice));
    void* weights1_local = nullptr;
    CK(cudaMalloc(&weights1_local, weight1_bytes));
    CK(cudaMemcpy(weights1_local, host_weights.data() + weight0_bytes, weight1_bytes, cudaMemcpyHostToDevice));

    CK(cudaSetDevice(1));
    CK(cudaMalloc((void**) &split.peer_mixed, peer_mixed_bytes));
    CK(cudaMalloc(&split.xq1, q8_bytes));
    CK(cudaMalloc(&split.xil1, il_bytes));
    CK(cudaMalloc((void**) &split.half1, (size_t) kMaxT * kN1 * sizeof(float)));
    void* weights1 = nullptr;
    CK(cudaMalloc(&weights1, weight1_bytes));
    CK(cudaMemcpy(weights1, host_weights.data() + weight0_bytes, weight1_bytes, cudaMemcpyHostToDevice));

    const size_t split0_peak =
        weight0_bytes + peer_mixed_bytes + q8_bytes + il_bytes +
        (size_t) kMaxT * kN0 * sizeof(float) + full_logits_bytes + (size_t) kMaxT * sizeof(int);
    const size_t split1_peak =
        weight1_bytes + peer_mixed_bytes + q8_bytes + il_bytes + (size_t) kMaxT * kN1 * sizeof(float);
    const size_t split_total = split0_peak + split1_peak;
    const size_t split_bound = full_peak + full_logits_bytes + peer_mixed_bytes + q8_bytes + il_bytes + (8u << 20);
    const bool memory_ok = split_total <= split_bound;
    std::printf("MEM process=%d split_total=%zu split_device0=%zu split_device1=%zu bound=%zu weight_equal=%d "
                "memory_gate=%s\n",
                process_index, split_total, split0_peak, split1_peak, split_bound,
                weight0_bytes + weight1_bytes == weight_bytes ? 1 : 0, memory_ok ? "PASS" : "FAIL");
    if (weight0_bytes + weight1_bytes != weight_bytes || !memory_ok) ++failures;

    auto run_concurrent = [&](const std::vector<float>& mixed, int t, bool il0, bool il1, bool copy_input,
                              bool launch_graphs, cudaGraphExec_t graph0, cudaGraphExec_t graph1) {
        CK(cudaSetDevice(0));
        if (copy_input)
            CK(cudaMemcpyAsync(split.mixed, mixed.data(), mixed.size() * sizeof(float), cudaMemcpyHostToDevice,
                               stream0));
        CK(cudaEventRecord(ready, stream0));
        CK(cudaSetDevice(1));
        CK(cudaStreamWaitEvent(stream1, ready, 0));
        CK(cudaMemcpyPeerAsync(split.peer_mixed, 1, split.mixed, 0, mixed.size() * sizeof(float), stream1));

        CK(cudaSetDevice(0));
        if (launch_graphs) {
            CK(cudaGraphLaunch(graph0, stream0));
        } else {
            K::native_quantize_q8_1(split.mixed, split.xq0, kNIn, t, stream0);
            launch_projection(weights0, split.xq0, split.xil0, split.half0, kN0, t, il0, stream0);
        }

        CK(cudaSetDevice(1));
        if (launch_graphs) {
            CK(cudaGraphLaunch(graph1, stream1));
        } else {
            K::native_quantize_q8_1(split.peer_mixed, split.xq1, kNIn, t, stream1);
            launch_projection(weights1, split.xq1, split.xil1, split.half1, kN1, t, il1, stream1);
        }
        CK(cudaEventRecord(peer_done, stream1));

        CK(cudaSetDevice(0));
        CK(cudaStreamWaitEvent(stream0, peer_done, 0));
        enqueue_merge(split.half0, split.half1, split.merged, t, stream0);
    };

    for (int raw_t = 1; raw_t <= kMaxT; ++raw_t) {
        const int t = 1 + (raw_t - 1 + process_index) % kMaxT;
        const Reference& ref = reference[(size_t) t];
        const bool il0 = use_il(t, kN0);
        const bool il1 = use_il(t, kN1);

        run_concurrent(ref.mixed, t, il0, il1, true, false, nullptr, nullptr);
        CK(cudaStreamSynchronize(stream0));
        CK(cudaStreamSynchronize(stream1));

        std::vector<float> half0((size_t) t * kN0), half1((size_t) t * kN1), merged((size_t) t * kNOut);
        CK(cudaSetDevice(0));
        CK(cudaMemcpy(half0.data(), split.half0, half0.size() * sizeof(float), cudaMemcpyDeviceToHost));
        CK(cudaSetDevice(1));
        CK(cudaMemcpy(half1.data(), split.half1, half1.size() * sizeof(float), cudaMemcpyDeviceToHost));
        CK(cudaSetDevice(0));
        CK(cudaMemcpy(merged.data(), split.merged, merged.size() * sizeof(float), cudaMemcpyDeviceToHost));
        const bool half0_ok = half_matches(ref.logits, half0, t, 0, kN0);
        const bool half1_ok = half_matches(ref.logits, half1, t, kN0, kN1);
        const bool merged_ok = same_bytes(merged.data(), ref.logits.data(), ref.logits.size() * sizeof(float));
        const bool finite_ok = all_finite(half0) && all_finite(half1) && all_finite(merged);
        std::printf("%s T=%d half0-memcmp=%s half1-memcmp=%s merged-memcmp=%s finite=%s\n",
                    half0_ok && half1_ok && merged_ok && finite_ok ? "PASS" : "FAIL", t, half0_ok ? "pass" : "FAIL",
                    half1_ok ? "pass" : "FAIL", merged_ok ? "pass" : "FAIL", finite_ok ? "pass" : "FAIL");
        if (!half0_ok || !half1_ok || !merged_ok || !finite_ok) ++failures;

        const std::vector<int> greedy = sample_ids(split.merged, t, split.picks, stream0, true, 0);
        const std::vector<int> sampled_c0 = sample_ids(split.merged, t, split.picks, stream0, false, 17);
        const std::vector<int> sampled_c1 = sample_ids(split.merged, t, split.picks, stream0, false, 10017);
        const bool sampling_ok = greedy == ref.greedy && sampled_c0 == ref.sampled_c0 &&
                                 sampled_c1 == ref.sampled_c1;
        std::printf("%s T=%d greedy/fixed-seed/counter\n", sampling_ok ? "PASS" : "FAIL", t);
        if (!sampling_ok) ++failures;

        cudaGraphExec_t graph0 = capture_graph(stream0, 0, [&] {
            K::native_quantize_q8_1(split.mixed, split.xq0, kNIn, t, stream0);
            launch_projection(weights0, split.xq0, split.xil0, split.half0, kN0, t, il0, stream0);
        });
        cudaGraphExec_t graph1 = capture_graph(stream1, 1, [&] {
            K::native_quantize_q8_1(split.peer_mixed, split.xq1, kNIn, t, stream1);
            launch_projection(weights1, split.xq1, split.xil1, split.half1, kN1, t, il1, stream1);
        });

        run_concurrent(ref.mixed, t, il0, il1, true, true, graph0, graph1);
        CK(cudaStreamSynchronize(stream0));
        CK(cudaStreamSynchronize(stream1));
        std::vector<float> graph_initial(merged.size());
        CK(cudaMemcpy(graph_initial.data(), split.merged, graph_initial.size() * sizeof(float),
                      cudaMemcpyDeviceToHost));

        run_concurrent(ref.changed_mixed, t, il0, il1, true, true, graph0, graph1);
        CK(cudaStreamSynchronize(stream0));
        CK(cudaStreamSynchronize(stream1));
        std::vector<float> graph_changed(merged.size());
        CK(cudaMemcpy(graph_changed.data(), split.merged, graph_changed.size() * sizeof(float),
                      cudaMemcpyDeviceToHost));
        const bool split_graph_ok =
            same_bytes(graph_initial.data(), ref.logits.data(), ref.logits.size() * sizeof(float)) &&
            same_bytes(graph_changed.data(), ref.changed_logits.data(), ref.changed_logits.size() * sizeof(float)) &&
            !same_bytes(graph_initial.data(), graph_changed.data(), graph_changed.size() * sizeof(float));
        std::printf("%s T=%d split-graph-changed-input\n", split_graph_ok ? "PASS" : "FAIL", t);
        if (!split_graph_ok) ++failures;
        CK(cudaSetDevice(0));
        CK(cudaGraphExecDestroy(graph0));
        CK(cudaSetDevice(1));
        CK(cudaGraphExecDestroy(graph1));

        CK(cudaSetDevice(0));
        const double serial_us = time_stream(stream0, 8, reps, [&] {
            K::native_quantize_q8_1(split.mixed, split.xq0, kNIn, t, stream0);
            launch_projection(weights0, split.xq0, split.xil0, split.half0, kN0, t, il0, stream0);
            launch_projection(weights1_local, split.xq0, split.xil0, split.half1, kN1, t, il1, stream0);
            enqueue_merge(split.half0, split.half1, split.merged, t, stream0);
        });
        const double concurrent_us = time_stream(stream0, 8, reps, [&] {
            run_concurrent(ref.mixed, t, il0, il1, false, false, nullptr, nullptr);
        });
        const double selection_us = time_stream(stream0, 8, reps, [&] {
            K::SamplerParams params = sampler(17, true);
            K::sample_tokens(split.merged, t, kNOut, nullptr, 0, params, split.picks, stream0);
        });
        const double full_us = benchmark_order[(size_t) ((t - 1 - process_index + kMaxT) % kMaxT)];
        const double saving_us = full_us - concurrent_us;
        const double projected_pct = 100.0 * saving_us / (kStage2WindowMs * 1000.0);
        std::printf("BENCH process=%d T=%d serial_us=%.3f concurrent_us=%.3f selection_us=%.3f "
                    "saving_us=%.3f projected_pct=%.4f\n",
                    process_index, t, serial_us, concurrent_us, selection_us, saving_us, projected_pct);
    }

    CK(cudaSetDevice(0));
    CK(cudaFree(weights0));
    CK(cudaFree(weights1_local));
    CK(cudaFree(split.mixed));
    CK(cudaFree(split.xq0));
    CK(cudaFree(split.xil0));
    CK(cudaFree(split.half0));
    CK(cudaFree(split.merged));
    CK(cudaFree(split.picks));
    CK(cudaSetDevice(1));
    CK(cudaFree(weights1));
    CK(cudaFree(split.peer_mixed));
    CK(cudaFree(split.xq1));
    CK(cudaFree(split.xil1));
    CK(cudaFree(split.half1));
    CK(cudaSetDevice(0));
    CK(cudaEventDestroy(ready));
    CK(cudaStreamDestroy(stream0));
    CK(cudaSetDevice(1));
    CK(cudaEventDestroy(peer_done));
    CK(cudaStreamDestroy(stream1));

    std::printf("SUMMARY process=%d failures=%d result=%s\n", process_index, failures,
                failures == 0 ? "PASS" : "FAIL");
    return failures == 0 ? 0 : 1;
}
