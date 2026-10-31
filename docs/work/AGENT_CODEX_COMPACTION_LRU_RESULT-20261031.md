# AGENT-CODEX-COMPACTION-LRU result

Date: 2026-10-31

Status: accepted and left running.

## Decision

The opt-in Codex compaction tool cache now keeps a thread-safe, 12-entry LRU
keyed by `(session_id, thread_id)`. In the real server path, a main thread and
three child threads retained their own tool prefixes across interleaved turns.
All four following compactions reused about 99.5% of their roughly 8.3K-token
prompts and completed in `0.69–0.89 s`.

With the previous one-entry cache, only the last child's tools survived. The
main thread and the first two children compacted without their own tools, so
their prompt did not begin with the rendered turn prompt and the cached
conversation could not be restored.

## Implementation

- `_kept_prompt_tools` is an `OrderedDict` keyed by conversation.
- A lock protects lookup, move-to-recent, replacement and eviction.
- Capacity is 12, matching the configured conversation-cache slot count.
- A stored `None` preserves the rule that a later no-tools turn replaces an
  earlier tool set for that conversation.
- Compaction reads return a fresh decoded list.
- Thread-title requests, invalid metadata and requests without both IDs do not
  touch the cache.
- The feature remains behind existing `codex_compaction_cache: true`; its
  default-off path is unchanged.

## Running shape

| Item | Value |
|---|---|
| Server / engine / vision | `33065 / 33073 / 33068` |
| Health | `loaded: true`, context `262144` |
| Engine | `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3` |
| Config | `b54fd35e166dd7feb35a30eff3767a5d05f8ad11a8792d5095f65a73b94f0d3f` |
| Candidate server file | `c88b171e2a76897cccf9ccdadc90d257dca0fb461e3d84e365542270ecf18dc8` |
| Shape | 6 slots, 2 groups, 6 x 256K |
| Options | `codex_thread_titles: true`, `codex_compaction_cache: true` |

B2 loaded in about 145 seconds. The engine binary did not change.

## Correctness and path evidence

The local and remote Responses suites both passed 45 tests; one schema test was
skipped on the remote environment because `jsonschema` was unavailable. The
candidate-specific tests covered:

- a main thread plus three children with distinct tool sets and matching prompt
  prefixes;
- a counterexample showing the old missing-tools compaction prompt does not
  begin with the conversation's rendered prompt;
- 1,600 concurrent cache operations across four threads;
- access-sensitive LRU eviction at capacity;
- malformed metadata, incomplete IDs and no-tools replacement;
- title turns not replacing another thread's entry;
- the default-off request path.

The real server replay then sent four cold turns followed by their compactions:

| Thread | Compaction prompt | Cached tokens | Reuse | Wall |
|---|---:|---:|---:|---:|
| main | 8,314 | 8,274 | 99.52% | 0.888 s |
| child-1 | 8,318 | 8,278 | 99.52% | 0.807 s |
| child-2 | 8,318 | 8,288 | 99.64% | 0.760 s |
| child-3 | 8,318 | 8,288 | 99.64% | 0.694 s |

All eight requests were HTTP 200. The finished-request and history deltas were
both 8. The conversation cache reported four restores, zero evictions and three
parked conversations. A title request in B2 still completed locally in
`3.8 ms` with zero engine request/token delta.

Health remained loaded, both GPUs were idle after the run, swap use remained
zero, and no HTTP, execution, CUDA, timeout or foreign-request failure was
observed. Available RAM settled around `11 GiB`, with about `856/958 MiB` free
on the two GPUs.

## Impact

This removes a full long-context reread from each non-last thread's compaction
when main and child work interleaves. Even at the tested 8.3K-token context,
the cold reads took `6.85–7.67 s` while the compactions took `0.69–0.89 s`.
At the user's larger histories the avoided reread grows with context.

No aggregate 1/2/4-request generation claim is made: this path only runs when a
Codex conversation compacts.

## Evidence

- `/opt/ai/logs/agent-codex-compaction-lru-20261031T0448Z/b2-replay.json`
- `/opt/ai/logs/agent-codex-compaction-lru-20261031T0448Z/full-responses.log`
- `/opt/ai/logs/agent-codex-compaction-lru-20261031T0448Z/local-unit.log`
- `/opt/ai/logs/agent-codex-compaction-lru-20261031T0448Z/original-sha256.txt`
- `/opt/ai/logs/agent-codex-compaction-lru-20261031T0448Z/candidate-sha256.txt`
- `/opt/ai/logs/agent-codex-compaction-lru-20261031T0448Z/b2-server-console.log`

## Rollback

Restore the two files under `original/`, disable `codex_compaction_cache`, and
restart the Python server. The engine does not need a rebuild.
