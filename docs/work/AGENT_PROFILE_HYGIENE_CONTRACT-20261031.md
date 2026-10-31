# AGENT-PROFILE-HYGIENE contract

Date: 2026-10-31

Status: pre-registered before the instrumentation-off A-B-A.

## Question

Do `STRATA_DECODE_TIMING=1` and `STRATA_VERIFY_PROFILE=1` cost at least one
percent of user-visible single-request completion time on the current B4
production shape, and can both be removed without changing output, finish,
slot health or resource safety?

This is an environment and diagnostics-hygiene experiment. It does not change
the engine binary, server source, model, sampling or prompt.

## Evidence

The live engine process has both profiler variables set to `1`. The just
completed `AGENT-REAL-DECODE-SHAPE` replay therefore measured the instrumented
arm. L-035 previously measured 6.4% slower single-stream and 10.7% slower
four-stream aggregate throughput with `STRATA_VERIFY_PROFILE=1` on this host,
but that was an older engine and is not a current adoption result.

## Arms

Keep the current model, engine, server source, config, six slots, two groups,
MTP settings, warm prompt and temperature-zero request shape fixed.

- A1: current live configuration with both profiling variables on.
- B: remove only `STRATA_DECODE_TIMING` and `STRATA_VERIFY_PROFILE`.
- A2: restore both variables to prove the comparison is reversible.
- B2: remove both variables again if B passes, leaving the faster diagnostic
  configuration in production.

Each arm uses the same 416, 1,490 and 4,483 maximum-output requests for one
stream, with three waves per size. Record wall, prompt, decode, TTFT,
completion count, finish and output SHA-256. The existing 416/1,490/4,483
three-wave replay is the A1 baseline when process identity and prompt shape
match.

## Acceptance

The hygiene candidate is adopted only if:

- every request returns HTTP success with no engine or resource error;
- at least one dominant real-size class improves by at least 1% in median
  completion span, and the 4,483 class has no regression above 1%;
- 416, 1,490 and 4,483 output/finish hashes match their corresponding A arm;
- A2 returns within 3% of A1, preventing one cold or warm process from being
  mistaken for profiler overhead;
- six slots remain available, health returns loaded and GPU/RAM checks show no
  new failure;
- the config delta is exactly the two profiler variables, with an exact
  rollback to the current instrumented config.

The known 4,483 early-stop anomaly is tracked separately. A repeated early
stop is an unexpected finish and fails this gate. If it occurs only in one
arm, no performance conclusion is drawn from that size.

## Stop rules

Stop and restore the current instrumented configuration on a failed load,
health failure, six-slot loss, GPU initialization error, HTTP/execution error,
unexplained finish change, or output-hash change. Do not combine this
experiment with MTP depth, group count, slot count, kernel or scheduling
changes.

Do not claim aggregate decode tok/s as user benefit. Use completion span,
single-request wall and TTFT.

## Restart budget

Each arm needs a fresh process to change engine environment variables. The
experiment allows three restarts: A1 -> B -> A2 -> B2. Any restart slower than
the existing 420-second readiness ceiling is a failure to investigate, not a
reason to relax the gate.
