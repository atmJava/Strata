// src/program/draft_kv_plan_test.cpp - which path computes the draft layer's prompt K/V (header-only, CPU).
//
//   1. one GPU: the batched pass is tried (the default);
//   2. a layer split: the drafter's own pass runs, silently;
//   3. no draft layer: nothing is tried and nothing is said.
#include "strata/program/draft_kv_plan.hpp"

#include <cstdio>
#include <initializer_list>

namespace dkv = strata::program::draft_kv;

namespace {
int g_fail = 0;
void check(bool ok, const char* what) {
    std::printf("  %-72s %s\n", what, ok ? "ok" : "FAIL");
    if (!ok) ++g_fail;
}
}  // namespace

int main() {
    std::printf("draft_kv_plan_test\n");
    for (const bool native : {false, true}) {
        const dkv::Plan p = dkv::plan(true, false, native);
        check(p.try_batched && p.why == nullptr, "one GPU: the batched pass is tried, nothing is reported");
    }

    for (const bool native : {false, true}) {
        const dkv::Plan p = dkv::plan(true, true, native);
        check(!p.try_batched && p.why == nullptr, "split: the drafter's own pass, silently");
    }

    for (const bool multi : {false, true}) {
        const dkv::Plan p = dkv::plan(false, multi, true);
        check(!p.try_batched && p.why == nullptr, "no draft layer: nothing tried, nothing said");
    }

    std::printf(g_fail == 0 ? "draft_kv_plan_test: all ok\n" : "draft_kv_plan_test: %d FAILED\n", g_fail);
    return g_fail == 0 ? 0 : 1;
}
