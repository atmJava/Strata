#include "strata/platform/progress_wait.hpp"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <thread>

int main() {
    {
        std::atomic<size_t> published{0};
        const std::atomic<bool> failed{false};
        std::thread producer([&] {
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
            published.store(1, std::memory_order_release);
        });
        const bool ready = strata::platform::wait_for_progress(published, 0, failed);
        producer.join();
        if (!ready) {
            std::fprintf(stderr, "progress_wait_test: a published item was missed\n");
            return 1;
        }
    }
    {
        const std::atomic<size_t> published{0};
        std::atomic<bool> failed{false};
        std::thread producer([&] {
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
            failed.store(true, std::memory_order_release);
        });
        const bool ready = strata::platform::wait_for_progress(published, 0, failed);
        producer.join();
        if (ready) {
            std::fprintf(stderr, "progress_wait_test: a producer failure looked like progress\n");
            return 1;
        }
    }
    std::puts("progress_wait_test: success and failure both wake the waiter");
    return 0;
}
