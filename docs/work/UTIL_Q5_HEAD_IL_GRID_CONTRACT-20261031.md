# UTIL-Q5-HEAD-IL-GRID contract

Status: pre-registered on 2026-10-31. No source or server candidate has been accepted yet.

## Objective

Reduce the production Q5_K output-head cost by changing only the launch geometry of
the existing exact interleaved MMVQ kernel. The first bounded scan is the persistent
CTA count for the canonical `2560 -> 248320` Q5_K head at two and three activation
columns. The existing kernel already has a grid-stride row loop, but its production
launch gives roughly 15,520-31,040 CTAs one row group each. Reusing a smaller number
of resident CTAs may remove scheduling and tail overhead without changing any
per-row arithmetic.

This is not another head fusion, cross-group head merge or peer split. It does not
change the pipeline schedule, slot order, logits layout, sampler, finish decision or
state commit. It also does not revisit B6 rows, HC/Q8 fusion, checkpoint movement,
fixed group counts, piped MTP, QSA slot merging or KV resolve.

## Source path and reach

`Verifier::record_window` reaches the final head on the last pipeline stage. On the
target RTX 2080 Ti cards with `STRATA_SM75_PERF=1`, two through four columns enter:

```text
native_quantize_q8_1
native_q8_1_interleave
native_mmvq_il
native_mmvq_il_kernel<I, NC, R>
```

The sm_75 table selects Q5_K rows-per-warp `R=4` at `T=2` and `R=2` at `T=3` for
the head's output-row class. The production profile measured the head at
`1.04-1.06 ms/window`; stage 2 was `18.27-19.22 ms/window`. The conservative
one-percent stage budget is therefore about `0.183-0.192 ms` per head call.

A server candidate is justified only if the head call itself falls by at least
`18.5%` (or by at least `0.192 ms`) and the result is stable across independent
processes. A smaller local kernel gain is rejected because it cannot recover one
percent of the measured full stage.

## Frozen inputs

Remote source root:

```text
/opt/ai/src/strata-freeze-throughput-20261029T0812Z
```

The worktree is intentionally dirty and belongs to the user. This experiment does
not reset, clean or check it out. Kernel source hashes at pre-registration:

| file | sha256 |
| --- | --- |
| `src/kernels/cuda/native_mmvq.cu` | `2260db1bd24794596ba4c1dd01a2a754707ffbbef47b648bf38d3922c813fae8` |
| `src/kernels/mmvq_il_parity.cpp` | `cd61b11e77f163cc5af3b188e98416e739c9a6efb8ddb5412069da518f044547` |
| `include/strata/kernels/native_mmvq.hpp` | `0c284577e02c67c09376799c376e1c575c01e01ca42d56891e3b61460301ec92` |

The current A2 server/engine/vision processes remain the baseline and are not
restarted for the kernel screen. The server is healthy with six slots, two groups,
health `loaded=true`, RAM available about 13 GiB and swap zero.

## Bounded experiment

Add one default-off environment switch:

```text
STRATA_MMVQ_IL_HEAD_GRID=<positive integer>
```

It caps the CTA grid only when `n_out > 100000`, so its production reach is the
single native output head and not dense-layer or expert projections. With the switch
unset or malformed, the launch is byte-for-byte the current geometry.

The grid scan is fixed to:

```text
160, 320, 640, 1280, 2560, 5120
```

No other grid values, row counts or kernel expressions are tested in this contract.
The existing `R=2` and `R=4` kernels remain unchanged.

## Correctness gate

The existing `mmvq_il_parity` harness must run against the real Q5_K head shape:

1. every candidate grid reports zero differing output bits against the current IL
   and plain-kernel references for `T=1..8`;
2. every output is finite;
3. graph replay with different activations gives the same bits as a direct call;
4. malformed or zero switches retain the current grid;
5. the default path's machine code and source expression for the per-row dot and
   reduction are unchanged.

## Performance gate

Screening uses the exact head shape with interleaved activation copies and enough
weight copies to miss L2. Seven independent processes alternate baseline and each
candidate grid. Reject immediately if the best grid does not:

1. produce a head median at least `18.5%` below baseline;
2. win at least five of seven paired process comparisons at both `T=2` and `T=3`;
3. remain within one percent of baseline at `T=1` and `T=4`;
4. show the same direction on both GPUs.

Only after the screen passes may the winning fixed value be built into the A engine
binary as a default-off switch. A server restart is then justified by the projected
`2/4/6` reach and costs one A-to-B load and one B-to-A2 restore, recently about
`150 s` per load plus about `9 s` per stop.

## Server gate after the kernel gate

Run A1-B-A2 on one boot with only the tested grid switch added:

1. three frozen greedy probes twice per arm are byte-identical and have the same
   finish reason;
2. three waves at each of `N=2/4/6/8`;
3. at least one level improves by at least one percent against the A1/A2 combined
   median and wins at least five of six paired waves;
4. every other level regresses by no more than one percent;
5. A1-to-A2 drift is no more than three percent;
6. zero HTTP or execution errors, unexpected finishes or foreign requests;
7. six slots, two groups, health, RAM, VRAM and swap remain safe;
8. the head-grid hit is proven by startup diagnostics and stage timing;
9. production systemd, config and runtime files remain unchanged.

## Rollback

The kernel screen does not touch the server. If a later server candidate fails any
gate, stop it, restore the exact A2 binary and config, and verify engine/config
hashes, health, process state, six slots, two groups, RAM, VRAM and swap. Never write
an API key, token or private prompt to the repository or evidence.

## Final screening decision

**Rejected on 2026-10-31 at the performance-screening gate.** The valid
`screen2-gpu0-*` measurements, together with the bitwise `mmvq_il_parity` gate, showed
that every capped grid was slower than the default interleaved path at `T=2`. The best
capped result was grid `2560` at `T=3`: `1.272%` faster than the plain kernel and only
`0.182` percentage points faster than the default. That is far below the required 18.5%
head reduction or `0.192 ms` full-stage budget. The seven-process confirmation and
server A-B-A were not run because the performance gap was over 15 percentage points.

The candidate source change was removed, and the A2 runtime does not contain
`STRATA_MMVQ_IL_HEAD_GRID`. Full measurements and evidence are in
[`UTIL_Q5_HEAD_IL_GRID_RESULT-20261031.md`](UTIL_Q5_HEAD_IL_GRID_RESULT-20261031.md).
