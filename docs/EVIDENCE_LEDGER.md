# 证据台账

每条记录要能追到原始日志。**同 boot A-B-A、每臂 ≥3 次、中位+sd、<3% 不结论**（本机跨 boot 漂移可达 5%）。
读代码得到的结论一律标 *静态阅读，未测*。

---

## L-000 · 基线快照（2026-10-24 00:5x CST / 2026-10-23 16:5x UTC，boot c61e5649）

**来源**：`systemctl show strata`、`nvidia-smi`、`free`、`df`、`systemctl list-timers`、
`/opt/ai/src/strata-0.2.2/strata-022.log`、`/opt/ai/logs/strata-service.log`。全部只读采集，未改任何东西。

| 项 | 值 |
|---|---|
| 服务 | `active/running`，`NRestarts=0`，MainPID 1298，起于 2026-10-23 15:41:09 UTC |
| 引擎 | 自建 `/opt/ai/src/strata-0.2.2/build/strata` = `strata 0.1.47`，env 含 `STRATA_SM75_PERF=1`、`STRATA_PIPED_CONCURRENT=1` |
| 显存 free | **GPU0 506 MiB**，GPU1 930 MiB（引擎自报同一数字 505） |
| 预留 | GPU0 `--vram-reserve-mib 960`；GPU1 `--vram-reserve-later-mib 1435`（语义：层切分里**后面那张卡**的预留，`docs/MULTI_GPU.md:256`） |
| 专家缓存 | auto：`16.85 GiB free, 960 MiB reserved -> 7334 slots`；本会话流量下实测 **98.0% hit**（`--batch 4`） |
| RAM | available 14 GiB（total 62，shared 34） |
| 磁盘 | 468 G 用 283 G，剩 162 G |
| 温度 / 功耗 | GPU0 75 ℃ @156 W，GPU1 58 ℃ @132 W（调优记录写的是 45–52 ℃，**待复核**） |
| NVLink | 每小时采样在跑，最新 `crc/replay/recovery` 全 0（两卡两 link） |
| 残留 | 无实验进程残留；无用户 crontab |

### 本轮发现（按重要性）

1. **#2065 的挂起风险正在现场触发**。引擎日志：
   `strata serve: WARNING: only 505 MiB of VRAM free at the start of this request (below 512 MiB): ... decode falls to
   a few tok/s and a window may never finish (#2065). Add --vram-reserve-mib 1024 ...`。
   该形态在日志里累计 **12 次**（`grep -c "WARNING: only"`）。**别数错**：`grep -c "MiB of VRAM free"` 是 734 次，
   但其中绝大多数是 `strata verify: capturing the N-token window (X MiB of VRAM free)` 这种 graph 捕获进度行，不是警告。
   本次 boot 至少 1 次。**这就是能卡死我自己会话的东西。**
2. **本 boot 的下降轨迹**：加载时 GPU0 预留 960 MiB → 现在 505 MiB，**−455 MiB**。
   其中 verify 窗口的 graph 捕获可解释约 −45 MiB（捕获 1/2/3/5/7/8 token 窗口时 GPU0 从 551 一路到 515，
   每个新宽度约 −10 MiB 且不归还）；**其余约 410 MiB 去向未查明**（*静态阅读，未测*）。
3. **GPU0 与 GPU1 的不对称是我们自己配的**：GPU1 拿 1435、GPU0 只拿 960，紧的正是 GPU0。
   `--vram-reserve-later-mib` 只影响后一张卡，所以调 `--vram-reserve-mib` 只动 GPU0（*静态阅读*，
   源码 `src/program/generate.cpp:617-619`、`docs/MULTI_GPU.md:256-258`）。
4. **上游 #3849 的前提在本机暂未复现**：#3849 报 `--batch` 使专家缓存命中 99%→86%（2× 3060）；
   本机 `--batch 4` 下本会话看到 98.0%。**这是单点读数，不是 A/B，不作结论。**

### 纠正之前写错的地方

- `SERVER_ADAPT_REQUIREMENTS.md` §9.4 说 NVLink CRC timer "尚未安装"——**错了，它在跑**（`nvlink-crc-sample.timer`，
  每小时，日志 `/opt/ai/logs/nvlink-crc.log`）。

---

## L-001 · GPU0 显存预留 960 → 1280（待执行）

**假设**：`--vram-reserve-mib` 是引擎给 auto 专家缓存 sizing 的**输入**（`docs/DETAILS.md:780`：引擎在 sizing 前扣掉它），
它**不保证**之后仍有这么多空闲；本 boot 之后又被吃掉 455 MiB。所以把 GPU0 预留从 960 提到 1280，
预期 plateau 从 ~505 抬到 ~825 MiB，脱离 512 的告警线；代价是 GPU0 专家缓存少 320 MiB（约 135 slot，−1.8%）。

**为什么不是 1024**（引擎自己建议的数）：1024 只补 64 MiB，plateau 约 569 MiB，距告警线只有 57 MiB；
本 boot 观测到的 post-load 消耗是 455 MiB，且其中 410 MiB 去向未查明——留 57 MiB 是在赌一个没量过的东西。

**判据**（这是稳定性改动，不用速度判胜负）：
1. 重启后同一负载下 `WARNING: only N MiB ... (#2065)` 不再出现（或 N 稳定 > 700 MiB）；
2. 专家缓存 hit 不低于改动前的 98.0% 超过 1 个百分点（掉到 ≤97% 算代价过大，回退）；
3. 单流 decode 落在噪声内（±2%，跨 boot 只作趋势参考，见 §判据规则）；
4. 回退：`set-config-key.py args.43 960 --config /opt/ai/src/strata-0.2.2/strata-022.json` + restart。

**执行**（2026-10-24 01:02–01:11 CST / 2026-10-23 17:02–17:11 UTC）：

| 步骤 | 结果 |
|---|---|
| 改配置 | 就地替换 JSON 文本，`args` 仍是 54 个字符串，`--vram-reserve-mib 1280`、`--vram-reserve-later-mib 1435` 未动。备份：`/opt/ai/backups/20261023/strata-022.json.pre-reserve1280-170233` |
| 中途事故 | 先用 `set-config-key.py args.43 1280` 试过，它把整个 list 换成 `{"43": 1280}`；**检查门拦下了重启**，从 `pre-key-args_43` 备份恢复后改用文本替换。坏配置副本留在 `/opt/ai/tmp/args-mangled-*.json` |
| 重启 | `sudo -S systemctl restart strata`（普通用户会被 polkit 拦），**到 `/health`=200 用了 165 s** |
| 缓存 sizing | `expert cache auto: 16.85 GiB free, 1280 MiB reserved -> **7190 slots**`（原 7334，**−144 slot = −1.96%**；我预估 −135，方向对） |
| 判据 1（不再告警） | 加载后 GPU0 free **1010 MiB**（原 504），一次探针请求后 **890**，5 分钟后被动采样 **864**；告警线 512 → **余量 350+ MiB**。`grep -c "WARNING: only"` 重启前后都是 **12**，**没有新增** ✅ |
| 判据 2（hit 不掉） | 采样到 **97.6%**（改动前本会话流量 97.9–99.2%，中位 ~98.8%）。单点读数，**未过 A/B，不作结论**；持续观察 `vram-perf.csv` |
| 判据 3（速度在噪声内） | 采样到 **86.3 tok/s** 单流（改动前本会话 72.5–79.3；台账基线 79.1）。跨 boot、不同 prompt 混合，**只算趋势** |
| 判据 4（回退） | 文本替换回 `"960"` + `sudo -S systemctl restart strata`，代价约 3 分钟不可用 |

**为什么这次没法按标准判胜负**：`--vram-reserve-mib` 是**加载时**的输入，改它必须重启 → 必然跨 boot；
而本机任何并发都会把单流腰斩（记录：2 路各 43–45 tok/s），我一边生成一边跑基准只会两头都脏。
所以 L-001 的**速度代价**用两条腿看：(a) 被动采样 `vram-perf.csv` 的真实流量分布（已装，每 5 分钟）；
(b) 等一个批准的停机窗口做同 boot A-B-A。**这是稳定性改动，判据 1 已经满足；速度代价待观察。**

---

## L-002 · 思考 token 预算（下一个候选，未开始）

**观察**（同一批探针，只读）：`max_tokens 400` 时思考吃掉 218 token、答案 0 个 ——
日志原话 `the reply reached max tokens while still thinking, so it has no answer`；
`max_tokens 500` 时思考 268、答案 350。配置里 `reasoning_budget_tokens = 12288`，
等于**思考基本不设上限**，只在撞到 `max_tokens` 时才收。

**为什么值得做**：这是 L1（任务墙钟）上唯一不需要重编译、不需要重启引擎就能试的杠杆——
调优记录 §21.2 自己估过"思考 −64% ≈ 墙钟 −25%"，比任何 kernel 改动都大。

**风险**：思考变短可能让工具调用正确率下降（`tool_call_recovery`、`reasoning_loop_recovery=recover` 是兜底，不是质量保证）。
**所以判据必须是任务级**：同一批 agent 任务，报完成率、工具调用通过率、空答案率、墙钟中位，不能只看 tok/s。

**待查**：`reasoning_budget_tokens` 是不是那 15 个可热改键之一（若是，A/B 成本大幅下降，不必重启）。
