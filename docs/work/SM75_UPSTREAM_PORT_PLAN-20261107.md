# sm_75 上游优化移植计划（2026-11-07）

**状态：计划。本文不包含任何已实施的优化，也不代表已有收益。**

> **执行状态（2026-11-07 晚）**：C2 已移植（opt-in，默认关）并通过 2×2080Ti 上的 bitwise parity；C1 仍待 A/B；C3 暂缓（生产显式 `--pcie-frac` 绕过自动规则）；C4/C5 确认不适用。执行记录与依据见 [SM75_PORT_RECORD-20261107.md](SM75_PORT_RECORD-20261107.md)。

目标：从上游 Strata 里挑出**能在 sm_75（2 × RTX 2080 Ti 22 GB）上运行、并且对推理速度有真实提升**的改动，选择性移植；其余明确排除，避免把时间和风险花在跑不起来或收益不落在我们形态上的代码上。

## 1. 结论先看

- 上游**没有专门为 sm_75 做的提速提交**。能用的只有「通用 CUDA、且没有门控在 sm_80+」的改动。
- 先做 **1 个零移植的测量**（chunk-major prefill），它可能直接回答“长 prompt 该走哪条路”。
- 真正要移植的是 **2 个中等改动**（FP16 weight cache、PCIe miss share 规则）和 **1 个 3 行改动**（DFlash2 batch slots，前提是生产确实用 DFlash2）。
- **1 个候选要先确认前提**（MMQ group scratch：只有 prompt 路径真走 MMQ 才有意义）。
- 其余全部排除：KVA 改变输出、split engine 工程量大、sm_80+ 独占、Turing 上被编译成 `__trap()` 桩的 F16 路径、非 CUDA 后端、app/安装器。

## 2. 硬约束与判定标准

本树的生产形态（`README.md`、`docs/STATE.md`）：

- Linux x86_64，CUDA **sm_75**，2 × RTX 2080 Ti 22 GB
- Qwen3.8-Flash-Next，6 slots / 2 groups，256K 上下文，INT8 KV
- layer split 24/24，experts 走 PCIe 流式读取
- 本地已有上游没有的并发改动：`STRATA_PIPED_CONCURRENT`、`STRATA_HC_SPLIT`、两路串行等

一个改动**只有同时满足**下面四条才进入移植：

1. 能在 sm_75 上编译并真实运行（不是只有 sm_80+ 的分支）；
2. 端到端有实测提速，或至少有一条可测的机制路径；
3. 可以单变量回滚（env 或 revert 一个提交）；
4. 不改变默认路径的数值结果，除非有质量门并事先批准。

不满足的写入第 6 节的排除清单，不再讨论。

## 3. 上游基线与工作方式

- 上游 `upstream/main` = `54828c7efbcdf9474697e70922d851f6707f4b32`（2026-11-06）
- 分叉点 = `4b9e05a2`（v0.2.6，2026-10-25），之后上游 1,650 个提交
- 临时目录（已验证 shallow clone，135 MB）：

```sh
git clone --depth 1 --branch main https://github.com/Niko1221/Strata.git /tmp/strata-upstream-54828c7e
```

工作规则：

- **不 merge、不 rebase 上游**。两个树已经分叉，文件内容差异很大（例：`src/prefill/gemm.cu` 本树 387 行、上游 1,496 行），cherry-pick 大概率冲突且会带进不需要的路径。
- 每个候选一个本地提交，按文件手工移植，单独可回滚。
- 每个候选先在临时目录里读上游实现，再对照本树改。
- 所有测量在 2080Ti 机器上做（本开发机无 GPU、无 `nvcc`）。

## 4. 候选详情（按执行顺序）

### C1 · chunk-major prefill —— 零移植，先测

| 项 | 内容 |
|---|---|
| 上游提交 | `3eb55adf`（11-02）：CUDA 分层切分默认从 layer-major 改为 chunk-major |
| 上游测量 | 2 × RTX A6000（IQ3_S、MTP、三档驻留），**32K prompt 读取快 37–55%**，8K 持平 |
| 本树现状 | 默认 layer-major（`src/prefill/prefill.cpp` 的 `lm_default_on()`：CUDA 且 `stages >= 2` 默认开） |
| 动作 | 不改代码：独立实例上设 `STRATA_PREFILL_LAYER_MAJOR=0`，与默认做同源码 A/B |
| 收益来源 | prompt 读取路径；我们的 150K 冷读 TTFT 主要耗在这里 |
| 若确认有效 | 把 `lm_default_on()` 改为 CUDA 也返回 false（约 5 行 + 说明），`STRATA_PREFILL_LAYER_MAJOR=1` 保留为 opt-in |
| 风险 | 上游旧默认恰好来自「2 × A4000 + 流式 experts」的测量，与我们的 2080Ti 形态接近；A6000 的数字不能直接套。且我们本地有 PIPED_CONCURRENT / HC_SPLIT，默认组合没测过 |

**这是唯一一个不用移植就能拿到数据的候选，应最先做。**

### C2 · FP16 weight cache（batch slots）

| 项 | 内容 |
|---|---|
| 上游提交 | `d8c94b45`（10-30 引入 opt-in）→ `e3189102`（`auto` 尺寸）→ `88a79a7e`（因 parity 关闭）→ `62853e78`（11-03 在 Linux CUDA + batch slots 默认开，预留 3 GiB，新增 `gemm_w16_parity`） |
| 机制 | 缓存反量化后的 dense 权重，batch slots 下重复 GEMM 不再重复反量化 |
| 本树现状 | 完全没有（`STRATA_PF_W16_CACHE_MIB` 0 个文件）；`gemm.cu` 是 CUDA-only 裁剪版 |
| 移植面 | `include/strata/prefill/gemm.hpp`、`src/prefill/gemm.cu`、`src/program/generate.cpp` 默认、CMake、`src/prefill/gemm_w16_parity.cu` 测试 |
| 上游证据 | `62853e78` 的正文：旧 parity 失败是测试 scratch 太小造成；引擎真实 scratch 下 cache on/off **bitwise** |
| 验收 | 引擎形状 bitwise parity → 6 slots batch A/B → 预留 3 GiB 后不 OOM |
| 风险 | `88a79a7e` 说明它曾经不 bit-exact；移植必须把开关和回退一起带上 |

### C3 · PCIe / CPU miss share 自动规则

| 项 | 内容 |
|---|---|
| 上游提交 | `213f1a39`（11-03）：离散卡默认按 CPU pool 调整；CUDA 分层切分细化各 stage 份额；`e364620c` 两个 tester 开关 |
| 上游数字 | 提交只给了规则（ceiling 0.95、k 从 1.85 到 0.8），**没有实测吞吐** |
| 本树现状 | 有 `STRATA_PCIE_AUTO_SPLIT`，但是 HIP 的 opt-in（`src/program/generate.cpp`），没有自动规则 |
| 移植面 | 新文件 `include/strata/core/pcie_share_rule.hpp`（51 行）+ 测试（63 行）+ `generate.cpp`（38 行改动） |
| 收益假设 | experts 流式读取时 miss share 更贴近 CPU pool；我们的形态对口，但要先测 |
| 回退 | `STRATA_PCIE_AUTO_SPLIT=0`、`STRATA_PCIE_K_RULE=0`、`STRATA_PCIE_SHARE_MAX=0.60` |
| 风险 | 没有上游数字，属于“机制合理、收益待验证”，优先级低于 C1/C2 |

### C4 · DFlash2 batch-slot drafts（条件候选）

| 项 | 内容 |
|---|---|
| 上游提交 | `3e7b8343`（11-02）：分层切分上默认开，单卡保持 opt-in；**3 行改动** |
| 本树现状 | 有 DFlash2 代码（`src/core/dflash2.cpp` 等），但生产是否启用需要确认（`docs/STATE.md` 记的是槽内 MTP 关闭） |
| 前提 | 生产确实使用 DFlash2 作为 drafter；否则直接记“不适用” |
| 收益来源 | batch slots 内的 draft 复用 |
| 风险 | 若生产不用 DFlash2，这 3 行没有意义，不应为了“有改动”而改 |

### C5 · MMQ group scratch（先确认前提）

| 项 | 内容 |
|---|---|
| 上游提交 | `fbf6e1c7`：MMQ 路径的 GU / H / Hq 只存一个 expert group 的行；opt-in、bitwise；`src/prefill/prefill.cpp` +124 行 |
| 上游例子 | **RTX 2060 SUPER（8 GB，sm_75）**，IQ3_XXS，1453 slots：4096-token chunk 占 1198/1325 可借 slots，`--prefill 8192` 回退到 4096 |
| 机制 | 更大 prompt chunk → 每个 expert 跨 PCIe 次数更少 → prompt 更快 |
| 前提 | 本树 prompt 路径是否真的走 MMQ。生产 env 里没有 `STRATA_MMQ_KQ`，需要先在启动日志/代码里确认；不走 MMQ 就跳过 |
| 验收 | bitwise、chunk 上限提升、prompt A/B、decode 非回退 |

## 5. 验收门（每个候选都必须过）

1. **构建**：sm_75-only CMake 构建 + `ctest`，在 2080Ti 机器上（本开发机无 `nvcc`）。
2. **数值**：对应 parity 测试 bitwise（C2 用 `gemm_w16_parity`；C5 用 MMQ parity；C1 要求 greedy 答案逐字一致）。
3. **微基准**：prompt 读取 / decode 分项计时，不只报总吞吐。
4. **端到端**：同源码、同构建选项、同 config、同 fixture；A-B-A；1/2 路 hot/cold 非回退，4 路不回退超过 1%，6 路只要求零错误与资源安全。
5. **质量**：固定种子/ greedy 输出一致；任何故意改变舍入的候选必须单列并事先批准（本计划里 C2 要求 bitwise，KVA 直接排除）。
6. **回滚**：env 或 revert 一个提交，一条命令恢复。
7. **证据**：`docs/work/*-RESULT-日期.md`，写清机器、命令、数字、未跑项；不把“读代码的判断”写成测量。

## 6. 明确排除

| 项 | 理由 |
|---|---|
| KVA（`STRATA_KVA=1`，“Faster long prompts”） | 近似 36–47 层 + 2048-token exact tail，**改变输出**；是质量换速度，未经授权不做 |
| split engine（0.2.9+，`docs/SPLIT_ENGINE.md`） | 全卡切分的新引擎路径，移植量远超本计划；上游 Auto 面向 2 × A6000 / RTX PRO 6000 |
| `STRATA_MMQ_KQ` 默认开（`52ac4e73`） | 门控 compute capability ≥ 8.0 |
| INT8 prompt attention、`GDN_TC`（`1bf5e13e`） | sm_80+ |
| `--prefill auto` 32768 chunk（`aa120255`） | cc ≥ 8.0 |
| fused F16 / warp-specialized（`10c8cc62`） | 上游原文：Turing pass 把 F16 实例编译成 `__trap()` 桩并**从不运行**，sm_75 无收益 |
| PDL、sm_120 系列（`081d1c04` 等） | cc 12.0 |
| HIP / SYCL / Metal / Strix Halo | 本树不构建这些后端 |
| app / 安装器 / 更新器 / 桌面 UI | 本树没有 |
| 12 GB 卡 route prior | 我们是 22 GB，且上游已改回默认关闭 |

## 7. 执行顺序与工作量

| 顺序 | 事项 | 预计 | 前置 |
|---|---|---|---|
| 1 | C1：独立实例 A/B `STRATA_PREFILL_LAYER_MAJOR=0`，记录结果 | 0.5 天 | 2080Ti 机器 |
| 2 | C1 若有效：改默认 + 更新状态文档 | 0.5 天 | 第 1 步 |
| 3 | C2：移植 FP16 cache + parity + 6 slots A/B | 2–3 天 | 第 1 步的基线 |
| 4 | C3：移植 PCIe 规则 + A/B | 1–2 天 | C2 结束（避免叠加变量） |
| 5 | C5：确认是否走 MMQ；走则移植 | 0.5 + 1–2 天 | 启动日志 |
| 6 | C4：确认 DFlash2 是否在用 | 0.5 天 | 生产配置 |
| — | C4/C5 不适用则跳过 | — | — |

合计约 **5–8 个工作日**，主要受 GPU 机器可用性和 A/B 轮次限制。

## 8. 风险与不做的事

- **不同时叠加多个候选**：A/B 必须单变量，否则收益无法归因（`docs/BENCHMARK_PROTOCOL.md`、`CONTRIBUTING.md`）。
- **不把上游数字当自己的**：A6000 的 37–55%、R9700 的收益都不能写进我们的结论。
- **不动生产默认**：任何默认变更先 opt-in 跑通、再同源码 A/B、再灰度；`strata.service` 的回退点保持可用。
- **不碰 KVA / split engine / sm_80+ 路径**，除非用户明确接受质量变化或工程投入。
