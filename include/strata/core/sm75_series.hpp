// strata/core/sm75_series.hpp - the Turing (RTX 20, compute capability 7.5) kernel series.
//
// The Turing kernels of PRs #2069-#2072, #2081, #2091-#2093, #2145-#2146, #2220-#2223, #1821 were measured on one RTX
// 2080 Ti (and a CMP 50HX) and are bitwise equal to the kernels they replace.  We have no sm_75 card to confirm them, so
// they are ON ONLY WHEN ASKED FOR:
//
//   STRATA_SM75_PERF=1   turns on every bitwise Turing kernel of the series on a compute capability 7.5 card.
//   unset / =0           off
//
// (A branch that made the series default-on for a 7.5 card was measured nowhere and is not in this tree: several of
// these kernels round differently from the ones they replace, and this repo changes a default only where it was
// measured faster end to end - see docs/VLLM_PORT_PLAN.md §6.2.)
//
// Each kernel keeps the author's own switch, which wins over STRATA_SM75_PERF in both directions (=0 off, =1 on, on any
// card where the PR said so for tests).  Nothing here changes a card that is not a 7.5: the check is the card's compute
// capability (STRATA_EMULATE_CC=75 counts, so a 75-virtual build on an sm_86 card runs the series for tests).
//
// The numerics-changing Turing options are NOT part of this switch and keep their own controls:
//   STRATA_SELECT_SIMT=1|2 (FP32 tiled block scorer), STRATA_PA_V2T=0 (select v1 prompt attention; v2 is the default),
//   STRATA_PF_FUSED_SM75=1 (fused int8 prompt experts), STRATA_BF16_TC=1 (FP16 tensor-core projections).
#pragma once

#include "strata/core/emulate.hpp"

#include <cstdlib>

namespace strata {

/// STRATA_SM75_PERF: -1 unset, 0 off, 1 on (read once)
inline int sm75_series_setting() {
    static const int setting = [] {
        const char* e = std::getenv("STRATA_SM75_PERF");
        if (e == nullptr || e[0] == '\0') return -1;
        return std::atoi(e) != 0 ? 1 : 0;
    }();
    return setting;
}

/// STRATA_SM75_PERF=1 exactly: the series is off unless asked for (upstream's default; a card that is not a 7.5 never
/// reaches a Turing kernel - see sm75_default)
inline bool sm75_series() { return sm75_series_setting() == 1; }

/// the device (raw cudaDevAttrComputeCapabilityMajor / Minor) is a Turing card, or emulates one (STRATA_EMULATE_CC=75)
inline bool is_sm75(int major, int minor) { return cc_major_of(major) == 7 && cc_minor_of(minor) == 5; }

/// the default of a bitwise Turing kernel switch: on for a Turing card when STRATA_SM75_PERF=1
inline bool sm75_default(int major, int minor) { return is_sm75(major, minor) && sm75_series(); }

/// a Turing switch with its own variable: `name` set means 0 off / anything else on (Turing only); unset follows the series
inline bool sm75_switch(const char* name, int major, int minor) {
    if (!is_sm75(major, minor)) return false;
    const char* v = std::getenv(name);
    return v != nullptr ? std::atoi(v) != 0 : sm75_series();
}

}  // namespace strata
