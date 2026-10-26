// src/platform/direct_file.cpp - see include/strata/platform/direct_file.hpp.
#include "strata/platform/direct_file.hpp"
#include "strata/platform/aux_cpus.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <limits>
#include <mutex>
#include <new>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

namespace strata::platform {

double now_us() {
    using namespace std::chrono;
    return (double) duration_cast<nanoseconds>(steady_clock::now().time_since_epoch()).count() / 1000.0;
}

void* DirectFile::alloc_aligned(size_t bytes) {
    void* p = nullptr;
    return posix_memalign(&p, alignment(), bytes) == 0 ? p : nullptr;
}

void DirectFile::free_aligned(void* p) {
    if (p == nullptr) return;
    std::free(p);
}

namespace {
/// A queued read: `submit` only queues it; the pool's threads issue it (perf-review C-4 / F-2).  One thread
/// issuing every read was the limit of the n-gram table's prompt reads: ~12 us of kernel time per read, ~80K
/// reads/s, while an NVMe drive serves several times that at depth.
struct Pending {
    uint64_t offset;
    void* buffer;
    uint32_t length;
    uint64_t tag;
};

/// The number of issuing threads: STRATA_IO_THREADS, else 4 (Windows: overlapped submits) / 16 (Linux: each
/// thread does one blocking pread, so the thread count is the queue depth).
int io_threads(int dflt) {
    const char* v = std::getenv("STRATA_IO_THREADS");
    const int n = v ? std::atoi(v) : dflt;
    return std::clamp(n, 1, 64);
}
}  // namespace

// ------------------------------------------------------------------------------------------------ POSIX
// perf-review F-2: a pool of threads, each doing blocking O_DIRECT preads, so reads run in parallel (the thread
// count is the queue depth).  It replaced one synchronous pread inside `submit`, which read the n-gram table's
// rows one at a time.
struct DirectFile::Impl {
    int fd = -1;
    uint64_t size = 0;
    bool accepting = false;
    std::mutex control;
    std::mutex mu;
    std::condition_variable cv_work, cv_done;
    std::deque<Pending> queue;
    std::deque<Completion> done;
    uint32_t wake_pending = 0;
    bool stop = false;
    std::vector<std::thread> pool;
    // A worker cannot allocate after a read has completed without another failure path.  If the completion deque
    // cannot grow, the worker keeps its one result here until `wait` consumes it.  One slot per worker is enough:
    // a worker parks on that slot until the result is taken or the handle closes.
    std::array<Completion, 64> fallback{};
    std::array<bool, 64> fallback_ready{};
    std::atomic<int> injected_done_failures{0};
    std::condition_variable cv_space;

    void worker(size_t worker_id) {
        std::unique_lock<std::mutex> lk(mu);
        for (;;) {
            cv_work.wait(lk, [&] { return stop || !queue.empty(); });
            if (stop && queue.empty()) return;
            const Pending p = queue.front();
            queue.pop_front();
            lk.unlock();
            const ssize_t got = pread(fd, p.buffer, p.length, (off_t) p.offset);
            lk.lock();
            const Completion completion{p.tag, got < 0 ? 0u : (uint32_t) got, got >= 0};
            int remaining = injected_done_failures.load(std::memory_order_relaxed);
            while (remaining > 0 &&
                   !injected_done_failures.compare_exchange_weak(remaining, remaining - 1,
                                                                 std::memory_order_relaxed)) {}
            try {
                if (remaining > 0) throw std::bad_alloc();
                done.push_back(completion);
            } catch (...) {
                fallback[worker_id] = completion;
                fallback_ready[worker_id] = true;
                cv_done.notify_all();
                cv_space.wait(lk, [&] { return !fallback_ready[worker_id] || stop; });
            }
            cv_done.notify_all();
        }
    }

    void stop_pool() {
        {
            std::lock_guard<std::mutex> lk(mu);
            stop = true;
        }
        cv_work.notify_all();
        cv_space.notify_all();
        for (std::thread& t : pool) t.join();
        pool.clear();
        stop = false;
    }

    void close_locked() {
        {
            std::lock_guard<std::mutex> lk(mu);
            accepting = false;
        }
        stop_pool();
        std::lock_guard<std::mutex> lk(mu);
        if (fd >= 0) ::close(fd);
        fd = -1;
        size = 0;
        done.clear();
        queue.clear();
        wake_pending = 0;
        fallback_ready.fill(false);
        injected_done_failures.store(0, std::memory_order_relaxed);
        cv_done.notify_all();
    }
};

DirectFile::DirectFile() : impl_(new Impl) {}
DirectFile::~DirectFile() { close(); delete impl_; }

bool DirectFile::open(const std::string& path, std::string& err) {
    std::lock_guard<std::mutex> control(impl_->control);
    impl_->close_locked();
    const int fd = ::open(path.c_str(), O_RDONLY | O_DIRECT);
    if (fd < 0) { err = "DirectFile: cannot open " + path; return false; }
    struct stat st;
    if (fstat(fd, &st) != 0) {
        ::close(fd);
        err = "DirectFile: cannot size " + path;
        return false;
    }
    {
        std::lock_guard<std::mutex> lk(impl_->mu);
        impl_->fd = fd;
        impl_->size = (uint64_t) st.st_size;
        if (const char* value = std::getenv("STRATA_TEST_DIRECT_DONE_FAIL"))
            impl_->injected_done_failures.store(std::max(0, std::atoi(value)), std::memory_order_relaxed);
    }
    const int n = io_threads(16);
    try {
        for (int i = 0; i < n; ++i)
            impl_->pool.emplace_back([this, i] {
                strata::aux_cpus::pin_current_thread();
                impl_->worker((size_t) i);
            });
    } catch (const std::exception& e) {
        impl_->close_locked();
        err = "DirectFile: cannot start an I/O worker: " + std::string(e.what());
        return false;
    } catch (...) {
        impl_->close_locked();
        err = "DirectFile: cannot start an I/O worker";
        return false;
    }
    {
        std::lock_guard<std::mutex> lk(impl_->mu);
        impl_->accepting = true;
    }
    return true;
}

void DirectFile::close() {
    std::lock_guard<std::mutex> control(impl_->control);
    impl_->close_locked();
}

bool DirectFile::is_open() const {
    std::lock_guard<std::mutex> lk(impl_->mu);
    return impl_->accepting && impl_->fd >= 0;
}

uint64_t DirectFile::size() const {
    std::lock_guard<std::mutex> lk(impl_->mu);
    return impl_->size;
}

bool DirectFile::submit(uint64_t offset, void* buffer, uint32_t length, uint64_t tag, std::string& err) {
    if (buffer == nullptr || offset % alignment() || length % alignment() ||
        ((uintptr_t) buffer) % alignment() || length == 0) {
        err = "DirectFile: unaligned request";
        return false;
    }
    {
        std::lock_guard<std::mutex> lk(impl_->mu);
        if (!impl_->accepting || impl_->fd < 0) {
            err = "DirectFile: not open";
            return false;
        }
        try {
            if (std::getenv("STRATA_TEST_DIRECT_QUEUE_FAIL") != nullptr) throw std::bad_alloc();
            impl_->queue.push_back(Pending{offset, buffer, length, tag});
        } catch (const std::exception& e) {
            err = "DirectFile: cannot queue a request: " + std::string(e.what());
            return false;
        } catch (...) {
            err = "DirectFile: cannot queue a request";
            return false;
        }
    }
    impl_->cv_work.notify_one();
    return true;
}

void DirectFile::wake() {
    std::lock_guard<std::mutex> lk(impl_->mu);
    if (impl_->fd < 0) return;
    if (impl_->wake_pending != std::numeric_limits<uint32_t>::max()) ++impl_->wake_pending;
    impl_->cv_done.notify_all();
}

int DirectFile::wait(Completion* out, int max, int timeout_ms) {
    std::unique_lock<std::mutex> lk(impl_->mu);
    auto fallback_count = [&] {
        return (int) std::count(impl_->fallback_ready.begin(), impl_->fallback_ready.end(), true);
    };
    auto ready = [&] {
        return !impl_->done.empty() || impl_->wake_pending != 0 || fallback_count() != 0 || impl_->fd < 0;
    };
    if (!ready() && timeout_ms != 0) {
        if (timeout_ms < 0) impl_->cv_done.wait(lk, ready);
        else impl_->cv_done.wait_for(lk, std::chrono::milliseconds(timeout_ms), ready);
    }
    int n = 0;
    for (size_t i = 0; i < impl_->fallback_ready.size() && n < max; ++i) {
        if (!impl_->fallback_ready[i]) continue;
        out[n++] = impl_->fallback[i];
        impl_->fallback_ready[i] = false;
    }
    if (n != 0) impl_->cv_space.notify_all();
    while (n < max && impl_->wake_pending > 0) {
        out[n++] = Completion{WAKE_TAG, 0, true};
        --impl_->wake_pending;
    }
    while (n < max && !impl_->done.empty()) {
        out[n++] = impl_->done.front();
        impl_->done.pop_front();
    }
    return n;
}

}  // namespace strata::platform
