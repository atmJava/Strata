// The graph-capture transaction ends capture and destroys its stream on every failure, and publishes an
// executable only after instantiation succeeds.  Link wrapping keeps this independent of a CUDA driver.
#include "strata/core/graph_capture.hpp"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <string>

namespace {

int stream_creates = 0;
int stream_destroys = 0;
int begin_captures = 0;
int end_captures = 0;
int graph_destroys = 0;
int instantiate_calls = 0;
bool capturing = false;
int fail_stream_create = 0;
int fail_begin = 0;
int fail_end = 0;
int fail_instantiate = 0;

void check(bool condition, const char* message) {
    if (condition) return;
    std::fprintf(stderr, "graph_capture_test: %s\n", message);
    std::exit(1);
}

void reset_counts() {
    stream_creates = stream_destroys = begin_captures = end_captures = 0;
    graph_destroys = instantiate_calls = 0;
    capturing = false;
    fail_stream_create = fail_begin = fail_end = fail_instantiate = 0;
}

} // namespace

extern "C" cudaError_t __wrap_cudaStreamCreate(cudaStream_t* stream) {
    ++stream_creates;
    if (fail_stream_create) {
        *stream = nullptr;
        return cudaErrorMemoryAllocation;
    }
    *stream = (cudaStream_t) 0x1000;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaStreamDestroy(cudaStream_t stream) {
    check(stream != nullptr, "a null stream reached cudaStreamDestroy");
    check(!capturing, "a stream was destroyed while still capturing");
    ++stream_destroys;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaStreamBeginCapture(cudaStream_t stream, int mode) {
    check(stream != nullptr, "a null stream began capture");
    check(mode == cudaStreamCaptureModeThreadLocal, "the wrong capture mode was requested");
    ++begin_captures;
    if (fail_begin) return cudaErrorInvalidValue;
    capturing = true;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaStreamEndCapture(cudaStream_t stream, cudaGraph_t* graph) {
    check(stream != nullptr && capturing, "capture ended on the wrong stream");
    ++end_captures;
    capturing = false;
    *graph = fail_end ? (cudaGraph_t) 0x3000 : (cudaGraph_t) 0x2000;
    return fail_end ? cudaErrorInvalidValue : cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaGraphInstantiate(cudaGraphExec_t* exec, cudaGraph_t graph, unsigned long long) {
    check(graph != nullptr, "a null graph was instantiated");
    ++instantiate_calls;
    if (fail_instantiate) {
        *exec = nullptr;
        return cudaErrorMemoryAllocation;
    }
    *exec = (cudaGraphExec_t) 0x4000;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaGraphDestroy(cudaGraph_t graph) {
    check(graph != nullptr, "a null graph reached cudaGraphDestroy");
    ++graph_destroys;
    return cudaSuccess;
}

extern "C" const char* __wrap_cudaGetErrorString(cudaError_t) {
    return "stub CUDA error";
}

int main() {
    using strata::core::capture_graph_exec;
    cudaGraphExec_t old_exec = (cudaGraphExec_t) 0x9000;
    cudaGraphExec_t exec = old_exec;
    std::string err;

    reset_counts();
    check(capture_graph_exec(
              "success",
              [](cudaStream_t stream, std::string&) {
                  check(stream != nullptr, "capture body received a null stream");
                  return true;
              },
              exec, err),
          "a successful capture was rejected");
    check(exec == (cudaGraphExec_t) 0x4000, "a successful executable was not published");
    check(stream_creates == 1 && stream_destroys == 1 && begin_captures == 1 && end_captures == 1,
          "successful capture leaked or skipped a stream operation");
    check(graph_destroys == 1 && instantiate_calls == 1 && !capturing,
          "successful capture leaked its graph or stayed in capture");

    reset_counts();
    exec = old_exec;
    err.clear();
    check(!capture_graph_exec(
              "body failure",
              [](cudaStream_t, std::string& body_err) {
                  body_err = "recorded call failed";
                  return false;
              },
              exec, err),
          "a failed capture body was published");
    check(exec == old_exec, "a failed capture body replaced the output executable");
    check(end_captures == 1 && graph_destroys == 1 && stream_destroys == 1 && !capturing,
          "a failed capture body did not close and release capture resources");
    check(err.find("recorded call failed") != std::string::npos, "the capture body error was lost");

    reset_counts();
    exec = old_exec;
    err.clear();
    check(!capture_graph_exec(
              "throwing body",
              [](cudaStream_t, std::string&) -> bool { throw std::runtime_error("body exception"); },
              exec, err),
          "a throwing capture body was published");
    check(exec == old_exec, "a throwing capture body replaced the output executable");
    check(end_captures == 1 && graph_destroys == 1 && stream_destroys == 1 && !capturing,
          "a throwing capture body did not close and release capture resources");
    check(err.find("body exception") != std::string::npos, "the capture exception was lost");

    reset_counts();
    fail_stream_create = 1;
    exec = old_exec;
    err.clear();
    check(!capture_graph_exec("create failure", [](cudaStream_t, std::string&) { return true; }, exec, err),
          "a stream creation failure was accepted");
    check(exec == old_exec && begin_captures == 0 && stream_destroys == 0,
          "a stream creation failure touched later resources");

    reset_counts();
    fail_begin = 1;
    exec = old_exec;
    err.clear();
    check(!capture_graph_exec("begin failure", [](cudaStream_t, std::string&) { return true; }, exec, err),
          "a capture begin failure was accepted");
    check(exec == old_exec && end_captures == 0 && stream_destroys == 1 && !capturing,
          "a capture begin failure retained its stream");

    reset_counts();
    fail_end = 1;
    exec = old_exec;
    err.clear();
    check(!capture_graph_exec("end failure", [](cudaStream_t, std::string&) { return true; }, exec, err),
          "a capture end failure was accepted");
    check(exec == old_exec && graph_destroys == 1 && stream_destroys == 1 &&
              instantiate_calls == 0 && !capturing,
          "a capture end failure retained or instantiated its graph");

    reset_counts();
    fail_instantiate = 1;
    exec = old_exec;
    err.clear();
    check(!capture_graph_exec("instantiate failure", [](cudaStream_t, std::string&) { return true; }, exec, err),
          "a graph instantiation failure was accepted");
    check(exec == old_exec && graph_destroys == 1 && stream_destroys == 1 && !capturing,
          "a graph instantiation failure retained a resource or published its executable");

    std::printf("graph_capture_test: capture failures close every owned resource\n");
    return 0;
}
