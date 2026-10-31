# AGENT-TOOL-CONTEXT-DIET contract

Date: 2026-10-31

Status: complete. Result:
[`AGENT_TOOL_CONTEXT_DIET_RESULT-20261031.md`](AGENT_TOOL_CONTEXT_DIET_RESULT-20261031.md).

## Problem

Across 26 local Codex rollouts, tool output is `79,745,864` characters. Outputs
of at least 10K characters are `63.985%` of that total; outputs from 2K to
10K add another `28.309%`. Large command logs therefore dominate the new
context introduced into a long coding task, even though most old log lines are
no longer needed for the next edit or test.

The read-only policy "keep the newest six outputs per conversation, keep every
error, and fold older successful outputs of at least 4K characters" selects
4,627 outputs and 59,653,543 characters. That is `74.79%` of tool output.
Using a conservative four characters per token gives at least 14.9M tokens.
At the measured B4 prompt order of roughly 1,000 token/s this is about 14,913
seconds, or an upper bound of `8.1%` of the 184,128-second root wall union.

This is a projection from client content and measured prompt rate, not a
server measurement of a transformed workload. A live replay must establish the
real saving.

## Candidate, revised after the first live rejection

The initial policy retained the newest six outputs and folded older successful
outputs. The live run showed that aging out one earlier output per turn changes
an early message on every request. That destroys the cached suffix, which costs
more than the removed text. It is rejected.

Implement an opt-in local Responses proxy for Codex:

- default off;
- before changing a request, write each candidate's full output unchanged to a
  mode-0600 file in a mode-0700 directory;
- replace each successful string tool output of at least 4K characters on its
  first appearance, before an aging rule can rewrite the same prefix again;
- keep every output classified as an error, failure, panic, traceback, denial,
  timeout or non-zero exit, and keep small successful outputs verbatim;
- keep the original item type, role and `call_id`;
- replace the output body with a short deterministic reference containing the
  exact byte count, SHA-256, absolute path and `sed`/`rg` reread commands;
- never call a model to summarize, never truncate the saved artifact and never
  silently affect ordinary direct-server traffic;
- bind the output directory to one run and refuse symlink or path traversal.

The proxy may reuse the privacy-safe parsing and forwarding helpers from
`tools/server/agent-shape-proxy.py`. Shape logging remains hash-only; full
outputs exist only under the explicitly selected local artifact directory.

## Quality gates

Before adoption:

1. Unit tests cover eager-prefix stability, optional recent-output retention,
   error retention, exact artifact round-trip, stable references, symlink
   refusal, default-off byte identity, case-insensitive Content-Length and
   malformed-output fallback.
2. A scripted real B4 task creates more than six large tool outputs, then asks
   for a fact from an early output, edits a file and runs the relevant tests.
3. Control and diet runs use the same prompt, model, sampling, concurrency and
   fixed output limits.
4. The diet run must recover the early fact through its reference file, finish
   normally, make the expected code change and pass the same tests.
5. No HTTP or execution errors; no unexpected finish; no lost stderr or
   non-zero exit evidence.
6. At least three paired waves report request prompt tokens, cached/uncached
   tokens, prompt time, wall time, tool round trips and task success.

## Adoption rule

Adopt only if a real-shape replay demonstrates at least `1%` whole-task wall
improvement after including reread commands and any extra model turns. A
candidate that only lowers token counts while making the task slower or less
reliable is rejected.

The candidate remains opt-in even after adoption. Default server requests stay
byte-identical. The artifact directory is temporary user data and is never
committed or uploaded.

## Stop rules

Stop before a broad replay if:

- most eligible output cannot be represented as a string tool output;
- old output is needed by the next model turn and folding predictably adds more
  wall time than it saves;
- the proxy cannot retain errors without a high false-negative rate;
- the full artifact cannot be made mode-0600 without revealing it in logs;
- the live request count is too low for a 1% whole-task claim.

Do not repeat context-size scans, add approximate in-request summaries, or hide
the transformation when the client did not opt in.

## Outcome

The delayed policy failed its first live run: cached input stayed near `4.5K`
and request time rose sharply. Eager first-appearance folding kept the prefix
stable, passed search and edit/test quality gates, and improved all three
paired walls by `13.63%`, `47.30%` and `20.28%`. It remains opt-in because the
evidence is one model and one synthetic workload shape.

No server restart or production configuration change was needed.
