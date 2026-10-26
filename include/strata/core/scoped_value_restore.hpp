#pragma once

namespace strata::core {

/// Restores a value on scope exit unless restore() already did so explicitly.
template <typename T>
class ScopedValueRestore {
public:
    explicit ScopedValueRestore(T& value) : value_(value), saved_(value) {}
    ~ScopedValueRestore() { restore(); }

    ScopedValueRestore(const ScopedValueRestore&) = delete;
    ScopedValueRestore& operator=(const ScopedValueRestore&) = delete;

    void restore() {
        if (!active_) return;
        value_ = saved_;
        active_ = false;
    }

private:
    T& value_;
    T saved_;
    bool active_ = true;
};

} // namespace strata::core
