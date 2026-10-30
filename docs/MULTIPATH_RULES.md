# 项目事实与运行口径

> 规则在 [`RULES.md`](RULES.md)，路线在 [`ROADMAP.md`](ROADMAP.md)。本文件只记录服务器事实和项目特有大坑。

## 生产环境

- 主机：`atm@192.168.9.103`，2× RTX 2080 Ti 22.5 GB，Xeon E5-2680 v4，62 GiB RAM。
- 服务：`strata.service`，端口 8080，API key `111`，仅用于当前实验环境。
- 当前形态（2026-10-25 V2a 后）：引擎 0.1.49.1（`84a0d410`）、server `b6b695a1`、
  launcher `0eeee160`、config `6c362d01`。
- 配置：`parallel=6`、`max_context=192000`，W3 staged read 阈值 4096。
- 部署树：引擎在 0.2.2 的 `build/strata`，launcher 指向 `/opt/ai/src/strata-port-v1`。
- 回滚快照：`/opt/ai/tmp/pre-V2-20261025T133346Z/`。

## 操作入口

- 改配置：`/opt/ai/bin/use-config.sh <json>`。
- 装后核对：`verify-running-config.sh`、`/health`、实际进程 cmdline、文件 md5。
- V2 窗口驱动：`tools/server/ops-v2-window.sh`（默认 dry-run）。
- 服务故障：按 [`SERVER_RECOVERY.md`](SERVER_RECOVERY.md) 先恢复，再继续分析。

## 本项目口径

- 4 路聚合的跨 boot 噪声曾达到约 9%；同会话默认低于 3% 不下结论。
- 小读/首字跨 boot 按约 7% 看。
- 一次引擎构建约 10.6 min；冷装载约 165–190 s。把时间用于减少构建和污染，不把重启次数设成额度。
- 测量期间不派子代理，不同时跑重负载。
- 上游 `vLLM-2080Ti-Definitive` 只读。

## 常用检查

```sh
python3 tools/server/preflight.py --offline
python3 tools/server/four-questions.py
sh tools/server/ops-v2-window.sh check
```

历史部署事故和完整命令见 `tools/server/README.md`、`SERVER_RECOVERY.md` 和 `archive/`。
