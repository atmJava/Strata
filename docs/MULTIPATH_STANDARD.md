# 多路服务标准（MULTIPATH_STANDARD）· v0.1 草案 · 2026-10-24

> 配套：[`RULES.md`](RULES.md)（怎么干活）、[`HANDOVER.md`](HANDOVER.md)（状态与决策）、
> [`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md)（证据格式）、[`BATCHING.md`](BATCHING.md)（引擎的批机制）、
> [`MULTIPATH_PLAN.md`](MULTIPATH_PLAN.md)（做什么、按什么顺序）。
>
> 本文回答四件事：**多路就绪怎么定义（§1）、怎么定容（§2）、怎么验收（§3–§5）、怎么记录与命名（§6–§7）**。
> 标准本身可以改，改完在文末写版本行（`RULES.md` 的写法）。

## 1. 术语与"多路就绪"

- **流 / 路（stream / path）**：一个客户端请求的生成过程；"N 路" = 同时在飞的请求数。
- **槽（slot）**：引擎批窗口里的行位，由 `--batch N` 决定；槽数 = 服务端准入上限（L-022）。
- **组（group）**：`--batch-groups` 的流水组；生产（服务器配置）是 2 组 × 2 槽（引擎在 2 阶段切分上默认
  `--batch-groups auto` = 一阶段一组，`BATCHING.md:91-92`）。
- **满槽**：N = `--batch`；**空槽**：N < `--batch`。
- **热读 / 冷读**：前缀命中会话或 prompt 缓存 / 从未见过（`cold-prefill.py` 专门造冷读）。
- **TTFT**：`first_token_s`，**含排队 + 读 + 首 token**（L-021；不是引擎 prefill）。
- **邻居停顿**：某条流两次收到 token 之间的最长间隔（客户端侧 SSE 量）。

**多路就绪**（本标准判定的对象）：N 路同时解码时，四条**同时**成立：

1. **准入**：新请求的**首 token 与第二 token 都在 ≤2 s** 内（首 token 之后静默 31 s 不算准入——暂存读能提前给首 token，但第二 token 仍要等槽，L-049）。满槽时的实测：A 上首/第二 token 都是 46 s；B（暂存读）首 token 1.7 s、第二 token 32.9 s。
2. **共存**：邻居流最长停顿 **≤1 s**（gate ON 已有 0.70 s 的量级，L-019）。
3. **吞吐**：N 路聚合 decode **≥ 基线 −3%**（不许为了准入牺牲总吞吐）。
4. **正确与健康**：质量门全过、无截断、空答案率 0、`/health` 绿、显存余量 ≥ 700 MiB。

四条缺一条就不算"多路就绪"；只满足 1–3 而质量掉，按否决处理。

## 2. 定容（sizing）

**预算式**（每次定容先把式子写出来，再填数）：

```
VRAM 每卡 ≈ 权重（层切分后） + 槽会话（每槽 × 槽数） + KV 驻留（--kv-resident） + 预留（--vram-reserve-mib）
约束：每卡 free ≥ 700 MiB（L-001 的 warn 线；#2065 说 <~500 MiB 会挂起）
启动门（F6）：`N × CTX ≤ GPU KV tokens`（N=max_num_seqs，CTX=max_model_len，照 fork `launcher.sh:176-205`）
per-step 预算参考：2048（fork 所有 shipped profile 都是这个值，`launcher.sh:6035`）
RAM 侧：conversation-cache-mib + 专家包驻留 + 流式 KV（开 --kv-resident 时每槽 128K ≈1.6 GB）+ 系统占用 ≤ 可用量
```

**规则**：

1. **槽会话成本要实测**，不抄别处。上游 0.56 GiB/槽是 RTX 5070 的数（`BATCHING.md:145`；§435 是"Measured"
   标题），本机没量过——W4 的第一件事就是在 2/3/4 槽下量本机的槽会话占用与 free 余量。
2. **KV 驻留的语义**：`--kv-resident N` 是**每个 QSA 层留在显存里的 cell 数**（最小 20480），其余 KV 在
   pinned RAM、上下文 ≤ N 时不流式（`generate.cpp:832-836`）——**不是"每流 KV 上限"**。定容要按
   "N 路 × 上下文"算总 KV，再对照 `--kv-resident` 的 cells 与 pinned RAM 预算；128K 每槽约 1.6 GB 的
   流式 KV 要算进 RAM（`BATCHING.md:145-147`，其他机型数字，本机需复量）。
3. **专家缓存不因多路被挤空**：`--expert-cache auto` 的槽数会随槽会话变化；命中率用
   `passive_windows.py` 读服务日志（别读 CSV 那一列，L-012）。#3849 说"`--batch` 使命中 99%→86%"
   是 2×3060 的上游报告，**本机未验**——W4 必须重验，不引用。
4. **组拓扑不动**：`--layer-split 24` 已证最优（L-040：23 是 −13%，25 是 −0.5%）；
   `--batch-groups 2` 与它绑定，不在定容里改。
5. **预设命名**（对齐 fork 的 `4x220K` 习惯，但 N 是我们自己的槽数上限）：

   ```
   strata-022.mp-<N>x<CTX>.json      例：strata-022.mp-4x256K.json
   N   = 路数（≤ --batch）           CTX = 上下文（K 为单位，256K→256K）
   ```

   只在矩阵全绿后落盘；预设进 git，服务器上留副本，`use-config.sh` 装载。

6. **定容验收矩阵**（每组预设都要跑）：

   | 路数 \ 读 | 热读 | 冷读 |
   |---|---|---|
   | 1 | 单流基线 | 冷 prefill |
   | 2 | 聚合 + TTFT | 邻居停顿 |
   | 3 | 聚合 + TTFT | 邻居停顿 |
   | 4（满槽） | 聚合 + TTFT | **准入 ≤2 s + 邻居停顿** |

## 3. 验收门

| 门 | 内容 | 通过线 | 工具 |
|---|---|---|---|
| G0 护栏 | `/health`、显存、温度、NVLink crc | 全绿；`free ≥ 700 MiB` | `/health`、`check-session-warnings.sh`、`metrics-peek.py` |
| G1 正确 | 逐位门（配置臂：固定题贪心输出一致；代码臂：`parity_window.py`） | 过 | `parity_window.py`、`quality_eval.py` |
| G2 性能 | 1/2/3/4 路 × 冷/热读 | 见 §1 四条 | `bench-strata.py`、`piped_scenario.py`、`cold-prefill.py` |
| G3 任务级 | agent 工具循环 | 墙钟中位不劣、空答案率 0、畸形调用 0 | `agent_budget_ab.py` |
| G4 API 契约 | 抽样 API 一致性（非流式/流式/usage/400/401） | 全过 | `tools/server/tests/test_mp_api.py`（T1） |

**判决词**只有四种，和台账一致：**采纳 / 否决 / 未定 / 参考**。

- **采纳**：≥3% 且两对独立 A-B-A 同向；或满足 §1 四条硬门槛。
- **否决**：实测反向且可复现；或过不了质量门。
- **未定**：<3%、方向不一致、样本不足、被别的流污染——**未定不许写成"接近采纳"**。
- **参考**：只读/背景数字。

## 4. 测量协议

1. **同 boot** A-B-A 或交错；每臂 ≥3 次；报中位 + sd；**<3% 不算结论**。
2. **跨 boot** 只用于趋势；小读/首字按 ~7% 看（L-031）；4 路跨 boot 漂移曾到 9%（L-040）——**别用单次跨 boot 数下结论**。
3. **不自污染**：测量期间本会话不生成、不派子代理；后台 job 发起后立刻 `job_output wait` 阻塞等（`HANDOVER.md` §7/§9.16）。
4. **先量仪器**：`STRATA_*_TIMING` 有代价（L-030 是 +2.5–8%）；开计时器的臂要单列，不和不开的臂混着比。
5. **原始日志 + 脚本名 + "没测什么"** 三件套缺一不可；没有证据的轮次按未发生处理（`RULES.md` §9.6）。
6. **跨机型不可横比**：引用上游/别机的数字一律标"不可横比"（`SERVER_ADAPT_REQUIREMENTS.md` §6.1）。

## 5. 变更门

- **配置臂**：`cp -p` 快照 → 改 `strata-022.json` → `use-config.sh` 装 → `verify-running-config.sh` 核 →
  测量 → 装回快照 → `cmp -s` 逐字节一致 → 台账。装失败先恢复再退出（`tools/server/README.md` 的形状）。
- **代码臂**：独立 build 目录（`build-mp/` 之类），**默认关**的运行时开关，生产 `build/` 一字节不动；
  部署验**文件**不验产物（`md5sum` 两边）；覆盖用 `cp … .new && mv -f`；后台构建不算完成（`RULES.md` §9）。
- **一次一个变量**；一次一个安装器（`pgrep -af "[u]se-config|[r]estore-prod"` 为空才动）。
- 每个改动写明回滚命令，回滚命令必须能在一次重启内完成。

## 6. 记录格式（台账条目模板）

```
## L-0xx · <一句话结论>（<日期>，<臂数> 臂，<同 boot/跨 boot>）

**假设**：……
**臂**：A=…… / B=…… / （C=……）
**做法**：<脚本名> <参数>；每臂 ≥3 次，交错。
**数字**：<表：中位、sd、N 路、冷/热、读的人 TTFT、邻居停顿、聚合>
**判决**：采纳 / 否决 / 未定 / 参考（理由一行）
**没测什么**：……
**原始日志**：`/opt/ai/logs/<ID>-<name>-<UTC ts>.log`（或 jsonl / runs）
```

多路实验在条目开头额外标三样：**路数 N、冷/热、满槽与否**。

## 7. 命名规范

| 类别 | 规则 | 例 |
|---|---|---|
| 文档 | `MULTIPATH_PLAN.md` / `MULTIPATH_STANDARD.md`；同类实验文档用 `<主题>_<用途>.md` | 本文件 |
| 预设 | `strata-022.mp-<N>x<CTX>.json` | `strata-022.mp-4x256K.json` |
| 实验脚本 | `exp-` 要重启；`probe-` 只读；`ops-` 改机器（`tools/server/README.md` 的角色前缀） | `exp-mp-matrix.sh` |
| 日志 | `/opt/ai/logs/MP-<ID>-<name>-<UTC ts>.log`（jsonl 同理） | `MP-L046-mp-baseline-...jsonl` |
| 台账 | 从 L-046 起接续；多路条目在标题里带"多路" | `L-046 · 多路基线 …` |

## 8. 与上游的关系

- 上游只读（`RULES.md` §1）：本标准的产出**只进我们自己的台账与文档**，不回帖、不开 PR。
- 上游已有的机制（`STRATA_PIPED_READ_PIECE` 等）优先用，**不重复造**（`RULES.md` §3）。
- 本机新增的代码/预设一律 **opt-in、默认路径字节一致**；离开这个仓库就失效的补丁不进 `main`。

---

**变更记录**
- v0.1 · 2026-10-24 · 初稿：术语与"多路就绪"四条、定容式与 4x256K 命名、G0–G3 门、测量协议、变更门、台账模板、命名规范。
