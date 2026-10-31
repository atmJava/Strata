# AGENT-PREFIX-PIN contract

Date: 2026-10-31

Status: accepted. Result:
[`AGENT_PREFIX_PIN_RESULT-20261031.md`](AGENT_PREFIX_PIN_RESULT-20261031.md).

## Problem

`AGENT-STATIC-SYSTEM-PREFIX` now puts the shared system before per-thread tools and saves
`5,120` cached tokens on later siblings. The rendered shared boundary is much later, around
`8.5K–9K` tokens, but the normal prompt cache restores only its last eligible checkpoint.

The engine already supports a pinned shared prefix through `strata_prefix`. A manual request
with `{"message": 0, "chars": len(system)}` was resolved to a `pin` at the end of the system
before the tool list. With four different tools and the same shared system:

| Request | Input | Cached | Reuse | Wall |
|---|---:|---:|---:|---:|
| 0, cold | 10,229 | 0 | 0% | `8.575 s` |
| 1 | 10,229 | 9,976 | 97.53% | `1.134 s` |
| 2 | 10,229 | 9,976 | 97.53% | `1.119 s` |
| 3 | 10,229 | 9,976 | 97.53% | `1.089 s` |

This is a manual boundary test, not yet an automatic production result.

## Candidate

Add an opt-in server config key `codex_auto_prefix_pin: false`. When enabled and these conditions
hold:

- the route is `/v1/responses`;
- `codex_system_before_tools` is true;
- tools are present;
- the first prepared message is a string system message;
- the request did not provide its own `strata_prefix`;

inject a private copy of:

```json
{"strata_prefix": {"message": 0, "chars": "<exact system content length>"}}
```

The existing `Service.resolve_prefix` must still convert that to the exact token boundary by
rendering and comparing with the full prompt. The pin changes cache retention only. Prompt text,
tool JSON, sampling, engine arithmetic and finish semantics must not change.

The feature does not touch title shortcuts, Chat Completions, Anthropic Messages, requests without
tools, requests without a string system block, or requests that explicitly set `strata_prefix`.

## Gates

Source and focused tests:

1. default off adds no `strata_prefix`;
2. enabled Responses gets a pin exactly before the tool boilerplate;
3. the rendered prompt and token ids are identical with the pin on and off;
4. an explicit user `strata_prefix` is never overwritten;
5. no-tools, non-system, title and compaction paths preserve current behavior;
6. malformed or unusable automatic boundaries are logged and omitted rather than refusing a
   normal request;
7. frontend, Responses and server prefix tests pass.

Real server:

1. one Python server restart only; engine hash and 6 slots / 2 groups stay unchanged;
2. four different-tool sibling requests reuse materially more than `5,120` tokens;
3. every request calls only its own declared tool;
4. a two-request path improves by at least 1%, and four interleaved siblings do not regress;
5. title remains local with zero engine/token and compaction remains above 99% reuse;
6. health, resources, HTTP/CUDA/timeout/finish checks pass;
7. four simultaneous requests remain safe and do not call another thread's tool.

## Upper bound

Manual pinning reduced later-request wall from about `5.0 s` to `1.1 s`, a further saving of
about `3.9 s` per sibling request. The whole four-request sequence would move from about
`22.7 s` to about `11.9 s`, another `47%`, before counting the cold first read. This comfortably
exceeds 1% and is worth one server restart if the source test passes.

## Rollback

Set `codex_auto_prefix_pin` to false or remove it and restart the Python server. No engine rebuild
or pack file change is required.

## Limits

- One pinned prefix exists per engine; a different concurrent shared document can replace which
  boundary is retained.
- Simultaneous cold requests still cannot share their first read.
- Pinning retains K/V memory already accounted for by the conversation cache; resource safety must
  still be measured on the real 12-slot / 6-batch configuration.
