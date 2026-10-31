# Agent session profile result

Date: 2026-10-31

Status: diagnostic complete. The current A2 server was not restarted or changed.

## Decision

The normal 1/2/4-session agent path already reuses its conversation prefix
well. Do not spend the next candidate on the core conversation-cache path.

The immediate actionable loss is Codex's thread-title request. The running A2
configuration leaves `codex_thread_titles` off, so a title request enters the
engine, occupies work and may displace the active conversation even though the
server can answer it directly.

The next candidate is therefore the existing opt-in
`AGENT-CODEX-TITLE`, not another engine-kernel change.

## Harness

Added `tools/server/probe-agent-sessions.py`. It is a read-only HTTP probe with
no engine restart and no prompt/response content in its output. Each level runs
independent conversations with a shared instruction prefix and consecutive
tool-like turns. The exact prompt size is taken from the server response.

The first A/A run used nominal 8K context, 1/2/4 sessions, six waves and two
rounds. Its raw JSON computed `expected=36` from the number of levels rather
than the sum of their session counts; the request counts themselves remained
valid. The analysis below uses the actual 84 measured requests. The harness was
corrected before the shared-prefix run.

The shared-prefix run used 1/2 sessions, four waves and two rounds. Its corrected
request accounting was `expected=24`, `seen=24`, `foreign=0`.

## Short-context A/A

Shared prefix was about 25% of the prompt. No measurement request failed.

| sessions | round | prompt tokens | reuse | prompt | wall | TTFT |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | cold/shared | 11,857 | 25.15% | 7,303.6 ms | 7.377 s | 7,330.5 ms |
| 1 | continuation | 12,453 | 95.16% | 1,450.4 ms | 1.556 s | 1,477.5 ms |
| 2 | cold/shared | 11,857 | 25.15% | 7,313.2 ms | 11.072 s | 10,993.0 ms |
| 2 | continuation | 12,453 | 95.19% | 1,414.5 ms | 2.365 s | 2,268.5 ms |
| 4 | cold/shared | 11,857 | 25.15% | 7,364.8 ms | 18.733 s | 18,467.5 ms |
| 4 | continuation | 12,453 | 95.21% | 1,378.4 ms | 5.892 s | 3,661.5 ms |

The first level-1 cold wave was a `10.009 s` outlier. Excluding it, five
level-1 cold waves were `7.325–7.382 s`. The remaining wave-level 95% intervals
were:

| sessions | first turn | continuation |
| ---: | ---: | ---: |
| 1 | 11.10% including the first outlier; stable waves about 1% | 1.53% |
| 2 | 0.28% | 2.45% |
| 4 | 0.16% | 0.68% |

This is why a candidate at 1/2 streams needs interleaved repeats. A sequential
single A-B-A sample is still too weak for level 1.

## Shared-prefix run

With about 75% of the prompt in the shared instruction block, the server reused
69.09% of the actual prompt on a new conversation:

| sessions | round | prompt tokens | reuse | prompt | wall |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | new conversation | 11,857 | 69.09% | 3,199.1 ms | 3.269 s |
| 1 | continuation | 12,453 | 95.16% | 1,350.1 ms | 1.451 s |
| 2 | new conversation | 11,857 | 69.09% | 3,177.1 ms | 4.886 s |
| 2 | continuation | 12,453 | 95.19% | 1,396.3 ms | 2.263 s |

Stable wave-level 95% intervals were `0.32–0.88%`. Therefore the cache is
working; the remaining opportunity is to keep work that is not a real
conversation turn out of the engine.

## Codex title baseline

Three `/v1/responses` requests carried Codex's
`thread_source: "thread_title"` metadata and the same roughly 12K-token
instruction prefix:

| request | wall | prompt tokens | reused | output tokens |
| ---: | ---: | ---: | ---: | ---: |
| 1 | `10.292 s` | `12,073` | `0` | `154` |
| 2 | `2.791 s` | `12,073` | `12,068` | `184` |
| 3 | `1.978 s` | `12,073` | `12,068` | `146` |

Every request added one engine request. Even after the instruction prefix was
cached, title generation cost about two seconds and produced a reasoning block
before the title.

## Current configuration

The running A2 config has neither `codex_thread_titles` nor
`codex_compaction_cache`. The server source already has and tests both opt-ins.
The focused codec/Responses test suite passed:

```text
41 passed, 3 subtests passed
```

## Evidence

- `/opt/ai/logs/agent-sessions-aa-20261031T0428Z.json`
  (sha256 `f54ea469977619f64364b58ac15a127a4b0c8c9f1a5d28a15806e37506b8ce06`)
- `/opt/ai/logs/agent-sessions-shared75-20261031T0438Z.json`
  (sha256 `20d6c367ca630ce9be3aa4fe7f1127edf8aa2a292bc9b487161090b02e605edc`)
- deployed probe at the run:
  `/tmp/probe-agent-sessions.py`
  (sha256 `5b7ad85e18bc499fbb3aa41ec4441122b9706658d5b59c24c7cebf5e91314d1d`)

The raw A/A JSON's request-accounting field predates the count fix and must not
be used. Its per-request rows and summary remain valid.

## Not measured

There was no real user traffic during the run. The probe models agent request
shape and concurrency; it is not presented as capture of the user's production
tokens. Long 128K/210K Agent traces and real multi-thread compaction frequency
remain for the next diagnostics.
