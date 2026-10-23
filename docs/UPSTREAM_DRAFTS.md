# 上游回帖草稿（**未发布**，等 atmJava 确认形式）

规则依据：CONTRIBUTING.md —— 先说问题与结果，再说做法，验证部分把"跑过的 / 跑挂的 / 没跑的"分开写，
读代码得到的结论标 *unconfirmed static analysis*，没测的要写出来。形式建议：**先评论贴数据**，
PR 只在维护者表示愿意接的时候开（这条是 §10 第 3 项待裁决）。

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
GSQ-RCO IQ3_XXS. We run `STRATA_SM75_PERF=1` in production today. We have **not** measured
`STRATA_EXP_MODE` — we are on the shipped mode-4 default for Turing.

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

**What we did not test.** `STRATA_EXP_MODE` at all, so we add nothing to the mode 4 / 0 / 8 comparison
yusheng227507 and Adamyno have run; the Q2_0 pack; three-card splits; engine 0.1.48.

---

## 草稿 C —— 若 mode 8 在本机成立，再贴 #2197 一条（**等 L-006 测完再写**）

