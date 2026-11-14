// batch_read.hpp: the pieces of a --batch prompt read, where the concurrent read runs (synthetic: no model, no GPU).
#include "strata/program/batch_read.hpp"

#include <cstdio>
#include <cstring>
#include <iterator>

using strata::program::batch_read_piece;
using strata::program::concurrent_read_off;
using strata::program::ConcurrentReadSetup;
using strata::program::piped_read_lead_in;

static int fails = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++fails; } } while (0)

int main() {
    // no --batch, or the pipelined groups: one run
    CHECK(batch_read_piece(false, false, false, false, 2048, 8) == 0);
    CHECK(batch_read_piece(true, true, true, false, 2048, 8) == 0);
    // one GPU: a chunk per piece, whether or not a slot decodes (the yield point is the chunk boundary)
    CHECK(batch_read_piece(true, false, false, false, 2048, 8) == 2048);
    CHECK(batch_read_piece(true, false, false, true, 2048, 8) == 2048);
    // layer split with slots decoding: a chunk per piece as before
    CHECK(batch_read_piece(true, false, true, true, 2048, 8) == 2048);
    // layer split, nobody decoding: pieces of 8 chunks - the BYIELD of a shorter request can be taken (it was one run)
    CHECK(batch_read_piece(true, false, true, false, 2048, 8) == 8 * 2048);
    CHECK(batch_read_piece(true, false, true, false, 1024, 4) == 4096);
    // chunks_per_piece 0: one run
    CHECK(batch_read_piece(true, false, true, false, 2048, 0) == 0);
    // a degenerate chunk never gives a zero piece
    CHECK(batch_read_piece(true, false, false, false, 0, 8) == 1);
    // a piece is always a multiple of the chunk
    for (int64_t c : {256, 1000, 2048, 4096})
        for (int64_t k : {1, 3, 8, 16}) CHECK(batch_read_piece(true, false, true, false, c, k) % c == 0);
    // concurrent_read_off: each setting alone keeps the concurrent read off and names its cause.
    CHECK(concurrent_read_off({}) == nullptr);
    bool ConcurrentReadSetup::*const each[] = {
        &ConcurrentReadSetup::pcie_dma,     &ConcurrentReadSetup::helper_caches, &ConcurrentReadSetup::staged_source,
        &ConcurrentReadSetup::other_source};
    const char* named[std::size(each)] = {};
    auto same = [](const char* a, const char* b) { return a != nullptr && b != nullptr && std::strcmp(a, b) == 0; };
    for (size_t i = 0; i < std::size(each); ++i) {
        ConcurrentReadSetup s;
        s.*each[i] = true;
        named[i] = concurrent_read_off(s);
        CHECK(named[i] != nullptr);
        for (size_t j = 0; j < i; ++j) CHECK(!same(named[i], named[j]));   // a reason of its own
    }
    CHECK(same(named[0], "--pcie-mode dma"));
    CHECK(same(named[3], "this expert source"));
    // with several, the first in the struct's order is named
    for (size_t i = 0; i < std::size(each); ++i) {
        ConcurrentReadSetup s;
        for (size_t j = i; j < std::size(each); ++j) s.*each[j] = true;
        CHECK(same(concurrent_read_off(s), named[i]));
    }
    {
        ConcurrentReadSetup s;
        s.staged_source = true;
        s.staged_prompt_independent = true;
        CHECK(concurrent_read_off(s) == nullptr);
    }
    {
        ConcurrentReadSetup s;
        s.staged_source = true;
        s.staged_prompt_independent = true;
        s.other_source = true;
        CHECK(same(concurrent_read_off(s), named[std::size(each) - 1]));
    }
    // piped_read_lead_in: on, beside decoding slots, within 1000 ms + the budget of the previous piecewise read's end
    CHECK(piped_read_lead_in(true, true, 0.0, 400.0));
    CHECK(piped_read_lead_in(true, true, 1400.0, 400.0));
    CHECK(!piped_read_lead_in(true, true, 1401.0, 400.0));
    CHECK(!piped_read_lead_in(true, true, -1.0, 400.0));
    CHECK(!piped_read_lead_in(false, true, 0.0, 400.0));
    CHECK(!piped_read_lead_in(true, false, 0.0, 400.0));
    if (fails == 0) std::printf("batch_read_test: ok\n");
    return fails == 0 ? 0 : 1;
}
