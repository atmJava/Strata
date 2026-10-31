# UTIL-BATCH-SIDE-MIXER result

**Status: rejected at the server throughput and drift gates. Not adopted.**

The default-off candidate passed its production-shaped GPU screen, exactness probes, a real
two-row production-path hit check, and a fresh post-reboot exactness check. The completed
same-boot A1-B-A2 matrix then rejected it: two and four streams regressed by more than 1%,
six streams did not improve by 1%, and the two-stream A1-to-A2 drift exceeded 3%. A2 is
restored from the original baseline engine and config.

## Candidate

`STRATA_BATCH_SIDE_MIXER=1` enables one narrow branch in `record_window`:

- only for `batch_rec_`, `T == 2`, and with profiling off;
- GDN qkv stays on the main stream while the gate projection runs on the side stream;
- QSA q and its split, norm, RoPE, and rotation stay on the side stream while K/V stay on
  the main stream;
- joins happen before recurrence reads GDN `z` and before attention reads QSA queries;
- one-row and three-to-eight-row windows keep the original serial path.

The candidate source is committed with the benchmark so this checkpoint is reproducible.
It is not enabled in production config.

## Production-shaped GPU screen

The corrected harness models 18 GDN-only layers, 6 QSA-only layers, and the actual
24-layer GDN/GDN/GDN/QSA order. Both cards ran seven independent processes at `T=2`.
Every one exited successfully, every direct and captured-graph output was byte-identical,
and no CUDA or graph error occurred.

| GPU | 24-layer median saving | Relative to 18.27 ms stage | Wins | Above 182.7 us |
| ---: | ---: | ---: | ---: | ---: |
| 0 | `177.0208 us` | `0.9689%` | 7/7 | 7/7 |
| 1 | `191.7025 us` | `1.0493%` | 7/7 | 7/7 |

Component medians were `120.4607 us` for GPU0's 18-GDN graph and `36.1760 us` for its
6-QSA graph; GPU1 measured `120.8416 us` and `33.0240 us`. Both components won 7/7 and
were byte-exact on both cards. GPU0's 24-layer value is just below 1% of the stage proxy,
while GPU1 is just above it; this is enough to justify the server matrix, not enough to
claim end-to-end adoption.

Evidence:

- `/opt/ai/logs/util-batch-side-mixer-prodshape-20261031T0225UTC/`
- binary sha256 `6e9c92e7d3bdf3f4015ae154309bd12d719aeb51b03b8ed4b9d26c8655bbe027`
- manifest sha256 `7140ae4f977c7e6a70192cfc2420d371802ad4f72fe54d4c9c6337a0c3bbe684`

## Candidate B exactness and production reach

Candidate B started manually with six slots, two groups, health loaded, and
`STRATA_BATCH_SIDE_MIXER=1`. Three frozen greedy probes ran twice. All six responses were
byte-identical to the expected frozen text and ended with `finish=stop`.

A two-slot request made both slots decode together. `/metrics` reported `busy=2,
decoding=2`, and the batch log showed two captured two-row windows over slots `0,1`.
This proves the production `batch_rec_ && T==2` condition was reached rather than relying
on the isolated benchmark.

Evidence:

- `/opt/ai/logs/util-batch-side-mixer2-candidate-20261031T0300UTC/`
- candidate engine sha256 `49e1e84cf805ea72b949c08c436575d089fb7b8a6073eff1ec9399ecf9c540ed`
- B config sha256 `c0e3bc9be6145cb36b4f46bfd72c23dc028cbd436f29cf0d7b9c30f79b20d0cb`
- B parity sha256 `55c1f02225d57a9abb1f1601b02679b48004bad6558ccf92c45dd43c4cdce151`
- manifest sha256 `b0fa4752742ff6f2fb14d36e1e6f06b7c62f08e60a0bc4b654762bbacc468f64`

## Same-boot A1-B-A2 throughput

The host was rebooted. A1, B and A2 each ran three 400-token waves at 2/4/6/8 streams in
one boot. Rates are aggregate output tokens per second. Paired wins compare each B
repetition with the same A1 and A2 repetition, giving six comparisons per level.

| streams | A1 | B | A2 | A1/A2 combined | B delta | paired wins | A drift |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 76.809 | 71.840 | 71.381 | 73.8755 | **-2.755%** | 4/6 | **-7.067%** |
| 4 | 115.342 | 107.790 | 115.882 | 115.612 | **-6.766%** | 1/6 | +0.468% |
| 6 | 125.511 | 127.629 | 128.785 | 127.148 | +0.378% | 2/6 | +2.609% |
| 8 | 114.787 | 120.449 | 111.930 | 114.747 | **+4.969%** | 5/6 | -2.489% |

Only eight streams met both the 1% gain and 5/6 paired-win gates. Two and four streams
regressed by more than 1%, and the two-stream A1-to-A2 drift was -7.067%. The candidate
therefore fails multiple hard gates and is rejected. This is a true end-to-end rejection,
not the earlier infrastructure block.

All 12 waves completed in every arm. There were no script, HTTP or execution errors, no
foreign requests, and no finish reasons other than `length` and `stop`: A1
`length=12, stop=5`; B `length=12, stop=6`; A2 `length=12, stop=8`.

## Initial A1 failure and recovery

A1 loaded successfully with the baseline engine and config:

```text
engine 105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3
config 88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f
```

Its frozen greedy parity passed. The 2-way and 4-way sweeps completed, but the second
6-way wave failed with:

```text
dequant launch: unspecified launch failure
```

The baseline server then exhausted its automatic restart attempts. After the failure,
`nvidia-smi` still listed both RTX 2080 Ti cards at about 1 MiB used, while `cuInit`
succeeded but `cuDeviceGetCount` returned one device. Selecting GPU1 alone still worked;
selecting both UUIDs together failed with `cuInit=100`. A retry of the same A config
failed before loading with `conversation parking does not support a layer split on one
GPU (--split-device 0)`.

Before recovery, the host uptime was still from 2026-10-29. Non-interactive sudo was
unavailable from the agent, so the reboot required the user's privileged session.
At that checkpoint `strata.service` was `inactive/dead`,
`MainPID=0`, `NRestarts=23`, and no server or engine process remains. The reverse SSH
service is enabled and its tunnel remains reachable.

The reboot restored both devices; `cuInit=0` and `cuDeviceGetCount=2`.
`strata.service` was stopped immediately and no old server, engine or benchmark process
remained. The new A1/B/A2 process triplets had the expected engine and config hashes.
Every arm passed the three frozen greedy probes twice, with all six normalized
text/finish tuples byte-identical across A1, B and A2.

The B load took about 185 seconds; B-to-A2 released the port in about 9 seconds and A2
reached loaded health about 181 seconds after launch. A server reboot was not repeated.

## Restored A2

| item | value |
| --- | --- |
| server / engine / vision PID | `7724 / 7744 / 7739` |
| engine sha256 | `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3` |
| config sha256 | `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f` |
| engine environment | no `STRATA_BATCH_SIDE_MIXER` |
| shapes | six slots, groups `0,1`, 262144 context |
| RAM / swap | `13 GiB` available / `0 B` used of `4 GiB` |
| VRAM | GPU0 `330 MiB`, GPU1 `434 MiB` free; both idle |
| production systemd | `inactive/dead`, `MainPID=0`, `NRestarts=0` |

A2 health remained loaded after the full sweep.

## Evidence

Directory:

```text
/opt/ai/logs/util-batch-side-mixer3-postreboot-20261031T1045CST/
```

The directory contains all three sweep and parity files, service logs, launch/health
timestamps, final process and resource observations, both configs, and `all-sha256.txt`.

- A1 sweep `1ec5d13a219850046163394adff3788d8a75d2f6c0e9d7754db31cdcc39a2089`
- B sweep `2a4f80bd7fed683bb0326751a1c38deefffa941718f27fec96f5555aa3c81744`
- A2 sweep `ceb6bc842371250812b9fc29b4dc5a03d65eac11513ca874b332a8cbe1ead1ac`
- A1 parity `d757c7b57f4c78484fa85ca71c38afdc450cde9c8ef2dc6092d6ca602d7e0754`
- B parity `911e3c1ee7eb19eaaa5a99a02e73f490d550a59515a135cfa03392ee97028680`
- A2 parity `8faaf0e0bed875771f1c6816491d42d724b22942613dd62804870139419eddc6`
- `all-sha256.txt` `a5904d25cef5b578a6baa389d9da9c6742715b7e5a0c991d668935fa6630dd01`

## Not measured

No long-context, mixed long-prefill traffic, natural-language quality suite, other model
or non-CUDA backend was run. The rejection applies to the existing T=2 side-stream path
on this two-2080-Ti production shape.
