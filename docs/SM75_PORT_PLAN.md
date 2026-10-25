# SM75 移植规则与工作项（W0–W8）

> **本页是计划书，不是现状说明。** 写入时按"默认值由代码按 compute capability 决定"设计，最后**没有采纳**：
> 本树里 Turing 系列仍然是 **opt-in**（`STRATA_SM75_PERF=1`），理由与结论见 `docs/VLLM_PORT_PLAN.md` §6.2——
> 这些默认化一项都没有在本机做过端到端测量，而其中三处（Q4 tensor core、v2 寄存器暂存、BF16→FP16 GEMM）
> 换的是**求和顺序**，仓库规则是"只有实测更快才改默认"。正文保留作为决策记录与 fork 侧数据的出处。
>
> 因此下表的 ✅ 表示"当时改过、后来回滚"，现状以 `VLLM_PORT_PLAN.md` §6.2 与代码为准：
> W0/W1/W2/W5/W6 = 未采纳（回到 opt-in）；W3/W7/W8 = 不做（理由见正文）；W4 = 已核对，与默认无关。

**目标**：把参考实现 `vLLM-2080Ti-Definitive`（本地 `/home/kali/IdeaProjects/vLLM-2080Ti-Definitive`）里
**为 SM75 硬件做的源码级适配**逐项落到 Strata 引擎里，让这台服务器按 2080 Ti 的真实能力跑。
**规则：只改源码，不做参数调优**——任何行为开关的默认值由代码按 compute capability 决定，环境变量只保留"关掉"的作用。

**完成判定**：W0–W8 全部落源码；`-DCMAKE_CUDA_ARCHITECTURES=75` 全量编译通过；引擎在主机上能起；
之后统一补测试与测量（移植期间只做编译检查与代码审查）。

---

## 1. 硬件事实（这台机器，2026-10-25 核实）

| 项 | 值 |
|---|---|
| 卡 | 2 × RTX 2080 Ti **22 GB**（TU102，`sm_75`），NVLink（实测 47.05 GB/s 对拷） |
| 限制 | 每 SM **1024 线程**；**无 cp.async**（sm_80+ 才有）；**无 bf16/tf32/fp8/fp4 tensor core**，只有 **fp16 + int8** |
| CPU / 内存 | Xeon E5-2680 v4（14C/28T，单 NUMA）/ 62 GiB |
| CUDA | 13.0（`/usr/local/cuda-13.0`），驱动 595.71.05（锁定） |
| 生产 | `/opt/ai/src/strata-0.2.2`，`strata.service`，6 槽 × 192K，`--kv int8`，`--mtp` + `--spec 8 --mtp-max-t 4`，IQ3_XXS 原生包 |

## 2. 参考实现给出的 SM75 规则（逐条，带出处）

| # | 规则 | 出处 |
|---|---|---|
| R1 | Turing 只有 **fp16 / int8** tensor core：bf16、tf32、fp8、fp4 的 kernel 要么不实例化，要么降到 fp16 | `csrc/libtorch_stable/quantization/marlin/marlin_template.h:259-283`；`csrc/libtorch_stable/quantization/w8a8/cutlass/scaled_mm_c2x_sm75_dispatch.cuh` |
| R2 | sm_80 以下没有 `cp.async` → 用同步 16 B 拷贝替代，barrier 保持不变 | `csrc/libtorch_stable/quantization/marlin/marlin.cuh:52-112` |
| R3 | 每 SM **1024 线程**：launch bounds / occupancy 按架构查表，Turing 单独一行 | `csrc/libtorch_stable/launch_bounds_utils.h:16-20` |
| R4 | Turing 上的量化 GEMM：**stages=2**（不是 4）、W4A16 用 **fp16 累加**、int8 MMA 用 m8n8k16 | `.../marlin/generate_kernels.py:13,226-232`；`marlin_template.h:272-283` |
| R5 | FP8 dispatch 在 sm_75 上必须回退（CUTLASS 2.x SM75 配置） | `CMakeLists.txt:490-557`；`CHANGELOG.md`（0.2.2「Prevents unsupported CUTLASS FP8 dispatch on SM75」） |
| R6 | SM75 多卡：小包走 1-stage，**≥32 KiB 走 2-stage**（PCIe T10 实测）；NVLink 双卡小包 1-stage | `csrc/custom_all_reduce.cuh:290-310` |
| R7 | SM75 没有 FA2/FA3/FA4 → 注意力走 Turing 可用的 kernel 与 KV 量化（TQ4NC / TQK8V4 / int8） | `vllm/v1/attention/backends/fa_utils.py:36`；`profiles/2x2080Ti/` |

## 3. Strata 现状（避免重复移植）

已有、不用再搬的：Turing 系列开关框架（`include/strata/core/sm75_series.hpp`、`tc_sm75.hpp`）、
MoE 提示内核的 Turing 版（同步拷贝 + m8n8k16，`src/prefill/moe_fused.cu:198`、`moe_fused_iq.cu:183`）、
量化 KV（`kv_q4.cu`/`kv_q8.cu`/`--kv int8`/`k8v4`）、CUDA Graph（`src/core/graph.cpp`）、
MTP/DFlash2 投机（`src/core/mtp.cpp`、`dflash2.cpp`）。

真缺口（本计划要改的）：

| # | 缺口 | 现状 |
|---|---|---|
| G0 | Turing 系列要 `STRATA_SM75_PERF=1` 才开 | 已改：**7.5 默认开**（`sm75_series.hpp`），`=0` 关 |
| G1 | sm_75 上 bf16 投影退化成 cuBLAS **SIMT fp32** GEMM（仓库注释：4K prompt 的"五分之一"） | 已改：**7.5 默认走 FP16 tensor core**（`src/prefill/gemm.cu` `bf16_path()`） |
| G2 | 提示路径三个 kernel 在 sm_80 以下**编译为空**、回退慢路径：`qsa_prompt_attn.cu`、`qsa_select.cu`、`native_qsa_score.cu`（tf32→FP32 FMA） | Turing 版存在但 env + 门控；要默认化并核对 `tc_sm75` |
| G3 | MMQ 在 `CC≥Volta` 一律吃 **Ampere** 的 tile 配置（llama.cpp `mmq.cuh` 分派），sm_75 没有自己的配置 | 要加 Turing 配置并接分派（对应 R4） |
| G4 | launch bounds/occupancy 没有 1024 线程/SM 的表（仓库里 258 处 `__launch_bounds__`） | 要按架构查表（对应 R3） |
| G5 | `--kv int8` 解码在 sm_75 上走 dp4a 还是 int8 tensor core 未按硬件分流 | 要按 R7 分流 |
| G6 | MoE 提示内核 Turing 版要 `STRATA_PF_FUSED_SM75=1`（数值变化） | 先接 parity 钩子，再默认化 |
| G7 | 投机解码 K：现网 MTP K=4；参考 DFlash2 默认 **K=7** | 按 sm_75 提高 K，保留回落 |
| G8 | 双卡 P2P 分块阈值按 PCIe 设，未按 NVLink 47 GB/s 校正 | 按 R6 校正 |

## 4. 工作项（顺序即依赖序）

| 项 | 内容 | 主要文件 | 验证 |
|---|---|---|---|
| W0 ✅ | Turing 系列 7.5 默认开 | `include/strata/core/sm75_series.hpp` | 编译；`STRATA_SM75_PERF=0` 仍可关 |
| W1 ✅ | 7.5 的 bf16 投影走 fp16 tensor core | `src/prefill/gemm.cu:782` | 编译；`gemm_bf16_parity`（测试阶段） |
| W2 ✅ | 提示路径 Turing 变体默认开：mode-4 的 m16n8k8 对（`STRATA_PROMPT_ATTN_Q4_TC`）、v2 寄存器暂存（`STRATA_PA_V2T`）；`qsa_select` 各点经 W0 已默认 | `qsa_prompt_attn.cu`、`qsa_select.cu`、`native_qsa_score.cu` | 编译 + 三处 parity 钩子 |
| W3 ✗ | MMQ 的 Turing tile 配置 | — | **不做**：llama.cpp 在 sm_75 上用 `turing_mma` + Ampere 配置，改 tile/stages 属调参、无本机测量依据 |
| W4 ✅ | 1024 线程/SM 上限核对 | 全仓 258 处 `__launch_bounds__` | 已核对：无一处 `threads × blocks/SM` 超过 1024（Turing 可以照原样编译运行） |
| W5 ✅ | int8 KV 解码的 Turing 内核 | `qsa_decode_attn.cu:449` | 由 W0 覆盖：该内核的 Turing 分支现在 7.5 默认启用 |
| W6 ✅ | MoE 提示内核 Turing 版默认开 | `moe_fused.cu`、`moe_fused_iq.cu` | 编译 + `parity_window`（测试阶段） |
| W7 ⏸ | 投机解码 K=7 | — | **暂缓**：生产配置显式传 `--mtp-max-t 4`，改代码默认不生效；提高 K 属调参 |
| W8 ✗ | NVLink 双卡 peer-copy 阈值 | — | **不做**：本机测量跨卡暂存只占窗口 0.46%，不是瓶颈 |

每项一个可回滚改动（未提交 worktree，`git checkout -- <file>` 即回滚）。

## 5. 不做的事

- 不加新的环境变量作为"开启"条件；`=0` 只作回滚开关。
- 不在移植期间跑测量/调参（用户指示：测试与调优放到全部移植完成之后）。
- 不改上游产品文档（`DETAILS.md`、`TERMINAL*`、`QUANTS.md` 等）。

## 6. 节奏与已得到的验证

1. 逐项写源码 → 2. 主机上 sm_75 编译检查（`/opt/ai/src/strata-023-check`，`-DCMAKE_CUDA_ARCHITECTURES=75`）→
3. 全部 W 项完成后再统一：引擎自测、`parity_window`、真机部署与测量。

**已得到（2026-10-25，主机 `/opt/ai/src/strata-023-check/build75`，`-arch=sm_75`）**

| 检查 | 结果 |
|---|---|
| 全量构建（含 `strata`、`strata-q36`、全部 parity 目标） | `EXIT=0`，0 个错误 |
| `bf16_gemv_parity` | 0 failures（bf16 vs fp16 激活差 0.14%） |
| `gemm_bf16_parity` | 0 failures，5 组形状 worst 差 0.000e+00 |
| `qsa_parity` | 0 failures（含 rope scaling、table/native indexer、state 位级相等） |
| `qsa_topk_parity --selftest` | PASS（262144/300001/524288 三档，id 全同） |
| `native_expert_parity --synthetic q2_0/q2_0` | 0 failures（GPU dequant 与 ggml `to_float` 逐位相同） |
| `./strata --version` | `strata 0.1.49.1`（sm_75 构建可运行） |

注意：W6 的 MoE 融合内核在 Turing 上只为 **Q2_0** 专家类型建立（`moe_fused_iq.cpp` 的 `t75` 分支），
纯 IQ3_XXS 模型不会走它——默认化对这类模型无行为变化，这是有意的（内核没准备好就用 MMQ 路径）。
