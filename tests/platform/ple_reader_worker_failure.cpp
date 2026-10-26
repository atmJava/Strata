// The PLE reader's persistent worker is optional concurrency, but its failure must end open tickets instead of
// leaving collect() asleep forever.  This test injects an exception in the worker before it takes any work.
#include "strata/ngram/ple_reader.hpp"

#include <array>
#include <cstdio>
#include <cstdlib>
#include <string>

#include <unistd.h>

int main() {
    char path[] = "/tmp/strata-ple-worker-test-XXXXXX";
    const int fd = mkstemp(path);
    if (fd < 0) return 1;
    const bool sized = ftruncate(fd, 4 * 4096) == 0;
    close(fd);
    if (!sized) {
        unlink(path);
        return 1;
    }

    setenv("STRATA_TEST_PLE_WORKER_FAIL", "1", 1);
    strata::ngram::PleReader reader;
    std::string err;
    const bool opened = reader.open(path, 0, 4, 4, 0, err, true, 4096);
    const uint32_t row = 0;
    std::array<uint8_t, 4096> out{};
    const auto ticket = opened ? reader.issue(&row, 1, out.data()) : strata::ngram::PleReader::Ticket{};
    const bool collected = opened && reader.collect(ticket, err);
    reader.close();
    unsetenv("STRATA_TEST_PLE_WORKER_FAIL");
    unlink(path);

    if (!opened || collected || err.find("worker stopped") == std::string::npos) {
        std::fprintf(stderr, "ple_reader_worker_failure: FAILED (%s)\n", err.c_str());
        return 1;
    }
    std::printf("ple_reader_worker_failure: pending ticket completed with the worker error\n");
    return 0;
}
