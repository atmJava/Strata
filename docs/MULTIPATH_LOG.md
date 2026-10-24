# 多路融合轮次日志（MULTIPATH_LOG）

> **每轮一条，最新在上。** 格式 = [`MULTIPATH_RULES.md`](MULTIPATH_RULES.md) §2 的复盘模板（七项缺一不可）。
> 数字的原始证据在 [`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md)，状态与决策在 [`HANDOVER.md`](HANDOVER.md)，
> 计划在 [`MULTIPATH_PLAN.md`](MULTIPATH_PLAN.md)。**复盘没写 = 本轮按未发生处理。**

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
