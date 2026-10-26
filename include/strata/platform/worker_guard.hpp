// Keep an exception inside a background worker and publish a failure the waiting path can observe.
#pragma once

#include <atomic>
#include <cstdio>
#include <exception>
#include <utility>

namespace strata::platform {

template <typename Work>
void run_worker_nothrow(const char* what, Work&& work, std::atomic<bool>& failed) noexcept {
    try {
        std::forward<Work>(work)();
    } catch (const std::exception& error) {
        failed.store(true, std::memory_order_release);
        std::fprintf(stderr, "%s stopped (%s)\n", what, error.what());
    } catch (...) {
        failed.store(true, std::memory_order_release);
        std::fprintf(stderr, "%s stopped\n", what);
    }
}

}  // namespace strata::platform
