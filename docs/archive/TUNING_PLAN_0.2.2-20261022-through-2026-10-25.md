# Strata 0.2.2 调参优化方案（2026-10-22）

> **状态：历史（2026-10-22 的计划）。** 里面的估算已被台账逐条验证或推翻（如 §21.2 的"思考 −64% ≈ 墙钟 −25%"、
> §7 的"输出不可复现"），**不要当现状**；结论看 [`EVIDENCE_LEDGER.md`](../EVIDENCE_LEDGER.md)。

依据：`strata-parameters-0.2.2-20261022.md`（本地 `D:\temp\atm\`，服务器 `/opt/ai/docs/`，179 flag + 67 配置键 +
724 环境变量）。本篇不再抄参数表，只回答三件事：**现在离默认值偏了多少、哪些还值得动、怎么测才算数**。

## 0. 先说结论

- 参数表本身可靠：抽查了 8 条（`--conversation-cache-park-gain` GEN:889、`--ple-inflight` 默认 256 GEN:811、
  `--mtp-window` GEN:2607、`--short-read` GEN:918、`--prefill auto:` 上限 GEN:623–624、`--lookup-chain` GEN:772、
  `--kv` 四值、`strata_tune` 四键 SRV:1405–1420），全部与源码一致。
- 它最有用的三点：① 参数分 6 层、优先级明确；② **29 个 flag 不在 `--help` 里**（`--vram-reserve-mib`、
  `--vram-reserve-later-mib`、`--pcie-frac`、`--mtp-window` 等都在用，却只能从源码确认）；③ 请求级
  `strata_tune` 支持 `pcie_frac` / `spec_min_p` / `prefill_pipe` / `aux_cpus` 四键——**这几项不用重启就能扫**。
- 这台机器性能侧的边际收益已经很小（历史记录：单请求天花板、两卡串行、`--pipeline-windows` −41% 等）。
  **本轮真正值钱的是“内存稳定性 + 长上下文草稿质量”，不是再挤 2% 解码。** 预估合计 decode +2~5%、
  TTFT −10~20%、缓存抖动显著减少；单流要回到 82+ 只能靠 TP 移植，不在本轮。

## 0.5 实况核对：原方案里被剔除的项（2026-10-22 复核）

参数表是通用的，套到这台机器上必须先过一遍真实约束。以下 6 项**已剔除**，不要下次又捡回来：

| 原计划 | 剔除原因（实况证据） |
|---|---|
| `--prefill-head-alias` | 官方写明**仅单 GPU**（`docs/DETAILS.md:2037`）；本机是两卡层切分，参数不适用 |
| `--mtp-window 65536` | 草稿层 KV 也在显存里，而两卡加载后只剩 **689 / 1039 MiB** 空闲；32K 是上游默认，改大先要显存 |
| `STRATA_PREFILL_AUTO_MAX=16384` | 真实请求每轮只新增约 **2.5K token**（prompt 复用 93.3%），8192 块已经一次装下，放大块只会抢显存 |
| `--ple-io mmap` / `ram` | 可用内存最低到过 **4.1 GB**；`ram` 历史已否决，`mmap` 无证据表明 PLE 是 3.2 s TTFT 的主因 |
| `--conversation-cache-park-gain 1024` | 文档写明代价是恢复时**多读最多 N 个 token**；TTFT 已 3.2 s，1K 重读可能抵消收益。改为不动（默认 0） |
| `--spec` / `--suffix-draft` / `--lookup-chain` 作为主力优化 | **两路会话同时在线时请求都在流水槽里，槽内按设计不跑 MTP 草稿**（引擎日志：`batch (pipelined, 2 groups of 2): … rows in …`，无 `MTP proposals accepted`）。这些只影响单流 |

> 表里 `--conversation-cache-min-tokens` / `-park-gain` 的语义我按源码与 `DETAILS.md:1215/1223` 核过：
> 前者在 `make_room()` **之前**判断，跳过的 park 不会触发驱逐；后者会让恢复多读 token——这正是上面剔除它的理由。

## 1. 当前基线（0.2.2 / 引擎 0.1.47，`parallel 4` + `--batch-groups 2`）

| 口径 | 实测 |
|---|---|
| 单流 decode（300 tok，3 次中位） | **79.1 tok/s** |
| 2 路并发 | 43–45 tok/s/路，合计 **80** |
| 4 路并发 | 28–31 tok/s/路，合计 **103–108** |
| 真实 agent 负载（2 路 × 80K 上下文） | 合计 **70–75 tok/s**（每路 35–37），TTFT 中位 **3.25 s** |
| prompt 复用 / 草稿接受率 | 93.3% / **66.9%** |
| 显存 | 21.5 / 21.1 GB used，空闲 689 / 1039 MiB |
| 内存 | engine RSS 53–58 GB；MemAvailable 在 **4.1–18 GB** 之间摆；会话缓存 1.3–14 GB、evictions 68–81 |

## 2. 现在离默认值偏了多少（只列与本轮有关的）

| 参数 | 默认 | 当前 | 备注 |
|---|---|---|---|
| `--max-context` | 4096 | **256000** | 4 槽各按此占 pinned RAM，是内存紧的根因 |
| `--batch` / `--batch-groups` | 0 / 1 | **4 / 2** | `parallel 4` + 两组流水；batch-mtp 在流水组下不跑，已关 |
| `--spec` / `--spec-min-p` | 0 / 0.0 | **8 / 0.5** | 草稿接受 66.9%，有优化余地 |
| `--suffix-draft` / `--mtp-max-t` | 3 / 0(=spec) | **4 / 4** | 重复文本/代码场景 |
| `--mtp-window` | 32768 | 32768（默认） | 会话 80–90K，草稿层只看最后 32K |
| `--lookup-chain` | 0 | 0（默认） | 未开 |
| `--prompt-cache` / `-every` | 6 / 16384 | **3 / 4096** | 检查点约 118 MB/份 |
| `--conversation-cache-mib` / `-slots` | 0 / 4 | **32768 / 24** | 触发 RAM 准入、park 了又被整体驱逐 |
| `--conversation-cache-min-tokens` / `-park-gain` | 0 / 0 | 0 / 0（默认） | 短对话也停、没推进也停 |
| `--kv` / `--kv-resident` | fp16 / 0 | **int8 / 32768** | 已是最省显存的组合 |
| `--prefill` | 0 | **auto**（块 ≤ 8192） | 冷 prompt 80K 要 10 块 |
| `--pcie-frac` | 模型默认 | **0.31** | VRAM 命中 99.6%，基本无关 |
| `--vram-reserve-mib` / `-later` | 700 / 同首卡 | **960 / 1435** | 空闲仅 689/1039 MiB，预留偏大 |
| `--resident-experts` + 12 个 `STRATA_*` | 关 | 开 | 低内存预设 + 本机历史调优 |

## 3. 值得动的参数（按性价比排序）

### 轨道 A：不用重启（请求级 `strata_tune`）——**已跑完，结论见下**

| 实验 | 扫的值 | 结论 | 是否落地 |
|---|---|---|---|
| A1 `spec_min_p` | 0.3 / 0.5 / 0.7 / 0.8 / 0.9 | **0.7 最优**：暖机 89.7 vs 0.5 的 87.4（3 轮全胜）；重启后暖机 4 次 84.8 vs 83.2（+1.9%）；0.8/0.9 明显回落；0.3 最差 | ✅ 落成 `--spec-min-p 0.7` |
| A2 `prefill_pipe` | 0 / 1 / 1500，冷 13.2K prompt ×6 | **1 最优**：1470 → 1552 tok/s（+5.6%），TTFT 9016 → 8541 ms，6/6 次赢；1500 介于中间 | ✅ 落成 `--prefill-pipe 1` |
| A3 `aux_cpus` | 0 / 1，冷 13.2K ×3 | 1480 vs 1481 tok/s，**无差异** | ❌ 不加 |
| A4 `pcie_frac` | 0.0 / 0.31 / 0.55 | 87.8 / 87.2 / 86.7 tok/s，**无差异**（与 99.6–99.7% VRAM 命中一致） | ❌ 保持 0.31 不动 |

> 方法：请求 body 里加 `"strata_tune": {...}`，同 boot 内交错发、每值 3–6 次取中位。
> **注意绝对值漂移**：同一组参数在缓存冷/暖之间可以从 80 跑到 90 tok/s，跨时间不能直接比，只能看同一交错块内的相对差。

> 源码路径：请求 body 的额外键会原样进入 `sampling`（SRV:6564 → `svc.run(..., req, ...)`），
> `sampling_keys` 再把它拼成 `GEN` 行的 `spec_min_p=` / `pipe=` / `aux_cpus=`。建议先用 2-token 请求验证一次行为。

### 轨道 B：内存稳定性（**已执行**，2026-10-22 12:00）

| 改动 | 从 → 到 | 作用 |
|---|---|---|
| `--conversation-cache-min-tokens` | 0 → **4096** | 短请求不再把长对话挤掉；实测日志 `skip parking (62 tokens, below the 4096 minimum)` |
| `--conversation-cache-mib` | 32768 → **12288** | 与 62 GB 内存和 4 槽匹配，避免一次想停 14–20 GB 再被 RAM 准入整体驱逐 |
| `--conversation-cache-slots` | 24 → **12** | 同上，按“长对话 ≈1.4–1.5 GB/份”算，12 份已够 |
| ~~`--conversation-cache-park-gain`~~ | 0 → 0（**不改**） | 见 §0.5：会让恢复多读 token |

验收看：`evictions` 增速、`skip parking` 次数、MemAvailable 最低值、TTFT p90。**这条不追速度，追的是不再抖。**

### 轨道 C：长上下文草稿质量（**只影响单流**，重启，逐项 A/B）

> 前提已核实：两路会话同时在线时，请求在流水槽里，**槽内不跑 MTP 草稿**。所以这一组只在“只剩一个会话”时有效，
> 不要用它解释并发场景的数字。

| 实验 | 从 → 到 | 假设 | 风险 |
|---|---|---|---|
| C1 `--lookup-chain 2` | 0 → **2** | 代码/重复文本场景多续 1–2 个 token，窗口最多 8 | 误接会浪费 verify 行 |
| C2 `--suffix-draft` | 4 → **3** | 与 C1 配合，避免窗口过宽 | 重复文本收益略降 |
| C3 `--spec` | 8 → **6** | 窗口更短、浪费行更少，接受率可能更高 | 每窗最多 1+4 token |
| ~~`--mtp-window`~~ | 32768 | **不改**：显存只剩 689/1039 MiB，草稿层 KV 也要显存 | — |

验收：decode 中位、`drafts_accepted/offered`、`rows/window`；用 `--window-hashes` 或 `--greedy` 保证答案可比。

### 轨道 D：冷 prompt / TTFT（**本轮暂缓**）

| 原候选 | 处置 | 原因 |
|---|---|---|
| `STRATA_PREFILL_AUTO_MAX 16384` | 暂缓 | 每轮只新增 ~2.5K token，8192 块已够 |
| `--prefill-head-alias` | **不可用** | 官方写明仅单 GPU |
| `--ple-io mmap` | 暂缓 | 需先证明 PLE 是 TTFT 主因；本机内存余量不足 |
| `--prefill-pipe` | 放进轨道 A（请求级可测） | 层切分冷读时按 b/a 分块，`strata_tune.prefill_pipe` 免重启 |

### 轨道 E：本轮不做

- **TP 补丁移植**：单流 +3%，但要重合并 + 在 batch 窗口下做逐 token 等价验证，是独立项目。
- **`--kv k8v4`**：KV 再省 23%，能换更多槽/更长上下文，但改数值质量，需先做质量评测（不在性能窗口里做）。
- **8 槽**：256K 下 8 槽要 ~34 GB pinned RAM，本机 62 GB 不够。

## 4. 明确不要再碰（历史实测已否决）

| 参数 | 结论 |
|---|---|
| `--pipeline-windows` | 两卡 −41%，不要再开 |
| `--pool-workers 27` | −5% |
| `--ple-io ram` | PLE 不是生成期瓶颈，且本机无 RAM 余量 |
| `--peer-device` | prefill −41% |
| `--kv-grow` | 仅单卡支持，层切分用不了 |
| `--batch-mtp` + 流水组 | 流水组按设计不跑槽内草稿；串行组已实测更慢（78–82 vs 103–108） |

## 5. A/B 方法（这台机器的噪声要尊重）

1. **能同 boot 交错就同 boot**（轨道 A）：每值 3 次、中位、交错发，避免热漂移。
2. 需要重启的：每臂重启 1 次、等 ready、warmup 8–12 请求、再测 3 次中位；单流数字 boot 间波动 **±2%**，
   **< 3% 的差异不算赢**。
3. 每次只改一组参数，配置另存 `strata-022.<arm>.json`；回滚就是 `cp` + restart。
4. 记录：decode 中位、接受率、rows/window、TTFT p50/p90、prefill tok/s、空闲显存、MemAvailable、
   `evictions`/`skip parking`。判据写进当次记录，避免事后解释。
5. 全程 `temperature 0`，固定 prompt；涉及答案可能变化的（C 组）加 `--window-hashes` 或前后各跑一次同题对比。

## 6. 执行状态与顺序

| 批次 | 内容 | 状态 |
|---|---|---|
| 第 1 批 | B 内存三项（`min-tokens 4096` / `mib 12288` / `slots 12`） | **✅ 03:55 UTC 已执行并重启** |
| 第 2 批 | A1–A4 请求级扫描 | **✅ 04:00–04:15 UTC 已跑完**；A1/A2 落地，A3/A4 否定 |
| 第 3 批 | 把 A1/A2 写入配置（`--spec-min-p 0.7`、`--prefill-pipe 1`） | **✅ 04:08 UTC 重启生效并复测** |
| 第 4 批 | C1–C3 逐项 A/B（只对单流） | 待定，每项一次重启 |
| 第 5 批 | 视真实流量决定是否再碰 prefill / 缓存预算 | 暂缓 |

### 第 1 批的执行证据

- 引擎命令行已生效：`--conversation-cache-mib 12288 --conversation-cache-slots 12 --conversation-cache-min-tokens 4096`。
- 服务端遥测一致：`conversation_cache.budget_mib=12288, slots=12, parked=0, evictions=0`（重启后清零）。
- 行为验证：发一个 62 token 的小请求，日志出现
  `conversation cache: skip parking (62 tokens, below the 4096 minimum; parked=0)`，且没有触发 `make_room()` 驱逐。
- 回滚：`/opt/ai/src/strata-0.2.2/strata-022.cache32.json`（改前原样），`cp` 回去重启即可。
- **还没验证到的**：12 份长对话（每份 ~1.4 GB）同时 parked 时的预算封顶行为——需要 9 个以上 80K 会话才会压到 12 GB，
  当前没有真实流量，暂不做合成压测；下次真实高峰看 `evictions` 与 `parked` 即可。

### 第 2/3 批的执行证据（A 组原始数据）

**A1 `spec_min_p`（300 token 贪心，同 boot 交错）**

| 值 | 暖机后 decode（中位） | 接受率 | 每请求提议数 |
|---|---:|---:|---:|
| 0.3 | 76.0 | 53% | ~334 |
| 0.5 | 79.8 → 87.4 | 64–72% | ~265 |
| **0.7** | **84.2 → 89.7** | **81–87%** | ~203 |
| 0.8 | 86.9 | 93% | ~177 |
| 0.9 | 83.0 | 97% | ~155 |

峰值 0.7：第一轮 3/3 次赢 0.5（84.2>79.0、90.1>84.1、81.3>79.8）；重启后 24 次暖机 + 4 轮交错，
0.7 = 84.8 vs 0.5 = 83.2（+1.9%，4 次里 3 次更高）。0.8/0.9 提议太少、0.9 掉到 83.0。

**A2 `prefill_pipe`（冷 13,252-token prompt，6 次）**

| 值 | prompt tok/s | prompt ms |
|---|---:|---:|
| 0（关） | 1470 | 9016 |
| **1** | **1552** | **8541** |
| 1500 | 1531（3 次） | 8656 |

6/6 次 `1` 都更快（+5.6%，TTFT −475 ms）；落地后复测冷 13K 为 1571–1596 tok/s、8.30–8.43 s，确认生效。

**A3/A4（否定验证）**：`aux_cpus` 0/1 = 1480/1481 tok/s；`pcie_frac` 0.0/0.31/0.55 = 87.8/87.2/86.7 tok/s。
两项都不值得加，`pcie_frac` 保持 0.31。

**最终生效配置**（`strata-022.json`，54 args）：
`--spec-min-p 0.7`、`--prefill-pipe 1`、`--conversation-cache-mib 12288`、`--conversation-cache-slots 12`、
`--conversation-cache-min-tokens 4096`，其余（`parallel 4`、`--batch-groups 2`、256K、int8 KV、4 槽）不变。
回滚点：`strata-022.trackA.json`（A1/A2 之前）、`strata-022.cache32.json`（缓存改动之前）。

重启代价：两次重启各断开当时的会话；客户端都没有自动重连，本次全部是合成请求。

---

## 7. 2026-10-23 追加：Turing 内核系列（已采纳）与 KV 预取（已否决）

> 本节由本次实验回写，代替"轨道 E·本轮不做"中的一条空白：`STRATA_SM75_PERF` 此前从未在本机试过。

### 7.1 结论

| 项 | 结果 | 处置 |
|---|---|---|
| `STRATA_SM75_PERF=1`（整套 sm_75 逐位内核，#2069–#2072/#2081/#2091–#2093/#2145–#2146/#2220–#2223/#1821） | 84K 冷 prefill **2375.6/2381.3 → 2552.0/2566.5/2556.9 tok/s（+7.2~7.6%）**；冷 prefill 43–50K **2108/2094 → 2236/2258（+6.0~7.8%）**；4 路并发 **111.3/112.5 → 119.7/117.1（+4.1~7.5%）**；decode 67.9–78.0 vs 67.8–71.2（方向向上但落在离散带内，不主张） | **✅ 已写入 `strata-022.json` 的 `env`** |
| `STRATA_KV_PREFETCH=1`（叠加在上项之上） | 84K prefill +0.6%、冷 prefill +1.0%、4 路 −2.2% | **❌ 无增益，已撤出配置** |

判据沿用本文档 §5：同 boot A-B-A、每臂重启、中位、**<3% 不算赢**。

### 7.2 执行细节

- 每臂重启一次，等 `/health` 的 `"loaded": true`，重启耗时 **132–177 s**（含专家缓存 19.8 s 预填）。
- 工具（新增，已放在 `/opt/ai/bin`）：
  - `run-arm.sh <label>`：一次跑完 `bench-strata.py`（单请求 + 4 并发，3 轮）+ `/tmp/parity.py`（84K prompt 后 512 token）+ `cold-prefill.py`（冷 prefill），结果落 `/opt/ai/logs/arm-<label>.log`。
  - `cold-prefill.py <label> [tokens] [reps]`：解析引擎自报的 `read in M ms (X tok/s)`。注意：填充词按字符数估算偏大，目标 13252 实际产生 **43–50K token**（已按实际值记录）。
  - `apply-arm.sh <set|unset> <KEY> [VAL]`、`set-env02.py`：改 `env` 键 + 备份 + 重启 + 等 ready + 打印进程实际环境变量。
- 回滚点：`/opt/ai/backups/20261023/strata-022.json.<HHMMSS>`（本次 5 份），或把 `STRATA_SM75_PERF` 改成 `-`。
- 启动日志无新增 warning；`is off beside` 计数 0（并发读 gate 正常）。

### 7.3 一个重要的方法学发现

**本机生产配置下的输出不可复现**：同一 prompt、同一臂内连跑三次，`text_sha256` 三个值、draft 数也不同
（75dc41ba / 7e2fe7b8 / …）。本文档 §5 与交接文档已说明"默认参数下 on/off 的 sha 本来就不同"，本次进一步确认
**同臂内也不可复现**，因此 `text_sha256` 不能作为任何逐位门。逐位必须按上游要求另开配置
（`STRATA_IQ_MT_MIN=1`、`--pcie-frac 0`、固定 adaptive、`--no-prefill-borrow`），**该项仍未做**。

### 7.4 为什么 KV 预取在本机不生效（未定论）

上游在双 3080 20 GB 上测得 32K +5.3%、64K +4.1%。本机同拓扑却只有 +0.6%。候选解释（**均未验证**）：
层切分让每卡只负责一半层、每层的 KV 上传量减半；前 32K 的 KV 已在 `--kv-resident 32768` 里；S1 已把读路径移出
decode 线程。记录为实测，不做归因。

### 7.5 下一步（本节给出的顺序）

1. **PLE**：本文档 §0.5 与 §D 把 `--ple-io` 判为暂缓，理由是"需先证明 PLE 是 TTFT 主因"。`strata-agent-latency-20261016.md` §12 已经证明：全新 8.2K prompt 的 6.9–7.2 s 里有 5.98–6.58 s 在宿主 PLE 桶。**该前提已成立**，可以重启这条线；但 `--ple-io ram` 仍否决（内存），`mmap` 需先解释日志里 `PLE 33xxx ms` 到底测的是什么。
2. **逐位门（T2）**：按上游配置补测一次 `STRATA_SM75_PERF` 的 on/off，否则"逐位"只能是借来的说法。
3. **`STRATA_PF_FUSED_SM75=1`**：会改数值，需 KL 质量门，排在上面的逐位之后。

### 7.6 2026-10-23 补测：逐位门（T2）已跑

- **配置**（按 `docs/BATCHING.md:403-408` 的上游要求）：`STRATA_IQ_MT_MIN=1`、`--pcie-frac 0`、
  `--adapt-every 1000000`、`--no-prefill-borrow`，另把 `parallel` 临时改成 1（单槽）。
  注意：**`--spec` 不能关**，native pack 要求 `--spec T (T>=2)`。
- **结果**：`STRATA_SM75_PERF=0` 与 `=1` 产出**同一段 512 token**（`aac0a04d109391fe`）。
  开臂：预热后连跑 4 次一致；再次重启后 3 次（含冷启动）也一致。共 9/10 一致。
- **一次例外**：开臂某次重启后的**第一条**请求得到 `00281f843ecd3543`，换一次重启未复现。
  记为"冷启首请求"差异，未归因。
- **结论**：预热后 Turing 系列在本机逐位成立；生产配置（4 槽、`--pcie-frac 0.31`、自适应）本身不可复现，
  所以逐位只能在 T2 配置下谈。
- 工具：`/opt/ai/bin/prepare-t2.py`、`use-config.sh`、`t2-run.sh`、`restore-prod.sh`；生产配置快照
  `strata-022.prod.json`（恢复后已核对：`parallel 4`、`--pcie-frac 0.31`、`--batch-groups 2`、SM75 开）。

### 7.7 2026-10-23 更正：`PLE` 字段是进程累计值，不是单请求耗时

读源码（`src/prefill/prefill.cpp`、`src/ngram/ple_reader.cpp`）后确认：

- 时间行里的 `PLE %.0f ms` 打印的是 `stats_.ms_ple`；它只在 `Prefill::reset()` 清零，而**服务路径从不调用**
  `reset()`（只有 `--prefill-head-alias` 和显存不足重试会调）。`Prefill` 是 `GpuStage` 的成员，与引擎同寿命。
- 所以日志里 `wall 421 ms` 旁边出现 `PLE 33674 ms` 不是矛盾：前者是本次请求，后者是**引擎启动以来的累计**。
  本机那些 `PLE 33xxx ms` 不能读成单次请求开销。
- `ms_ple` 计入三处：等 SSD gather + 上传（`prefill.cpp:2803-2829`）、批量 PLE 块（3121）、逐 token 回退块（3135）。

补充：`strata-agent-latency-20261016.md` 的 5.98–6.58 s 不受影响——那是在**一次性引擎运行**上测的，
进程从空开始，累计值即该次请求值。

**对后续的影响**：单请求 PLE 成本要取两次打印值的差。这是 W2.2 的第一步（冷 8K / 冷 32K，关闭并发读）。
源码细节见仓库 `docs/SERVER_PLE.md`。

### 7.8 2026-10-23 实测：PLE 在当前配置下不是瓶颈（W2.2 结论）

方法：`PLE` 是累计值，取**本次引擎启动**的日志，用"请求前最大值 → 请求后最大值"作差。两次测量的坑：

- **层切分每 stage 一行**，后一行可能是 PLE 为 0 的那个 stage，取"最后一行"会恒为 0；
- **合成填充词无效**：n-gram 重复，100 万行缓存几乎全命中，实测每次只加 ~1 ms。改用 `/opt/ai/docs` 的真实正文，
  且每次换不同片段。

| prompt | read | PLE（重启后第一条） | PLE（之后） |
|---|---|---|---|
| 6.2–6.7K token 真实正文 | 4.9–6.6 s（1,179–1,255 tok/s） | **1,015 ms** | 0–1 ms |
| 24.1K token 真实正文 | 12.2–12.4 s（1,935–1,967 tok/s） | 203 ms | 0–62 ms |

结论：**旧结论（8.2K 提示 6.9–7.2 s 里 PLE 占 5.98–6.58 s）在 0.2.2 + 并发读下不再成立**——gather 在 prompt
自己的读线程上跑，与 GPU 并行，基本被完全遮住；只剩**引擎启动后第一条**约 1 s（行缓存与页缓存冷）。

决定：**不做 PLE 代码改动**。`--ple-io ram` 内存不够；缩小读单元需要改 pack；"提前发起"对第 2..N 个 chunk 已做。
W2.3/W2.4 以"前提未通过实测"关闭，只有当真实负载出现可测代价时再开。

方向修正：数字指向的是**小 prompt 每 token 的固定成本**——同一段文本 6K 时 1,179–1,255 tok/s、24K 时 1,935–1,967
tok/s。下一个候选是 `STRATA_PF_FUSED_SM75`（sm_75 的 int8 张量核 prompt 专家），它直接作用在这段读上，但会改数值，
需要 KL 质量门。

### 7.9 2026-10-23 补测：127K 冷读（最终构建，并发读开/关）

工具：`/tmp/piped_scenario_127k.py`（既有脚本，未改）：一条流在解码，同时发一个 126,050 token 的冷 prompt，
统计冷读耗时、读期间流的速度、最长停顿。

| 臂 | 冷读 | 读期间流 | 读期间产出 | 最长停顿 | >2s 的 gap |
|---|---|---|---|---|---|
| 并发读开（生产） | **58.2 / 58.5 s** | **19.7 / 20.3 tok/s** | 1,148 / 1,190 tok | **0.9 s** | 0% |
| 并发读关 | 49.0 / 49.6 s | 0.3 / 0.4 tok/s | 16 / 21 tok | 48.7 / 49.3 s | 5–6% |

- 结论与 84K 那次同向：开并发读要多花 **9.3–8.9 s（约 19%）**的读时间，换来旁边那条流从"停摆 49 s"变成
  "持续 20 tok/s、最长停 0.9 s"。对服务器场景这笔交换是值得的。
- 每臂一次重启、2 个 rep、同一 boot；冷读耗时在两臂内都很稳（±0.6 s）。
- 本次在**最终构建**（0.1.47 + S1）上补齐了交接文档里"127K 只在旧构建跑过"的空缺。

### 7.10 2026-10-23 补测：长读中途取消（T7）

测试：`/opt/ai/bin/t7-cancel.py`——发一个 55,035 token 的流式冷 prompt，客户端读 10 s 后**直接断开**，随后立刻发一个短请求，
再查日志与 health。三次结果一致：

| 次 | 客户端断开 | 引擎停止处 | 停止时刻 | 随后短请求 | health |
|---|---|---|---|---|---|
| 1 | 10.3 s | 39,680 / 55,035 | 16.66 s | 6.7 s 完成 | 200 |
| 2 | 10.2 s | 39,680 / 55,035 | 16.53 s | 6.6 s 完成 | 200 |
| 3 | 10.2 s | 39,680 / 55,035 | 16.61 s | 6.7 s 完成 | 200 |

- 取消按设计工作：读在**当前 chunk 边界**停下，没有跑完整个 prompt；日志记 `(cancelled)`。
- 代价是**取消延迟约 6.4 s**（10.2 s 断连 → 16.6 s 停下），这段时间里排队的下一个请求要等；
  停稳之后一切正常。
- 三次取消都没有产生 `keeps its conversation` 行，未观察到会话缓存泄漏。
- 判据（T7）：请求结束、无残留、引擎继续服务——全部满足。

### 7.11 2026-10-23 结论：`STRATA_PF_FUSED_SM75` 对本机模型不适用（W3 关闭）

读源码（`src/prefill/moe_fused_iq.cu`）确认：

- 第 1300 行：非 sm_80 卡只有 `STRATA_PF_FUSED_SM75=1` 才继续；第 1302–1304 行在 Turing 上**只**装配
  `T_Q2_0` 的 gate/up 与 down（注释：IQ 码本格式在 4 warp/行下放不进 64 KiB 共享内存）。
- 第 1425 行：`if (d.t75 && (gu_type != T_Q2_0 || d_type != T_Q2_0)) return false;` —— Turing 上除 Q2_0 之外的
  任何格式一律回退 MMQ。
- 本机是 `sc117_iq3_xxs` 原生 pack，因此该开关**根本到不了**这段代码；要吃到它得换 Q2_0 模型，属于换模型，不在范围内。

所以 W3.1（KL 质量门）与 W3.2（速度）**以"不适用"关闭**，没有跑测——这是读代码得出的判断，按规矩标为未验证的静态分析：
若要证伪，只需在有 Q2_0 pack 的机器上跑一次 `quant_quality.py` 的 REF/RUN 对比。

附带结论：预填阶段的专家 GEMM（`gemm gate/up` 占 18–26%）在这个 pack 上没有可用的融合路径，性能侧的可选项到此为止；
剩下的是 W4（上游报告、TP 决定）与 W5.2（配置变更后的内存稳定性）。

### 7.12 2026-10-23 soak：47 分钟干净数据，被一次外部重启截断

**结论先行：服务本身没有崩。** 06:49:23 UTC，`apt-daily-upgrade`（unattended-upgrades，06:48:39 启动）触发 needrestart，
把 `strata.service` 连同 udisks2/fwupd/packagekit/rsyslog 一起重启了。证据：journal 里是干净的
`Stopping … Deactivated successfully … Started`，`NRestarts=0`；随后 2.5 分钟是引擎重新加载，soak 客户端此时
疯狂重试（连接被拒），刷出 70 万行错误——这是 soak 脚本没有退避的缺陷，不是服务错误。

| 项 | 数据 |
|---|---|
| 有效区间 | 06:02:23 → 06:49:23（47 分钟），**3,995 个请求，0 错误** |
| RSS | 38.655 → 38.803 GiB（+148 MB / 39 min，0.4%） |
| MemAvailable | 23.28–23.63 GiB（历史最低曾到 4.1 GiB） |
| 会话缓存 | 1,120 parks / 1,119 restores / **evictions 0**，bytes 稳定在 589.8 MB，未增长 |
| GPU0 | 182 → 272 W，61 → 82 °C（首/末三分位均温 77.1 → 77.8 °C） |
| GPU1 | 200 → 265 W，53 → 65 °C（62.6 → 61.4 °C） |

判读：一小时目标没跑满（47/60 分钟），但 47 分钟里没有崩溃、没有漂移、显存与内存平坦；会话缓存零驱逐，说明
`12288/12/min-tokens 4096` 这套设置在真实压力下站得住（W5.2）。**GPU0 的 82 °C 是唯一需要盯的点**（接近
throttle 区间），1 小时的数据不足以判断是否热漂移。

**已修**：`/etc/needrestart/conf.d/50-strata.conf` 把 `strata.service` 加入 `override_rc`，unattended-upgrades
不再顺带重启它。今后再跑 soak 可以跑满 60 分钟。另记一笔：soak 脚本在服务不可用时应立即退避，而不是空转重试。

### 7.13 2026-10-23 实测：`--prefill` 块大小的权衡（已回退到 auto）

6.5K token 提示只有一个 chunk，两级流水**结构上无法重叠**（`STRATA_PIPED_READ_PIECE` 只切整 chunk）。实测两种块大小：

| 提示 | `--prefill auto`（8192） | `--prefill 4096` | 变化 |
|---|---|---|---|
| ~6.5K 冷读（客户端墙钟） | 5.61 / 5.86 s | 5.24 / 5.35 s | **+8%** |
| ~22.7K 冷读 | ~1,950 tok/s（12.2–12.4 s） | 1,830 tok/s（12.34–12.49 s） | **−6%** |

小提示变快、长提示变慢，是流水重叠与每块开销的取舍。当前真实负载每轮只新增约 2.5K token（记录 §1），但缺少
真实流量分布，无法判定该偏哪边；且 chunk 与 ring 尺寸绑定（`--prefill-pipe` 下 ring 96 槽按 8192 规划），
按请求改 chunk 不是免费的。

**决定：保持 `--prefill auto`**，把这条记成"可翻转的已测选项"：若流量证实以 <8K 提示为主，改 4096 可让首 token 快约 8%。
`STRATA_PREFILL_TIMING=1` 已在测完后移除，生产 env 恢复为 14 个键。
