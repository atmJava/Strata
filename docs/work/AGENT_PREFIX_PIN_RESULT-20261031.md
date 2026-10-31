# AGENT-PREFIX-PIN result

Date: 2026-10-31

Status: accepted and left running.

## Decision

The opt-in `codex_auto_prefix_pin: true` now pins the end of the shared Codex
system block before the per-thread tool list. With four different tools and the
same system, the first request remained cold and the next three each reused
`10,013 / 10,287 = 97.34%` of the prompt. Their wall time was `1.392 / 1.322 /
1.291 s`, down from `5.019 / 4.996 / 4.986 s` in B3a, a further `73.31%`.

The complete four-request sequence fell from `22.676 s` in B3a to `13.322 s`
(`41.25%`). Against the B2 no-shared-system baseline it fell from `30.059 s`
to `13.322 s` (`55.68%`); the first two-request path alone improved from
`15.065 s` to `10.709 s` (`28.91%`).

## Implementation

The server adds an internal `strata_prefix` with:

```json
{"message": 0, "chars": "<system content length>"}
```

only when all of these hold:

- `/v1/responses`;
- `codex_system_before_tools` and `codex_auto_prefix_pin` are enabled;
- tools are present;
- the first prepared message is a string system message;
- the client did not send its own `strata_prefix`.

The existing `Service.resolve_prefix` renders the boundary and converts it to
an exact token prefix. Prompt tokenization is unchanged; only the engine's
`pin=N` cache-retention policy changes. The feature remains default off.

## Tests

- Default off sends no prefix.
- Enabled request gets a resolved pin whose decoded boundary ends exactly after
  the system content and immediately before `\n\n# Tools`.
- Enabled and disabled requests produce identical prompt token ids.
- An explicit client prefix is not overwritten.
- Requests without tools or a string system message are not pinned.
- Existing Responses, title, compaction, tool and structured-output paths pass.

Local `serve.test_responses`: 49 tests OK. Remote: 49 tests OK with one optional
`jsonschema` test skipped. Local and remote focused tests: 3 tests OK.

## Real-server result

### Sequential siblings

| Thread | Input | Cached | Reuse | Wall | Correct tool |
|---|---:|---:|---:|---:|---|
| 0 | 10,287 | 0 | 0% | `9.318 s` | yes |
| 1 | 10,287 | 10,013 | 97.34% | `1.392 s` | yes |
| 2 | 10,287 | 10,013 | 97.34% | `1.322 s` | yes |
| 3 | 10,287 | 10,013 | 97.34% | `1.291 s` | yes |

The first cold read took `9.318 s`; later sibling requests are now dominated by
queue/decode rather than repeated prefill.

### Concurrency and preserved paths

- Four simultaneous requests all completed with their own tools in
  `12.473–12.843 s`; three reused `10,014` tokens and one cold request completed
  normally.
- A separate interleaved cold-start run produced three restores at
  `15,087 / 15,345 = 98.32%` and one cold request; all four completed in
  `14.63–14.76 s` with their own tools.
- Eight simultaneous requests all completed with the correct tool. Seven reused
  `9,807 / 10,065` tokens; one cold request completed. Maximum wall was
  `18.315 s`, expected under six-slot overload.
- Four interleaved main/child compactions reused
  `8,827/8,831/8,831/8,831` tokens (`99.53%`) and all completed.
- Thread title stayed local at `1.9 ms`; all token and metrics deltas were zero.

Health remained loaded at `262144` context. The engine remained at six slots,
two groups and SHA-256
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`.
There were no HTTP, execution, CUDA, timeout, traceback or foreign-request
errors.

## Running shape

| Item | Value |
|---|---|
| Host UTC ready | `2026-10-31T05:17:27Z` |
| Server / vision / engine | `38589 / 38592 / 38627` |
| API port | `127.0.0.1:8080` |
| Health | loaded, context `262144` |
| Shape | 6 slots, 2 groups, 6 x 256K |
| Engine SHA-256 | `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3` |
| Config SHA-256 | `1d7dca5893df287dd8ef1b9a60784f6579c8bca95d11291cc0a8c7635230150e` |
| Server SHA-256 | `a38a6655e64f5b9d15af0bbf88b8a7dcfd2ffb3c587c98944e805b154ab3c594` |

The Python server loaded in about `147 s`. The engine was not rebuilt.

## Limits

- One boundary is pinned at a time per engine; concurrent prompts with different
  shared systems can replace which prefix is retained.
- Simultaneous cold siblings cannot reuse the first read.
- The four- and eight-request runs are both correctness and overload-safety
  evidence, not new aggregate-throughput claims.
- The first turn of a genuinely new system remains a cold prefill; this feature
  removes repeated sibling reads after that boundary exists.

## Evidence

- `/opt/ai/logs/agent-prefix-pin-20261031T0515Z/`
- `b4-sequential.json`
- `b4-parallel4.json`
- `b4-safety8-complete.json`
- `b4-compaction.json`
- `b4-title.json`
- `focused-unit.log`
- `full-responses.log`
- `final-metrics.json`
- `original/` and `original-sha256.txt`

## Rollback

Set `codex_auto_prefix_pin` to false or remove it and restart the Python server.
The system-before-tools ordering and its `5,120`-token reuse continue to work.
No engine rebuild or pack change is required.
