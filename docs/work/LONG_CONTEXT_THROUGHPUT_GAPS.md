# Strata 长上下文吞吐优化遗漏审查（2026-10-30）

**状态：分析记录及 2026-10-31 L0/显式 layer-major 执行记录；不是已实现的优化，也不是新的默认配置。**

本文审查 `freeze/server-adapt-0.2.7` 在真实服务器和长上下文负载下仍未被主线覆盖的吞吐
优化方向。逐项实现和已经测得的局部收益见
[`HANDOVER-2026-10-29-freeze-027-throughput.md`](../archive/HANDOVER-2026-10-29-freeze-027-throughput.md)。
本文只补充尚未形成主线的路线、证据强度和下一步实验边界。

## 1. 范围与证据口径

审查对象：

- 分支：`freeze/server-adapt-0.2.7`
- 审查基线：`e9ba6e56`
- GPU：2 x RTX 2080 Ti，每卡 22528 MiB
- 卡间链路：2 条 NVLink，每条 25.781 GB/s，P2P 可用
- 生产形态：`layer_split=24`、262144 上下文、int8 KV、32768 resident KV
- 真实负载：150K token 冷读、150K token 续读、210K token 会话恢复和 25% 位置改写

证据标记：

- **已观测**：来自当前代码、启动日志、阶段 profile 或已完成基准。
- **静态判断**：来自代码路径，尚未做端到端验证。
- **待验证**：方向合理，但收益、质量或内存边界尚不明确。

36K token 只用于低成本 kernel 筛选和回归，不作为长上下文端到端验收。

## 2. 当前阶段 profile

153017-token prompt 的候选服务阶段 profile：

```text
GPU timeline 55688 ms, wall 57052 ms, host staging 270 ms

embed+steps        4336 ms   7.8%
hc read            4640 ms   8.3%
gdn                3533 ms   6.3%
qsa proj           2186 ms   3.9%
qsa indexer           3 ms   0.0%
qsa select         3874 ms   7.0%
qsa attn           4122 ms   7.4%
router+shared      1084 ms   1.9%
host grouping       617 ms   1.1%
gather              382 ms   0.7%
wait copy           268 ms   0.5%
dequant            1712 ms   3.1%
gemm gate/up      12084 ms  21.7%
gemm down          6085 ms  10.9%
combine             906 ms   1.6%
ple                1370 ms   2.5%
kv stage            818 ms   1.5%
gdn conv+gates      666 ms   1.2%
gdn recurrence     4079 ms   7.3%
gdn out proj       1256 ms   2.3%
hc write           1666 ms   3.0%
```

由此得到的第一个结论是：**MoE 仍是最大单项，但长上下文 QSA selection 已足以和 attention
并列；继续只优化 attention 或单个 MMQ tile 的边际收益有限。**

代表性长上下文结果：

| 场景 | 结果 |
| --- | --- |
| 149151-token 冷读 | 2047.1 token/s，73.046 s |
| 208825-token 冷读 | 2311.1 token/s，92.335 s |
| 150K token、25% 改写 | 58.156 s，cached 7936，read 133283 |
| 210K token、25% 改写 | 85.376 s，cached 8192，read 192445 |
| 139292-token、25% 改写，32K 尺度锚点 | 45.890 s，cached 32768，read 106524 |
| 209028-token、25% 改写，32K 尺度锚点 | 76.267 s，cached 32768，read 176260 |

32K 尺度锚点已经证明 checkpoint 对齐有效，但 210K 改写仍重读 176260 token，说明会话恢复
还有独立的结构性成本。

## 3. 优先级矩阵

| 优先级 | 方向 | 当前证据 | 主要收益 | 主要成本或风险 |
| --- | --- | --- | --- | --- |
| P0 | 恢复 layer-major prefill | 启动日志和代码路径均已确认 | 减少专家重复 staging、gather 和短分组 GEMM | 内存、阶段 overlap 和输出舍入需验证 |
| P0 | 优化 QSA selection | profile 已确认随上下文增长 | 降低长上下文尾块 7%–10% 的单项成本 | 选择精度和 query 共享需要质量门 |
| P0 | 双卡共同计算同一 stage | 当前只用 NVLink 做 handoff | 可能并行当前最大的 MoE/dense 工作 | 架构改造和通信风险最高 |
| P1 | 恢复 piped concurrent | 活服务日志确认实际关闭 | 提高真实混合负载的聚合吞吐和邻居 TTFT | staged expert source 需要线程安全缓冲 |
| P1 | 继续 MoE 流水线优化 | MoE 已观测占 32.6% | 减少 launch 和跨 group 中间结果往返 | 必须先有 kernel timeline |
| P1 | K/V page copy-on-write | 会话快照和文档均确认当前未实现 | 降低 210K 快照拷贝、容量和 eviction | 引用计数和写入隔离较复杂 |
| P1 | 分离两 stage 的负载平衡 | 当前只有合并 profile | 找到 24/24 划分造成的 stage 等待 | 需增加按 stage 计时，收益尚不确定 |
| P2 | `STRATA_SPLIT_MTP_BATCH=1` | 其他机器有 3.3%–6.7% 收益 | 每 chunk 的 draft prefill 固定成本 | 本机未测，且会影响并发路径 |
| P2 | HC、GDN、embedding 热点 | profile 合计约 36% | 有进一步融合或减少往返的空间 | 已有部分实验失败，需要新算法依据 |

## 4. P0：被 pipe plan 关闭的 layer-major prefill

### 已观测事实

启动日志同时打印：

```text
layer-major prefill is on for this layer split
layer-major off for this prompt: a --prefill-pipe plan sets the chunks of this read
```

当前服务参数包含：

```text
--prefill auto --prefill-pipe 1 --batch 6 --batch-groups 2 --layer-split 24
```

代码入口：

- [`../../src/prefill/prefill.cpp`](../../src/prefill/prefill.cpp)：`force_chunk_ > 0` 时拒绝
  layer-major。
- [`../../src/program/generate.cpp`](../../src/program/generate.cpp)：`--prefill-pipe` 为整个
  prompt segment 规划固定 chunk，并调用 `sp.force_chunk(...)`。

149K 请求的阶段日志由约 7424-token chunk 组成，因此每条 prompt 大约 20 次经过全部 48 层；
209K 请求约 28 次。layer-major 默认 super-chunk 是 32768 token，同一层专家可以在一个更大
的分组中计算，专家经过次数约减少 4 倍。

### 为什么可能有价值

layer-major 不会减少 token 数和矩阵乘法的基本工作量，但会减少：

- 同一层专家在每个小 chunk 中被重复 staging；
- gather、分组表和短 MMQ launch 的次数；
- 小 expert group 无法充分利用 68 个 SM 的情况；
- 专家权重在 layer-major 模式下可以在更大的行集合上复用。

### 边界

- 专家求和及 MMQ 的行分组顺序会变化，不能假定输出逐位一致。
- layer-major 对 VRAM、pinned host 和两 stage super-chunk 大小有额外要求。
- 当前 layer-major 不支持 peer expert share 或 stage helper。
- `--prefill-pipe 1` 曾经是调优结果，但它可能早于 layer-major 成为多卡默认路径；现在需要
  重新测量，而不是继续沿用旧结论。

### 下一次受控实验

只改一个变量：从候选服务配置移除 `--prefill-pipe 1`，保持二进制、环境、模型和 batch 配置
不变。优先测：

1. 150K token 冷读；
2. 150K token、25% 续读；
3. 210K token 会话恢复；
4. 同时记录 VRAM、pinned RAM、super-chunk 大小、回退原因和 eviction。

如果日志没有进入 layer-major，应把具体拒绝原因作为结果保存，不再继续调相邻参数。

### 2026-10-31 执行结果：L0 被显存门槛否决

受控 A1 保持 `--prefill-pipe 1`，固定 seed 完成 150K/210K 六请求；B 只移除这一项。
A1 的 prompt 吞吐为：

| 场景 | A1 prompt tok/s | wall |
| --- | ---: | ---: |
| 150K cold | 2104.1 | 71.064 s |
| 150K warm suffix | 843.8 | 7.641 s |
| 150K rewrite25 | 2348.9 | 50.147 s |
| 210K cold | 2373.1 | 88.293 s |
| 210K warm suffix | 1097.7 | 11.529 s |
| 210K rewrite25 | 2313.0 | 76.400 s |

B 在首个请求即打印：

```text
layer-major off for this prompt: no VRAM for a super-chunk (888 MiB free)
```

默认开启路径保留 512 MiB 余量，现役 batch 与 expert cache 配置下连最小 super-chunk 都
无法分配。因此 L0 的“只移除 pipe 就会启用 layer-major”被判为否决；这不是 layer-major
计算性能的结论，B 没有完成任何性能样本。A2 已恢复 A 配置并通过 health。

随后验证了显式 `STRATA_PREFILL_LAYER_MAJOR=1` 的 96 MiB 余量策略、16K super-chunk 和
host residual。150K/210K 分别真实执行 10/13 个 super-chunk，但六个请求的 prompt 吞吐中位
从 `2208.55` 降到 `1892.9 tok/s`（−14.29%）。150K warm 的墙钟从 `7.641` 降到
`3.559 s`，同时 cached tokens 从 `142848` 增到 `147456`，是命中差异，不是 layer-major
的独立收益。该策略因此被否决，不再扫描 16K 邻近 target。

证据：`/opt/ai/logs/layer-major-l0-20261030T160244Z/`、
`/opt/ai/logs/lm-explicit-20261030T161831Z/`。

## 5. P0：QSA selection 的长上下文成本

### 已观测事实

153K profile 中：

```text
qsa proj    3.9%
qsa select  7.0%，约 3874 ms
qsa attn    7.4%，约 4122 ms
```

QSA 三部分合计约 18.3%。分 chunk 观察时，`qsa select` 从首块约 1% 增长到尾部约 10.4%；
它随上下文中的块数增长，而 attention 只在选定的固定宽度上工作。

### 尚未进入主线的方向

1. **相邻 query 的候选复用**：prompt 中相邻 token 的 indexer 选择可能高度重叠。若
   `STRATA_SEL_OVERLAP=1` 显示 16-query tile 的 union 明显小于逐 query 宽度之和，可以做
   分层候选、coarse-to-fine top-k 或 tile 内共享候选。
2. **FP16 indexer keys**：`STRATA_IDX_FP16_CHECK=1` 已能比较 FP16 keys 与 FP32 keys 的
   selected cells。Turing 没有 TF32，但支持 FP16 tensor core；如果选择集合一致，可用 FP16
   score 代替当前 FP32 tiled/SIMT scorer。
3. **score 与 top-k 融合**：当前 score 写入 `[queries, blocks]`，随后 top-k 再读。可研究
   分桶或分层选择，减少中间 scores 的全局内存读写。
4. **按实际 active blocks 细分网格**：现有路径已经把 grid 限制到 batch 最后一个 query
   的 active blocks；下一步应按 tile 内实际块数进一步减少空 warp 和尾块浪费。

### 质量边界

任何改变候选集合或数值精度的方案，都必须至少满足以下之一：

- 和原选择得到完全相同的 selected cells；
- 在真实 150K/210K prompt 上通过模型质量门；
- 能证明不同 cells 只发生在分数差距极小的边界，并由独立评估覆盖。

不能把合成 benchmark 的加速直接当作生产收益。

## 6. P0：NVLink 计算协同

准确地说，NVLink 并非完全没有参与。`STRATA_HANDOFF_P2P=1` 已经用于两 stage 的层间
handoff：325MB chunk 从主机路径约 53 ms 降到约 6.9 ms。限制是端到端只节省约 0.2 秒。

当前没有做的是让两张卡同时计算同一个 stage：

- 现有 layer split 是 pipeline parallelism；
- 同一时刻主要由持有当前层段的 GPU 工作；
- 另一张卡不能共同分担当前层的 hot/miss experts；
- layer-major 又明确拒绝 peer expert share。

可能的下一步按风险从低到高排列：

1. 为长 prompt 的 layer-major 增加同步 peer expert share；
2. 按专家放置做 expert parallel，远端 expert 只传 token 行并通过 NVLink 回传结果；
3. tensor parallel，把每层 GEMM 的输出维切到两张卡。

第一项仍然受 MoE 路由同步和输出求和顺序影响；后两项属于架构改造，必须单独立项，不应与
kernel 微调混在同一个提交中。

## 7. P1：piped concurrent 在真实服务中被关闭

活进程环境中有：

```text
STRATA_PIPED_CONCURRENT=1
```

但日志稳定显示：

```text
STRATA_PIPED_CONCURRENT is off beside an expert source that stages its blobs
```

当前 native GGUF/专家源会回收交给 prompt 的 blob buffer，因此只允许一个线程读取。代码路径
在 [`../../src/program/generate.cpp`](../../src/program/generate.cpp) 的
`ConcurrentReadSetup` 检查中关闭该功能。

这意味着：

- 单条长 prompt 的总时间不一定因此变差；
- 但在长 prefill 旁边有 decode 请求时，当前生产路径没有实现设计中的无 drain 并行；
- 真实服务的 aggregate throughput 和邻居请求 TTFT 仍有未兑现空间。

修复方向和单一环境变量无关。需要让 staged expert source 支持：

- 多个消费者可见的稳定 blob 生命周期；
- 读线程和 batch window 的独立缓冲所有权；
- 在未消费完时禁止回收；
- 错误、取消和 BYIELD 后仍能安全回收。

## 8. P1：MoE 深层流水线

MoE 当前占 32.6%，但现有数据已经否定了几个低成本方向：

- 64 专家组在完整 stream/gather 管线中没有稳定收益，16 组保持默认；
- `STRATA_MMQ_DIRECT=1` 需要特定 build 和额外 VRAM，当前没有稳定端到端收益；
- 继续调单个 MMQ tile 的空间已经很小。

因此下一轮需要先拿到 kernel/launch timeline，再决定实现。重点检查：

- 每层实际的 gate/up、SwiGLU、quantize、down launch 数；
- resident expert 的 tensor-core 计算是否真的和 miss expert copy 并行；
- `Dm` 是否必须先整体写回，能否把 down 输出和 router weight 的 combine 融合；
- 相邻专家组的中间结果能否保持在更小的 shared/register 工作集；
- layer-major 的 32K 行分组是否使现有的 16 专家上限和 stage ring 过小。

在这些问题有 timeline 证据前，不建议再写新的专家融合 kernel。

## 9. P1：210K 会话快照和 K/V copy-on-write

当前日志中：

```text
149K parked in 0.8–1.8 s，snapshot 2.75–3.35 GiB
209K parked in 1.2–1.4 s，snapshot 4.27 GiB
```

210K 场景还会因为 snapshot 加 2560 MiB floor 而驱逐已有会话。多个 snapshot 的
`reused_kv_bytes` 为 0，说明完整复制仍然常见。

[`../RESEARCH_RUNS.md`](../RESEARCH_RUNS.md) 已说明 copy-on-write K/V pages 没有实现：
每个 batch slot 自己保存 K/V，分支前缀会复制整段 K/V。对共享 system prompt、
sub-agent 和压缩会话，这会造成不必要的 RAM 和拷贝。

值得研究：

- K/V page 引用计数；
- 共享前缀只保存一份；
- 写入时只复制变化的 page；
- snapshot 复制与下一阶段计算重叠；
- 在 4 GiB 级 available RAM 下更准确的准入和 park 策略。

这不是简单调大 `--conversation-cache-mib`。当前 RAM 余量不足以靠扩容解决。

## 10. P1：两 stage 负载平衡和混合负载

当前固定 `--layer-split 24`，但阶段 profile 是两张卡合并后的结果。不同层的 QSA/GDN、
expert residency 和权重布局并不完全相同，24/24 可能不是长上下文的最佳切分。

下一步应分别记录：

- stage 0 和 stage 1 的 GPU busy 时间；
- stage 间 handoff 等待；
- 每 stage 的 miss expert 和 resident expert 数；
- super-chunk 边界上的空闲时间。

此外，现有基准主要是单请求。至少应补一个混合场景：一个 150K prefill 与一至两个 decode
同时运行，记录 aggregate token/s、长请求 TTFT 和邻居请求 TTFT。否则无法判断
`STRATA_PIPED_CONCURRENT` 关闭对真实服务吞吐的影响。

## 11. P2：其他未闭环方向

### MTP draft prefill

`STRATA_SPLIT_MTP_BATCH=1` 在其他双卡机器上测得 25K、51K、104K 分别约 `+6.7%`、`+4.3%`、
`+3.3%`。本机尚未验证。它会关闭 `STRATA_PIPED_CONCURRENT`，所以只适合作为受控 A/B，
不能据此默认打开。

### HC、GDN 和 embedding

153K profile 中：

- HC read + write 约 11.3%；
- GDN 四部分约 17.1%；
- embed + steps 约 7.8%。

这些不是当前第一优先级。GDN 已尝试增加并行度和 barrier，没有形成保留收益；HC 已做过
write+norm 融合。下一步若继续，应优先减少 norm、projection 和中间结果往返，不能重复
已经失败的同类实验。

## 12. 暂不优先的方向

- **KV prefetch**：153K profile 的 `kv stage` 只有约 1.5%，稳定读取也不是 NVMe 瓶颈。
- **`--kv k8v4`**：文档已记录 prompt 慢 2%–5%；没有显著换回 expert residency 时不值得。
- **native fused IQ 默认化**：36K 没有稳定端到端收益，应先等 layer-major 结果。
- **继续调 64 专家组**：完整管线没有稳定收益。
- **重复 `STRATA_MMQ_DIRECT`**：已有端到端中性或负面结果，且需要额外 VRAM。

## 13. 建议的新全局目标与验收

建议把下一阶段目标收窄为：

> 在不降低输出质量、不扩大内存风险的前提下，先恢复长 prompt 的 layer-major 调度，再降低
> QSA selection 的长上下文成本；随后根据 stage timeline 和混合负载结果，决定是否推动
> 双卡同层 expert parallel。每次只改一个变量，并保留可解释的代码注释和失败记录。

最低验收矩阵：

| 场景 | 指标 |
| --- | --- |
| 150K token 冷读 | wall、prompt token/s、GPU timeline |
| 150K token 续读 | cached/read token、TTFT、wall |
| 210K token 25% 改写 | cached/read token、wall、checkpoint 命中 |
| 210K 会话恢复 | snapshot 时间、eviction、reused K/V |
| 一个 150K prefill + 1–2 decode | aggregate token/s、长请求和邻居 TTFT |

同时记录：

- VRAM 峰值和每卡余量；
- host MemAvailable 最低值；
- expert cache hit/miss；
- layer-major、piped concurrent 和 helper 是否实际生效；
- 每个实验的完整环境变量和启动参数。

## 14. 推荐执行顺序

1. **layer-major A/B**：只移除 `--prefill-pipe 1`，测 150K 和 210K。
2. **QSA 诊断**：在真实 150K run 上打开 selection overlap 和 FP16 selection 检查。
3. **根据结果实施一个最小改动**：优先 QSA candidate 复用或 FP16 selection，不同时改
   MoE 和两卡架构。
4. **stage 时间线**：拆分 stage 0/1 后决定是否调整 `layer-split`。
5. **混合负载**：验证 `STRATA_PIPED_CONCURRENT` 的真实服务价值。
6. **架构评审**：只有前三步仍显示双卡计算利用率不足时，才进入 expert/tensor parallel。

## 15. 可实施的代码切入点

本节把候选方向拆成可以独立评审、独立回退的工作项。除 L0 外，以下项目都还没有实现，
不能引用为已有收益。

| ID | 工作项 | 首先交付什么 | 通过条件 | 停止条件 |
| --- | --- | --- | --- | --- |
| L0 | layer-major 配置 A/B | 同二进制的配置 A/B 和日志 | 150K/210K 有可复现收益，内存安全 | 回退、OOM 或收益低于噪声 |
| L1 | layer-major 与 pipe plan 的兼容决策 | 明确优先级和日志 | 默认路径不会自相矛盾 | 两种模式无法安全共存 |
| Q1 | FP16 QSA indexer 检查 | selected-cell 一致率 | 真实 prompt 上选择集合稳定 | 关键 cell 明显变化 |
| Q2 | QSA 候选复用或 score/top-k 融合 | 局部 benchmark 和真实选择对照 | 长上下文 select 明显下降 | 质量或代码复杂度不划算 |
| C1 | staged source 并发读 | 可证明的所有权和回收测试 | 邻居 TTFT 改善且无 stale blob | 线程安全无法证明 |
| M1 | MoE kernel/launch timeline | 每层 launch 和 overlap 数据 | 找出可量化的空泡或重复搬运 | 只剩硬件算力限制 |
| S1 | K/V page copy-on-write | 共享前缀快照的原型设计 | snapshot bytes 和 eviction 降低 | 引用/写入复杂度不可控 |
| P1 | 两 stage 独立计时 | stage busy 和 handoff 数据 | 找到阶段失衡 | 24/24 已平衡 |
| N1 | 同 stage 双卡 expert share | 设计文档或离线原型 | NVLink 通信后仍有净收益 | 通信抵消全部并行收益 |

### L0：先验证配置，不先改代码

当前最短路径不是实现一个新的 layer-major，而是让已经存在的路径真正运行。候选配置只移除
`--prefill-pipe 1`，不修改源码。日志必须从：

```text
layer-major off for this prompt: a --prefill-pipe plan sets the chunks of this read
```

转为实际的：

```text
strata prefill: layer-major (layers ...): ... tokens in ... super-chunk(s) ...
```

如果路径成功但速度不变，优先检查：

- super-chunk 是否被自动缩到 7424 token 左右；
- `R` 是否落在 host 而非 VRAM；
- 两 stage 是否因为最小可用 super-chunk 相互限制；
- expert 是否大多数已经 resident，导致重复 staging 本来就不是成本；
- host staging、dequant 和 `wait copy` 是否发生变化。

如果路径回退，保留 `why`，不要在同一轮继续改 chunk 或专家组大小。

### L1：若 A/B 有收益，再处理代码优先级

首选方案是配置层面移除 `--prefill-pipe 1`，因为改动最小、回退最简单。若维护者希望用户同时
开启两者时自动得到安全的默认行为，可考虑在
[`../../src/program/generate.cpp`](../../src/program/generate.cpp) 的 pipe plan 生成之前先询问
layer-major 是否可用：

- layer-major 可用时，不设置 `force_chunk_`；
- 显式关闭 `STRATA_PREFILL_LAYER_MAJOR=0` 时，保留 pipe plan；
- 日志明确说明选择了哪条路径及原因；
- 不删除 pipe plan 的 BYIELD、checkpoint 和 batch interleave 语义。

该改动会让执行路径变复杂，只有在 L0 证明收益后才值得做。

### Q1：FP16 indexer 的离线筛选

相关代码：

- [`../../src/prefill/prefill.cpp`](../../src/prefill/prefill.cpp)：现有
  `STRATA_IDX_FP16_CHECK` 钩子；
- [`../../include/strata/kernels/qsa_select.hpp`](../../include/strata/kernels/qsa_select.hpp)：
  score 和 top-k 接口；
- [`../../src/kernels/qsa_select_bench.cpp`](../../src/kernels/qsa_select_bench.cpp)：131K 级别的
  离线 scorer/top-k benchmark。

建议顺序：

1. 用合成 benchmark 确认 FP16 score 的理论速度和数值误差；
2. 用真实 150K prompt 检查 selected-cell 一致率；
3. 只在一致率达到质量负责人接受的门限后实现缓存 FP16 pooled keys；
4. 保持 FP32 路径可回退，不改变默认行为，直到长上下文 A/B 通过。

FP16 在 sm_75 上可以用 tensor core，但 2080 Ti 没有 TF32。不能把 sm_80+ 的 3xTF32 结论
直接套到本机。

### Q2：候选复用和融合

如果 `STRATA_SEL_OVERLAP=1` 显示相邻 query 的候选高度重叠，优先做 query tile 级候选，
不要先写一个全局近似算法。一个可管理的原型是：

1. 先用粗粒度 block score 选取候选区；
2. 在候选区内做精确 score；
3. 保留 top-k 的 tie break 和输出顺序；
4. 逐层比较 ids，不在只有 kernel timing 时声称质量不变。

如果 overlap 很低，转做 score 与 top-k 的中间表示融合，减少全局内存往返，不要强行共享
不存在的选择集。

### C1：staged source 并发

相关检查在 [`../../src/program/generate.cpp`](../../src/program/generate.cpp)：

```text
staged_source = srcp == &src && src.stages_blobs()
```

`concurrent_read_off()` 看到它后关闭 piped concurrent。实现时要先定义 buffer 所有权，而不是
直接去掉检查：

- prompt reader 拿到 blob 时增加 pin/lease；
- batch window 不得复用仍有 lease 的 stage slot；
- BYIELD、取消和异常路径必须释放 lease；
- 读线程不得触发隐式 `cudaFree` 或 device synchronize；
- 现有 `tools/batch_interleave_test.py` 和 `tools/piped_read_join_bench.py` 都要回归。

通过标准不仅是长 prompt 变快，还包括邻居 decode 停顿下降、输出不交叉污染、取消后没有
stale 或 double-free。

### M1：MoE 先测量再融合

现有阶段 profile 已把 MoE 分成 gate/up、down、dequant、gather、wait copy 和 combine，但
还缺少每层 launch 级时间线。先记录：

- 每层每种 projection 的 launch 数；
- 实际 grid 大小和 SM 占用；
- resident 与 streamed expert 的交错顺序；
- copy stream 与 compute stream 的重叠比例；
- 每个 group 的 rows/entries 分布。

如果发现 down 之后单独的 combine 造成大块读写，再考虑融合；如果发现 launch 和尾块占比
很低，继续改 tile 不会解决 32.6% 的主体。

### S1：K/V page copy-on-write

相关文件：

- [`../../include/strata/core/conversation_snapshot.hpp`](../../include/strata/core/conversation_snapshot.hpp)
- [`../../include/strata/core/session_save_reclaim.hpp`](../../include/strata/core/session_save_reclaim.hpp)
- [`../../include/strata/kernels/kv_stream.hpp`](../../include/strata/kernels/kv_stream.hpp)

原型先解决共享只读前缀，不先改所有 K/V 写入：

1. snapshot 保存 page 引用；
2. 新会话从同一前缀借 page；
3. 首次写某 page 时复制并切换到私有 page；
4. eviction 只减少引用，最后一个引用释放存储；
5. 保留 prefix borrow 的现有语义。

验证要同时覆盖 `tools/parking_test.py`、`tools/conversation_cache_parity.py` 和一个
两个会话共享 system prompt、随后分别改写的场景。

### P1 和 N1：双卡工作的边界

`P1` 只增加观测，风险最低；先确认 stage 0/1 是否确实不平衡。`N1` 才涉及计算分工。
如果直接做 `N1`，至少要比较三种方案：

1. 远端 miss expert 同步计算；
2. expert parallel；
3. tensor parallel。

每种方案都必须记录 NVLink 传输、同步等待、输出合并和显存额外占用。不能只报某次 GEMM
从一卡变成两卡后的局部时间。

## 16. 受控实验协议

当前服务启动和专家缓存加载成本高，实验应按“先筛选、后重启、最后一次只改一个变量”的顺序。

### 16.1 实验前

记录：

- `git rev-parse HEAD`；
- engine 二进制 SHA-256；
- 完整启动参数和环境变量；
- 模型、量化、expert profile 和 tokenizer；
- 两张卡的型号、驱动、VRAM、NVLink/P2P 状态；
- 运行中的 systemd、HTTP server 和 engine PID；
- 最近一次成功 health、模型加载时间和可用内存。

配置和日志中不得提交 API key、密码、私有地址或用户内容。

### 16.2 layer-major A/B（已完成，否决）

这项重启实验已经完成；保留原始设计以便复核：

| 字段 | 基线 | 候选 |
| --- | --- | --- |
| 二进制 | 不变 | 不变 |
| 环境变量 | 不变 | 增加 `STRATA_PREFILL_LAYER_MAJOR=1`、16K target、host residual |
| 模型和量化 | 不变 | 不变 |
| layer split | 24 | 24 |
| `--prefill-pipe` | `1` | 移除 |
| `STRATA_PREFILL_LAYER_MAJOR` | 默认开 | `1` |
| `STRATA_PREFILL_LM_TOKENS` | 默认 | `16384` |
| `STRATA_PREFILL_LM_R` | 默认 | `host` |

执行：

1. 150K cold；
2. 150K 25% rewrite；
3. 210K 25% rewrite；
4. 210K 会话恢复；
5. 如果冷读和改写方向相反，保留两个结果，不取平均掩盖。

判定：

- wall 和 token/s 同方向改善，且日志证明进入 layer-major，才算候选有效；
- wall 改善但 TTFT 恶化，要按真实交互决定；
- VRAM 余量低于安全阈值、OOM、host ram 触发 paging 或 snapshot 被驱逐增多，直接停下；
- 若没有收益，检查 `why` 后结束这条路线，不连续试 16K/24K/28K 多个 super-chunk。

### 16.3 QSA 诊断（已完成）

layer-major 决策已经结束；QSA 诊断使用真实 150K prompt，不先用 36K 得出结论。

一次 run 同时记录：

- `STRATA_SEL_OVERLAP=1` 的 16-query union 比例；
- `STRATA_IDX_FP16_CHECK=1` 的选择一致率和差异 cells；
- `qsa select` 每 chunk 的绝对时间和占比；
- 尾部 block 数、active blocks 和 selection width。

如果 overlap 高且 FP16 一致，再实现对应 kernel；如果二者都否，转向融合 score/top-k 或
kernel tiling，而不是继续重复相同检查。

结果见 [`QSA_DIAGNOSTIC_RESULT-20261031.md`](QSA_DIAGNOSTIC_RESULT-20261031.md)。真实 150K
prompt 的 overlap 中位为 `28.0%`，但 FP16 全选择一致率只有 `70.9207%`，因此半精度 scorer
否决；候选共享按预注册公式有 `1.996%` 乐观上界，进入一次 exact attention-side K/V 共享读取
实现。final-selection union 只能说明 attention 读取有共享机会，不能用于跳过或近似 indexer
score/top-k。

### 16.4 QSA-UNION-ATTN（已完成，否决）

最小候选只合并相邻 query 对已选 K/V cell 的读取，保持每个 query 自己的 selection、softmax
和输出算术。必须同负载 A-B-A，wall 与 `qsa attn` 稳定同向改善 ≥1%，selection/attention parity、
needle 和资源门通过；否则回滚并转向 UTIL-NCU。

2026-10-31 已完成一次低风险实验：把三个相邻 query 的独立计算放进同一个 384-thread CTA，
共享同一 SM cache，而不做 union 排序或跳过读取。编译资源仍为 168 registers/thread、无
stack/local，输出 hash 不变；但普通选择慢 `20.05%`，`QA_SAME=4` 的完全共享上界仍慢
`20.75%`。该设计已否决，不进入长请求，也不再重试同形 cache co-residency。细节见
[`QSA_UNION_ATTN_RESULT-20261031.md`](QSA_UNION_ATTN_RESULT-20261031.md)。

### 16.5 UTIL-NCU（已完成）

完整请求的 full-section NCU replay 在 `98,329` token prompt 的最终 verify window 等待超过
60 秒，触发引擎 stall watchdog，没有生成报告。改用候选二进制、真实 GGUF 权重和
`G=64, T=6` 的 mode-8 微内核后，报告在数秒内完成。`native_gu_fused_kernel<18>` 的
MIO throttle 为 `29.75%`，long scoreboard `17.50%`，Tensor `0.04%`，SM `58.07%`。

因此下一候选只处理 shared-memory/codebook bank conflict，不恢复 Tensor 路线，也不把
微内核数字扩大为端到端收益。结果和预注册见
[`UTIL_NCU_RESULT-20261031.md`](UTIL_NCU_RESULT-20261031.md)、
[`UTIL_KERNEL_CONTRACT-20261031.md`](UTIL_KERNEL_CONTRACT-20261031.md)。

### 16.5.1 UTIL-KERNEL（已完成，否决）

四副本 rotated IQ3_XXS codebook 在真实权重层 36、`G=64, T=6` 上进行了 7 组交替进程。
输出 hash 全部保持 `9a055a5a94e15c87`，但 gate/up 中位从 `474.1` 变为 `474.5 us`
（−0.084%），candidate 仅胜 2/7。NCU 计数进一步显示 bank conflicts
`7,115,366 → 7,293,965`，shared-load wavefronts `17,698,406 → 17,877,005`，寄存器
73→80。随机 codebook 访问下复制没有减少真实冲突，反而增加了 staging 和资源成本。

该机制关闭，不扫描 2/8/16 副本。同一次 counter 还测得 shared-load instructions
`10,583,040`，global-load instructions 仅 `296,960`；下一候选改为每个 k 显式装入一次
q8_1 activation，并让 gate/up 两个 row dot 复用。详见
[`UTIL_KERNEL_RESULT-20261031.md`](UTIL_KERNEL_RESULT-20261031.md) 和
[`UTIL_ACT_RELOAD_CONTRACT-20261031.md`](UTIL_ACT_RELOAD_CONTRACT-20261031.md)。

### 16.5.2 UTIL-ACT（已完成，否决）

显式把同一 q8_1 activation 装入寄存器一次、供 gate/up 两行复用，保持 hash
`9a055a5a94e15c87`；但七组 gate/up 中位为 `475.2` 对 baseline `475.1 us`（−0.021%），
仅 3/7 胜。NCU 证明两臂都执行 `10,583,040` 条 shared-load 且都用 73 registers，
wavefronts 也相同，因此编译器本来就已复用同一 activation load。

下一机制不再搬 activation，而是复用 weight-side decode：同一 k 的 codebook words、
`aux32` 和 scale 目前会在 NTC chunk 的每个 token 上重复解码；候选在每个 k 只解码一次。
详见 [`UTIL_ACT_RESULT-20261031.md`](UTIL_ACT_RESULT-20261031.md) 和
[`UTIL_WEIGHT_REUSE_CONTRACT-20261031.md`](UTIL_WEIGHT_REUSE_CONTRACT-20261031.md)。

### 16.5.3 UTIL-WEIGHT（已完成，否决）

显式复用每个 k 的 weight-side decode 得到 `473.2` 对 `475.8 us`（改善 `0.546%`），
6/7 组胜出，但未过预注册 1%。NCU 显示两臂 shared/global load instructions 分别为完全相同的
`10,583,040/296,960`，wavefronts 与 bank conflicts 也基本不变；寄存器从 73 升到 80。
因此这是源代码层面的重复，NVCC 已经完成 CSE，不能作为稳定机器级收益采纳。

同时找到首版四副本 bank rotation 的具体缺陷：`4*index+replica` 每个 replica 只能覆盖八个
bank。下一候选固定四副本并使用 stride 257，让 `index+replica (mod 32)` 覆盖全部 32 banks。
详见 [`UTIL_WEIGHT_RESULT-20261031.md`](UTIL_WEIGHT_RESULT-20261031.md) 和
[`UTIL_GRID_PAD_CONTRACT-20261031.md`](UTIL_GRID_PAD_CONTRACT-20261031.md)。

### 16.5.4 UTIL-GRID-PAD（已完成，否决）

四副本 codebook 改为 `replica * 257 + index` 后，每个 replica 覆盖全部 32 个 shared banks，
输出 hash 仍为 `9a055a5a94e15c87`；但七组交替进程的 gate/up 中位为 baseline `475.7 us`、
candidate `489.3 us`（−2.86%），candidate 仅 3/7 胜。剔除第一组冷时钟离群后仍慢约 1.2%。
因此时间门之后没有运行 NCU 或 engine A/B。

源码和测试二进制已从快照恢复，parity 均 0 failure。候选停机 12 秒、恢复 loaded health
161 秒，总中断约 `2:53`。结果表明“每个 copy 覆盖 32 banks”并不能抵消每访问选择 copy、
复制四份 codebook 和地址运算的成本；不再扫描副本数或邻近 stride。结果见
[`UTIL_GRID_PAD_RESULT-20261031.md`](UTIL_GRID_PAD_RESULT-20261031.md)。

下一项 `UTIL-WEIGHT-RO` 改用独立的 long-scoreboard 证据：当前 SASS 中 weight 块读取为
`LD.E.U16.SYS`，只读参数为 `LDG.E.CONSTANT.SYS`。候选只把 fused IQ3 gate/up 的 weight
`qs`、aux/sign word 和 scale 改走显式只读路径，先检查机器码，再过 1% 时间与 counter 门。
契约见 [`UTIL_WEIGHT_RO_CONTRACT-20261031.md`](UTIL_WEIGHT_RO_CONTRACT-20261031.md)。

### 16.5.5 UTIL-WEIGHT-RO（已完成，否决）

显式只读加载真实改变了 SASS：默认 56 条 `LD.E.U16.SYS`，候选产生 48 条
`LDG.E.U16.CONSTANT.SYS`；两臂输出 hash 相同，默认/候选分别为 73/80 registers 且无 stack。
但七组 gate/up 中位仅从 `472.6` 降到 `471.0 us`（+0.339%），candidate 6/7 胜，未达 1%。
因此没有运行 NCU 或 engine A/B，源码和测试二进制已恢复。

候选停机 8 秒、恢复 loaded health 162 秒，总中断约 `2:50`。结果说明 fused gate/up 的
load-policy 微调仍是正向但不足；不扫描 cache operator、row-pair、NTC 或邻近几何。结果见
[`UTIL_WEIGHT_RO_RESULT-20261031.md`](UTIL_WEIGHT_RO_RESULT-20261031.md)。

下一项 `UTIL-PIPED-MTP` 转为用户可感知的并发凹点。v0.2.7 已有保留两组流水且每槽 MTP
草稿的 `--batch-mtp` + `STRATA_PIPED_MTP=1`，双卡 A6000 文档记录 2 路 `+16.0%`、4 路
`+10.2%`；当前 2080 Ti 六槽尚未开启。必须保留 6 slots / 2 groups，按 A1-B-A2 测 2/4/6 路，
并守住输出、health 和 RAM/VRAM 门。契约见
[`UTIL_PIPED_MTP_CONTRACT-20261031.md`](UTIL_PIPED_MTP_CONTRACT-20261031.md)。

### 16.5.6 UTIL-PIPED-MTP（已完成，否决）

六槽、两组的 B 配置真实启用了 `--batch-mtp` 和 `STRATA_PIPED_MTP=1`，启动日志与 metrics
都证明 drafters 和流水路径生效。A1/A2 合并的 2/4/6 路中位为
`78.3/108.55/125.7 tok/s`，B 为 `93.9/104.4/113.4`：2 路改善 `19.92%`、5/6 胜，
但 4/6 路回退 `3.82%/9.79%`。固定 greedy 的自然语言探针还在 B 内生成两次不同文本，
因此候选同时失败高并发和输出门。

B 的 CUDA1 expert cache 为 `7040` slots，A 为 `7209`，总数为 `17104` 对 `17274`。
上游文档说明只有同 expert cache 时才能做文本等价比，且 CPU/GPU 不同舍入会改变末位 token。
因此下一项不扫描草稿深度、槽数或组数，而是只恢复 A 的 primary/CUDA1 计数，先验证 parity。
若 parity 仍失败即结束该分支；若通过，再确认 4/6 路回退是否来自 cache 挤压。

两次切换分别停机 8/9 秒，启动到 loaded 约 181/232 秒。A 已恢复，engine hash
`105a4171...`、health loaded、6 slots / 2 groups、production systemd 不变。结果见
[`UTIL_PIPED_MTP_RESULT-20261031.md`](UTIL_PIPED_MTP_RESULT-20261031.md)，契约见
[`UTIL_PIPED_MTP_CACHE_CONTRACT-20261031.md`](UTIL_PIPED_MTP_CACHE_CONTRACT-20261031.md)。

### 16.5.7 UTIL-PIPED-MTP-CACHE（已完成，启动门否决）

候选只把 B 的 `--expert-cache auto` 替换为 `10065`，并追加
`--expert-cache-device1 7209`。源码与引擎实际行为表明 device1 是额外 helper GPU 的 cache，
不是 layer-split 第二 stage 的槽位上限。双卡都已经承担 stage，没有第三块 helper GPU，因此
引擎在读取权重前报 “needs a GPU that runs no stage” 并以 exit 2 退出。

候选从未 loaded，所以没有 parity、吞吐或显存驻留比较。A 已恢复，6 slots / 2 groups、
`expert_slots=17274`、health loaded；停机 8 秒，至 A 恢复 loaded 的总中断为 `3:47`。

下一步 `UTIL-PIPED-MTP-ORDER` 不强行改 cache，只给原 B 加现有
`--expert-cache-cpu-order`。它测试 GPU expert 按 CPU 累加顺序计算后，expert 驻留位置是否
不再改变 greedy 输出。仍然先过逐字 parity，再允许性能测量。结果见
[`UTIL_PIPED_MTP_CACHE_RESULT-20261031.md`](UTIL_PIPED_MTP_CACHE_RESULT-20261031.md)，契约见
[`UTIL_PIPED_MTP_ORDER_CONTRACT-20261031.md`](UTIL_PIPED_MTP_ORDER_CONTRACT-20261031.md)。

### 16.5.8 UTIL-PIPED-MTP-ORDER（已完成，parity 门否决）

候选只给原 B 加 `--expert-cache-cpu-order`，仍以 6 slots / 2 groups loaded，engine hash、
health 和槽数均正确。`STRATA_PARITY_ALPHA` 与算术结果 `944` 两个探针可重复且等于 A；自然
语言探针两次仍是不同文本，并与上一轮 B 逐字相同。固定质量门失败后没有跑候选吞吐。

恢复 A3 的 2/4/6 路聚合中位为 `81.0/116.1/133.2 tok/s`，TTFT 中位
`142.0/293.0/967.5 ms`，所有窗口外来请求为 0。合法早停均为 `finish_reason=stop`，已计入
各自的 token 总数。A→候选启动和候选→A4 恢复分别耗时至 loaded `146/171` 秒，总中断
`6:51`。结果见 [`UTIL_PIPED_MTP_ORDER_RESULT-20261031.md`](UTIL_PIPED_MTP_ORDER_RESULT-20261031.md)。

下一步 `UTIL-BATCH-GROUP-FIT` 不改模型算术，只比较当前两组与一组调度。A3 先补 8 路；
candidate 先过相同 parity，再同测 2/4/6/8 路并恢复 A4。2 路改善至少 1% 且 5/6 胜，
4/6/8 路不得回退超过 1%。若收益只在低并发出现，固定一组的配置仍否决，并保留实测
crossover 作为 concurrency-aware 调度的预注册依据。契约见
[`UTIL_BATCH_GROUP_FIT_CONTRACT-20261031.md`](UTIL_BATCH_GROUP_FIT_CONTRACT-20261031.md)。

### 16.5.9 UTIL-BATCH-GROUP-FIT（已完成，parity 门否决）

A3 的 2/4/6/8 路聚合中位为 `81.0/116.1/133.2/104.4 tok/s`，TTFT 中位为
`142.0/293.0/967.5/1926.0 ms`。8 路低于 6 路，说明真实并发平台存在；8 路三次为
`103.4/115.4/104.4`，draft 接受率 `75.48%`，没有外来请求。

候选只把 `--batch-groups` 从 JSON 字符串 `"2"` 改成数字 `1`，深拷贝恢复后与 A 完全相等。
B 正常以 6 slots / 1 group loaded，但两次自然语言 greedy 输出同一段 94-token 文本并以
`finish=stop` 结束；A 可重复生成不同的 96-token 文本并以 `finish=length` 结束。parity 门
失败后没有运行候选吞吐，也不能把历史 4 路回退写成新的候选实测。

这次失败推翻了“只改 group 调度不改模型算术”的假设。A 已恢复，6 slots / 2 groups、health
loaded、engine/config hash 正确；A→B 和 B→A 分别停机 8/9 秒，启动至 loaded
`206/180` 秒，总中断 `3:10`。结果见
[`UTIL_BATCH_GROUP_FIT_RESULT-20261031.md`](UTIL_BATCH_GROUP_FIT_RESULT-20261031.md)。

下一步先短路验证 `UTIL-EXPDEC-GU`：候选只增加 `STRATA_EXPDEC=1`，启动日志必须证明两张卡的
gate/up 都进入 expdec 路径，down 保持默认；真实路径接管并通过冻结 parity 后，才允许做
2/4/6/8 路 A-B-A 和端到端 1% 门。

### 16.5.10 UTIL-EXPDEC-GU（启动前路径否决）

当前 A 的进程环境和 config 都固定 `STRATA_EXP_MODE=8`，进程里没有 `STRATA_EXPDEC`。部署
源码在 mode 8 下先由 `native_gu_fused_kernel<18>` 或 mode-8 LDS gate/up 完成 gate/up，
`expdec_gu()` 位于这些分支之后；down 走 `native_down_lds_kernel` 后直接 return，
`expdec_down()` 也不可达。因此只增加 `STRATA_EXPDEC=1` 不可能让两卡的目标 EXPDEC 内核开始
执行。

本轮没有构造候选、重启、运行 parity 或吞吐；结论属于源码控制流的静态路径门，不是 EXPDEC
性能负结果。原始证据为
`/opt/ai/logs/util-expdec-gu-static-20261030T2008Z.txt`
（sha256 `bc3ac9bfbbddc2f153118d984e62b296a77895e1a1ad0eb6c9cd2e35232c32b3`）。下一步转向
8 路流水固定成本，优先检查最后不足窗口、重复 handoff 和每请求约 183 engine tokens 的来源。

### 16.5.11 UTIL-SLOT-CKPT-MOVE（已完成，吞吐门否决）

A 已启用 self-commit、direct slot copy、batch sync 与 checkpoint pool/threads。唯一新增候选
`STRATA_SLOT_CKPT_MOVE=1` 把准入 turn checkpoint 移入槽位而非复制约 112 MiB。它正常以
6 slots / 2 groups loaded，三组 greedy 探针两次均与 A 逐字相同，说明该路径保持观察到的
token 选择。

同周期 A3/B2/A4 的 2/4/6/8 路候选中位为 `81.5/110.9/123.1/107.8 tok/s`。相对 A3/A4
合并中位，候选为 `+3.099%/+2.259%/+0.122%/-3.014%`，胜场 `4/6, 3/6, 3/6, 2/6`；
没有一级同时达到 1% 与 5/6，8 路明确回退，2/4/6 的 A-A 漂移还达到
`8.944%/6.166%/7.055%`。A 已恢复，不扫描 checkpoint/copy/cache 邻近设置。

结果见 [`UTIL_CKPT_MOVE_RESULT-20261031.md`](UTIL_CKPT_MOVE_RESULT-20261031.md)。下一步把
8 路尾部拆到 one-row GDN batch 与流水窗口状态，寻找减少 scalar/barrier/launch 序列但保持
每行算术的新机制。

### 16.5.12 UTIL-GDN-GROUPS（吞吐/漂移门否决）

`STRATA_BATCH_ATTN_ROWS=1` 在 `kv_mode=1` 的 streaming 实例不可达，因此没有作为候选。
可达的 `STRATA_BATCH_GDN_GROUPS=1` 还缺少与 A 已启用 self-commit 的状态写回配合；补上
keep pointer 和最终 state 写回后，1/4/8 组输出、每槽 state 与三组冻结 greedy 探针全部
bitwise 等于 A。

同 boot A3/B/A4 的 2/4/6/8 路候选为 `77.8/102.2/121.6/106.7 tok/s`。相对 A3/A4 合并
中位为 `+0.777%/-8.011%/+0.913%/-1.386%`，胜场 `2/6/2/6/3/6/2/6`；没有一级同时达到
1% 与 5/6，4 路回退超过 1%，所有级别的 A3/A4 漂移都超过 3%。A 已恢复，不继续扫
GDN group 数或同类 launch 聚合。

结果见
[`UTIL_GDN_GROUPS_RESULT-20261031.md`](UTIL_GDN_GROUPS_RESULT-20261031.md)。下一步先做
`UTIL-WINDOW-PROFILE`：用现有 stage stamps 和 batch telemetry 拆分 active rows、stage
hand-off、streamed KV resolve、slot turnover 与每请求 ITL/TTFT，再选择新的源码机制。

### 16.5.13 UTIL-WINDOW-PROFILE（完成，定位 8 路 underfilled tail）

A1/P/A2 各跑 2/4/6/8 路、每级 3 波。P 只增加 `STRATA_VERIFY_PROFILE=1` 与
`STRATA_BATCH_TELEMETRY=1`。P 的绝对吞吐比 A 低 3.6–7.6%，所以只用其中同 run 的分布。

P 的 6/8 路为 `113.352/103.373 tok/s`。8 路平均占用 `5.010`，6 路为 `5.333`；
slowest/median tail 从 `1.0026` 升到 `1.1349`，最大 slot wait 从 `1,859 ms` 升到
`12,596 ms`。BSTAT active rows/window 从 `2.9515` 降到 `2.6837`，padding 反而更低；
按活跃行计 stage 1/2 成本升 `4.70%/4.54%`。KV resolve 为 `0.000 ms`。

结论是六槽队列结束后的 underfilled pipeline，而不是 padding replay 或流式 KV resolve。
下一步候选为 `UTIL-BATCH-HEAD`：两个 pipeline group 在 final stage 各自跑一次 head，
把 ready rows 合并后可一次读 output projection，理论上有 1%+ 空间。
契约见 [`UTIL_BATCH_HEAD_CONTRACT-20261031.md`](UTIL_BATCH_HEAD_CONTRACT-20261031.md)。

### 16.5.14 UTIL-BATCH-HEAD（源码门否决）与 UTIL-HEAD-SPLIT

后续源码审计否定了“两个 group 同时 ready”的前提。每个 stage 只有一个 verifier，末级的
`head_mixed_`、`head_logits_` 和 `m_out_` 都只有一套；`stage_group` 每次只绑定一个 group，
`batch_poll()` 返回后 scheduler 才释放末级。等齐两组会延迟前一组 token，在二组流水里制造
至少一个 `16.9–19.2 ms` 窗口空窗，而合并只可能省约 `1.04 ms`，因此没有构建或重启。

新的 `UTIL-HEAD-SPLIT` 沿 vocabulary 维拆分 output projection。两张卡各持有一半权重（总
显存不高于当前完整 head），由独立 head stream 并发计算，再按 vocab offset 组合 logits；
group verdict 时序不变。第一道门是独立 GPU harness：半区 logits、greedy、固定种子采样与
draw counter 必须和 full head `memcmp` 相等，accepted path 实测投影端到端至少 1% 后才接
server。契约见 [`UTIL_HEAD_SPLIT_CONTRACT-20261031.md`](UTIL_HEAD_SPLIT_CONTRACT-20261031.md)。

### 16.6 并发和会话快照

这两项放在单流路径稳定后。并发测试至少要有：

- 1 个 150K prefill；
- 1 个短 decode；
- 2 个短 decode；
- 邻居 TTFT、长请求 TTFT、aggregate token/s；
- `STRATA_PIPED_CONCURRENT` 实际是否生效的日志。

会话快照测试至少要有：

- 两个共享长前缀的会话；
- 一个 branch 改写 25%；
- 一个 210K snapshot；
- eviction 前后 `reused_kv_bytes`；
- 恢复输出与未 park 路径一致。

## 17. 质量门与回滚

### 调度和并发改动

layer-major、checkpoint、batch interleave 和 COW 不应悄悄改变会话状态。验收包括：

- 同一 prompt 的检索答案和关键 token；
- 续读路径与不中断路径的输出比较；
- 长上下文 needle/recall；
- 取消、BYIELD、断连后的状态一致性；
- 现有单元测试和 parity 测试。

### 数值近似改动

FP16 QSA、候选复用或 fused kernel 需要比调度改动更严格的门：

- selected ids 完全一致是首选；
- 不能完全一致时，给出差异比例、分数 margin 和受影响的层；
- 真实任务评估覆盖 retrieval、代码和多轮会话；
- 若只有合成数据收益、真实任务不稳定，不保留默认路径。

### 必须回滚的情况

- 启动时间明显增加且没有可解释收益；
- 150K 或 210K OOM、host paging 或 eviction 爆炸；
- 邻居 decode 的 p99 停顿变差；
- 输出质量门失败；
- 路径回退到 chunk-major 但日志仍被误报为 layer-major；
- 性能收益小于同机重复噪声且需要扩大复杂度；
- 代码注释无法说明优化原理和适用边界。

## 18. 证据清单和复现信息

本轮审查使用的代表性证据：

| 证据 | 用途 |
| --- | --- |
| `strata-verify.log` | 153K 阶段 profile、layer-major 关闭原因、piped concurrent 关闭原因 |
| `strata-long-context-baseline.json` | 150K/210K 冷读和改写基线 |
| `strata-long-context-p2p.json` | NVLink handoff 对端到端的影响 |
| `strata-long-context-fresh-140k.json` | fresh 140K 对照 |
| `strata-long-context-scaleanchors-140k.json` | 32K checkpoint 尺度收益 |
| `strata-long-context-scaleanchors-210k.json` | 210K checkpoint 尺度收益 |

当前候选服务的关键环境开关：

```text
STRATA_SM75_PERF=1
STRATA_STAGE_TRIM=1
STRATA_BF16_TC=1
STRATA_HC_Q8=1
STRATA_MTP_STAGE0=1
STRATA_PIPED_CONCURRENT=1
STRATA_SELECT_SIMT=1
STRATA_HANDOFF_P2P=1
STRATA_PIPE_BYIELD_NODRAIN=1
STRATA_ROUTE_TAIL_SKIP=0
```

这些开关是当前观测条件，不是对其它硬件的推荐默认值。每次实验必须重新确认它们没有被
其它 wrapper 或 systemd 覆盖。

## 19. 结论

layer-major 的调度收益假设没有通过端到端验证：容量策略成功打开路径，但当前 16K
host-residual 配置整体慢 14.29%。attention v2 已按 1% 门复验为当前默认。长 prompt 的下一个
高信息量热点是 QSA selection：先用真实 150K prompt 读取 overlap 与 FP16 一致性，再决定
是否实现候选复用或半精度选择。

本文没有修改引擎代码，也没有改变生产配置。P6 复验在候选 8080 上重启两次，生产
`strata.service` 始终未触动。
