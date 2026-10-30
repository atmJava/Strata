# 资源利用率优化计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Version:** v4，2026-10-30 重写。目标从"提高资源利用率"提升为"逼近双 2080 Ti 的硬件上限"：
解码 200 tok/s、预填 5000 tok/s、多路稳定、冷会话快读、重启快加载。v3 的并发 MTP 与预填让路
被吸收为本计划的 W1/W3；内核优化降为条件项。

**Goal:** 用性能账本驱动，先拿结构性乘数（每窗口 token 数、预填复用、启动并行），再拿百分比
优化（kernel、同步、launch），最终让真实会话场景达标。

**Architecture:** 把系统拆成四个可计算的部分——解码窗口、预填流水、缓存与调度、启动阶段。
每个目标先反推所需乘数，再选杠杆；每个候选都有 A/B/A 门限与服务器侧自愈回滚。

**Tech Stack:** Ubuntu、CUDA 13、sm_75、双 RTX 2080 Ti（层切分 24，2 段 2 卡）、
Qwen3.8-Flash-Next（`qwen4exp`，IQ3_XXS 71 GB、专家包 42.9 GB）、Strata batch /
batch-groups / MTP / self-commit / layer-major prefill / expert cache / conversation cache。

---

## 1. 需求翻译

| 你的目标 | 可测定义（本计划口径） | 判定场景 |
| --- | --- | --- |
| Decode 200 | 6 路聚合稳定 ≥200 tok/s，连续 90 s 的 p10 ≥190 | S5 |
| Decode 200（单路） | 单路 400 token 中位 ≥200 tok/s | S1，单独记为 stretch |
| Prefill 5000 | 32K 冷预填 ≥5000 tok/s；200K 冷预填 ≤45 s | S3/P3 |
| 多路会话 | 6 路 ×192K 无饥饿；每路 ≥30 tok/s，聚合 ≥200 | S5 |
| 冷会话快速读 | 50K ≤15 s；200K ≤45 s；前缀命中 90% 的 200K ≤10 s | S3 |
| 重启快速加载 | 热重启（页缓存已热）≤60 s；冷启动 ≤90 s | S6 |

两种口径必须分开：**聚合 200** 是"把已有 164.7 提高 21%"，**单路 200** 是"把 64.8 提高
3.1 倍"。前者靠批处理与窗口开销就能到；后者必须改变"每窗口 token 数"，不是调参能保证的。
本计划把聚合 200 作为硬目标，单路 200 作为有 go/no-go 门的 stretch，见第 5 节。

## 2. 性能账本（先算清楚钱花在哪）

### 2.1 解码：每窗口 token 数 ÷ 窗口时间

实测（2026-10-30，生产日志与并行会话测量）：

| 路数 | 聚合 tok/s | 每窗口 token | 窗口 ms | 每路 tok/s |
| --- | --- | --- | --- | --- |
| 1 | 64.8 | 1.74 | 26.8 | 64.8 |
| 2 | 75.8 | 2.00 | 26.4 | 37.9 |
| 6 | 164.7 | 6.00 | 36.4 | 27.5 |

单路细节：200 token / 85 个被接受草稿 → 115 个窗口；105 个草稿 → **0.91 草稿/窗口**，
接受率 81%。也就是说 `--spec 8 --mtp-max-t 4` 的窗口实际平均只发 0.9 个草稿。

结论：

- 加一路几乎不增加窗口时间（26.8 → 26.4 ms），所以**两路掉一半是草稿缺失，不是带宽**。
- 窗口固定成本约 24–26 ms；6 路时每路摊销后仍要 36.4 ms/窗口。
- 聚合 200 @6 路 → 窗口 ≤30 ms（当前 36.4，需 −17%）。
- 单路 200 → 需要 5.4 token/窗口（当前 1.74）或窗口 13.5 ms + 2.7 token/窗口；
  即**接受草稿要从 0.74/窗口 提到 1.7–4.4/窗口**，同时窗口不能变慢。

### 2.2 预填：chunk ÷（计算 + 专家流 + 同步）

实测：201K 冷预填约 100 s ≈ **2.0K tok/s**；日志里 `--prefill-pipe 1` 把 36K 提示切成
7424 token、2 段流水。启动日志确认：

- 专家包 42.9 GB，两卡常驻缓存合计约 28 GB；
- 未常驻补集 **19.62 GiB 被 pin 在 RAM**（启动时 58–62 s 完成）；
- 层优先预填默认"每 32K super-chunk 每层专家只读一次"，但 `--prefill-pipe` 计划会把
  layer-major 关掉（日志原话），于是专家按 7424 chunk 反复读。

PCIe 账本（PCIe 3.0 x16 实测有效约 12 GB/s）：

| chunk | 每 chunk 补集流量上限 | 5K tok/s 所需 PCIe | 可行性 |
| --- | --- | --- | --- |
| 7424 | ≈19.6 GiB | 13.2 GiB/s | 贴着上限，基本不可达 |
| 16384 | ≈19.6 GiB | 6.0 GiB/s | 可行，需 ~50% 有效利用率 |
| 32768 | ≈19.6 GiB | 3.0 GiB/s | 宽裕，回到计算/同步瓶颈 |

计算侧：3B 激活参数 × 2 FLOP × 5000 tok/s = 30 TFLOP/s，双卡各 15 TFLOP/s，
约为 sm_75 FP16 峰值的 14%，算力不是硬墙。**预填的硬墙是专家补集的 PCIe 复用。**

### 2.3 冷会话与缓存

- 200K @5000 = 40 s；50K @5000 = 10 s。
- 当前前缀命中率 92.7%，但日志 7 次"长会话无法停车"（95K–216K），因为没有每槽 draft K/V。
- 所以冷读要两条腿：提高预填 tok/s（W3）+ 让长会话能停车复用（W4）。

### 2.4 启动：一条串行流水

实测启动阶段（多次重启日志）：

| 阶段 | 耗时 |
| --- | --- |
| dense 权重 831 MiB | 10–11 s（冷）/ 1.3 s（热） |
| 原生投影 1313 MiB | 0.4–1.5 s |
| MTP draft 837 MiB | 1.1–3.7 s |
| 专家槽预填 10065 slots | 15–25 s |
| **补集 19.62 GiB pin + map** | **58–62 s** |
| vision 模型 | 0.7–11.4 s |
| CUDA 初始化、图捕获、元数据等 | 其余约 40–60 s |
| 合计 | ≈165 s |

目标 60 s 意味着必须动最大的一项（pin）与串行顺序，而不是换一个更快的磁盘。

## 3. 当前代码与架构

请求路径：准入（`STRATA_ADMIT_*`、短请求优先）→ 预填（chunk、layer-major、prefill-pipe、
BYIELD、lead-in）→ 批窗口（`--batch`、`--batch-groups`、self-commit、MTP 草稿、verify/commit）
→ 采样与流式返回。

三条解码路径各自为政：

| 能力 | solo | batch `groups=1` | pipelined `groups=2` |
| --- | --- | --- | --- |
| MTP 草稿 | 开（平均 0.91 草稿/窗口） | 需 `BATCH_MTP=1` | 需 `BATCH_MTP=1` + `PIPED_MTP=N` |
| 窗口自提交 | 不适用 | 有（T=1 窗口） | 与 `PIPED_MTP` 互斥（`generate.cpp:5256`） |
| 预填并发读 | 不适用 | 不适用 | 需无 staged/helper/pcie-dma；现网被 staged 源关掉 |
| 每槽 draft K/V、停车 | 有 | 仅 `BATCH_MTP` | 仅 `PIPED_MTP` |
| 生产现状 | — | — | 草稿关、并发读关、停车缺 |

关键代码点：

- `src/program/generate.cpp:5037-5097`：batch MTP 门槛与分组解析。
- `src/program/generate.cpp:5251-5275`：`PIPED_MTP` 最终判定；`SELF_COMMIT` 一票否决。
- `src/core/verify.cpp:181-200`：self-commit 逐窗口生效（`one_row_per_slot`）。
- `include/strata/program/batch_read.hpp:44-56`：并发读关闭原因清单。
- `kVerifyMaxT=8`：每窗口最多 8 行；`batch=6, groups=2` 时 `PIPED_MTP=1` 是唯一可行档。

## 4. 设计理念

1. **乘数优先**：先改"每窗口 token 数""每 super-chunk 专家读次数""并行启动阶段"，
   再改 kernel 的百分比。
2. **窗口是解码的原子**：任何解码改动都要回答"每窗口多几个 token、窗口慢几毫秒"。
3. **预填是复用问题**：5000 tok/s 的关键是让同一份专家只读一次服务更多 token。
4. **每槽状态一等公民**：草稿、K/V、停车、公平性必须对每个 slot 成立。
5. **组合必须显式**：能力组合要么可用，要么有具名、可观测、可测试的降级。
6. **按场景选策略**：2 路与 6 路用不同的组/草稿策略，不追求一套参数。
7. **先证据后改码**：任何代码改动前，先有窗口/阶段/预填/启动的实测账本。
8. **可回滚**：每轮停服都有服务器侧自愈与 `layer=relaunch` 收尾。

## 5. 可行性裁决

| 目标 | 当前 | 缺口 | 判断 | 关键杠杆 |
| --- | --- | --- | --- | --- |
| 聚合 200 @6 路 | 164.7 | +21% | **可达**（W1+W2） | 并发草稿、窗口固定成本、组数 |
| 单路 200 | 64.8 | 3.1× | **条件可达**，W2 后 go/no-go | 草稿链 1.7–4.4/窗口 + 窗口 13.5–27 ms |
| 预填 5000 | ≈2.0K | 2.5× | **条件可达**（W3） | 16–32K super-chunk、layer-major、overlap |
| 6 路 ×192K | 164.7（短） | 长上下文待测 | 可达 | 停车、公平性、准入 |
| 200K 冷读 ≤45 s | ≈100 s | 2.2× | 跟随预填目标 | 同 W3 |
| 热重启 ≤60 s | ≈165 s | 2.75× | 可达（W5） | 跳过重复 pin、并行阶段、懒加载 vision |

单路 200 的数学不会因为"再调一调"而改变：当前窗口 26.8 ms 只能装 1.74 token。要么把
接受草稿提到 4.4/窗口，要么把窗口压到 13.5 ms 且草稿 2.7/窗口。W2 阶段会用实测数据裁决
"继续投资"还是"把 200 改成聚合口径"。

## 6. 里程碑与工作流

### M0：自愈门 + 性能账本（阶段 0–1）

**目的**：先保证可回滚，再把四个部分的基线量出来。

- [ ] **基础 1：自愈演练**：`util-restore.sh` / `util-watchdog.sh` / `dump-runtime.py`
  部署并真实演练，要求 `WINDOW RESTORED ... layer=relaunch`。脚本正文见 commit
  `fa6bfafd` 的附录 A。
- [ ] **基础 2：正式快照**：冻结 engine/server/config + runtime JSON + manifest。
- [ ] **基础 3：解码账本**：单路、2 路、6 路各 3 轮，记录聚合/每路 tok/s、`draft_n`、
  窗口数与窗口 ms（用 `--stats` 的 `strata decode timing:` 与 `strata decode GPU stages`）。
- [ ] **基础 4：预填账本**：32K、128K、200K 冷预填各 3 轮，记录 chunk 数、每 chunk ms、
  实际读 token 与 tok/s。
- [ ] **基础 5：启动账本**：重启 3 次，记录四个阶段耗时（权重、槽预填、pin、vision/其它）。

Expected: 基线与本文件第 2 节差距 ≤10%。差距更大时先查环境，不进入 W1。

### W1：解码乘数（并发草稿）

**目标**：两路每路 ≥50、6 路聚合回到 ≥190；为单路 200 打基础。

- [ ] **W1-1 候选 A**：`BATCH_MTP=1`、`PIPED_MTP=1`、`SELF_COMMIT=0`（6 槽 2 组 = 6 行 ≤8）。
- [ ] **W1-2 草稿深度**：在 `PIPED_MTP=1` 下对照 `--spec-min-p` 与 `--mtp-max-t`，
  只测"每窗口草稿数/接受数"，不测别的。
- [ ] **W1-3 结果门**：两路 `draft_n>0`、每路 ≥50；6 路聚合 ≥190；无 OOM/降级。

Expected: 引擎日志出现 `STRATA_PIPED_MTP ... draft up to 1 MTP proposal`；失败时记录引擎
拒绝原因（行数、显存、helper cache），不叠加改动。

### W2：解码窗口效率（固定成本）

**目标**：6 路窗口 ≤30 ms（聚合 ≥200）；给出单路 200 的 go/no-go。

- [ ] **W2-1 定位固定成本**：开 `--stats`，抓 `decode timing` 与每 stage ms/window，
  分清 launch、同步、PCIe 尾巴、采样各占多少。
- [ ] **W2-2 候选**：组数 1 vs 2、self-commit on/off、CUDA 图覆盖、KV 批处理；
  一次只动一个，A/B/A。
- [ ] **W2-3 单路推演**：用实测窗口与草稿分布计算单路 200 需要的变化；达不到就转
  "单路 120–150 + 聚合 200"路线，并写明差多少。

Expected: 6 路聚合 ≥200；单路 200 的裁决有数据、有公式、有停止点。

### W3：预填吞吐（专家复用 × 流水）

**目标**：32K 冷预填 ≥5000；200K 冷 ≤45 s。

- [ ] **W3-1 layer-major 对照**：`--prefill-pipe 1`（当前）vs 去掉 pipe 让 layer-major
  按 32K super-chunk 复用专家。
- [ ] **W3-2 chunk 扫描（只 3 档）**：8192 / 16384 / 32768，记录 tok/s 与输出 parity
  （>8192 的非位精确风险必须记录）。
- [ ] **W3-3 重叠**：专家流与计算重叠、两段流水；若配置做不到，立一个"layer-major +
  pipelined stages"代码项。
- [ ] **W3-4 结果门**：32K ≥5000、200K ≤45 s；若只到 3500–4500，保留并进入 W6 内核项。

Expected: 用 PCIe 账本解释每个数字；达不到 5000 时必须能指出是流量、计算还是同步。

### W4：多路调度与冷会话

**目标**：6 路 ×192K 无饥饿；50K ≤15 s、200K ≤45 s；90% 前缀 ≤10 s。

- [ ] **W4-1 停车**：W1 的 MTP 打开后，验证 conversation cache 能停车 95K–216K；
  日志不再出现 `not parked ... without --batch-mtp`。
- [ ] **W4-2 公平性**：6 路同跑 + 一路 200K 冷读，记录每路 tok/s 与最大停顿；
  需要时用 `PIPED_READ_PIECE` 让预填在 block 边界让路。
- [ ] **W4-3 冷读**：50K/200K 冷读与前缀命中各 3 轮。

Expected: 6 路每路 ≥30、聚合 ≥200；冷读达标；无 slot 饥饿。

### W5：启动加速

**目标**：热重启 ≤60 s，冷启动 ≤90 s。

- [ ] **W5-1 并行化**：权重、槽预填、vision、MTP 并行；vision 懒加载。
- [ ] **W5-2 去掉重复 pin**：页缓存已热时跳过/延后 19.62 GiB pin；失败自动回退当前路径。
- [ ] **W5-3 结果门**：3 次热重启 ≤60 s，冷启动 ≤90 s，health loaded 与 parity 正常。

Expected: 启动账本逐项下降，不接受"总时间变短但某项更慢"的假象。

### W6：内核与平台（条件项）

只有 W1–W3 后仍差目标、且 `--stats`/Nsight 证明瓶颈在 kernel 时才启动：一次一个 kernel，
一次一个机制，复用 v3 的受控 NCU 方法（`tools/server/ncu-exec.py` + 自愈窗口）。

## 7. 场景矩阵

| 编号 | 负载 | 主要指标 |
| --- | --- | --- |
| S1 | 单路 4K prompt，400 token | 中位/p10 tok/s、窗口 ms、草稿/窗口 |
| S2 | 两路并发 400 token | 每路、聚合、`draft_n` |
| S3 | 两路 + 200K 冷预填 | 邻居 tok/s、最大停顿、预填总时长 |
| S4 | 24K 工具调用 + 1 路聊天 | 工具调用总时长、邻居 tok/s、p95 停顿 |
| S5 | 六路 ×192K | 聚合、每路 p10、饥饿、stall |
| S6 | 热重启 / 冷启动 | 各阶段秒数、总秒数 |

## 8. 测量协议

- 每项 ≥3 轮，报中位与 p10/p90；A/B/A，A1/A2 差 >5% 时加到 5 轮。
- 所有窗口都有自愈；窗口硬截止 30 分钟、接管点 15 分钟；每个窗口 `layer=relaunch`。
- 质量门：greedy parity、needle、`ctx256` 一小时 soak。
- 证据落 `/opt/ai/logs/util-v4-$TS/`，含原始日志、JSON、判决与回滚路径。

## 9. 停止条件

- 基线无法在 ±10% 内复现：停止，先查环境。
- 引擎拒绝候选能力（行数、显存、helper cache）：记录原因后停止该工作流，不叠加。
- OOM、swap、槽位降级、parity/needle 失败：立即回滚。
- 6 路聚合回退 >3%、单路回退 >3%：候选不采纳。
- 自愈没有 `layer=relaunch`：停止全部停服动作。
- 单窗口 >20 分钟或累计 >8 个：停止复盘。

## 10. 最终验收门

- 聚合：6 路 ≥200 tok/s，90 s p10 ≥190；每路 ≥30。
- 单路：≥120（M2）；200 作为 stretch，只有 W2 判可达才纳入验收。
- 预填：32K ≥5000 tok/s；200K 冷 ≤45 s。
- 冷读：50K ≤15 s；90% 前缀 200K ≤10 s。
- 启动：热重启 ≤60 s；冷启动 ≤90 s。
- 质量：parity、needle、soak 全过；所有窗口 layer=relaunch。

## 11. 关键命令速查

```sh
# 解码账本：两路并发，记录每路/聚合 tok/s 与 draft_n
python3 /opt/ai/bin/session_matrix.py --streams 2 --tokens 400 --rounds 3 \
  --label v4-s2 --out "/opt/ai/logs/util-v4-$TS/s2.jsonl"

# 窗口与每阶段耗时（引擎需带 --stats）
grep -aE "strata decode timing:|strata decode GPU stages" \
  /home/atm/verify-20261027/strata-verify/strata-verify.log | tail -4

# 预填账本：32K 冷预填 3 轮
python3 /opt/ai/bin/cold-prefill.py v4-32k 32768 3

# 能力行：请求了草稿/并发读是否真的生效
grep -aE "STRATA_PIPED_MTP|WARNING: .* is off|PIPED_CONCURRENT is off" \
  /home/atm/verify-20261027/strata-verify/strata-verify.log | tail -5

# 启动账本：四个阶段的耗时
grep -aE "weights loaded|pre-filled .* slots|pinned cache complement|model files loaded" \
  /home/atm/verify-20261027/strata-verify/strata-verify.log | tail -8
```

候选 A 的配置仍按 v3 的约定：复制冻结 `server-runtime.json`，把 `--config` 换成候选配置，
写 `$SNAP/live-runtime.json`，清进程后 `util-restore.sh`；回到冻结候选时删除
`live-runtime.json` 再恢复。脚本正文见 commit `fa6bfafd` 的附录 A。
