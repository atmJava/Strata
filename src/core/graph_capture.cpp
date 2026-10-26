#include "strata/core/graph_capture.hpp"

#include <exception>

namespace strata::core {

bool capture_graph_exec(const std::string& label, const GraphCaptureBody& body, cudaGraphExec_t& exec,
                        std::string& err) {
    cudaStream_t stream = nullptr;
    const cudaError_t create = cudaStreamCreate(&stream);
    if (create != cudaSuccess) {
        err = label + ": stream create: " + cudaGetErrorString(create);
        return false;
    }

    const cudaError_t begin = cudaStreamBeginCapture(stream, cudaStreamCaptureModeThreadLocal);
    if (begin != cudaSuccess) {
        (void) cudaStreamDestroy(stream);
        err = label + ": begin capture: " + cudaGetErrorString(begin);
        return false;
    }

    std::string body_error;
    bool body_ok = false;
    try {
        body_ok = body(stream, body_error);
    } catch (const std::exception& error) {
        body_error = std::string("exception: ") + error.what();
    } catch (...) {
        body_error = "unknown exception";
    }

    // CUDA requires a stream to leave capture mode before it can be destroyed, including when recording the body
    // failed.  EndCapture may still hand back a graph for the partial recording; that graph is never published.
    cudaGraph_t graph = nullptr;
    const cudaError_t end = cudaStreamEndCapture(stream, &graph);
    (void) cudaStreamDestroy(stream);
    if (!body_ok) {
        if (graph != nullptr) (void) cudaGraphDestroy(graph);
        err = label;
        if (!body_error.empty()) err += ": " + body_error;
        return false;
    }
    if (end != cudaSuccess) {
        if (graph != nullptr) (void) cudaGraphDestroy(graph);
        err = label + ": end capture: " + cudaGetErrorString(end);
        return false;
    }
    if (graph == nullptr) {
        err = label + ": capture produced no graph";
        return false;
    }

    cudaGraphExec_t instantiated = nullptr;
    const cudaError_t instantiate = cudaGraphInstantiate(&instantiated, graph, 0);
    (void) cudaGraphDestroy(graph);
    if (instantiate != cudaSuccess) {
        err = label + ": instantiate: " + cudaGetErrorString(instantiate);
        return false;
    }
    if (instantiated == nullptr) {
        err = label + ": instantiate returned an empty executable";
        return false;
    }
    exec = instantiated;
    return true;
}

} // namespace strata::core
