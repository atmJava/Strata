// Compare the layer-split hand-off's current pinned-host route with direct
// peer access on the exact row shape used by long-context prefill.
//
// This is deliberately a transport-only benchmark.  It proves whether a P2P
// hand-off is worth integrating; it does not test model math or claim an
// end-to-end prompt speedup.  Keep it runnable while the serving engine is
// loaded so the large [T, 10240] allocations use the same 2080 Ti pair.

#include <cuda_runtime.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#define CK(call)                                                                                                        \
    do {                                                                                                                \
        cudaError_t status_ = (call);                                                                                   \
        if (status_ != cudaSuccess) {                                                                                   \
            std::fprintf(stderr, "%s:%d: %s: %s\n", __FILE__, __LINE__, #call, cudaGetErrorString(status_));           \
            std::exit(2);                                                                                               \
        }                                                                                                               \
    } while (0)

namespace {

constexpr int kWidth = 10240;
// The production 2080 Ti pair selected 7,936-token prompt chunks in the
// 150K baseline.  The larger shapes show whether a future auto-chunk change
// makes the hand-off's fixed-size copies more or less attractive.
constexpr int kProductionChunkRows = 7936;

struct Result {
    double ms = 0.0;
};

Result time_p2p(float* dst, int dst_dev, float* src, int src_dev, size_t bytes, cudaStream_t stream,
                int stream_dev, int warmups, int repeats) {
    cudaEvent_t begin = nullptr, end = nullptr;
    int previous_dev = 0;
    CK(cudaGetDevice(&previous_dev));
    // Production issues the peer copy on the producing stage's stream, so measure that direction too.  Events are
    // created in the stream's device context.
    CK(cudaSetDevice(stream_dev));
    CK(cudaEventCreate(&begin));
    CK(cudaEventCreate(&end));
    for (int i = 0; i < warmups; ++i)
        CK(cudaMemcpyPeerAsync(dst, dst_dev, src, src_dev, bytes, stream));
    CK(cudaStreamSynchronize(stream));

    CK(cudaEventRecord(begin, stream));
    for (int i = 0; i < repeats; ++i)
        CK(cudaMemcpyPeerAsync(dst, dst_dev, src, src_dev, bytes, stream));
    CK(cudaEventRecord(end, stream));
    CK(cudaEventSynchronize(end));
    float ms = 0.0f;
    CK(cudaEventElapsedTime(&ms, begin, end));
    CK(cudaEventDestroy(begin));
    CK(cudaEventDestroy(end));
    CK(cudaSetDevice(previous_dev));
    return {ms / repeats};
}

Result time_host_staged(float* dst, int dst_dev, float* src, int src_dev, float* host, size_t bytes,
                        cudaStream_t src_stream, cudaStream_t dst_stream, int warmups, int repeats) {
    cudaEvent_t downloaded = nullptr;
    // The downloaded event belongs to the source device; the destination stream
    // waits on it.  CUDA cannot compare elapsed time across two devices' events,
    // so this two-stream route is timed by host wall clock after the final sync.
    CK(cudaSetDevice(src_dev));
    CK(cudaEventCreateWithFlags(&downloaded, cudaEventDisableTiming));

    auto once = [&] {
        // This is the current layer-split shape: the producing stage downloads
        // the completed rows to pinned memory, then the consuming stage uploads
        // them.  The event is the minimum ordering dependency between the two.
        CK(cudaMemcpyAsync(host, src, bytes, cudaMemcpyDeviceToHost, src_stream));
        CK(cudaEventRecord(downloaded, src_stream));
        CK(cudaStreamWaitEvent(dst_stream, downloaded, 0));
        CK(cudaMemcpyAsync(dst, host, bytes, cudaMemcpyHostToDevice, dst_stream));
    };

    for (int i = 0; i < warmups; ++i) {
        once();
        CK(cudaStreamSynchronize(src_stream));
        CK(cudaStreamSynchronize(dst_stream));
    }

    float total_ms = 0.0f;
    // Sync both ends per iteration so every sample includes both the download
    // and the upload, not just the transfer that happened to finish last.
    for (int i = 0; i < repeats; ++i) {
        CK(cudaStreamSynchronize(src_stream));
        CK(cudaStreamSynchronize(dst_stream));
        const auto t0 = std::chrono::steady_clock::now();
        once();
        CK(cudaStreamSynchronize(dst_stream));
        total_ms += std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    }

    CK(cudaSetDevice(src_dev));
    CK(cudaEventDestroy(downloaded));
    CK(cudaSetDevice(dst_dev));
    return {total_ms / repeats};
}

}  // namespace

int main(int argc, char** argv) {
    int src_dev = 0, dst_dev = 1, warmups = 1, repeats = 3;
    if (argc > 1) src_dev = std::max(0, std::atoi(argv[1]));
    if (argc > 2) dst_dev = std::max(0, std::atoi(argv[2]));
    if (argc > 3) repeats = std::max(1, std::atoi(argv[3]));
    if (src_dev == dst_dev) {
        std::fprintf(stderr, "source and destination devices must differ\n");
        return 2;
    }

    int can_ab = 0, can_ba = 0;
    CK(cudaDeviceCanAccessPeer(&can_ab, src_dev, dst_dev));
    CK(cudaDeviceCanAccessPeer(&can_ba, dst_dev, src_dev));
    if (!can_ab || !can_ba) {
        std::fprintf(stderr, "P2P unavailable: %d -> %d=%d, %d -> %d=%d\n", src_dev, dst_dev, can_ab, dst_dev, src_dev,
                     can_ba);
        return 3;
    }
    CK(cudaSetDevice(src_dev));
    CK(cudaDeviceEnablePeerAccess(dst_dev, 0));
    CK(cudaSetDevice(dst_dev));
    CK(cudaDeviceEnablePeerAccess(src_dev, 0));

    for (int rows : {kProductionChunkRows, 2 * kProductionChunkRows, 4 * kProductionChunkRows}) {
        const size_t bytes = (size_t) rows * kWidth * sizeof(float);
        float* src = nullptr;
        float* dst = nullptr;
        float* host = nullptr;
        cudaStream_t src_stream = nullptr, dst_stream = nullptr;

        CK(cudaSetDevice(src_dev));
        CK(cudaMalloc(&src, bytes));
        CK(cudaStreamCreateWithFlags(&src_stream, cudaStreamNonBlocking));
        CK(cudaMemsetAsync(src, 0x5a, bytes, src_stream));
        CK(cudaSetDevice(dst_dev));
        CK(cudaMalloc(&dst, bytes));
        CK(cudaStreamCreateWithFlags(&dst_stream, cudaStreamNonBlocking));
        CK(cudaHostAlloc(&host, bytes, cudaHostAllocDefault));

        const Result p2p = time_p2p(dst, dst_dev, src, src_dev, bytes, src_stream, src_dev, warmups, repeats);
        const Result host_route =
            time_host_staged(dst, dst_dev, src, src_dev, host, bytes, src_stream, dst_stream, warmups, repeats);
        const double gib = (double) bytes / (1024.0 * 1024.0 * 1024.0);
        std::printf("rows=%d bytes=%zu p2p_ms=%.3f p2p_GiBps=%.2f host_ms=%.3f host_GiBps=%.2f ratio=%.2fx\n",
                    rows, bytes, p2p.ms, gib / (p2p.ms / 1000.0), host_route.ms, gib / (host_route.ms / 1000.0),
                    host_route.ms / p2p.ms);
        std::fflush(stdout);

        CK(cudaSetDevice(src_dev));
        CK(cudaStreamSynchronize(src_stream));
        CK(cudaFree(src));
        CK(cudaStreamDestroy(src_stream));
        CK(cudaSetDevice(dst_dev));
        CK(cudaStreamSynchronize(dst_stream));
        CK(cudaFree(dst));
        CK(cudaStreamDestroy(dst_stream));
        CK(cudaFreeHost(host));
    }
    return 0;
}
