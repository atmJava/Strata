# AGENT-CODEX-TITLE contract

Date: 2026-10-31

Status: preregistered before enabling the existing opt-in on the server.

## Question

Can the current agent configuration stop Codex thread-title requests from
consuming a prompt cache slot and an inference turn?

## Measured baseline

The running A2 configuration does not set `codex_thread_titles`. Three synthetic
Codex title requests used the same roughly 12K-token instruction prefix:

| request | wall | prompt tokens | reused | output tokens |
| ---: | ---: | ---: | ---: | ---: |
| 1 | `10.292 s` | `12,073` | `0` | `154` |
| 2 | `2.791 s` | `12,073` | `12,068` | `184` |
| 3 | `1.978 s` | `12,073` | `12,068` | `146` |

Every request increased the engine request count by one. The first title after a
cold prefix pays a full read; later titles still decode a model answer even when
the prefix is cached. This workload is relevant to the user's normal
one-main/three-child-agent pattern because title work is not part of the
conversation answer and competes with the active threads.

## Candidate

Set the existing opt-in configuration value:

```json
"codex_thread_titles": true
```

This changes no engine source and does not set a new code default. The server
recognizes Codex's `thread_source: "thread_title"` metadata and answers the
title request directly with the documented title schema.

## Gates

Before a server reload:

1. the focused Codex title unit tests pass;
2. the normal full-agent request still reaches the engine;
3. an unrecognized/non-title request still reaches the engine.

After the reload:

1. health, engine/server/config hashes and six-slot/two-group shape are recorded;
2. the config value is visible in the running config and process;
3. at least three title requests return a valid 36-character title;
4. title requests add zero engine requests, prompt tokens and output tokens;
5. a normal `/v1/responses` request still adds one engine request;
6. no HTTP, execution, CUDA, timeout or foreign-request error occurs;
7. the change is rollback-safe by removing the config key and restoring the A2
   process.

The candidate is accepted only if the title path is reached and avoids engine
work. Performance is reported as saved wall time per title; no aggregate 2/4/6/8
claim is made from this one-time event.

## Rollback

Restore the exact A2 config and baseline process, then verify engine/config
hashes, health, request routing and the absence of the candidate-only behavior.

Never write an API key or token to the repository, logs or conversation.
