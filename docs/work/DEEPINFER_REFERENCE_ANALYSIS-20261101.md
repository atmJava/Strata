# DeepInfer 源码分析与 Strata 可借鉴项

日期：2026-11-01

状态：研究文档。本轮只阅读和比较源码，没有修改生产配置、构建引擎或重启服务。

## 0. 结论摘要

DeepSeek 当前公开的主推理框架是
[deepseek-ai/DeepInfer](https://github.com/deepseek-ai/DeepInfer)，Apache-2.0。它不是可替换 Strata 的通用
推理服务，而是面向 `DeepSeek-V4.1-Flash`、NVIDIA SM100 和 Ascend 950 的生产核心子集。

对 Strata 有价值的主要不是它的模型实现或大型集群组件，而是以下几个调度思想：

1. 把 batch 组成对数值结果的影响作为一等正确性问题处理。
2. 按实际工作量而不是只按请求数或空 slot 数分配请求。
3. 用预算、quota 和老化机制限制长 prefill 对 decode 的阻塞。
4. 在确有必要时，把 CPU 调度和后处理移出 GPU 关键路径。
5. 在 batch 内按全局 token budget 分配 MTP 验证长度。

当前 Strata 已经有相当多同类机制：`--batch 6 --batch-groups 2`、prompt chunk、BYIELD、
`STRATA_ADMIT_SHORT_FIRST`、prefill planner、adaptive expert cache 和 cost-aware `DraftPolicy`。
因此不能把 DeepInfer 的调度器整体移植过来，也不应重新实现一套并行服务。

真正应优先补的是让“改变 slot、group 或 batch 组成”不改变模型输出。Strata 已有证据表明，仅把两个
pipeline group 改成一个就会让固定 greedy 文本不同；在这个前提解决之前，动态组路由和会话迁移都会引入
用户可见的非确定性。

## 1. 阅读范围与证据等级

分析快照：

```text
repository  deepseek-ai/DeepInfer
commit      553741557a57df44a46fb3ab72b6aafeaf945337
commit date 2026-10-25T07:59:27-04:00
license     Apache-2.0
files       348 tracked files
clone       shallow clone in /tmp/deepinfer-analysis.HZxQz0/DeepInfer
```

GitHub 元数据在 2026-11-01 约为 24,684 stars、2,990 forks、229 open issues。仓库创建于 2026-10-16，
公开 API 显示 5 个 commit，没有 release 或 tag，Python package version 为 `0.1.0`。

本文用以下标签区分证据：

- **源码事实**：直接来自开源树中的代码。
- **官方说明**：来自 README 或部署 Q&A，但相应生产组件或测量环境未公开。
- **Strata 证据**：来自本仓库已有测试结果、计划或文档。
- **建议**：尚未在 2 x RTX 2080 Ti 上实现或测量。

本轮没有下载模型权重，也没有构建 DeepInfer。目标机器是 CUDA compute capability 7.5；DeepInfer
公开支持的是 SM100 和 Ascend 950，因此本机不能形成有效的构建或性能证据。

## 2. 开源边界

[README](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/README.md)
说明这是“offline inference with the core features needed for serving at scale”。

| 能力 | 开源树中的状态 | 对本文的意义 |
| --- | --- | --- |
| V4.1 Flash 模型运行时 | 有 | 与 Strata 的 Qwen 模型和量化格式不兼容 |
| C++/Python batch scheduler | 有 | 可研究公平性、抢占和异步调度 |
| Chunked prefill | 有 | 可借鉴预算和防饿死思想 |
| Batch-invariant inference | 有，NVIDIA | 最值得研究的正确性机制 |
| Deterministic sampling | 有 | 需要固定 `seq_seed`，并关闭变长 MTP |
| EPLB | 有 | 只适用于 EP/MoE 多卡布局，不直接适用 layer split |
| Balanced CP | 有 | 依赖 DeepEP all-gather，不适用于当前两卡 pipeline |
| DSpark/MTP 变长验证 | 有 | 调度数学可研究，confidence head 属于 V4.1 模型 |
| Structured output | 有 | 与当前 GPU 利用率目标关系较低 |
| 在线 API serving wrapper | 无 | 不能把开源仓库直接当成在线服务 |
| 完整 KV cache 系统 | 无 | 不能复现官方跨机器 cache hit 和路由 |
| Prefill/Decode replica 负载均衡 | 无完整实现 | Q&A 中的算法不能当作现成代码 |
| VE/Prefill/Decode 完整分离 | 只有部分引擎功能 | 不适合当前单机双卡 |

边界在源码中也有明确提示：

- [README 的未包含项](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/README.md#L38-L42)。
- [LLMEngine 的在线服务说明](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/deepinfer/engine/llm_engine.py#L140-L141)。
- [官方部署 Q&A 的未开源说明](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/doc/inference_deployment_qa.md#L21-L21)。

一个容易误判的点是：Q&A 对 Prefill Load Balancer 给出了四档上下文分桶、compute quota 和二次成本模型，
但开源 scheduler 只实现了 token 上限、chunk checkpoint、block/state 准入、抢占和步型投票。完整的
跨 replica 分桶与成本模型属于未开源基础设施，不能从当前树中直接复制。

## 3. 硬件与部署规模

源码和 README 的硬门槛：

- NVIDIA SM100 或 Huawei Ascend 950。
- Linux、GCC 13、Python 3.12。
- NVIDIA 版本要求 CUDA 13、`torch==2.13.0+cu132` 和 `flash-attn-4`。
- 还包含 DeepGEMM、FlashMLA、DeepEP、TileKernels、DeepSelect 和大量 TileLang kernel。

当前 Strata 目标是 compute capability 7.5 的 2 x RTX 2080 Ti，不在支持列表中。源码可以阅读，模型
checkpoint 和优化 kernel 都不能在目标机上沿用。

官方说明的生产形态是：

- Prefill 使用 EP2-8 / DP2-8。
- Decode 使用 Wide-EP64-128 / DP64-128。
- Attention 使用 DP，MoE 使用 EP。
- 最小高效部署单元约为 64 个 Prefill GPU 加 64 个 Decode GPU。
- KV cache 位于独立 3FS，TTL 为 agent 约 3 天、chatbot 约 7 天。

这些数字来自
[部署 Q&A](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/doc/inference_deployment_qa.md#L41-L67)
和 [KV 章节](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/doc/inference_deployment_qa.md#L129-L177)，
不是本机测量。它们说明 DeepInfer 的很多收益来自规模和拓扑，不能按比例缩到两张消费级 GPU。

## 4. 调度器源码分析

### 4.1 NORMAL、PROMPT 和 COMPLETION 三种引擎

[scheduler.hpp](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/csrc/engine/scheduler.hpp)
定义三种引擎：

- `NORMAL`：同一进程交替执行 prefill 和 decode。
- `PROMPT`：只执行 prefill。
- `COMPLETION`：只执行 decode。

这是 E/P/D 分离的基础。当前 Strata 是单个 pipeline 引擎同时处理 prompt 和 decode，没有独立阶段进程。

### 4.2 ALL / ANY 防饿死投票

`NORMAL` 引擎在 DP 组内必须让所有 rank 做同一种步型。DeepInfer 用两次 all-reduce：

1. ALL 点：所有 rank 都有 prefill 工作才做全组 prefill，避免一个 rank 工作、其他 rank 陪跑。
2. ANY 点：任一 rank 有 prefill 工作就定期触发，防止孤立 rank 无限等待。
3. 第二次投票确认本轮确实有 rank 排出了 prefill，避免全组空手进入 prefill 后反复清空 decode step。

对应源码是
[scheduler.hpp L294-L337](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/csrc/engine/scheduler.hpp#L294-L337)。

Strata 没有 DP rank 联盟，不需要照搬这两个 all-reduce。值得借鉴的是策略本身：

- 常规路径追求组级同步效率。
- 保底路径保证等待最久的请求不会永久被错过。
- 进入昂贵步型前先验证“真的能排到工作”，避免空转。

### 4.3 Chunked prefill 与抢占

开源 scheduler 支持：

- 一个 prompt 按 `max_num_batched_tokens`、checkpoint 和多模态边界切片。
- block cache 与 state cache 联合准入。
- decode 时为 KV block 抢占请求；被抢占请求可重算或直接停止。
- 结构化输出请求不能安全重算时直接停止。

这些是通用机制。Strata 已有 prompt chunk、BYIELD、slot 状态复制和对话 checkpoint，因此不能因为
DeepInfer 有同名机制就认为 Strata 缺少它们。

开源代码没有直接实现 Q&A 中按二次成本模型求解 `cost(q, c)` 的完整前填充均衡器。`FlopsEstimator`
只估算 attention、MoE、router 和 logits 的 FP8-equivalent FLOPs，用于统计，不用于求 chunk。

### 4.4 异步调度

[worker.py](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/deepinfer/worker/worker.py#L96-L184)
的重叠顺序是：

```text
GPU: forward(N) -> sample(N)
CPU: schedule(N+1) -> launch forward(N+1) -> post-process(N-1)
```

关键实现不是简单开线程，而是保存 `prev_step_state`，并把下一步 metadata 与上一步实际采样结果做 remap。
[`_correct_seqlens_qk`](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/deepinfer/worker/worker.py#L193-L245)
处理 MTP acceptance、batch 变化和 pinned D2H 修正。

这说明真正可复用的不是“异步”两个字，而是以下约束：

- 下一步 metadata 必须能用旧结果修正。
- 请求进入、退出和抢占都必须有单调的版本标识。
- sampling、post-process 和 request output 不能提前释放仍在 GPU 上读取的存储。
- 非有限值、取消和异常不能留下在途状态。

### 4.5 EPLB

[EPLB policy](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/deepinfer/model_executor/eplb/policy.py)
做两件事：

- 根据历史窗口为热门 logical expert 分配冗余副本。
- 用贪心算法把所有 physical expert 放到各 rank，使历史 step 的峰值 rank load 尽量低。

运行时按窗口收集 token 数，周期重排权重。单机内通过 `symm_mem`/IPC 搬运，跨节点退回 NCCL P2P。

这对 Strata 的直接价值较低，原因不是算法不好，而是前提不同：

- DeepInfer 有 EP 和冗余 expert。
- Strata 是 layer split，每张卡负责不同层的 expert cache。
- Strata 的 expert cache 已经会根据 profile、路由计数和 adaptive swap 调整常驻专家。

可以研究其“最小化历史窗口内峰值负载”的目标函数，用于未来的多卡 expert-cache 规划；不应复制整套
EPLB runtime。

### 4.6 Context Parallel

[CPManager](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/deepinfer/model_executor/parallel_utils/cp.py)
用 DeepEP `BucketBuffer` 做 all-gather。模型侧采用 mirror/zigzag 分块，让长序列 token 在不同 rank 间
较均衡地分配。

Strata 的两卡 layer split 中，每个 token 本来就会经过两张卡，问题不是“把一条序列拆成两份 attention”，
而是两个 pipeline stage 的吞吐是否平衡。因此 CP 不适合当前架构，也不应在没有 NVLink 的小卡上引入。

### 4.7 MTP 变长验证

DeepInfer 的 MTP 规划器比“固定验证 5 个 token”细：

- Confidence head 给出每个 draft position 的接受概率。
- [`MTPVarlen`](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/csrc/engine/mtp_varlen.hpp)
  按预期有效 token/时间选择总验证宽度。
- 使用固定性能曲线加运行时中位数修正 forward 和 sampler 成本。
- [`compute_seqlens_q_by_confidence_rates`](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/deepinfer/model_executor/layers/tilelang_ops/schedule_mtp_varlen.py)
  在 GPU 上按累计接受概率重新分配全局 query budget。

官方说明称固定 5 token 可降到平均 3.2-4.4，生产约 60 seq/rank 时提高 4%-7% 吞吐。这是 V4.1、
EP64/DP64 环境下的官方数字，不能外推到 Strata。

Strata 并不是从零开始：

- [DraftPolicy](../../include/strata/spec/draft_policy.hpp) 已根据 draft confidence、接受率和实测 round cost 选择窗口。
- `--spec-min-p`、`--draft-conf`、`--mtp-max-t` 已提供多个控制面。
- `STRATA_SPEC_COST=1` 已有 cost-aware MTP chain 逻辑。

因此值得借鉴的是“一个 batch 内按全局预算重新分配验证 token”，而不是重新实现 confidence head。

### 4.8 Batch invariance

这是与当前 Strata 最直接相关的能力。

DeepInfer 的 `DEEPINFER_BATCH_INVARIANT=1` 会：

- 启用 DeepGEMM/TileKernels 的确定性算法。
- 在 attention backend 选择 batch-invariant 路径。
- 避免 MoE reduction 因 batch 组成不同而改变归约顺序。
- 用 sequence seed 和 sample position 生成独立随机流。
- 用[测试](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/tests/models/test_batch_invariant.py)
  比较同一 prompt 在 batch 内单独运行与整批运行的结果。

源码入口见
[runtime_config.py](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/deepinfer/worker/runtime_config.py#L56-L69)
和 [attention backend](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/deepinfer/model_executor/layers/attention_backend.py#L75-L145)。

Strata 当前情况更严峻。已有
[UTIL-BATCH-GROUP-FIT 结果](UTIL_BATCH_GROUP_FIT_RESULT-20261031.md)
证明：

- 6 slots、2 groups 与 6 slots、1 group 都能稳定重复自己的结果。
- 两种路径的固定 greedy 文本不同。
- 因此只改变 group 数也不是“只改调度、不改算术”。

这意味着动态会话迁移或动态 group 选择目前不能只依赖“再计算一遍 KV”来保证一致。需要先把
batch-dependent arithmetic 找出来。

## 5. Strata 当前已经拥有的能力

把 DeepInfer 和 Strata 对照后，以下能力不能算缺口：

| 能力 | Strata 现状 |
| --- | --- |
| 多请求并发 | `--batch` / `--slots`，生产为 6 slots |
| Layer split pipeline | 每层按卡分段，生产为 layer split 24 |
| Pipeline group | `--batch-groups`，生产为 2 groups |
| 短请求优先 | `STRATA_ADMIT_SHORT_FIRST`，带最大等待老化 |
| 长 prompt 让出 | BYIELD 和 chunk 边界 |
| Prompt 与 decode 交错 | `STRATA_BATCH_DECODE_SHARE` 控制每段 prompt 后的 decode 时间 |
| 自适应 prefill chunk | `PrefillPlan` 根据文件、页缓存、ring 和预算选择 |
| Prompt 与 decode 并发 | 实验性 `STRATA_PIPED_CONCURRENT=1` |
| Slot prefix 复用 | `pick_slot` 优先选择能复用最长对话前缀的 slot |
| Pipeline stage 重叠 | `--pipeline-windows`、batch groups、hand-off pipeline |
| MTP 接受率模型 | `DraftPolicy`、`spec-min-p`、`draft-conf` |
| Expert cache 热点调整 | profile、routing counts、adaptive swap、RAM/VRAM tier |

参考 [BATCHING.md](../BATCHING.md)、[MULTI_GPU.md](../MULTI_GPU.md) 和
[DETAILS.md](../DETAILS.md)。

因此如果目标是改善“多会话时两卡利用率”，最可能的问题不是缺少一个全新 scheduler，而是：

1. 当前 group/pipeline 组成是否产生了无法消除的空窗。
2. 前端 admission 和状态复制是否处于关键路径。
3. 长 prompt 的固定 chunk 和 decode share 是否符合当前负载。
4. 改 group 或 batch 形状时，数值路径变化是否让正确性门先失败。
5. MTP 是否因 pipelined batch 路径限制而没有在并发时贡献收益。

## 6. 可以借鉴的具体方向

### 6.1 优先级 A：建立 batch-shape invariance 契约

这是其他动态调度方案的前置条件，优先级高于直接写新调度器。

目标不是立即让生产默认路径完全 batch-invariant，而是先建立一个可复现、可测量的实验路径：

1. 同一 prompt、temperature 0、固定 seed，在 solo、2/4/6/8 slots、不同 group 分配下生成。
2. 比较 token ids、logprobs、top-k logits、finish reason 和 key state。
3. 对 MoE reduction、attention row reduction、GEMV/MMVQ、sampler RNG 分别建立小 harness。
4. 找出第一个产生差异的阶段，而不是只比较最终自然语言文本。
5. 任何 deterministic 路径都必须 opt-in，先测吞吐和显存，再决定是否可默认。

DeepInfer 的价值在这里是“把 invariance 当成运行时能力和回归测试”，不是某个可以直接复制的 kernel。

可行性：中高。Strata 已有较多 kernel parity 工具和 greedy parity 流程，可增量验证。

风险：高。要稳定多 row 归约通常会增加寄存器、workspace 或取消融合，可能牺牲 1%-10% 性能。

### 6.2 优先级 A：工作感知的 admission 和 group 选择

当前 `pick_slot` 已经考虑 prefix、hole、busy group 和 LRU。下一步可以从“请求数”升级为“估计工作量”。

建议的评分维度：

```text
prompt_work
  = fresh_tokens
  + context_length x attention_factor
  + estimated_expert_misses x miss_cost
  + slot_or_checkpoint_copy_cost

group_load
  = remaining_decode_work
  + queued_prompt_work

request_score
  = normalized_group_load
  + prefix_miss_penalty
  + predicted_slot_copy_penalty
  - waiting_age_compensation
```

这不是 DeepInfer 可以直接复制的公式，而是把它公开的调度思想映射到 Strata：

- Prompt 成本同时取决于新 token 数和已有 context。
- Decode 成本同时取决于 active rows 和总 KV 长度。
- 长 prompt 不能让短请求永久等待。
- 已经缓存的长前缀值得保留 affinity，但不能无条件压过负载。

按 DeepInfer 的设计，工作应被分到“当前 quota 负载最低”的 rank；在 Strata 中，对应对象是一个 slot
及其 pipeline group，而不是把运行中的会话迁到另一张卡。

可行性：中。可在纯 Python admission 层做 trace replay 和 shadow mode。

前提：必须先有 batch-shape invariance；否则同一个会话换 group 就可能改变输出。

### 6.3 优先级 B：时间预算驱动的 chunk 和 decode 保底

DeepInfer 官方说明用时间预算求解 prefill chunk，并在 DP 组内取最小预算。开源代码只公开了 token 上限
和 chunk checkpoint，因此应借用“模型 + 预算 + 最小保证”的结构。

Strata 的对应位置是：

- [batch_read.hpp](../../include/strata/program/batch_read.hpp)
- [prefill_plan.hpp](../../include/strata/program/prefill_plan.hpp)
- [server.py 的 admission](../../serve/server.py:1815)

建议先做离线 trace：

1. 记录每个 prompt 的 fresh tokens、cache hit、unique experts、page-cache residency 和实际 chunk 时间。
2. 记录每个 group 的 active slots、window rows、KV tokens 和端到端 decode gap。
3. 拟合“chunk 时间”和“一个 decode window 时间”，不要直接采用 DeepInfer 的四档阈值。
4. 用固定 trace 回放多个 chunk policy，比较 queue wait p50/p95、decode gap p99、总吞吐和 GPU 利用率。
5. 只有回放中至少一个目标改善且其他目标不显著回退，才进入运行时实现。

不建议直接改生产 `STRATA_BATCH_DECODE_SHARE`。该值已有本机测量背景，缺少当前混合负载 A-B-A 时不应扫描。

### 6.4 优先级 B：跨 step 异步调度

只有在 profiling 证明 GPU 在等 CPU 时，才考虑 DeepInfer 风格的重叠。

当前需要先测量：

- 每 window 的 host schedule 时间。
- batch poll/verdict/hand-off 时间。
- sampling D2H、tokenization、slot copy 和 server line handling 时间。
- prompt 读取期间 decode 的空窗。
- 两个 group 的 stage occupancy 和 hand-off gap。

DeepInfer 的 `WorkerStepState` 结构可以作为设计参考，但 Strata 还有 GDN recurrence、slot state、
pipelined MTP、adaptive expert cache 和非有限值恢复，状态机更复杂。

直接照搬容易出现：

- 上一步 acceptance 改变下一步 metadata。
- 被抢占或取消的请求仍在 GPU 上。
- 同一 slot 的 draft K/V 被下一步覆盖。
- post-process 释放了尚在读取的 buffer。
- engine restart 后在途状态无法恢复。

可行性：高复杂度。应等 profiling 给出明确的关键路径后再做。

### 6.5 优先级 B：全局 MTP token budget

Strata 已有 cost-aware `DraftPolicy`，因此在 solo 请求上重复实现 DeepInfer 的 confidence 逻辑价值有限。

可以借鉴的是：

- 把 batch 内每个 slot 的候选 draft position 放入一个全局队列。
- 用累计接受概率估计每个候选的边际收益。
- 用运行时 forward/sampler 中位数估计成本。
- 在总 verify row 预算内选择收益最高的候选。

潜在落点是 [draft_policy.cpp](../../src/spec/draft_policy.cpp)，但必须保持：

- pipelined group 的 row 上限。
- 每个 slot 的 draft K/V 与 commit 顺序。
- 固定 greedy parity。
- 显式取消和 EOS 的处理。

官方 4%-7% 是 V4.1 EP64/DP64 的数字，不能作为 Strata 采纳依据。

### 6.6 优先级 C：峰值负载感知的 expert cache 规划

DeepInfer 的 EPLB 贪心目标是最小化历史窗口中“最慢 rank / 平均 rank”的负载，而不是只最小化平均负载。

Strata 没有冗余 EP expert，但可以用相同目标做离线 cache 规划：

- 输入是每层的 token-to-expert 路由窗口。
- 输出是每张卡固定 expert slots 中保留哪些 blob。
- 目标是减少每个 decode window 的最慢 stage 的 miss 数。

这可以与当前 profile 和 adaptive swap 做 A/B，而不是替换它们。

可行性：中低。Strata 已有 profile 和 dynamic adaptation，预计额外收益需要真实 trace 才能证明。

## 7. 不建议直接移植的部分

### 7.1 把会话迁到另一张 GPU

在 Strata 当前层切分中，一张卡只有部分层，不能独立完成模型。把会话从 GPU0 迁到 GPU1 不成立；两张卡
仍必须共同处理该会话。

真正可迁移的是 slot 和 pipeline group 的归属。代价包括：

- 每张卡上的 session/KV/state 复制。
- 两个 group 的 drain 和重新填充。
- prefix cache 可能失效。
- 新 group 的数值路径可能改变输出。

DeepInfer 本身也不是持续迁移活跃请求。它主要在入口路由请求；官方 Q&A 中的 decode engine 转发只发生在
shutdown grace period 或故障场景，并依赖独立 KV 存储。

### 7.2 Wide-EP、EPLB runtime 和 NVLink 权重重排

需要 EP 和较大的 NVLink 域。两层 MoE 权重重排和 SM100 kernel 不能在 2080 Ti 上提供有效收益。

### 7.3 3FS 和跨机器 KV cache

需要高速 RDMA、分布式存储和独立 cache 目录。Strata 当前使用本地 RAM/VRAM/session checkpoint，
直接引入会显著增加故障域。

### 7.4 Context Parallel

需要模型层内部按序列拆 attention，并依赖 DeepEP all-gather。当前瓶颈更可能是 layer-split pipeline
平衡，不是单请求 context attention 的卡间切分。

### 7.5 DeepInfer 的模型 kernel 和量化格式

SM100、FP8/FP4、FlashMLA、DeepGEMM 与 Strata 的 IQ/Q 量化、sm_75 CUDA kernel 不兼容。只可作为
算法说明，不能复制实现。

### 7.6 直接采用 DeepInfer 的 group fairness 实现

Strata 已尝试过 `STRATA_BATCH_FAIR_SERVE=1`，通过启动、路径和 parity 门，但完整并发曲线在 4 路和
8 路回退，因此被否决。见
[UTIL-BATCH-FAIR-SERVE 结果](UTIL_BATCH_FAIR_SERVE_RESULT-20261031.md)。

这说明 Stage doorbell 的公平轮转和 DeepInfer 的 rank 级派发不是同一个问题。“公平”不能直接等同于
“每个 stage 轮一次”。

## 8. 建议的实施顺序

### Phase 0：只观测，不改变行为

在同一 boot、当前生产和 2/4/6/8 路负载下记录：

- prompt 长度分层后的 queue wait p50/p95/p99。
- group 内 active rows、busy slots、KV tokens。
- 每个 stage 的 busy 时间、hand-off gap 和 host serve 时间。
- prompt chunk 时间与 fresh/cached token 比例。
- expert miss、RAM/file/VRAM 来源。
- MTP drafts offered/accepted。
- GPU 利用率和端到端请求完成时间。

输出应是一份可 replay 的 trace。没有 trace 的调度公式没有可靠依据。

### Phase 1：batch-shape invariance

先用小 harness 证明差异来自哪里，再决定是否需要修改 kernel。

建议分别测：

- solo vs 2/4/6/8 slots。
- 同一 6 slots 下 1 group vs 2 groups。
- 同一 row 集合换 slot 顺序。
- prefill chunk 8K vs 16K。
- 固定 seed 与 greedy。

没有这个能力，动态 group 路由只能做“输出可能变化”的实验，不能成为灵活调度的可靠基础。

### Phase 2：shadow admission

在 `pick_slot` 旁计算工作感知评分，但不改变实际选择。记录：

- 现有选择和新选择。
- 预计 group load。
- prefix 节省量。
- 实际等待时间。

用 trace replay 验证后，再以环境变量做 opt-in A-B-A。

### Phase 3：全局 MTP budget

只在有并发 MTP 的真实路径上实验。若生产 pipelined batch 根本没有运行 slot drafters，则先解决该路径，
否则只改 `DraftPolicy` 不会产生声称的收益。

### Phase 4：异步调度

只有 Phase 0 显示显著 CPU 空窗时才做。先做双缓冲 metadata 和 shadow schedule，再逐步加入 in-flight
sample/post-process。

## 9. 验收边界

任何候选都应继续遵守 Strata 现有证据规则：

- 默认路径先保持不变，候选 opt-in。
- 固定模型、pack、量化、layer split、context、KV、expert cache 和采样参数。
- 先过启动、路径、资源和固定 greedy parity。
- 再做同 boot A-B-A 的 2/4/6/8 路测试。
- 至少一个目标级别有可复现收益，其他级别不出现超过门槛的回退。
- 失败后不扫描邻近参数，先把失败原因定位到单个机制。
- 不把 DeepInfer 在 SM100/EP64 上的官方数字写成 Strata 本机结果。

如果 batch-shape invariance 无法在不显著损失速度的情况下实现，那么“按当前负载动态换 group”应保持关闭。
更现实的方向仍是在固定 group 形状内优化 admission、prefix 复用、chunk/decode 交错和 MTP。

## 10. 术语和源码索引

| 名称 | 位置 |
| --- | --- |
| DeepInfer 总览 | [README](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/README.md) |
| 部署与生产设计 | [inference_deployment_qa.md](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/doc/inference_deployment_qa.md) |
| C++ scheduler | [scheduler.hpp](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/csrc/engine/scheduler.hpp) |
| 异步 worker | [worker.py](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/deepinfer/worker/worker.py) |
| EPLB policy | [policy.py](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/deepinfer/model_executor/eplb/policy.py) |
| CP communication | [cp.py](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/deepinfer/model_executor/parallel_utils/cp.py) |
| MTP 变长规划 | [mtp_varlen.hpp](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/csrc/engine/mtp_varlen.hpp) |
| Batch invariance test | [test_batch_invariant.py](https://github.com/deepseek-ai/DeepInfer/blob/553741557a57df44a46fb3ab72b6aafeaf945337/tests/models/test_batch_invariant.py) |
| Strata batch 行为 | [BATCHING.md](../BATCHING.md) |
| Strata 双卡 pipeline | [MULTI_GPU.md](../MULTI_GPU.md) |
| Strata group parity 结果 | [UTIL_BATCH_GROUP_FIT_RESULT-20261031.md](UTIL_BATCH_GROUP_FIT_RESULT-20261031.md) |
| Strata fair serve 结果 | [UTIL_BATCH_FAIR_SERVE_RESULT-20261031.md](UTIL_BATCH_FAIR_SERVE_RESULT-20261031.md) |

## 整体总结

DeepInfer 是一份质量较高、信息量很大的生产推理框架参考实现，但它的公开边界必须看清：它开源了
DeepSeek-V4.1-Flash 的离线推理核心、模型执行、调度、MTP、EPLB 和正确性机制，没有完整开源在线服务封装、
KV cache 集群、跨 replica 负载均衡和完整 E/P/D 控制器。它适合研究“DeepSeek 如何组织大规模推理”，
不适合作为 Strata 的直接替换框架。

对 Strata 最有价值的五个点是 batch-shape invariance、工作感知的 admission/group 选择、带保底机制的
预算调度、跨 step CPU/GPU 重叠，以及 batch 内全局 MTP 验证预算。前两项是当前双卡多会话问题的主要方向；
后两项要先由 profiling 证明存在收益空间；MTP 预算则可以先借鉴数学，但不要复制模型专属 confidence head。

最不现实的方案是把正在生成的会话直接迁到另一张卡。当前 layer split 不存在一张卡可独立承载完整模型的
前提，DeepInfer 自身也不是持续迁移活跃会话，而是在入口做 replica 路由，只在停机或故障时借助外部
KV 存储转发请求。Strata 更现实的路线是保持两卡共同计算，通过固定 group 内的 admission、prefix 复用、
prompt/decode 交错和可验证的 invariance 减少空窗。任何改变 group 或 batch 数值路径的动态调度，都应先过
正确性门，再谈吞吐。
