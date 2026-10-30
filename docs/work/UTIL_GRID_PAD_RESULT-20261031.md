# UTIL-GRID-PAD result

Run: 2026-10-31 on the candidate snapshot, one idle RTX 2080 Ti for the microkernel, CUDA 13.0,
compute capability 7.5.

## Decision

**UTIL-GRID-PAD is rejected by the pre-registered throughput gate.** The four-copy codebook with
`replica * 257 + index` made every replica span all 32 shared-memory banks and kept the frozen
output bitwise exact, but it made the fused gate/up kernel slower. Baseline and candidate medians
were `475.7` and `489.3 us`, a `2.86%` regression; the candidate won only 3/7 pairs.

The negative result remains significant after removing pair 1, whose baseline contained the usual
cold-clock outlier: the remaining six-pair medians were approximately `483.1` and `488.85 us`, so
the candidate was still about `1.2%` slower. No NCU pass or engine A/B was run because the time
gate had already failed.

The mechanism is closed. Do not scan replica counts or nearby strides. The original problem remains
the codebook's random shared-memory access, but this address-rotation strategy adds enough address
and staging cost to overwhelm any conflict reduction.

## Frozen workload

```text
native_expert_bench SHARD1 36 64 6 8 8 1
```

The exact IQ3_XXS shard 1 used by the candidate server ran at layer 36 with 64 expert blobs and six
entries per group. Both GPUs were idle. Every baseline and candidate process printed:

```text
hash 9a055a5a94e15c87
native_expert_bench: 0 differing
```

The preflight one-run check reported baseline `663.6 us` immediately after the GPU became idle and
candidate `485.2 us`. That cold-clock baseline was not used as a conclusion.

## Seven paired process runs

| Pair | Baseline gate/up us | Candidate gate/up us | Candidate |
| ---: | ---: | ---: | :---: |
| 1 | 663.6 | 485.4 | win |
| 2 | 471.0 | 485.6 | lose |
| 3 | 471.2 | 496.5 | lose |
| 4 | 490.9 | 488.4 | win |
| 5 | 557.1 | 489.3 | win |
| 6 | 475.3 | 489.4 | lose |
| 7 | 475.7 | 489.5 | lose |

The candidate lost three pairs by roughly `14–25 us`; its three wins include two unusually slow
baseline processes. The pre-registered median therefore gives baseline `475.7 us`, candidate
`489.3 us`, improvement `-2.86%`, candidate wins `3/7`, and candidate p75 `489.5 us` above the
baseline median. Every keep condition beyond output equality failed.

## Implementation checked

The experiment added an opt-in `STRATA_IQ3_GRID_PAD` path only to
`native_gu_fused_kernel<18>`. It staged the 256 IQ3_XXS codebook words into four copies with a
257-word stride, selected `lane & 3`, and otherwise kept indices, signs, dp4a, scales, row sums,
SwiGLU, q8_1 quantization and stores unchanged. The shared array was sized for its maximum live
index (`3 * 257 + 255 = 1026`) before compilation.

The experiment source SHA-256 was
`a5ccc574a7c60c4f79e8b961c18b1dbb381d10113a242f0a9510180e1cf00355`.
The rebuilt benchmark, expert parity and multi-format parity binaries had SHA-256 values
`b9565aa8fa9ef71827bbb52fa134e42f3d87f257f71c28cf79f3712fd1be97c8`,
`6e492909836586d95dea693a3729328b378f7d32c460e6ec138666a6bb239fcb`, and
`11bd877b41e668e13b67e40602c324af4215b1d66026b2adf33c936cca6b5fde`.

## Restore and validation

The pre-experiment backup at
`/opt/ai/tmp/pre-util-grid-pad-20261031T2032Z/` was restored. The source hash returned to
`47009da533393ff4bcb62e6831427204237da3a1f9679ad4a691d4ddc897971b`; the engine stayed at
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`.

- `native_expert_parity --synthetic iq3_xxs/q2_0`: 0 failures;
- `iq_multi_parity`: 0 failures, all grouped cases bitwise equal;
- restored benchmark: hash `9a055a5a94e15c87`, 0 differing.

The candidate was stopped once for the isolated measurements. Normal shutdown took 12 seconds;
after restoring the baseline, the measured restart to loaded health took 161 seconds. Total
service interruption was therefore about `2:53`. Production `strata.service` remained
`inactive/dead`, `MainPID=0`, `NRestarts=23`.

Final candidate state:

- server PID `511649`, engine PID `511741`;
- engine health 200 with `loaded=true`;
- no `STRATA_IQ3_*`, `STRATA_PROFILE_*`, or `STRATA_NCU_*` in the engine environment;
- the restored dual-GPU engine has the expected `CUDA_VISIBLE_DEVICES=0,1`;
- config SHA-256 `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f`.

## Evidence

- Remote restore log:
  `/opt/ai/logs/qsa-diag-20261031T1653Z/restore-util-grid-pad.out`
  (sha256 `108509f3fab8d0c44b29ad64298cef042a3872431920e5bf4af8e776faf6f1dd`)
- Loaded-health record:
  `/opt/ai/logs/qsa-diag-20261031T1653Z/restore-util-grid-pad.ready`
  (sha256 `ea1860c4de8c47778dab35331883e69317ae0d7a588fb1e131d740690f00855d`)
- Baseline candidate config:
  `/opt/ai/logs/qsa-diag-20261031T1653Z/a-baseline.json`
  (sha256 `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f`)
- Pre-experiment source and binary backup:
  `/opt/ai/tmp/pre-util-grid-pad-20261031T2032Z/`

The seven-pair numbers above are the measured stdout from the interleaved remote process command.
They were not inferred from the preflight run or copied from an earlier experiment.

## Not measured

This covers one real GGUF layer and the mode-8 fused gate/up geometry. It rejects this padded
four-copy address layout, not every possible way to reduce random codebook latency. A new
mechanism needs new counter evidence and its own contract.
