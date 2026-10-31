# UTIL-SIDE-MIXER result

## Decision

**Reject the existing `STRATA_SIDE_MIXER=2` env-only candidate before the server gate.**

The isolated qkv+gates and QSA q/k/v overlap is byte-identical and saves enough time at row
widths three and four to justify a new batch-window implementation. It cannot improve the
current production instance: every production decode window is captured through
`Verifier::capture_batch()`, which sets `batch_rec_=true`; both `side_proj` and `sidem` include
`!batch_rec_`. The measured overlap therefore runs zero times at two, four, six or eight
external streams. Enabling the variable would only create unused streams and events.

No server candidate was built and Strata was not restarted.

## Exactness

`mixer_overlap_bench` used the production tensor types and shapes:

```text
GDN qkv  IQ4_XS 2560 -> 10240
GDN gate IQ3_S  2560 ->  6144
QSA q    IQ4_XS 2560 -> 12288
QSA k    IQ4_XS 2560 ->   512
QSA v    Q6_K   2560 ->   512
```

Seven independent processes ran on each physical GPU and covered `T=1..8`. Every one of their
196 result lines reported equality for all outputs: qkv and gate for SIDEMIX, and q, k and v
for QSAMIX. That is 560 device-buffer comparisons with no mismatch. All 14 processes exited
successfully and no CUDA error was printed. The two one-shot full runs also passed.

`CUDA_VISIBLE_DEVICES=1` renumbers the physical second card to logical device zero, so the raw
line `gpu=0` in the `gpu-visible1-*` logs does not mean both sweeps used physical GPU 0.

## Isolated timing

Each cell is the median save of seven processes; each process reports the median of five
event-timed batches of 100 iterations.

### Physical GPU 0

| T | SIDEMIX save us | SIDEMIX pct | QSAMIX save us | QSAMIX pct |
| ---: | ---: | ---: | ---: | ---: |
| 1 | `-6.6826` | `-10.6593%` | `+0.7347` | `+1.6570%` |
| 2 | `+8.5610` | `+6.4267%` | `+6.4611` | `+7.6044%` |
| 3 | `+9.5693` | `+5.5069%` | `+9.5658` | `+8.5667%` |
| 4 | `+8.2282` | `+3.8921%` | `+8.9178` | `+6.8423%` |
| 5 | `+7.1859` | `+2.8379%` | `+8.4339` | `+5.7364%` |
| 6 | `+8.9680` | `+3.0448%` | `+7.5514` | `+4.5657%` |
| 7 | `+7.4845` | `+2.2255%` | `+7.0080` | `+3.8665%` |
| 8 | `+8.5981` | `+2.2957%` | `+8.0662` | `+4.0605%` |

### Physical GPU 1

| T | SIDEMIX save us | SIDEMIX pct | QSAMIX save us | QSAMIX pct |
| ---: | ---: | ---: | ---: | ---: |
| 1 | `-3.6218` | `-5.8438%` | `-0.0227` | `-0.0517%` |
| 2 | `+7.2710` | `+5.5070%` | `+6.3248` | `+7.4409%` |
| 3 | `+10.2288` | `+5.7734%` | `+8.4736` | `+7.5327%` |
| 4 | `+8.4474` | `+3.9534%` | `+10.4794` | `+7.8058%` |
| 5 | `+7.8077` | `+3.0416%` | `+8.9674` | `+6.0270%` |
| 6 | `+7.5584` | `+2.5742%` | `+7.5303` | `+4.5574%` |
| 7 | `+9.8480` | `+2.9419%` | `+8.5344` | `+4.6520%` |
| 8 | `+7.5149` | `+1.9892%` | `+8.0979` | `+4.0447%` |

The branch loses badly for one row in SIDEMIX. A future batch implementation must not enable it
for one-row windows.

## Whole-window ceiling

One twenty-four-layer stage has about 18 GDN and 6 QSA layers. Multiplying each measured save
by those counts gives an optimistic ceiling that assumes every operation is on the critical path
and ignores overlap between the two cards:

| T | GPU 0 us | GPU 0 pct | GPU 1 us | GPU 1 pct |
| ---: | ---: | ---: | ---: | ---: |
| 1 | `-115.9` | `-0.634%` | `-65.3` | `-0.358%` |
| 2 | `192.9` | `+1.056%` | `168.8` | `+0.924%` |
| 3 | `229.6` | `+1.257%` | `235.0` | `+1.286%` |
| 4 | `201.6` | `+1.104%` | `214.9` | `+1.176%` |

The denominator is the fastest measured `18.27 ms` stage. T=3 and T=4 clear the user's 1%
threshold on both cards. Production six and eight stream waves normally form groups of three
or four rows, so these are the relevant widths for a new implementation. T=2 is below 1% on
GPU 1 and T=1 regresses.

These numbers are a ceiling, not a server result. CUDA graph events, stream scheduling, overlap
with other work and pipeline-stage imbalance can remove the saving.

## Production reach

The dispatch chain is:

```text
batch_step
  -> run_slot_rows
  -> stage_batch
  -> capture_batch
  -> batch_rec_ = true
  -> record_window
```

In `record_window`:

```cpp
const bool side_proj = side_mixer_mode >= 2 && !batch_rec_;
const bool sidem = side_mixer && !batch_rec_;
```

That condition is the reason this experiment stops before a restart. A server A-B-A of the
existing switch would compare two identical numeric paths while adding idle resources.

## State and cost

The A2 instance remained running throughout:

| item | value |
| --- | --- |
| health | HTTP 200, `loaded=true` |
| server / engine / vision | `604897 / 604926 / 604905` |
| engine sha256 | `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3` |
| config sha256 | `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f` |
| shapes | six slots, two groups, 262144 context |
| systemd | `inactive/dead`, `MainPID=0`, `NRestarts=23` |
| RAM | 49 GiB used, about 12.7 GiB available |
| swap | `12.3 MiB` used of 4 GiB |
| GPUs | `21198 / 21098 MiB` used, both idle |

The running environment has `STRATA_SM75_PERF=1` and none of
`STRATA_SIDE_MIXER`, `STRATA_VERIFY_PROFILE` or `STRATA_BATCH_TELEMETRY`.

No API key, token or private prompt was written to the repository or evidence.

## Evidence

Remote directory:

```text
/opt/ai/logs/util-side-mixer-screen-20261031T0240UTC/
```

| artifact | sha256 |
| --- | --- |
| harness binary | `78e62c8e364665e660e5a67ad9de56ab044ef4516feeed6c9102c5359256c55d` |
| harness source | `71ba7fb82537984a99214b9119510a2c427fed9002bc9ec2d2b3bea81e0643e2` |
| one-shot physical GPU 0 | `2ab3363c1146d1a6a52bf1b06a128bbd9a4a7a14067b945819b62905490ecf28` |
| one-shot physical GPU 1 | `449f460a996cdd6627015ccea1924310df67975d33d173046cdf3cf08b6b234c` |
| `all-sha256.txt` | `5e45f383c0eb07560efe6adf4ff018d7dd245ef1cb8917f1cd81638688022183` |

`all-sha256.txt` contains the binary, source, object, both one-shot logs and all fourteen
seven-process logs.

## Next

Open a different candidate that is reachable from `capture_batch()`: a narrow batch-window
side branch for the GDN qkv/gate pair only, gated to three or four rows. It must preserve graph
capture, slot-local state, row order and outputs, and should leave one-row and two-row windows
on the existing serial path. Only after exactness and a batch-window harness reproduce at least
`0.183 ms` may an env-only A1-B-A2 server test be considered.
