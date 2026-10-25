# 运行状态（每轮开始先读，结束必更新）

> 上下文丢失、会话重启、换人接手：先读本文件 + `WORK_QUEUE.md` + 上轮 `MULTIPATH_LOG.md` 条目。

- 目标：持续优化循环（`EVERGREEN_LOOP.md`；不设完成，直到用户叫停）；当前主线 = 移植代码的服务器验证战役
  V0→V3（`SELF_ITERATION_PLAN.md` §4）
- 当前批次：**V3-1 部分完成**（2026-10-25 14:23–14:31Z：8×128K 装得下，8 路聚合 +8.6%/单路 −5.8%；4 路 M1 对比没测成；生产保持 6×192K）；下一动作 **安静窗口补 4 路 M1 两档对比**
- 当前项：V3（容量与投机批；V3-1 差 4 路口径）
- 下一动作：**安静窗口补 4 路（M1）两档对比**——用 `/metrics` 的 `last_request` 年龄确认没有外部流量，
  再 `BENCH_N=4` 各 3 跑（6×192K 与 8×128K）；拿到数才能判预设。没有安静窗口就改做 S2（每请求时延指标）
  或 S0b 的机制性改进（长读分块让出）。
- 环境（V2a 装后实测，2026-10-25 13:52Z）：health 200；引擎 **0.1.49.1** / `84a0d410`；server **`b6b695a1`**
  （`/opt/ai/src/strata-port-v1/serve/server.py`）；launcher `0eeee160`；config `6c362d01`（未动）；
  W3 启用（阈值 4096）；`batch_slots=6`；config `parallel=6`、54 args（运行进程 59，5 个由 server 注入）；
  `ARGS : MATCH`、`ENV : DIFFERENT` 只有 launcher 的 `STRATA_MP_STAGED_READ_MIN_TOKENS`。
- **V3-1 结果（部分）**：8×128K 装得下（`batch_slots 8`、`expert_slots 17437`、`vram_free 1011`）；
  6 路聚合 138.7 / 单路 75.2 vs 8 路聚合 150.6 / 单路 70.8（路数不同，聚合差 +8.6% 不等于 M1 更好；单路 −5.8%）；
  **4 路 M1 对比没测成**（BENCH_N=4 跑 9 min 0 rep 后被杀）；生产保持 6×192K。证据 `bench-results.jsonl`。
- **V2c 结果**：~22K 读期间邻居停顿 A（piped 1）中位 10.87 s / B（0）10.91 s，**+0.37% 无差异**；
  停顿≈读时长本身（11.5 s 读）；同 prompt 第二次读 0.6 s 是**前缀缓存命中**。**S0b 未达**，PIPED_CONCURRENT
  不是杠杆。证据 `/opt/ai/logs/V2c-piped-20261025T1412Z/`。
- **V2b 结果**：fused A-B `prompt_ms` +0.61% <3% → 否决，parity 过，臂已恢复；证据 `/opt/ai/logs/V2b-fused-20261025T1359Z/`。
- **V2a 结果**：矩阵 4/4 PASS（V2-0 装后核对、V2-4 warmup、V1-5 显存账、V2-1 S0d+长读+短请求）；
  **S0c 未达（前提不成立）**：引擎被 kill 后 server 不退出（journal 无 systemd 事件）、`loaded`/health 仍报 true；
  在途 7 请求各报一次 `engine stopped unexpectedly (-9)`（同一个 kill 的 7 次报告）；此后**没有自动重启**，
  后续请求 503 直到 `use-config.sh` 重启（~183 s）。证据 `/opt/ai/logs/V2a-matrix-20261025T1345Z/`。
- **新发现（进队列）**：① 引擎崩溃后**不自动重启**、health/loaded 仍报 true、请求 503 到手动重启（M8）；
  ② `probe-version-stamp.py` 固定读 0.2.2 树，要按 launcher 定位（V2a 读到的是旧文件）。
- **第一次装失败的教训（已恢复）**：0.2.3 `serve/` 覆盖 0.2.2 树 → `ImportError: strata_control.tls`，
  服务重启循环；`restore --go` 恢复。最终部署形状 = 引擎进 0.2.2/build、launcher 指向自包含 port 树、
  **config 不动**（这样 use-config.sh 与 verify-running-config.sh 都照旧）。

## V1 结果（2026-10-25 12:52–13:06Z，0 重启）

- 实验树 `/opt/ai/src/strata-port-v1` = HEAD `2294d4de`（`serve/server.py` md5 `b6b695a1` = 本机 HEAD）；
  构建 **7m01s**、`BUILD_EXIT=0`、0 error；产物 `build75/strata` = **0.1.49.1**、md5 `84a0d410`。
- 矩阵 `/opt/ai/logs/V1-matrix-20261025T2115Z/`（5/5 PASS）：V1-1 版本戳一致；V1-2 源码树单测
  **61 passed / 10 skipped / 0 failed**；V1-3 `isa_guard_check` Passed 2.36 s；V1-4 默认过 / `CHUNK=128` 被
  static_assert 拒；V1-5 staged wedge 3 passed。
- 首跑 12 failed 的原因是测试 venv 缺 `jinja2`（补后 8 failed）与 `regex`（补后 0）；不是代码缺陷。

## V2b 窗口检查表（按序）

1. `python3 tools/server/four-questions.py`（有残留进程就停）+ `preflight.py`。
2. 臂 A（默认 `STRATA_PF_FUSED=0`）：`ops-v2-arm.sh show` 确认当前臂 → `ops-v2-preflight.py` 应称 "fused (B) is present"；
   `python3 tools/server/run-matrix.py --batch V2` 的 V2-2 会跑 `exp-v2-prefill-ab.py`。
3. 臂 B：`ops-v2-arm.sh set STRATA_PF_FUSED 1 --go`（1 次重启）→ `exp-v2-prefill-ab.py` 3 次
   → `exp-v2-prefill-ab.py --tag parity` 对比同 prompt 文本。
4. `exp-v2-ab-summary.py A.json B.json` 出中位/离散/±3%；`/metrics` 的 `prefill_ms` 交叉。
5. 结论落账 L-059、STATE、Gate、§4.2；臂关回去（默认 0）——**无论判决如何都恢复默认臂**。

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
- 日重启：2026-10-25 4/6（V0、V1 各用 0）。**用户 2026-10-25 21:33 明确：当前没有重启限制，可随时重启服务/服务器**——
  V2a 不再等 00:05，预算计数只作记录，不再当闸门。
- 未完成采纳项：18（见 `WORK_QUEUE.md`；V0–V3 批次行不计入 backlog）
- 阻塞：无
- 最后更新：2026-10-25T21:13+08:00（V2 准备完成：驱动 + 快照就绪，窗口 00:05）
