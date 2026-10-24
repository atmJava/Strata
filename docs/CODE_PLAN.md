# 代码优化计划（CODE_PLAN）· 2026-10-24 · v1

配套：[`RULES.md`](RULES.md)（怎么干活）、[`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md)（已有结论）、
[`HANDOVER.md`](HANDOVER.md)（状态与决策）。本文件只回答一件事：**接下来写什么代码、为什么、怎么证明、怎么回滚。**

## 0. 为什么是现在、按什么顺序

参数/开关这条线已经试尽（L-036…L-044）：采纳的只有 `HC_Q8`（4 并发 +3.8…+7.1%），其余全是否决或惰性；
而**同一配置跨 boot 的漂移量到过 9%**（L-040），意味着**小于 ~5% 的改动在这台机器上无法证明**。
所以后续只做两件事：有**物理余量**的底层内核，和**结构级**的调度改动；每一件都必须能被 ≥5% 的效应证明。

**测量协议（对所有 P 生效，写死在 `tools/server/` 的驱动里）**
- `bench-strata.py`：**每臂 ≥10 次、两臂交替**（A,B,A,B…），报 median 与 p10/p90；优先同 boot，跨 boot 只认成对。
- 判据①用 `agent_budget_ab.py`（14 任务，每臂 ≥2 次）。
- 逐位门：`parity_window.py` / `moe_hit_parity`；质量门：`quality_eval.py`。
- 达不到 5% 的效应 → 结论写"不可分辨"，**不采纳**。

---

## P1 · sm_75 的 IMMA 解码专家内核（唯一有 2.5–3× 物理余量的地方）

### 现状（源码事实，均已核对）
- `src/kernels/cuda/s2_expert_grouped.cu`：`gu_kernel` / `down_kernel`（**一 warp 一行**，`STRATA_DP4A`），
  `gu_pair_kernel` / `down_pair_kernel`（多行变体）；`moe_hit_grouped_s2` 是 resident 专家的入口
  （`expert_source.cpp:5299`），也就是相图里 **`VRAM hits` 那 1.71 ms/窗口（12.6%）**。
- **算术契约**（`s2_expert_grouped.cu:70–125`，逐字核过）：IQ3_XXS 是 2-bit 码、8 字节 = 32 元素、
  权重 scale fp16 **每 64 元素**、激活 q8_0 每 32 元素一块（34 B = fp16 scale + 32 int8），
  每 32 元素块累加 `dw*dx*(Σ code·x − Σ x)`。
- **微基准证据**（`expdec_bench`，L-039）：T=4 时这条内核只跑到 **29–40% 峰值 DRAM**；而且 4 路聚合只有单流的
  1.8×（若是带宽受限该接近 4×）→ **算力 / 延迟受限**，正是张量核能打的地方。
- 2080 Ti（TU102）：`mma.sync.aligned.m16n8k32.s8.s8.s32` 的 INT8 吞吐比 SIMT `dp4a` 高一个数量级。

### 设计
1. 新开关 **`STRATA_GU_IMMA=1`**（默认关；默认路径一个字节不动）。
2. 对「单专家 + ≤8 行」的 gate/up 与 down 走 IMMA：**每 32 元素 K 片一次 MMA**（`m16n8k32`，A=行×32 的 int8 码、
   B=32×8 的权重量化值），int32 累加。**整数加法可交换 → 与 dp4a 版本逐位相同**；float 只做逐块的 `dw*dx*(s−hx)` 缩放。
3. **解包只做一次**：2-bit 码 → int8 在 shared memory 里展开，供该专家的所有行复用
   （现在 dp4a 版是"每行每块"重复解包，这本身就是一条独立收益）。
4. 作用域：只覆盖 **resident（hit）** 专家；miss / 流式 / peer 路径不动。

### 风险与止损（写死）
- **解包可能吃掉全部收益**（EXPDEC 在 T=1 反而慢 23% 就是这类教训）：先只做 gate/up，量 `expdec_bench`；
  若比 dp4a 慢，就**只保留 down 投影**或整个放弃，不留半吊子。
- 逐位门：`moe_hit_parity`（现成，逐位 memcmp）；性能门：`expdec_bench` → `bench-strata`（≥10 臂）。
- 回滚：`STRATA_GU_IMMA=0` 或删 env（无需改代码）。

### 交付拆分（两次提交）
- **P1a（骨架 + 正确性）**：`s2_expert_grouped.cu` 新 kernel + dispatch 一行（`expert_source.cpp`），
  `moe_hit_parity` 全绿；性能未必要赢（先证明逐位）。
- **P1b（性能收尾）**：`expdec_bench` 对照（def vs imma，T=1..4，全部格式对），再 `bench-strata` A-B-A。
- 规模估计：400–700 行；预计 2–3 个回合（含两轮重启测量）。

### 成功判据
`expdec_bench` 在 18/20 与 18/42 的 T=4 上 ≥30% 快（当前 def 200.5 / 188.1 µs），且逐位相同；
`bench-strata` 4 并发 ≥5% 提升（≥10 臂）。

---

## P2 · 把 LM head 的 vocab 劈到两张卡（上限 ~3%，**先不做**）

- 事实：head 1.0 ms/窗口（7.4%），675 MB/窗口、**675 GB/s 已贴着 616 GB/s 的 HBM 上限** → 只能靠"两边各读一半"。
- 难点：最后一段才是瓶颈；要 GPU0 在空闲时接手半张表（+337 MB 显存，当前只剩 0.8–1.0 GB）；
  残差要反向过卡；采样请求还要把对侧 logits 拉回来。
- 上限 3% < 噪声 → **在 P1 之前不做**；只有当 P1 落地后噪声仍无法降低、且需要挤这 3% 时才动。

## P3 · 结构级（上限大、风险大，暂不排期）

- 事实：4 路聚合 135 tok/s 对单流 75 = 1.8×，说明批窗口在**算力**上并不宽裕；
  `--batch-mtp` 在 `--batch-groups > 1` 时被**代码直接禁用**（`generate.cpp:5320`），
  上游注释写明原因："流水组跑不了槽内草稿；4 路 2 卡时流水组比串行 MTP 快 25–30%" → **不要再打 batch-mtp 的主意**。
- `--pipeline-windows`（L-044）是唯一被上游写好的"窗口重叠"机制，在 CUDA 上默认关；确认结果见台账。
- 再往上（按专家亲和性给窗口分组、跨会话调度）是研究型改动，**不排期**，除非 P1 之后仍有 ≥10% 的目标需要。

---

## 不做清单（都已实测否决，别再捡）

MMQ 指针表（L-031）、TP 补丁（L-033）、`QFUSE`（L-043）、`EXPDEC`（L-039）、`PF_FUSED_SM75`（L-041）、
层切分配平（L-037/L-040）、会话 parking（L-032）、read-ahead（L-025）、升 0.2.4.1（L-034）。

## 每轮结束的固定动作

台账一条（含原始日志路径与"没测什么"）→ HANDOVER §13 的"下一轮先做"改成一条 → 提交推送 →
复盘（cron/timer、残留进程、`vram-perf.csv`、重启次数）。
