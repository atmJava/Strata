# 真实 AI vibe coding 优化计划

日期：2026-10-31

状态：第十二次评审后修订。`AGENT-END-TO-END-TRACE`、
`AGENT-COMPACTION-REAL-SHAPE`、`AGENT-TOOL-CONTEXT-DIET` 和
`AGENT-REAL-DECODE-SHAPE`、`AGENT-PROFILE-HYGIENE`、
`AGENT-DRAFT-BREAKDOWN`、`AGENT-OLD-SESSION-RESUME`、
`AGENT-OLD-SESSION-PREFILL-PROFILE` 和 `AGENT-PLE-INFLIGHT` 已完成。
当前项改为 `AGENT-PLE-ROW-CACHE`：在不改变 1,024 在途读的基础上，验证把
默认 1,048,576 行缓存提高到 4,194,304 行能否继续缩短真实 223K 旧会话冷 prefill。

## 全局目标

除非用户明确停止，持续缩短真实 AI vibe coding 任务从提交到产生可用结果的时间，并降低
完成同一任务所需的重复模型工作；同时保持工具调用、代码修改、测试结果和完成状态正确。

主目标按用户可见结果排序：

1. 根任务从提交到可用结果的 p50、p75 和 p90 墙钟。
2. 首个可执行结果时间：首次有效计划、工具调用或代码修改，不只看首个 stream token。
3. 主线程与子代理同时工作时，主线程首结果和第二条关键请求的 TTFT。
4. 每个“修改→验证→纠错”周期的时间，以及重复测试、重复读取和无效思考的轮次。
5. 隔天旧会话恢复、recovery 卡顿和取消延迟的 p90/p99 尾部。
6. 任务成功率；更快但更常做错、被中止或需要回滚不算收益。

资源与吞吐只用于解释用户时间，不作为成功的替代指标。

## 负载定义

用户通常运行 1 个主代理，偶尔 2 个，峰值 1 主 3 子代理。`2/4/6/8` 指同时进入模型服务
的在途请求数，不是打开的会话数或服务器槽数。工具执行、用户思考和等待子代理时可能没有
在途模型请求，因此真实负载必须按事件时间统计。

- 1/2 路是主要优化和验收目标。
- 4 路是非回退上限。
- 6/8 路只做安全、健康和严重退化回归，不是成功定义。

长上下文继续分成三个不可互换的场景：

1. **当前会话 compaction**：通常在约 210K token 触发。
2. **隔天旧会话恢复**：超过一天未使用的会话重新打开，携带约 180K 历史；缓存可能过期、
   被淘汰或随服务重启消失。
3. **compaction 后旧历史分支**：压缩后重新请求压缩前同一段历史。真实立即触发很低，
   不能代表第 2 类。

## 当前证据

冻结开发会话快照包含 14 个 root task、4,180 个模型请求、6,297 次工具调用和 7.54 亿
input token，其中 97.09% 命中缓存。output 中位/p90/p99 为 `483/1,714/5,069`，
reasoning 总计 `982,658`。真实输出形状明显不是此前的 64-token Agent 探针。

根任务墙钟 union 为 `54,324 s`；推断模型区间 union `20,329 s`，工具区间 union
`36,363 s`，两者有重叠。最长 root 为 `44,783 s`，其中模型 `14,855 s`、工具
`31,088 s`、未归因空档仅 `15.843 s`。工具跨度主要是命令执行和轮询，不能都算作
Strata 可消除成本。

工具等待中位 `910 ms`、p90/p99 约 `30.0/30.2 s`。工具输出 `2,645 万`字符，其中 914 个
`>=10K` 字符输出占 `68.57%`。`write_stdin` 946 次、累计 `22,409 s`，618 次至少 30 秒；
`exec_command` 4,677 次、累计 `16,710 s`。该日志是客户端请求/事件形状，不是 Strata
server timing。

进一步扫描本地 rollout 得到两组快照。较早的 26 文件快照包含 143 root、14,531 模型请求、
2,992,567,554 input、10,206,569 output 和 3,307,087 reasoning token；工具输出
`79,745,864` 字符，2,600 个 `>=10K` 输出占 `63.985%`，另有 4,387 个 `2K-10K`
输出占 `28.309%`。加入本轮真实请求探针后的 35 文件快照包含 152 root、14,768 模型请求、
10,343,967 output 和 3,347,547 reasoning token；模型区间 union `69,236.197 s`，
root wall union `184,604 s`。完整快照中只有 `171.303 s` 的模型区间处于恰好两路并发，
两路转三路仅 1 次；因此两路角色调度不是当前总体第一大头。

这些 rollout 的 provider 是 DeepSeek 直连，不是 B4。真实 Codex compaction 已通过
隐私安全代理单独在 B4 重放三次，当前服务分别复用
`11,124/11,225`、`11,349/11,452`、`11,627/11,729`，均完成。原先 43/44 次低命中不能
归因 Strata，已从待修问题中剔除。

服务端已确认：

| 场景 | 已知结果 | 能说明什么 |
|---|---|---|
| 12K Agent 续轮，约 95% cached | prompt `1.4–1.5 s`，短 decode `50–73 ms` | 热缓存路径仍有固定秒数 |
| 94K/189K 续轮，`99.36%/99.68%` cached | wall `2.086/2.430 s` | 固定成本不能只用 uncached token 解释 |
| 180K cold turn | wall `65.555 s` | 冷读昂贵，但不是隔天旧会话实测 |
| 180K 合成 compaction | cached `99.984%`，wall `2.385 s` | 该构造形状可复用，不能推广到真实客户端 |
| 210K compaction 正常臂 | cached `99.987%`，prompt `248–283 ms` | 正常 prompt 读取接近完成 |
| 210K recovery 臂 | wall `85.017 s`，约 75 秒无新 token | 无进展尾部是独立风险 |
| 480-token 真实形状 | 1 路 `4.682 s`；2 路完成跨度 `9.354 s`，第二请求 TTFT `4.7 s` | 两路串行确实牺牲第二个首结果 |
| 1708-token 真实形状 | 1 路 `16.385 s`；2 路完成跨度 `32.790 s`，第二请求 TTFT `16.384 s` | p90 输出下第二个请求等待完整一代 |
| DeepSeek 直连会话 compaction | 44 次只有 1 次缓存率 >=50%；43 次中位重读 `277,803` token | 客户端 provider 的形状证据，不属于 Strata |
| 压缩后旧分支 | 无 pin 约 `9 s`，精确 pin `0.274 s` | 机制成立，但真实立即触发 0/109 |
| B4 + Codex 0.164 真实 compaction（3 次） | prompt `451.5–529.4 ms`，cached `99.1002–99.1304%`，均 completed | 当前 kept-tool + prefix cache 已覆盖真实 compaction |
| 较早 26 文件 rollout 并发 | 恰好两路模型区间 `143.727 s`，两路转三路 1 次 | 角色感知两路重要但不是总体第一大头 |
| 上下文饮食（三组配对） | wall `38.106→32.914 s`、`58.307→30.727 s`、`41.002→32.686 s` | 首次出现即折叠全部过 1%，延迟折叠否决 |
| 最新 35 文件模型并发 | 单路 union `69,064.894 s`、两路 union `171.303 s` | 用户时间主要由单路请求构成，两路调度总体空间不足 |
| 真实尺寸 replay | 416/1490/4483 的一至两路 span；两路约等于串行翻倍 | 两路 aggregate decode 的计时重叠不是用户收益；4483 出现提前 `stop` |
| 现役 instrumentation | 引擎实际带 `STRATA_DECODE_TIMING=1` 和 `STRATA_VERIFY_PROFILE=1` | replay 来自仪器开启臂；历史成本 6.4%/10.7%，当前配置需 A-B-A |
| 真实 49 天旧会话恢复 | 223,114 prompt token，cached `0`，TTFT `113.014 s`，wall `113.3 s`，decode `292.8 ms` | 隔天旧会话的 p99 主成本是冷 prefill，不是 recovery 或 decode |
| MTP draft 拆分 | round `2.463 ms`，host graph gap `0.017 ms`；全消除 draft-head 的 root 上界约 `0.8%` | 低于 1% 门；关闭 depth、阈值、group、cache 和同形变体 |
| 223K prefill 相位 | 5 次启用相位的冷请求；host PLE wait 可解析、21 个 GPU 相和资源门均通过 | 直接 PLE I/O 与主 GPU 计算都是可测主体，不是 96K 结论的简单外推 |
| 直接 PLE I/O profile | 256 相臂 host PLE `50,362 ms`、主 PLE `39,291 ms`；`mmap` wall 为 `182.157 s` | 1,024 在途读是首个可做、可回滚的 1% 候选；`mmap` 否决 |
| PLE inflight 1,024 | 三组非剖析冷配对 prompt 改善 `2.87%/1.10%/5.58%`，wall `0.05%/1.09%/5.21%` | 配对中位 prompt `2.87%`、wall `1.09%`；作为显式配置采纳，默认仍为 256 |

## 已完成并保留

- Codex thread title 在服务端直接回答，零引擎请求和零 prompt/output token。
- 线程级 compaction cache，支持主线程和子代理交错，约 99.5% 复用。
- system-before-tools，让兄弟 Agent 复用共享长前缀。
- 自动 boundary prefix pin，使后续兄弟请求复用约 97.34%。
- PromptEncoder 已复用前一轮 token id，不需要重做普通 prompt tokenization 缓存。
- 会话可在 host RAM 中 park/restore；磁盘 session file 也能跨重启保存完整引擎状态。
- 恰好两路时 solo 串行、第三条恢复 batch。它改善总完成跨度，但第二条 TTFT 约 5.1 秒，
  不能未经验证当作交互式双会话收益。
- 隐私安全的 Agent shape proxy 可原样转发 Responses 请求，只记录结构哈希、缓存用量、
  finish 和时延，不保存正文。
- 上下文饮食作为 opt-in 代理能力：大型成功工具输出首次出现时保存为完整文件并替换为
  稳定引用，错误/失败/小输出保持原样。它不进入服务默认路径。
- 生产引擎已移除 `STRATA_DECODE_TIMING` 和 `STRATA_VERIFY_PROFILE`；稳定 416/1490
  输出尺寸分别改善 `6.61%/5.38%`，B2 保持在产。
- 真实 49 天旧会话已通过隧道重放，确认约 113 秒冷 prefill 是 p99 尾部主体；后续不得
  再用合成 180K 或 compaction 结果代替该场景。
- PLE direct reader 已在真实旧会话上完成 profile 并采纳
  `--ple-inflight 1024`。三组非剖析配对的 prompt 全部改善，配对中位改善 `2.87%`；
  wall 配对中位改善 `1.09%`。默认仍为 256，回滚是移除一个显式参数。

这些能力默认继续保留，除非有同 boot、真实任务形状和质量门证明替换更好。

## 验收与选择门

候选先建立保守模型：

```text
预期任务收益 =
  真实触发次数 × 单次关键路径绝对节省
  + 减少的后续轮次 / compaction / 工具回流收益
  - 新增排队、TTFT、decode、资源或失败成本
```

选择规则：

- 普通候选必须对真实根任务给出至少 1% 的保守时间上界；1% 也采纳，但必须以绝对秒数和
  真实触发频率证明。
- 低频但单次几十秒的旧会话恢复可以按 p90/p99 用户体验单独立项，不必伪装成总体 1%。
- 1 路不退化；2 路同时报告第二条 TTFT、单请求 wall 和完成跨度；4 路不得回退 >1%。
- 6/8 路零错误、health loaded、无严重资源退化。
- 任何改变生成、工具选择或上下文的候选先过冻结 greedy、工具调用、finish、代码修改和
  测试结果质量门。
- 默认路径保持兼容；新行为 opt-in、可关闭并有精确回滚。
- 重启前证明预期节省能覆盖约 2–3 分钟加载、cache 冷启动和中途不可用成本。

## 修订后的执行顺序

### 阶段 1：AGENT-END-TO-END-TRACE（已完成）

这是选择后续大头的必要信息，不修改模型默认行为，不重启服务。

目标：把真实 Codex root task 拆成可核对的时间账本，并明确哪些秒数属于 Strata。

至少输出：

- root task、thread、model turn 和工具调用数量；
- input、cached、uncached、output、reasoning token；
- 模型区间墙钟，以及工具调用到返回的覆盖墙钟；
- tool output 字符数、大输出数量和长等待数量；
- compaction、recovery、abort 和取消；
- 根任务墙钟中未被模型、工具或已知事件覆盖的空档；
- 每次调用的派生 token/秒或 token/调用比，但不把客户端跨度冒充 server timing。

安全边界：

- 不输出 prompt、assistant、reasoning 或工具正文；
- 只输出 ID、时间、类别、计数和派生值；
- 不把重叠工具时间直接相加成用户墙钟；
- 明确标记客户端估算、未知空档和缺少 server timing 的范围。

验收：

- 至少用 3 个真实 root task 和 1 个长会话复算；
- 根任务总时长守恒，分类总和与原始完成跨度误差有明确边界；
- 能回答模型、工具、compaction、空档各自占多少，以及 Strata 可优化上界；
- 有只读解析和合成回归测试，不依赖 GPU。

### 阶段 2A：AGENT-COMPACTION-REAL-SHAPE（已完成）

真实冻结会话中，compaction 占全部 uncached input `54.32%`，其推断模型跨度占总 root
墙钟 union `2.18%`。即使只消除一半，整会话上界也约为 `1.09%`。但同一会话的 43/44 次
compaction 缓存率低于 50%，与 180K 合成请求的 99.984% 明显不一致。

本阶段先做隐私安全的请求形状对齐：

- 不记录 prompt、assistant、reasoning 或工具正文；
- 记录 metadata 命中、工具来源、结构边界、token 前缀长度和缓存结果；
- 找出真实 compaction 与上一轮 prompt 的第一个结构分歧；
- 只有证明存在内容完全相同的可复用前缀，才实现 opt-in 布局候选；
- Python server 重载约 2–3 分钟，必须用真实频率证明节省能覆盖成本。

结果已推翻原来的 Strata 归因：冻结 rollout 的 `deepseek` provider 是 API 直连；B4
上的三次真实 Codex compaction 均复用约 `99.1%`。当前分支停止，不实现新布局、不重启。

### 阶段 2B：AGENT-TOOL-CONTEXT-DIET（已完成）

全部本地会话的工具输出中，`>=10K` 字符占 `63.985%`，`2K-10K` 再占 `28.309%`。这是除
模型 decode 外最大的可复核上下文成本，也是 compaction 大 prompt 的主要来源之一。

候选必须：

- opt-in、默认关闭，不静默修改服务收到的普通请求；
- 保留最近若干工具输出和所有错误输出；
- 把旧的大型成功输出保存为确定性可重读文件，只替换为有大小、哈希、路径和重读命令的引用；
- 不调用额外模型生成摘要；
- 用真实 coding task 验证代码修改、测试结果、错误定位和 finish；
- 降低冷 prompt/compaction uncached input，至少给出真实触发率后的整任务 1% 保守收益；
- 不把命令执行时间、工具轮询时间或用户空闲算作 Strata 节省。

结果：延迟的“保留最新六条、折叠更早输出”策略会在每轮改写早期历史，使缓存从 `25K+`
跌到 `4,546` token，前九请求总和 `116.177 s`，已否决。改为每个合格成功输出首次出现
即折叠；三组八命令配对墙钟分别为 `38.106→32.914 s`、`58.307→30.727 s`、
`41.002→32.686 s`，改善 `13.63%/47.30%/20.28%`。模型通过精确 `rg` 命令从引用找回
早期 needle；真实编辑与测试为 `2 passed` 并返回 `PASS`。能力保持 opt-in，生产服务、
配置和引擎均未改变、未重启。见
[`AGENT_TOOL_CONTEXT_DIET_RESULT-20261031.md`](../../work/AGENT_TOOL_CONTEXT_DIET_RESULT-20261031.md)。

**前缀稳定规则**：同一历史项的模型可见表示一经写入，后续轮次不得因“输出变旧”再次改写。
任何折叠、摘要或引用都必须在该项首次进入上下文时确定性完成；如果新策略需要逐轮移动
边界，必须先用同请求对照证明缓存不失效。

### 阶段 2C：AGENT-REAL-DECODE-SHAPE（已完成）

35 文件、152 root 的真实快照包含 `10,343,967` output 和 `3,347,547` reasoning token；
模型区间 union 为 `69,236.197 s`。先只读拆解，不直接改引擎：

- 按请求类型、输入尺寸、output/reasoning 尺寸统计 p50/p90/p99 和模型区间秒数；
- 区分短工具回复、正常代码编辑、长思考、compaction 和 recovery；
- 对 1/2 路真实尺寸记录 TTFT、单请求 wall、完成跨度和 finish；
- 用同模型、同采样和固定输出上限做 1/2 路 replay；
- 只有形成真实触发率后的整任务 `>=1%` 保守上界，且不改变代码、工具选择、测试结果和
  finish，才实现一个可回滚机制；
- 不把降低思考长度、静默截断输出或牺牲任务成功率算作性能收益。

结果：模型时间中 `69,064.894 s` 在单路、仅 `171.303 s` 在恰好两路；output
`512-2047` 占 38.231% 请求和 49.225% 模型时间，`2048+` 占 5.261% 请求和
22.828% 时间，reasoning `512+` 占 12.006% 请求和 31.910% 时间。416/1490/4483
token 的单路完成跨度为 `4.240/14.347/45.451 s`；两路为
`8.199/28.919/85.701 s`，第二请求 TTFT 接近第一请求整代。4483 的两路首波和次波
各有一条在 `3892` token 提前 `stop`，因此长输出完全稳定尚未证明。

同时发现现役引擎实际启用了 `STRATA_DECODE_TIMING=1` 和
`STRATA_VERIFY_PROFILE=1`，本阶段 replay 也是仪器开启数据。历史同机测量中
`STRATA_VERIFY_PROFILE` 使单路慢 6.4%、四路慢 10.7%；当前版本必须用同形状 A-B-A
确认，不能直接把历史数字当成当前收益。见
[`AGENT_REAL_DECODE_SHAPE_RESULT-20261031.md`](../../work/AGENT_REAL_DECODE_SHAPE_RESULT-20261031.md)。

### 阶段 2D：AGENT-PROFILE-HYGIENE（已完成）

生产仪器污染比继续写新内核更优先。固定当前模型、引擎、server、6 slots / 2 groups、
MTP 和三个真实 output 尺寸，先量 `STRATA_VERIFY_PROFILE=1` 与
`STRATA_DECODE_TIMING=1` 的现役成本：

- A1 为当前配置；B 只移除两个 profiling env；A2 恢复两者验证可逆；B 通过后以 B2
  留在生产；
- 每臂对 416/1490/4483 token 各跑 3 波单路，记录 wall、prompt、decode、TTFT、
  finish 和输出 hash；
- 至少一个主要尺寸中位改善 `>=1%`，4483 不回退超 1%，A2 漂移 `<=3%`；
- 三个尺寸的 finish/输出 hash 必须与对应 A 臂一致；4,483 的已知提前 `stop`
  仍是独立未决项，重复出现不得被吞成性能收益；
- 配置 delta 只能有两个 env，失败精确恢复，3 次重启预算必须计入。

结果：B2 已移除两个 profiling env 并留在生产。416/1490 的 A1/A2 漂移为
`2.45%/0.66%`，B 对相邻中位改善 `6.61%/5.38%`，均过 1%；4483 方向改善
12.03%，但 A1/A2 漂移 13.77%，不用于采纳结论。416/1490 的 finish/hash 完全一致。
4483 在 A2 和 B 都出现两条 `3892/stop`，不属于本次环境改动，但仍是独立质量风险。
最终 health loaded、6 slots / 2 groups、engine `0.1.51`，64-token smoke 成功。见
[`AGENT_PROFILE_HYGIENE_RESULT-20261031.md`](../../work/AGENT_PROFILE_HYGIENE_RESULT-20261031.md)。

### 阶段 2E：AGENT-DRAFT-BREAKDOWN（已完成，否决实现方向）

无仪器配置后，单路窗口仍有约 `2.36 ms` 的 draft，占 `42.7–45.5 ms/window` 的
5.2–5.5%。最新真实 trace 中模型 union 只占 root wall 37.5%，因此即使彻底去掉整个
draft，乐观 root 上界也只有约 2.1%；要过 1% 必须至少消掉约一半，或同时减少后续轮次。

执行了一次受控拆分，没有改默认：

- B2 上只临时增加了 `STRATA_DRAFT_TIMING=1` 与 `STRATA_MTP_PROFILE=1`；
- 用 416/1490/4483 token、各 3 波单路拆了 round graph、逐 step GPU、host staging、
  graph launch、host 决策间隙、PLE prefetch、首步等待和同步；
- 记录了 wall、prompt、decode、TTFT、finish/hash，完成后精确恢复了 B2；
- 只有在可移除组件真实触发后整任务保守上界 `>=1%`，且不改 draft token、
  verification、sampling 或 finish 时，才允许预注册实现；
- 实际未过门，因此关闭 MTP draft 微调，回到更大的真实任务地图；不再扫 depth、阈值、group
  或 cache 邻近参数。

结果：draft round 收敛到约 `2.463 ms`，其中 round graph `0.978 ms`、step GPU
`1.451 ms`、host graph gap 仅 `0.017 ms`，staging/PLE/launch 都远小于 0.1 ms。最大
单项是 draft-head GEMV，但即使完全消除 round 与 step 的 head，乐观 root 上界也只有
约 `0.8%`，未过整任务 1% 门。该方向关闭，不扫 depth、`spec-min-p`、group、cache
或同形 tree 变体。见
[`AGENT_DRAFT_BREAKDOWN_RESULT-20261031.md`](../../work/AGENT_DRAFT_BREAKDOWN_RESULT-20261031.md)。

### 阶段 2F：AGENT-OLD-SESSION-RESUME（已完成）

本地 35 个 Codex 会话没有任何超过 24 小时的本地请求间隔，因此旧 trace 不能代表
隔天恢复。使用 2026-09-12 的真实存储会话，在 2026-10-31 经 B4 隧道 resume：

- 会话年龄约 49 天，原始 cwd 为 `/home/kali/项目/st`；
- prompt/input `223,114` token，cached `0`；
- prompt/首 token 约 `112.863/113.014 s`，wall `113.3 s`；
- decode `292.8 ms`，输出 21 token，finish `stop`，无工具执行；
- 服务请求 id `4ec7f4d8c2f8`。

这确认隔天约 180K 级旧会话是真实的 p99 大头，且约 99.7% 的用户等待发生在首个 token
之前。它不是 compaction recovery，也不能由新合成 180K prompt 或压缩后立即回读替代。
见 [`AGENT_OLD_SESSION_RESUME_RESULT-20261031.md`](../../work/AGENT_OLD_SESSION_RESUME_RESULT-20261031.md)。

### 阶段 2G：AGENT-OLD-SESSION-PREFILL-PROFILE（已完成）

目标是在真实 223K 旧会话上只增加 `STRATA_PREFILL_TIMING=1`，取得 21 个 prefill 相、
host staging、主/对端 GPU 时间线和整请求 wall。当前项不先实现持久化或内核：

- 重启前保存 B2 config、process/env/hash；唯一环境 delta 是 prefill timing；
- 同会话只做一次冷 profile，因为同进程第二次会命中已填充的缓存；
- 记录 input/cached、TTFT、wall、decode、finish/hash、RAM/VRAM 和健康；
- 只有单相可保守节省至少 `1.13 s`，即真实请求 wall 的 1%，才允许预注册一个实现；
- 若没有相过门，关闭 prefill 内核方向，转向 state persistence、跨进程 cache residency、
  预取/预热和客户端历史处理的结构比较，不再扫描 kernel 邻近参数；
- 完成后恢复 B2，并确认两个 decode profiling env 仍不存在。

结果：默认 256 在途 direct 的 prompt 为 `135,073.4 ms`，A2 为
`132,645.8 ms`，漂移 `1.80%`。host PLE wait 为 `50,362 ms`，主 PLE phase 为
`39,291 ms`；`gemm gate/up`、`gemm down`、`qsa select`、`hc read` 和 `qsa attn`
分别为 `16,157/8,321/6,784/6,189/5,135 ms`。`--ple-io mmap` 把 wall 拉到
`182.157 s`，已否决。新增独立契约后，只把在途读提高到 1,024；profile prompt
降至 `128,303.5 ms`、host PLE 降至 `43,453 ms`、主 PLE phase 降至 `18,525 ms`。

最终非剖析三组相邻配对如下：

| pair | direct-256 prompt | 1,024 prompt | prompt gain | direct-256 wall | 1,024 wall | wall gain |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 123,571.7 ms | 120,024.4 ms | 2.87% | 130.198 s | 130.127 s | 0.05% |
| 2 | 130,616.7 ms | 129,175.0 ms | 1.10% | 137.576 s | 136.071 s | 1.09% |
| 3 | 123,543.4 ms | 116,645.9 ms | 5.58% | 130.352 s | 123.564 s | 5.21% |

所有臂均为 cached `0`、sentinel 正确、finish `stop`、零工具、零错误。客户端 wall 第一组
有约 3.5 秒启停离群，但服务端 prompt 和三组配对方向均一致。采纳为显式参数，源码默认
仍为 256；当前直接 slab 从约 2 MiB 增至 8 MiB。见
[`AGENT_PLE_INFLIGHT_RESULT-20261031.md`](../../work/AGENT_PLE_INFLIGHT_RESULT-20261031.md)。

### 阶段 2H：AGENT-PLE-ROW-CACHE（当前）

PLE 仍占冷 prefill 的主要可移除部分。源码默认缓存 1,048,576 行，IQ4_NL 每行 90 B，
连同 keys/replacement 约 95 MB；头文件在冻结语料上报告同一长 prompt 内 20-34% 行重复。
真实旧会话尚未验证扩大缓存是否继续有效。

当前只做一项有界实验：

- 基线是已采纳的 `--ple-inflight 1024`；
- 候选只增加 `--ple-row-cache 4194304`，增量约 270 MiB，总 row cache 约 360 MiB；
- 相邻 profile 臂必须同向改善 server prompt、host PLE 和 phase；
- 通过 profile 后再做至少两组非剖析相邻 A/B，所有 prompt 配对同向，配对中位至少 1%；
- 失败即恢复 1,024 配置并关闭行缓存容量路线，不扫描邻近值；
- 不组合 mmap、keep-alive、其他 cache、chunk 或 kernel 改动。

契约见
[`AGENT_PLE_ROW_CACHE_CONTRACT-20261031.md`](../../work/AGENT_PLE_ROW_CACHE_CONTRACT-20261031.md)。

### 阶段 2I：成本账本排序的其余候选

候选及触发条件：

1. `AGENT-HOT-CACHE-PATH`：热缓存恢复/固定成本占比高。拆 render、token cache、checkpoint
   restore、park、slot wait 和首 token；低于每轮约 100 ms 的机制通常不足以覆盖构建和
   重启成本。
2. `AGENT-TWO-STREAM-ROLE-AWARE`：两路串行把第二个请求 TTFT 推到 `4.7–16.4 s`。验证
   主代理+子代理、两个独立会话和错峰请求是否应采用不同准入；必须同时报告第二请求
   TTFT、首个可执行结果和总完成时间。完整 rollout 的恰好两路跨度只有 `171.303 s`，
   因此必须与更大项比较后再构建。
3. `AGENT-RECOVERY-AND-CANCEL`：recovery 或 abort 占比高。先统计真实频率和停滞秒数，
   再设计无进展提示、取消传播、stop/recover 策略。
4. `AGENT-HISTORY-STATE-RESIDENCY`：只有 prefill 相图无法给出 1% 机制时才恢复。比较
   内存 park、磁盘 session file、跨重启持久化、后台预取和客户端历史处理；必须覆盖
   过期、淘汰、重启、并行限制、RAM/VRAM 和约 3 GB 级 180K 状态成本。

### 阶段 3：真实任务 replay

候选通过请求级门后，至少 replay：

- 单会话多轮工具调用，包含短工具结果和实质正文；
- 一主一子代理并行，检查主线程首结果和第二条 TTFT；
- 峰值 1 主 3 子代理；
- 一次真实约 210K compaction；
- 一次隔天约 180K 旧会话恢复；
- 一次取消和一次 recovery 尾部。

主判据是根任务墙钟、首个可执行结果、任务成功率和资源安全。请求吞吐只用于解释原因。

### 阶段 4：闭环

每轮立即：

1. 写可验证结论、未知范围和不采纳理由；
2. 更新 `STATE`、`ROADMAP`、`WORK_QUEUE`、`ITERATION_LOG`、`EVIDENCE_LEDGER`、
   `HANDOVER` 和对应 contract/result；
3. 运行格式、UTF-8/BOM、offline preflight 和聚焦测试；
4. 提交并推送，核对远端 commit；
5. 从新成本地图选择下一项并继续，直到用户明确停止。

## 当前不做

- 不继续扫描已否决的 kernel、poll、head、LFUSE、MTP、EXPDEC、checkpoint move 和
  QSA 同形变体。
- 不把精确 pin / `cap=9` 自动启用；真实 compaction 后立即回读只有 0/109。
- 不把 180K 新合成 cold turn 当作隔天旧会话已覆盖。
- 不因一次 85 秒 recovery 样本改默认或重启。
- 不把 6/8 路合成峰值当主要成功标准。
- 不根据 token/s 单指标采纳候选。
- 不让 Strata 静默修改 prompt、截断工具结果或降低思考长度；相关能力必须 opt-in，并测
  代码修改和任务成功。
- 不在没有证据时重启服务器；每次重启成本必须计入当轮。

## 计划评审

结论：**第十二次修订通过，可以继续执行。**

通过条件：

- 全局成功指标从引擎吞吐改为用户任务时间、首个可执行结果和成功率；
- 1/2 路主优化、4 路非回退、6/8 安全回归；
- 旧会话恢复保留为 p99 必测，不再越过成本地图直接成为第一项；
- 真实 output/reasoning 分位进入优化依据，64-token 探针只代表短工具回复；
- 工具等待和大型输出进入任务成本模型，但明确标为 Strata 不能直接消灭的外部成本；
- recovery、取消和长任务错误止损进入正式路线；
- 已关闭的局部 kernel 路线不再占据主计划；
- 真实任务 trace 已成为选项目的入口，且工具、模型和空档口径分开；
- 合成 compaction 的成功不再被扩展成“真实 compaction 已解决”；
- 区分客户端 provider 与 Strata：DeepSeek 直连的低 compaction 命中不再计入 Strata 缺陷；
- 用 B4 上三次真实 Codex compaction 收口当前缓存路径；
- 用全部本地会话复算工具输出与并发频率，避免只看一个 29 小时文件；
- 将 `>=2K` 工具输出 `92.294%` 的上下文占比列为主要非 kernel 候选，并用真实 paired
  wave 从候选推进到 opt-in 工具；
- 明确否决延迟折叠，因为逐轮改写前缀破坏缓存；只允许首次出现时确定性折叠；
- 上下文饮食保持 opt-in，不把代理 pilot 扩写成服务默认路径已切换；
- 工具输出 token 占比、请求数、root 数与最新 35 文件快照分别标注来源，避免旧快照当当前；
- 两路角色调度降级，因为完整快照仅 `171.303 s` 模型区间处于恰好两路；
- 真实尺寸 replay 证明 aggregate decode 的计时重叠不是用户收益；
- 现役 profiling env 被识别为独立的高优先级配置噪声，先用 A-B-A 移除；
- 长输出 4,483 的一次提前 `stop` 保留为独立质量风险，不因 temperature 0 而静默忽略。
- profiling 移除已在两个稳定尺寸通过 1% 门并以 B2 留在生产；
- draft 拆分证明 host 可移除成本可忽略，head 全消除的 root 上界也只有约 0.8%，
  因而关闭 MTP 微调；
- 4483 的异常在 B 与 A2 都出现，但 A1/A2 漂移过大，故不用于加速归因。
- 真实旧会话不再缺测：49 天会话给出 `223,114/0` prompt/cache 和 `113.3 s` wall；
- 223K profile 已把直接 PLE I/O 选为第一机制；`mmap` 已否决；
- 1,024 在途读已按三组非剖析配对采纳，默认仍保持 256；
- 新的当前项只改变已有 row-cache 容量，不重置或扫描 inflight 参数；
- 低频旧会话按 p99 单独立项，门槛使用单次绝对节省 `>=1.13 s`，不伪装成总体 1%。

已知局限：

- 单个 Codex 会话不能代表所有用户；
- 当前客户端日志没有完整 Strata server `prompt_ms/decode_ms`，不能把 compaction
  失配直接归因给 Strata；
- 工具区间重叠、用户空闲和客户端组装时间需要在 trace 输出中明确区分；
- 已测一个真实 49 天旧会话的成本；其人口频率仍未测；
- 当前 replay 的 prompt 类只有一个短 warm prompt，不能推广到所有 vibe coding 请求；
- 4,483 长输出的跨 MTP/cache 状态逐字稳定性尚未解决。

## 当前断点

`AGENT-PROFILE-HYGIENE`、`AGENT-DRAFT-BREAKDOWN`、`AGENT-OLD-SESSION-RESUME`、
`AGENT-OLD-SESSION-PREFILL-PROFILE` 和 `AGENT-PLE-INFLIGHT` 均已完成。
真实 49 天旧会话的 223K 冷 prefill 被拆到 PLE I/O，`mmap` 否决，1,024 在途读以
三组非剖析配对采纳。当前执行 `AGENT-PLE-ROW-CACHE`：从该配置只增加
`--ple-row-cache 4194304`，验证重复行命中能否继续缩短 prefill。当前 B4
server/vision/engine `89451/89453/89458`，config
`6ece1a13f4ac2d875bdafd903c7aba23fdce6be5ccc387e17b987a130a486811`，
health loaded、6 slots / 2 groups；失败时恢复 direct-config
`4b396f44...`。
