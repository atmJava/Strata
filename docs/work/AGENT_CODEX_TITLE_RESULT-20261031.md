# AGENT-CODEX-TITLE result

Date: 2026-10-31

Status: accepted and left running.

## Decision

The existing opt-in `codex_thread_titles: true` removes Codex thread-title work
from the engine. Three requests with the same long Codex instruction prefix
returned valid titles in `1.8–2.1 ms` each. The server totals for finished
engine requests, prompt tokens and output tokens did not change. A following
ordinary Responses request still reached the engine.

This is an accepted configuration change, not an engine-source change.

## Running shape

| Item | Value |
|---|---|
| Server / engine / vision | `31228 / 31235 / 31230` |
| Health | `loaded: true`, context `262144` |
| Engine | `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3` |
| A config | `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f` |
| B config | `36dcd9088e4e1effda66bf59fb8cea7d16f755bfe1fbc4b71ad82ec8a9b649f4` |
| Shape | 6 slots, 2 groups, 6 x 256K |
| Candidate-only difference | `codex_thread_titles: null -> true` |

The server was ready after about 176 seconds. The A and B engine binaries are
byte-identical; only the server configuration differs.

## Gates

Before the reload:

- the focused Codex title tests passed;
- an ordinary Responses request reached the engine;
- a request without the title metadata reached the engine.

After the reload:

1. Three title requests returned HTTP 200 and non-empty titles:
   `Implement retry logic for remote tun`,
   `修复工具调用超时并保持会话缓存`, and
   `Profile long context prefill reuse`.
2. Their wall times were `2.1`, `2.0` and `1.8 ms`.
3. Each reported zero input, cached, output, reasoning and total tokens.
4. Server history stayed at zero and every finished-request total stayed
   `0`: requests, prompt tokens, reused tokens, output tokens, prompt time,
   decode time and draft counters.
5. One ordinary `/v1/responses` request then completed in `1.048 s`, added one
   finished request, read 64 prompt tokens and produced 8 output tokens.
6. Health remained loaded, all three processes remained alive, and the console
   had no HTTP, execution, CUDA or timeout failure.

Compared with the measured A2 title requests (`1.978–10.292 s`, 146–184 output
tokens), the title-path saving was `1.976–10.290 s` per request. It also
avoided competition with a real conversation's prefix cache and decode slots.
No aggregate throughput claim is made from this one-time event.

## Evidence

- `/opt/ai/logs/agent-codex-title-20261031T0448Z/a-config.json`
- `/opt/ai/logs/agent-codex-title-20261031T0448Z/b-config.json`
- `/opt/ai/logs/agent-codex-title-20261031T0448Z/b-server-console.log`
- `/opt/ai/logs/agent-codex-title-20261031T0448Z/engine-sha256.txt`

## Rollback

Stop the B server, restore `a-config.json`, start the A configuration, and
verify health, the engine/config hashes and 6-slot/2-group shape. The candidate
only changes the configuration key, so no engine rebuild is needed.

## Limit

This removes a real but event-shaped latency source. It does not make normal
generation faster. The next candidate must address a repeated cost on a
conversation's main path.
