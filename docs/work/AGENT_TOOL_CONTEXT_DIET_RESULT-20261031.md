# AGENT-TOOL-CONTEXT-DIET result

Date: 2026-10-31

Status: complete. Adopt eager deterministic references for the opt-in Codex
proxy pilot. Reject the delayed "keep newest six, fold older output" policy.

Contract:
[`AGENT_TOOL_CONTEXT_DIET_CONTRACT-20261031.md`](AGENT_TOOL_CONTEXT_DIET_CONTRACT-20261031.md)

## Decision

The original delayed policy was counterproductive on a hot prefix. Folding one
older tool output on each request rewrote early history and invalidated the
cached suffix. Cached input fell to `4,546` tokens and the first nine request
sum increased from the `38.106 s` control to `116.177 s`.

The replacement policy folds every eligible successful output on its first
appearance. Prior references then remain byte-identical while later turns
append new context. On the same eight-command shape, three paired waves
completed with these client wall times:

| Pair | Control | Eager references | Reduction |
|---:|---:|---:|---:|
| 1 | 38.106 s | 32.914 s | 13.63% |
| 2 | 58.307 s | 30.727 s | 47.30% |
| 3 | 41.002 s | 32.686 s | 20.28% |

Every run completed normally. The first eager run also performed an additional
tool turn to search the saved reference and still finished 13.63% sooner.

## What was implemented

`tools/server/agent-shape-proxy.py` now supports an opt-in context diet:

- `--context-diet-keep-recent 0` folds all eligible successful outputs on first
  appearance; a positive value retains that many newest outputs verbatim;
- every saved artifact is unchanged, mode-0600, under a mode-0700 run
  directory;
- the model receives a stable reference with byte count, SHA-256, absolute path,
  and exact `sed`/`rg` reread commands;
- small outputs, detected errors, failures, panics, tracebacks, denials and
  timeouts remain verbatim;
- no model summary is generated and ordinary direct-server traffic is
  unchanged;
- proxy forwarding now replaces `Content-Length` case-insensitively. The first
  live attempt exposed a duplicate-header hang when the transformed body
  changed length; a regression test now covers the failure.

The proxy tests pass `13` tests. The wire-body round trip, artifact bytes,
permissions, stable references, symlink refusal and eager-prefix stability are
covered.

## Eight-command evidence

Each request generated eight separate `1,400`-line outputs. Seven outputs were
`8,505` bytes including the command terminator and the first was `6,015` bytes.

With eager references:

- request 2 prompt input fell from the control's `15,320` to `9,788` tokens;
- request 9 input reached only `13,159` tokens versus control `26,419`;
- cached input remained `9,400-13,513` tokens after folding began;
- eight saved outputs removed `60,694` characters from the model-visible
  history;
- the model recovered `NEEDLE=lyra-8153` from the artifact's exact `rg` command
  and returned `READY`;
- a separate task changed `calc.py` from subtraction to addition, ran
  `python -m pytest -q`, obtained `2 passed`, and returned `PASS`.

The one earlier code-quality attempt produced a malformed textual
`apply_patch` call and made no change. It was rerun with an explicit
`exec_command` edit and test path; this is a model tool-format limitation, not
a context-diet data-loss finding.

## Privacy and production state

Shape logs contain canonical hashes, item kinds, lengths, usage, status and
timing only. They do not contain prompts, assistant messages, reasoning or tool
body text. Full tool artifacts are temporary mode-0600 user data outside the
repository. No artifact was uploaded or committed.

The B4 server, vision and engine processes stayed at `50648/50651/50656`; no
production service or model was restarted and no server configuration changed.
The candidate remains opt-in because content-loss risk and false-negative error
classification are workload-dependent.

## Evidence

- `/tmp/agent-control-shape-20261031.jsonl`
  SHA-256 `074b7140605ec47c466170cce15f6c7e7bbf2e729b468551d654cd516922a96f`
- `/tmp/agent-diet-eager-shape-20261031.jsonl`
  SHA-256 `c8517cd63d425d3c1d881d8acf76d01eeaa8cc4630c1a8f806928a15338f53ab`
- `/tmp/agent-diet-fixed-shape-20261031.jsonl`
  SHA-256 `1cad80f66e378c61e40838f295fdccfd9ea2a9b71bba218547f9a5c60490f576`
- `/tmp/agent-control-waves-shape-20261031.jsonl`
  SHA-256 `01be4a95e1e8392ff013f734d292055225e14dcc271c10d0bcd52d79bb5ab940`
- `/tmp/agent-diet-eager-waves-shape-20261031.jsonl`
  SHA-256 `3ed6341a24c1b95facdd44ae29c62f386c9bbde62b3a639b8160f0d118094e63`
- `/tmp/strata-agent-diet-eager-20261031/artifacts/`
- `/tmp/strata-agent-diet-eager-waves-20261031/artifacts/`

## Limits and next item

This is one model and one synthetic command shape repeated three times, not a
cross-model production replay. It does not measure mixed concurrent sessions,
streaming TTFT or non-string/custom tool output coverage. The candidate should
remain opt-in until a real user coding session passes the same edit/test gate.

The next measured head is model decode. The 35-file / 152-root trace contains
`10,294,789` output and `3,337,375` reasoning tokens, with model union
`68,957.043 s` against root wall union `184,604 s`. The next item is
`AGENT-REAL-DECODE-SHAPE`: map low-concurrency decode cost and only implement a
mechanism with at least a 1% whole-task bound and quality preservation.
