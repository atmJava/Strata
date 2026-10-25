# vLLM-2080Ti fork → Strata 移植计划（单一事实源）

> 来源：对 `/home/kali/IdeaProjects/vLLM-2080Ti-Definitive`（v0.2.2-post3，上游基线 `b23433088b`）的
> 全树逐文件比对（fork 独有 42 路径、内容不同 120 文件），与对 Strata 引擎的挂点分析。
> 本文档只收**经过两侧源码核实**的判定；标注"未实测"的推断不得作为承诺。
> 姊妹文档：`SM75_PORT_PLAN.md`（SM75 适配规则 W0–W8，已近收尾）。本文档是其后的**优化层**。

## 0. 边界（用户约定）

- **不碰生产服务**：`atm@192.168.3.14` 的 `strata.service` 不重启、不重部署、不改配置。
- 验证路径：服务器独立源码树 `-arch=75` 构建 + parity/微基准二进制（显存余量 ~1.3 GiB/卡，
  只够 kernel 级基准）；本地 CPU 构建 + 单测 + 策略测试。
- 一切"上生产"动作 = 产出**可部署分支 + 验证记录**，重启窗口由用户批准。

## 1. 已被 Strata 覆盖 / 结构不适用（不移植，逐条带证据）

| fork 项 | 不移植的理由（已核实） |
|---|---|
| Marlin W4A16 / Marlin-MoE SM75 | 上游能力，fork 只是启用；Strata 已有 llama.cpp MMQ（int4→int8→int8-MMA，Turing 分派）+ 自研 fused int8（`moe_fused*.cu`，7.5 默认）+ `STRATA_PF_WMMA`（shared-mem 反量化 FP16 MMA）三条路 |
| TQK8V4 keys 必须 e5m2 | **不适用**：Strata k8v4 的 K 是 INT8+per-group scale（`kv_stream.hpp:45`、`layer.hpp:318`），无 FP8 动态范围问题 |
| DFlash2 BF16→FP16 数值 codec | **不适用**：Strata DFlash2 走 Q8_0 int8 GEMM + `bf16f` 精确位扩展（`dflash2.cu:31,497`），没有"BF16 权重塞进 FP16 MMA"的溢出/舍入问题 |
| FlashInfer graph-stable FA2 + draft metadata 原地刷新 | Strata 图设计已等价：每 token 变化的量一律走 device buffer（`graph.hpp:22-27`），draft 步骤捕获在图内 |
| FULL-graph metadata 捕获+回放刷新 | 同上：Strata 无"host 侧 metadata 重放"问题 |
| custom all-reduce 2–16 rank / same_node / PCIe 阈值；QuickReduce | **结构不适用**：Strata 无 TP、全仓无 all-reduce；两卡是层切分 pipeline。QuickReduce 上游 CUDA 即不可用 |
| GDN 移出 Inductor / recurrent 顺序保护 | Strata 无 Inductor；图内 kernel 顺序即捕获顺序 |
| CUDA13 `cudaGetLastError` 绕行、Inductor E8M0 backport | Strata server 是纯 Python + 自研 C++ 引擎，不用 torch |
| launcher/profile 体系、构建裁剪、两个无消费方开关 | fork 工程层，非引擎能力 |
| FP8/INT8 KV、TurboQuant 本体、YaRN | Strata 已有 int8 / q4_0+FWHT（=TQ 的 Hadamard 核心）/ k8v4，且 `--kv int8` 是生产配置 |
| SSD prefix KV 持久化（指纹绑定目录） | 思路可留档；Strata 的 PLE `O_DIRECT` + conversation cache 已覆盖主要场景，收益未证 |

## 2. 移植项（按 预期收益 ÷ 难度 排序）

### P1 · DFlash2 上生产轨道（最大速度杠杆，跨引擎证据）

fork 实测（2×2080Ti 22GB NVLink，Qwen3.8-27B NVFP4，MTP4 FP8KV 2×229K 口径见 §4）：
**DFlash2/7 = 220.84 tok/s vs MTP4 = 116.04 tok/s（4K/128）≈ 1.9× 每请求 decode**。
Strata 有 DFlash2 实现（`dflash2.cpp/.cu`，7 drafts + selector）但生产走 `--mtp --mtp-max-t 4`。

- 要做：①`draft_cap.hpp` 按 sm_75 的 cap 与代价模型核对（`STRATA_DF2_*`）；②VRAM 预算：draft 权重 +
  ring + 每槽 draft 的显存从哪让（对照 P2 的 accounting 审计）；③server 侧 `drafting.apply()` 的
  dflash2 路线在 6×192K 预设下的配置预设 + 回滚演练脚本；④K=7 窗口与 `kVerifyMaxT=8` 的布局核对（G7 复活）。
- 验证：本地 `dflash2_parity` + `dflash2_*_bench` 编译过；服务器微基准；**吞吐数字只在用户批准的部署窗口产生**。
- 风险：IQ3_XXS 生产包上的 draft 接受率未知（fork 数字是 NVFP4/FP8 dense 路线）——先在日志里
  跑被动统计（`passive_windows.py` 类）再切默认。

**设计结论（只读代码分析，2026-10-25，逐条带 文件:行）**：

- df2 的图/状态是**单序列·多 context 行**设计，不是批形状：`n=1..8` 是本轮追加的 context 行数
  （`dflash2.cpp:489-502`），block 恒 8 行 = 一条序列的 anchor+7 mask（`dflash2.hpp:35`）。接 serve/batch
  的正路 = 照抄 `--batch-mtp` 的"每槽一个 drafter 实例 + 槽驻留"（`generate.cpp:5520-5544`），但要先给
  `Dflash2Drafter` 加共享权重参数（`load(..., shared)`，照 `mtp.hpp:47-48`）与异步 launch/collect
  （现在 `draft()` 同步，`dflash2.cpp:538`）；否则 6 槽 = 6×0.76 GiB 不成立。
- 层切分：drafter 驻**最后 stage**（照 MTP `:5467/:5521-5524`）；taps 过卡最省的做法是**扩 hand-off 布局**
  （`handoff_floats` 加 stage0 的 tap 行，`verify.hpp:194`；解除 `verify.cpp:788` 的单 stage 限制，各 stage
  记 `requested ∩ [lb,le)` 子集），与 flag 发布同路、零额外同步。字节量：24/24 切分下过卡仅 stage0 的 2 个
  tap = 20 KiB/行、≤160 KiB/窗，47 GB/s 口径 ~3.5 µs——不是瓶颈。
- 分阶段：**A1 serve solo**（去掉 gate 的 `o.serve`，bind/prepare 挪到引擎级 `:9160-9190`，解码环换
  df2.draft，prompt 走 `prefill.set_taps/on_taps`）→ **A2 serve+batch**（去掉 `verify.cpp:2203` 的
  `!batch_rec_`，每槽 taps staging + `prepare`，`batch_step` 参数化 rows_per_slot）→ **B1 层切分 serial**
  （memcpyPeer 搬 taps）→ **B2 pipelined groups**（df2 只读 tap_dev_ + 自己 ring、与 commit 无依赖，draft
  可完全藏在管道下——MTP 做不到）。A1 改动 ~150 行 generate.cpp；A2 ~600 行 + dflash2 ~200 行重构。
- df2 独有红利：7 个提案一次前向产出，接受即链仍有效 → 提案队列跨窗复用（82% 接受率下省 ~40-50% draft 轮，
  需实测），MTP 是每窗必重发（`:5532`）。
- 必须实测才能定：sm_75 的 draft_cap（`draft_cap.hpp:12-19` 对 Ampere 类返回 0）、draft_conf 每槽截多长、
  rows_per_slot×groups、提案复用命中率、draft 轮 GPU ms、IQ3_XXS 包接受率、温度>0 请求退 MTP 的比例。
- 生产配置（`--serve`/`--batch`/多卡）下 df2 **本就不启用**（`generate.cpp:5476-5480`），所以这条移植的收益
  全部来自"打开它"，不是修 bug；VRAM 账：共享权重设计 ≈ 0.76 + 0.2 + 6×0.25 ≈ **2.5 GiB**（全压最后 stage），
  顶替 batch_mtp 时回收 slot_mtp 私有 K/V。

### P2 · draft/target 显存与容量 accounting 审计（"更强"= 更多槽，直连队列项 C1）

fork 教训：共享池 accounting 曾把 DFlash2 限到约 1/6 容量；修复 = 独立池 + "请求必须装进每个池"。
- 要做：审计 Strata 的 MTP 常驻（708 MB 专家）+ draft KV + DFlash2 ring + verifier KV 的预留是否
  重复计费（`mtp.cpp`、`generate.cpp` 的 VRAM 命令、`pin_budget`、`--peer-reserve-mib`）；能省 = C1
  （6×192K / 8×128K）直接受益。产出"每池需求表"+ 若发现重复预留则改为按池独立 accounting 的 patch。
- 验证：本地策略单测（`tests/core/` 模式）+ 构建 + 部署窗口实测容量。

**审计结果（只读代码，2026-10-25，数字由代码公式推出，MiB=1024²）**：

- 结论：Strata **没有统一显存预算器**。session（KV 池）、MTP drafter、DFlash2、expert cache、verify arena、
  CUDA graph 各自独立 `cudaMalloc`，互不记账。**真重复计费只有 3 处**（双 head 85 MiB×2、每 slot RoPE 表
  重复 48 MiB×6、`--mtp-q4` 反向增），其余是**记账空洞**（预留公式漏项），不是双扣。
- 生产 6×192K：每卡 1 主 + 6 slot 的 session ≈ **10,126 MiB（9.89 GiB）**（KV int8 8,316 + fp32 indexer
  1,008 + GDN 131 + RoPE 336），22 GB 卡剩 ~7 GiB 给 expert cache/graph。8×128K = **8,873 MiB/卡**（省 1.2 GiB），
  7×160K = 9,730，6×128K = 6,901；`serve/server.py` 的 `PARALLEL_MAX=8` 不挡 `--batch 8`。
- MTP drafter ≈ **839 MiB**（dense 111 + 512 专家 Q2_0 675 + draft KV 33.1 + arena 17-20 + logits 7.6 + head 85.2），
  在 expert cache 定容**之前**分配 → 真实挤压 cache（顺序性，非双扣）；`--mtp-q4` 不省显存（新增 `dense4_`）。
- 记账空洞（会让预测偏乐观）：split 搜索每 stage 只按 **1 个 session** 定价（`generate.cpp:4578-4581`）→ 生产
  要 7 个 → **每卡预测偏大约 8.7 GiB**，搜索给出的 cache/覆盖率预测不可直接用于 C1 决策；`kDrafterMib` 等只活
  在搜索里（`:4508-4515`），真实末 stage 定容（`:6543`）不记 draft head 与 slot logits。
- 可回收（按性价比）：①**跨 slot 共享 RoPE 表 ~288 MiB/卡**（`session.cpp:69,108-119`）；②`--mtp-window 8192`
  ~240 MiB（末 stage，接受率需 A/B）；③`head_logits_` 按子词表宽度 ~30 MiB；④`--draft-vocab en` ~53 MiB（接受率
  变化）；⑤verify `staging_` blob 上限 ~34 MiB（max_blob 需实测）；⑥CUDA graph LRU 上限（每对 20-30 MiB，需实测）。
- 另两条对 C1 直接有用的现状：`--batch-mtp` 在 2 张 N 卡上**默认被 drop**（`batch_groups.hpp` `resolve()`，
  `nv_serial_faster` 默认 true，`generate.cpp:5295`）——别假设 6 个 slot drafter 在跑；多卡下 `--kv-grow`
  弹性 KV 是硬关闭（`:5196-5212`）。
- 对 C1 的建议：先做 ①+⑥+③（≈320 MiB/卡，正确性风险低），再上 6×192K；8×128K 是更稳的容量点。
- 待部署窗口实测（代码不可定）：每 stage 权重/free 打印（`:5267`）、`expert_cache_mib/slots` INFO 行
  （`:11078-11100`）、`expert_layout().max_blob`、graph exec 实际 footprint、真实 split 边界、IQ3_XXS pack 是否
  落 VRAM、pinned host 副本是否全部成功（否则 drafter KV 各 +198 MiB）。

### P3 · GDN FlashQLA 递归 kernel 研究移植 → **降级为研究项**（2026-10-25 实测占比后）

fork 独有 535 行 CUDA（`tools/flashqla_sm75_patches/gdn_forward.cu`）：warp 内 WIDTH=16/32 subgroup 持
状态列组 + `__shfl_down_sync` 归约，varlen packed 一次 launch 多请求。**fork 无实测加速比**。
Strata 的 GDN prefill 已是 chunked wmma FP16 TC（`src/prefill/gdn_chunk.cu`）——这是**算法替代**不是补能力。
**降级依据（本机实测，L030 日志 `/opt/ai/logs/L030-prefill-timing-20261024T053111Z.log`）**：45K token
prompt 的 GPU 16.3 s 里 gdn 全部阶段 ≈ 10%（gdn 8.6% + conv+gates 1.2% + recurrence）——2× 上限也只省 ~5%，
恰在跨 boot 噪声门上。**专家管线才是长读大头：gemm gate/up 18.4% + gemm down 9.2% + dequant 3.4% +
gather/grouping 1.2% ≈ 32%；hc read 9.2%、qsa attn 9.2%、embed+steps 7.7%、ple 6.2%。**

### P3b · 专家管线定性 → **已完成（本机实测）**：MMQ 的 IQ3_XXS 是**反量化指令受限**，不是带宽受限

`mmq_expert_bench`（本机 2080 Ti，sm_75 构建，2026-10-25，IQ3_XXS case 为本计划新增）：

| 案例 | R=12 | R=160（生产行制） | R=626 |
|---|---|---|---|
| iq3_xxs gate/up 1280×2560 | 9.3 TOPS | **39.0 TOPS / 46.6 GB/s 权重** | 45.8 TOPS |
| iq3_xxs down 2560×640 | 9.8 TOPS | 38.3 TOPS | 37.4 TOPS |
| iq4_nl down | 8.2 TOPS | 31.1 TOPS | 30.2 TOPS |

int8 TC 峰值 ≈ 456 TOPS（2080 Ti fp16 228 TFLOPS ×2）、HBM 有效 ≈ 450 GB/s。R=160 时算力 8.5%、
权重带宽 ~10%——**两头都不到 15% ⇒ 循环内反量化（shuffle/查表）是瓶颈**，正是 Marlin 设计的靶点。
生产 45K prompt 的 gemm gate/up 3005 ms 与 bench 速率一致（按 16 专家组 × R≈160 推算 ≈3.2 s ✓），
排除"PCIe 搬运主导"假设（`--pcie-frac` 调参与 `STRATA_MMQ_TAB` 留作 P4 一并扫）。

### P3c · fused_iq 扩展到 Turing 的 IQ3_XXS/IQ4_NL（旗舰移植项，已实现·提速低于 1.5× 门槛）

Strata 已有 Marlin 形制的 fused kernel（`moe_fused_iq.cu`：codebook→shared→int8→s8-MMA，
Turing 分支 `__ldg` 拷贝 + `m8n8k16` + ASTAGES_DEV=2 都在），IQ3_XXS 主体在 sm_120/HIP 已实例化；
Turing 只放行 Q2_0/Q2_0 的理由是注释"codebook 格式在 4 warps/row 下装不进 64 KB shared"——
**smem 估算 WW=2 路径 ~47 KB 可装**（`smem_bytes` 公式 :110），即扩展面可能只是 setup/覆盖判定 +
pick_ww 尊重 WW=2。收益上限：gate/up 18.4% + down 9.2% 相位 × fused 对 MMQ 的倍数。

**结果（本机 2080 Ti，sm_75 构建 `strata-port-20261025/build75`，2026-10-25，未 commit）**：假设证实——
IQ3_XXS 主体在 Turing 直接可编译可跑，改动只在 setup/覆盖判定：`dev_info` Turing 分支加 IQ3_XXS gate/up +
IQ4_NL down 的 setup（smem 按卡逐 kernel 实测：WW=2 46,592 B 与 WW=4 60,672 B **都装得下**，
fallback 到 WW=2-only 的通路在，本卡未触发）；`native_supported` 放行该对；`experts_native` 只 clamp
未装下的形状。数值（`prefill_fused_iq_test --only="IQ3_XXS / IQ4_NL"`，R64_E=16）：fused vs 自身 int8
模型 rel RMS 1.22e-4（worst 3.64e-3），fused vs MMQ rel RMS 1.21e-4（worst 3.64e-3；MMQ vs FP32 1.43e-2），
timing 段 fused vs MMQ 1.8-2.4e-4——全绿；Q2_0/Q2_0 与其余对在 Turing 上行为不变。
速度（同进程交替中位数，16 专家组，`S20_E=16 --chunks=20,256,1002` ⇒ R≈12/160/626；当日对照
`mmq_expert_bench`：iq3_xxs gate/up R=160 = 31.9 TOPS、iq4_nl down 25.9）：

| R | MMQ 全路（含 gather） | fused | fused/MMQ | MMQ 仅积（无 gather） |
|---|---|---|---|---|
| ≈12  | 0.400 ms | 0.344 ms | **1.16×** | 0.246 ms（fused 0.72×） |
| 160  | 1.111 ms | 0.986 ms | **1.13×** | 0.958 ms（fused 0.97×） |
| ≈626 | 3.269 ms | 3.228 ms | **1.01×** | 3.131 ms（fused 0.97×） |

**未达 1.5×**：Turing 版 fused 的 kernel 本身只到 MMQ 积的水平（fused ≈25 TOPS vs MMQ 积 ≈30），收益全部
来自省掉 gather/swiglu/quantize 步骤。原因（读码+`cuobjdump -res-usage` 佐证，未逐项测）：无 cp.async
（2 阶段裸拷贝 + 全块 barrier）、m16n8k32 用 4 条 m8n8k16 拼、60 KB smem ⇒ 1 block/SM（16 warps）、
128 寄存器贴顶个别 kernel 有 16 B spill；IQ3_XXS 解码段比 Q2_0 fused 多 ~0.24 ms/层（0.986 vs 0.745 ms，
Q2_0/Q2_0 同形制 fused 对 MMQ 为 1.31×/1.17×仅积）。对照 fused 在他卡的优势：R9700（gfx12）同对 1 层
2048 tok MMQ 24.5 vs fused 4.4 ms（5.6×）、8192 tok 31.7 vs 13.2（2.4×），引擎 prompt 4K +16.7%/20K +19.0%
（AMD_HIP.md）；RTX PRO 5000（sm_120）prompt +7.0%/+5.2%/+3.4%（moe_fused_iq.cu 注释）。
默认态（已拍板，2026-10-25）：**保持 opt-in**——`native_supported` 在 7.5 卡上默认仍只给 Q2_0/Q2_0 的既有行为，
IQ3_XXS/IQ4_NL 需 `STRATA_PF_FUSED=1`；`STRATA_PF_FUSED_SM75=0` 在任何情况下回 MMQ。理由：实测 1.13×（生产行制）
≈ 3-4% prompt 时间、低于本计划 1.5× 默认化门槛，且输出数值与 MMQ 有 2e-4 级差异、端到端未在现网测，按仓库
"默认路径字节不变"规则不翻默认。服务器探针实测（2080 Ti/build75）：unset → IQ3_XXS/IQ4_NL=0；`STRATA_PF_FUSED=1`
→ =1；`STRATA_PF_FUSED=1 STRATA_PF_FUSED_SM75=0` → available=0。opt-in 下 parity PASS（fused vs MMQ rel RMS 1.2e-4）。
后续若要真提速：Turing 侧解码段重调（反量化指令是双方共同瓶颈，P3b 定性仍成立）。

### P4 · Turing tile 调参扫描（decode 注意力/选块，测量驱动）

fork 在 Triton TQ decode 上发现 BLOCK_KV=2 对 Turing 寄存器/shared 平衡明显更好。
- 要做：`qsa_decode_attn` CHUNK（现 64 cell/块、256 线程）与 `qsa_select` 变体的微基准扫描
  （服务器 ≤1 GiB 内可跑）；只有胜出 ≥5%（跨 boot 噪声门，`runtime_flag` 同 boot A/B）才动默认。
- 验证：`qsa_rows_bench`/`qsa_select_bench` 扩展 + parity 全绿。

**实测（2026-10-25，服务器卡 1，`qsa_rows_bench --ctx 131072 --max-context 131072 --iters 30 --rows`，
int8 池、真实几何、无模型，单 QSA 层每次调用 µs，3 次中位数）**：

| 阶段 | T=1 | T=2 | T=3 | T=4 | 每行增量 |
|---|---:|---:|---:|---:|---:|
| scores `qsa_block_scores` (multi) | 37.2 | 39.9 | 49.6 | 51.0 | +4.6 µs |
| topk `qsa_block_topk` | 39.2 | 40.0 | 40.4 | 40.5 | +0.4 µs |
| **attn over selection (cap 2051)** | 41.2 | 49.5 | 81.4 | 95.4 | **+18.1 µs** |
| append indexer | 2.7 | 2.7 | 2.6 | 2.5 | ±0 |
| 层合计 | 133.9 | 143.3 | 185.6 | 170.9 | +12.3 µs |

换算：每行 12.3 µs/QSA 层 → 4 层 stage0 0.05 ms、8 层 stage1 0.10 ms（该 bench 口径）。

**两条结论**：
1. **Turing 上每行代价的大头是"对选择集做注意力"（+18.1 µs/行），不是 block scores（+4.6）**——fork 的
   BLOCK_KV=2 直觉要落的地方是 `qsa_decode_attn_batch`（CHUNK=64 是编译期常量，无 env，需变体构建对比），
   P4 的扫描应聚焦它，`qsa_select` 只是次要项。
2. `STRATA_QSA_ROWS=1`（`block_scores_rows`）在 Turing **不是单调赢**：scores/ids 与参照逐位相同（8 cases
   0 differ），但 T=1 −1.4 µs、T=2 −2.3 µs、T=3 +0.7 µs、T=4 +7.4 µs —— T≥3 反向。所以**不默认开**；
   若要开，正确形态是"仅 NQ≤2 时走 rows kernel"（当前开关只按 env 判，不看 NQ）。收益上限 ~2 µs/层/窗（<0.5%）。

注：`qsa_block_scores` 在 7.5 卡上的**默认路径是 `block_scores_halve_kernel`**（`qsa_select.cu:1723` 分支，
逐位相同），bench 的标签写的 "(multi)" 是历史名——上表的 37.2/39.9/49.6/51.0 实际是 Turing 默认的 halve 路径，
所以这个对比就是 rows vs 生产默认，结论可直接用。


### P5 · server 侧 Qwen3 parser 正确性修复 → **判定：已覆盖，不移植**（2026-10-25 核实）

fork 修复的三件事 Strata 都有等价实现且各有测试：①函数名只在 `tool_start` 发一次
（`serve/server.py:5988-5991`，后续 delta 只带 arguments）；②输出截断在参数流中间时
finish_reason 不给 `tool_calls`、非流式合并时丢弃不完整 call（`server.py:5999-6001,6044` "#211"，
`serve/test_tool_call_unclosed.py`、`test_tool_call_envelope.py`）；③`<think>` 跨块与无 `</think>`
恢复在 `serve/dsml.py:172,211`（`test_parser_hold.py`、`test_reasoning_loop_recovery.py`）。

### P6 · 调度：prefill frontier/barrier 思路 → **对照分析完成：不直接移植，结论汇入 S0b/S0c**

fork 的 barrier 三条语义与 Strata 的对应面（两侧源码核实）：

| fork 语义（scheduler.py:336-720） | Strata 对应面 | 判定 |
|---|---|---|
| ①"最后一个 prefill step 也采样第一个 token，block-decode-only 太晚"→ cohort 按 frontier 对齐步长 | Strata 是固定槽 + 串行 staged read（BREAD→BRDY→BADOPT FCFS），adopt 后首 token 天然进批窗口 | **无对应物**：架构不同（连续批 vs 槽位+串行读），frontier 逻辑没有放置点 |
| ②新 prefill 不得让已在 decode 的 batch 整步停摆 | `BYIELD`（chunk 边界让路）+ `STRATA_PIPED_CONCURRENT`（读在独立线程） | **已实现**；残余停摆向量 = `ConcurrentReadSetup` 的互斥矩阵（helper/pcie-dma/staged 任一在场即关并发）——这正是 S0b（邻居停顿 3.43 s）的战场 |
| ③KV 准入失败的请求必须退出 frontier（防 livelock） | staged 队列的失败/取消清理不变量；现状 S0c：引擎死于 BREAD → `mp_staged` 永久遗留 → 之后所有准入卡死 | **同一类 bug**，fork 的修法（失败即出队）= S0c 的验收判据，已在用户队列 |

**结论：barrier 本身不移植（调度架构不同）；它的三个洞察里两个 Strata 已有等价机制，第三个已作为
S0c 在队列里。本项产出即本页。**

### P6b · S0c（staged read 失败/取消清理）用例与一个真实缺陷（2026-10-25）

用例 `serve/test_staged_wedge.py`（本地假引擎说 batch+staged 协议，3 例，`pytest` 2.2 s 全绿）：

1. 引擎在 BREAD 期间死 → 请求报 `EngineDied`、`mp_staged` 必须清空、重启后下一请求正常准入（S0c 判据）；
2. 引擎在任何请求前就死 → 下一请求重启引擎并照常准入（不卡死）；
3. 全槽占满 → 长请求 BREAD→BRDY 后等槽；释放槽后必须 BADOPT 采用并跑完、`mp_staged` 清空。

**用例抓到一个真缺陷（同批 staged-read 代码里，未提交）**：`serve/server.py` 的 staged 等待分支
`yield "ping", None` —— `generate_batched` 的消费者是 `_run` 的 token 循环（只认 `None` 心跳），元组会被当
token 送进 detokenizer，`TypeError: '>=' not supported between tuple and int`，即"读到一半等槽"的请求必崩
（BRDY 之后 100% 复现）。改为 `yield None` 后三例全绿。该行与 `serve/test_staged_wedge.py` 都留在工作区，
随 staged-read（W3/S0c）代码一起提交更合适，本页只记录判据与结论。

失败路径的清理（`finally` 里 `mp_staged = None` + BCANCEL/BCANCELLED）经用例 1 验证有效；用例 3 验证
采用路径不遗留。这也把 fork 的"失败即出队"判据在 Strata 上落成了可回归的用例。

## 3. 验证基线（本机已跑通，2026-10-25）

- 本地：CPU-only 构建 100%；`iq_avx1_parity`（all rows match）、`iq_avx2_parity`（0 failures）。
- 移植基线树（本机 2026-10-25 建立）：服务器 `/opt/ai/src/strata-port-20261025`（= 本地工作区 rsync，
  含未提交的 W3 改动），`build75`：Release / `arch=75` / cuda-13.0 nvcc / `GGML_DIR=/opt/ai/src/llama.cpp-3cf03257`
  / `BUILD_TESTS=ON`；构建 detached（nice 19，`/tmp/port-build-20261025.log`），不动生产目录。
- 服务器（既有记录，`SM75_PORT_PLAN` §6）：`-arch=75` 全量 EXIT=0，5 个 parity 全 0。
- 移植每一项的验收 = 构建 + 对应 parity + 微基准数字（≥5% 且同 boot A/B）+ 本表格状态更新；
  端到端吞吐留给部署窗口，用 `bench-strata.py` / `parity_window.py` 口径。

## 4. 对照基线（fork 实测，引用必须带口径）

口径：prefix cache 关闭、单请求、4K/128 取 3 次中位数、32K/512 跑到完成；2×2080Ti 22GB NVLink；
CUDA 13.0 / torch 2.13 / 驱动 595.91.07；2026-09。

| 路线 | 4K/128 | 32K/512 |
|---|---:|---:|
| Qwen3.8-27B NVFP4 MTP4 FP8KV 2×229K | 116.04 | 109.72 |
| 同上 TQ4NC 3×262K（1.79× KV token） | 127.86 | 68.64（长上下文 −37%） |
| Qwen3.8-27B NVFP4 **DFlash2/7** FP8KV | **220.84** | 209.35 |
| Qwen3.6-35B-A3B-FP8（无投机，262K FP16KV） | 113.63（prefill 6690） | 105.92（prefill 5941） |

Strata 现网（`STATE.md` 快照，2026-10-25）：单路 71.0、4 路聚合 122.1 tok/s、接受率 82.0%、
长读首 token 17.9 s。**注意口径差异**（模型量化、槽数、prefix cache、投机方法都不同），
此表只用于定位"Strata 在哪个环节落后"，不用于宣称胜负。

## 5. 状态表（每项完成即更新）

| 项 | 状态 | 证据 |
|---|---|---|
| P1 DFlash2 轨道 | 待做（设计分析中） | 引擎侧 `--draft dflash2` 现仅 CLI spec loop（`generate.cpp:5448`），`ENGINE_SERVES_DFLASH2=False`（`strata_control/drafting.py:147`）；serve/batch/层切分集成是真实工作量 |
| P2 accounting 审计 | 进行中 | 子代理审计 MTP/DFlash2/KV/expert 池预留 |
| P3 GDN recurrent 移植 | 降级为研究项 | L030 实测 gdn 全部阶段 ≈10%（45K prompt），2× 上限省 ~5% |
| P3b 专家管线定性 | 已完成 | bench：IQ3_XXS gate/up R=160 = 39.0 TOPS（8.5% 峰值 / 10% HBM）⇒ 反量化受限 |
| P3c fused_iq Turing IQ3_XXS | 已实现·opt-in（未达 1.5× 门槛，不翻默认） | parity PASS（fused vs MMQ rel RMS 1.2e-4，2080 Ti/build75）；速度 1.16/1.13/1.01× @R=12/160/626 全路；探针 unset=0 / PF_FUSED=1 → 1 / SM75=0 → 0；`STRATA_PF_FUSED=1` 才启用（§P3c） |
| P4 tile 扫描 | 待做 | |
| P5 parser 修复 | 已覆盖·不移植 | `server.py:5988-6001,6044`、`serve/dsml.py:172,211` + 各自测试（2026-10-25 核实） |
| P6 barrier 对照分析 | 待做 | |
| 不移植清单的逐条证据复核 | 已完成 | 本文档 §1（2026-10-25 源码核实） |
