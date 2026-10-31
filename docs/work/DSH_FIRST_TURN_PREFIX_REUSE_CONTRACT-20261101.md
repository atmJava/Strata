# DSH first-turn prefix reuse contract

Date: 2026-11-01

Status: adopted. The production result and corrected test method are in
[`DSH_FIRST_TURN_PREFIX_REUSE_RESULT-20261101.md`](DSH_FIRST_TURN_PREFIX_REUSE_RESULT-20261101.md).

## Problem

Three consecutive fresh DSH sessions sent an identical system/tool prefix but only
reused `582` prompt tokens. The existing automatic pin is at the end of the system
message, before the tool definitions. The next unchanged tool block plus system
message is `5,463` tokens in the measured DSH shape.

A privacy-safe proxy experiment set `strata_prefix={"messages":1}` without changing
the prompt. The engine then resolved `pin=5463`. Session 1 remained cold at `582`
cached tokens. Sessions 2 and 3 restored `5,463`, read only `599` fresh tokens, and
reduced the main prompt from about `5.1–6.1 s` to `1.42–1.43 s`.

This proves a mechanism candidate. It does not yet prove the product path, a
controlled whole-task median, or two-session behavior.

## Candidate

Add a default-off server opt-in:

```sh
STRATA_PIN_DSH_TOOL_PREFIX=1
```

When enabled, a Responses request that exactly matches the current DSH main-request
wire shape and has no explicit `strata_prefix` receives
`strata_prefix={"messages":1}`. This pins the end of the first system message and
the rendered tool list. The prompt token ids, tools, sampling, engine and model
weights do not change.

The exact current DSH gate is:

- streaming Responses request;
- `max_output_tokens=32000`;
- no top-level `instructions` and no `client_metadata`;
- exactly the 25 current DSH tool names;
- a first developer message that becomes the template system message.

The server records a canonical fingerprint of the first accepted DSH system/tool
prefix and template kwargs. Later requests pin only when that fingerprint is
unchanged. A DSH upgrade or changed tool description fails open and must not create
a second pinned prefix within the same server start. An explicit client
`strata_prefix` always wins. Title requests never match.

## Gates

Source and focused tests:

1. the feature is off by default and the default prompt is byte-identical;
2. the exact DSH shape receives `{"messages":1}` while the same request without
   the option does not;
3. an explicit prefix is not overwritten;
4. title, non-DSH, and changed-prefix requests do not receive the tool-end pin;
5. enabled and disabled prompts have identical token ids;
6. focused Responses, frontend and shape-proxy tests pass.

Production:

1. backup the current server file and restart into health `loaded`, six batch slots,
   two groups and the same engine hash;
2. run at least three consecutive fresh DSH sessions; every request after the first
   must report `pin=5463`-class reuse rather than `582`;
3. the 1-session whole-task median must improve by at least 1 percent with absolute
   seconds reported; session 1 cold-read cost is included and not hidden;
4. a two-session run must complete, provider titles must remain present, and no
   title/cache conflict may appear;
5. code edits and tests must remain correct; protected tests must be unchanged;
6. zero HTTP/event/tool errors, health loaded, six slots / two groups, and zero new
   NVRM Xid;
7. restart cost is reported separately and not counted as a user speedup.

The candidate passed these gates and is adopted. The measured steady-state whole-task
median improved from `11.3715 s` to `7.3165 s` (`35.66%`, `4.055 s`). Six coding
fixtures passed, the two-session run retained provider titles, and the production
window added no NVRM Xid.

## Test-method correction

The original plan named an ABBA comparison. That is invalid for this candidate after
the first arm writes a longer durable conversation-cache checkpoint: a later control
request can reuse the candidate's `5,463`-token boundary, and even a proxy override
for the shorter `582` boundary may be superseded by the existing conversation
checkpoint. The accepted sequence was therefore a clean boot with A first and B
second, with each group's cold session reported separately. It is not described as
interleaved ABBA.

## Rollback

Remove `STRATA_PIN_DSH_TOOL_PREFIX=1` and restart the Python server. The default
path keeps the existing system-boundary behavior. The model, engine binary,
quantization and config do not change.

## Limits

- The first cold session cannot reuse a prefix that has not been written yet.
- The measured benefit is for consecutive fresh DSH sessions sharing the same
  system and tool definitions.
- The pin changes cache lifetime and must not be generalized to clients with
  changing tool descriptions.
- No claim is made about unrelated OpenAI, Codex, Claude or browser clients.
