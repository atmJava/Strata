#include "strata/kernels/cpu/pool.hpp"
#include "strata/kernels/cpu/expert.hpp"
#include "strata/kernels/cpu/expert_layout.hpp"
#include "ggml.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <future>
#include <string>
#include <vector>

namespace cpu = strata::kernels::cpu;

int main() {
    cpu::ExpertPool pool(1, false, false);
    int bad = 0;

    {
        auto decode = pool.decode_window();   // default path: no batch guard, no accounting or dispatch lock
        auto prefill = pool.try_prefill();
        const bool old_path_unchanged = static_cast<bool>(prefill);
        std::printf("guard off leaves solo path unblocked: %s\n", old_path_unchanged ? "yes" : "*** NO ***");
        if (!old_path_unchanged) ++bad;
    }

    pool.set_batch_prefill_guard(true);
    {
        auto decode = pool.decode_window();
        auto prefill = pool.try_prefill();
        const bool refused = !prefill;
        std::printf("decode active refuses prefill: %s\n", refused ? "yes" : "*** NO ***");
        if (!refused) ++bad;
    }

    auto first = pool.try_prefill();
    const bool first_ok = static_cast<bool>(first);
    std::printf("prefill acquires idle pool: %s\n", first_ok ? "yes" : "*** NO ***");
    if (!first_ok) ++bad;

    auto second = pool.try_prefill();
    const bool second_refused = !second;
    std::printf("held prefill refuses second prefill: %s\n", second_refused ? "yes" : "*** NO ***");
    if (!second_refused) ++bad;

    first.release();
    auto after = pool.try_prefill();
    const bool after_ok = static_cast<bool>(after);
    std::printf("released prefill is reusable: %s\n", after_ok ? "yes" : "*** NO ***");
    if (!after_ok) ++bad;

    {
        auto decode = pool.decode_window();
        after.release();
        auto blocked = pool.try_prefill();
        const bool blocked_ok = !blocked;
        std::printf("new decode blocks subsequent prefill: %s\n", blocked_ok ? "yes" : "*** NO ***");
        if (!blocked_ok) ++bad;
    }

    auto final_lease = pool.try_prefill();
    const bool final_ok = static_cast<bool>(final_lease);
    std::printf("decode exit restores prefill: %s\n", final_ok ? "yes" : "*** NO ***");
    if (!final_ok) ++bad;
    final_lease.release();

    // A lease must not hold the pool dispatch lock while its owner waits for an async pool call:
    // prefill's CPU future calls run_split_multi_native on another thread. The real kernel has its
    // own AVX-512 gate; keep this lock regression independent of whether this host can run it.
    if (cpu::cpu_avx2_ok()) {
        cpu::NativeFmt f;
        std::string err;
        if (!cpu::native_fmt(GGML_TYPE_IQ3_XXS, GGML_TYPE_IQ4_NL, cpu::H, cpu::FF, f, err)) {
            std::fprintf(stderr, "native format unavailable: %s\n", err.c_str());
            ++bad;
            goto test_done;
        }
        std::vector<uint8_t> blob(f.bytes, 0);
        std::vector<uint8_t> act(f.act_bytes);
        std::vector<float> x(cpu::H, 0.25f), out(cpu::H);
        cpu::native_quant_act(f, x.data(), act.data());
        cpu::ExpertJobMulti job;
        job.blob = blob.data();
        job.nt = 1;
        job.nact[0] = act.data();
        job.out[0] = out.data();
        auto lease_for_future = pool.try_prefill();
        auto work = std::async(std::launch::async, [&] { pool.run_split_multi_native(f, &job, 1); });
        const bool async_ok = work.wait_for(std::chrono::seconds(2)) == std::future_status::ready;
        std::printf("lease does not deadlock its async pool call: %s\n", async_ok ? "yes" : "*** NO ***");
        if (async_ok) {
            work.get();
        } else {
            std::fflush(stdout);
            std::_Exit(4);   // the future's destructor would wait forever on the old design
        }
    } else {
        std::printf("lease async pool call: SKIPPED (CPU lacks AVX2)\n");
    }

test_done:
    std::printf("\npool_lease_test: %d failure(s)\n", bad);
    return bad == 0 ? 0 : 1;
}
