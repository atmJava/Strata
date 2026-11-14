#include "strata/program/split_ring.hpp"

#include <cstdint>
#include <cstdio>
#include <vector>

using namespace strata::program;

#define CHECK(cond)                                                                       \
    do {                                                                                  \
        if (!(cond)) {                                                                    \
            std::printf("split_ring_test FAIL line %d: %s\n", __LINE__, #cond);           \
            return 1;                                                                     \
        }                                                                                 \
        ++checks;                                                                         \
    } while (0)

// a cache of `slots` slots whose blobs vary per layer like a native pack's (2.4 .. 3.6 MB), as the prefix sum the
// expert cache keeps
static std::vector<uint64_t> make_offsets(int64_t slots) {
    std::vector<uint64_t> off((size_t) slots + 1, 0);
    for (int64_t i = 0; i < slots; ++i) off[(size_t) i + 1] = off[(size_t) i] + 2400000ull + (uint64_t) (i % 13) * 100000ull;
    return off;
}

int main() {
    int checks = 0;

    // 1. the ring the split chooses: 96 when most experts are resident, 0 = the pinned-share rule
    CHECK(split_ring_slots(0.9) == 96);
    CHECK(split_ring_slots(0.75) == 96);
    CHECK(split_ring_slots(0.74) == 0);
    CHECK(split_ring_slots(0.1) == 0);

    // 2. the lend count: uniform slots round up, sized slots walk from the end
    CHECK(split_lend_slots(1000, 1000 * 100, nullptr, 100, 0) == 0);
    CHECK(split_lend_slots(1000, 1000 * 100, nullptr, 100, 1) == 1);
    CHECK(split_lend_slots(1000, 1000 * 100, nullptr, 100, 100) == 1);
    CHECK(split_lend_slots(1000, 1000 * 100, nullptr, 100, 101) == 2);
    CHECK(split_lend_slots(1000, 0, nullptr, 0, 500) == 0);   // no blob size: nothing to count
    {
        const std::vector<uint64_t> off = make_offsets(50);
        const int64_t bytes = (int64_t) off.back();
        CHECK(split_lend_slots(50, bytes, off.data(), 3600000, 0) == 0);
        const uint64_t last = off[50] - off[49];
        CHECK(split_lend_slots(50, bytes, off.data(), 3600000, last) == 1);
        CHECK(split_lend_slots(50, bytes, off.data(), 3600000, last + 1) == 2);
        CHECK(split_lend_slots(50, bytes, off.data(), 3600000, off[50]) == 50);
        CHECK(split_lend_slots(50, bytes, off.data(), 3600000, off[50] + 1) == 50);   // never past the cache
    }

    // 3. the order: the RAM copy's lend region and the borrow must be sized with the SAME ring.  The need of a chunk is
    //    its own buffers plus ring_slots * max_blob (Prefill::bytes_needed); the engine used to size the region with
    //    the default ring and apply the override afterwards, so a bigger ring borrowed more slots than the region kept.
    {
        const int64_t slots = 6000;
        const std::vector<uint64_t> off = make_offsets(slots);
        const int64_t bytes = (int64_t) off.back();
        const uint64_t max_blob = 3600000, buffers = 3000ull << 20;
        auto need = [&](int ring) { return buffers + (uint64_t) ring * max_blob; };
        auto lend = [&](int ring) { return split_lend_slots(slots, bytes, off.data(), max_blob, need(ring)); };

        const int ring = split_ring_slots(0.9);   // most experts resident: the 96-slot ring
        const int64_t borrowed = lend(ring);      // what the prompt path borrows with the ring in force
        const int64_t kept = lend(split_ring_slots(0.9));   // decided first, then sized
        CHECK(kept == borrowed);                  // the copy keeps every borrowed slot
        // few experts resident (production: the pinned-share rule stays, ring 96 either way): nothing changes
        CHECK(lend(split_ring_slots(0.5) > 0 ? split_ring_slots(0.5) : 96) == lend(96));
        // a bigger ring borrows monotonically more
        CHECK(lend(96) <= lend(256) && lend(256) <= lend(384) && lend(384) <= lend(512));
    }

    std::printf("split_ring_test OK (%d checks)\n", checks);
}
