// src/kernels/native_expert_parity.cpp - the production native-expert formats on CPU and GPU.
//
//   native_expert_parity --synthetic IQ3_XXS/IQ4_NL ...
//   native_expert_parity --bf16-embd
//   native_expert_parity <shard.gguf> [layer ...]
//
// The accepted expert formats are Q2_0, IQ3_XXS, IQ4_NL and Q8_0.  The production
// combinations exercised by CTest are IQ3_XXS/IQ4_NL, IQ3_XXS/Q2_0 and Q8_0/Q8_0.
#include "strata/artifact/gguf_reader.hpp"
#include "strata/kernels/cpu/native_expert.hpp"
#include "strata/kernels/iq_kernels.hpp"

#include "ggml-cpu.h"
#include "ggml.h"

#include <cuda_runtime.h>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <string>
#include <vector>

namespace cpu = strata::kernels::cpu;

namespace {
constexpr int64_t kEmbedding = 2560;
constexpr int64_t kFeedForward = 640;
constexpr int kNTokens = 3;
constexpr size_t kQ8_1Bytes = 36;

double rel(const std::vector<float>& a, const std::vector<float>& b) {
    double numerator = 0.0, denominator = 0.0;
    for (size_t i = 0; i < a.size(); ++i) {
        numerator += std::fabs((double) a[i] - b[i]);
        denominator += std::fabs((double) b[i]);
    }
    return numerator / (denominator + 1e-30);
}

void cuda_ok(cudaError_t error, const char* what) {
    if (error == cudaSuccess) return;
    std::fprintf(stderr, "native_expert_parity: %s: %s\n", what, cudaGetErrorString(error));
    std::exit(1);
}

std::vector<uint8_t> synthetic_blob(const cpu::NativeFmt& format, int seed) {
    std::vector<uint8_t> blob(format.bytes);
    std::mt19937 rng(seed);
    std::normal_distribution<float> normal(0.0f, 1.0f);
    const auto quantize = [&](int type, int64_t rows, int64_t cols, uint8_t* dst) {
        std::vector<float> weights((size_t) (rows * cols));
        for (int64_t row = 0; row < rows; ++row) {
            const float scale = 0.02f * (0.5f + (float) (row % 7) / 7.0f);
            for (int64_t col = 0; col < cols; ++col) weights[(size_t) (row * cols + col)] = scale * normal(rng);
        }
        const std::vector<float> importance((size_t) cols, 1.0f);
        ggml_quantize_chunk((ggml_type) type, weights.data(), dst, 0, rows, cols,
                            ggml_quantize_requires_imatrix((ggml_type) type) ? importance.data() : nullptr);
    };
    quantize(format.gu_type, kFeedForward, kEmbedding, blob.data());
    quantize(format.gu_type, kFeedForward, kEmbedding, blob.data() + format.up_off);
    quantize(format.d_type, kEmbedding, kFeedForward, blob.data() + format.down_off);
    return blob;
}

int check_blob(const cpu::NativeFmt& format, const std::vector<uint8_t>& blob, const std::string& label,
               cudaStream_t stream) {
    int failures = 0;
    const auto* gu_traits = ggml_get_type_traits((ggml_type) format.gu_type);
    const auto* down_traits = ggml_get_type_traits((ggml_type) format.d_type);

    std::vector<float> gate((size_t) (kFeedForward * kEmbedding));
    std::vector<float> up((size_t) (kFeedForward * kEmbedding));
    std::vector<float> down((size_t) (kEmbedding * kFeedForward));
    for (int64_t row = 0; row < kFeedForward; ++row) {
        gu_traits->to_float(blob.data() + (size_t) row * format.gu_row, gate.data() + row * kEmbedding, kEmbedding);
        gu_traits->to_float(blob.data() + format.up_off + (size_t) row * format.gu_row,
                            up.data() + row * kEmbedding, kEmbedding);
    }
    for (int64_t row = 0; row < kEmbedding; ++row) {
        down_traits->to_float(blob.data() + format.down_off + (size_t) row * format.d_row,
                              down.data() + row * kFeedForward, kFeedForward);
    }

    std::mt19937 rng(11 + (int) label.size());
    std::normal_distribution<float> normal(0.0f, 1.0f);
    std::vector<float> x((size_t) (kNTokens * kEmbedding));
    for (float& value : x) value = normal(rng);

    std::vector<float> reference((size_t) (kNTokens * kEmbedding));
    for (int token = 0; token < kNTokens; ++token) {
        std::vector<float> hidden((size_t) kFeedForward);
        for (int64_t row = 0; row < kFeedForward; ++row) {
            double gate_sum = 0.0, up_sum = 0.0;
            for (int64_t col = 0; col < kEmbedding; ++col) {
                const float value = x[(size_t) (token * kEmbedding + col)];
                gate_sum += (double) gate[(size_t) (row * kEmbedding + col)] * value;
                up_sum += (double) up[(size_t) (row * kEmbedding + col)] * value;
            }
            hidden[(size_t) row] = (float) (gate_sum / (1.0 + std::exp(-gate_sum)) * up_sum);
        }
        for (int64_t row = 0; row < kEmbedding; ++row) {
            double sum = 0.0;
            for (int64_t col = 0; col < kFeedForward; ++col) {
                sum += (double) down[(size_t) (row * kFeedForward + col)] * hidden[(size_t) col];
            }
            reference[(size_t) (token * kEmbedding + row)] = (float) sum;
        }
    }

    std::vector<std::vector<uint8_t>> acts((size_t) kNTokens, std::vector<uint8_t>(cpu::kNativeActBytes));
    std::vector<std::vector<uint8_t>> hidden_q((size_t) kNTokens, std::vector<uint8_t>(cpu::kNativeHBytes));
    std::vector<std::vector<float>> hidden((size_t) kNTokens, std::vector<float>((size_t) kFeedForward));
    std::vector<float> cpu_out((size_t) (kNTokens * kEmbedding));
    const void* act_ptrs[kNTokens];
    const void* hidden_ptrs[kNTokens];
    float* hidden_out[kNTokens];
    float* cpu_ptrs[kNTokens];
    for (int token = 0; token < kNTokens; ++token) {
        cpu::native_quant_act(format, x.data() + token * kEmbedding, acts[(size_t) token].data());
        act_ptrs[token] = acts[(size_t) token].data();
        hidden_out[token] = hidden[(size_t) token].data();
        cpu_ptrs[token] = cpu_out.data() + token * kEmbedding;
    }
    cpu::native_gu_rows(format, blob.data(), act_ptrs, kNTokens, hidden_out, 0, (int) kFeedForward);
    for (int token = 0; token < kNTokens; ++token) {
        cpu::native_quant_h(format, hidden[(size_t) token].data(), hidden_q[(size_t) token].data());
        hidden_ptrs[token] = hidden_q[(size_t) token].data();
    }
    cpu::native_down_rows(format, blob.data(), hidden_ptrs, kNTokens, cpu_ptrs, 0, (int) kEmbedding);

    const auto layout = strata::kernels::native_expert_layout(format.gu_type, format.d_type, kEmbedding, kFeedForward);
    void* device_blob = nullptr;
    void* device_x = nullptr;
    void* device_xq = nullptr;
    void* device_scratch = nullptr;
    float* device_out = nullptr;
    unsigned long long* device_ptr = nullptr;
    int32_t* device_start = nullptr;
    int32_t* device_groups = nullptr;
    int32_t* device_dst = nullptr;
    int32_t* device_tok = nullptr;
    cuda_ok(cudaMalloc(&device_blob, blob.size()), "cudaMalloc(blob)");
    cuda_ok(cudaMalloc(&device_x, x.size() * sizeof(float)), "cudaMalloc(x)");
    cuda_ok(cudaMalloc(&device_xq, (size_t) kNTokens * kEmbedding / 32 * kQ8_1Bytes), "cudaMalloc(xq)");
    cuda_ok(cudaMalloc(&device_scratch,
                       strata::kernels::native_expert_scratch_bytes(kNTokens, kFeedForward)), "cudaMalloc(scratch)");
    cuda_ok(cudaMalloc((void**) &device_out, (size_t) kNTokens * kEmbedding * sizeof(float)), "cudaMalloc(out)");
    cuda_ok(cudaMalloc((void**) &device_ptr, sizeof(unsigned long long)), "cudaMalloc(ptr)");
    cuda_ok(cudaMalloc((void**) &device_start, 2 * sizeof(int32_t)), "cudaMalloc(start)");
    cuda_ok(cudaMalloc((void**) &device_groups, sizeof(int32_t)), "cudaMalloc(groups)");
    cuda_ok(cudaMalloc((void**) &device_dst, kNTokens * sizeof(int32_t)), "cudaMalloc(dst)");
    cuda_ok(cudaMalloc((void**) &device_tok, kNTokens * sizeof(int32_t)), "cudaMalloc(tok)");
    cuda_ok(cudaMemcpy(device_blob, blob.data(), blob.size(), cudaMemcpyHostToDevice), "copy blob");
    cuda_ok(cudaMemcpy(device_x, x.data(), x.size() * sizeof(float), cudaMemcpyHostToDevice), "copy x");
    const unsigned long long pointer = (unsigned long long) device_blob;
    const int32_t start[2] = {0, kNTokens};
    const int32_t one = 1;
    const int32_t ids[kNTokens] = {0, 1, 2};
    cuda_ok(cudaMemcpy(device_ptr, &pointer, sizeof(pointer), cudaMemcpyHostToDevice), "copy ptr");
    cuda_ok(cudaMemcpy(device_start, start, sizeof(start), cudaMemcpyHostToDevice), "copy start");
    cuda_ok(cudaMemcpy(device_groups, &one, sizeof(one), cudaMemcpyHostToDevice), "copy groups");
    cuda_ok(cudaMemcpy(device_dst, ids, sizeof(ids), cudaMemcpyHostToDevice), "copy dst");
    cuda_ok(cudaMemcpy(device_tok, ids, sizeof(ids), cudaMemcpyHostToDevice), "copy tok");

    strata::kernels::quantize_q8_1_rows((const float*) device_x, kNTokens, kEmbedding, device_xq, stream);
    strata::kernels::native_expert_grouped(layout, device_ptr, device_start, device_groups, device_dst, device_tok, 1,
                                           kNTokens, device_xq, device_scratch, device_out, stream);
    cuda_ok(cudaStreamSynchronize(stream), "stream synchronize");
    std::vector<float> gpu_out(cpu_out.size());
    cuda_ok(cudaMemcpy(gpu_out.data(), device_out, gpu_out.size() * sizeof(float), cudaMemcpyDeviceToHost), "copy out");

    cudaFree(device_blob);
    cudaFree(device_x);
    cudaFree(device_xq);
    cudaFree(device_scratch);
    cudaFree(device_out);
    cudaFree(device_ptr);
    cudaFree(device_start);
    cudaFree(device_groups);
    cudaFree(device_dst);
    cudaFree(device_tok);

    const double cpu_error = rel(cpu_out, reference);
    const double gpu_error = rel(gpu_out, reference);
    const double cpu_gpu_error = rel(cpu_out, gpu_out);
    const bool ok = std::isfinite(cpu_error) && std::isfinite(gpu_error) && cpu_error < 3e-2 && gpu_error < 3e-2;
    std::printf("%-12s %-8s/%-7s  cpu %.2e  gpu %.2e  cpu-gpu %.2e  %s\n", label.c_str(),
                ggml_type_name((ggml_type) format.gu_type), ggml_type_name((ggml_type) format.d_type), cpu_error,
                gpu_error, cpu_gpu_error, ok ? "ok" : "FAIL");
    if (!ok) ++failures;
    return failures;
}

int type_of(std::string name) {
    for (char& c : name) c = (char) std::tolower((unsigned char) c);
    for (int type = 0; type < GGML_TYPE_COUNT; ++type) {
        const char* text = ggml_type_name((ggml_type) type);
        if (text == nullptr) continue;
        std::string candidate(text);
        for (char& c : candidate) c = (char) std::tolower((unsigned char) c);
        if (candidate == name) return type;
    }
    return -1;
}

int check_bf16_embedding(cudaStream_t stream) {
    constexpr int kBf16 = 30;
    constexpr int64_t kRows = 61, kTokens = 97;
    if (!strata::kernels::embed_type_supported(kBf16) || strata::kernels::iq_supported(kBf16) ||
        strata::kernels::iq_row_bytes(kBf16, kEmbedding) != (size_t) kEmbedding * 2) {
        std::printf("bf16 embedding: support or row bytes are wrong\n");
        return 1;
    }
    std::mt19937 rng(290);
    std::vector<uint16_t> table((size_t) (kRows * kEmbedding));
    for (uint16_t& value : table) {
        do value = (uint16_t) (rng() & 0xffff); while ((value & 0x7f80) == 0x7f80 && (value & 0x7f));
    }
    std::vector<int32_t> tokens((size_t) kTokens);
    for (int32_t& token : tokens) token = (int32_t) (rng() % kRows);

    void* device_table = nullptr;
    int32_t* device_tokens = nullptr;
    float* device_out = nullptr;
    cuda_ok(cudaMalloc(&device_table, table.size() * sizeof(uint16_t)), "cudaMalloc(table)");
    cuda_ok(cudaMalloc((void**) &device_tokens, tokens.size() * sizeof(int32_t)), "cudaMalloc(tokens)");
    cuda_ok(cudaMalloc((void**) &device_out, (size_t) kTokens * kEmbedding * sizeof(float)), "cudaMalloc(embd)");
    cuda_ok(cudaMemcpy(device_table, table.data(), table.size() * sizeof(uint16_t), cudaMemcpyHostToDevice), "copy table");
    cuda_ok(cudaMemcpy(device_tokens, tokens.data(), tokens.size() * sizeof(int32_t), cudaMemcpyHostToDevice), "copy tokens");
    strata::kernels::iq_embed_rows(kBf16, device_table, (size_t) kEmbedding * 2, device_tokens, kTokens, kEmbedding,
                                   device_out, stream);
    cuda_ok(cudaStreamSynchronize(stream), "bf16 synchronize");
    std::vector<float> output((size_t) (kTokens * kEmbedding));
    cuda_ok(cudaMemcpy(output.data(), device_out, output.size() * sizeof(float), cudaMemcpyDeviceToHost), "copy embd");
    cudaFree(device_table);
    cudaFree(device_tokens);
    cudaFree(device_out);

    size_t different = 0;
    for (size_t row = 0; row < (size_t) kTokens; ++row) {
        const uint16_t* source = table.data() + (size_t) tokens[row] * kEmbedding;
        const float* got = output.data() + row * kEmbedding;
        for (int64_t col = 0; col < kEmbedding; ++col) {
            const uint32_t bits = (uint32_t) source[col] << 16;
            float want;
            std::memcpy(&want, &bits, sizeof(want));
            different += std::memcmp(&got[col], &want, sizeof(float)) != 0;
        }
    }
    std::printf("bf16 embedding: %zu of %lld values differ\n", different, (long long) (kTokens * kEmbedding));
    return different == 0 ? 0 : 1;
}
}  // namespace

int main(int argc, char** argv) {
    setvbuf(stdout, nullptr, _IONBF, 0);
    if (argc < 2) {
        std::fprintf(stderr,
                     "usage: native_expert_parity --synthetic GU/DOWN\n"
                     "       native_expert_parity --bf16-embd\n"
                     "       native_expert_parity <shard.gguf> [layer ...]\n");
        return 2;
    }
    if (std::getenv("STRATA_IQ_MT_MIN") == nullptr) setenv("STRATA_IQ_MT_MIN", "1", 1);

    cudaStream_t stream;
    cuda_ok(cudaStreamCreate(&stream), "cudaStreamCreate");
    int failures = 0;
    const std::string mode = argv[1];
    if (mode == "--bf16-embd") {
        failures += check_bf16_embedding(stream);
    } else if (mode == "--synthetic") {
        for (int arg = 2; arg < argc; ++arg) {
            const std::string pair = argv[arg];
            const size_t slash = pair.find('/');
            const int gu = slash == std::string::npos ? -1 : type_of(pair.substr(0, slash));
            const int down = slash == std::string::npos ? -1 : type_of(pair.substr(slash + 1));
            cpu::NativeFmt format;
            std::string error;
            if (gu < 0 || down < 0 ||
                !cpu::native_fmt(gu, down, kEmbedding, kFeedForward, format, error) ||
                !strata::kernels::native_expert_supported(gu, down, kEmbedding, kFeedForward)) {
                std::printf("%s: %s\n", pair.c_str(), error.c_str());
                ++failures;
                continue;
            }
            failures += check_blob(format, synthetic_blob(format, arg), pair, stream);
        }
    } else {
        const strata::GgufModel model(strata::gguf_split_paths(argv[1]));
        std::vector<int> layers;
        for (int arg = 2; arg < argc; ++arg) layers.push_back(std::atoi(argv[arg]));
        if (layers.empty()) layers = {0, 1, 2, 3};
        for (int layer : layers) {
            const char* roles[3] = {"gate", "up", "down"};
            const strata::TensorInfo* tensors[3] = {};
            const uint8_t* data[3] = {};
            size_t shards[3] = {};
            for (int role = 0; role < 3; ++role) {
                const std::string name =
                    "blk." + std::to_string(layer) + ".ffn_" + roles[role] + "_exps.weight";
                tensors[role] = model.find(name, &shards[role]);
                if (tensors[role] != nullptr) {
                    data[role] = model.shard(shards[role]).tensor_data(*tensors[role]);
                }
            }
            if (tensors[0] == nullptr || tensors[1] == nullptr || tensors[2] == nullptr) {
                std::printf("layer %d: missing expert tensors\n", layer);
                ++failures;
                continue;
            }
            cpu::NativeFmt format;
            std::string error;
            if (!cpu::native_fmt((int) tensors[0]->type, (int) tensors[2]->type, kEmbedding, kFeedForward, format,
                                 error)) {
                std::printf("layer %d: %s\n", layer, error.c_str());
                ++failures;
                continue;
            }
            std::vector<uint8_t> blob(format.bytes);
            std::memcpy(blob.data(), data[0], format.up_off);
            std::memcpy(blob.data() + format.up_off, data[1], format.up_off);
            std::memcpy(blob.data() + format.down_off, data[2], format.bytes - format.down_off);
            failures += check_blob(format, blob, "layer " + std::to_string(layer), stream);
        }
    }
    cudaStreamDestroy(stream);
    std::printf("native_expert_parity: %d failures\n", failures);
    return failures == 0 ? 0 : 1;
}
