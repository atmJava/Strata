# Flash-Next 单模型运行时重构计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Version:** v7.1，2026-10-30。v6 及更早计划全部由本文件取代。

**Goal:** 只使用 Qwen3.8-Flash-Next，把双 RTX 2080 Ti 上的 Strata 重构为硬件感知、
机制先行、可观测、可调度的单模型运行时。产品目标是单路解码稳定 200 tok/s、
冷预填 5000 tok/s 以上、多路会话不互相拖死、冷状态可快速恢复，并且生产重启失败时
自动回到 last-known-good。

**Architecture:** 先冻结真实运行证据，再计算硬件 roofline，随后依次裁决草稿器、
PP/peer/TP/EP 拓扑、调度与状态缓存、服务端输出链路。任何性能结论必须能落到一段
关键路径和一份资源账本。统一运行时只保留一套请求状态和一套能力裁决，外部 owner
runtime 是生产生命周期的唯一控制者。

**Tech Stack:** Ubuntu、CUDA 13、sm_75、双 RTX 2080 Ti 22 GB、NVLink NV2、
Qwen3.8-Flash-Next IQ3_XXS、DFlash2、Strata、systemd/等价启动器。

---

## 0. 决策摘要

### 0.1 本计划的六条硬约束

1. 当前只使用 Flash-Next；其他主模型、27B 快速档和未经批准的第二模型均不在范围内。
2. 200 tok/s 是单路产品目标，不用多路聚合、greedy 专项数字或 engine 内部估算替代。
3. 先证明物理可行和关键路径，再决定实现；不先写一个“统一策略层”再寻找性能来源。
4. solo、batch、PP、peer 和未来 TP/EP 共用一套请求状态与能力矩阵，不保留互相矛盾的隐藏分叉。
5. 当前会话经过生产 Strata。没有唯一监督者、自动回滚、health/smoke 和恢复语义，不重启生产。
6. 允许删除制造路径分叉的旧参数，但每项删除都必须有替代机制、观测和迁移说明。

### 0.2 v6 的主要不足

- 200 tok/s 尚未绑定 sampling、API、上下文长度和客户端可见口径。
- 物理 roofline 放在 G2，太晚；可能写完 DFlash2 batch 才发现窗口上限不足。
- host expert 只被当成“CPU 流量”，没有拆成 GPU 驻留、peer 驻留、CPU 计算和存储读取四层账。
- 没有把仓库已有的 `--peer-device` 作为正式拓扑候选。
- 没有规定 `strata runtime`、server 内部 engine restart、systemd 三套控制面谁拥有最终裁决权。
- 没有覆盖 HTTP、tokenize、structured output、tool parser、detokenize、SSE 和 Python GIL。
- 没有证明 engine log、server log、`/metrics` 和 Nsight 的时钟及请求 ID 可以对齐。
- 没有明确缓存失效原因分类、统计样本协议、冷启动 I/O 账和生产/探针噪声边界。

### 0.3 v7 的裁决顺序

```text
产品口径
  -> 真实运行证据
  -> 硬件与请求物理上限
  -> 草稿与采样机制
  -> 拓扑和状态放置
  -> 调度、缓存和 API 输出链路
  -> 整机可行性裁决
  -> 代码级实施计划
```

任何跳过前一层的“优化”都只能是诊断实验，不能进入生产主路径。

---

## 1. 产品目标与验收口径

### 1.1 目标口径矩阵

所有 decode 数字都以“已提交的生成 token”为分子，不使用 accepted draft token。
engine 指标从第一个生成 token 到最后一个生成 token 计量；客户端指标从收到首个可见
delta 到最后一个可见 delta 计量。两类数字必须同时保留。

| ID | 场景 | 必须固定的条件 | 硬目标 |
| --- | --- | --- | --- |
| D1-4K | 单路产品路径 | 4K prompt、4096 output、当前生产 sampling、主 Codex API、固定网络路径 | p50 >= 200 tok/s |
| D1-32K | 单路产品路径 | 32K prompt、4096 output、当前生产 sampling、主 Codex API、固定网络路径 | p50 >= 200 tok/s |
| D1-200K | 单路产品路径 | 200K prompt、4096 output、当前生产 sampling、主 Codex API、固定网络路径 | p50 >= 200 tok/s |
| D1-stable | 稳定性 | 覆盖以上长度，warmup 后按 1 秒滑窗统计 | 滑窗 p10 >= 190，最差连续 1 秒 >= 150 |
| D1-greedy | 机制诊断 | 同长度、greedy、允许 DFlash2 | p50 >= 200，仅用于归因，不能替代产品验收 |
| P32 | 冷预填 | 32K、无会话缓存、engine prompt 处理时间 | p50 >= 5000 tok/s，p10 >= 4500 |
| P200 | 长冷预填 | 200K、无会话缓存、engine prompt 处理时间 | p50 >= 5000 tok/s，总时长 <= 45 s |
| M2 | 两路交互 | 一路长输出、一路短 JSON/代码，两路都活跃 | 每路 p50 >= 120，聚合 p50 >= 240，最大 token gap <= 500 ms |
| M6 | 六路混合 | 长上下文、短问答、工具调用混合，持续 90 s | 聚合 p50 >= 400，每路 p10 >= 30，最大 gap <= 1 s |
| C50 | 冷会话恢复 | 50K 会话快照、同模型同运行时 | <= 15 s 到可继续首 token |
| C200 | 长会话恢复 | 200K 会话快照、同模型同运行时 | <= 45 s 到可继续首 token |
| C90 | 高复用恢复 | 200K 中 90% 前缀命中 | <= 10 s 到可继续首 token |
| R-warm | 热重启 | OS page cache 热、配置和模型不变 | <= 60 s 恢复服务 |
| R-cold | 冷重启 | page cache 冷、模型和运行时不变 | <= 90 s 恢复服务 |
| R-rollback | 失败回滚 | 候选启动或 smoke 失败 | <= 180 s 回到 last-known-good |

以上是产品验收值。G1 若证明当前硬件不存在可行机制，必须提交物理账并请求重新裁决，
不得静默降低、改分母或拿另一个 workload 的数字补洞。

### 1.2 API、sampling 与客户端口径

G0 只冻结了当前配置默认值：`temperature=0.6`、`top_p=0.95`、`top_k=20`、
`repetition_penalty=1.0`。逐请求实际 sampling 和客户端覆盖仍未记录，不能把配置默认值
当作真实会话分布，也不能沿用此前未经同一实例复核的 `temperature=1.0` 观察。

代码事实：

- DFlash2 在 `temperature > 0`、repetition penalty、`logit_bias` 或部分 checkpoint
  resume 时回退 MTP。
- 因此 greedy DFlash2 数字不能代表当前 Codex 会话。
- 200 tok/s 的产品验收必须覆盖真实 sampling；greedy 只作为隔离机制变量。

每个目标样本必须记录：

- 实际 API：Chat Completions、Responses 或 Anthropic-compatible。
- `temperature`、`top_p`、`top_k`、penalty、`logit_bias`、thinking 和 tool choice。
- prompt token、reused token、read token、output token 和 finish reason。
- engine decode、服务端 SSE cadence、客户端可见 decode 三层结果。
- stream 与 non-stream 分开；结构化输出和 tool call 分开。
- 客户端到 server 的实际路径：loopback、LAN、Tailscale、Cloudflare 或代理；网络路径变化时
  不得直接把客户端 tok/s 与另一个路径混比。

G0 确认 Codex 实际使用的主 API。主 API 进入 D1 硬验收；其余兼容 API 至少要证明
token 正确、事件语义正确且没有数量级额外开销。

### 1.3 测量分母与稳定性

- 原始请求账本保留全部样本；生产性能分布只纳入分类为 `production` 的完成请求。
- 启动 warmup、短 smoke、固定 400-token 压测、仪器探针和人工合成负载全部从生产分布剔除。
- 失败、取消、超时、提前 EOS 和断连进入单独的可靠性 cohort，不从账本删除，也不得混入
  成功请求的 decode 分布。
- 每次剔除都必须写出 request ID 和原因；报告必须同时给出原始数、剔除数和生产数。
- engine 日志中的 verify、batch 和 prompt summary 行不是 HTTP 请求，不能直接计入请求分布。
- p50、p10、p90、p95、p99、最大值、最大 token gap 和样本数必须一起报告。
- 固定长度实验先做 warmup，再做至少 20 个有效样本；200K 冷样本至少 5 个。
- 真实流量至少按请求形状累计 100 个样本后才形成分布结论。
- A/B 实验采用同一 boot、同一模型、同一配置和 A-B-A 交错；顺序随机化，固定 seed
  只在语义允许时使用。
- 缓存实验必须明确 cold、page-cache warm、prefix warm、full state warm 四种初态。
- 吞吐差异必须给出 bootstrap 置信区间；小于已测噪声线或方向不一致时不形成结论。

### 1.4 “会话继续”的定义

重启期间允许 in-flight HTTP 流明确失败，不允许静默丢 token 或半提交状态。恢复后
服务地址、API key、模型和配置身份必须一致，客户端可安全重试。

只有同时满足组件版本、模型 hash、KV/GDN/QSA/draft 状态和最后已提交 token 一致，
才允许从会话状态续读。当前文档确认：

- parked snapshot 不跨 restart 保存；session file 可以跨同版本 restart。
- session file 当前不支持 `--layer-split`、`--peer-device`、`--batch`。
- session file 也不保证跨 restart 的 exact token replay。

因此，当前生产 PP 路线实际上没有满足多路、PP 和快速恢复三者兼容的会话恢复能力。
这项矛盾必须在 G4 前解决，不能由 supervisor 单独掩盖。

---

## 2. 已确认事实与待冻结事实

### 2.1 G0 冻结基线（2026-10-30）

证据保存在 `docs/work/evidence/flash-next-runtime-g0-20261030.json`，状态为
`partial_pass_g0_gate_closed`。本表只使用同一 boot、同一实例、同一配置下取得的只读证据。

| 场景 | G0 结果 |
| --- | ---: |
| `/health` | 20/20 成功；p50 0.745 ms，max 1.181 ms |
| `/metrics?requests=all` | 原始 97 条；剔除 11 条探针；生产 86 条 |
| 生产完成状态 | `finish=stop` 86/86 |
| 生产 prompt / reused / read | 7,246,803 / 6,721,079 / 525,724 tokens |
| reused 比例 | 92.75% |
| 生产 output / engine generated | 149,445 / 143,009 tokens，比例 1.045 |
| 聚合 prompt read | 1,150.4 tok/s |
| 聚合 output / engine decode | 76.4 tok/s |
| engine decode 分布 | p10/p50/p90 = 40.05 / 73.2 / 83.1 tok/s |
| 零草稿生产请求 | 13 条；decode p10/p50/p90 = 38.8 / 39.9 / 40.6 tok/s |
| 正草稿生产请求 | 73 条；decode p10/p50/p90 = 65.0 / 74.2 / 84.0 tok/s |
| 请求重叠 | 71 条 solo、14 条两路重叠、1 条长请求与 12 个后续请求重叠 |
| 冷预填 | 4 条；745.2-1,997.6 tok/s；200K 为 100.8 s |
| 专家缓存命中 | p10/p50/p90 = 95.2% / 98.0% / 98.9% |
| PCIe offload share | p50/p90/p99/max = 0.2% / 0.75% / 3.24% / 4% |
| file blob | 生产样本为 0 |

当前 `/metrics.decode_tok_s` 的分子是 `engine_generated`，分母是 engine `decode_ms`，
既不是 `output_tokens/decode_ms`，也不是客户端 SSE cadence。计划必须同时保留：

- engine generated / engine decode。
- 已提交 output token / engine decode。
- 客户端可见 token / 首 delta 后墙钟。

长工具调用 `2038b6a7d98f` 的 output 为 24,319，engine generated 为 17,894，
总墙钟 497.9 s；只报 engine decode 会隐藏两层 token 口径差异。

此前固定 400-token 和两路压测只保留为机制诊断：单路中位 62.5 tok/s、两路聚合中位
75.8 tok/s、每路约 36.4 tok/s、一路结束后邻居恢复到约 68.4 tok/s。它们不进入
86 条真实生产分布。单窗口诊断值 1.74 token / 26.8 ms 也仍需在同一实例上复测。

硬件与运行边界同样进入基线：

- 双 RTX 2080 Ti，各 22,528 MiB；G0 时空闲显存仅 819/920 MiB。
- NVLink 为 NV2，每卡双链路，单链路报告 25.781 GB/s；P2P 与 P2P atomics 可用。
- 所有 NVLink Replay/Recovery 为 0；CRC 在 05:00 UTC 后未继续增长，不能据此判故障。
- Xeon E5-2680 v4 为 14C/28T、单 NUMA；13 个 expert worker 绑定 CPU 1-13，
  host/stage 线程使用 CPU 0，不能把 28 个逻辑线程都算作 expert 并行资源。
- RAM 可用约 6.53 GiB，engine RSS 约 54.86 GB，swap 基本未使用；起第二实例有 OOM 风险。
- 两张卡 requested/current/default limit 均为 250 W，max limit 才是 280 W；
  旧的 280 W tuning 记录不能作为当前性能基线。
- 空闲时 `nvidia-smi` 显示 Gen1 x16 是省电状态，不能据此判断请求期间的 PCIe 代数。

保持 26.8 ms 窗口时，200 tok/s 需要约 5.36 accepted token/window。若只有
3.5 token/window，则窗口必须降到约 17.5 ms。接受长度和窗口耗时必须分开优化。

### 2.2 当前代码与运行路线

- 双卡生产路线是 `--layer-split` pipeline parallel，不是 tensor parallel。
- 仓库没有现成 TP all-reduce 执行器。
- DFlash2 已实现，但当前不允许 `multi_gpu`、`--batch`、`--layer_split` 或
  `--peer-device`。
- 当前环境启用 `STRATA_BATCH_SELF_COMMIT`，但没有 `STRATA_BATCH_MTP` 和
  `STRATA_PIPED_MTP`，代码又禁止 self-commit 与 pipelined MTP 组合。
- 13 条零草稿生产请求全部落在重叠窗口，但 `8465e3cb04d2` 在并发中仍报告 9,157 个草稿，
  邻居 `bdbb1d76fb3b` 只有 42.1 tok/s。因此草稿关闭是强相关现象，不是已证明的唯一原因。
- 修掉互斥只恢复 MTP，不会自动带来 3 倍 decode。
- `batch-groups` 是 PP 流水组，不是连续批处理调度器。
- `generate.cpp` 同时承担入口、资源预算、执行规划、slot、parking 和能力门，导致同一能力
  在 solo、batch、PP 中语义不同。
- 当前 profile 记录 17,248 个专家槽；模型共有 24,576 个 experts，每 token active 10。
  未驻留专家会进入 CPU 计算、pinned host arena、page cache 和 PCIe 路径。

### 2.3 已有但未进入主计划的路线

`--peer-device` 已实现第二张卡的专家 tier：

- CUDA0 保留 dense、KV、state、draft 和主专家 cache。
- peer 卡保存另一组专家，并在卡上计算自己的 decode 与 prompt rows。
- 激活和结果走 NVLink/P2P，失败时回退 host route。
- 它与 `--layer-split`、`--expert-cache-device1..3` 互斥。
- 当前不兼容 DFlash2、session file 和 pipeline windows。

`--expert-cache-device1..3` 与 `--remote-expert-opt` 是另一条 helper cache 路线，
通过 pinned host buffer 汇总结果。它和 peer 路线不是同一机制，不能混写成一个“第二卡优化”。

### 2.4 外部参照可以借鉴什么

| 项目 | 可借鉴 | 不能直接假定 |
| --- | --- | --- |
| vLLM 2080Ti fork | DFlash2/7、TP2、async draft 的实测参考 | 27B NVFP4 不是 176B Flash-Next IQ3_XXS；prefill 也只有约 1.3K tok/s |
| SGLang | waiting/running、chunked prefill、decode 让路、统一 radix cache | 不替换 Strata 引擎；只吸收算法和状态机 |
| llama.cpp | layer/row/tensor split、专家 offload 语义 | 不替换现有专家 cache 和量化路径 |
| NCCL/RCCL | TP/EP 的标准集合通信和拓扑感知 | sm_75、双卡 NV2 上必须先微基准，不能假定线性加速 |

[vLLM 2080Ti Definitive](https://github.com/weicj/vLLM-2080Ti-Definitive/tree/93c9013e375b1ecbf2da88179d2a31175a0d5c99)
的 DFlash2 220.84 tok/s 是重要上限参考，不是 Strata 目标已经可达的证明。

### 2.5 当前控制链与重启边界

- 当前生产实例不是 `strata.service` 管理的实例。server PID 340994 的 PPID 为 1，
  engine PID 341001，两个进程都在 `session-2144.scope`。
- `strata.service` 为 enabled 但 inactive/dead，`NRestarts=23`，unit 指向另一棵
  `strata-0.2.7` 源码树和另一份配置，与当前 freeze 实例不一致。
- 登录 session 2144 已处于 `closing`，`Linger=no`；当前实例的跨会话生命周期保证未验证。
- 当前没有已验证的唯一 owner、last-known-good、health/smoke 门禁或自动回滚演练。
- 直接执行旧 service restart 可能切到不同代码和配置。当前会话经过该 Strata，因此没有
  LKG 和回滚证据前禁止重启生产。

---

## 3. 目标运行时架构

### 3.1 六个边界

1. **协议与准入层**：HTTP、鉴权、取消、流式写回、容量拒绝和错误语义。
2. **请求状态与调度层**：waiting/running、token budget、prefill chunk、公平和抢占。
3. **执行计划层**：每个窗口的 row、draft、verify、commit、sampling 和降级原因。
4. **拓扑与放置层**：PP、peer、helper、TP、EP、host expert 和 stage handoff。
5. **内存与状态层**：VRAM 池、host arena、full KV、GDN、QSA、draft/tap 和会话快照。
6. **观测与生命周期层**：请求账本、硬件采样、实验探针、owner supervisor 和回滚。

边界之间只传显式状态和不可变 capability profile，不再靠散落的全局环境变量互相猜测。

### 3.2 统一请求状态机

```text
WAITING
  -> ADMITTED
  -> PREFILLING
  -> DECODING
  -> PARKED
  -> RESUMING
  -> FINISHED
  -> FAILED
```

每个状态必须声明：

- 已提交 token、位置和稳定的 request/session ID。
- 持有的 full KV、GDN、QSA、draft/tap 和 host/peer 组件。
- 是否可抢占、可取消、可恢复。
- 恢复所需的组件版本、hash 和最小 checkpoint。
- 失败时客户端可见错误、是否允许重试、从哪里重试。

### 3.3 运行时不变式

- solo 与并发使用同一状态机，不维护三套能力语义。
- 任何降级必须输出一次结构化原因；禁止静默把 DFlash2 换成 MTP、把 P2P 换成 host 或把
  batch 路径换成 solo 而不留证据。
- cache 命中必须按组件判断；KV 命中不能证明 GDN/QSA/draft 可复用。
- 每个窗口先形成物理预算，再选择机制，不允许机制先选完再补预算。
- 观测热路径不得为每个 token 执行高开销 JSON、进程查询或 GPU 同步。

---

## 4. 六本性能账

### 4.1 请求时间账

```text
T_request =
  T_accept + T_auth + T_parse + T_tokenize
  + T_queue + T_admit
  + sum(T_prefill_chunk)
  + sum(T_decode_window)
  + sum(T_resume)
  + T_structured_parse + T_tool_parse
  + T_detokenize + T_sse + T_finalize
```

每个请求至少输出 queue、prefill、decode、resume、output、finish 六段时间。任何阶段
超过总时长 10% 或尾延迟 p99 时，必须进入对应账本。

### 4.2 Decode 物理 roofline

单路延迟和聚合吞吐不是同一个公式：

```text
单路窗口延迟 >= sum(每个 PP stage 的串行延迟) - 可证明的跨窗口 overlap
多路聚合吞吐 <= accepted tokens / 最慢 pipeline stage 的服务能力
```

G1 在机制实验前必须计算以下每 token 或每 verifier window 的成本：

- dense 权重读取字节 / HBM 带宽。
- active experts 的 GPU/peer 命中、CPU 计算和 host/PCIe 读取。
- full attention 的 KV 读取量，以及 QSA 索引和查询成本。
- GDN recurrent state 读写。
- MTP、DFlash2 selector、draft head 和 drafter KV/ring。
- 每层 kernel launch、图重放、同步和 sampler 固定成本。
- PP handoff 的字节、次数和有效 NVLink 延迟。
- TP all-reduce/all-gather 的字节、延迟和同步。

最低物理检查：

```text
required_tokens_per_window = 200 * measured_window_seconds
```

若已验证的 `T_floor` 和最大可验证 token/window 无法满足该不等式，G1 必须停止该拓扑。
增加 batch、并发或 CPU 线程不能缩短单路串行关键路径，因此不能作为 D1 的替代答案。

### 4.3 Host expert 四层账

专家路径必须分别记录，不能用“专家命中率”一个百分比概括：

| 层级 | 必须记录 |
| --- | --- |
| CUDA0 驻留 | slot 数、命中次数、HBM 读取字节、替换、抖动 |
| CUDA1 peer/helper | peer 命中、P2P/PCIe 选择、激活和结果字节、等待时间、远端计算时间 |
| CPU expert | active entries、核数、NUMA、内存通道、量化/反量化、每层 CPU ms |
| 存储与 page cache | mmap/page fault、readahead、NVMe 字节和延迟、cache hit、重读 |

每个 decode/prefill 窗口都要回答：

- 专家总请求数、GPU0 命中数、peer 命中数、CPU 计算数和 I/O miss 数。
- host-to-device 与 device-to-host 字节，PCIe 实测带宽和等待时间。
- CPU 专家是否与 tokenize、detokenize、telemetry 或另一路会话争抢核心。
- 专家替换是否改变了输出质量或仅改变求和顺序。

### 4.4 多路与公平账

多路不能只看聚合 tok/s。每个活跃请求必须记录：

- admission、slot 或 continuous batch 的身份。
- 每轮分配到的 row/token budget。
- 自己的 TTFT、decode p50、最大 token gap 和 finish reason。
- 邻居开始、结束、取消、长 prefill 或专家 miss 时，本路速度的变化。
- 是否被 preemption、重新预填、重新计算 recurrent state 或降级到另一路径。

公平性失败的模式包括：固定 slot 被长请求占住、PP group 被整组等待、prefill 长块
阻塞 decode、CPU expert 池只有一个锁、SSE 写回阻塞推理线程。

### 4.5 缓存与冷状态账

缓存键至少绑定：

- 模型、量化、pack 和 expert profile hash。
- engine/runtime 版本、CUDA/HIP、kernel 布局。
- KV/GDN/QSA 精度、布局、RoPE 和 context 配置。
- draft 类型、tap 层、DFlash2 pack 和 drafter ring。
- 最后一个已提交 token 和组件完整性。

失效原因必须结构化分类：

| 类别 | 示例 |
| --- | --- |
| 身份变化 | 模型、engine、量化、RoPE、context 改变 |
| token 失配 | 客户端重写历史、context compaction、checkpoint 太浅 |
| 组件缺失 | full KV 命中但 GDN/QSA 未命中 |
| draft 失配 | DFlash2 ring 缺行、采样参数触发 MTP 回退 |
| 资源淘汰 | parked、page、peer cache、host arena 被回收 |
| 请求形状 | 工具结果增长、schema 改变、多 subagent、取消后重试 |
| 运行故障 | engine restart、Xid、PCIe/NVLink error、OOM |

### 4.6 冷启动 I/O 账

一次 cold start 至少拆成：

1. 进程和 owner runtime 启动。
2. 模型文件 open、mmap、readahead 和 hash。
3. dense、专家、MTP/DFlash2 和 profile 读取。
4. pinned host arena、page cache 和 NUMA 放置。
5. CUDA context、kernel module、cuBLAS/cuDNN 首次加载。
6. graph capture、expert cache 定容和预热。
7. server ready、health 和 smoke。
8. 可选 session file 恢复、状态校验和首 token。

当前模型约 71 GB、专家约 43 GB。必须区分 NVMe 顺序读取、page cache 命中、并行加载、
hash 校验、pin 内存、GPU reset/Xid 和 NVLink replay。冷启动不能只报一个“加载秒数”。

### 4.7 服务端与客户端输出账

engine decode 200 tok/s 不代表 Codex 感受到 200 tok/s。服务端必须记录：

- HTTP accept、body read、auth、JSON parse。
- tokenize、admission、engine token、detokenize。
- structured output 和 tool parser 是否缓冲 token。
- SSE delta 组装、JSON 编码、socket flush 和客户端 ACK/断连。
- Python GIL、日志锁、metrics 锁和 API monitor 是否进入关键路径。
- stream idle timeout、取消、retry 和重试幂等语义。

在每个阶段记录时间戳后，才能判断瓶颈是 engine、控制面还是输出封装。

### 4.8 Host OS、PCIe 与运行环境账

CPU expert、P2P 和尾延迟会被系统状态直接改变。G0 必须冻结并在实验前后复核：

- CPU governor、C-state、频率、SMT、isolated/reserved CPUs 和 IRQ affinity。
- process affinity、priority、`mlock`、open-file/memlock limit 和 swap policy。
- transparent huge page、显式 huge page、pinned arena 大小、页大小和内存碎片。
- NUMA policy、内存通道、CPU 到 GPU 的实际 PCIe root complex。
- IOMMU、ACS、P2P disable、`CUDA_DEVICE_ORDER`、`CUDA_VISIBLE_DEVICES` 和驱动 P2P 状态。
- NVIDIA persistence mode、GPU clock/power limit、温度、功耗和是否发生降频。
- NVMe scheduler、readahead、filesystem mount、page cache 初态和可用空间。
- 是否有容器、cgroup、其他服务或监控器限制 CPU、内存、GPU 或 I/O。

性能实验默认使用真实生产设置。若为归因临时固定 CPU/GPU clock 或隔离核心，必须单独标注，
那些数字不能直接作为产品验收值。

### 4.9 资源准入与隔离账

VRAM、host RAM、pinned arena、peer cache、session file 和 parked snapshot 必须有独立上限：

- 每池独立预算，禁止共享池隐藏重复预留。
- admission 前计算最坏新增 KV/GDN/QSA、专家 staging、draft 和输出缓冲。
- 显存碎片、保留区、CUDA context 和图捕获预留进入预算。
- long prefill、六路并发、取消恢复和 session save 都不得把整机推入 swap 或 OOM。
- 当前是单用户专用服务器，不设计多租户隔离；KV/GDN/QSA/draft 缓存仍必须按模型、
  session 和组件身份隔离，避免不同会话因 hash 碰撞或状态错位而错误复用。

---

## 5. 拓扑与机制候选

### 5.1 候选矩阵

| 候选 | 当前状态 | 主要收益 | 主要风险 |
| --- | --- | --- | --- |
| PP + MTP | 生产路线 | 容量扩展，已有实现 | 单路仍串行，并发时 MTP 可能关闭 |
| PP + DFlash2 | 未开放 | 更长 speculative window | multi-GPU/batch/采样回退尚未解决 |
| 单卡主模型 + peer experts | 已实现，实验性 | 第二卡计算自己的专家，利用 P2P | 与 PP/helper/DFlash2/session file 互斥 |
| 主卡 + helper expert caches | 已实现，实验性 | 提高专家驻留 | host 汇总、pipeline windows 关闭、求和顺序变化 |
| peer experts + DFlash2 | 未开放 | 同时增加专家容量和 draft | 当前能力门直接关闭，需证明关键路径 |
| TP2 dense + MoE | 未实现 | 单层并行、降低单卡权重读取 | 需要 NCCL/自定义 all-reduce，NV2 通信和量化切分未知 |
| dense TP2 + expert EP2 | 未实现 | dense 并行、专家分片 | 两套通信和放置同时存在，调试面最大 |
| 外部运行时承载 Flash-Next | 参考路线 | 可复用成熟 scheduler/TP | 精确模型、sm_75、22 GB 和质量兼容性未证 |

### 5.2 拓扑裁决规则

- PP 不能当成 TP；它不会自动降低单层权重读取时间。
- peer 和 helper 不是同一路线；P2P、host route、CPU expert 必须分别计量。
- TP2 必须优先使用成熟集合通信库或经过验证的原语，禁止手写完整 all-reduce 栈。
- 只有 stage 通信账、显存账、专家账和窗口 roofline 同时成立，拓扑才能进入原型。
- 拓扑切换必须保留同一模型、量化、上下文和输出质量门。

### 5.3 Drafter 裁决规则

G2 必须分别评估：

- sampling-aware DFlash2，包括 proposal distribution 和 rejection sampling。
- coupled MTP 或现有 `STRATA_SPEC_PROB` 路线。
- DFlash2 greedy 专项路线，仅作为机制上限。
- 多槽共享 drafter 权重、独立 tap/ring 和异步 launch。
- PP 最后 stage 与第一 stage 放置 drafter 的关键路径差异。

只让 greedy 达到 200、而产品 sampling 仍回退 MTP，不算完成目标。

---

## 6. 观测、探针与瓶颈判决

### 6.1 四层观测

| 层级 | 何时使用 | 工具 | 运行约束 |
| --- | --- | --- | --- |
| P0 常驻 | 默认，真实会话 | `/health`、`/metrics`、`/v1/telemetry`、service/engine log、低频 NVML、NVLink CRC | 不改变请求路径；采样成本必须实测 |
| P1 请求账本 | 每个请求和实验 | 结构化 request/session span、PSTAT/BSTAT、S2 trace | 原样记录 prompt/answer 和统计；保留长度、hash 和关联 ID |
| P2 系统轻采样 | 受控实验 | `pidstat`、`mpstat`、`vmstat`、`iostat`、smaps、numastat、NVML 1 Hz | 先运行 `probe-instrument-cost.py` 证明低于噪声线 |
| P3 深探针 | 已定位到短窗口 | Nsight GPU Metrics、Nsight Systems | 只采 5-30 秒目标阶段，不长期附加现役 engine |
| P4 内核探针 | 已锁定一个内核 | NCU、`ncu-exec.py`、perf | 需要停服或隔离实例；逐内核结果不能冒充端到端 throughput |

现有入口优先复用：

- `probe-version-stamp.py`
- `four-questions.py`
- `probe-gpu-utilization.py`
- `probe-instrument-cost.py`
- `probe_slot_admission.py`
- `vram-perf-sample.sh`
- `nvlink-crc-sample.sh`
- `metrics-peek.py`

### 6.2 字段矩阵

G0 必须把每个信号标成 `direct`、`infer` 或 `missing`，不能把推断写成观测事实。

| 信号 | 当前来源 | 可信边界 | G0 要确认 |
| --- | --- | --- | --- |
| 进程、argv、cwd、hash | `/proc/<pid>`、version stamp | 证明跑的是哪份代码，不证明性能 | owner/server/engine 三层 PID 和父子关系 |
| 请求 ID、slot、queue、prompt/decode 时间 | service/engine log、metrics | 字段存在不等于可关联 | 同一请求能否在两侧使用同一稳定 ID |
| request/session/turn span | 应用日志、S2 trace | 可能不完整 | 缺失字段和采样是否改变热路径 |
| GPU SM Active/Issue/Tensor/warps/DRAM | Nsight Metrics | `nvidia-smi utilization.gpu` 不是 Tensor 利用率 | 计数器可用性和双通道一致性 |
| VRAM、温度、功耗、时钟 | NVML、telemetry | 显存余量不等于专家命中 | 每池独立 VRAM 和 free budget |
| NVLink CRC/Replay/Recovery | NVLink query、sample script | 链路健康不等于有效吞吐 | link、P2P copy 和 handoff 微基准 |
| PCIe copy bytes/ms | engine trace、Nsight | 与 host route 和 helper 路线分开 | root complex、lane 和实际 P2P 选择 |
| CPU/RSS/NUMA/page fault/swap | `/proc`、pidstat、numastat | RSS 不等于 GPU 驻留 | CPU expert 核心、内存通道和 pinned arena |
| NVMe/readahead/page cache | iostat、pidstat、smaps | 热 cache 不能解释冷加载 | 冷/热启动定义和实际读取字节 |
| application draft/cache counters | PSTAT/BSTAT、S2、metrics | 计数器可能只在部分路径更新 | solo、batch、PP、fallback 的字段一致性 |
| 客户端 SSE cadence | 客户端探针、服务端 span | chunk 数不等于 token 数 | 主 API 的 token/delta 映射 |

G0 的目标不是“日志看起来很全”，而是产出字段矩阵：

```text
已直接可观测
只能从组合信号推断
当前缺失，需要后续加埋点
字段存在但不能跨进程/时钟对齐
```

### 6.3 时钟与请求关联契约

实验记录至少包含：

- host boot ID。
- owner ID、instance ID、server PID、engine PID、engine launch ID。
- request/session/turn 稳定 ID。
- monotonic timestamp 和墙钟 timestamp。
- profile revision、config hash、model hash、draft hash。
- engine log offset、server log offset 和采集器实例 ID。
- Nsight capture start、GPU clock domain 和对应 request ID。

G0 必须实际证明这些字段能对上。没有对齐前，不能用“日志里同一时间附近”推断因果。

### 6.4 症状到第一探针

| 症状 | 先看 | 需要区分 | 禁止结论 |
| --- | --- | --- | --- |
| TTFT 高、稳态 decode 正常 | queue、admit、tokenize、prefill chunk、首 token | 准入、缓存恢复、专家 staging、输出封装 | “GPU 慢” |
| 单路 decode 低 | token/window、window ms、`draft_n`、fallback 原因 | DFlash2/MTP、batch rows、stage bubble、sampler | “MTP 没开”是唯一原因 |
| NVML 显示 100% 但 SM Issue 低 | SM Active、Issue、Tensor、warps、DRAM | 等待、同步、依赖链、低占用 | “算力已经打满” |
| SM Issue 与 DRAM 同时高 | per-kernel DRAM 与 bytes/token | 权重或 KV 带宽饱和 | “需要更多并发” |
| SM Issue 与 DRAM 都低 | warp stall、handoff、copy wait、launch | 依赖、锁、host sync、PP bubble | “可以继续融合 kernel” |
| PCIe copy wait 高 | 专家 miss、staging bytes、P2P 回退 | CPU expert、host arena、PCIe 饱和 | “NVLink 能解决” |
| 两路后每路骤降 | row budget、group、draft、邻居变化 | 批路径回退、公平性、CPU 争抢 | “聚合没降就正常” |
| 长会话重复前缀仍慢 | KV/GDN/QSA/draft 组件命中 | 部分命中、recurrent 重算、checkpoint 太浅 | “prefix cache 命中就应近零” |
| 200K 冷读拖慢 decode | prefill chunk、专家 staging、PCIe/CPU | 抢占、锁、输出 GIL、page cache | “并发本来就会这样” |
| 取消后邻居仍慢 | cancellation、drain、staged read、copy engine | 工作未停、槽位泄漏、重试 | “客户端断开就立即停止” |
| engine 快但 Codex 慢 | tokenize、parser、SSE、client cadence | tool parser 缓冲、GIL、timeout、retry | “服务端已经 200” |

### 6.5 瓶颈判决顺序

所有实验遵循：

```text
环境与身份一致
  -> workload 和 sampling 可比
  -> 先拆请求总时间
  -> 找主导阶段
  -> 建该阶段物理预算
  -> 用最低成本探针排除环境
  -> 用深探针区分计算、带宽、延迟、同步和调度
  -> 一次只改一个机制
  -> A-B-A 验证性能、尾延迟、质量和回滚
```

每一步都必须写“该探针能回答什么、不能回答什么、失败时保留哪些证据”。

### 6.6 证据保留与噪声边界

- 当前服务器没有其他使用者，所有会话只服务本机开发；本计划不设计脱敏流程。
- prompt、answer、argv、environment、request body、SSE chunk 和原始 trace 可以原样保留，
  以便复现请求关联、采样和输出链路问题。
- 原始账本不删除任何请求；性能统计必须经过 cohort 分类，只纳入 `production`。
- G0 的 97 条原始请求中，剔除 2 条启动 warmup、3 条短 smoke、6 条固定 400-token 压测，
  生产 cohort 为 86 条；剔除 ID 和原因必须写入证据文件并可由脚本复算。
- 所有后续实验在请求开始和结束写入 `cohort=production|warmup|smoke|synthetic|probe|cancel|failure`
  标记；没有标记的请求不得进入性能比较。
- 失败、取消和超时保留在可靠性账本，与成功请求的 decode 分布分开报告。
- engine verify、batch window 和 prompt summary 日志行保留作机制证据，但不计作 HTTP 请求。

---

## 7. 生命周期、唯一控制者与重启契约

### 7.1 唯一控制者

生产只能有一个生命周期裁决者：

```text
systemd/启动器
  -> strata runtime owner
  -> server child
  -> engine child
```

- systemd 或等价启动器只负责启动 owner，不直接重启 server/engine。
- `strata runtime` owner 持有 profile、candidate、last-known-good、argv、cwd、hash 和回滚点。
- server 只拥有同一个 instance 内的 engine 子进程恢复。
- 任何手工 kill/restart 都视为违反生产控制契约。

三个恢复层必须职责清晰：

| 层级 | 责任 | 允许动作 | 禁止动作 |
| --- | --- | --- | --- |
| L0 request | 单请求失败/断流 | 明确错误、客户端安全重试 | 静默重放非幂等工具调用 |
| L1 engine | 同 instance 内 engine crash/stuck | 有界重启、清理 orphan slot、记录原因 | 配置/版本错误时无限循环重启 |
| L2 owner | server 或候选失败 | drain、重启、health/smoke、自动回滚 | 与 server 内部 watchdog 同时争夺控制权 |
| L3 host | 主机或 systemd 故障 | 启动 owner、审计恢复 | 绕过 owner 直接拉起旧 engine |

### 7.2 Session 与 drain 语义

- drain 开始后拒绝新推理准入，现有请求在时限内完成或明确取消。
- 流式连接可收到明确 terminal error；不得只断开而不写 finish/error 语义。
- 工具调用重试必须携带幂等边界；服务端不能假定客户端能安全重放副作用。
- 同版本 session file 可以在明确成功后恢复；parked snapshot 不跨 restart。
- session file 与 PP/peer/batch 的兼容性解决前，不宣称“当前多路生产会话可随时重启恢复”。

### 7.3 Restart 事务

```text
restart plan
  -> identity/config/hash 复核
  -> 保存可回滚 launch bytes
  -> drain
  -> stop old instance
  -> start candidate
  -> process ready
  -> /health
  -> engine smoke
  -> API smoke
  -> optional session restore
  -> mark ready
  -> retire rollback snapshot
```

失败时：

```text
stop candidate
  -> restore last-known-good launch bytes
  -> start LKG
  -> health + smoke
  -> keep service address
  -> emit audit record
```

候选和 LKG 不同时加载，因此不要求零停机切换。重启窗口内的新连接可以明确失败，
但恢复后必须回到同一地址并满足 R-rollback。若将来要求零中断，必须另立 front
listener/socket activation 项目，不能把 owner restart 说成透明续流。

### 7.4 Smoke 最小集合

- `/health` 报告 loaded 且模型身份正确。
- 固定短 prompt 生成至少一个 token。
- stream 模式收到合法 delta 和 finish。
- 主 Codex API 完成一次合法请求。
- tool call 或 structured output 至少有一条解析路径。
- 失败时不影响 LKG 启动和会话文件读取。

---

## 8. 架构门禁

### G0：零停机证据冻结

**目的:** 在任何性能实验、深探针或重启前，确认测的是哪台机器、哪个进程、哪份模型、
哪套配置、哪种真实 sampling，以及当前观测还能回答什么。

**输出:** `docs/work/evidence/flash-next-runtime-g0-20261030.json`

- [x] **G0-0 只读预检**

运行 `four-questions.py` 和 `probe-version-stamp.py`。确认没有残留构建或探针、服务已加载、
当前 owner/server/engine PID 和进程树可识别。记录当前 slot、活跃请求、日志位置和
当前会话是否通过生产 Strata。

首轮结果：`four-questions.py` residual 为空，版本戳无问题，health 20/20；当前实例
server/engine 进程树和 session scope 已记录。

- [x] **G0-1 冻结硬件与内核**

记录 GPU、显存、compute capability、功耗、温度、NVLink、P2P、PCIe、CPU、socket、
NUMA、逻辑 CPU、RAM、swap、NVMe、文件系统、kernel、driver 和 CUDA。至少保存：

```text
nvidia-smi -q
nvidia-smi topo -m
nvidia-smi nvlink -s
nvidia-smi nvlink -c
nvidia-smi nvlink -e
lscpu
numactl -H
cat /proc/meminfo
lsblk -o NAME,MODEL,SIZE,ROTA,TYPE,MOUNTPOINTS
df -h
uname -a
cat /proc/cmdline
lscpu -e
cpupower frequency-info
cat /sys/kernel/mm/transparent_hugepage/enabled
```

文档中 Xeon E5-2680 v4、28 线程、单 NUMA 与 E5-2673 v4、80 逻辑 CPU 的冲突以本次实测为准。
同时记录 cgroup、process affinity、IRQ affinity、NUMA policy、memlock/swap、IOMMU/ACS、
NVIDIA persistence、clock/power limit 和当前温度。

首轮结果：双 RTX 2080 Ti、NV2/P2P、250 W 实际限制、E5-2680 v4 单 NUMA、
CPU expert 绑定 1-13 和 6.53 GiB available RAM 已写入证据。负载态 SM/Tensor/DRAM
利用率仍未采集。

- [x] **G0-2 冻结运行时与控制链**

记录 server/engine 可执行路径、argv、cwd、PID、端口、profile revision、config hash、
模型 pack hash、expert profile hash、draft hash、受控 environment、`/health`、
`/metrics` 和启动日志。确认：

- 当前是 PP、peer、helper 还是其他模式。
- 可见设备号和物理设备号。
- systemd、owner runtime、server 内部 restart 各自是否启用。
- `/proc/<pid>/environ` 的原样内容；当前专用服务器不执行脱敏。
- 会话缓存按模型、session 和 request 身份的隔离方式，以及前缀复用的组件一致性。

首轮结果：已冻结 server/engine argv、cwd、可执行文件 hash、配置 hash、模型 pack
组件 hash、draft hash、expert profile hash、受控环境、PP layer split 和 systemd 漂移。
当前实例没有 LKG、health/smoke gate 或已验证回滚。

- [x] **G0-3 回放真实会话与字段矩阵**

只读分析现有 engine/service 日志、`/metrics` 请求环和客户端可见记录，按请求形状分组：

```text
短对话
长上下文首轮
长上下文续轮
长输出
工具调用
两路邻居
取消或断连
失败和重试
```

每组记录样本数、prompt/read/reused token、TTFT、prompt/decode 时间、`draft_n`、
expert/cache 命中、finish reason、异常值和无法归属的字段。同时冻结：

- Codex 实际使用的 API。
- 实际 sampling 分布。
- DFlash2 可用率和 MTP 回退原因。
- request ID、slot、queue 和 engine 窗口能否关联。
- service/engine/Nsight 时钟能否对齐。
- 当前 session file 与 PP/peer/batch 的实际兼容性。

首轮结果：97 条原始请求已分类为 11 条噪声和 86 条生产请求；请求形状、时间账、
两路重叠、零草稿相关性、冷预填、专家缓存、PCIe offload 和字段矩阵已冻结。97 个
`/metrics` request ID 在 server/engine 文本日志中均无法关联；逐请求 API 路由、实际
sampling、SSE cadence、batch/stage timeline 和请求期间硬件序列仍缺失。

**G0 当前 Gate: CLOSED。** G0-0 至 G0-3 的零停机首轮冻结已完成，但请求关联和关键
运行时字段仍不满足性能裁决条件。不得因为 checkbox 完成而进入 G0B。

### G0A：观测闭环

**目的:** 在不重启生产、不发送合成生成负载的前提下，把“能否定位瓶颈”补齐到可裁决状态。

- [x] server、engine 和客户端使用同一稳定 request/launch ID。HTTP exchange、request ledger、
  engine 的 `DONE`/`BDONE` 和客户端探针共用 `X-Request-ID`；协议 fake 测试与隔离编译通过。
- [x] 每个请求记录 API 路由、stream 模式、实际 sampling、cohort 和 finish reason。
- [x] 记录 queue、admit、tokenize、prefill、decode、resume、parser、detokenize、SSE 时间。
- [x] 补齐 batch window 成员、row budget、draft/fallback 原因和 PP stage in-flight timeline。
  parser、request ledger 关联和 C++ 输出路径已测试或编译；尚无真实模型的隔离运行样本。
- [x] 在每个请求期间记录 1 Hz GPU、CPU、RAM、NVLink、PCIe 时间序列，并标出采样开销。
  NVLink 字节计数在当前 NVML/驱动/卡组合上明确报告 `nvml_not_supported`，缺失值保持 null。
- [x] 记录客户端首个和最后一个可见 delta，映射服务端 SSE chunk 到 token/delta。
  `tools/server/probe-client-sse.py` 已通过 OpenAI、Responses、Anthropic 三种真实 HTTP SSE
  隔离测试；测量边界是客户端 HTTP 读取，不是应用渲染，也没有伪造应用层 ACK。
- [x] 把噪声分类、剔除 ID、公式和分位数做成可重复运行的校验脚本。
- [x] 冻结当前 launch bytes 和进程状态，形成未来 LKG 的只读快照；不据此宣称已有自动回滚。

**Gate:** request ID 不能跨 server/engine/client 对齐，或 batch/stage timeline 仍缺失时，
继续关闭 G0B。观测补丁先在隔离构建和测试中验证，不直接部署到当前生产实例。

**G0A 执行结果（2026-10-30）：** 实现与隔离验证完成，证据见
`docs/work/evidence/flash-next-runtime-g0-20261030.json`。隔离 C++ 构建、链接和
`strata --help` 成功；`batch_telemetry_test`、`batch_groups_test`、`batch_rows_test`、
`message_boundary_test`（21 checks）和 `controller_test` 通过。Python 按真实套件分组验证：
`serve` 为 `1050 passed, 11 skipped, 10866 subtests passed`；Terminal、server tools 和
G0 审计为 `304 passed, 55 skipped, 93 subtests passed`，黑盒目标固定为不可达的
`127.0.0.1:9`；G0A/探针专项为 `26 passed` 和 `15 passed`。`tools/g0_audit.py audit`
复算 97 条原始请求、11 条噪声和 86 条生产请求，结果 PASS。根目录直接 `pytest -q`
不是有效全仓套件：它会采集人工脚本及缺少 torch/Windows DLL 的测试并产生 11 个
collection errors，因此不能写成“全仓 pytest 通过”。
但新二进制尚未在真实模型上执行一次 request ID/BTRACE 协议演练，也未部署到生产，因此：

- G0A 实现 Gate：PASS（仅限隔离构建和 fake/unit/HTTP 测试）。
- 生产观测 Gate：CLOSED（生产实例仍运行旧代码，不能把设计能力当作现网实测）。
- G0B：不启动。持久 LKG 和新 owner startup fallback 已在隔离 fake-runtime 中实现并通过
  测试，但还没有真实模型、生产 systemd 恢复或会话恢复证据，也没有替代访问。

下一安全检查点是“隔离原生协议演练”：只有在不挤占当前生产显存、不重启生产且能记录完整
`X-Request-ID -> DONE/BDONE -> BTRACE` 链时执行。若硬件条件不成立，就保持 Gate 关闭，
先完成 owner runtime、LKG 和回滚演练，不重复重启生产来换一次不可复核的观测。

### G0A.1 owner runtime 持久 LKG

**目的:** 把“候选失败后重新启动旧 argv”的内存态补救，升级为 owner 进程退出后仍可读取、
校验和审计的 last-known-good。

- [x] 只有 `/v1/telemetry` 身份、launch attestation 和模型 state 检查通过后，才原子写入
  owner-private `<runtime>/<profile>.lkg.json`；配置 bytes、SHA-256、profile revision、
  server argv 和验证 instance 一并记录。
- [x] 成功的候选 restart 在 generation probe 通过后替换 LKG；失败的候选不覆盖旧记录。
- [x] restart 在 drain 前校验 LKG；缺失、损坏、符号链接、摘要不匹配或当前运行实例不一致时
  拒绝停机。
- [x] 候选失败后从持久 LKG 恢复。原 config 仍为同一普通文件时直接复用；被原地修改或
  不可用时从 LKG bytes 创建 mode-0600 私有快照，不覆盖操作者文件。
- [x] 新 owner 在候选 launch 失败、ready 前退出或 generation probe 失败时，从启动前捕获的
  持久 LKG 单次恢复；候选一旦达到 ready，之后的崩溃不进入 startup-only fallback。
- [x] `runtime start --wait 0` 时由 owner 后台完成同一恢复；generation lock 阻止旧 probe
  在新实例启动后写回过期 LKG。
- [x] 回滚结果在 operation journal 中记录 `rollback_lkg_source_sha256`；不回写配置 secret
  或完整 manifest。
- [x] 停止 owner、换一个全新 Python 进程后仍能读取同一 LKG；manifest 的 JSON、大小、
  路径、私有权限、配置摘要和 `--config` 参数都经过零信任校验。
- [x] readiness 在 telemetry 身份和 attestation 通过后显式请求 `/health`，校验
  `status=ok`、`service=strata`、模型身份和 loaded 状态；结果以 `health_verified`
  写入 runtime 和 operation journal。
- [x] LKG 记录 engine 可执行文件路径、大小和 SHA-256，以及 `serve/`、`strata_control/`
  服务代码集合摘要；测试、fixtures、CLI 和 tools 不进入摘要，避免无关变更制造假故障。
  restart plan 同时报告运行中与磁盘候选的 `artifact_id`，restart/startup fallback 在
  drain 或启动前拒绝无法与 LKG 精确匹配的制品。
- [ ] 当前能力是制品身份门禁，不是完整制品回滚。LKG 仍不保存旧 engine、Python 服务
  代码、模型包和动态库，也没有原子 `current` 指针；其中任一路径被覆盖后，系统只能
  明确拒绝回滚，不能自行恢复旧制品。进入代码级自动回滚前必须补不可变 release。
- [ ] 真实模型启动、真实 GPU drain、真实 owner/systemd 重启恢复仍未演练。
- [ ] engine smoke、stream smoke、tool smoke 和 session restore 尚未组合成 G0B 的
  完整验收门。

**隔离结果（2026-10-30）：** fake-runtime 新增覆盖新 owner 候选失败后的持久 LKG 恢复、
无 LKG 时保留原始失败、损坏 LKG 明确拒绝、`wait=0` 后台恢复、CLI journal 记录、
回滚成功后的正常 restart 清除旧标记、旧 generation probe 不得替换 LKG，以及 engine
或 Python 服务代码变化时在 drain 前拒绝假回滚。新增制品用例为
`4 passed, 64 deselected`，完整 `tests/terminal/test_runtime.py` 为
`68 passed, 11 subtests passed`，完整 `tests/terminal` 为
`219 passed, 45 skipped, 97 subtests passed`。此前的 serve lifecycle、lifeline 和
ready-timeout 隔离结果为 `20 passed, 6 subtests passed`。运行过程中还定位并修复了
mock server 的既有 SIGTERM 启动窗口竞态：ready 发布与 serving wait 现在处于同一个
clean-stop 作用域，ready 后立即发送 SIGTERM 不再以 `-2` 退出。

证据见 `docs/work/evidence/flash-next-runtime-lkg-20261030.json`。

**Gate:** 隔离持久 LKG 与 startup fallback：PASS。生产自动回滚：CLOSED。当前实现证明
候选 activation 失败后，新 owner 可以在 fake runtime 中从磁盘 LKG 恢复；这不等价于
零中断切换，也没有证明真实模型、生产 systemd 重启或会话恢复。`/health` 隔离门禁为
PASS，engine 与 Python 服务代码身份门禁也为 PASS，但 LKG 尚不能保存和恢复旧制品，
因此生产自动回滚继续 CLOSED。

### G0B：受控基线与恢复演练

只有在服务空闲、当前会话有替代访问、owner runtime 具备 last-known-good，并且回滚路径
已在隔离环境演练后执行。

- [ ] 冻结单路固定 400-token 基线。
- [ ] 冻结两路并发和互相恢复。
- [ ] 冻结 32K/200K 冷预填。
- [ ] 冻结长工具调用、取消和断连。
- [ ] 记录 P2 探针自身开销。
- [ ] 验证 health、engine smoke、API smoke 和 LKG rollback。
- [ ] 建立第一版 critical-path 账本。

**Gate:** 同一形状基线漂移超过 10% 时停止性能实验，先定位环境。没有自动回滚和会话恢复
证据，不重启生产。

### G1：物理可行性与 Roofline

**目的:** 在改 DFlash2、batch 或 TP 之前，判断 200 tok/s 和 5000 tok/s prefill 在当前
硬件、模型、量化和上下文下是否存在机制上可达的路径。

- [ ] 建立 dense、expert、KV、QSA、GDN 和 drafter 的每 token/window 字节表。
- [ ] 建立 HBM、SM/INT8/Tensor、CPU DRAM、PCIe、NVLink、NVMe 实测上限。
- [ ] 跑 P2P copy、PP handoff、host copy 和 CPU expert 微基准。
- [ ] 计算 solo latency floor 和多路 aggregate floor。
- [ ] 对 4K、32K、200K 分别写 required token/window 和 window budget。
- [ ] 输出“可达、需要何种机制、物理不可达”的结论。

**Gate:** 物理上无法满足 D1 或 P1 的候选不得进入实现。若所有可行候选都不够，停止并要求
目标或硬件重新裁决。

### G2：Drafter 与采样机制

- [ ] 单卡比较 MTP、DFlash2 greedy 和 sampling-aware drafter。
- [ ] 用真实采样参数计算 DFlash2 覆盖率与 MTP 回退率。
- [ ] 验证每槽 drafter 权重共享、tap/ring 隔离和异步 launch。
- [ ] 验证 PP stage 放置和 handoff。
- [ ] 验证 batch 路径的 commit 隔离、取消和恢复。
- [ ] 写出 token parity、sampling 统计和质量门。

**Gate:** 若 DFlash2 在生产 sampling 下无显著收益，停止把它作为 200 tok/s 的唯一依据，
转向其他 drafter、MTP 或拓扑机制。

### G3：拓扑与专家放置

- [ ] 比较 PP + MTP、PP + DFlash2、peer experts、helper caches 和 peer + DFlash2。
- [ ] 决定是否需要 TP2、dense TP2 + expert EP2 或外部运行时原型。
- [ ] 记录每层消息大小、调用次数、有效带宽、延迟和同步点。
- [ ] 记录每池 VRAM、host arena、peer cache 和重复预留。
- [ ] 记录 P2P、host route 和 CPU expert 的实际分流。
- [ ] 裁决 NCCL/RCCL、现有 P2P 原语或外部运行时。

**Gate:** 没有通信、显存、专家和 roofline 账的拓扑不得进入代码。

### G4：调度、缓存与输出链路

- [ ] 设计 waiting/running、token budget、chunked prefill、decode 让路和抢占。
- [ ] 验证每路 p10、最大 gap、取消和邻居影响。
- [ ] 建 full KV、GDN、QSA、draft/tap 的组件化缓存和失效分类。
- [ ] 解决 session file 与 PP/peer/batch 的不兼容。
- [ ] 测量 tokenize、structured output、tool parser、detokenize 和 SSE。
- [ ] 检查 Python GIL、日志锁和 metrics lock 是否进入关键路径。
- [ ] 验证缓存 COW、部分命中、淘汰、写回、恢复和版本 hash。

**Gate:** 聚合吞吐、每路 p10、TTFT、恢复正确性和输出质量必须同时通过。

### G5：整机可行性裁决

必须形成一份决策表，每行至少包含：

```text
候选拓扑
真实 sampling 覆盖率
solo latency floor
多路 aggregate floor
prefill floor
冷会话恢复
显存/host/PCIe/NVLink 预算
质量门
实现风险和工期
```

只有某候选能在误差范围内解释 D1、P32/P200、M2/M6、C50/C200 和 R-rollback，才进入代码级
实施计划。若没有候选成立，停止重构并提交硬件或目标裁决。

---

## 9. 复用边界

重构不等于把所有成熟项目重新实现一遍。复用原则如下：

| 能力 | 优先复用 | Strata 只做什么 |
| --- | --- | --- |
| Request scheduler | SGLang 的队列、chunked prefill、prefill delayer、radix cache 设计 | 在现有 C++/Python 边界内实现最小状态机和预算 |
| Speculative decode | 现有 Strata DFlash2/MTP 库和 vLLM 的 async draft 思路 | 补 sampling-aware、每槽状态、PP/batch 接线 |
| Tensor/expert 通信 | NCCL/RCCL 或经过验证的 P2P 原语 | 量化布局、stage membership、预算和错误回收 |
| Session/cache | Strata session、parked、SGLang unified radix 对照 | 解决 full KV/GDN/QSA/draft 的组件语义 |
| Observability | NVML、Nsight、Prometheus、现有 probes | 统一 request ID、预算账本和判决协议 |
| Lifecycle | 现有 `strata runtime` owner、systemd 启动器 | 补唯一控制契约、health/smoke 和会话恢复门 |

复用前必须回答：

- 是否支持精确模型、量化、sm_75 和 22 GB 显存。
- 是否保留 sampling 和输出质量。
- 通信/调度开销是否已经计入 roofline。
- 失败是否能被 owner 观察和回滚。

---

## 10. 代码边界与参数策略

### 10.1 后续实现边界

G5 通过前只写计划和证据，不写运行时抽象。之后预期按独立切片实施：

| 阶段 | 范围 | 主要验证 |
| --- | --- | --- |
| R1 | 请求状态机、capability profile、统一预算 | 状态转换单测、无 silent fallback |
| R2 | waiting/running、chunked prefill、公平调度 | 两路/六路、取消、邻居最大 gap |
| R3 | 选定拓扑、per-slot drafter、PP/handoff | solo/batch/PP parity、roofline |
| R4 | full KV/GDN/QSA/draft 缓存 | cold/warm restore、hash 和 COW |
| R5 | owner supervisor、smoke、rollback | LKG、drain、恢复和审计 |

每个阶段先写失败测试或基准，再做最小实现，再做局部验证、全局一致性检查和提交。

### 10.2 允许删除的旧分叉

- `STRATA_BATCH_MTP`
- `STRATA_PIPED_MTP`
- `STRATA_BATCH_SELF_COMMIT`
- `STRATA_PIPED_CONCURRENT`
- 作为用户能力选择的 `--batch-groups`
- 关闭 DFlash2、peer、layer split 的临时互斥门，前提是替代机制和测试已存在

只保留资源上限、实验覆盖和硬件事实，不保留“是否存在某项能力”的开关。旧参数兼容解析
只能存在于一个输入适配层，并打印参数名、替代机制和原因。

---

## 11. 质量、安全与生产门

### 11.1 正确性

- greedy token parity。
- 固定 seed 的 sampling 统计对比。
- needle 50% 和 90% 深度。
- 长工具调用、structured output 和断流续读。
- DFlash2/MTP fallback parity。
- full KV、GDN、QSA、draft 组件 hash 和版本一致性。

### 11.2 资源

- 每池 VRAM 独立记账。
- RSS、available memory、swap、page cache 和 IO。
- 专家命中、CPU expert、PCIe、NVLink、GPU 空闲时间。
- 一小时后内存、吞吐和尾延迟不漂移。

### 11.3 生产

- 没有唯一 owner runtime，不切生产。
- 没有 last-known-good，不重启生产。
- 没有 health、engine smoke、API smoke 和回滚演练，不宣称可随时重启。
- 没有 session 兼容性证明，不宣称 in-flight 或多路会话透明恢复。
- 生产性能统计不得包含 warmup、smoke、固定压测、仪器探针或人工合成负载。
- 原始 prompt、answer、trace 和完整 environment 可以直接保留；它们只作为证据，不计入生产分布。

---

## 12. 停止条件

- 硬件指纹或基线无法复现：停止性能实验。
- G1 roofline 证明目标物理不可达：停止实现并要求目标或硬件裁决。
- 真实 sampling 下 drafter 覆盖率不足且 MTP 无剩余空间：停止 DFlash2 单一路线。
- PP 通信/串行瓶颈无法解释目标，而 TP2/EP2 成本不可接受：停止拓扑重构。
- 预填 5000 在可行 chunk/专家布局下仍低于物理上限：提交证据。
- session file 无法与 PP/peer/batch 保证组件一致：禁止开启会话恢复。
- OOM、swap 持续增长、输出质量失败或静默降级：立即回滚。
- 单个候选连续两次没有超过测量噪声：停止该机制，换结构，不重复同形实验。
- 观测字段无法关联或探针成本超过噪声线：先修观测，不做性能裁决。

---

## 13. 立即下一步

G0-0 至 G0-3 的零停机证据冻结已经完成，G0 Gate 继续关闭。下一步只做 G0A 观测闭环：

1. 在代码和测试中定义稳定 request/launch ID，贯穿 server、engine 和客户端。
2. 补齐逐请求 API、sampling、cohort、stage timeline、batch window 和 fallback 原因。
3. 设计请求期间 1 Hz 硬件采样，先测自身开销，再接入隔离构建。
4. 记录客户端 SSE cadence，并与服务端已提交 token 对齐。
5. 把 97 条原始请求到 86 条生产 cohort 的分类写成可重复运行的校验。
6. 为当前 launch bytes、进程和配置建立只读 LKG 快照，但不把快照等同于可回滚。

这些工作先在源码和隔离构建中完成，不发送合成请求、不运行 Nsight/NCU、不加载第二个模型、
不重启当前生产服务。G0A Gate 通过前不进入 G0B/G1，不改生产 default，不删除生产参数，
不启用 peer/DFlash2 新组合，不新增 TP2。
