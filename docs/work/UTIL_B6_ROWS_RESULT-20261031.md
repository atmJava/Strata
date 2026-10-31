# UTIL-B6-ROWS result

## Decision

**UTIL-B6-ROWS is rejected at the kernel screening gate.** The existing exact
`STRATA_B6_MMVQ_ROWS=1` path produced bitwise-identical output, but it did not beat the IL
rows choice selected by the live `STRATA_SM75_PERF=1` production path. At production width
two it lost as a median, and several Q5_K/Q6_K cells regressed by 16-39%.

No source routing change was added. No server candidate was built, no server process was
restarted, and the A3 instance remained unchanged.

## Reach

The reach test compared B6 with the actual IL choice, not with the older exact
`native_mmvq` default. The production environment selects `native_mmvq_il` for two to four
columns when the sm_75 table returns a non-zero rows choice. B6 is reached only through
`native_mmvq`, so enabling its environment variable alone would not replace that production
call.

Duration-weighted pair statistics across the benchmarked supported cells:

| T | median B6 vs selected IL | duration-weighted B6 vs IL | B6 wins |
| ---: | ---: | ---: | ---: |
| 2 | `-5.868%` | `+2.215%` | 0/22 |
| 3 | `0.000%` | `+0.496%` | 7/28 |
| 4 | `+0.586%` | `-1.000%` | 18/28 |

The weighted sum at T=2 is positive only because losses are concentrated in a few expensive
calls; B6 won none of the 22 paired cells. The robust conclusion is that the candidate does
not provide a conservative >=1% improvement at any production width.

Representative regressions:

| case | change |
| --- | ---: |
| GPU1 Q6_K 2560x12288, T=2 | `67.3 -> 93.3 us` (`-38.63%`) |
| GPU1 Q6_K 2560x6144, T=2 | `37.9 -> 48.3 us` (`-27.44%`) |
| GPU0 Q6_K 2560x6144, T=3 | `38.0 -> 52.4 us` (`-37.89%`) |
| GPU1 Q5_K 2560x10240, T=2 | `58.9 -> 68.3 us` (`-15.96%`) |

There were positive cells, for example Q5_K 2560x12288 at T=4 improved from `124.5` to
`116.7 us` (`+6.27%`). They were mixed with the larger regressions and did not make the
candidate safe for a server test.

## Correctness

Parity was complete and exact on both cards:

- `b6_mmvq_bench 500 -1 1`: every case and width matched the default, matched independent
  single-column calls, and B6 matched those calls; no output was non-finite. Final result:
  `b6_mmvq_bench: ok (0 case/width comparisons failed)`.
- `mmvf_rows_parity`: `432 cases, 1660200 values, 0 mismatches` on both cards.
- `mmvq_il_parity --bench` on each card separately: every case, `T=1..8`, explicit rows
  `1/2/4` and the table choice were bitwise equal.

The rejection is therefore about reach and performance, not arithmetic.

## Production-shape projection

The actual GGUF tensor distribution was read and saved in the evidence directory:

```text
gate_cuda0 {'IQ3_S': 7, 'IQ4_XS': 7, 'Q4_K': 2, 'Q6_K': 2}
gate_cuda1 {'IQ3_S': 3, 'IQ4_XS': 4, 'Q4_K': 5, 'Q5_K': 3, 'Q6_K': 3}
q_cuda0 {'IQ4_XS': 4, 'Q4_K': 2}
q_cuda1 {'IQ3_S': 1, 'Q4_K': 1, 'Q6_K': 4}
qkv_cuda0 {'IQ4_XS': 7, 'Q4_K': 5, 'Q5_K': 1, 'Q6_K': 5}
qkv_cuda1 {'IQ4_XS': 4, 'Q4_K': 3, 'Q5_K': 2, 'Q6_K': 9}
head ('Q5_K', 635699200)
```

A duration-weighted proxy over the 15 benchmark calls and these tensor counts projected:

| T | CUDA0 | CUDA1 |
| ---: | ---: | ---: |
| 2 | `-0.240%` | `-1.628%` |
| 3 | `-0.134%` | `-0.426%` |
| 4 | `+0.093%` | `+0.535%` |

This is not a complete production estimate because some real shapes, notably Q4_K
2560x12288, are absent from the IL benchmark. It is additional evidence against the
candidate, not the primary reason for rejection.

## Build and source control

Remote source root:

```text
/opt/ai/src/strata-freeze-throughput-20261029T0812Z
```

Build directory: `build75`.

| file | sha256 |
| --- | --- |
| `src/kernels/b6_mmvq_bench.cpp` | `5363a5842616f2c9eeed1df780b09eadf7dc4c2f1966116a25fa408850c8c60e` |
| `src/kernels/mmvq_il_parity.cpp` | `cd61b11e77f163cc5af3b188e98416e739c9a6efb8ddb5412069da518f044547` |
| `src/kernels/mmvf_rows_parity.cpp` | `1ee1c71a01e306fc619958ea0a9e36517db248afadb30668f3931ce8ec2be225` |
| `src/kernels/cuda/native_mmvq.cu` | `2260db1bd24794596ba4c1dd01a2a754707ffbbef47b648bf38d3922c813fae8` |
| `include/strata/kernels/native_mmvq.hpp` | `0c284577e02c67c09376799c376e1c575c01e01ca42d56891e3b61460301ec92` |

| binary | sha256 |
| --- | --- |
| `b6_mmvq_bench` | `f73354b9c8f1e7ee186623110bb660932c71a2e136c692c6b94a1e564a3d2020` |
| `mmvq_il_parity` | `37cdeb9ca14c7b8c52d46c744b7893a655ee9c9a7be4ca923b35fa3fc2e9a971` |
| `mmvf_rows_parity` | `90c318ee296fce51713219a7bd5fa10221b9981921f3d8f9512761de30a964ae` |

## Server state

No restart was justified by a path that could not replace IL. After the microbench:

- server/engine/vision PID remained `593069/593112/593073`;
- engine sha256 remained
  `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
- config sha256 remained
  `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f`;
- health was `loaded=true` with six slots and groups `0,1`;
- `strata.service` remained inactive/dead, `MainPID=0`, `NRestarts=23`;
- runtime contained `STRATA_SM75_PERF=1` and no `STRATA_B6_MMVQ_ROWS`.

## Evidence

Directory:

```text
/opt/ai/logs/util-b6-rows-20261031T091503CST/
```

| file | sha256 |
| --- | --- |
| `all-sha256.txt` | `24de9051306bb4bc5bc3699bd0a64e37d515f9e6c99710aba6d6b56c75dad6f4` |
| `b6-screen.log` | `1480a8bde7dbee81a74880fecdad6de59b1b0be0627054b92888b417ab16fda4` |
| `mmvq-il-screen.log` | `05a0af332452070a5730e5fe7979f62e9e9441e778cb5876c1b84d8a5846efdb` |
| `mmvq-il-screen-dev1.log` | `de5f3ec64024ddfae90726557d71af2f0c42c88dd2a69ae93dad48a156906c99` |
| `mmvf-rows-dev0.log` | `8fb6137114f17b51ebfe96238192e59eb2e97685dea663e0a4ace0570519dcd4` |
| `mmvf-rows-dev1.log` | `8fb6137114f17b51ebfe96238192e59eb2e97685dea663e0a4ace0570519dcd4` |
| `tensor-types.txt` | `not in the manifest` |

## Not measured

No seven-process confirmation, server build, restart, A-B-A, long prompt, mixed prompt,
natural-language parity or other backend run was made. Those gates were intentionally not
entered because the candidate failed the production-path reach and screening gate.
