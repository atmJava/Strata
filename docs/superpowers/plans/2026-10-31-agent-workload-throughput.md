# Agent workload throughput plan

Date: 2026-10-31

## Objective

Improve Strata's end-to-end performance for the workload the user actually runs:
usually one Codex conversation, sometimes two, and never more than four
(one main agent and up to three sub-agents). A stable improvement of at least 1%
at a primary concurrency level counts. Four streams must not regress by more than
1%. Six and eight streams remain safety and overload regressions, not joint
throughput targets.

## Why this replaces the previous loop

The previous loop optimized against a 2/4/6/8 matrix in which six streams was the
throughput peak and eight streams was overload. The user rarely reaches six and
never reaches eight. That mismatch encouraged low-value local kernel work and
rejected low-concurrency mechanisms for regressions in levels the user does not
normally run.

The benchmark also had too little statistical power for a 1% decision. In the
last matrix, three same-configuration two-stream A1 samples ranged from
`63.854` to `85.196 tok/s`, and A1-to-A2 drift reached `7.067%`. A sequential
three-wave A1-B-A2 comparison cannot distinguish a real 1% effect from that
noise.

## Phase 1: measure the real bottleneck without changing the engine

Use the current A2 process and existing metrics. Do not restart or modify the
engine.

1. Read aggregate and per-request metadata only:
   `prompt_tokens`, `cached_tokens`, `prompt_ms`, TTFT, decode time/tokens,
   `slot_wait_ms`, finish reason and total wall time.
2. Inspect the existing conversation-cache view and request history for:
   reuse ratio, requests with reuse, total reused tokens, prompt tokens and
   parked conversations.
3. Build a deterministic agent-shaped replay only where real metadata is
   insufficient. It must cover:
   - one continuing conversation with tool-call-shaped turns;
   - two concurrent conversations;
   - one main thread and one child thread sharing a session;
   - four conversations, with no more than six server slots;
   - cold and warm prefixes at 4K, 32K, 128K and near-210K tokens.
4. Run A/A repetitions at 1/2/4 streams and report median, dispersion and the
   minimum detectable effect. A data set with an MDE above 1% is calibration
   evidence, not candidate acceptance evidence.

Stop condition: identify whether the dominant cost is context reread, cold
long-context computation, queueing, or decode. If reuse is already high, skip
the cache candidate and move directly to the dominant long-context phase.

### Phase 1 result

The normal conversation cache is working. In the measured agent-shaped runs:

- same-conversation continuation reused `95.16–95.21%` of an approximately
  12K-token prompt;
- even a new conversation with roughly 69% of the actual prompt shared reused
  `69.09%`;
- stable wave-level 95% intervals after warmup were `0.16–2.45%` for the normal
  2/4-parallel cases and about `1%` for stable one-stream cases.

The core conversation-cache path is therefore not the first candidate.
However, Codex thread-title requests still enter the engine because
`codex_thread_titles` is not enabled. Three approximately 12K-token title
requests took `10.292 s` cold and `1.978–2.791 s` warm, each adding an engine
request and 146–184 output tokens. The first candidate is the existing local
title short-circuit, registered in
[`AGENT_CODEX_TITLE_CONTRACT-20261031.md`](../work/AGENT_CODEX_TITLE_CONTRACT-20261031.md).

## Phase 2: one structural candidate

Choose exactly one candidate from the measured result:

- context reuse or slot affinity if cold/warm reuse is poor;
- QSA selection/attention if long cold prefill dominates;
- MoE gate/up or down if it remains the largest long-prefill phase;
- a low-concurrency decode mechanism only if decode dominates and can preserve
  bitwise output.

The candidate contract must state:

- the real trigger count and production path;
- the conservative whole-turn absolute saving, not only a local percentage;
- whether the saving is on the critical path;
- numeric or bitwise impact and a default-off rollback;
- why the potential gain exceeds the measured noise at 1/2 streams.

Reject before a server reload when the upper bound is below 1% of a primary
turn, the path is unreachable, or the expected saving is hidden by existing
noise.

## Phase 3: validation

Use low-cost local or same-process screens first. A server candidate must pass:

1. startup, readiness, resource and real-path evidence;
2. bitwise or frozen-greedy parity without unexplained finish changes;
3. enough interleaved waves to keep the measured MDE below 1%;
4. a stable improvement of at least 1% at one of 1/2 streams;
5. no regression above 1% at four streams;
6. zero request, CUDA, timeout and foreign-request errors.

Six and eight streams are reported as safety regressions only. Do not reject a
valid low-concurrency feature solely because an overload level it does not
target regresses.

## Phase 4: loop

After each decision:

1. restore the exact baseline;
2. update the short evidence ledger and the current handoff;
3. run `git diff --check`, UTF-8/BOM checks and
   `python tools/server/preflight.py --offline`;
4. commit, push over HTTPS and verify `ls-remote`;
5. write a brief retrospective and preregister the next source- or
   measurement-backed target.

Continue until the user explicitly stops.

## Plan review

The plan is accepted with these controls:

- It targets the user's measured concurrency rather than the server's synthetic
  peak.
- It measures the likely largest gain first: avoiding repeated long-context
  prefill.
- It does not assume shared prefixes or cache misses without request evidence.
- It replaces the noisy A1-B-A2 decision rule before judging another 1% result.
- It prevents another run of sub-millisecond kernels whose whole-window upper
  bound cannot reach 1%.
- It keeps a hard four-stream non-regression gate while removing six/eight from
  joint candidate selection.
- It does not authorize an unnecessary reboot; a server reload still needs a
  candidate that passed a local upper-bound gate.
