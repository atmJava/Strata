#include "strata/platform/worker_guard.hpp"

#include <atomic>
#include <cstdio>
#include <stdexcept>
#include <thread>

int main() {
    {
        std::atomic<bool> failed{false};
        std::atomic<bool> ran{false};
        std::thread worker([&] {
            strata::platform::run_worker_nothrow("worker_guard_test success", [&] {
                ran.store(true, std::memory_order_release);
            }, failed);
        });
        worker.join();
        if (!ran.load(std::memory_order_acquire) || failed.load(std::memory_order_acquire)) {
            std::fprintf(stderr, "worker_guard_test: a successful worker reported failure\n");
            return 1;
        }
    }
    {
        std::atomic<bool> failed{false};
        std::thread worker([&] {
            strata::platform::run_worker_nothrow("worker_guard_test failure", [] {
                throw std::runtime_error("injected worker failure");
            }, failed);
        });
        worker.join();
        if (!failed.load(std::memory_order_acquire)) {
            std::fprintf(stderr, "worker_guard_test: a worker exception was not published\n");
            return 1;
        }
    }
    std::puts("worker_guard_test: worker exceptions are contained and published");
    return 0;
}
