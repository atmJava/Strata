#pragma once

#include <cuda_runtime.h>

#include <functional>
#include <string>

namespace strata::core {

/// Records one CUDA stream capture and publishes its executable graph only after the body, end-capture and
/// instantiate steps all succeed.  `body` receives the capture stream and writes a detail into `err` on failure.
using GraphCaptureBody = std::function<bool(cudaStream_t, std::string&)>;
bool capture_graph_exec(const std::string& label, const GraphCaptureBody& body, cudaGraphExec_t& exec,
                        std::string& err);
/// Instantiates `graph`, always consumes it, and publishes `exec` only on success.
bool instantiate_graph_exec(cudaGraph_t graph, cudaGraphExec_t& exec, const std::string& label, std::string& err);
/// Uploads `exec` and waits for the upload.  On failure, destroys it and clears `exec`.
bool upload_graph_exec(cudaGraphExec_t& exec, cudaStream_t stream, const std::string& label, std::string& err);

} // namespace strata::core
