# Strata 冻结版吞吐优化交接（2026-10-29）

## 1. 当前目标与边界

当前全局目标（2026-10-30 更新）是在 `freeze/server-adapt-0.2.7` 上，以真实生产硬件和会话负载
提高 Strata 的吞吐。验收基线是 2 x RTX 2080 Ti（每卡 22528 MiB）、两卡之间 2 条 NVLink
（每条 25.781 GB/s，P2P OK）、生产 `layer_split=24`、262144 上下文、int8 KV、32768 resident KV
和会话缓存配置。

必须覆盖以下负载，36K 只作 kernel 筛选，不能作为端到端验收：

- 150K token 长上下文冷读（全量 prefill）；
- 150K token 会话续读（前缀缓存命中后的增量 prefill）；
- 210K token 压缩会话恢复（checkpoint、局部重写和部分前缀重读）；
- 小规模回归，防止优化损害常规短请求。

优先定位专家流式加载、QSA/KV、会话 checkpoint 和分层 handoff 的真实瓶颈，并验证当前双 NVLink
能否通过 P2P 层间传输带来可测收益。优先采用可解释的最小改动，用定向构建、位级回归和受控 GPU
基准验证，并尽量少重启服务。

运行约束：

- 不修改生产 `systemd`、生产配置或生产运行目录。
- 服务启动约 3 分钟，专家缓存加载还需数分钟；没有明确假设和低成本指标时不要重启。
- 保留工作树内已有未提交修改，不重置或覆盖其他工作。
- 每次代码优化必须在改动处写注释，明确说明优化了什么、为什么可能有效、适用的硬件/形状/布局边界；
  有测量时写明收益，失败或不再保留的实验也要记录其边界，便于后续避免重复。
- 先做低成本 kernel/parity 测量，再决定是否值得做端到端 A/B。

## 2. 工作区与远端

- 本地工作区：`/home/kali/IdeaProjects/strata`
- 分支：`freeze/server-adapt-0.2.7`
- 基线 HEAD：`97b81be201cefc498ed3a190a1a70201ea52f641`
- 远端 SSH：`ssh -p 8686 atm@129.28.27.137`
- 候选源码：`/opt/ai/src/strata-freeze-throughput-20261029T0812Z`
- 候选构建：`/opt/ai/src/strata-freeze-throughput-20261029T0812Z/build75`
- GPU：2 x NVIDIA GeForce RTX 2080 Ti（sm_75）

当前工作树仍未提交，包含本轮代码、测试和本文档。生产 systemd 与生产配置未改。
候选 HTTP 服务和 engine 在最后一次 210K 验收后仍在 `8080` 运行；不要与生产 `8081` 混淆。

最终关键文件 SHA-256：

```text
47009da533393ff4bcb62e6831427204237da3a1f9679ad4a691d4ddc897971b  iq_kernels.cu
9c0ed8cff63dd2725890393e1034c38347fd8818623e1d7105f28ff143eba9a0  include/strata/kernels/iq_kernels.hpp
8614413074ca6cb5cea9baf2672ddd5114f7a22507f5495c0bf247a2631b6e25  iq_multi_parity.cpp
1d1181f6901b223468d309cd37e5492f21c5e508f011167956a6fbfac6affa04  qsa_prompt_attn.cu
1f3a77ff5a33131259e807466ffbe0addd2a0ad40ab9821848a816b04165e6db  src/prefill/kernels.cu
eb3cd74ccbd00649fec25df364d0dd34f4b0446cf656781e634d9eda0257a098  include/strata/prefill/kernels.hpp
439ead2a95d607b86b0a991aee5704f3efa87a66a3fb50505fe68672084fc608  src/prefill/gemm.cu
```

远端候选工作树以 `c219fbcac2cc6d877590c3c1da4fb98028f4acf8` 为 HEAD，但本次涉及引擎的文件已与
本地 `97b81be...` 工作树同步；后三个本地提交只改 server、诊断和文档，不改变本次测试的生产 engine 源文件。

## 3. 本轮代码改动

### 3.1 QSA prompt attention

文件：`src/kernels/cuda/qsa_prompt_attn.cu`

- 使用已通过六个场景位级校验的 v2 int8 路径和 32-cell chunk。
- `p_at()` 按 softmax 写模式和 p.v 读模式共同设计行异或布局，消除 shared-memory
  bank conflict；存储值不变。
- 代码注释说明两个访问模式、异或低五位的作用及适用前提。
- v2 现在是默认路径；`STRATA_PA_V2T=0` 可回退 v1。

32K prompt / 2048 输出、int8 的稳定中位数：

- 基线：`8.787 ms`
- 候选：`8.252 ms`
- 提升：约 `6.1%`

两个短场景分别提升约 `5.7%` 和 `6.2%`。单次 NCU 对照：

| 指标 | 基线 | 候选 |
|---|---:|---:|
| kernel 时间 | 10.77 ms | 9.86 ms |
| shared conflicts | 105.57M | 54.69M |
| shared wavefronts | 349.20M | 299.42M |
| registers | 168 | 168 |
| dynamic smem | 19.73 KB | 19.66 KB |

优化后的完整 prompt timeline 中，QSA 从旧 profile 的 `2.60 s` 降到 `1.94 s`（约 `25%`）。
又试过把 v2 chunk 从 32 个 cell 减到 16，期望减少寄存器和 shared memory 以得到第 4 个
resident block；实际 int8 路径从 `8.25 ms` 退到 `8.82 ms`，且三个场景输出错误，因此已撤销。
这一结果也说明当前 32-cell 版本的 occupancy 取舍是正确的。

### 3.2 IQ dequant

文件：`src/kernels/cuda/iq_kernels.cu`

- IQ3_XXS 的连续 8 个 fp16 输出改为一次 16-byte store。
- IQ4_NL 的两组连续 4 个 fp16 输出改为 8-byte store。
- 原 32-thread block 改为 4 个独立 superblock 共处一个 128-thread block，绕开 Turing
  resident-block 限制，并加入尾块边界检查。
- 注释说明原路径是指令瓶颈、向量写的对齐假设，以及 block 合并原因。

精确生产形状，100 次调用的五次复测中位数：

| 路径 | 基线 | 最终候选 | 改善 |
|---|---:|---:|---:|
| IQ3_XXS gate/up 1280 x 2560 | 30.11 us | 22.06 us | -26.7% |
| IQ4_NL gate/up 1280 x 2560 | 25.01 us | 21.42 us | -14.4% |
| IQ4_NL down 2560 x 640 | 13.21 us | 8.01 us | -39.4% |

三组输出 FNV hash 在优化前后完全一致：

```text
1170b8003712d041
7f5e46c726f7e741
fb89038272843b64
```

向量化后的单次 NCU 已测得 `30.85 -> 23.39 us`，DRAM sustained 从 `34.0%` 到
`45.25%`。额外试过 8-warp block：IQ3_XXS 持平，IQ4_NL gate/up 仅快约 `0.35%`，
down 慢约 `1.1%`，因此最终保留 4 warp。

### 3.3 Prompt MMQ 覆盖

- 增加 `IQ2_XXS`、`IQ2_XS`、`IQ2_S`、`IQ3_S` 的 prompt MMQ 实例和派发。
- `tests/cuda/mmq_expert_parity.cpp` 覆盖新增格式的 q8_1 激活量化、gate/up MMQ、
  SwiGLU、H 量化、down MMQ 全链路。

### 3.4 Native fused IQ

- 修复 native fused IQ 错误调用 Q2_0 `requested()` 的问题。
- native 路径现在使用自己的 setup/coverage 结果和 `STRATA_PF_FUSED`，不再被 Q2_0
  的 sm_75 附加条件错误拒绝。
- 36K、两次 `STRATA_PF_FUSED=1` 的生产 wall 为 `18.300 / 17.947 s`；相邻 MMQ 对照为
  `18.328 / 17.984 s`。没有观察到 fused banner 或阶段级收益，当前不能证明端到端提速，因此没有把
  native fused IQ 改成默认。
- `prefill_fused_iq_test` 的 3584-token 对照存在偶发失败：五次短回归中四次通过，一次出现
  `fused vs MMQ` 的 `worst row 3.298e-2`；通过样本为 `3.096e-3`。失败与通过时 fused 输出 hash
  都是 `5d40e60db106f9b1`，因此不稳定量来自 MMQ 对照输出，而不是 fused kernel 的确定性输出。
  后续已定位为测试顺序问题，修复见 3.15。

### 3.5 其他吞吐和正确性改动

- `gr_broadcast` 改为一个 block 处理一个 token，`float4` 读取一次后广播到 4 路。
- 新增 `prefill_gr_broadcast_test`，位级比较全部输出并检查尾后 sentinel。
- Q8_0 grouped expert 网格改用与真实 kernel 一致的 `gu_sub16_layout()`，修复
  `80-dot` 条件不一致导致的部分 up 行未调度。
- `mmvq_il_parity` 的 Q6_K cell 期望从旧值 `2` 修正为实际表值 `0`。
- `iq_multi_parity --dequant-bench` 只测生产 expert 形状并输出 hash，便于低成本回归。

### 3.6 Write + norm 融合

文件：`src/prefill/kernels.cu`、`include/strata/prefill/kernels.hpp`

`gr_write_norm_rs` 原来把第一遍的 40 个 float 全部保留在线程寄存器中，造成很高寄存器压力；
第二遍紧接着读取同一行，数据通常会留在 cache。新默认实现只保留 block reduction 所需状态，
第二遍重读刚写入的 R 行。两种实现保持完全相同的遍历顺序、归约顺序和输出位。

同一测试程序交替运行两种实现，每项 12 次计时并取中位数：

| tokens | 保留寄存器 | 重读 R | 改善 |
|---:|---:|---:|---:|
| 128 | 0.052 ms | 0.036 ms | 30.8% |
| 512 | 0.190 ms | 0.124 ms | 34.6% |
| 2048 | 0.753 ms | 0.471 ms | 37.5% |
| 8192 | 2.970 ms | 1.891 ms | 36.3% |

`R`、`rs`、`xn16` 全部逐字节一致。优化后 profile 中该 kernel 是 `1.26 s`，按微基准预计
降到约 `0.82 s`，为整条 prompt 节省约 `0.44 s`。

### 3.7 BF16 -> FP16 转换向量化

文件：`src/prefill/gemm.cu`

sm_75 的 FP16 tensor-core 路径会先把 BF16 权重和激活转换成 FP16。原 kernel 每个线程只处理
一个 16-bit 元素；新路径在长度是 4 的倍数且输入、输出都按 8 字节对齐时，每个线程一次加载并
写回 4 个元素。每个值仍执行与原实现相同的 `65504` 有限值饱和和 round-to-nearest-even，
不支持向量访问的长度或布局继续走标量 kernel。代码注释写明收益来源和适用边界。

同一 benchmark 的 Nsight Systems 内核汇总，336 次调用总计：

| 路径 | 转换总时间 | 相对标量 |
|---|---:|---:|
| 标量 | 19.41 ms | 基线 |
| 4-value 向量 | 12.52 ms | -35.5% |

`STRATA_BF16_TC=1 ./build75/gemm_bf16_parity` 的 7 个生产常见形状全部通过；最差相对误差
`1.70e-05`，其余不超过 `2.71e-06`。此前的完整 36K profile 中转换总计约 `0.250 s`，按内核
降幅推算整条 prompt 只节省约 `0.09 s`，因此没有为这一项重启 HTTP 服务做端到端 A/B。
这是可解释的局部收益，不应把它与 GEMM 整体形状对照中的时钟噪声混为一谈。

### 3.8 Native token embedding 合并子块

文件：`src/kernels/cuda/iq_kernels.cu`、`include/strata/kernels/iq_kernels.hpp`

SC117 的 native token embedding 是 IQ3_S、宽度 2560。旧 `iq_embed_rows` 对每个 token 的每个
256-value 子块启动一个 32-thread block；36K prompt 每个 token 产生 10 个小 block，并受 Turing
32 个 resident block 的限制。新路径让一个 block 的 10 个 warp 协作处理同一 token 的子块，
保持原有 `dq_dispatch`、目标地址和字节序；默认路径因此是逐位兼容的。

36864 token、2560 embedding、mapped host table 的同机样本：

| 路径 | 三次时间 | 说明 |
|---|---:|---|
| 旧 one-warp block | 6.689 / 6.711 / 6.797 / 6.796 ms | 四次样本 |
| 新 per-token block | 6.494 / 6.477 / 6.530 / 6.559 ms | 四次样本 |

新路径稳定快约 `3.5%–4.0%`，每次约省 `0.23–0.27 ms`；旧实现和新实现输出 hash 均为
`cd3f9e040066ea83`。绝对值很小，不能据此宣称端到端吞吐提升。新网格还是一维的，移除了旧
`grid.y` 的 65,535-token 上限。`--embed-bench` 可重复该对照；旧路径只保留为 A/B 和边界检查。

### 3.9 MoE 16 / 32 / 64 专家分组

`MMQ_GROUP` 保持 16，`STAGE_GRP` 保持 32，`kGatherGroupMax` 保持 16。合成基准在 R=145
显示 64 组对 IQ3_XXS gate/up 和 IQ4_NL down 有局部吞吐优势，但相邻 36K 生产 A/B 没有稳定兑现：

| 场景 | gate/up | down | GPU timeline | wall |
|---|---:|---:|---:|---:|
| 16 组，请求 1 | 2894 ms | 1437 ms | 12758 ms | 13631 ms |
| 64 组，请求 1 | 2727 ms | 1506 ms | 13378 ms | 14297 ms |
| 16 组，请求 2 | 2851 ms | 1421 ms | 13322 ms | 14173 ms |
| 64 组，请求 2 | 2764 ms | 1534 ms | 12994 ms | 13890 ms |

结果受相邻 boot 和请求波动影响，不能证明大组在完整 stream/gather 管线中更快。生产代码已恢复
16 组；`MMQ_BENCH_EXPERTS=N` 只供合成基准改变每 launch 的专家数，不改变生产缓冲布局。

### 3.10 当前 36K 阶段剖面

最新可控样本的近似占用如下，绝对时间会随时钟和相邻 boot 变化：

| 阶段 | 时间 | 占比 |
|---|---:|---:|
| gemm gate/up | 2.7–3.0 s | 约 21–23% |
| gemm down | 1.4–1.5 s | 约 11% |
| embed+steps | 1.5–1.7 s | 约 12% |
| hc read | 约 1.1 s | 8.3% |
| QSA attention | 约 0.98 s | 7.6% |
| GDN recurrence | 约 0.95 s | 7.5% |

MoE 仍是最大热点；embedding 的局部收益已确认很小。下一步优先级应放在 MoE kernel 波次、gather
和短 launch 的实际时间证据上，不要继续微调单个 tile 参数。

### 3.11 MoE 与 GDN 的低成本剖析边界

用 Nsight Systems 剖析 `mmq_expert_bench 145`，16 专家一次 launch 的单 kernel 时间如下：

| 产品 | MMQ kernel 时间 |
|---|---:|
| IQ3_XXS gate/up | 0.532 ms |
| IQ4_NL down | 0.320 ms |

同一 profile 中 `quantize_mmq_q8_1` 只占约 `0.3%`，因此合成基准里的主要成本明确是 MMQ 本体，
不是 Python 或 benchmark 外壳。`prefill_mmq_scatter_test --bench` 在 32K x 10 行时，
`quantize_scatter` 为 `3.535 ms`，低于每行 gather 后量化的 `8.400 ms`；生产路径已选择前者。
这说明 MoE 还有优化空间，但方向必须是 MMQ 内部的波次/数据复用；仅减少 Python host 调度或
再次调 gather 不会解释 `2.7–3.0 s` 的 gate/up 时间。

GDN recurrence 也做了现成基准确认：68 SM、32768 token 时，旧 column kernel 为 `38.68 ms`，
key-head 为 `38.16 ms`，约 `1.01x`，没有足够收益。额外尝试把每 token 的多个 output-norm
head 合进同一 block：

| output norm | 2048 token | 8192 token | 32768 token |
|---|---:|---:|---:|
| 1 head/block（现生产） | 0.26 ms | 1.02 ms | 4.92 ms |
| 2 heads/block | 0.25 ms | 1.01 ms | 4.99 ms |
| 4 heads/block | 0.26 ms | 1.03 ms | 5.24 ms |
| 8 heads/block | 0.27 ms | 1.13 ms | 5.95 ms |

所有原型输出都做了逐位比较；但收益不稳定，32K 时长 block 反而变慢，因此原型代码及其测试改动已
撤销。该负结果说明 output norm 已是带宽/调度平衡点，不应再用“减少 block 数”作为优化假设。

### 3.12 Hyper-connection mix 的向量化边界

`gr_mix_r` 在 profile 中约 `0.69 s`。它已是带宽型内核：每个输出列重读四个 hyper-connection 流，
并读取 norm、gate，再写出 FP32、BF16、FP16 三种结果。尝试让每个线程处理四个相邻列，用 128-bit
load/store 替换标量访问；四个分量仍保持原来的逐列乘法与 `fmaf` 累加顺序。

RTX 2080 Ti 交替运行标量和 float4 路径，输出逐字节一致：

| tokens | 标量 | float4 | 变化 |
|---:|---:|---:|---:|
| 128 | 0.030 ms | 0.031 ms | -1.5% |
| 512 | 0.110 ms | 0.109 ms | +0.5% |
| 2048 | 0.426 ms | 0.424 ms | +0.5% |
| 8192 | 1.695 ms | 1.722 ms | -1.6% |

生产形状没有稳定收益，说明减少地址/transaction 指令没有降低实际 DRAM 流量。原型及测试入口已撤销，
`gr_mix_r` 保持原实现；后续优化必须减少 R/gated 的读写字节，单纯改访问宽度不应再测。

### 3.13 cuBLAS FP16 算法探针

HC down（`T x 10240 x 320`）和 up（`T x 320 x 10240`）的 FP16 产品在 RTX 2080 Ti 上枚举了
cuBLAS 的默认算法和 `ALGO0..23`。默认算法不是枚举里的数值编号，且明显快于所有可用固定算法：

| 形状 | tokens | 默认 | 最快固定算法 |
|---|---:|---:|---|
| down | 4096 | 529 us | 2779 us |
| down | 8192 | 1539 us | 5474 us |
| up | 4096 | 565 us | 2680 us |
| up | 8192 | 2142 us | 5287 us |

因此不应为这两个形状硬编码 cuBLAS 固定算法。探针程序已删除；生产 `Gemm::f16` 继续用
`CUBLAS_GEMM_DEFAULT`，除非将来出现能在两种 token 规模都稳定胜出的新 cuBLAS 版本。

down 与 inject 共享同一份 `X[T,10240]`，因此又测了两次默认 GEMM 对一次 324 行 GEMM：

| tokens | down + inject | fused 324 行 + 拆分 | fused 相对 |
|---:|---:|---:|---:|
| 512 | 152.3 us | 218.7 us | -43.5% |
| 4096 | 775.8 us | 908.7 us | -17.1% |
| 8192 | 1453.5 us | 1790.3 us | -23.2% |

324 行落到了效率更低的 cuBLAS 解法，节省一次 X 读取不足以抵消。该融合不进入生产代码，HC down /
inject 继续分两次调用。

### 3.14 GDN recurrence 的 occupancy 边界

Nsight Compute 对生产 `gdn_rec_cols_pipe_kernel` 的采样：DRAM 吞吐约 `68%`、SM 约 `18%`、
每 SM 实际约 `10.4` 个活跃 warp（理论 28 个）。192 个 block 在 68 SM 上每卡只有约三波 block，
因此活跃 warp 少来自 grid 本身，而不是寄存器 spill；local memory spill 为 0。

尝试把每列的 4×32 row-group 改成 8×16，使每个 block 从 4 个 warp 增到 8 个：

| tokens | 4×32（现路径） | 8×16 | 相对 |
|---:|---:|---:|---:|
| 2048 | 2.24 ms | 3.08 ms | 0.74x |
| 8192 | 8.96 ms | 12.94 ms | 0.74x |
| 32768 | 39.01 ms | 55.56 ms | 0.74x |

增加线程和 barrier 后并不提高 DRAM 的有效利用，所有形状稳定慢 26%。原型和测试改动已撤销。
GDN recurrence 要再优化，必须减少每 token 对 q/k/v/gate/beta 的访问或 barrier 次数，不能只增加
row-group 并行度。

也试过只改变 block 到 `(key head, value head, column block)` 的映射，让共享 q/k 的三个 value
head 相邻执行。所有 T 的输出和 state 逐位一致，但同轮交替 A/B 在 2K/8K/32K 均无可重现收益；
台面差异来自跨轮时钟漂移。映射恢复原样。

### 3.15 fused IQ 对照测试的稳定化

文件：`tests/cuda/prefill_fused_iq_test.cpp`

3584-token 重复回归再次复现偶发失败。失败并非随机分散，而是集中在单个恰好 64 行的专家
（例：expert 32）；同一轮 fused 输出 hash 仍为 `5d40e60db106f9b1`。检查测试顺序后发现，计时循环
每轮最后运行 `direct_run()`，它用于测量“无 gather 的 MMQ 本体时间”，却会把 `mb.dm` 覆盖成
诊断路径的输出。随后最终校验直接下载这个 buffer，错误地把它当成生产 `gather + MMQ` 序列的结果。

修复只调整测试：计时结束后、最终下载和误差比较前，显式再运行一次生产 `mmq_run()` 并同步。改动处
注释说明了 `direct_run` 仅用于计时、为什么不能占用最终校验 buffer，以及修复适用的比较边界。生产
MMQ/fused kernel 未改。

验证：

- 修复前，同参数 10 次短回归中 1 次失败，失败同样集中在单个 64 行专家。
- 修复后，`--no-ref --chunks=3584` 连续 20/20 通过；fused hash 全部为
  `5d40e60db106f9b1`，最终 `fused vs MMQ` 均为 RMS `1.341e-04`、worst row `3.096e-3`。
- 完整 `ctest -R "^prefill_fused_iq_test$"` 通过，19.62 秒。

### 3.16 SwiGLU 与 H 的 q8_1 量化融合

文件：

- `src/prefill/moe_mmq.cu`
- `include/strata/prefill/moe_mmq.hpp`
- `src/prefill/prefill.cpp`
- `src/prefill/mmq_expert_bench.cpp`
- `tests/cuda/mmq_expert_parity.cpp`

生产 MMQ 原来先运行 `swiglu()` 把 H 写回全局 FP32，再运行 `quantize()` 读回 H 并写 q8_1。down MMQ
只消费 q8_1，因此新增 `swiglu_quantize()`，让每个 warp 直接计算 128 个 H 值并按 llama.cpp 的 D4
q8_1 布局写回。它去掉 H 的一次 FP32 写和一次读，也把两个 kernel launch 合成一个。

实现保留原路径的 `__expf` 表达式、32-value amax shuffle 顺序、`roundf` 和 d4 scale 写入位置；支持的
D4 类型为 IQ2_XXS、IQ2_XS、IQ2_S、IQ3_XXS、IQ3_S、IQ4_NL、Q2_0、Q8_0。其他 q8_1 布局仍走原
两段路径。代码注释写明了收益来源、硬件/布局适用范围和回退边界；`moe_swiglu_quantize()` 保留统一的
支持检查与 fallback，三条生产 MMQ 调用路径均已接入。

RTX 2080 Ti、split GU、IQ4_NL D4 q8_1，每组 25 次中位数，连续三轮：

| H rows | 原两段（ms） | 融合（ms） | 相对 |
|---:|---:|---:|---:|
| 4,000 | 0.093–0.094 | 0.054 | 约 1.74x |
| 40,000 | 0.884–0.895 | 0.502–0.507 | 约 1.76–1.77x |
| 400,000 | 8.775–8.810 | 5.021–5.058 | 约 1.74–1.75x |

正确性：

- `mmq_expert_parity` 对 IQ2_XXS、IQ2_XS、IQ2_S、IQ3_S 全链路通过。
- 新增校验对 split 和 interleaved 两种 GU 布局逐字节比较原 `swiglu + quantize` 与融合路径写出的
  q8_1 payload，全部相同；固定 over-read tail 不属于任何一条路径写入的内容，因此不参与比较。
- 定向 `prefill_fused_iq_test`、`prefill_mmq_scatter_test` 和四个 `mmq_expert_parity_*` 共 6/6 通过。

这是局部 kernel 收益。按 36K 的 `T*K` 行数和 36 层粗估可省约 `0.13 s` prompt 时间（约 0.7%），但尚未做
端到端 A/B，不能把 1.75x 的局部加速等同于整体吞吐提升。

### 3.17 长会话 checkpoint 的尺度锚点

文件：`include/strata/program/conv_cache.hpp`、`src/program/generate.cpp`、
`src/program/conv_cache_test.cpp`、`docs/DETAILS.md`、`tools/long_context_http_bench.py`。

原 `--prompt-cache-long` 只把 cap 从 3 提高到 8，并沿用 tail-heavy 的普通 spread。真正冷读
139,138 tokens 后，保留位置是：

```text
8192, 73728, 90112, 106496, 114688, 122880, 131072, 139131
```

这组点在尾部过密，25% 位置之前只剩 8,192 一个可复用点；因此原来 25% 位置改写仍然全量读完约
131K tokens。新增 `long_spread_victim()`，在长模式下把六个内部槽位锚定到最新长度的
1/8、1/4、1/2、3/4、7/8、15/16，并保护共同 root、最新 checkpoint、显式 pin 和 tail 优先级。
短 prompt 与普通 `--prompt-cache-spread` 的行为不变；长模式仍是显式 `--prompt-cache-long` opt-in。

`conv_cache_test` 全部通过，包括长模式的 25% 编辑、近尾编辑、root/newest 保护、pin 保护和原有
LRU/tail/spread 场景。RTX 2080 Ti 双卡、生产 HTTP 配置的长度匹配结果：

| 场景 | 旧保留/读取 | 新保留/读取 | prompt wall | 变化 |
|---|---:|---:|---:|---:|
| 约 140K，25% 改写 | 复用 8,192 / 读 130,949 | 复用 32,768 / 读 106,524 | 55,895 → 45,890 ms | -8.96 s，约 -16.0% |
| 约 210K，25% 改写 | 复用 8,192 / 读 200,637 | 复用 32,768 / 读 176,260 | 85,154 → 76,267 ms | -8.89 s，约 -10.4% |

140K 和 210K 新样本使用新的随机种子强制真正冷读，token 数分别与旧样本接近但不逐字节相同；这是
同机、同配置、同长度的场景 A/B，不是内容完全相同的严格 microbenchmark。冷读本身没有观察到稳定
吞吐变化；收益集中在位置改写和会话压缩恢复。

## 4. 端到端结果

使用 `/home/atm/verify-20261027/strata-verify-8080.json` 的相同模型和参数，通过 HTTP
服务入口测试；每次丢弃一次 warm-up。

36K、固定生成方式，本轮之前候选三次：

```text
1955.4 / 1939.7 / 1842.1 tok/s
median = 1939.7 tok/s
```

历史同规模基线：

```text
1825.8 / 1840.2 tok/s
```

36K、相同脚本，启用低寄存器 write+norm 后的最终候选三次：

```text
1985.6 / 1978.6 / 1973.4 tok/s
median = 1978.6 tok/s
```

最终候选相对上一候选 `1939.7 tok/s` 提升约 `2.0%`，相对历史同规模基线约 `+7.5%`。
这些是同机器、同模型和同 prompt 脚本的结果，但不是相邻 boot 的严格基线 A/B，因此整体
收益的绝对归因仍保留该限制；write+norm 本身有独立的生产规模微基准支持。

32K 候选三次为 `1858.5 / 1329.7 / 1853.3 tok/s`，中位数 `1853.3 tok/s`；中间一次约有
10 秒 chunk 尾段 I/O/调度停顿，不作为稳定速度依据。

## 5. 验证状态

恢复 16 专家版本后的历史定向 CTest：

```text
10 of 11 passed; prefill_fused_iq_test failed once (fixed and rechecked in 3.15)
```

覆盖：

- `qsa_prompt_attn_parity`
- `prefill_gr_broadcast_test`
- `dequant_wide_parity`
- `iq_multi_parity`
- `native_grouped_parity`
- `elementwise_parity`
- `kv_hybrid_parity`
- `prefill_fused_iq_test`（修复对照顺序后完整通过；3584-token 重复 20/20 通过）
- `prefill_mmq_scatter_test`
- 四个 `mmq_expert_parity_*`
- `gdn_rec_parity`
- `gemm_bf16_parity`
- `prefill_gr_broadcast_test`
- `ple_reader_selftest`

单独重跑 `prefill_fused_iq_test` 后通过；五次 `--no-ref --chunks=3584` 短回归中四次通过、
一次失败。该失败已按 3.15 定位并修复：测试最后把 `direct_run` 的计时输出误当成生产 MMQ 结果参与
最终比较；修复后 20/20 通过。该历史失败不能作为 fused 路径的非确定性证据。

embedding 改动后的低成本验证：

- `iq_multi_parity`：`0 failures`，所有 MMQ 格式和 grouped native path 位级通过。
- `native_expert_parity --bf16-embd`：`0 of 248320 values differ`，`0 failures`。
- `iq_multi_parity --embed-bench`：三次均 `0 failures`，IQ3_S 输出 hash 相同。
- `strata` 与 `iq_multi_parity` 增量构建成功。
- GDN output-norm 原型撤销后重新构建 `gdn_rec_parity` 并运行，`0 failures`。

另外：

- 六个 QSA 场景输出 hash 全部与基线一致。
- `iq_multi_parity: 0 failures`。
- `native_grouped_parity: 0 failures`。
- `mmvq_il_parity` 的每个 case、T=1..8、rows=1/2/4 和表检查均位级通过。
- `prefill_gr_broadcast_test --bench-write-norm` 覆盖 T=128/512/2048/8192，两种
  `gr_write_norm_rs` 实现的 R、rs、xn16 全部位级一致。
- BF16 转换最终版另跑 `gemm_bf16_parity`、`gdn_rec_parity` 和
  `prefill_gr_broadcast_test`，`3/3 passed`。BF16 的正常范围和生产形状已覆盖，但没有单独
  加入 BF16 inf/NaN/有限溢出极值 fixture；这些值仍执行与标量路径相同的逐值转换表达式。
- 最终 `strata` 增量构建成功；`prefill_gr_broadcast_test` 和 `elementwise_parity` 通过。
- SwiGLU/q8_1 融合后 `strata`、`mmq_expert_parity`、`mmq_expert_bench` 增量构建成功；
  `prefill_fused_iq_test`、`prefill_mmq_scatter_test`、四个 `mmq_expert_parity_*` 共 6/6 通过。
- 全部 CTest 尚未在最终版重跑，不能描述为“全量测试通过”。

## 6. 运行与复测

增量构建：

```sh
cd /opt/ai/src/strata-freeze-throughput-20261029T0812Z
cmake --build build75 -j
```

低成本 dequant 回归：

```sh
./build75/iq_multi_parity --dequant-bench
```

write+norm 位级与性能回归：

```sh
./build75/prefill_gr_broadcast_test --bench-write-norm
```

SwiGLU/q8_1 融合微基准：

```sh
./build75/mmq_expert_bench --hquant-bench
```

embedding 生产形状位级与性能回归：

```sh
./build75/iq_multi_parity --embed-bench
```

定向回归：

```sh
ctest --test-dir build75 --output-on-failure \
  -R "^(qsa_prompt_attn_parity|prefill_gr_broadcast_test|dequant_wide_parity|iq_multi_parity|native_grouped_parity|elementwise_parity|kv_hybrid_parity|prefill_fused_iq_test|prefill_mmq_scatter_test|mmq_expert_parity_)"
```

正确 HTTP 启动方式：

```sh
cd /opt/ai/src/strata-freeze-throughput-20261029T0812Z
/opt/ai/venvs/test-027/bin/python serve/server.py \
  --engine strata \
  --config /tmp/strata-freeze-candidate.json \
  --port 8080
```

不要直接用 `build75/strata --serve` 做 HTTP 测试：它会输出 `READY`，但不会监听 HTTP。
系统 Python 缺 `regex`，使用 `/opt/ai/venvs/test-027/bin/python`。

`/tmp/strata-freeze-candidate.json` 已把 `exe`、`cwd` 和 `port` 指向候选目录与 `8080`。

## 7. 后续优先级

1. 当前最大热点仍是 MoE gate/up `2.7–3.0 s` 和 down `1.4–1.5 s`；先用 nsys 的单 kernel、
   wave/内存证据定位，再选一个低风险的最小改动。不要重复已失败的 QSA 16-cell、dequant
   8-warp、GDN 三屏障/双缓冲和 MoE 64 组默认化。
2. GDN 的 key-head kernel 在 68 SM 上约 `1.00-1.02x`，收益不足。又试过把 4 个 row group
   放进相邻 lane、用 shuffle 代替两次 shared reduction，并双缓冲把每 token 的屏障降到一次；
   全场景输出/状态位级一致，但 `T=2048/8192/32768` 慢到原路径的 `0.25-0.27x`，已完全撤销。
   开销来自每 token 的跨 lane 数据广播和新的 shared 访问模式，不能只按屏障数量估算。
3. MMQ 目前已占 GPU 时间的大头；若继续优化，应优先减少短 kernel/分组 launch，而不是调
   单个 MMQ tile 的微小参数。
4. 若追求严格端到端 A/B，相邻启动基线和候选，至少各三次并报告中位数。
5. 全量 CTest 只在准备合并或改动跨后端接口时运行，避免与每次局部调参重复。
6. 服务启动与专家缓存加载时间较长。只有微基准显示至少约 `1%` 的明确收益，或修复了正确性/
   容量边界时，才值得启动一次候选服务做端到端 A/B；相邻 boot 至少各三次并报告中位数。
