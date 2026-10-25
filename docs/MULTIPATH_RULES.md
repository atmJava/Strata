# 多路融合规则 · 项目适配层（MULTIPATH_RULES·v0.3）· 2026-10-25

> **通用规则在 [`PORTING_RULES.md`](PORTING_RULES.md)（v1）**：DoD、每轮 Gate、禁止清单、测量证据、
> 预算、阻塞、收口确认，全部在那里，本文件不重复。
> 本文件只补**本项目特有**的东西：硬件/部署口径、重启与构建预算、真实脚本名、文档落点、子代理规则。
> 与 `RULES.md` 冲突时以 `RULES.md` 为准；本文件只能加严。
> v0.2 的会话事故复盘（为什么有这套）已迁到 `PORTING_RULES.md` 的版本行与 `MULTIPATH_LOG.md` R0–R10，
> 不再写进规则正文，避免把一次事件的上下文带进下一轮。

## 1. 本项目的事实（写死，供每轮自检）

- 生产：`atm@192.168.3.14`，`strata.service` → `/opt/ai/src/strata-0.2.2/run-022.sh`，配置 `strata-022.json`，
  端口 8080，API key `111`；2× RTX 2080 Ti 22.5 GB，Xeon E5-2680 v4，62 GiB。
- 当前形态（2026-10-25 起）：W3 暂存读**生产启用**，阈值 4096；引擎 `4f6f635f`、server `e8edc558`；
  回滚备份 `/opt/ai/tmp/mp-w3-A3-*`（引擎/server/launcher/config 快照）。
- 部署唯一入口：`/opt/ai/bin/use-config.sh <json>`；装后 `verify-running-config.sh`；回滚 = 停服 → cp 回 → use-config。
- 测试从 kali 跑：`MP_BASE_URL=http://192.168.3.14:8080 MP_API_KEY=111 python3 -m pytest tools/server/tests ...`。

## 2. 预算（本项目口径）

- 每轮重启 ≤2 次；第 3 次在复盘写理由。
- 一次引擎构建 ≈10.6 min（`tools/server/README.md`）；构建单独计。
- 4 路聚合噪声地板：跨 boot ≤9%（L-040）只作趋势；同会话 <3% 不下结论。
- 小读/首字跨 boot 按 ~7%（L-031）。

## 3. 每轮 Gate（字段与 `PORTING_RULES.md` §3 一致）

```
### Gate（本轮）
- 轮次：R<n>
- 项：<ID>（≤1 个代码项；测量轮写"测量：<臂>"）
- 验收判据：<可测+阈值+条件>
- 实现：<file:line / 配置键 / 无（测量轮）>
- 测试：`<命令>` → PASS
- 生产：health / 版本或 md5 / 开关；含开工自检
- 证据：<原始日志路径 + 脚本名>
- 回滚：<命令或快照>（演练：是/否）
- 台账：L-<n>
- 剩余未完成项：<n>（逐项列）
- 没测什么：<清单>
```

机器检查：`python3 tools/server/round_gate.py`（默认查最新一条，`--round R11` 查指定轮）；
不 PASS 不许提交。

## 4. 本项目特有禁止

- 上游 `vLLM-2080Ti-Definitive` 只读：不回帖、不开 PR、不提交。
- 生产 `build/strata` 与 `serve/server.py` 只能在快照后覆盖；覆盖用 `cp .new && mv -f`。
- 测量期间不派子代理、不并行跑重活（子代理也是这台机器上的第二路流）。
- 生产不可用时的恢复顺序：`docs/SERVER_RECOVERY.md`。

## 5. 文档落点（本项目文件名）

| 内容 | 文件 |
|---|---|
| 做什么、顺序、臂 | [`MULTIPATH_PLAN.md`](MULTIPATH_PLAN.md) |
| 术语、阈值、矩阵、命名 | [`MULTIPATH_STANDARD.md`](MULTIPATH_STANDARD.md) |
| 通用规则、Gate 模板 | [`PORTING_RULES.md`](PORTING_RULES.md) |
| 本项目适配 | 本文件 |
| 每轮复盘 + Gate | [`MULTIPATH_LOG.md`](MULTIPATH_LOG.md)（最新在上） |
| 结论与数字 | [`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md) |
| 状态、决策、回滚 | [`HANDOVER.md`](HANDOVER.md) §3 / §13 |

## 6. 四问（自检固定项，通用规则未覆盖的项目视角）

① cron/timer 在跑吗；② 有残留进程吗；③ `/opt/ai/logs/vram-perf.csv` 有漂移吗；④ 方向要不要调。

---

**变更记录**
- v0.1 · 2026-10-24 · 初稿（已被替代）。
- v0.2 · 2026-10-25 · DoD + Gate + 禁止清单（通用部分已迁到 `PORTING_RULES.md`）。
- v0.3 · 2026-10-25 · 拆分：通用规则独立成 `PORTING_RULES.md` v1；本文件缩成项目适配层（事实、预算、
  真实脚本名、文档落点、四问），事故复盘移出正文。
