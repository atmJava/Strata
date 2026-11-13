// include/strata/program/mtp_placement.hpp - the one layout decision for the MTP drafter.
//
// The first-stage placement belongs only to the pipelined, non-batch decode it was measured for.  A run that asked
// for slots keeps every drafter on the last stage even if those slots later do not fit: changing the drafter's device
// after stage loading would also move its draft head out of the cache and VRAM planned for it.  Keep the rule
// header-only and test it without a GPU or model.
#pragma once

#include <cstddef>

namespace strata::program::mtp_placement {

/// Where the drafter lives after the layer split and slot layout are known from their inputs.
enum class DrafterStage {
    Unsplit,  ///< no layer split; the drafter stays on the process's primary device
    First,    ///< the pipelined, non-batch placement
    Last,     ///< the serial decode and every run that requested batch slots
};

struct Inputs {
    bool draft_head_ready = false;   ///< --mtp with --spec >= 2 and the draft-vocabulary subset
    int pipeline_windows = 0;        ///< the final --pipeline-windows decision
    std::size_t later_stages = 0;    ///< GPUs after the process's primary device
    int requested_slots = 0;         ///< --batch/--slots, or server "parallel"; fixed before fit checks
};

struct Layout {
    DrafterStage drafter = DrafterStage::Unsplit;
    bool pipelined_decode = false;
};

/// Resolve every input once.  A later reduction in the number of slots that fit must not call this function again
/// with zero slots: the device has already been priced into the split and expert-cache reservation.
inline Layout resolve(const Inputs& in) {
    Layout out;
    if (in.later_stages == 0) return out;

    // The prefill-only pipeline (window count 1) does not make the decode pipeline, and the first-stage drafter is
    // valid only for its decode pipeline.  Any requested slot keeps the drafter with the last stage's head and rows.
    out.pipelined_decode =
        in.draft_head_ready && in.pipeline_windows >= 2 && in.requested_slots == 0;
    out.drafter = out.pipelined_decode ? DrafterStage::First : DrafterStage::Last;
    return out;
}

/// The primary device for an unsplit or first-stage drafter, or the last stage's device otherwise.
inline int drafter_device(const Layout& layout, int primary_device, int last_stage_device) {
    return layout.drafter == DrafterStage::Last ? last_stage_device : primary_device;
}

}  // namespace strata::program::mtp_placement
