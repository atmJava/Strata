# 开源推理引擎与双卡调度源码分析

日期：2026-11-01

状态：研究文档。本文只总结已经拉取的源码和 Strata 已有证据。没有构建第三方仓库、下载模型、
重启生产服务或修改生产配置。

## 0. 结论摘要

先回答双卡和多会话的问题。

1. **当前常见配置不是“一个会话占一张卡”。** Strata 的双卡是把一个模型按层切成两段，每个
   token 或 verify window 依次经过两张卡。两张卡属于同一个 pipeline，不是两个独立模型。
2. **一个请求通常会让两张卡先后参与计算，但不会让两张卡同时为它计算。** 多 slot、多
   `--batch-groups`、prompt chunk 流水和 `STRATA_PIPED_CONCURRENT` 可以让不同请求在两个
   stage 上重叠，因此两张卡可能同时有事做；单请求或 stage 不均衡时仍会出现等待。
3. **不能把 layer-split 中的一个会话直接搬到另一张卡完成。** 另一张卡只有另一部分层，没有
   完整模型。可在同一 pipeline 内改变 slot/group、排队顺序、prefill/decode 交错，目标应是
   “提高两个 stage 的重叠和工作保持率”，而不是“把会话变成单卡任务”。
4. **真正的跨完整模型迁移只在 data-parallel replica 下成立。** 每个 replica 必须有自己的
   完整模型和独立卡组。两卡机器只有在完整模型能放进每张卡时，才可考虑两个单卡 replica；
   当前 Qwen MoE 和 22 GB 卡的组合通常不具备这个前提。
5. **动态组路由目前受正确性门槛阻塞。** Strata 已有实测表明，6 slots、2 groups 与 6 slots、
   1 group 都各自稳定，但固定 greedy 文本不同：一种答案是 94 token、`finish=stop`，另一种
   是 96 token、`finish=length`。在找到 batch-shape 依赖的算术来源之前，动态迁移会产生用户
   可见的输出变化。
6. **最值得借的不是某个现成调度器，而是以下组合：** 按真实 token/缓存/state/专家 miss
   估计工作量；为请求完整生命周期预留资源；短请求优先并有显式老化；长 prefill 与 decode
   按时间或 token 预算交错；按前缀和负载联合路由；逐层审计 batch 与单流差异。
7. **最高优先级仍是 GPU0 的 Xid 13 稳定性。** 动态调度实验不能绕过当前 P0；任何新 Xid、
   engine 非预期退出或输出不一致都应先停止性能实验。

本文中的“两张卡都在忙”与“总吞吐提高”不是同一件事。layer-split 的 stage 交接、专家缓存
miss、CPU 专家路径和 batch 形状都可能让 GPU 看起来忙而端到端任务并没有更快。最终判断必须
使用真实 agent 任务墙钟、TTFT、第二请求 TTFT、跨度、错误和输出一致性。

## 1. 证据范围与版本

研究快照位于：

```text
/tmp/strata-github-research.aoXYy2/repos
/tmp/strata-github-research.aoXYy2/notes/SOURCES.md
```

31 个仓库均用 `git clone --depth 1 --single-branch --filter=blob:none` 取得并固定 commit。
TensorRT-LLM 另做 sparse checkout，只取文档、Python runtime、C++ runtime 和示例。快照约
2.1 GB。所有仓库只读；没有运行其安装器、构建系统、benchmark 或服务。

当前 Strata 工作树基线为：

```text
branch  freeze/server-adapt-0.2.7
HEAD    bd8a2337b2ea79bc4ce79e4ddfce09d78970c220
date    2026-11-01T11:24:22+08:00
```

31 仓库快照中的上游 Strata 固定在 `c62d5e3a15b4`；当前工作树比快照多了本地适配和未提交
修改。因此本文同时使用：

- **快照源码事实**：固定 GitHub commit 中的代码和说明。
- **当前工作树事实**：本地 Strata 的源码、测试结果和文档。
- **仓库作者实测**：第三方 README、报告或 issue 中的数字，但没有在本机复现。
- **本机 Strata 证据**：在当前 2 x RTX 2080 Ti 或本仓库已有实验目录中的数字。
- **建议**：尚未实现，不能当成收益承诺。

## 2. 当前双卡拓扑与多会话现实

### 2.1 layer-split 是什么意思

[MULTI_GPU.md](../MULTI_GPU.md) 明确说明这是 pipeline parallelism，不是 tensor
parallelism。两张卡持有同一个模型的不同连续层；最后一张卡还持有输出 head、MTP draft
layer 和验证逻辑。每张卡有自己的 session state、KV cache 和针对本层范围的专家缓存。

```text
token / verify window
        |
        v
GPU0: layers 0..K-1  --- hand-off ---> GPU1: layers K..N, head, draft, verify
```

单请求的基本时序是串行的。把一层从 GPU0 算完再交给 GPU1，不意味着两张卡在同一毫秒都执行
矩阵运算。prompt chunk 可以在后面的卡处理 chunk `c` 时，让前面的卡读取 chunk `c+1`；
batch groups 也可以让一组窗口进入第二 stage 时，另一组开始第一 stage。这些才是当前双卡
提高并行度的手段。

### 2.2 A 会话与 B 会话会怎样

现有生产形态记录为 2 x RTX 2080 Ti 22 GB、262144 context、6 slots / 2 groups，见
[STATE.md](../STATE.md)。在这个配置下：

| 场景 | GPU0 | GPU1 | 是否是独立双卡计算 |
| --- | --- | --- | --- |
| 一个请求独占 | 先执行前段 | 后执行后段/head | 否，是同一 pipeline 的两段 |
| 两个 group 都有 decode window | 可执行 group A 的前段时，第二卡执行 group B 的后段 | 可反向轮转 | 可以重叠，但 group 不是固定绑卡 |
| 一个长 prompt 加若干 decode | prompt chunk 与 decode 窗口可在独立流上重叠 | 同上 | 可行，但会争 GPU、专家缓存和带宽 |
| 两个完整 replica，每个一张卡 | replica A | replica B | 是，但要求每卡装得下完整模型 |
| 两个独立模型，每个一张卡 | 模型 A | 模型 B | 是产品能力，不是单模型会话迁移 |

所以“A 独占导致 B 长期等待”需要拆开看：

- 如果 A 占的是 pipeline 中某一 group、控制线或长 prefill，B 可通过组分配、短请求优先、
  chunk 让行和 prefill/decode 交错减少等待。这是现实可行的调度问题。
- 如果 A 和 B 属于同一个 layer-split 模型，而 B 要“只在 GPU1 上算”，不可行；GPU1 没有
  GPU0 的层。
- 如果 A、B 是完整 replica，B 可以被路由到另一张卡；当前大模型是否能每卡完整驻留必须先
  按模型、量化、上下文、expert arena 和 runtime workspace 实测。不能只看权重文件大小。

### 2.3 Strata 已经有的并发与公平机制

| 机制 | 当前行为 | 证据 |
| --- | --- | --- |
| `parallel` / `--batch` / `--slots` | 多个会话进入 batch slots；默认仍是一个请求 | [BATCHING.md](../BATCHING.md) |
| `--batch-groups G` | layer split 的 slots 分成 G 组，以 pipeline 方式穿过各卡 | [BATCHING.md](../BATCHING.md) |
| prompt chunk | 长 prompt 分块读取，段间可让其他 stage 工作 | [MULTI_GPU.md](../MULTI_GPU.md) |
| `STRATA_ADMIT_SHORT_FIRST=N` | 不超过 N token 的新 prompt 先取得控制线 | [serve/server.py](../../serve/server.py#L318) |
| `STRATA_ADMIT_MAX_WAIT_S` | 长请求等待默认 30 s 后按短请求处理，防止永久饥饿 | [serve/server.py](../../serve/server.py#L325) |
| `BYIELD` | 长读在 chunk 边界为不到其一半长度的等待请求让路，每请求最多两次 | [serve/server.py](../../serve/server.py#L1813) |
| `STRATA_BATCH_DECODE_SHARE` | 单卡 batch 的 prompt chunk 后按时间让 slot 解码 | [BATCHING.md](../BATCHING.md) |
| `STRATA_PIPED_CONCURRENT=1` | 独立线程读 prompt，slot groups 继续 decode | [BATCHING.md](../BATCHING.md) |
| `--pipeline-windows 2` | 前一卡预计算下一 verify window，CUDA 上仍是 opt-in | [MULTI_GPU.md](../MULTI_GPU.md) |
| slot 返回 solo / MTP | 只剩一个活跃请求时回到单流投机解码路径 | [serve/server.py](../../serve/server.py#L1885) |
| 有界队列 | `max_queue`、`queue_timeout`，不承诺无限接单 | [serve/server.py](../../serve/server.py#L3945) |
| data-parallel replicas | 可选多个独立 engine，每个要自己的完整模型和卡组 | [serve/replicas.py](../../serve/replicas.py#L1) |

这些能力说明 Strata 不是“没有任何灵活调度”，而是已经有 slot、group、短请求优先、
老化、让行、prompt/decode 并发和实验性 replica 路由。现在的缺口主要是：

- 不同机制之间的统一成本模型；
- 对 stage wait、KV 水位、专家 miss 和 decode 剩余工作量的可解释估计；
- 动态选中 group 前的一致性与可回滚契约；
- 在 batch 形状变化时保证相同 greedy 轨迹。

### 2.4 正确性先于动态迁移

[UTIL-BATCH-GROUP-FIT 结果](UTIL_BATCH_GROUP_FIT_RESULT-20261031.md) 是最关键的本机证据：

- 6 slots / 2 groups 可以重复自己的结果。
- 6 slots / 1 group 也可以重复自己的结果。
- 两者之间的固定 greedy 文本不同。
- 1 group 输出 94 token，`finish=stop`；2 groups 输出 96 token，`finish=length`。
- 这不是随机采样，也不能用“重算 KV”解释掉。

因此“动态把 B 放进更空的 group”今天会改变模型经过的 batch 形状，从而可能改变输出。
必须先证明或修复 batch-shape invariance，再谈基于负载的动态 group 选择。

vLLM 的[批不变量说明](https://github.com/vllm-project/vllm/blob/e0d00e4b8eb0c89ec30921d0695cfdd409e9c211/docs/features/batch_invariance.md#L18-L25)
提供了概念参考，但其当前硬件要求是 compute capability 8.0 或更高，不能直接作为 SM75
实现。可借的是测试方法和“改变 kernel 选择/归约顺序会改变输出”的认识，不是开关本身。

## 3. 31 个固定版本仓库

下表中的“相关性”是本文判断，不代表仓库质量高低。

### 3.1 通用推理引擎与模型运行时

| 仓库 | 固定版本 | 相关性 | 根许可证 |
| --- | --- | --- | --- |
| [DeepInfer](https://github.com/deepseek-ai/DeepInfer/tree/553741557a57) | `553741557a57` | DeepSeek 当前公开推理核心；调度和 batch invariance 参考 | Apache-2.0 |
| [vLLM](https://github.com/vllm-project/vllm/tree/e0d00e4b8eb0) | `e0d00e4b8eb0` | 通用 continuous batching、prefix cache、资源预算 | Apache-2.0 |
| [SGLang](https://github.com/sgl-project/sglang/tree/b146a8576de9) | `b146a8576de9` | 短 prefill、HRRN、前缀感知准入 | Apache-2.0 |
| [LMDeploy](https://github.com/InternLM/lmdeploy/tree/dd4db0da274b) | `dd4db0da274b` | 长/短 prefill 轮换和可回滚准入，最接近当前问题 | Apache-2.0 |
| [TGI](https://github.com/huggingface/text-generation-inference/tree/b4adbf2f6e2e) | `b4adbf2f6e2e` | 严格 FCFS 和 token/block 预算的反例 | Apache-2.0 |
| [MLC-LLM](https://github.com/mlc-ai/mlc-llm/tree/44fd06d04f22) | `44fd06d04f22` | Action 化执行阶段、prefix commit 与 forward 重叠 | Apache-2.0 |
| [TensorRT-LLM](https://github.com/NVIDIA/TensorRT-LLM/tree/d2a9c816ad9e) | `d2a9c816ad9e` | 完整生命周期预留、暂停和抢占策略 | Apache-2.0 |
| [LuceBox](https://github.com/Luce-Org/lucebox/tree/4414d1c813a9) | `4414d1c813a9` | 短读轮次、长 prompt/decode 时间份额、KV offload | Apache-2.0 |
| [llama.cpp](https://github.com/ggml-org/llama.cpp/tree/a91eab67d838) | `a91eab67d838` | SM75 基础路径、GGUF 和 kernel 参考 | MIT |
| [ik_llama.cpp](https://github.com/ikawrakow/ik_llama.cpp/tree/13cffc006b82) | `13cffc006b82` | INT8/量化 CUDA kernel 参考，Turing 基础可用 | MIT |
| [ExLlamaV2](https://github.com/turboderp-org/exllamav2/tree/7dc12af3a81f) | `7dc12af3a81f` | 消费级 GPU kernel/量化参考 | MIT |
| [ExLlamaV3](https://github.com/turboderp-org/exllamav3/tree/bd1f51917240) | `bd1f51917240` | 同上；快照 README 未证明当前版本完整支持 SM75 | MIT |

### 3.2 Strata 生态与混合引擎

| 仓库 | 固定版本 | 相关性 | 根许可证 |
| --- | --- | --- | --- |
| [Strata](https://github.com/Niko1221/Strata/tree/c62d5e3a15b4) | `c62d5e3a15b4` | 当前架构、layer split、batch、SM75 路径 | MIT |
| [Strata-2080TI](https://github.com/fushengss/Strata-2080TI/tree/eb1842f7cbe3) | `eb1842f7cbe3` | 0.1.40.3 旧 fork，8 x 2080 Ti 实测 | MIT |
| [Strata-T8](https://github.com/T8mars/Strata-T8/tree/009d7ae3f27a) | `009d7ae3f27a` | Windows 整合发行、ComfyUI、视频和语音 | MIT |
| [Strata-architectds](https://github.com/architectds/Strata/tree/901ebe5267a9) | `901ebe5267a9` | 旧性能 fork，含双卡 pipeline/多 slot 实验 | MIT |
| [strata-manager](https://github.com/hongfaqiu/strata-manager/tree/52934bc7b3af) | `52934bc7b3af` | 安装、配置、监控、下载和验证 | MIT |
| [strata-nvfp4](https://github.com/sergqwer/strata-nvfp4/tree/586dc5e4611b) | `586dc5e4611b` | SM75 kernel 系列、NVFP4 和量化路径 | MIT |
| [hydragen](https://github.com/thirteenthd/hydragen/tree/99813ca09836) | `99813ca09836` | vLLM 批处理 + Strata GGUF 专家分页的混合设计 | Apache-2.0 |
| [viiwork](https://github.com/janit/viiwork/tree/46b167ad10fb) | `46b167ad10fb` | 多机器、多引擎编排 | MIT |
| [qwen3.8-flash-next-local-deploy](https://github.com/yxhpy/qwen3.8-flash-next-local-deploy/tree/04f37971327f) | `04f37971327f` | Qwen/Strata 部署实测，但根目录无许可证 | 需确认 |

### 3.3 RTX 20 系、SM75 与双 2080 Ti 专项

| 仓库 | 固定版本 | 相关性 | 根许可证 |
| --- | --- | --- | --- |
| [2080Ti-LLM-Toolbox](https://github.com/weicj/2080Ti-LLM-Toolbox/tree/cc05fdf42ce2) | `cc05fdf42ce2` | 双 2080 Ti 配方、patch 与 benchmark 汇总 | 无根文件，需确认 |
| [vLLM-2080Ti-Definitive](https://github.com/weicj/vLLM-2080Ti-Definitive/tree/93c9013e375b) | `93c9013e375b` | 面向双 2080 Ti 的 vLLM fork、profile 和 launcher | Apache-2.0 |
| [qwen3-8-27b-dual-2080ti-vllm](https://github.com/zyYuc/qwen3-8-27b-dual-2080ti-vllm/tree/da687aa9cc25) | `da687aa9cc25` | 双卡 NV2、TP2、180K 的完整部署配方 | MIT |
| [triton-turing](https://github.com/Chennesxu/triton-turing/tree/8da2085caa00) | `8da2085caa00` | Turing MMA、无 `cp.async` 流水线和 autotune | MIT |
| [PocketLLM](https://github.com/lvyufeng/PocketLLM/tree/16aedbcf1e82) | `16aedbcf1e82` | 明确面向 2080 Ti 的小模型引擎 | 根文件写 MIT，README 写 Apache，冲突 |
| [QwenForge](https://github.com/ziyiguanmomo/QwenForge/tree/39fad15c0ea4) | `39fad15c0ea4` | 0.5B 小模型 Turing 教学/benchmark 引擎 | MIT |
| [pyre](https://github.com/Lalitaditya-tickoo/pyre/tree/9c5141db5f48) | `9c5141db5f48` | T4/SM75 教学型 paged KV、continuous batching | 无根文件；README 称 Apache-2.0 |
| [Whetstone](https://github.com/Anonym0usWork1221/Whetstone/tree/1d7d5d779270) | `1d7d5d779270` | Turing kernel、量化和 roofline 研究 | Apache-2.0 |
| [llmcuda-rs](https://github.com/nixabe/llmcuda-rs/tree/4be4406dfcac) | `4be4406dfcac` | SM75 完整实验引擎、负载路由、batch divergence 审计 | Apache-2.0 |
| [gpu-unlock](https://github.com/Zagitta/gpu-unlock/tree/7eb51bccae0a) | `7eb51bccae0a` | 实验 Linux kernel module，不是推理引擎 | 无根文件；README 称 GPL-2.0，需确认 |

### 3.4 许可证边界

可参考其设计与测试，不等于可以直接复制代码。尤其注意：

- `2080Ti-LLM-Toolbox`、`pyre`、`qwen3.8-flash-next-local-deploy` 和 `gpu-unlock`
  的根目录没有可作为授权依据的 LICENSE。
- `PocketLLM` 的根 `LICENSE` 是 MIT，但 README 指向同一文件并称 Apache-2.0；这是仓库内
  冲突，复制前必须向维护者确认。
- `gpu-unlock` 是内核模块，涉及驱动、安全、保修和稳定性，不属于可随手移植的推理优化。
- 第三方仓库的模型权重另有许可证；代码许可证不自动覆盖模型或基准数据。

## 4. 主流通用引擎源码对照

### 4.1 vLLM：预算、准入门槛和水位

vLLM 当前调度配置有几个非常清晰的分层，见
[`config/scheduler.py`](https://github.com/vllm-project/vllm/blob/e0d00e4b8eb0c89ec30921d0695cfdd409e9c211/vllm/config/scheduler.py#L48-L209)：

- `max_num_batched_tokens` 控制一个 iteration 的 token 预算。
- `max_num_scheduled_tokens` 允许投机解码在 batch 内追加 token。
- `max_num_seqs` 决定 runner/CUDA graph 的容量。
- `max_num_active_seqs` 单独限制准入的 RUNNING 请求数，可以小于 runner 容量。
- `long_prefill_token_threshold` 限制长 prompt 每轮可拿的 token。
- `long_prefill_token_threshold_adaptive` 用排队和运行请求数的公平份额调整阈值。
- `max_num_queued_reqs` 和 `max_num_queued_tokens` 把等待请求数和等待 prompt token 数
  变成有界 QoS 阀门。
- `scheduler_reserve_full_isl` 在准入时检查整个 input sequence 是否最终装得下，而不只看
  第一块。
- `watermark` 给 KV cache 留出水位，减少刚准入就被驱逐的抖动。
- `prefill_schedule_interval=N` 允许插入只做 decode 的步，防止长 prefill 每步都抢预算。

请求队列支持 FCFS 和 priority，见
[`request_queue.py`](https://github.com/vllm-project/vllm/blob/e0d00e4b8eb0c89ec30921d0695cfdd409e9c211/vllm/v1/core/sched/request_queue.py#L13-L205)。

最值得 Strata 借的不是 PagedAttention，而是四个约束：

1. runner 上限、并发准入上限和工作量预算不是同一个数。
2. 长 prompt 的 token 份额必须可被其他请求约束。
3. 请求被拒/排队/重试必须有清晰的 HTTP 语义。
4. 资源预留必须覆盖请求整个生命周期，而非只看第一 chunk。

vLLM 的[特性矩阵](https://github.com/vllm-project/vllm/blob/e0d00e4b8eb0c89ec30921d0695cfdd409e9c211/docs/features/README.md#L64-L73)
确认 Turing 支持 chunked prefill、automatic prefix caching、speculative decoding 和
CUDA graph。量化必须逐项看：[Marlin 支持 Turing，但排除 Marlin MXFP4](https://github.com/vllm-project/vllm/blob/e0d00e4b8eb0c89ec30921d0695cfdd409e9c211/docs/features/quantization/README.md#L67-L83)。

### 4.2 SGLang：短 prefill、token 老化和前缀 leader

SGLang 的 [`schedule_policy.py`](https://github.com/sgl-project/sglang/blob/b146a8576de930cf3adc104e601feb2790cdf3f6/python/sglang/srt/managers/schedule_policy.py#L348-L495)
有几个可以直接转成设计的机制：

- `shortest_prefill_work` 使用没有命中缓存的 prompt 工作量，而不是原始 prompt 长度。
- 短 prefill 优先，同时在等待队列里暂缓重复前缀的 follower，让第一个请求先建立缓存。
- active chunk 会为更短的等待请求预留 page 对齐的 token budget。
- HRRN 用“已经处理的总 prefill token 数 / 当前未缓存 token 数”的老化值排序，避免以秒为
  单位在不同吞吐阶段失真。
- [`prefill_decode_interval`](https://github.com/sgl-project/sglang/blob/b146a8576de930cf3adc104e601feb2790cdf3f6/python/sglang/srt/managers/scheduler.py#L1404-L1479)
  在 batch 足够大时插入 decode-only 轮次。

这对 agent 负载很相关：多个子代理可能共享一大段 system prompt/tools，但随后各自走不同分支。
只按 prompt 长度排序会把共享前缀请求同时放进来，反而重复读取或重复 miss。

SGLang 整体依赖 PagedAttention、radix cache、DP/TP 和更复杂的模型 runner，不适合移植整套。

### 4.3 LMDeploy：长 prefill 与普通 prefill 分轮

LMDeploy 的
[`prefill_scheduler.py`](https://github.com/InternLM/lmdeploy/blob/dd4db0da274b17c751fbf49f3d3d1ba31a6df6fb/lmdeploy/pytorch/paging/prefill_scheduler.py#L50-L242)
是这批通用引擎中与 Strata 长 prompt 问题最接近的实现：

- `STANDARD`、`SHORT_ONLY`、`LONG_FIRST`、`LONG_FIRST_IF_FINAL` 四种 turn policy。
- 普通/最终 prefill 与未完成的长 prefill 分开。
- short turn 先处理 normal/final，把非最终 long prefill 留在后面。
- long turn 只放一个 long waiter，再填充普通 prefill，防止所有 stage 被长读占满。
- long prompt 按预计 chunk 数和等待时间获得老化信用。
- prefix match、容量检查和 runtime state 分配组成可回滚事务；失败时恢复匹配前的精确状态。

这比简单的“短请求优先”更完整。Strata 如果只增加一个静态长度阈值，会重现仓库自己已经
发现的饥饿和参数扫描问题；应同时定义老化单位、长读轮次和资源回滚。

### 4.4 LuceBox：五阶段调度与时间份额

LuceBox 的
[`scheduler.cpp`](https://github.com/Luce-Org/lucebox/blob/4414d1c813a965ccbbe87cf67c93d915be0a3fa0/server/src/server/scheduler.cpp#L980-L1013)
把每个循环明确分成 admission、decode、prefill、flush、reap。它还有：

- `kShortFirstSteps = 4`：连续四步短 prompt 优先后，长 prompt 必须加入，防止短请求持续到达
  造成永久饥饿。
- 能一步完成的短 prompt 不与更长 prompt 混在同一步。
- 共享 system prompt/tools 的 subagents 有 prefix leader：第一个先保存前缀，后续尝试 restore。
- 长 prompt 和 decode 在 Qwen 路径上使用 512-token mixed slice、`prefill_time_share=0.5`
  和 8192-token 长阈值，见
  [`qwen35_seq_engine.h`](https://github.com/Luce-Org/lucebox/blob/4414d1c813a965ccbbe87cf67c93d915be0a3fa0/server/src/qwen35/concurrency/qwen35_seq_engine.h#L91-L115)。
- KV 不足时可 offload、recompute 或 park 较新的 decode，而不是让一个请求无限增长。

可以借“按时间份额交错”和“短读轮次有上限”。不能照搬常量 512/8192 或整套 KV offload；这些
必须由本机 prefill/decode 速度、expert miss 和 VRAM 决定。

### 4.5 llmcuda-rs：完整模型 worker 路由和逐层差异审计

这个仓库明确面向 SM75，虽然仍是实验项目，但有两个高价值部分。

第一是
[`router.rs`](https://github.com/nixabe/llmcuda-rs/blob/4be4406dfcacaf144dc1f479f137cf2cdc31e4a4/crates/llmcuda-engine/src/router.rs#L1-L178)
的 data-parallel worker 路由：

```text
score(w) = a * prefix_fraction
         - b * queue_pressure
         - c * kv_utilization
         - d * resident_sequences
```

作者记录了真实教训：只有前缀、队列和 KV 三项时，共享 system prompt 的多个 agent 请求仍会
堆在最早预热前缀的卡上，另外一卡空闲。decode 中的请求不产生大量“尚未计算”的 queued
tokens，KV 水位也变化太慢；最后必须加入等待中和运行中的 resident sequence 数，才能把
请求真正分散。默认权重为 prefix `1.0`、queue `0.3`、KV `0.5`、resident `2.0`。

第二是
[`audit_batch_divergence.rs`](https://github.com/nixabe/llmcuda-rs/blob/4be4406dfcacaf144dc1f479f137cf2cdc31e4a4/crates/llmcuda-engine/src/bin/audit_batch_divergence.rs#L1-L27)
的逐层审计：同时运行 batch 和 single-stream forward，在每层 mixer 与 MoE block 后读取
hidden state，报告第一处分歧。Strata 当前需要同样的“哪一层、哪个归约、哪个专家路径开始
不同”的证据，而不是继续只比较最终文本。

其 scheduler 还明确对每个请求预留完整 decode 生命周期预算、给 KV 留 watermark，并拒绝
小到 decode 会永久吃光 prefill budget 的配置，见
[`llmcuda-sched/config.rs`](https://github.com/nixabe/llmcuda-rs/blob/4be4406dfcacaf144dc1f479f137cf2cdc31e4a4/crates/llmcuda-sched/src/config.rs#L35-L180)。

### 4.6 TGI：严格 FCFS 的反例

TGI 的
[`queue.rs`](https://github.com/huggingface/text-generation-inference/blob/b4adbf2f6e2e/backends/v3/src/queue.rs#L277-L373)
从队首弹出请求；如果请求超过 prefill/total token budget，就放回队首并停止本批扫描。
这能保持严格顺序，但队头长 prompt 会阻塞后面很小的请求。它不是 Strata 应照搬的策略，
适合作为“短请求优先 + 明确老化”的对照。

### 4.7 MLC-LLM：执行阶段和提交时序

MLC-LLM 把 prefill、draft、verify、decode 做成独立 Engine Action，见
[`action_commons.cc`](https://github.com/mlc-ai/mlc-llm/blob/44fd06d04f22/cpp/serve/engine_actions/action_commons.cc#L14-L134)。
在 [`batch_verify.cc`](https://github.com/mlc-ai/mlc-llm/blob/44fd06d04f22/cpp/serve/engine_actions/batch_verify.cc#L143-L145)
中，prefix cache 的上一轮 commit 与当前 GPU verify 重叠。

Strata 可借的是执行阶段边界和“哪些 CPU 工作能安全地与 GPU 下一步重叠”。不能借的是
MLC 的编译器和执行模型本身；Strata 已有自己的 engine protocol、slot checkpoint 和
prefix cache。

### 4.8 TensorRT-LLM：三种资源策略的取舍

TensorRT-LLM 的
[`scheduler.py`](https://github.com/NVIDIA/TensorRT-LLM/blob/d2a9c816ad9e/tensorrt_llm/_torch/pyexecutor/scheduler/scheduler.py#L1914-L2512)
提供三种清晰策略：

- `GuaranteedNoEvict`：准入时为请求完成预留 block，尽量不发生驱逐。
- `StaticBatch`：只有没有活跃请求时才准入新请求。
- `MaxUtilization`：尽量塞满 batch，必要时暂停已经开始但未完成的请求。

它还有两次预算预测、prefix-aware duplicate deferral 和多种 block manager。Strata 可借：

1. 把“吞吐最大”“不驱逐”“静态 batch”明确成不同目标，不要用一个开关同时暗示三者。
2. 任何抢占/暂停都要有可恢复状态和明确的用户体验。
3. 完整生命周期预留宁愿少接单，也不要准入后反复抖动。

TRT-LLM 的复杂度和目标硬件与 SM75 双卡差距太大，不应移植。

### 4.9 DeepInfer

DeepSeek 的 DeepInfer 是面向 SM100/Ascend 950 的生产核心子集，不是可替换 Strata 的消费级
引擎。对当前问题最值得读的是 batch invariance、按工作量调度、抢占/配额和异步 worker
metadata 修正。完整的开源边界、EPLB/CP/MTP 分析见
[DeepInfer 专文](DEEPINFER_REFERENCE_ANALYSIS-20261101.md)。

## 5. Strata 衍生项目与 SM75 专项

### 5.1 Strata-2080TI：可用的旧版实测，但不能直接搬配置

`Strata-2080TI` 固定的是上游 0.1.40.3 一带的 fork，不是当前 0.2.6/2.7 主线。

作者在 8 x RTX 2080 Ti 22 GB、仅 PCIe、无 NVLink/P2P、两张 NUMA 节点、377 GB RAM、
CUDA 12.8、Q8_0、所有专家驻留的机器上报告：

- 4K / 16K / 32K prompt：1080 / 2240 / 2920 tok/s，100K：3974 tok/s。
- 单请求 decode：57-60 tok/s。
- 4 路合计约 120 tok/s；8 路合计 136 tok/s。
- 4 slots / 4 groups 优于 8 slots；串行大约 50 tok/s。

其两个工程发现比数字更有价值：

- 原 `--prefill auto:32768` 借走每卡 2295/3072 expert slots，prompt 期间专家重新经过 PCIe。
  fork 用 `--split-chunks N` 把 prompt 控制在 2048-4096 token 块。
- stage prompt 线程继承了 host 线程的 CPU affinity，35 个线程挤在 CPU0，GPU 曾因 host
  停顿等 14%；修复后该段 8.4 s 降到 0.4 s。

这只是 fork 作者在 8 卡机器上的实测，不是本机 2 卡结果。当前 Strata 已有
`--prefill-pipe`、`STRATA_PIPED_CONCURRENT`、`STRATA_HANDOFF_PIPE` 等后续实现；移植前应先
逐项对照，不能把旧 `split_chunks` 直接复制回来。

### 5.2 strata-nvfp4 和当前 SM75 kernel 系列

`strata-nvfp4` 包含 `include/strata/core/sm75_series.hpp`、
`include/strata/core/tc_sm75.hpp` 和一批 Turing kernel。当前工作树已经有更成熟的开关：

- `STRATA_SM75_PERF=1` 开启一组声称 bitwise 等价的 Turing kernel。
- `STRATA_TC_SM75=0|1` 单独控制 FP16 tensor-core projection 路径。
- `STRATA_PF_FUSED_SM75=1`、`STRATA_BF16_TC=1` 等改变数值路径，必须单独 opt-in。

Turing 的硬限制在
[`tc_sm75.hpp`](../../include/strata/core/tc_sm75.hpp) 中写得很清楚：

- 没有 TF32 MMA；
- 没有原生 BF16 MMA；
- 没有 `cp.async`；
- 有 FP16 `m16n8k8` 和 INT8 tensor-core 路径；
- cuBLAS 在 SM75 上的 BF16 GEMM 可能落到慢 FFMA 路径。

本机已有证据见 [SERVER_HW.md](../SERVER_HW.md)：

- `STRATA_SM75_PERF=1` 对 84K 冷 prefill 从 2375.6/2381.3 提高到
  2552.0/2566.5/2556.9 tok/s，约 +7.2% 到 +7.6%。
- 43-50K prefill 约 +6.0% 到 +7.8%；四路约 +4.1% 到 +7.5%。
- 单流 decode 在噪声内，不应宣称稳定收益。
- 512-token 同答案测试为 9/10；首次重启异常未复现，因此文本相等仍不是数学证明。
- `STRATA_PF_FUSED_SM75` 主要覆盖 Q2_0/Q2_0 路径，对当前 IQ3_XXS 模型无效。

结论：kernel 可以继续从 Turing 专项仓库借，但必须遵守“Turing only + opt-in + 逐位测试 +
真实模型端到端测量”的门。

### 5.3 hydragen：架构参考大于性能证据

`hydragen` 试图把 vLLM continuous batching、prefix cache、Triton Fused MoE 专家分页和
Strata GGUF expert paging 结合起来，包含：

- `PagedExpertManager`：专家驻留、LRU、异步 DMA；
- expert-locality queue reordering；
- prefix slot affinity；
- `BYIELD`；
- solo MTP promotion；
- vLLM 与 Strata 两套 engine 后端。

它的设计方向值得借鉴，尤其是“同一个 agent 系统提示词导致前缀热点”和“专家命中率决定
decode 速度”。但 README 的大幅加速数字来自单个消费级 GPU 配置，没有在本机双 2080 Ti
复现，不能当成本机收益。

### 5.4 其他 Strata 生态

- `Strata-architectds` 是旧 0.1.40.2 性能 fork，pipelined verify windows、CPU share、
  async adaptive tier 等部分后来已进入上游。可读其双卡实验记录，但不应把旧分支覆盖当前树。
- `Strata-T8` 主要是 Windows 整合发行、ComfyUI、视频和语音，不是双卡会话调度核心。
- `strata-manager` 提供安装、配置、监控和槽位可视化；它适合成为调度实验的观测面。
- `viiwork` 是多机器、多引擎编排器。它把 GPU 归属到一个 model/backend，与当前单机
  layer-split 内动态 group 调度不是同一层问题。
- `qwen3.8-flash-next-local-deploy` 是 Strata 部署实测，但根目录无许可证，不能复用代码。

## 6. RTX 2080 Ti / SM75 专项结论

### 6.1 不能按新卡资料推断 SM75

快照中只有以下项目明确把 SM75 作为主目标：

- `Strata-2080TI`
- `strata-nvfp4`
- `2080Ti-LLM-Toolbox`
- `vLLM-2080Ti-Definitive`
- `qwen3-8-27b-dual-2080ti-vllm`
- `triton-turing`
- `PocketLLM`
- `QwenForge`
- `pyre`
- `Whetstone`
- `llmcuda-rs`

LMDeploy 文档明确列出 Turing/SM75，见
[安装说明](https://github.com/InternLM/lmdeploy/blob/dd4db0da274b17c751fbf49f3d3d1ba31a6df6fb/lmdeploy/docs/en/get_started/installation.md#L9)
和 [W8A8 矩阵](https://github.com/InternLM/lmdeploy/blob/dd4db0da274b17c751fbf49f3d3d1ba31a6df6fb/lmdeploy/docs/en/quantization/w8a8.md#L5-L13)。
vLLM 上游对 Turing 的通用 feature 支持较清楚，但具体 quantization/attention kernel 要逐项查。
SGLang、TGI 和稀疏 TRT-LLM 快照本身没有提供足以证明当前版本适配 SM75 的证据，不能仅凭
“能启动”或“某模型 smoke 通过”当作生产兼容。

### 6.2 双 2080 Ti vLLM 配方说明了什么

`qwen3-8-27b-dual-2080ti-vllm` 的硬件是改装 22 GB 卡、双 NVLink NV2、TP2、180K context、
`max-num-seqs=1`、chunked prefill、prefix cache、MTP3。它报告：

- W8A8 INT8 Tensor Core 比 FP8 权重路线 TTFT 低 19%-35%，prefill 高 24%-54%。
- 代价是部分 decode 变慢 11%-30%。
- `TRITON_ATTN` 在 d256、长上下文下远慢于 FlashInfer。
- Triton-Turing fork 在该服务的端到端热路径收益不超过 0.4%。
- W8A8 尚未做业务侧质量回归。

这组数据对本机有两个直接启示：

1. Turing 没有原生 FP8 Tensor Core，量化路线不能按 H100/Ada 直觉选择。
2. 编译器或 attention fork 的 microbenchmark 收益不等于端到端收益；热路径归属必须先测。

但它使用 TP2，而 Strata 是 layer split；它依赖 NVLink，而当前 Strata 文档强调无 NVLink/P2P
也可运行。不能把其并行策略和启动参数直接搬到 Strata。

### 6.3 `2080Ti-LLM-Toolbox` 与 `triton-turing`

Toolbox 把 SM75 配方、patch 和 benchmark 集中起来，报告了 AWQ、MTP K=3、FlashInfer/FA2 和
FlashQLA GDN 的组合。它的价值是“哪些路线值得试、哪些已经失败”的索引，不是可直接复制到
当前 Strata 的实现。

`triton-turing` 的 kernel 事实值得保留：

- 没有 `cp.async` 时，用多 stage 的 `ld.global -> st.shared -> bar.sync` 软件流水；
- 针对 64 KB/CTA shared memory 做 autotune；
- FP16 `m16n8k8`、INT8 `m8n8k16`；
- BF16 转 FP16 tensor core 是 opt-in，且会改变数值；
- microbenchmark 可能超过 cuBLAS，但端到端是否受益取决于热路径是否真的走 Triton。

当前 Strata 不应为了一个 fork 替换全部 kernel；应从逐层 profile 中找出真实热点，再单独做
parity 和 A-B-A。

### 6.4 小引擎和研究仓库的定位

- `PocketLLM` 只覆盖 Qwen3-0.6B/1.7B 等小模型，且服务端明确一次一个请求。
- `QwenForge` 只针对 Qwen2.5-0.5B，使用 2080 Ti 做统一评测，CUDA graph 和手写 kernel
  有学习价值，但只涉及单卡、小模型、贪心。
- `pyre` 有 paged KV、continuous batching、prefix cache 和 Triton attention，适合教学；
  没有根许可证，不能直接搬。
- `Whetstone` 适合读 Turing roofline、量化和 kernel 思路，不是 Strata 服务的替代。
- `llmcuda-rs` 是这批里最完整的 SM75 调度实验参考，但作者自己标为实验，不能冒充生产。
- `gpu-unlock` 不是推理引擎。它改的是显卡能力/时钟层面的行为，存在驱动和安全风险，不应
  作为软件调度优化的一部分运行。

## 7. 可借鉴项与优先级

### P0：先让 batch 形状可验证

#### 7.1 逐层 batch/single divergence 审计

借 `llmcuda-rs` 的 waypoint 思路，在 Strata 的两条路径上记录相同 hidden state：

- solo / 1 group / 2 groups；
- 1 slot、2 slots、6 slots；
- short decode、long prompt admit、prompt + decode 并发；
- expert resident、expert miss、CPU expert path。

每个 waypoint 至少分开：

1. mixer/GDN/attention 输出；
2. MoE gate/router 输出；
3. expert group reduction；
4. dense projection/head；
5. 最终 logits 与 greedy argmax。

先定位第一处越过可接受误差的位置，再决定是否修改归约顺序、Kernel 选择或 graph shape。
在这一点上，最终文本不同只是末端现象。

#### 7.2 建立可重复的 parity probe

现有 94/96-token 失败应升级为 fixture suite，至少包含：

- 固定短问答；
- 共享 system/tools 的两个子代理；
- 40K 长读加一个短问题；
- 两轮对话，第二轮命中前缀；
- `batch=1/2/4/6` 和 `groups=1/2` 的交叉；
- `pipeline-windows`、`PIPED_CONCURRENT`、`BYIELD` 开关交叉。

每个 fixture 记录 token ids、finish、首 token、最后 token、工具调用和 logits 差异，不只记录
文本相似度。

### P1：把“空 slot”改成“工作量 + 资源”

#### 7.3 请求工作量估计

当前 replica router 只用：

```text
cost = (prompt_tokens - matched_prefix_tokens) + 3000 * inflight
```

见 [serve/replicas.py](../../serve/replicas.py#L15) 和 [serve/replicas.py](../../serve/replicas.py#L359)。
这个公式简单、可解释，适合现有 replica 场景，但不适合直接套到 layer-split 的 slot/group
调度，因为它没有 stage wait、KV/state 水位、专家 miss 和剩余 decode 工作量。

建议把候选请求的特征定义成可测的 token 或毫秒：

```text
fresh_prompt_tokens        # 真正需要计算的 prompt token
cached_prefix_tokens       # 已经可恢复的前缀
state_restore_bytes        # GDN、QSA K/V、slot checkpoint 等
kv_and_state_headroom      # 剩余生命周期是否装得下
estimated_expert_misses    # 按层/专家 profile 估计
expected_decode_tokens     # 从客户端 max_tokens/历史形状估计，不做硬承诺
```

硬约束先于打分：上下文长度、state 容量、slot 容量、专家缓存和进程健康不满足时，不能因为
前缀得分高就准入。

#### 7.4 请求完整生命周期预留

vLLM 的 `scheduler_reserve_full_isl` 和 TRT-LLM 的 no-evict 都说明同一件事：只看第一轮预算
会把请求放进一个它永远无法完成的资源状态。Strata 需要至少报告并预留：

- 当前 slot 在整个 context 上的 KV/state；
- 长 prompt 完成前需要的 chunk 数；
- decode 到 `max_tokens` 的上界；
- 并发 slot 对专家缓存和 VRAM 的挤占。

这不是要求每次按最坏情况拒绝请求，而是要求：

1. 明确是“保证完成”还是“最大利用率”模式；
2. 如果采用最大利用率，必须说明可能暂停、重算、排队或拒绝的条件；
3. 排队和拒绝要在 HTTP 层有稳定语义，而不仅是日志。

#### 7.5 prefix-aware duplicate deferral

SGLang 与 LuceBox 都采用同一种做法：多个等待请求有相同前缀时，先让一个请求建立缓存，
其余短暂等待，不要同时重复读取相同 token。

这在 agent 负载中很可能比新增一个并发 slot 更有效，因为 subagents 常常共享同一段
system/tools。实现门槛：

- 只延后 follower，不饥饿它；
- leader 完成、失败、停顿或客户端离开时必须释放 follower；
- 共享前缀必须真的相同，不能只按文本近似；
- 延后时间必须计入请求排队指标。

### P2：让长读和多会话公平共享 stage

#### 7.6 短请求优先必须有老化上界

Strata 已有 `STRATA_ADMIT_SHORT_FIRST=2048` 和 30 s 老化，这是正确方向。下一步应报告：

- 短请求进入控制线等待的 p50/p95/max；
- 长请求被连续插队的次数；
- 长请求首次准入等待和实际让行次数；
- 短请求优先是否把长任务的整任务墙钟推过 1% 门槛。

不应只增加更多阈值。若现有 2048/30 s 在真实 1 主 + 子代理负载上没有明确收益，就保持原状。

#### 7.7 长 prefill 与 decode 的时间份额

LuceBox 的 0.5 time share 是一个实验起点，不是 Strata 常量。可在当前
`STRATA_BATCH_DECODE_SHARE`、`STRATA_PIPED_READ_PIECE` 和 `BYIELD` 之上做单变量矩阵：

- share 0、0.25、0.5；
- 短于 2048、4K、40K、127K 的冷读；
- 1、2、4 个 decode slot；
- 有和没有共享 prefix。

记录 decode 最长停顿、TTFT、总任务时间和两 stage busy 区间。只有 decode 不饥饿且长读的
总任务时间不退化，才可采纳。

#### 7.8 前缀和负载联合路由

如果将来启用 data-parallel replica，直接从 llmcuda-rs 迁移以下结构，而不是继续增加
`LOAD_TOKENS` 常量：

```text
score = prefix_fraction
      - queue_pressure
      - kv_utilization
      - resident_sequences
```

先按单位归一化：

- prefix 用匹配前缀 / prompt 长度；
- queue 用等待 token / 每步 token 预算；
- KV 用使用率；
- resident sequence 覆盖等待和运行；
- 不可准入的 replica 先硬过滤。

随后在本机重新拟合权重。任何 replica 路由优化都必须先验证“共享 system prompt 的并发请求
不会堆到同一张卡”。

### P3：观测与内核

#### 7.9 统一调度观测

建议的采样字段：

```text
request_id, kind, prompt_tokens, cached_tokens, fresh_tokens
slot, group, replica, assigned_stage
admit_wait_ms, stage_wait_ms, max_stage_gap_ms
prefill_tokens, decode_tokens, ttft_ms, finish
expert_hit_rate, expert_misses, kv_state_bytes, kv_watermark
gpu_busy_a, gpu_busy_b, handoff_ms
```

`gpu_busy` 不能只看平均利用率。还要看两个 stage 是否在同一个 10-50 ms 窗口都非空、哪一段
先空、空的是因为等待、KV、专家 miss 还是 host 工作。

#### 7.10 Turing kernel 继续守三条线

- 只在真实 SM75 卡上启用；
- bitwise 与非逐位改变分开开关；
- 每个 kernel 有 microbenchmark、模型端到端和真实 agent 任务三层证据。

当前 `STRATA_SM75_PERF=1` 已是本地已验证的正向路径，但这是 kernel 层收益，不应被写成
“动态调度”或“多卡 replicas”的收益。

## 8. 不建议迁移的机制

### 8.1 不建议移植整套 vLLM/SGLang/TRT-LLM 调度器

Strata 已有自己的 engine protocol、slot state、GGUF expert arena、layer split 和 MTP。
引入另一个框架的 PagedAttention/block manager 会同时替换 KV、模型 runner、cache、graph
和 batch 语义，风险远大于调度收益。

### 8.2 不建议做 PD 分离

Prefill/decode 分离适合多机、独立 prefill 池和可迁移 KV。两张消费卡当前已经共享
layer-split 和专家路径，再拆成两个 OS 进程只会增加 KV 传输、启动时间和故障面。LuceBox 和
DeepInfer 的阶段化思想可研究，不构成当前部署方案。

### 8.3 不建议直接套用 8 卡配置

`Strata-2080TI` 的 8 x 2080 Ti、PCIe-only、全专家驻留数据只适用于 8 卡机器。其
`parallel=4 + groups=4` 也不能根据 2 卡结果反推。当前两张 22 GB 卡的专家容量、上下文、
CPU 和 NUMA 条件不同。

### 8.4 不建议在没有质量回归时切换量化

W8A8、FP8、NVFP4 和 BF16 tensor-core 的可比重必须逐模型确认。尤其是：

- `STRATA_BF16_TC=1` 不是逐位路径；
- W8A8 配方作者明确说未做业务侧质量回归；
- FP8 KV 和压缩 KV 会影响长上下文、数学、代码和工具协议；
- 微基准的吞吐提升不能代替 agent 任务成功率。

### 8.5 不建议运行 `gpu-unlock`

它修改显卡能力/时钟相关内核行为，根目录也没有清晰的许可证文件。即使当前项目已有硬件解锁
历史，新的调度研究也不应把它作为变量。

## 9. 推荐实验顺序

### 阶段 0：维持 P0

先让 `GPU-XID13-STABILITY` 的 60 分钟真实混合曝光、完整质量、4/6/8 安全门继续。出现新 Xid，
暂停性能实验并保留现场。

### 阶段 1：正确性

1. 将 94/96-token parity 固化成 fixture。
2. 在 mixer、attention、MoE、reduction 和 head 各加 waypoint。
3. 比较 solo / one group / two groups / prompt+decode 并发。
4. 找出第一处差异，区分 graph capture、专家路径、归约顺序和采样。
5. 只有固定 greedy 和工具/finish 门通过后，才允许改变 group assignment。

### 阶段 2：只观测的 shadow scheduler

不改变实际路由，只对每个到达请求计算：

- fresh prompt tokens；
- prefix hit；
- KV/state 估算；
- expert miss 估算；
- group/stage 等待；
- 预计 decode tokens；
- 候选短/长队列和老化分数。

离线比较“当前分配”和“shadow 推荐分配”，确认推荐不是单纯偏向前缀热点、长请求或最新请求。

### 阶段 3：现有开关的 A-B-A

使用已经存在的机制，不先写新调度器：

- `STRATA_ADMIT_SHORT_FIRST=2048`；
- `STRATA_ADMIT_MAX_WAIT_S=30`；
- `STRATA_PIPED_CONCURRENT=1`；
- `STRATA_BATCH_DECODE_SHARE`；
- `BYIELD`；
- 必要时测试已有 `--batch-groups` 和短读让行。

同一源码、同一模型、同一 config、同一 runner 下做 A-B-A。每个臂至少覆盖：

1. 单请求短问；
2. 单请求长读；
3. 1 个 decode + 1 个短问题；
4. 1 个 decode + 1 个 40K/127K 长读；
5. 1 主 + 3 子代理的共享前缀；
6. 6/8 路安全回归，不把 6/8 当日常主负载。

### 阶段 4：时间份额和前缀 leader

只有阶段 3 找到实际空转或等待后再测试：

- 长 prompt 与 decode 的 time share；
- prefill chunk 后 decode 时间；
- duplicate prefix follower 延时；
- 两个 stage 的非对称 token slice。

每一项单独开关、单独回滚，不能把多个变量打包成一个“更智能”参数。

### 阶段 5：replica 路由

仅当以下条件全部满足：

- 完整模型能放进每个 replica 卡组；
- 两 replica 的 model 加载、RAM arena、上下文和专家 cache 都保持；
- 工具/输出在 replica 之间完全一致；
- 轮换 replica 不会破坏对话 checkpoint；
- 单 replica 失败时服务能恢复。

然后才测试两个单卡 replica、1/2/4 请求分配、共享 system prompt 的 burst，以及 queue/KV/
resident sequence 路由权重。当前大型 MoE 不应为了两卡并行强行选择这个模式。

## 10. 验收边界

任何动态调度候选至少同时满足：

1. **输出正确性**：固定 greedy token ids、finish、工具调用和 agent fixture 与基线一致。
2. **确定性**：同一配置至少在多次启动和重复请求中稳定；一次重启后的异常不能忽略。
3. **性能**：1 路和 2 路不退化；4 路不超过现有非回退界；6/8 路零错误、资源完整。
4. **公平性**：报告长请求等待、短请求 p95、第二 TTFT 和最长 stage gap，不能只看总 token/s。
5. **资源**：VRAM、RAM、swap、expert slots、KV/state 留在安全范围内。
6. **稳定性**：零新 Xid、零 engine 非预期退出、零槽位泄漏、恢复后可重复。
7. **可回滚**：默认路径保持关闭；删除开关即可回到原路径；配置与源码 hash 可核对。

“GPU 利用率更高”只能作为诊断指标，不能单独作为采纳条件。现有 A3 证据已经出现
2/4/6 路总吞吐上升、8 路下降的平台效应，见
[UTIL-BATCH-GROUP-FIT](UTIL_BATCH_GROUP_FIT_RESULT-20261031.md)：

| Streams | Aggregate median tok/s | TTFT median ms |
| ---: | ---: | ---: |
| 2 | 81.0 | 142.0 |
| 4 | 116.1 | 293.0 |
| 6 | 133.2 | 967.5 |
| 8 | 104.4 | 1926.0 |

这些是 2026-10-31 的本机历史基线，不是本轮的重新测量。它们说明继续增加并发并不自动增加
有效工作。

## 11. 针对当前问题的直接回答

### “当前多个会话时两个 GPU 都在做运算吗？”

不一定。

- 单会话：两张卡先后参与同一个 pipeline，不是持续同时满载。
- 2 groups 且有多个会话：group A 的后段与 group B 的前段可以重叠，两张卡可能同时忙。
- long prompt + decode：`STRATA_PIPED_CONCURRENT=1` 等机制可以让 prompt 和 slot 窗口共用
  GPU，但会争相同算力和内存带宽。
- 两个完整 replica：两张卡可以独立同时计算，但每个 replica 必须有完整模型和独立资源。

### “能不能把因 A 独占而长期等待的 B 调度到另一张卡？”

如果 A、B 属于同一个 layer-split 模型：不能完成“把 B 变成单卡会话”。可以把 B 放进不同
slot/group、缩短它的准入等待、让长读在 chunk 边界让行，或让两个 group 在两张卡之间流水
重叠。这是**调度 B 在另一张卡的工作时段**，不是把 B 的完整模型状态搬到另一卡。

如果 A、B 属于两个完整 replica：可以。需要完整模型副本、足够 VRAM/RAM、独立 engine 和
正确路由。`serve/replicas.py` 已有机制，但当前 router 仍只用 prompt 重读和固定
`LOAD_TOKENS * inflight`；大模型是否能在每张 22 GB 卡上完成独立副本必须先实测。

### “现实吗？”

现实的部分：

- 提高 stage 重叠；
- 短请求优先 + 显式老化；
- 长 prefill/decode 交错；
- 前缀感知的准入和 slot 选择；
- 完整 replica 下的 data-parallel 路由；
- 先 shadow、再 A-B-A 的可回滚实验。

暂不现实或不应直接做的部分：

- 在现有 layer-split 中把一个会话整体迁到另一张卡；
- 未解决 batch 形状差异就动态切换 group；
- 把 8 卡旧 fork 参数照搬到双卡；
- 以 GPU 利用率替代输出、TTFT 和整任务墙钟；
- 在 GPU Xid P0 未收敛时扩大并发和动态迁移。

## 对该批仓库的整体总结

这 31 个仓库共同说明，消费级双卡推理的主要瓶颈已经从“有没有一个能跑的 kernel”转成
“batch 形状、KV/state 预留、专家缓存、前缀复用和 stage 重叠能否被统一调度”。vLLM、SGLang、
LMDeploy、LuceBox 和 TensorRT-LLM 提供了成熟的公平性、预算、水位和抢占设计；DeepInfer
把 batch invariance 和按真实工作量调度提升为正确性问题；llmcuda-rs 则把这套思路落到 SM75，
并给出了逐层审计和完整模型 worker 路由的直接参考。Strata 的优势是已有适配 Qwen MoE 的
GGUF expert paging、slot state、layer split、MTP、BYIELD、prompt/decode 并发和可选的
data-parallel replicas，因此不应替换整套引擎，而应吸收这些机制并用本机证据重新定参。

RTX 20 系专项仓库进一步证明，Turing 不能按 Ampere/Ada 的性能模型推断：它没有原生 BF16
MMA、TF32 和 `cp.async`，FP8/INT4 路径也需要逐模型验证。现有 Strata 的
`STRATA_SM75_PERF=1` 已有真实正向 prefill 证据，但这些 kernel 收益不能被误写成调度收益；
W8A8、BF16 tensor-core 和第三方 vLLM 配方也必须经过业务质量回归。

对当前双卡问题，最稳妥的路线不是立即加入复杂的“会话搬卡”，而是先把 1 group 与 2 groups
的数值差异定位清楚，再 shadow 记录 fresh prompt、前缀命中、KV/state、专家 miss、stage
等待和 decode 剩余工作量；随后只用现有短请求优先、老化、BYIELD、prompt/decode 交错和
pipeline 能力做 A-B-A。真正的跨卡会话迁移应留给“完整模型能放进每张卡或每个卡组”的
data-parallel replica，而不是施加于当前 layer-split 大 MoE。整体上，这批仓库最有价值的
输出不是一套可以整体移植的调度器，而是一组边界清楚、可测量、可回滚的调度原则：先保证
相同输入产生相同输出，再为请求完整生命周期保留资源，最后才追求更高的双卡有效占用率。
