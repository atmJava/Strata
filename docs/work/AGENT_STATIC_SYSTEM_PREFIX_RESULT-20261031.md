# AGENT-STATIC-SYSTEM-PREFIX result

Date: 2026-10-31

Status: accepted and left running.

## Decision

The opt-in `codex_system_before_tools: true` now places the shared Codex
instruction/system block before the per-thread tool list. On a real server,
four sibling requests with the same long system block and four different tools
reused `5,120` prompt tokens on every request after the first. Mean wall time
fell from the B2 no-reuse baseline of `7.515 s` to `5.669 s` (`24.56%`); the
three later sibling requests fell from `7.502 s` to `5.001 s` (`33.34%`).

All four requests called their own declared tool. A concurrent four-request
run also returned the correct tool in every request, while all four reused
`5,120` tokens. An eight-request safety run completed with eight correct tool
calls; seven reused `8,192` tokens and one cold request completed normally.

## Source gate

The frozen fixture rendered by the previous template and by the new default-off
path has the same SHA-256. With different tools:

| Item | Before | Enabled |
|---|---:|---:|
| Rendered characters | 71,579 | 71,579 |
| Common prefix | 320 bytes | 54,366 bytes |
| System position | after tools | before tools |

Local and remote suites passed:

- `serve.test_frontend`: 30 tests OK;
- `serve.test_responses`: 46 tests OK locally; 46 tests OK remotely with one
  optional `jsonschema` test skipped;
- `python tools/server/preflight.py --offline`: PASS.

The tests cover byte-identical default off, complete system/tool content,
declared tool order, the Responses opt-in and existing tool/thinking paths.

## Runtime template location

B3 initially still reported zero cache reuse because the server loads
`<pack>/tokenizer/chat_template.jinja` before the repository fallback
`serve/chat_template.jinja`. Updating only the repository template therefore did
not change the live prompt. The original pack template was backed up and the
candidate template copied into the pack before the final restart. This explains
the otherwise contradictory first replay and is a required deployment step for
future template work.

## Real-server result

The comparable B2 baseline used the same four-different-tool Agent shape with
`11,081` input tokens. Every request reused zero tokens and took
`7.554/7.511/7.486/7.508 s`.

With the delivered template and opt-in, the four-request replay took:

| Thread | Input | Cached | Reuse | Wall | Correct tool |
|---|---:|---:|---:|---:|---|
| main-like 0 | 9,888 | 0 | 0% | 7.674 s | yes |
| sibling 1 | 9,888 | 5,120 | 51.78% | 5.019 s | yes |
| sibling 2 | 9,888 | 5,120 | 51.78% | 4.996 s | yes |
| sibling 3 | 9,888 | 5,120 | 51.78% | 4.986 s | yes |

The first two comparable requests improved from `15.065 s` total to
`12.693 s` (`15.74%`). This exceeds the 1% primary gate.

Four simultaneous warm-prefix requests all reused `5,120` tokens and returned
their own tools. Their walls were `6.112/19.975/11.100/15.675 s`; unlike the
sequential replay these include six-slot admission and batch waiting, so they
are a safety result, not a throughput comparison.

An eight-request safety run completed all requests with the expected own tool.
Seven reused `8,192` tokens; one cold request completed normally. Maximum wall
was `20.147 s`, consistent with six-slot overload. No request failed.

## Preserved paths

Four interleaved main/child turns followed by compactions still reused
`8,130/8,138/8,138/8,138` tokens, or `99.50%`. A compaction allowed 256 output
tokens also completed normally at `99.48%` reuse. Title generation stayed on the
local path: `2.0 ms`, all token counters zero, and all metrics deltas zero.

Health remained loaded at `262144` context. The engine stayed at six slots,
two groups and the original SHA-256
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`.
There were no HTTP, execution, CUDA, timeout, traceback or foreign-request
errors.

## Running shape

| Item | Value |
|---|---|
| Host UTC ready | `2026-10-31T05:07:56Z` |
| Server / vision / engine | `36610 / 36613 / 36618` |
| API port | `127.0.0.1:8080` |
| Health | loaded, context `262144` |
| Shape | 6 slots, 2 groups, 6 x 256K |
| Engine SHA-256 | `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3` |
| Config SHA-256 | `a8a8a42f8c886ee508e20032c337c841a473eae57b127239f714d2fef6698b36` |
| Template SHA-256 | `8f0ac93c1fd73ccf71c8ba62a55eae08b8383d4553785ad3c4f99266c0ba5f0e` |

The candidate did not rebuild the engine. The Python server reload took about
`151 s`; after the pack-template correction it took about `138 s`.

## Limits

- Reuse stops at a `5,120`-token checkpoint, not at the full shared block.
- Truly simultaneous cold first requests cannot reuse the first read.
- Four simultaneous requests still queue behind the six-slot engine; the
  accepted claim is sibling-prefix reuse and lower critical-path prefill, not a
  new aggregate peak.
- The comparable B2 baseline was captured immediately before deployment; a
  second multi-arm engine reboot was not spent because the engine and prompt
  length were unchanged and the real path saved seconds per sibling request.

## Evidence

- `/opt/ai/logs/agent-static-system-prefix-20261031T0500Z/`
- `b3a-sequential-replay.json`
- `b3a-parallel4.json`
- `b3a-safety8.json`
- `b3a-compaction-replay.json`
- `b3a-compaction-complete.json`
- `b3a-title.json`
- `frontend-unit.log`
- `responses-unit.log`
- `final-metrics.json`
- `original/` and `original-sha256.txt`

## Rollback

Remove `codex_system_before_tools` or set it to false, restore
`original/pack-chat_template.jinja`, and restart the Python server. The engine
does not need a rebuild.
