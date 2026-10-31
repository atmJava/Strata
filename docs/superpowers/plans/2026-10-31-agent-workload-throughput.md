# 真实 Agent 负载优化计划

日期：2026-10-31

状态：第三次复盘后重拟并通过评审。旧版按“容易测的合成吞吐”排序，本版改为按
真实用户可见的秒数和触发频率排序。阶段 1 已取得第一组结果，当前转入长前缀热恢复
的只读阶段拆解；仍不提前固定 kernel 或调度候选。

## 全局目标

除非用户明确停止，否则持续缩短真实 Codex 任务从提交到产生可用结果的时间，并减少完成
同一任务所需的重复模型工作。优化的是秒数，不是 token/s、单窗口百分比或否决数量。

用户通常一个会话，偶尔两个；峰值是一主三子代理。并发定义如下：

- 1/2 路是默认主验收；
- 4 路只做非回退；
- 6/8 路只做安全、健康和严重退化回归，不参与“成功”定义。

这里的路数是实际同时在途的模型请求，不是 Codex 会话数或服务器槽数。工具执行、用户思考
和等待子代理时没有在途模型请求，不能算作并发。

### 主指标

1. 根任务墙钟：从用户提交到主线程产生可用结果，优先看 p50 和 p75，再检查 p90 尾部。
2. 首可用结果时间：单请求看有效 TTFT；两路交互任务还必须看第二条请求的 TTFT，不能只看
   两个请求最后哪个先完成。
3. 单请求关键路径：wall、prompt、decode、slot wait、cached/uncached token 和 finish。
4. 非正文成本：title、compaction、context 重建等应尽量不进入引擎，或保持接近零重读。
5. 正确性和资源：冻结输入输出、工具选择、错误、RAM/VRAM/swap 与健康状态。

批量任务可以用完成跨度作为主指标；交互式双会话不能把“第二条等待数秒、但两个请求总完成
更早”直接算成用户收益。两种目标必须分开命名。

### 选择与采纳门

任何候选先建立下面的保守成本模型：

```text
预期节省秒数 =
  真实触发次数
  × 单次关键路径绝对节省
  - 新增排队、TTFT 或 decode 回退
```

必须用真实请求尺寸和频率估算，不允许拿单 kernel 百分比直接当整任务收益。预计不足 1%
的工作不构建、不部署、不重启。1% 可以采纳，但至少需要同 boot、interleaved/paired 重复
和稳定的绝对秒数证据；不能把 3–7% 的跨 boot 漂移当收益。

每个候选还须满足：

- 1 路不退化，主要 Agent 形状稳定改善 ≥1%；
- 2 路不得用数秒级首结果延迟换取聚合完成跨度，除非真实任务 replay 证明该请求并不在用户
  关键路径；
- 4 路不回退 >1%；6/8 路零错误、无严重退化；
- 数值变化先过冻结 greedy、工具选择、finish 和内容质量门；
- 默认路径字节不变，新能力 opt-in、可关闭并能恢复精确基线；
- 重启成本计入实验价值；不值得覆盖约 2–3 分钟加载和 cache 冷启动时不重启。

## 第三次复盘：上一版仍不够接近大头

正确的地方：

- title、线程级 compaction LRU、共享 system 前缀和 boundary pin 都直接消除了真实工作中
  的重复请求或重读，收益远高于后来多数局部 kernel 候选；
- 1/2 路主验收、4 路非回退、6/8 安全回归的边界正确；
- 请求级成本优先于亚毫秒 kernel 的方向正确。

仍需修正的地方：

1. 上一版下一项直接定为 12K 热续轮约 `1.4–1.5 s` prompt，但真实会话单请求 input 中位是
   `188,562` token。12K 只能证明存在固定成本，不能代表其绝对值或用户影响。
2. 512-token 长输出测试更适合判断 decode，但不能代表日常的工具调用短回复；两者都要进入
   成本地图，不能用一个合成形状定义全局优先级。
3. token/s 和两个请求的完成跨度都不是用户主指标。两路串行把第二条 TTFT 推到约 `5.1 s`，
   只证明了“总完成时间优先”的批处理取舍，没有证明交互式双会话更好。
4. 根任务日志显示，已完成任务墙钟从 `4.168 s` 到 `112.353 s`，另有一个约 `10.9 h` 的持续
   执行任务；多数有效 TTFT 为 `0.777–1.354 s`，一个为 `28.485 s`。用户等待的大头可能是
   多轮请求、工具和编排的累积，而不是某一个 prompt 段或 kernel。
5. 局部优化连续被否决并非执行不够，而是缺少“真实频率 × 绝对秒数”的排序。继续问“哪个
   kernel 还能快 1%”会再次把时间花在错误层级。

当前最重要的未知量是：真实长前缀在已高度缓存时，Strata 仍然花多少秒；这些秒分布在
host、checkpoint restore、uncached prefill、准入和 decode 的哪里。没有这个地图，任何
后续实现都只是猜测。

## 已有秒级证据

### 客户端真实请求形状

一个约 29 小时的 Codex session 有 3,614 条 token usage：

- input `640,411,228`，cached `622,593,536`（`97.22%`），uncached `17,817,692`；
- 单请求 input 中位 `188,562`、p90 `283,539`、p99 `298,935`；
- uncached input 中位仅 `265`，但 p90 `7,907`、p99 `42,631`；
- 36 次 compaction 前的大请求约 `26–30 万` input，旧 provider 缓存通常只有 `3.5–7.6K`。

这些是客户端请求频率和 token 形状，不是 Strata 时延。它们说明必须测长前缀，也说明少数
compaction/冷读事件可能压过大量普通续轮。

### Strata 服务端

| 形状 | 已知结果 | 能说明什么 |
|---|---|---|
| 12K 冷首轮 | prompt `7.3 s` | 冷读昂贵 |
| 12K 续轮，约 95% cached | prompt `1.4–1.5 s`，decode `50–73 ms` | 固定/恢复成本确实存在 |
| 实际 94K 续轮，99.36% cached | prompt `1.94 s`，wall `2.09 s` | 热前缀并非按长度线性变慢 |
| 实际 189K 续轮，99.68% cached | prompt `2.24 s`，wall `2.43 s` | 长前缀仍有约 2 秒固定/恢复成本 |
| 96K 冷读 | wall 约 `42 s` | 长冷读是秒级大头 |
| 180K 冷 turn | wall `65.555 s` | 冷读更长 |
| 180K 真实会话重读 | 已做冷读后同会话 compaction | 复读路径已覆盖一次 |
| 180K compaction，LRU 命中 | cached `180,272/180,301 = 99.984%`，wall `2.385 s` | 已完成线程级缓存，不再重读整段 |
| 210K 冷读/续读/25% 改写 | cold `88.293 s`；warm suffix `11.529 s`；rewrite25 `76.400 s` | 长读历史已测；重新定位/部分改写仍昂贵 |
| 210K 后 compaction | 输入 `208,554`，cached `208,527 = 99.987%`；输出 347/532/734 token；wall `5.853/8.845/85.017 s`，均 completed | 前缀缓存已解决；一次 recovery 后 75 秒无新 token 的尾部异常成为新问题 |
| 512-token 单路 | wall 约 `4.5 s` | 有实质正文时 decode 是重要成本 |
| 两路 512-token batch | 聚合约 `88 tok/s`，完成跨度 `11.7 s` | 两路 batch 存在低并发凹点 |
| 两路 solo 串行 | 完成跨度 `10.0 s`，第二条 TTFT `5.1 s` | 只适合总完成时间优先，不能直接算交互收益 |

目前已有 94K/189K/208K 热续轮成本及 180K/210K compaction。第一次成本地图显示：

- 208K 完全相同的热历史 turn 三次稳定在 `9.605–9.784 s`；
- 同样 compaction 的 prompt 只新增 22–27 token，前缀恢复不是主要问题；
- 正常 compaction 输出 347/532 token 时为 `5.853/8.845 s`；
- 第三次在 512 个 reasoning token 后触发 recovery，随后约 75 秒没有新 token，最终
  输出 734 token、wall `85.017 s`。这是一个明确的无进展尾部，不能混入普通中位数。

按当前证据，第一优先不是继续改 compaction prompt cache，而是拆解 208K 热状态从 host
恢复到可 decode 的约 9.6 秒，并确认 recovery 无进展尾部能否避免。

## 执行阶段

### 阶段 0：验明当前基线（完成）

当前候选已加载并验证：

- server/vision/engine `50648/50651/50656`；
- 源码 `/opt/ai/src/strata-two-stream-serial-20261031`；
- engine `105a4171...` 未重建，config `7c901092...`，server `10bd8318...`；
- `STRATA_BATCH_SERIAL_TWO=1`，health loaded，6 slots / 2 groups，262144 context；
- `strata.service` inactive/dead。加载耗时约 177 秒。

回滚是启动原源码目录的 `strata.py serve --config a-config.json`，且不带
`STRATA_BATCH_SERIAL_TWO`。原源码和原配置未被覆盖。

### 阶段 1：AGENT-COST-MAP（第一轮完成，继续拆最大项）

目标不是实现候选，而是得到一个能按“真实触发次数 × 秒数”排序的成本地图。所有测量复用
当前服务，不重建、不重启。

采集：

1. 现有热续轮成本为 12K `1.4–1.5 s`、实际 94K `1.94 s`、实际 189K `2.24 s`；不再扫描
   邻近尺寸。
2. 1 路冷读已有 96K/180K/210K 结果；除非频率证明必要，不重跑冷读。
3. 180K 后 compaction 已在 `2.385 s` 内复用 `99.984%`；约 210K 的同会话测试也已完成，
   三次均复用 `99.987%` 且 completed。压缩前缀缓存路线收口，只剩输出/recovery 尾部。
4. 用户常见的约 180K 历史重读已由冷读 + compaction 覆盖；不重复整段冷读，改测重读后一次
   普通续轮是否仍保持高复用。
5. 2 路只测一个代表性尺寸，主流验收第二条 TTFT、每个请求 wall 和完成跨度，同时报告三种
   指标，不用完成跨度覆盖首结果延迟。
6. 输出长度用短工具结果和 512-token 正文两类，分别代表交互回复与实质生成。

每条记录必须包含：实际 prompt、cached/uncached、prompt、decode、TTFT、slot wait、wall、
finish、错误、实际并发数；不保存 prompt/response 正文和密钥。

成本拆解：

- 先用已有 `prompt_ms`、`STRATA_TRACE`、checkpoint/restore 和 stage timing 能提供的字段；
- 先做只读源码审计，确认字段是否在现役二进制可达；
- 只补 instrumentation 能改变观测开销而无重启路径的 trace；
- 将热点分为 host/prompt-cache validation、checkpoint restore、uncached prefill、准入/排在
  decode，并记录方差与重叠关系；
- 用客户端真实尺寸频率加权，得到每类成本的“每请求”和“每 root task”秒数上界。

停止条件：

- 若一个机制的最大可消除秒数按真实频率加权不足 root task 的 1%，直接否决，不改源码；
- 若无法从当前日志定位最大秒数，先做最小 trace instrumentation，而不是猜一个 kernel；
- 若 12K prompt 固定成本与 180K 不成比例，改用真实长前缀目标；若近似常数，则优先攻击固定
  路径而不是继续扩上下文扫描。

### 阶段 2：只执行成本地图的第一名

根据阶段 1 的绝对秒数选一个方向，顺序不预先写死。可能的大头只有以下几类：

1. 热缓存长前缀的 host/checkpoint/restore 固定或线性成本：208K 同 turn 已稳定
   `9.6 s`，而 189K 只需 `2.24 s`；
2. 64K–280K 冷 prefill 的主要 stage；
3. 单路实质正文 decode，或真实双路低并发 decode；
4. reasoning-loop recovery 后无新 token 的长尾；当前 3 次 210K compaction 中 1 次出现
   约 75 秒停滞，但还需真实频率确认；
5. 真实 replay 证明存在的准入/公平性损失。

候选必须先给出触发率、单次绝对节省、整任务上界、预计新增 TTFT、回滚和正确性影响，再决定
是否构建。一次候选最多一次服务器重启；修改 engine 还要额外证明收益覆盖构建和加载成本。

### 阶段 3：用户级 replay，而不只是同步矩阵

候选通过请求级门后，至少 replay：

- 单会话多轮工具调用，包含短回复和一次实质正文；
- 两会话交互，检查第二条 TTFT；
- 一次真实尺寸 compaction 和一次长前缀续轮；
- 4 路非回退和 6/8 路安全回归。

主判据是根任务墙钟和首可用结果时间；请求级吞吐只用于解释原因。若模型层收益被工具/编排
的累积等待吞没，应记录真实上界并转向下一项，不能把局部胜场包装成用户收益。

### 阶段 4：闭环与持续循环

每轮立即：

1. 写一句可验证结论和未测范围；
2. 更新 `STATE`、`ROADMAP`、`WORK_QUEUE`、`ITERATION_LOG`、`EVIDENCE_LEDGER`、
   `HANDOVER` 和 contract/result；
3. 跑格式、UTF-8/BOM、offline preflight 和聚焦测试；
4. 提交、HTTPS 推送并核对远端 commit；
5. 从更新后的成本地图选择下一项并立即继续。

## 当前不再做

- 不扫描 MTP draft depth、阈值、组数、CPU order、PCIe fraction、aux CPU 或 checkpoint 邻近值；
- 不重开 waitA、fair-service、poll 快返回、head/LFUSE/Q5/Q6/IQ3、QSA、checkpoint move、
  EXPDEC、PIPED-MTP 的同形路线；
- 不以 6/8 路合成峰值为低并发成功标准；
- 不在真实频率和绝对秒数上界不足 1% 时构建或重启；
- 不因局部 kernel 快 1–2% 就假设整任务变快；
- 不把两路串行增加的第二条 TTFT 隐藏在一个聚合完成数字里；
- 不重复已完成的 title、基础前缀复用、线程 compaction LRU 和 180K compaction 验证；
- 不把 API key、token、sudo 密码或 prompt 正文写入仓库、日志或回复。

## 计划评审

结论：**通过，允许先执行 AGENT-COST-MAP。**

保留：

- 真实低并发负载优先；
- 请求级重复工作优先；
- 1% 也计入，但必须先乘真实触发率；
- 默认关闭、正确性、资源和精确回滚不可放宽。

修正：

- 从“预测一个候选”改为“先测成本地图”；
- 主指标从 token/s 和完成跨度改为根任务墙钟、首可用结果时间和单请求 wall；
- 把 12K prompt 证据降级为形状提示，不当作 188K 真实负载代表；
- 明确两路串行的 TTFT 取舍，不再把它直接写成全局用户收益；
- 删除已经否决或已测无差异的参数、kernel 和矩阵扫描。

已知局限：

- 单个 Codex session 不能代表全部用户；
- 客户端 token 记录无法直接归因 Strata server；
- 工具执行、思考时间和人工等待不是 Strata 可消除成本；
- 子代理线程归属不总是完整，实际并发必须由服务端在途请求确认。

## 当前断点

阶段 0、trace 工具、`UTIL-MTP-LOWCONC`、`UTIL-TWO-STREAM-SERIAL` 和第一轮
`AGENT-COST-MAP` 已完成。当前服务 server/vision/engine `50648/50651/50656`，
`STRATA_BATCH_SERIAL_TWO=1`，health loaded，无重启。

下一项是 `AGENT-LONG-HOT-RESTORE`：先只读拆解 208K 热 turn 的约 `9.6 s`，并统计
reasoning-loop recovery 停滞的真实触发率；没有可测的 1% 整任务上界前不构建、不重启。
