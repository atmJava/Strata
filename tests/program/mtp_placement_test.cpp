// The MTP drafter's layer-split placement: header-only, no GPU and no model.
#include "strata/program/mtp_placement.hpp"

#include <cstdio>

using strata::program::mtp_placement::DrafterStage;
using strata::program::mtp_placement::drafter_device;
using strata::program::mtp_placement::Inputs;
using strata::program::mtp_placement::resolve;

namespace {
int failures = 0;

void check(bool condition, const char* what) {
    if (condition) return;
    std::fprintf(stderr, "FAIL: %s\n", what);
    ++failures;
}

void check_layout(Inputs in, DrafterStage stage, bool pipelined_decode, const char* what) {
    const auto layout = resolve(in);
    check(layout.drafter == stage && layout.pipelined_decode == pipelined_decode, what);
}
}  // namespace

int main() {
    const auto first = resolve({true, 2, 1, 0});
    check(first.drafter == DrafterStage::First && first.pipelined_decode,
          "a pipelined non-batch split uses the first stage");
    check(drafter_device(first, 0, 1) == 0, "the first-stage drafter runs on the primary device");

    const auto unsplit = resolve({true, 2, 0, 0});
    check(unsplit.drafter == DrafterStage::Unsplit && !unsplit.pipelined_decode,
          "an unsplit model has no layer-split placement");
    check(drafter_device(unsplit, 2, 3) == 2, "an unsplit drafter follows the primary device, not literal CUDA0");

    check_layout({false, 2, 1, 0}, DrafterStage::Last, false,
                 "a full draft head prevents first-stage placement");
    check_layout({true, 0, 1, 0}, DrafterStage::Last, false,
                 "pipeline off keeps the serial last-stage placement");
    check_layout({true, 1, 1, 0}, DrafterStage::Last, false,
                 "the prefill-only pipeline keeps the drafter on the last stage");
    check_layout({true, 2, 1, 1}, DrafterStage::Last, false,
                 "one requested slot pins the drafter to the last stage");
    check_layout({true, 2, 1, 2}, DrafterStage::Last, false,
                 "two requested slots pin the drafter to the last stage");
    check_layout({true, 2, 1, 8}, DrafterStage::Last, false,
                 "eight requested slots pin the drafter to the last stage");
    check(drafter_device(resolve({true, 2, 1, 2}), 0, 1) == 1,
          "a batch drafter uses the actual last-stage device");

    // Command-line --batch, the alias --slots and the server's "parallel" all reach this input as requested_slots.
    // The fit pass may reduce that count or disable batching later; it must not recompute the layout as unsplit.
    check_layout({true, 2, 1, 4}, DrafterStage::Last, false,
                 "--parallel/--batch requested slots keep the final layout stable");

    if (failures == 0) std::printf("mtp_placement_test: ok\n");
    return failures == 0 ? 0 : 1;
}
