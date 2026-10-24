# 上游回帖（A/B/C **已于 2026-10-24 02:08 UTC 发出**）

| 草稿 | 贴到哪 | 状态 | 链接 |
|---|---|---|---|
| A（#2275 并发读：gate 的判定条件） | #2275 | 已发 2026-10-24 02:08:13Z | [issuecomment-6378566315](https://github.com/Niko1221/Strata/issues/2275#issuecomment-6378566315) |
| B（#2197 第七台 sm_75 + 那条例外） | #2197 | 已发 2026-10-24 02:08:15Z | [issuecomment-6378566608](https://github.com/Niko1221/Strata/issues/2197#issuecomment-6378566608) |
| C（#2197 mode 8 的本机数据） | #2197 | 已发 2026-10-24 02:08:16Z | [issuecomment-6378566819](https://github.com/Niko1221/Strata/issues/2197#issuecomment-6378566819) |

发之前的状态（留档）：A/B 早先写好未发，C 是 2026-10-24 按 L-011/L-012 的数据新写的；
atmJava 批准"先写 C，三份一起发"。发之前核过上游动静：#2197 最后一条是 rafatxf 2026-10-23 21:54Z
（在核 yusheng227507 的 DFlash2 那一行到底跑的是什么），#2275 最后一条是 Adamyno 2026-10-22 21:09Z，
#3724 仍然 0 评论，**#2326 已 closed**（Niko1221 2026-10-19：改动已在 0.2.0-beta 的 main 里，跟踪器重置所以关掉）。

规则依据：CONTRIBUTING.md —— 先说问题与结果，再说做法，验证部分把"跑过的 / 跑挂的 / 没跑的"分开写，
读代码得到的结论标 *unconfirmed static analysis*，没测的要写出来。**三份都只是评论，没有开 PR**
（PR 等维护者表示愿意接：A 里那句"要不要改 gate 判定条件"就是去问这个的）。

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

