#include "strata/platform/direct_file.hpp"
#include <atomic>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <unistd.h>
using strata::platform::DirectFile;
using strata::platform::Completion;
static void check(bool ok, const char* why) { if (!ok) throw std::runtime_error(why); }
int main() {
    char name[] = "/tmp/strata-direct-test-XXXXXX";
    int fd = mkstemp(name);
    if (fd < 0) return 1;
    close(fd);
    void* memory = DirectFile::alloc_aligned(4096 * 18);
    void* race_memory = nullptr;
    try {
        check(memory != nullptr, "buffer allocation");
        { std::ofstream f(name, std::ios::binary); std::string bytes(4096 * 16 + 17, 'x'); f.write(bytes.data(), bytes.size()); }
        DirectFile f;
        std::string error;
        setenv("STRATA_IO_THREADS", "4", 1);   // the pool's size (out-of-range values are clamped to 1..64)
        setenv("STRATA_TEST_DIRECT_DONE_FAIL", "1", 1);
        check(!f.is_open(), "new handle reports open");
        check(!f.submit(0, memory, 4096, 0, error) && error.find("not open") != std::string::npos,
              "closed handle accepted a request");
        check(f.open(name, error), error.c_str());
        unsetenv("STRATA_TEST_DIRECT_DONE_FAIL");
        check(f.submit(0, memory, 4096, 200, error), error.c_str());
        Completion fallback;
        check(f.wait(&fallback, 1, 5000) == 1 && fallback.tag == 200 && fallback.ok &&
                  fallback.bytes == 4096 && static_cast<char*>(memory)[0] == 'x',
              "completion-queue allocation fallback lost or changed the read result");
        check(!f.submit(1, memory, 4096, 0, error), "unaligned request accepted");
        setenv("STRATA_TEST_DIRECT_QUEUE_FAIL", "1", 1);
        check(!f.submit(0, memory, 4096, 0, error) && error.find("cannot queue") != std::string::npos,
              "queue allocation failure was not reported");
        unsetenv("STRATA_TEST_DIRECT_QUEUE_FAIL");
        for (unsigned i = 0; i < 17; ++i)
            check(f.submit(i * 4096, static_cast<char*>(memory) + i * 4096, 4096, i, error), error.c_str());
        std::vector<bool> seen(17);
        unsigned count = 0;
        while (count < 17) {
            Completion c[17]; int n = f.wait(c, 17, 5000);
            check(n > 0, "completion timeout");
            for (int i = 0; i < n; ++i) {
                check(c[i].tag < 17 && !seen[c[i].tag], "wrong or repeated completion");
                seen[c[i].tag] = true; ++count;
                check(c[i].ok && c[i].bytes == (c[i].tag == 16 ? 17u : 4096u), "incorrect full/EOF completion");
                check(static_cast<char*>(memory)[c[i].tag * 4096] == 'x', "read data mismatch");
            }
        }
        std::jthread waker([&] { f.wake(); });
        Completion c; check(f.wait(&c, 1, 5000) == 1 && c.tag == DirectFile::WAKE_TAG, "wake lost");
        waker.join();
        f.wake(); f.wake();
        Completion wakes[2];
        check(f.wait(wakes, 2, 5000) == 2 && wakes[0].tag == DirectFile::WAKE_TAG &&
              wakes[1].tag == DirectFile::WAKE_TAG, "allocation-free wakes were lost");
        check(f.submit(0, memory, 4096, 99, error), error.c_str());
        f.close(); // Must drain outstanding buffer writes before returning.
        std::memset(memory, 0, 4096);
        check(f.open(name, error), error.c_str());
        check(f.wait(&c, 1, 0) == 0, "stale completion on reopen");
        check(f.submit(0, memory, 4096, 100, error), error.c_str());
        check(f.wait(&c, 1, 5000) == 1 && c.ok && c.tag == 100, "reopened read failed");
        f.close();
        check(!f.is_open(), "closed handle still reports open");
        check(!f.submit(0, memory, 4096, 101, error) && error.find("not open") != std::string::npos,
              "closed handle accepted a request");

        DirectFile contended;
        race_memory = DirectFile::alloc_aligned(4096 * 4096);
        check(race_memory != nullptr, "concurrent buffer allocation");
        check(contended.open(name, error), error.c_str());
        std::atomic<size_t> accepted{0};
        std::thread producer([&] {
            for (size_t i = 0; i < 4096; ++i) {
                auto* buffer = static_cast<char*>(race_memory) + i * 4096;
                if (!contended.submit(0, buffer, 4096, i, error)) break;
                accepted.store(i + 1, std::memory_order_release);
            }
        });
        while (accepted.load(std::memory_order_acquire) == 0) std::this_thread::yield();
        contended.close();
        producer.join();
        check(accepted.load(std::memory_order_acquire) > 0 && !contended.is_open(),
              "concurrent submit/close");
        DirectFile::free_aligned(race_memory);
        race_memory = nullptr;

        check(!f.open("/tmp/strata-direct-test-no-such-file", error), "missing file opened");
        check(!f.is_open() && !f.submit(0, memory, 4096, 102, error), "failed open left the handle usable");

        DirectFile closing;
        check(closing.open(name, error), error.c_str());
        std::atomic<int> blocked_result{-1};
        std::atomic<bool> waiter_started{false};
        std::thread blocked([&] {
            Completion c;
            waiter_started.store(true, std::memory_order_release);
            blocked_result.store(closing.wait(&c, 1, -1), std::memory_order_release);
        });
        while (!waiter_started.load(std::memory_order_acquire)) std::this_thread::yield();
        closing.close();
        blocked.join();
        check(blocked_result.load(std::memory_order_acquire) == 0, "close did not wake a blocked wait");
        closing.wake();
        check(closing.open(name, error), error.c_str());
        check(closing.wait(&fallback, 1, 0) == 0, "wake after close leaked into the reopened handle");
        closing.close();

        DirectFile::free_aligned(memory); unlink(name);
        std::cout << "direct_file_async: passed queue/completion fallback, reads, short EOF, wakes, close drain, reopen\n";
        return 0;
    } catch (const std::exception& e) {
        DirectFile::free_aligned(race_memory);
        DirectFile::free_aligned(memory);
        unlink(name);
        std::cerr << e.what() << '\n'; return 1;
    }
}
