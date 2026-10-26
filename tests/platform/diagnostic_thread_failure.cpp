#include "strata/platform/diagnostic_thread.hpp"

#include <atomic>
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <thread>

int main() {
    std::atomic<bool> entered{false};
    strata::platform::start_diagnostic_thread("diagnostic_thread_failure", [&] {
        entered.store(true, std::memory_order_release);
        throw std::runtime_error("injected body failure");
    });
    while (!entered.load(std::memory_order_acquire)) std::this_thread::yield();
    std::this_thread::sleep_for(std::chrono::milliseconds(100));   // an escaped exception would already have terminated us
    std::cout << "diagnostic_thread_failure: body exception did not escape the diagnostic thread\n";
}
