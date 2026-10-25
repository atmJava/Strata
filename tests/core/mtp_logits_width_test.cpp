// tests/core/mtp_logits_width_test.cpp - the MTP draft logits' width, bind_bytes' increment, and the per-card /
// per-architecture constants the drafter's sizing depends on.
//
// THE CONTRACTS PINNED HERE.
//   * `MtpDrafter::bind_bytes` prices `max_t_` rows over the DRAFT HEAD'S token subset (n_dvocab, from
//     rt/draft_vocab.bin or --draft-vocab) or over the whole vocabulary when there is no subset
//     (src/core/mtp.cpp:545-572, mtp.hpp:80-83).  Full vocabulary minus subset therefore costs exactly
//     `max_t * (n_vocab - n_subset) * 4` bytes of logits.
//   * It returns only the part BEYOND what the drafter already holds - `want - head_logits_cap_` floats - so a
//     second call after `bind()` sized the buffer exactly is 0 (src/core/mtp.cpp:561-565).
//   * dflash2_auto_cap: Ampere/Ada (cc 8) and Turing (cc 7) get NO cap (0 = the --spec value is left alone),
//     Blackwell sm_120 gets 6 and Strix Halo/UMA 5 (include/strata/core/draft_cap.hpp:8-16; the "Ampere-like
//     returns 0" claim is docs/VLLM_PORT_PLAN.md:70).
//   * The Turing series is a 7.5-and-only-7.5 predicate, on by default there and unable to touch any other
//     compute capability (include/strata/core/sm75_series.hpp:24-45, include/strata/core/tc_sm75.hpp:29-31).
//
// TWO MODES, THE SAME ASSERTIONS.  `STRATA_TEST_REAL_ENGINE` (set by CMakeLists.txt when `TARGET strata_engine`
// exists, i.e. a CUDA or HIP build) calls the real `MtpDrafter::bind_bytes`.  Its width inputs are private and
// the only public setter (`load()`) needs a device and the real model files, so this file reaches them through
// the test-only `#define private public` below - it changes no layout and mtp.cpp is compiled normally.  Without
// the macro (the CPU-only build: `mtp.cpp` is only in `strata_engine`, CMakeLists.txt:335 and :583) the formula
// is transcribed line by line with its source line on each line.  The transcription is also the independent
// oracle the real mode compares against.
#include "strata/core/draft_cap.hpp"
#include "strata/core/emulate.hpp"
#include "strata/core/geometry.hpp"
#include "strata/core/sm75_series.hpp"
#include "strata/core/tc_sm75.hpp"

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

#if defined(STRATA_TEST_REAL_ENGINE)
// Everything mtp.hpp includes, taken first with normal access - then the access-specifier change touches only
// MtpDrafter's own body (the include guards make these includes no-ops inside mtp.hpp).
#include "strata/core/coupled_draft.hpp"   // kCoupledHistCap, coupled_draft_env
#include "strata/core/pinned_ring.hpp"
#include "strata/core/session.hpp"
#include <functional>
#define private public
#include "strata/core/mtp.hpp"
#undef private
#endif

namespace {

int g_checks = 0;
int g_failures = 0;

void check(bool ok, const char* expr, const char* file, int line, const std::string& context) {
    ++g_checks;
    if (ok) return;
    ++g_failures;
    std::fprintf(stderr, "FAIL %s:%d: %s  [%s]\n", file, line, expr, context.c_str());
}

#define CHECK(cond, context) check((cond), #cond, __FILE__, __LINE__, (context))

void unset(const char* name) {
#if defined(_WIN32)
    _putenv_s(name, "");
#else
    unsetenv(name);
#endif
}

void put(const char* name, const char* value) {
#if defined(_WIN32)
    _putenv_s(name, value);
#else
    setenv(name, value, 1);
#endif
}

#if defined(STRATA_TEST_REAL_ENGINE)
using strata::core::kCoupledHistCap;
#else
// The CPU-only build cannot include coupled_draft.hpp (it pulls kernels/sampler.hpp -> <cuda_runtime.h>), so
// this is the one constant taken by value.  include/strata/core/coupled_draft.hpp:55.
constexpr int kCoupledHistCap = 4096;
#endif

// ================================ the transcription of bind_bytes ================================
//
// src/core/mtp.cpp:545-572.  `vocab_file_bytes` is the size `bind_bytes` would see in vocab_file(): 0 when the
// file is absent, and the source ignores a file smaller than 4 bytes or not a whole number of int32 tokens
// (:555-558).  `coupled_env` / `cparams` are the coupled-sampler terms (:570-571), included so the few bytes
// that only exist under STRATA_SPEC_COUPLED are not silently missing from the oracle.
struct BindWidths {
    int64_t max_t = 0;
    int64_t n_vocab = 0;
    int64_t n_dvocab = 0;
    int64_t vocab_file_bytes = 0;
    bool dhead = false;              ///< dhead_ != nullptr (a bound draft head)
    bool owns_draft_head = true;
    bool full_head = false;          ///< full_head_env()
    bool bound_logits = false;       ///< head_logits_ != nullptr
    int64_t head_logits_cap = 0;
    bool coupled_env = false;
    bool cparams = false;
};

int64_t n_subset_from_file(int64_t bytes) {
    return (bytes >= 4 && bytes % 4 == 0) ? bytes / 4 : 0;   // :555-558 (size >= 4 && size % 4 == 0)
}

uint64_t bind_bytes_transcription(const BindWidths& w, uint64_t head_row_bytes) {
    int64_t n_subset = 0;                                                              // :549
    if (w.dhead)                                                                       // :550
        n_subset = w.n_dvocab > 0 && w.n_dvocab < w.n_vocab ? w.n_dvocab : 0;          // :551
    else if (!w.full_head)                                                             // :552
        n_subset = n_subset_from_file(w.vocab_file_bytes);                             // :553-558
    const int64_t n_logits = n_subset > 0 ? n_subset : w.n_vocab;                      // :560
    const int64_t want = w.max_t * n_logits;                                           // :563
    const uint64_t have = w.bound_logits ? (uint64_t) w.head_logits_cap : 0;           // :564
    uint64_t bytes = want > 0 && (uint64_t) want > have
                         ? ((uint64_t) want - have) * sizeof(float) : 0;               // :565
    if (n_subset > 0 && !w.dhead && w.owns_draft_head)                                 // :566
        bytes += (uint64_t) n_subset * head_row_bytes + (uint64_t) n_subset * 4;       // :567
    if (w.coupled_env && !w.cparams)                                                   // :570
        bytes += (uint64_t) w.n_vocab * 4 + (uint64_t) (kCoupledHistCap + w.max_t) * 4 +
                 64ull * 64ull * 8ull + 4096;                                          // :571
    return bytes;
}

uint64_t logits_bytes(int64_t max_t, int64_t n_logits) {
    return max_t > 0 && n_logits > 0 ? (uint64_t) max_t * (uint64_t) n_logits * sizeof(float) : 0;
}

// ================================ real-mode access ================================

#if defined(STRATA_TEST_REAL_ENGINE)
std::vector<std::filesystem::path>& temp_files() {
    static std::vector<std::filesystem::path> files;
    return files;
}

/// A file of exactly `bytes` bytes (the width comes from its size alone); empty on failure.
std::string temp_vocab_file(int64_t bytes) {
    const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    const auto path = std::filesystem::temp_directory_path() /
                      ("strata_mtp_width_" + std::to_string((long long) stamp) + "_" + std::to_string(bytes) + ".bin");
    std::ofstream out(path, std::ios::binary);
    const std::vector<char> zeros((size_t) (bytes > 0 ? bytes : 1), 0);
    out.write(zeros.data(), (std::streamsize) bytes);
    out.close();
    if (!out) return {};
    temp_files().push_back(path);
    return path.string();
}

uint64_t bind_bytes_real(const BindWidths& w, uint64_t head_row_bytes) {
    // Default construction allocates nothing and touches no device (mtp.hpp:45); the destructor only frees
    // non-null pointers, so every fake pointer below is cleared before it runs.
    strata::core::MtpDrafter d;
    d.max_t_ = (int) w.max_t;
    d.n_dvocab_ = w.n_dvocab;
    d.owns_draft_head_ = w.dhead ? false : w.owns_draft_head;
    d.cparams_ = nullptr;
    d.dvocab_path_ = w.vocab_file_bytes > 0 ? temp_vocab_file(w.vocab_file_bytes)
                                           : "/nonexistent/strata_mtp_width_test/draft_vocab.bin";
    static float fake_logits = 0.0f;
    static uint8_t fake_head = 0;
    if (w.dhead) d.dhead_ = &fake_head;   // bind_bytes only compares it to nullptr
    if (w.bound_logits) {
        d.head_logits_ = &fake_logits;
        d.head_logits_cap_ = w.head_logits_cap;
    }
    const uint64_t out = d.bind_bytes(head_row_bytes, w.n_vocab);
    d.head_logits_ = nullptr;
    d.head_logits_cap_ = 0;
    d.dhead_ = nullptr;
    return out;
}
#endif

// ================================ the logits width ================================

void run_logits_width() {
    const int64_t n_vocab = 248320;   // the compiled vocabulary (geometry.hpp:27)
    constexpr uint64_t kRowBytes = 5120;
    CHECK(strata::core::kCompiledGeometry.n_vocab == n_vocab, "compiled n_vocab");

    // 1. FULL vs SUBSET, logits only (no draft-head rows in either, so the difference is the logits).
    for (int64_t max_t : {1, 4, 8, 63}) {
        for (int64_t n_subset : {int64_t{1}, int64_t{40000}, n_vocab - 1}) {
            BindWidths full;
            full.max_t = max_t;
            full.n_vocab = n_vocab;
            full.dhead = false;
            full.owns_draft_head = false;

            BindWidths subset = full;   // the width comes from the file when the head is not bound yet
            subset.vocab_file_bytes = n_subset * 4;

            BindWidths bound = full;    // ...and from n_dvocab_ once it is
            bound.dhead = true;
            bound.n_dvocab = n_subset;

            const uint64_t full_bytes = bind_bytes_transcription(full, kRowBytes);
            const uint64_t subset_bytes = bind_bytes_transcription(subset, kRowBytes);
            const uint64_t bound_bytes = bind_bytes_transcription(bound, kRowBytes);
            const std::string at = "max_t=" + std::to_string(max_t) + " n_subset=" + std::to_string(n_subset);

            CHECK(full_bytes == logits_bytes(max_t, n_vocab), at + " | full mode is max_t*n_vocab*4");
            CHECK(subset_bytes == logits_bytes(max_t, n_subset), at + " | file subset is max_t*n_subset*4");
            CHECK(bound_bytes == subset_bytes, at + " | a bound subset and a file subset have one width");
            CHECK(full_bytes - subset_bytes == (uint64_t) max_t * (uint64_t) (n_vocab - n_subset) * sizeof(float),
                  at + " | full - subset == max_t*(n_vocab - n_subset)*4");

#if defined(STRATA_TEST_REAL_ENGINE)
            CHECK(bind_bytes_real(full, kRowBytes) == full_bytes, at + " | real full mode == transcription");
            CHECK(bind_bytes_real(subset, kRowBytes) == subset_bytes, at + " | real file subset == transcription");
            CHECK(bind_bytes_real(bound, kRowBytes) == bound_bytes, at + " | real bound subset == transcription");
#endif
        }
    }

    // The headline number, for the record: 8 rows over 40000 of 248320 tokens.
    std::printf("logits: max_t=8 full = %llu B, subset(40000) = %llu B, difference = %llu B (= 8*(248320-40000)*4)\n",
                (unsigned long long) logits_bytes(8, n_vocab),
                (unsigned long long) logits_bytes(8, 40000),
                (unsigned long long) (logits_bytes(8, n_vocab) - logits_bytes(8, 40000)));

    // 2. A subset that still has to be GATHERED prices its head rows too (mtp.cpp:566-567): the logits
    //    difference above is unchanged, the total grows by n_subset * row_bytes + n_subset * 4.
    {
        BindWidths w;
        w.max_t = 8;
        w.n_vocab = n_vocab;
        w.vocab_file_bytes = 40000 * 4;
        w.owns_draft_head = true;
        const uint64_t with_rows = bind_bytes_transcription(w, kRowBytes);
        const uint64_t logits = logits_bytes(8, 40000);
        CHECK(with_rows == logits + (uint64_t) 40000 * kRowBytes + (uint64_t) 40000 * 4,
              "unbound subset reserves the head rows on top of the logits");
#if defined(STRATA_TEST_REAL_ENGINE)
        CHECK(bind_bytes_real(w, kRowBytes) == with_rows, "real unbound subset (head rows) == transcription");
#endif
    }

    // 3. THE ZERO INCREMENT: bind() leaves head_logits_cap_ == max_t*n_dvocab (mtp.cpp:765-774), so a second
    //    bind_bytes call on the wide-enough buffer must reserve nothing.  A cap one float short reserves one
    //    float, which is the "not 0, and not the whole width twice" contract (:561-565).
    for (int64_t max_t : {1, 8}) {
        for (bool dhead : {false, true}) {
            // With no bound head the width comes from the file; with one it is n_dvocab_ (and the file is not
            // read).  A full-vocabulary state is dhead == false with no file, or a head whose n_dvocab is not
            // below n_vocab.
            std::vector<int64_t> widths;
            if (dhead)
                widths = {40000, n_vocab};
            else
                widths = {n_vocab};
            for (int64_t n_logits : widths) {
                BindWidths w;
                w.max_t = max_t;
                w.n_vocab = n_vocab;
                w.dhead = dhead;
                w.n_dvocab = n_logits;
                w.owns_draft_head = false;
                const int64_t effective = dhead ? n_logits : n_vocab;
                const int64_t want = max_t * effective;
                const std::string at = "max_t=" + std::to_string(max_t) + " dhead=" + (dhead ? "yes" : "no") +
                                       " n_logits=" + std::to_string(n_logits);

                w.bound_logits = true;
                w.head_logits_cap = want;
                CHECK(bind_bytes_transcription(w, kRowBytes) == 0, at + " | cap == want: 0 increment");
                w.head_logits_cap = want + 4096;
                CHECK(bind_bytes_transcription(w, kRowBytes) == 0, at + " | cap > want: 0 increment");
                w.head_logits_cap = want - 1;
                CHECK(bind_bytes_transcription(w, kRowBytes) == sizeof(float),
                      at + " | cap == want-1: exactly one float");
                w.head_logits_cap = 0;
                CHECK(bind_bytes_transcription(w, kRowBytes) == logits_bytes(max_t, effective),
                      at + " | cap 0: the whole logits");

#if defined(STRATA_TEST_REAL_ENGINE)
                BindWidths real = w;
                real.bound_logits = false;
                real.head_logits_cap = 0;
                CHECK(bind_bytes_real(real, kRowBytes) == logits_bytes(max_t, effective),
                      at + " | real cap 0 == transcription");
                real.bound_logits = true;
                real.head_logits_cap = want;
                CHECK(bind_bytes_real(real, kRowBytes) == 0, at + " | real cap == want: 0");
                real.head_logits_cap = want + 4096;
                CHECK(bind_bytes_real(real, kRowBytes) == 0, at + " | real cap > want: 0");
                real.head_logits_cap = want - 1;
                CHECK(bind_bytes_real(real, kRowBytes) == sizeof(float), at + " | real cap == want-1: 4");
#endif
            }
        }
    }

    // 4. WHEN A SUBSET DOES NOT APPLY.  n_dvocab >= n_vocab is what a full-head bind() stores (:759), a
    //    file shorter than one int32 or not a whole number of tokens is ignored (:555-558), and
    //    STRATA_MTP_FULL_HEAD suppresses the file entirely (:552).  All four must price the whole vocabulary.
    {
        BindWidths base;
        base.max_t = 8;
        base.n_vocab = n_vocab;
        base.owns_draft_head = false;
        const uint64_t full = logits_bytes(8, n_vocab);
        BindWidths w;

        w = base; w.dhead = true; w.n_dvocab = n_vocab;
        CHECK(bind_bytes_transcription(w, kRowBytes) == full, "n_dvocab == n_vocab: full width");
        w = base; w.dhead = true; w.n_dvocab = n_vocab + 1;
        CHECK(bind_bytes_transcription(w, kRowBytes) == full, "n_dvocab > n_vocab: full width");
        w = base; w.dhead = true; w.n_dvocab = 0;
        CHECK(bind_bytes_transcription(w, kRowBytes) == full, "n_dvocab == 0: full width");
        w = base; w.vocab_file_bytes = 0;
        CHECK(bind_bytes_transcription(w, kRowBytes) == full, "no subset file: full width");
        w = base; w.vocab_file_bytes = 2;
        CHECK(bind_bytes_transcription(w, kRowBytes) == full, "file < 4 bytes: ignored");
        w = base; w.vocab_file_bytes = 6;
        CHECK(bind_bytes_transcription(w, kRowBytes) == full, "file not a multiple of 4: ignored");
        w = base; w.vocab_file_bytes = 40000 * 4; w.full_head = true;
        CHECK(bind_bytes_transcription(w, kRowBytes) == full, "STRATA_MTP_FULL_HEAD: file ignored");

#if defined(STRATA_TEST_REAL_ENGINE)
        w = base; w.dhead = true; w.n_dvocab = n_vocab;
        CHECK(bind_bytes_real(w, kRowBytes) == full, "real n_dvocab == n_vocab: full width");
        w = base; w.dhead = true; w.n_dvocab = n_vocab + 1;
        CHECK(bind_bytes_real(w, kRowBytes) == full, "real n_dvocab > n_vocab: full width");
        w = base; w.dhead = true; w.n_dvocab = 0;
        CHECK(bind_bytes_real(w, kRowBytes) == full, "real n_dvocab == 0: full width");
        w = base; w.vocab_file_bytes = 0;
        CHECK(bind_bytes_real(w, kRowBytes) == full, "real no subset file: full width");
        w = base; w.vocab_file_bytes = 6;
        CHECK(bind_bytes_real(w, kRowBytes) == full, "real file not a multiple of 4: ignored");
#endif
    }

    // 5. ZERO ROWS / the coupled upper bound.  max_t == 0 prices nothing at all (no call can happen before
    //    load() sets it), and the coupled-sampling reservation is bind_bytes' documented upper bound (:570-571).
    {
        BindWidths w;
        w.max_t = 0;
        w.n_vocab = n_vocab;
        w.dhead = true;
        w.n_dvocab = 40000;
        CHECK(bind_bytes_transcription(w, kRowBytes) == 0, "max_t == 0: nothing to reserve");

        BindWidths c = w;
        c.max_t = 8;
        c.coupled_env = true;
        const uint64_t expected = logits_bytes(8, 40000) + (uint64_t) n_vocab * 4 +
                                 (uint64_t) (kCoupledHistCap + 8) * 4 + 64ull * 64ull * 8ull + 4096;
        CHECK(bind_bytes_transcription(c, kRowBytes) == expected,
              "coupled sampling adds its documented upper bound");
        c.cparams = true;
        CHECK(bind_bytes_transcription(c, kRowBytes) == logits_bytes(8, 40000),
              "coupled buffers already set up: no second reservation");
    }

#if defined(STRATA_TEST_REAL_ENGINE)
    // The real environment must let the 0-increment cases above mean what they say: with the coupled switch
    // off, bind_bytes adds no coupled term.
    CHECK(!strata::core::coupled_draft_env(), "STRATA_SPEC_COUPLED / STRATA_SPEC_PROB unset for this test");
#endif
}

// ================================ the card-class constants ================================

void run_draft_cap() {
    using strata::core::dflash2_auto_cap;
    using strata::core::dflash2_small_nvidia;

    // 0 = no cap: the --spec width the caller asked for stays.  Ampere/Ada (cc 8) and Turing (cc 7) are the
    // "unmeasured" cards in docs/VLLM_PORT_PLAN.md:70 - the function must NOT invent a cap for them.
    CHECK(dflash2_auto_cap(false, 8) == 0, "Ampere/Ada (cc 8): no cap");
    CHECK(dflash2_auto_cap(false, 7) == 0, "Turing (cc 7.5): no cap (unmeasured)");
    CHECK(dflash2_auto_cap(false, 9) == 0, "Hopper (cc 9): no cap");
    CHECK(dflash2_auto_cap(false, 0) == 0, "HIP reports cc_major 0: no cap");
    CHECK(dflash2_auto_cap(false, -1) == 0, "no compute capability at all: no cap");
    // The two measured classes.
    CHECK(dflash2_auto_cap(false, 12) == 6, "Blackwell sm_120: 6");
    CHECK(dflash2_auto_cap(true, 0) == 5, "Strix Halo / UMA: 5");
    CHECK(dflash2_auto_cap(true, 12) == 5, "UMA wins over the Blackwell cap");

    // The 13 GiB cutoff, both sides of it.
    CHECK(dflash2_small_nvidia(true, (13ull << 30) - 1), "CUDA and < 13 GiB: small");
    CHECK(!dflash2_small_nvidia(true, 13ull << 30), "CUDA and exactly 13 GiB: not small");
    CHECK(!dflash2_small_nvidia(false, 8ull << 30), "not CUDA: never small");
    CHECK(!dflash2_small_nvidia(false, 0), "not CUDA, zero bytes: never small");
}

void run_turing_series() {
    namespace s = strata;
    // 7.5 and only 7.5 (sm75_series.hpp:41): Turing has the series, every other compute capability does not.
    CHECK(s::is_sm75(7, 5), "cc 7.5 is Turing");
    CHECK(!s::is_sm75(7, 0), "Volta cc 7.0 is not Turing");
    CHECK(!s::is_sm75(7, 2), "cc 7.2 is not Turing");
    CHECK(!s::is_sm75(8, 0), "Ampere cc 8.0 is not Turing");
    CHECK(!s::is_sm75(8, 6), "sm_86 is not Turing");
    CHECK(!s::is_sm75(12, 0), "sm_120 is not Turing");

    // The default: the series is OFF unless STRATA_SM75_PERF=1 (sm75_series.hpp:24, :40 - the repo changes a default
    // only where it measured it faster, and this branch's Turing default-on was measured nowhere).  main() unsets
    // STRATA_SM75_PERF before this runs, so this is the documented default and not the caller's environment.
    CHECK(s::sm75_series_setting() == -1, "STRATA_SM75_PERF is unset for this test");
    CHECK(!s::sm75_series(), "the series is off by default");
    CHECK(!s::sm75_default(7, 5), "Turing: off until asked for");
    CHECK(!s::sm75_default(8, 0), "Ampere: never on");
    CHECK(!s::sm75_default(12, 0), "sm_120: never on");
    // (STRATA_SM75_PERF=1 cannot be exercised here: the value is read once per process, and the checks above already
    // read it - a second process would be needed, like the emulated-CC cases below.  The =1 path is covered by the
    // data path: every Turing kernel switch calls sm75_default, whose env branch is the one line above.)

    // tc_sm75.hpp:29-31: its own switch wins, but only on a 7.5 card.
    CHECK(!s::core::tc_sm75_enabled(7, 5), "STRATA_TC_SM75 unset on a 7.5: follows the series (off)");
    CHECK(!s::core::tc_sm75_enabled(8, 6), "sm_86: off whatever the series says");
    put("STRATA_TC_SM75", "0");
    CHECK(!s::core::tc_sm75_enabled(7, 5), "STRATA_TC_SM75=0 turns it off on a 7.5");
    put("STRATA_TC_SM75", "1");
    CHECK(s::core::tc_sm75_enabled(7, 5), "STRATA_TC_SM75=1 turns it on on a 7.5");
    CHECK(!s::core::tc_sm75_enabled(8, 0), "STRATA_TC_SM75=1 cannot touch an 8.0 card");
    CHECK(!s::core::tc_sm75_enabled(12, 0), "STRATA_TC_SM75=1 cannot touch sm_120");
    unset("STRATA_TC_SM75");

    // emulate.hpp: with no STRATA_EMULATE_CC the queries pass the real card through.  (The emulated values
    // cache the environment once, so testing them needs a second process - noted in docs/PORT_AUDIT_TESTS_CPU.md.)
    CHECK(s::emulated_cc() == 0, "no STRATA_EMULATE_CC");
    CHECK(s::cc_major_of(8) == 8 && s::cc_minor_of(6) == 6, "unemulated capability passes through");
    CHECK(s::smem_optin_of(1234) == 1234, "unemulated opt-in shared memory passes through");
}

}  // namespace

int main() {
    // The environment first: three of these switches cache their value on the first call (full_head_env,
    // coupled_draft_env, sm75_series, emulated_cc), so it has to be clean before any of them is asked.
    unset("STRATA_MTP_FULL_HEAD");
    unset("STRATA_SPEC_COUPLED");
    unset("STRATA_SPEC_PROB");
    unset("STRATA_MTP_TOP2");
    unset("STRATA_SM75_PERF");
    unset("STRATA_TC_SM75");
    unset("STRATA_EMULATE_CC");

#if defined(STRATA_TEST_REAL_ENGINE)
    const char* mode = "real (strata_engine)";
#else
    const char* mode = "transcription (CPU-only build: mtp.cpp is not compiled)";
#endif
    std::printf("mtp_logits_width_test: mode=%s\n", mode);

    run_logits_width();
    run_draft_cap();
    run_turing_series();

#if defined(STRATA_TEST_REAL_ENGINE)
    for (const auto& p : temp_files()) {
        std::error_code ignored;
        std::filesystem::remove(p, ignored);
    }
#endif

    if (g_failures == 0) {
        std::printf("mtp_logits_width_test: ok (%d checks)\n", g_checks);
        return 0;
    }
    std::fprintf(stderr, "mtp_logits_width_test: %d of %d checks FAILED\n", g_failures, g_checks);
    return 1;
}
