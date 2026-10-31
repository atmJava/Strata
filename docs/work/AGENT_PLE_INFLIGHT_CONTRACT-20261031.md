# AGENT-PLE-INFLIGHT contract

Date: 2026-10-31

Status: completed and adopted as an explicit configuration argument. See
[`AGENT_PLE_INFLIGHT_RESULT-20261031.md`](AGENT_PLE_INFLIGHT_RESULT-20261031.md).

## Why this candidate

The real aged-session profile used the default PLE direct reader with
`max_inflight=256`. Its prompt took `135,073 ms`, and host PLE wait was
`50,362 ms`. The existing `--ple-io mmap` arm was rejected because it raised
wall to `182.157 s` and PLE wait to `90,955 ms`.

A no-restart benchmark of the existing reader on the production PLE shard used
`20,000` tokens (`320,000` rows), no row cache and the same random rows in
three repetitions per queue depth:

| inflight | median batch time | us/token | read p50 | read p99 |
|---:|---:|---:|---:|---:|
| 256 | 15,737.4 ms | 786.9 | 3,590 us | 7,954 us |
| 512 | 15,365.4 ms | 768.3 | 7,297 us | 14,323 us |
| 1,024 | 13,917.7 ms | 695.9 | 15,285 us | 26,161 us |

`1,024` is `11.56%` faster than `256` at the batch level. Individual read
latency rises because more requests are in flight, while total throughput
improves. The direct-read slab grows from about 2 MiB to 8 MiB, which is small
relative to the 64 GiB host.

## Candidate

Add only this engine argument to the current profile arm:

```text
--ple-inflight 1024
```

Keep the cached version's default at 256. Keep the source, engine, model, six
slots, two groups, context, MTP settings, `--ple-io direct`, stored session and
`STRATA_PREFILL_TIMING=1` fixed.

## First screen

Run one cold aged-session resume in a fresh process. Continue only if:

- the sentinel response, `finish=stop`, zero tools and zero errors are preserved;
- prompt time improves by at least 1% against the direct-256 profile arm;
- host PLE wait improves by at least 1.13 s and moves in the same direction as
  the end-to-end time;
- health, six slots, two groups, RAM and VRAM remain safe.

If this screen fails, close queue-depth tuning. If it passes, use a profiler
arm with `256` again as A2 to measure cross-boot drift before an unprofiled
MMAP-style adoption comparison.

## Adoption gate

The final unprofiled 1,024 arm is adopted only if:

- direct-256 A1/A2 drift is at most 3% for prompt time;
- the 1,024 arm reduces aged-session prompt/wall by at least 1% against the
  adjacent direct-256 result;
- the result is consistent with the PLE phase reduction;
- output sentinel, finish, tool behaviour and health are unchanged;
- swap does not grow, minimum available RAM remains safe and no request or
  engine error occurs;
- rollback is one argument and the cached/default value remains 256.

## Stop rules

Restore exact B2 on load failure, health or six-slot loss, response/finish
change, tool execution, memory pressure, swap growth or an engine error.

Do not combine this with mmap, PLE cache size, keep-alive, context size,
prefill chunk size or kernel changes. Do not scan 768, 2,048 or adjacent queue
depths. The next step after this decision must come from a new observation.
