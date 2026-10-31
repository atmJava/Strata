# DSH end-of-turn title isolation contract

Date: 2026-11-01

Status: rejected on 2026-11-01. The candidate cannot coexist with DSH's title
lifecycle. It did not change the model, prompt ids, sampling, tools, output
parsing or engine.

## Problem

The existing `STRATA_DEFER_TITLE_REQUESTS=1` gate holds DSH's exact title request
until the ordinary request active at arrival completes. DSH then immediately
submits the next tool step, so the title and step 2 run together. A production
DSH fixture measured step 2 at `2.681 s` to first engine token with only
`1,148.4 ms` of engine prompt; the extra `1.533 s` was concurrent work. This is
already `11.6%` of the `13.261 s` task.

## Frozen observation

- one ordinary main DSH session;
- exact current DSH tool and request shape;
- hidden title request has `max_output_tokens=4096`, no tools and no top-level
  instructions/client metadata;
- title must remain provider-generated;
- no user profile change;
- no prompt, reasoning, tool or response bodies retained;
- two ordinary sessions fail open exactly as today.

## Candidate behavior

The title request waits while one ordinary main request is active and while the
ordinary turn has not reached a terminal assistant response. A terminal assistant
response is one that returns no tool/function call. The gate may start the title
only when:

1. no ordinary request is active;
2. the most recent ordinary response in the observed single session contained no
   tool call; and
3. the title client has not exceeded its bounded wait deadline.

Any of the following fail open to existing behavior:

- more than one ordinary session;
- malformed or unknown response shape;
- title-only traffic after the grace period;
- client cancellation;
- server draining;
- missing terminal signal before the existing maximum wait.

The initial implementation must be default-off. The production rollback is to
remove `STRATA_DEFER_TITLE_REQUESTS=1` or disable the new end-of-turn mode with a
single environment flag.

## Gates

Correctness and isolation:

- existing exact-shape title tests remain green;
- two titles never wait for each other;
- two ordinary sessions fail open;
- title-only traffic fails open after grace;
- intermediate tool-call responses do not release the title;
- a terminal no-tool response releases it when ordinary active count is zero;
- cancellation, timeout and draining release admission exactly once;
- title remains provider-generated and no fallback replaces it in the measured
  single-session path.

Performance:

- use a real multi-step DSH fixture, not a one-step synthetic request;
- compare current production against the candidate with the same endpoint,
  model, tools, prompt, sampling and cache state where possible;
- report task wall, per-step first stream, title overlap and token usage;
- at least three AC and CA pairs if persistent cache permits isolation;
- require median task-wall improvement of at least 1% and at least 5/6 paired
  wins, with absolute seconds reported;
- no intermediate title request or title decode may overlap a main model step in
  the candidate arm;
- no title quality regression, main task quality regression, event/tool errors,
  unexpected finish, Xid, resource regression or configuration drift.

If the gate merely moves the title to another main step or leaves a title-only
client waiting until fallback, record the candidate as failed. Do not call the
first-step-only improvement sufficient.

## Rejection

The local state machine and HTTP ordering tests passed, but the real DSH client
cancels the hidden title request when the main turn ends. A strict `single-file`
run kept the title waiting `9.421 s`; once the proxy released it after the final
main response, the client was already gone and the proxy recorded
`BrokenPipeError` / HTTP 502. Sending SSE headers early and writing valid chunked
heartbeats did not extend the title's logical lifetime.

Holding the final main response until the title completes would make the title
sequential. The observed title takes about `4.3 s` while competing with later
main steps, and standalone title samples are about `2 s`; that cannot establish
a conservative 1% task gain and may make the task slower. The implementation was
removed after the rejection.

The test method must therefore record title request arrival, release, upstream
start/finish, main turn end, and client cancellation. A task-wall comparison
alone cannot distinguish a successful isolation from a title that was silently
cancelled.

## Cost

The server candidate requires a production restart. Before doing so, first prove
the state machine locally and with a no-restart proxy/real DSH fixture. A restart
is justified only when measured step-2 overlap is removed and the conservative
task-level benefit exceeds the restart/loading cost for the intended validation.

No engine rebuild or GPU unlock work is part of this contract.
