# 多路并发推理融合计划（MULTIPATH_PLAN）· v0.1 草案 · 2026-10-24

> 配套：[`RULES.md`](RULES.md)（怎么干活）、[`HANDOVER.md`](HANDOVER.md)（状态与决策）、
> [`CODE_CONCLUSIONS.md`](CODE_CONCLUSIONS.md)（已封的路）、[`MULTIPATH_STANDARD.md`](MULTIPATH_STANDARD.md)（新标准）、
> [`MULTIPATH_RULES.md`](MULTIPATH_RULES.md)（执行规则与复盘模板）、[`MULTIPATH_LOG.md`](MULTIPATH_LOG.md)（每轮复盘）、
> [`FEATURE_PARITY.md`](FEATURE_PARITY.md)（功能清单、测试用例与验收标准）、
> [`../tools/server/README.md`](../tools/server/README.md)（工装）。
>
> **状态：草案，待用户过目。** 本文件只回答"要做什么、怎么证明、怎么回退"；批准前**不动生产**。
> 第一轮（W1）是**只读测量**，没有重启、没有配置改动。

## 0. 一页纸

**目标**：在 `atm@192.168.3.14` 这台 2×2080 Ti 服务器上，让 **4 路并发推理**——4 条流同时解码、新请求随时能进来——
成为可验收的常态。借 `weicj/vLLM-2080Ti-Definitive` 的 **continuous batching 语义**（late-join prefill、
chunked prefill 与 decode 混批、按路定容），**不搬它的代码**。

**判据**（沿用 `HANDOVER.md` §1，不放宽）：L1 真实 agent 墙钟 > L2 4 路聚合 decode 与 TTFT p50/p90 > L3 单流。
同 boot A-B-A、每臂 ≥3 次、中位+sd、**<3% 不算结论**（跨 boot 小值按 ~7%，4 路跨 boot 漂移曾到 9%，L-040）。

**现状**：机器余量在并发——单流 63 tok/s、三流 136 tok/s（`CODE_PLAN.md` §24）；但**满 4 槽时新请求的读要排队
36–43 s**（L-020/L-021），读与解码在批次边界上串行。

**缺口**：① **无槽准入（read-ahead）**——满槽时读根本发不出去（L-022/L-024/L-025，引擎代码活）；② **读时分段
让解码继续**——上游已有开关 `STRATA_PIPED_READ_PIECE` 等，但生产没开（W2 先试，免代码）。

**四个工作流**：W1 基线复现（0 重启）→ W2 配置臂（≤2 重启）→ W3 无槽准入（引擎，2 次重启 + 1 次构建，
分 R3/R4 两轮）→ W4 定容与预设（1–2 重启）。详见 §4、§9。

## 1. 需求与判据（照抄既有，不再发明）

- **目标函数**（`HANDOVER.md` §1）：真实 agent 任务完成墙钟（含思考 token 数、**空答案率**、工具调用通过率）
  优先；其次 4 路聚合 decode 与 TTFT p50/p90；**最后**才是单流 tok/s。
- **每轮六步**（`RULES.md` §2 五步 + `MULTIPATH_RULES.md` 的复盘必做）：事前审查 → 计划（含"结果为 0 怎么办"）→
  执行（一次一个变量）→ 验证（原始日志 + 数字 + 是否逐字节恢复）→ **复盘（写 `MULTIPATH_LOG.md`）** →
  归档（台账 + HANDOVER + 服务器文档）。
- **批准边界**（用户 2026-10-24 明确）：**引擎也可以大改**；"什么对我好就移植融合什么"；目标机就是这台生产机。
  停机决定权在会话里，但纪律照旧：低峰跑、`cp -p` 快照、测完逐字节恢复并 `cmp -s`、台账写清停了多久。
- **上游只读**（`RULES.md` §1）：不回帖、不开 PR、不提交任何东西。
- **不自污染**（`HANDOVER.md` §7/§9.16）：本会话的 token 由这台机器生成；测量期间不生成、**不派子代理**；
  后台任务发起后立刻 `job_output wait` 阻塞等。

## 2. 现状（全部已测，引台账；不是推断）

| 项 | 数字 | 来源 |
|---|---|---|
| 单流 decode | 63 tok/s（T≈2.05） | `CODE_PLAN.md` §24 |
| 三流并发 | 每流 45、聚合 **≈136 tok/s** | `CODE_PLAN.md` §24 |
| 四流聚合 | 121–125 tok/s（L-019 三臂带内） | L-019/L-020 |
| 窗口成本 | ≈12 ms 固定 + ≈4.4 ms/token | `CODE_CONCLUSIONS.md` |
| 满槽冷读排队 | **36–43 s**（读 11.0–14.8 s 在引擎里，其余是排队） | L-020/L-021 |
| 真实流量 TTFT | 中位 0.80 s、p90 2.46 s、最长 83.82 s（n=34） | L-021 |
| gate ON 的代价/收益 | 读的人 +21.6%（2 流）/+44.5%（3 流）；邻居停顿 **11.64 → 0.70 s** | L-019/L-020 |
| 每轮读的固定成本 | ≈0.7 s + 1,370 tok/s（45K 大读 2,275 tok/s） | L-026 |
| 槽会话成本 | 0.56 GiB/槽（**RTX 5070 实测，不可横搬到本机**） | `BATCHING.md:145` |
| 上游读分片实测 | 2×RTX 5090：最长停顿 12.2 → 4.2 s、读的人 TTFT 6.9 → 8.1 s、流 token 289 → 527 | `BATCHING.md` §287（**其他机型，不可横比**） |

**机制盘点**（有 / 无，决定 W2 能不能免代码）：

| 机制 | 状态 | 备注 |
|---|---|---|
| `--batch 4` / `--batch-groups 2` | **有，生产** | 槽数=准入上限，没有"少准入"旋钮（L-022） |
| `BGEN` 运行中入批 | **有** | 批在跑时收新行；除 `BSTOP` 外都会先排空在飞窗口（`generate.cpp:11953-11958`，L-024） |
| `BYIELD` 短提示让路 | **有，分路径** | `batch_read_piece` 对 piped 返回 0（`batch_read.hpp:24`）；但**开 `STRATA_PIPED_READ_PIECE` 后**，piped 路径在每个 piece 边界检查 BYIELD（`generate.cpp:13877-13885`）→ W2 打开后可用 |
| `STRATA_PIPED_CONCURRENT=1` | **有；生产状态未核**（配置在服务器，静态只看得到开关） | 满槽时读发不出去（L-021）；只解决"有空槽时的共存" |
| `STRATA_PIPED_READ_PIECE` / `STRATA_PIPED_DECODE_{MAX,MIN}_MS` | **有，未开** | 只在 pipelined 组生效（`generate.cpp:13811`）；`STRATA_BATCH_DECODE_SHARE` **默认 0.5、单 GPU 一直生效**（`13421-13424`），pipelined 要配合 READ_PIECE；`piped` 要求 layer split（`11741`）；**W2 的对象** |
| 无槽准入（read-ahead） | **无** | BGEN 才是触发读的命令（`12006-12007`）；**今天没有暂存**：读直接写主会话 `ss`（`13535`），槽只在最后 `copy_to_slot`（`15394`）；要（i）无槽读命令（ii）新的暂存（iii）准入 adopt（替换 BGEN 的读、走 `BADM`）；L-025，另见下方更正 |
| K/V 落地 + quiesce | **有约束** | `kvg_ensure(n+256, kv_quiesce)`（`12539-12542`）、增长路径 `15078-15079`；quiesce 要求设备全静（`10857-10861`） |
| 多路定容（每流 KV/槽预算） | **有参数，无预设** | `--kv-resident 32768`、`--conversation-cache-mib 12288`、`--vram-reserve-mib 1280/1435` |

**静态复核更正（2026-10-24，子代理核对 `src/`；未测）**：L-025 把 `_park_slot` 当成"会话 parking"是错的——
`serve/server.py:1636-1638` 是 BGEN 被拒后的 2 s 忙标（#1994），不是会话；会话 parking 在引擎
`--conversation-cache-*`（`generate.cpp:729-740`）与每槽 held prefix（`server.py:1166`、`2127-2157`）。
W3 不按 L-025 的"按槽记账"说法设计。

## 3. 借鉴清单（六格，按 `SERVER_ADAPT_REQUIREMENTS.md` §6.1）

| # | fork 的机制 | sm_75 前置 | 我们对应的现状 | 预期收益 | 成本 | 判据与回退 |
|---|---|---|---|---|---|---|
| B1 | continuous batching 的**步契约**：每请求是 cursor（`computed` vs `target`）；每步先喂饱 running decodes，剩余预算给 waiting prefill（2048）；晚到 prefill 只推迟"还没吐 token"的请求（`num_output_tokens==0`），已解码的流不停 | 无（调度语义，与 ISA 无关） | 部分有：BGEN 中途入批、BYIELD；**无统一 per-step 预算**；读仍停住全部槽 | 满槽冷读 36–43 s → 目标 ≤ 引擎读时间 + 2 s | W2 免代码；W3 中等工程 | 见 §4 W3；回退=换回原二进制路径 + 1 次重启 |
| B2 | 无槽准入 + KV 按块增量（paged）+ **全序列 KV 准入检查**（`scheduler_reserve_full_isl`：整条序列装得下才准入，不是只装第一块） | 无 | **无**；KV 预留要 quiesce（L-025） | 同上，且读与 4 条流真正并行 | 引擎 + 服务端改动 | 逐位门 + 质量门 + 4 路 bench 不回退 |
| B3 | 每路 KV 定容与启动门：`N=MAX_NUM_SEQS`、`CTX=floor(MAX_MODEL_LEN/1000)`；launcher 启动后强制 `N×CTX ≤ GPU KV tokens`；所有 profile 的 per-step 预算 = 2048 | 无 | 参数已有，**无预设、无启动门** | 4 路 × 256K 稳定、无截断 | W4 定容脚本/预设 | 1/2/3/4 路矩阵全绿 |
| B4 | SSD 前缀 KV 持久化 #243：`OffloadingConnector` + FS tier（`checkpoint-<sha256>`、每 block 一个 `.bin`、`PYTHONHASHSEED=0`），命中异步装入 `WAITING_FOR_REMOTE_KVS` | 无 | 无；重启冷加载 ~165 s | 重启后首个请求 TTFT | 中（存储 + 指纹） | 暂缓（P3）：重启不频繁，先做 P1 |
| B5 | MTP / DFlash2 投机解码 | 已支持 | **已有** `--spec 8 --mtp-max-t 4`，热接受率 ~99%（L-026） | — | 不借 | 已有；文件头 = 不做清单 |
| B6 | TP over NVLink | — | **已实测无位置**（decode 跨卡交接 ~410 KB/窗口） | — | 不借 | 已否决（`CODE_PLAN.md` §8） |

## 4. 工作流

### W1 · 基线复现（免停机，0 重启）

- **假设**：满槽冷读的 36–43 s 排队可复现；读的人 TTFT 是主要痛点，邻居停顿随流数恶化。
- **做法**：`bench-strata.py`（单流 + 4 路）、`piped_scenario.py`（一条流解码 + 一条冷读）、
  `gate_prod_streams.sh` 的只读部分、`turn-ttft.py`（多轮形状）；用 `api_monitor` 的 `first_token_s`（L-021）。
  同时跑 `tools/server/tests/` 的 **T1/T3** 记录当前值（T3 预期 xfail，xfail 消息里带数字）。每形状 ≥3 次，同 boot，交错。
- **约束**：测量期间本会话不生成、不派子代理；后台 job + `job_output wait` 阻塞等。
- **出口**：36–43 s 窗口的 p50/p90、邻居停顿分布、4 路聚合基线落到 `/opt/ai/logs/MP-*`；误差条 ≤ 噪声地板。
  **如果复现不出来**：先查当时有没有别的流（`/api/requests`、引擎日志行序），不硬下结论。

### W2 · 配置臂：读分片（≤2 重启）

- **假设**：`STRATA_PIPED_READ_PIECE=32768` + `STRATA_PIPED_DECODE_MAX_MS=400`（上游最好那臂就是这两条，
  `BATCHING.md:331`；`STRATA_BATCH_DECODE_SHARE` 默认 0.5，不必显式设）让读的人稍慢、邻居不停顿，
  3 路场景聚合不降；4 路场景预期**无效**（满槽时 BGEN 准入不到——这正是 W3 的动机）。
- **臂**：A = 生产（现跑着，不装）；B = 生产 + 读分片 env（装 1 次，~156 s）；恢复 A（装 1 次）。
  每臂内 3 次测量交错进行。**一次只改一组 env**。
- **判据**：邻居最长停顿 ≤1 s（gate ON 已有 0.70 s）且聚合不比 A 差 3%；读的人 TTFT 劣化 ≤30%
  （上游 2×5090：64K joiner +17%（6.9→8.1 s）、32K joiner +39%（6.1→8.5 s），聚合 token 率 173→166 = −4%；
  本机待测）。**<3% 记"未定"**，方向不一致记"未定"。
- **回退**：`use-config.sh <快照>`（快照在安装前 `cp -p` 留）。
- **出口**：有效 → 作为 W3 验收的 B 臂基线；无效 → 台账记否决 + 不做清单。

### W3 · 无槽准入 read-ahead（引擎，≤3 重启 + 1 次构建）

- **改动**（最小、默认关、独立 build 目录——`build/` 一字节不动，按 `RULES.md` §9）：
  1. **先看清现在的顺序**：BGEN 才是触发读的命令（`generate.cpp:12006-12007` 在 BGEN 分支里做读；没槽就
     `ERR BGEN: no such free slot`）；读今天直接写主会话 `ss`（`13535`；`copy_to_slot` 在 `11427-11433`），
     **没有暂存**；槽只在最后 `copy_to_slot`（`15394`）拿到 K/V。所以"先读后挂"要**新增暂存**，不是复用。
  2. **无槽读命令**（新命令，opt-in）：把 prompt 的 K/V 读进**不按槽记账**的暂存会话；读期间要让出控制行
     （服务端 `self.ctl` 从读开始持有到 `2003` 才释放，`1179`）。
  3. **准入 adopt**：槽空出后把暂存复制进槽（`copy_to_slot`，`15394`→`11427`；线上日志 `strata batch: slot 0
     takes … (copied in 42.6 ms)` 打在 `15463`）并让请求在槽里继续（`BADM`），**不再重读**；
     `15432-15464` 是 checkpoint 挑选/搬移，不是暂存复制。
  4. **K/V 落地**：`kvg_ensure` 的 quiesce（`12539-12542`；增长路径 `15078-15079`）要在满槽时也能完成——
     先量"ensure 多长、设备多快安静"（L-025 留的问题），再决定预分配还是延迟。
  5. **服务端**：`pick_slot`（`serve/server.py:2126`）只选槽；真正发 BGEN 的是 `generate_batched` 的等槽循环
     （`1876-1909` → `1931/1940`）；拒绝路径 `1974-1984`（#1994 GpuBusy）要重审。
- **验收**：4 路 + 冷读：读的人 TTFT 从 36–43 s 降到 ≤ 引擎读时间 + 2 s；邻居停顿不劣于 W2-B；4 路聚合不回退。
- **门**：`parity_window.py`（逐位）+ `quality_eval.py`（质量）+ `bench-strata.py`（性能）+ 健康。
- **回退**：配置里二进制路径换回 `build/strata`，一次重启；补丁默认关，env 关掉即回到原行为。
- **预算**：构建 ~10.6 min（照 `tools/server/README.md`）；**R3 装 B（1 重启）+ 逐位/质量门，R4 验收后恢复 A（1 重启）**，
  合计 2 次重启，不超每轮预算（`MULTIPATH_RULES.md` §3）。

### W4 · 多路定容与预设（1–2 重启）

- **定容**：按 `MULTIPATH_STANDARD.md` §2 的公式，给 1/2/3/4 路 × 冷/热读算出 KV/会话槽/专家槽预算，
  检查 `vram-reserve` 不变式与 `free ≥ ~700 MiB`（L-001 的 warn 线）。
- **预设**：`strata-022.mp-4x256K.json` 这类命名（见标准 §7），`use-config.sh` 装、`verify-running-config.sh` 核。
- **验收**：1/2/3/4 路矩阵全绿（健康、无截断、空答案率 0、聚合 ≥ 基线、TTFT p90 目标）；agent 质量不劣。
- **出口**：预设 + 标准 + 台账一条；进生产由 `HANDOVER.md` §3 决策表记录（含回滚）。

## 5. 验收门

| 门 | 内容 | 通过线 |
|---|---|---|
| G0 护栏 | `/health`、显存余量、温度、NVLink crc | 全绿，`free ≥ 700 MiB` |
| G1 质量 | 逐位门 / 质量门 | 配置臂：固定题贪心输出一致或质量不劣；代码臂：`parity_window.py` 过 |
| G2 性能 | 1/2/3/4 路 × 冷/热读矩阵 | 4 路聚合 ≥ 基线 −3%；读的人 TTFT 目标见 §4；邻居停顿 ≤1 s |
| G3 任务级 | `agent_budget_ab.py`（真实工具循环） | 墙钟中位不劣于基线、空答案率 0、畸形工具调用 0 |

判决词只有四种（同台账）：**采纳 / 否决 / 未定 / 参考**。

## 6. 测量协议（摘要，标准里有全文）

- 同 boot A-B-A 或交错，每臂 ≥3 次，报中位 + sd；<3% 不算结论（跨 boot 小值按 ~7%）。
- 测前先量仪器（`STRATA_*_TIMING` 自身代价，L-030 是 +2.5–8%）。
- 原始日志路径 + 脚本名 + "没测什么"缺一不可（`RULES.md` §5）。
- 跨机型数字一律标"不可横比"（`SERVER_ADAPT_REQUIREMENTS.md` §6.1 的可比性核对）。

## 7. 风险与回滚

| 风险 | 影响 | 预案 | 回滚 |
|---|---|---|---|
| 满槽读与 4 路解码抢机械，邻居掉速超收益 | 聚合不升反降 | W3 验收带"邻居停顿不劣于 W2-B"；不达标即否决 | 换回 `build/strata` + 1 次重启；env 关 |
| K/V 预留的 quiesce 在满槽时拖住批次 | 准入更慢 | 先量 drain/ensure 耗时（`STRATA_TRACE=1`），不预判 | 只保留无槽读、不预分配 |
| 会话槽吃掉专家缓存，命中率掉 | 专家上 CPU，变慢 | W4 定容时量命中（`passive_windows.py`）；#3849 的"batch 使命中 99%→86%"是本机未验的警示 | 恢复旧预设 |
| 测量被自己的会话污染 | 数字脏 | 只读轮次；后台 job + wait；不派子代理 | 重测，不入台账 |
| 重启把生产玩死 | 服务不可用 | `use-config.sh` 自带快照与 PID 校验；一次一个安装器 | 装回快照（`restore-prod.sh`） |

## 8. 交付物

1. **文档**：本计划；`MULTIPATH_STANDARD.md`（口径）；`MULTIPATH_RULES.md`（执行规则与复盘模板）；
   `MULTIPATH_LOG.md`（每轮复盘，**必填**）；`FEATURE_PARITY.md`（功能清单/测试/验收）；
   台账条目（L-046 起）；`HANDOVER.md` §13 的"下一轮先做"。
2. **测试**：`tools/server/tests/`（T1–T4，已可运行；T3 在 W3 前 xfail 记基线）；T5–T7 随对应轮次补。
3. **工具**：`bench-strata.py` / `piped_scenario.py` / `gate_prod_streams.sh` 已够用则**不写新的**；
   若矩阵形状不够，加一个 `exp-mp-matrix.sh`（按 `tools/server/README.md` 的固定形状写）。
4. **预设**：`strata-022.mp-*.json`（命名见标准 §7）。
5. **代码**：W3 的引擎补丁（默认关、独立 build 目录）+ 服务端 `pick_slot` 改动；全部可一键回退。

## 9. 轮次排期

| 轮 | 内容 | 重启预算 | 出口条件 |
|---|---|---|---|
| R1 | W1 基线复现（只读） | 0 | 基线数字 + 误差条进台账 |
| R2 | W2 读分片 A-B-A | ≤2 | 采纳 / 否决 / 未定，三选一 |
| R3 | W3 构建 + 逐位/质量门 | ≤2 | 补丁过门，进独立 build 目录 |
| R4 | W3 多路验收 | ≤1 | 4 路 + 冷读的数字 |
| R5 | W4 定容 + 预设 | ≤2 | 矩阵全绿 + 预设落盘 |
| R6 | 复盘 + 文档收口 | 0 | 四问（cron/残留/漂移/方向）+ HANDOVER 更新 |

每轮遵守 `RULES.md` §2 五步 + `MULTIPATH_RULES.md` 的第六步**复盘**（写入 `MULTIPATH_LOG.md`，**必做**）；
每轮末回答四问；每轮开始前先查台账与 LOG，**不重复已封的路**。

## 10. 不做清单

- 不搬 fork 的 NVFP4/FP8 权重路线（换模型栈，收益归单流，判据③）。
- 不碰 TP / NVLink（已实测 decode 无位置，`CODE_PLAN.md` §8）。
- 不做 DFlash2/MTP 的移植（已有 `--spec 8`，热接受率 ~99%，L-026）。
- 暂缓 SSD 前缀 KV 持久化（B4；重启不频繁，先做 B1/B2）。
- 不换驱动、不换引擎 0.2.4.1（L-034）、不再碰 MMQ tab / `PF_FUSED_SM75` / `QFUSE` / `HC_PERSIST` /
  `--pipeline-windows` / parking gain（全部已否决，见 `CODE_CONCLUSIONS.md` §"Closed"）。

## 11. 待确认 / 授权记录

- 用户 2026-10-24：**引擎也可以大改**；"什么对我好就移植融合什么"；目标机是这台生产机。
- 本文件是草案；W1 只读，不碰生产。W2 起每次改生产都先快照、后安装、测完恢复、台账留痕。
- 上游只读（`RULES.md` §1）；回调上游的动作一律不做。

---

**变更记录**
- v0.1 · 2026-10-24 · 初稿：一页纸、现状（全引台账）、六格借鉴清单、W1–W4、验收门、风险回滚、轮次排期、不做清单。
