# 真实 AI vibe coding 优化计划

> 归档：2026-11-01。用户明确要求停止优化计划并维护稳定；本文件只保留历史目标、
> 已完成收益、否决方向和接手证据。当前没有主动性能实验，生产断点以 `STATE.md` 为准。

日期：2026-10-31（2026-11-01 第四十五次修订）

状态：已停止并归档。计划停止点原为 `AGENT-CURRENT-PROD-COST-REMAP`；用户决定不再
继续长输出 decode 和局部 1% 优化，生产保持现有已验收配置。只有用户重新授权，或出现
新的生产故障时才恢复计划。`GPU-XID13-STABILITY` 仍在维护稳定时被动监测；HC_SPLIT
不是硬件修复。

## 全局目标

除非用户明确停止，持续提高真实 AI vibe coding 的任务成功率、指令遵循、工具协议和代码
正确性，再缩短任务从提交到可用代码、测试结果或明确阻塞的时间。用户最主要的痛点是慢；
1% 也是收益，但必须给出绝对秒数、真实触发率、质量边界和可回滚路径。

严重运行时问题有最高抢占权：新增 Xid、engine 非预期退出、静默错误或错误答案被当成
成功时，立即暂停性能实验、保存事故并优先处理。没有新事故时，不空等低频故障。

当前工作负载通常是 1 个主代理、偶尔 2 个，峰值 1 主 3 子代理。模型服务的 1/2 路是
主要场景；4 路不退化；6/8 只做安全回归。`2/4/6/8` 表示同时在途请求，不是会话数。

每轮选择候选时先回答“它是否改善整任务最大可改成本”。源码路径、隔离实例、微基准或
单层机制只能提供方向；没有现役 batch/slot、layer split、真实 DSH 触发和保守绝对秒数，
不得进入生产矩阵。已经证明不可达的相邻参数不重扫。

## 当前结论

### 已采纳并保持

- `DSH-TITLE-INTERFERENCE`：精确标题请求等当前主请求释放。生产单会话主 turn
  `19.629→16.078 s`（`18.091%`），双会话 fail-open。
- `DSH-FIRST-TURN-PREFIX-REUSE`：精确 DSH system+tools 前缀 pin。稳态 task 中位
  `11.3715→7.3165 s`（`35.66%`、绝对 `4.055 s`），首步 cached `582→5,463`。
- `UTIL-TWO-STREAM-SERIAL`：恰好两路串行走 solo，第三条恢复 batch。512-token 两路完成
  跨度改善 `15.05%`；第二条 TTFT 约 `5.1 s` 是已接受取舍。
- PLE `--ple-inflight 1024`：真实 223K 旧会话三组配对的 prompt/wall 中位改善
  `2.87%/1.09%`。
- 生产 decode 剖析器已移除；416/1490 token 的 decode wall 改善 `6.61%/5.38%`。
- `AGENT-BATCH-PREFILL-CPU-SHARE-GUARD` 部署为 opt-in：生产选项 1/2 路 hot-short
  wall 改善 `11.825%/7.896%`，hot-long `10.991%/8.056%`，均 `3/3` 波胜；质量
  B0/B1 均 `7/7`，6/8 安全通过。
- `AGENT-PROMPT-STAGING-CONCURRENT` 已部署：冻结约 154K cold prompt 下，两个解码
  邻居最长静默 `82.059→1.713 s`，整场景 wall `104.843→83.118 s`，joiner TTFT
  `84.058→72.384 s`；4 路 5 波 `14.0→13.1 s`。

### 当前成本结构

真实 DSH 7 会话的 runner wall 为 `197.776 s`，模型 step `184.332 s`（`93.202%`），
工具区间并集 `10.505 s`（`5.312%`）。40/40 个主 step 都成功关联到服务端；7 个额外
请求正好是每个新会话一次的标题。

普通 DSH step 的首 token 只比 engine prompt 多约 `25–26 ms`；生产 tokenizer/template
在 6K/94K/189K 下的热 host prepare 中位为 `6.439/11.527/18.345 ms`。host 请求路径
低于整任务 1%，已判 null。

标题仍是新会话中可观的并发源：旧样本中位 `9.8 s`、约 118 output token。现有标题门
只等首个主请求，标题会与 step 2（有时与 step 3）重叠。这个问题没有安全的“延后到 turn
结束后”版本：DSH 在最终主回复完成时结束 turn，并取消仍在等待的标题客户端。

### 标题方向已关闭

| 实验 | 结果 | 判决 |
|---|---|---|
| end-of-turn gate | 标题等待 `9.421 s` 后客户端取消，代理记录 `BrokenPipeError/502`；即使提前 SSE + 心跳，DSH 仍在 turn 结束时关闭标题 | 否决：生命周期晚于客户端截止 |
| 标题上限 `128` | provider 完成、输出 `89` token、耗时 `4.679 s`；跨 step 2/3，任务不更快 | 否决 |
| 标题上限 `64` | provider 完成、输出 `56` token、耗时 `4.324 s`；仍跨后续主 step | 否决 |
| 标题前缀 `messages=1` | 连续两次 title `cached=0`、耗时 `4.401/4.674 s` | 否决：没有命中 |

不能把这些单次 wall 当作加速结论；拒绝依据是标题请求仍跨主 step，或客户端被取消。
继续围绕标题只会进入局部参数循环。若要根本解决，需要 DSH 改变标题所有权，让标题在
主 turn 结束后由独立生命周期生成；Strata 服务端不能伪造标题或安全关闭该请求。

## 当前主动项

### `GPU-XID13-STABILITY`（被动监测；新事故抢占）

最新严重会话共 5 步，`task_wall_s=36.31 s`，成功 4 步模型墙 `32.853 s`；第 5 步
等待 `2.296 s` 后以 error 结束。此前的成本探针只统计成功
`assistant/message`，把它误报成 4 步；现已把 `assistant/attempt` 计入模型墙，并只保留
finish kind/error code，不保留正文。专项测试 `9 passed`。

已完成第一步：

1. 保持 engine/config/Layer split/CPU-share 不变，只增加 `STRATA_HC_SPLIT=1`；
2. `/proc/295955/environ` 已确认变量，health 为 loaded、262144、6 slots / 2 groups；
3. 两张卡的 engine 日志都出现 split 路径和逐位自检；启动到 ready 约 `138 s`；
4. 1/2 路各 3 波共 27 请求，零 HTTP/执行错误、零外来请求、期望回复 `27/27`、
   finish 全 `stop`；
5. 相对旧生产 B1，六种形状变化为
   `+0.048%/+0.897%/+0.163%/+0.889%/+0.124%/-0.057%`，均在约 1% 噪声内。

P0 的有限退出条件：

1. 完整 7 项 coding quality fixture 与旧生产身份对照，不得出现候选回归；
2. 1/2 路 cold/hot 均无超过 1% 的明确回退；
3. 4 路不回退超过 1%，6/8 零错误且资源安全；
4. 至少 60 分钟、覆盖用户实际的 1/2 会话编辑—测试路径和长输出，窗口内无新 Xid；
5. 达到以上条件后标记为“风险缓解候选通过有限窗口”，恢复
   `AGENT-CURRENT-PROD-COST-REMAP`，继续被动监测，不改写为“GPU 已修复”；
6. 未达到则恢复旧 config；若出现新 Xid，再单独测可回滚显存降频，最后才隔离 GPU0。
   不围绕 HC_SPLIT 邻近开关无限试参。

### `AGENT-CURRENT-PROD-COST-REMAP`（当前主动项）

真实 rollout 中 `512-2047` 与 `2048+` output 请求合计占 `43.492%`，模型跨度占
`72.053%`；普通 hot 短步之外，这类 decode 是下一性能大头。用户明确说“鹈鹕那个不管了”，
所以那个 `15,036` output-token / `173.323 s` 会话只作异常值，不能作为代表性验收。现在
从真实 `512+` 输出分布继续，不重跑 CPU-share、cold prompt gate、head/group 或 host
邻域。

## 质量、可靠性与长上下文

- DSH Web 当前走 Qwen/Strata relay；同一路径 6 个基础 fixture 代码/测试 `6/6`，
  明确最终 assistant message 契约后严格 sentinel `3/6→6/6`。
- GPU0 低频 Xid 13/43 仍是 P0 风险。原请求约 60 次重放未复现；固定 prompt 不能证明
  硬件稳定性。每个真实窗口从 UTC 起点读取新增 NVRM Xid，并按 PCI、5 秒窗口聚簇。
- 用户所说“180K 旧会话”是放置超过一天后重新打开约 180K token 的会话。真实 49 天
  223K 样本 cached `0`、TTFT `113.014 s`，是低频 p99，不挤占普通热续轮。
- 210K compaction 已高度缓存；真实 Codex compaction 复用约 `99.1%`。compaction 后立即
  回读旧分支在 109 次中触发 0 次，只保留低频诊断。

## 硬件与生产

- 两张 RTX 2080 Ti 的算力解锁已固定：cuBLAS FP16 从 `62.40 TFLOPS` 提升到
  `93.69/94.07 TFLOPS`，当前 boot 两份日志均为 `unlocked=1`。
- 生产由 `strata.service` 管理，稳定 launcher/config 位于
  `/opt/ai/bin/run-strata-production.sh` 和 `/opt/ai/etc/strata-production.json`；
  unit 为 `Restart=always`、15 秒、无限重试。重启到 ready 约 3 分钟，必须证明收益能
  覆盖加载、缓存丢失和不可用成本。
- 当前生产为 service/engine `41769/41789`、端口 `8080`。
- A1 恢复身份：server/vision/engine `254299/254303/254308`，端口 `8080`。
- server cwd：`/opt/ai/src/strata-two-stream-serial-20261031`。
- engine：`/opt/ai/src/strata-batch-prefill-guard-20261101/build-guard-prod/strata`；
  候选同 hash 构建位于 `build-prompt-concurrent-prod/strata`。
- engine SHA-256：`e1208e5166b11b541fe110f3ed99c140da20718e981621c9849a5917b582e707`。
- 当前 config SHA-256：`d76772e7089c18af38c0a8932588d99674a8b6bd8208f386b6c8599fd81c863a`。
- 回滚 config SHA-256：`2c4c7ac6c346c7b64d162502080fc3147b5da232de156a20a9d1db81b521985e`。
- A1 回滚 engine/config：`105a4171.../6ece1a13...`。
- server SHA-256：`bd3e428932c0b9bb645b2ae9f37924b2e440b590333a0322cdd00cd28dd4ed82`。
- health loaded、262144 context、6 slots / 2 groups。
- 实测 server env 为 `STRATA_ADMIT_SHORT_FIRST=2048`、
  `STRATA_DEFER_TITLE_REQUESTS=1`、`STRATA_MP_STAGED_READ_MIN_TOKENS=2048`、
  `STRATA_PIN_DSH_TOOL_PREFIX=1`。engine env 含 `STRATA_BATCH_SERIAL_TWO=1` 与三个
  CPU-share 开关、`STRATA_HC_SPLIT=1`、`STRATA_PIPED_CONCURRENT=1`，不含 debug。

## 优先级

1. 正确、安全、可恢复；GPU/运行时事故和错误结果优先。
2. 普通单代理编辑—测试循环中的模型等待。
3. 首次代码修改、首个工具结果和首次测试结果时间。
4. 真实根任务 p50/p75/p90，以及重复读取、重复测试和纠错轮次。
5. 隔天旧会话、长上下文 compaction、取消和长输出尾部。
6. 1 主 3 子代理的角色场景。

token/s 和资源利用率只用于解释等待，不替代用户任务时间。没有进入用户实际路径的能力
不算产品收益。

## 验收门

普通候选必须满足：

- 真实触发率、单次绝对秒数和保守整任务收益至少 1%；
- 1 路不退化；2 路同时报告第二请求 TTFT、单请求 wall 和完成跨度；
- 4 路不回退超过 1%；6/8 零错误、health loaded、资源安全；
- 输出、finish、工具调用、代码修改和测试结果全过；
- 新能力默认关闭、可精确回滚；未采纳时默认路径保持不变；
- 客户端 wall、server timing、静态阅读和推断值分开标注。

会写持久 cache checkpoint 的实验不能用未验证的同 boot ABBA。先用独立 boot，或明确写成
不可逆顺序 A→B 并单列冷启动。任何新 Xid 立即作废性能结论。

## 测试方法

- 主任务使用首个 `turn/start` 到最后一个 `turn/end` 的 `task_wall_s`；runner 进程 wall
  只作次指标，并注明包含 turn 后辅助工作。
- 路径审计、机制归因和端到端性能是三种不同证据。审计二进制只回答路径/fallback，不给
  加速判决；性能主比较必须在同一源码、同一构建选项和同一 config 内完成。
- 候选动手前先做生产可达性门：核对真实进程参数和代码控制流。`env` 存在、源码有同名
  分支或隔离实例能启动，都不等于现役生产能走到该分支。
- 热轮三臂比较必须记录相同波数并要求全部波齐全；候选至少在三分之二的波胜出，必须同时
  达到配置的百分比与绝对 wall 门。普通 1/2 路 cold、hot-short、hot-long 都不得回退超过
  同一百分比门；缺波、臂名错误、外来请求、错误 finish 或期望回复不符直接失败。
- batch prefill lease 必须单独测“async pool call 在 lease 持有期间完成”；lease 持有
  派发锁再等待 `cpu_fut` 是死锁，不是保守实现。默认关闭 guard 时 decode window 必须
  不计数、`run*` 不加锁，这一默认路径要与候选保护路径分开记录。
- resident 路线的正确比较顺序是：审计源码证明真实层覆盖；同源码 `fused_staged` 基线
  B0；只增加批间流水开关的 B1；生产臂 A 只用于回归和外部有效性。禁止把“B1 对旧生产
  A”的差值全部归因给批间流水，也禁止把候选整体相对 A 的 1.86% 当成流水增量。
- 逐层审计至少记录 `layer/T/use_mmq/native/native_supported/pp/cpu/peer/sort/routed/
  staged/fused_staged`；必须覆盖 cold、hot-short、hot-long 三种真实 chunk 形状，并核对
  48 层都有记录。只有一次性聚合日志不算覆盖证据。
- 批次流水候选在实现前必须证明：copy 事件仍按实际发出顺序等待；ring slot 不会在后批
  compute 前复用；最后一个 fused launch 后才记录释放事件；固定 greedy 与基线逐字一致。
- 共享 engine 日志必须按远端保存的字节偏移截取，记录实际大小和 SHA-256；截取后核对
  文件只含本臂窗口。`not shared` 不能被宽松的 ` shared,` 正则误算。热轮 comparator
  必须显式参数化 baseline/candidate/control 和 gain/absolute thresholds。
- 流式代理必须逐块转发；不得先读完整 SSE 再冒充 TTFT。
- 辅助请求实验必须同时记录客户端请求到达、代理释放、上游开始、上游完成、turn 结束和
  客户端断开；只记录 task wall 不足以证明隔离成功。
- 标题实验必须记录 provider 完成、输出 token、cached token、客户端状态，以及 title
  区间与每个主 step 的重叠。
- 多步 A/B 必须配对并有足够重复；工具数、模型输出量和质量不同时，不能把 wall 差归因给
  候选。
- 质量 fixture 的最终 assistant 消息必须只含 `RESULT=PASS`。若实现/测试正确但消息是
  “摘要 + sentinel”，应修测试提示词并记录 fixture hash，不能把它判成代码质量回退；
  新 suite 必须让 B0/B1 使用同一哈希重新对照。
- 质量报告必须记录并比较 runner/profile、模型、reasoning、模型目录 SHA-256、fixture
  suite SHA-256、prompt suffix SHA-256 和 provider URL。B0→B1 候选从通过变失败即否决；
  双方共享失败只能证明“不是候选回归”，结论必须是阻断采纳，不能写成质量通过。
- 质量全过后才跑 6/8 安全回归；6/8 只作零错误、health、资源与 Xid 检查，不用单次
  吞吐波动替代质量门。并发输出哈希只用于诊断；报告必须如实保留一个或多个变体，不能
  在没有逐行核对时写“哈希一致”。固定 greedy 逐字 parity 属于成对正确性实验。
- 任何被拒代码在文档判决后从工作树移除；测试工装只保留能服务后续真实判定的部分。

## 已否决方向（不要重复）

- 通用关闭标题；长任务 `0/2` 胜。
- end-of-turn 标题延迟、128/64 输出上限、标题固定前缀 pin。
- 标题之后继续尝试邻近等待时间、输出额度或提示词微调。
- host JSON、Responses 转换、模板、PromptEncoder、assembler 微优化；总成本为毫秒级。
- `short_read=64→0`；B0 无胜场，短热续反而慢。
- 重复测试缓存/跳过；成功或失败后立即无编辑重复均为 0。
- streamed expert ring、staged lookahead 8→15、GDN conv 融合、官方 `0.1.55` 版本替换。
- 已否决的 head split/fusion、跨 stage fairness、LFUSE、checkpoint move、固定 groups、
  piped MTP 同形组合。除非出现新的真实触发证据，不重开。

## 当前断点

**2026-11-01 第四十五次修订。** 冷 prompt 并发读修复已部署并完成冻结负载、正确性、
4 路和健康/Xid 门，收益远超 1%。当前不再围绕该 gate 或 HC_SPLIT 邻近参数打转；主动项
恢复为成本重排，从真实 `512+` 输出的 decode 分布继续。用户明确忽略的鹈鹕 15K 会话
只作异常值。HC_SPLIT 与 GPU Xid 保持被动监测，新事故抢占。

## 每轮强制复盘

形成判决和选择下一项前必须回答：

1. 是否命中最高频的用户等待，而不是低频局部现象？
2. 是否改善真实 vibe coding 编辑—测试路径？
3. 预计收益是否至少 1%，绝对节省多少秒，触发频率依据是什么？
4. 是否仍在局部参数或已否决机制附近打转？
5. 为什么高于另外两个候选？
6. 重启成本是否值得，能否在同一 boot 内完成？
