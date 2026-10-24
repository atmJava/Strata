# 上游：我们**不再往上游发东西**（2026-10-24 用户定），发过的三条**已删除**

**规矩（2026-10-24 上午用户下的，覆盖此前所有做法）**：**不回帖、不开 PR、不提交任何东西**到
`Niko1221/Strata`。本篇因此从"待发草稿"变成**留档**：里面的英文正文是我们台账的上游侧副本，
以后不再发布，也不再改措辞给人看。

| 草稿 | 原本对着哪条 thread | 状态 |
|---|---|---|
| A（#2275 并发读：gate 的判定条件） | #2275 | 2026-10-24 02:08:13Z 发过 → **已删**（DELETE 204，再 GET 404） |
| B（#2197 第七台 sm_75 + 那条例外） | #2197 | 2026-10-24 02:08:15Z 发过 → **已删**（同上） |
| C（#2197 mode 8 的本机数据） | #2197 | 2026-10-24 02:08:16Z 发过 → **已删**（同上） |
| D（#3724 MMQ tile 上限在本机的适用性） | #3724 | **未发，按新规不再发** |
| E（回 rafatxf 的提问） | #2197 | **未发，按新规不再发** |

**留下的痕迹（我们不动它）**：rafatxf 2026-10-24 02:19:34Z 在我们 B/C 底下回了一条
[issuecomment-6378674185](https://github.com/Niko1221/Strata/issues/2197#issuecomment-6378674185)，
说这台机器是新 CPU 级别、两条都收进 body、mode 8 的 +8.5%/+7.9% 是他目前见过最大增益。
**那是他的评论**（我们不是维护者，删不掉也不该删），我们的三条已删，所以他那条现在指向已消失的引用。
**结论仍然记在台账里**（L-011/L-012/L-015/L-016/L-017），不依赖上游是否采纳。

---

## 草稿 A —— 贴到 #2275（[Engine] [Direction] long-context multi-user，open，11 评论）

> 我们这台机器正是"gate 是唯一拦路者"的那台。判据与数字全部来自我们自己跑的实验。

### Our box is the shape where the gate is the only thing in the way — measured trade on 2×2080 Ti, engine 0.1.47 plus our two-line change

**Setup, and it is not a stock release.** 2× RTX 2080 Ti 22 GB (modded, `sm_75`), NVLink NV2 (2 × 25.78 GB/s),
Xeon E5-2680 v4 (AVX2, no AVX-512, 14c/28t, one NUMA), 62 GiB RAM, driver 595.71.05, CUDA 13.0, 280 W vBIOS
power cap. Engine `strata 0.1.47` built from source for `sm_75` with `STRATA_SM75_PERF=1`, on upstream 0.2.2
plus the two lines below. Model Qwen3.8-Flash-Next GSQ-RCO IQ3_XXS, `--max-context 256000 --kv int8
--kv-resident 32768 --resident-experts --layer-split 24 --batch 4 --batch-groups 2 --spec 8 --mtp-max-t 4
--spec-min-p 0.7 --pcie-frac 0.31 --vram-reserve-mib 1280 --vram-reserve-later-mib 1435`, expert cache `auto`
→ 7,190 slots, `parallel 4`. Our expert cache hits 98–99% in normal traffic, so this is a box where the experts
are effectively resident and the read is about *bytes arriving*, not about eviction.

**What we measured.** Script `/tmp/piped_scenario_127k.py` (existing, unchanged): one stream decodes while a
126,050-token cold prompt is read; we time the cold read, the neighbour's speed during it and its longest gap.
One restart per arm, two reps per arm, same boot; the cold-read time is stable within ±0.6 s inside each arm.
The two arms are **the same binary** with `STRATA_PIPED_CONCURRENT` set or unset — on the unpatched engine the
variable does nothing here (the gate refuses), which is what the next section is about. Both runs used the gate
config from `docs/BATCHING.md` (`--batch-groups 1 --pcie-frac 0 --spec 0 --suffix-draft 0 --no-prefill-borrow`,
`parallel 1`), not our production shape — we have not measured the trade in the production shape yet.

| arm | cold read | neighbour during the read | neighbour output | longest gap |
|---|---|---|---|---|
| gate open (`STRATA_PIPED_CONCURRENT=1`) | 58.2 / 58.5 s | **19.7 / 20.3 tok/s** | 1,148 / 1,190 tok | **0.9 s** |
| gate closed | 49.0 / 49.6 s | 0.3 / 0.4 tok/s | 16 / 21 tok | 48.7 / 49.3 s |

An earlier run in the same shape at 84K pointed the same way: 42.2 s read with the neighbour at 21–22 tok/s and a
0.8 s longest gap, against 35.7 s / 0.3 tok/s / 35.5 s with the gate closed.

So the trade on this box is: **the read costs about 19% more (9 s on a 49 s read), and the neighbour decodes at
~20 tok/s instead of stopping for the whole read.** For a server with one long read and several live sessions that
is the direction this issue asks for; for one user alone it is a straight 19% loss. The shape matches what
docs/BATCHING.md already shows on your hardware (streams 2–3 tok/s → 85–92 tok/s during a read, run 5–8% sooner),
only the ratio is smaller here because a 2080 Ti has far less to give.

**Why the switch alone does nothing here.** `concurrent_read_off()` (`include/strata/program/batch_read.hpp:44`)
refuses with "an expert source that stages its blobs (the GGUF in place, unbuffered, STRATA_IO_PREFETCH)" because
`staged_source` is set from `src.stages_blobs()` at `src/program/generate.cpp:13562`. Our source runs with io
prefetch on (it engages by itself when the RAM headroom allows it), so the gate closes and
`STRATA_PIPED_CONCURRENT=1` measures identical with and without it — exactly what superniker reported in the
0.2.0-beta retest.

**The two lines we run.**

```diff
-                    s.staged_source = srcp == &src && src.stages_blobs();
+                    // The prompt reader never enters the stage pool: Prefill copies every transient expert
+                    // with copy_blobs and takes blob_stable only when !transient, where the source answers
+                    // with a resident or a mapped pointer.  This box has the io-prefetch/mapped-experts
+                    // source (io_prefetch=1, no role pointers, no unbuffered handles); Phase 0 measured 0
+                    // staged_blob, 0 claim_grow and 0 begin_layer calls on the reader thread over an
+                    // 84K-token read.  That shape is not "staged" for this gate; the GGUF in place and an
+                    // unbuffered source, not measured here, stay off.
+                    const bool mapped_io_source = src.io_prefetch() && !src.unbuffered() && !src.gguf_mode();
+                    s.staged_source = srcp == &src && src.stages_blobs() && !mapped_io_source;
```

Two files, +16/−1, the other file being a comment in `batch_read.hpp`. No environment-variable back door, no
probe header in the shipped build, no switch of our own: `STRATA_PIPED_CONCURRENT` is still the only way in, so a
run that does not set it is unchanged.

**The hazard the comment at `expert_source.hpp:953` is about, and what we did about it.** We read it as: with io
prefetch on, `blob()` hands out stage buffers it recycles, so one thread at a time. Our answer is measured, not
read: on a throwaway instrumented build, an 84K-token read beside the decoding slots made **0 `staged_blob`,
0 `claim_grow` and 0 `begin_layer` calls on the reader thread** — the reader takes resident or mapped pointers and
never claims a recycled buffer. We cannot generalise that to shapes we cannot run.

**What we did not test.** (1) No bitwise comparison between the two arms. In our production shape the output is
not reproducible arm-to-arm anyway — three runs of one prompt in the same arm give three different `text_sha256`
values and different draft counts — so a bitwise gate for this change needs the separate single-slot config we
used for `STRATA_SM75_PERF`, and we have not run it here. (2) No 4 × 96K simultaneous reads. (3) No measurement
of the GGUF-in-place or unbuffered variants (we left both gated). (4) The 47-minute soak we ran was on the
production build with the gate open and showed no corruption, but it is one box, one model, one workload mix.

**Question for the maintainers.** Should the gate predicate itself change, so the existing switch engages for a
source that answers with resident or mapped pointers and does not hand out recycled stage buffers — or do you want
this kept as a separate opt-in? We are not asking to merge #2326 (a real read thread is a different mechanism);
we are asking whether `staged_source` should be false for `io_prefetch()` sources that are neither GGUF-in-place
nor unbuffered. If the answer is "the predicate should change", we will open a PR against current main
(the two lines rebase cleanly onto 0.2.3.1) with the numbers above.

---

## 草稿 B —— 贴到 #2197（把 `STRATA_SM75_PERF` 变默认，open，32 评论）

> 2026-10-24 改写：**这条线程已经被同行证据推到收尾**（rafatxf 2026-10-22："on 0.1.47 … IQ3_XXS is now
> 12/12 byte-identical"、"that closes the last open line"，另有 6 台 sm_75 机器交过数据）。
> 所以我们那 +7% 与逐位门**大半是重复**，回帖改短，只交两样新东西：那条偶发例外，和跑 T2 配置的实操坑。

### A seventh sm_75 box confirms it, with one exception worth writing down before the body is final

**Setup.** 2× RTX 2080 Ti 22 GB (modded, `sm_75`), NVLink NV2, Xeon E5-2680 v4 (AVX2, no AVX-512, 14c/28t),
62 GiB, driver 595.71.05, CUDA 13.0. Engine `0.1.47` built from source for `sm_75`, Qwen3.8-Flash-Next
GSQ-RCO IQ3_XXS. We run `STRATA_SM75_PERF=1` in production today. Since this comment was drafted we have
also measured `STRATA_EXP_MODE` on the same box — that data is in the separate comment below (Draft C),
so the mode 4 / 0 / 8 line in the body can carry a seventh box.

**The gate, run the way `docs/BATCHING.md:403-408` asks.** `STRATA_IQ_MT_MIN=1`, `--pcie-frac 0`,
`--adapt-every 1000000`, `--no-prefill-borrow`, `parallel 1`. One trap for anyone reproducing it: `--spec`
cannot be turned off on a native pack (it wants `--spec T`, T ≥ 2), so what you are gating is a spec window,
not a plain decode — worth saying in the body so the next person does not hunt for a switch that does not exist.

`STRATA_SM75_PERF=0` and `=1` produced the same 512-token answer (`aac0a04d109391fe`). On the `=1` arm: four
runs identical after warm-up, and after a further restart three more identical including the cold start —
9 of 10.

**The exception, which is the reason for this comment.** After one restart the **first** request on the `=1`
arm gave a different answer (`00281f843ecd3543`); restarting again did not reproduce it, and we did not
attribute it. Since rafatxf's summary (12/12 byte-identical on 0.1.47) is what the body will carry, it seems
worth recording that on one box the only divergence was the first request after a start — if that is expected
(the first window warms caches and picks expert placement), a line in the body would save someone chasing it.

**Speed on this box** (production shape: 4 slots, `--pcie-frac 0.31`, adaptive): 84K cold prefill
2,375.6 / 2,381.3 → 2,552.0 / 2,566.5 / 2,556.9 tok/s (+7.2 to +7.6%); 43–50K cold prefill 2,108 / 2,094 →
2,236 / 2,258 (+6.0 to +7.8%); 4 concurrent streams 111.3 / 112.5 → 119.7 / 117.1 (+4.1 to +7.5%).
Single-stream decode 67.9–78.0 against 67.8–71.2 — pointing up but inside the run-to-run band, so we do not
claim it. In the production shape the output is not reproducible arm to arm (three runs of one prompt give
three different `text_sha256` values and different draft counts), which is why the bitwise claim above is
made only in the gate config.

**What we did not test.** the Q2_0 pack; three-card splits; engine 0.1.48. `STRATA_EXP_MODE` we have since
measured on this box — that is the separate comment below, not this one.

---

## 草稿 C —— 贴到 #2197（**mode 8 的本机数据**，2026-10-24 新写）

> 2026-10-24：这条线程里 mode 4 / 0 / 8 那一行目前只有 yusheng227507（IQ3_XXS，+14%，文本中性）与
> Adamyno（IQ3_S，+4.9% / +6.4% / +13.1%）两份同行数据。我们这条是**第七台机、同一 pack 类别、
> 同 boot 括号**，而且是 env 改动（不用重编译）。台账编号 **L-011**（速度+逐位+agent 质量）、
> **L-012**（上线后的被动接受率/命中率/截断）。

### `STRATA_EXP_MODE=8` on a seventh sm_75 box: +8.5% single-stream decode over the mode-4 default, answers identical on our (narrow) gate, now our production setting

**Setup.** Same box as the comment above: 2× RTX 2080 Ti 22 GB (modded, `sm_75`), NVLink NV2, Xeon E5-2680 v4
(AVX2, no AVX-512, 14c/28t), 62 GiB RAM, driver 595.71.05, CUDA 13.0, 280 W vBIOS cap. Engine `0.1.47` built
from source for `sm_75`, Qwen3.8-Flash-Next GSQ-RCO **IQ3_XXS**, `STRATA_SM75_PERF=1` already on. Production
shape: `parallel 4`, `--batch 4 --batch-groups 2 --layer-split 24 --resident-experts --expert-cache auto
--kv int8 --kv-resident 32768 --max-context 256000 --spec 8 --mtp-max-t 4 --spec-min-p 0.7 --pcie-frac 0.31
--vram-reserve-mib 1280 --vram-reserve-later-mib 1435`. The change is environment only
(`STRATA_EXP_MODE=8` against the shipped mode-4 default), no recompile.

**How it was measured.** Same-boot bracket, install A (mode 4) → B (mode 8) → A2 (mode 4), three installs,
816 s of downtime in total. Per arm: `bench-strata.py` 3 reps (fixed prompts, `temperature: 0`), a cold prefill
of 13,252 tokens 3 times, and a bitwise probe (`temperature: 0`, hash the answer and the reasoning separately).
Median per arm, and the bar we use is "<3% inside the bracket is not a finding".

| arm (bracket order) | single-stream decode | 4 concurrent streams | cold prefill 13,252 tok |
|---|---:|---:|---:|
| A — mode 4 | 67.2 tok/s | 118.9 tok/s | 2,260 tok/s |
| B — mode 8 | **73.5 tok/s** | 120.4 tok/s | 2,256 tok/s |
| A2 — mode 4 | 67.7 tok/s | 111.9 tok/s | 2,249 tok/s |

The A/A2 bracket differs by 0.7% on single stream, and mode 8 sits **+8.5% / +7.9%** above the two — so that
column is a finding on this box. The 4-stream column has a bracket spread of 5.9% by itself, so **we do not
claim anything there**. Cold prefill is unchanged (−0.2%).

**Output identity.** 24 probes across both arms (6 per arm in the speed round, 3 per arm in a separate quality
round): the 512-token answer hashed to `b17bc92de6ba8a97` **24 times out of 24**, no exception. Read the
narrowness with it: that is one 25-character answer on a 7.6K-token prompt at `temperature: 0`. We did **not**
run the 4,995-position teacher-forced value check Adamyno ran, so we are not claiming equivalence at that depth.

**Agent-shaped quality, not just one prompt.** 14 scored tasks, 8 of which can only be got right by computing
(multi-step arithmetic, divisibility counts, remainders, binary popcount, a linear equation, a speed/distance
conversion, an elimination puzzle) — no lookups. High effort, `temperature: 0`, three interleaved reps per arm:

| arm | passed (three reps) | completion tokens (median) | 14-task wall (median) |
|---|---|---:|---:|
| mode 4 | 14/14, 14/14, 14/14 | 2,178 | 44.5 s |
| mode 8 | 14/14, 13/14, 14/14 | 2,230 (+2.4%) | **42.3 s (−4.9%)** |

Token difference is inside our 3% bar; the direction of the wall clock matches the +8% decode. The one failure
was a truncated answer (`the reply is "…driver version is "`), 1 of 42 task instances against 0 for mode 4 —
not a signal, recorded because empty or truncated answers are one of our headline metrics.

**Speculative decoding is not what moved** (measured after the fact, on real traffic, so uncontrolled): over the
first 6.9 h in production, acceptance was **82.11% of 82,847 drafts** against **83.22% of 157,747** in the
preceding mode-4 window; restricted to matched prompt-length bands it is +1.2% (40–90K) and +0.8% (90–160K),
both under 3%. Expert-cache hit median 98.70% against 98.90% (p10 97.80 against 97.60), and 0 truncated
replies in 101 completed requests against 23 in 1,110. Traffic mix differs between those windows, so this is
"nothing moved measurably", not a controlled comparison.

**Operational notes for anyone scripting this.** Ready time is unchanged (mode 8 144–153 s against mode 4
141–147 s on a cold load) — but our own readiness loop first judged a slow-but-fine start as a failure and
rolled back, because it allowed 120 s against a ~150 s cold load; a loop that short will misjudge this box.
`/health` also keeps answering 200 from the engine that is about to be replaced, so a restart check has to wait
for the engine PID to change before it starts polling readiness.

**What we did not test.** (1) The bitwise claim in the production shape: it cannot be made there — three runs of
one prompt in the same arm give three different `text_sha256` values and different draft counts, which is why the
24/24 gate above was run in the narrow probe, not in traffic. (2) mode 8 at 127K context. (3) 8-slot concurrency.
(4) The mode 4 vs mode 0 leg. (5) The Q2_0 pack, three-card splits, engine 0.1.48 (we are on 0.1.47 self-built).
(6) DFlash2 at all — nothing in this comment speaks to rafatxf's question about which draft path ran.

**Where it stands.** `STRATA_EXP_MODE=8` is our production setting now (verified in the live engine process
environment, not just in the config file), with one command to revert. On this box the mode-4 default for
Turing is leaving about 8% of single-stream decode on the table, and we saw no cost we could measure.


---

## 草稿 D（发去 #3724）— **未发，等 atmJava 点头**

> 2026-10-24 新写。这条**全是读代码**，一条都没测，所以正文第一句就标 static reading。
> 对上游的价值有两点：**(1)** 告诉上报者"在 Strata 自己的构建里 decode 不进 MMQ，那 +12.66% 不可能来自这一行"；
> **(2)** mirror + CMake 指纹这个坑——backport 会让 `STRATA_MMQ_TAB` 自己关掉。

### Same ggml pin, 2×2080 Ti: in a Strata build this line is reached by the prompt path only, and the shape says the effect here would be small

We run the pin this issue names (`3cf03257`, 2026-09-20 — still the pin in 0.2.3.1) on 2× RTX 2080 Ti
(sm_75), a Qwen3 MoE IQ3_XXS pack, engine 0.1.47, CUDA 13.0, driver 595.71.05.

**Everything below is static reading, unconfirmed.** No measurement, no build, no run. We did not measure
prefill with and without MMQ, and we did not profile which grid the kernel launches with.

**1. The ggml function that gates this is not in our binary at all.** Our build is configured
`GGML_CUDA=OFF`: `build/ggml/src/` holds `libggml-base.a` and `libggml-cpu.a` only, and
`nm -C build/strata` has no `ggml_cuda_should_use_mmq`. Strata takes the handful of ggml-cuda host symbols
it needs from `src/prefill/ggml_cuda_host.cu` and decides for itself in `mmq::supported()` / `mmq::fits()`
(`src/prefill/moe_mmq.cu`). For the record that function would answer *true* for IQ3_XXS on sm_75 —
IQ3_XXS is in the whitelist at `mmq.cu:292`, the 48 KiB shared-memory test passes on Turing's 64 KiB, and
`turing_mma_available()` returns true at `mmq.cu:319-321`, before the `ne11 < MMQ_DP4A_MAX_BATCH_SIZE` test,
so batch size never enters the decision — but in this product that path is dead code.

**2. Where `mmq.cuh:1444` is actually reached here.** Prompt experts, through the gather path:
`prefill.cpp:1176/1185` → `moe_mmq.cu:428` (`p.tab` is false: `STRATA_MMQ_TAB=OFF` in the build cache and
`STRATA_MMQ_DIRECT` unset in the running process) → `moe_mmq.cu:455`,
`mul_mat_q_case<GGML_TYPE_IQ3_XXS>` → `mmq.cuh:1561 → 1478 → 1396 → 1444`. The symbol
`mul_mat_q_case<(ggml_type)18>` is present in the running binary, so the path is compiled in. The drafter's
batched Q8_0 projections reach the same launcher. **Not reached:** decode experts
(`verify.cpp:2031` → `native_expert_grouped` in `src/kernels/cuda/iq_kernels.cu`) and the dense layers
(`native_mmvq`, or cuBLAS through `Gemm` — the dense MMQ entry in `src/prefill/gemm.cu` is
`#if defined(STRATA_PREFILL_MMQ) && defined(__HIPCC__)`, HIP-only and off here). A read of the pack's GGUF
header says the same thing from the other side: dense tensors are IQ4_XS / Q6_K / Q4_K / Q5_K / Q8_0 / BF16,
and IQ3_XXS appears only on expert tensors. So a decode-only gain cannot come from this line in a Strata
build; it would have to come from a ggml-served path.

**3. On this card the prompt-path effect looks small even where it is reached.** With the PR's
`nwork = ntiles_dst * ceil(ncols_x / K_vram)` and `K_vram = MMQ_ITER_K = 256`: the gate/up projections are
1280×2560, so `nty = ceil(1280/128) = 10` and 10 k-splits give `nwork ≥ 100`, against `nsm = 68` SMs on a
2080 Ti (datasheet, not measured here) — `min(nsm, nwork) = nsm`, which is exactly today's grid. The down
projection (2560×640: `nty = 20`, 3 k-splits) only lands under the cap when a group holds a single expert
and `ntx = 1`, where `nwork = 60 < 68` and eight blocks are dropped. We have no counter that says how often
that case occurs in a real request.

**4. A build interaction worth knowing before this lands in a tree Strata pins.** Strata mirrors this
scheduler in `src/prefill/moe_mmq_tab.cuh`; its line 427 is byte-identical to the pinned `mmq.cuh:1444`.
CMake fingerprints the mirrored region — `mul_mat_q_process_tile` through `#define DECL_MMQ_CASE`, which is
`mmq.cuh:875..1571` and contains 1444 — against a registered hash, and on a mismatch turns `STRATA_MMQ_TAB`
off by itself with `mmq.cuh differs from the mirrored scheduler`. It is OFF today, so the backport changes
nothing about that flag now; but after the backport, anyone turning the tab path on gets the gather path's
new grid rule and the tab path's old one unless the mirror is updated in the same change and the hash
re-registered. The PR notes that partial iterations change the FP32 summation grouping, which is precisely
what would break the bitwise equivalence the mirror documents.

**What we did not test.** (1) Nothing was run, so every number above is arithmetic on tile shapes.
(2) We did not compare prefill with MMQ on and off (`STRATA_PREFILL_MMQ=0` against the default).
(3) We did not measure `smpbo` or `nsm` at runtime, and did not confirm which `J` the launcher picks per
group. (4) We did not follow whether the drafter's batched projection is enabled in a two-card
`--layer-split 24` run. (5) We have no way to compare against the reporter's 2080 Ti + 4070 SUPER pair,
and did not try to explain the reported decode numbers.

**So, from our side:** the change is reachable on sm_75 in a Strata build, but only on the prompt path, and
the shapes we can compute say it is a no-op for the two projections that dominate that path. If someone
wants a cheap check before spending a rebuild on it: print `ntiles_dst`, `nsm`, `tiles_efficiency_percent`
and `nwork` once per (type, J) at `mmq.cuh:1444` — that is a host-side print, one target to rebuild
(`strata_mmq`) and one relink, and it says on the real workload whether the `< 90` branch is ever taken with
`nwork < nsm`.

---

## 草稿 E（回 rafatxf 在 #2197 的提问）— **未发，等 atmJava 点头**

> 2026-10-24 写。他问的两个检查里**免费那个（比 prompt chunk）已经做完了**（L-017，纯读日志、没停机）：
> **chunk 从来没变过（53/53 都是 8192 tokens / 96-slot ring）**，他那条猜测在引擎自己写的日志里找不到支持。
> 顺手把同窗口挖到的两条"看着像出事其实没事/看着像崩溃其实是配置被拒"写进去——这两条对上游写 body 有用。
> 第二个检查（钉 `--prefill`）要重启，等停机窗口。

### The free check first: the chunk did not change, and here is what the retained log does say

Thanks — the chunk hypothesis is the one I can test without touching the box, so I did that one first.

`strata-022.log.1` here covers 2026-10-22 02:12:03 → 2026-10-23 19:10:57 and 71 engine starts. Of those, 53
logged the auto-chunk decision, and **all 53 say the same thing**: `prompt chunk auto: 8192 tokens, a 96-slot
ring`. The starts the `=0` / `=1` arms ran in are among them (04:49:49, 04:56:19, 05:01:41, 05:04:18,
05:10:03; the arm windows are in `/opt/ai/logs/arm-*.log`), so on the evidence the engine wrote down, the
divergent start used the same chunk every neighbouring start used. The expert-slot line is a function of the
config (`--batch: 4 slot sessions on CUDA0` in the production shape, one slot in the gate shape), not of the
start, so it cannot separate the two either.

What I cannot give you: the per-run stdout of that T2 pass was not kept — the script lived in `/tmp`. I can
restate the hashes (`aac0a04d109391fe` for the nine, `00281f843ecd3543` for the divergent first request) from
the contemporaneous record I keep for this box (§7.6 of its tuning plan, which has dated backups), but not
re-derive them from engine logs. The script is in git now and future runs append to `/opt/ai/runs`, so if it
happens again there will be a file behind it.

Two things from the same window that I think belong near the cold-start line, because both make "restart it
until it matches" misleading:

- 11 consecutive starts at 2026-10-23 05:17:56–05:20:54 printed `is a native (IQ) pack: it needs --native
  SHARD1, --spec T (T >= 2) and --prefill CHUNK` and exited, 18 s apart. A config the pack refuses restarts
  like a crash loop; someone counting restarts would call that instability.
- `ggml_cuda_init: failed to initialize CUDA: no CUDA-capable device is detected` appears in 70 of those 71
  starts and comes from the CPU vision helper. It reads like a dead card and means nothing on this box.

Check (b) — pin `--prefill N` above the prompt and repeat the restarts — needs the restarts, so it goes into
the next window I am allowed to take the box down for; folding it into the mode-8 revert/install cycles is the
cheapest way to get it. Until then the fallback you propose is what our evidence supports, and I would add
the honest half-sentence: we have not attributed the divergence, and the one mechanism we could check
against the log did not move.
