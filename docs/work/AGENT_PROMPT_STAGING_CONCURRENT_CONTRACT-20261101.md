# AGENT-PROMPT-STAGING-CONCURRENT contract

Date: 2026-11-01

Status: **pass; adopted in the production opt-in path.**

Result: [AGENT_PROMPT_STAGING_CONCURRENT_RESULT-20261101.md](AGENT_PROMPT_STAGING_CONCURRENT_RESULT-20261101.md).

## Problem

The live three-session trace showed cold `160K-180K` prompt reads freezing two
decoding neighbors for 44-80 seconds. `STRATA_PIPED_CONCURRENT=1` is present in
the production environment, but the gate refuses every read because
`FileExpertSource::stages_blobs()` includes `STRATA_IO_PREFETCH`.

That boolean is too coarse for the production pack:

- decode `blob()` may use the source's recycled, mutex-protected staging buffers;
- the prompt `Stager` calls `copy_blobs()` with destinations in its own pinned ring;
- for an `experts.bin` page-fill source, `copy_blob()` copies either from the
  resident complement or directly from the read-only mapping into the caller's
  destination, and its `pread` scratch is thread-local;
- there is no direct/unbuffered handle and no GGUF in-place role assembly in the
  production path.

The candidate therefore narrows the gate instead of removing it.

## Code contract

1. `ExpertSource` exposes a conservative `prompt_copies_independent()` query;
   the default remains false.
2. `FileExpertSource` returns true only for the page-fill `experts.bin` shape:
   `io_pf_`, no GGUF role assembly, no full unbuffered handles, and no prompt
   direct handles.
3. `concurrent_read_off()` still refuses every other staged source.
4. The production gate uses that query only when the request source is the main
   file source, not a helper/remote source.
5. No kernel, arithmetic, token selection, sampling, finish rule or default
   outside the explicit `STRATA_PIPED_CONCURRENT=1` path changes.

## Frozen workload

- Production model, pack, two-GPU layer split, six slots, two groups and all
  current engine/server options stay unchanged.
- Each repetition starts two greedy decode streams. After six seconds, one new
  cold prompt of approximately 160K tokens joins and asks for 64 output tokens.
- Prompts and tags differ between repetitions so conversation-cache reuse cannot
  hide a cold read. Every arm receives a warmup before measurement.

## Required gates

Path and correctness:

- the engine log contains the concurrent-read line with more than zero decoded
  rows during the read;
- zero HTTP/execution errors, unexpected finish reasons or health failures;
- fixed greedy prompts complete with identical answer bytes in candidate and
  restored baseline where the baseline itself repeats.

Performance:

- at least two of three paired repetitions reduce the worst neighbour no-token
  silence by at least 10 seconds and 20% versus baseline;
- median whole-scenario wall improves by at least 1%;
- joiner TTFT does not regress by more than 5%;
- aggregate peer token production during the read does not fall;
- a four-stream smoke does not regress wall by more than 1% and has zero errors.

Rollback:

- any path miss, wrong output, unexpected finish, resource/health failure, new
  Xid, or failure of the performance gates restores the byte-identical baseline
  config and engine.

## Restart budget

One candidate boot and one baseline restore boot are required, each about three
minutes. A baseline process is already idle, so its first arm needs no restart.

## Verdict

All pre-registered performance, path, parity, four-stream, health and Xid gates
passed in the frozen 154K workload. The exact measurements and the controls for
the initial noisy four-stream sample are recorded in the result document.
