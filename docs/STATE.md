# 当前状态

> 接手顺序：`RULES.md` → 本文件 → `ROADMAP.md` → `ITERATION_LOG.md` 最近一条。
> 完整证据在 `EVIDENCE_LEDGER.md`；候选和回滚在 `WORK_QUEUE.md`。

- 全局目标（第四十三次修订）：持续提高真实 AI vibe coding 的任务成功率、指令遵循、
  工具协议和代码正确性，
  再缩短可用代码、测试结果或明确阻塞的出现时间。严重 GPU/运行时事故有最高抢占权；没有
  新事故时继续处理真实用户等待。用户通常 1 个主代理、偶尔 2 个，4 路非回退，6/8 只做
  安全回归。1% 也算收益，但必须先证明候选进入现役 batch/slot、layer split 和真实 DSH
  路径，并给出触发率、绝对秒数和保守整任务上界。不可达的 env-only 结果只算机制证据；
  每轮先处理真实整任务中最大的可改成本，局部机制没有生产可达性和 1% 上界就立即关闭。
  质量归因必须使用同 runner、模型目录、fixture、提示词和 provider 的 B0/B1 配对；
  B0/B1 共同失败只能证明不是候选回归，不能算质量通过。
- 未完成采纳项：21
- 当前项：GPU-XID13-STABILITY（P0 抢占）
- 上一项完成：`AGENT-BATCH-PREFILL-CPU-SHARE-GUARD` 已采纳为默认关闭、生产 opt-in。
  生产选项 engine `e3d25b38...` 的 B0/B1/B0r 判决 `pass`，控制漂移最大 `1.53%`；
  1 路 hot-short/hot-long wall 改善 `11.825%/10.991%`，2 路
  `7.896%/8.056%`，全部 `3/3` 波胜。4 路同源码隔离证据仍为
  `1.341%/8.474%/1.453%`；生产二进制未单跑 4 路。
- 质量门已过：fixture schema 5 修正最终消息歧义后，B0/B1 均 `7/7`，比较器 `pass`，
  零身份不一致、零候选回归、零共享失败。旧 B1 `5/7` 与首轮 `6/7` 分别是 XML tool-call
  重复采样波动和最终消息摘要格式歧义，不再作为质量回退证据。
- 当前断点：最新严重 DSH 会话已按失败步骤重新核算。该 turn 共 5 步，`task_wall_s`
  `36.31 s`、含失败步骤的模型墙 `35.149 s`；第 5 步等待 `2.296 s` 后以
  `server_error: verify: layer 11 never rang (an illegal instruction was encountered)`
  结束，GPU0 同秒记录 8×Xid 13 + Xid 43。这是用户任务直接失败，优先级高于继续提速；
  当前先做单变量 `STRATA_HC_SPLIT=1` 风险缓解验证。
- 测试方法已修正：`probe-dsh-session-cost.py` 过去只统计成功的 `assistant/message`，
  会把上述 5 步失败会话报成 4 步。现在 `assistant/attempt` 也进入 step/model wall，并只
  保留 `failed`、finish kind 和 error code，不保留推理或错误正文；专项 `9 passed`。
- 暂停项：`AGENT-CURRENT-PROD-COST-REMAP`。最新长输出会话 `task_wall_s=202.486 s`，
  单个 `15,036` output-token step 占 `173.323 s`，模型总计 `96.799%`；这确认长输出
  decode 是后续性能大头，但在 GPU 用户可见失败收敛前不继续调它。
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
  契约后严格 sentinel `3/6→6/6`。Codex CPU guard 在 schema 5 下 B0/B1 均 `7/7`；
  fixture 现在要求最终消息只含 `RESULT=PASS`，消除“摘要后附 sentinel”的误判。
- GPU 抢占项：`GPU-XID13-STABILITY`。DSH 最终请求曾在 GPU0 触发 8×Xid 13 + Xid 43；
  约 60 次重放未复现。固定 prompt 不是硬件稳定证明。每个真实窗口从 UTC 起点统计新增
  NVRM Xid；任何新 Xid 立即抢占。
- 长上下文：真实 49 天 223K 旧会话 cached `0`、TTFT `113.014 s`，是低频 p99；
  210K compaction 和当前 Codex compaction 已高度缓存。compaction 后立即回读旧分支在
  109 次中触发 0 次，只保留低频诊断。
- 硬件：两张 RTX 2080 Ti 的算力解锁已固定；cuBLAS FP16
  `62.40→93.69/94.07 TFLOPS`，当前 boot `unlocked=1`。不重新研究或修改解锁。
- 运行状态：CPU-share 候选已作为生产 opt-in 部署。server/engine `292517/292525`，
  A1 server cwd `/opt/ai/src/strata-two-stream-serial-20261031`，端口 `8080`，
  health loaded、262144、6 slots / 2 groups；最终 engine `e60a334c...`、config
  `2c4c7ac6...`、server `bd3e4289...`。最终 RAII 顺序修复后的 launch 到 ready 约
  `150 s`。A1 回滚身份
  仍是 server/vision/engine `254299/254303/254308`，engine `105a4171...`、config
  `6ece1a13...`。生产 server 实测 env 为
  `STRATA_ADMIT_SHORT_FIRST=2048`、`STRATA_DEFER_TITLE_REQUESTS=1`、
  `STRATA_MP_STAGED_READ_MIN_TOKENS=2048`、`STRATA_PIN_DSH_TOOL_PREFIX=1`。
  engine env 另有 `STRATA_BATCH_SERIAL_TWO=1` 和三个 CPU-share 生产开关；没有 DEBUG。
- 本机到生产：`127.0.0.1:18080 -> 127.0.0.1:8080`，SSH relay 端口 `8686`。
  不改用户 DSH profile；实验使用显式 overlay/proxy。
- 主要验收门：真实触发；1 路不退化，2 路报告第二 TTFT/跨度，4 路不回退 >1%，
  6/8 零错误和资源安全；输出、finish、工具、修改和测试全过；默认路径可回滚；
  跨 boot 或持久 checkpoint 污染必须单列。
- 测试口径：主指标是 DSH `task_wall_s`；runner wall 包含 turn 后辅助工作，只作次指标。
  辅助请求必须记录到达、释放、上游开始/完成、turn 结束和客户端断开；不能只看 task wall。
  路径审计、机制增量与生产回归分开；性能主比较必须同源码、同构建选项、同 config，旧生产
  臂不能承担候选内部增量的归因。热轮比较器必须参数化臂名和 `1% + 绝对秒数`门，要求
  三臂记录的全部波齐全、候选至少赢三分之二波，并同时检查 1/2 路 cold 与 hot 非回退；
  不能用历史脚本写死的 `A64/B0/A64r` 或 `0.15 s` 代替当前契约。
- 质量配对必须保存并核对 runner、模型、reasoning、模型目录 SHA-256、fixture suite
  SHA-256、prompt suffix SHA-256 和 provider URL。比较器把候选从通过变失败判为回归，
  把双方共享失败判为 `blocked_shared_failures`；后者不能进入性能矩阵或生产。
- 当前文档：总计划
  [`2026-10-31-agent-workload-throughput.md`](superpowers/plans/2026-10-31-agent-workload-throughput.md)
  已完成第四十三次修订；旧平台全局目标仍是 paused，接口不能改写正文，本文件和仓库计划
  是当前权威目标。
