// strata/core/sm75_series.hpp - the Turing (RTX 20, compute capability 7.5) kernel series.
//
// The Turing kernels of PRs #2069-#2072, #2081, #2091-#2093, #2145-#2146, #2220-#2223, #1821 were measured on one RTX
// 2080 Ti (and a CMP 50HX) and are bitwise equal to the kernels they replace.  This box is two RTX 2080 Ti, so the
// series is the default path here and is always on.
//
// Each kernel used to keep the author's own switch; the switches are gone with the series-wide one (a bitwise kernel
// needs no control).  Nothing here changes a card that is not a 7.5: the check is the card's compute capability
// (STRATA_EMULATE_CC=75 counts, so a 75-virtual build on an sm_86 card runs the series for tests).
//
// The numerics-changing Turing kernels are NOT part of this switch: the FP32 tiled block scorer and the FP16
// tensor-core projections are chosen by the code, not by a switch here.
#pragma once

#include "strata/core/emulate.hpp"

namespace strata {

/// The Turing kernels are on: this deployment's cards are compute 7.5.
inline bool sm75_series() { return true; }

/// the device (raw cudaDevAttrComputeCapabilityMajor / Minor) is a Turing card, or emulates one (STRATA_EMULATE_CC=75)
inline bool is_sm75(int major, int minor) { return cc_major_of(major) == 7 && cc_minor_of(minor) == 5; }

/// whether a bitwise Turing kernel applies to this device: 7.5 and only 7.5
inline bool sm75_default(int major, int minor) { return is_sm75(major, minor) && sm75_series(); }

}  // namespace strata
