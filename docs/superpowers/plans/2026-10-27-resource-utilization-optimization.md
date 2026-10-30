# 资源利用率优化计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Version:** v3，2026-10-30 重写。v2 从 NCU 内核入手，只回答了"窗口里哪个 kernel 慢"；
本版从真实会话 SLO 和批处理解码架构入手，先修"结构性串行与能力缺失"，再把内核优化降为最后手段。

**Goal:** 让 1–6 路真实交互会话在长上下文下保持可预期的吞吐与延迟：并发时不掉一半，
长预填不吞掉正在解码的会话，超长工具调用不把邻居拖成几分钟。

**Architecture:** 先把"会话单元"作为一等对象：每条会话必须有草稿、K/V、停车与调度策略。
用场景矩阵（1 路、2 路、2 路 + 200K 预填、24K 工具调用、6 路）定义 SLO，再按证据逐层修：
并发 MTP → 预填让路 → 能力矩阵防护 → 条件性的代码改动。每一层都是一个可回滚的候选，
不是一次性大改。

**Tech Stack:** Ubuntu、CUDA 13、sm_75、双 RTX 2080 Ti 层切分（2 段 2 卡）、Strata batch /
batch-groups / MTP / self-commit / piped concurrent 路径、Python 探针、JSON 证据。

---

## 为什么再次重写

v2 的思路是"停服 → NCU 看 kernel → 改一个内核"。它没有回答三个更基本的问题：

1. 真实会话是 2 路并发、50K–200K 上下文、偶发 24K token 工具调用，内核计数再漂亮也不等于
   用户等待更短。
2. 当前生产配置同时落在三个"能力被静默关掉"的格子里（见"问题分解"），这属于架构与配置面
   问题，不是 kernel 效率问题。
3. 过去反复修却进展不大，是因为每条路径（solo、batch、pipelined groups）都有一套自己的
   能力矩阵，缺一套"组合是否成立"的检查；修好一格，另一格又退化。

本版的裁决：**先配置、后代码；先结构、后内核**。内核优化只在结构性修复完成且仍不达标时启动，
见附录 B。

## 设计原则

1. **场景先行**：任何改动必须对应场景矩阵中的一行，并给出可观察的前后对比；没有场景的改动
   不进本计划。
2. **延迟优先、工作守恒**：正在解码的会话是前台，长预填是后台。后台不能整段占用流水线。
3. **每槽状态一等公民**：草稿、K/V、长上下文停车都必须对每个 slot 存在，不能只有单路路径有。
4. **组合必须显式**：任何特性组合要么可用，要么有具名、可观测、可测试的降级；不允许"悄悄关掉"。
5. **按规模选策略**：2 路与 6 路的最优组数/草稿策略不同。计划按场景选，不追求一套参数打天下。
6. **可回滚、可证伪**：A/B/A、分布指标、门限预注册；服务器侧自愈先于一切停服改动。
7. **复用现有机制**：`BATCH_MTP`、`PIPED_MTP`、`PIPED_READ_PIECE`、lead-in、conversation cache
   都已存在，先用配置把它们组合正确，再考虑改代码。

## 实测事实（2026-10-30）

### 会话侧（来自并行会话的实测，本计划开工前由阶段 1 重测）

- 单路 400 token：`55.9 / 69.0 tok/s`，中位 `62.5`。
- 两路并发：聚合中位 `75.8 tok/s`，每路约 `36.4 tok/s`；两路请求 `draft_n=0`。
- 一路结束，另一路立即从约 `40` 恢复到 `68.4 tok/s`，可稳定复现。
- 最近 90 次完成请求：解码中位 `72.5`，p10 `39.9`；9 次超过 60 s，3 次超过 180 s，
  1 次 498 s（24,319 tokens，平均 57.5 tok/s；约 423 s 解码 + 约 75 s 预填与开销）。
- 上下文常在 50K–200K；缓存命中率约 92.7%；201K 全量预填约 100 s。
- `/health` 20/20 成功、`2.1–3.4 ms`，局域网无丢包：网络不是原因。

### 引擎侧（本次只读复核，均有原文可查）

- 引擎 argv：`--batch 6 --batch-groups 2 --prefill-pipe 1 --spec 8 --mtp-max-t 4 --mtp ...
  --max-context 262144 --kv int8 --resident-experts --conversation-cache-slots 12`。
- 配置 `env`（17 个开关，engine 进程合计 24 个 `STRATA_*` 变量）中**没有**
  `STRATA_BATCH_MTP` 与 `STRATA_PIPED_MTP`，但有
  `STRATA_BATCH_SELF_COMMIT=1`、`STRATA_PIPED_CONCURRENT=1`、`STRATA_BATCH_STAGED_READ=1`、
  `STRATA_MP_STAGED_READ=1`、`STRATA_PIPED_DECODE_LEADIN_MS=1600` 等 24 个开关。
- 引擎启动日志确认：2 卡层切分（CUDA0 层 0–23，CUDA1 层 24–47）、每卡独立专家缓存
  （CUDA0 6708 slots、CUDA1 7209 slots / 13.05 GiB）、`kVerifyMaxT = 8`。
- 生产日志出现 **15 次** `STRATA_PIPED_CONCURRENT is off beside an expert source that stages
  its blobs`：预填并发读在现网从未生效。
- 生产日志出现 **7 次** `slot N not parked before its overwrite (its 146573/204057/216086 ...
  tokens have no per-slot draft K/V without --batch-mtp)`：长上下文无法停车复用。
- 282 条专家缓存命中行，命中 94.2%–100%；预填 chunk `8192`，`--prefill-pipe 1` 时按
  `7424` token 分块、2 段流水。

### 代码锚点

- `src/program/generate.cpp:5037-5097`：`--batch-mtp` / `STRATA_BATCH_MTP` 的门槛——需要
  `--batch ≥2`、`--mtp`、`--spec ≥2`、`--serve`、每段独立 GPU；层切分 + `batch-groups >1`
  且没有 `PIPED_MTP` 时会直接关掉草稿。
- `src/program/generate.cpp:5251-5275`：`STRATA_PIPED_MTP` 在最终分组后再次判定；
  `STRATA_BATCH_SELF_COMMIT` 为开时拒绝组合，原因是"窗口会自己保留行"。
- `src/core/verify.cpp:181-200`：self-commit 本身是**逐窗口**生效的
  （`one_row_per_slot` 才启用），所以"MTP 窗口 + 普通窗口混跑"在语义上并非天然冲突，
  当前是全局开关一票否决。
- `include/strata/program/batch_read.hpp:44-56`：并发读关闭原因清单；当前命中的是
  `staged_source`（GGUF in place / unbuffered / `STRATA_IO_PREFETCH`）。

## 问题分解

### P0：并发解码失去草稿（两路直接掉一半）

机制链：`batch-groups=2` → 走 pipelined 路径 → 没有 `BATCH_MTP`，所以 `batch_mtp=false`；
即使打开 `BATCH_MTP`，pipelined 组仍不草稿，除非再开 `PIPED_MTP`；而 `PIPED_MTP` 又被
`SELF_COMMIT=1` 一票否决。结果：每个窗口只推进每槽 1 个 token，`draft_n=0`。

证据：两路 `36.4 tok/s`、`draft_n=0`、一路结束后另一路回到 `68.4`（窗口变小即恢复），
以及 `docs/BATCHING.md` 在 2×RTX 3080 上的实测：草稿组聚合 `89.2`，无草稿串行 `77.3`，
无草稿流水 `77.7`。

### P1：长预填整段吞掉流水线

机制链：`STRATA_PIPED_CONCURRENT=1` 被 staged 专家源关闭 → 预填期间没有并发读 →
`STRATA_PIPED_READ_PIECE` 未设置，整段 prompt 一次读完 → 解码会话在整个预填期间停摆。
201K 预填约 100 s，邻居就停约 100 s。

证据：生产日志 15 次关闭告警；`docs/VLLM_PORT_PLAN.md` 记录的 S0b 邻居停顿 3.43 s 属同一
机制的小样本；本次复核实测到整段吞没。

### P2：长上下文无法停车

机制链：没有 `--batch-mtp` 就没有每槽 draft K/V → conversation cache 无法停车 95K–216K
的会话 → 命中率 92.7% 里剩下的部分反复重读，叠加 P1 变成几分钟等待。

证据：7 条 "not parked" 日志，token 数 474–216,086。

### 横向根因：能力矩阵没有测试

三个问题不是三个孤立 bug，而是同一条设计缺陷：**solo / batch / pipelined 三条路径的能力
不一致，且组合退化只打一行 warning**。所以本计划把"能力矩阵防护"（阶段 4）当成正式交付物，
而不是附带说明。

## 目标（SLO，阶段 2 开工前由用户确认）

| 场景 | 指标 | 当前 | 目标 |
| --- | --- | --- | --- |
| S1 单路 400 token | 中位 tok/s | 62.5 | ≥60（保持，不回退） |
| S2 两路 400 token | 每路中位 tok/s | 36.4 | ≥50 |
| S2 | 聚合中位 tok/s | 75.8 | ≥110 |
| S2 | `draft_n` | 0 | 两路都 >0 |
| 90 次完成请求 | p10 tok/s | 39.9 | ≥50 |
| S3 两路 + 200K 冷预填 | 邻居每路 tok/s / 单次最大停顿 | 0 / 整段 | ≥30 / ≤5 s |
| S3 | 200K 预填总时长 | ≈100 s | ≤110 s（允许 10% 回退换低停顿） |
| S4 24K 工具调用 + 1 路聊天 | 工具调用总时长 / 邻居 | 498 s / 未知 | ≤440 s / ≥45 tok/s，p95 停顿 ≤5 s |
| S5 六路 | 聚合 tok/s | 149.8–164.7 | 不回退超过 3% |

目标不达标但方向正确的候选，最多保留为"条件采纳"，并且必须写明差多少、差在哪个场景。

## 场景矩阵

| 编号 | 负载 | 关键观察量 |
| --- | --- | --- |
| S1 | 单路，约 4K prompt，400 token 输出 | 中位/ p10 tok/s |
| S2 | 两路并发，不同主题，400 token | 每路与聚合 tok/s、`draft_n`、窗口时长 |
| S3 | 两路解码 + 一路 200K 冷预填 | 邻居 tok/s、最大停顿、预填总时长 |
| S4 | 一路 24K token 工具调用 + 一路普通聊天 | 工具调用总时长、邻居 tok/s、p95 停顿 |
| S5 | 六路并发（batch 上限） | 聚合 tok/s、每路 p10 |

采集工具：S1/S2/S5 用本计划新增的 `tools/server/session_matrix.py`；S3 用现有
`tools/server/piped_scenario.py`（127K 冷读 + 邻居解码）；S4 用 `session_matrix.py`
的 `--tokens 24000` 变体。所有结果写 `/opt/ai/logs/util-v3-$TS/` 下的 JSON。

## 能力矩阵（当前状态）

| 能力 | solo | batch `groups=1` | pipelined `groups=2` |
| --- | --- | --- | --- |
| MTP 草稿 | 开（`spec 8`，最多 4 草稿） | 需 `BATCH_MTP=1` | 需 `BATCH_MTP=1` + `PIPED_MTP=N` |
| 窗口自提交 | 不适用 | 有（T=1 窗口） | 与 `PIPED_MTP` 互斥：`SELF_COMMIT=1` 直接关掉草稿 |
| 预填并发读 | 不适用 | 不适用 | 需 `PIPED_CONCURRENT=1`，且无 staged/helper/pcie-dma |
| 每槽 draft K/V、长上下文停车 | 有 | 仅 `BATCH_MTP` 时 | 仅 `PIPED_MTP` 时 |
| **当前生产** | — | — | 草稿关、并发读关、停车缺：三格同时降级 |

容量约束：一个组窗口最多 `kVerifyMaxT=8` 行。`batch=6, groups=2` → 每组 3 槽，
`PIPED_MTP=1` 时 3×(1+1)=6 行可用；`PIPED_MTP=2` 时 9 行超限，会被拒绝。

## 测量协议

- 每个数字至少 3 轮；报中位与 p10/p90，不报单次峰值。
- 每个候选做 A/B/A：A1 冻结候选 → B 候选 → A2 冻结候选；A1/A2 差异超过 5% 时加到 5 轮。
- 所有改配置的步骤都经过"自愈门"（附录 A），停服窗口硬截止 30 分钟、接管点 15 分钟。
- 质量门：greedy parity（`tools/server/parity_window.py`）、needle、一轮 `ctx256` soak。
- 所有原始日志、JSON 与判决写入 `/opt/ai/logs/util-v3-$TS/`，不覆盖旧证据。

## 阶段 0：自愈门（前置，所有停服动作的前提）

**Files:**
- Create: `tools/server/util-restore.sh`、`tools/server/util-watchdog.sh`
- Create: `tools/server/dump-runtime.py`、`tools/server/tests/test_dump_runtime.py`
- Modify: `tools/server/ncu-exec.py`（补 cwd 还原）

- [ ] **Step 1: 部署自愈工具并演练一次恢复**

脚本见附录 A；部署后必须做一次真实停服演练，预期 `WINDOW RESTORED ... layer=relaunch`，
总停服接近 165 s 量级。

- [ ] **Step 2: 演练看门狗接管**

驱动被 `SIGKILL` 时，看门狗必须在接管点之后、硬截止之前把生产带回 `loaded=true`。

- [ ] **Step 3: 建立正式回滚快照**

```sh
ssh atm@192.168.9.103 'TS=$(date -u +%Y%m%dT%H%M%SZ)
TREE=/opt/ai/src/strata-freeze-throughput-20261029T0812Z
SNAP="/opt/ai/tmp/util-v3-$TS"; install -d -m 750 "$SNAP"
cp -p "$TREE/build75/strata" "$SNAP/engine"
cp -p "$TREE/serve/server.py" "$SNAP/server.py"
install -m 600 /tmp/strata-freeze-candidate-long.json "$SNAP/config.json"
python3 /opt/ai/bin/dump-runtime.py --pattern "serve/[s]erver.py --engine" \
  --out "$SNAP/server-runtime.json"
python3 /opt/ai/bin/dump-runtime.py --pattern "[s]trata --serve --pack" \
  --out "$SNAP/engine-runtime.json"
sha256sum "$SNAP/engine" "$SNAP/server.py" "$SNAP/config.json" > "$SNAP/MANIFEST.sha256"
printf "%s\n" "$TS" > /tmp/strata-util-run-ts; chmod 600 /tmp/strata-util-run-ts
printf "%s\n" "$SNAP" > /tmp/strata-util-snapshot-path; chmod 600 /tmp/strata-util-snapshot-path
sha256sum -c "$SNAP/MANIFEST.sha256"'
```

Expected: manifest 全项 OK；`server-runtime.json`/`engine-runtime.json` 为 `0600`；
快照里没有 `live-runtime.json`（阶段 1 生产就是冻结候选）。

Expected（阶段门）：两条演练 + 快照都通过才允许进入阶段 1/2；只做静态检查不算通过。

## 阶段 1：基线复核（只读，不重启）

**Files:**
- Read: `/tmp/strata-freeze-candidate-long.json`
- Read: `/home/atm/verify-20261027/strata-verify/strata-verify.log`
- Create: `tools/server/session_matrix.py`、`tools/server/tests/test_session_matrix.py`
- Create: `/opt/ai/logs/util-v3-$TS/`

- [ ] **Step 1: 采集配置与日志证据**

```sh
ssh atm@192.168.9.103 'L=/home/atm/verify-20261027/strata-verify/strata-verify.log
python3 -c "import json;c=json.load(open(\"/tmp/strata-freeze-candidate-long.json\"));\
print(sorted(c.get(\"env\",{})))"
grep -ac "PIPED_CONCURRENT is off beside" "$L"
grep -ac "not parked before its overwrite" "$L"
grep -a "prefill plan: chunk" "$L" | tail -1'
```

Expected: 配置里没有 `BATCH_MTP`/`PIPED_MTP`，有 `BATCH_SELF_COMMIT`；两条告警计数非零；
chunk 为 `8192`。与本计划"实测事实"一致才能继续。

- [ ] **Step 2: 建立 S1/S2/S5 基线**

`session_matrix.py` 按固定 prompt、`temperature=0` 并发发请求，记录每路与聚合 tok/s、
`draft_n`/`draft_n_accepted`、完成时长：

```sh
ssh atm@192.168.9.103 'TS=$(date -u +%Y%m%dT%H%M%SZ)
python3 /opt/ai/bin/session_matrix.py --streams 1 --tokens 400 --rounds 3 \
  --label baseline-s1 --out "/opt/ai/logs/util-v3-$TS/s1.jsonl"
python3 /opt/ai/bin/session_matrix.py --streams 2 --tokens 400 --rounds 3 \
  --label baseline-s2 --out "/opt/ai/logs/util-v3-$TS/s2.jsonl"
python3 /opt/ai/bin/session_matrix.py --streams 6 --tokens 400 --rounds 3 \
  --label baseline-s5 --out "/opt/ai/logs/util-v3-$TS/s5.jsonl"'
```

Expected: S2 每路中位与聚合落在并行会话结果的 ±10% 内；`draft_n=0`。
本步骤会产生真实负载，必须在用户明确同意的时间段执行。

- [ ] **Step 3: 基线判决**

三条基线全部复现才继续；任一条偏差超过 10% 时，先查环境（温度、其他占用、缓存状态），
不进入改动。

## 阶段 2：候选 A —— 并发 MTP 恢复（配置级）

**Files:**
- Read: `/tmp/strata-freeze-candidate-long.json`
- Create: `/opt/ai/tmp/util-candidate-a-$TS.json`
- Create: `/opt/ai/logs/util-v3-$TS/candidate-a-*.jsonl`

- [ ] **Step 1: 预检组合是否成立**

```text
batch=6, groups=2, kVerifyMaxT=8
每组槽位 = 6/2 = 3
PIPED_MTP=1 → 每组窗口 3×(1+1)=6 行 ≤ 8  ✓
PIPED_MTP=2 → 9 行 > 8  ✗（会被引擎拒绝）
```

同时确认：`--mtp` 存在、`--spec ≥2`、两段在两块 GPU 上、没有 helper 专家缓存
（日志无 `--expert-cache-device1..3`）、显存余量足够 6 个槽草稿（约 50 MiB/槽 + K/V ring）。

- [ ] **Step 2: 生成候选配置**

只改 `env`，其余字段逐字不变：

```json
{
  "STRATA_BATCH_MTP": "1",
  "STRATA_PIPED_MTP": "1",
  "STRATA_BATCH_SELF_COMMIT": "0"
}
```

```sh
ssh atm@192.168.9.103 'TS=$(cat /tmp/strata-util-run-ts)
python3 - /tmp/strata-freeze-candidate-long.json "/opt/ai/tmp/util-candidate-a-$TS.json" <<PY
import json, sys
base = json.load(open(sys.argv[1], encoding="utf-8"))
cand = json.loads(json.dumps(base))
cand.setdefault("env", {}).update({
    "STRATA_BATCH_MTP": "1",
    "STRATA_PIPED_MTP": "1",
    "STRATA_BATCH_SELF_COMMIT": "0",
})
assert {k: v for k, v in cand.items() if k != "env"} == \
       {k: v for k, v in base.items() if k != "env"}
json.dump(cand, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY'
```

Expected: 除 `env` 三个键外与生产配置完全一致。

- [ ] **Step 3: 装载候选配置并启动**

候选配置的装载方式（阶段 3 复用同一约定）：复制冻结 `server-runtime.json`，把 `--config`
换成候选配置路径，写成 `$SNAP/live-runtime.json`，清进程后用 `util-restore.sh` 拉起；
回到冻结候选 = 删除 `live-runtime.json` 再 `util-restore.sh`。

```sh
ssh atm@192.168.9.103 'TS=$(cat /tmp/strata-util-run-ts); SNAP=$(cat /tmp/strata-util-snapshot-path)
python3 - "$SNAP" "/opt/ai/tmp/util-candidate-a-$TS.json" <<PY
import json, pathlib, sys
snap, cfg = pathlib.Path(sys.argv[1]), sys.argv[2]
runtime = json.loads((snap / "server-runtime.json").read_text(encoding="utf-8"))
runtime["argv"][runtime["argv"].index("--config") + 1] = cfg
out = snap / "live-runtime.json"
out.write_text(json.dumps(runtime, ensure_ascii=False), encoding="utf-8")
PY
chmod 600 "$SNAP/live-runtime.json"
pkill -TERM -f "serve/[s]erver.py --engine" || true
pkill -TERM -f "[s]trata --serve" || true; sleep 5
bash /opt/ai/bin/util-restore.sh "$SNAP"'
```

回到 A1/A2（冻结）：`rm -f "$SNAP/live-runtime.json"` 后同样清进程并 `util-restore.sh`。

Expected: 版本探针的 running config 指向 `/opt/ai/tmp/util-candidate-a-$TS.json`；能力日志出现
`STRATA_PIPED_MTP: 2 pipelined groups of 3 slots draft up to 1 MTP proposal`；显存余量未跌破安全线。

- [ ] **Step 4: A/B/A 窗口**

每个 arm 都是一次停服：A1 冻结 → B 候选 A → A2 冻结。每个 arm 内跑 S1、S2 各 3 轮，
并抓引擎启动日志中的能力行。

Expected（B arm）：

- 引擎日志出现 `STRATA_PIPED_MTP: 2 pipelined groups of 3 slots draft up to 1 MTP proposal ...`；
- 不出现 `WARNING: STRATA_PIPED_MTP is off` 与 `--batch-mtp is off`；
- S2 两路 `draft_n > 0`，每路 ≥50、聚合 ≥110；
- S1 不回退超过 3%，无 OOM、swap、槽位降级。

- [ ] **Step 5: 判决**

通过 → 候选 A 进入阶段 7 综合验收；不通过 → 记录引擎拒绝原因（VRAM、行数、helper cache、
其它），回到阶段 1 重新定位，不叠加别的改动。

## 阶段 3：候选 B —— 预填给解码让路（配置级）

**Files:**
- Read: `tools/server/piped_scenario.py`
- Create: `/opt/ai/logs/util-v3-$TS/s3-*.jsonl`

- [ ] **Step 1: 复现 S3 现状**

```sh
ssh atm@192.168.9.103 'TS=$(cat /tmp/strata-util-run-ts)
python3 /opt/ai/bin/piped_scenario.py 3 800000 \
  > "/opt/ai/logs/util-v3-$TS/s3-baseline.txt"'
```

Expected: 邻居解码在整段预填期间停顿（引擎日志出现 `PIPED_CONCURRENT is off beside ...`，
没有 `read ... the slots decoded N rows` 行）。

- [ ] **Step 2: 候选 B1/B2 两个窗口**

在候选 A 之上只加一个键，分别测两个粒度：

```text
B1: STRATA_PIPED_READ_PIECE=8192   （1 个 chunk，停顿约 4 s）
B2: STRATA_PIPED_READ_PIECE=16384  （2 个 chunk，停顿约 8 s，但开销更小）
```

生成方式：以候选 A 的配置为底，只加 `STRATA_PIPED_READ_PIECE`，写成
`/opt/ai/tmp/util-candidate-b1-$TS.json` 与 `-b2-`，再用阶段 2 Step 3 的装载方式各开
一个窗口；每个 arm 前后都要回到冻结候选做对照。

Expected：B1 最大停顿 ≤5 s、预填总时长 ≤110 s；B2 作为"更省开销但停顿更长"的对照。

- [ ] **Step 3: 判决**

采用满足 SLO 的最小开销粒度；若两者都无法把停顿压到 5 s 以内，保留较好者并进入阶段 6。

## 阶段 4：能力矩阵防护（代码，无性能改动）

**Files:**
- Create: `tools/server/capability_check.py`、`tools/server/tests/test_capability_check.py`
- Modify: `tools/server/probe-version-stamp.py`

- [ ] **Step 1: 先写失败测试**

用真实日志片段做 fixture：`PIPED_MTP ... draft up to`、`WARNING: ... is off`、
`PIPED_CONCURRENT is off beside`、`not parked ... without --batch-mtp`。测试要求：

- 请求了草稿但日志显示关闭 → `problems` 非空；
- 请求了并发读但日志显示关闭 → `problems` 非空；
- 全部生效 → `problems=[]`。

- [ ] **Step 2: 实现检查并接入版本探针**

`capability_check.py` 只读引擎启动日志与配置 `env`，输出结构化能力表：
`drafts`、`piped_drafts`、`concurrent_read`、`slot_parking`，每项 `on|off|na` 与 `reason`。

- [ ] **Step 3: 验证**

```powershell
python -m pytest tools/server/tests/test_capability_check.py `
  tools/server/tests/test_probe_version_stamp.py -q
```

Expected: 全绿；现网跑一次能准确报出"草稿关、并发读关、停车缺"，与阶段 1 的证据一致。

## 阶段 5：候选 C —— self-commit 与 PIPED_MTP 共存（条件启动）

只有满足任一条才启动：阶段 2 因 `SELF_COMMIT` 无法验证；或阶段 2 通过但普通窗口比 A1 慢 5% 以上。

**Files:**
- Modify: `src/program/generate.cpp`
- Modify: `src/program/batch_groups_test.cpp`
- Modify: `tests/program/batch_read_test.cpp`（如涉及）

- [ ] **Step 1: 用测试钉住语义**

先写失败测试：`SELF_COMMIT=1` + `PIPED_MTP=1` + 6 槽 2 组时，引擎不得再全局关闭草稿；
普通 T=1 窗口仍走 `batch_self_commits()`，MTP 窗口仍走正常 commit 图。

- [ ] **Step 2: 最小实现**

把"全局一票否决"改成"逐窗口判定"：`verify.cpp` 已经是逐窗口的，只需要确认混合窗口下
状态快照/回放不交叉；实现加一个显式环境开关，默认保持旧行为，便于回滚。

- [ ] **Step 3: 验证**

目标单测 + `batch_groups_test` + 一次 B 窗口 S2；要求 token 完全一致（greedy parity）。

## 阶段 6：候选 D —— staged 源下的并发读（条件启动，代码）

只有阶段 3 判不了 S3 的停顿目标时启动。机制是 staged GGUF 源"一次只交给一个线程"，
需要让读线程拥有自己的 ring 或让回收串行化；这是本计划风险最高的改动，默认不做。

Expected: S3 邻居 ≥30 tok/s、最大停顿 ≤5 s、预填 ≤110 s，且 6 路无明显回退；否则回滚。

## 阶段 7：综合验收与回写

- [ ] **Step 1: 全场景 A/B/A**：S1、S2、S3、S4、S5 各 3 轮；报中位与 p10/p90。
- [ ] **Step 2: 质量门**：greedy parity、needle、`ctx256` 一小时 soak；0 错误、0 降级。
- [ ] **Step 3: 判决**：只有 SLO 全部达标（或用户明确接受差额）才设为生产候选。
- [ ] **Step 4: 回写**：`docs/STATE.md`、`docs/WORK_QUEUE.md`、`docs/EVIDENCE_LEDGER.md`、
  `docs/ITERATION_LOG.md` 分别记录已验证收益、未验证项、残余风险与回滚路径。

## 停止条件

- 阶段 1 基线无法在 ±10% 内复现：停止，先排除环境变化。
- 引擎拒绝候选能力（行数超限、显存不足、helper cache、staged 源）：停止并记录原因，
  不叠加第二个改动去"绕过"。
- 任一场景 OOM、swap、槽位降级、needle 失败或 greedy parity 不一致：立即回滚。
- 6 路聚合回退超过 3%：候选不采纳。
- 自愈演练没有以 `layer=relaunch` 收尾：停止全部停服动作。
- 单次停服窗口超过 20 分钟或累计窗口超过 8 个：停止复盘，重排优先级。

## 最终验收门

- S2 两路：每路 ≥50 tok/s、聚合 ≥110、`draft_n>0`（两路）。
- S3：预填期间邻居 ≥30 tok/s、最大停顿 ≤5 s、预填 ≤110 s。
- S4：24K 工具调用 ≤440 s，邻居 ≥45 tok/s，p95 停顿 ≤5 s。
- S5：6 路聚合回退不超过 3%。
- 90 次完成请求 p10 ≥50 tok/s。
- 质量门全过；每个窗口 `layer=relaunch`；证据、判决、回滚路径全部落盘。

## 附录 A：自愈脚本（阶段 0 使用）

### util-restore.sh

```bash
#!/bin/bash
# 无条件把生产带回健康状态：先按快照重启冻结候选，失败再退回 systemd。
# 不依赖 agent 在线，也不依赖被停掉的 strata。
# 用法: util-restore.sh [SNAP_DIR]；输出 WINDOW RESTORED ts=... layer=relaunch|systemd|none
set -u

TS=$(cat /tmp/strata-util-run-ts 2>/dev/null || true)
SNAP="${1:-$(cat /tmp/strata-util-snapshot-path 2>/dev/null || true)}"
CFG="/tmp/strata-freeze-candidate-long.json"
export RESTORE_LAYER=none
if [ -z "$SNAP" ]; then
  echo "WINDOW RESTORED ts=${TS:-?} layer=none reason=no_snapshot"; exit 1
fi

log() { printf '%s restore %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

wait_health() {
  for _ in $(seq 1 140); do
    H=$(curl -fsS -m 5 http://127.0.0.1:8080/health 2>/dev/null || true)
    case "$H" in *'"loaded": true'*) return 0 ;; esac
    sleep 3
  done
  return 1
}

clear_pair() {
  pkill -TERM -f "serve/[s]erver.py --engine" 2>/dev/null
  pkill -TERM -f "[s]trata --serve" 2>/dev/null
  sleep 5
  pkill -KILL -f "serve/[s]erver.py --engine" 2>/dev/null
  pkill -KILL -f "[s]trata --serve" 2>/dev/null
  return 0
}

relaunch() {
  [ -x "$SNAP/engine" ] || return 1
  # 冻结 runtime 永远保留作回滚；live-runtime 只在候选装载后出现，优先使用。
  RT="$SNAP/server-runtime.json"
  [ -f "$SNAP/live-runtime.json" ] && RT="$SNAP/live-runtime.json"
  [ -f "$RT" ] || return 1
  clear_pair
  if [ ! -f "$CFG" ]; then
    [ -f "$SNAP/config.json" ] || return 1
    install -m 0600 "$SNAP/config.json" "$CFG" || return 1
  fi
  cd "$SNAP" || return 1
  nohup python3 /opt/ai/bin/ncu-exec.py "$RT" \
    >>/opt/ai/logs/utilization-restore.log 2>&1 &
  wait_health
}

systemd_fallback() {
  clear_pair
  echo 'p99832586' | sudo -S systemctl start strata.service >/dev/null 2>&1
  wait_health
}

if relaunch; then
  RESTORE_LAYER=relaunch
elif systemd_fallback; then
  RESTORE_LAYER=systemd
fi
log "layer=$RESTORE_LAYER ts=${TS:-?}"
echo "WINDOW RESTORED ts=${TS:-?} layer=$RESTORE_LAYER"
[ "$RESTORE_LAYER" = none ] && exit 1
exit 0
```

### util-watchdog.sh

```bash
#!/bin/bash
# 停服窗口的看门狗：驱动脚本可能被 SIGKILL，恢复不能只靠驱动自己的 trap。
# 用法: util-watchdog.sh RUN_TS DEADLINE_EPOCH TAKEOVER_EPOCH
set -u
TS="${1:-}"; DEADLINE="${2:-0}"; TAKEOVER="${3:-$(( $(date +%s) + 900 ))}"
[ -n "$TS" ] || exit 2
MARK="/tmp/strata-util-window-in-progress"

log() { printf '%s watchdog ts=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TS" "$*"; }

healthy() {
  H=$(curl -fsS -m 5 http://127.0.0.1:8080/health 2>/dev/null || true)
  case "$H" in *'"loaded": true'*) return 0 ;; esac
  return 1
}

busy() {
  pgrep -f "/opt/ai/bin/[n]cu-exec.py" >/dev/null 2>&1 && return 0
  pgrep -f "[n]sys profile" >/dev/null 2>&1 && return 0
  pgrep -f "/usr/local/cuda-13.0/bin/[n]cu " >/dev/null 2>&1 && return 0
  pgrep -f "util-[c]andidate-install" >/dev/null 2>&1 && return 0
  return 1
}

attempts=0
miss=0
while :; do
  NOW=$(date +%s)
  [ -f "$MARK" ] || { log "marker gone, exit"; exit 0; }
  if healthy; then
    [ "$NOW" -ge "$DEADLINE" ] && { log "healthy at deadline"; exit 0; }
    miss=0
  elif busy; then
    miss=0
  else
    miss=$((miss + 1))
    if [ "$NOW" -ge "$DEADLINE" ] || { [ "$NOW" -ge "$TAKEOVER" ] && [ "$miss" -ge 2 ]; }; then
      attempts=$((attempts + 1))
      if [ "$attempts" -gt 3 ]; then log "give up after $attempts attempts"; exit 1; fi
      log "force restore attempt=$attempts now=$NOW deadline=$DEADLINE"
      bash /opt/ai/bin/util-restore.sh "$(cat /tmp/strata-util-snapshot-path 2>/dev/null || true)"
      log "restore rc=$?"
      miss=0
    fi
  fi
  sleep 15
done
```

### dump-runtime.py 约定

`dump-runtime.py --pattern <正则> --out <路径>` 从 `/proc` 抓 argv、env、cwd，输出 `0600`
JSON，形状与 `ncu-exec.py` 的输入一致。解析部分写成纯函数
`runtime_from(cmdline: bytes, environ: bytes, cwd: str) -> dict`，单测覆盖空 argv、cwd 落盘
与文件权限；`ncu-exec.py` 在 `execvpe` 前执行 `os.chdir(cwd)`。

## 附录 B：内核微优化（降级为可选，默认不做）

只有同时满足以下条件才启动 NCU：

1. 阶段 2–4 完成后，S1/S2 已达标，但 S3 或 S4 仍不达标；
2. Nsys 时间线显示解码窗口里 kernel 执行时间占比 >85%，而不是等待调度/预填；
3. 已定位到单个复现稳定的 kernel，且有对应 parity 测试。

届时再启用受控 NCU（`tools/server/ncu-exec.py` + 自愈窗口），一次只采集一个 kernel，
只改一个机制；内核候选同样要过本计划的最终验收门。
