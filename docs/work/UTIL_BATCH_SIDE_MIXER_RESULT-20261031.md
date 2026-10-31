# UTIL-BATCH-SIDE-MIXER result

**Status: server gate blocked. Not adopted and not rejected.**

The default-off candidate has passed its production-shaped GPU screen, exactness probes,
and a real two-row production-path hit check. The required same-boot A1-B-A2 throughput
matrix has not run because GPU runtime state was lost after a baseline A1 failure. The
server remains stopped until the host is rebooted and CUDA reports both devices again.

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

## A1 failure and current block

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

The host uptime is still from 2026-10-29. Non-interactive sudo is unavailable, so the
agent cannot safely reboot the machine. `strata.service` is `inactive/dead`,
`MainPID=0`, `NRestarts=23`, and no server or engine process remains. The reverse SSH
service is enabled and its tunnel remains reachable.

This is a baseline/infrastructure failure, not evidence for or against the candidate.
No 6-way or 8-way pair exists for either arm, and no A1-B-A2 performance conclusion can
be drawn.

## Required continuation

1. Reboot the host with the user's existing privileged session.
2. Stop `strata.service` immediately after boot so it cannot occupy the GPUs.
3. Confirm `cuDeviceGetCount == 2`, both UUIDs initialize together, and no server,
   engine, or benchmark process remains.
4. Reload the same A1 config and rerun frozen parity.
5. Run the full 2/4/6/8-stream, three-wave, 400-token A1-B-A2 matrix with one same-boot
   session and restore A immediately after B.
6. Apply the contract's 1%, 5/6 paired-win, 1% non-regression, 3% drift, request,
   finish, foreign-request, six-slot, health, RAM, VRAM, and swap gates.

Only a full pass may enable the candidate by default. A failure must remove the env and
restore A.
