// tests/core/qsa_indexer_position_test.cpp - the indexer's position is relative to the sequence's first cell.
//
// The QSA layer uploads two position buffers for every token: `step[kStepPos]` is the cell number in this
// sequence, while `pos_dev` is the absolute RoPE row (`pos_base + step[kStepPos]`).  The table indexer takes
// the relative number and applies `pos_base` itself.  Passing `pos_dev` therefore pools under the absolute
// block number and applies the base twice when it rotates the pooled key; on a restored conversation that can
// read past the RoPE table near the end of the context.
#include "strata/core/layer.hpp"

#include <cstdint>
#include <cstdio>
#include <vector>

int main() {
    strata::core::QsaState st{};
    std::vector<int32_t> steps(strata::kernels::kStepCount + 1, 0);
    std::vector<int32_t> rope_positions{100, 100, 100, 100};
    st.step = steps.data();
    st.pos_dev = rope_positions.data();
    steps[strata::kernels::kStepPos] = 37;

    int failures = 0;
    const int32_t* relative = strata::core::qsa_indexer_relative_pos(st);
    if (relative != steps.data() + strata::kernels::kStepPos) {
        std::fprintf(stderr, "FAIL: the indexer position is not step[kStepPos]\n");
        ++failures;
    }
    if (relative == st.pos_dev) {
        std::fprintf(stderr, "FAIL: the indexer position aliases the absolute RoPE positions\n");
        ++failures;
    }
    if (relative == nullptr || *relative != 37) {
        std::fprintf(stderr, "FAIL: the indexer does not read the relative cell number\n");
        ++failures;
    }
    if (st.pos_dev == nullptr || *st.pos_dev != 100) {
        std::fprintf(stderr, "FAIL: the absolute RoPE staging fixture changed\n");
        ++failures;
    }

    if (failures == 0) std::printf("qsa_indexer_position_test: 4 checks passed\n");
    return failures == 0 ? 0 : 1;
}
