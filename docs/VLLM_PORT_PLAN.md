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

### P2 · draft/target 显存与容量 accounting 审计（"更强"= 更多槽，直连队列项 C1）

fork 教训：共享池 accounting 曾把 DFlash2 限到约 1/6 容量；修复 = 独立池 + "请求必须装进每个池"。
- 要做：审计 Strata 的 MTP 常驻（708 MB 专家）+ draft KV + DFlash2 ring + verifier KV 的预留是否
  重复计费（`mtp.cpp`、`generate.cpp` 的 VRAM 命令、`pin_budget`、`--peer-reserve-mib`）；能省 = C1
  （6×192K / 8×128K）直接受益。产出"每池需求表"+ 若发现重复预留则改为按池独立 accounting 的 patch。
- 验证：本地策略单测（`tests/core/` 模式）+ 构建 + 部署窗口实测容量。

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

### P3c · fused_iq 扩展到 Turing 的 IQ3_XXS/IQ4_NL（旗舰移植项，进行中）

Strata 已有 Marlin 形制的 fused kernel（`moe_fused_iq.cu`：codebook→shared→int8→s8-MMA，
Turing 分支 `__ldg` 拷贝 + `m8n8k16` + ASTAGES_DEV=2 都在），IQ3_XXS 主体在 sm_120/HIP 已实例化；
Turing 只放行 Q2_0/Q2_0 的理由是注释"codebook 格式在 4 warps/row 下装不进 64 KB shared"——
**smem 估算 WW=2 路径 ~47 KB 可装**（`smem_bytes` 公式 :110），即扩展面可能只是 setup/覆盖判定 +
pick_ww 尊重 WW=2。收益上限：gate/up 18.4% + down 9.2% 相位 × fused 对 MMQ 的倍数。
- 验证：`prefill_fused_iq_test` 在服务器 sm_75 构建上跑 IQ3_XXS/IQ4_NL parity；bench 对比 MMQ（R=160 ≥1.5× 才默认化）。
- 状态：实现进行中，基线树 `strata-port-20261025/build75`。

### P4 · Turing tile 调参扫描（decode 注意力/选块，测量驱动）

fork 在 Triton TQ decode 上发现 BLOCK_KV=2 对 Turing 寄存器/shared 平衡明显更好。
- 要做：`qsa_decode_attn` CHUNK（现 64 cell/块、256 线程）与 `qsa_select` 变体的微基准扫描
  （服务器 ≤1 GiB 内可跑）；只有胜出 ≥5%（跨 boot 噪声门，`runtime_flag` 同 boot A/B）才动默认。
- 验证：`qsa_rows_bench`/`qsa_select_bench` 扩展 + parity 全绿。

### P5 · server 侧 Qwen3 parser 正确性修复 → **判定：已覆盖，不移植**（2026-10-25 核实）

fork 修复的三件事 Strata 都有等价实现且各有测试：①函数名只在 `tool_start` 发一次
（`serve/server.py:5988-5991`，后续 delta 只带 arguments）；②输出截断在参数流中间时
finish_reason 不给 `tool_calls`、非流式合并时丢弃不完整 call（`server.py:5999-6001,6044` "#211"，
`serve/test_tool_call_unclosed.py`、`test_tool_call_envelope.py`）；③`<think>` 跨块与无 `</think>`
恢复在 `serve/dsml.py:172,211`（`test_parser_hold.py`、`test_reasoning_loop_recovery.py`）。

### P6 · 调度：prefill frontier/barrier 思路（候选，等 S0b 定性后再动）

fork 的 `--prefill-batch-barrier`：cohort 的 prefill_frontier 对齐步长；"最后一个 prefill step 也采样
第一个 token，所以 block-decode-only 太晚"。Strata 对应面 = staged read（BREAD/BADOPT）+ BYIELD + pump 组。
- 现状：生产 S0b（长读暂存时邻居停顿 3.43 s）正在用户循环里定性；**避免同一战场双头改**。
- 动作：先写一页"barrier 语义 ↔ Strata 准入语义"对照分析；等 S0b 有结论再决定是否做策略 patch。

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
| P3c fused_iq Turing IQ3_XXS | 进行中 | 子代理 8b81b340；验收 = parity + fused/MMQ ≥1.5× @R=160 |
| P4 tile 扫描 | 待做 | |
| P5 parser 修复 | 已覆盖·不移植 | `server.py:5988-6001,6044`、`serve/dsml.py:172,211` + 各自测试（2026-10-25 核实） |
| P6 barrier 对照分析 | 待做 | |
| 不移植清单的逐条证据复核 | 已完成 | 本文档 §1（2026-10-25 源码核实） |
