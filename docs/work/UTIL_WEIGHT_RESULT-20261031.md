# UTIL-WEIGHT result

Run: 2026-10-31 on the candidate snapshot, one idle RTX 2080 Ti for the microkernel, CUDA 13.0
profiling tools, compute capability 7.5.

## Decision

**UTIL-WEIGHT is rejected by the pre-registered throughput gate.** The explicit weight-decode
working set kept outputs bitwise exact and showed a directionally positive result, but the gain
was only `0.546%`: baseline gate/up median `475.8 us`, candidate `473.2 us`. The candidate won
6/7 pairs and its p75 was below the baseline median, but the required `1.0%` improvement was not
reached.

The bounded NCU follow-up explains why the source rearrangement had little effect. Baseline and
candidate both executed exactly `10,583,040` shared-load instructions and `296,960` global-load
instructions. Candidate registers rose from 73 to 80 while wavefronts and bank conflicts stayed
effectively unchanged. The compiler had already hoisted both the activation and weight-side work
needed by the two row dots; the source-level working set did not remove machine instructions.

The time difference is retained as a rejected result, not an accepted 0.5% optimization. The
contract requires 1%; declaring this change would also increase register pressure and code
complexity for a result not distinguishable from replay noise.

## Frozen workload

```text
native_expert_bench SHARD1 36 64 6 8 8 1
```

The exact IQ3_XXS shard 1 used by the candidate server, layer 36, 64 expert blobs and 6 entries
per group. Each process used `CUDA_VISIBLE_DEVICES=0` while both GPUs were otherwise idle. All
baseline and candidate processes printed hash `9a055a5a94e15c87` and `0 differing`.

## Seven paired process runs

| Pair | Baseline gate/up us | Candidate gate/up us | Candidate |
| ---: | ---: | ---: | :---: |
| 1 | 663.6 | 470.6 | win |
| 2 | 473.1 | 474.0 | lose |
| 3 | 474.5 | 473.2 | win |
| 4 | 476.5 | 473.7 | win |
| 5 | 475.0 | 473.2 | win |
| 6 | 475.8 | 472.8 | win |
| 7 | 476.6 | 475.3 | win |

The first baseline process contains the repeatable cold-clock outlier. Median improvement is
`0.546%`, candidate wins `6/7`, candidate p75 `473.85 us`, and baseline median `475.8 us`.

## Counter follow-up

| Counter | Baseline | Candidate |
| --- | ---: | ---: |
| Kernel replay duration | 666.720 us | 663.648 us |
| Registers/thread | 73 | 80 |
| Shared-load instructions | 10,583,040 | 10,583,040 |
| Global-load instructions | 296,960 | 296,960 |
| Shared-load wavefronts | 17,698,290 | 17,698,304 |
| Shared-load bank conflicts | 7,115,250 | 7,115,264 |
| MIO-throttle warps/issue-active | 3.872857 | 4.069270 |
| Active warps | 71.618825% | 71.551537% |

The unchanged load instruction counts show that the compiler already performs the intended
weight-side common-subexpression elimination. The candidate only changes register allocation and
schedule, which does not support a stable speed claim.

## Restore and validation

The original source hash `47009da533...` and engine hash `105a4171...` were restored. Both changed
targets were rebuilt, then:

- `native_expert_parity --synthetic iq3_xxs/q2_0`: 0 failures;
- `iq_multi_parity`: 0 failures, all grouped cases bitwise equal;
- restored `native_expert_bench`: hash `9a055a5a94e15c87`, 0 differing.

The candidate was stopped once for the isolated kernels and restarted once after restore. No
engine A/B was run. The measured cold start was `2:50`. Final state is server/engine PID
`510062/510127`, vision PID `510067`, health 200 with `loaded=true`, no experiment environment,
and production `strata.service` remains `inactive/dead`, `MainPID=0`, `NRestarts=23`.

## Evidence

- `/opt/ai/logs/util-weight-build-20261031T2007Z.log`
  (sha256 `15fe6fdfdb2fa3e34d759d131adcf3109ee807ebcd0e536e01d350451acb7834`)
- `/opt/ai/logs/util-weight-ab-20261031T2010Z.log`
  (sha256 `a946e50ea9a9ee5a02bb1b0a871c5dc691ee42864317e904c98021b799935918`)
- `/opt/ai/logs/util-weight-baseline-counters-20261031T2013Z.ncu-rep`
  (sha256 `d85080d26151dd077c823fa245e4f65dbfcd5093c3606fa014ce81bcb87ab959`)
- `/opt/ai/logs/util-weight-candidate-counters-20261031T2013Z.ncu-rep`
  (sha256 `56cef7a0d6ffc66dd7cf832893c4c71f409e2b7d43edae5859f06228c2876e84`)
- `/opt/ai/logs/util-weight-restore-build-20261031T2018Z.log`
  (sha256 `669bde5b249538e46e4281cabc3e53ea1abff10bcb7606fdce73b9b34150601f`)
- `/opt/ai/logs/util-weight-restored-server-20261031T2020Z.log`
  (sha256 `19b2089f8cea8775938040531b9db548c16f06182081919032f7279e731c11d2`)
- `/opt/ai/tmp/pre-util-weight-20261031T2007Z/`

## Not measured

One real GGUF layer and mode-8 geometry. The result rejects this source-level weight working set
and does not prove that codebook bank behavior is optimal. The earlier four-copy layout exposed
only eight banks per replica because its address was `4 * index + replica`; a padded replica
layout can expose all 32 banks and must be tested as a separate mechanism.
