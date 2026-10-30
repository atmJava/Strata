// tests/cuda/prefill_gr_broadcast_test.cpp - 校验 prefill 的嵌入广播逐位复制四个流，且不越过输出缓冲区。

#include "strata/prefill/kernels.hpp"

#include <cuda_runtime.h>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <random>
#include <string_view>
#include <vector>

namespace {

constexpr int64_t kTokens = 257;
constexpr int64_t kEmbedding = 2560;
constexpr int64_t kHyper = 4;
constexpr int64_t kHyperWidth = kEmbedding * kHyper;
constexpr int64_t kGuard = 32;
constexpr uint32_t kSentinel = 0x7fc12345u;

/**
 * 在 CUDA 调用失败时输出诊断并返回 false。
 *
 * @param error CUDA 调用返回值。
 * @param what 调用名称。
 * @return CUDA 调用成功时返回 true。
 */
bool cuda_ok(cudaError_t error, const char* what) {
    if (error == cudaSuccess) return true;
    std::fprintf(stderr, "prefill_gr_broadcast_test: %s: %s\n", what, cudaGetErrorString(error));
    return false;
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
     */
    explicit DeviceBuffer(size_t bytes) : bytes_(bytes) {}

    ~DeviceBuffer() {
        if (data_ != nullptr) cudaFree(data_);
    }

    DeviceBuffer(const DeviceBuffer&) = delete;
    DeviceBuffer& operator=(const DeviceBuffer&) = delete;

    /**
     * 分配设备内存。
     *
     * @return 分配成功时返回 true。
     */
    bool allocate() {
        return cuda_ok(cudaMalloc(&data_, bytes_), "cudaMalloc");
    }

    /**
     * 返回设备内存指针。
     *
     * @return 设备内存起始地址。
     */
    void* data() const noexcept { return data_; }

private:
    size_t bytes_ = 0;
    void* data_ = nullptr;
};

/**
 * 生成包含普通值和特殊位模式的输入，随后按 uint32_t 比较输出位模式。
 *
 * @return 测试通过时返回 0。
 */
int run_test() {
    std::vector<uint32_t> input((size_t) kTokens * kEmbedding);
    std::mt19937 rng(20261029u);
    for (uint32_t& bits : input) bits = rng();
    input[0] = 0x7fc12345u;
    input[1] = 0x80000000u;
    input[2] = 0x00000001u;
    input[3] = 0xffc54321u;

    std::vector<uint32_t> expected((size_t) kTokens * kHyperWidth + kGuard, kSentinel);
    DeviceBuffer input_device(input.size() * sizeof(uint32_t));
    DeviceBuffer output_device(expected.size() * sizeof(uint32_t));
    if (!input_device.allocate() || !output_device.allocate()) return 1;
    if (!cuda_ok(cudaMemcpy(input_device.data(), input.data(), input.size() * sizeof(uint32_t),
                            cudaMemcpyHostToDevice), "copy input") ||
        !cuda_ok(cudaMemcpy(output_device.data(), expected.data(), expected.size() * sizeof(uint32_t),
                            cudaMemcpyHostToDevice), "initialize output")) {
        return 1;
    }

    strata::prefill::gr_broadcast(static_cast<const float*>(input_device.data()),
                                  static_cast<float*>(output_device.data()), kTokens, nullptr);
    if (!cuda_ok(cudaDeviceSynchronize(), "synchronize") ||
        !cuda_ok(cudaMemcpy(expected.data(), output_device.data(), expected.size() * sizeof(uint32_t),
                            cudaMemcpyDeviceToHost), "copy output")) {
        return 1;
    }

    for (int64_t token = 0; token < kTokens; ++token) {
        for (int64_t stream = 0; stream < kHyper; ++stream) {
            for (int64_t column = 0; column < kEmbedding; ++column) {
                const size_t source = (size_t) token * kEmbedding + (size_t) column;
                const size_t destination =
                    (size_t) token * kHyperWidth + (size_t) stream * kEmbedding + (size_t) column;
                if (expected[destination] != input[source]) {
                    std::fprintf(stderr,
                                 "prefill_gr_broadcast_test: mismatch token=%lld stream=%lld column=%lld "
                                 "got=%08x want=%08x\n",
                                 (long long) token, (long long) stream, (long long) column, expected[destination],
                                 input[source]);
                    return 1;
                }
            }
        }
    }
    for (int64_t i = 0; i < kGuard; ++i) {
        const size_t index = (size_t) kTokens * kHyperWidth + (size_t) i;
        if (expected[index] != kSentinel) {
            std::fprintf(stderr, "prefill_gr_broadcast_test: guard changed at %lld: %08x\n", (long long) i,
                         expected[index]);
            return 1;
        }
    }
    std::printf("prefill_gr_broadcast_test: %lld tokens, 4 streams, bitwise ok\n", (long long) kTokens);
    return 0;
}

/**
 * 对 gr_write_norm_rs 的两种等价实现做位级比较和交替计时。
 *
 * 保留版每线程保存 40 个第一遍结果；重读版降低寄存器和提高可驻留 block 数，代价是再读刚写过的 R 行。
 *
 * @return 全部形状位级一致时返回 0，否则返回 1。
 */
int run_write_norm_bench() {
    constexpr int64_t kHyperWidthBench = kHyper * kEmbedding;
    const float eps = 1e-6f;
    std::mt19937 rng(20261029u);
    std::uniform_real_distribution<float> value(-0.25f, 0.25f);
    int failures = 0;

    for (const int64_t tokens : {int64_t{128}, int64_t{512}, int64_t{2048}, int64_t{8192}}) {
        const size_t r_count = (size_t) tokens * kHyperWidthBench;
        const size_t plain_count = (size_t) tokens * kEmbedding;
        const size_t hyper_count = (size_t) tokens * kHyper;
        std::vector<float> initial(r_count), bo(plain_count), inj(hyper_count), norm(kHyperWidthBench);
        for (float& x : initial) x = value(rng);
        for (float& x : bo) x = value(rng);
        for (float& x : inj) x = value(rng);
        for (float& x : norm) x = 0.5f + value(rng);

        DeviceBuffer r_keep(r_count * sizeof(float));
        DeviceBuffer r_reread(r_count * sizeof(float));
        DeviceBuffer bo_device(plain_count * sizeof(float));
        DeviceBuffer inj_device(hyper_count * sizeof(float));
        DeviceBuffer norm_device(norm.size() * sizeof(float));
        DeviceBuffer rs_keep(hyper_count * sizeof(float));
        DeviceBuffer rs_reread(hyper_count * sizeof(float));
        DeviceBuffer xn_keep(r_count * sizeof(uint16_t));
        DeviceBuffer xn_reread(r_count * sizeof(uint16_t));
        if (!r_keep.allocate() || !r_reread.allocate() || !bo_device.allocate() || !inj_device.allocate() ||
            !norm_device.allocate() || !rs_keep.allocate() || !rs_reread.allocate() || !xn_keep.allocate() ||
            !xn_reread.allocate()) {
            return 1;
        }

        auto copy_in = [&]() {
            return cuda_ok(cudaMemcpy(r_keep.data(), initial.data(), initial.size() * sizeof(float),
                                     cudaMemcpyHostToDevice), "copy keep R") &&
                   cuda_ok(cudaMemcpy(r_reread.data(), initial.data(), initial.size() * sizeof(float),
                                     cudaMemcpyHostToDevice), "copy reread R") &&
                   cuda_ok(cudaMemcpy(bo_device.data(), bo.data(), bo.size() * sizeof(float),
                                     cudaMemcpyHostToDevice), "copy bo") &&
                   cuda_ok(cudaMemcpy(inj_device.data(), inj.data(), inj.size() * sizeof(float),
                                     cudaMemcpyHostToDevice), "copy inj") &&
                   cuda_ok(cudaMemcpy(norm_device.data(), norm.data(), norm.size() * sizeof(float),
                                     cudaMemcpyHostToDevice), "copy norm");
        };
        if (!copy_in()) return 1;

        auto run = [&](bool reread, void* r, void* rs, void* xn) {
            strata::prefill::gr_write_norm_rs(
                static_cast<float*>(r), static_cast<const float*>(bo_device.data()),
                static_cast<const float*>(inj_device.data()), kHyper,
                static_cast<const float*>(norm_device.data()), eps, static_cast<float*>(rs),
                static_cast<uint16_t*>(xn), tokens, nullptr, nullptr, kHyperWidthBench, false, reread);
        };
        run(false, r_keep.data(), rs_keep.data(), xn_keep.data());
        run(true, r_reread.data(), rs_reread.data(), xn_reread.data());
        if (!cuda_ok(cudaDeviceSynchronize(), "synchronize write norm")) return 1;

        auto equal_device = [&](const void* a, const void* b, size_t bytes, const char* what) {
            std::vector<uint8_t> ha(bytes), hb(bytes);
            if (!cuda_ok(cudaMemcpy(ha.data(), a, bytes, cudaMemcpyDeviceToHost), what) ||
                !cuda_ok(cudaMemcpy(hb.data(), b, bytes, cudaMemcpyDeviceToHost), what)) {
                return false;
            }
            if (ha == hb) return true;
            std::fprintf(stderr, "prefill_gr_broadcast_test: write-norm mismatch tokens=%lld in %s\n",
                         (long long) tokens, what);
            return false;
        };
        failures += !equal_device(r_keep.data(), r_reread.data(), r_count * sizeof(float), "R") +
                    !equal_device(rs_keep.data(), rs_reread.data(), hyper_count * sizeof(float), "rs") +
                    !equal_device(xn_keep.data(), xn_reread.data(), r_count * sizeof(uint16_t), "xn16");

        cudaEvent_t begin = nullptr, end = nullptr;
        if (!cuda_ok(cudaEventCreate(&begin), "create begin event") ||
            !cuda_ok(cudaEventCreate(&end), "create end event")) {
            return 1;
        }
        constexpr int rounds = 12;
        std::vector<double> keep_ms, reread_ms;
        for (int i = 0; i < rounds + 2; ++i) {
            for (int order = 0; order < 2; ++order) {
                const bool reread = (i + order) & 1;
                void* r = reread ? r_reread.data() : r_keep.data();
                void* rs = reread ? rs_reread.data() : rs_keep.data();
                void* xn = reread ? xn_reread.data() : xn_keep.data();
                if (!cuda_ok(cudaMemcpy(r, initial.data(), initial.size() * sizeof(float),
                                        cudaMemcpyHostToDevice), "reset benchmark R") ||
                    !cuda_ok(cudaEventRecord(begin), "record begin")) {
                    return 1;
                }
                run(reread, r, rs, xn);
                if (!cuda_ok(cudaEventRecord(end), "record end") ||
                    !cuda_ok(cudaEventSynchronize(end), "synchronize event")) {
                    return 1;
                }
                float ms = 0.0f;
                if (!cuda_ok(cudaEventElapsedTime(&ms, begin, end), "elapsed time")) return 1;
                if (i >= 2) (reread ? reread_ms : keep_ms).push_back(ms);
            }
        }
        cudaEventDestroy(begin);
        cudaEventDestroy(end);
        std::sort(keep_ms.begin(), keep_ms.end());
        std::sort(reread_ms.begin(), reread_ms.end());
        const double keep = keep_ms[keep_ms.size() / 2], reread = reread_ms[reread_ms.size() / 2];
        std::printf("write_norm T=%lld: keep %.3f ms, reread %.3f ms, %+.1f%%\n", (long long) tokens, keep, reread,
                    100.0 * (keep - reread) / keep);
    }
    return failures ? 1 : 0;
}

}  // namespace

int main(int argc, char** argv) {
    int device_count = 0;
    if (cudaGetDeviceCount(&device_count) != cudaSuccess || device_count == 0) {
        std::printf("prefill_gr_broadcast_test: no CUDA device, skipped\n");
        return 77;
    }
    if (argc > 1 && std::string_view(argv[1]) == "--bench-write-norm") return run_write_norm_bench();
    return run_test();
}
