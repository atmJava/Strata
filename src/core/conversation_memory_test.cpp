#include "strata/core/conversation_memory.hpp"

#include <cstdio>
#include <cstdlib>
#include <limits>
#include <sstream>

using namespace strata::core;
namespace {
int checks = 0;
void check(bool ok, const char* label) {
    ++checks;
    if (!ok) { std::fprintf(stderr, "FAIL: %s\n", label); std::exit(1); }
}
}

int main() {
    for (const char* text : {"", "MemFree: 100 kB\n", "MemAvailable: -1 kB\n",
                            "MemAvailable: +1 kB\n", "MemAvailable: 1 MB\n",
                            "MemAvailable: 1\n", "MemAvailable: 1x kB\n",
                            "MemAvailable: 18446744073709551615 kB\n",
                            "MemAvailable: 18446744073709551616 kB\n",
                            "MemAvailable: 1 kB trailing\n",
                            "MemAvailable: 1 kB\nMemAvailable: 2 kB\n"}) {
        std::istringstream input(text);
        check(!conversation_mem_available(input), "missing/malformed telemetry is unknown");
    }
    std::istringstream normal("MemTotal: 999999 kB\nMemAvailable:    12345 kB\nSwapFree: 777 kB\n");
    check(conversation_mem_available(normal) == 12345ULL * 1024, "only MemAvailable is counted");
    std::istringstream zero("MemAvailable: 0 kB");
    check(conversation_mem_available(zero) == 0, "zero available memory is known");
    std::istringstream broken("MemAvailable: 123 kB\n");
    broken.setstate(std::ios::badbit);
    check(!conversation_mem_available(broken), "I/O failure declines admission");
    check(!conversation_memory_admit({}, 0, 0), "unknown fails closed even with zero floor");
    check(conversation_memory_admit(100, 40, 60), "exact allocation plus floor fits");
    check(!conversation_memory_admit(99, 40, 60), "one byte below required memory rejected");
    check(!conversation_memory_admit(59, 0, 60), "floor subtraction cannot underflow");
    check(conversation_memory_admit(60, 0, 60), "post-capture floor check");
    check(!conversation_memory_admit(100, std::numeric_limits<uint64_t>::max(), 1), "allocation arithmetic cannot overflow");
    check(conversation_memory_admit(std::numeric_limits<uint64_t>::max(),
                                   std::numeric_limits<uint64_t>::max(), 0), "maximal exact bound");
    check(!conversation_memory_admit(std::numeric_limits<uint64_t>::max(),
                                    std::numeric_limits<uint64_t>::max(), 1), "maximal sum overflow rejected");
    // #3493: the budget used when --conversation-cache-mib is not given
    const auto GiB = [](uint64_t n) { return n << 30; };
    check(auto_conversation_cache_mib({}, 2560) == 0, "unknown telemetry: no cache");
    check(auto_conversation_cache_mib(GiB(2), 2560) == 0, "below the parking floor: off");
    check(auto_conversation_cache_mib(GiB(4), 2560) == 0, "4 GiB free: 40% of 1.5 GiB is under 1 GiB: off");
    check(auto_conversation_cache_mib(GiB(8), 2560) == 2048, "8 GiB free: 40% of 5.5 GiB, in steps of 256");
    check(auto_conversation_cache_mib(GiB(20), 2560) == 7168, "20 GiB free: 40% of 17.5 GiB");
    check(auto_conversation_cache_mib(GiB(40), 2560) == 8192, "capped at 8 GiB");
    check(auto_conversation_cache_mib(GiB(1000), 0) == 8192, "a big server: still the cap");
    check(auto_conversation_cache_mib(GiB(40), 1000000) == 0, "a floor above the RAM: off, no underflow");
    // The working-set path: five conversations at the 160K compaction point, times the engine's per-token cost.  The
    // INT8 number on this deployment is 14,220 B/token, so the set is 5 * 163,840 * 14,220 = 11,109.4 MiB and the
    // budget must hold all of it: 11,264 in steps of 256, not 11,008.
    check(kWorkingSetConversations == 5 && kWorkingSetConversationTokens == 163840, "the working set is DSH's shape");
    check(auto_conversation_cache_mib(GiB(40), 2560, 14220) == 11264, "INT8: the whole working set, rounded up");
    check(auto_conversation_cache_mib(GiB(40), 2560, 8460) == 6656, "a four-bit K/V: the same set costs less");
    check(auto_conversation_cache_mib(GiB(40), 2560, 14220) == auto_conversation_cache_mib(GiB(1000), 2560, 14220),
          "more RAM than the set needs does not buy more cache");
    check(auto_conversation_cache_mib(GiB(12), 2560, 14220) == 9728, "less RAM than the set: as much as the RAM allows");
    check(auto_conversation_cache_mib(GiB(4), 2560, 14220) == 1536, "a small box: whatever is left, still in steps");
    check(auto_conversation_cache_mib(GiB(3), 2560, 14220) == 0, "too little left for 1 GiB: off");
    check(auto_conversation_cache_mib({}, 2560, 14220) == 0, "unknown telemetry is still no cache");
    // Test the real provider without assuming any particular amount of free RAM.
    const auto available = available_host_bytes();
    check(!available || conversation_memory_admit(available, 0, 0), "provider returns bytes or unknown");
    std::printf("conversation_memory_test: %d checks passed\n", checks);
}
