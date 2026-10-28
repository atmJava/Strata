// Native (GGUF-form) experts: ggml activation quantizers and format-specific CPU row kernels.
// Default keeps the existing dispatch. WindowInvariant preserves that dispatch's single-token arithmetic
// while retaining shared weight decoding where the grouped kernel gives the same result.
#include "strata/kernels/cpu/native_expert.hpp"
#include "strata/kernels/cpu/expert.hpp"
#include "strata/kernels/cpu/iq_avx512.hpp"
#include "strata/kernels/cpu/iq_avx2.hpp"
#include "strata/kernels/cpu/iq_avx1.hpp"
#include "strata/kernels/cpu/expert_layout.hpp"

#include "ggml.h"
#include "ggml-cpu.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <mutex>
#include <string>
#include <vector>

namespace strata::kernels::cpu {
namespace {

const ggml_type_traits_cpu* traits(int type) { return ggml_get_type_traits_cpu((ggml_type) type); }

// Window-invariant arithmetic (NativeArithmetic::WindowInvariant): the reference rows below reproduce ggml-cpu's one-token dot bit for bit
// only if that ggml was built to sum the same way (contraction of the scalar tail into an FMA, for one, depends on the build). So each
// reference is checked once against the linked ggml on synthetic blocks, odd block counts included, and a mismatch sends the invariant
// path to ggml's own one-token dot per token (slower, never different).
bool iq4nl_reference_ok() {
    static const bool ok = [] {
        const ggml_type_traits_cpu* tr = traits(GGML_TYPE_IQ4_NL);
        const ggml_type_traits_cpu* t8 = traits(GGML_TYPE_Q8_0);
        if (tr == nullptr || tr->vec_dot == nullptr || t8 == nullptr || t8->from_float == nullptr) return false;
        uint32_t seed = 4242u;
        auto rnd = [&seed] { seed = seed * 1664525u + 1013904223u; return seed >> 8; };
        for (const int n : {64, 96, 160, 640}) {   // even and odd numbers of blocks: the odd one ends in the scalar tail
            constexpr int rows = 16;
            const size_t rb = ggml_row_size(GGML_TYPE_IQ4_NL, (int64_t) n), nb = (size_t) n / 32, bb = rb / nb;
            std::vector<uint8_t> w((size_t) rows * rb), act(ggml_row_size(GGML_TYPE_Q8_0, (int64_t) n));
            for (auto& b : w) b = (uint8_t) rnd();
            for (int r = 0; r < rows; ++r)
                for (size_t k = 0; k < nb; ++k) { uint8_t* d = &w[(size_t) r * rb + k * bb]; d[0] = 0x66; d[1] = 0x2e; }
            std::vector<float> x((size_t) n);
            for (auto& v : x) v = ((float) (rnd() % 2001) - 1000.f) * 0.001f;
            t8->from_float(x.data(), act.data(), n);
            std::vector<float> got((size_t) rows), want((size_t) rows);
            const void* hp[1] = {act.data()};
            float* op[1] = {got.data()};
            iq4nl_reference_rows(w.data(), rb, n, hp, 1, op, 0, rows);
            for (int r = 0; r < rows; ++r) tr->vec_dot(n, &want[(size_t) r], 0, w.data() + (size_t) r * rb, 0, act.data(), 0, 1);
            if (std::memcmp(got.data(), want.data(), got.size() * sizeof(float)) != 0) {
                std::fprintf(stderr, "strata: the window-invariant IQ4_NL rows differ from this ggml-cpu's dot (n=%d): its dot runs per token instead\n", n);
                return false;
            }
        }
        return true;
    }();
    return ok;
}

// The same check for the AVX-512 accumulation of IQ3_XXS / IQ3_S gate/up rows (types 18, 21 - this tree's
// iq_avx512.cpp carries no IQ2_S table), SwiGLU included.
bool iq512_reference_ok(int type) {
    static std::mutex mu;
    static std::map<int, bool> memo;
    std::lock_guard<std::mutex> lk(mu);
    if (const auto it = memo.find(type); it != memo.end()) return it->second;
    bool ok = false;
    const ggml_type_traits_cpu* tr = traits(type);
    const ggml_type_traits_cpu* t8 = traits(GGML_TYPE_Q8_K);
    if (tr != nullptr && tr->vec_dot != nullptr && t8 != nullptr && t8->from_float != nullptr) {
        constexpr int n = 2560, rows = 24;
        const size_t rb = ggml_row_size((ggml_type) type, n), nb = (size_t) n / 256, bb = rb / nb;
        std::vector<uint8_t> w((size_t) 2 * rows * rb), act(ggml_row_size(GGML_TYPE_Q8_K, n));
        uint32_t seed = 777u;
        auto rnd = [&seed] { seed = seed * 1664525u + 1013904223u; return seed >> 8; };
        for (auto& b : w) b = (uint8_t) rnd();
        for (int r = 0; r < 2 * rows; ++r)
            for (size_t k = 0; k < nb; ++k) { uint8_t* d = &w[(size_t) r * rb + k * bb]; d[0] = 0x66; d[1] = 0x2e; }
        std::vector<float> x(n);
        for (auto& v : x) v = ((float) (rnd() % 2001) - 1000.f) * 0.001f;
        t8->from_float(x.data(), act.data(), n);
        std::vector<float> got((size_t) rows), want((size_t) rows);
        const void* ap[1] = {act.data()};
        float* fp[1] = {got.data()};
        iq512_reference_gu_rows(type, w.data(), rb, (size_t) rows * rb, n, ap, 1, fp, 0, rows);
        for (int r = 0; r < rows; ++r) {
            float g = 0.f, u = 0.f;
            tr->vec_dot(n, &g, 0, w.data() + (size_t) r * rb, 0, act.data(), 0, 1);
            tr->vec_dot(n, &u, 0, w.data() + (size_t) rows * rb + (size_t) r * rb, 0, act.data(), 0, 1);
            want[(size_t) r] = (g / (1.f + std::exp(-g))) * u;
        }
        ok = std::memcmp(got.data(), want.data(), got.size() * sizeof(float)) == 0;
        if (!ok) std::fprintf(stderr, "strata: the window-invariant 512-bit rows of type %d differ from this ggml-cpu's dot: its dot runs per token instead\n", type);
    }
    memo[type] = ok;
    return ok;
}

// 0.2.5: the 512-bit exact rows (iq512x_rows) take one token of an IQ3_XXS / IQ3_S expert only if this build's ggml-cpu dot gives
// the same bits on synthetic blocks of that type (a ggml built for AVX-512 may carry another summation order): checked once a type.
bool x512_one_ok(int type) {
    static std::mutex mu;
    static std::map<int, bool> memo;
    std::lock_guard<std::mutex> lk(mu);
    if (const auto it = memo.find(type); it != memo.end()) return it->second;
    bool ok = false;
    const ggml_type_traits_cpu* tr = traits(type);
    const ggml_type_traits_cpu* t8 = traits(GGML_TYPE_Q8_K);
    if (tr != nullptr && tr->vec_dot != nullptr && t8 != nullptr && t8->from_float != nullptr && iq512x_supported(type)) {
        constexpr int n = 2560, rows = 24;
        const size_t rb = ggml_row_size((ggml_type) type, n), nb = (size_t) n / 256, bb = rb / nb;
        std::vector<uint8_t> w((size_t) rows * rb), act(ggml_row_size(GGML_TYPE_Q8_K, n));
        uint32_t seed = 12345u;
        auto rnd = [&seed] { seed = seed * 1664525u + 1013904223u; return seed >> 8; };
        for (auto& b : w) b = (uint8_t) rnd();
        for (int r = 0; r < rows; ++r)
            for (size_t k = 0; k < nb; ++k) { uint8_t* d = &w[(size_t) r * rb + k * bb]; d[0] = 0x66; d[1] = 0x2e; }   // d = ~0.1 (f16)
        std::vector<float> x(n);
        for (auto& v : x) v = ((float) (rnd() % 2001) - 1000.f) * 0.001f;
        t8->from_float(x.data(), act.data(), n);
        std::vector<float> got((size_t) rows), want((size_t) rows);
        const void* ap[1] = {act.data()};
        float* op[1] = {got.data()};
        iq512x_rows(type, w.data(), rb, n, ap, 1, op, 0, rows);
        for (int r = 0; r < rows; ++r) tr->vec_dot(n, &want[(size_t) r], 0, w.data() + (size_t) r * rb, 0, act.data(), 0, 1);
        ok = std::memcmp(got.data(), want.data(), got.size() * sizeof(float)) == 0;
        if (!ok) std::fprintf(stderr, "strata: the 512-bit exact one-token rows of type %d differ from this ggml-cpu's dot: not used\n", type);
    }
    memo[type] = ok;
    return ok;
}

void init_once() {
    static std::once_flag once;
    std::call_once(once, [] { ggml_cpu_init(); });
}

}  // namespace

bool native_experts_available() noexcept { return true; }

// The "is this format handled by that kernel" questions, answered here and not next to the kernels: iq_avx512.cpp is
// compiled for AVX-512 and iq_avx2.cpp / kq_avx2.cpp for AVX2, and native_gu_rows asks these on every CPU before it
// has checked what the CPU can run.  A function in a wide-ISA file may use that ISA anywhere in its body, so an
// answer that is only a comparison must not come from one (#795).
bool iq512_supported(int type) noexcept {
    return type == 18;
}

bool iq256_supported(int type) noexcept {
    return type == 18;
}

bool native_fmt(int gu_type, int d_type, int64_t n_embd, int64_t n_ff, NativeFmt& f, std::string& err) {
    init_once();
    // Keep one compatibility boundary for the server: the production pack's mixed i-quant gate/up
    // (IQ2_XXS, IQ2_XS, IQ3_XXS, IQ3_S, IQ2_S) or Q8_0, with IQ4_NL, Q2_0 or Q8_0 down. A future format
    // must be added here and in the CUDA whitelist together.
    const bool gu_ok = gu_type == GGML_TYPE_IQ2_XXS || gu_type == GGML_TYPE_IQ2_XS || gu_type == GGML_TYPE_IQ3_XXS ||
                       gu_type == GGML_TYPE_IQ3_S || gu_type == GGML_TYPE_IQ2_S || gu_type == GGML_TYPE_Q8_0;
    const bool d_ok = d_type == GGML_TYPE_IQ4_NL || d_type == GGML_TYPE_Q2_0 || d_type == GGML_TYPE_Q8_0;
    if (!gu_ok || !d_ok) {
        err = "native experts: this sm_75 server requires IQ2_XXS, IQ2_XS, IQ3_XXS, IQ3_S, IQ2_S or Q8_0 gate/up "
              "and IQ4_NL, Q2_0 or Q8_0 down";
        return false;
    }
    const ggml_type_traits_cpu* tg = traits(gu_type);
    const ggml_type_traits_cpu* td = traits(d_type);
    if (tg == nullptr || tg->vec_dot == nullptr || td == nullptr || td->vec_dot == nullptr) {
        err = "native experts: ggml-cpu has no dot product for type " +
              std::to_string(tg == nullptr || tg->vec_dot == nullptr ? gu_type : d_type);
        return false;
    }
    const ggml_type vdt_g = tg->vec_dot_type;
    const ggml_type vdt_d = td->vec_dot_type;
    const ggml_type_traits_cpu* ag = traits(vdt_g);
    const ggml_type_traits_cpu* ad = traits(vdt_d);
    if (ag == nullptr || ag->from_float == nullptr || ad == nullptr || ad->from_float == nullptr) {
        err = "native experts: ggml-cpu cannot quantize an activation for this layer";
        return false;
    }
    if (n_embd % ggml_blck_size((ggml_type) gu_type) || n_ff % ggml_blck_size((ggml_type) d_type) ||
        n_embd % ggml_blck_size(vdt_g) || n_ff % ggml_blck_size(vdt_d)) {
        err = "native experts: expert geometry is not whole blocks";
        return false;
    }
    f.gu_type = gu_type;
    f.d_type = d_type;
    f.gu_act = (int) vdt_g;
    f.d_act = (int) vdt_d;
    f.n_embd = n_embd;
    f.n_ff = n_ff;
    f.gu_row = ggml_row_size((ggml_type) gu_type, n_embd);
    f.d_row = ggml_row_size((ggml_type) d_type, n_ff);
    f.up_off = f.gu_row * (size_t) n_ff;
    f.down_off = 2 * f.up_off;
    f.bytes = f.down_off + f.d_row * (size_t) n_embd;
    f.act_bytes = ggml_row_size(vdt_g, n_embd);
    f.h_bytes = ggml_row_size(vdt_d, n_ff);
    if (f.act_bytes > kNativeActBytes || f.h_bytes > kNativeHBytes) {
        err = "native experts: activation larger than the pool's buffers";
        return false;
    }
    return true;
}

namespace {
// Q8_K (the activations of every i-quant row): ggml-cpu's x86 quantizer is the scalar reference, ~3 us per token
// and layer on the host before the pool can start; q8k_quant_avx2 writes the same bytes.  cpu_avx2_ok() too:
// iq_avx2.cpp is compiled for AVX2 (an AVX-only CPU, or STRATA_FORCE_ISA=avx, keeps ggml's).  STRATA_NO_Q8K_AVX2=1:
// ggml's on any CPU.
bool q8k_avx2(int type) {
    static const bool on = cpu_avx2_ok() && std::getenv("STRATA_NO_Q8K_AVX2") == nullptr;
    return on && type == (int) GGML_TYPE_Q8_K;
}
// the older CPUs' copy (iq_avx1.cpp): byte-identical like the AVX-2 one, and only where AVX2 is absent (same
// policy as STRATA_NO_IQ128 above; STRATA_NO_Q8K_AVX1=1 keeps ggml's scalar reference on those CPUs).
bool q8k_avx1(int type) {
    static const bool on = !cpu_avx2_ok() && cpu_avx1_ok() && std::getenv("STRATA_NO_Q8K_AVX1") == nullptr;
    return on && type == (int) GGML_TYPE_Q8_K;
}
}  // namespace

// STRATA_ACT_DUMP=<prefix> (research, off by default): the second moments of the vectors that enter the CPU experts, in the
// basis the experts use: per layer the gate/up input (the token's hidden
// state, once per token) and the down input (the SwiGLU output, once per routed expert).  STRATA_ACT_DUMP_LAYERS=10,20 picks
// the layers (default none), STRATA_ACT_DUMP_COV=1 adds the full covariance.  Files <prefix>_L<l>_<gu|dn>.bin: int64 n,
// int64 count, double sum x^2 [n], (double sum x x^T [n*n]); rewritten every 2000 records and at exit.
namespace {
struct ActAcc {
    int64_t n = 0, count = 0;
    std::vector<double> sq, cov;
};
struct ActDump {
    bool on = false, cov = false;
    std::string prefix;
    std::vector<int> layers;
    std::map<std::pair<int, int>, ActAcc> acc;
    std::mutex mu;
    int64_t records = 0;
    ActDump() {
        const char* p = std::getenv("STRATA_ACT_DUMP");
        if (p == nullptr || *p == 0) return;
        prefix = p;
        cov = std::getenv("STRATA_ACT_DUMP_COV") != nullptr && std::atoi(std::getenv("STRATA_ACT_DUMP_COV")) != 0;
        if (const char* l = std::getenv("STRATA_ACT_DUMP_LAYERS")) {
            std::string s(l);
            size_t at = 0;
            while (at < s.size()) {
                layers.push_back(std::atoi(s.c_str() + at));
                const size_t c = s.find(',', at);
                if (c == std::string::npos) break;
                at = c + 1;
            }
        }
        on = !layers.empty();
        if (on) std::atexit([] { act_dump_flush(); });
    }
    static void act_dump_flush();
};
ActDump& act_dump() {
    static ActDump* d = new ActDump;   // never destroyed: the atexit flush must still find it
    return *d;
}
void ActDump::act_dump_flush() {
    ActDump& d = act_dump();
    std::lock_guard<std::mutex> lk(d.mu);
    for (auto& kv : d.acc) {
        const std::string path = d.prefix + "_L" + std::to_string(kv.first.first) + (kv.first.second ? "_dn.bin" : "_gu.bin");
        FILE* f = std::fopen((path + ".tmp").c_str(), "wb");
        if (!f) continue;
        std::fwrite(&kv.second.n, 8, 1, f);
        std::fwrite(&kv.second.count, 8, 1, f);
        std::fwrite(kv.second.sq.data(), 8, kv.second.sq.size(), f);
        if (!kv.second.cov.empty()) std::fwrite(kv.second.cov.data(), 8, kv.second.cov.size(), f);
        std::fclose(f);
        std::rename((path + ".tmp").c_str(), path.c_str());
    }
}
void act_dump_record(int layer, int kind, const float* v, int64_t n) {
    ActDump& d = act_dump();
    if (std::find(d.layers.begin(), d.layers.end(), layer) == d.layers.end()) return;
    std::lock_guard<std::mutex> lk(d.mu);
    ActAcc& a = d.acc[{layer, kind}];
    if (a.n == 0) {
        a.n = n;
        a.sq.assign((size_t) n, 0.0);
        if (d.cov) a.cov.assign((size_t) n * (size_t) n, 0.0);
    }
    for (int64_t i = 0; i < n; ++i) a.sq[(size_t) i] += (double) v[i] * v[i];
    if (d.cov)
        for (int64_t i = 0; i < n; ++i) {
            double* row = a.cov.data() + (size_t) i * (size_t) n;
            const double vi = v[i];
            for (int64_t j = 0; j < n; ++j) row[j] += vi * v[j];
        }
    ++a.count;
    if (++d.records % (d.cov ? 400000 : 2000) == 0) {
        d.mu.unlock();
        ActDump::act_dump_flush();
        d.mu.lock();
    }
}
}  // namespace

void native_quant_act(const NativeFmt& f, const float* x, void* dst) {
    if (act_dump().on) act_dump_record(f.layer, 0, x, f.n_embd);
    if (q8k_avx2(f.gu_act)) { q8k_quant_avx2(x, dst, f.n_embd); return; }
    if (q8k_avx1(f.gu_act)) { q8k_quant_avx1(x, dst, f.n_embd); return; }
    traits(f.gu_act)->from_float(x, dst, f.n_embd);
}

void native_quant_h(const NativeFmt& f, const float* h, void* dst) {
    if (act_dump().on) act_dump_record(f.layer, 1, h, f.n_ff);
    if (q8k_avx2(f.d_act)) { q8k_quant_avx2(h, dst, f.n_ff); return; }
    if (q8k_avx1(f.d_act)) { q8k_quant_avx1(h, dst, f.n_ff); return; }
    traits(f.d_act)->from_float(h, dst, f.n_ff);
}

int native_gu_mt_min(int gu_type) {
    (void) gu_type;
    // #152: from how many tokens the multi-token kernels run (ggml's vec_dot below that).  The default 2 is the
    // measured-fastest rule, but a token's expert rows then round differently alone than in a group, so greedy output
    // can depend on how many drafts a verify window held.  STRATA_IQ_MT_MIN=1 (opt-in) uses the multi-token kernels
    // for every group.
    static const char* env = std::getenv("STRATA_IQ_MT_MIN");
    static const int mt_min = env ? std::atoi(env) : 2;
    return mt_min;
}

template <NativeArithmetic Arithmetic>
void native_gu_rows_impl(const NativeFmt& f, const uint8_t* blob, const void* const* act, int nt, float* const* ff,
                    int r0, int r1) {
    // the multi-token kernels decode the weights once for all tokens: 2.0-2.4x ggml-cpu at three tokens, no faster
    // at one (all are bound by the codebook lookups, ~5 GB/s per core), measured by native_expert_parity.  AVX-512
    // first, then the AVX-2 one (Zen 2/3, Intel 12th-14th gen).  STRATA_NO_IQ512 drops an AVX-512 CPU to the
    // AVX-2 kernel, STRATA_NO_IQ256 drops the AVX-2 kernel; ggml-cpu's single-token vec_dot is reached only with
    // both set (and on a CPU without AVX-512, STRATA_NO_IQ512 changes nothing).
    static const bool avx512 = cpu_avx512_ok() && std::getenv("STRATA_NO_IQ512") == nullptr;
    // STRATA_NO_IQ256 drops the AVX-2 kernel.  cpu_avx2_ok() is ALSO required: iq_avx2.cpp and kq_avx2.cpp
    // are compiled /arch:AVX2 and use AVX2 and FMA3, so calling them on a CPU without either is an
    // illegal instruction, not a slow path.  The gate sat next to `avx512` above, which does test
    // cpu_avx512_ok(), and that asymmetry is what let a pre-Haswell CPU reach iq256_gu_rows.
    // Without it this falls through to ggml-cpu's vec_dot below, which is compiled for whatever baseline the build
    // selected (AVX1 here). That path loops over tokens itself, so it is correct for any `nt`, not just one.
    static const bool avx2 = cpu_avx2_ok() && std::getenv("STRATA_NO_IQ256") == nullptr;
    // The older-CPU tier (STRATA_ISA_FLOOR=avx builds): without it every expert on such a CPU runs on ggml-cpu's
    // scalar _generic dots.  Only where AVX2 is absent, so a modern CPU's rounding never moves (and
    // STRATA_NO_IQ256=1 there still means ggml, as before).  iq_avx1.cpp is compiled for AVX and gated on
    // cpu_avx1_ok().  Measured on an E5-2470 v2 (iq_avx1_parity --bench): 1.2-3.1x ggml's dot from two tokens on,
    // at one token ggml's auto-vectorized generic loop matches the 128-bit kernel - so the same #152 mt_min rule
    // as the AVX-2 kernels, and STRATA_IQ_MT_MIN=1 takes the kernel for one token too (its rows are then
    // independent of the drafting: iq_avx1_parity checks every width bit for bit).
    static const bool avx1 = !cpu_avx2_ok() && cpu_avx1_ok() && std::getenv("STRATA_NO_IQ128") == nullptr;
    // The exact rows are ggml-cpu's AVX2 single-token dot bit for bit, so they need ggml-cpu itself built for AVX2 and
    // not AVX-512 (ggml_cpu_has_*): a STRATA_ISA_FLOOR build runs ggml's AVX1/SSE dots, a native build on an AVX-512 CPU
    // run with STRATA_FORCE_AVX2=1 runs its AVX-512 one, and both round differently (#2215's guard).
    static const bool exact_one = [] {
        const char* v = std::getenv("STRATA_CPU_IQ_ALL_EXACT1");
        return v && v[0] == '1' && v[1] == 0 && ggml_cpu_has_avx2() && !ggml_cpu_has_avx512();
    }();
    // only where ggml's dot runs today (below the #152 multi-token rule): with STRATA_IQ_MT_MIN=1 a lone token keeps the
    // multi-token kernel, so a token's rows still do not depend on how many tokens share its expert
    if (exact_one && nt == 1 && nt < native_gu_mt_min(f.gu_type) && avx2 && !cpu_avx512_ok() &&
        f.gu_type == 18) {
        static std::once_flag announced;
        std::call_once(announced, [] { std::fprintf(stderr, "strata ab: GATE=STRATA_CPU_IQ_ALL_EXACT1 active=1\n"); });
        iq256_gu_rows_exact_one(f.gu_type, blob, f.gu_row, f.up_off, (int) f.n_embd, act[0], ff[0], r0, r1);
        return;
    }
    // 0.2.5 (AVX-512 CPUs where a gather is cheap: Intel Sapphire Rapids and later): ONE token of an IQ3_XXS expert on
    // the 512-bit exact rows, ggml-cpu's single-token AVX2 dot bit for bit with the grid words gathered: 1.3x ggml's dot on a Xeon 8468
    // (x512_bench), the same output.  Where the multi-token rule (#152) already sends one token to the multi-token kernel
    // (STRATA_IQ_MT_MIN=1) that stays; STRATA_NO_IQ512X1=1 turns this off; the first use checks the bits against this ggml.
    static const bool x512_one = avx512 && iq512_gather_on() && std::getenv("STRATA_NO_IQ512X1") == nullptr;
    if (x512_one && nt == 1 && nt < native_gu_mt_min(f.gu_type) && f.gu_type == 18 &&
        x512_one_ok(f.gu_type)) {
        const int n1 = (int) f.n_embd;
        float g[64], u[64];
        float* gp[1] = {g};
        float* up[1] = {u};
        for (int c0 = r0; c0 < r1; c0 += 64) {
            const int c1 = std::min(r1, c0 + 64);
            iq512x_rows(f.gu_type, blob + (size_t) c0 * f.gu_row, f.gu_row, n1, act, 1, gp, 0, c1 - c0);
            iq512x_rows(f.gu_type, blob + f.up_off + (size_t) c0 * f.gu_row, f.gu_row, n1, act, 1, up, 0, c1 - c0);
            for (int r = c0; r < c1; ++r) ff[0][r] = (g[r - c0] / (1.f + std::exp(-g[r - c0]))) * u[r - c0];
        }
        return;
    }
    const int mt_min = native_gu_mt_min(f.gu_type);   // #152
    const bool singleton_iq = Arithmetic == NativeArithmetic::WindowInvariant && mt_min > 1;
    static const bool cpu512 = cpu_avx512_ok();
    if (nt >= mt_min && (iq512_supported(f.gu_type) || (!cpu512 && iq256_supported(f.gu_type)))) {
        if (singleton_iq) {
#if defined(__x86_64__) || defined(_M_X64)
            if (avx512 && ggml_cpu_has_avx2() && ggml_cpu_has_fma() &&
                (f.gu_type == 18 || f.gu_type == 21) && iq512_reference_ok(f.gu_type)) {
                iq512_reference_gu_rows(f.gu_type, blob, f.gu_row, f.up_off, (int) f.n_embd, act, nt, ff, r0, r1);
                return;
            }
#endif
            // Otherwise use the singleton dot below, keeping its row-major weight reuse across tokens.
        } else {
            if (avx512 && iq512_supported(f.gu_type)) {
                iq512_gu_rows(f.gu_type, blob, f.gu_row, f.up_off, (int) f.n_embd, act, nt, ff, r0, r1);
                return;
            }
            if (avx2 && iq256_supported(f.gu_type)) {
                iq256_gu_rows(f.gu_type, blob, f.gu_row, f.up_off, (int) f.n_embd, act, nt, ff, r0, r1);
                return;
            }
        }
    }
    if (!singleton_iq && avx1 && nt >= mt_min && iq128_supported(f.gu_type)) {
        iq128_gu_rows(f.gu_type, blob, f.gu_row, f.up_off, (int) f.n_embd, act, nt, ff, r0, r1);
        return;
    }
    const int n = (int) f.n_embd;
    const ggml_vec_dot_t dot = traits(f.gu_type)->vec_dot;
    for (int r = r0; r < r1; ++r) {
        const uint8_t* gr = blob + (size_t) r * f.gu_row;
        const uint8_t* ur = blob + f.up_off + (size_t) r * f.gu_row;
        for (int t = 0; t < nt; ++t) {
            float g = 0.f, u = 0.f;
            dot(n, &g, 0, gr, 0, act[t], 0, 1);
            dot(n, &u, 0, ur, 0, act[t], 0, 1);
            ff[t][r] = (g / (1.f + std::exp(-g))) * u;
        }
    }
}

template <NativeArithmetic Arithmetic>
void native_down_rows_impl(const NativeFmt& f, const uint8_t* blob, const void* const* hq, int nt, float* const* out,
                      int r0, int r1) {
    // IQ4_NL down rows: the AVX-2 multi-token kernel decodes the nibbles and absolutises the weights once per
    // block instead of once per token; ggml-cpu's dot is single-token.  STRATA_NO_IQ4NL falls back to it.
    static const bool iq4nl_mt = std::getenv("STRATA_NO_IQ4NL") == nullptr;
    static const int mt_min = [] { const char* e = std::getenv("STRATA_IQ_MT_MIN"); return e ? std::atoi(e) : 2; }();
    // The multi-token kernel below is an /arch:AVX2 translation unit (iq_avx2.cpp),
    // so a CPU without AVX2 has to reach ggml-cpu's vec_dot instead - same reasoning as the gate/up
    // rows above, where `avx512` tested cpu_avx512_ok() and `avx2` did not.
    if (cpu_avx2_ok() && nt >= mt_min && f.d_type == 20 && iq4nl_mt) {   // #152: the same rule as the gate/up rows
        if (Arithmetic == NativeArithmetic::Default || mt_min <= 1) {
            iq4nl256_down_rows(blob + f.down_off, f.d_row, (int) f.n_ff, hq, nt, out, r0, r1);
            return;
        }
#if defined(__x86_64__) || defined(_M_X64)
        if (ggml_cpu_has_avx2() && ggml_cpu_has_fma() && iq4nl_reference_ok()) {
            iq4nl_reference_rows(blob + f.down_off, f.d_row, (int) f.n_ff, hq, nt, out, r0, r1);
            return;
        }
#endif
    }
    // the older-CPU tier: IQ4_NL and Q2_0 down rows in 128-bit (ggml-cpu's dots for both are scalar here)
    static const bool avx1 = !cpu_avx2_ok() && cpu_avx1_ok() && std::getenv("STRATA_NO_IQ128") == nullptr;
    if (avx1 && iq128_down_supported(f.d_type)) {
        iq128_down_rows(f.d_type, blob + f.down_off, f.d_row, (int) f.n_ff, hq, nt, out, r0, r1);
        return;
    }
    const int n = (int) f.n_ff;
    const ggml_vec_dot_t dot = traits(f.d_type)->vec_dot;
    for (int r = r0; r < r1; ++r) {
        const uint8_t* dr = blob + f.down_off + (size_t) r * f.d_row;
        for (int t = 0; t < nt; ++t) {
            float s = 0.f;
            dot(n, &s, 0, dr, 0, hq[t], 0, 1);
            out[t][r] = s;
        }
    }
}

// Keep one dispatch implementation: only width-sensitive IQ kernels need a different arithmetic path.
// Already invariant kernels retain their shared decoding and scale handling (port: NVFP4 is not a format of
// this tree, the fork's whitelist is the IQ2/IQ3 family plus Q8_0/Q2_0/IQ4_NL).
void native_gu_rows(const NativeFmt& f, const uint8_t* blob, const void* const* act, int nt,
                    float* const* ff, int r0, int r1) {
    native_gu_rows_impl<NativeArithmetic::Default>(f, blob, act, nt, ff, r0, r1);
}

void native_down_rows(const NativeFmt& f, const uint8_t* blob, const void* const* hq, int nt,
                      float* const* out, int r0, int r1) {
    native_down_rows_impl<NativeArithmetic::Default>(f, blob, hq, nt, out, r0, r1);
}

void native_gu_rows_invariant(const NativeFmt& f, const uint8_t* blob, const void* const* act, int nt,
                              float* const* ff, int r0, int r1) {
    native_gu_rows_impl<NativeArithmetic::WindowInvariant>(f, blob, act, nt, ff, r0, r1);
}

void native_down_rows_invariant(const NativeFmt& f, const uint8_t* blob, const void* const* hq, int nt,
                                float* const* out, int r0, int r1) {
    native_down_rows_impl<NativeArithmetic::WindowInvariant>(f, blob, hq, nt, out, r0, r1);
}

}  // namespace strata::kernels::cpu
