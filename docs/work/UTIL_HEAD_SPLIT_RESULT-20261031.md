# UTIL-HEAD-SPLIT harness result

## Decision

**The independent GPU gate passed on 2026-10-31, but the server throughput gate
rejected the candidate.** The vocabulary-row partition is byte exact, graph replay
uses changed activations, and the two-card critical path projected above 1% at
every measured window size. The production A1-B-A2 run did not reproduce that gain:
4 and 6 streams regressed by `1.305%` and `8.659%`, above the 1% non-regression
limit.

The opt-in server integration was implemented and passed startup, path-hit, greedy
parity and resource checks. It was not adopted. The full-head A path remains the
default and `STRATA_HEAD_SPLIT` is absent from the production process.

No Strata restart was used for this result. The current A2 process and production
files were not changed.

## Harness

The test uses the production native head shape and format:

```text
Q5_K, n_in=2560, n_out=248320, halves=124160+124160
```

The full call on one RTX 2080 Ti is the byte reference. The split path allocates
disjoint vocabulary ranges on both cards, quantizes one set of activation rows, runs
both halves on private streams and graphs, and joins the peer half through NV2 peer
access. The tested path covers `T=1..8`, the 2-4-column IL path, graph replay with
changed input, greedy selection, fixed-seed sampling and two draw counters.

Source: [`src/kernels/head_split_parity.cu`](../../src/kernels/head_split_parity.cu).

## Exactness

All of the following passed for each `T=1..8`:

- first half logits are `memcmp`-equal to `full[0:124160]`;
- second half logits are `memcmp`-equal to `full[124160:248320]`;
- merged row-major logits are `memcmp`-equal to the full logits;
- all outputs are finite;
- greedy ids match;
- fixed-seed sampled ids match at draw counters 17 and 10017;
- full and split graph replays change with changed activations and match the
  corresponding full references;
- the one-device fallback is byte-stable.

Seven alternating processes each covered all eight sizes. Every process printed
`SUMMARY ... failures=0 result=PASS`.

## Critical path

Medians across the seven alternating processes:

| T | full us | concurrent us | projected end-to-end | worst single projection |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 1094.779 | 763.241 | 1.813% | 1.727% |
| 2 | 1932.903 | 1071.851 | 4.611% | 2.610% |
| 3 | 2364.746 | 1243.243 | 6.052% | 4.556% |
| 4 | 2517.119 | 1347.023 | 6.439% | 5.287% |
| 5 | 2806.073 | 1494.654 | 7.213% | 6.456% |
| 6 | 3083.637 | 1643.123 | 7.935% | 7.745% |
| 7 | 3479.548 | 1833.882 | 9.031% | 8.771% |
| 8 | 3881.892 | 2066.048 | 9.936% | 9.825% |

The projection divides the saved head-path time by the fastest measured stage-2
window, `18.27 ms`. This is deliberately conservative. It assumes no other part of
the window becomes slower and does not include server orchestration. The server gate
still has to prove the benefit end to end.

## Memory

| item | bytes |
| --- | ---: |
| full head weights | 437,043,200 |
| full-path peak | 445,116,192 |
| split-path peak, both devices | 453,189,152 |
| allowed split bound | 461,577,760 |

The two partition weight sizes sum exactly to the full head. The extra total is
bounded activation copy, Q8_1, partial-logits and merge scratch: 8,072,960 B,
about 7.7 MiB. On the last-stage card alone, only its half of the weights is
retained; the peer retains the other half.

## Reproduction

Independent source and build:

```text
/opt/ai/src/strata-head-split-harness-20261031T0625CST
```

Evidence:

```text
/opt/ai/logs/util-head-split-20261031T0625CST/
```

Key SHA-256:

```text
seven-processes.log
e7d136c5835e718f120a913d9f73d70349d0e03de01405603f9205af5d50fc4c

head_split_parity.cu (the source built remotely)
71abf5f2b3dd32abe9131d8af1999f36c4b38d33ab232102f253887765ccf962

head_split_parity binary
2e37b3b9953eb083031ab45a4d53075f2f7a682be708ecab91bc4a538ebd030c

process-0.log acc99cb194e3da893d4f262061c82ab82df3def1fca791a5bc752049e8276f1c
process-1.log f3a061df64d3003096e4ce0fceda3bbbd7d47801d8c75f2c8d15782c855517e2
process-2.log 33579e99781e2b0e5820291dc10e8f3c6226c31c0e502fcdd0b392ddae456a6f
process-3.log 667dce0beb7622fcb66af9ae98a47d33ae7a88039d5d8ac631b29146cfaea3be
process-4.log c6d2d9a5dd0a9012176af85cc25204a1d8ce0d79f320c864f7edcd563c9bf5b2
process-5.log a041ca5495f8023835baca4861f7e423fcf57611a9594e396f41a140453549f2
process-6.log 661a1445a385e7d319a86a48b2ecf17f560f01ba06cc1fd721caffe12d67c1cb
```

The synthetic weights are chosen only to exercise Q5_K block decoding. The harness
does not claim model-quality parity beyond the exact native-kernel output it compares.

## Server integration result

The server candidate loaded with 6 slots / 2 groups and two free-VRAM readings of
`1002/1069 MiB`. Logs show peer graphs captured for every row count `1..8`. Three
frozen greedy probes repeated exactly in B and matched the expected text with
`finish=stop`; A2 also passed.

A1-B-A2 throughput in tokens/s:

| N | A1 | B | A2 | A combined | B vs A |
|---:|---:|---:|---:|---:|---:|
| 2 | 77.0 | 80.8 | 77.8 | 77.70 | **+3.99%** |
| 4 | 115.7 | 113.4 | 114.1 | 114.90 | **-1.305%** |
| 6 | 124.0 | 113.4 | 124.3 | 124.15 | **-8.659%** |
| 8 | 106.6 | 106.2 | 106.0 | 106.30 | -0.094% |

A1-to-A2 drift was within 3% at every level and no foreign requests were seen. The
throughput script recorded both `length` and `stop` outcomes in its arm summary; the
hard rejection is the reproducible 4/6 regression, not an unexpected-finish claim.

Integration exposed and fixed three defects: MTP needed to accept the default CUDA
stream, two-device startup could not use `stages.size()==2`, and a peer graph could
not capture `cudaMemcpyPeerAsync`. The final peer path reads the primary device's
Q8_1 activation directly. Both the single-process and seven-alternating-process
parity logs passed with `failures=0`.

Evidence:

```text
/opt/ai/logs/util-head-split-server-20261031T0800CST/

peer-read-direct-seven-processes.log
2b32c31d7db7a39cb99fa7103d9936f0b3ab973867f54a3c29707c68c1a89ed4

ab-summary.json
8319e5c72b4342eb7c2f0151470f24d62b334dca1b66c1de2c3ccf7e0c18d28b

a2-restored-metrics.json
cd539246dd2678255d9e4bbc36489a4b46b32323ff8d523221060e3c2dba143b

all-sha256.txt
a7aed4cd7d12d5e333819bbca30d5a1ad56b792f9794bd63aa82ae00923975cd
```

Restored A2: server/engine/vision `573407/573418/573413`; engine sha256
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`;
config sha256
`88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f`.

## Server integration contract

Keep the switch default off:

```text
STRATA_HEAD_SPLIT=1
```

The production implementation must:

1. load the two disjoint row ranges directly from `output.weight`, never keep a full
   third copy;
2. preserve the complete single-device path when the switch or prerequisites are
   absent;
3. use private peer stream/events and one shared Q8_1 activation image;
4. join each half at its vocabulary offset before the existing argmax, NaN and
   sampler logic;
5. launch the peer graph for every single-request and batch window layout the last
   stage can run;
6. make the main graph wait only for its own two head halves, never another pipeline
   group;
7. prove split-path hits in logs and keep the fallback byte-identical.

Only after exact startup/parity can it consume one A-to-B load and one B-to-A restore.
The server gate remains the previously registered A1-B-A2 2/4/6/8 test.
