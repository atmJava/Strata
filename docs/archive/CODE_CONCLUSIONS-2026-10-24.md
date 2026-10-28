# Strata on this server: what is closed, and why

> **历史快照（2026-10-24）**：本页保留当时的生产形状、测量数字和已关闭路线，不能作为当前生产
> 或指标基线使用。当前事实以 [`STATE.md`](../STATE.md)、[`ROADMAP.md`](../ROADMAP.md)、
> [`EVIDENCE_LEDGER.md`](../EVIDENCE_LEDGER.md) 为准。文中 `0.1.47`、`parallel 4` 等均为历史值。

One page. Every line below was measured on this machine on 2026-10-24 (night), with the production build
unless it says otherwise. The evidence is in `docs/archive/CODE_PLAN-2026-10-24.md` (section numbers given)
and the commits named.
Read this before re-testing anything: most ideas have already been tried and closed.

**The box**: 2× RTX 2080 Ti 22,528 MiB (sm_75, PCIe 3.0 x16), Xeon E5-2680 v4 (AVX2, no VNNI), 62 GiB RAM.
Production: Strata 0.2.2 + engine 0.1.47, `layer_split 24`, `parallel 4`, `--batch 4 --batch-groups 2`,
`--pcie-frac 0.31`, `--kv-resident 32768`, 12,288 expert slots.

## The number that matters

**One stream: 63 tok/s. Three streams at once: ≈136 tok/s.** The same box, the same config, the same prompts.
Single-stream decode spends 22–30 ms a window of which only 8.5–9.3 ms is the GPU actually computing; the rest
is per-token fixed overhead. The hardware is under-used because requests arrive one at a time, not because a
kernel is slow. **Getting more concurrency is worth more than any change listed below.** (archived CODE_PLAN §24)

## The decode cost curve (measured, three loads)

| tokens in the window | ms / window | ms / token |
|---|---|---|
| T=1 (one stream) | 22.2 | 22.2 |
| T≈2 (one stream, 2 slots busy) | 30.1 | 14.7 |
| T=4 (four streams) | ≈37 (from 108 tok/s) | ≈9.3 |

That is a fixed cost of ≈12 ms a window plus ≈4.4 ms for each token in it. The fixed part is the window's
hand-offs and launches, and it is why filling the slots is worth more than any kernel change: at T=1 three
quarters of the window is paid for one token.

## Closed, with the number that closed it

| Idea | Result | Where |
|---|---|---|
| prefill helper on the second card (NVLink) | 4.5k new tokens **+8.0%** (0/6 pairs won), 1.5k −3~−4%. Off. | §16, commit `2f230e26` |
| Foresight swap: hide host staging with predicted experts | needs **2,268 MiB** on CUDA0; the 44 GB are resident experts. Impossible here. | §17, `4dc4caab` |
| Change the layer split (23/25/…) | the per-layer wait is uniform, so layer count changes nothing on the idle side | §18 |
| `STRATA_SPLIT_OVERLAP=1` (shared-flag hand-off, no host sync) | per-layer wait **17.63 → 19.3 ms (+9%)**, decode in the noise. Off. | §19, `fc9bf00d` |
| Rewrite the grouped expert kernel for bandwidth | it already runs at **≈620 GB/s = HBM peak** (1.52 ms a window, 0.063 ms a layer). Nothing to take. | §23, `541b5d0e` |
| Chase the 1.56 ms/window `waitCPU` (GPU idle waiting for the CPU pool) | a light-load artefact: at three streams it disappears into other work | §24, `57156567` |

Earlier, already closed (kept in `docs/archive/CODE_PLAN-2026-10-24.md` §6): `--pipeline-windows 2`, `layer_split` 23/25,
`STRATA_QFUSE=1`, EXPDEC, `PF_FUSED_SM75`, HC_PERSIST, the MMQ tab port, the TP patch, 0.2.4.1 upgrade,
park-gain, read-ahead, session parking. Adopted in this goal: only `STRATA_HC_Q8=1`.

## Why so little landed

Decode's per-token cost here is architecture, not a replaceable kernel: one window per token, one ring hand-off
per layer (0.37 ms a layer, identical on every layer), and expert weights that already stream at HBM peak.
Every candidate that attacks a *component* of that therefore lands under the 5% the rules require. That is a
finding, not an unfinished job.

## What is still open

1. **Deployment concurrency.** `--batch 4`/`parallel 4` are ready and unused. A client that keeps 3–4 requests
   in flight gets ~2.2× the tokens per second. This needs no code change and no restart.
2. **Prefill of a cold request** streams the whole pack (≈34 GB, ≈3.0 s at the 11 GB/s host link) before the
   first token. Prompt-cache hits avoid it; nothing else measured does. (§15)
3. **A real kernel project, if someone wants one**: the per-layer GPU work is dominated by the GDN dense path
   (`q8+qkv 0.75`, `out-proj 0.76`, `z 0.56`, `shared+quant 0.57`, `hc0 up/down/norm ≈1.03` ms a window) at
   T=1–2, where M is too small for the tiles. That is a shape problem, not a bandwidth one, and it needs the
   0.2.4.x kernel line — a multi-day job, not an overnight one.

## Rules that came out of this night

- A number without a same-boot A/B, ≥3 reps and a median is not a result. Under 5% is "indistinguishable".
- `STRATA_DECODE_TIMING=1` + `STRATA_VERIFY_PROFILE=1` gives the per-window GPU stage account; use it first.
- Static arithmetic must be labelled as such. The 44%-of-peak estimate in §21 was wrong and the measurement in
  §23 replaced it — compute the model, then measure the kernel before planning work on it.
- Never touch `build/strata`, `strata-022.json` or the unit while measuring; replace a running binary with
  `cp … .new && mv -f`; verify the deployed file, not the build artefact.
- One installer at a time (`pgrep -af "[u]se-config|[r]estore-prod"`), and put production back with
  `/opt/ai/bin/restore-prod.sh` — `md5 strata-022.json` must equal `ad4962eb5a02852bedba4fb9fe76f45e`.
