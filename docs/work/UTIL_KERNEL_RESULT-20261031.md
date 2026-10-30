# UTIL-KERNEL result

Run: 2026-10-31 on the candidate snapshot, one idle RTX 2080 Ti for the microkernel, CUDA 13.0
profiling tools, compute capability 7.5.

## Decision

**UTIL-KERNEL is rejected.** The four-copy, bank-rotated IQ3_XXS codebook kept the frozen
benchmark bitwise exact, but it did not improve gate/up time. The candidate median was
`474.5 us` against a `474.1 us` baseline median, a `0.084%` regression, with only 2 of 7 paired
runs favoring the candidate. The bounded NCU check was run only to explain the rejection:
bank-conflict wavefronts rose from `7,115,366` to `7,293,965`, and registers rose from 73 to 80.

The mechanism is closed. Replica counts 2, 8 and 16 will not be scanned. The next candidate must
target the repeated shared-memory activation reads discovered in the counter follow-up, not more
codebook replication.

## Frozen workload

```text
native_expert_bench SHARD1 36 64 6 8 8 1
```

The exact model file was the IQ3_XXS shard 1 already used by the candidate server. Each process
used `CUDA_VISIBLE_DEVICES=0` while both GPUs were otherwise idle. The output hash was
`9a055a5a94e15c87` in every baseline and candidate run, and both reported `0 differing`.

## Seven paired process runs

| Pair | Baseline gate/up us | Candidate gate/up us | Candidate |
| ---: | ---: | ---: | :---: |
| 1 | 664.1 | 472.4 | win |
| 2 | 474.1 | 471.7 | win |
| 3 | 471.3 | 474.5 | lose |
| 4 | 471.8 | 475.1 | lose |
| 5 | 473.3 | 473.8 | lose |
| 6 | 475.1 | 475.6 | lose |
| 7 | 475.5 | 476.3 | lose |

The first baseline process contains a cold-clock outlier. The pre-registered median still applies:
baseline `474.1 us`, candidate `474.5 us`, improvement `-0.084%`, candidate p75 `475.35 us`
above the baseline median, and candidate wins `2/7`. Every keep condition beyond output equality
therefore failed.

## Counter follow-up

The one-launch metric pass used the same candidate binary and kernel geometry as UTIL-NCU.
Absolute replay duration was not used as a performance sample.

| Counter | One-copy baseline | Four-copy candidate |
| --- | ---: | ---: |
| Kernel replay duration | 665.632 us | 666.944 us |
| Registers/thread | 73 | 80 |
| Static shared memory/block | 2,048 B | 5,120 B |
| Shared-load instructions | 10,583,040 | 10,583,040 |
| Shared-load wavefronts | 17,698,406 | 17,877,005 |
| Shared-load bank conflicts | 7,115,366 | 7,293,965 |
| Wavefronts per load instruction | 1.672 | 1.689 |

The replicated layout made bank behavior slightly worse while adding three copies of staging work,
4 KiB of shared memory and seven registers. On this random codebook distribution, the original
256-entry layout is already close to the practical bank-conflict floor; replication changes the
physical bank permutation but not the number of random simultaneous addresses.

The same pass measured `10,583,040` shared-load instructions versus `296,960` global-load
instructions. The next mechanism should therefore remove duplicate shared-memory activation loads:
the fused kernel currently calls the dot routine separately for two weight rows, and each call
reloads the same q8_1 activation words and scale.

## Restore and validation

The original source file was restored byte-for-byte to sha256
`47009da533393ff4bcb62e6831427204237da3a1f9679ad4a691d4ddc897971b`, and the changed
bench/parity targets were rebuilt. The restored engine remained sha256
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`.

- `native_expert_parity --synthetic iq3_xxs/q2_0`: 0 failures;
- `iq_multi_parity`: 0 failures, all grouped cases bitwise equal;
- restored `native_expert_bench`: hash `9a055a5a94e15c87`, 0 differing.

The candidate service was stopped for the isolated kernels and restarted once from the restored
engine/config after validation. The cold restart costs about 2.5–3 minutes; no further restart was
needed for the rejected kernel. The measured cold start was `2:45`. Final state is server/engine
PID `507164/507178`, health 200 with `loaded=true`, no experiment environment, and production
`strata.service` remains `inactive/dead`, `MainPID=0`, `NRestarts=23`.

## Evidence

- `/opt/ai/logs/util-kernel-build-20261031T1912Z.log`
  (sha256 `56b6782bbee0ada9823cfc9ee8c5757ae45c38bef353242fdf03af5b432a71ac`)
- `/opt/ai/logs/util-kernel-ab-20261031T1914Z.log`
  (sha256 `fe4bd0dd9b77af1046eedf83beafc008faf885af586305a10fb86b804c5e9536`)
- `/opt/ai/logs/util-lds-counters-20261031T1918Z.ncu-rep`
  (sha256 `12814ae612e7140959e084cd3024654ec6f203ecbb79b3376dfc8c19cededd20`)
- `/opt/ai/logs/util-lds-counters-20261031T1918Z.csv`
  (sha256 `bf174f3bd53b8e94075f30cb12691707c1a421c8a5c526e8b59dd024472cae03`)
- `/opt/ai/logs/util-lds-repl-counters-20261031T1921Z.ncu-rep`
  (sha256 `873517088c55436c931235aab6c64d5dd212b0b91139a6b38ee030ad86d71e3e`)
- `/opt/ai/logs/util-lds-repl-counters-20261031T1921Z.csv`
  (sha256 `de6f2c7133863cbcf0304b540a58f7f19d8dad66bdca295e91260d4b0a59a1c6`)
- `/opt/ai/logs/util-kernel-restore-build-20261031T1925Z.log`
  (sha256 `669bde5b249538e46e4281cabc3e53ea1abff10bcb7606fdce73b9b34150601f`)
- `/opt/ai/logs/util-kernel-restored-server-20261031T1928Z.log`
  (sha256 `19b2089f8cea8775938040531b9db548c16f06182081919032f7279e731c11d2`)
- `/opt/ai/tmp/pre-util-kernel-20261031T1910Z/`

## Not measured

This result covers one real GGUF layer and the mode-8 `G=64, T=6` geometry. It rejects the
four-copy bank-rotation mechanism, not all shared-memory optimization. An activation-reload
candidate still needs its own output, time, counter, parity and end-to-end gates.
