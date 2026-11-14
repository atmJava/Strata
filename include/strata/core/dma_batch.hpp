#pragma once
// dma_batch.hpp - independent host-to-device blob copies: copy_blobs.
//
// The copies are submitted one cudaMemcpyAsync per blob, in order, on the caller's stream.  CUDA 13's
// cudaMemcpyBatchAsync was measured nowhere faster on this box (its only use was the opt-in STRATA_DMA_BATCH mode,
// now gone), so the loop is the path.
#include <cuda_runtime.h>

#include <cstddef>
#include <cstdint>

namespace strata::core {

/// `n` independent copies, dst[i] <- src[i] (bytes[i] each), on `stream`.  The sources are immutable pinned host
/// blobs and the destinations disjoint; the caller keeps them alive until the stream completes.  Ordering with
/// earlier and later work on the stream is that of the copies one by one.  Returns the first error (the loop
/// stops there).
inline cudaError_t copy_blobs(void* const* dst, const void* const* src, const size_t* bytes, size_t n,
                              cudaStream_t stream) {
    for (size_t i = 0; i < n; ++i) {
        const cudaError_t e = cudaMemcpyAsync(dst[i], src[i], bytes[i], cudaMemcpyHostToDevice, stream);
        if (e != cudaSuccess) return e;
    }
    return cudaSuccess;
}

/// The verifier's form: `n` blobs of `bytes` each into consecutive slots of `dst`.
inline cudaError_t copy_expert_blobs(uint8_t* dst, const uint8_t* const* src, int n, size_t bytes,
                                     cudaStream_t stream) {
    for (int i = 0; i < n; ++i) {
        const cudaError_t e = cudaMemcpyAsync(dst + (size_t) i * bytes, src[i], bytes, cudaMemcpyHostToDevice, stream);
        if (e != cudaSuccess) return e;
    }
    return cudaSuccess;
}

/// generate.cpp still asks for the removed batch mode and passes it to copy_blobs: 0 is the loop above.  Drop both
/// once its call sites stop asking (the mode that was never picked is gone).
inline int dma_batch_mode() { return 0; }
inline cudaError_t copy_blobs(void* const* dst, const void* const* src, const size_t* bytes, size_t n,
                              cudaStream_t stream, int /*mode*/) {
    return copy_blobs(dst, src, bytes, n, stream);
}

}  // namespace strata::core
