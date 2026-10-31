# AGENT-PLE-IO-COLD-READ contract

Date: 2026-10-31

Status: pre-registered after the first real 223K aged-session prefill profile.

## Evidence that selects the experiment

The real 49-day session resumed with 228,188 prompt tokens and zero cache reuse
in the profile arm. Server prompt time was 135,073 ms. The phase report shows:

- host PLE wait `50,362 ms`;
- PLE phase on the GPU timeline `39,291 ms` in the dominant chunk;
- host chunk setup `39 ms`, inter-chunk wait `0 ms` and post-chunk work
  `0 ms`;
- expert I/O was not the explicit PLE wait, although it competes for the same
  host memory and storage.

The earlier profiler-free replay used 223,114 prompt tokens and took 113.3 s.
The 5,074-token shape difference can come from the current client template and
must not be treated as a cache or code effect. The profile arm also costs extra
time; the event instrumentation therefore cannot be used as the production
baseline.

## Candidate

Use the existing `--ple-io mmap` mode. It changes how the same PLE rows are
read, not the row indices, dequantization, model arithmetic, sampling or
output. The profile arm remains:

```text
STRATA_PREFILL_TIMING=1
```

Keep the same source, engine, model, six slots, two groups, 262144 context,
MTP settings, stored session and resume command. The only new argument is:

```text
--ple-io mmap
```

## First screen

Run one real cold aged-session resume in a fresh process. The MMAP arm is worth
repeating only if all of the following hold:

1. HTTP/tool/output behaviour is unchanged: sentinel response, `finish=stop`,
   zero tool activity and zero errors;
2. prompt time drops by at least 1% against the direct profile arm;
3. the PLE host wait drops by at least 1.13 s, showing that the end-to-end
   change is attributable to this phase rather than general noise;
4. host memory remains safe, swap does not grow, and health/6 slots/2 groups
   remain available.

A weaker result closes the candidate without spending more restarts. A strong
result proceeds to a direct A2 comparison with the same profiler shape and
then an unprofiled MMAP B2 only after the profile-off direct arm establishes
the comparison.

## Adoption gate

For the aged request, the final unprofiled MMAP arm must:

- reduce user-visible wall or prompt/TTFT by at least 1%;
- preserve the stored history, sentinel output, finish and tool behaviour;
- show a PLE-specific reduction consistent with the end-to-end change;
- keep RAM, VRAM, cache and swap within the production safety envelope;
- be an explicit reversible config change with no source/default change.

Cross-boot drift must be shown with direct A1/A2 or another cold control. One
process is not enough to adopt a small effect.

## Stop rules

Stop and restore exact B2 on load failure, health loss, six-slot loss, output or
finish change, tool execution, memory pressure, swap use or an engine error.

Do not scan other PLE cache, inflight or keep-alive parameters in this
iteration. Do not compare a second replay in the same process with the cold
sample. Do not call aggregate throughput a user benefit.
