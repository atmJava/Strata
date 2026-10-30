# 单路解码 200、预填 5000 与可随时重启的架构计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Version:** v5，2026-10-30。v4 及更早计划全部由本文件取代。

**Goal:** 在不牺牲正确性、可恢复性和可维护性的前提下，把双 RTX 2080 Ti 的 Strata
服务推进到单路解码稳定 200 tok/s、32K 冷预填 5000 tok/s 以上，并同时满足多路会话、
冷会话快速读取和随时可安全重启。

**Architecture:** 一个统一运行时，三条性能预算：解码窗口的 token 预算、预填阶段的专家
复用预算、启动阶段的关键路径预算。单路、批处理和多路不再各自维护一套互相排斥的参数组合；
由一个纯策略层根据 slot、上下文、显存和实测状态选择窗口、草稿、流水与停车方式，执行层只保留
一套可测试的路径。

**Tech Stack:** Ubuntu、CUDA 13、sm_75、双 RTX 2080 Ti（24/24 层切分）、
Qwen3.8-Flash-Next（`qwen4exp`，IQ3_XXS 71 GB、专家包 42.9 GB）、Strata batch、MTP、
suffix/lookup drafter、layer-major prefill、expert cache、conversation cache、
systemd 或等价的进程监督器。

---

## 1. 任务定约

### Objective

- 把“单路解码稳定 200 tok/s”作为硬目标，而不是聚合数字或 stretch goal。
- 把“6 路多会话不塌陷、不饥饿”作为独立目标，不允许用聚合吞吐掩盖每路退化。
- 把 32K 冷预填 ≥5000 tok/s、200K 冷读 ≤45 s 作为预填验收。
- 把冷会话恢复、热重启、故障自动回滚做成产品能力，而不是实验脚本。
- 删除那些让单路、批处理、预填和停车无法共存的全局参数分支；允许破坏旧配置兼容性。

### Non-goals

- 不以聚合 200 代替单路 200。
- 不继续做无边界的参数扫描；参数只能在机制候选实现后用于部署验证。
- 不为了保留旧配置而长期维护 solo、serial batch、piped batch 三套语义。
- 不在本轮引入第二个模型、分布式跨机推理或与当前两张卡无关的通用调度框架。
- 不在没有 parity、压力测试和自愈演练的情况下更换生产主路径。

### Scope

- 允许修改解码窗口、草稿策略、批处理分组、预填复用、会话停车、启动流水和运行时监督相关代码。
- 允许新增一个纯策略模块及其测试，减少 `src/program/generate.cpp` 中分散的运行时判断。
- 允许删除会制造不可用组合的旧环境变量、CLI 开关和文档说明。
- 允许改变 prompt 分块和部分数值路径，但必须通过明确的质量门；非位精确必须如实记录。

### Verification

- 每个机制先有失败测试或可复现基线，再有最小实现，再做同配置 A/B/A。
- 每次性能变更同时记录：端到端 tok/s、窗口 token 数、窗口耗时、stage 时间、草稿深度分布。
- 每个生产候选必须经过自愈演练、质量门、资源门和真实场景矩阵。
- 最终验收以第 12 节为准，不以单个峰值或单次成功请求为准。

### Risks / Unknowns

- 单路 200 不是调参问题。当前每窗口只产生 1.74 token，要跨越到约 5.4 token/窗口，
  或把窗口压到约 17.5 ms；必须先证明至少一条结构路径成立。
- 16K/32K 预填 super-chunk 可能带来非位精确结果，必须用固定 prompt 的 parity、needle
  和真实会话回归共同验证。
- 当前生产是手动启动的 server/engine 进程对；在监督器接管前，任何停服都可能留下
  不可恢复窗口。
- 当前会话本身经过目标服务器；真实停服演练会让本会话短暂中断，必须由服务器侧自愈，
  不能依赖对话客户端保活。

## 2. 验收口径

### 2.1 单路解码

| 指标 | 定义 | 目标 |
| --- | --- | --- |
| D1 单路主指标 | 单连接、4K prompt、4096 output，自然语言/代码/工具调用三类各 3 轮 | p50 ≥200 tok/s，逐秒样本 p10 ≥190 |
| D1 窗口指标 | 每个 verify window 的接受 token 数、窗口 ms、草稿深度 | 平均 ≥5.4 token/window，或窗口 ≤17.5 ms 且 ≥3.5 token/window |
| D1 无冷启动作弊 | 模型已加载，但每轮使用独立会话；prompt cache 命中不计入 decode | 仍达到 D1 |
| D1 可中断 | 生成中断流、取消、下一轮恢复 | 不破坏会话，不遗留脏 draft/KV |

“稳定”指同一模型、同一服务进程、同一批参数下的多轮结果，不允许用一次峰值代表达标。

### 2.2 多路与预填

| 指标 | 定义 | 目标 |
| --- | --- | --- |
| D2 两路 | 两连接同时生成 400 token | 每路 ≥50 tok/s，`draft_n>0`，无一路被饿死 |
| D3 六路 | 6×192K 上下文，90 s 混合负载 | 聚合 p50 ≥200 tok/s，每路 p10 ≥30，最大停顿 ≤1 s |
| P1 冷预填 | 32K 冷 prompt，独立会话，3 轮 | ≥5000 tok/s |
| P2 长冷读 | 200K 冷 prompt | ≤45 s |
| C1 冷会话读 | 50K/200K 冷会话；90% 前缀命中 | 分别 ≤15 s / ≤45 s / ≤10 s |
| R1 重启 | 热重启、冷启动 | 热 ≤60 s；冷 ≤90 s；失败自动回到上一份可服务快照 |

### 2.3 故障恢复

故障恢复的“会话继续”定义为：服务地址不变，客户端重试后恢复到同一模型和同一 conversation
状态；恢复期间已发出的请求可以返回明确错误，但不能静默丢 token、写入半截状态，或把服务
留在半启动状态。

- 自动检测故障 ≤10 s。
- 失败回滚或重新拉起 ≤180 s；W5 完成后热恢复 ≤60 s。
- 每次恢复必须有 `WINDOW RESTORED ... layer=relaunch` 或等价审计行。
- 没有恢复记录的停服动作一律视为失败。

## 3. 性能账本

### 3.1 单路解码：先看每窗口 token 数

当前生产实测与并行会话数据：

| 路数 | 聚合 tok/s | 每窗口 token | 窗口 ms | 每路 tok/s |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 64.8 | 1.74 | 26.8 | 64.8 |
| 2 | 75.8 | 2.00 | 26.4 | 37.9 |
| 6 | 164.7 | 6.00 | 36.4 | 27.5 |

单路 200 的基本账：

```text
200 tok/s = accepted tokens per window / window seconds
```

- 保持当前 26.8 ms 窗口，需要约 5.36 token/窗口。
- 若只做到 3.5 token/窗口，需要约 17.5 ms/窗口。
- 若做到 4.5 token/窗口，需要约 22.5 ms/窗口。
- `kVerifyMaxT=8` 允许最多 8 行，但 5.36 token/窗口几乎没有留给拒绝和调度浪费的余量。

所以单路 200 的主攻方向不是“再开一个参数”，而是提高有效草稿链的接受 token 数，同时降低
窗口固定成本。必须采集：

- 每个窗口实际提供的 MTP 草稿数，而不是只看平均 `draft_n`。
- 按草稿深度分桶的接受率，看到第 1、2、...、7 个猜测各剩多少。
- suffix/lookup 草稿与 MTP 草稿的命中比例。
- verify、commit、host 同步、跨卡 hand-off、采样各占窗口多少。
- 两卡是否出现等待空转；单路窗口利用率是否被层切分串行放大。

### 3.2 多路：每路不能靠聚合掩盖

6 路当前的 6.00 token/窗口说明，多路已经接近“每 slot 每窗口一个 token”。聚合 200 需要
把窗口压到约 30 ms 并保持 6 个 slot 都有可验证工作；单路 200 需要完全不同的乘数。

因此 D1 和 D3 必须分开验收：

- D1 失败不能由 D3 通过来抵消。
- D3 通过也不能把单路 35–50 tok/s 解释成“多路总吞吐很好”。
- 2 路、6 路、8 路都必须记录每路 p10、最大停顿、slot 等待和上下文长度。

### 3.3 预填：专家复用决定上限

当前 201K 冷预填约 100 s，约 2.0K tok/s。`--prefill-pipe 1` 把提示切成约 7424 token
的块，并且会关闭 layer-major 的“每层专家只读一次”能力。专家包约 42.9 GB，两卡常驻约
28 GB，补集约 19.62 GiB。

PCIe 账本：

| chunk | 每 chunk 补集流量上限 | 5K tok/s 所需 PCIe | 判断 |
| ---: | ---: | ---: | --- |
| 7424 | ≈19.6 GiB | 13.2 GiB/s | 贴着有效上限，基本不可达 |
| 16384 | ≈19.6 GiB | 6.0 GiB/s | 可行，需要约 50% 有效利用率 |
| 32768 | ≈19.6 GiB | 3.0 GiB/s | 有余量，瓶颈转为计算、同步和公平性 |

预填架构必须让同一份专家为更大的 token 区间服务，而不是继续在 7K 小块上反复搬运。
16K/32K super-chunk 是候选，不是默认事实；必须同时测速度、显存、PCIe、输出质量和
冷读公平性。

### 3.4 启动：当前是一条串行流水

| 阶段 | 当前耗时 |
| --- | ---: |
| dense 权重 831 MiB | 10–11 s 冷、1.3 s 热 |
| 原生投影 1313 MiB | 0.4–1.5 s |
| MTP draft 837 MiB | 1.1–3.7 s |
| 专家槽预填 10065 slots | 15–25 s |
| 补集 19.62 GiB pin + map | 58–62 s |
| vision | 0.7–11.4 s |
| CUDA 初始化、图捕获、元数据 | 约 40–60 s |
| 合计 | 约 165 s |

热重启 60 s 的关键是跳过重复 pin/map、并行可独立阶段、懒加载 vision，并保留故障回滚
所需的 last-known-good 快照；不能只优化磁盘读取。

## 4. 当前代码与架构诊断

请求路径目前是：

```text
准入
  -> 预填（chunk / prefill-pipe / layer-major / concurrent read / lead-in）
  -> 批窗口（batch / batch-groups / self-commit / MTP / verify / commit）
  -> 采样与流式返回
```

问题不在某一条 `if`，而在同一能力被三种路径重复实现：

| 能力 | solo | serial batch | piped batch |
| --- | --- | --- | --- |
| MTP 草稿 | 有 | 需要 `BATCH_MTP` | 还要 `PIPED_MTP` |
| 窗口提交 | 普通 commit | 可 self-commit | self-commit 与 piped MTP 互斥 |
| 每槽 draft K/V / 停车 | 有 | 依赖 batch MTP | 依赖 piped MTP |
| 并发预填 | 不适用 | 不适用 | 被 staged source 等能力关闭 |
| layer-major 预填 | 可选 | 可选 | `--prefill-pipe` 会关闭它 |

这形成了几类结构性死路：

- `STRATA_BATCH_SELF_COMMIT=1` 会让 `STRATA_PIPED_MTP` 直接失效；现网因此出现两路
  `draft_n=0`，一路结束后另一路立即恢复。
- `--prefill-pipe 1` 与 layer-major 复用在当前实现中互相冲突，预填吞吐被 7424-token
  的专家重复读取限制。
- `STRATA_PIPED_CONCURRENT` 被 staged/helper/pcie-dma 等源关闭，日志多次出现并发读
  未生效。
- 长会话停车依赖 `--batch-mtp`，没有草稿能力时 95K–216K 会话不能被可靠保存和复用。

这些不是“再调一个组合”能修好的问题。它们要求把能力选择从全局环境变量移到一次性计算出的
运行时策略中。

## 5. 目标架构

### 5.1 一个统一的解码执行器

不再让 solo、serial batch、piped batch 各自解释一套开关。新增一个纯策略层，例如：

```text
DecodePlan {
    window_cap          // 1..8，单路也要能使用完整窗口
    draft_budget        // 1..7，由接受率和置信度控制
    group_count         // 由 slot 数、卡数和窗口上限计算
    overlap_mode        // 无重叠、跨窗口流水、跨阶段流水
    state_mode          // 普通、停车、恢复
    self_commit         // 只能是执行器内部优化，不再是用户能力开关
}
```

策略层只回答“本请求本窗口应该怎么跑”，执行层继续复用现有 Verifier、MTP drafter、
commit 图和 slot 状态。这样：

- 单路与多路使用同一个窗口语义。
- 每个 slot 都拥有自己的 draft/KV/停车状态，不再依赖某个全局 batch 开关。
- 任何组合不能运行时有明确的降级原因，而不是静默变成另一条旧路径。
- 参数只决定资源上限，不决定是否存在功能。

### 5.2 单路窗口必须同时攻击两个乘数

单路 200 按顺序验证两类机制，不能一开始就做 kernel 微优化：

1. **提高有效 token/窗口**：让 MTP 链、suffix 和 lookup 草稿共享一个 8 行验证预算，
   按实测接受率动态决定草稿深度；不再把 `--mtp-max-t 4` 当作长期产品边界。
2. **降低窗口关键路径**：把两卡的 verify/hand-off/commit 做成可重叠的窗口流水；
   如果单路仍被固定 launch/同步拖住，再进入 W6 的内核与图捕获优化。

两项都必须有独立指标。只提高草稿深度但窗口变慢，不能算成功；只压低窗口但没有足够 token，
也不能算成功。

### 5.3 预填是“专家复用 + 公平让路”

预填路径收敛为：

```text
PreFillPlan {
    superchunk_tokens    // 优先 16K/32K，按显存和 parity 选择
    layer_major          // 主路径启用，不再由 per-request pipe 关闭
    expert_stream        // 与 attention/router/compute 重叠
    fairness_quantum     // 长读给解码让路的最小时间片
}
```

目标是每个专家在大 super-chunk 内只读一次，同时让正在输出的会话获得有界停顿。
`--prefill-pipe` 可以通过兼容层映射，但不再作为主架构的互斥开关。

### 5.4 会话状态与停车是一等公民

每个 slot 必须能保存：

- 已提交的 token 和位置。
- live KV、QSA indexer、GDN 状态。
- MTP draft K/V 和链位置。
- 可恢复的 checkpoint。
- 会话是否处于 cold、parked、resuming、serving 的明确状态。

冷会话读取不是“重新跑一遍 prompt”，而是从最近的可信前缀继续；长会话没有 draft K/V 时
不能停车，必须显式报错并进入修复队列，而不是静默退回普通 slot。

### 5.5 启动由监督器负责，不由操作员记忆负责

生产必须由一个受管 supervisor 持有：

- 启动前校验 engine/server/config/hash 与 GPU 拓扑。
- 保留 last-known-good 快照。
- 启动失败自动回滚并重新 health check。
- 进程异常、日志管道断裂、CUDA 初始化失败都能被观察到。
- 不再依赖“上一条命令还在不在”或人工记 PID。

“随时可重启”不等于没有停机，而是每次停机都有自动恢复、状态快照和审计。

## 6. 参数与兼容策略

用户已明确允许废弃/移除参数。原则是：删除制造架构分叉的全局开关，保留必要的数值上限和
单次实验覆盖；不为了旧配置完整复刻旧执行路径。

| 旧参数/变量 | 处理 | 原因 |
| --- | --- | --- |
| `STRATA_BATCH_MTP` | 删除为全局门；由 `DecodePlan` 自动选择 | 不应决定“批处理有没有草稿” |
| `STRATA_PIPED_MTP` | 删除为全局门；合并为 `draft_budget` | 不应要求操作员同时开两个变量 |
| `STRATA_BATCH_SELF_COMMIT` | 从用户配置移除；若保留只能是窗口内部策略 | 与 piped MTP 互斥，制造死组合 |
| `--mtp-max-t` | 从产品配置移除；策略按接受率选择 1..7 | 静态上限掩盖真实草稿收益 |
| `--prefill-pipe` | 兼容一个迁移周期后删除；主路径改为 layer-major super-chunk | 当前实现会关闭主要复用能力 |
| `STRATA_PIPED_CONCURRENT` | 合并进统一预填调度器 | 不是独立性能产品，应与公平性同一策略 |
| `--batch-groups` | 保留为诊断/上限，不作为用户必须理解的组合 | 组数应由 slot、stage、窗口预算推导 |
| `--spec-min-p` | 暂时保留为实验覆盖，主路径由策略自适应 | 仍可用于验证置信度与接受率关系 |

兼容实现只允许存在于一个明确的适配层：读取旧配置，转换成一个 `RuntimeDecision`，
并打印“已弃用/已忽略”的原因。适配层不得复制旧执行逻辑。完成一次版本迁移和 soak 后
删除旧变量。

代码可读性要求：

- 一个策略模块只做纯计算，可用单元测试覆盖，不直接操作 GPU 或进程。
- 执行器不读取散落的环境变量；需要的决策由策略对象传入。
- 新增逻辑优先拆成小而明确的函数，不继续扩大单个 `generate.cpp` 分支。
- 删除旧分支比新增兼容分支优先；兼容只保留在输入边界。

## 7. 可行性裁决

| 目标 | 当前 | 缺口 | 结论 | 主要杠杆 |
| --- | ---: | ---: | --- | --- |
| D1 单路 200 | 64.8 | 3.1× | **硬目标；W2 必须给出可行机制或明确失败证据** | 5.4 token/window 或 17.5 ms/window |
| D3 六路聚合 200 | 164.7 | +21% | 可达 | 6 slot 保持活跃、窗口 ≤30 ms、公平调度 |
| P1 32K 预填 5000 | ≈2.0K | 2.5× | 条件可达，先做 layer-major 16K/32K | 专家复用、流式重叠 |
| C1 200K 冷读 ≤45 s | ≈100 s | 2.2× | 跟随 P1 | 相同预填架构 |
| 热重启 ≤60 s | ≈165 s | 2.75× | 可达 | 跳过重复 pin、并行、vision 懒加载 |

单路 200 的数学约束不能绕开。若 W2 实测显示线性 MTP 链的接受率上限不足，下一步必须是
改变草稿机制（例如 suffix/lookup 组合、受限分支或跨窗口流水），而不是把验收改成聚合数字。
若所有结构路径都无法达到 200，必须停下来报告证据并请用户裁决；不得静默降低目标。

## 8. 里程碑

### M0：可恢复运行时与正式账本

**目的**：先把“随时可重启”变成基础设施，再测性能。M0 未通过前不启动任何生产候选。

- [ ] **M0-1 运行时盘点**：记录实际 server/engine/config 的可执行路径、argv、cwd、
  hash、PID、端口和 GPU 拓扑；确认当前手动进程与 `strata.service` 的关系。
- [ ] **M0-2 部署监督器**：实现或接入 `util-restore.sh`、`util-watchdog.sh`、
  `dump-runtime.py`，禁止硬编码 sudo 口令；使用受限 sudoers 或受控 askpass。
- [ ] **M0-3 回滚测试**：先用假进程/非生产端口演练启动失败、健康检查失败、日志管道断裂、
  引擎崩溃和 supervisor 自身重启。
- [ ] **M0-4 真实演练**：在可接受的维护窗口让监督器接管生产；要求日志出现
  `WINDOW RESTORED ... layer=relaunch`，并验证客户端重试后恢复同一 conversation。
- [ ] **M0-5 正式快照**：冻结 engine、server、config、runtime JSON 和 manifest；
  记录旧快照恢复命令。
- [ ] **M0-6 解码账本**：单路、2 路、6 路各 3 轮；记录窗口 T、接受 token、
  草稿深度分布、stage ms、commit ms、hand-off 和每路 p10。
- [ ] **M0-7 预填账本**：32K、128K、200K 冷预填各 3 轮；记录 layer-major 是否启用、
  super-chunk、专家读取量、PCIe、GPU timeline 和 wall。
- [ ] **M0-8 启动账本**：3 次热重启和 1 次冷启动，拆分权重、slot 预填、pin/map、
  vision、图捕获和健康检查。

**Gate:** 自愈演练成功；基线与本文件差距 ≤10%；未解释的配置漂移为零。否则停止性能实验，
先修观测和恢复。

### W1：统一解码策略

**目标**：删除 solo/serial/piped 三套能力门的结构性分叉。

**Files:**
- Create: `include/strata/program/decode_plan.hpp`
- Create: `tests/program/decode_plan_test.cpp`
- Modify: `src/program/generate.cpp`
- Modify: `docs/BATCHING.md`
- Test: `tests/program/` 与 `tools/batch_test.py`

- [ ] **W1-1 写失败测试**：覆盖 1/2/6/8 路、不同上下文、8 行上限、显存不足、
  两卡切分和 unsupported source；断言每种状态都有且只有一个合法 `DecodePlan`。
- [ ] **W1-2 实现纯策略**：输入 slot 数、stage 数、上下文、可用显存、草稿接受历史和
  预填状态，输出窗口上限、草稿预算、组数、重叠模式和停车能力。
- [ ] **W1-3 接入执行器**：让单路、批处理和流水组都消费同一策略；旧变量只在适配层读取。
- [ ] **W1-4 删除死组合**：去掉 self-commit 一票否决、BATCH_MTP/PIPED_MTP 双重开关
  和其他会让路径静默退化的分支。
- [ ] **W1-5 回归门**：单路、2 路、6 路 greedy/sampled token parity；取消、断流、
  长 prompt、slot 结束、下一轮继续全部通过。

**Gate:** 任意受支持路径都能产生非零草稿和每槽状态；不存在“参数被接受但机制静默关闭”。

### W2：单路 200 的窗口机制

**目标**：用实测数据证明并实现单路 200，不以调参替代结构改动。

- [ ] **W2-1 采集深度分布**：对 4K prompt、4096 output，记录每个 draft 深度的提供/
  接受数量；自然语言、代码和工具调用各 3 轮。
- [ ] **W2-2 实现候选 A：深草稿预算**：在 8 行上限内动态提供最多 7 个提案，比较
  MTP、suffix、lookup 的组合收益；保持窗口耗时不变或下降。
- [ ] **W2-3 实现候选 B：跨窗口/跨阶段流水**：让 stage 0 的下一窗口与最后一 stage
  的当前窗口重叠；只有在单路实际缩短关键路径时保留。
- [ ] **W2-4 实现候选 C：受限分支草稿**：如果线性链的接受率上限不足，评估一个请求内
  多条候选前缀共享验证预算的方案；必须先证明状态和 commit 可正确隔离。
- [ ] **W2-5 结果门**：达到 5.4 token/window（≤26.8 ms）或 3.5 token/window
  （≤17.5 ms），且不破坏 D2/D3；否则停止并向用户报告具体缺口。

**Gate:** 单路 p50 ≥200、逐秒 p10 ≥190；没有用 prompt cache、缩短输出或只测
数字序列作弊。不达标时不得把目标改成聚合 200。

### W3：预填复用与长冷读

**目标**：32K 冷预填 ≥5000 tok/s；200K 冷读 ≤45 s。

- [ ] **W3-1 主路径切换**：把 layer-major super-chunk 设为标准路径；旧 `prefill-pipe`
  只经适配层映射，不再关闭 layer-major。
- [ ] **W3-2 super-chunk 三档**：8K、16K、32K 各做同 prompt A/B/A；记录 PCIe、
  expert 命中、GPU timeline、VRAM 峰值和输出质量。
- [ ] **W3-3 专家流与计算重叠**：专家读取、router、attention、expert compute
  能重叠的放同一流水；不能重叠的必须有计时证据。
- [ ] **W3-4 公平让路**：长预填按固定 quantum 让解码，记录邻居最大停顿；不能靠
  无限加长 lead-in 让短请求“看起来快”。
- [ ] **W3-5 质量门**：greedy parity、needle、64K/128K/200K 真实会话、取消和续读；
  非位精确路径必须报告 KL/needle/可复现差异，不得写成 bit-identical。

**Gate:** P1、P2 达标；若只达到 3500–4500 tok/s，保留账本并进入 W6 条件内核项，
不能把中间速度写成最终通过。

### W4：多路、停车与冷会话

**目标**：6×192K 无饥饿；冷会话恢复不重读已完成前缀。

- [ ] **W4-1 每槽状态**：确认每个 slot 的 draft K/V、live KV、GDN、checkpoint
  能独立保存和恢复；日志不再出现因为缺少 batch MTP 而无法停车。
- [ ] **W4-2 公平调度**：6 路同跑 + 一路 200K 冷读，记录每路 p10、最大停顿、
  slot wait、预填 quantum 和取消后的资源回收。
- [ ] **W4-3 冷读**：50K、200K、90% 前缀命中 200K 各 3 轮；使用同一
  conversation cache 版本，验证重启后仍能恢复。
- [ ] **W4-4 容量边界**：6 路满载、第 7 路等待、8 路、长工具调用和客户端断流；
  区分 queue、admission、prefill、decode 四种等待。

**Gate:** D2/D3/C1 达标；没有 slot 饥饿、无 swap 抖动、无静默降级。

### W5：启动与重启

**目标**：热重启 ≤60 s、冷启动 ≤90 s，失败自动恢复。

- [ ] **W5-1 并行启动**：dense、专家槽、MTP、vision、CUDA 图捕获中可独立的阶段并行。
- [ ] **W5-2 去掉重复 pin/map**：页缓存热时复用可信映射或延后 pin；失败自动回退
  当前路径，不允许为了速度猜测状态。
- [ ] **W5-3 vision 懒加载**：不影响文本首请求和 health；vision 失败不能拖垮文本服务。
- [ ] **W5-4 恢复演练**：kill engine、kill server、损坏候选配置、GPU 初始化失败
  各一次；每次检查 `layer=relaunch`、conversation 可续和旧快照不变。
- [ ] **W5-5 性能门**：3 次热重启、1 次冷启动；记录每阶段时间和总时间。

**Gate:** R1 达标，且任何失败都能回到 last-known-good。

### W6：条件性内核与图优化

只有 W2/W3 已证明瓶颈在某个 kernel、launch 或图捕获，且结构改动后仍差目标时才启动。

- 一次只改一个 kernel 或一个图路径。
- 必须同时改善目标硬件计数和端到端 wall。
- 必须重跑单路、2 路、6 路、预填、冷读和 parity。
- NCU 不能附加现役进程；必须用受控实例和自愈窗口。

## 9. 场景矩阵

| 编号 | 场景 | 必须记录 |
| --- | --- | --- |
| S1 | 单路 4K prompt，4096 output，混合自然语言/代码/工具 | p50/p10、窗口 token、窗口 ms、草稿深度 |
| S2 | 两路并发，每路 400 output | 每路/聚合、draft_n、最大停顿、TTFT |
| S3 | 一路 200K 冷读 + 一路持续解码 | 邻居 tok/s、最长静默、预填 quantum、总时长 |
| S4 | 24K 工具调用 + 一路聊天 + 一次取消 | 工具总时长、取消回收、下一轮会话 |
| S5 | 6 路 ×192K，90 s | 聚合 p10、每路 p10、slot wait、内存/swap |
| S6 | 第 7 路晚到、8 路过载 | admission 时间、排队上界、拒绝/恢复 |
| S7 | 热重启、冷启动、故障注入 | 每阶段秒数、health、恢复 audit、会话续读 |

## 10. 测量协议与证据

- 每项至少 3 轮；报告 p50、p10/p90，不只报告最好一次。
- A/B/A 必须在同一 boot、同一模型、同一 prompt、同一输出上限和同一 cache 状态下完成。
- A1/A2 漂移 >5% 时加到 5 轮；仍漂移则先查环境，不叠加新改动。
- 每次实验写独立目录：`/opt/ai/logs/util-v5-$TS/`。
- 目录至少含：manifest、运行 argv、hash、原始日志、JSONL、质量结果、判决和回滚路径。
- 性能数据必须附带硬件、驱动、模型、context、cache、并发数和未测项。
- 每个生产窗口有自愈和硬截止；没有 `layer=relaunch` 时不得开始下一轮。

### 质量门

- greedy token parity：同一输入在旧/新路径逐 token 比较；允许的非位精确必须提前声明。
- sampled 输出：固定 seed 做统计比较，不能把“能生成”当作质量通过。
- needle：至少覆盖 50%、90% 深度。
- 真实会话：长工具调用、代码修改、断流重试、上下文续读。
- 一小时后段 RSS、可用内存、swap、专家命中率和 GPU 空闲显存保持稳定。

## 11. 停止条件

- 自愈没有 `layer=relaunch`：停止所有停服动作。
- 基线无法在 ±10% 内复现：停止性能实验，先查环境。
- OOM、swap 持续增长、slot 静默降级、parity/needle 失败：立即回滚。
- 单路回退 >3%、6 路回退 >3%、200K 冷读回退 >5%：候选不采纳。
- 同一候选连续两次没有超过噪声下限：停止该机制，换结构，不重复同形实验。
- 单窗口 >20 分钟或累计 >8 个窗口：停止复盘，不继续堆实验。
- W2 无法证明单路 200 的结构路径：停止并向用户报告缺口，不擅自降低验收口径。

## 12. 最终验收

- D1：单路 4K/4096 三类负载 p50 ≥200 tok/s，逐秒 p10 ≥190。
- D2：两路每路 ≥50 tok/s，双方都有非零草稿，无明显饥饿。
- D3：6×192K 聚合 p50 ≥200，每路 p10 ≥30，最大停顿 ≤1 s。
- P1：32K 冷预填 ≥5000 tok/s；P2：200K 冷读 ≤45 s。
- C1：50K ≤15 s；200K ≤45 s；90% 前缀 200K ≤10 s。
- R1：热重启 ≤60 s；冷启动 ≤90 s；故障回滚 ≤180 s。
- 质量：parity、needle、长上下文、取消、续读和一小时 soak 全过。
- 运维：生产由监督器持有，所有重启可审计、可回滚，不依赖手工 PID。
- 代码：没有让能力静默消失的全局参数死组合；策略有单元测试，执行路径可读、可删、可重启。

## 13. 命令与实验入口

以下命令只描述当前已知入口；具体路径在 M0 快照时按实际进程 argv 和 hash 核对，
不从旧目录猜。

```sh
# 解码：单路/多路账本
python3 /opt/ai/bin/session_matrix.py --streams 1 --tokens 4096 --rounds 3 \
  --label v5-s1 --out "/opt/ai/logs/util-v5-$TS/s1.jsonl"
python3 /opt/ai/bin/session_matrix.py --streams 2 --tokens 400 --rounds 3 \
  --label v5-s2 --out "/opt/ai/logs/util-v5-$TS/s2.jsonl"

# 窗口、草稿深度、stage 时间
grep -aE "strata decode timing:|strata decode GPU stages|accepted per round|spec depth" \
  /home/atm/verify-20261027/strata-verify/strata-verify.log | tail -20

# 冷预填
python3 /opt/ai/bin/cold-prefill.py v5-32k 32768 3
python3 /opt/ai/bin/cold-prefill.py v5-200k 200000 3

# 自愈与恢复审计
rg -a "WINDOW RESTORED|layer=relaunch|health|rollback" \
  "/opt/ai/logs/util-v5-$TS"
```

实验时只允许：

1. 建快照和 manifest。
2. 在受限候选端口或监督器窗口启动候选。
3. 先做 smoke、parity、health。
4. 再做 A/B/A；失败立即恢复并记录。
5. 通过门槛后才修改生产 default，并在下一个窗口复测。

任何“先改生产再观察”的做法都视为违反本计划。
