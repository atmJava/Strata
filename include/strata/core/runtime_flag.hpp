#pragma once
// A switch that can change while the engine runs.
//
// **WHY THIS EXISTS.**  Every switch in this engine is read once, into a function-local `static` (`split_help_env`,
// `old_kernels`, `stream_ahead_enabled`, `expdec_mode`, ...).  An A/B therefore needs a restart per arm (a cold load
// is ~190 s here), and the arms land in different boots - which is the one thing the measurements cannot survive:
// the same configuration has read 127.4 / 133.0 / 139.1 tok/s in three boots on this box (9%), so anything under
// ~5% is unprovable by construction.  A flag file removes the restart from the loop: the arms alternate inside one
// boot, and the difference is measured against the same clocks, the same resident set and the same page cache.
//
// **WHAT IT IS NOT.**  It is not a second configuration system: with no file (the default) every caller keeps its
// `getenv()` behaviour exactly, so the default path is unchanged.  Values are strings; the caller parses.
//
// File format: `name=value` per line, `#` starts a comment, blank lines ignored.
// Path: `STRATA_FLAG_FILE`, else `./strata-flags` (the engine's working directory).
// Re-read at most every 100 ms and only when the file's mtime/size changed, so a loop that asks per kernel launch
// costs one clock read.
#include <sys/stat.h>

#include <chrono>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <mutex>
#include <string>

namespace strata::core {

namespace runtime_flags_detail {

struct State {
    std::map<std::string, std::string> values;
    std::string path;
    bool loaded = false;
    bool missing = true;
    long long mtime_ns = 0;
    long long size = 0;
    std::chrono::steady_clock::time_point checked{};
};

inline State& state() {
    static State s;
    return s;
}

/// The file's identity, or false when it cannot be stat'ed (absent = the default path, not an error).
inline bool fingerprint(const std::string& p, long long& mtime_ns, long long& size) {
    struct stat st;
    if (p.empty() || ::stat(p.c_str(), &st) != 0) return false;
    mtime_ns = (long long) st.st_mtim.tv_sec * 1000000000LL + (long long) st.st_mtim.tv_nsec;
    size = (long long) st.st_size;
    return true;
}

inline void refresh(State& s) {
    const auto now = std::chrono::steady_clock::now();
    if (now - s.checked < std::chrono::milliseconds(100)) return;   // one clock read per kernel launch
    s.checked = now;
    long long mt = 0, sz = 0;
    if (!fingerprint(s.path, mt, sz)) {
        s.missing = true;
        if (s.loaded) {   // the file went away: fall back to the environment, exactly as if it never existed
            s.values.clear();
            s.loaded = false;
        }
        return;
    }
    s.missing = false;
    if (s.loaded && mt == s.mtime_ns && sz == s.size) return;   // unchanged since the last parse
    std::ifstream in(s.path);
    if (!in) return;
    std::map<std::string, std::string> vals;
    std::string line;
    while (std::getline(in, line)) {
        const size_t hash = line.find('#');
        if (hash != std::string::npos) line.resize(hash);
        const size_t eq = line.find('=');
        if (eq == std::string::npos) continue;
        auto trim = [](std::string v) {
            const char* ws = " \t\r\n";
            const size_t b = v.find_first_not_of(ws);
            if (b == std::string::npos) return std::string();
            const size_t e = v.find_last_not_of(ws);
            return v.substr(b, e - b + 1);
        };
        std::string key = trim(line.substr(0, eq));
        if (key.empty()) continue;
        vals[key] = trim(line.substr(eq + 1));
    }
    s.values.swap(vals);
    s.loaded = true;
    s.mtime_ns = mt;
    s.size = sz;
}

}  // namespace runtime_flags_detail

/// The value of `name` in the flag file, or false when there is no file or no such key (the caller then uses its
/// own environment/default rule, so this changes nothing by default).
inline bool runtime_flag(const char* name, std::string& out) {
    using namespace runtime_flags_detail;
    static std::mutex mu;
    std::lock_guard<std::mutex> lock(mu);
    State& s = state();
    if (!s.loaded && s.path.empty()) {
        const char* p = std::getenv("STRATA_FLAG_FILE");
        s.path = p != nullptr ? p : "strata-flags";
    }
    refresh(s);
    if (!s.loaded) return false;
    const auto it = s.values.find(name);
    if (it == s.values.end()) return false;
    out = it->second;
    return true;
}

inline bool runtime_flag_int(const char* name, long long& out) {
    std::string v;
    if (!runtime_flag(name, v) || v.empty()) return false;
    char* end = nullptr;
    const long long n = std::strtoll(v.c_str(), &end, 10);
    if (end == v.c_str()) return false;
    out = n;
    return true;
}

inline bool runtime_flag_double(const char* name, double& out) {
    std::string v;
    if (!runtime_flag(name, v) || v.empty()) return false;
    char* end = nullptr;
    const double d = std::strtod(v.c_str(), &end);
    if (end == v.c_str()) return false;
    out = d;
    return true;
}

}  // namespace strata::core
