# 当前状态

> 接手顺序：`RULES.md` → 本文件 → `ROADMAP.md` → `ITERATION_LOG.md` 最近一条。
> 完整证据在 `EVIDENCE_LEDGER.md`；候选和回滚在 `WORK_QUEUE.md`。

- 全局目标：持续提高真实 AI vibe coding 的任务成功率、指令遵循、工具协议和代码正确性，
  再缩短可用代码、测试结果或明确阻塞的出现时间。严重 GPU/运行时事故有最高抢占权；没有
  新事故时继续处理真实用户等待。用户通常 1 个主代理、偶尔 2 个，4 路非回退，6/8 只做
  安全回归。1% 也算收益，但必须有真实触发率、绝对秒数和保守整任务上界。
- 未完成采纳项：21
- 当前项：AGENT-HOT-TURN-RESIDENT-PATH-AUDIT
- 当前断点：对 resident native direct-pointer 路径做只读 48 层覆盖/fallback 审计。
  group 级 `7.848` 对 MMQ `11.671 ms/layer`；596 fresh 热长轮 A1/B/A2 wall
  `1.522684/1.490017/1.513878 s`，B 相对合并基线改善 `1.862%`、绝对 `32.667 ms`。
  先确认 1.86% 与 group 级乐观上界之间的真实缺口；没有新的整任务 1% 收益就关闭路线，
  有才补 4/6/8 非回退和生产重启。
- 标题方向已关闭：end-of-turn 会让 DSH 在最终主回复后取消标题客户端，真实代理记录
  `BrokenPipeError/502`；该轮 task wall `9.598 s`、runner wall `11.628 s`。
  128/64 output cap 仍跨两个主 step；固定前缀连续两次 `cached=0`。详见
  [标题调度结果](work/DSH_TITLE_SCHEDULING_RESULT-20261101.md)。
- 已采纳的标题隔离只等当前主请求：生产单会话主 turn `19.629→16.078 s`
  （`18.091%`），双会话 fail-open。
- 已采纳的 DSH 首轮前缀复用：干净 boot 顺序 A→B 的稳态 task 中位
  `11.3715→7.3165 s`（`35.66%`、`4.055 s`）；首步 cached `582→5,463`。
- 当前成本：真实 DSH 模型 step 占 `93.202%`，工具并集 `5.312%`。普通 step 首 token
  只比 engine prompt 多约 `25–26 ms`；6K/94K/189K host prepare 中位
  `6.439/11.527/18.345 ms`，host 路径已判 null。
- 质量：DSH Web 走 Qwen/Strata relay；6 个基础 fixture 代码/测试 `6/6`，明确最终消息
  契约后严格 sentinel `3/6→6/6`。
- GPU 抢占项：`GPU-XID13-STABILITY`。DSH 最终请求曾在 GPU0 触发 8×Xid 13 + Xid 43；
  约 60 次重放未复现。固定 prompt 不是硬件稳定证明。每个真实窗口从 UTC 起点统计新增
  NVRM Xid；任何新 Xid 立即抢占。
- 长上下文：真实 49 天 223K 旧会话 cached `0`、TTFT `113.014 s`，是低频 p99；
  210K compaction 和当前 Codex compaction 已高度缓存。compaction 后立即回读旧分支在
  109 次中触发 0 次，只保留低频诊断。
- 硬件：两张 RTX 2080 Ti 的算力解锁已固定；cuBLAS FP16
  `62.40→93.69/94.07 TFLOPS`，当前 boot `unlocked=1`。不重新研究或修改解锁。
- 生产：手动实例，`strata.service` inactive；重启到 ready 约 3 分钟。
  server/vision/engine `232891/232893/232899`，health loaded、262144、
  6 slots / 2 groups。server `bd3e4289...`，engine `105a4171...`，
  config `6ece1a13...`。server env 有 `STRATA_DEFER_TITLE_REQUESTS=1`、
  `STRATA_BATCH_SERIAL_TWO=1`、`STRATA_PIN_DSH_TOOL_PREFIX=1`。
- 本机到生产：`127.0.0.1:18080 -> 127.0.0.1:8080`，SSH relay 端口 `8686`。
  不改用户 DSH profile；实验使用显式 overlay/proxy。
- 主要验收门：真实触发；1 路不退化，2 路报告第二 TTFT/跨度，4 路不回退 >1%，
  6/8 零错误和资源安全；输出、finish、工具、修改和测试全过；默认路径可回滚；
  跨 boot 或持久 checkpoint 污染必须单列。
- 测试口径：主指标是 DSH `task_wall_s`；runner wall 包含 turn 后辅助工作，只作次指标。
  辅助请求必须记录到达、释放、上游开始/完成、turn 结束和客户端断开；不能只看 task wall。
- 当前文档：总计划
  [`2026-10-31-agent-workload-throughput.md`](superpowers/plans/2026-10-31-agent-workload-throughput.md)
  已完成第三十六次修订；旧平台全局目标仍是 paused，接口不能改写正文，本文件和仓库计划
  是当前权威目标。
