# AGENT-CODEX-COMPACTION-LRU contract

Date: 2026-10-31

Status: accepted. Result:
[`AGENT_CODEX_COMPACTION_LRU_RESULT-20261031.md`](AGENT_CODEX_COMPACTION_LRU_RESULT-20261031.md).

## Problem

The opt-in `codex_compaction_cache` renders a Codex compaction with the tools
from that conversation's previous prompt. This keeps the compaction prompt's
tool prefix aligned with the cached conversation instead of forcing the engine
to reread the whole, longest prompt.

The current cache stores one global `(session_id, thread_id)` entry. A user
normally has one main thread and up to three sub-agents. Their requests
interleave, and each may have a different tool set. When another thread sends a
turn before one thread compacts, the original entry is replaced. That thread's
compaction then starts without its own tools and loses the reusable prefix.

This is static source evidence, not a measured production frequency. The
candidate must first prove the prompt-prefix behavior locally; it must not claim
a throughput gain from the one-entry assumption alone.

## Candidate

In `serve/responses.py`, replace the single global tuple with a bounded
thread-safe LRU keyed by `(session_id, thread_id)`:

- capacity 12, matching the configured conversation-cache slot count and
  comfortably covering one main plus three sub-agent threads;
- a lock protects lookup, move-to-recent and insertion/eviction;
- each value is the exact JSON text previously used to render that
  conversation's tools; `None` is recorded when its last prompt had no tools;
- reads return a fresh decoded list, so callers cannot mutate the cache;
- compaction reads move the entry to most-recent without changing its value;
- non-compaction turns insert or replace their own conversation's value;
- thread-title requests, invalid metadata and requests without both IDs do not
  read or write the cache;
- eviction removes the least-recently-used conversation, never a partial value.

The public behavior remains behind the existing opt-in
`codex_compaction_cache: true`. When the option is off, the server never calls
these functions; the default request and prompt path remain unchanged.

## Gates

Before any server reload:

1. Existing Responses tests pass.
2. A turn followed by its own compaction renders a compaction prompt beginning
   with the turn's rendered prompt.
3. Three interleaved child threads plus one main thread each compact with their
   own tools; no thread receives another thread's tools.
4. Repeated concurrent reads/writes for four conversations complete without
   error or cache corruption.
5. Capacity is enforced as LRU: an accessed old entry survives one unrelated
   insertion, while the least-recently-used entry is evicted.
6. A title request, malformed metadata, an incomplete conversation key and a
   no-tools turn preserve the documented cache semantics.
7. The default-off server path produces the same prompt as before the change.
8. Local UTF-8/BOM, `git diff --check` and offline preflight pass.

If these gates pass, the server candidate may be tested with both
`codex_compaction_cache: true` and `codex_thread_titles: true`. A server reload
is justified only after the local tests prove a real prefix mismatch in the old
one-entry behavior and a matching prefix in the LRU behavior.

## Rollback

Revert this source change and use the accepted title-only B configuration.
The engine binary and model files do not change.

## Limits

This cache retains only tool JSON, not model output, prompts or secrets. Its
capacity bounds memory. It does not replace the conversation cache or change
tokenization, sampling, finish semantics or compaction output.
