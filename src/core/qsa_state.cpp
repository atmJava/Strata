#include "strata/core/layer.hpp"

#include "strata/core/pin_budget.hpp"
#include "strata/kernels/mrope.hpp"

#include <cuda_runtime.h>

#include <sys/mman.h>

namespace strata::core {
namespace {

uint64_t g_kv_host_bytes = 0;

void released_kv_host_bytes(uint64_t bytes) {
    g_kv_host_bytes = bytes <= g_kv_host_bytes ? g_kv_host_bytes - bytes : 0;
}

} // namespace

uint64_t qsa_kv_host_bytes() {
    return g_kv_host_bytes;
}

void qsa_kv_host_allocated(uint64_t bytes) {
    g_kv_host_bytes += bytes;
}

void qsa_state_release(QsaState& st) {
    if (st.host_step != nullptr) {
        host_free_guarded(st.host_step);
        st.host_step = nullptr;
    }
    if (st.host_pos != nullptr) {
        host_free_guarded(st.host_pos);
        st.host_pos = nullptr;
    }
    if (st.kv_host_allocation != nullptr) {
        if (st.kv_host_registered) {
            (void) pin_unregister(st.kv_host_allocation);
            (void) munmap(st.kv_host_allocation, (size_t) st.kv_host_allocation_bytes);
        } else {
            host_free_guarded(st.kv_host_allocation);
        }
        released_kv_host_bytes(st.kv_host_allocation_bytes);
        st.kv_host_allocation = nullptr;
        st.kv_host_allocation_bytes = 0;
        st.kv_host_registered = false;
    }
    st.host = {};
    if (st.owns_rope) {
        strata::kernels::rope_table_release(st.cos_tab);
        st.owns_rope = false;
    }
}

} // namespace strata::core
