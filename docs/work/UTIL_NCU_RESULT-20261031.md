# UTIL-NCU result

Run: 2026-10-31 on the candidate snapshot, 2 x RTX 2080 Ti, driver `595.71.05`,
CUDA 13.0 profiling tools, compute capability 7.5.

## Decision

**MIO throttle is the one selected root cause.** The mode-8 IQ3_XXS/Q2_0 gate/up kernel
`native_gu_fused_kernel<18>` spent 29.75% of warp issue cycles on MIO throttle. Long scoreboard
was second at 17.50%, short scoreboard was 12.80%, and barrier was only 3.76%. Tensor-pipe
activity was 0.04%, so the P7 tensor-core route is not supported by this evidence.

The next candidate is a bounded UTIL-KERNEL experiment on `native_gu_fused_kernel<18>`. It must
reduce the measured MIO stall without changing output arithmetic. Its contract is in
[`UTIL_KERNEL_CONTRACT-20261031.md`](UTIL_KERNEL_CONTRACT-20261031.md).

## Full-request attempt

The controlled candidate used the preserved engine and server snapshots and read 98,329 prompt
tokens. Nsight Systems reports generated for earlier forked server runs did not contain CUDA
kernel data. A direct-engine wrapper was added so the profiler is the engine's parent; NCU was then
used with a kernel-name filter and the four requested sections.

The full-request NCU replay reached the final verify window at token position 98,322 and then made
no GPU progress for more than 60 seconds. The engine's existing watchdog ended the request after
920 seconds. No `.ncu-rep` was produced. This is retained as a profiling-cost result, not a
performance sample:

- `ncu-gu-server-20261030T174402Z.log`
- `ncu-gu-engine-20261030T174402Z.log`

The failure matters: section-heavy kernel replay is not a usable iteration loop on this
two-stage, resident-expert service. A profiler run that can trip the production watchdog cannot
serve as an A/B harness.

## Bounded kernel measurement

The fallback keeps the candidate's real kernel and real GGUF weights, but removes the
cross-stage service dependency:

- candidate binary sha256
  `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
- `native_expert_bench`, mode 8;
- layer 36, IQ3_XXS gate/up and Q2_0 down;
- 64 distinct expert blobs, 6 tokens per group;
- output hash `9a055a5a94e15c87`, bitwise equal between the two benchmark arms.

The benchmark reports about 474 microseconds for the gate/up launch. NCU measured one replayable
launch at 653.18 microseconds with uncontrolled clocks and caches. Absolute profiler duration is
not the comparison metric; the counters below identify the execution limitation.

| Counter | Value |
| --- | ---: |
| SM throughput | 58.07% |
| Achieved active warps | 71.59% |
| FMA pipe active | 41.15% |
| Tensor pipe active | 0.04% |
| DRAM throughput | 38.09% |
| L2 throughput | 15.76% |
| MIO throttle stall | 29.75% |
| Long-scoreboard stall | 17.50% |
| Short-scoreboard stall | 12.80% |
| Not-selected stall | 9.98% |
| Wait stall | 9.15% |
| Math-pipe throttle | 4.31% |
| Barrier stall | 3.76% |

Occupancy is not the first problem: achieved active warps are 71.59%, but SM throughput is
58.07%. The kernel is issuing enough warps; its shared-memory/vector path is constraining issue.
The codebook is copied to shared memory and the dot path performs random 32-bit codebook lookups
plus q8_1 activation loads from shared memory. The leading candidate therefore targets shared
bank conflicts, not a larger CTA or more warps.

## Recovery

All profiler and model processes were ended after the failed full-request run. The original
candidate was then restored manually on port 8080:

- server pid `504632`, engine pid `504639`;
- health 200 with `loaded=true`;
- engine sha256
  `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
- no `STRATA_PROFILE_*`, `STRATA_NCU_*`, QSA diagnostic, or QSA experiment environment;
- production `strata.service` remains `inactive/dead`, `MainPID=0`, `NRestarts=23`.

## Tooling

`tools/server/ncu-engine-wrapper.py` makes Nsight Compute or Nsight Systems the direct parent of
the real engine selected by the server's config. It preserves argv and environment and bounds NCU
with a demangled kernel regex, launch skip, and launch count. Its six tests pass.

The wrapper is useful even though the full-request path was rejected: it proved the process shape
and gives a reproducible way to collect a future lighter metric set.

## Evidence

- `/opt/ai/logs/ncu-micro-gu-fused-20261030T180606Z.ncu-rep`
  (sha256 `d37c4e9c041a850143e93ca86a08d1d601a71fb925d5e0c9efea9657b85125ed`)
- `/opt/ai/logs/ncu-micro-gu-fused-20261030T180606Z.csv`
  (sha256 `b213229aa2d10a8a5380d612051b4e96a6b42d05ad6c5bcd93a97329865ab7d3`)
- `/opt/ai/logs/ncu-micro-gu-metrics-20261030T180641Z.ncu-rep`
  (sha256 `48044aa0f609f87476d7fc4d8a25e21043b5bf115df95fe483c2c9dac9a70df5`)
- `/opt/ai/logs/ncu-micro-gu-metrics-20261030T180641Z.details.csv`
  (sha256 `57a3bdc3d545929fe75dbd6ddf498ef5ab5b6e0899087c2cb64ef73688c328b6`)
- `/opt/ai/logs/ncu-gu-server-20261030T174402Z.log`
  (sha256 `12e1b64434d38bff9e9423a18c63c1bbd4b93e87dfbdf44a343f919c81282861`)
- `/opt/ai/logs/ncu-gu-engine-20261030T174402Z.log`
  (sha256 `9756030e2ac9df1a3525ff0660b04b88703394bd9b49f809c4ef43b142d811a4`)
- `/opt/ai/logs/ncu-selection-20261031-UTIL-NCU.json`
  (sha256 `23bb3a0207249e2e854040f7e94de79bc88870734bd1c51c8b7eac10385338df`)
- `/opt/ai/logs/utilization-decision-20261031-UTIL-NCU.json`
  (sha256 `43dafba4c24deec442e1700adc7fbc84541ad4c78851cf5acd4fa15ad7d55ba6`)

## Not measured

The counter evidence is one real-weight microkernel geometry, not a successful full-request NCU
run. It does not prove the same stall percentage for every expert format, layer, natural-language
prompt, concurrent service, or backend. A candidate must pass its pre-registered kernel gate
before any end-to-end claim is made.
