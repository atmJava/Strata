// Wait for a background producer to publish an item without turning a producer failure into an infinite wait.
#pragma once

#include <atomic>
#include <cstddef>
#include <thread>

namespace strata::platform {

inline bool wait_for_progress(const std::atomic<size_t>& published, size_t target,
                              const std::atomic<bool>& failed) {
    while (published.load(std::memory_order_acquire) <= target) {
        if (failed.load(std::memory_order_acquire)) return false;
        std::this_thread::yield();
    }
    return true;
}

}  // namespace strata::platform
