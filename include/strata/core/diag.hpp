#pragma once

namespace strata::core::diag {

/// The verifier's and the drafter's trace hooks stay in the code; nothing turns them on.
inline bool enabled() { return false; }

}  // namespace strata::core::diag
