// The MTP drafter's layer-split placement: header-only, no GPU and no model.
#include "strata/program/mtp_placement.hpp"

#include <cstdio>
#include <initializer_list>

using strata::program::mtp_placement::drafter_device;
using strata::program::mtp_placement::Inputs;
using strata::program::mtp_placement::on_first_stage;

namespace {
int failures = 0;

void check(bool condition, const char* what) {
    if (condition) return;
    std::fprintf(stderr, "FAIL: %s\n", what);
    ++failures;
}
}  // namespace

int main() {
    const Inputs first{true, 2, 1, 0};
    check(on_first_stage(first), "a pipelined non-batch split uses the first stage");
    check(drafter_device(first, 1) == 0, "the first-stage drafter runs on CUDA0");

    const Inputs last{true, 2, 1, 2};
    check(!on_first_stage(last), "batch mode keeps the drafter on the last stage");
    check(drafter_device(last, 1) == 1, "the batch drafter runs on the last stage's device");

    check(!on_first_stage({false, 2, 1, 0}), "a missing draft head prevents first-stage placement");
    check(!on_first_stage({true, 1, 1, 0}), "one pipeline window is not the measured placement");
    check(!on_first_stage({true, 2, 0, 0}), "a single stage has no first-stage split placement");
    check(drafter_device({true, 2, 0, 0}, 1) == 0, "an unsplit model keeps the drafter on CUDA0");

    for (const int batch : {1, 2, 8}) {
        const Inputs in{true, 2, 1, batch};
        check(!on_first_stage(in), "every batch size keeps the drafter on the last stage");
        check(drafter_device(in, 1) == 1, "every batch size resolves CUDA1 as the drafter device");
    }

    if (failures == 0) std::printf("mtp_placement_test: ok\n");
    return failures == 0 ? 0 : 1;
}
