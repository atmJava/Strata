# AGENT-COMPACTION-REAL-SHAPE result

Date: 2026-10-31

Status: complete. The current Codex compaction cache path reproduced high reuse on
three real requests. No server implementation candidate was justified.

Contract:
[`AGENT_COMPACTION_REAL_SHAPE_CONTRACT-20261031.md`](AGENT_COMPACTION_REAL_SHAPE_CONTRACT-20261031.md)

## What invalidated the original attribution

The frozen 29-hour rollout has 44 compactions with low cache reuse, but its
session metadata names provider `deepseek` and `~/.codex/config.toml` points that
provider at `https://api.deepseek.com/v1`. Those requests did not reach Strata.
Their token and cache fields are still useful workload evidence; they are not
evidence that Strata's compaction cache failed.

The CLI version in the frozen rollout was `0.163.0`; the live shape capture used
`0.164.0`.

## Privacy-safe live capture

Added `tools/server/agent-shape-proxy.py`. It forwards Responses traffic without
changing it and records:

- Codex `request_kind`, `thread_source` and a hash of the session/thread pair;
- instructions, tools and every input item as canonical SHA-256 hashes;
- item kind, role and content kind;
- response status, token usage and elapsed time.

It does not write prompt, assistant, reasoning or tool bodies. The capture log
was checked for the probe prompt with `rg`; it was absent.

Live source:

```text
/tmp/agent-shape-b4-20261031.jsonl
SHA-256 d368e4cbb1bdbd42da927106bea6d9f16e41e21559d7e5142b954378ccaf73dc
8 requests, 31,386 bytes
```

The proxy forwarded to the active B4 Strata service through its existing
`127.0.0.1:18080` SSH tunnel. The server, engine and vision processes were not
restarted.

## Three real Codex compactions

Every real compaction arrived with `tools=[]`. The server's kept-tool path
therefore selected the previous turn's nine tools instead of the raw empty
list. The structural comparison on the last pair found `tools` as the first
raw-request divergence, with previous count `9` and compaction count `0`.

| Request | Input | Cached | Uncached | Prompt ms | Decode ms | Result |
|---|---:|---:|---:|---:|---:|---|
| compaction 1 | 11,225 | 11,124 | 101 | 518.5 | 13,914.9 | completed |
| compaction 2 | 11,452 | 11,349 | 103 | 529.4 | 14,948.3 | completed |
| compaction 3 | 11,729 | 11,627 | 102 | 451.5 | 13,135.4 | completed |

Cache reuse was `99.1002%`, `99.1006%` and `99.1304%`. All three returned HTTP
200, finished normally, and the Codex turns returned `OK` with no execution
errors.

The approximately 100 uncached tokens are the appended compaction instruction
and tokenizer boundary. There is no whole-history reread to remove. The
remaining decode time is summary generation, not prompt-cache recovery.

## Server metrics cross-check

Recent B4 `/metrics` entries agree with the proxy usage. The three compaction
requests report reused `11627/11729`, `11349/11452`, and `11124/11225`, prompt
times `451.5/529.4/518.5 ms`, and decode times
`13,135.4/14,948.3/13,914.9 ms`.

The ordinary requests around them show why a simple "make every turn reuse 99%"
claim is unsafe:

- the first cold turn uses `7,967.2 ms` prompt time;
- the next continuation adds material new context and takes `3,111.0 ms` prompt
  time with `8,581/11,101` reused;
- after compaction, the next ordinary window reuses only `3,705` tokens and
  takes `6,195.8 ms`; its history changed by design.

Those are new model-visible input and summary content, not a compaction cache
miss.

## Full local session scan

A privacy-safe scan of all 26 local rollout files and 333 MiB of JSONL measured:

| Item | Result |
|---|---:|
| Root tasks | 143 |
| Model requests | 14,531 |
| Inferred model union | 68,006.565 s |
| Tool union | 122,894.799 s |
| Input / cached | 2,992,567,554 / 2,933,197,312 |
| Output / reasoning | 10,206,569 / 3,307,087 |
| Ordinary reuse >=50% | 14,349 / 14,418 |
| Compactions | 113 |
| Compaction model span | 3,129.517 s |
| Tool output | 79,745,864 characters |
| Tool outputs >=10K | 2,600, `51,025,406` characters (`63.985%`) |
| Tool outputs 2K-10K | 4,387, `22,574,942` characters (`28.309%`) |

Only `143.727 s` of the union had exactly two model requests active, with 184
transitions into two active model requests and one into three. Role-aware
two-stream scheduling remains useful for latency when it occurs, but it cannot
be the next whole-task main item from this trace alone.

## Decision

Stop the compaction implementation branch. The current kept-tool LRU and
prefix cache work on real Codex compaction requests. Do not add another
compaction layout, exact pin, context-size scan, or server reload for this
evidence.

Move the next main item to `AGENT-TOOL-CONTEXT-DIET`: tool output at or above
2K characters is `92.294%` of all tool output, and at or above 10K characters
is `63.985%`. The experiment must be opt-in, preserve full output as a
deterministically readable artifact, retain recent and error output, and pass
code-edit and test-result quality gates.

## Verification

```text
python -m pytest -q tools/server/tests/test_agent_shape_proxy.py
6 passed

python -m pytest -q tools/server/tests/test_trace_agent_tasks.py \
  tools/server/tests/test_probe_codex_sessions.py
10 passed

python -m py_compile tools/server/agent-shape-proxy.py
git diff --check
```

No production server or model process was restarted.
