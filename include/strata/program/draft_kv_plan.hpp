// include/strata/program/draft_kv_plan.hpp - which code computes the draft layer's prompt K/V for a chunk.
//
// One GPU: the prompt path's batched pass (E-9, `Prefill::draft_kv`) tries first and the drafter's own
// per-8-row pass (`MtpDrafter::prefill`) takes what it declines.  A layer split: the drafter's own pass runs; the
// last stage's prompt path does not (its K/V would come from Q8_1 x Q8_0 MMQ instead of the drafter's own mmvq, so
// its drafts could differ a little; the target's tokens are decided by the verify window and do not).
//
// Pure (no GPU, no environment read): generate.cpp passes in what it knows.
#pragma once

namespace strata::program::draft_kv {

struct Plan {
    bool try_batched = false;      ///< call Prefill::draft_kv for this chunk
    const char* why = nullptr;     ///< when `try_batched` is false only because of a missing prerequisite: what is missing
};

/// `use_mtp`: the engine runs a draft layer. `multi_gpu`: a layer split. `has_native_embed`: the GGUF-form token
/// embedding table exists (a --native pack): the split's last stage reads it, a single GPU's path can also gather
/// from its own weights.
inline Plan plan(bool use_mtp, bool multi_gpu, bool has_native_embed) {
    Plan p;
    if (!use_mtp) return p;
    if (!multi_gpu) {
        p.try_batched = true;
        return p;
    }
    return p;   // the default on a split: the drafter's own pass, nothing to say
}

}  // namespace strata::program::draft_kv
