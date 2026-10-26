#pragma once

#include <cstdio>
#include <exception>
#include <thread>
#include <utility>

namespace strata::platform {

/// Start an optional detached diagnostic (a watchdog, a sampler).  A refused thread start and an exception escaping
/// its body both disable only that diagnostic; they must not turn an observability aid into a process failure.
template <typename Work>
void start_diagnostic_thread(const char* what, Work work) {
    auto run = [what, body = std::move(work)]() mutable {
        try {
            body();
        } catch (const std::exception& e) {
            std::fprintf(stderr, "%s stopped (%s); continuing without it\n", what, e.what());
        } catch (...) {
            std::fprintf(stderr, "%s stopped; continuing without it\n", what);
        }
    };
    try {
        std::thread thread(std::move(run));
        thread.detach();
    } catch (const std::exception& e) {
        std::fprintf(stderr, "%s did not start (%s); continuing without it\n", what, e.what());
    } catch (...) {
        std::fprintf(stderr, "%s did not start; continuing without it\n", what);
    }
}

}  // namespace strata::platform
