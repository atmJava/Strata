// The PLE worker updates its counters while a keep-alive read is outstanding. stats() must return a consistent
// snapshot rather than a reference into data the worker is concurrently changing.
#include "strata/ngram/ple_reader.hpp"
#include "strata/platform/direct_file.hpp"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <thread>

#include <unistd.h>

int main() {
    char path[] = "/tmp/strata-ple-stats-test-XXXXXX";
    const int fd = mkstemp(path);
    if (fd < 0) return 1;
    const bool sized = ftruncate(fd, 4 * strata::ngram::PAGE) == 0;
    close(fd);
    if (!sized) {
        unlink(path);
        return 1;
    }

    strata::ngram::PleReader reader;
    std::string error;
    if (!reader.open(path, 0, 4, 4, 0, error, true, strata::ngram::PAGE)) {
        std::fprintf(stderr, "ple_reader_stats_snapshot: open failed: %s\n", error.c_str());
        unlink(path);
        return 1;
    }

    reader.set_keepalive(2.0, 2.0);
    const uint32_t row = 0;
    void* raw = strata::platform::DirectFile::alloc_aligned(strata::ngram::PAGE);
    if (raw == nullptr) {
        reader.close();
        unlink(path);
        return 1;
    }
    uint8_t* out = static_cast<uint8_t*>(raw);
    if (!reader.collect(reader.issue(&row, 1, out), error)) {
        std::fprintf(stderr, "ple_reader_stats_snapshot: collect failed: %s\n", error.c_str());
        strata::platform::DirectFile::free_aligned(raw);
        reader.close();
        unlink(path);
        return 1;
    }

    uint64_t last = reader.stats().keepalive_reads;
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(300);
    while (std::chrono::steady_clock::now() < deadline) {
        const strata::ngram::ReaderStats stats = reader.stats();
        if (stats.keepalive_reads < last || stats.read_us.size() > 65536) {
            std::fprintf(stderr, "ple_reader_stats_snapshot: inconsistent snapshot\n");
            strata::platform::DirectFile::free_aligned(raw);
            reader.close();
            unlink(path);
            return 1;
        }
        last = stats.keepalive_reads;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }

    const uint64_t keepalives = reader.stats().keepalive_reads;
    strata::platform::DirectFile::free_aligned(raw);
    reader.close();
    unlink(path);
    if (keepalives == 0) {
        std::fprintf(stderr, "ple_reader_stats_snapshot: keep-alive did not update statistics\n");
        return 1;
    }
    std::printf("ple_reader_stats_snapshot: concurrent statistics returned consistent copies\n");
    return 0;
}
