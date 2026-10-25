# 运行状态（每轮开始先读，结束必更新）

> 上下文丢失、会话重启、换人接手：先读本文件 + `WORK_QUEUE.md` + 上轮 `MULTIPATH_LOG.md` 条目。

- 目标：持续优化循环（`EVERGREEN_LOOP.md`；不设完成，直到用户叫停）；当前主线 = 移植代码的服务器验证战役
  V0→V3（`SELF_ITERATION_PLAN.md` §4）
- 当前批次：**V1 完成**（2026-10-25 21:06，矩阵 5/5 PASS）；下一批 **V2（窗口批，排 00:00 后）**
- 当前项：V2（窗口批：V2-1…V2-4；V2 准备先做）
- 装前静态核（已完成）：0.2.3 server + 生产 config 的 mock 起服务 health 200；0.1.49.1 引擎解析 54 个
  args 无 unknown argument（只报假路径 cannot open）——证据 `/opt/ai/logs/V2-prep-*-20261025T1320Z.log`。
- 下一动作：**V2a 窗口 00:05**（`ops-v2-window.sh install --go`；快照 `/opt/ai/tmp/pre-V2-20261025T131022Z/`
  已 md5sum -c 全过；起不来 `restore --go`；预算 ≤2 重启）；矩阵 `run-matrix.py --batch V2`（V2-0 装后核对 →
  V2-4 warmup → V1-5 显存账 → V2-1 staged 演练）；矩阵后可选 `ops-v2-s0c-kill.sh --go`（S0c 崩溃测试，用第 2 次重启）
- 环境：health 200（2026-10-25 20:46 复核）；引擎 0.1.47 / `383b426a`；server `a3c7df58`；launcher `9b6bf792`；
  config `6c362d01`；W3 启用（阈值 4096）；`batch_slots=6`；config `parallel=6`、54 args（运行进程 59，5 个由 server 注入）

## V1 结果（2026-10-25 12:52–13:06Z，0 重启）

- 实验树 `/opt/ai/src/strata-port-v1` = HEAD `2294d4de`（`serve/server.py` md5 `b6b695a1` = 本机 HEAD）；
  构建 **7m01s**、`BUILD_EXIT=0`、0 error；产物 `build75/strata` = **0.1.49.1**、md5 `84a0d410`。
- 矩阵 `/opt/ai/logs/V1-matrix-20261025T2115Z/`（5/5 PASS）：V1-1 版本戳一致；V1-2 源码树单测
  **61 passed / 10 skipped / 0 failed**；V1-3 `isa_guard_check` Passed 2.36 s；V1-4 默认过 / `CHUNK=128` 被
  static_assert 拒；V1-5 staged wedge 3 passed。
- 首跑 12 failed 的原因是测试 venv 缺 `jinja2`（补后 8 failed）与 `regex`（补后 0）；不是代码缺陷。

## V2a 窗口检查表（00:05，按序）

1. `python3 tools/server/four-questions.py`（有残留进程就停）+ `python3 tools/server/preflight.py`。
2. `sh /opt/ai/tmp/ops-v2-window.sh snapshot --go`（窗口时的新快照，`md5sum -c` 过）——
   再 `install --go`（它用最新快照；起不来 `restore --go`）。
3. 装后核对：`probe-version-stamp.py --expect-engine-md5 84a0d410…` + `verify-running-config.sh` 的 ARGS MATCH
   + 服务日志 `staged read` 计数基线；认进程用 `pgrep -f 'build/strata --serve'`，不看日志最后一行。
4. `python3 tools/server/run-matrix.py --batch V2`（V2-0 → V2-4 → V1-5 → V2-1，6 邻居）。
5. 可选 `sh tools/server/ops-v2-s0c-kill.sh --go`（第 2 次重启；先看到 `staged read sent` 增量才杀）。
6. 落账 L-058、STATE、Gate、§4.2、§11、队列；提交。

## 版本戳基线（V0 实测：`tools/server/probe-version-stamp.py`，2026-10-25 20:46）

| 件 | md5 | 备注 |
|---|---|---|
| engine | `383b426aea84783352c8166c3d93d1be` | 与 R11 记录一致；运行 pid 的 cmdline 指向的就是被 hash 的二进制 |
| server | `a3c7df58611518dcfab01d84b2e7d508` | **R11 记的 `52b84d54` 是 patch 工作树 `/opt/ai/src/strata-mp` 的 hash，不是现网** |
| launcher | `9b6bf792ae6ef3d8367839aca75af07c` | export `STRATA_MP_STAGED_READ=1`、`MIN_TOKENS=4096` |
| config | `6c362d015fd45042db42323fd98d43cd` | `parallel=6`；`verify-running-config.sh` 判 ARGS MATCH、进程晚于配置写入 |

现网 server 是 **hybrid**：0.2.2 + W3 补丁 + R12/R13 + staged-wait `yield "ping"` + S2 `done["metrics"]`，
缺 `46d12fa9` 的两处（`BCANCEL_DRAIN_S`、BRDY 严格解析），且与任何 commit 都不相等——
细节与证据见 `tools/server/patches/README.md` "Production is not the patch tree"。
`docs/MULTIPATH_RULES.md` §1 已按本表更正（旧的 `4f6f635f`/`e8edc558` 是更早部署，非现网）。

## 备份与回滚

- 当前态快照：`/opt/ai/tmp/rollback-V0-20261025T124629Z/`（strata.bin / server.py / run-022.sh / strata-022.json
  + MANIFEST.md5 / MANIFEST.sha256）。`md5sum -c`、`sha256sum -c`、与 live 的 `cmp -s` 全过；
  回滚**演练**：文件级（复制 → 字节核对 → 原子替换 → 再核对）是，**停机级（停服/重启回滚）否**（V0 0 重启，
  留给 V2 窗口）。日志 `/opt/ai/logs/V0-rollback-rehearsal-*.log`。
- `/opt/ai/tmp/mp-w3-A3-*` = **W3 前**的旧快照（引擎 79076000 B / server 513180 B，与现网不同），只作"退回 A"用。

## 指标快照（每轮更新）

| M1 4路聚合 | M2 单路 | M3 长读首 token | M4 邻居停顿 | M5 短读满槽 | M6 接受率 | M7 缓存 | M8 第5请求 |
|---|---|---|---|---|---|---|---|
| 122.1（R10） | 71.0 | 17.9（R11） | **3.43（R11）** | 40.7（R11） | 82.0%（R10） | T4 PASS | 未测 |

- V0 **没有新性能测量**（0 构建 / 0 重启 / 0 生产改动）；上表仍是 R10/R11 的数。
- 日重启：2026-10-25 4/6（V0、V1 各用 0；V2/V3 排到 00:00 后，跨轮日预算重置）
- 未完成采纳项：17（见 `WORK_QUEUE.md`；V0–V3 批次行不计入 backlog）
- 阻塞：无
- 最后更新：2026-10-25T21:13+08:00（V2 准备完成：驱动 + 快照就绪，窗口 00:05）
