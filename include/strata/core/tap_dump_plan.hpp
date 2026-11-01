// include/strata/core/tap_dump_plan.hpp - which verifier tap buffers are needed.
//
// DFlash2 needs only tap_dev_.  STRATA_TAP_FILE also needs the mapped host copy
// (and, on builds without the DFlash2 kernels, the per-row scratch).  A layer
// split cannot safely write one path without "%d": every stage opening that
// path with "wb" would truncate the previous stage's records.  Such a stage
// therefore records no dump, unless the drafter separately requested taps.
#pragma once

#include <cstdint>

namespace strata::core {

struct TapDumpPlan {
    bool record;      ///< allocate tap_dev_ and record the requested layers
    bool dump_copy;   ///< allocate the dump-only scratch and mapped host copy
};

inline TapDumpPlan tap_dump_plan(bool has_path, bool path_per_stage, int64_t layer_lo, int64_t layer_hi,
                                 int64_t n_layers, bool drafter_requested) {
    const bool whole_model = layer_lo == 0 && layer_hi == n_layers;
    const bool writes_dump = has_path && (path_per_stage || whole_model);
    return {drafter_requested || writes_dump, writes_dump};
}

}  // namespace strata::core
