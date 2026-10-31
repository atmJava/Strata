# AGENT-PLE-ROW-CACHE contract

Date: 2026-10-31

Status: rejected by the profile-selection gate. See
[`AGENT_PLE_ROW_CACHE_RESULT-20261031.md`](AGENT_PLE_ROW_CACHE_RESULT-20261031.md).

## Why this candidate

The accepted direct-reader configuration uses `--ple-inflight 1024`. Its
profile arm still recorded `43,453 ms` of host PLE wait and an `18,525 ms` PLE
phase in the dominant chunk. PLE I/O remains a large removable part of the
223K cold prefill.

The source default is a 1,048,576-row set-associative cache:

```text
src/program/generate.cpp:463  ple_row_cache = 1 << 20
```

Each IQ4_NL row is 90 bytes. The default therefore stores about 95 MB including
keys and replacement state. The header reports that 20-34% of rows recur within
one long prompt on the frozen corpus. The real aged session has not yet been
measured with a larger cache, so the expected gain is directional rather than
assumed.

Candidate size is exactly 4,194,304 rows. The incremental allocation is about
270 MiB, raising the row cache to roughly 360 MiB. Server RAM had more than
13 GiB available in the adoption test, so this fits without touching swap.

## Fixed base

Start from the adopted 1,024-inflight candidate configuration. The candidate
adds only:

```text
--ple-row-cache 4194304
```

Keep the engine binary, server source, model, stored session, six slots, two
groups, context, MTP, sampling, direct I/O, keep-alive and all other settings
unchanged. Keep `STRATA_PREFILL_TIMING` off in the final performance arms.

## Selection gate

First profile the exact adopted base and the row-cache candidate in adjacent
fresh boots. Continue only if the candidate:

- preserves the sentinel response, `finish=stop`, zero tools and zero errors;
- improves server prompt time by at least 1%;
- reduces host PLE wait by at least 1.13 seconds in the same direction;
- keeps health, six slots, two groups, swap and minimum available RAM safe.

If the profile screen passes, run at least two adjacent unprofiled base/
candidate pairs. Adopt only if:

- every pair moves prompt time in the candidate's favor;
- the median paired server-prompt improvement is at least 1%;
- the median paired client-wall improvement is positive and at least one pair
  clears 1%;
- output sentinel, finish, tool count, health and resources are unchanged;
- rollback is one argument and the source default remains 1,048,576 rows.

If the screen fails, restore the adopted 1,024-inflight configuration and close
row-cache sizing without scanning nearby sizes.

## Stop rules

Restore the adopted base on load failure, six-slot loss, swap growth, memory
pressure, response change, finish change, tool execution or an engine error.

Do not combine this with mmap, keep-alive, inflight changes, context changes,
another cache capacity, prefill chunk changes or kernel edits. A miss closes
row-cache sizing rather than inviting a parameter sweep.
