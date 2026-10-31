# 真实 AI vibe coding 优化计划

日期：2026-10-31（2026-11-01 第三十六次修订）

状态：继续执行。当前断点从 DSH 标题调度转回已有正向证据的 resident direct-path。
本轮先做 48 层覆盖/fallback 审计；只有新的整任务保守收益至少 1% 时，才补 4/6/8
非回退和生产重启。标题延迟、标题输出上限和标题前缀 pin 已连续否决，不再换邻近参数。

## 全局目标

除非用户明确停止，持续提高真实 AI vibe coding 的任务成功率、指令遵循、工具协议和代码
正确性，再缩短任务从提交到可用代码、测试结果或明确阻塞的时间。用户最主要的痛点是慢；
1% 也是收益，但必须给出绝对秒数、真实触发率、质量边界和可回滚路径。

严重运行时问题有最高抢占权：新增 Xid、engine 非预期退出、静默错误或错误答案被当成
成功时，立即暂停性能实验、保存事故并优先处理。没有新事故时，不空等低频故障。

当前工作负载通常是 1 个主代理、偶尔 2 个，峰值 1 主 3 子代理。模型服务的 1/2 路是
主要场景；4 路不退化；6/8 只做安全回归。`2/4/6/8` 表示同时在途请求，不是会话数。

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

### `AGENT-HOT-TURN-RESIDENT-PATH-AUDIT`（恢复）

已有真实正向信号：

- group 级 native direct-pointer `7.848 ms/layer` 对 MMQ gather `11.671 ms/layer`，
  `1.49x`；
- 596 fresh-token 热长轮 A1/B/A2 wall 中位
  `1.522684/1.490017/1.513878 s`；
- B 相对 A1/A2 合并基线改善 `1.862%`、绝对 `32.667 ms`，三波全胜；
- 固定 greedy 逐字一致，finish 全为 `stop`；
- 候选默认关闭，当前生产未包含它。

之前因绝对 `0.05 s` 门暂停。用户已明确“1% 多也算”，所以本轮继续，但先回答：

1. 48 层中多少层实际进入 `fused_staged`；
2. 每层 fallback 的真实原因：pack 类型、非驻留 expert 数量、peer/CPU 分摊、段大小、
   graph 条件或其他；
3. 1.86% 与 group 级 12.1% 乐观上界之间，是否存在可安全覆盖的真实缺口；
4. 新缺口能否给出整请求至少 1%、绝对秒数明确的保守收益。

审计不改生产、不重启、不扫邻近 staging/cache 参数。若没有可覆盖缺口，关闭 resident
direct 路线；若有，预注册一个源码或接线候选，再补 4/6/8 非回退。

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
- 生产为手动实例，`strata.service` inactive；重启到 ready 约 3 分钟，必须证明收益能覆盖
  加载、缓存丢失和不可用成本。
- server/vision/engine：`232891/232893/232899`。
- server cwd：`/opt/ai/src/strata-two-stream-serial-20261031`。
- engine：`/opt/ai/src/strata-freeze-throughput-20261029T0812Z/build75/strata`。
- engine SHA-256：`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`。
- config SHA-256：`6ece1a13f4ac2d875bdafd903c7aba23fdce6be5ccc387e17b987a130a486811`。
- server SHA-256：`bd3e428932c0b9bb645b2ae9f37924b2e440b590333a0322cdd00cd28dd4ed82`。
- health loaded、262144 context、6 slots / 2 groups。
- server env 含 `STRATA_DEFER_TITLE_REQUESTS=1`、`STRATA_BATCH_SERIAL_TWO=1`、
  `STRATA_PIN_DSH_TOOL_PREFIX=1`。

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
- 流式代理必须逐块转发；不得先读完整 SSE 再冒充 TTFT。
- 辅助请求实验必须同时记录客户端请求到达、代理释放、上游开始、上游完成、turn 结束和
  客户端断开；只记录 task wall 不足以证明隔离成功。
- 标题实验必须记录 provider 完成、输出 token、cached token、客户端状态，以及 title
  区间与每个主 step 的重叠。
- 多步 A/B 必须配对并有足够重复；工具数、模型输出量和质量不同时，不能把 wall 差归因给
  候选。
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

**2026-11-01 第三十六次修订。** 标题调度方向已关闭；生产仍为
`232891/232893/232899`，环境、health、6 slots / 2 groups 未变，无新 Xid。当前主动项
恢复为 `AGENT-HOT-TURN-RESIDENT-PATH-AUDIT`：先只读统计 48 层 direct-path 覆盖和
fallback。没有新的整请求 1% 缺口就关闭该路线；有则预注册 4/6/8 验证，再决定是否值得
重启生产。

## 每轮强制复盘

形成判决和选择下一项前必须回答：

1. 是否命中最高频的用户等待，而不是低频局部现象？
2. 是否改善真实 vibe coding 编辑—测试路径？
3. 预计收益是否至少 1%，绝对节省多少秒，触发频率依据是什么？
4. 是否仍在局部参数或已否决机制附近打转？
5. 为什么高于另外两个候选？
6. 重启成本是否值得，能否在同一 boot 内完成？
