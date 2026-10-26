# 服务器专用化重构计划（先瘦身，再特化）

日期：2026-10-26。分支：`hw/server-adapt-0.2.3`（HEAD `31082e5e`，领先上游 `upstream/main` 210 个提交，merge-base `4b9e05a2`）。
方向由用户裁决：**混合路线——先做保持可 rebase 的轻度瘦身（阶段 A），再逐步走向深度特化（阶段 B）**。

## 1. 任务定约

```
Objective:
- 把仓库从"多后端、多硬件、面向消费级 PC 的发行版 fork"收敛为"面向本机
  （2× RTX 2080 Ti 22 GB / sm_75 / NVLink NV2 / Xeon E5-2680 v4 / 62 GiB / CUDA 13.0）
  的服务器专用树"，同时保住 Terminal + serve 双轨与可持续 rebase 上游的能力（阶段 A）。

Non-goals:
- 不改生产运行时配置与环境变量开关（STRATA_SM75_PERF 等原样保留）；
- 不动 serve/ 的对外 API 行为与 TUI 交互；
- 阶段 A 不做引擎内部大重构（generate.cpp 一类巨型文件的内部结构最后再动）；
- 不删 docs/ 证据链（台账、日志、归档只加注释不删除）。

Scope:
- Allowed: apps/、packaging/（安装器与 mac 签名）、metal/、sycl/、.github/workflows/、
  CMakeLists.txt、setup.py、src/ 与 include/ 中 HIP/gfx/SM60/ISA-floor 分支、
  strata_cli/ 与 strata_control/ 的后端探测与选择逻辑、Dockerfile、docs/ 的状态注记；
- Out of scope: 生产机上的 /opt/ai 部署树、驱动、引擎构建参数之外的系统状态。

Constraints:
- Terminal + serve 双轨必须保留且功能不回退；
- 每个切片一个 commit（fork 惯例：单改动、可单独 rebase），提交信息遵循 AGENTS.md 中文多行规范；
- 默认行为不变：砍掉的后端不得改变 CUDA/sm_75 路径的产物与运行时行为；
- 生产服务（192.168.2.223:8080）在阶段 A 期间不重启，除非切片需要上机验证，
  且必须先确认回滚快照（/opt/ai/tmp/pre-ctx256k-20261026T075110Z/）可用。

Verification:
- 本机（Windows）：python -m pytest tests/terminal -q（受 jinja2/textual 缺失限制，见 §5）；
- 服务器（权威验证位）：同名套件 + serve 套件 + tools/server/preflight.py --offline +
  four-questions.py + /health + 一次基线对照跑（同 boot 内 A-A，<3% 噪声线内）；
- 构建门：服务器 cmake 配置 + 构建通过（仅 CUDA/sm_75，命令见 docs/SERVER_HW.md）；
- 每个切片必须先复现基线、再改动、再重验，失败即回到最小闭环。

Risks / Unknowns:
- HIP/gfx 守卫散布在 src+include 约 77 个文件，与 CUDA 路径交织：删除必须保持 CUDA 产物逐字节等价（构建哈希对比）；
- 删 apps/ 会牵动 packaging/ 的 acceptance_matrix.py、feed_tool.py、package_terminal.py 与 mac 签名脚本，需要配套小改；
- strata_control/engine_component.py 的 backend 探测（cuda/hip/sycl/metal）与 strata_cli/cli.py 的 BACKENDS 元组是共享代码，
  删除分支时要同步更新测试夹具，否则测试假阴性；
- upstream 后续发布会持续触碰被删文件，rebase 冲突是长期成本（阶段 A 接受，阶段 B 通过 hard-fork 消化）；
- 本机 8080 即生产服务：任何上机验证前先确认不影响线上进程。
```

## 2. 事实基线（2026-10-26 实测）

- 服务器：`ubuntu-server`，`atm@192.168.2.223`（无线网卡 wlx 当前地址；`docs/SERVER_HW.md` 中"已迁至 192.168.3.14、2.223 废弃"的记录已过期，另一地址 3.14 仍可 SSH 到达）。
- 2× RTX 2080 Ti 22528 MiB，driver 595.71.05，compute_cap 7.5，power.limit 280 W；NVLink NV2 每卡 2 链路 × 25.781 GB/s。
- Xeon E5-2680 v4（28 线程、单 NUMA），62 GiB RAM，kernel 7.0.0-38，CUDA 13.0（/usr/local/cuda-13.0）。
- `strata.service` active；`/health`：loaded=true，model=qwen3.8-flash-next-iq3_xxs-abliterated，max_context=262144。

## 3. 阶段 A：轻度瘦身（保持可 rebase）

每个切片 = 一个 commit + 一次局部验证；顺序按风险从低到高。

1. [x] **A0 基线锚点**（2026-10-26 完成）：锚点落盘 `docs/work/evidence/a0-baseline-anchor-20261026.json`（引擎 SHA256、25 个生产开关、/health、回滚快照、硬件实测）。偏差两条：部署树非 git 仓库，指纹改用文件 SHA256；A-A 吞吐对照因内存余量仅约 3 GiB 并入 A1 验证窗口。
2. [ ] **A1 删桌面产品层**：`apps/`（855 文件 / 136k 行）+ `packaging/mac/` 签名 + 三平台安装器脚本 + `packaging/README.md`、`UPDATES.md` 的 app 段落。验证：`python -m pytest tests/terminal -q`（packaging 相关套件）+ 服务器 `strata update --check` 正常。
3. [ ] **A2 删 Metal**：`metal/`（100 文件 / 21k 行）、`.github/workflows/metal-build.yml`、`packaging/mac/` 余项、`engine_component.py` 的 metal 分支。验证：CI 语法检查 + 终端测试套件 + 服务器构建门。
4. [ ] **A3 删 SYCL/Intel Arc**：`sycl/`（254 文件 / 150k 行）、`STRATA_ENABLE_SYCL` 块、`catalog.py` 的 `INTEL_ARC` PCI 表与 Arc 告警分支、`bench_facts.py` 的 sycl 探针。验证：同 A2。
5. [ ] **A4 删 HIP/AMD**：`STRATA_ENABLE_HIP`/`STRATA_HIP_GFX906`/`STRATA_PREFILL_MMQ` 构建选项、`include/strata/platform/hip_compat/`、`hip_extra.h`、`gfx_arch.hpp`/`dp4a.hpp` 及约 77 个文件里的 gfx 守卫（只删 HIP 分支，CUDA 分支保持逐字节等价）。验证：服务器构建产物与 A0 锚点哈希一致（fatbin/cubin 对比）+ 功能 smoke。
6. [ ] **A5 删老 CPU 兜底**：`STRATA_ISA_FLOOR`、`STRATA_PORTABLE`、`STRATA_EXPERIMENTAL_SM60` 与 sm<80 回退（Volta GDN chunk、FP16/FP32 GEMM 回退）。`STRATA_NATIVE_EXPERTS` 的 CPU 专家路径保守处理：A5 只删构建层，引擎内 CPU 兜底留给阶段 B。验证：构建门 + 终端套件。
7. [ ] **A6 CLI/Control 收口**：`cli.py` 的 `BACKENDS` 收敛为 `("cuda", "cpu")`、`engine_component.py` 探测链去 hip/sycl/metal、`bench_facts.py` 探针同步。验证：`tests/terminal` 全绿（服务器）+ `strata doctor --live` 上机。
8. [ ] **A7 文档与 CI 收口**：`Dockerfile` 注释、`docs/` 过期状态注记（SERVER_HW.md 的 IP 条目等）、workflows 清理。验证：`scripts-test.yml` 本地复跑 + 链接检查。

## 4. 阶段 B：深度特化（hard-fork，A 全部落地并稳定一个上游发布周期后启动）

1. [ ] **B1 架构硬编码**：`CMAKE_CUDA_ARCHITECTURES` 默认 75，删运行期 cc 分叉中 >75 的分支（保留防御性断言）。验证：服务器构建 + 基线对照跑（吞吐/TTFT 在 ±3% 内）。
2. [ ] **B2 launch/plan 特化**：面向本机生产形态（parallel=6、256K、W3 staged read）收敛参数组合与预设；保留 `--plan` 的可解释性。验证：`tools/server/check_preset.py` + preflight + 一次 mixed soak。
3. [ ] **B3 服务器原生能力**：NVLink 感知的切分策略、serve 层多实例/调度、`generate.cpp` 内部结构重组。逐项独立切片，不并行。

## 5. 验证协议的已知限制

- 本机缺 `jinja2`、`textual`：`tests/terminal` 有 15 个模块收集失败（1480 个可收集）；serve 套件有 63 个收集错误。**终端与 serve 套件的权威执行位是服务器**（只读验证，不重启服务）。
- 引擎构建只能在服务器做（唯一后端 CUDA/sm_75，一次约 10.6 min）；构建门是 A4/A5 的硬门禁。
- 生产输出不可复现（ledger L-004）：任何性能断言只用同 boot 内 A-A/B-B 对照，阈值 ±3%。

## 6. 续跑点

- 下一步（A0）：在服务器落锚点文件，然后开 A1 分支 `codex/refactor-a1-drop-apps`。
- 每完成一个切片：更新本文件 §3 的完成状态标记，并在 `docs/ITERATION_LOG.md` 记一条。
