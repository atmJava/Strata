// tests/core/tap_dump_plan_test.cpp - keep unreachable STRATA_TAP_FILE work out of a layer-split stage.
#include "strata/core/tap_dump_plan.hpp"

#include <cstdio>

namespace {

int failures = 0;

void check(bool ok, const char* what) {
    if (ok) return;
    std::fprintf(stderr, "FAIL: %s\n", what);
    ++failures;
}

}  // namespace

int main() {
    using strata::core::tap_dump_plan;

    {
        const auto p = tap_dump_plan(false, false, 0, 48, 48, false);
        check(!p.record && !p.dump_copy, "no path and no drafter allocates nothing");
    }
    {
        const auto p = tap_dump_plan(false, false, 0, 24, 48, true);
        check(p.record && !p.dump_copy, "drafter taps need tap_dev_ but no dump-only buffers");
    }
    {
        const auto p = tap_dump_plan(true, false, 0, 48, 48, false);
        check(p.record && p.dump_copy, "a whole-model dump writes one path");
    }
    {
        const auto p = tap_dump_plan(true, false, 24, 48, 48, false);
        check(!p.record && !p.dump_copy, "a split stage with one shared path does not record");
    }
    {
        const auto p = tap_dump_plan(true, false, 24, 48, 48, true);
        check(p.record && !p.dump_copy, "drafter taps survive a non-writing split stage");
    }
    {
        const auto p = tap_dump_plan(true, true, 24, 48, 48, false);
        check(p.record && p.dump_copy, "a per-stage path writes each split stage");
    }

    if (failures != 0) return 1;
    std::puts("tap_dump_plan_test: passed");
    return 0;
}
