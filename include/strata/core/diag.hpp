// include/strata/core/diag.hpp - the one opt-in diagnostic switch shared by incident probes.
//
// STRATA_DIAG is not a performance decision or a supported tuning knob.  It is off by default and may be enabled for
// one controlled run while tracing a failure already under investigation.
#pragma once

#include <cstdlib>

namespace strata::core::diag {

inline bool enabled() {
    static const bool on = [] {
        const char* value = std::getenv("STRATA_DIAG");
        return value != nullptr && value[0] != '\0' && value[0] != '0';
    }();
    return on;
}

}  // namespace strata::core::diag
