// tests/cuda/mmq_expert_parity.cpp - 校验新增 IQ2/IQ3 gate/up 格式在 prompt MMQ 全链路中的数值正确性。
//
// 每个用例使用 ggml 自身的量化器生成合法权重，依次执行 q8_1 激活量化、gate/up MMQ、SwiGLU、
// H 量化、down MMQ，并与反量化权重上的双精度参考比较。该测试直接覆盖模板实例、支持判断和运行时派发，
// 不依赖 fused expert 内核是否支持同一格式。

#include "strata/prefill/moe_mmq.hpp"

#include "ggml.h"

#include <cuda_runtime.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

namespace mmq = strata::prefill::mmq;

constexpr int kEmbedding = 2560;
constexpr int kFeedForward = 640;
constexpr int kRows = 17;
constexpr int kGuRows = 2 * kFeedForward;
constexpr size_t kMmqTailBytes = 4096;

/**
 * 在失败时抛出带 CUDA 错误文本的运行时异常。
 *
 * @param error CUDA 调用返回值。
 * @param what 调用名称，用于定位失败步骤。
 * @throws std::runtime_error CUDA 调用失败时抛出。
 */
void cuda_ok(cudaError_t error, const char* what) {
    if (error == cudaSuccess) return;
    throw std::runtime_error(std::string(what) + ": " + cudaGetErrorString(error));
}

/**
 * 持有测试期间的设备内存，析构时释放。
 */
class DeviceBuffer {
public:
    /**
     * 分配指定字节数的设备内存。
     *
     * @param bytes 需要分配的字节数。
     * @throws std::runtime_error 分配失败时抛出。
     */
    explicit DeviceBuffer(size_t bytes) {
        cuda_ok(cudaMalloc(&data_, bytes), "cudaMalloc");
    }

    ~DeviceBuffer() {
        if (data_ != nullptr) cudaFree(data_);
    }

    DeviceBuffer(const DeviceBuffer&) = delete;
    DeviceBuffer& operator=(const DeviceBuffer&) = delete;

    /**
     * 返回底层设备指针。
     *
     * @return 设备内存起始地址。
     */
    void* data() const noexcept { return data_; }

    /**
     * 以指定类型返回底层设备指针。
     *
     * @tparam T 目标元素类型。
     * @return 同一设备地址的类型化指针。
     */
    template <typename T>
    T* as() const noexcept {
        return static_cast<T*>(data_);
    }

private:
    void* data_ = nullptr;
};

/**
 * 生成并量化一个测试矩阵。
 *
 * @param type 目标 GGML 权重格式。
 * @param rows 矩阵行数。
 * @param cols 矩阵列数。
 * @param rng 随机数生成器。
 * @return 按 GGML 行布局排列的量化矩阵。
 * @throws std::runtime_error 行宽不匹配或量化器不可用时抛出。
 */
std::vector<uint8_t> make_quantized_matrix(ggml_type type, int rows, int cols, std::mt19937& rng) {
    const size_t expected = mmq::matrix_bytes((int) type, rows, cols);
    if (ggml_row_size(type, cols) * (size_t) rows != expected) {
        throw std::runtime_error(std::string(ggml_type_name(type)) + ": unexpected row size");
    }

    std::vector<float> values((size_t) rows * cols);
    std::normal_distribution<float> normal(0.0f, 1.0f);
    for (int row = 0; row < rows; ++row) {
        const float scale = 0.02f * (0.5f + (float) (row % 7) / 7.0f);
        for (int col = 0; col < cols; ++col) {
            values[(size_t) row * cols + col] = scale * normal(rng);
        }
    }

    std::vector<float> importance((size_t) cols, 1.0f);
    std::vector<uint8_t> quantized(expected);
    const size_t written = ggml_quantize_chunk(
        type, values.data(), quantized.data(), 0, rows, cols,
        ggml_quantize_requires_imatrix(type) ? importance.data() : nullptr);
    if (written != expected) {
        throw std::runtime_error(std::string(ggml_type_name(type)) + ": quantizer wrote an unexpected byte count");
    }
    return quantized;
}

/**
 * 使用 GGML 的权重反量化器恢复测试矩阵的浮点值。
 *
 * @param type GGML 权重格式。
 * @param data 量化矩阵起始地址。
 * @param rows 矩阵行数。
 * @param cols 矩阵列数。
 * @return 行优先的浮点矩阵。
 */
std::vector<float> dequantize_matrix(ggml_type type, const std::vector<uint8_t>& data, int rows, int cols) {
    const auto* traits = ggml_get_type_traits(type);
    const size_t row_bytes = ggml_row_size(type, cols);
    std::vector<float> values((size_t) rows * cols);
    for (int row = 0; row < rows; ++row) {
        traits->to_float(data.data() + (size_t) row * row_bytes, values.data() + (size_t) row * cols, cols);
    }
    return values;
}

/**
 * 从设备复制浮点缓冲区到主机。
 *
 * @param source 设备缓冲区。
 * @param count 浮点元素数量。
 * @return 主机端副本。
 */
std::vector<float> download(const DeviceBuffer& source, size_t count) {
    std::vector<float> values(count);
    cuda_ok(cudaMemcpy(values.data(), source.data(), count * sizeof(float), cudaMemcpyDeviceToHost), "download");
    return values;
}

/**
 * 计算相对 RMS 误差与最差行的相对最大误差。
 *
 * @param got GPU 输出。
 * @param reference 双精度路径得到的参考输出。
 * @param rows 输出行数。
 * @param cols 每行元素数。
 * @return 依次为相对 RMS 误差和最差行相对误差。
 */
std::pair<double, double> relative_error(const std::vector<float>& got, const std::vector<float>& reference,
                                         int rows, int cols) {
    double squared_error = 0.0;
    double squared_reference = 0.0;
    double worst = 0.0;
    for (int row = 0; row < rows; ++row) {
        double row_max_error = 0.0;
        double row_max_reference = 0.0;
        for (int col = 0; col < cols; ++col) {
            const size_t index = (size_t) row * cols + col;
            if (!std::isfinite(got[index]) || !std::isfinite(reference[index])) {
                return {INFINITY, INFINITY};
            }
            const double difference = (double) got[index] - reference[index];
            squared_error += difference * difference;
            squared_reference += (double) reference[index] * reference[index];
            row_max_error = std::max(row_max_error, std::fabs(difference));
            row_max_reference = std::max(row_max_reference, (double) std::fabs(reference[index]));
        }
        if (row_max_reference > 0.0) {
            worst = std::max(worst, row_max_error / row_max_reference);
        } else if (row_max_error > 0.0) {
            return {INFINITY, INFINITY};
        }
    }
    const double rms = squared_reference > 0.0 ? std::sqrt(squared_error / squared_reference) : 0.0;
    return {rms, worst};
}

/**
 * 计算双精度 gate/up、SwiGLU 与 down 参考输出。
 *
 * @param x 输入激活，形状为 [kRows, kEmbedding]。
 * @param gu 反量化后的 gate/up 权重，形状为 [2*kFeedForward, kEmbedding]。
 * @param down 反量化后的 down 权重，形状为 [kEmbedding, kFeedForward]。
 * @return 输出，形状为 [kRows, kEmbedding]。
 */
std::vector<float> reference_output(const std::vector<float>& x, const std::vector<float>& gu,
                                    const std::vector<float>& down) {
    std::vector<float> output((size_t) kRows * kEmbedding);
    std::vector<float> hidden((size_t) kFeedForward);
    for (int row = 0; row < kRows; ++row) {
        const float* activation = x.data() + (size_t) row * kEmbedding;
        for (int feature = 0; feature < kFeedForward; ++feature) {
            double gate = 0.0;
            double up = 0.0;
            const float* gate_row = gu.data() + (size_t) feature * kEmbedding;
            const float* up_row = gu.data() + (size_t) (kFeedForward + feature) * kEmbedding;
            for (int col = 0; col < kEmbedding; ++col) {
                gate += (double) gate_row[col] * activation[col];
                up += (double) up_row[col] * activation[col];
            }
            hidden[(size_t) feature] = (float) (gate / (1.0 + std::exp(-gate)) * up);
        }
        for (int out = 0; out < kEmbedding; ++out) {
            double sum = 0.0;
            const float* down_row = down.data() + (size_t) out * kFeedForward;
            for (int feature = 0; feature < kFeedForward; ++feature) {
                sum += (double) down_row[feature] * hidden[(size_t) feature];
            }
            output[(size_t) row * kEmbedding + out] = (float) sum;
        }
    }
    return output;
}

/**
 * 运行一个 gate/up 格式与 IQ4_NL down 的 MMQ 全链路。
 *
 * @param type gate/up 的 GGML 权重格式。
 * @return 0 表示通过，1 表示数值校验失败。
 * @throws std::runtime_error 资源、CUDA 或格式配置失败时抛出。
 */
int run_case(ggml_type type) {
    if (!mmq::supported((int) type) || !mmq::fits((int) type, kGuRows) ||
        !mmq::supported(GGML_TYPE_IQ4_NL) || !mmq::fits(GGML_TYPE_IQ4_NL, kEmbedding)) {
        std::fprintf(stderr, "%s: MMQ support check failed\n", ggml_type_name(type));
        return 1;
    }

    std::mt19937 rng(20261029u + (unsigned) type * 131u);
    const std::vector<uint8_t> gu_host = make_quantized_matrix(type, kGuRows, kEmbedding, rng);
    const std::vector<uint8_t> down_host = make_quantized_matrix(GGML_TYPE_IQ4_NL, kEmbedding, kFeedForward, rng);

    std::vector<float> x((size_t) kRows * kEmbedding);
    std::normal_distribution<float> normal(0.0f, 1.0f);
    for (float& value : x) value = normal(rng);

    const size_t gu_bytes = mmq::matrix_bytes((int) type, kGuRows, kEmbedding);
    const size_t down_bytes = mmq::matrix_bytes((int) GGML_TYPE_IQ4_NL, kEmbedding, kFeedForward);
    DeviceBuffer gu_device(gu_bytes + kMmqTailBytes);
    DeviceBuffer down_device(down_bytes + kMmqTailBytes);
    DeviceBuffer x_device(x.size() * sizeof(float));
    DeviceBuffer xq_device(mmq::q8_bytes(kRows, kEmbedding));
    DeviceBuffer gu_out_device((size_t) kRows * kGuRows * sizeof(float));
    DeviceBuffer hidden_device((size_t) kRows * kFeedForward * sizeof(float));
    DeviceBuffer hidden_q_device(mmq::q8_bytes(kRows, kFeedForward));
    DeviceBuffer output_device((size_t) kRows * kEmbedding * sizeof(float));
    DeviceBuffer ids_device((size_t) kRows * sizeof(int32_t));
    DeviceBuffer bounds_device(2 * sizeof(int32_t));

    cuda_ok(cudaMemcpy(gu_device.data(), gu_host.data(), gu_bytes, cudaMemcpyHostToDevice), "copy gate/up");
    cuda_ok(cudaMemcpy(down_device.data(), down_host.data(), down_bytes, cudaMemcpyHostToDevice), "copy down");
    cuda_ok(cudaMemset(gu_device.as<uint8_t>() + gu_bytes, 0, kMmqTailBytes), "clear gate/up tail");
    cuda_ok(cudaMemset(down_device.as<uint8_t>() + down_bytes, 0, kMmqTailBytes), "clear down tail");
    cuda_ok(cudaMemcpy(x_device.data(), x.data(), x.size() * sizeof(float), cudaMemcpyHostToDevice), "copy x");
    const int32_t bounds[2] = {0, kRows};
    cuda_ok(cudaMemcpy(bounds_device.data(), bounds, sizeof(bounds), cudaMemcpyHostToDevice), "copy bounds");

    mmq::iota(ids_device.as<int32_t>(), kRows, nullptr);
    mmq::quantize(x_device.as<float>(), nullptr, xq_device.data(), (int) type, kEmbedding, kEmbedding, kRows, nullptr);

    mmq::Context context;
    mmq::Product gate_up;
    gate_up.w = gu_device.data();
    gate_up.type = (int) type;
    gate_up.w_rows = kGuRows;
    gate_up.w_cols = kEmbedding;
    gate_up.expert_bytes = gu_bytes;
    gate_up.n = 1;
    gate_up.xq = xq_device.data();
    gate_up.bounds = bounds_device.as<int32_t>();
    gate_up.ids = ids_device.as<int32_t>();
    gate_up.total_rows = kRows;
    gate_up.max_rows = kRows;
    gate_up.dst = gu_out_device.as<float>();
    gate_up.ld_dst = kGuRows;
    context.run(gate_up, nullptr);
    cuda_ok(cudaDeviceSynchronize(), "gate/up synchronize");

    mmq::swiglu(gu_out_device.as<float>(), hidden_device.as<float>(), kRows, kFeedForward, false, nullptr);
    mmq::quantize(hidden_device.as<float>(), nullptr, hidden_q_device.data(), (int) GGML_TYPE_IQ4_NL, kFeedForward,
                  kFeedForward, kRows, nullptr);
    // 字节级校验融合路径：MMQ 只消费 Hq，所以 swiglu_quantize 必须与原 SwiGLU 写 H + quantize 读 H
    // 产生完全相同的 q8_1 字节。两种 SwiGLU 布局都覆盖，避免 Strata pack 的 interleaved 路径漏测。
    for (const bool interleaved : {false, true}) {
        mmq::swiglu(gu_out_device.as<float>(), hidden_device.as<float>(), kRows, kFeedForward, interleaved, nullptr);
        mmq::quantize(hidden_device.as<float>(), nullptr, hidden_q_device.data(), (int) GGML_TYPE_IQ4_NL, kFeedForward,
                      kFeedForward, kRows, nullptr);
        cuda_ok(cudaDeviceSynchronize(), "reference hidden quantize");
        // q8_bytes includes a fixed tail for MMQ's over-read; compare only the payload both paths are required to write.
        const size_t payload = mmq::q8_bytes(kRows, kFeedForward) - mmq::q8_bytes(0, kFeedForward);
        std::vector<uint8_t> expected_q(payload);
        cuda_ok(cudaMemcpy(expected_q.data(), hidden_q_device.data(), expected_q.size(), cudaMemcpyDeviceToHost),
                "download reference hidden q8_1");

        DeviceBuffer fused_hidden_q_device(mmq::q8_bytes(kRows, kFeedForward));
        cuda_ok(cudaMemset(fused_hidden_q_device.data(), 0xa5, mmq::q8_bytes(kRows, kFeedForward)),
                "sentinel fused hidden q8_1");
        mmq::swiglu_quantize(gu_out_device.as<float>(), fused_hidden_q_device.data(), kRows, kFeedForward, interleaved,
                             (int) GGML_TYPE_IQ4_NL, nullptr);
        cuda_ok(cudaDeviceSynchronize(), "fused hidden quantize");
        std::vector<uint8_t> actual_q(expected_q.size());
        cuda_ok(cudaMemcpy(actual_q.data(), fused_hidden_q_device.data(), actual_q.size(), cudaMemcpyDeviceToHost),
                "download fused hidden q8_1");
        if (actual_q != expected_q) {
            const auto mismatch = std::mismatch(actual_q.begin(), actual_q.end(), expected_q.begin());
            throw std::runtime_error("swiglu_quantize differs from swiglu + quantize at byte " +
                                     std::to_string(mismatch.first - actual_q.begin()) + " (interleaved=" +
                                     (interleaved ? "true" : "false") + ")");
        }
    }
    // Down MMQ continues with the split-layout q8_1 bytes, as in production native packs.
    mmq::swiglu_quantize(gu_out_device.as<float>(), hidden_q_device.data(), kRows, kFeedForward, false,
                         (int) GGML_TYPE_IQ4_NL, nullptr);
    cuda_ok(cudaDeviceSynchronize(), "production-layout fused hidden quantize");

    mmq::Product down;
    down.w = down_device.data();
    down.type = (int) GGML_TYPE_IQ4_NL;
    down.w_rows = kEmbedding;
    down.w_cols = kFeedForward;
    down.expert_bytes = down_bytes;
    down.n = 1;
    down.xq = hidden_q_device.data();
    down.bounds = bounds_device.as<int32_t>();
    down.ids = ids_device.as<int32_t>();
    down.total_rows = kRows;
    down.max_rows = kRows;
    down.dst = output_device.as<float>();
    down.ld_dst = kEmbedding;
    cuda_ok(cudaMemset(output_device.data(), 0, (size_t) kRows * kEmbedding * sizeof(float)),
            "clear down output");
    context.run(down, nullptr);
    cuda_ok(cudaDeviceSynchronize(), "MMQ synchronize");

    const std::vector<float> gate_up_debug = download(gu_out_device, (size_t) kRows * kGuRows);
    const std::vector<float> hidden_debug = download(hidden_device, (size_t) kRows * kFeedForward);
    const std::vector<float> gu_float = dequantize_matrix(type, gu_host, kGuRows, kEmbedding);
    const std::vector<float> down_float = dequantize_matrix(GGML_TYPE_IQ4_NL, down_host, kEmbedding, kFeedForward);
    const std::vector<float> expected = reference_output(x, gu_float, down_float);
    const std::vector<float> actual = download(output_device, expected.size());
    const auto non_finite = [](const std::vector<float>& values) {
        return std::count_if(values.begin(), values.end(), [](float value) { return !std::isfinite(value); });
    };
    const auto max_abs = [](const std::vector<float>& values) {
        double result = 0.0;
        for (float value : values) {
            if (std::isfinite(value)) result = std::max(result, (double) std::fabs(value));
        }
        return result;
    };
    if (non_finite(gu_float) != 0 || non_finite(down_float) != 0 || non_finite(expected) != 0 ||
        non_finite(gate_up_debug) != 0 || non_finite(hidden_debug) != 0 || non_finite(actual) != 0) {
        std::printf("%s: non-finite values: gu=%lld down=%lld reference=%lld gate/up=%lld hidden=%lld actual=%lld; "
                    "max |gu| %.3e, |hidden| %.3e, |reference| %.3e\n",
                    ggml_type_name(type), (long long) non_finite(gu_float), (long long) non_finite(down_float),
                    (long long) non_finite(expected), (long long) non_finite(gate_up_debug),
                    (long long) non_finite(hidden_debug), (long long) non_finite(actual), max_abs(gate_up_debug),
                    max_abs(hidden_debug), max_abs(expected));
        int printed = 0;
        for (size_t index = 0; index < actual.size() && printed < 8; ++index) {
            if (!std::isfinite(actual[index])) {
                std::printf("  actual[%zu] row=%zu col=%zu\n", index, index / kEmbedding, index % kEmbedding);
                ++printed;
            }
        }
        if (non_finite(actual) != 0) {
            mmq::Context fresh_context;
            fresh_context.run(down, nullptr);
            cuda_ok(cudaDeviceSynchronize(), "fresh down synchronize");
            const std::vector<float> fresh = download(output_device, expected.size());
            std::printf("  a fresh MMQ context writes %lld non-finite values and last column %.3e\n",
                        (long long) non_finite(fresh), fresh[(size_t) kRows * kEmbedding - 1]);
        }
    }
    const auto [rms, worst] = relative_error(actual, expected, kRows, kEmbedding);
    const bool ok = rms < 3e-2 && worst < 0.25;
    std::printf("%-10s / %-7s rows=%d  relative RMS %.3e  worst row %.3e  %s\n", ggml_type_name(type),
                ggml_type_name(GGML_TYPE_IQ4_NL), kRows, rms, worst, ok ? "ok" : "FAIL");
    return ok ? 0 : 1;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 2) {
        std::fprintf(stderr, "usage: mmq_expert_parity iq2_xxs|iq2_xs|iq2_s|iq3_s\n");
        return 2;
    }
    const std::string name = argv[1];
    ggml_type type = GGML_TYPE_COUNT;
    if (name == "iq2_xxs") type = GGML_TYPE_IQ2_XXS;
    if (name == "iq2_xs") type = GGML_TYPE_IQ2_XS;
    if (name == "iq2_s") type = GGML_TYPE_IQ2_S;
    if (name == "iq3_s") type = GGML_TYPE_IQ3_S;
    if (type == GGML_TYPE_COUNT) {
        std::fprintf(stderr, "unknown type: %s\n", name.c_str());
        return 2;
    }

    int device_count = 0;
    if (cudaGetDeviceCount(&device_count) != cudaSuccess || device_count == 0) {
        std::printf("no CUDA device: skipped\n");
        return 77;
    }
    try {
        return run_case(type);
    } catch (const std::exception& error) {
        std::fprintf(stderr, "mmq_expert_parity: %s\n", error.what());
        return 1;
    }
}
