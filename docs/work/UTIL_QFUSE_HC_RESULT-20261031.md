# UTIL-QFUSE-HC result

## Decision

**UTIL-QFUSE-HC is rejected at the production projection gate.**

The full path is exact and faster:

```text
A: Q8 HC read -> native_quantize_q8_1
B: Q8 HC read with gr_up_q8_q32_kernel writing its q8_1 image
```

On both cards, every A/B output buffer and q8_1 image is byte-identical for `T=1..8`. The
complete A path is `0.44-0.79 us` slower per read at the production-relevant row widths, which
passes the per-path 1% and 5/7 gates. That local gain is too small to move the whole model
stage by 1%.

The production stage covers 24 layers and two hyper-connection reads per layer, or 48 reads.
The measured saving therefore projects to about `21-38 us` per card stage. The fastest measured
stage-2 window was `18.27 ms`, so one card's best production-width result is only about
`0.11-0.20%`. Summing both cards' savings before allowing any pipeline overlap still gives an
optimistic ceiling below 0.5%. A server build and A1-B-A2 cannot plausibly produce the required
1% gain.

No source opt-in, server binary, config, production service or runtime was changed.

## Exactness

The standalone harness generated identical BF16/Q8 weights and pending state for both arms,
poisoned every output, and compared:

- `R`, `lo`, `rs`, `inject_out` and `mixed`;
- the complete q8_1 image.

Both `CUDA_VISIBLE_DEVICES=0` and `1` printed `values=equal q8=equal` for every `T=1..8`.
The parity log hash is identical on both cards:

```text
76e57ebab70cb4d8c8e26bf821b1864ad239e17dcecb1b568b61ff2c9b8e61a5
```

This closes the gap in the older `gr_parity` QFUSE check, which covered the BF16 up path rather
than the production Q8 up path.

## Full-path timing

Seven alternating A/B processes ran on each card. Each process covered `T=1..8`; each point is
the median of five batches of 200 event-timed iterations. Columns are A/B medians in
microseconds, B gain, and paired wins.

### GPU 0

| T | A us | B us | gain | wins |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 29.653 | 28.860 | **+2.674%** | 7/7 |
| 2 | 36.221 | 35.607 | **+1.695%** | 7/7 |
| 3 | 43.605 | 42.855 | **+1.720%** | 6/7 |
| 4 | 54.575 | 53.968 | **+1.112%** | 7/7 |
| 5 | 64.616 | 64.103 | +0.794% | 6/7 |
| 6 | 75.126 | 74.332 | **+1.057%** | 6/7 |
| 7 | 85.987 | 84.604 | **+1.608%** | 7/7 |
| 8 | 95.159 | 93.860 | **+1.365%** | 7/7 |

### GPU 1

| T | A us | B us | gain | wins |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 29.802 | 29.108 | **+2.329%** | 7/7 |
| 2 | 36.057 | 35.584 | **+1.312%** | 7/7 |
| 3 | 43.889 | 43.203 | **+1.563%** | 6/7 |
| 4 | 55.178 | 54.734 | +0.805% | 6/7 |
| 5 | 65.305 | 64.819 | +0.744% | 7/7 |
| 6 | 74.719 | 74.697 | +0.029% | 5/7 |
| 7 | 84.326 | 83.599 | +0.862% | 7/7 |
| 8 | 94.739 | 94.205 | +0.564% | 7/7 |

Every process produced one value and q8 hash per `T` and arm; each `T` had exactly one hash
per GPU, so all seven A and B processes agreed. There were no CUDA errors.

## Stage projection

At six streams each group has three rows. The measured saving at `T=3` is `0.750 us` on GPU 0
and `0.686 us` on GPU 1. Over 48 reads:

```text
GPU 0: 48 * 0.750 us = 36.0 us = 0.187% of a 19.22 ms stage
GPU 1: 48 * 0.686 us = 32.9 us = 0.171% of a 19.22 ms stage
```

Using the faster `16.08 ms` stage from the profile makes 8-stream `T=4` a more optimistic
non-production estimate, but it still gives only:

```text
GPU 0: 0.152%
GPU 1: 0.111%
```

The production widths at two streams (`T=1`) and four streams (`T=2`) also remain below 0.25%
per card. Even summing both cards without overlap uses only 2.11 us/read at non-production
`T=7`, or 101.3 us over 48 reads: 0.554% of the fastest stage. There is no credible path to 1%.

## State and cost

The A2 server remained running throughout. Final observations:

| item | value |
| --- | --- |
| health | HTTP 200, about 1.2 ms |
| production systemd | `inactive`, unchanged |
| free VRAM | `803 / 904 MiB` |
| RAM | `49 GiB` used, `12 GiB` available |
| swap | `12 MiB` used of `4 GiB` |

No server restart occurred. The screening build used the existing frozen
`build75/libstrata_kernels.a` and only temporary allocations.

## Evidence

Directory:

```text
/opt/ai/logs/util-qfuse-hc-total-20261031T0210UTC/
```

Contents and SHA-256:

```text
qfuse_hc_bench.cpp  2f577777ea673f39975cb1306acb3e844594f91795e4dba66dbdaf35c8759778
qfuse_hc_bench      1cc3830a9095e20facceafdbcffb1419ebd16227bb145d602592d86dabdb14c2
parity-gpu0.log     76e57ebab70cb4d8c8e26bf821b1864ad239e17dcecb1b568b61ff2c9b8e61a5
parity-gpu1.log     76e57ebab70cb4d8c8e26bf821b1864ad239e17dcecb1b568b61ff2c9b8e61a5
sweep-gpu0.log      63c1601b02b91cb64df8a892bc7e6c5fc8044274529354441d8bdfaf7dc0c5e0
sweep-gpu1.log      069bd45a48f60e1077a8c7b27190e7112fed25a899637849d8c88eda69f56282
all-sha256.txt      f51774e0b9653443b2b4e333657240501cedecec50ba1ba040141599ef67e4c2
```

## Not measured

No server-side A-B-A, graph replay, long-context request, natural-language parity or mixed
traffic ran because the measured per-read saving cannot meet the 1% whole-stage threshold.
