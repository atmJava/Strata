# 过时源码树清理（2026-10-24T06:48Z）

**保留**（`/opt/ai/src` 下现在只有这四个目录 + 三个原始 tar 包）：

| 目录 | 大小 | 为什么留 |
|---|---|---|
| `strata-0.2.2` | 2.2G | **在线**的服务源码与 `build/strata`（引擎 0.1.47） |
| `llama.cpp-3cf03257` | 175M | 0.2.2 的 ggml 构建依赖（pinned commit） |
| `strata-0.1.40.1` | 1.8G | **运行时依赖**：`.venv`（serve/server.py 的 Python）、`strata-vision`、`data/expert-profile.bin` |
| `server-dashboard-0.2.6` | 2.3M | 控制台的最新版（`/opt/ai/bin/fix-dashboard.py` 指向它） |

**删除**（都不是运行时依赖；已核对 systemd unit、`/opt/ai/bin`、`/opt/ai/config`、文档里无引用）：

| 目录 | 大小 | 内容 |
|---|---|---|
| `strata-0.1.45` | 913M | 0.1.45 引擎 + 本机 TP 补丁 |
| `strata-0.1.45-tp-20261018` | 353M | TP 补丁实验树 |
| `strata-0.2.0-beta` | 1.1G | 0.2.0-beta 干净树 |
| `strata-0.2.0-tp-20261020` | 1.1G | TP 补丁移植到 0.2.0-beta 的树 |
| `strata-official-main-477ac13c` | 381M | 上游 main 快照（0.1.45） |
| `llama.cpp-v0.4.1` | 971M | llama.cpp 运行时（已停止，`config/llama-cpp.env` 一并退役） |
| `server-dashboard-0.1.0 … 0.2.5` | 13M | 控制台旧版本（0.2.6 保留） |

合计释放约 **4.8 GB**（`/opt` 可用从 161 GB 到 166 GB）。

## 从这里抢救出来的东西（本目录）

- `tp/tp-0.2.0-beta.patch`（87K）、`tp/tp-0.1.45.patch`（20K）：
  **本机唯一一处代码级优化**——张量并行 GDN（solo verify window），11 个文件、约 1.1k 行新增
  （`verify.cpp` +301、`generate.cpp` +174、`native_mmvq_split.cu` +105、`tp_dense.cpp` +90、
  一个 286 行的 bench，其余是头文件与 kernel）。实测 **+3% 解码**、每卡多占约 **150 MiB** 显存。
- `experiments/`：TP 实验用的配置与日志（`strata-tp-verify-on.json`、`strata-nospec-off.json` 等）、
  各树的模型配置与启动脚本、`llama-cpp.env.retired`。

## 为什么不合并进 fork（现在的判断）

开发机台账 **L-033**：它是**单流窗口**的 +3%，而生产是 4 槽批处理形态（批槽里按设计不跑 TP）；
移植要跨 0.1.45 → 0.2.2 两个上游版本重做 `verify.cpp` / `generate.cpp`，且改变数值需要过质量门；
收益落在判据③（最低权重）。补丁已归档，将来真要做，成本可估：约 11 个文件、重建 + 2 次重启 + 质量门。
