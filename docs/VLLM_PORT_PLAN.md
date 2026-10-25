# vLLM-2080Ti fork → Strata 移植计划（单一事实源）

> 来源：对 `/home/kali/IdeaProjects/vLLM-2080Ti-Definitive`（v0.2.2-post3，上游基线 `b23433088b`）的
> 全树逐文件比对（`git -C <fork> diff --name-status b23433088b HEAD` = **79 新增 / 120 改动 / 722 删除**；
> `vllm/`+`csrc/` 下只 1 个新增文件 `dflash2_sm75.py`，引擎真增量全在那 120 个改动文件里），与对 Strata 引擎的
> 挂点分析。逐项覆盖矩阵见 `docs/PORT_AUDIT_COVERAGE.md`（59 个优化项）。
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
| FP8/INT8 KV、TurboQuant 本体、YaRN | Strata 已有 int8 / q4_0+FWHT（=TQ 的 Hadamard 核心）/ k8v4，且 `--kv int8` 是生产配置。**这一行原来把 TQ 整块判成"已有"= 没审**：按机制拆成 6 项后逐条落在 `PORT_AUDIT_COVERAGE.md`（tile / e5 key / 续写阈值 128 / prefix-combine / raw-current verifier / 图稳定分裂数），5 项已有等价、1 项（验窗读原始 K/V）是本次新发现的可判定缺口 |
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
  接线的验收钩子已就位：`tools/server/tap_parity.py`（`869c1224`，14 例单测）可以 `compare` 同层两份 dump
  （CLI 窗 vs serve 窗 vs 批窗）或 `merge` 各 stage 的 dump（`STRATA_TAP_FILE=/tmp/t%d.stap`，`%d`=layer_lo）
  再与单 stage 整窗比——这是 A2/B1 之后"taps 接线对不对"的机器判据，不需要权重。
- 风险：IQ3_XXS 生产包上的 draft 接受率未知（fork 数字是 NVFP4/FP8 dense 路线）——先在日志里
  跑被动统计（`passive_windows.py` 类）再切默认。

**设计结论（只读代码分析，2026-10-25，逐条带 文件:行）**：

- df2 的图/状态是**单序列·多 context 行**设计，不是批形状：`n=1..8` 是本轮追加的 context 行数
  （`dflash2.cpp:489-502`），block 恒 8 行 = 一条序列的 anchor+7 mask（`dflash2.hpp:35`）。接 serve/batch
  的正路 = 照抄 `--batch-mtp` 的"每槽一个 drafter 实例 + 槽驻留"（`generate.cpp:5520-5544`）。**前置两项已就位**
  （`b009fab5`）：共享权重 `load(..., shared)`（只共享 blob，head 仍每实例一份）、异步
  `draft_launch`/`draft_collect`（`dflash2.hpp:75,78`；图内 launch、带外取回）；所以 6 槽 = 0.76 GiB + 6×(buffers+head)
  才成立。A1（serve 单序列）已用上这两项；A2 的每槽实例与层切分仍是未做项（无 drafter 权重，未验证）。
- 层切分：drafter 驻**最后 stage**（照 MTP `:5467/:5521-5524`）；taps 过卡最省的做法是**扩 hand-off 布局**
  （`handoff_floats` 加 stage0 的 tap 行，`verify.hpp:194`），与 flag 发布同路、零额外同步。**多 stage 的 taps 已经
  打通**（`0ab5bdd1`）：每 stage 记 `requested ∩ [lb,le)` 子集，`tap_layers_local()` 给槽序、`tap_layers()` 给全局
  并集；`STRATA_TAP_FILE=/tmp/t%d.stap` 的 `%d` = 该 stage 的 `layer_lo`（整模型 0），各 stage 各写一份、可与单
  stage 整窗 dump 逐窗比（`tools/server/tap_parity.py merge|compare`）。字节量：24/24 切分下过卡仅 stage0 的 2 个
  tap = 20 KiB/行、≤160 KiB/窗，47 GB/s 口径 ~3.5 µs——不是瓶颈。
- 分阶段：**A1 serve solo ✅（`40125aa4`）**（gate 去掉 `o.serve`，bind/prepare 在引擎级 `:9167-9211`，解码环换
  df2.draft，prompt 走 `prefill.set_taps/on_taps`，温度>0/penalty/logit_bias/resume 落窗内的请求各自退 MTP）→
  **A2 serve+batch**（去掉 `verify.cpp:2203` 的
  `!batch_rec_`，每槽 taps staging + `prepare`，`batch_step` 参数化 rows_per_slot）→ **B1 层切分 serial**
  （memcpyPeer 搬 taps）→ **B2 pipelined groups**（df2 只读 tap_dev_ + 自己 ring、与 commit 无依赖，draft
  可完全藏在管道下——MTP 做不到）。A1 改动 ~150 行 generate.cpp；A2 ~600 行 + dflash2 ~200 行重构。
- df2 独有红利：7 个提案一次前向产出，接受即链仍有效 → 提案队列跨窗复用（82% 接受率下省 ~40-50% draft 轮，
  需实测），MTP 是每窗必重发（`:5532`）。
- 必须实测才能定：sm_75 的 draft_cap（`draft_cap.hpp:12-19` 对 Ampere 类返回 0）、draft_conf 每槽截多长、
  rows_per_slot×groups、提案复用命中率、draft 轮 GPU ms、IQ3_XXS 包接受率、温度>0 请求退 MTP 的比例。
- 生产配置（`--serve --batch`/多卡）下 df2 **仍不启用**（`generate.cpp:5476-5480` 仍拒 `o.batch>0`/multi_gpu），
  `--serve` 单卡现在可用（A1）；这条移植的收益全部来自"打开它"，不是修 bug。VRAM 账：共享设计实测口径
  ＝ owner（pack 0.76 GiB + head 0.27 GiB + dvocab 0.4 MiB）+ 每实例 ≈87.6 MiB（ring 80.6 + 其它 4.1 + logits 3.2），
  6 槽合计 ≈ **1.5 GiB**（计划里 2.5 GiB 是保守估计），全压最后 stage；顶替 batch_mtp 时回收 slot_mtp 私有 K/V。
- **验证前置（已核实，2026-10-25）**：`dflash2_parity <pack> <case.bin>` 需要 DFlash2 drafter 的
  safetensors+config（`tools/dflash2/mk_parity_case.py` 用 torch 参考生成 case）。服务器上**没有**这份权重
  （`/opt/ai` 下只有 fork 自己的 `quality-smoke-dflash2-*.json` 日志，那是 vLLM 侧的），所以 df2 的 kernel 级
  验证在拿到 drafter 权重前做不了——A1 之前需要用户提供该 checkpoint 或批准在部署窗口内贴。

### P2 · draft/target 显存与容量 accounting 审计（"更强"= 更多槽，直连队列项 C1）

fork 教训：共享池 accounting 曾把 DFlash2 限到约 1/6 容量；修复 = 独立池 + "请求必须装进每个池"。
- 要做：审计 Strata 的 MTP 常驻（708 MB 专家）+ draft KV + DFlash2 ring + verifier KV 的预留是否
  重复计费（`mtp.cpp`、`generate.cpp` 的 VRAM 命令、`pin_budget`、`--peer-reserve-mib`）；能省 = C1
  （6×192K / 8×128K）直接受益。产出"每池需求表"+ 若发现重复预留则改为按池独立 accounting 的 patch。
- 验证：本地策略单测（`tests/core/` 模式）+ 构建 + 部署窗口实测容量。

**审计结果（只读代码，2026-10-25，数字由代码公式推出，MiB=1024²）**：

- 结论：Strata **没有统一显存预算器**。session（KV 池）、MTP drafter、DFlash2、expert cache、verify arena、
  CUDA graph 各自独立 `cudaMalloc`，互不记账。**真重复计费只有 2 处**（双 head 85 MiB×2、每 slot RoPE 表
  重复 48 MiB×6），其余是**记账空洞**（预留公式漏项），不是双扣；`--mtp-q4` 也不是双扣，它是**反向增**
  （多一份 `dense4_`，省不了显存）。
- 生产 6×192K：每卡 1 主 + 6 slot 的 session ≈ **10,126 MiB（9.89 GiB）**＝ KV int8 8,316 + fp32 indexer
  1,008 + GDN 131 + RoPE 336 + 其它缓冲（qsa/moe/block/ple 等）335。22 GB（22,528 MiB）卡扣掉 session 后
  理论剩 12.1 GiB，再扣权重/图/expert cache 自身开销，生产上 cache 实际只剩 ~7 GiB。8×128K =
  **8,873 MiB/卡**（省 1.2 GiB），7×160K = 9,730，6×128K = 6,901；`serve/server.py` 的 `PARALLEL_MAX=8`
  不挡 `--batch 8`。
- MTP drafter ≈ **839 MiB**（dense 111 + 512 专家 Q2_0 675 + draft KV 33.1 + arena 17-20），**另有** draft head
  85.2 与每 slot 的 logits 7.6（单列，别把这 92.8 加进 839）；它在 expert cache 定容**之前**分配 → 真实挤压
  cache（顺序性，非双扣）。`head_logits_` 已按子词表宽度改（省 30.34 MiB/7 个 drafter，见下）。
- 记账空洞（会让预测偏乐观）：split 搜索每 stage 只按 **1 个 session** 定价（`generate.cpp:4578-4581`）→ 生产
  要 7 个 → **每卡预测偏大约 8.7 GiB**，搜索给出的 cache/覆盖率预测不可直接用于 C1 决策；`kDrafterMib` 等只活
  在搜索里（`:4508-4515`），真实末 stage 定容（`:6543`）不记 draft head 与 slot logits。
- 可回收（按性价比）：①**跨 slot 共享 RoPE 表 ~288 MiB/卡**（`session.cpp:69,108-119`）——**已实现**
  （`session_bytes(...,borrow_rope)`/`session_init(...,share_rope_from)`，`ca4d6e85`）；②`--mtp-window 8192`
  ~240 MiB（末 stage，接受率需 A/B）；③`head_logits_` 按子词表宽度 ~30 MiB——**已实现**（`ab7b682a`，实测 30.34 MiB/7 drafter）；④`--draft-vocab en` ~53 MiB（接受率
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
| iq3_xxs gate/up 1280×2560 | 8.0 TOPS（1905 MHz 时 10.4） | **31.9 TOPS** | 36.5 TOPS |
| iq3_xxs down 2560×640 | 8.5 TOPS | 32.4 TOPS | 32.2 TOPS |
| iq4_nl down | 7.5 TOPS | 26.6 TOPS | 26.9 TOPS |

（口径：2080 Ti / build75 / arch 75 / CUDA 13.0，中位。**只有 R=160 可以下结论**：这一列两次独立运行一致
（31.9/32.4/26.6 与 32.0/32.8/26.8，差 ≤1.2%），而 R=626 两次跑出 **36.5→51.5 / 32.2→40.0 / 26.9→31.8**（同一次
运行里 nvfp4 反而慢 1.8×），说明长 R 的分支受升频与显存状态影响太大，不能拿来比较。R=12 那列在"先跑 qsa 顶到
1905 MHz"的对照里是 10.4。
2026-10-25 二次复现：原表写的 9.3/9.8/8.2、39.0/38.3/31.1、45.8/37.4/30.2 的 **R=160 列**复现不出（低 15-18%）；
P3c 一节引用的 31.9/25.9 与这次一致（31.9/26.6），两处数字原来互相矛盾，以这次为准。R=160 的差不是升频导致——
1905 MHz 下仍 31.7。功耗上限当天从 250 W 抬到 280 W，`qsa_rows_bench` 预热后稳定 1905-1920 MHz / 242 W，
`mmq_expert_bench` 全程停在 1350 MHz（突发型 bench 不触发升频）。）
int8 TC 峰值 ≈ 456 TOPS（2080 Ti fp16 228 TFLOPS ×2）、HBM 有效 ≈ 450 GB/s。R=160 时算力 7.0%、
权重带宽 ~10%——**两头都不到 15% ⇒ 循环内反量化（shuffle/查表）是瓶颈**，正是 Marlin 设计的靶点。
生产 45K prompt 的 gemm gate/up 3005 ms 与 bench 速率一致（按 16 专家组 × R≈160 推算 ≈3.2 s ✓），
排除"PCIe 搬运主导"假设（`--pcie-frac` 调参与 `STRATA_MMQ_TAB` 留作 P4 一并扫）。

### P3c · fused_iq 扩展到 Turing 的 IQ3_XXS/IQ4_NL（旗舰移植项，已实现·提速低于 1.5× 门槛）

Strata 已有 Marlin 形制的 fused kernel（`moe_fused_iq.cu`：codebook→shared→int8→s8-MMA，
Turing 分支 `__ldg` 拷贝 + `m8n8k16` + ASTAGES_DEV=2 都在），IQ3_XXS 主体在 sm_120/HIP 已实例化；
Turing 只放行 Q2_0/Q2_0 的理由是注释"codebook 格式在 4 warps/row 下装不进 64 KB shared"——
**smem 估算 WW=2 路径 ~47 KB 可装**（`smem_bytes` 公式 :110），即扩展面可能只是 setup/覆盖判定 +
pick_ww 尊重 WW=2。收益上限：gate/up 18.4% + down 9.2% 相位 × fused 对 MMQ 的倍数。

**结果（本机 2080 Ti，sm_75 构建 `strata-port-20261025/build75`，2026-10-25；代码已提交 `d8ba8593`）**：假设证实——
IQ3_XXS 主体在 Turing 直接可编译可跑，改动只在 setup/覆盖判定：`dev_info` Turing 分支加 IQ3_XXS gate/up +
IQ4_NL down 的 setup（smem 按卡逐 kernel 实测：WW=2 46,592 B 与 WW=4 60,672 B **都装得下**，
fallback 到 WW=2-only 的通路在，本卡未触发）；`native_supported` 放行该对；`experts_native` 只 clamp
未装下的形状。数值（`CUDA_VISIBLE_DEVICES=1 R64_E=16 S20_E=16 STRATA_PF_FUSED=1 ./build75/prefill_fused_iq_test
--only="IQ3_XXS / IQ4_NL" --chunks=20,256,1002`；`R64_E`/`S20_E` 是 **env var**，写成位置参数会被静默忽略并按
E=64/512 跑到 OOM；这个测试自己 `setenv("STRATA_PF_FUSED","1")`，所以"外部不设就该 skip"这件事它证明不了）：fused vs 自身 int8
模型 rel RMS 1.22e-4（worst 3.64e-3），fused vs MMQ rel RMS 1.21e-4（worst 3.64e-3；MMQ vs FP32 1.43e-2），
timing 段 fused vs MMQ 1.8-2.4e-4——全绿。注意 Q2_0/Q2_0：**不设 `STRATA_PF_FUSED` 时仍是 MMQ**（默认 token 路径
逐位不变），但 7.5 卡上设了 `STRATA_PF_FUSED=1` 时它现在也走 fused —— 改动前 7.5 卡要显式
`STRATA_PF_FUSED_SM75=1` 才可能，这次随 W6 的 series 一起默认打开（那 1.31×/1.17× 的 Q2_0 数字就是这条路），
所以对"已开 `STRATA_PF_FUSED=1` 的 7.5 用户"这是一处真实的路径变化，写在这里而不是藏在代码里。
速度（同进程交替中位数，16 专家组，`S20_E=16 --chunks=20,256,1002` ⇒ R≈12/160/626；当日对照
`mmq_expert_bench`：iq3_xxs gate/up R=160 = 31.9 TOPS、iq4_nl down 26.6；2026-10-25 二次复现，见
`docs/PORT_AUDIT_MEASUREMENTS.md`）：

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
默认态（已拍板，2026-10-25）：**保持 opt-in**——`native_supported` 的 env<0（未设 `STRATA_PF_FUSED`）路径在 7.5 卡上
返回 false，token 仍走 MMQ；IQ3_XXS/IQ4_NL 需 `STRATA_PF_FUSED=1`；`STRATA_PF_FUSED_SM75=0` 在任何情况下回 MMQ。
7.5 卡上即使不开 `STRATA_PF_FUSED`，`dev_info` 也会做一次 Q2_0+IQ 的 setup 并打一行 stderr banner（W6 series 的
默认化）——这是**启动日志/驱动调用**层面的变化，数字路径没变。理由：实测 1.13×（生产行制）
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
int8 池、真实几何、无模型，单 QSA 层每次调用 µs）**。口径先说清：这张图的分数随**时钟态**变化很大，冷启动
首跑比预热后慢 +39/+35/+9/+0.3%（attn，T=1..4；T=1/2 明显，T=4 几乎为零），所以下面分成两组，比较只认预热组。

(a) 同一进程冷启动首跑的**分量**（只用来定位开销在哪，不用来算收益；2026-10-25 二次复现：T=1/2 与旧表一致，
T=3/4 全不复现，重测为下表的 44.8/45.0（scores）、32.8/30.9（topk）、68.3/77.6（attn））：

| 阶段 | T=1 | T=2 | T=3 | T=4 | 每行增量 |
|---|---:|---:|---:|---:|---:|
| scores `qsa_block_scores` | 37.2 | 39.9 | 44.8 | 45.0 | +2.6 µs |
| topk `qsa_block_topk` | 39.2 | 40.0 | 32.8 | 30.9 | −2.8 µs |
| append indexer | 2.7 | 2.7 | 2.6 | 2.5 | ±0 |
| **attn over selection (cap 2051)** | 41.2 | 49.4 | 68.3 | 77.6 | **+12.1 µs** |

(b) 预热到 1920/6800 MHz 后 **7 次交错运行取中位**（可信的绝对数，含 P4 tile 扫描的基线列）：

| 阶段 | T=1 | T=2 | T=3 | T=4 | 每行增量 |
|---|---:|---:|---:|---:|---:|
| attn（CHUNK=64） | 29.5 | 36.6 | 62.9 | 77.3 | **+15.9 µs** |
| 层合计（append+scores+topk+attn） | 109.2 | 115.2 | 148.9 | 160.7 | +17.2 µs |

换算：每行 15.9-17.2 µs/QSA 层 → 4 层 stage0 0.06-0.07 ms、8 层 stage1 0.13 ms（该 bench 口径）。

**两条结论**：
1. **Turing 上每行代价的大头是"对选择集做注意力"**（预热态 +15.9 µs/行，冷启动口径 +12.1；scores 只有
   +2.6），且 attn 约占层合计每行增量的 92%——fork 的 BLOCK_KV=2 直觉要落的地方是
   `qsa_decode_attn_batch`。P4 的扫描确实聚焦了它，但结论是**这条路的默认 64 cell 不能动**（见本节末的
   正确性发现），`qsa_select` 只是次要项。
2. `STRATA_QSA_ROWS=1`（`block_scores_rows`）在 Turing **不是单调赢**：scores/ids 与参照逐位相同（8 cases
   0 differ），但 T=1 −1.4 µs、T=2 −2.3 µs、T=3 +0.7 µs、T=4 +7.4 µs —— T≥3 反向。所以**不默认开**；
   若要开，正确形态是"仅 NQ≤2 时走 rows kernel"（当前开关只按 env 判，不看 NQ）。收益上限 ~2 µs/层/窗（<0.5%）。

**变体扫描（2026-10-25，同一 bench、预热到 1920/6800 MHz 后 7 次交错运行取中位，µs）**：

| (CHUNK,THREADS) | attn T=1..4 | 层合计 T=1..4 | vs 基线（attn） |
|---|---|---|---|
| (64,256) | 29.5 / 36.6 / 62.9 / 77.3 | 109.2 / 115.2 / 148.9 / 160.7 | — |
| (128,256) | 40.8 / 42.9 / 53.2 / 57.0 | 130.0 / 133.0 / 141.4 / 147.2 | +38.3 / +17.2 / −15.4 / −26.3 % |
| (256,256) | 70.5 / 72.3 / 77.8 / 90.8 | 178.2 / 175.4 / 183.2 / 185.9 | +139.0 / +97.5 / +23.7 / +17.5 % |
| (32,128) | **illegal memory access** | — | — |

**结论：128/256 在 T=3/4 的“赢”是假的，不能默认。** 正确性用 /tmp 的 bench 副本 dump 输出、与 pristine
`libstrata_kernels.a` 逐位比：stock ≡ (64,256) 且重跑确定；**128/256 rel RMS 13-16×、max abs 36.6（输出范围
±36 vs 基线 ±1.28），256/256 30-35×，32/128 直接崩**。根因：三个 kernel 的 exp/max 相位都只覆盖
`lane` 与 `lane+32`（`qsa_decode_attn.cu:~262` 默认、`:~424` v3、`:~669` pre75——改 tile 时三处都要动，
漏一处就是今天这个 bug）——CHUNK>64 时 64+ cell 把原始 scaled score
当概率、`part_m/part_l` 只统计前 64；CHUNK=32 时读越界。原来那条“G*CHUNK 整除 THREADS”的 static_assert 只查
了整除，放过了非法 tile。

处理：`qsa_decode_attn.cu` 现在 `static_assert(CHUNK == 64 && THREADS == 256)`（默认构建通过、`-D...CHUNK=128`
编译期被拒，已实测 nvcc exit=2），注释写清为什么；要真扫 tile，必须先把 64-cell 相位改成 `CHUNK/64` 子 tile
循环 + running max，并配自己的 parity 门。

**这个 parity 门怎么做（下次改 tile 直接照抄，本次就是这样发现问题的）**：仓库里没有 decode-attn 的正确性测试，
现有 parity 都在同一个 CHUNK 下编译两边、天然查不出 tile 错。做法是把 `src/kernels/qsa_rows_bench.cpp` 复制到
/tmp，在计时循环后把 T=1..4 的 attn 输出 dump 成 float32，把变体 `qsa_decode_attn.o` 与 **pristine
`libstrata_kernels.a`** 各链一次，逐位比：stock 必须与自身重跑一致（确定性），变体与 stock 的 rel RMS/max abs
超阈值即失败。命令模板（2080 Ti/build75，本次实测有效）：

```
nvcc ... --options-file build75/CMakeFiles/strata_kernels.dir/includes_CUDA.rsp -DSTRATA_QSA_ATTN_CHUNK=<CH>      -x cu -c src/kernels/cuda/qsa_decode_attn.cu -o /tmp/p4/<v>/qsa_decode_attn.o
c++ ... build75/CMakeFiles/qsa_rows_bench.dir/src/kernels/qsa_rows_bench.cpp.o /tmp/p4/<v>/qsa_decode_attn.o      -o /tmp/p4/<v>/bench build75/libstrata_kernels.a build75/libstrata_core.a ...   # 变体版；stock 版不换 .o
CUDA_VISIBLE_DEVICES=1 P4_DUMP_DIR=/tmp/p4/<v>/dump /tmp/p4/<v>/bench --ctx 131072 --max-context 131072 --rows
```

其它可用事实：v3 kernel **128 regs/thread → 2 blocks/SM（512 线程 = sm_75 的 50% 占用）**；
`cuobjdump -sass` 里 **0 条 HMMA/IMMA/DP4A**（纯标量 FMA，m8n8k16 与这层无关）；(256,256) 两块要 53.2 KB smem，
超 48 KB 默认 carveout（静态推断 1 块/SM）。

**时钟态警告**：冷启动首跑是 41.2/49.4/68.3/77.6（二次复现值；第一次记的 81.4/95.4 复现不出），预热后是
29.5/36.6/62.9/77.3——冷启动慢 +39/+35/+9/+0.3%（T=1 最大，T=4 几乎为零）。以后这类比较必须预热 + 交错 A/B，
而且要看**启动时 SM 时钟已 ≥1905 MHz**才算预热（跑 5 次预热不够，空闲 3 s 就掉回 300）。

注：`qsa_block_scores` 在 7.5 卡上的**默认路径是 `block_scores_halve_kernel`**（`qsa_select.cu:1723` 分支，
逐位相同），bench 的标签写的 "(multi)" 是历史名——上表的 37.2/39.9/49.6/51.0 实际是 Turing 默认的 halve 路径，
所以这个对比就是 rows vs 生产默认，结论可直接用。


#
**第三次复测（功耗上限 250→280 W 之后，同一 build）**：`qsa_rows_bench --ctx 131072` 预热到 1920 MHz，
attn **29.2 / 35.9 / 62.2 / 76.8 µs**（+15.9 µs/行，与上表预热组一致），层合计 109.8 / 116.7 / 151.0 / 163.9
（+18.0 µs/行；T=3/4 比上表高 1.4-2.0%，因为默认的 scores 现在是 `multi` 内核——Turing 的 halve 属 opt-in 系列）。
结论不变：每行大头仍是 attn，抬功耗上限没有改变这个比例。

**长上下文的钱花在哪儿（2026-10-25 补测）**：ctx 32K/128K/256K 交错 3 轮，`attn over selection` 每次
28.9/29.6/29.1 µs、每行 15.9/15.9/16.7 µs —— **几乎不随 ctx 变**；而 `qsa_block_scores` T=1 是
6.1/35.9/68.0 µs（线性于块数），到 256K 已占该层时间的 48%，层每行增量 15.3→17.3→20.3 µs。所以"长上下文 decode
更贵"成立，但贵在**选块**不在 attn；层每行增量 15.3→17.3→20.3 µs（32K→128K→256K）就是 +33%，按 15.9-17.2 µs/行
去估 229K 的账会偏小。
## P5 · server 侧 Qwen3 parser 正确性修复 → **判定：已覆盖，不移植**（2026-10-25 核实；本节的 `server.py` 行号是
**工作区**（W3 staged-read 已打）的基准，HEAD 上会偏移）

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

用例 `serve/test_staged_wedge.py`（已提交 `bfe515ca`；本地假引擎说 batch+staged 协议，3 例）。**它在 HEAD 上按设计
skip 3 例**（HEAD 的 `serve/server.py` 没有 staged 代码，用例用 `f"BREAD ` 是否出现在 `generate_batched` 里来判），
只有把未提交的 staged-read（W3）放进工作区才真跑——那种状态下 `pytest` 2.0 s 3 passed：

1. 引擎在 BREAD 期间死 → 请求报 `EngineDied`、`mp_staged` 必须清空、重启后下一请求正常准入（S0c 判据）；
2. 引擎在任何请求前就死 → 下一请求重启引擎并照常准入（不卡死）；
3. 全槽占满 → 长请求 BREAD→BRDY 后等槽；释放槽后必须 BADOPT 采用并跑完、`mp_staged` 清空。

**用例抓到一个真缺陷（同批 staged-read 代码里，未提交）**：`serve/server.py` 的 staged 等待分支
`yield "ping", None` —— `generate_batched` 的消费者是 `_run` 的 token 循环（只认 `None` 心跳），元组会被当
token 送进 detokenizer，`TypeError: '>=' not supported between tuple and int`，即"读到一半等槽"的请求必崩
（BRDY 之后 100% 复现）。改为 `yield None` 后三例全绿。用例已提交（`bfe515ca`），**那一行 `yield None` 仍未提交**
——它属别人未提交的 W3 staged-read 改动，随 W3 一起提交更合适，本页只记录判据与结论。（`serve/server.py` 里另有 4 处
`yield "ping", None` 是 `_run` 自己的 `("event", Event)` 协议，不是同一个洞；审查时不要一起"修"。）

另外两处**代码读出来的缺口**（未实测，属 W3 未提交代码，交回 W3 作者）：
- 假引擎不校验 `STRATA_BATCH_STAGED_READ`（真实引擎要它，`generate.cpp:11395`）：用例现在把两个 env 都设上，
  但仍只是"照生产的样子设"，假引擎不读它，所以"生产 env 漏配"这种情况用例证明不了；
- 真实引擎在槽忙时回 `ERR BADOPT: slot busy`，而 `serve/server.py` 是在读 BADM **之前**清 `mp_staged`、失败时
  不发 BCANCEL → 引擎侧 `staged_admit.pending` 残留，后续准入可能被拒（按代码推断，未复现）。

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
| P1 DFlash2 轨道 | **A1 已实现（opt-in）·A2/B1 待做** | 库层已就位：`load(shared)` 共享 pack/blob/head、`draft_launch/collect` 异步、`set_lazy_capture`（`dflash2.hpp:52,72,75,83`）；taps 支持多 stage（`tap_layers_local()/tap_layers()`，`STRATA_TAP_FILE` 的 `%d` 给每 stage 一份 dump）；批窗只填 `tap_dev_`，**不写 dump**（`verify.cpp:3240` 仍限非批窗）；`--draft dflash2` 已接 serve solo 解码环、gate 去掉 `o.serve`（`generate.cpp:5478,9167-9211,15396-15418`）。`ENGINE_SERVES_DFLASH2=False`（`strata_control/drafting.py:146`）。A2 batch（每槽实例+共享权重+异步）→B1 层切分→B2 管道隐藏；验证前置=drafter 权重（服务器没有） |
| P2 accounting 审计 | 已完成（只读）；回收 ①③ 已实现，②④⑤待做 | 无统一预算器、真双扣 2 处（双 head、每 slot RoPE；`--mtp-q4` 是反向增，不算双扣）；6×192K session 10,126 MiB/卡、8×128K 8,873；MTP 839 MiB 在 cache 定容前扣；split 搜索按 1 session 定价 → 预测偏大 ~8.7 GiB/卡；回收清单见 §P2。已落地：①RoPE 跨 session 借用（`ca4d6e85`，48 MiB/session、288 MiB/stage，含 split range 的 release 漏释放修复）、③`head_logits_` 按子词表宽度（`ab7b682a`，30.34 MiB/7 drafter）；**接线（把 slot session 的 donor 传进去）留到 C1 部署窗口**，因为那会改默认显存账 |
| P3 GDN recurrent 移植 | 降级为研究项 | L030 实测 gdn 全部阶段 ≈10%（45K prompt），2× 上限省 ~5% |
| P3b 专家管线定性 | 已完成（数字已复现校正） | bench：IQ3_XXS gate/up R=160 = **31.9 TOPS**（7.0% 峰值 / 10% HBM，二次复现；原表 39.0 复现不出）⇒ 反量化受限 |
| P3c fused_iq Turing IQ3_XXS | 已实现·opt-in（未达 1.5× 门槛，不翻默认） | parity PASS（fused vs MMQ rel RMS 1.2e-4，2080 Ti/build75）；速度 1.16/1.13/1.01× @R=12/160/626 全路；探针 unset=0 / PF_FUSED=1 → 1 / SM75=0 → 0；`STRATA_PF_FUSED=1` 才启用（§P3c） |
| P4 tile 扫描 | 完成·**默认不动** | 每行大头=attn over selection（预热 15.9 / 冷启 12.1 µs/行 vs scores +2.6）；QSAROWS 在 Turing T≥3 反向，不默认开；tile 扫描证明 128/256 与 256/256 **数值是错的**、32/128 崩溃，`static_assert(CHUNK==64 && THREADS==256)` 已锁死（要扫先重写 64-cell 相位）；decode attn 是纯标量 FMA（0 HMMA） |
| P5 parser 修复 | 已覆盖·不移植 | `server.py:5988-6001,6044`、`serve/dsml.py:172,211` + 各自测试（2026-10-25 核实） |
| P6 barrier 对照分析 | 已完成·不移植 | 三洞察两条已有等价机制、第三条=S0c；S0c 用例 `serve/test_staged_wedge.py`（已提交 `bfe515ca`）在**含未提交 staged 代码的工作区** 3 passed（HEAD 上按设计 skip），并抓到 `yield ("ping", None)` 真缺陷（§P6b） |
| 不移植清单的逐条证据复核 | 已完成 | 本文档 §1（2026-10-25 源码核实） |

## 6. 本轮交付与刻意留下的边界（2026-10-25）

已提交：`d8ba8593`（P3c opt-in）、`55c802a5`+`d71b7311`（P4 宏→锁死 64/256 + 扫描结论）、`bfe515ca`（S0c 用例）、
`869c1224`+`483c53f5`（tap 差分/合并工具 + 14 例单测）、`ca4d6e85`（P2 ①RoPE 借用 + release 修复）、
`b009fab5`（dflash2 共享/异步）、`0ab5bdd1`+`1820894f`（verify 批窗/多 stage taps + `%d` per-stage dump）、
`ab7b682a`（P2 ③logits 宽度）、`40125aa4`（P1 A1 serve solo）、`9c671598`（S0c 用例加固）、
`0da05465`（评审修正的文档口径）。

**明确没做、以及为什么**（不把"编译过"当"做完"）：

1. **P1 A2（serve+batch 每槽 drafter）与 B1（层切分）**：库层（共享权重、异步、批窗/多 stage taps、per-stage dump）
   和 A1 都在，A2 本体约 600 行 `generate.cpp`。**没有 drafter 权重**（服务器只有主模型），跑不了
   `dflash2_parity`，也没有任何 e2e 路径能验证 6 个实例的 draft/commit 记账；交一份一次都没运行的 600 行改动
   不算"做完"，所以停在这里。拿到 `draft_model` 的 checkpoint 后按 §P1 的 A2 步骤写，验收钩子已备好
   （`tap_parity compare/merge`）。
2. **P2 ① 的接线**（slot session 传 donor）：会改默认显存账，属"默认路径变更"，按仓库规则要部署窗口实测。
3. **P3c 翻默认**：实测 1.13×（<1.5× 门槛）且数值 2e-4 级不同，不翻。
4. **P4 tile 调参**：扫描证明除 64/256 外的 tile 是错的（128/256 与 256/256 数值错、32/128 崩），宏已锁死；
   真要更快得先重写 64-cell 相位（`CHUNK/64` 子 tile + running max），那是新的实现工作，不是调参。
5. **P1 的吞吐数字**：`220.84 vs 116.04` 是 fork 在 NVFP4/FP8 路线上的数据，Strata 侧只在拿到权重 + 部署窗口后
   才谈得上数字（§4 口径）。
### 6.1 2026-10-25 的全面移植审查（四份报告 + 由此产生的修复）

`PORT_AUDIT_COVERAGE.md`（fork 59 项逐条对照）、`PORT_AUDIT_DEFAULTS.md`（默认路径不变性）、
`PORT_AUDIT_MEASUREMENTS.md`（GPU1 重测）、`PORT_AUDIT_TESTS_CPU.md`（新测试清单）四份报告，改出三个提交：

- `61c1bfc9` 修默认路径副作用与语义边界：7.5 卡不请求 fused 时不再做 `cudaFuncSetAttribute`/打 banner；无 `%d` 的
  层切分不再互相截断 dump（**只有整模型写，切分必须用 `%d`**，该 stage 会在 init 时明说"本 stage 不 dump"）；
  drafter 的 tap 顺序与验证器不一致时关掉 DFlash2 而不是读错槽；`session_bytes`/`session_init` 补回旧形参重载。
  **`Dflash2Drafter::load` 的旧 3 参符号没有补**——`b009fab5` 给它加了成员、`SessionState` 也加了 `rope_donor`，
  类布局已经变了，补个符号只会把"链接失败"换成"静默 UB"；升级后**必须删掉旧的 `.o`/增量构建目录**，
  这一点 `PORT_AUDIT_DEFAULTS.md` §3.4 已写明。
- `b8eb9a73` 三个可回归测试：RoPE 借用的"恰少一张表"（215/312 checks）、MTP logits 宽度（116/183 checks）、
  **QSA 选块默认分派 vs `STRATA_QSA_ROWS=1` 逐位一致**（839,740 个 score float + 61,530 个 id，0 differ，0.88 s）。
  最后一个补的是 P4 里说过"没有现成测试"的洞，并顺带证明 `qsa_rows_bench --rows` 的 0 differ 不能当回归
  （它在 `STRATA_QSA_ROWS=1` 下参照侧也走 rows）。
- `8dbd89da` 只做四份报告入库；**P3b/P4/P3c 的数字校正在 `e7f35829`**（本页 §P3b/§P4/§P3c 已按
  `PORT_AUDIT_MEASUREMENTS.md` 的原始输出逐格改过）。

审查留下的**未做候选**（都在 `PORT_AUDIT_COVERAGE.md` §2 有最小验证实验，按收益÷难度排）：

1. **定容前扣掉每卡持久 scratch**：P2 自己算出的"每卡预测偏大 8.7 GiB"正是这类空洞；纯 host 公式 + 空模型基线可验。
2. **图形状启动时钉死/预热**：`GraphRegistry` 是惰性捕获（`graph.hpp:89`），首次捕获会落在某个真实请求里。
3. **"装进每个池"当批准入判据**：用 `tools/server/tests/test_mp_admission_limit.py` 加一个槽容量不足用例。
4. **验窗 K/V 读量化池 vs 读原始**：fork 为此写了约 210 行 kernel；Strata 现在是 append→读回（`verify.cpp`），
   可用 `kv_q8_parity` 合成数据判定（<50 MB），收益未知。

### 6.2 Turing 默认化（W0/W1/W2/W5/W6）：看过、**不采纳**

工作区里曾有一组把 Turing 系列默认打开的改动（`sm75_series.hpp` 让系列"未设也算开"、
`qsa_prompt_attn.cu` 让 Q4 tensor core 与 v2 寄存器暂存默认开、`moe_fused.cu` 让 MoE 提示内核默认开、
`gemm.cu` 的注释把 BF16→FP16 默认算成已生效）。决定：**移除**，本树回到 opt-in（`STRATA_SM75_PERF=1`）。

理由（按重要性）：

1. **一项都没有在本机测过**。`docs/SM75_PORT_PLAN.md` 自己写着"不在移植期间跑测量/调参"，而仓库规则是
   "只有实测更快才改默认，并给出测量对象"。默认化等于把未测的行为推给每一个 7.5 用户。
2. **三处换的是求和顺序**：Q4 的 m16n8k8 对、v2 寄存器暂存、BF16→FP16 tensor core GEMM——它们不是"逐位相同"，
   而是"数值在 FP32 量级上不同"。这与本轮其它 transplant 的验收口径（逐位/可判定的 parity）不一致。
3. **`gemm.cu` 那处连代码都没改**：只有注释声称"7.5 默认走 FP16 tensor core"，实际条件仍是 7.0–7.4，
   这种"文档改了、代码没改"的状态最危险。
4. 真要默认化，前置条件是本机能跑端到端：`parity_window` + 同一 prompt 的 teacher-forced KL + 冷/热各 3 轮的
   prompt 速度（本仓库对 12.0 卡就是这么做的）。这项工作没有做，所以不采纳，而不是"以后再说"。

保留的部分：`sm75_series.hpp` 新增 `sm75_series_setting()`（区分 unset/0/1），默认语义不变；测试
`mtp_logits_width_test` 现在断言"未设 = 关、`=1` 才开"，把这条决定钉在回归里。

