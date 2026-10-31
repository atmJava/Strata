# AGENT-OLD-SESSION-RESUME contract

Date: 2026-10-31

Status: complete. Result:
[`AGENT_OLD_SESSION_RESUME_RESULT-20261031.md`](AGENT_OLD_SESSION_RESUME_RESULT-20261031.md).

## Why now

Draft timing is closed: its removable host portion is negligible and even a
full draft-head removal is below the one-percent root-task bound. The next
user-facing tail is a conversation resumed after a long idle period with a
large history. This is distinct from compaction and from re-reading an old
branch immediately after compaction.

The local, content-free scan found 35 Codex sessions. No local request pair had
a gap above 24 hours, so the existing traces do not contain a real aged-resume
event. A stored session from 2026-09-12 now provides a greater-than-one-day gap
without inventing a prompt: its recorded first request context was about 191K
tokens, close to the user's described 180K class.

## Request

Resume that stored session through the local HTTP tunnel to B4:

- `codex exec resume <session-id> ... --ephemeral`;
- override the provider base URL to the tunnel and the model to the current B4
  model;
- sandbox read-only and no tools;
- prompt asks only for the literal response `RESUMED`;
- capture stdout/stderr to files, not the terminal;
- do not print or commit prompt, assistant, reasoning or tool content.

This creates a real replay of the stored conversation shape, not a synthetic
filled context. The session is old enough that B4 has no cache for it.

## Measurements

Record:

- request count, input, cached and uncached tokens;
- prompt/decode/TTFT/wall/slot wait;
- finish and output hash, not output text;
- health, six slots, GPU memory and host RAM;
- the time between the resumed turn's start and first useful token.

If the request fails before model execution, record the exact stage and do not
substitute a synthetic 180K turn.

## Decision rule

This is a p90/p99 diagnostic, not a claim that every task is improved. A
mechanism is eligible only if it:

1. reduces the resumed turn's user-visible wall or first executable result by
   at least one second, or moves the p99 failure mode below its pre-registered
   bound;
2. preserves the full old history and normal tool/task correctness;
3. is opt-in, bounded in RAM/VRAM, and has an exact rollback;
4. costs less than the restart and cache loss it introduces.

If the aged replay is close to a normal cold 180K prompt and no additional
stall appears, the user-facing tail is cold prefill itself; do not build a
special pinned-history heuristic on the assumption that old sessions recover
cheaply.

## Stop rules

Stop on HTTP/engine error, context overflow, missing authentication, any tool
execution, or output other than the requested sentinel. Do not combine this
run with MTP, batching, kernel or compaction changes.
