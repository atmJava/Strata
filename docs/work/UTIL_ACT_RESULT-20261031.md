# UTIL-ACT result

Run: 2026-10-31 on the candidate snapshot, one idle RTX 2080 Ti for the microkernel, CUDA 13.0
profiling tools, compute capability 7.5.

## Decision

**UTIL-ACT is rejected.** Explicitly loading one q8_1 activation block for the paired gate/up row
dots preserved the output but produced no speedup. Seven alternating process runs gave a
`475.1 us` baseline median and `475.2 us` candidate median, a `0.021%` regression, with only
3 of 7 pairs favoring the candidate.

The counter comparison explains why: the compiler already reused the activation loads. Baseline
and candidate had the same `10,583,040` shared-load instructions and the same 73 registers per
thread. Shared-load wavefronts were `17,698,354` and `17,698,418`; the two reports therefore
describe the same machine work within profiler noise. This source-level change was a no-op.

## Frozen workload

```text
native_expert_bench SHARD1 36 64 6 8 8 1
```

The exact IQ3_XXS shard 1 used by the candidate server, layer 36, 64 expert blobs and 6 entries
per group. Each process used `CUDA_VISIBLE_DEVICES=0` while both GPUs were otherwise idle. Every
baseline and candidate process printed hash `9a055a5a94e15c87` and `0 differing`.

## Seven paired process runs

| Pair | Baseline gate/up us | Candidate gate/up us | Candidate |
| ---: | ---: | ---: | :---: |
| 1 | 663.6 | 495.1 | win |
| 2 | 476.1 | 473.1 | win |
| 3 | 475.1 | 475.4 | lose |
| 4 | 471.7 | 472.8 | lose |
| 5 | 476.6 | 474.8 | win |
| 6 | 473.8 | 475.2 | lose |
| 7 | 475.1 | 475.2 | lose |

The first baseline process again contains the cold-clock outlier. The pre-registered median gate
still fails: improvement `-0.021%`, candidate p75 `475.3 us` above the baseline median, candidate
wins `3/7`.

## Counter follow-up

Bounded one-launch metric runs used the same real weights and kernel geometry. Absolute replay
duration was not used as a performance sample.

| Counter | Baseline | Candidate |
| --- | ---: | ---: |
| Kernel replay duration | 664.928 us | 665.504 us |
| Registers/thread | 73 | 73 |
| Shared-load instructions | 10,583,040 | 10,583,040 |
| Shared-load wavefronts | 17,698,354 | 17,698,418 |
| Shared-load bank conflicts | 7,115,314 | 7,115,378 |
| MIO-throttle warps/issue-active | 3.866710 | 3.837811 |
| Active warps | 71.598257% | 71.607481% |

The MIO difference is below the duration/time result and does not accompany fewer instructions or
wavefronts, so it is not a speed claim. The compiler was already satisfying the intended load
reuse across the two inlined row dots.

## Restore and validation

The original source hash `47009da533...` and engine hash `105a4171...` were restored. Both
changed bench/parity targets were rebuilt, then:

- `native_expert_parity --synthetic iq3_xxs/q2_0`: 0 failures;
- `iq_multi_parity`: 0 failures, all grouped cases bitwise equal;
- restored `native_expert_bench`: hash `9a055a5a94e15c87`, 0 differing.

The candidate service was stopped once for the isolated kernels. No engine A/B was run because
the kernel gate failed; the restored candidate was started once afterward. The restart cost is
recorded separately with its measured duration. This cold start took `3:50`, longer than the
previous `2:45` restart. Final state is server/engine PID `508675/508741`, vision PID `508681`,
health 200 with `loaded=true`, no experiment environment, and production `strata.service` remains
`inactive/dead`, `MainPID=0`, `NRestarts=23`.

## Evidence

- `/opt/ai/logs/util-act-build-20261031T1942Z.log`
  (sha256 `d0a79a2ce68f608a7e025382bf2178c50f2249bfffa2face5a4c4ec30e1ac07b`)
- `/opt/ai/logs/util-act-ab-20261031T1945Z.log`
  (sha256 `6aa33b75d67cbb2363f8b5a28b4b0ccb988d52ea4292bb0cd90dc68fa2cbdf46`)
- `/opt/ai/logs/util-act-baseline-counters-20261031T1948Z.ncu-rep`
  (sha256 `2e64c4bfea047adcabfa150b1df41aaf319389f6d1dc27f3325920a27c38a87d`)
- `/opt/ai/logs/util-act-candidate-counters-20261031T1948Z.ncu-rep`
  (sha256 `f8791940d1fb4aba2b90fe3f96a214310964cf0c4038f68e6d5d65afd46a71d6`)
- `/opt/ai/logs/util-act-restore-build-20261031T1952Z.log`
  (sha256 `f49af4efc7e428fd27aa5b3d3272f41d7b81b3f80b066da42c8806396b3d9ff1`)
- `/opt/ai/logs/util-act-restored-server-20261031T1955Z.log`
  (sha256 `24a0ff19a82cf4834448d88b9253f5ac7a899e0e10354dd7a309fdbac66dba04`)
- `/opt/ai/tmp/pre-util-act-20261031T1942Z/`

## Not measured

This experiment covers one real GGUF layer and mode-8 geometry. It does not argue that all MIO
pressure is compiler-optimal. The next candidate must make the weight decoder reuse explicit:
the current kernel repeats weight-side codebook lookups for every token in an NTC chunk.
