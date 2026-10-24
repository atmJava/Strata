# 代码优化计划（CODE_PLAN）· v3 · 2026-10-24 夜

配套：[`RULES.md`](RULES.md)（怎么干活）、[`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md)（已有结论）、
[`HANDOVER.md`](HANDOVER.md)（状态与决策）。本文件只回答一件事：**接下来写什么代码、为什么、怎么证明、怎么回滚。**

> **v3 是一次通宵无人值守的执行版。** 新增 §10 复盘、§11 helper 可达性（实测）、§12 通宵计划 R1–R6、
> §13 无人值守规则。执行只看 §12，判据写在前面。
>
> **v2 是自我复核后的版本。** v1（`6e6d7017`）把 P1 定为"专家 GEMM 是算力受限，改用 sm_75 的 IMMA"。
> 复核把这条前提否掉了（§1 F1/F2），并把"逐位相同"这个判据推翻（F3）。v1 留在 git 历史里，**不再作为执行依据**。

## 9. 现状一页纸（2026-10-24 10:25Z，接手先看这张表）

| 项 | 现在的值 |
|---|---|
| 生产 | Strata 0.2.2 + 自建引擎 0.1.47；`layer_split 24`、`parallel 4`、`--batch-groups 2`、`--pcie-frac 0.31`、`--kv-resident 32768`、`--vram-reserve-mib 1280`；**16 个 env，含 `STRATA_HC_Q8=1`** |
| 服务状态 | `active`、`NRestarts=0`、PID 195236、`/health` ok、配置与 10:07 的干净快照 byte-identical |
| 本目标内采纳 | **只有 `HC_Q8`**（4 并发 +3.8…+7.1%，单流 −0.5%，质量 14/14 同判） |
| 本目标内否决 | 13 条，见 §6（全有实测；IMMA decode 因物理前提不成立进不做清单） |
| **今天作废的运行** | P0d#1（10:07Z，helper 未触发，**两种结论都不成立**）、P0d#2（10:13Z，**我重叠了两个安装器**，A 臂撞上重启）→ 4 次重启白花 |
| 实测硬件（可引用） | **NVLink 46.8 GB/s vs host 11.0 GB/s = 4.25×**（64 MiB `cudaMemcpyPeerAsync` ×12 取中位）；HBM 616 GB/s；VRAM 2×22 GB；PCIe 3.0 x16 |
| **A1 字节账（2026-10-24，只读日志计数器）** | 冷启动第一个请求就把**整份专家包**从 RAM 层读一遍（~48,400 blob ≈ 34 GB，按 11.0 GB/s ≈ **3.0 s**，这就是首字的底）；稳态每个请求（一轮）中位 **5,711 blob ≈ 3.9 GB ≈ 359 ms**（11.0 GB/s），p90 20,981 blob。`files` 层与 `MB read` 是**文件层**的计数器（读它们之前先读 `generate.cpp:15554`，我第一版就错在这里），与主机 RAM 无关。 |
| 重启 | 今天 40+ 次。**预算：一轮 ≤2 次，且必须先说清判据** |
| **helper 可达性（2026-10-24 实测，取代旧结论）** | ① `set_stage_helper` **确实被调用**（`serve=1 multi_gpu=1 stages=1 split_devs=1`，两个 stage 各一次）——旧说法“serve 里没接线”作废；② 生产 `--prefill-pipe 1` 时，**真实请求根本不进 `bind_stage_helper`**（只有 106 token 的建上下文撞到）；③ 改成 `--prefill-pipe 0` 后，6.8k 提示词**进来过**（被 `split_help_frac` 拒），但同一 boot 稍后又**不再进来**（2.3k/4.5k/6.8k 都没有记录）——说明还有一道**随状态变化**的门（`single_chunk` 或批量槽路径），未定；④ 结论：**helper 不是 env 能打开的**，要改的是“哪条路径去问它”，属于 P1 代码活 |

**这张表的用法**：任何"要不要再试一次"的问题，先看上面两行（采纳/否决），再看"作废"那一行 ——
**没触发 ≠ 不成立**，这是今天两次空转的教训。

## 0. 为什么是现在、按什么顺序

参数/开关这条线已经试尽（L-036…L-044）：采纳的只有 `HC_Q8`（4 并发 +3.8…+7.1%），其余全是否决或惰性；
而**同一配置跨 boot 的漂移量到过 9%**（L-040），意味着**小于 ~5% 的改动在这台机器上无法证明**。
所以后续只做两件事：有**物理余量**的底层内核，和**结构级**的调度改动；每一件都必须能被 ≥5% 的效应证明。

**测量协议（对所有 P 生效）**
- `bench-strata.py`：**每臂 ≥10 次、两臂交替**，报 median 与 p10/p90；优先同 boot，跨 boot 只认成对。
- 判据①用 `agent_budget_ab.py`（14 任务，每臂 ≥2 次）。逐位/容差门见 §3。质量门：`quality_eval.py`。
- 达不到 5% 的效应 → 结论写"不可分辨"，**不采纳**。
- **成本要算进计划**：env 门在进程内只读一次（`old_kernels()` 的 `static`），所以**每臂一次重启**。
  一次冷装载 ~190 s，10 臂 ≈ 32 min 装载 + 10 次启动（2026-10-24 当天已 41 次）。端到端最多 3 对（6 臂），
  其余判定交给微基准。

---

## 1. 复核记录：v1 的六处问题（证据在右，不是印象）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| **F1** | **"算力受限"不成立**：张量核补的正是这台机器不缺的那一维 | 满带宽下这条内核要 `616 GB/s × 8 MAC/B ≈ 4.9 T MAC/s`；2080 Ti 的 `dp4a` 可供 `4352×1.63 GHz×4 ≈ 27 T MAC/s` → **余量 5.5×**，实测 30–40% 带宽时余量 ~14× | P1 的物理前提消失 |
| **F2** | **IMMA 的 M 维在 decode 里是空的**：512 专家、top-10、4 并发 → 每层 40 个 hit，其中两个 token 撞同一专家的期望约 **1 对/层**（`6×10×10/512`），**组大小最大 2，永远凑不出 16 行** | `geometry.hpp`：`n_expert=512, n_expert_used=10`；`moe_hit_grouped_s2` 一个 hit 一行 | `m16n8k32` 的 16 行里 15 行作废（`m8n8k16` 也一样），理论利用率 ≤1/8，**比 dp4a 慢一个量级** |
| **F3** | **"逐位相同"这个判据实现不了，那个门也不存在** | 现役内核的浮点累加顺序 = lane 跨步 + `warp_sum` 树；张量核的线程→元素映射必然不同 → 最多做到"host 双精度参考 4e-5·Σ\|term\| 内"。门是 `src/kernels/s2_expert_grouped_parity.cpp`（old vs new **逐字节** + host 参考），不是 `bench/micro/moe_hit_parity.cu`（**该文件在本仓库不存在**） | P1a 会卡在一个不可达的门上；或为了逐位而去掉张量核的全部优势 |
| **F4** | **现状描述过期**：现役默认不是"一 warp 一行" | `gu_pair_kernel`/`down_pair_kernel` 是默认（`STRATA_GROUPED_PAIR_MIN_HITS` 默认 0）：8 字节 `uint2` 载入、gate/up **共用同一激活块**、`byte_perm` 解包 | v1 的"解包只做一次"这条收益已经被吃掉了一部分 |
| **F5** | **P2 的数字不自洽** | head 675 MB / 1.01 ms = **668 GB/s > 616 GB/s** 的"HBM 上限"；且 `native_head` 只有**一个** `device_`，不可能是两张卡各读一半 | 要么峰值常数错（那 F1 的 29–40% 也得重算），要么流量/时长算错。先修数字再谈方案 |
| **F6** | **相图不闭合** | L-038 表内 9.06 ms vs 窗口 14.18 ms，**36% 不在表里**（大概是 12 个 QSA 层） | "专家 GEMM 是最大一相（12.6%）"要带这个限定，否则会高估它的优先级 |

**一个静态估算（未验证，P0a 就是来验它的）**：按现役 `gu_pair_kernel` 的指令数推一遍——每处理 16 B 专家码
（gate+up 各 8 B）+ 34 B 激活，约 95 条 warp 指令；68 SM × 4 调度器 × 1.63 GHz = 443 G warp-instr/s，除下来
**指令预算的上限约 235 GB/s**，而实测是 180–245 GB/s（616 的 29–40%）。两者吻合得太好，不能当巧合：
**这条内核大概率是发射/载入密度受限，不是 DRAM 也不是 MAC**。如果成立，`IMMA` 只省掉 16 条 `dp4a` 里的 15 条
（~15%），端到端 ~1.9% —— 和 L-039 里 EXPDEC 只拿到 −4% 是同一个量级。

---

## 2. P0 · 先花两小时决定"要不要写内核、写哪个"（可执行，不碰生产）

两个实验都不需要新内核、不需要重启，只动 bench。

**P0a · 三点剖析（30 行变体，`s2_expert_grouped_parity --bench` 或 `expdec_bench`）**
同一形状跑三遍，只改内循环：
1. `read-only`：把 codes 当 `uint2` 读出来直接累加（不展开、不 `dp4a`）→ **纯流式上限**；
2. `no-unpack`：跳过 `expand_codes`，把原始字当 int8 用（数值是错的，指令数是对的）；
3. `def`：现役。

| 结果 | 含义 | 下一步 |
|---|---|---|
| `read-only` ≈ `def` | 纯带宽/延迟受限 | 内核改写**放弃**；只剩"少读字节"（量化/缓存） |
| `def − no-unpack` 占大头 | 解包受限 | P1-B（预展开 int8 缓存）或放弃 |
| `no-unpack − read-only` 占大头 | 发射/载入密度受限 | P1-A（在飞负载），这是唯一能做类的"免费"优化 |
| 三者都接近 | 延迟/小内核受限 | 转结构（减少 launch 与依赖屏障），不是内核问题 |

**P0b · 字节账与组大小（1 次 trace，无重启）**
记录每层 `n_hits`、resident 命中数、每窗口移动字节 → 算 **achieved GB/s**；同时出**组大小直方图**（验 F2）。
**kill criteria**：achieved ≥70% 峰值，或 `read-only ≈ def` → 专家 GEMM 这条线关闭，转 P2/其它相。

**P0c · 互联账（和 P0b 同一次 trace，顺带记）**：每窗口跨卡字节里，多少走了 **NVLink（P2P）**、多少走了
**PCIe/主机内存**（`--pcie-frac 0.31` 的那部分、stage 交接的 mapped 缓冲）。见 §8：这是为了把
"NVLink 用没用上"从印象变成数字，一条 trace 的量，不额外重启。

**P0d · prefill 的 peer helper（1 次重启；NVLink 唯一有量级的位置，见 §8）**
`STRATA_PREFILL_HELP_FRAC=0.4`，测 4.5k 新增 token 的 TTFT（`exp-env-ab.sh MEASURE=ttft`，两档各 ≥3 轮）。
判据：TTFT 中位 **−5% 以上**才继续；赢了就排代码项（`split_help_frac` 认 P2P + 允许分块提示词启用 helper），
输了就记"NVLink 在 decode 与 prefill 都验证过不划算"，整条线关闭。
**先做它**：最便宜，而且这是上一轮我判断错了的地方。

---

## 3. P1（重写）· 只在 P0 指到具体病因后才动

三个候选，规模 100–300 行，按 P0a 的结果选**一个**，不并行：

- **P1-A · 在飞负载**（默认候选）：一块处理两段 code、或双缓冲预取；**位级不变**，
  直接用现成的 `s2_expert_grouped_parity` 逐字节门，风险最低。
- **P1-B · 预展开 int8 权重缓存**：把 `(c−1)` 直接展开成 int8 存 VRAM，`hx` 项和内循环展开全部消失。
  代价：每专家 ~4.9 MB（gate/up 3.3 + down 1.6），resident 集合要重算显存预算（当前只剩 0.8–1.0 GB）；
  数值与现在**不同**（少了 `hx` 的抵消），门只能用容差 + 质量套件。
- **P1-C · IMMA**：**只有** P0a 显示"MAC/发射受限且预展开可行"才做；预期上限 <5% 内核（§1 的估算），
  按现在的证据**不排期**。

共同要求：默认关、回滚=删 env、默认路径字节不变、逐字节门（A）或容差+质量套件（B）、
`bench-strata` **最多 3 对**（6 臂）端到端。

**成功判据（重算过，v1 的 30% 是自相矛盾的）**：专家 GEMM 这一相 12.6%，即使内核快 30% 也只
3.8% 端到端，低于 5% 的判定线（L-039 自己写过"翻倍也只 ~6%"）。所以判据是：
**内核 ≥40%（1.71 → ≤1.0 ms）才可能端到端过 5%**；否则结论写"不可分辨"，不留级。

---

## 4. P2 · 把 LM head 的 vocab 劈到两张卡（**数字修好之前不动**）

- 事实（**待复核**）：head 1.01 ms/窗口（7.4%），675 MB/窗口；但 675/1.01 = **668 GB/s**，超过 616 GB/s 的
  峰值口径，而且 `native_head` 目前是**单 device**。
- 先做：核清 616 是哪来的（`nvidia-smi -q -d CLOCK` 的内存频率 × 位宽）、head 到底读了多少字节/窗口。
  这两个数决定上限是 3% 还是 7%。
- 难点不变：+337 MB 显存（只剩 0.8–1.0 GB）、残差要反向过卡、采样要把对侧 logits 拉回来。
- **上限 ~3–7% 且贴着噪声 → P1 之后再做。**

## 5. P3 · 结构级（上限大、风险大，暂不排期）

- 4 路聚合 135 tok/s 对单流 75 = 1.8×，说明批窗口在**算力**上并不宽裕；
  `--batch-mtp` 在 `--batch-groups > 1` 时被**代码直接禁用**（`generate.cpp:5320`），上游注释写明原因：
  "流水组跑不了槽内草稿；4 路 2 卡时流水组比串行 MTP 快 25–30%" → **不要再打 batch-mtp 的主意**。
- `--pipeline-windows` 两对测完均否决（L-044/L-045），这条路关闭。
- 再往上（按专家亲和性给窗口分组、跨会话调度）是研究型改动，**不排期**。

---

## 6. 不做清单（都已实测否决，别再捡）

MMQ 指针表（L-031）、TP 补丁（L-033）、`QFUSE`（L-043）、`EXPDEC`（L-039）、`PF_FUSED_SM75`（L-041）、
层切分配平（L-037/L-040）、会话 parking（L-032）、read-ahead（L-025）、升 0.2.4.1（L-034）、
`--pipeline-windows`（L-044/L-045）、**IMMA decode 专家内核**（v2 §1 F1/F2：物理前提不成立，除非 P0a 翻案）。

## 7. 每轮结束的固定动作

台账一条（含原始日志路径与"没测什么"）→ HANDOVER §13 的"下一轮先做"改成一条 → 提交推送 →
复盘（cron/timer、残留进程、`vram-perf.csv`、重启次数）。

> 台账 L-038/L-039/L-040 里"675 GB/s 贴着 616 GB/s 上限"与"29–40% 峰值 → 2.5–3× 余量"两处按本文件 §1 F5/F1 视为**待复核**；
> 旧条目不改，引用时以本文件为准。

---

## 8. 硬件取向：NVLink 在这个项目里是干什么用的（对我上一版的更正）

**硬件事实**（2026-10-24 现场查的，不是猜）：`topo -m` = **NV2**（桥在），`nvlink -s` = 每卡 2 条 ×
**25.781 GB/s**（单向 ~51.6 GB/s），`cudaDeviceCanAccessPeer` 双向 = 1，显存 2×22 GB，PCIe 3.0 x16。
**实测**（`cudaMemcpyPeerAsync` 64 MiB × 12 取中位）：**P2P 46.8 GB/s，主机方向 11.0 GB/s = 4.25×**。
这个 4.25× 是本文件所有"走 NVLink 还是走 host"的判断的底座 —— 之前几版我用的是 spec 值，不够。

上一版我写"NVLink 不是杠杆"，理由里有一条是错的：我把"两卡各只剩 0.8–1.0 GB 空闲"当成了"没有可用显存"。
**分层存储不是靠空闲，是靠把哪一层放在哪儿** —— 而这个项目的第二个 GPU 有三份用途：多一份 VRAM、
**多一条 PCIe 链路**、和对卡之间 ~4× 于主机内存的交换带宽。三份用途里，NVLink 撑的是第三份。

**它对错的三个位置：**

| 位置 | 现状 | 结论 |
|---|---|---|
| decode 的 stage 交接 | mapped 主机内存（`generate.cpp:8977`），`handoff_floats = 12,804` × 8 行 ≈ **410 KB/窗口** | 改 P2P 也只省 26 µs = **0.18%** → 不值得写代码 |
| `--peer-device`（对卡当专家二级缓存） | **与 `--layer_split` 互斥**（`generate.cpp:3371`） | 我们是 layer split → 用不上 |
| **prefill 的 stage helper** | **对我们的提示词尺寸默认是关的** | **这才是 NVLink 在本项目的设计用途，而我们把它关了** |

**第三行就是答案。** `set_stage_helper`（`generate.cpp:8898`）在单 chunk 提示词时让**空闲的下一段 GPU**
"边流边算"这一段的一部分专家 —— `prefill.cpp:2626` 的注释写得很直白：
"the next stage's GPU idles while this one reads; it streams and computes a share of this stage's experts then"。
它的收益来源是**第二条 PCIe 链路**（两块卡各有一条，prefill 时一块在空转），代价是**激活与结果要跨卡** ——
正好是 NVLink 管的那一段。**这就是"为什么要有 NVLink"**：不是搬权重，是让本来闲着的那块卡在 prefill
期间变成第二份 host 带宽 + 第二份算力，交换走 NVLink 而不是主机内存往返。

**还有一处更关键的（源码，2026-10-24 核过）：helper 自己把 P2P 关掉了。**
`set_stage_helper` 里写死 `pp->p2p = false;  // the mapped host route, whether or not the cards could reach each other
(measured there)` —— 也就是说**它即使跑起来，跨卡那段也走主机内存（11.0 GB/s），不用 NVLink（实测 46.8）**。
而通用的 peer 路径（`--peer-device`）是 `pp->p2p = peer->p2p();`（`prefill.cpp:2056`），**管道现成**：
一行开关就能让 helper 走 P2P，然后在这台机器上重测。这才是"为这台硬件改代码"的第一个具体项，
而不是调 `split_help_frac`。

**它现在为什么关着**（2026-10-24 外部评审更正过三处，见 §10）：
- `split_help_frac(T)`（`prefill.cpp` 约 1900 行）：表是在 **2× RTX 3090、PCIe 4.0、无 P2P** 上测的，
  规则写成 "T 大于 ~3.3k 就不帮"；我们的提示词是 **4,535 新增 token → share = 0**（除非 `STRATA_PREFILL_HELP_FRAC`）。
- **不是分块挡住的**：`pipeline_chunk` 在 `k <= 1` 时直接 `return cap`，cap = `--prefill` 的 8,192（日志里真正的
  分块行都在 ≥10,992 token），4,524 token 的提示词一直是**单 chunk**。所以 helper 未绑定只能是被
  `set_stage_helper` 的三条**静默早退**或 `bind_stage_helper` 的几道门之一拒掉 —— 必须打印出来，不能猜。
- 注释自己的表在 4k–8k 上 helper 是**赢**的（6K 2038 → 1471，−28%），而它下面的规则在 3.3k 就把 share 关掉；
  表和规则不该同时成立。但这张表是**无 P2P 的 3090** 测的，移植到本机等于二次继承，不算证据。

**我在这里犯的三个事实错误（评审指出，已核）**：
1. "`--pcie-frac 0.31` = 31% 的专家缓存住在主机内存" —— **错**。它是**错过专家在"PCIe 链路"与"CPU 池"之间的
   分配比**（`generate.cpp:2174`：`link / (link + 1.85 × pool rate)`），跟"多少专家在内存里"无关。
   靠它推出的"PCIe 只有 3090 一半 → 多一条链路收益翻倍"整段作废。
2. "4.5k 被 `--prefill-pipe` 切成多块" —— **错**（见上，单 chunk）。
3. "decode 跨卡只有 410 KB/窗口" —— **漏了 KV 流式**：整份 K/V 可以在 pinned RAM（1.51 GiB）里。
   但现成计数器就在日志里（`strata serve: KV streaming: 79.6% of 558 block reads hit VRAM, 0.5 MiB read from RAM`），
   量级是 MiB 级 → **不是杠杆**。教训：这些计数器一直在打印，计划不该不知道。

**所以 NVLink 的正确结论是（改口）**：decode 里它没有位置（跨卡是 stage 交接 + 极小 KV 流，量级 <1 ms/窗口）；
**prefill 里它最多抹平 helper 自己制造的主机往返成本**（`pp->p2p = false` 把跨卡写成走主机内存），
上限等于"prefill 真正从主机流过来的专家份额"× 4.25× 的链路差 —— **这个份额必须先量**，
`--resident-experts` 打开时它可能很小（评审的独立判断，我采纳）。
TP 在这台机器上只值 +3%（L-033）说明它不是"算力协作"的钥匙；它值钱的地方是**替空闲的卡分担 IO**，且要先是 IO。

**下一步（顺序固定，评审给的顺序我接受）**：
① **计数器**（0 重启）：每窗口 host 字节（`expert tiers` / `KV streaming` / `prefill wall`）→ 若 host <15 MB/窗口，
   decode 侧 NVLink 上限 <1 ms，只做 prefill 线；
② **绑定证明**（1 重启）：`STRATA_PREFILL_HELP_DEBUG=1` 打上（`patches/local-strata/help-debug-0.2.2.patch`，
   已含三条静默早退），单次启动看被哪道门拒；
③ **同 boot 交替**（20 行）：把 `split_help_env()` / `old_kernels()` 这类函数内 `static` 改成读一个 flag 文件，
   换来"不重启就能换臂"—— 这才是把 9% 跨 boot 漂移从"天花板"降成"噪声"的唯一办法；
④ 只有 ①②③ 都过了才做 A/B，判据第一条是日志里出现 `peer GPU` 且 `peer-streamed experts > 0`。

---

## 10. 外部评审的采纳记录（2026-10-24，子代理，独立读码）

**采纳（已改到 §8）**：`--pcie-frac` 语义错、4.5k 未分块、KV 流式被漏、以及"计数器→绑定证明→同 boot 交替→A/B"的顺序。
**采纳（测量协议）**：`host staging 130↔286 ms` 是**双峰**不是噪声；判定要用引擎自报的 `prefill wall / GPU timeline`，
而不是混了会话快照（`--prompt-cache-every 4096`、12 GiB 会话缓存）的端到端 TTFT；先做**同配置 6 次噪声分解**再谈 5%。
**采纳（IMMA 的降级理由要写准）**：F1/F2 否掉的是"这条内核还有 M 维/ALU 余量"，不是"专家 GEMM 不可能更快"。
它留在不做清单里，但 P0a 的问法改成"字节 / 发射 / 延迟哪一个"，而不是"要不要 IMMA"。
**有条件采纳（对卡当专家缓存）**：容量受限而非带宽受限 —— 各剩 0.8–1.0 GB ≈ 750 blob = 全部专家的 3%，
且 miss 的基线是"PCIe 11 GB/s 或 CPU 池"二选一，4.25× 不直接适用。**判据：每窗口 miss 字节 <30 MB 即关线**；
要过 5% 必须先腾 ≥10 GB（挪 KV / 减 resident），那是另一个量级的改动。
**采纳（结构性线索，未验证）**：相图 36% 不在表里、`waitA` 1.54 ms/窗口 —— 若 VRAM hits 真只跑 30–40% HBM，
约束可能是"48 层 × 4 路的小内核串行"，杠杆在 graph/层内合并。**一次 nsys 可判**，排在 ③ 之后。

**未采纳**：无。评审没有一条被我驳回 —— 这在今天之前的三轮里是第一次。


---

## 10. 复盘：今天的时间花在哪，为什么

**有效的**（都可复核）：measured P2P 46.8 vs host 11.0；A1 字节账（冷启动 34 GB、稳态 3.9 GB/轮）；
`HC_Q8` 采纳；两次“作废运行”的教训写进流程；runtime flag（`include/strata/core/runtime_flag.hpp`）落地并独立验证。

**浪费的**（按代价排）：
1. **部署动作本身出错两次**：在链接完成前 `cp`（拷到旧二进制）、`cp` 到正在运行的二进制（`Text file busy`）→
   多花 2 次冷装载（~7 min）。**规则**：构建后台跑完必须用 `md5sum`/`strings` 验**部署后的文件**；
   覆盖运行中的二进制一律 `cp` 到 `.new` + `mv -f`。
2. **读错计数器**（`files … MB read` 当主机字节）→ 一轮结论作废。**规则**：引用计数器前先读打印它的那 5 行源码。
3. **在旧结论上锚定**：`pp->p2p=false` 的 `(measured there)` 我引用了两次，其实是“本机从未测过”。
4. **重叠安装器**（10:13Z）一次；**A/B 脚本在引擎 READY 之前就发请求**一次（4 臂全空）。

**代价**：今天 45+ 次重启。今晚预算：**每轮 ≤2 次**，而且**先写判据再动手**；不够就改成“同 boot flag 交替”。

## 11. helper 的完整地图（2026-10-24 实测 + 静态）

- **接线**：`generate.cpp:8766` `if (multi_gpu && !stages.empty())` → 对每个 stage 调 `set_stage_helper`（**实测进了，成功**）。
- **启动后的门**（`set_stage_helper`，现在会打印）：`helper==nullptr/self`、`split_help_env()`、同设备、`mmq_plan().any`。**实测全过**。
- **每次提示词的门**（`bind_stage_helper`，`prefill.cpp:2673`，`const bool helped = single_chunk && bind_stage_helper(n)`）：
  - **只有 `single_chunk` 才调用**。生产 `--prefill-pipe 1` 下真实请求不走这里（实测：0 条记录）；
  - `split_help_frac(T)`：默认规则在 ~3.3k 以上为 0；flag 文件/env 可覆盖；
  - `T ≥ stream_all_min()`（1024）、`m.src != null`、`m.ring > STAGE`、`!fused_ring()`；
  - 逐层 `mmq_plan().layer[l]` 且 `!fused::native_supported(...)`（我们 IQ3_XXS 在 sm_75 上 native 为 false → **过**）。
- **仍未知**：`--prefill-pipe 0` 时为什么有时进、有时不进（2.3k/4.5k/6.8k 后来都没进）。**下一步只需一个探针**
  （在 `single_chunk` 计算处打印 `n/cap/single_chunk`），不是猜。

## 12. 通宵计划 v3.1（执行只看这一节；已按 2026-10-24 夜评审改过）

**评审改掉了三件事**（都接受）：R1 的探针基本是重复劳动（pipe=1 时 `planned_C = pipeline_chunk(n) < n` → `single_chunk=false`
已经解释了“不进 helper”；pipe=0 时 6.8k 的 10 条 `declined` 已经证明“进得去”）；R4 的“去重”前提错（gate/up 与 down 是两组不同
字节，不是重复）；R3“0 改动按窗口统计”做不到（现有计数器是每请求累计，必须加打印）。**顺序按评审改成：先判 helper 生死，
再把预算给“字节的暴露度”。**

| # | 任务 | 重启 | 判据 / 止损 | 回滚 |
|---|---|---|---|---|
| **S1** | **helper 生死判决**：配置 = 生产 + `--prefill-pipe 0` + `STRATA_PREFILL_HELP=1`、`STRATA_PREFILL_HELP_FRAC=0.4`（**用 env 绕开 flag 文件的疑问**）、`STRATA_PREFILL_HELP_DEBUG=1`；发一条 6.8k 提示词 | 1 | 日志出现 `peer GPU … computes its experts' rows` **且** `peer-streamed experts > 0` → 活；只有 `declined …` → 记下那道门，**关线**（进 §6 不做清单） | 已有 `prefill.cpp.bak-help`；配置用 `restore-prod.sh` |
| **S2** | 若 S1 活：同 boot 用 **flag 文件**交替（`prefill_help_frac` 0 / 0.4），3 轮、每臂 2 条提示词，**配对差**判定 | 0 | 配对中位差 ≥5% 且符号检验 p<0.1 → 采纳；否则"不可分辨"，关线 | flag 置 0 |
| **S3** | **字节的暴露度**（比去重更可能有效）：给 `prefill.cpp` 加**每窗口**的 host 字节与等待打印（默认关），把 3.9 GB/轮 分成"被计算遮住的"与"真等待的" | 1 | 若 `host staging` 与 `waitA` 显示等待占比 >30% → 写 S4；<10% → 结论"字节不是瓶颈"，转 S5 | 去掉打印 |
| **S4** | **预取深度 / 两卡各自的 H2D**：把专家预取提前一层（或按 stage 拆一条 prefetch 流），默认关、逐位不变 | 2（A/B） | 同 boot 配对 TTFT −5% 才采纳；`parity_window.py` 必过 | flag 关＝回到今天 |
| **S5** | **结构性线索**：相图 36% 不在表里、`waitA` 1.54 ms/窗口 → 判是不是"48 层 × 4 路小内核串行"。`nsys` 要停服务、自己拉起（评审指出：不是 0 重启，是 2 次） | 2（或跳过） | 先出"时间去哪了"的表；没有 ≥5% 的候选就不写代码 | — |
| **S6** | **收尾**：`restore-prod.sh`，验 `md5sum strata-022.json` 与 `strata-022.prod.json` 相同、`NRestarts=0`、`/health`、16 env；数字进 `HANDOVER.md` / `EVIDENCE_LEDGER.md` | 1 | `verify-running-config.sh` 的 VERDICT 行 + md5 相同 | 文档已提交 |

**今晚不做的**：IMMA decode（F1/F2 已否）；对卡专家缓存（容量受限）；`--prefill-pipe`/`layer_split`/`--pipeline-windows`
这类已被实测否决的开关；KV 流（命中 79.6%，读 RAM 仅 0.5 MiB，已到底）。

## 13. 无人值守规则（今晚额外加的四条）

1. **每次重启前**把“要跑什么、判据、回滚”写进 `docs/CODE_PLAN.md` 的本轮小节，再动手。
2. **部署后验文件**：`md5sum` + `strings | grep <新字符串>` 对**部署路径**做，不对构建产物做。
3. **一个安装器**：动手前 `pgrep -af "[u]se-config|[r]estore-prod|[f]lag-ab|[e]xp-env-ab"` 必须为空。
4. **每轮结束必须留证据**：日志路径、脚本名、命令、以及“没测什么”。没有证据的轮次按未发生处理。
5. **每轮开头与结尾各跑一次** `verify-running-config.sh` 并记 `md5sum strata-022.json`；两次不一致，该轮**作废**。
6. **不碰生产三件套**：`build/strata`、`strata-022.json`、`strata.service`。实验二进制只进 `/opt/ai/engines/<名字>/build/`，
   收尾必须 `restore-prod.sh` + md5 相同 + `NRestarts=0`。
7. **每轮记协变量**：`nvidia-smi --query-gpu=index,temperature.gpu,clocks_throttle_reasons.active`（长跑降频会被误读成代码效应）。


---

## 14. S1/S2 结果（2026-10-24 夜，实测，同 boot 配对）

**S1：helper 活了。** 之前两次失败的真正原因是我自己的 flag 文件：`split_help_env()` 让**文件优先于 env**，
而文件里写着 `prefill_help=0` → 启动时被自己的开关关掉；`--prefill-pipe 1` 时真实请求又不进 `bind_stage_helper`
（`planned_C = pipeline_chunk(n) < n` → `single_chunk=false`）。两个条件缺一不可：
**`--prefill-pipe 0` + `prefill_help=1`**。这一版启动后 `set_stage_helper` 两个 stage 都成功，
6.8k 提示词的 `chunk decision` 显示 `single_chunk=1`、没有任何 `declined` 行 → 真的把行分给了对端。

**S2：同 boot 配对 A/B（flag 文件交替 `prefill_help_frac` 0 ↔ 0.4，每臂 2 条、共 3 轮）**

| 每轮新增 | 配对差中位 | B（helper）胜 | 判定 |
|---|---|---|---|
| 1,500（2 轮合并 4/4） | **−3.0% / −3.9%** | 4/4 | 方向一致，但 **<5% → 不可分辨** |
| 4,500（3 轮） | **+4.8% / +18.9% / +8.1%**（中位 ≈ +8%） | **0/6** | helper **更慢** |

**结论**：helper 在 1.5k 上略快（与上游 3090 表一致），在 4.5k 上稳定更慢（也与表的"~4K 以上 none 更好"一致）。
我们的实际负载是 4.5k+ 新增 token → **这条线关闭**，`prefill_help` 保持关。
**但它顺带证明了两件可复用的事**：① `--prefill-pipe 0` 是 helper 能被问到的前提；② **运行时 flag 文件真的生效**
（A 臂出现 `declined … share is 0`、B 臂没有），A2 的工装可用 —— 以后所有 env 型开关都能同 boot 交替。

**教训（写进 RULES §9 的第 5 条）**：给开关加"文件优先于 env"的覆盖时，必须同时接受"文件会覆盖我自己的 env"。


---

## 15. S3 结果：窗口时间去哪了（2026-10-24 夜，`STRATA_SPLIT_TIMING=1`，生产二进制 + 该 env）

不要新写探针：`STRATA_SPLIT_TIMING` **已经在打印**每个 stage 每窗口的四段（`generate.cpp:15484`）。
4,524 新增 token、生成 200 token、114 与 202 个窗口两次采样：

| stage | 窗口数 | 等 GPU | pool + plan | **host staging** | commit | 合计 |
|---|---|---|---|---|---|---|
| 0 | 114 | **9.251 ms** | 2.836 ms | **1.969 ms** | 0.206 ms | 14.26 ms |
| 1 | 114 | **8.607 ms** | 2.736 ms | 0.001 ms | 0.210 ms | 11.55 ms |
| 0 | 202 | **9.482 ms** | 1.922 ms | **1.159 ms** | 0.229 ms | 12.79 ms |
| 1 | 202 | **8.927 ms** | 2.106 ms | 0.001 ms | 0.235 ms | 11.27 ms |

**读数**（与 decode 78–89 tok/s ≈ 11–13 ms/token 自洽）：
1. **最大项是"等 GPU"（65–73%）**，不是主机字节。stage 0 比 stage 1 多 ~0.6 ms，正是拿不到数据的那一半。
2. **host staging 占 stage 0 的 8–14%**（1.16–1.97 ms/窗口）—— 这就是 A1 那 3.9 GB/轮 的**暴露部分**。
   若完全遮住，上限约 **8–14%**，**过 5% 判据**。
3. stage 1 的 staging ≈ 0：它的专家来自本卡驻留/对卡，不经主机 —— 与"NVLink 只在跨卡那一段有意义"一致。
4. 所以 S4 的靶子从"按 stage 拆 H2D"改成更直接的：**把 stage 0 的 host staging 藏到计算背后**（预取提前一层）。

**已有的旋钮**（先测现成的，不写新的）：`STRATA_IO_PREFETCH`、`STRATA_IO_PREFETCH_DEPTH`、`STRATA_IO_PF_AHEAD`、
`STRATA_IO_PF_STAGE`、`STRATA_FILL_AHEAD`、`STRATA_IQ_PREFETCH`、`STRATA_HANDOFF_PIPE`。
**下一轮**：把这 7 个中与 decode 驻留路径相关的几个接进 `runtime_flag`（1 次构建 + 1 次安装），
然后**同 boot 交替**逐个 A/B（0 重启/个），判据：decode tok/s 配对中位 **+5%** 且 staging 值下降；否则不采纳。


---

## 16. 下一轮卡片（S4，交接给下一轮的第一件事）

**已知**：decode 每窗口 11–14 ms，其中 `等 GPU` 占 65–73%，`host staging` 占 stage 0 的 8–14%（1.16–1.97 ms）；
stage 1 的 staging ≈ 0。**"routing-aware prefetch" 已经开着**（`strata generate: routing-aware prefetch on (the next
layer's router, file tier)`）—— 但它服务的是**文件层**，而暴露出来的 staging 是 **RAM 层**的 blob→VRAM 拷贝。

**所以 S4 只有两种写法，先做 (a)**：
- **(a) 把 lookahead 扩到 RAM 层**（或给 `ram_stage` 路径加"提前一层"）：逐位安全（只是把拷贝提前），默认关，
  flag 可 A/B。判据：`STRATA_SPLIT_TIMING` 的 `host staging` 从 1.2–2.0 ms/窗口降到 <0.5 ms，且 decode 配对 **+5%**。
- **(b)** 若 (a) 的窗口里 staging 已经被算进"等 GPU"（即重叠本来就有、1.97 ms 只是残余），那就改攻"等 GPU"本身：
  先量每层 ring 等待的分布（24 层 × 0.39 ms），再看能不能把两层的提交合并成一个 ring。

**做法约束**：只动 `build-imma`，二进制走 `/opt/ai/engines/help/build/` + `mv` 部署；实验配置每次实验后
`restore-prod.sh` 并验 md5；每晚 ≤2 次重启，超了就改成同 boot flag 交替。


---

## 17. S4 结果：重叠机制存在，但这台机器没有给它显存（2026-10-24 夜，实测）

`STRATA_FS_SLOTS` 打开的是 **Foresight swap space**（`generate.cpp:9814`）—— 它正是"把预测到的专家提前拷进
VRAM"的那套机制（`RouterLookahead::set_foresight`），也是唯一能直接遮住 S3 那 1.2–2.0 ms/窗口 staging 的现成东西。

实测（`STRATA_FS_SLOTS=64` + `STRATA_SPLIT_TIMING=1`，其余同生产）：

```
strata serve: foresight swap: cannot allocate 2268 MiB on CUDA0 (raise --vram-reserve-mib by about that much) - swap space off
```

**结论**：每槽约 35 MiB，64 槽要 2.27 GiB；而这台机器的显存已经全给了驻留专家（`--vram-reserve-mib 1280`、
12,288 槽、`--pcie-frac 0.31`），**没有 2.27 GiB 可给**。要腾出来只能减驻留槽 —— 那正是让 decode 快的那部分，
用它换 8–14% 的 staging 重叠属于**拆东墙补西墙**。

**所以 S4 关闭**（进 §6 不做清单），理由不是"没意义"，而是**在这台机器的显存预算内不成立**。
这条也回答了"大显存是不是已经吃干"：是 —— 44 GB VRAM 全部换成了驻留专家，任何额外缓冲都要从那里切。

**下一步（S5）**：剩下的最大项是"等 GPU"（9.25 ms/窗口，65–73%）。它按 24 层均摊约 0.39 ms/层，
要动它只能合并 ring/减少每层同步 —— 结构性改动，需要先量每层等待的**分布**（新探针，1 次构建+1 次安装），
并且必须承认它很可能超出今晚剩下的重启预算。若预算不够，就只写设计、不动代码。


---

## 18. S5 结果：等待是**均匀**的，没有单点可修（2026-10-24 夜，实测）

新探针 `STRATA_WAIT_LAYERS=1`（默认关，`verify.cpp` 在每层 ring 回来的那一刻累加，每 64 窗口打印一次）：

```
strata serve: layer waits, ms per window (STRATA_WAIT_LAYERS, 12544 windows):
  sum 17.63 ms over 48 layers; L0 0.21 L1 0.50 L2 0.34 L3 0.48 L4 0.35 L5 0.39 ... slowest L1 0.50 ms
```

**读数**：
1. 48 层共 17.6 ms/窗口（≈ 每 stage 8.8 ms，与 S3 的 9.25 / 8.61 一致）→ **0.37 ms/层**。
2. 分布**均匀**：最慢的 L1 也只有 0.50 ms，L0 0.21 ms。**没有"某几层特别慢"的热点**，所以调层、调层数都没用。
3. 这不是"某个内核慢"，而是**每层一次 ring 交接的固定成本**（`verify.cpp:2890-2941`：raise flag → GPU 跑完 → rang 回来）。

**唯一能改变它的结构性开关**已在代码里：`Verifier::chain_overlap()`（`verify.cpp:2678`）——
"两个 stage 通过共享 flag 直接交接、主机不参与"。它要求 `next_->hflag_in_h_ == hflag_out_h_`（两 stage **共享同一份
flag 缓冲**）。生产日志里那句 `one hand-off per window` 说明我们走的是**非 chain** 路径，也就是每层都要主机参与一次交接。
**要拿到这部分收益，得让 stage 之间共享 flag 缓冲 —— 驱动层改动，不是今晚剩下的预算能安全做的。**

**所以本轮结束时的判断**：decode 的 65–73% 是"每层 0.37 ms 的交接成本 × 48 层"，均匀、无热点；
下一步是**设计级**改动（chain 交接），不是调参或小改内核。这条已写进 HANDOVER 作为下一阶段的候选，
而不是今晚硬上。


---

## 19. S6 结果：overlap 交接在这台机器上**更慢**（2026-10-24 夜，实测）

`STRATA_SPLIT_OVERLAP=1` 在源码里已经存在（`generate.cpp:8908`，注释写明"measured on 2x RX 7900 XTX only"），
开它就是让两 stage 用共享 ready flag 直接交接、主机不参与 —— 正是 S5 指出的那条结构性候选。
启动日志确认它真的生效了：

```
strata serve: layer split: layers 0-23 (CUDA0), 24-47 (CUDA1),
              one hand-off per window (overlapped: STRATA_SPLIT_OVERLAP=1)
```

**但实测更慢**（其余同生产，`STRATA_WAIT_LAYERS` + `STRATA_SPLIT_TIMING` 同开）：

| 指标 | 基线（关） | overlap（开） |
|---|---|---|
| 每层等待合计 | **17.63 ms / 48 层** | **19.2–19.5 ms / 48 层**（+9%） |
| 最慢层 | L1 0.50 ms | L1 0.60 ms |
| stage 0 等 GPU | 9.25 / 9.48 ms | 8.92 / 9.98 ms（噪声内） |
| stage 0 host staging | 1.97 / 1.16 ms | 1.74 / 1.32 ms |
| decode | 69–89 tok/s | 87.7 tok/s（噪声内） |

**结论**：这条 flag 交接在本机（CUDA + PCIe 3.0 + 2×2080Ti）**没有收益，每层等待反而 +9%**。
不采纳，`STRATA_SPLIT_OVERLAP` 保持关。**S5 指向的那条结构性候选因此也被否掉** ——
"每层 0.37 ms"不是主机同步造成的，把主机从交接里拿掉并不会变快；它更像是 GPU 侧每层启动/核间等待的固有成本。

**今晚到此的完整判断**：decode 侧的四条候选（helper、显存换重叠、调层数、overlap 交接）**全部有实测且全部否决**；
剩下的方向必须在**内核层**（每层 GPU 侧的计算/启动开销）或**层数结构**（更少的层×更大的核）上，
而不是调度/交接方式。这条结论本身就值得写进台账：**不要再在 decode 的调度上花时间。**


---

## 20. 每层的成本模型（静态估算，**未实测**；用来决定下一阶段攻哪里）

几何（`include/strata/core/geometry.hpp`）：`n_embd 2560`、`n_ff 640`、`n_expert_used 10`，窗口 T=4（4 个 slot）。
一层一窗口的专家权重字节（int8，Q4≈0.4 B/权重）：

```
gate/up 2*640*2560*10*4 = 131.1 MFLOP   down 2560*640*10*4 = 65.5 MFLOP   合计 196.6 MFLOP
同一批专家假设下的权重字节 ≈ 196.6e6 * 0.4 B ≈ 78.6 MB（4 token 各自 10 个专家、互不共享时的上界）
```

实测每层等待 **0.29–0.37 ms**（S5）。把它当上界代入：

| 量 | 值 | 对比 |
|---|---|---|
| 有效带宽 | 78.6 MB / 0.29 ms ≈ **271 GB/s** | HBM 峰值 616 GB/s → **44%** |
| 有效算力 | 197 MFLOP / 0.29 ms ≈ **0.68 TFLOP/s** | 远低于卡的峰值 → 不是算力受限 |

**若 4 个 token 的专家有共享**（连续 token 很可能共享），真实字节会更少（例如 20 个不同专家 → 39 MB），
那效率就只有 20% 左右 —— **所以下一步必须先测"每层每窗口到底读了多少不同的专家"**，这个数字决定效率是 20% 还是 44%，
也决定"攻内核"能拿回多少。

**结论（下一阶段）**：decode 的每层成本**主要是从 VRAM 流专家权重**，效率在 20–44% 的峰值带宽区间（区间宽度取决于上面的未知数）。
这正是**上游 0.2.4.x 那条 `s2_expert_grouped` 内核线**（有逐位 parity 门和 `--bench`）要解决的问题 ——
下一阶段把工作重心从"调度/交接"转到**这条内核线**：先量不同专家数，再把 0.2.4.x 的成对内核搬过来做同 boot A/B。


---

## 21. 收窄 20–44%：不同专家数其实算得出来（静态，不需新探针）

上一节留的未知数是"每层每窗口到底读了多少**不同**专家"。用几何和路由器就能定它，不必再装探针：

- 每窗口 T=4 个 slot（4 个 token），每个 token top-10 / 512 个专家；
- 两个 token 命中同一个专家的概率 ≈ 10/512 ≈ **2%**，40 次抽取的期望共享对 ≈ 0.8；
- 所以**每层每窗口的不同专家数 ≈ 40**（39–40），字节上界 78.6 MB 基本就是实际值，而不是"太悲观的上界"。

**于是效率收到一个点：271 GB/s ÷ 616 GB/s ≈ 44% 的 HBM 峰值**（0.68 TFLOP/s，远非算力受限）。
也就是说每层 0.29 ms 里有约 **0.13 ms 是"该花的"，0.16 ms 是效率损失**；把效率做到 ~70% 就能让
窗口从 ~14 ms 降到 ~9 ms，**decode 有 +50% 量级的空间** —— 这是今晚算出来的**唯一一个 ≥5% 且机理明确的靶子**。

**这也解释了为什么它是内核问题而不是调度问题**：专家权重已经驻留在 VRAM（`GPU hits` 十万级/请求），
MMQ/dp4a 只是在**读它们**；读的效率取决于每次取多少个矩阵行、几次访存、几次解包
（上游 0.2.4.x 的 `gu_pair_kernel`/`down_pair_kernel` 正是为此写的："the same integers from fewer instructions"）。

**下一轮的具体动作（二选一，按风险排序）**：
1. **先量、不动生产**：把上游 `s2_expert_grouped_parity`（自带 `--bench` 与逐位门）在**单独目录**里编出来，
   在这台卡上量"每字节/每次取行"的吞吐，得到一个与生产无关的基线；
2. 只有 1 的读数支持（≥60% 峰值可达）才谈**把成对内核搬进 0.2.2 树**，并以 `STRATA_OLD_GROUPED` 做同 boot A/B。
   判据：decode 配对 ≥5%、逐位门必过；止损：接口不兼容就只交测量报告，不改生产。


---

## 22. 轮末状态（2026-10-24 夜，round 6 收尾）

- 生产：`/health` ok；配置 `strata-022.json` md5 `ad4962eb…`（= `strata-022.prod.json`）；引擎 `build/strata`；
  `NRestarts=0`；GPU 0/1 各 20,990 / 20,930 MiB、利用率 0%、温度 37/31 ℃（空闲，无降频）。
- **本轮 0 次重启**，只读计数器 + 静态算术。
- 待做（下一轮第一件事）：在 `build-imma` 里加一个**独立微基准**，直接调 `moe_hit_grouped_s2`
  （`include/strata/kernels/s2_expert_grouped.hpp:72`，0.2.2 树就有）：造 40 个专家 × 4 token 的 slot 表，
  循环计时，量出这条命中内核的 **GB/s 与占比**。它把"44% 峰值"从一个算术推断变成实测，
  也是将来搬 0.2.4.x 成对内核时的**同机基线**。注意：与生产同卡跑，只申请 ~80 MiB；若显存不够就直接报错退出，不影响服务。


---

## 23. **实测推翻了第 20/21 节的成本模型**：专家命中内核已经在满带宽，杠子其实在 CPU 池（2026-10-24 夜）

`STRATA_DECODE_TIMING=1` 一次请求的实测（T≈2.05、1.90 token/窗口，30.09 ms/窗口）：

```
verify 25.09 = GPU-reach wait 9.29 + per-layer host 2.22 [plan 0.17 actq 0.10 jobs 0.02 CPU 2.62] + stage 1.47
per layer-window: CPU experts 0.52 (0.61 entries), VRAM hits 19.83, PCIe 0.03
strata decode GPU stages (ms/window, stage 0):
  GDN q8+qkv 0.75 conv 0.07 ab 0.12 z 0.56 rec 0.24 out-proj 0.76 router 0.34 shared+quant 0.57
  waitA 0.53 | VRAM hits 1.52 | waitB 0.14 | PCIe grp 0.12 | waitCPU 1.56 | copy 0.16 (gap 0.02)
  hc0 norm 0.29 down 0.36 up 0.38
```

**两条硬读数**：

1. **`VRAM hits` 只占 1.52 ms/窗口 → 0.063 ms/层**。每层每窗口 19.83 个命中、每专家 ≈1.96 MB（Q4）→
   39 MB / 0.063 ms ≈ **620 GB/s ≈ HBM 峰值**。**专家命中内核已经跑到满带宽了** ——
   第 20/21 节按"40 个不同专家、0.29 ms/层"算出的 44% 是**错的**（真实是 ~20 个不同专家、0.063 ms/层）。
   **所以"改内核拿 +50%"这条路不存在**，我把结论改正。
2. **`waitCPU` 1.56 ms/窗口（stage 0）+ waitA 0.53 + waitB 0.14** —— GPU 有空等，等的是 **CPU 池**。
   这就是"等 GPU 9.29 ms"之外唯一一块可动的空闲：CPU 池的每窗口延迟。

**修正后的下一阶段**：靶子从"专家内核"改成 **CPU 池延迟**（`--pcie-frac` 决定多少专家交给 CPU；
当前 0.31）。可能的动作：池的线程/亲和性、每层 CPU 专家数、池与 GPU 的重叠。判据：`waitCPU` 从 1.56 降到 <0.5 ms/窗口，
且 decode 配对 ≥5%。**这条需要新的同 boot A/B**，下一轮做。


---

## 24. 并发实测：单流 63 tok/s → 三流 136 tok/s，机器的余量在**并发**上（2026-10-24 夜）

同一份 profile 配置，三个请求**同时**发（同一 4524 token 前缀，命中 prompt cache）：

| 负载 | 每流 decode | 聚合 |
|---|---|---|
| 单流（T≈2.05） | 63 tok/s | 63 |
| 三流（每个 T=1） | 45 tok/s | **≈136 tok/s** |

**读数**：GPU 在单流时明显没吃饱 —— 单流 22–30 ms/窗口里，`GPU-reach wait` 只有 8.5–9.3 ms，
其余是每 token 的固定延迟（CPU 池 2.6–3.3 ms/窗口 + 主机环节 + 帧粒度）。三条流一叠，聚合吞吐**翻倍有余**。

**结论**：
1. 第 23 节那 1.56 ms/窗口的 `waitCPU` 是**轻载假象**，不是稳态损失；加并发它会被别的流的计算盖住。
2. 这台机器"发挥不出硬件实力"的**主因是没人并发地用它**，不是内核或调度 ——
   配置里 `--batch 4 / parallel 4` 本来就准备好了，缺的是客户端并发。
3. 因此**不建议**为了这 20% 去做有风险的池/内核改动；收益在部署侧（并发请求），不在代码侧。

**这条也解释了为什么今晚所有代码级候选都过不了 5%**：单流延迟由"每 token 的固定开销"主导，
而固定开销的来源是架构（每 token 一次窗口、每层一次交接），不是某个可替换的内核。
