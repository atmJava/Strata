# 多路融合轮次日志（MULTIPATH_LOG）

> **每轮一条，最新在上。** 格式 = [`MULTIPATH_RULES.md`](MULTIPATH_RULES.md) §2 的复盘模板（七项缺一不可）。
> 数字的原始证据在 [`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md)，状态与决策在 [`HANDOVER.md`](HANDOVER.md)，
> 计划在 [`MULTIPATH_PLAN.md`](MULTIPATH_PLAN.md)。**复盘没写 = 本轮按未发生处理。**

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
