# 多路融合轮次日志（MULTIPATH_LOG）

> **归档（2026-10-25）**：新复盘写入 `docs/ITERATION_LOG.md`；本文件保留旧格式的完整历史。

> **每轮一条，最新在上。** 复盘格式见 [`PORTING_RULES.md`](../PORTING_RULES.md) §11；**从 R11 起**每条必须带
> `### Gate（本轮）` 的 11 个字段，并过 `python3 tools/server/round_gate.py --round R<n>`；
> xfail/skip/FAIL 记 FAIL。数字的原始证据在 [`EVIDENCE_LEDGER.md`](../EVIDENCE_LEDGER.md)，状态在
> [`HANDOVER.md`](../HANDOVER.md)，计划在 [`MULTIPATH_PLAN.md`](MULTIPATH_PLAN-2026-10-24.md)。
> **Gate 不 PASS = 本轮按未发生处理。**

---

## V3-1 收口 · 2026-10-25 · 三档容量预设的 M1 对比：8×128K +11%（上下文 −33%）、8×192K 持平、默认保持 6×192K

- **改了什么**：config 三档（6×192K / 8×128K / 8×192K），各 1 次重启；测完恢复 6×192K（`cmp -s` 一致）。
- **数字**（`BENCH_N=4` 各 3 跑，别人 0，中位）：6×192K 单 77.1 / 聚合 **120.6**；8×128K 单 77.4 / 聚合
  **133.9（+11.0%）**；8×192K 单 78.3 / 聚合 **122.8（+1.8%）**。
- **容量**：8×192K 也起得来（192K 上下文、8 槽、VRAM 1005/1073 MiB free）——8 槽不缺显存。
- **判决**：8×128K 的 +11% 要拿 192K→128K 换（−33% 上下文），且 6 槽那组离散大（101.7–126.3）、n=3；
  8×192K 在噪声内。**默认保持 6×192K**，两档 8 槽作为可选预设记录。
- **自检**：health 200；`cmp -s` 恢复；四问绿；queue/STATE 一致。
- **重启记录**：4 次（8×128K、8×192K、恢复、外加早前一次换档）；不设次数上限。
- **下一批第一件事**：V3 的容量问题收口后，转 S2（每请求时延指标）；随后做 S0b 的机制性改进（长读分块让出，
  把 10.8 s 邻居停顿压下来）。

### Gate（本轮）
- 轮次：V3-1（收口）
- 项：V3
- 验收判据：6/8 路聚合 ≥ 基线−3%、单路 ≥15 tok/s、晚到首 token ≤ 读时间+2 s、邻居 ≤1 s、选一个预设
- 实现：只换 config（`--max-context` / `parallel`），无代码改动
- 测试：三档 × 3 跑 `BENCH_N=4` → PASS（别人 0）
- 生产：health 200；engine 0.1.49.1 / `84a0d410`；server `b6b695a1`；config 恢复 `6c362d01`（parallel 6 / 192K）
- 证据：`/opt/ai/logs/bench-results.jsonl`（V3-m1-6slot/8slot/8x192）、`/tmp/v3-cap8c.log`、`/tmp/v3-cap8x192.log`、`/tmp/v3-restore4.log`；脚本 `bench-strata.py`
- 回滚：快照 `pre-V2-20261025T133346Z/strata-022.json`（`cmp -s` 一致）
- 台账：L-064
- 剩余未完成项：18
- 没测什么：n≥6 的跟跑、8 槽下的晚到首 token/邻居停顿、128K 对真实长会话的影响

---

## V3-4 · 2026-10-25 · MTP 草稿深度 A/B（`--mtp-max-t` 2/4/6）：三档都在噪声内，保持 4

- **改了什么**：只换 config 的 `--mtp-max-t`（2 → 6，各 1 次重启），测完恢复 4（1 次重启），`cmp -s` 一致。
- **数字**（每档 3 跑，每跑验"本基准 5、别人 0"；中位）：
  - 4（现状）：单请求 75.5、4 路聚合 113.9、接受率 ~80%。
  - 2：单请求 71.4（−5.4%）、聚合 114.9（+0.9%）、接受率 83–89%。
  - 6：单请求 74.0（−2.0%）、聚合 111.6（−2.0%）、接受率 71–79%。
  - 同一臂内单请求就从 63.0 到 76.1 波动（≈20%）→ −5.4% 也是噪声；**没有 ≥3% 的可采纳差异，保持 4**。
- **坑（重要）**：先前"bench 跑不完/0 rep"多半是**残留 bench 进程**互相抢引擎（`timeout`/ssh 超时后进程
  没死）；kill 要锚定：`pkill -f "^python3 bench-strata.py"`，每次先确认无残留。
- **自检**：health 200；`cmp -s` 恢复；四问绿；queue/STATE 一致。
- **重启记录**：3 次（2、6、恢复 4）；不设次数上限。
- **下一批第一件事**：安静窗口重测 8×128K 的 4 路（排除残留 bench 后），拿到 M1 两档对比再判预设；
  否则转 S2（每请求时延指标）。

### Gate（本轮）
- 轮次：V3-4
- 项：V3
- 验收判据：单请求或 4 路聚合有 ≥3% 且可复现的差异才采纳
- 实现：只换 config（`--mtp-max-t`），无代码改动
- 测试：`BENCH_N=4 bench-strata.py V3-mtp4/mtp2-c1..3/mtp6-c1..3 1` → PASS（每档 3 跑、别人 0）
- 生产：health 200；engine 0.1.49.1 / `84a0d410`；server `b6b695a1`；config 恢复 `6c362d01`；`--mtp-max-t 4`
- 证据：`/opt/ai/logs/bench-results.jsonl`（V3-mtp4、V3-mtp2-c1..3、V3-mtp6-c1..3）、`/tmp/v3-mtp2.log`、`/tmp/v3-mtp6.log`、`/tmp/v3-restore3.log`；脚本 `bench-strata.py`
- 回滚：快照 `pre-V2-20261025T133346Z/strata-022.json`（`cmp -s` 一致）
- 台账：L-063
- 剩余未完成项：18
- 没测什么：`--spec`/`--spec-min-p` 组合、MTP 对长读的影响、真实流量混合

---

## V3-1 · 2026-10-25 · 容量矩阵（部分）：8×128K 装得下，8 路聚合 +8.6%、单路 −5.8%；4 路 M1 对比没测成

- **改了什么**：只换 config（`--max-context 128000` + `parallel 8`，server 注入 `--batch 8`），1 次重启；
  测完从快照恢复 6×192K（1 次重启），`cmp -s` 逐字节一致。
- **数字**（`bench-strata.py`，每档 3 跑中位）：
  - 6×192K：单请求 75.2 tok/s；6 路聚合 138.7 tok/s；墙钟 17.3 s；接受率 75–80%。
  - 8×128K：单请求 70.8 tok/s；8 路聚合 150.6 tok/s；墙钟 21.3 s；接受率 80.97%。
  - 每路摊到的聚合 23.1 vs 18.8 tok/s；单路 **−5.8%**；**路数不同，聚合 +8.6% 不等于 M1 更好**。
- **容量**：8×128K 起得来——`batch_slots 8`、`expert_slots 17437`、`max_context 128000`、`vram_free 1011 MiB`。
- **没测成**：4 路（M1）两档对比——`BENCH_N=4` 跑了 9 min 0 rep 后被杀（疑似排在真实流量后面）；
  晚到请求首 token、8 路邻居停顿也没测。→ **不选预设**，生产保持 6×192K。
- **自检**：health 200；`cmp -s` 恢复验证；四问绿；queue/STATE 一致。
- **预算**：重启 2（换 8×128K 1、恢复 1）。
- **下一批第一件事**：安静窗口里补 4 路（M1）两档对比；若 8×128K 不更好，转 S0b 的机制性改进
  （长读分块让出，把邻居停顿从 ~10.8 s 压下来）或补 S2（每请求时延指标）。

### Gate（本轮）
- 轮次：V3-1
- 项：V3
- 验收判据：6/8 路聚合 ≥ 基线−3%；单路 ≥15 tok/s；晚到首 token ≤ 读时间+2 s；邻居停顿 ≤1 s；选一个预设
- 实现：只换 config（`--max-context 128000` + `parallel 8`），无代码改动
- 测试：`BENCH_N=6 bench-strata.py V3-cap6 3` 与 `BENCH_N=8 bench-strata.py V3-cap8 3` → PASS（各 3 跑）；`BENCH_N=4` 未完成（9 min 0 rep，被杀）
- 生产：health 200；engine 0.1.49.1 / `84a0d410`；server `b6b695a1`；config 恢复 `6c362d01`；parallel 6 / max-context 192000
- 证据：`/opt/ai/logs/bench-results.jsonl`（V3-cap6、V3-cap8）、`/tmp/v3-cap8.log`、`/tmp/v3-restore.log`；脚本 `bench-strata.py`
- 回滚：快照 `pre-V2-20261025T133346Z/strata-022.json`（`cmp -s` 一致）
- 台账：L-062
- 剩余未完成项：18（V3 批次进行中）
- 没测什么：4 路 M1 两档对比、晚到请求首 token、8 路邻居停顿、真实流量下的稳定性

---

## V2c · 2026-10-25 · S0b A-B：邻居停顿 10.87 vs 10.91 s（+0.37%，无差异）；停顿≈读时长本身

- **改了什么**：只换臂。臂 A = `STRATA_PIPED_CONCURRENT=1`（默认，V2a 部署）；臂 B = `ops-v2-arm.sh set
  STRATA_PIPED_CONCURRENT 0 --go`（1 次重启）；测完从快照恢复 config 并重启（1 次），`cmp -s` 逐字节一致。
- **数字**（`exp-v2-warmup.py` 每臂 3 跑，第一个 ~22K 读期间邻居最大 token 间隔）：
  - A：19.06 / 10.81 / 10.87 s → 中位 **10.87**（读 wall 19.94/11.54/11.66）。
  - B：20.2 / 10.9 / 10.91 s → 中位 **10.91**（读 wall 21.15/11.60/11.63）。
  - **B−A = +0.37%**（<3%）→ `STRATA_PIPED_CONCURRENT` 不是这个停顿的杠杆；**S0b 未达**（要 ≤1 s 或上界）。
- **关键观察**：邻居停顿与读自身时长同量级（10.8 s ≈ 11.5 s 读）——单读占着 GPU 时邻居拿不到解码机会；
  同 prompt 第二次读 0.60–0.64 s 是**前缀缓存命中**（邻居间隔 0.15–0.24 s），V2-4 的"第二次 0.73 s"按此解释。
- **预期被推翻**：S0b 的探针假设"换 `STRATA_PIPED_CONCURRENT` 就能把邻居停顿压到 1 s"——实测两臂无差异；
  要 ≤1 s 需要分块/抢占式的读（新机制），不是这个开关。
- **自检**：health 200；`cmp -s` 恢复验证；四问绿；queue/STATE 一致（18）。
- **预算**：重启 2（臂 B 1、恢复 1；用户已取消重启限制）。
- **没测什么**：不同读长度（8K/16K/32K）的停顿曲线；staged 场景；decode 侧并发；多卡。
- **下一批第一件事**：V3-1 容量矩阵（6×192K / 8×128K）或先修 S0e（引擎崩溃不自动重启）——按 EVERGREEN §3
  的收益/风险排序，S0e 是可用性缺陷，优先。

### Gate（本轮）
- 轮次：V2c
- 项：V2c
- 验收判据：邻居停顿 ≤1 s 或给出可接受上界（A-B 两臂各 3 跑，≥3% 才下结论）
- 实现：只换臂（`ops-v2-arm.sh`），无代码改动
- 测试：`python3 tools/server/exp-v2-warmup.py http://192.168.3.14:8080` ×3/臂 + `exp-v2-ab-summary.py --field neighbour_gap_s` → PASS（两臂各 3 跑，无失败）
- 生产：health 200；engine 0.1.49.1 / `84a0d410`；server `b6b695a1`；launcher `0eeee160`；config 恢复 `6c362d01`；`STRATA_PIPED_CONCURRENT=1`
- 证据：`/opt/ai/logs/V2c-piped-20261025T1412Z/`（armA/B 各 3 跑 + read1 汇总）、`/tmp/v2c-armB.log`、`/tmp/v2-restore-c2.log`；脚本 `exp-v2-warmup.py`、`exp-v2-ab-summary.py`、`ops-v2-arm.sh`
- 回滚：快照 `pre-V2-20261025T133346Z/strata-022.json`（`cmp -s` 一致）
- 台账：L-060
- 剩余未完成项：18
- 没测什么：不同读长度的停顿曲线、staged 场景、decode 侧并发、多卡

---

## V2b · 2026-10-25 · fused A-B：22K 读的 prompt 时间 +0.61%（<3%，否决），输出文本一致

- **改了什么**：只换臂，不改代码。臂 A = `STRATA_PF_FUSED` 不设（默认关，V2a 的部署）；臂 B = `ops-v2-arm.sh
  set STRATA_PF_FUSED 1 --go`（改 config env + `use-config.sh` 重启，1 次）；测完从快照恢复 config 并重启（1 次）。
- **数字**（`prompt_ms` 中位；每臂 3 跑；`exp-v2-prefill-ab.py`）：
  - A：24597.2（冷）/ 10956.2 / 10968.9 → 中位 **10968.9**，去首跑中位 10962.6。
  - B：20188.3（冷）/ 10990.3 / 11035.6 → 中位 **11035.6**，去首跑中位 11013.0。
  - **B−A = +0.61%**（去首跑 +0.46%）→ **<3%，不下结论**；两臂文本 sha256 相同 `64140bc330e9…` → **parity 过**。
- **确认两臂不同**：fused 臂的启动日志有 `strata: prompt fused experts (sm_75): … IQ3_XXS / IQ4_NL set up at both
  shapes (WW=2 46592 B, WW=4 60672 B fits)`（仅 1 条，属于 13:59:03 的启动）；关的臂没有。
- **恢复**：臂后 config 从快照恢复并 `cmp -s` **逐字节一致**（`6c362d01`）；引擎环境已无 `STRATA_PF_FUSED`；
  health 200、engine 0.1.49.1。
- **预期**：没有预设 fused 一定快；<3% 就按规则不下结论。离散度大（A 124%、B 83%）来自两臂首跑都是冷启动。
- **自检**：health 200；`cmp -s` 恢复验证；四问绿（无残留、cron/timer 正常）；queue/STATE 一致（18）。
- **四问**：① cron/timer 正常；② 无残留；③ vram-perf 稳定；④ 方向：V2c（S0b 1/0 两臂）。
- **预算**：重启 3（臂 B 1、恢复 1、加上臂 A 本就用 V2a 的部署；用户已取消重启限制）。
- **没测什么**：decode/MTP 路径；只一条 prompt 形状与 max_tokens；无 B-A-A 反向复测；无第二次重复（首跑冷启动污染中位）。
- **下一批第一件事**：V2c——`STRATA_PIPED_CONCURRENT` 1→0 两臂，各 3 跑邻居停顿，判据 ≤1 s 或给出上界。

### Gate（本轮）
- 轮次：V2b
- 项：V2b
- 验收判据：同 boot A-B 的 `prompt_ms` 中位差 ≥3% 才下结论；输出文本 parity 过
- 实现：只换臂（`ops-v2-arm.sh`），无代码改动
- 测试：`python3 tools/server/exp-v2-prefill-ab.py http://192.168.3.14:8080 3` 两臂 + `exp-v2-ab-summary.py` → PASS（两臂各 3 跑，无失败）
- 生产：health 200；engine 0.1.49.1 / `84a0d410`；server `b6b695a1`；launcher `0eeee160`；config 恢复 `6c362d01`；无 `STRATA_PF_FUSED`
- 证据：`/opt/ai/logs/V2b-fused-20261025T1359Z/`（armA.json、armB.json）、`strata-022.log` 的 fused 标记、`/tmp/v2b-armB.log`、`/tmp/v2-restore-default.log`；脚本 `exp-v2-prefill-ab.py`、`exp-v2-ab-summary.py`
- 回滚：快照 `pre-V2-20261025T133346Z/strata-022.json`（`cmp -s` 一致）
- 台账：L-059
- 剩余未完成项：18
- 没测什么：decode/MTP 路径、其他 prompt 形状、反向复测、第二次重复

---

## V2a · 2026-10-25 · 窗口批：0.1.49.1 + 0.2.3 自包含树上生产（矩阵 4/4 PASS；S0c 前提不成立）

- **改了什么**：生产部署。引擎 `84a0d410`（0.1.49.1）进 `/opt/ai/src/strata-0.2.2/build/strata`；launcher
  `0eeee160` 改为跑自包含 port 树 `/opt/ai/src/strata-port-v1/serve/server.py`（`b6b695a1`）；**config 未动**
  （`6c362d01`，54 args / 17 env），这样 `use-config.sh` 的重启检测与 `verify-running-config.sh` 照旧可用。
- **第一次失败（已恢复）**：先把 0.2.3 的 `serve/` 覆盖到 0.2.2 树 → `ImportError: cannot import name 'tls'
  from 'strata_control'`，服务重启循环；`ops-v2-window.sh restore --go` 恢复（引擎 `383b426a`、server
  `a3c7df58`、health 200），随后改用自包含树。
- **数字**：
  - V2-0：engine **0.1.49.1**、`batch_slots 6`、`expert_slots 17872`（0.1.47：17755）、`max_context 192000`、`kv int8`；
    `ARGS : MATCH`（54 条全在 + 5 条 server 注入）、`ENV : DIFFERENT` 只有 launcher 的
    `STRATA_MP_STAGED_READ_MIN_TOKENS`。
  - V2-4：~22K 冷读 **29.37 s**，邻居最大间隔 **28.48 s**（邻居 29 tokens）；第二次读 **0.73 s**，邻居间隔 **0.15 s**。
    （与文档 S0b 的 3.43 s 不是同一条件：这里是重启后首读；**不作结论**。）
  - V1-5：`expert_cache_mib 29227`、`arena_mib 18987`、`vram_free_mib 1009`、`kv_resident 32768`；两张 2080 Ti
    都报（22.7/22.6 GB、54/47 °C）；RAM 45.6 GB。
  - V2-1（只判 **S0d**）：取消暂存读者后 6 邻居继续（`gap_after_close 0.13 s`、`tokens_after_close 5562`），
    health 200；长读 6.04 s、短请求 0.64 s。**同一演练的 `neighbour_gap_max_s` 是 10.83 s**（暂存读等待期间），
    判据说的是"取消之后不停"。日志 staged sent/done/adopted 511/510/509、cancelled 1、重启后 0 traceback。
  - **S0c 未达（前提不成立）**：`kill -9` 引擎后 server **不退出**（journal 无 systemd 事件）、`loaded`/health
    仍报 true；在途的 7 个请求各打印一次 `engine stopped unexpectedly (-9)`（同一个 kill 的 7 次报告，不是 7 次
    重启）；此后**没有自动重启**（无新 `engine started:`），后续请求拿到 503（`...it was not sent`），直到
    `use-config.sh` 重启（**~183 s**，冷加载 ~165 s）。
- **预期被推翻**：S0c 的设计假设"引擎死了 server 会退出、systemd 拉起新进程"——实际 server 是引擎的监督者，
  不退出，也**没有自动重启**（实测 503 直到手动重启）；因此"新进程可服务"这个判据不成立，"健康检查 = 能服务"
  也不成立。
- **自检**：health 200；四问绿（无残留、cron/timer 正常、vram 58.6/92.2）；queue/STATE 一致；`round_gate` 待过。
- **四问**：① cron/timer 正常；② 无残留安装/构建进程；③ vram-perf 稳定；④ 方向：V2b fused 两臂。
- **预算**：重启：V2a 共 4 次（第一次装 1、恢复 1、正式装 1、S0c 后恢复 1；13:34–13:52Z）——**用户 21:33 已明确当前无重启限制**，
  原 ≤2/批、≤6/日只作记录。
- **没测什么**：S0c 的"新进程可服务"；3 次重复（单轮矩阵）；V2b/V2c 的臂；首读 29.4 s 的复现；
  引擎崩溃后"无并发单请求"的自愈是否干净。
- **下一批第一件事**：V2b 臂 A/B（`STRATA_PF_FUSED` 0→1，各 1 次重启），`exp-v2-prefill-ab.py` 3 次 + parity。

### Gate（本轮）
- 轮次：V2a
- 项：V2
- 验收判据：V2-0 装后核对一致；V2-4 两次读都答完并打印数字；V1-5 双卡与引擎账都出数；V2-1 S0d 判据通过；S0c 前提成立才判
- 实现：引擎 `84a0d410` 部署 + launcher `0eeee160` 指向自包含 port 树；config 未动
- 测试：`python3 tools/server/run-matrix.py --batch V2 --out-dir /tmp/V2-matrix-20261025T1345Z` → PASS（4/4）
- 生产：health 200；engine 0.1.49.1 / `84a0d410`；server `b6b695a1`；launcher `0eeee160`；config `6c362d01`；`ARGS : MATCH`
- 证据：`/opt/ai/logs/V2a-matrix-20261025T1345Z/`（summary.json + V2-0/V2-4/V1-5/V2-1 日志 + V2-s0c-kill/s0c2/recover）、`/opt/ai/logs/V2a-install-20261025T1342Z.log`；脚本 `run-matrix.py`、`ops-v2-window.sh`
- 回滚：`/opt/ai/tmp/pre-V2-20261025T133346Z/`（md5sum -c 过）+ `ops-v2-window.sh restore --go`（本次实跑过一次，成功）
- 台账：L-058
- 剩余未完成项：18（S0c 否决，S0d 本次 PASS 待复核后从清单移出；V2b/V2c 未做）
- 没测什么：S0c 前提、3 次重复、V2b/V2c 臂、首读 29.4 s 复现、无并发崩溃自愈

---

## V1 · 2026-10-25 · 免停机批：服务器源码树全绿（构建 7m01s / 0 错；单测 61 passed / 10 skipped；isa_guard PASS；64-256 锁死；staged wedge 3 passed）

- **改了什么**：**无生产改动、无仓库代码改动**。把 HEAD `2294d4de` 用 `git archive` 落到实验树
  `/opt/ai/src/strata-port-v1`（`serve/server.py` md5 `b6b695a1`，与本机 HEAD 一致），实验目录一次全量构建，
  然后跑 `run-matrix.py --batch V1`。测试 venv 补了 `jinja2` 与 `regex`（`serve/` 在 import 时要用；
  不是仓库改动，也不是移植代码缺陷）。
- **数字**：
  - 构建：configure `12:52:53Z` → build `12:53:00Z–13:00:01Z`（**7m01s**），`BUILD_EXIT=0`、0 error、24 warning；
    产物 `build75/strata` = **0.1.49.1**、md5 `84a0d410`。
  - V1-1 版本戳：四件 hash 与 V0 完全一致（引擎 `383b426a` / server `a3c7df58` / launcher `9b6bf792` / config `6c362d01`）。
  - V1-2 服务器源码树单测：**61 passed / 10 skipped / 0 failed**（skip = 需要活服务器；用
    `MP_BASE_URL=127.0.0.1:9` 让它们 skip，**不给生产发请求**）。
  - V1-3 `isa_guard_check`：Passed 2.36 s。
  - V1-4：默认 64/256 编译过；`-DSTRATA_QSA_ATTN_CHUNK=128` 被 static_assert 拒（14.4 s）。
  - V1-5 `serve/test_staged_wedge.py`：3 passed / 2.20 s。
- **预期被推翻**：V1-2 首跑 **12 failed**——全部是 venv 缺 `jinja2`；补 `jinja2` 后 8 failed（缺 `regex`）；
  再补 `regex` 后 0 failed。结论："服务器源码树跑单测"的隐含前提是 `pytest` + 运行时依赖，不只是 pytest。
  已写进 `tools/server/tests/README.md`；首跑日志留在 `/opt/ai/logs/V1-firstrun-20261025T2100Z/`。
- **自检**：health 200；queue/STATE 一致（17）；上轮 V0 全绿；四件生产 hash 未变（无部署）。
- **四问**：① cron/timer 正常；② 无残留构建/安装进程（`nvcc/cc1plus/cmake --build/pip` 全空）；
  ③ `vram-perf.csv` 最新 `2026-10-25T13:05:01Z,818,1122,0,58.6,92.2`，与 V0 基线同值；④ 方向：V2 要窗口，排 00:00 后。
- **预算**：构建 1（7m01s，实验目录）、重启 0、停机 0。
- **没测什么**：端到端（生产配置/真模型/真并发）；6/8 路；fused；停机级回滚；10 条 live 用例（V2 窗口才跑）；
  0.1.49.1 引擎与生产 config 的启动兼容性（V2 的第一个门）。
- **下一批第一件事**：V2 准备——写安装/回滚驱动、检查 0.1.49.1 对生产 config 54 个 args 的兼容；
  窗口排在 00:00 之后。

### Gate（本轮）
- 轮次：V1
- 项：V1
- 验收判据：矩阵 5 项全 PASS（版本戳复跑一致 / 源码树单测 0 failed / isa_guard PASS / 64-256 锁死 / staged wedge 3 passed）
- 实现：无仓库/生产改动；实验树 `/opt/ai/src/strata-port-v1` = HEAD `2294d4de`
- 测试：`python3 tools/server/run-matrix.py --batch V1 --src /opt/ai/src/strata-port-v1` → PASS
- 生产：health 200；四件 hash 与 V0 相同（引擎 `383b426a`、server `a3c7df58`、launcher `9b6bf792`、config `6c362d01`）；无部署
- 证据：`/opt/ai/logs/V1-matrix-20261025T2115Z/`（summary.json + 5 个日志；目录名 2115Z 是手写笔误，`summary.started`=13:05:15Z）、`/tmp/v1-build.log`、`/opt/ai/logs/V1-firstrun-20261025T2100Z/`；脚本 `run-matrix.py`
- 回滚：无生产改动；`/opt/ai/tmp/rollback-V0-20261025T124629Z/` 仍在（演练：文件级是、停机级否）
- 台账：L-057
- 剩余未完成项：17（S0c、S0d、S0b、C1、F1F2、S2、S3、S8、S1、S7、S9、S10、S11、S12、S13、S14、cache_salt）
- 没测什么：端到端生产配置、6/8 路、fused、停机级回滚、0.1.49.1 的启动兼容性

---

## V0 · 2026-10-25 · 准备批：版本戳基线、0.2.2 harness、补丁追平、矩阵 runner、探针代价、回滚演练（0 构建 / 0 重启 / 0 生产改动）

- **改了什么**：
  - `tools/server/probe-version-stamp.py`（新）：四件 hash + 运行进程 + `STRATA_*` env + feature markers + `/health`。
  - `tools/server/run-matrix.py`（新）：一批一次矩阵；V1 的 5 项已定义（判据/超时/日志/`summary.json`）。
  - `tools/server/probe-instrument-cost.py`（新）：S2 每请求时延探针**自身代价**。
  - `tools/server/check_assert_lock.py`（新）：64/256 负向编译（V1-4 的方法，已在旧移植树验证）。
  - `tools/server/tests/staged_patch_harness.py`：基准改为 `patches/base-0.2.2/` 的 v0.2.2 副本（md5 钉住）。
  - `tools/server/patches/mp-w3-server-0.2.2.patch`：追平 `46d12fa9`（`BCANCEL_DRAIN_S`=10 s、BRDY 严格解析）；
    新增 `test_mp_patch_parity.py` 钉住这两处。
  - 文档：`STATE.md` 版本戳表、`MULTIPATH_RULES.md` §1 hash、`RULES.md` v4 / `PORTING_RULES.md` v1.2 /
    `MULTIPATH_RULES.md` v0.4（C1/C2 批次化）。
- **数字（全部只读或本机，无性能结论）**：
  - 现网四件 md5：引擎 `383b426a`、server `a3c7df58`、launcher `9b6bf792`、config `6c362d01`；
    health 200、engine 0.1.47、`batch_slots=6`、config `parallel=6`/54 args（运行进程 59）。
  - 现网 server 是 **hybrid**：有 `yield "ping"`（5 处）与 `request_started`（S2），**没有** `BCANCEL_DRAIN_S`、
    `malformed staged-read`；`52b84d54` 是 `/opt/ai/src/strata-mp` 的 hash，**不是现网**。
  - 本机测试：`pytest tools/server/tests` = **61 passed / 10 skipped / 0 failed**（10 个 skip 是"要活服务器"，
    本机 127.0.0.1 无服务，**不是通过**）；门禁用的 6 个文件 = **31 passed**。
  - S2 探针代价：中位 **1.356–1.398 µs/请求**（3 次独立运行，各 5×200k，离散度 3.0–8.9%；`/opt/ai/logs/V0-probe-cost-20261025T1258Z.log`）。
  - 回滚演练：`md5sum -c`、`sha256sum -c`、与 live 的 `cmp -s` 全过（第一次 `md5sum -c` 在错 cwd 下失败、修正后过，两条都在日志）；**停机级回滚未做**。
  - 负向编译（旧移植树）：默认 64/256 编译过；`STRATA_QSA_ATTN_CHUNK=128` 被 static_assert 拒。
- **预期被推翻**：R11 记的 server hash `52b84d54` 是 patch 工作树的，不是现网；两份文档的现网 hash 都不对。
  `mp-w3-A3-*` 是 **W3 前**状态（与现网不同），不能当当前态回滚包。
- **自检**：health 200；queue/STATE 一致（17 未完成）；上轮 R11 剩余 15，审计新增 S0c/S0d 后 17；V0 不加不减。
- **独立复核**（只读子代理，V0 后）：7/7 项 PASS，总判决 `V0 EVIDENCE: PASS`。它抓到一处**时间戳未来化**
  （"21:05/21:1x" 在写入时还没到，会让 V1 构建落进 V0 的"0 构建"窗口）——已改为 20:40–20:51；
  另把 S2 探针代价从 1 次改成 **3 次独立运行**（1.356/1.389/1.398 µs）并归档日志。
- **四问**：① cron/timer 正常（`vram-perf` */5、`nvlink-crc` 每小时）；② 无残留安装器/构建（pgrep 空）；
  ③ `vram-perf.csv` 539 行，最新 `58.6/92.2`，无漂移告警；④ 方向：先 V1（免停机），V2/V3 排到 00:00 后。
- **预算**：构建 0、重启 0、停机 0。单文件负向编译 1 次（旧移植树，输出到 `/tmp`，不计批构建）。
- **没测什么**：任何性能数字；服务器源码树单测（V1-2）；停机级回滚；引擎侧 `STRATA_*_TIMING`；
  V0 期间现网有真实请求（`/opt/ai/logs/V0-metrics-baseline-20261025T124655Z.json`），本轮没有独立流量探针。
- **下一批第一件事**：V1 = rsync HEAD 到 `/opt/ai/src/strata-port-v1`，实验目录一次构建，跑 `run-matrix.py --batch V1`。

### Gate（本轮）
- 轮次：V0
- 项：V0
- 验收判据：四件 hash 实测且运行进程对得上；harness 两红转绿；补丁含 `46d12fa9` 两处；矩阵 runner 可列出/干跑；探针代价已量；回滚包字节核对；四问与基线归档；C1/C2 版本行
- 实现：`tools/server/probe-version-stamp.py`、`run-matrix.py`、`probe-instrument-cost.py`、`check_assert_lock.py`、`tools/server/patches/base-0.2.2/`、`tools/server/patches/mp-w3-server-0.2.2.patch`
- 测试：`python3 -m pytest tools/server/tests/test_mp_patch_parity.py tools/server/tests/test_mp_staged_patch.py tools/server/tests/test_mp_staged_cancel.py tools/server/tests/test_mp_preflight.py tools/server/tests/test_mp_round_gate.py serve/test_staged_wedge.py -q` → PASS
- 生产：health 200；engine `383b426a`、server `a3c7df58`、launcher `9b6bf792`、config `6c362d01`；无生产改动（0 重启）
- 证据：`/opt/ai/logs/V0-metrics-baseline-20261025T124655Z.json`、`/opt/ai/logs/V0-vram-perf-baseline-20261025T124655Z.csv`、`/opt/ai/logs/V0-rollback-rehearsal-20261025T124635Z.log`、`/tmp/v0-*`；脚本 `probe-version-stamp.py`、`run-matrix.py`、`probe-instrument-cost.py`、`check_assert_lock.py`
- 回滚：`/opt/ai/tmp/rollback-V0-20261025T124629Z/`（字节核对过；演练：文件级是、停机级否）
- 台账：L-056
- 剩余未完成项：17（S0c、S0d、S0b、C1、F1F2、S2、S3、S8、S1、S7、S9、S10、S11、S12、S13、S14、cache_salt）
- 没测什么：性能、停机级回滚、引擎侧 TIMING、服务器源码树单测（V1）

---

## R11 · 2026-10-25 · S0 修复开关语义与阈值默认（引擎 383b426a / server 52b84d54）；长读暂存**邻居停顿 3.43 s**

- **改了什么**：引擎开关从裸 `getenv()!=nullptr` 改为 `strcmp(v,"0")!=0`（`=0` 真的关）；server 的
  `STRATA_MP_STAGED_READ_MIN_TOKENS` 默认从 0 改为 **4096**；重构建 exit 0；部署并恢复健康。
  新增 `test_mp_staged_switch.py` 防回退。
- **数字**（同机、4 槽全忙）：
  - 短读 452（<4096，不暂存）：首 token **40.68 s**、第二 40.73 s、邻居停顿 0.98 s → xfail（设计如此）。
  - 长读 22.7K（暂存）：首 token **17.89 s**、第二 36.90 s、总 37.31 s、邻居总时长 41.37 s，
    **邻居在读者窗口内最大停顿 3.43 s**（> 1 s 门槛）。
  - 部署后引擎 `383b426a`、server `52b84d54`、health 200。
- **预期被推翻**：R9/R10 只测了邻居**总时长**，看起来“不受害”；这次加了窗口内 token 停顿，实测 **3.43 s**。
  “暂存读不卡邻居”不成立，至少在这个并发分支下不成立。这是 R11 最重要的发现。
- **自检**：health 200；queue 一致；上轮 R10 剩余 14 → 本轮 S0 完成、新增 S0b，仍 15。
- **四问**：① cron/timer 正常；② 无残留构建进程（`cc1plus` 0）；③ vram 未读；④ 方向：先查 3.43 s 的成因
  （`STRATA_PIPED_CONCURRENT=1` vs `0`），再修审计指出的 `mp_staged` 泄漏与 BCANCEL 竞态。
- **预算**：构建 1 次（exit 0）、重启 1 次（部署；一次 start 因 sudo 失败后补起，不重复计）。
- **没测什么**：`STRATA_PIPED_CONCURRENT=0` 臂；6/8 路；`=0` 语义的运行验证（只有静态测试）。
- **下一轮第一件事**：R12 = 测长读邻居停顿的成因（同 boot A-B：`PIPED_CONCURRENT=1/0`），给 3.43 s 定性。

### Gate（本轮）
- 轮次：R11
- 项：S0
- 验收判据：补丁含 `strcmp(=0 关)` + 阈值默认 4096；重构建 exit 0；部署后长读 17.89 s 走暂存、短读 40.68 s 不暂存
- 实现：`src/program/generate.cpp:11242`、`serve/server.py:1786`、`tools/server/patches/*`
- 测试：`pytest tools/server/tests/test_mp_staged_switch.py` → PASS
- 生产：engine `383b426a`、server `52b84d54`、health 200
- 证据：`/tmp/mp-w3-rebuild-*.log`；探针 `tools/server/tests/probe_long_read.py`；pytest 输出
- 回滚：`/opt/ai/tmp/mp-w3-B0-strata.bin` + `mp-w3-B0-server.py`（演练：否）
- 台账：L-055
- 剩余未完成项：15（S0b、F1F2、C1、12 项 S）
- 没测什么：PIPED_CONCURRENT=0、6/8 路、失败路径

---

## R10 · 2026-10-25 · A-B-A 闭环：回滚 A 验证（42.3/36.3 s）→ 重新启用 B（35.9/17.7 s）；3 流冷读 8.0 s

- **改了什么**：按 G4 做了一次**真实回滚**：从 `/opt/ai/tmp/mp-w3-A3-*` 恢复引擎 `4fd45369`、server `aec97a89`、
  `run-022.sh`、config 快照 → 验证 health 200、无 STAGED → 跑短读/长读 → 再按 R9 的方式**重新启用** B
  （引擎 `4f6f635f`、server `e8edc558`、阈值 4096）。2 次重启。
  同时把 3 流探针改成**唯一提示词**（此前被前缀缓存命中，0.2 s 的数字无效），得到冷读 3 流数据。
- **数字（同一会话 A-B-A-B）**：

| 臂 | 短读 452 首 token | 长读 22.7K 首 token | 长读总时长 | 邻居总时长 |
|---|---|---|---|---|
| A（回滚后，01:22Z） | **42.27 s** | **36.27 s** | 36.78 s | 37.89 s |
| B（再启用，01:28Z） | **35.93 s**（<4096 不暂存） | **17.74 s**（暂存） | 36.15 s | 41.90 s |

  3 流冷读（唯一提示词）：首 token **7.98 s**、第二 8.06 s、总 8.41 s、邻居 27.21 s（1 个空闲槽，读者正常入槽；
  空闲槽时读只要 ~8 s，4 槽全忙时 17 s——**读本身受并发影响**）。
  终态 bench（B）：单 **71.0 tok/s**、4 路聚合 **122.1 tok/s**、接受率 **82.0%**（A 基线 L-046：72.7 / 120.5 / 79.5%；
  R9 的 130.2 是同一臂的另一次，两次中位 ~126，均 ≥ 基线 −3%）。
- **结论**：回滚路径**实测可用**（G4 ✓）；长读首 token A 36.3 → B 17.7 s（−51%，本轮 n=1）；短读两臂同量级
  （35.9 vs 42.3，槽等待方差主导）；邻居 ≤1 s 不破。保留 B。
- **预期 / 推翻**：前一轮 3 流“0.2 s”是缓存假象，已修正；A 臂长读 36.3 s 比 L-049 的 47.3 s 快，
  说明**槽等待的方差很大**（邻居流结束时刻决定），单次数字只能当区间用。
- **四问**：① cron/timer —— 正常；② 残留进程 —— 无；③ vram-perf —— 未读；④ 方向 —— W3 收口，P1 项排期。
- **预算**：2 次重启，在预算内。
- **没测什么**：B 臂的 4 路 bench（沿用 R9 的 130.2 tok/s）；聚合吞吐在本轮的 A 臂没重跑。
- **回滚**：同 R9（A3 备份）——本轮已实测走通一遍。

---

## R9 · 2026-10-25 · W3 **生产启用**（阈值 4096）：长读首 token 17.1 s、短读不假开始；G1–G4 通过；2 次重启

- **改了什么**：装补丁引擎 `4f6f635f` + 补丁 server（`e8edc558`）并**保留**；`run-022.sh` 里
  `export STRATA_MP_STAGED_READ=1` 与 `STRATA_MP_STAGED_READ_MIN_TOKENS=4096`，配置 `env` 里
  `STRATA_BATCH_STAGED_READ=1`。第一次启用后发现**短读也走暂存**（452 token：首 token 1.7 s，然后静默 38.65 s），
  对话是“假开始”；加**提示词长度阈值**（<4096 token 不暂存）后再重启一次。
- **数字（同机；A 基线 L-049 与本轮重启前的 A 复测）**：

| 形状 | 现在（B + 阈值） |
|---|---|
| 短读 452（<4096，**不暂存**） | 首 token **40.88 s**、第二 40.94 s、邻居停顿 0.97 s（与 A 46 s 同量级） |
| 长读 22.7K（≥4096，**暂存**） | 首 token **17.07 s**、第二 33.78 s、总 34.16 s、邻居总时长 **38.58 s**（A：47.3 / 47.4 / 47.9 / 50.3） |
| bench（4 并发） | 单 **72.9 tok/s**、聚合 **130.2 tok/s**、接受率 **79.77%**（L-046 A：72.7 / 120.5 / 79.47%） |
| 回归 | T1/T4/T5/T7 14 passed、T2 xfail（非 exact，预期） |

- **门槛**：G2 邻居 ≤1 s → 0.97 s ✓；G3 聚合 ≥ 基线 −3% → 130.2 > 116.9 ✓；G4 回退可用 ✓（A3 备份齐）。
  **G1 说明**：短读不暂存（40.9 s）是**有意**的——暂存只帮长读；所以“准入 ≤2 s”的判据只适用于
  “长读首 token ≤ 读时间 + 2 s”（17.1 s ≈ 22.7K 的读时间），不再当作通用准入。
- **预期 / 推翻**：第一版“全量暂存”在短读上是**坏体验**（首 token 后静默 38 s），这是本轮最重要的发现；
  阈值把“长读的快到”和“短读不假开始”分开，才算真的可用。
- **四问**：① cron/timer —— 正常；② 残留进程 —— 无；③ vram-perf —— 未读；④ 方向 —— W3 收口，S 项按 P1/P2 排期。
- **预算**：**2 次重启**（启用 + 阈值），在预算内。
- **没测什么**：3 流形状被前缀缓存污染（命中后 0.2 s，不能代表冷读）；A/B 同轮 bench 未做（用 L-046 基线）；
  `checks` 随暂存搬入槽（v1 不做）。
- **回滚**：`/opt/ai/tmp/mp-w3-A3-*`（引擎 `4fd45369`、server `aec97a89`、`run-022.sh`、config 快照
  `mp-w3-A3-20261025T010949Z.json`）：停服 → cp 回 → `use-config.sh` 用快照 → 起。

---

## R8 · 2026-10-25 · 预设负例（T7 6/6）+ 清单收口

- **改了什么**：新增 `tools/server/tests/test_mp_preset.py`（T7 负例：`--batch` 不等于 `parallel`、
  `--batch-groups` 不整除、`--max-context` 超上限、缓存/预留低于下限、`--kv-resident` 低于引擎下限），
  6/6 通过；`check_preset.py` 对生产配置 **OK**。
- **数字**：T7 负例 6/6；`check_preset.py` 退出码 0（4x256、groups 2x2、KV 流式、引擎 0.1.47 形状一致）。
- **清单收口**：F1/F2 **部分采纳**、F3/F4/F7 **采纳**、F6 **部分采纳**、F5/F11 **搁置**、F8/F9/F10 **否决**——
  **每项都有判决**（见 FEATURE_PARITY 末尾“判决总表”）。
- **四问**：① cron/timer —— 正常；② 残留进程 —— 无；③ vram-perf —— 未读；④ 方向 —— 收口，跑最终回归。
- **预算**：重启 0、构建 0。未超支。
- **没测什么**：T6（SSD KV，F5 搁置，未建）；N×CTX 矩阵扫描（F6 只做静态/运行中对账 + 负例）。
- **下一轮第一件事**：无（本轮为收口轮）：跑最终回归（T1–T5、T7），确认生产健康，然后交最终报告。

---

## R7 · 2026-10-25 · 预设校验（F6/T7）+ 清单判决（F5/F7/F11）

- **改了什么**：新增 `tools/server/check_preset.py`（F6/T7）：检查 `parallel==--batch`、`--batch-groups` 整除、
  `--max-context`/`--kv-resident`/`--conversation-cache-mib`/`--vram-reserve-mib` 下限，并在 `/metrics` 可达时
  与**运行中的引擎**对账（batch_slots/max_context/kv_resident）；打印预设名 `strata-022.mp-<N>x<CTX>.json`。
  对生产配置跑了一遍：**OK**（4x256、groups 2x2、KV 32768 cells 流式、引擎 0.1.47 形状一致）。
- **清单判决**：F6 **部分采纳**（预设命名 + 校验工具；fork 的 `N×CTX ≤ KV tokens` 在 Strata 的 KV 流式下
  **不适用**，工具会明确打印这一点）；F7 **采纳**（T1 6/6）；F11 **搁置**（W3 已交付 TTFT，抢占/优先级仍无需求证据）；
  F5 **搁置**（触发条件未出现，T6 未建）。
- **数字**：`check_preset.py` 退出码 0；打印 `4 x 256000 = 1024000 tokens`、`KV streams beyond 32768 cells`、
  `engine 0.1.47 matches`。0 重启。
- **四问**：① cron/timer —— 正常；② 残留进程 —— 无；③ vram-perf —— 未读；④ 方向 —— 清单只剩 F5 的 T6 与
  F8–F10（已否决）待收口，下一轮做 W4 的 4x256 预设落地与文档收口。
- **预算**：重启 0、构建 0。未超支。
- **没测什么**：`check_preset.py` 对**错误配置**的拒绝（只跑了正确配置——下一轮补一个负例测试）；预设矩阵（N×CTX 扫描）。
- **下一轮第一件事**：R8 = 预设负例测试（故意错的配置必须被拒）+ `strata-022.mp-4x256.json` 入仓 + 文档收口。

---

## R6 · 2026-10-25 · T5 接受率验收 + T1/T2/T4 复跑（A 上，只读流量）

- **改了什么**：新增 `tools/server/tests/test_mp_spec.py`（T5：3 条 200-token 请求，从 `/metrics`
  取 `drafts_offered/accepted` 的窗口差值；门槛 offer≥20、接受率≥50%）；T1/T2/T4 在 A 上复跑。
- **数字**：T5 窗口 offered 244 / accepted 169 = **69.3%**，累计 70.5%（19 请求）；bench 的接受率 78–82%
  （L-046/L-049）。T1 **6/6**、T4 **通过**（前缀缓存命中 ≥50%）、T2 **xfail**（生产未开 exactness）。
  窗口比率含本会话自己的流量（同一引擎），所以是**下限**不是精确值。
- **预期 / 推翻**：T5 的窗口比率比 bench 低 9 个点，原因是窗口里混进了本会话的 token——测试因此用**下限**判据，
  并在文档里写明“窗口是混合流量”。没有推翻什么。
- **四问**：① cron/timer —— 正常；② 残留进程 —— 无；③ vram-perf —— 未读；④ 方向 —— F3/F4 判采纳，下一轮
  做 F5/F6 与其余清单项的判决。
- **预算**：重启 0、构建 0。未超支。
- **没测什么**：exact 臂上的 T2（需要换配置）；接受率随温度/prompt 分布的变化；4 路下的接受率。
- **下一轮第一件事**：R7 = 清单收口：F5（SSD KV）判决、F6（唯一可用性配置/预设）、F7–F11 逐项判决，
  然后 W4 的定容矩阵（`N×CTX`）与 `strata-022.mp-*.json` 预设。

---

## R5 · 2026-10-25 · W3 暂存读**真的生效**：长读 TTFT 47.3 → 17.2 s；短读首 token 46.2 → 1.7 s；第二 token 仍等槽

- **改了什么**：重装 B（补丁引擎 + 补丁 server + staged env；这次 `run-022.sh` 里 `export STRATA_MP_STAGED_READ=1`，
  因为配置 `env` 只进引擎子进程）。测完恢复 A；两次重启，在预算内。给补丁加了 3 行服务端日志（BREAD 发出 /
  BRDY 完成 / BADOPT 收养）。
- **数字（A-B-A，同机，A 为恢复后的现役）**：

| 形状 | A（无暂存） | B（暂存读） |
|---|---|---|
| T3 短读（452 token，4×1200 流） | 首 token **46.23 s**、第二 token 46.27 s、邻居停顿 0.97 s | 首 token **1.73 s**、第二 token **32.91 s**（stall 31.18 s）、邻居停顿 **0.50 s** |
| 长读（22.7K token，4×1200 流） | 首 token **47.32 s**、第二 token 47.38 s、总 47.86 s、邻居总时长 50.26 s | 首 token **17.16 s**、第二 token 34.38 s、总 34.76 s、邻居总时长 **37.77 s** |

  长读 TTFT **−64%**；短读 TTFT **−96%**；第二 token 仍要等槽（31–34 s）；邻居不受害（反而更早结束：
  长读不再拖在最后一条流的尾巴上）。
- **机制证据**：B 的两次运行都出现“4 槽全忙”（/metrics）+ 读者在 4 槽 busy 时 1.7 s/17.2 s 拿到首 token，
  这正是 `BREAD`（读进主会话、不占槽）的行为；恢复 A 后同样的探针回到 47 s。**staged 路径这次真的跑了**
  （R4 的 0 次是因为测试假通过、根本没进满槽场景）。
- **预期被推翻**：原以为“暂存读对短读没用”——**错**：短读 TTFT 从 46 s 降到 1.7 s。真正的限制是**第二 token**：
  它不是读的问题，是槽的问题。W3 的收益是“首 token/感知延迟”，不是“完整准入”。
- **四问**：① cron/timer —— 正常；② 残留进程 —— 无；③ vram-perf —— 未读（无异常）；④ 方向 —— 把 W3 定为
  部分采纳（opt-in），下一轮转向 F3/F4 的验收与 F5/F6 判决。
- **预算**：重启 2、构建 0。在预算内。
- **没测什么**：3 条流下的暂存；长读 + 3 流的组合；聚合吞吐（B 的 bench 未跑）；`checks` 随暂存搬入槽（v1 不做）。
- **下一轮第一件事**：R6 = T5 投机接受率验收 + T4/T1/T2 在 A 上复跑；同时把“第二 token”判据写进标准（已改）。

---

## R4 · 2026-10-25 · W3-B 装机 + 复原：staged 路径**未触发**，T3 曾假通过 → 测试已加固；3 次重启

- **改了什么**：装 B 两次（补丁引擎 `4f6f635f` + 补丁 server `9a14cafc` + staged env）。
  第一次装失败：`systemctl stop` 要 sudo（引擎二进制 `Text file busy`），脚本自动复原；
  第二次用 `sudo -S stop` 装上，health 200。随后发现**服务端进程看不到** `STRATA_MP_STAGED_READ`
  （配置 `env` 只进引擎子进程——B 引擎 env 里有，服务端 `/proc/<pid>/environ` 没有），补
  `run-022.sh` 的 `export` 后又重启一次。测完**恢复 A**：引擎 `4fd45369`、server `aec97a89`、
  `run-022.sh` 复原、health 200。
- **证据**：B 的两次运行里引擎日志 **0 条 BREAD / BRDY / BADOPT**；读者仍以 `slot 0 takes 452 tokens`
  的正常 BGEN 入槽。T3 在 B 上“PASS”（读者首 token 0.25 s）是**假通过**——那一刻有空槽（4 条邻居还没都进槽），
  测的不是准入。加固后的 T3 在 A 上：**/metrics 确认 4 槽全忙**、读者首 token **46.23 s**、第二 token 46.27 s、
  邻居停顿 0.97 s → xfail（真基线）。
- **预期被推翻**：原以为“装上 env 就会走 staged”。两个前提都错：① 配置 `env` 不进服务端进程；
  ② T3 的“满槽”前提从没验证过，会跑出假结果。两处都已修（`run-022.sh` export + T3 的 /metrics 门 + 第二 token 判据）。
- **四问**：① cron/timer —— 正常；② 残留进程 —— 无（装/复原后 `pgrep` 为空）；③ `vram-perf` —— 未读；
  ④ 方向 —— 先证明 staged 真的触发（R5），再谈收益。
- **预算**：**重启 3 次（超 ≤2 预算）**。复盘：多出的两次都是没先验证就动手——① 不知道 stop 要 sudo；
  ② 没确认服务端 env 的来源。教训进规则：**装之前先探（停/起权限 + 服务端 env 是否可见）**。
- **没测什么**：staged 路径的运行时（一行没跑过）；W3 的收益（短读本来就≈0，长读才是候选）；G1–G4。
- **下一轮第一件事**：R5 = 用加固后的 T3 重装 B，**先确认真的出现 BRDY/BADOPT**（必要时服务端加一行日志），
  再测 4×1200 + 22.7K 长读的 TTFT（预期 41 s → ~28 s）；若仍不触发，查服务端 `slot is None` 的时序。

---

## R3 · 2026-10-24/25 · W3 引擎补丁：暂存读编译通过（默认关，+145 行），生产未动

- **改了什么**：① 新建 `/opt/ai/src/strata-mp`（生产 0.2.2 源码副本，947 MB，rsync 4 s）；
  ② 全量构建基线：`build-mp/` 333 目标、`ninja -j6` 约 10 分钟，二进制 md5 `57dd3a24…`；
  ③ 打 W3 补丁（`BREAD` / `BADOPT <slot>` / `BCANCEL`，默认关，`STRATA_BATCH_STAGED_READ` 才可达，
  3 个 hunk / 145 行 diff）→ 增量编译链接通过，二进制 md5 `5904997f…`；
  ④ 设计文档 [`MULTIPATH_W3.md`](MULTIPATH_W3-2026-10-24.md)；⑤ 补丁入库 `tools/server/patches/mp-w3-0.1.47.patch`
  （`git apply --check` 在本仓库 0.1.48 上**也通过**）。
- **数字**：构建 10 分钟（6 并发）；补丁 145 行；**0 重启**；生产 `build/`、`build/strata` 与配置一字节未动。
  基线/补丁二进制的 md5 都记在 `/opt/ai/logs/MP-R3-build.log` 末尾（见"原始日志"）。
- **静态核对（本轮最重要的结论）**：`BGEN` 的读本来就写**主会话 `ss`**，槽只是 `copy_to_slot` 的目的地；
  批量解码的提交走 `ver.commit_slots` → **槽会话**（`verify.hpp:365` 的 `slots_`），不写 `ss`。
  所以"读进 ss、稍后收养"**不需要新增 KV 缓冲**，暂存期间 ss 安全——这是 W3 可行的根据。
- **四问**：① cron/timer —— `vram-perf` 每 5 分钟、`nvlink-crc` 每小时，正常；② 残留进程 —— 无；
  ③ `vram-perf.csv` —— 未读（本轮无生产动作，R4 动手前读）；④ 方向 —— 服务端 + 验收，R4。
- **预算**：重启 0、构建 1（在 `strata-mp`）、生产改动 0。未超支。
- **没测什么**：补丁的**运行时**行为（只编译过，**没有跑过一行**）；服务端 `BREAD/BADOPT` 未写；
  G1–G4 全未测；MTP 首窗无 draft（v1 设计）。
- **下一轮第一件事**：**R4 = 服务端暂存路径**（`serve/server.py`：`pick_slot` 满槽 → `BREAD`；`BRDY` → 等槽 →
  `BADOPT`；断连 → `BCANCEL`）+ 协议冒烟（要停生产把 patched engine 跑起来，**记 1 次重启**），然后 A-B-A。

---

## R2 · 2026-10-24 · W2 配置臂（读分片）：**不触发**，2 次重启，恢复逐字节一致

- **改了什么**：装 B 臂（生产 + `STRATA_PIPED_READ_PIECE=32768`、`STRATA_PIPED_DECODE_MAX_MS=400`、
  `STRATA_PIPED_DECODE_MIN_MS=100`），测完装回 A 快照。快照 `/opt/ai/tmp/mp-w2-A-20261024T151531Z.json`，
  恢复后 `cmp -s` 一致、ENV MATCH、health 200。
- **数字**（详见 L-047）：B 臂 T3 36.3 s / 1.06 s（A 27.2 / 0.68）；bench 74.0 / 125.2（A 72.7 / 120.5，+1.8%/+3.9%）；
  piped 14.2 s、45.9 字符/s（A 14.1 s、44.2）。**判决：否决**——B 臂引擎日志 "piped read" 0 条，
  读全走 concurrent 线程路径：生产的 `STRATA_PIPED_CONCURRENT=1` 让分片开关从不触发。
- **预期被推翻**：原以为"4 路无效、3 路以下有用"；实测**生产 gate 下连 1 路+冷读也不触发**——分片是串行读路径的
  旋钮，gate ON 把它整个旁路。W2 这条配置臂到此关闭。
- **四问**：① cron/timer —— 查过：`vram-perf-sample` 每 5 分钟、`nvlink-crc` 每小时都在跑；② 残留进程 ——
  安装前后 `pgrep` 为空；③ vram-perf —— 15:05–15:15 free 826→798 MiB（GPU0），在 700 线以上，未漂移出界；
  ④ 方向 —— W2 关闭，进 W3。
- **预算**：重启 2（装 B + 恢复 A，≈3m23s + 3m38s 安装/恢复；停机在安装窗口内，服务不可用约 190 s/次）、
  构建 0。未超支。
- **没测什么**：3×3 重复的 T3 中位；gate OFF 时该开关的效果（不再候选）。
- **下一轮第一件事**：**R3 = W3 构建**：独立目录 `build-mp/`，加"无槽读 + 暂存 + adopt"补丁（默认关），
  先过逐位门/质量门，不动生产二进制；同时准备 R4 的 A-B-A（≤2 重启）。

---

## R1 · 2026-10-24 · W1 基线：4 路满槽冷读准入 **27.2 s**（只读，0 重启）

- **改了什么**：无（只读测量，生产 0 重启）。顺手两件：① 服务器上清掉一个 **6h48m 的残留等待进程**
  （PID 171331，在等一个早就不存在的 `use-config`，`pgrep` 现有 0）；② `tools/server/piped_scenario.py`
  的 corpus 路径改到 `docs/archive/...`（原路径随文档重组失效），部署后两边 md5 一致。
- **数字**（详见 L-046）：T3（4×1200-token 流 + 452-token 冷读）读者首 token **27.23 s**、邻居停顿 0.68 s；
  对照（邻居只答 ~200 字）读者 5.90 s；bench 单流 **72.7** / 4 路聚合 **120.5 tok/s**、接受率 79.47%；
  piped 冷读 B 墙钟 14.1 s、A 44.2 字符/s、最长间隔 0.70 s。判决：**参考（基线）**。
- **预期被推翻**：原以为"36–43 s"是固定量；实测**排队 ≈ 剩余批次时长**——短答（~200 字）只等 5.9 s，
  长流（1200 token）才等 27 s。T3 以后必须用长流，否则测的是"短批"。
- **四问**：① cron/timer —— 未查（本轮无生产动作；R2 动手前必查）；② 残留进程 —— **查到并清掉 171331**，
  现 `pgrep -af "[u]se-config|[r]estore-prod"` 为空；③ `/opt/ai/logs/vram-perf.csv` 漂移 —— 未读（R2 前读）；
  ④ 方向 —— 按计划 R2 配置臂。
- **预算**：重启 0、停机 0、构建 0。未超支。
- **没测什么**：3 条流形状；4 路 + 冷读的聚合吞吐（T3 未记）；长流分布；agent 形态（L1）。
- **下一轮第一件事**：**R2 = W2 读分片配置臂**（`STRATA_PIPED_READ_PIECE=32768` + `STRATA_PIPED_DECODE_MAX_MS=400`），
  A-B-A（A 现跑着不装 → 装 B 1 次 → 恢复 A 1 次，≤2 重启），同形状跑 T3/bench 对照；先读 `vram-perf.csv` 与 cron。

---

## R0 · 2026-10-24 · 立项与草稿（本轮无生产改动，无新测量）

- **改了什么**：新增 [`MULTIPATH_PLAN.md`](MULTIPATH_PLAN-2026-10-24.md)（W1–W4 计划）、
  [`MULTIPATH_STANDARD.md`](../MULTIPATH_STANDARD.md)（多路口径）、
  [`MULTIPATH_RULES.md`](../MULTIPATH_RULES.md)（执行规则）、[`FEATURE_PARITY.md`](FEATURE_PARITY-2026-10-24.md)（功能清单/测试/验收）、
  本文件；`docs/README.md` 登记五份文档。参考仓库 `/home/kali/IdeaProjects/vLLM-2080Ti-Definitive` 已拉到本地
  （main = v0.2.2-post3）。**未碰生产。** 目标已建：`goal-402218d9-57d7-4af1-aea0-391a1494acd9`（60 轮上限）。
  新增 `tools/server/tests/` 黑盒验收套件（T1–T4，stdlib，可直接跑；T3 在 W3 前 xfail 记基线）。
  子代理核对 `src/` 与 `serve/` 后修正了静态引用与三处设计前提（W3 的"暂存"今天不存在、BGEN 才是读的触发、
  等槽循环在 `generate_batched` `1876-1909`、BYIELD 在 READ_PIECE 下可触发、`--kv-resident` 语义、
  W2 的上游数字）——**全部静态阅读，未测**。
  子代理深读 fork 后补入步契约事实：每请求 cursor、decodes 先喂、剩余 2048 给新 prefill、late prefill 只推迟
  `num_output_tokens==0` 的请求（`scheduler.py:734-740`）；profile 命名 `N×CTX` 与启动门 `N×CTX ≤ GPU KV tokens`
  （`launcher.sh:176-205`）；SSD 持久化 #243 的 `checkpoint-<sha256>` 布局。官方测试盘点：1,877 个 pytest、
  API 70 个文件、spec_decode 30 个；`benchmark_serving.py` 已废弃（改用 `vllm bench serve`）。
- **数字**：本轮不产生性能测量。**功能测试已跑**（2026-10-24，本机 → `192.168.3.14:8080`，key 111）：
  - `test_mp_api.py`（T1）**6/6 通过**（health、models、非流式/流式 usage、400、401）；
  - `test_mp_batch.py`（T2）**xfail**：批内回答与单发不同——生产没开 exactness 设置，符合预期（改文档
    `BATCHING.md` §Exactness）；exact 臂上设 `MP_EXACT=1` 会转成断言；
  - `test_mp_prefix.py`（T4）**通过**：第二次读命中前缀缓存（`cached_tokens` ≥50%）；
  - `test_mp_admission.py`（T3）**未跑**：需要没有别的流的干净窗口（本会话自己就是第二路流），留 R1。
  引用的基线：单流 63 tok/s、三流聚合 136 tok/s、满槽冷读排队 36–43 s（L-019/L-020/L-021）。
- **预期被推翻**：无（本轮只规划）。
- **四问**：① cron/timer —— 未查（本轮无生产动作，R1 开测前必查）；② 残留进程 —— 未查（R1 前查）；
  ③ `vram-perf.csv` 漂移 —— 未读（R1 前读）；④ 方向 —— 按计划走 W1。
- **预算**：重启 0、停机 0、构建 0。未超支。
- **没测什么**：全部多路性能数字（R1 开始）；W2 的读分片在本机的效果；W3 的 read-ahead 一切。
- **下一轮第一件事**：**R1 = W1 基线复现**（只读、0 重启、不派子代理）：
  `bench-strata.py` + `piped_scenario.py` + `gate_prod_streams.sh` 只读部分，把"4 路满槽 + 冷读"的
  p50/p90 与邻居停顿钉下来。

---

## 模板（以后每轮复制这一段）

```
## R<n> · <日期> · <一句话>
- 改了什么：<diff 摘要 + 文件；没改就写"无">
- 数字：<表：臂 / 中位 / sd / N 路 / 冷热 / 判据>；判决：<采纳/否决/未定/参考>
- 预期被推翻：<"我们原以为……，实测……"；没有就写"无">
- 四问：① cron/timer ② 残留进程 ③ vram-perf 漂移 ④ 方向
- 预算：重启 <n>（上限 2）、停机 <s>、构建 <n>；超支原因
- 没测什么：<清单>
- 下一轮第一件事：<一句>
```
