# UTIL-HEAD-Q8-FUSE result

Date: 2026-10-31. Status: **rejected at the independent GPU/correctness gate**.
No server candidate was built and Strata was not restarted.

## Question

The production `STRATA_HC_Q8=1` path performs the final-layer pending write and
the final mixer as two operations:

```text
copy R
gr_write_multi(R, block_out, inject, R)
Q8 fused_gr_read_multi(apply=false, R)
```

The Q8 multi-read kernel already supports `apply=true`, so the candidate is:

```text
copy R
Q8 fused_gr_read_multi(apply=true, R, block_out, inject)
```

The contract required byte-identical `R`, `lo`, `rs`, `mixed` and the quantized
Q8_1 image, plus at least `182.7 us` saved in the fastest `18.27 ms` stage-2
window. The user accepts a 1% improvement.

## Method

- Production geometry: `n_embd=2560`, `hc=4`, `hc_lr=320`.
- Remote CUDA0, independent `head_q8_fuse_parity` binary.
- Five alternating processes; each ran 200 iterations and five rounds.
- Every process covered `T=1..8`.
- Direct launches and a captured graph of each sequence were compared.
- Timings include the harness's operation sequence and graph replay.

## Correctness

For every process, every byte comparison failed:

- `T=1..8` direct sequence;
- `T=1..8` captured-graph sequence;
- `R`, `lo`, `rs`, `mixed` and the final Q8_1 image.

That is 16 failed comparisons per process and 80 across five processes. Every
process ended with `result=REJECT`.

## Performance

| process | best direct saving | 1% threshold | captured graph |
| ---: | ---: | ---: | --- |
| 0 | `1.308 us` | `182.700 us` | `T=4..8` slower, up to `3.123 us` |
| 1 | `1.315 us` | `182.700 us` | `T=3..8` slower, up to `3.048 us` |
| 2 | `0.921 us` | `182.700 us` | `T=3..8` slower, up to `3.080 us` |
| 3 | `1.113 us` | `182.700 us` | `T=3..8` slower, up to `3.030 us` |
| 4 | `1.098 us` | `182.700 us` | `T=3..8` slower, up to `3.019 us` |

The best saving is about `0.72%` of the required threshold. Even if exactness
were ignored, the result is far outside the accepted improvement scale.

## Decision

Reject. Do not build a server candidate and do not restart for this mechanism.
The production process is unchanged:

- server / engine / vision: `573407 / 573418 / 573413`;
- engine sha256:
  `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
- config sha256:
  `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f`;
- health `loaded=true`, six slots and two groups;
- `systemctl is-active strata.service` remains `inactive`;
- `STRATA_FUSE_HEAD_GR` is absent.

## Evidence

Remote directory:
`/opt/ai/logs/util-head-q8-fuse-20261031T0740CST/`.

| artifact | sha256 |
| --- | --- |
| `process-0.log` | `cae5fc1c0a5668f72ae39ef3383d26d46a295f7ceb971ad70c99931c1d1f59f9` |
| `process-1.log` | `6c485a7f79752758518e77c2a03d7eb209f6ce5417ae0d629665940e02588df2` |
| `process-2.log` | `44365ce80eafba4e5ecb8a000cef9649cdd63e20444424cec4025bcb60803f9a` |
| `process-3.log` | `a7830b23457901719aa8788025ab5de19d407e1d556ed2e0c15590ba14ac2a04` |
| `process-4.log` | `b196d7b22531b5b89120d16f4a69f17446f07638d6d14f57a11efceed8910645` |
| harness binary | `3474ea97fb4a6ce55f48f427381e7b74ea0a1ee9487dcf5df52500956b67c0e3` |
| `all-sha256.txt` | `5ccba05c4f060ef85f3a845ec7e377cccec16eec0bea484e13f6c1ff338af9a4` |

The manifest also covers the harness source, its copied contract and the source
tree's `CMakeLists.txt`; `sha256sum -c` passed.

## Next

`UTIL-POLL-TURNOVER`: audit the existing `pump()` completion-poll, group
hand-off and next-stage launch chain. This is a different scheduling mechanism.
It must first prove a real `1%` critical-path gap from existing telemetry or a
controlled read-only sample. A simple loop-order change is not a candidate.
