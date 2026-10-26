#include "strata/core/scoped_value_restore.hpp"

#include <cstdio>
#include <cstdlib>
#include <stdexcept>

namespace {

void check(bool condition, const char* message) {
    if (condition) return;
    std::fprintf(stderr, "scoped_value_restore_test: %s\n", message);
    std::exit(1);
}

} // namespace

int main() {
    using strata::core::ScopedValueRestore;

    int explicit_value = 3;
    {
        ScopedValueRestore<int> restore(explicit_value);
        explicit_value = 9;
        restore.restore();
        check(explicit_value == 3, "an explicit restore did not put back the saved value");
        explicit_value = 7;
    }
    check(explicit_value == 7, "scope exit overwrote a value after an explicit restore");

    bool scope_value = false;
    {
        ScopedValueRestore<bool> restore(scope_value);
        scope_value = true;
    }
    check(!scope_value, "scope exit did not restore a value");

    int exception_value = 11;
    try {
        ScopedValueRestore<int> restore(exception_value);
        exception_value = 12;
        throw std::runtime_error("unwind");
    } catch (const std::runtime_error&) {
    }
    check(exception_value == 11, "exception unwinding did not restore a value");

    std::puts("scoped_value_restore_test: explicit, scope and exception restoration pass");
    return 0;
}
