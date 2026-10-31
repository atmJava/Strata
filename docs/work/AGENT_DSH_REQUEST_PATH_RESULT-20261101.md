# DSH request-path result

Date: 2026-11-01

Status: host preparation is a null result. The next active item is end-of-turn
title isolation, not another host, template or `PromptEncoder` micro-optimization.

## Result

The production tokenizer and chat template were measured with
`tools/server/probe-host-request-path.py` at approximately 6K, 94K and 189K
prompt tokens. The probe used no engine and did not restart or change production.

| prompt | cold `Service.prepare` | hot `Service.prepare` median | hot p90 | request JSON |
|---:|---:|---:|---:|---:|
| 6K | `8.161 ms` | `6.439 ms` | `6.607 ms` | `26,564 B` |
| 94K | `46.161 ms` | `11.527 ms` | `11.778 ms` | `430,822 B` |
| 189K | `87.861 ms` | `18.345 ms` | `18.708 ms` | `864,683 B` |

Hot JSON receipt was `0.101 / 0.415 / 0.754 ms`, Responses preparation was
`0.099 / 0.099 / 0.105 ms`, and the assembler was about `0.03 ms`. These figures
are far below 1% of a representative DSH task. Optimizing this host path would be
local work with no user-visible result.

The cold `PromptEncoder` on a first 189K request remains about `72.6 ms`, but that
is not the ordinary hot continuation path and cannot be used to claim a hot-turn
optimization. Cold 189K preparation still belongs to the older-session tail, not
the current per-step goal.

## Live DSH path

One production-compatible DSH `single-file` fixture completed in `13.261 s`
task wall. All six main steps joined to server rows by exact token counts; the
seventh request was the one title request.

| step | fresh/cached | client model wall | step start to engine admitted | engine prompt | first engine token | decode |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | `616/5,463` | `2.695 s` | `213 ms` | `1,563.2 ms` | `1,589 ms` | `879.5 ms` |
| 2 | `236/6,156` | `3.713 s` | `99 ms` | `1,148.4 ms` | `2,681 ms` | `919.4 ms` |
| 3 | `89/6,465` | `1.492 s` | `120 ms` | `606.4 ms` | `632 ms` | `733.5 ms` |
| 4 | `159/6,612` | `2.369 s` | `93 ms` | `861.9 ms` | `887 ms` | `1,374.5 ms` |
| 5 | `36/6,884` | `1.103 s` | `100 ms` | `294.4 ms` | `320 ms` | `683.5 ms` |
| 6 | `33/6,981` | `0.740 s` | `96 ms` | `264.6 ms` | `290 ms` | `350.1 ms` |

The model steps total `12.112 s` (`91.287%` of task wall); the tool union is
`0.991 s` (`7.469%`). First stream consumes `7.166 s` and the remainder after
first stream is `4.946 s`. On the ordinary steps, first token follows the
engine's prompt span by about `25–26 ms`; client first stream follows the matched
engine first token by only a few milliseconds. This closes the earlier
unattributed-gap hypothesis: there is no separate multi-second server or host
phase in ordinary steps.

Step 2 is different. It starts about `0.2 s` after the title request and records
`2.681 s` to first engine token with only `1,148.4 ms` of engine prompt. The
extra `1.533 s` is concurrent title/main work, not host preparation. The current
`STRATA_DEFER_TITLE_REQUESTS=1` gate waits only for the ordinary request that is
active when the title arrives. As soon as that first step finishes, the title is
admitted; DSH immediately starts its next tool step, and both share the GPU.

This reproduces a product gap that the previous single-turn A/B did not expose.
An end-of-turn gate must track the complete DSH tool loop, not just release the
title when the first ordinary admission reaches zero.

## Probe defect fixed

Current DSH exports place `assistant/message.data.stream` beside `message` and
write the wall-clock timestamp at `chunk.time`; older exports placed `stream`
inside `message` and used `chunk.time0`. The old probe read only the older shape,
so every current export reported `time_to_first_stream_s = null`.

`tools/server/probe-dsh-session-cost.py` now accepts both shapes and prefers
`chunk.time`, with regression coverage in
`tools/server/tests/test_probe_dsh_session_cost.py`. The severe four-step export
then resolves to:

| step | first stream | after first stream | output | fresh/cached |
|---:|---:|---:|---:|---:|
| 1 | `13.707 s` | `3.893 s` | `151` | `10,097/0` |
| 2 | `2.977 s` | `2.987 s` | `226` | `3,057/10,248` |
| 3 | `1.278 s` | `1.298 s` | `104` | `382/13,531` |
| 4 | `1.628 s` | `5.085 s` | `400` | `614/14,019` |

That session's task wall is `36.310 s`, model wall `32.853 s`, and tool union
`1.004 s`. Both first-token work and real output work matter; the former
short-output samples cannot be generalized to every request.

## Decision

Close `AGENT-DSH-REQUEST-PATH-MAP` without a source change. The next active item
is `DSH-TITLE-END-OF-TURN`:

- preserve the real provider-generated title;
- do not modify the user's DSH profile;
- keep two-session fail-open;
- hold the exact title shape until the single main DSH loop reaches a terminal
  assistant response and no ordinary request is active;
- allow a bounded timeout and preserve existing title fallback behavior;
- require a multi-step real DSH A/B, not a one-step synthetic gate;
- use task wall as the primary metric and per-step overlap as the mechanism
  proof.

The live step-2 contention has a conservative `1.533 s` upper bound, already
`11.6%` of this task and far above the 1% gate. It cannot all be removed by
scheduling, so the implementation is accepted only after measured A/B.

## Evidence

- `/tmp/dsh-host-path-20261101.json`, SHA-256
  `b8e9d566dec4c8c8c4dda0f0444cf976cdb957a14e7202c900c8e48ae22ab371`
- `/tmp/dsh-live-gap-cost-corrected-20261101.json`, SHA-256
  `75a5d4bf0a3b7ad931b676953b7ce67a87e4ca39a9c7281f41000bad4afadb23`
- `/tmp/dsh-live-gap-metrics-20261101.json`, SHA-256
  `d423ff77e8e39b3316fb53d48891789b9124421667c3e9c6111ace14e0aaa44e`
- `/tmp/dsh-live-gap-iSqiPf/report.json`, SHA-256
  `6172c69524e344f58d5df58f13366a567ab09b7ea845e6e4e474646a6d893d6c`
- DSH v4 session,
  `/home/kali/.dsh/sessions/--tmp-dsh-live-gap-iSqiPf-work-single-file--/session-1fadf9ba-07af-4817-9ec4-2e7ae0cf07b4/session.v4.jsonl.zstd`,
  SHA-256 `6a0817f58f1603bea0bb5d06d338143cbd0af7cc95537d71c7ac7985d8848ed0`
- `/tmp/dsh-second-cost-corrected-20261101.json`, SHA-256
  `a71cf9e0d7db254e82cf4cf8680472ac3b8215162ba5ca1a9db74f325a9f5444`

No prompt, reasoning, tool output or credential body is retained in these
aggregate reports. Production server, engine and configuration were not changed;
no restart was performed.
