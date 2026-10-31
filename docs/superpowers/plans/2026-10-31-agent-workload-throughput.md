# 真实 Agent 负载优化计划

日期：2026-10-31

状态：已按用户最新要求重拟、评审并开始执行。

## 全局目标

缩短用户真实 Codex 任务从提交到可用结果的时间，并减少完成同一任务所消耗的引擎请求、
prompt token、长上下文读取、compaction 重读和排队时间。用户通常单会话，偶尔双会话，
峰值是一主三子代理。因此：

- 1/2 路是主验收；
- 4 路是非回退上限；
- 6/8 路只保留安全、健康、无错误和严重退化回归。

这里的一路指一个正在处理的请求流，不是服务器槽位，也不要求每个子代理在每个时刻都
有生成请求。任何局部优化都先计算“真实触发率 × 单次关键路径节省 / 整任务时间”的保守
上界；预计不到 1% 的工作不进入构建、部署或长测。

以下任一稳定结果都算成功：真实任务墙钟、TTFT、完成时间或关键阶段至少改善 1%；或者
在不增加墙钟的前提下消除至少 1% 的重复 prompt/引擎工作。服务器和 Strata 可以重启，
但必须先证明候选值得约 2–3 分钟加载及 cache 冷启动成本，并记录停机与恢复时间。

除非用户明确停止，否则持续执行：每轮完成判决、复盘、交接、提交、推送和下一项预注册，
然后继续。不等待额外的“是否继续”确认。

## 昨晚低收益的复盘

问题不是执行量不足，而是优化对象和决策门错位：

1. 6/8 路合成矩阵被当成用户目标，掩盖了用户主要是单会话这一事实。
2. 多个内核候选的单窗口上界只有亚毫秒到数毫秒，乘以真实触发率后不到整任务 1%，
   却仍消耗构建、加载和多臂测试。
3. 3 次并发波次的 A/A 漂移可达 3–7%，却用 1% 门判断微小差别。
4. token 吞吐不等于用户等待时间。title、compaction、工具往返、主线程等待和子代理交错
   可能比单个 kernel 更靠近整任务关键路径。
5. 已采纳的 title、线程级 compaction LRU、共享 system 前缀和 boundary pin 都来自真实
   Codex 请求结构，收益远高于同阶段多数局部内核试验。

因此后续先找“重复工作”和“上下文断裂”，再考虑“把同一条工作算快一点”。

## 真实 trace 证据

只读解析本机一个约 29 小时的 Codex 会话日志，共 3,614 条 token usage 记录：

- 输入总计 `640,411,228` token，缓存 `622,593,536`，缓存率 `97.22%`；
- 未缓存输入 `17,817,692` token；
- 输出 `2,769,364` token，其中 reasoning `794,887`；
- 36 次 compaction 前的大请求输入约 `26–30 万` token，但缓存通常仅 `3.5–7.6K`；
  单次产生约 `25–29 万` 未缓存 token，这 36 次合计约 `970 万`，约占全部未缓存输入的
  一半。

这些是 Codex 客户端 token usage 和请求形状，不是 Strata 服务器的逐请求耗时；不能把
客户端模型 token 速率直接当作远端 engine 的 A/B 结果。它们用于选择实验对象和估计
触发率，最终性能结论仍须由同一 Strata server 的 1/2 路真实 HTTP 负载给出。

## 已完成成果

### AGENT-CODEX-TITLE

title 请求在本地生成合法标题，`1.8–2.1 ms`，零引擎请求，零 prompt/output token。

### AGENT-CODEX-COMPACTION-LRU

12 项线程安全 LRU 保留每个 Codex 线程自己的工具集合。1 主 3 子代理交错后，四次
compaction 各复用约 `99.5%`，墙钟 `0.69–0.89 s`。

### AGENT-STATIC-SYSTEM-PREFIX

共享 system 放在各线程 tools 前。不同 tools 的共同前缀从 `320` 增至 `54,366` 字节；
后续兄弟请求复用 `5,120` token，平均墙钟 `7.515 → 5.669 s`（`-24.56%`），后三个兄弟
`7.502 → 5.001 s`（`-33.34%`）。

### AGENT-PREFIX-PIN

默认关闭，配置 `codex_auto_prefix_pin: true` 时在共享 system 结束处自动注入精确
`strata_prefix`。B4 中后续兄弟请求复用 `10,013 / 10,287 = 97.34%`，墙钟
`1.392/1.322/1.291 s`；相对 B3a 的总墙钟 `22.676 → 13.322 s`（`-41.25%`），后三请求
`5.001 → 1.335 s`（`-73.31%`）。并发、8 路安全、compaction、title 和工具隔离通过。

### AGENT-COMPACTION-LARGE

在现役 B4 上使用同一真实 HTTP 路径，先读 `180,279` token 长历史，再发送该会话的
Codex compaction：

- 冷 turn：`65.555 s`，cached 0；
- compaction：`180,301` 输入，cached `180,272`，复用 `99.984%`；
- compaction 墙钟 `2.385 s`，只重读 `29` token。

这补上了此前 compaction 验证只覆盖 8.3K prompt 的规模缺口，证明线程工具 LRU 在长历史
下仍能避免整段重读。

## 指标与决策门

### 主指标

- 端到端任务墙钟：主任务开始到主线程给出可用结果，包含子代理和工具轮次；
- 主线程关键路径：只计算延迟最终结果的部分，不用聚合吞吐掩盖；
- 请求成本：input/cached/uncached token、prompt ms、TTFT、decode tok/s、slot wait、finish；
- 非正文开销：title 应为零引擎/token；compaction 应保持高复用；
- 队列与资源：请求错误、timeout、CUDA/execution failure、RAM/VRAM/swap、健康状态。

### 决策门

1. 客户端 token trace 与服务器 timing 分开记录；静态源码结论明确标注未测。
2. 1/2 路至少一种真实 Agent 形状稳定改善 ≥1%，并给出触发率和保守整任务上界。
3. 4 路相对同 boot 基线不回退 >1%；6/8 路只要求全部完成、无严重退化和零异常。
4. 数值或文本发生变化时，冻结 greedy parity、工具选择、finish 和内容质量门必须先通过。
5. 默认路径保持 byte-identical；新能力 opt-in，失败可关闭并精确恢复。
6. 任何一级不得出现 HTTP、CUDA、timeout、traceback、意外 finish 或外来请求错误。

## 执行阶段

### 阶段 0：固化当前成果

1. 把 B4 源码、测试、contract/result 与长 compaction 证据写入全部交接文档。
2. 跑 `git diff --check`、UTF-8/BOM、离线 preflight、Responses 和 frontend 测试。
3. 提交并通过 HTTPS 推送；用 `ls-remote` 核对远端提交。
4. 记录 B4 的进程、端口、hash、健康、加载时间和回滚方法。

### 阶段 1：建立可重复的真实 trace 入口

把本次只读统计固化成小工具，只读取本地 Codex session JSONL，输出：

- 总 input/cached/uncached/output/reasoning；
- 按 root turn 的调用数、时间跨度和 token；
- compaction 次数、前一轮缓存率、compaction 缓存率和未缓存量；
- 长请求分位数和 top uncached 事件。

这是选择下一项的依据，不直接宣称性能改善。工具不应输出 prompt 正文、API key 或密码。

### 阶段 2：选择当前最大可行动瓶颈

当前候选按以下顺序比较：

1. **长历史 compaction**：已由 AGENT-COMPACTION-LARGE 验证，不再重复同形实验。
2. **正文 decode**：真实 trace 的输出量很大，但必须先用同 server metrics 区分 decode、
   tool 往返和模型编排等待；只有出现可归因的 1% 机制才进入 engine。
3. **动态上下文增量**：普通续轮已约 95% 缓存，剩余 median 约 265 token，主要集中在
   tool output 和新消息；不得为了压低这些 token 改变语义。
4. **准入/排队**：只在真实 1/2 路路径出现明显 slot wait 时处理，不用 6/8 合成峰值驱动。

源码候选必须写清生产命中条件、真实触发率、绝对节省、保守整任务上界、数值影响和回滚。

### 阶段 3：单候选闭环

1. 先跑无重启的源码、单元、fixture、同进程或独立 GPU 门；上界不足 1% 立即否决。
2. 只有通过上界门才构建/部署；一次候选最多一次 server 重启。
3. 只有候选修改 engine 且收益足以覆盖构建与 2–3 分钟加载时，才重建 engine。
4. 做 1/2 路主验收、4 路非回退门和 6/8 路安全回归。
5. 采纳则保持运行；否决则恢复精确基线和 hash。两者都记录全部证据。

### 阶段 4：持续复盘循环

每轮结束立即：

1. 写一句可验证结论：消除了多少关键路径时间，或为什么没有收益；
2. 更新 `STATE`、`ROADMAP`、`WORK_QUEUE`、`ITERATION_LOG`、`EVIDENCE_LEDGER`、
   `HANDOVER` 和 contract/result；
3. 跑格式、UTF-8/BOM、preflight 和测试；
4. 提交、HTTPS 推送并核对远端；
5. 根据最新瓶颈预注册下一项，立即继续。

## 不再重开

- 不以 6/8 路合成峰值决定低并发优化；
- 不在没有真实触发率和 1% 整任务上界时构建 server 或重启；
- 不重开 waitA、fair-service、poll 快返回、head/LFUSE/Q5/IQ3、QSA、
  checkpoint move、EXPDEC、PIPED-MTP 的同形路线；
- 不把 PIDs 当端口，不把静态源码发现写成性能改善；
- 不把 API key、token 或 sudo 密码写入仓库、日志或回复。

## 计划评审

评审结论：**通过，允许执行。**

保留的正确点：

- 以真实低并发 Agent 形状为主，而不是追求 6/8 路峰值；
- 优先请求级重复工作和长上下文，而非亚毫秒 kernel；
- 1% 收益也计入，但必须先乘以真实触发率；
- 默认关闭、正确性、资源、回滚和远端 dirty 文件保护不可放宽。

本轮修正：

- 把客户端 trace 与 Strata server timing 明确分开，防止错误归因；
- 把 36 次 compaction 的高触发率写进计划，并补做 180K 真实 HTTP 验证；
- 把 B4 和三笔此前已采纳成果写成已完成，不再继续“下一项验证”；
- 下一项先固化 trace 工具，再按 decode、动态上下文和准入的实际证据选择，不预设 kernel。

已知局限：

- 单个 29 小时会话不能代表所有用户；
- 客户端 provider 的 token 统计不能代替远端 engine 性能；
- 工具往返和模型编排的空闲时间无法仅靠 Strata kernel 消除；
- 子代理在 session log 中的线程归属并非总是完整，不能由日志行数直接推定并发。

## 当前断点

阶段 0 已提交 B4 源码/测试/result。阶段 1 的 `probe-codex-sessions.py` 已通过 4 tests，
阶段 2 的同服务 breakdown 已完成：1 路冷/续轮 wall `7.382/1.610 s`，2 路
`11.075/2.290 s`；续轮 cached 约 95%，prompt 仍 `1.4–1.5 s`，短输出 decode 仅
`50–73 ms`。当前进入 AGENT-PROMPT-FIXED，只做无重启相位拆解；若候选整任务上界不足
1%，立即转向动态上下文或长输出 decode。
