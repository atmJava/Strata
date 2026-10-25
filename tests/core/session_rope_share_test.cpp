// tests/core/session_rope_share_test.cpp - the RoPE table a session shares, and what `borrow_rope` is worth.
//
// THE CONTRACT PINNED HERE.  `session_bytes(..., borrow_rope = false)` sizes ONE RoPE table for the whole
// session and `borrow_rope = true` sizes none: `session_init`'s `share_rope_from` points every QSA state of the
// new session at another session's table (include/strata/core/session.hpp:63-74, 111-118).  So the difference is
// EXACTLY ONE table, `max_cells * (n_rot/2) * 4 * 2` bytes (64 MiB at 262144 cells, 48 MiB at 196608 with the
// compiled n_rot = 64), for every layer range, every cell count and every k - not one table per QSA layer.
// src/core/session.cpp:69-77 builds exactly `qsa_state_bytes(g, cells, true) + (q_n - 1) * qsa_state_bytes(...,
// false)` when this session owns the table, and `q_n * qsa_state_bytes(..., false)` when it borrows: the first
// state is the only owner, and `src/core/session.cpp:184-193` asserts that externally.
//
// TWO MODES, THE SAME ASSERTIONS.
//   * `STRATA_TEST_REAL_ENGINE` (CMakeLists.txt sets it when `TARGET strata_engine` exists, i.e. a CUDA or HIP
//     build): the real `strata::core::session_bytes` and `session_rope_owner` are called.
//   * otherwise - the CPU-only build this file was written and run in (no nvcc; `strata_engine` is created only
//     under `if(STRATA_ENABLE_CUDA OR STRATA_ENABLE_HIP)`, CMakeLists.txt:335 and :583): `session_bytes` is pure
//     arithmetic, but it lives in a CUDA translation unit (`src/core/session.cpp` includes <cuda_runtime.h> and
//     calls the kernel layer), so NO object in a CPU-only build provides the symbol.  The functions are
//     therefore TRANSCRIBED below, one source line per line, and the assertions run on the transcription.  The
//     transcription is also the independent oracle the real mode compares against, so a drift in either shows.
//
// NOT TESTED HERE, AND WHY (docs/PORT_AUDIT_TESTS_CPU.md has the full list): `session_init` cannot be called
// without a device - it cudaMallocs the table and builds it with the rope kernels - so this file pins the
// SIZING side only, which is the side the layer-split search prices.
#include "strata/core/geometry.hpp"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

#if defined(STRATA_TEST_REAL_ENGINE)
#include "strata/core/session.hpp"
#endif

namespace {

using strata::core::ModelGeometry;

int g_checks = 0;
int g_failures = 0;

void check(bool ok, const char* expr, const char* file, int line, const std::string& context) {
    ++g_checks;
    if (ok) return;
    ++g_failures;
    std::fprintf(stderr, "FAIL %s:%d: %s  [%s]\n", file, line, expr, context.c_str());
}

#define CHECK(cond, context) check((cond), #cond, __FILE__, __LINE__, (context))

std::string where(const char* what, int64_t lo, int64_t hi, int64_t cells, int64_t k) {
    return std::string(what) + " range=[" + std::to_string(lo) + "," + std::to_string(hi) + ") cells=" +
           std::to_string(cells) + " k=" + std::to_string(k);
}

// ================================ the transcription ================================
//
// Every line below names the source line it copies.  Nothing here is invented: the two arithmetic functions are
// `session_bytes` (src/core/session.cpp:57-83) and the rope term of `qsa_state_bytes`
// (src/core/layer.cpp:774-791).  Terms `borrow_rope` cannot reach are arguments, so that the difference the test
// asserts is the difference the source computes.

uint64_t align_up(uint64_t n, uint64_t a) { return (n + a - 1) / a * a; }   // src/core/session.cpp:39

/// src/core/layer.cpp:787 + :790.  `kv_bytes` is the whole non-rope part of the state (K/V pools, page table,
/// indexed rows, step counters, pos_dev); it never reads `with_rope`, so it cancels in the borrow difference.
uint64_t qsa_state_bytes_transcription(const ModelGeometry& g, int64_t max_cells, bool with_rope,
                                       uint64_t kv_bytes = 0) {
    uint64_t n = kv_bytes;
    if (with_rope) n += (uint64_t) max_cells * (g.n_rot / 2) * 4 * 2;   // layer.cpp:787
    return align_up(n, 16) + 256;                                       // layer.cpp:790
}

/// src/core/session.cpp:62-64: how many QSA layers the range holds - `layer / interval` of them below `hi` minus
/// those below `lo`, with the floor of 1 when the model has any QSA layer (a range with none still carves one
/// primary state so the model-level uses of `qsa_states[primary]` stay valid, session.hpp:56).
int64_t qsa_layers_in_range(const ModelGeometry& g, int64_t layer_lo, int64_t layer_hi) {
    const int64_t I = std::max<int64_t>(g.qsa_interval, 1);                  // :62
    const int64_t q_lo = layer_lo / I, q_hi = layer_hi / I;                  // :63
    return std::max<int64_t>(q_hi - q_lo, g.n_qsa_layers() > 0 ? 1 : 0);     // :64
}

/// src/core/layer.cpp:391-395: `moe_buffers_bytes` is the only k-dependent term of `session_bytes` (:79), and k
/// enters it only through the ids and weights arrays (k * 4 each, each aligned up to 16).  Modelled exactly so
/// that "k does not affect the borrow saving" is an assertion about real k dependence, not about an ignored
/// argument.
uint64_t moe_k_bytes(int64_t k) { return 2 * align_up((uint64_t) k * 4, 16); }   // layer.cpp:391-395

/// src/core/session.cpp:57-83.  `qsa_kv_bytes` and `other_bytes` stand for the terms the flag cannot reach
/// (:67-68, :78-81); they are passed in so their alignment cannot hide a wrong table size.
uint64_t session_bytes_transcription(const ModelGeometry& g, int64_t max_cells, int64_t k, int64_t layer_lo,
                                     int64_t layer_hi, bool borrow_rope, uint64_t qsa_kv_bytes = 0,
                                     uint64_t other_bytes = 0) {
    if (layer_hi < 0 || layer_hi > g.n_layers) layer_hi = g.n_layers;        // :59
    if (layer_lo < 0) layer_lo = 0;                                          // :60
    const int64_t q_n = qsa_layers_in_range(g, layer_lo, layer_hi);          // :62-64
    uint64_t n = other_bytes + moe_k_bytes(k);                               // :67-68, :78-81
    if (g.n_qsa_layers() > 0)                                                // :75
        n += qsa_state_bytes_transcription(g, max_cells, !borrow_rope, qsa_kv_bytes) +                  // :76
             (uint64_t) (q_n - 1) * qsa_state_bytes_transcription(g, max_cells, false, qsa_kv_bytes);    // :77
    return align_up(n, 256) + 4096;                                          // :82
}

uint64_t rope_table_bytes(const ModelGeometry& g, int64_t max_cells) {
    return (uint64_t) max_cells * (g.n_rot / 2) * 4 * 2;                     // layer.cpp:787 / session.hpp:113
}

// ================================ the matrix ================================

struct Range {
    int64_t lo, hi;
    const char* name;
};

void run_borrow_matrix(const ModelGeometry& g) {
    const Range ranges[] = {{0, 48, "full"}, {0, 24, "first-half"}, {24, 48, "second-half"}};
    const int64_t cells_list[] = {4096, 196608, 262144};
    const int64_t ks[] = {1, 10, 32};
    constexpr uint64_t kQsaKv = 12345;   // bytes of QSA state that are NOT the table
    constexpr uint64_t kOther = 6789;    // the GDN/MoE/block/PLE terms (plus moe_k_bytes)

    for (const Range& r : ranges) {
        for (int64_t cells : cells_list) {
            const uint64_t table = rope_table_bytes(g, cells);
            const int64_t q_n = qsa_layers_in_range(g, r.lo, r.hi);
            // THE TABLE ITSELF, before any session arithmetic: n_rot = 64 -> 256 bytes per cell.
            CHECK(table == (uint64_t) cells * ((uint64_t) g.n_rot / 2) * 4 * 2,
                  where("table formula", r.lo, r.hi, cells, 0));
            CHECK(table == (uint64_t) cells * 256, where("256 B/cell at n_rot=64", r.lo, r.hi, cells, 0));
            CHECK(table % 256 == 0, where("table is a 256-byte multiple (no alignment slack)", r.lo, r.hi, cells, 0));

            for (int64_t k : ks) {
                const uint64_t owned = session_bytes_transcription(g, cells, k, r.lo, r.hi, false, kQsaKv, kOther);
                const uint64_t borrowed = session_bytes_transcription(g, cells, k, r.lo, r.hi, true, kQsaKv, kOther);
                const std::string at = where("borrow", r.lo, r.hi, cells, k);
                CHECK(owned >= borrowed, at);
                // THE ONE-TABLE CONTRACT (session.cpp:75-77, session.hpp:111-118).
                CHECK(owned - borrowed == table, at + " | owned-borrowed must be exactly one table");
                // ...and the explicit spelling of the same thing: the borrowed size plus one table.
                CHECK(owned == borrowed + table, at + " | owned == borrowed + table");
                CHECK(borrowed == owned - table, at + " | borrowed == owned - table");
                // The brief's `q_n * table` is only right when the range holds ONE QSA layer; the source shares
                // one table inside the session.  Kept as an explicit guard so a change to per-layer tables fails.
                if (q_n == 1)
                    CHECK(owned - borrowed == (uint64_t) q_n * table, at + " | single-QSA range: q_n * table");
                else
                    CHECK(owned - borrowed != (uint64_t) q_n * table,
                          at + " | range has " + std::to_string(q_n) + " QSA layers: ONE table, not q_n tables");

#if defined(STRATA_TEST_REAL_ENGINE)
                const uint64_t real_owned = strata::core::session_bytes(g, cells, k, r.lo, r.hi, false);
                const uint64_t real_borrowed = strata::core::session_bytes(g, cells, k, r.lo, r.hi, true);
                CHECK(real_owned >= real_borrowed, at + " | real");
                CHECK(real_owned - real_borrowed == table, at + " | real session_bytes: exactly one table");
                CHECK(real_owned - real_borrowed == owned - borrowed, at + " | real == transcription");
#endif
            }
        }
    }

    // THE DOCUMENTED SIZES: 48 MiB at 192K, 64 MiB at 262K (include/strata/core/session.hpp:113).
    CHECK(rope_table_bytes(g, 196608) == 48ull << 20, "table(196608) == 48 MiB");
    CHECK(rope_table_bytes(g, 262144) == 64ull << 20, "table(262144) == 64 MiB");
    std::printf("one table: 4096 cells = %llu B, 196608 = %llu B (48 MiB), 262144 = %llu B (64 MiB)\n",
                (unsigned long long) rope_table_bytes(g, 4096),
                (unsigned long long) rope_table_bytes(g, 196608),
                (unsigned long long) rope_table_bytes(g, 262144));
}

void run_linearity_and_edges(const ModelGeometry& g) {
    // max_cells doubles -> the saving doubles (three independent pairs).
    const int64_t pairs[][2] = {{4096, 8192}, {98304, 196608}, {131072, 262144}};
    for (const auto& p : pairs) {
        const uint64_t one = rope_table_bytes(g, p[0]);
        const uint64_t two = rope_table_bytes(g, p[1]);
        const uint64_t owned_one = session_bytes_transcription(g, p[0], 10, 0, 48, false, 777, 999);
        const uint64_t borrowed_one = session_bytes_transcription(g, p[0], 10, 0, 48, true, 777, 999);
        const uint64_t owned_two = session_bytes_transcription(g, p[1], 10, 0, 48, false, 777, 999);
        const uint64_t borrowed_two = session_bytes_transcription(g, p[1], 10, 0, 48, true, 777, 999);
        CHECK(two == 2 * one, "table(2c) == 2 * table(c)");
        CHECK((owned_two - borrowed_two) == 2 * (owned_one - borrowed_one),
              "borrow saving(2c) == 2 * borrow saving(c)");
        CHECK(owned_two - borrowed_two == two, "borrow saving at 2c is one 2c table");
#if defined(STRATA_TEST_REAL_ENGINE)
        CHECK(strata::core::session_bytes(g, p[1], 10, 0, 48, false) -
                  strata::core::session_bytes(g, p[1], 10, 0, 48, true) ==
              2 * (strata::core::session_bytes(g, p[0], 10, 0, 48, false) -
                   strata::core::session_bytes(g, p[0], 10, 0, 48, true)),
              "real borrow saving is linear in max_cells");
#endif
    }

    // k: in the source it reaches only moe_buffers_bytes (session.cpp:79), which never sees the flag.
    for (int64_t cells : {4096, 262144}) {
        const uint64_t d1 = session_bytes_transcription(g, cells, 1, 0, 48, false) -
                            session_bytes_transcription(g, cells, 1, 0, 48, true);
        const uint64_t d10 = session_bytes_transcription(g, cells, 10, 0, 48, false) -
                             session_bytes_transcription(g, cells, 10, 0, 48, true);
        const uint64_t d32 = session_bytes_transcription(g, cells, 32, 0, 48, false) -
                             session_bytes_transcription(g, cells, 32, 0, 48, true);
        CHECK(d1 == d10 && d10 == d32, "k does not change the borrow saving");
        CHECK(d10 == rope_table_bytes(g, cells), "k=10 saving is one table");
#if defined(STRATA_TEST_REAL_ENGINE)
        CHECK(strata::core::session_bytes(g, cells, 32, 0, 48, false) -
                  strata::core::session_bytes(g, cells, 32, 0, 48, true) ==
              d32,
              "real k=32 saving == transcription");
#endif
    }

    // Alignment cannot eat or add bytes: the table is a 256-byte multiple and the session rounds up to 256,
    // so the difference is exact for every residue of the non-table terms (session.cpp:71-74 claims this).
    for (uint64_t qsa_kv : {0ull, 1ull, 7ull, 15ull, 16ull, 123ull}) {
        for (uint64_t other : {0ull, 1ull, 255ull, 256ull, 4096ull}) {
            const uint64_t owned = session_bytes_transcription(g, 196608, 10, 0, 48, false, qsa_kv, other);
            const uint64_t borrowed = session_bytes_transcription(g, 196608, 10, 0, 48, true, qsa_kv, other);
            const std::string at = "qsa_kv=" + std::to_string(qsa_kv) + " other=" + std::to_string(other);
            CHECK(owned - borrowed == 48ull << 20, at + " | alignment slack must not change the one-table saving");
        }
    }

    // A model with no QSA layer at all owns no table, so the flag saves nothing (session.cpp:75 guard).
    ModelGeometry no_qsa = g;
    no_qsa.n_layers = 2;
    no_qsa.qsa_interval = 4;   // 2 / 4 == 0 QSA layers
    CHECK(no_qsa.n_qsa_layers() == 0, "no-QSA fixture");
    CHECK(session_bytes_transcription(no_qsa, 4096, 10, 0, 2, false) ==
              session_bytes_transcription(no_qsa, 4096, 10, 0, 2, true),
          "no QSA layer: borrow_rope saves nothing");

    // A range with no QSA layer inside a model that has them still carves one primary state (q_n floor of 1),
    // and that one state is the one carrying the table - so the flag still saves exactly one.
    CHECK(qsa_layers_in_range(g, 0, 3) == 1, "range [0,3) has no QSA layer but q_n floors at 1");
    CHECK(session_bytes_transcription(g, 4096, 10, 0, 3, false) -
              session_bytes_transcription(g, 4096, 10, 0, 3, true) ==
          rope_table_bytes(g, 4096),
          "QSA-less sub-range still saves one table");

    // The range arguments are clamped exactly as the source clamps them (:59-60), so -1 and n_layers agree.
    CHECK(session_bytes_transcription(g, 4096, 10, 0, -1, false) ==
              session_bytes_transcription(g, 4096, 10, 0, 48, false),
          "layer_hi=-1 == n_layers");
    CHECK(session_bytes_transcription(g, 4096, 10, -1, 48, false) ==
              session_bytes_transcription(g, 4096, 10, 0, 48, false),
          "layer_lo=-1 == 0");
    CHECK(session_bytes_transcription(g, 4096, 10, 0, 99, false) ==
              session_bytes_transcription(g, 4096, 10, 0, 48, false),
          "layer_hi>n_layers clamps to n_layers");

#if defined(STRATA_TEST_REAL_ENGINE)
    CHECK(strata::core::session_bytes(g, 4096, 10, 0, -1, false) ==
              strata::core::session_bytes(g, 4096, 10, 0, 48, false),
          "real: layer_hi=-1 == n_layers");
#endif
}

#if defined(STRATA_TEST_REAL_ENGINE)
// ================================ the borrow chain, on the real function ================================
//
// `session_rope_owner` is a pure pointer walk over host structs (src/core/session.cpp:85-101), so it IS testable
// without a device - unlike session_init, which builds the table with the rope kernels.  These cases are the
// chain contract the header documents (session.hpp:63-74, 130-141).
void run_rope_owner() {
    using strata::core::QsaState;
    using strata::core::SessionState;
    float table[8] = {};
    QsaState states[4] = {};
    states[3].owns_rope = true;
    states[3].cos_tab = table;
    states[3].sin_tab = table + 4;

    SessionState owner;
    owner.qsa_states = states;
    owner.qsa_ord0 = 3;
    owner.qsa_alloc = 1;
    CHECK(strata::core::session_rope_owner(owner) == &states[3], "owner answers with its occupied ordinal");

    QsaState borrowed_states[1] = {};
    SessionState borrower;
    borrower.qsa_states = borrowed_states;
    borrower.qsa_ord0 = 0;
    borrower.qsa_alloc = 1;
    borrower.rope_donor = &owner;
    CHECK(strata::core::session_rope_owner(borrower) == &states[3], "borrower resolves to the donor's owner");

    QsaState chained_states[1] = {};
    SessionState chained;
    chained.qsa_states = chained_states;
    chained.qsa_ord0 = 0;
    chained.qsa_alloc = 1;
    chained.rope_donor = &borrower;
    CHECK(strata::core::session_rope_owner(chained) == &states[3], "a borrow chain walks to the real owner");

    // A released owner (session_release clears owns_rope, :216-220) ends the chain at nullptr, which is what
    // makes a new borrow of a dead donor fail in session_init instead of taking a dangling pointer.
    states[3].owns_rope = false;
    CHECK(strata::core::session_rope_owner(owner) == nullptr, "released owner: nullptr");
    CHECK(strata::core::session_rope_owner(borrower) == nullptr, "borrower of a released owner: nullptr");
    CHECK(strata::core::session_rope_owner(chained) == nullptr, "chain to a released owner: nullptr");
    states[3].owns_rope = true;

    // ...and a table that was never built (owns_rope but no cos_tab) is not an owner either.
    states[3].cos_tab = nullptr;
    CHECK(strata::core::session_rope_owner(owner) == nullptr, "owns_rope without cos_tab: nullptr");
    states[3].cos_tab = table;

    // No QSA state allocated at all: nullptr, never a read of qsa_states[0].
    SessionState empty;
    CHECK(strata::core::session_rope_owner(empty) == nullptr, "no qsa_states: nullptr");
    SessionState zero_alloc;
    zero_alloc.qsa_states = states;
    zero_alloc.qsa_alloc = 0;
    CHECK(strata::core::session_rope_owner(zero_alloc) == nullptr, "qsa_alloc=0: nullptr");

    // The cycle guard (:93-99): two borrowers pointing at each other must terminate at nullptr.
    QsaState a_state[1] = {}, b_state[1] = {};
    SessionState a, b;
    a.qsa_states = a_state;
    a.qsa_alloc = 1;
    b.qsa_states = b_state;
    b.qsa_alloc = 1;
    a.rope_donor = &b;
    b.rope_donor = &a;
    CHECK(strata::core::session_rope_owner(a) == nullptr, "borrow cycle terminates at nullptr");
}
#endif

}  // namespace

int main() {
#if defined(STRATA_TEST_REAL_ENGINE)
    const char* mode = "real (strata_engine)";
#else
    const char* mode = "transcription (CPU-only build: session.cpp is not compiled)";
#endif
    std::printf("session_rope_share_test: mode=%s\n", mode);

    const strata::core::ModelGeometry g = strata::core::kCompiledGeometry;
    std::printf("geometry: n_layers=%lld qsa_interval=%lld n_qsa_layers=%lld n_rot=%lld\n",
                (long long) g.n_layers, (long long) g.qsa_interval, (long long) g.n_qsa_layers(),
                (long long) g.n_rot);
    CHECK(g.n_rot == 64, "compiled n_rot is 64 (the table numbers in session.hpp:113 assume it)");

    run_borrow_matrix(g);
    run_linearity_and_edges(g);
#if defined(STRATA_TEST_REAL_ENGINE)
    run_rope_owner();
#endif

    if (g_failures == 0) {
        std::printf("session_rope_share_test: ok (%d checks)\n", g_checks);
        return 0;
    }
    std::fprintf(stderr, "session_rope_share_test: %d of %d checks FAILED\n", g_failures, g_checks);
    return 1;
}
