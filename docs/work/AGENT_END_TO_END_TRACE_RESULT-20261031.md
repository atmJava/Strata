# AGENT-END-TO-END-TRACE result

Date: 2026-10-31

Status: complete. The next active item is
[`AGENT-COMPACTION-REAL-SHAPE`](AGENT_COMPACTION_REAL_SHAPE_CONTRACT-20261031.md).

## Scope

This trace reads one frozen Codex rollout snapshot. It does not read Strata
server timing for these requests and does not claim that all model or tool time
is removable by Strata.

The tool reports:

- root task wall time;
- client-inferred model spans;
- tool call to tool output spans;
- the union of overlapping model and tool spans;
- token counts, compaction, completion and abort;
- tool output size classes and wait percentiles.

It never emits prompt, assistant, reasoning or tool bodies.

Frozen source:

```text
/tmp/rollout-01a18a33-20261031T-trace.jsonl
SHA-256 fe7850a8437145585e620777a42a6b1cc5b97f7bfa47eb7d4606dd74a4660803
```

The source is an active-session snapshot, so a later scan of the live file
will contain more requests. The reported results below always refer to this
hash.

## Accounting rules

- A tool span starts at `function_call`, `custom_tool_call` or
  `tool_search_call` and ends at the first matching output.
- A model span starts at the latest user or tool-output boundary in its thread
  and ends at the token-usage record. This is a conservative client-side
  inference, not server `prompt_ms` plus `decode_ms`.
- Overlapping intervals use a union. Tool time is never added naively to root
  wall time.
- `unattributed_gap_s` is only time not covered by the inferred model or tool
  intervals. It excludes no client assembly or clock uncertainty.
- A tool span includes command execution, subprocess work and polling. Strata
  cannot remove most of that time by optimizing inference.

## Overall result

| Item | Result |
|---|---:|
| Root tasks | 14 |
| Completed / aborted roots | 11 / 3 |
| Model requests | 4,180 |
| Tool calls / unpaired | 6,297 / 2 |
| Root wall union | 54,324 s |
| Inferred model union | 20,329.489 s |
| Tool union | 36,362.965 s |
| Input tokens | 753,516,637 |
| Cached input | 731,605,376 (97.09%) |
| Output tokens | 3,360,058 |
| Reasoning output | 982,658 |
| Output p50 / p90 / p99 | 483 / 1,714 / 5,069 |
| Reasoning p50 / p90 / p99 | 45 / 646 / 2,741 |
| Tool wait p50 / p90 / p99 | 910 / 30,004 / 30,166 ms |
| Tool output | 26,452,799 characters |
| Tool outputs >=10K characters | 914 |
| Characters in >=10K outputs | 68.57% |

The model and tool intervals cover almost all root wall time, but they overlap.
The long root below has only `15.843 s` outside those intervals over `44,783 s`.

## Longest root

Root `01a18d7c-9e70-7ef3-bbdf-1377736657c1` lasted `44,783 s` (12 h 26 min):

| Item | Result |
|---|---:|
| Model requests | 3,287 |
| Tool calls | 4,769 |
| Inferred model union | 14,854.926 s |
| Tool union | 31,087.885 s |
| Model/tool overlap | 1,175.654 s |
| Unattributed gap | 15.843 s |
| Compactions | 32 |
| Output / reasoning tokens | 2,446,571 / 690,179 |
| Tool output | 19,489,653 characters |
| Tool calls >=30 s | 786 |

The second long root was aborted after `6,791 s`, with 532 model requests,
824 tool calls and 3,990.536 s of tool union.

These roots show that tool orchestration and long-context model work are both
material. They do not justify another unmeasured kernel change.

## Tool cost

| Tool | Calls | Span sum | Calls >=30 s | Output >=10K |
|---|---:|---:|---:|---:|
| `write_stdin` | 946 | 22,408.605 s | 618 | 10 |
| `exec_command` | 4,677 | 16,710.132 s | 274 | 904 |
| `apply_patch` | 571 | 15.080 s | 0 | 0 |
| `tool_search` | 13 | 7.228 s | 0 | 0 |

Most of the wait is command execution or deliberately polling a still-running
session. A future tool-context change can reduce repeated model input, but it
must preserve command output and correctness. It cannot claim the command's
own runtime as saved inference time.

## Real output-shape decode

The active server was measured with a warm prompt and deterministic output
lengths. No restart was used.

- 480 output tokens, one stream: `4.682 s`, `104.83 tok/s`.
- 480 output tokens, two streams: completion span `9.354 s`; the second
  request's TTFT was `4.701-4.708 s`.
- 1,708 output tokens, one stream: `16.385 s`.
- 1,708 output tokens, two streams: completion span `32.790 s`; the second
  request's TTFT was `16,384 ms`.

All requests returned the same hash per shape, all finished with `length`, and
there were no errors. The current exactly-two-stream policy is therefore truly
serial: the second request does no decode until the first finishes.

This resolves the earlier ambiguity. The policy improves total completion for
two equal requests by about 15%, but it delays the second request's first token
by the entire first generation. It cannot be presented as an interactive
multi-session win without a role-aware policy.

## Compaction

The frozen trace has 44 compaction requests:

| Item | Result |
|---|---:|
| Compactions with >=50% cache reuse | 1 |
| Compactions with <50% cache reuse | 43 |
| Total input | 12,480,353 |
| Total cached input | 578,048 (4.63%) |
| Total uncached input | 11,902,305 |
| Miss median input | 283,884 |
| Miss median cached | 7,424 |
| Miss median uncached | 277,803 |
| Compaction inferred span | 1,186.236 s |
| Share of all uncached input | 54.32% |
| Share of root wall union | 2.18% |

The single cache hit reused `291,328 / 291,699` input tokens. The other 43
requests used only about two percent cached input.

This is the strongest measured whole-task opportunity. If only half of the
compaction model span were removed, the conservative whole-session upper bound
would still be about `1.09%`. It also matches the earlier all-session scan,
where compaction was `51.24%` of uncached input.

The 180K synthetic Strata replay previously reused `99.984%`. That result is
still valid for the request shape it sent, but it is not evidence that real
client compactions always match that shape. The next item must identify the
first structural divergence before implementing another cache mechanism.

## Server correlation limit

The current `/metrics` keeps only recent requests. A long rollout cannot be
matched reliably to its old server timing after more than 500 requests, cache
changes and process restarts. Existing matched subsets also came from
different providers and concurrency states. No production frequency or
server-side duration is derived from those partial matches.

## Decision

`AGENT-END-TO-END-TRACE` is complete.

The next active item is `AGENT-COMPACTION-REAL-SHAPE`, ahead of:

1. role-aware two-stream admission;
2. hot prompt/restore fixed cost;
3. tool-context diet;
4. recovery and cancellation;
5. old-session p99 restore.

No engine rebuild or server restart was performed.

## Verification

```text
python -m pytest -q tools/server/tests/test_trace_agent_tasks.py \
  tools/server/tests/test_probe_codex_sessions.py
10 passed
```

`py_compile` and `git diff --check` also passed.

The trace tool initially counted only `function_call` records. This audit found
and fixed the omission of `custom_tool_call` and `tool_search_call`; the new
regression checks that custom and search tools are counted and their bodies
never appear in output.

## Evidence

- `/tmp/agent-trace-01a18a33-frozen.json`
- `/tmp/agent-decode-480-20261031.json`
- `/tmp/agent-decode-1708-20261031.json`
- `/tmp/rollout-01a18a33-20261031T-trace.jsonl`

The raw rollout is intentionally not copied into the repository.

