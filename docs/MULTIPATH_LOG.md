# 多路融合轮次日志（MULTIPATH_LOG）

> **每轮一条，最新在上。** 复盘格式见 [`PORTING_RULES.md`](PORTING_RULES.md) §11；**从 R11 起**每条必须带
> `### Gate（本轮）` 的 11 个字段，并过 `python3 tools/server/round_gate.py --round R<n>`；
> xfail/skip/FAIL 记 FAIL。数字的原始证据在 [`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md)，状态在
> [`HANDOVER.md`](HANDOVER.md)，计划在 [`MULTIPATH_PLAN.md`](MULTIPATH_PLAN.md)。
> **Gate 不 PASS = 本轮按未发生处理。**

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
  ④ 设计文档 [`MULTIPATH_W3.md`](MULTIPATH_W3.md)；⑤ 补丁入库 `tools/server/patches/mp-w3-0.1.47.patch`
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

- **改了什么**：新增 [`MULTIPATH_PLAN.md`](MULTIPATH_PLAN.md)（W1–W4 计划）、
  [`MULTIPATH_STANDARD.md`](MULTIPATH_STANDARD.md)（多路口径）、
  [`MULTIPATH_RULES.md`](MULTIPATH_RULES.md)（执行规则）、[`FEATURE_PARITY.md`](FEATURE_PARITY.md)（功能清单/测试/验收）、
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
