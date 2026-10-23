# The PLE read path: source notes

Workstream W2, task W2.1 of [SERVER_MASTER_PLAN.md](SERVER_MASTER_PLAN.md). Read from the tree on this checkout
(`hw/server-adapt`, which equals the serving tree except for the S1 change). File and line references are to 0.2.2 /
engine 0.1.47. Nothing here was measured; where a number appears it is quoted from the server's own record, and
that is said.

## What a PLE row is

- The n-gram table is one GGUF shard (`--ple-gguf`). A token asks for 16 rows
  (`PLE_N_HEADS = (NGRAM_SIZE - 1) * HEADS_PER_NGRAM`, `include/strata/kernels/ngram.hpp:35`), each 160 floats
  (`PLE_HEAD_DIM`, line 32), so 2,560 floats per token.
- On disk in IQ4_NL a row is 90 bytes (`PLE_ROW_BYTES = (PLE_HEAD_DIM / 32) * 18`, line 47). Other formats carry
  their own size: the reader's `row_bytes` is a runtime parameter since the FP8 table (`src/ngram/ple_reader.cpp:301`).

## How a prompt's rows are read

1. `prefill.cpp:2665` `ple_gather()` turns the chunk's tokens into 16 row ids each (`ngram_rows`) and calls
   `PleTable::gather_batch` **once for the whole chunk**.
2. `prefill.cpp:2790-2827`: the gather runs on an `std::async` thread pinned to an aux CPU
   (`aux_cpus::pin_current_thread`). The next chunk's gather starts while the current chunk computes (two buffers,
   `ple_buf ^= 1`), and `ple_land()` waits for it and uploads the rows just before layer 1 reads them. The comment at
   2790-2792 says the first chunk is read beside layer 0 on purpose: gathering it first left the GPU idle for the
   whole read (~0.4 s of a 32K prompt).
3. `PleTable::gather_batch` (`src/kernels/ngram.cpp:538`) is one reader request. It dedupes by page and sorts across
   the whole batch, and uses the reader's full queue depth.
4. The reader is `src/ngram/ple_reader.cpp`. Reads are **page-granular**: `PAGE = 4096`
   (`include/strata/ngram/ple_reader.hpp:28`), and a job is one aligned page, or **two pages when the 90-byte row
   straddles a page boundary** (`Job::length`, `ple_reader.cpp:80-87`; the `in_page + row_bytes > bytes` case at 232).
5. I/O runs on one worker thread by default (`io_thread`), with up to 256 reads in flight
   (`PleIoOptions::max_inflight`, `ngram.hpp:152`) and a bounded 8-way row cache of 1,048,576 rows, about 95 MB in
   IQ4_NL (`ngram.hpp:153`, `RowCache` at `ple_reader.cpp:36`).

So the "90 bytes read as 8 KiB" in the server's own record is, in this source: **4,096 bytes per touched page and
8,192 for a row that straddles a page**, with 16 rows per token. A token asks for 1,440 bytes of data and touches up
to 16 pages — 64 KiB before the page dedupe, less after it.

## What `PLE` in the timing line measures, and why it cannot be a per-request number

`prefill.cpp:5010-5012` prints `stats_.ms_ple`. It is added in three places:

| Where | What is timed |
|---|---|
| `prefill.cpp:2803-2829` (`ple_land`) | the wait for the async gather plus the host→device upload of the rows |
| `prefill.cpp:3121` | the batched PLE block over the chunk (native/BF16 postops, one GEMM pair) |
| `prefill.cpp:3135` | the per-token fallback block (`STRATA_PLE_BATCH=0`, or a table without the native key) |

`stats_` is a `PrefillStats` member of `Prefill` (`include/strata/prefill/prefill.hpp:281`), and `Prefill` is a member
of `GpuStage` (`src/program/generate.cpp:1208`). That object lives as long as the engine. `Prefill::reset()` is the
only place the counters are cleared (`prefill.cpp:1056-1060`), and its only callers are `--prefill-head-alias`
(`generate.cpp:12376`, `13985`) and the out-of-memory retry that asks for a smaller chunk (`8731`, `8734`).

**Therefore, in a serving process, `PLE` is a running total since the engine started.** A line that reads
`wall 421 ms ... PLE 33674 ms` is not a contradiction: one number is this request, the other is the process. The
consequence for this workstream: the per-request PLE cost has to be measured as the difference between two printed
values (or with a temporary probe), and the server's record reading `PLE 33xxx ms` as a per-request cost overstates
it.

The record's 5.98–6.58 s for a new 8.2K prompt is not invalidated by this: those measurements were taken on one-shot
engine runs, where the process starts empty and the total is that run's.

## What this changes for the candidates

1. **The measurement comes first.** Take the delta on a cold 8K and a cold 32K prompt with the concurrent read off,
   so the read is on the critical path. That delta is the number a change has to move.
2. **The 4 KiB-per-row amplification is real in the source.** A smaller read unit needs the file opened without
   O_DIRECT alignment, or a table repacked with row-aligned rows — the second is a pack change, not an engine one.
3. **"Issue the read earlier" is already done for chunks 2..N** (one chunk ahead). What remains exposed is the first
   chunk of a request, which is read beside layer 0 by design.
4. **`--ple-io ram` is rejected** (no RAM for 28.8 GB) and **`mmap`** goes through the page cache, whose behaviour the
   engine will not predict (#2040).

## Still open (W2.2 input)

The box reports 7,018–8,580 rows/s while the disk was probed at 111,804 IOPS elsewhere in the record. At 4 KiB and
256 reads in flight, 7K rows/s is about 36 ms per read, which looks like a queue that is not saturating rather than a
slow drive. Reading the submit/reap loop against that number is the next step; it is unconfirmed static analysis
until it is measured.

## Measured 2026-10-23: PLE is not the bottleneck any more

Method: `PLE` is a running total, so the per-request cost is the difference between the value before a cold prompt
and the value after it, over the lines of the current engine start only (a layer split prints one line per stage and
the last line in the file is not always the stage that does the PLE work). Real prose from `/opt/ai/docs` is used as
the prompt, a different slice each time: synthetic filler has repeating n-grams, and the reader's million-row cache
answers those, which measured about 1 ms per request and would have hidden the effect.

| Prompt | read | PLE, first request after a start | PLE, later requests |
|---|---|---|---|
| 6,248–6,663 tokens of real text | 4.9–6.6 s (1,179–1,255 tok/s) | **1,015 ms** | 0–1 ms |
| 24,060–24,101 tokens of real text | 12.2–12.4 s (1,935–1,967 tok/s) | 203 ms | 0–62 ms |

Read as: with the concurrent read (S1) in place, the gather runs on the prompt's own thread while the GPU works, so
it hides; what remains is the first request after an engine start, when the row cache and the page cache are cold.
That is a one-off per restart, not a per-session cost.

Two limits of this measurement: two to three samples per size in one boot, and the first-request figure was seen
twice at two sizes, not repeated per size.

**Decision (W2.2):** none of the three PLE candidates is implemented now. `--ple-io ram` is out for RAM; the smaller
read unit is a pack change; issuing the read earlier is already done for chunks 2..N, and the measurement shows the
first chunk no longer dominates. W2.3/W2.4 are closed on this evidence and re-open only if a real load shows a cost
that matters.

What the numbers do point at is the shape every agent request has: the same real text reads at 1,179–1,255 tok/s at
6K and 1,935–1,967 tok/s at 24K. The small prompt pays a fixed cost per token, which is the effect the server's
record already described; the next performance candidate is W3 (the `sm_75` prompt-expert path), which targets that
read directly.

