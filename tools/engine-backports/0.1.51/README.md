# Engine patches this checkout does not build

`serve/` is what this checkout serves; the CUDA engine it talks to is built from a tree that is
not here. The deployment at 192.168.9.100 runs an engine built from
`strata-batch-prefill-guard-20261101` (reports `0.1.51`, CUDA 13.0, `sm_75`). These files are what
was applied to that tree and measured, kept here so the change is reviewable and replayable
instead of living on one machine only.

## 0001-conversation-cache-release-free-heap.patch

Upstream `8c71c4927` (#5697), backported onto the tree above. A parked conversation's segments are
16 MiB allocations; once glibc's dynamic mmap threshold has risen they come from the heap and
`free()` returns only the heap's top, so the physical-RAM gate evicted every parked conversation and
still refused. `evict_oldest()` now hands the freed pages back (`malloc_trim`), `make_room()` keeps
dropping without it, and the admission gate in `generate.cpp` hands back once before evicting.

Apply from the engine tree root:

    patch -p1 --forward < 0001-conversation-cache-release-free-heap.patch
    cp conversation_heap_test.cpp src/core/

(`0001` also adds the `conversation_heap_test` target to `CMakeLists.txt`; the source file is
beside it because a patch that adds a new file cannot be applied without it.)

## What it measured

    conversation_heap_test: parked 3 x 512 MiB, RSS 1539 MiB -> 1027 MiB after evicting the oldest
    (511 MiB returned to the system), eviction took 10.14 ms
    the second eviction took 9.77 ms, RSS now 515 MiB
    ok

Upstream's own measurement was 511 MiB returned in 8.8-9.3 ms (Boston, glibc 2.39), so the
backport reproduces it.

Build, from the engine tree's build folder (the whole tree does not build: `cuBLAS` in that folder
is missing `cublasGetStatusString`, and `tests/cuda/prefill_fused_iq_test.cpp` does not compile, both
older than this change):

    cmake --build . --target strata -j 20
    cmake --build . --target conversation_heap_test -j 20

## Not measured

The eviction path under real traffic. The service parks conversations only when a request's prompt
is long enough to checkpoint, and `parks`/`evictions` were still 0 on the deployment after the swap,
so the changed code has been exercised by the unit test and by an idle cache, not by a parked
conversation being evicted under load.

## The PLE buffer, in the same rebuild

`src/program/generate.cpp` sized a probe buffer with a literal `2560` floats a token; `gather_batch`
writes `PLE_N_HEADS x PLE_HEAD_DIM`, which is 16 x 160 = 2560 in this build but larger where the
layer-input width is. The literal is now that product. On this build the rebuild came out **byte for
byte identical** to the binary it replaced (`md5 e900039a98f827cf9f37dc88394f107b`), which is the
evidence that it changes no behaviour here and is a fix for other builds only.
