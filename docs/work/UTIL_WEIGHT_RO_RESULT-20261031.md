# UTIL-WEIGHT-RO result

Run: 2026-10-31 on the candidate snapshot, one idle RTX 2080 Ti for the microkernel, CUDA 13.0,
compute capability 7.5.

## Decision

**UTIL-WEIGHT-RO is rejected by the pre-registered throughput gate.** Explicit read-only loads
changed the intended machine instructions and kept the frozen result bitwise exact, but the
seven-pair gate/up median improved only from `472.6` to `471.0 us` (`0.339%`). The candidate won
6/7 pairs and its p75 was below the baseline median, so the direction is positive, but the gain is
below the required `1.0%`.

No NCU pass or engine A/B was run after the time gate failed. The result does not justify retaining
the extra template path and seven extra registers for a sub-one-percent kernel effect.

## SASS path proof

The baseline fused kernel used 56 `LD.E.U16.SYS` instructions, 73 registers and no stack frame.
The candidate kernel emitted 48 `LDG.E.U16.CONSTANT.SYS` instructions, 80 registers and no stack
frame. The candidate's principal IQ3 weight loads therefore did enter the read-only path.

An earlier version also routed the IQ3 scale through `__ldg`; it compiled with an eight-byte stack
frame and `STL/LDL` traffic. The final measured candidate kept the scale load on its original path
and used a separate three-block launch-bound wrapper, which removed the stack frame. Because the
time gate failed, this implementation difference does not affect the final decision.

## Frozen workload

```text
native_expert_bench SHARD1 36 64 6 8 8 1
```

The exact IQ3_XXS shard 1 used by the candidate server ran at layer 36 with 64 expert blobs and six
entries per group. Both GPUs were idle. Every baseline and candidate process printed hash
`9a055a5a94e15c87` and `0 differing`.

The preflight process immediately after shutdown reported a cold baseline `665.4 us` and candidate
`470.8 us`; that pair was used only to confirm execution and was not used for the decision.

## Seven paired process runs

| Pair | Baseline gate/up us | Candidate gate/up us | Candidate |
| ---: | ---: | ---: | :---: |
| 1 | 665.6 | 583.7 | win |
| 2 | 472.3 | 471.0 | win |
| 3 | 472.7 | 471.0 | win |
| 4 | 470.9 | 471.8 | lose |
| 5 | 473.1 | 469.0 | win |
| 6 | 471.5 | 470.9 | win |
| 7 | 472.6 | 470.5 | win |

The pre-registered median gives baseline `472.6 us`, candidate `471.0 us`, improvement `0.339%`,
candidate wins `6/7`, and candidate p75 `471.8 us`. Excluding the first cold-clock pair gives
approximately `472.45` and `470.95 us`, or `0.317%`, so the sub-one-percent result is stable.

## Restore and validation

The pre-experiment backup at `/opt/ai/tmp/pre-util-weight-ro-20261030T1845Z/` was restored. The
source hash returned to
`47009da533393ff4bcb62e6831427204237da3a1f9679ad4a691d4ddc897971b`; the engine stayed at
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`.

- `native_expert_parity --synthetic iq3_xxs/q2_0`: 0 failures;
- `iq_multi_parity`: 0 failures, all grouped cases bitwise equal;
- restored benchmark remained SHA-256
  `8255d36ecc4f638c570f8e439024df45c908b582a420b7eba8a4bc7f49bf760a`.

The candidate was stopped once. Normal shutdown took 8 seconds; restart to loaded health took
162 seconds, so total interruption was about `2:50`.

Final candidate state:

- server PID `514867`, engine PID `514949`, vision PID `514871`;
- engine health 200 with `loaded=true`;
- engine environment has `CUDA_VISIBLE_DEVICES=0,1` and no `STRATA_IQ3_*`,
  `STRATA_PROFILE_*` or `STRATA_NCU_*`;
- config SHA-256 `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f`;
- production `strata.service` remains `inactive/dead`, `MainPID=0`, `NRestarts=23`.

## Evidence

- `/opt/ai/logs/util-weight-ro-sass-20261031T2048Z.txt`
  (sha256 `c79e7e7fdaa55fbe4e26ca9bfc41128557b65e71918c4cba4e16346cfa78ff36`)
- `/opt/ai/logs/util-weight-ro-ab-20261031T2050Z.log`
  (sha256 `0e7fe82e12904a0826d462499d38252ae78851f7e542763fc45e05e9e957c20f`)
- `/opt/ai/logs/qsa-diag-20261031T1653Z/restore-util-weight-ro.out`
  (sha256 `19b2089f8cea8775938040531b9db548c16f06182081919032f7279e731c11d2`)
- `/opt/ai/logs/qsa-diag-20261031T1653Z/restore-util-weight-ro.ready`
  (sha256 `ea1860c4de8c47778dab35331883e69317ae0d7a588fb1e131d740690f00855d`)
- `/opt/ai/tmp/pre-util-weight-ro-20261030T1845Z/`

## Not measured

This covers one real GGUF layer and the mode-8 fused gate/up geometry. No NCU, engine A/B,
concurrency or natural-language workload was run. The result rejects this read-only load mechanism
as a standalone improvement; the broader long-scoreboard hypothesis remains unproven because the
necessary 1% process gate was not reached.
