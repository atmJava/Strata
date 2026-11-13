// include/strata/program/mtp_placement.hpp - where a layer split runs the MTP drafter.
//
// The first-stage placement belongs only to the pipelined, non-batch decode it was measured for.  A batch binds one
// drafter per slot on the last stage; moving that drafter to CUDA0 also takes its draft head out of the last stage's
// cache budget and broke concurrent MTP on the 2 x RTX 2080 Ti deployment.  Keep the rule header-only and test it
// without a GPU or model.
#pragma once

#include <cstddef>

namespace strata::program::mtp_placement {

struct Inputs {
    bool draft_head_ready = false;  ///< --mtp with --spec >= 2 and the draft vocabulary file
    int pipeline_windows = 0;       ///< --pipeline-windows
    std::size_t stages = 0;         ///< later GPU stages; stage 0 is the current device
    int batch = 0;                  ///< --batch slots, or the server's parallel slot count
};

/// True only when the first-stage placement can run.  Batch mode always keeps the drafter on the last stage.
inline bool on_first_stage(const Inputs& in) {
    return in.draft_head_ready && in.pipeline_windows >= 2 && in.stages != 0 && in.batch == 0;
}

/// CUDA0 when the drafter is on the first stage (or there is no split), else the last stage's device.
inline int drafter_device(const Inputs& in, int last_stage_device) {
    return in.stages != 0 && !on_first_stage(in) ? last_stage_device : 0;
}

}  // namespace strata::program::mtp_placement
