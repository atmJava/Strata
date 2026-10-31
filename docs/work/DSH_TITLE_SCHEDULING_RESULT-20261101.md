# DSH title scheduling: end-of-turn, caps and prefix pin rejected

Date: 2026-11-01

Status: rejected. No candidate code remains in the working tree. Production was
not restarted or changed.

## Decision

The hidden DSH title request belongs to the active turn. DSH cancels it when the
main turn ends. Deferring the title until the terminal assistant response is
therefore too late even if the HTTP connection is kept alive.

Three follow-up attempts were also rejected:

- cap the exact title request at 128 output tokens;
- cap it at 64 output tokens;
- pin its fixed first-message prefix.

None removed the title from later main steps in a way that demonstrated a
conservative task-wall gain. Further waiting, cap or prompt-neighbour tuning is
closed.

## End-of-turn gate

The local state machine and HTTP ordering tests passed. In the real strict
`single-file` run:

- task wall: `9.598 s`;
- runner wall: `11.628 s` (includes client-side post-turn work);
- code, repository tests, protected tests and strict final sentinel passed;
- five ordinary model steps completed with HTTP 200;
- title deferred `9.421 s`;
- after release, the title client had disconnected;
- proxy recorded `BrokenPipeError` and HTTP 502.

Early SSE headers and valid chunked heartbeats were tested to rule out a broken
transfer framing bug. They did not extend DSH's logical title lifetime: the
client was cancelled at turn end. Holding the final main response until title
completion would serialize the title and add approximately the standalone title
latency, so it cannot justify a 1% speed claim.

## Output caps

| arm | title defer | title duration | title output | title status | task wall |
|---|---:|---:|---:|---|---:|
| cap 128 | `2.462 s` | `4.679 s` | 89 tokens | provider completed | `12.004 s` |
| cap 64 | `2.301 s` | `4.324 s` | 56 tokens | provider completed | `11.490 s` |

The cap reduced title tokens but not enough to keep the title inside one
inter-step gap. The title still overlapped at least two later main steps. The
single-arm task walls are not used as performance proof because model/tool work
differed; the mechanism fails before that comparison.

The corresponding runner-process walls were `13.942 s` and `13.541 s`. They are
recorded separately because they include client-side work after the measured
turn and are not the primary task metric.

## Prefix pin

The exact title request reports `input_tokens=285` and `cached_tokens=0` on each
new session. An explicit `strata_prefix: {"messages": 1}` was accepted, but two
consecutive strict runs still reported `cached_tokens=0`:

| run | title duration | output | cached | task wall |
|---|---:|---:|---:|---:|
| D1 | `4.401 s` | 115 | 0 | `14.161 s` |
| D2 | `4.674 s` | 135 | 0 | `12.221 s` |

The two title inputs had identical content hashes, so this is not a
different-prompt artifact. The direction has no observed cache benefit and is
closed.

## Test-method correction

Auxiliary-request experiments must record:

1. request arrival;
2. gate release;
3. upstream start;
4. upstream finish;
5. main turn end;
6. client cancellation;
7. provider status, output tokens and cached tokens;
8. overlap against every main model step.

Task wall alone is insufficient. It can look normal while the auxiliary request
is cancelled or while it still overlaps multiple main steps.

## Evidence

Local artifacts:

- `/tmp/dsh-title-end-b2r-20261101/report.json`
- `/tmp/dsh-title-end-b2-proxy-20261101.jsonl`
- `/tmp/dsh-title-cap128-c1-20261101/report.json`
- `/tmp/dsh-title-cap128-proxy-20261101.jsonl`
- `/tmp/dsh-title-cap64-c1-20261101/report.json`
- `/tmp/dsh-title-cap64-proxy-20261101.jsonl`
- `/tmp/dsh-title-prefix-d1-20261101/report.json`
- `/tmp/dsh-title-prefix-d2-20261101/report.json`
- `/tmp/dsh-title-prefix-proxy-20261101.jsonl`

All runs used the same strict final-message suffix and the real DSH
`single-file` fixture. No credential, prompt body, reasoning text or tool body
was written to repository artifacts.
