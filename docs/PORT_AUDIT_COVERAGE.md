# fork 优化项 → Strata 覆盖审计（逐条，只读）

> 对象：`/home/kali/IdeaProjects/vLLM-2080Ti-Definitive`（`v0.2.2-post3`，上游基线 `b23433088b`）；对照：本仓库 HEAD `738c65d9`
> + 工作区未提交的 W3（`serve/server.py` 等 15 个文件）。姊妹文档：`docs/VLLM_PORT_PLAN.md`（P1–P6）、`docs/SM75_PORT_PLAN.md`（W0–W8）、
> `docs/DETAILS.md`。
>
> 标记约定：**†** = fork 自述的数字或收益，**未在本机复现**，只用于定位"它认为值钱的地方"，不当作 Strata 的收益预期。
> 所有 status 判定都带行号；行号以本文件写作时的两侧树为准（fork 是 `git diff b23433088b HEAD` 的 `+` 侧，Strata 是工作区）。

## 0. 方法与范围

**做了**：用 `git diff --name-status b23433088b HEAD` 把 fork 的增量定死，再逐 hunk 读。得到的实测面：**79 个新增文件、
120 个改动文件、722 个上游文件不在树里**（裁剪）。其中 `vllm/` 与 `csrc/` 下只有 **1 个**新增文件
（`vllm/model_executor/models/dflash2_sm75.py`）——所以"fork 独有"的主体是 profiles、tests、tools、launcher，**引擎内核真增量
集中在 120 个改动文件里**。读了这些改动文件的全部 hunk，以及 `profiles/**/*.env`（27 个档）、`tools/flashqla_sm75_patches/`、
`CHANGELOG.md`、`README.md`、`profiles/*/README.md` 的测量表（含口径）。

**没读**：722 个被裁掉的上游文件（fork 删的，不是它加的）；`vllm/models/`（DeepSeek/Kimi/多模态等 route，与 2080 Ti 单模型路线无关）
的绝大部分；`tests/` 里与结论无关的用例；fork 的外部 audit 目录（`profiles/*/README.md` 提到的原始日志，不在本机）。

**口径声明**：fork 公布的所有吞吐（`profiles/2x2080Ti/README.md:7-35`）都是"prefix cache 关、单请求、4K/128 取 3 次中位、
32K/512 跑到完成"的单请求 lane，即使档位写了 `MAX_NUM_SEQS=4` 也是这个 lane 测的。Strata 现网是 6 槽并发、`--kv int8`、
IQ3_XXS。**两者不可直接相减**，本文件只比"机制"不比"数字"。

## 1. 覆盖矩阵

### 1.1 KV cache 量化 / 布局

| # | 优化项 | fork 位置 | 声称收益与口径（fork 自述†） | 原理（一句） | Strata 状态 | Strata 证据 |
|---|---|---|---|---|---|---|
| 1 | TurboQuant 4-bit KV 档（TQ4NC） | `vllm/v1/attention/backends/turboquant_attn.py`（2336 行）；`profiles/2x2080Ti/qwen27b/w4a16/mtp4-tq4nc-3x262K-text-only.env` | 3×262K 槽（1.79× KV token），4K decode 127.86、32K 68.64（−37%）† | V 走 4-bit 质心码本（Hadamard 旋转域），K 走 FP8；拿精度换槽数 | **已有等价实现** | `--kv q4_0`（FWHT 旋转 + q4 存储）、`int8`、`k8v4` 三条：`generate.cpp:831,3199`、`include/strata/kernels/kv_stream.hpp:44-49`。没有"质心码本"形式，但 4-bit + 旋转是同一思路 |
| 2 | TQK8V4（FP8 K + 4-bit V） | `vllm/v1/attention/ops/triton_turboquant_decode.py:63-99`、`triton_turboquant_store.py:145-260`；profile `mtp3-tqk8v4-2x155K` | 2×155K / 1×196K 槽† | K 保持高精度（精确打分）、V 压到 4-bit | **已有等价实现** | `--kv k8v4`＝INT8 K（+per-group scale）+ 旋转 Q4_0 V，816 B/cell：`generate.cpp:831,3199`、`kv_stream.hpp:45-48`、`prefill.cpp:3330` |
| 3 | SM75 上 FP8 key 必须用 **e5**（不是 e4b15/e4nv） | `triton_turboquant_decode.py:63-99`（`_fp8_format_code`，cap<8.0→code 2）；`triton_turboquant_store.py:150-165` | 无数值；注释称用 e4b15 会"丢检索质量并压低后续 MTP 接受率" | post-RoPE 原始 key 的动态范围超出 e4b15 | **明确不适用** | Strata 的 K 是 INT8 + per-group scale，没有 FP8 动态范围问题：`kv_stream.hpp:45`、`generate.cpp:3199` |
| 4 | TQ 解码 tile `BLOCK_KV=2`（Turing） | `triton_turboquant_decode.py:24-59`（env 默认 `"2"`，上游是 4）、`:819` | "Turing 的寄存器/共享内存平衡明显更好"†，无数字 | 每 tile 两 token，压寄存器压力 | **已评估·不移植** | P4 已扫过同一决策面：`qsa_decode_attn.cu:20-45` 记录 128/256、256/256 数值错（rel RMS 13-35×）、32/128 崩，`static_assert(CHUNK==64&&THREADS==256)` 锁死（`55c802a5`+`d71b7311`） |
| 5 | 图稳定的 KV 分裂数（`VLLM_TURBOQUANT_MAX_KV_SPLITS`） | `turboquant_attn.py:118-131`、`:745-760` | 无数字；"分裂数同时决定解码 grid 与图 workspace 形状，必须与 metadata 一致" | 可变分裂数会让捕获的 grid 失效 | **已有等价实现** | 解码/验窗的 `cap_` 在构造时固定 → `n_chunks=(cap+63)/64` 恒定：`qsa_decode_attn.cu:970`、`verify.cpp:763` |
| 6 | Hadamard 用一次 GEMM（预算 D×D 矩阵） | `turboquant_attn.py:254-269` | "一次 cuBLAS GEMM 代替 log2(D) 次蝶形 launch；D=128 时 64 KB"† | 预乘正交矩阵 | **已有更强实现** | 单 kernel 内蝶形，不落 64 KB 矩阵、不多读一遍：`prefill.cpp:1869-1870`（`fwht256_inplace_cuda`） |
| 7 | 块紧凑 BLHNC 布局 + 接受任意 16 的倍数的页对齐 | `turboquant_attn.py:303-325`、`:384-400` | 无数字；注释称"压缩 cache 与 DFlash FP16 页共分配器时需要"、"混合 Mamba 可能把页对齐到更大的 state 页" | 一种布局要同时喂两种形状 | **明确不适用** | 结构原因：每个 session 一套 pool、格式单一，没有跨格式共分配器：`session.cpp:57-100`、`layer.hpp`（`QsaState` 自带 k/v/scale 指针） |
| 8 | target / draft 独立 KV 池 + 每池独立块 ID + 每池 zeroing | `vllm/v1/core/kv_cache_utils.py`（`_dflash_group_capacities`）、`kv_cache_manager.py:take_new_block_ids`、`v1/worker/utils.py:KVBlockZeroer`、`sched/output.py:289` | 注释："共享池 accounting 曾把 DFlash2 限到约 1/6 容量"（修复前） | 请求必须装进**每个**池，块 ID 不能跨池复用 | **明确不适用**（但判据有用，见 §2 N3） | 结构原因：draft ring 是 drafter 自己的一块连续显存，不在 KV 池里，没有块 ID 共享：`dflash2.hpp:39`（`kRing=2064`）、`:52`（`load()`） |
| 9 | 续写反量化工作区在 KV 定容**前**扣除 | `kv_cache_utils.py:3045-3071`（`_turboquant_prefill_workspace_reserve_bytes`） | 无数字；注释称不预留则"首个续写请求变成 OOM" | 持久 scratch 必须先记账再定容 | **已有等价实现（更好）** | Strata 没有独立反量化工作区：prompt/续写注意力直接读量化池（`qsa_prompt_attn.cu:12-13,146-160`），短续写走解码 kernel（`prefill.cpp:3503-3510`）。**但"定容前记账"这个做法本身是缺口** → §2 N1 |

### 1.2 attention 后端与 tile

| # | 优化项 | fork 位置 | 声称收益与口径† | 原理 | Strata 状态 | 证据 |
|---|---|---|---|---|---|---|
| 10 | SM75 上解禁 FlashInfer FA2（上游把地板抬到 SM80） | `vllm/v1/attention/backends/flashinfer.py:622-632` | 无数字；注释称"Turing 路线由真实 Qwen3.8 FP8 smoke 与 benchmark 覆盖" | 上游因 issue 3620 临时禁 SM75，fork 保留 | **明确不适用** | Strata 不用 FlashInfer；Turing 注意力是自研 qsa 三件套：`qsa_prompt_attn.cu`、`qsa_decode_attn.cu`、`qsa_select.cu` |
| 11 | 投机 prefill 的"图稳定" FA2 wrapper（query 宽度钉成 k+1） | `flashinfer.py:106-157`（`_sm75_spec_prefill_graph_query_len`）、`:1329-1372`、`:1117-1122` | 无数字；注释称"SM75 投机 FA2 prefill 用稳定 metadata 缓冲做 FULL 图重放" | capture sizes 必须恰为 `(k+1)×batch`，否则不上 FULL 图 | **已有等价实现** | 验窗本身就是固定 `kVerifyMaxT` 行的图：`verify.cpp:763,1889-1935`；"每步变化的量走 device buffer"是硬规则：`graph.hpp:18-27` |
| 12 | 每层一份 FlashInfer workspace → 每设备一份，并在 profiling 时预留 | `flashinfer.py:159-215`（`_flashinfer_workspace_buffer_size`、`reserve_flashinfer_workspace_for_profiling`） | 无数字；注释称"KV 定容后 builder 才建，大 cache 会把 workspace 挤掉，首个真实请求 OOM" | 同一 device 上所有层共享一块持久 scratch | **未覆盖** | 同一类空洞在 P2 已列出（split 搜索按 1 session 定价、`kDrafterMib` 只活在搜索里），但没有"持久 scratch 清单 + 定容前扣除"的机制 → §2 N1 |
| 13 | SM<8 的后端优先级表 | `vllm/platforms/cuda.py:155-165` | 无数字 | Turing 无 FA，候选顺序 FlashInfer→Triton→Flex→TQ | **明确不适用** | Strata 不选后端，qsa 是唯一路径 |
| 14 | 短续写块走解码 kernel（阈值 128 token） | `turboquant_attn.py:101-106`（`_CONTINUATION_DECODE_THRESHOLD`）、`:1819-1946`、`:1947-2190` | 无数字；注释称"消除长上下文 O(N²/chunk) 崩溃" | 续写块 ≤128 时直接读已压缩 cache，不做整段反量化 | **已有等价实现** | 短续写直接 `qsa_decode_attn_batch`（32 query/tile，`prefill.cpp:3503-3510`）；prompt 路径也直接读池，**不存在 O(cached) 反量化环节** |
| 15 | 前缀压缩 + 当前块原始 K/V 的 online-softmax 合并（≥20480 token 起 auto） | `turboquant_attn.py:134-166`、`:1819-1946` | 无数字 | 前缀用压缩 cache、当前块用原始 K/V，两侧 LSE 合并 | **明确不适用**（但见 §2 N4） | Strata 一个 kernel 内对全前缀打分，没有"原始 vs 压缩"两条路；合并逻辑无处安放（`qsa_decode_attn.cu` 的 `attn_merge_kernel` 是 split-KV 合并，不是这个） |
| 16 | verifier 当前块保留原始 K/V，不重读压缩行 | `triton_turboquant_decode.py:525-735`（`_tq_spec_continuation_merge_kernel`）、`turboquant_attn.py:1438-1510` | 无数字；注释称"压缩 cache 是有损的，重读刚写入的行会改 verifier logits" | 刚写入的行读回来等于多损一次 | **未覆盖** | Strata 的验窗正是"先 append 到量化池、再从池里读回"：`verify.cpp:1820-1855`（append）→ `:1889-1935`（从 `qsa_attn_pools` 读）→ §2 N4 |
| 17 | SM75：投机候选按 B=1 逐个解码，避开 B=4 物化页表 | `turboquant_attn.py:1290-1300` | 无数字；"仍然是图内可重放，只是图拓扑不同" | 多几次小 launch 换掉一张大表 | **已有等价实现** | K 个候选一次 `attn_batch`，页表按 slot 逐行解析、不物化：`verify.cpp:1916-1935` |
| 18 | draft 步骤原地刷新页元数据（不重建对象） | `flashinfer.py:988-1000`、`:1993-2040`（`update_draft_decode_metadata`） | 无数字 | 融合 draft 步只改 data，地址不变 | **已有等价实现** | `graph.hpp:18-27`：每步变化的量一律是 device buffer 的数据 |
| 19 | FA2/FA3 扩展缺失时的能力回退 | `fa_utils.py:21-45,100-110,381-390` | 无数字 | Turing 没有 FA 扩展时不报错、走别路 | **明确不适用** | Strata 不 import `vllm_flash_attn`；`rotary_embedding` 也没有"FA rotary 缺失"的分支 |

### 1.3 投机解码（MTP / DFlash2 / 其他）

| # | 优化项 | fork 位置 | 声称收益与口径† | 原理 | Strata 状态 | 证据 |
|---|---|---|---|---|---|---|
| 20 | DFlash2：一次前向 7 提案 + selector | `qwen3_dflash2.py:375`（`CandidateSelector`）、`:494`；`spec_decode/dflash/speculator.py`；profile `dflash2-fp8kv-1x262K` | **220.84** tok/s decode（2×2080Ti 22GB NVLink、Qwen3.8-27B NVFP4、FP8KV、262K、单请求、4K/128 三次中位）；同表 MTP4 116.04 ⇒ ≈1.9×† | 8 行 block（anchor+7 mask）一次出 7 个候选，rank-256 双线性选路 | **已保留（显式 opt-in）** | `dflash2.hpp:37-39`（8 行/7 草稿/ring 2064）；selector 在 `dflash2.cu:590-605,782-785`（rank 固定 256）；serve 单请求接线：`40125aa4`；库层共享+异步：`b009fab5`；`ENGINE_SERVES_DFLASH2=True`，缺包或不适配时安全回落 MTP；生产默认仍走 MTP，且当前缺权重 |
| 21 | DFlash2 的 SM75 BF16→FP16 数值 codec | `dflash2_sm75.py:27-383`（`_ACTIVATION_GAIN=32`、`_RESIDUAL_GAIN=256`、RNE 到 BF16、行缩放 payload）；`qwen3_dflash2.py:292-300` | 无数字；注释称"BF16 checkpoint 的激活边界 vs Turing 的 FP16 dense 层" | 在训练边界上取整，MLP 输出按行缩放后再进 FP16 下投影 | **明确不适用** | Strata 的 drafter 走 Q8_0 int8 GEMM + `bf16f` 精确位扩展，没有"BF16 权重塞 FP16 MMA"的溢出路径：`dflash2.cu:31`、`:601` |
| 22 | 融合的 Triton grouped conv（DFlash2 动态 2-tap） | `qwen3_dflash2.py:36-135`（`_dflash2_grouped_conv_kernel` + `direct_register_custom_op`） | 无数字 | 一行一个 program、tap 静态展开，省掉系数物化与两次 pass | **明确不适用** | 量级不成比例：Strata 的 block 只 8 行 × 5 层（`dflash2.hpp:37`），conv 是 prepare/finish 两段同形，融合收益在 µs 级；fork 需要它是因为 vLLM 那侧每层都跑全 batch 行 |
| 23 | MTP drafter 保持 BF16，不跟 target 量化 | `qwen3_5_mtp.py`（`VLLM_QWOPUS_MTP_BF16_DRAFT`） | 无数字；注释只说加载行为 | drafter 精度影响接受率，不必与 target 同量化 | **未覆盖（方向相反）** | Strata 提供的是反向旋钮 `--mtp-q4`（把 drafter 量化，P2 判定"反向增"）。**未见"drafter 用更高精度"的路线** → §2 N7 / §4 |
| 24 | `VLLM_SM75_SPEC_SYNC_MODE=nosync` | `envs.py:50-53,893-897` | 无数字；注释称"TQK8V4 CUDA-graph 路线上验证过的峰值吞吐设置" | 去掉投机路径上多余的 stream 同步 | **未审计** | 没有对应开关，也没有"每 token 同步数"的度量 → §2 N6 |
| 25 | GDN 投机状态的 padding 用 `PAD_SLOT_ID(-1)`，不能用 0 | `vllm/v1/attention/backends/gdn_attn.py`（`spec_state_indices_tensor` 两处填充） | 无数字；注释："GDN state slot 0 是合法槽，所以图 padding 必须用 -1" | 0 与合法槽撞号 | **明确不适用** | Strata 每槽一套 session arena，没有共享 state-slot 表：`session.cpp:57-100`（GDN 状态按 layer 切在 session 里） |
| 26 | DFlash 验窗逐窗 trace（env 限步数） | `spec_decode/utils.py:get_trace_limit`、`rejection_sampler.py`、`gdn_attn.py`（`DFLASH_GDN_TRACE`） | 无数字（诊断用） | 打印 target_argmax/draft/接受序列 | **已有等价实现** | `STRATA_TAP_FILE`（`%d`=layer_lo）+ `tools/server/tap_parity.py compare / merge`：`869c1224`、`483c53f5`；另有 `--spec-oracle` 强制续写 |

### 1.4 MoE 专家 / 量化内核

| # | 优化项 | fork 位置 | 声称收益与口径† | 原理 | Strata 状态 | 证据 |
|---|---|---|---|---|---|---|
| 27 | Marlin W4A16 / Marlin-MoE 在 Turing 上可用 | `csrc/libtorch_stable/quantization/marlin/*`、`moe/marlin_moe_wna16/*`（**fork 未改动**，`git diff` 无 hunk；只被 profile 选中） | 无 fork 自述数字 | int4 权重 + fp16 MMA，靶点是反量化指令开销 | **已有等价实现 + 明确不移植** | 三条路已在：MMQ（llama.cpp Turing 分派）、`moe_fused*.cu` 自研 int8、`STRATA_PF_WMMA`；P3c 实测 fused/MMQ = 1.16/1.13/1.01×（R=12/160/626），未达 1.5× 门槛（`d8ba8593`） |
| 28 | NVFP4 routed experts 在无 FP4 TC 的卡上可用 | `vllm/model_executor/layers/quantization/modelopt.py:1562`（上游注释："SM75+ 走 Marlin W4A16"） | profile 的 NVFP4 路线 116–220 tok/s† | FP4 权重要么解成 int8 要么解成 fp16 | **已有等价实现** | NVFP4 走 ggml int8 MMQ（"sm_75 起"，软件解码 UE4M3/E2M1）：`docs/NVFP4.md:210-219`、`moe_mmq.cu:245-250,482-483`；W4A4 才需要 Blackwell |
| 29 | CUTLASS 2.x SM75 int8 GEMM 按 M 分档选 tile | `csrc/libtorch_stable/quantization/w8a8/cutlass/scaled_mm_c2x_sm75_dispatch.cuh:20-60`（M32/64/128/256 四档，smem 32–64 KB） | 无数字（上游） | 小 M 用窄 tile，省 smem 换占用 | **明确不适用** | Strata 无 CUTLASS / FP8 dense GEMM；dense 走自研 `prefill/gemm.cu`（Turing 默认 FP16 TC，`gemm.cu:782`） |
| 30 | SM75 上对不支持的 CUTLASS FP8 dispatch 回退 | `CHANGELOG.md:97-98`（v0.2.2）、`scaled_mm_entry.cu`/`cutlass.py` | 无数字 | 能力探测失败即走 fallback，不崩 | **明确不适用** | Strata 不读 FP8 权重（模型变体里没有 FP8 dense 档） |
| 31 | FLA 的 gated RMSNorm 在 SM75 上退回 native | `layernorm.py:19,316-322`（`VLLM_SM75_FLA_NATIVE_NORM`） | 无数字；注释称"FLA Triton RMSNorm 的 JIT 在四卡并发首个请求时死锁"（EXL3 资格路线） | 首个请求触发 JIT 编译，四卡互相等 | **明确不适用** | Strata 无 Triton JIT、无 FLA；RMSNorm 是自研 kernel 且不在请求路径里编译 |
| 32 | GDN prefill 的 FlashQLA legacy SM75 kernel | `tools/flashqla_sm75_patches/gdn_forward.cu`（535 行）、`qwen_gdn_linear_attn.py:96-175`（`_flashqla_legacy_forward`） | 无数字 | warp 内 WIDTH=16/32 子组各持状态列 + `__shfl_down_sync` 归约 | **已有等价实现（算法替代）** | chunked wmma FP16 TC：`src/prefill/gdn_chunk.cu`；P3 实测 45K prompt 里 gdn 全部阶段 ≈10%，2× 上限只省 ~5% ⇒ 降级为研究项 |
| 33 | packed varlen：一次 launch 跑多请求的 GDN 扫描 | `gdn_forward.cu`（`gdn_forward_varlen`，cu_seqlens 映射到 grid 的 batch 轴） | 无数字 | 多请求占满 grid 的 y 轴 | **明确不适用** | Strata 的 prefill 按 slot 串行 staged（一个引擎循环一次一个请求的 chunk）：`serve/server.py:1163,1886-1925`；没有"多请求同一次 GDN launch"的放置点 |

### 1.5 调度 / 批处理

| # | 优化项 | fork 位置 | 声称收益与口径† | 原理 | Strata 状态 | 证据 |
|---|---|---|---|---|---|---|
| 34 | `--prefill-batch-barrier`：前沿对齐，让同批 prefill 一起进 decode | `vllm/v1/core/sched/scheduler.py:335-345,638-720`、`engine/arg_utils.py`（新 flag） | 无数字 | 最后一个 prefill step 也采样首 token，所以只挡 decode 太晚；只推进最大剩余 prompt 的前沿 | **明确不适用** | P6 已核：Strata 是固定槽 + 串行 staged read，没有共享批步，前沿逻辑无放置点（`VLLM_PORT_PLAN.md` §P6；`serve/server.py:1163`） |
| 35 | KV 准入失败的请求必须退出前沿（防 livelock） | `scheduler.py:1328-1340`（`_prefill_barrier_admission_blocked`） | 无数字；注释称否则"永久空转的调度环" | 失败请求不能继续占着前沿 | **已有等价实现（待验收）** | S0c 判据 + 用例：`bfe515ca`、`9c671598`；工作区 `serve/server.py:2018`（失败/取消在 finally 清 `mp_staged`） |
| 36 | 新 prefill 不得让已在 decode 的批整步停摆 | `CHANGELOG.md:52-54`（v0.2.2-post1）；`scheduler.py`（`barrier_has_running_prefill` 的反向条件） | 无数字 | 只在有在飞 prefill 时才对 cohort 加前沿 | **已有等价实现** | `BYIELD`（chunk 边界让路）+ `STRATA_PIPED_CONCURRENT`（读在独立线程）；P6 行② |
| 37 | Mamba 块对齐切分 / prefill checkpoint 对齐 | `scheduler.py:355-400`（`need_mamba_block_aligned_split`、`prefill_checkpoint_alignment`）、`mamba/abstract.py:supports_prefill_checkpoint` | 无数字 | 循环状态只能在块边界留 checkpoint | **已有等价实现** | Strata 的 checkpoint 存**整个 session 状态**（含 GDN recurrent + draft ring），不是"可重算的 checkpoint"：`conversation_snapshot.cpp:88-113`、`--prompt-cache-every` |
| 38 | 混合 Mamba prefix-cache 语义（GDN 只允许块对齐命中） | `CHANGELOG.md:16-21`（#241）、`kv_cache_utils.py`（哈希边界 = 组块边界） | 无数字 | 细粒度命中要求后端能恢复 recurrent state | **已有等价实现** | 快照按同一个 `upto` 同时存 target K/V 与 draft ring，位置天然一致：`conversation_snapshot.cpp:88-113` |

### 1.6 Triton / CUDA 图

| # | 优化项 | fork 位置 | 声称收益与口径† | 原理 | Strata 状态 | 证据 |
|---|---|---|---|---|---|---|
| 39 | FULL 图 metadata 原地刷新 + 运行时拓扑校验 | `vllm/v1/worker/gpu/cudagraph_utils.py:60-140`（`_copy_fullgraph_metadata`）、`:591-660`（`run_fullgraph(attn_metadata=...)`） | 无数字；注释称"V2 FULL 图保留捕获期对象，运行时 builder 新建对象，重放前必须把内容拷进捕获拥有的存储" | 捕获拥有的地址不变，内容每步覆盖 | **已有设计等价，缺运行时检查** | `graph.hpp:18-27` 是**规则**（"每步变化的量必须是 device buffer 的数据"），fork 是**校验**（拓扑字段不符直接 raise）。差额见 §3 C4 |
| 40 | V2 mixed prefill+decode warmup 按每池容量分配块 | `vllm/v1/worker/gpu/warmup.py`（`_alloc_blocks(group_id, n)`） | 无数字 | 预热要按每池容量算，否则跳过或越界 | **已有等价实现** | Strata 没有 warmup 概念；图按 `(layer_type, n_tokens)` 惰性捕获（`graph.hpp:89`）。**但"惰性捕获发生在请求里"是缺口** → §2 N2 |
| 41 | `VLLM_ALLOW_MAMBA_SPEC_FULL_CUDAGRAPH`：越过能力检查把 Mamba 投机上 FULL 图 | `vllm/config/compilation.py:1405-1425` | 无数字；自带警告"可能降低输出稳定性" | 接受长度变化会改 recurrent 更新拓扑，通用检查不敢放 | **已有等价实现** | 验窗固定 `kVerifyMaxT` 行、接受数走 device 标量，本来就在图里：`verify.cpp:763,1889-1935` |
| 42 | 图捕获显存 profiling + 上限（profiling 期关 custom all-reduce） | `vllm/v1/worker/gpu_model_runner.py:_profile_cudagraph_memory_impl`、`_is_drafter_rank`、`custom_all_reduce.py:35-37` | 无数字 | 逐图量 delta 再外推；profiling 不建 IPC handle | **未覆盖** | 无"每图 footprint"度量；P2 已把 "graph exec 实际 footprint" 列为待部署窗口实测项 → 并入 §2 N1 |
| 43 | CUDA 13 绑定缺 `cudaGetLastError` 的绕行 | `vllm/v1/kv_offload/cpu/gpu_worker.py:205-220`（ctypes 调 libcudart 清错） | 无数字 | torch 绑定缺函数时用 ctypes | **明确不适用** | Strata 是纯 C++ 引擎，直接用 `cudaGetLastError` |

### 1.7 显存与模型加载

| # | 优化项 | fork 位置 | 声称收益与口径† | 原理 | Strata 状态 | 证据 |
|---|---|---|---|---|---|---|
| 44 | CPU KV offload + pinned mmap 区（`cudaHostRegister`） | `vllm/v1/kv_offload/cpu/{spec,gpu_worker}.py`、`kv_connector/v1/offloading/*` | 无数字；注释称固定失败"仍能传，只是变成非固定 DMA" | KV 分块放 host，按需换入 | **已有等价实现** | `--kv-resident N`（KV 流式：每 QSA 层常驻 N 个 cell，其余在 host）：`generate.cpp:2481,3200-3204,4752-4758`；host 镜像与换入在 `verify.cpp:1816-1825`（`mirror`/`st.host`）、`kv_stream.cu` |
| 45 | SSD prefix KV 持久化 + checkpoint 内容指纹 | `tools/checkpoint_fingerprint.py`（整树 sha256 + stat 一致性）、`launcher.sh:1748-1810`（`disk_kv_transfer_config`）、`CHANGELOG.md:9-14` | 无数字；"跨 host 重启后缓存恢复已验"（4×T10） | 内容指纹绑定目录，跨重启复用 | **已有等价实现** | conversation file 缓存（整状态落盘 + token 前缀校验）：`conversation_file.cpp`、`--prompt-cache`（`generate.cpp:2640`） |
| 46 | 显存利用率档位 0.93–0.96（换槽位） | `profiles/**/*.env`（`GPU_UTIL`） | 档位差 1–3 个槽† | 显存利用率换并发 | **已有等价实现** | `--vram-reserve-mib` + `--expert-cache auto`（P2 的 capacity 讨论）；DETAILS 有对应说明 |
| 47 | drafter 驻最后 stage / TP·PP 拓扑推荐工具 | `tools/recommend_gpu_topology.py`（TP 分组 + PP 顺序 + P2P 检查）、`tests/model_executor/test_qwen3_5_mtp_pp.py` | 无数字 | drafter 只依赖末 stage 的残差 | **已有等价实现** | `generate.cpp:5467`（`on_mtp(last_st ...)`）、`:5519-5529`（每槽 drafter 从末 stage 的槽 session 借上下文长度与 RoPE） |

### 1.8 通信（单卡 / 双卡）

| # | 优化项 | fork 位置 | 声称收益与口径† | 原理 | Strata 状态 | 证据 |
|---|---|---|---|---|---|---|
| 48 | custom all-reduce 扩到 2–16 rank + PCIe 32 KiB 阈值 + same_node 单段 | `csrc/custom_all_reduce.cuh:299-320`（`auto_use_1stage`）、`:325-338`（`REDUCE_CASE(3..16)`）、`custom_all_reduce.py:112` | fork 自述"四张 SM75 PCIe 卡上两段式从 32 KiB 起更快"† | 小包一段直读各 rank 输入，大包两段各自只减一份 | **明确不适用** | 结构原因：Strata 无 TP、全仓无 all-reduce；两卡是层切分 pipeline + peer 专家单向搬（`peer_experts.cpp`）。阈值直觉已由 W8 否掉（跨卡暂存只占窗口 0.46%） |
| 49 | QuickReduce | `csrc/custom_quickreduce.cu`、`quickreduce/`（上游，fork 未改） | 无数字 | 量化后的 all-reduce | **明确不适用** | 同上（无 all-reduce） |

### 1.9 prefix caching

| # | 优化项 | fork 位置 | 声称收益与口径† | 原理 | Strata 状态 | 证据 |
|---|---|---|---|---|---|---|
| 50 | prefix 命中报告排除 draft 池（draft KV 每次准入重建） | `vllm/v1/core/kv_cache_coordinator.py`（`get_prefix_cache_group_ids` 等） | 无数字 | draft KV 不可复用，不该算进 target 命中率 | **明确不适用** | Strata 没有块级命中率 API；缓存命中按会话快照算（`--prompt-cache`），draft ring 在快照里同存 |
| 51 | DFlash2 的 target replay state 跨 prefix 命中保留 | `CHANGELOG.md:34-44`；`spec_decode/dflash/speculator.py:95-110,182-190,845-905`（`num_cached_tokens` + `shift_draft_block_tables`） | 无数字 | 缓存恢复的 token 没走过 target 前向，draft 上下文 KV 是垃圾，必须从 draft 注意力里藏起来 | **已有等价实现** | Strata 快照把 target K/V 与 draft ring 按同一 `upto` 一起存/一起恢复（`conversation_snapshot.cpp:88-113`），另提供 `context()` 补喂没有草稿的上下文行（`dflash2.hpp:65`） |

### 1.10 采样与约束

| # | 优化项 | fork 位置 | 声称收益与口径† | 原理 | Strata 状态 | 证据 |
|---|---|---|---|---|---|---|
| 52 | thinking budget 的跨块 `<think>` 标记修正 | `vllm/v1/sample/thinking_budget_state.py:171-207`（`_longest_marker_prefix_suffix` / `_reset_after_thinking_block`） | 无数字；只是正确性 | 块边界切断起始标记时，重置扫描偏移必须保留重叠前缀 | **已有等价实现（机制不同）** | Strata 按 token 计数封口并注入 `\n</think>\n\n`，不扫描起始标记：`serve/server.py:207-213,5188-5215`。边界差异见 §2 N5 |
| 53 | 命名 tool_choice 的流式包装 + finish_reason | `entrypoints/openai/chat_completion/serving.py:483-486,675-710,1022-1060` | 无数字 | 命名语法只吐参数时不要把 JSON 漏成 content | **已有等价实现** | `forced_call`/`force_text` 在开头就强制函数名，模型只需续参数：`serve/server.py:7262-7264,7514-7516` |
| 54 | `--use-fp64-gumbel`（采样噪声 fp64） | `engine/arg_utils.py:564,922`（上游） | 无数字 | 大词表 argmax 需要更高精度噪声 | **未核实** | `src/kernels/cuda/sampler.cu` 的指数/噪声精度未读 → §4 |

### 1.11 日志 / 指标

| # | 优化项 | fork 位置 | 声称收益与口径† | 原理 | Strata 状态 | 证据 |
|---|---|---|---|---|---|---|
| 55 | 选路一次性 INFO（FA2 graph wrapper、barrier、SM75 spec prefill） | `flashinfer.py:1358-1366`、`scheduler.py:340-345`、`compilation.py:1420-1427` | 无数字 | 启动时把"走了哪条路"说清 | **已有等价实现** | `sm75_series.hpp` 的 7.5 默认 banner + 各子系统启动行；相位耗时表 `generate.cpp:2509` |
| 56 | 逐窗 dump / 对比工具 | `VLLM_DFLASH_TRACE_STEPS`、`VLLM_DFLASH_VERIFIER_TRACE_STEPS`（`spec_decode/utils.py`） | 无数字 | 限步数打印，便于定位接受率 | **已有等价实现** | `STRATA_TAP_FILE=/tmp/t%d.stap` + `tools/server/tap_parity.py compare / merge`（`869c1224`、`483c53f5`，14 例单测） |

### 1.12 工程层（非引擎）

| # | 优化项 | fork 位置 | 声称收益与口径† | 原理 | Strata 状态 | 证据 |
|---|---|---|---|---|---|---|
| 57 | 27 个 profile 档 + launcher（6563 行新增） | `profiles/**`、`launcher.sh` | 每档一张测量表（口径见 §0） | 把测过的组合固化 | **明确不适用（工程层）** | Strata 用 `strata launch --plan` / preset；但 profile 背后的**规则**（KV 位数换槽数、TP 换上下文）值得抄进 C1 指南 → §3 C5 |
| 58 | 构建裁剪（722 个上游文件不在树里）+ `build.sh`/`update.sh` | `CMakeLists.txt`、`build.sh`、`update.sh` | 无数字 | 只留 SM75 路线要的算子 | **明确不适用** | 构建系统不同 |
| 59 | 单请求 profile 工具 | `tools/profile_request.py`（402 行） | 无数字 | 固定 prompt/输出长度与请求 JSON | **已有等价实现** | `tools/server/bench-strata.py`、`--profile` 相位表（`generate.cpp:2509`） |

**计数**：59 项。**已移植 1**（#20 DFlash2，opt-in）、**已有等价实现 30**（含 #6/#9 的"更强"与 #39 的"设计等价"）、
**已评估·不移植 1**（#4）、**明确不适用 21**、**未覆盖 4**（#12、#16、#23、#42，其中 #42 并入 §2 N1）、
**未审计/未核实 2**（#24、#54）。

## 2. 真缺口（按 收益 ÷ 难度 排序）

零权重实验的边界先写清：Strata 能 `--serve` 起一个**不加载模型**的 API（`docs/DETAILS.md:2347`）；纯 host 策略单测在
`tests/core/` 有现成模式（`pin_budget_test.cpp`、`draft_cap_test.cpp`）；kernel 级微基准都在 ≤1 GiB 内。
**凡标"需要权重"的，就是一次已加载模型的短跑，不能算零权重。**

### N1. 定容前把"每卡持久 scratch 清单"扣掉（fork 的两处实现）

- **为什么值得做**：P2 已算出 split 搜索按 1 个 session 定价 ⇒ 生产 6 槽时**每卡预测偏大约 8.7 GiB**；P2 的结论是"没有统一
  预算器"，而 fork 给的解法**不是做预算器**，而是两条小改动：把持久 scratch 变成定容前的减项（`kv_cache_utils.py:3045`），
  以及"同一 device 只留一份、并在 KV 定容前预留"（`flashinfer.py:190-215`）。Strata 的对应面是 `--expert-cache auto` 与
  `--vram-reserve-mib` 的定容公式，今天靠人手填 reserve。
- **最小验证实验（零权重）**：把定容公式抽成纯函数（照 `pin_budget_test.cpp` 的写法），喂 6×192K/IQ3_XXS 参数，算出
  "预测 free"；再拿 `--serve`（不加载模型）打印的基线 free 减掉逐项 scratch 的名义值，两者之差就是漏项清单的长度。
  不需要跑 bench，也不需要权重——要的是部署窗口里一次正常启动的 free 打印做对照。
- **预期风险**：改的是默认显存账；漏项改错会变成"启动看着够、首个长 prompt OOM"。必须先在 `--batch 1` 上对账。

### N2. 图的形状集合在启动时钉死/预热（fork 的 capture-size 不变量）

- **为什么值得做**：fork 把"capture sizes 必须恰为 `(k+1)×batch`，否则不上 FULL 图"写成硬前置
  （`flashinfer.py:106-157`），好处是**捕获永远不在请求里发生**。Strata 的 `GraphRegistry` 是"首次遇到某 key 才捕获"
  （`graph.hpp:89`），捕获（毫秒级 + 一次显存峰值）会落在某个真实请求上；`--spec` × `--batch` 还会放大 key 数。
- **最小验证实验（零权重）**：量一次捕获的耗时与显存峰值，再写一个 host 枚举（spec 1..8 × batch 1..6）列出可达 key
  数，乘出"最坏首请求捕获成本"。注意 `graph.hpp:6-8` 引用的 `bench/micro/graph_capture.cu` **在本树里不存在**
  （`bench/micro/` 只有 `cuda_callback_affinity.cpp`、`fusions_parity.cu`），所以这个计时要么补上该文件，要么照
  `fusions_parity.cu` 的模式写一个 20 行的小 bench。
- **预期风险**：预热会拉长启动、多占图内存（P2 估每对 20–30 MiB），需要上限和"超限就不预热"的回退。

### N3. 用"装进每个池"当 batch 准入的判据（fork 的独立池教训）

- **为什么值得做**：fork 的 independent-pool 修复背后是一条可复用的判据——**"请求必须装进每个池，而不是总量够"**
  （`kv_cache_utils.py` 的 `independent_block_pools` 分支）。Strata 的 6 个槽各有自己的 session arena，准入时若只校验
  "总量还够"，就会出现"半途失败"而不是"干净拒绝"；S0c 那一类卡死正属此列。
- **最小验证实验（零权重）**：照 `tools/server/tests/test_mp_admission_limit.py` / `serve/test_staged_wedge.py` 加一个用例：某槽 session 容量
  小于 prompt，断言引擎回 `BADOPT`/明确错误并清理 `mp_staged`，而不是跑到一半死。
- **预期风险**：判据过严会拒掉本可"分块装"的请求；需要区分"槽容量"与"分块预填的临时占用"。

### N4. 验窗的 K/V 精确性：读量化池 vs 读原始（fork 的 raw-current merge）

- **为什么值得做**：fork 为这件事写了 210 行 kernel，理由是"重读刚写入的压缩行会改 verifier logits"
  （`triton_turboquant_decode.py:645-680`）。Strata 的验窗**正是**"先 append 到量化池、再从池里读回"
  （`verify.cpp:1820-1855` → `:1889-1935`）。差别在于：Strata 的 int8/q4 往返误差比 TQ 的 4-bit 质心小得多，所以
  **收益未知，但问题是可判定的**。
- **最小验证实验（零权重）**：用 `kv_q8_parity`/`kv_hybrid_parity` 的合成数据造 8 行窗口，比较 (a) 从池读回、(b) 直接用原始
  K/V 的 attention 输出 rel RMS 与 top-1 一致性；<50 MB 显存。
- **预期风险**：多一份窗口缓冲（8×2×2×256×2 B ≈ 16 KB/层）可忽略，但"验窗读原始、下一步读池"会引入**两处不一致**，
  接受率 A/B 才能拍板；不要只看数值。

### N5. thinking 封口后的边界（fork 的 split-marker 修法）

- **为什么值得做**：fork 修的是"起始标记跨块"时扫描偏移错位（`thinking_budget_state.py:171-207`）。Strata 按 token 计数封口，
  不需要扫起始标记，但有两个相邻边界没人测过：封口后模型又吐 `<think>`、预算 = 1。
- **最小验证实验（零权重，纯 Python）**：照 `serve/test_parser_hold.py` / `test_reasoning_loop_recovery.py` 加 3 个用例，
  零显存、秒级。
- **预期风险**：几乎没有；最坏是文档口径要改。

### N6. 投机路径的同步次数（fork 的 `nosync` 模式）

- **为什么值得做**：fork 把"去掉多余 stream 同步"做成一个**验证过的峰值吞吐档**，说明在他们那条 TQK8V4 图路线上值钱；
  Strata 的每窗是 `draft_launch → collect(等流) → verify`，`--batch` 下每槽一次还是每窗一次没人数过。
- **最小验证实验**：先静态计数（`src/core/verify.cpp`、`dflash2.cpp`、`generate.cpp` 里的
  `cudaStreamSynchronize`/`cudaEventSynchronize` 逐路径）；**动态需要一个已加载模型的短跑**（部署窗口内、只加计数器，
  不改行为），把"每 token 同步数"和 "drafter idle ms"（`prefill.cpp:1885` 已有打印）一起记。
- **预期风险**：去掉同步而漏掉一条真依赖会直接算错；"collect 必须在 verify 之前"这条不能动，其余按路径逐条核。

### N7. drafter 精度方向（fork：保持 BF16；Strata：`--mtp-q4` 量化）

- **为什么值得做**：接受率是投机收益唯一的杠杆，而两侧默认**方向相反**。fork 的 `VLLM_QWOPUS_MTP_BF16_DRAFT`
  （`qwen3_5_mtp.py`）明确说"不套 target 的量化配置"。
- **最小验证实验**：**零权重做不到**。最便宜的是离线重放：拿被动日志里已记录的 (drafter 分布, target argmax) 对，
  按接受规则比"量化前/后"的接受长度；需要先有被动日志。空间上 drafter 升到 fp16 要 +约 700 MB（P2 的 839 MiB 账里
  dense 111 + 专家 Q2_0 675），**贴住 800 MB 上限**。
- **预期风险**：显存直接吃紧，可能挤掉 expert cache（P2 已指出 MTP drafter 在 cache 定容前分配）。

## 3. 计划文档需要更正的地方

**C1（§P1 设计结论 vs §5 状态表，自相矛盾）**：§P1 第 2 条仍写"现在 `draft()` 同步（`dflash2.cpp:538`），要先加异步
launch/collect"；HEAD 已经有 `draft_launch`/`draft_collect`（`include/strata/core/dflash2.hpp:75,78`，提交 `b009fab5`），
§5 状态表也写着"异步"。读 §P1 的人会以为还差这一步。

**C2（§P6b 的"仍在工作区"已过期）**：§P6b 说"那一行 `yield None` 仍在工作区"。当前工作区里 staged 等待分支**已经**是
`yield None`（`serve/server.py:1983`，带 S0d/F1F2 注释）。同文件另有 4 处 `yield ("ping", None)`（`:5323,5384,5427,5900`），
走的是 `_run` 的 `kind == "ping"` 分支（`:5972,6104`），**与 staged 分支不是同一类**。文档应把两者分开写，否则读者会以为
staged 分支还坏着、或以为那 4 处也该改。

**C3（§1 把 "TurboQuant 本体" 整块判成"已有"，等于没审）**：§1 一行"FP8/INT8 KV、TurboQuant 本体、YaRN → Strata 已有
int8 / q4_0+FWHT / k8v4"把**格式**和**机制**混在一起。fork 的 TQ 在机制层有 6 个独立项（tile #4、e5 key #3、续写阈值 #14、
prefix-combine #15、raw-current verifier #16、图稳定分裂数 #5）。结论可能仍然对（Strata 没有反量化工作区、`cap_` 固定），
但"未审计"不等于"已覆盖"——本文件把这 6 项逐条落了判定。

**C4（§1 "FULL-graph metadata 刷新 → 已等价"差一个检查）**：Strata 的等价是**设计规则**（`graph.hpp:18-27` 的文字约束：
每步变化的量必须是 device buffer 的数据）；fork 的等价物是**运行时校验**（`cudagraph_utils.py:_copy_fullgraph_metadata`
比对 shape/dtype/device 与拓扑字段，不符直接 raise）。差额是"图重放错配会被静默重放" vs "启动/首步就报错"。应写成
"规则等价、检查不等价"，并把"给图的输入加一个 debug 断言"列为可选加固。

**C5（§1 把 profile 体系整体判成"工程层"）**：27 个 `.env` 是 fork 的**结论载体**，它背后有一条规则：**KV 位数换槽数**
（FP8 1×262K / TQ4NC 3×262K / TQK8V4 2×155K；`MAX_NUM_SEQS` 1–4）、**TP 换上下文**。§4 只抄了数字没抄规则。
C1 的配置指南应写成"每卡会话字节 → 可开槽数 → KV 格式"的查表，而不是再列一遍 tok/s。

**C6（§0 的量化面）**：§0 写"fork 独有 42 路径、内容不同 120 文件"。120 与实测一致；**42 不可复现**——本机
`git diff --name-status b23433088b HEAD` 是 **79 新增 / 120 改动 / 722 删除**，且 `vllm/`+`csrc/` 下只有 1 个新增文件。
建议把口径写清（是否只算 `vllm/`、是否算 profiles/tests/tools）。

**C7（§P1 "6 槽 DFlash2 ≈1.5 GiB" 的适用范围）**：fork 的 DFlash2 档确实配到 `MAX_NUM_SEQS=4`（4×T10 TQK8V4 档），但
**公布的数字全是单请求 lane**。所以"1.9× 每请求 decode"只能支撑"打开 DFlash2"，不能支撑"6 槽会线性放大"。A2 的收益
论证应改写为"每请求延迟 ÷ 每实例 87.6 MiB"，而不是外推吞吐。

## 4. 我没能核实的

1. **fork 的全部吞吐数字**（220.84 / 209.35 / 116.04 / 127.86 / 68.64 / 191.89 …）。原始日志在外部 audit 目录，不在本机；
   本文件一律标 †。
2. **`--mtp-q4` 是不是"反向增"**（P2 的判断）。我只读了 P2 的文字，没有逐行核 `dense4_` 的分配路径。
3. **`--use-fp64-gumbel` 的 Strata 对应面**：没读 `src/kernels/cuda/sampler.cu` 的指数/噪声精度，也没找到"种子跨 batch
   可复现"的既有测试。
4. **N4 的量级**：int8/q4 往返对 verifier logits 的影响没有实测；fork 的结论只在它自己的 4-bit 路线成立，不能直接搬。
5. **fork 的 DFlash2 接受率口径**：README 说"high speculative-hit rates 的合成文本"，但没给逐档接受率；`profiles` 的
   表只有 tok/s。
6. **`STRATA_PF_FUSED` 的默认化**（W6 系列）当前只在**工作区**（`include/strata/core/sm75_series.hpp` 未提交），
   本文件的 Strata 状态按工作区写；HEAD 上的行为可能不同。
7. **fork 的 `MTP` 与 `DFlash2` 在 2080 Ti 上的实测显存占用**：只有 `README.md`/`generate` 提示里的 1.2 GiB 这类量级，
   没有逐项账。
8. **`bench/micro/graph_capture.cu`**：`graph.hpp:6-8` 把它当作三条设计规则的测量出处，但该文件不在本树。三条规则本身
   有测量描述，只是复现路径缺了一个文件（与本次审计无关，顺手记下）。
