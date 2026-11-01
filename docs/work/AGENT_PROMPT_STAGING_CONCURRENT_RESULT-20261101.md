# AGENT-PROMPT-STAGING-CONCURRENT result

Date: 2026-11-01

Status: **adopted in the production opt-in `STRATA_PIPED_CONCURRENT=1` path**.

## Result

The production gate used to disable concurrent prompt reads whenever
`FileExpertSource::stages_blobs()` was true. `STRATA_IO_PREFETCH` makes that
query true even on the production `experts.bin` page-fill path, so a cold
154K-token prompt stopped both decoding neighbours for about 82 seconds.

The candidate adds one conservative source-level ownership query:

- `ExpertSource::prompt_copies_independent()` defaults to false.
- `FileExpertSource` returns true only for the page-fill `experts.bin` shape:
  no GGUF role assembly, no direct decode handles, no direct prompt handles,
  and a live mapping.
- The concurrent-read gate permits a staged source only when that query is
  true and the source is the main file source.

The prompt copy writes to the stager's own caller-owned ring. Decode's recyclable
`stage_buf_` does not participate. Page-fill `pread` destinations are caller
owned and its decode marker is thread-local. The change does not touch kernels,
arithmetic, sampling, token selection or finish rules.

## Frozen workload

Two greedy decoding streams start first. Six seconds later one unique,
uncached synthetic source prompt joins and asks for 64 output tokens. Every
repetition uses a different run tag; the stream output limit is 1,600 tokens.

The prompt corpus was frozen after discovering that two documentation files
created after the original baseline changed the generated workload. Baseline A2
used 154,994 prompt tokens and candidate used 154,992; all six measured requests
reported `cached_tokens=0`. The two extra tokens come from the different fixed
tag prefix.

## Performance

The directly recorded whole-scenario wall, neighbour gap and token count come
from the same runner implementation on both restored baseline A2 and candidate.

| metric | baseline A2 median | candidate median | change |
|---|---:|---:|---:|
| whole-scenario wall | 104.843 s | 83.118 s | 20.721% faster |
| joiner TTFT | 84.058 s | 72.384 s | 13.888% faster |
| worst neighbour no-token silence | 82.059 s | 1.713 s | 97.912% lower |
| neighbour tokens during read | 215 | 1,548 | 7.20x |
| decode aggregate tok/s | 16.9 | 27.7 | 63.905% higher |

All three candidate repetitions reduced the worst neighbour silence by more
than 10 seconds and 20%. The candidate logs contain three real concurrent-read
lines, with 1,795, 1,552 and 1,554 decoded rows during the reads.

## Four streams

An initial three-wave candidate sample happened to land at 14.6 s while a
three-wave baseline A2 sample landed at 14.1 s. Five-wave controls resolved the
small-sample noise:

| arm | 4-stream wall median | aggregate | requests |
|---|---:|---:|---:|
| baseline A2 | 14.0 s | 110.7 tok/s | 25 measured + 5 warmups |
| candidate | 13.1 s | 119.0 tok/s | 25 measured + 5 warmups |

The candidate was 6.4% faster by wall and 7.5% higher by aggregate throughput.
The initial three-wave result remains recorded rather than discarded.

## Correctness and safety

- Fixed greedy prompts 1, 2, 3 and 5 are byte-identical in two candidate runs
  and both baseline runs.
- Prompt 4 is unstable in both arms; prompt 6 is unstable in baseline. They are
  not used as arbitrary byte-parity gates because the baseline itself cannot
  supply a stable reference.
- `batch_read_test: ok`; `file_expert_source_test: PASS`; `py_compile` and
  `git diff --check` pass.
- Health stayed loaded with 262144 context, 6 slots and 2 groups.
- No HTTP/execution error, unexpected finish, engine restart, or NVRAM Xid was
  recorded in the measurement windows.
- Production config stayed byte-identical at
  `d76772e7089c18af38c0a8932588d99674a8b6bd8208f386b6c8599fd81c863a`.

## Production identity and rollback

```text
candidate build
/opt/ai/src/strata-batch-prefill-guard-20261101/build-prompt-concurrent-prod/strata
e1208e5166b11b541fe110f3ed99c140da20718e981621c9849a5917b582e707

installed production engine
/opt/ai/src/strata-batch-prefill-guard-20261101/build-guard-prod/strata
e1208e5166b11b541fe110f3ed99c140da20718e981621c9849a5917b582e707

restored baseline engine
e60a334c0ca5c1974d79385256aaf90519bcb646e2626f7e66de818d104b0ab7

verified pre-deployment backup
/opt/ai/tmp/pre-prompt-staging-20261101TEkZYfn
```

The final production process is the candidate binary with
`STRATA_PIPED_CONCURRENT=1`, health loaded, 6 slots / 2 groups. Rollback is to
copy the byte-identical `e60a334c...` engine back and restart the service.

## Limits

- This is a bounded 154K cold-read window, not a proof for every prompt size or
  every possible expert source. Other staged sources remain refused.
- Four streams were checked for throughput/error/health, while the detailed
  correctness and path proof used the two-stream join workload.
- GPU Xid stability remains a separate risk. No new Xid appeared here, but this
  short window does not close that incident.
- Cold process startup to health was 170-240 seconds; that cost is not counted
  as request latency.
