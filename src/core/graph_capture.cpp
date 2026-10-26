#include "strata/core/graph_capture.hpp"

#include <exception>

namespace strata::core {

bool instantiate_graph_exec(cudaGraph_t graph, cudaGraphExec_t& exec, const std::string& label, std::string& err) {
    if (graph == nullptr) {
        err = label + ": no graph to instantiate";
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

bool upload_graph_exec(cudaGraphExec_t& exec, cudaStream_t stream, const std::string& label, std::string& err) {
    if (exec == nullptr) {
        err = label + ": no executable graph to upload";
        return false;
    }
    const cudaError_t upload = cudaGraphUpload(exec, stream);
    const cudaError_t sync = upload == cudaSuccess ? cudaStreamSynchronize(stream) : upload;
    if (upload == cudaSuccess && sync == cudaSuccess) return true;

    err = label + ": graph upload: " + cudaGetErrorString(upload != cudaSuccess ? upload : sync);
    (void) cudaGraphExecDestroy(exec);
    exec = nullptr;
    return false;
}

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
    return instantiate_graph_exec(graph, exec, label, err);
}

} // namespace strata::core
