# AGENT-OLD-SESSION-RESUME result

Date: 2026-10-31

Status: diagnostic complete. A real Codex session idle for 49 days resumed
through the B4 HTTP path and spent essentially all of its 113.3 s completion
time in cold prefill.

Contract:
[`AGENT_OLD_SESSION_RESUME_CONTRACT-20261031.md`](AGENT_OLD_SESSION_RESUME_CONTRACT-20261031.md)

## Why the local traces were insufficient

The privacy-safe scan covered 35 local Codex sessions. None contained a request
pair separated by more than 24 hours, so the existing end-to-end trace could
not measure an aged-session resume. A synthetic cold 180K prompt was only a
size proxy; it did not preserve a real stored conversation.

## Replayed source

The replay used this stored session without modifying its history:

```text
session 01a09434-68ad-78f0-bb2f-a9b4663038e8
origin 2026-09-12T06:00:57.774Z
original cwd /home/kali/项目/st
original client 0.153.4
age about 49 days on 2026-10-31
```

It resumed the session through `codex exec resume` and the existing
`127.0.0.1:18080` tunnel to B4. The resume request was read-only, used no
tools, and requested only the literal sentinel `RESUMED`.

Prompt, assistant, reasoning and tool bodies were written to private local
artifacts rather than the terminal or repository. The completed assistant item
was seven characters and matched the sentinel.

## Result

| Field | Value |
|---|---:|
| Server request id | `4ec7f4d8c2f8` |
| Prompt / input tokens | 223,114 |
| Cached tokens | 0 |
| Uncached tokens | 223,114 |
| Prompt / first-token time | about 112.863 / 113.014 s |
| Client completion wall | 113.3 s |
| Decode time | 292.8 ms |
| Output tokens | 21 |
| Finish | `stop` |
| Tool executions | 0 |

The resumed request preserved the stored old history and returned the expected
sentinel. There was no additional recovery stall to explain: approximately
112.9 of 113.3 seconds were cold prompt processing and cache population.

This is a p90/p99 user-facing tail, not a claim that every request or task
loses two minutes. The CLI's cumulative usage field was not used as the
per-request token count; the server-side request accounting is the source for
the 223,114 prompt tokens and zero cache reuse.

## Decision

Close `AGENT-OLD-SESSION-RESUME` as a successful diagnostic. The old-session
problem is real and is now covered by a genuine aged replay rather than a
synthetic proxy. The next item is
`AGENT-OLD-SESSION-PREFILL-PROFILE`: obtain a phase map of this same request
class before implementing persistence, prefetch or kernel changes.

Do not combine this result with:

- 210K compaction, which reuses almost the entire prefix;
- an immediately re-read branch after compaction, which occurred in 0/109
  real compactions;
- a freshly constructed cold 180K prompt, which is not a stored aged session.

## Production and safety

This replay did not change model, engine, server source, config, sampling,
slots, groups, MTP settings or the production default. The B4 process remained
loaded with six slots and two groups.

## Raw evidence

- `/tmp/agent-old-session-resume-20261031.jsonl`
  SHA-256 `43c10d42b38244a9669a54f7275d5fd3bec6c74bf965f06efc804d181b96b9c4`
- `/tmp/agent-old-session-resume-20261031.err`
  SHA-256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- stored session metadata identifies the 2026-09-12 timestamp and
  `/home/kali/项目/st`; raw session content is not committed.

## Not measured

Only one aged session was replayed. Its 49-day age and 223K prompt establish a
real p99 tail and a valid prefill workload, but not the population frequency
of that tail. The cache is populated after the first replay, so a second run
in the same process is not another cold sample.
