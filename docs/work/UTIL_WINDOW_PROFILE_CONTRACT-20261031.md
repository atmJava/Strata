# UTIL-WINDOW-PROFILE contract

Status: complete on 2026-10-31. See
[`UTIL_WINDOW_PROFILE_RESULT-20261031.md`](UTIL_WINDOW_PROFILE_RESULT-20261031.md).

## Evidence and selected problem

The A3-B-A4 GDN experiment reproduced the same broad shape as the earlier
checkpoint experiment: six streams reached `122.6 tok/s`, while eight streams fell to
`105.5 tok/s`. Combining one-row GDN recurrence launches did not help, and a fixed group count,
checkpoint move, piped MTP, QSA slot merge and the existing EXPDEC path have already failed.

The next question is where the eight-stream tail actually is. Existing measurements do not yet
separate:

- active request slots from padded or processed verifier rows;
- the two pipeline groups' stage occupancy and hand-off gaps;
- streamed-KV resolve and the other GPU stages;
- a slot waiting for turnover versus two requests sharing a six-slot pool;
- aggregate throughput from per-stream ITL and the slowest client.

This round uses only existing instrumentation. It does not change production configuration or
source, and it does not retry any rejected optimization.

## Instrumentation

The current A has neither switch enabled. A temporary deep copy of A adds exactly:

```text
STRATA_VERIFY_PROFILE=1
STRATA_BATCH_TELEMETRY=1
```

`STRATA_VERIFY_PROFILE` gives per-stage GDN/QSA phase time at the end of an idle batch run.
`STRATA_BATCH_TELEMETRY` emits `BSTAT` rows/window and `PSTAT` per-stage host in-flight spans.
The profiler disables some fused/side-stream paths, so its absolute throughput is not a production
claim; phase shares and the 2/4/6/8 differences are the evidence.

The collection tool also samples `/v1/telemetry` during each wave to measure occupied slots,
queued/waiting requests and request progress. It records each response's TTFT, slot wait, ITL,
prompt/decode time and finish reason. `BSTAT` rows are verifier work, not emitted-token counts.

## Frozen load

Use the same eight fixed 400-token prompts and greedy request body as
`tools/server/bench-strata.py`. Warm once, then run `N=2/4/6/8`, three repetitions per level, with
all requests in a wave released by one barrier. At `N=8`, exactly two requests must wait behind the
six slots.

All three arms run in the same boot:

1. A1: current restored A, three repetitions per level.
2. P: the A config plus only the two instrumentation switches.
3. A2: the identical A config and binary after a clean restart.

Record the engine/config SHA-256, six slots, two groups, health, production systemd state,
RAM/VRAM/swap and engine environment for every arm. Stop if the config hash or process environment
does not match the expected arm.

## Decision rule

This round is successful only if all of the following hold:

1. all 60 responses per arm have valid usage, timings and finish reasons (12 waves:
   `(2+4+6+8) x 3`);
2. no HTTP/execution error occurs and no unexpected request enters a wave;
3. six slots and two groups remain visible in A1/P/A2;
4. each A1/P/A2 level has three complete waves;
5. at least two independent metrics distinguish the six-stream peak from the eight-stream tail.
   The expected discriminating metrics are live slot occupancy and per-request tail/ITL, with
   `BSTAT` padding/slot ratio or `PSTAT` stage occupancy as independent corroboration;
6. the stage profile has at least two complete GDN and QSA reports at each level, unless the
   candidate is rejected early for a stronger reason.

If fewer than two metrics separate the levels, record a profiling-negative result and change the
measurement or hypothesis rather than inventing a source optimization.

The result must name one next source candidate with:

- the measured mechanism it addresses;
- the exact code path and trigger rate;
- why it can exceed the 1% user threshold;
- its bitwise/numerical impact;
- its rollback; and
- a pre-registered exactness and A3-B-A4 performance gate.

## Restart cost

The current A must be stopped to install the instrumentation and stopped again to restore A.
Each load has recently taken about `150` seconds and each stop about `9` seconds, so two transitions
cost about five to six minutes. The restart is necessary because both switches are read only when
the engine starts. No source rebuild is needed.

## Safety and restoration

Keep `strata.service` inactive/dead and its production config untouched. Preserve the original A
config and engine binary. Do not expose the API key. After the profile, restore the exact A config,
verify health, PIDs changed, engine/config hashes, six slots/two groups, absence of both profiler
environment entries, and the existing RAM/VRAM/swap envelope.
