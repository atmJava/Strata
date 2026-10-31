# DSH request-path map contract

Date: 2026-11-01

Status: active. This is a no-restart discovery round. It does not authorize an engine,
kernel or production-default change until the measured host-path upper bound justifies
one.

## Problem

The existing request timings start inside `Service.run()`, after the HTTP body has been
read, the Responses request has been parsed and rendered, `PromptEncoder` has produced
the ids and `Service.prepare()` has completed. Consequently, `queue_ms`, `ttft_ms` and
`wall_ms` do not describe the whole DSH step:

- request upload and JSON receipt;
- Responses input/tool/parameter conversion;
- chat-template rendering;
- prompt encoding and prompt-cache lookup;
- `Service.prepare()`;
- Response assembler setup.

Separately, `prompt_ms` is the engine's prompt phase and `decode_ms` is its first-token
to last-token decode span. Their sum is not necessarily the request duration: time
before the first token and time after the last token must be measured, not guessed.

Three matched production DSH sessions on the adopted tool-prefix build show why both
boundaries matter. In the two steady sessions, continuation step 2 had client model
wall `3.879/4.178 s`, server run duration `3.7/4.0 s`, engine prompt `485.6/481.0 ms`
and decode `2460.9/2771.2 ms`. Step 3 had client wall `1.163/1.235 s`, server run
duration `1.0/1.1 s`, prompt `430.4/442.7 ms` and decode `598.7/662.6 ms`.

The title request is not the explanation for those gaps. Production logs show the
title remained deferred for `2.749/2.401 s` across the short tool gaps, so it did not
run beside steps 2 and 3. The unexplained interval may be host preparation, engine
setup before its first token, streaming/finish work or measurement boundaries. This
contract separates them before choosing a mechanism.

## Frozen workload

Use the exact current DSH Responses shape:

- streaming Responses request;
- `max_output_tokens=32000`;
- no top-level `instructions` or `client_metadata`;
- a leading developer message that becomes the system message;
- exactly the 25 current `DSH_MAIN_TOOLS` names;
- one cold context and hot continuations with approximately 54-57 fresh input tokens.

Measure these context sizes:

| size | purpose |
|---|---|
| about 6K | current ordinary DSH coding loop |
| 94K | established long coding session |
| 189K | p90/p99 long coding session |

The probe uses synthetic, deterministic text with the production tokenizer and
template. It records only token counts, hashes and timings. It never writes prompt,
reasoning, tool-result or response bodies.

## Phase boundaries

For each size, report cold and hot results separately.

| phase | boundary |
|---|---|
| wire receipt | serialized request bytes through `json.loads` |
| Responses preparation | `input_messages`, `request_tools`, `template_kwargs`, format preparation |
| template | the production `ChatTemplate.render` call |
| prompt encoder | `PromptEncoder.encode`, including resume lookup |
| service prepare | `Service.prepare` end to end without images |
| assembler | `Assembler` construction |
| client step | DSH `step/start` through assistant message |
| server run | `Service.run` request clock through finish |
| engine prompt | engine `prompt_ms` |
| pre-first-token gap | server run start through `first_engine_token_s`, less `prompt_ms` only when proved by one clock source |
| decode | engine `decode_ms` |
| post-decode/finish | server run duration less the measured engine spans |

Client, server and engine clocks stay separate. Do not subtract values measured on
different clocks without stating the uncertainty.

## Measurement method

1. Run `tools/server/probe-host-request-path.py` locally against the production
   tokenizer/template files. It does not start an engine or contact the live server.
2. Run it on the production host as well, because CPU and filesystem differences are
   part of the user path.
3. Use at least seven hot repetitions per size after one warm-up; report median,
   p90, spread and absolute milliseconds. Cold encoding is reported separately and
   is never mixed into the hot median.
4. Join the existing three-session DSH evidence to server metrics without retaining
   message text. Keep unmatched title/auxiliary requests visible.
5. Do not change production timing, restart the server or enable an engine profiler
   during this stage.

## Decision gates

Implement a host-path candidate only when all of these hold:

- it affects a real DSH main request, not only a benchmark;
- its measured or conservative upper bound is at least 1% of a representative whole
  task, with absolute seconds reported;
- the same mechanism is material at more than one relevant context size, or has a
  measured p90 trigger;
- prompt ids, tools, sampling, finish semantics and output parity can remain unchanged;
- the candidate is default-off and has a one-line rollback;
- a restart's expected saved seconds exceed the measured loading/cache-loss cost for
  the intended validation.

If the combined host preparation upper bound is below 1%, record a null result and
close this candidate. The next decision compares:

1. the engine's pre-first-token setup after a warm prompt-cache hit;
2. long-output decode and reasoning length;
3. another user-visible bottleneck with stronger weighted evidence.

Do not move to a kernel parameter merely because it is nearby. Do not revisit
`short_read`, stream ring, staged lookahead, GDN, head split or fair-serve variants
that already failed.

## Privacy and rollback

The probe never records or prints message bodies, generated text, API keys or titles.
It writes only size buckets, token counts, timings and hashes. Discovery requires no
production change, so rollback is deletion of temporary result files; a later
candidate must define its own exact rollback before deployment.
