# Upstream report: the engine dies in prefill with CUDA Xid 13

Draft for an upstream issue. Not posted: it waits for a human to paste it into the
[bug report form](https://github.com/Niko1221/Strata/issues/new/choose); this fork keeps its upstream text in the
repository first, as it did for [UPSTREAM_REPORT_SM75.md](UPSTREAM_REPORT_SM75.md). A crash report needs no scope
agreement, only the evidence below.

**Proposed title:** `[Engine] engine dies in prefill with CUDA Xid 13 (illegal memory access) after a 62K-token turn`

---

Problem: on 2026-11-02 at 09:52:10 UTC the engine process died during prefill on this fork's two-card box. The driver
logged 202 `Xid 13` lines and one `Xid 43` in that single second, the engine's last log line was
`prefill copy_i32: an illegal memory access was encountered`, and the supervisor restarted it 114 s later. The request
in flight failed. The on-screen message blamed RAM, and that sent the first hour of triage in the wrong direction.

## Environment

- Hardware: 2 × RTX 2080 Ti **22 GB** (TU102, compute capability 7.5), NVLink, one layer split (24/24); Xeon E5-2680 v4
  (28 threads, no AVX-512), 62 GiB RAM, 4 GiB swap; Ubuntu 24.04.5, kernel 7.0.0-38, driver 595.71.05, CUDA 13.2.
- Build: `strata 0.2.6 (build 0.1.51; python 3.12.3)`, engine `0.1.51`; the binary this box serves from,
  `build-guard-prod/strata`, was built 2026-11-01 04:25 UTC. **The source tree has no `.git`**, so there is no commit
  hash to give; it has to be rebuilt from whatever that working tree was.
- Model: a native IQ3_XXS pack (`Qwen3.8-Flash-Next GSQ-RCO-abliterated`), context 262144, vision on, INT8 K/V,
  `--layer-split 24 --batch 6 --batch-groups 2 --prefill-pipe 1 --conversation-cache-slots 12 --ple-inflight 1024`.
- Environment: `STRATA_HC_SPLIT=1 STRATA_BATCH_SERIAL_TWO=1 STRATA_PREFILL_CPU_SHARE_BATCH_GUARD=1 STRATA_EXP_MODE=8
  STRATA_HC_Q8=1 STRATA_PIPED_CONCURRENT=1`.

## What happened

The engine had served agent traffic for six hours without a break. It was on the turn after a completed 62,219-token
request (`1185 generated in 17033 ms`, an agent loop alternating `read` and `grep` tool calls). The next request's
prefill faulted; its prompt was never logged, because prefill died before the "read N tokens" line.

```
NVRM: Xid (PCI:0000:03:00): 13, Graphics SM Warp Exception on (GPC 5, TPC 3, SM 1): Out Of Range Address
NVRM: Xid (PCI:0000:03:00): 13, Graphics SM Global Exception on (GPC 5, TPC 3, SM 1): Multiple Warp Errors
...
NVRM: Xid (PCI:0000:03:00): 43, pid=4719, name=strata, channel 0x0000000b
```

202 of the 203 lines are `Xid 13` (68 distinct `Graphics Exception: ESR` events, each with its two companion lines),
all on GPU 0; the last is `Xid 43` for the engine's own channel. The engine then logged:

```
prefill copy_i32: an illegal memory access was encountered
```

and the supervisor:

```
[strata] the engine stopped unexpectedly (exit code None). The usual cause is running out of RAM: ...
[strata] done: 0 tokens in 7 s (no speed) (error, cancel=False)
```

It restarted the engine at 09:54:04 and was serving again at about 09:56:26, 142 s to load. Total impact about four
minutes.

## Reproduce

Not established. This is one occurrence after ~6 h of uninterrupted traffic, and there is no way to replay the exact
prompt. What is known about the turn:

- The request before it completed normally at 62,219 prompt tokens and 1,185 generated.
- Earlier in the same session a 168,465-token prompt completed, so a long context alone is not sufficient.
- The faulting request's length was never logged.

## What the logs say

| Check | Result |
|---|---|
| cgroup `memory.events` for the service | `oom 0`, `oom_kill 0`, `max 0` |
| cgroup `memory.max` | `max` (no limit) |
| `memory.peak` | 64,285,589,504 B — 59.9 GiB of 62 GiB |
| `journalctl -k \| grep -i -E 'killed process\|out of memory'` | nothing |
| `journalctl -k -b -1 / -2 / -3 \| grep -c Xid` | 0, 0, 0 |
| `grep -c "illegal memory" strata-engine.log` | 1 in a 5.4 MB log spanning several engine starts |
| `grep -c "engine stopped unexpectedly" strata-service.log.1` | 0 (the rotated log of the day before) |

So it was not out of RAM, not the OOM killer, and not a build that always crashes: the same binary ran about 23 h
before this boot and six hours in it. After the fault the cards were already idle — GPU0 43 °C, GPU1 36 °C, ~50 W each,
`clocks_throttle_reasons.active = 0x1` (`GpuIdle`) — so no thermal or power-limit throttling, though those are not
readings taken during the fault. The boot-time tuning sets 280 W, and the three previous boots ran at the same limit
with no `Xid` at all.

## Why `copy_i32` is probably not the culprit

**Reading, not measurement.** `src/prefill/kernels.cu:101`:

```c
void check(const char* what) {
    const cudaError_t e = cudaGetLastError();
    if (e != cudaSuccess) { std::fprintf(stderr, "prefill %s: %s\n", what, cudaGetErrorString(e)); std::exit(1); }
}
```

`cudaErrorIllegalAddress` is sticky: the launch that faulted was already queued, and the next `cudaGetLastError()` —
the one after `copy_i32` (`src/prefill/kernels.cu:2784`) — is what notices it. `copy_i32` itself is a guarded loop,
`for (i < n) dst[i] = src[i];` (`src/prefill/kernels.cu:2766`), so either a bad pointer or length was handed to it, or
an earlier launch faulted.

The same signature has been seen before, `src/kernels/cuda/qsa_prompt_attn.cu:2359-2362`:

```c
// A card below sm_75 has no m16n8k8: its builds hold a __trap() in those kernels, so forcing them
// there killed the engine ("prefill copy_i32: unspecified launch failure" on a Tesla P100)
```

An unrelated kernel faulted and `prefill copy_i32` took the blame. One consistency check, not proof: the layer split
puts layers 0-23 on CUDA0 and prefill's first stage runs there (`the prompt path borrows 2515 CUDA0 cache slots`), and
CUDA0 is the card that logged the `Xid 13`.

## What would confirm it

Re-run with `CUDA_LAUNCH_BLOCKING=1` so the sticky error is raised at the faulting launch, or with
`compute-sanitizer --tool memcheck`. Both cost a lot of speed, so either needs a maintenance window. If a maintainer
wants one of those arms, say which and this box will run it.

## Related, but not the same

- [PR #360](https://github.com/Niko1221/Strata/pull/360), "qsa_prompt_attn: Volta (sm_70) crashes in the prompt path" —
  same area, different compute capability (this box is sm_75/Turing) and a different failure.
- [Issue #3797](https://github.com/Niko1221/Strata/issues/3797), "Startup fails on dual RTX 2080 Ti 22GB GPUs due to
  CUDA weight arena OOM" — same card pair, a different phase (startup, not prefill).

A search for this signature turned up those two and nothing matching; if a thread already covers it, this text should
move there instead of opening a new issue.

## What I did not test

- It was not reproduced: no repeatable request, no second crash to compare against.
- `CUDA_LAUNCH_BLOCKING=1` and `compute-sanitizer` were not run; they are the next step, not a result.
- No other card model, no HIP or SYCL build, no Windows.
- The crash may be specific to this build (`build-guard-prod`, a local development tree) rather than to 0.1.51 in
  general; the fork cannot say, because it has no clean 0.1.51 build on this machine to compare with.

## Reproduction status

Observed once, in `journalctl -k` and the engine log, on the box named above; **not reproduced**. Everything under
"Why `copy_i32` is probably not the culprit" is reading the source, not an observation.
