# AGENT-OLD-SESSION-PREFILL-PROFILE contract

Date: 2026-10-31

Status: completed. The profile selected direct PLE queue depth as the first
removable component; its separate candidate also completed. See
[`AGENT_PLE_INFLIGHT_RESULT-20261031.md`](AGENT_PLE_INFLIGHT_RESULT-20261031.md).

## Question

Where do the roughly 113 seconds of a real 223,114-token aged-session resume
go, and is there at least one removable mechanism with a conservative
user-visible saving of at least 1.13 seconds without changing the prompt,
output, finish or tool behaviour?

This is a diagnostic followed by one clearly scoped implementation decision.
It is not permission to tune unrelated kernels, context size or persistence
parameters.

## Fixed evidence

The real aged-session replay in
[`AGENT_OLD_SESSION_RESUME_RESULT-20261031.md`](AGENT_OLD_SESSION_RESUME_RESULT-20261031.md)
used 223,114 prompt tokens, reused zero cached tokens, spent about 112.863 s
before the first token, and completed in 113.3 s. Decode was 292.8 ms.

Earlier synthetic 96K profiling found the GPU, rather than host staging,
dominated prefill. That result guides the measurement but cannot select a
223K mechanism. The current source already exposes
`STRATA_PREFILL_TIMING=1`, which records 21 ordered GPU phases plus host
staging on the production compute path.

## Controlled profile arm

Start from the exact profiler-free B2 config. The only intended delta is:

```text
STRATA_PREFILL_TIMING=1
```

Keep the model, engine binary, server source, six slots, two groups, context
262144, MTP settings, sampling, prompt and stored session history fixed.

Before restart, preserve the B2 config, process list, environment and hashes.
Starting the engine costs about 142-199 seconds based on the immediately
preceding B2 restarts. A restart is justified only because the aged request
cannot be measured cold after the production process has cached it.

## Measurements

Replay the same stored session exactly once in the profiling process. Record:

- request id, prompt/input, cached and uncached tokens;
- wall, prompt/TTFT, decode, output count, finish and output hash;
- all 21 prefill phase values and the host staging/chunk-setup values;
- GPU timeline versus whole-request wall;
- primary and peer-device attribution where emitted;
- health, six slots, two groups, RAM, VRAM and swap;
- confirmation that the only engine instrumentation delta is
  `STRATA_PREFILL_TIMING=1`.

Do not print prompt, assistant, reasoning or tool bodies. Preserve private raw
logs and record only hashes and aggregate fields in the repository.

## Selection gate

A mechanism may proceed to implementation only if:

1. the measured phase map identifies one removable component whose
   conservative contribution to the 113.3-second resumed request is at least
   1.13 seconds;
2. the mechanism preserves the full stored history, exact tokenization,
   sampling, output/finish and tool semantics;
3. the change is opt-in or default-byte-identical, bounded in RAM/VRAM and has
   an exact rollback;
4. the expected saving exceeds build, deployment and cold-cache cost for the
   real trigger frequency.

If no phase supports that bound, close the prefill-kernel branch and compare
the remaining structural alternatives: session-state persistence,
cross-process cache residency, prefetch/warm-up and client-side history
handling. Those alternatives must be ranked by p99 benefit, storage cost,
staleness risk and frequency before implementation.

## Stop rules

Stop and restore B2 on failed load, health loss, six-slot loss, HTTP or engine
error, unexpected output/finish, resource pressure, or inability to prove the
single environment delta.

Do not scan context sizes, speculative parameters, groups, tiles or unrelated
kernels. Do not use a second replay in the same process as another cold
sample. Once the request has populated cache, a subsequent candidate
comparison needs a distinguishable cold control or a newly measured aged
source.

## Restart budget

The initial profile allows two restarts:

1. B2 to the profile arm with only `STRATA_PREFILL_TIMING=1`;
2. profile arm back to exact B2.

An implementation experiment requires a new pre-registered contract after the
phase result; it cannot silently spend additional restarts.
