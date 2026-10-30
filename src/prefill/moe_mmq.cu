// src/prefill/moe_mmq.cu - see include/strata/prefill/moe_mmq.hpp.  llama.cpp's MMQ (ggml-cuda, MIT) is compiled
// from the pinned llama.cpp checkout the build already takes ggml from; src/prefill/ggml_cuda_host.cu supplies the
// few host symbols of ggml-cuda.cu it references.
#include "strata/prefill/moe_mmq.hpp"

#include "common.cuh"
#include "mmq.cuh"
#include "quantize.cuh"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace strata::prefill::mmq {
namespace {
inline int64_t type_blck(int t) { return (int64_t) ggml_blck_size((ggml_type) t); }
inline size_t type_bytes(int t) { return ggml_type_size((ggml_type) t); }
}  // namespace
namespace {
void ck(cudaError_t e, const char* what) {
    if (e != cudaSuccess) {
        std::fprintf(stderr, "prefill mmq: %s: %s\n", what, cudaGetErrorString(e));
        std::exit(1);
    }
}

int64_t pad512(int64_t n) { return (n + 511) / 512 * 512; }

// ... plus the zeroed MMQ tails after the group's last slot (nz uint4 after ab_dst's and c_dst's copies): one launch
// per expert instead of a kernel and, per group, two memsets.
__global__ void copy16_kernel(const uint4* __restrict__ a, int64_t na, const uint4* __restrict__ b, int64_t nb,
                              uint4* __restrict__ ab_dst, const uint4* __restrict__ c, int64_t nc, uint4* __restrict__ c_dst,
                              int64_t nz) {
    const int64_t i = (int64_t) blockIdx.x * blockDim.x + threadIdx.x;
    const int64_t n = na + nb + nc;
    if (i < na) ab_dst[i] = a[i];
    else if (i < na + nb) ab_dst[i] = b[i - na];
    else if (i < n) c_dst[i - na - nb] = c[i - na - nb];
    else if (i < n + nz) ab_dst[na + nb + (i - n)] = make_uint4(0, 0, 0, 0);
    else if (i < n + 2 * nz) c_dst[nc + (i - n - nz)] = make_uint4(0, 0, 0, 0);
}
// copy16_kernel for an MMQ group: blockIdx.y is the expert (first + y); the last one (n - 1) also zeroes nz uint4
// after its gu and d slots.
struct GroupArgs {
    const uint8_t* blob[kGatherGroupMax];
    int64_t up_off, down_off, gu_stride, d_stride;   // in uint4
};
__global__ void copy16_group_kernel(GroupArgs ga, int first, int n, int64_t na, int64_t nc, int64_t nz,
                                    uint4* __restrict__ gu_dst, uint4* __restrict__ d_dst) {
    const int q = first + (int) blockIdx.y;
    const uint4* src = (const uint4*) ga.blob[q];
    uint4* ab = gu_dst + (int64_t) q * ga.gu_stride;
    uint4* cd = d_dst + (int64_t) q * ga.d_stride;
    const int64_t i = (int64_t) blockIdx.x * blockDim.x + threadIdx.x;
    const int64_t m = 2 * na + nc;
    if (i < na) ab[i] = src[i];
    else if (i < 2 * na) ab[i] = src[ga.up_off + (i - na)];
    else if (i < m) cd[i - 2 * na] = src[ga.down_off + (i - 2 * na)];
    else if (i < m + nz) { if (q == n - 1) ab[ga.gu_stride + (i - m)] = make_uint4(0, 0, 0, 0); }
    else if (i < m + 2 * nz) { if (q == n - 1) cd[ga.d_stride + (i - m - nz)] = make_uint4(0, 0, 0, 0); }
}
__global__ void copy1_kernel(const uint8_t* __restrict__ a, int64_t na, const uint8_t* __restrict__ b, int64_t nb,
                             uint8_t* __restrict__ ab_dst, const uint8_t* __restrict__ c, int64_t nc, uint8_t* __restrict__ c_dst) {
    const int64_t i = (int64_t) blockIdx.x * blockDim.x + threadIdx.x;
    if (i < na) ab_dst[i] = a[i];
    else if (i < na + nb) ab_dst[i] = b[i - na];
    else if (i < na + nb + nc) c_dst[i - na - nb] = c[i - na - nb];
}

// Strata blob: gate/up codes [1280][640 B], down codes [2560][160 B], gate/up scales [1280][40] f16, down scales
// [2560][10] f16 (the layout of prefill/kernels.cu's blob_dequant_kernel).  A GGUF Q2_0 block is {f16 d; 16 code
// bytes} with the same 2-bit codes in the same order, so a block is a scale and a 16-byte run of codes.
__global__ void strata_q2_kernel(const uint8_t* __restrict__ blob, uint16_t* __restrict__ gu, uint16_t* __restrict__ dn) {
    constexpr size_t O_D_CODES = (size_t) 1280 * 640, O_GU_SC = O_D_CODES + (size_t) 2560 * 160,
                     O_D_SC = O_GU_SC + (size_t) 1280 * 40 * 2;
    const int64_t i = (int64_t) blockIdx.x * blockDim.x + threadIdx.x;   // one GGUF block
    const int64_t n_gu = 1280LL * 40, n_d = 2560LL * 10;
    const uint8_t* codes;
    const uint16_t* scale;
    uint16_t* out;
    if (i < n_gu) {
        const int64_t row = i / 40, b = i % 40;
        codes = blob + row * 640 + b * 16;
        scale = (const uint16_t*) (blob + O_GU_SC) + row * 40 + b;
        out = gu + i * 9;
    } else if (i < n_gu + n_d) {
        const int64_t j = i - n_gu, row = j / 10, b = j % 10;
        codes = blob + O_D_CODES + row * 160 + b * 16;
        scale = (const uint16_t*) (blob + O_D_SC) + row * 10 + b;
        out = dn + j * 9;
    } else {
        return;
    }
    const uint4 q = *(const uint4*) codes;
    const uint16_t* qh = (const uint16_t*) &q;
    out[0] = *scale;
#pragma unroll
    for (int k = 0; k < 8; ++k) out[1 + k] = qh[k];
}

__global__ void swiglu_kernel(const float* __restrict__ gu, float* __restrict__ h, int64_t rows, int64_t n_ff,
                              bool interleaved) {
    const int64_t i = (int64_t) blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= rows * n_ff) return;
    const int64_t r = i / n_ff, k = i % n_ff;
    const float* row = gu + r * 2 * n_ff;
    const float g = interleaved ? row[2 * k] : row[k], u = interleaved ? row[2 * k + 1] : row[n_ff + k];
    h[i] = g / (1.0f + __expf(-g)) * u;
}

// 吞吐优化：生产 MMQ 的 down 只需要 q8_1 的 H，不需要可读回的 FP32 H。原路径先写 H、再启动
// quantize_mmq_q8_1 读回，消耗一次 launch 和 H 的一写一读；这里每个 warp 直接计算 128 个 H 值并写成
// block_q8_1_mmq。计算顺序、SwiGLU 的 __expf 表达式、amax shuffle 和量化舍入都照搬原路径，因此适用于
// D4 q8_1 布局（IQ2/IQ3/IQ4_NL/Q2_0/Q8_0）且输出应当逐字节不变；其他布局仍走原两段路径。
__global__ void swiglu_quantize_d4_kernel(const float* __restrict__ gu, void* __restrict__ vy, int64_t rows,
                                          int64_t n_ff, bool interleaved) {
    const int64_t row = blockIdx.x;
    const int64_t i0 = ((int64_t) blockDim.x * blockIdx.y + threadIdx.x) * 4;
    const int64_t k_block = i0 / QK8_1_MMQ;

    float4 h = make_float4(0.0f, 0.0f, 0.0f, 0.0f);
    const float* gr = gu + row * 2 * n_ff;
    if (i0 < n_ff) {
        float4 g, u;
        if (interleaved) {
            // Strata pack: gate 2k, up 2k+1.  Eight source floats produce this lane's four H values.
            const float4 a = *(const float4*) (gr + 2 * i0);
            const float4 b = *(const float4*) (gr + 2 * i0 + 4);
            g = make_float4(a.x, a.z, b.x, b.z);
            u = make_float4(a.y, a.w, b.y, b.w);
        } else {
            // GGUF native layout: the gate row is followed by the up row.
            g = *(const float4*) (gr + i0);
            u = *(const float4*) (gr + n_ff + i0);
        }
        h.x = g.x / (1.0f + __expf(-g.x)) * u.x;
        h.y = g.y / (1.0f + __expf(-g.y)) * u.y;
        h.z = g.z / (1.0f + __expf(-g.z)) * u.z;
        h.w = g.w / (1.0f + __expf(-g.w)) * u.w;
    }

    float amax = fabsf(h.x);
    amax = fmaxf(amax, fabsf(h.y));
    amax = fmaxf(amax, fabsf(h.z));
    amax = fmaxf(amax, fabsf(h.w));
#pragma unroll
    for (int offset = 4; offset > 0; offset >>= 1) {
        amax = fmaxf(amax, __shfl_xor_sync(0xffffffff, amax, offset));
    }

    const float d_inv = 127.0f / amax;
    char4 q;
    q.x = roundf(h.x * d_inv);
    q.y = roundf(h.y * d_inv);
    q.z = roundf(h.z * d_inv);
    q.w = roundf(h.w * d_inv);
    const float d = 1.0f / d_inv;

    // quantize_mmq_q8_1_cuda stores the block axis first: y[k_block * rows + row].
    block_q8_1_mmq* y = (block_q8_1_mmq*) vy + k_block * rows + row;
    ((char4*) y->qs)[threadIdx.x & 31] = q;
    if ((threadIdx.x & 31) % 8 == 0) y->d4[(threadIdx.x & 31) / 8] = d;
}

__global__ void iota_kernel(int32_t* dst, int64_t n) {
    const int64_t i = (int64_t) blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) dst[i] = (int32_t) i;
}

unsigned blocks(int64_t n) { return (unsigned) ((n + 255) / 256); }

}  // namespace

bool built() { return true; }

bool supported(int t) {
    switch ((ggml_type) t) {
        case GGML_TYPE_Q2_0:
        case GGML_TYPE_IQ2_XXS:
        case GGML_TYPE_IQ2_XS:
        case GGML_TYPE_IQ2_S:
        case GGML_TYPE_IQ3_XXS:
        case GGML_TYPE_IQ3_S:
        case GGML_TYPE_IQ4_NL:
        case GGML_TYPE_Q8_0:   // the draft layer's dense matrices (E-9)
            return true;
        default:
            return false;
    }
}

bool fits(int t, int64_t w_rows) {
    if (!supported(t)) return false;
    // mul_mat_q_case's choice: the "fallback" configs when the rows are not a multiple of 128; then
    // mul_mat_q_switch_J's loop - a tile size whose config exists for this card and fits its shared memory
    const bool fallback = w_rows % 128 != 0;
    const ggml_cuda_device_info& info = ggml_cuda_info();
    for (int id = 0; id < info.device_count; ++id) {
        const int cc = info.devices[id].cc;
        const size_t smpbo = info.devices[id].smpbo;
        bool any = false;
        for (int J = 8; J <= 128 && !any; J += 8) {
            const ggml_cuda_mmq_config c = ggml_cuda_mmq_get_config((ggml_type) t, J, fallback, cc);
            any = c.type != GGML_TYPE_COUNT && mmq_get_nbytes_shared(c, cc) <= smpbo;
        }
        if (!any) {
            static bool said[GGML_TYPE_COUNT] = {};
            if (t >= 0 && t < GGML_TYPE_COUNT && !said[t]) {
                said[t] = true;
                std::fprintf(stderr, "strata: prompt kernels: llama.cpp's MMQ has no tile for %s (%lld rows) on GPU %d "
                                     "(cc %d, %zu bytes of shared memory per block): that product takes the non-MMQ path "
                                     "(#420)\n", ggml_type_name((ggml_type) t), (long long) w_rows, id, cc, smpbo);
            }
            return false;
        }
    }
    return true;
}

size_t matrix_bytes(int t, int64_t rows, int64_t cols) {
    return (size_t) rows * (size_t) (cols / type_blck((int) t)) * type_bytes((int) t);
}

size_t q8_bytes(int64_t rows, int64_t cols) {
    return (size_t) rows * (size_t) pad512(cols) * sizeof(block_q8_1_mmq) / (4 * QK8_1) + 128 * sizeof(block_q8_1_mmq);
}

void quantize(const float* x, const int32_t* ids, void* xq, int t, int64_t cols, int64_t ld, int64_t rows,
              void* stream) {
    if (rows <= 0) return;
    quantize_mmq_q8_1_cuda(x, ids, xq, (ggml_type) t, cols, ld, rows * ld, rows * ld, pad512(cols), rows, 1, 1,
                           (cudaStream_t) stream);
    ck(cudaGetLastError(), "quantize");
}

void quantize_scatter(const float* x, const int32_t* slot, const int32_t* src, void* xq, int t, int64_t cols, int64_t ld,
                      int64_t tokens, int k_used, void* stream) {
    if (tokens <= 0 || k_used <= 0) return;
    static const bool gather = [] { const char* e = std::getenv("STRATA_QUANT_GATHER"); return e != nullptr && std::atoi(e) != 0; }();
    if (gather) {
        quantize(x, src, xq, t, cols, ld, tokens * k_used, stream);
        return;
    }
    quantize_scatter_mmq_q8_1_cuda(x, slot, xq, (ggml_type) t, cols, ld, pad512(cols), tokens, tokens * k_used, k_used,
                                   (cudaStream_t) stream);
    ck(cudaGetLastError(), "quantize_scatter");
}

Context::Context() {
    int dev = 0;
    cudaGetDevice(&dev);
    ctx_ = new ggml_backend_cuda_context(dev);
}
Context::~Context() { delete (ggml_backend_cuda_context*) ctx_; }

void Context::run(const Product& p, void* stream) {
    if (p.n <= 0 || p.max_rows <= 0) return;
    const ggml_type t = (ggml_type) p.type;
    const int64_t qk = type_blck((int) t), bpr = p.w_cols / qk;
    const mmq_args a = {(const char*) p.w, t, (const int*) p.xq, p.ids, p.bounds, p.dst, nullptr,
                        p.w_cols, p.w_rows, p.total_rows, bpr, p.total_rows, p.ld_dst,
                        p.n, p.n, (int64_t) (p.expert_bytes / type_bytes((int) t)), 0, 0,
                        1, 1, 0, 0, 0,
                        p.max_rows, p.max_rows};
    auto& ctx = *(ggml_backend_cuda_context*) ctx_;
    const cudaStream_t s = (cudaStream_t) stream;
    switch (t) {
        case GGML_TYPE_Q2_0: mul_mat_q_case<GGML_TYPE_Q2_0>(ctx, a, s); break;
        case GGML_TYPE_IQ2_XXS: mul_mat_q_case<GGML_TYPE_IQ2_XXS>(ctx, a, s); break;
        case GGML_TYPE_IQ2_XS: mul_mat_q_case<GGML_TYPE_IQ2_XS>(ctx, a, s); break;
        case GGML_TYPE_IQ2_S: mul_mat_q_case<GGML_TYPE_IQ2_S>(ctx, a, s); break;
        case GGML_TYPE_IQ3_XXS: mul_mat_q_case<GGML_TYPE_IQ3_XXS>(ctx, a, s); break;
        case GGML_TYPE_IQ3_S: mul_mat_q_case<GGML_TYPE_IQ3_S>(ctx, a, s); break;
        case GGML_TYPE_IQ4_NL: mul_mat_q_case<GGML_TYPE_IQ4_NL>(ctx, a, s); break;
        case GGML_TYPE_Q8_0: mul_mat_q_case<GGML_TYPE_Q8_0>(ctx, a, s); break;
        default:
            std::fprintf(stderr, "prefill mmq: type %d is not covered\n", (int) t);
            std::exit(1);
    }
    ck(cudaGetLastError(), "mul_mat_q");
}

bool whole_tiles(int t, int64_t w_rows, int n_experts) {
    if (!supported(t) || w_rows % 128 != 0 || n_experts <= 0) return false;
    // launch_mul_mat_q's grid: without stream-k one block per tile; with it one block per tile when the tiles fill
    // their waves to >= 90 % (NVIDIA), else nsm blocks that split tiles along K at boundaries set by the tile count.
    // The tile count is nty * n_experts * ntx, ntx = any positive count of J-wide column tiles (max_rows decides),
    // and ntiles >= 9 * nsm always fills its waves to >= 90 %: the ntx below that are checked one by one.
    const ggml_cuda_device_info& info = ggml_cuda_info();
    for (int id = 0; id < info.device_count; ++id) {
        const int cc = info.devices[id].cc, nsm = info.devices[id].nsm;
        const size_t smpbo = info.devices[id].smpbo;
        bool any = false, stream_k = false;
        ggml_cuda_mmq_config c0 = ggml_cuda_mmq_get_config((ggml_type) t, 8, false, cc);
        for (int J = 8; J <= 128; J += 8) {
            const ggml_cuda_mmq_config c = ggml_cuda_mmq_get_config((ggml_type) t, J, false, cc);
            if (c.type == GGML_TYPE_COUNT || mmq_get_nbytes_shared(c, cc) > smpbo) continue;
            if (!any) c0 = c;
            any = true;
            // every tile width accumulates each output over the same K tiles in the same order
            if (c.I != c0.I || c.K_vram != c0.K_vram || c.sram_layout != c0.sram_layout) return false;
            stream_k = stream_k || c.stream_k;
        }
        if (!any) return false;
        if (!stream_k) continue;
        if (!GGML_CUDA_CC_IS_NVIDIA(cc) || nsm <= 0) return false;
        const int64_t per_ntx = (w_rows / c0.I) * n_experts;
        for (int64_t ntx = 1; per_ntx * ntx < 9 * (int64_t) nsm + per_ntx; ++ntx) {
            const int64_t tiles = per_ntx * ntx, waves = (tiles + nsm - 1) / nsm;
            if (100 * tiles / (nsm * waves) < 90) return false;
        }
    }
    return true;
}

void gather_native(const void* gate, const void* up, size_t gu_half_bytes, const void* down, size_t d_bytes,
                   void* gu_dst, void* d_dst, void* stream, size_t zero_bytes) {
    const cudaStream_t s = (cudaStream_t) stream;
    const bool a16 = ((uintptr_t) gate | (uintptr_t) up | (uintptr_t) down | (uintptr_t) gu_dst | (uintptr_t) d_dst |
                      gu_half_bytes | d_bytes | zero_bytes) % 16 == 0;
    if (a16) {
        const int64_t na = (int64_t) gu_half_bytes / 16, nc = (int64_t) d_bytes / 16, nz = (int64_t) zero_bytes / 16;
        copy16_kernel<<<blocks(2 * na + nc + 2 * nz), 256, 0, s>>>(
            (const uint4*) gate, na, (const uint4*) up, na, (uint4*) gu_dst, (const uint4*) down, nc, (uint4*) d_dst,
            nz);
    } else {
        if (zero_bytes) {
            cudaMemsetAsync((uint8_t*) gu_dst + 2 * gu_half_bytes, 0, zero_bytes, s);
            cudaMemsetAsync((uint8_t*) d_dst + d_bytes, 0, zero_bytes, s);
        }
        const int64_t na = (int64_t) gu_half_bytes, nc = (int64_t) d_bytes;
        copy1_kernel<<<blocks(2 * na + nc), 256, 0, s>>>((const uint8_t*) gate, na, (const uint8_t*) up, na,
                                                         (uint8_t*) gu_dst, (const uint8_t*) down, nc, (uint8_t*) d_dst);
    }
    ck(cudaGetLastError(), "gather_native");
}

bool gather_native_group(const GatherGroup& g, size_t up_off, size_t gu_half_bytes, size_t down_off, size_t d_bytes,
                         void* gu_dst, size_t gu_stride, void* d_dst, size_t d_stride, void* stream, size_t zero_bytes) {
    if (g.first < 0 || g.n <= g.first || g.n > kGatherGroupMax) return false;
    uintptr_t a = (uintptr_t) gu_dst | (uintptr_t) d_dst | up_off | gu_half_bytes | down_off | d_bytes | gu_stride | d_stride |
                  zero_bytes;
    for (int q = g.first; q < g.n; ++q) a |= (uintptr_t) g.blob[q];
    if (a % 16 != 0) return false;
    GroupArgs ga{};
    for (int q = g.first; q < g.n; ++q) ga.blob[q] = g.blob[q];
    ga.up_off = (int64_t) up_off / 16;
    ga.down_off = (int64_t) down_off / 16;
    ga.gu_stride = (int64_t) gu_stride / 16;
    ga.d_stride = (int64_t) d_stride / 16;
    const int64_t na = (int64_t) gu_half_bytes / 16, nc = (int64_t) d_bytes / 16, nz = (int64_t) zero_bytes / 16;
    const int64_t work = 2 * na + nc + 2 * nz;
    copy16_group_kernel<<<dim3(blocks(work), (unsigned) (g.n - g.first)), 256, 0, (cudaStream_t) stream>>>(
        ga, g.first, g.n, na, nc, nz, (uint4*) gu_dst, (uint4*) d_dst);
    ck(cudaGetLastError(), "gather_native_group");
    return true;
}

void gather_strata_q2(const uint8_t* blob, void* gu_dst, void* d_dst, void* stream) {
    strata_q2_kernel<<<blocks(1280LL * 40 + 2560LL * 10), 256, 0, (cudaStream_t) stream>>>(blob, (uint16_t*) gu_dst,
                                                                                         (uint16_t*) d_dst);
    ck(cudaGetLastError(), "gather_strata_q2");
}

void swiglu(const float* gu, float* h, int64_t rows, int64_t n_ff, bool interleaved, void* stream) {
    if (rows <= 0) return;
    swiglu_kernel<<<blocks(rows * n_ff), 256, 0, (cudaStream_t) stream>>>(gu, h, rows, n_ff, interleaved);
    ck(cudaGetLastError(), "swiglu");
}

bool swiglu_quantize_supported(int t) {
    switch ((ggml_type) t) {
        case GGML_TYPE_Q2_0:
        case GGML_TYPE_IQ2_XXS:
        case GGML_TYPE_IQ2_XS:
        case GGML_TYPE_IQ2_S:
        case GGML_TYPE_IQ3_XXS:
        case GGML_TYPE_IQ3_S:
        case GGML_TYPE_IQ4_NL:
        case GGML_TYPE_Q8_0:
            return true;
        default:
            return false;
    }
}

void swiglu_quantize(const float* gu, void* hq, int64_t rows, int64_t n_ff, bool interleaved, int t, void* stream) {
    if (rows <= 0) return;
    if (!swiglu_quantize_supported(t) || n_ff % 4 != 0) {
        std::fprintf(stderr, "prefill mmq: swiglu_quantize does not cover type %d, n_ff %lld\n", t, (long long) n_ff);
        std::exit(1);
    }
    const int64_t padded = pad512(n_ff);
    constexpr int kThreads = 128;
    swiglu_quantize_d4_kernel<<<dim3((unsigned) rows, (unsigned) (padded / (4 * kThreads))),
                               kThreads, 0, (cudaStream_t) stream>>>(
        gu, hq, rows, n_ff, interleaved);
    ck(cudaGetLastError(), "swiglu_quantize");
}

void iota(int32_t* dst, int64_t n, void* stream) {
    if (n <= 0) return;
    iota_kernel<<<blocks(n), 256, 0, (cudaStream_t) stream>>>(dst, n);
    ck(cudaGetLastError(), "iota");
}

}  // namespace strata::prefill::mmq
