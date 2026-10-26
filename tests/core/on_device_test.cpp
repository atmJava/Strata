#include "strata/core/on_device.hpp"

#include <cstdio>
#include <cstdlib>

namespace {

int current_device = 0;
int set_calls = 0;
bool fail_get = false;
bool fail_set = false;

void check(bool condition, const char* message) {
    if (!condition) {
        std::fprintf(stderr, "on_device_test: %s\n", message);
        std::exit(1);
    }
}

}  // namespace

extern "C" cudaError_t __wrap_cudaGetDevice(int* device) {
    if (fail_get) return cudaErrorInvalidValue;
    *device = current_device;
    return cudaSuccess;
}

extern "C" cudaError_t __wrap_cudaSetDevice(int device) {
    ++set_calls;
    if (fail_set) return cudaErrorInvalidValue;
    current_device = device;
    return cudaSuccess;
}

int main() {
    {
        strata::core::OnDevice on(1);
        check(on.ok && on.error == cudaSuccess, "a successful switch must be reported");
        check(on.previous == 0, "the caller's device must be retained for restoration");
        check(current_device == 1, "the requested device must be current inside the scope");
    }
    check(current_device == 0, "scope exit must restore the caller's device");
    check(set_calls == 2, "scope exit must issue one restoration switch");

    set_calls = 0;
    {
        strata::core::OnDevice on(0);
        check(on.ok && on.previous == -1, "an already-current device needs no switch");
    }
    check(set_calls == 0, "an already-current device must not trigger driver calls");

    fail_set = true;
    {
        strata::core::OnDevice on(1);
        check(!on.ok && on.error == cudaErrorInvalidValue, "a failed switch must be reported");
        check(current_device == 0 && on.previous == -1, "a failed switch must not claim another device");
    }
    check(current_device == 0, "a failed switch must leave the caller's device unchanged");
    fail_set = false;

    fail_get = true;
    {
        strata::core::OnDevice on(1);
        check(!on.ok && on.error == cudaErrorInvalidValue, "a failed device query must be reported");
    }
    fail_get = false;

    set_calls = 0;
    {
        strata::core::OnDevice on(-1);
        check(on.ok && on.previous == -1, "a negative device remains a no-op");
    }
    check(set_calls == 0, "a negative device must not issue driver calls");
    std::puts("on_device_test: switch success, failure and restoration");
    return 0;
}
