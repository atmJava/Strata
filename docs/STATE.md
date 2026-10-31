# 当前状态

> 接手顺序：`RULES.md` → 本文件 → `ROADMAP.md` → `ITERATION_LOG.md` 最近两条。
> 当前断点写在前；后面的“近期判决索引”只用于防重复，不驱动当前任务。完整证据见
> `WORK_QUEUE.md` 和 `EVIDENCE_LEDGER.md`。

- 全局目标：针对真实 AI vibe coding 负载，同时提高任务成功率、指令遵循、工具协议和
  代码正确性，再缩短任务完成时间、首个可执行结果时间和严重尾部。用户通常 1 个主代理、
  偶尔 2 个、峰值 1 主 3 子代理；1/2 路是主要性能场景，4 路非回退，6/8 只做安全回归。
- **继续执行**：用户明确要求继续计划。旧的 resident-path 局部性能项降为暂停历史候选；
  当前先把“速度快但质量感觉下降”拆成可验证的客户端工具面、模型/采样和真实任务成功问题。
- 当前项：AGENT-TERMINAL-QUALITY-COMPARISON
- 当前诊断：模型目录缺失会让 Codex 从 14 个工具降到 9 个并省略
  `apply_patch`；服务日志累计记录 6 次模型写未声明 `apply_patch` 后退回文本。目录供给
  已产品化为 `strata codex catalog [--apply]`，幂等、先备份、模板缺 freeform
  `apply_patch` 时拒绝写入。Schema 3 的当前 Qwen/Strata 结果是 6 个基础 fixture
  严格 `5/6`、测试 `6/6`，`apply_patch` 独立重复 `3/3`；唯一基础失败是代码/测试正确、
  最终回复多写了说明后再给 sentinel。DSH/DeepSeek 直连在同一 Schema 3 为 `6/6`，
  但模型、provider 和 hosting 同时不同，不能归因 Strata 引擎。下一步把 DSH 作为正式
  终端对照，消除报告中的模型标签歧义，并检查 DSH 能否经 Strata endpoint 运行。
  见 [结果](work/AGENT_CODEX_MODEL_CATALOG_QUALITY_RESULT-20261101.md)。
- 总计划：[真实 Agent 负载优化计划](superpowers/plans/2026-10-31-agent-workload-throughput.md)
  已完成第二十五次修订并继续执行。质量、任务成功率和工具协议是硬门；局部优化必须先给出
  真实触发率后的整任务 1% 上界；低频但
  单次数十秒的旧会话恢复可按 p90/p99 用户体验单独立项。没有接入用户实际路径的能力
  只算机制收益，不能写成体验已改善；重启成本必须计入当轮。
  平台内的旧“2/4/6/8 路引擎”目标仍为 paused。第二十五版更新后仍受旧 unfinished goal
  限制，当前工具不能改写目标文本或恢复它。本仓库计划是当前执行的权威目标，不能把平台
  旧目标当作已更新。
- 当前阶段：先收口 Codex/DSH 终端工具面和代表性质量回归，再恢复速度优化。此前真实
  Agent 请求级优化已采纳 title、线程级 compaction LRU（在对应测试请求
  形状上有效）、共享 system 前缀顺序、自动 boundary pin 和两路串行准入。B4 后三个兄弟
  请求复用 `97.34%`，后三墙钟 `5.001 → 1.335 s`（`-73.31%`）。
- 当前已采纳：`UTIL-TWO-STREAM-SERIAL`。恰好两路时第一条保持 solo、第二条随后也走
  solo；第三条出现后恢复生产 batch。512-token 两路完成跨度 `11.776 → 10.004 s`
  （`-15.05%`），64-token 两路 `1.692 → 1.554 s`（`-8.18%`）；1 路相对 A1/A2
  合并中位 `-0.38%`，4 路吞吐/完成时间不退，6 路 `163.07 tok/s` 安全。第二条请求
  TTFT 约 `5.1 s` 是已接受的取舍。见 L-134。
- 上一项已完成：`AGENT-TOOL-CONTEXT-DIET`。延迟折叠旧输出会逐轮改写前缀，缓存从
  `25K+` 跌到 `4,546` token，前九请求总和 `116.177 s`，因此否决。改为“大型成功输出
  首次出现即保存完整文件并替换为稳定引用”，三组八命令配对墙钟
  `38.106→32.914 s`、`58.307→30.727 s`、`41.002→32.686 s`，分别提速
  `13.63%/47.30%/20.28%`；早期 needle 可从引用精确找回，真实编辑/测试为 `2 passed`
  并返回 `PASS`。候选保持 opt-in，生产 server 未改、未重启。见
  [AGENT_TOOL_CONTEXT_DIET_RESULT](work/AGENT_TOOL_CONTEXT_DIET_RESULT-20261031.md)。
- 上一项已完成：AGENT-REAL-DECODE-SHAPE 最新只读 trace 覆盖 35 个文件、152 root、
  14,768 模型请求、10,343,967 output 和 3,347,547 reasoning token；单路模型 union
  `69,064.894 s`，恰好两路仅 `171.303 s`，root wall union `184,604 s`。
  416/1490/4483 token 的单路完成跨度为 `4.240/14.347/45.451 s`，两路为
  `8.199/28.919/85.701 s`；两路约等于串行翻倍，第二请求 TTFT 接近第一请求整代。
  4483 两路里出现两条 `3892/stop`，长输出完全稳定仍未证明。见
  [AGENT_REAL_DECODE_SHAPE_RESULT](work/AGENT_REAL_DECODE_SHAPE_RESULT-20261031.md)。
- 上一项已完成：AGENT-PROFILE-HYGIENE。现役 engine 去除
  `STRATA_DECODE_TIMING` 与 `STRATA_VERIFY_PROFILE` 后，416/1490/4483 的 B 中位为
  `3.911/13.620/37.230 s`；416/1490 相对 A1/A2 相邻中位改善
  `6.61%/5.38%`，A2 漂移 `2.45%/0.66%`。4483 改善 12.03%，但 A1/A2 漂移
  13.77%，不用于加速归因；B 与 A2 都有两条 `3892/stop`，不是 B 引入但仍为质量风险。
  B2 已在生产，health loaded、6 slots / 2 groups、64-token smoke 通过。见
  [AGENT_PROFILE_HYGIENE_RESULT](work/AGENT_PROFILE_HYGIENE_RESULT-20261031.md)。
- 上一项已完成：`AGENT-DRAFT-BREAKDOWN`。draft round 收敛到 `2.463 ms`，host graph
  gap 仅 `0.017 ms`；最大项 draft-head GEMV 即使全部消除，乐观 root 上界也只有约
  `0.8%`，低于 1% 门。该方向关闭，不扫 depth、`spec-min-p`、group、cache 或同形变体。
  见 [result](work/AGENT_DRAFT_BREAKDOWN_RESULT-20261031.md)。
- 已完成：AGENT-OLD-SESSION-PREFILL-PROFILE。真实 49 天旧会话的默认 256 在途 direct
  profile 为 prompt `135,073.4 ms`、A2 `132,645.8 ms`（漂移 1.80%），host PLE
  wait `50,362 ms`，主 PLE phase `39,291 ms`；`--ple-io mmap` 慢到 wall
  `182.157 s`，否决。下一步只测已有读取队列深度。
- 已采纳：AGENT-PLE-INFLIGHT。三组非剖析相邻配对的服务端 prompt
  `123,571.7→120,024.4 ms`、`130,616.7→129,175.0 ms`、
  `123,543.4→116,645.9 ms`，改善 `2.87%/1.10%/5.58%`；客户端 wall 改善
  `0.05%/1.09%/5.21%`。配对中位为 prompt `2.87%`、wall `1.09%`。所有请求 cached
  `0`、sentinel/finish 正确、零工具、零错误。`--ple-inflight 1024` 作为显式配置保留，
  engine 默认仍为 256。见
  [result](work/AGENT_PLE_INFLIGHT_RESULT-20261031.md)。
- 上一项已否决：`AGENT-PLE-ROW-CACHE`。4M 行 profile 的 prompt 为
  `136,549.2 ms` 对 `117,477.3 ms`，慢 `16.23%`；host PLE `51,905 ms` 对
  `32,341 ms`，慢 `60.49%`；PLE phase 慢 `24.07%`。按预注册门恢复 1,024-inflight，
  不扫邻近容量。见 [result](work/AGENT_PLE_ROW_CACHE_RESULT-20261031.md)。
- 上一项否决：`AGENT-CONTEXT-DIET-REAL-YIELD`。覆盖率审计过门后，三组真实 Codex
  编辑—测试配对的 diet wall 慢 `17.365%/86.935%/41.276%`，中位慢 `41.276%`；input
  中位多 `38.190%`，两组请求增加、三组工具调用增加。六臂最终测试和 artifact 恢复正确，
  但用户路径成本明确反向，因此不产品化。见
  [result](work/AGENT_CONTEXT_DIET_REAL_YIELD_RESULT-20261031.md)。
- 上一项否决：AGENT-HOT-TURN-FIXED-COST。A64/B0/A64r 各 63 请求，B0 没有热续轮
  胜场；1 路短热续 prompt/wall 为 `520.8 ms/0.643 s` 对 `458.0 ms/0.596 s`，
  慢 `13.75%/8.14%`；1 路长热续慢 `3.96%/3.55%`，2/4 路同样反向。保留默认
  `short_read=64`，不扫描邻近阈值。A64r 已恢复，见
  [result](work/AGENT_HOT_TURN_FIXED_COST_RESULT-20261031.md)。
- 上一项完成：`AGENT-EDIT-TEST-LOOP-TRACE`。36 文件、170 root、34 thread、22,749
  工具、2,318 编辑、576 测试中，只有 19/170 root 同时含编辑和测试。首修改前模型/工具
  为 `3,235.782/6,365.546 s`，首修改到首测试为 `4,072.900/8,241.516 s`。178 次测试
  命令签名重复中 173 次之间有新编辑，无编辑重复只有 5 次且状态不可判定，成功/失败后
  立即重复均为 0。重复测试、测试缓存和自动跳过否决。见
  [AGENT_EDIT_TEST_LOOP_RESULT](work/AGENT_EDIT_TEST_LOOP_RESULT-20261031.md)。
- 上一项已否决：AGENT-SPLIT-MTP-BATCH。启动日志真实命中末级 batched draft K/V；
  94K 热续 prompt `1271.5 → 1241.5 ms`（改善 `2.359%`），但 wall
  `1.433790 → 1.434450 s`（反向 `0.046%`），远低于预注册 `0.05 s` 绝对门。
  按停止门未跑 189K 和 2/4/6/8；生产已恢复 A2，不保留该 env。见
  [AGENT_SPLIT_MTP_BATCH_RESULT](work/AGENT_SPLIT_MTP_BATCH_RESULT-20261031.md)。
- 上一项已否决：ENGINE-0.1.55-EVAL。官方 CLI 不接受本地 `--prompt-cache-long`；移除该
  128K-only 选项后，官方 `0.1.55` 成功启动且 parity/smoke 全过，但 596/94K
  hot-long wall 相对 A1/A2 参考中位慢 `5.853%/4.259%`，绝对慢
  `0.087835/0.059525 s`。A1/A2 漂移均低于 3%，版本级候选否决并恢复旧引擎。见
  [ENGINE_0155_EVAL_RESULT](work/ENGINE_0155_EVAL_RESULT-20261031.md)。
- 上一项否决：`GDN-CONVL2`。B 通过了实际 env、6/2、health、三组固定 greedy、smoke
  和全部请求质量门；A2 prompt/wall 漂移 596 `-0.2483%/-0.3875%`、94K
  `+0.2461%/+0.0359%`。但 B 相对 A1 的 596/94K wall 只改善
  `0.8245%/0.2051%`，绝对 `0.012476/0.002898 s`，未达 `1%` 且 `0.05 s` 双门。
  生产已恢复原 engine/config，不跑 189K、并发矩阵或邻近 GDN 参数。见
  [GDN_CONVL2_RESULT](work/GDN_CONVL2_RESULT-20261031.md)。
- 上一项完成：`AGENT-HOT-TURN-PHASE-MAP`。同口径 596/94K 热长轮实际 fresh 读取都约
  554 token；stage 1 的 GPU timeline/host staging 中位为 `459/501 ms`，stage 2 为
  `647/788 ms`。stage 2 最大 phase 为 `wait copy 173 ms`，随后
  `gemm gate/up 130 ms`、`dequant 106 ms`、`gemm down 68 ms`。profiler 请求
  零错误、parity 全过，生产已恢复且无 `STRATA_PREFILL_TIMING`。见
  [契约](work/AGENT_HOT_TURN_PHASE_MAP_CONTRACT-20261031.md)。
- 上一项否决：`AGENT-HOT-TURN-STREAM-RING`。B 只设置
  `STRATA_PREFILL_STREAM_MIN=0`，真实命中 full streamed ring，并通过实际 env、
  六 slots / 两 groups、health、资源、三组 fixed greedy 和 smoke。但 596 hot-long
  wall 从 A1 中位 `1.509808 s` 升到 `1.898041 s`，慢 `25.714%`，绝对多
  `0.388233 s`；prompt 中位从 `1401.7 ms` 升到 `1787.9 ms`。full ring 按全部专家
  顺序建立 whole-chunk stream，并同时关闭 staged 路径的 shared-expert defer、改用
  cross-layer issuer；该复合分支不适合本形状，但不能把结果单独归因给 ring 深度。
  554 x top-10 实际有 `5,540` 个 routed pairs、每层 512 个专家，不是少量专家。
  按停止门未跑 94K、2 路或并发矩阵；生产已恢复 A2。见
  [result](work/AGENT_HOT_TURN_STREAM_RING_RESULT-20261031.md)。
- 已完成：`AGENT-HOT-TURN-ROUTING-PREFETCH-AUDIT`。生产 staged walk 在每层 router
  完成后读取该层 top-k，按实际选中专家建序，已经只拷贝本层 routed、非驻留专家；
  后续层 routing 依赖当前层结果，不能提前获知。源码没有跨层 speculative routing，
  因此该方向关闭。当前 grouped staged path 实际只保持 8 个传输在前，而不是 15 个位置。
- 上一项否决：`AGENT-HOT-TURN-STAGED-PREFETCH`。B 只增加
  `STRATA_PREFILL_STAGED_AHEAD=15`，路径、实际 env、质量、health 和资源门全过；但
  596 wall 从 A1 `1.510583 s` 变为 `1.505333 s`，只快 `0.3475%`、绝对 `5.25 ms`；
  prompt 从 `1384.3 ms` 变为 `1392.6 ms`，反慢 `0.5996%`。按停止门未跑 B 的 94K、
  2/4/6/8，不扫邻近深度或 stager 参数。A2 已恢复。见
  [result](work/AGENT_HOT_TURN_STAGED_PREFETCH_RESULT-20261031.md)。
- 上一项否决：`AGENT-HOT-TURN-DEQUANT-AUDIT`。phase map 的 `dequant 106 ms` 实际是
  native IQ3_XXS `gather_native_group` 的原字节搬运，不是 FP16 反量化。每层 `order`
  只含唯一 selected expert，每 16 个执行一次 gather；stage 1/2 是不同层，
  staged/streamed/fused/peer 路径互斥，未发现同一 expert/layout 重复转换。resident cache
  slot 仍须转成 MMQ 固定-stride 连续布局，不能安全直接删除。未构建、未重启，不扫
  dequant/group/profiler 参数。见
  [result](work/AGENT_HOT_TURN_DEQUANT_AUDIT_RESULT-20261031.md)。
- 暂停项：`AGENT-HOT-TURN-RESIDENT-PATH-AUDIT`。group 级 native direct-pointer 为
  `7.848 ms/layer` 对 MMQ `11.671 ms/layer`（`1.49x`），48 层乐观上界 `183.5 ms`。
  596 fresh 的 1 路 A1/B/A2 wall 为 `1.522684/1.490017/1.513878 s`；B 相对合并基线
  改善 `1.862%`，相对 A1 改善 `2.145%`、`32.667 ms`，三波全胜。按用户“1% 多也算”
  这是正向信号，但未过原契约绝对 `0.05 s` 门。94K wall 名义改善但 prompt 持平，不能
  归因给 prefill；4 路 A2 被暂停，6/8 未跑。候选默认关闭、未采纳，恢复后先审计逐层
  覆盖/fallback。见
  [result](work/AGENT_HOT_TURN_RESIDENT_PATH_RESULT-20261031.md)。
- 降级项：`AGENT-HISTORY-STATE-RESIDENCY` 仍保留为低频 p99 可行性。34 个有效会话只有
  1 个超过 24 小时间隔、2 个超过 12 小时，不得排在普通热续轮之前；检查双卡 layer split、
  六 batch slots、prompt cache 下多 stage 状态格式、223K 容量、恢复时间、写放大、
  RAM/VRAM 峰值和安全删除。只有真实旧会话等待至少减少 1% 且成本不转移到每轮才实现。
- 旧会话口径：用户所说的“重读历史 180K 会话”是放置超过一天后重新打开约 180K token
  的旧会话，不是 compaction 后重走旧历史分支。现有真实样本年龄 49 天，prompt
  `223,114` token，cached `0`；新合成 180K 冷 turn `65.555 s` 只保留为尺寸代理。见
  [result](work/AGENT_OLD_SESSION_RESUME_RESULT-20261031.md)。
- 长上下文口径：210K 合成 compaction 已复用 `99.987%`；两次未触发 recovery 的运行
  prompt `0.248–0.283 s`，recovery 运行的 `prompt_ms` 为 `75.474 s`，其中包含约
  75 秒无新 token 的尾部。冻结真实 session 的 44 次低命中属于 DeepSeek 直连；B4
  上三次真实 Codex compaction 已复用约 `99.1%`，不能再写成 Strata 缺陷。精确 pin
  可把 208K 压缩后旧分支从约 9 秒降到 `0.274 s`，但真实本地 rollout 的 109 次 compaction
  中立即回读为 0/109，因此 exact pin / `cap=9` 降为低频诊断，不作当前实现目标。
- 当前运行：server/vision/engine `172634/172636/172641`。server cwd 为
  `/opt/ai/src/strata-two-stream-serial-20261031`；engine 二进制为
  `/opt/ai/src/strata-freeze-throughput-20261029T0812Z/build75/strata`，cwd 为
  `/opt/ai/src/strata-freeze-throughput-20261029T0812Z`。监听 `127.0.0.1:8080`；
  engine `105a4171...` 未重建，config `6ece1a13...`，server `10bd8318...`，health loaded、
  262144 context、6 slots / 2 groups、expert slots `17274`；实际核对到 `--ple-inflight 1024`、
  `STRATA_BATCH_SERIAL_TWO=1`、
  `STRATA_ADMIT_MERGE_FIRST=1` 和 `STRATA_PIPE_BYIELD_NODRAIN=1`；engine env 不含
  `STRATA_DECODE_TIMING`/`STRATA_VERIFY_PROFILE`。
  `strata.service` 仍 inactive/dead、`MainPID=0`、`NRestarts=8`；engine env 不含
  `STRATA_PREFILL_STAGED_AHEAD`。回滚为同一源码目录 +
  direct-256 config
  `4b396f44...`；移除 `--ple-inflight` 即恢复默认 256。
  恢复过程中的 `use-config.sh` 误指向 systemd，而现网是手动实例；单元因实验配置不兼容重试 8 次后
  已停止；单元原配置按备份恢复，手动实例由恢复日志验证。以后手动实验臂不得用
  `use-config.sh` 作为“已切换”证据。
- 口径修正：两路串行把第二条 TTFT 推到约 `5.1 s`，只证明“总完成时间优先”的批处理
  取舍，不能作为交互式双会话或全局用户收益。后续双路实验必须同时报告第二条 TTFT、
  单请求 wall 和完成跨度。
- 当前证据断点：冻结 29 小时快照的 provider 是 DeepSeek 直连，不是 B4；其 43/44 次
  低 compaction 命中不能归因 Strata。通过隐私安全代理让 Codex 0.164 经 B4 发起三次真实
  compaction，分别复用 `11,124/11,225`、`11,349/11,452`、`11,627/11,729`，prompt
  `451.5–529.4 ms`，全部 completed。当前 kept-tool + prefix cache 无需新候选。见
  [AGENT_COMPACTION_REAL_SHAPE_RESULT](work/AGENT_COMPACTION_REAL_SHAPE_RESULT-20261031.md)。
- 最新成本地图：实际 94K 热续轮 wall `2.086 s`、189K `2.430 s`；210K compaction
  输入新增约 27 token、缓存 `99.987%`；两次正常运行的 prompt 为 `0.248–0.283 s`，
  输出 347/532 token、wall `5.853/8.845 s`。recovery 运行输出 734 token、wall
  `85.017 s`，其 `prompt_ms=75.474 s`，不能并入正常 prompt 范围。208K 压缩后旧分支的
  精确 pin 已动态成功：分支 cached `208528/208533`、wall `0.274 s`；无 pin 对照
  `9.016–9.523 s`。但该分支立即触发为 0/109，不能替代旧会话恢复。
  第三次 compaction 触发 `reasoning_loop_recovery` 后约 75 秒无新 token，真实频率未知。
  见 [AGENT_210K_COMPACTION_RESULT](work/AGENT_210K_COMPACTION_RESULT-20261031.md)。
- 长上下文测试已固化：`probe-long-compaction.py` 与 session trace 探针当前
  `11 passed`；long-context runner 与 `serve/test_reasoning_loop_recovery.py` 的联合
  回归曾为 `24 passed in 0.88s`。GPU 上的 180K/210K 结果来自 live probe，不是普通离线
  测试；合成分支 pin 是人工 live 验证，不是自动 GPU 回归。
- trace 工具已固化：`tools/server/trace-agent-tasks.py` 只读关联 root、模型、全部工具、
  compaction、取消和空档，不输出正文；当前与 session 探针联合 `10 passed`，
  `agent-shape-proxy.py` 自身 `13 passed`。
- 同服务 1/2 路 breakdown：1 路冷/续轮 wall `7.382/1.610 s`，2 路
  `11.075/2.290 s`；续轮 cached 约 95%，prompt 仍 `1.4–1.5 s`，短输出 decode 仅
  `50–73 ms`。见 [AGENT_SERVER_BREAKDOWN](work/AGENT_SERVER_BREAKDOWN-20261031.md)。
- 最新整任务形状校正：冻结快照有 14 个 root、4,180 个模型请求、6,297 次工具调用、
  7.54 亿 input token、97.09% cached；output 中位/p90/p99 `483/1,714/5,069`。
  工具等待中位 `910 ms`、p90/p99 `30.0/30.2 s`；2,645 万输出字符中，914 个 `>=10K`
  输出占 `68.57%`。11 个任务完成、3 个中止；最长 root `44,783 s`，其中模型
  `14,855 s`、工具 `31,088 s`、未归因空档 `15.843 s`。该形状说明真实 decode、工具轮转
  和上下文膨胀都不能由 64-token 探针代表。
- 较早全量本地 rollout 校正：26 文件、143 root、14,531 请求、29.93 亿 input、
  98.0% cached；工具输出 7,974 万字符，恰好两路模型并发只有 `143.727 s`。该快照用于
  工具输出与并发排序，不能冒充最新 35 文件的 decode 尺寸；加入本轮探针后的 35 文件
  快照为 152 root、14,768 请求、output/reasoning `10,343,967/3,347,547` token。

## 近期判决索引（只用于防重复）

- 上一项：AGENT-STATIC-SYSTEM-PREFIX 已把共同前缀由 `320` 字节升到 `54,366` 字节；
  真实四线程不同 tools 的后续请求各复用 `5,120` token，平均墙钟
  `7.515 → 5.669 s`（`-24.56%`），后三个兄弟请求 `7.502 → 5.001 s`（`-33.34%`）。
  结果见 [AGENT_STATIC_SYSTEM_PREFIX_RESULT](work/AGENT_STATIC_SYSTEM_PREFIX_RESULT-20261031.md)。
- 上一项：`UTIL-MTP-LOWCONC` 已否决。serial batch-MTP 在 2 路为 `+3.25%`，但 4 路
  `-28.99%`；A1/A2 漂移 `-1.39%/-0.74%/-0.15%` 在 3% 内，A2 已恢复。证据见
  [UTIL_MTP_LOWCONC_RESULT](work/UTIL_MTP_LOWCONC_RESULT-20261031.md)。
- 上一项：AGENT-CODEX-COMPACTION-LRU 已采纳并保持运行。B2 server/engine/vision
  `33065/33073/33068`，配置启用 title + compaction cache；真实 1 主 3 子代理 8.3K-token
  交错测试中四次 compaction 分别复用 `8274/8278/8288/8288` token（约 99.5%），墙钟
  `0.69–0.89 s`，旧单条目缓存会让前三个线程丢失工具前缀。结果见
  [AGENT_CODEX_COMPACTION_LRU_RESULT](work/AGENT_CODEX_COMPACTION_LRU_RESULT-20261031.md)。
- 上一项：AGENT-CODEX-TITLE 已采纳并保持运行。B server/engine/vision PID
  `31228/31235/31230`，6 slots / 2 groups、health loaded，engine `105a4171...`、config
  `36dcd908...`，相对 A 只多 `codex_thread_titles: true`。三个长前缀 title 请求均为
  HTTP 200、合法标题、`1.8–2.1 ms`，finished request 与 prompt/output token 增量全为 0；
  随后的普通 Responses 请求正常增加 1 个引擎请求。结果见
  [AGENT_CODEX_TITLE_RESULT](work/AGENT_CODEX_TITLE_RESULT-20261031.md)、台账 L-126。
- Agent 会话诊断证据：普通同会话续轮复用 `95.16–95.21%`，首轮约 `7.3 s` 的 prompt 读取降到
  约 `1.4 s`；共享约 69% 实际前缀的新会话首轮 prompt 为约 `3.2 s`。正式结果是
  [AGENT_SESSION_PROFILE_RESULT](work/AGENT_SESSION_PROFILE_RESULT-20261031.md)、台账 L-125。
- 上一项：UTIL-BATCH-FAIR-SERVE 已在同 boot A1-B-A2 吞吐门否决。默认关闭的候选在每个
  stage 每轮最多服务一个已到达的 layer，以让 `pump()` 先访问另一 stage。B 通过构建、
  6 slots / 2 groups、启动标记、8 个多行 batch capture、A1/B1/B2/A2 冻结 greedy 逐字
  parity 和零请求错误门，但 2/4/6/8 路相对 A1/A2 合并中位为
  `+8.901%/-5.121%/+2.114%/-2.612%`，胜场 `5/6, 3/6, 4/6, 2/6`；4/8 路回退超过 1%，
  2 路 A 漂移 `-7.067%`，6 路仅 4/6 胜。
- 上一项证据：B 启动约 `148 s`、恢复 A2 约 `198 s`，B 停止释放端口约 `9 s`；A2 已恢复且
  parity 通过。证据目录
  `/opt/ai/logs/util-batch-fair-serve-20261031T0355UTC/`，结果见
  [UTIL_BATCH_FAIR_SERVE_RESULT](work/UTIL_BATCH_FAIR_SERVE_RESULT-20261031.md)、台账 L-124。
- 上一项：UTIL-Q5-HEAD-IL-GRID 已在性能筛选门否决。候选只限制生产
  `2560 -> 248320` Q5_K head 的 persistent CTA grid，不改变每行算术、reduction、
  dispatch、token 选择或 finish 语义。所有 parity 逐字相等，但 `T=2` 的每个 capped
  grid 都慢于默认路径；`T=3` 最好的 grid `2560` 只比默认快 `0.182` 个百分点。
- 上一项证据：有效计时来自 `screen2-gpu0-*`；`T=2/3/4` 最佳 capped gain 为
  `+0.847%/+1.272%/+1.377%`，远低于预注册的 18.5% head 或约 `0.192 ms` 整 stage
  门槛，因此没有跑七进程确认和 server A-B-A，没有构建 server、没有重启。临时源码已移除，
  A2 环境没有 `STRATA_MMVQ_IL_HEAD_GRID`。
- 上一项：UTIL-BATCH-SIDE-MIXER 同 boot A1-B-A2 的 2/4/6/8 路相对 A 合并中位为
  `-2.755%/-6.766%/+0.378%/+4.969%`，胜场 `4/6, 1/6, 2/6, 5/6`；只有 8 路过双门，
  2/4 路回退超过 1%，2 路 A 漂移 `-7.067%` 超过 3%。候选已恢复，不扫描 T=2 邻近窗口。
- 上一项：UTIL-SIDE-MIXER 在 server 可达性门否决。两卡 14 个独立进程、196 条结果行和
  560 个 device buffer 全逐字相等；T=3/4 的理想整窗上界达到 `1.10–1.29%`。但生产
  `capture_batch()` 设置 `batch_rec_=true`，而 `side_proj/sidem` 都要求 `!batch_rec_`，
  所以现有 env 在生产 2/4/6/8 路命中率为零。没有构建、没有重启。
- 上一项：UTIL-QFUSE-HC 的生产 Q8 `read + quantize` A/B 在两卡 `T=1..8` 全 bitwise。
  完整路径在 T=1/2/3 有 `+1.31–2.67%`、6/7 或 7/7 胜，但单次只省 `0.44–0.79 us`；
  每 stage 48 次 read 投影仅 `0.11–0.20%`，不扣两卡 overlap 的乐观上界也低于 0.6%。
  没有构建 server、没有重启、生产文件未变。
- 上一项：UTIL-LFUSE-SHARED 启动和正确性全过。两卡 `mmvf_rows_parity` 的
  `432 cases / 1,660,200 values` 零 mismatch，`fusions_parity` 零失败；B/A2 三组冻结 greedy
  两轮逐字一致。A1-B-A2 的 2/4/6/8 路为 `-3.513%/-8.782%/-2.697%/+4.362%`，胜场
  `1/6, 1/6, 2/6, 4/6`，A 漂移 `-15.536%/-6.256%/-1.494%/+4.053%`；只有 8 路名义过 1%，
  但未过 5/6 胜与漂移门，2/4/6 回退超过 1%。A2 已恢复，不保留 LFUSE/MMVF env。
- 上一项：UTIL-B6-ROWS 在 kernel screening/reach 门否决。两卡全部 parity；但相对生产
  `STRATA_SM75_PERF=1` 选择的 IL rows，T=2/3/4 中位为
  `-5.868%/0.000%/+0.586%`，胜场 `0/22, 7/28, 18/28`，Q5_K/Q6_K 单点回退
  `15.96-38.63%`。仅设置 `STRATA_B6_MMVQ_ROWS=1` 不能替换 IL dispatch；没有构建或重启。
- 上一项：UTIL-POLL-TURNOVER 已完成并在端到端非回退门否决。首个 B1 漏掉 A 的 MMQ/Q6K
  编译开关，已作废；同选项 B2 通过启动、路径和多轮 greedy parity。相邻 A2/B2/A3 的
  2/4/6/8 路相对 A2/A3 合并中位为 `+9.129%/+7.885%/-3.241%/-6.122%`，胜场
  `4/6, 5/6, 1/6, 0/6`；没有一级同时过 1% 与 5/6，6/8 回退超过 1%。源码已移除，A3 已恢复。
- 上一项：UTIL-HEAD-Q8-FUSE 在独立 GPU/正确性门否决。5 个交替进程、每进程 200 iters、
  5 rounds；每个 `T=1..8` 的 direct 与 captured-graph 共 16 个逐字比较全部失败，五进程共
  80 个失败。最大单次节省仅 `1.315 µs`，远低于最快 stage-2 窗口 1% 的 `182.7 µs`；
  captured graph 在 `T=4..8` 反而慢约 `1–3 µs`。没有构建服务器候选、没有重启，
  `STRATA_FUSE_HEAD_GR` 不在运行环境。
- 上一项：UTIL-HEAD-SPLIT 服务端集成通过启动、路径命中、输出与资源门，但吞吐门否决。
  `1/2/3/4/5/6/7/8` 行 peer graph 全部捕获；6 slots / 2 groups、health loaded，三组固定
  greedy 各两轮逐字等于期望值，A2 同样通过。A1-B-A2 的 2/4/6/8 路为 A
  `77.70/114.90/124.15/106.30`、B `80.80/113.40/113.40/106.20 tok/s`，相对变化
  `+3.99%/-1.31%/-8.66%/-0.09%`；4/6 路回退超过 1%，因此不采纳。A2 已恢复，
  `STRATA_HEAD_SPLIT` 不在运行进程中。
- 上一项：UTIL-BATCH-HEAD 已在源码门否决。两个 group 在末级并非同时 ready；每级只有一个
  verifier 和一套 `head_mixed_/head_logits_/m_out_`，`stage_group` 一次只绑定一个 group。
  等齐后再合并会把 A 的 verdict 延迟一个 16.9–19.2 ms 的末级窗口，远大于约 1.04 ms 的
  合并收益。未构建、未重启。
- 上一项：UTIL-WINDOW-PROFILE 已完成。A1/P/A2 各 12 波；P 的 6/8 路为
  `113.352/103.373 tok/s`，8 路 occupancy `5.010`、tail `1.1349`、max slot wait
  `12,596 ms`，BSTAT active rows/window `2.6837` 对 6 路 `2.9515`。按活跃行计的
  stage 2 成本上升 `4.54%`；KV resolve 为 0.000 ms，padding 不是原因。
- 上一项：UTIL-GDN-GROUPS 已完成并否决。修正 grouped self-commit 状态写回后，GPU parity
  输出和状态全部 `memcmp` 通过；2/4/6/8 路相对 A3/A4 合并中位为
  `+0.777%/-8.011%/+0.913%/-1.386%`，胜场 `2/6/2/6/3/6/2/6`，4 路回退超过 1%，
  A3/A4 漂移全部超过 3%。候选源码已反向、A 原二进制已恢复。
- 上一项：UTIL-SLOT-CKPT-MOVE 已完成并否决。三组 greedy 探针两次逐字等于 A；同周期
  2/4/6/8 路为 `81.5/110.9/123.1/107.8 tok/s`，相对 A3/A4 仅 2/4 路名义
  `+3.099%/+2.259%`、各胜 4/6 与 3/6，6 路 `+0.122%`，8 路 `-3.014%`。
- 上一项：UTIL-EXPDEC-GU 已在启动前路径门否决。部署源码的 mode 8 先执行 fused/LDS gate/up，
  down 也在 LDS 分支直接返回，因此 `STRATA_EXPDEC=1` 无法到达目标内核；未重启、未跑 parity
  或吞吐。证据为 `util-expdec-gu-static-20261030T2008Z.txt`。
- 上一项：UTIL-BATCH-GROUP-FIT 已完成并在 parity 门否决。A3 的 2/4/6/8 路中位为
  `81.0/116.1/133.2/104.4 tok/s`，确认 8 路平台真实存在；候选保持 6 slots / 1 group 并正常
  loaded，但自然语言探针在 B 内可重复、与 A 不同且提前以 `finish=stop` 结束，因此没有跑
  候选吞吐。
- 上一项：UTIL-PIPED-MTP-ORDER 已完成并在 parity 门否决。候选保持六槽/两组，只增加
  `--expert-cache-cpu-order`；自然语言探针仍两次不同且与 A 不同，CPU-order 对 layer-split
  verifier 路径无可见效果，因此没有跑候选吞吐。
- 上一项：UTIL-PIPED-MTP-CACHE 已完成并在启动门否决。`--expert-cache-device1 7209`
  被引擎解释为 helper cache，而双卡 layer split 已占用两块 GPU，候选在读取权重前以 exit 2
  退出；没有跑 parity 或性能。
- 上一项：UTIL-PIPED-MTP 已完成并否决。六槽两组 drafters 真实启动，2 路
  `78.3 → 93.9 tok/s`（+19.92%，5/6 胜），但 4/6 路回退 `3.82%/9.79%`，且固定 greedy
  文本在 B 内不可重复。
- 上一项：UTIL-WEIGHT-RO 已完成并否决。显式只读加载生成 48 条
  `LDG.E.U16.CONSTANT.SYS`，hash 不变，但 gate/up 中位 `471.0` 对 `472.6 us`
  （+0.339%），6/7 胜；未达 1%。
- 上一项：UTIL-GRID-PAD 已完成并否决。四副本 stride 257 保持 hash，但 gate/up 中位
  `489.3` 对 `475.7 us`（−2.86%），仅 3/7 胜。
- 上一项：UTIL-WEIGHT 已完成并否决。weight-decode working set 达到 `+0.546%`、6/7 胜，
  但两臂 shared/global load 指令完全相同且寄存器 73→80；未达 1%。
- 上一项：UTIL-ACT 已完成并否决。显式 activation reload 中位 `475.2` 对 `475.1 us`
  （−0.021%），仅胜 3/7；编译器已复用。
- 上一项：UTIL-KERNEL 已完成并否决。四副本 rotated IQ3_XXS codebook 中位
  `474.5` 对 `474.1 us`（−0.084%），仅胜 2/7；bank conflicts `7,115,366 → 7,293,965`，
  寄存器 73→80。
- 上一项：UTIL-NCU 已完成。完整请求 full-section replay 触发 stall watchdog；同候选二进制的
  真实权重 mode-8 微内核测得 MIO throttle `29.75%`、long scoreboard `17.50%`、Tensor
  `0.04%`，唯一主因选 MIO。
- 上一项：QSA-UNION-ATTN 已完成并否决。三个相邻 query 同 CTA 的实验保持 parity 和
  `168 registers/thread`、无 spill，但普通选择慢 `20.05%`，`QA_SAME=4` 最强共享上界仍慢
  `20.75%`；按预注册 kernel 门停止，未消耗 150K A-B-A 或重启预算。
- 上一项：QSA-DIAG 已完成。真实 `150,069` token 冷读中，19 个 chunk 的 16-query selected-cell
  union 中位为 `28.0%`；FP16 全选择一致率只有 `70.9207%`，半精度路线否决。
- 前一项：P6-1PCT 已完成并通过。当前默认 v2 对 v1 的固定 96K A-B-A 中，engine read
  改善 `1.814%`、wall 改善 `1.905%`，A2 漂移约 1.26%，parity/needle 全过。
- 前一项：LM-EXPLICIT 已完成并否决。16K host-residual 路径真实执行了 10/13 个
  super-chunk，但六请求 prompt 吞吐中位 `1892.9` 对 A1 `2208.55 tok/s`（−14.29%）；
  候选 8080 已恢复带 pipe 的 A 配置。
- 当前候选：`AGENT-TERMINAL-QUALITY-COMPARISON`。现役 B4 是已采纳的
  两路串行版本，engine 仍为
  `105a4171...`；title、compaction LRU、共享 system、boundary pin 与两路串行均已通过
  各自安全门；PLE inflight 已按三组配对采纳，4M row-cache 已否决。上下文饮食真实
  客户端配对反向，已否决产品化；短读阈值已否决，保留默认 64；当前只读分析编辑—测试
  循环。P7 不恢复：实测
  Tensor pipe 仅 `0.04%`。
  256K 切换前生产快照为 `/opt/ai/tmp/pre-ctx256k-20261026T075110Z/`；更早的 P4 回滚基线为
  `/opt/ai/tmp/pre-P4-strict-20261026T054515Z/`。
- 执行计划：[真实 Agent 负载优化计划](superpowers/plans/2026-10-31-agent-workload-throughput.md)
  已重写和评审，按请求级缓存、排队和工具路径优先，不再从低收益局部内核开始。
- 未完成采纳项：22
- 阻塞：服务器已恢复到双卡可用，当前无基础设施阻塞。DFlash2 drafter 权重仍缺失；代码、
  服务接线和工具已保留，该路线不阻塞主线。

## 生产

- 最近核对时 `strata.service` 为 `inactive/dead`、`MainPID=0`、`NRestarts=8`。当前 B4
  手动实例 server/engine/vision PID `162903/162910/162905`，health loaded，host uptime 自
  `2026-10-31 02:43:50 UTC`，两张 2080 Ti 均可联合 initialize。server cwd 与 engine
  路径不同，见上。
- 历史 A2 对照基线的 engine/config hash 为
  `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3` /
  `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f`，frozen greedy
  parity 通过，运行 env 无候选开关；它不是当前 B4 实例。已停 fair-service B
  engine/config hash 为
  `0099f11a08fa3e8a5a58783e0fdc1e2c4fdb60cebb288c6044d3b7e31b3002b7` /
  `c68b9e81ae2c28a654700218a3155be37464c8044db8b7aa0fe362b6818c3c31`，仅供证据复现。
- **systemd 生产回退版本为上游合并构建 `0.1.51` / `0e4a9596`**：代码树
  `/opt/ai/src/strata-027-picks`
  干净停在 `c219fbca`；engine sha256 `2aaf2751…`、server md5 `11fd33f0`、
  config md5 `023663ba`。服务实际启动的 engine 为
  `/opt/ai/src/strata-027-picks/build75-027/strata`；修复后的 T-probe 从进程 argv 识别实际
  engine/server/config，现网返回 `problems=[]`。
- 生产配置：`parallel=6`、`max_context=262144`、`STRATA_HC_Q8=1`，W3 staged read 开启，阈值 2048；
  lead-in 1600 ms、等待 750 ms、只处理 ≥4096-token 的冷读，短请求首准入阈值 2048。
- 256K 下 `batch_slots=6`、`kv_resident=32768`、`expert_slots=17280`、`max_context=262144`；
  服务进程参数包含 `--batch 6 --layer-split 24 --batch-groups 2`。
- `STRATA_ADMIT_SHORT_FIRST` 由 systemd service 环境提供；写进 config 的 `env` 只会传给引擎子进程。
- 旧 `0.1.51` / `a09d78b1` 二进制仍保留在 `/opt/ai/src/strata-0.2.2/build/strata`，可用于
  紧急回退；切换前的 `6×256K` 历史快照仍在 `/opt/ai/tmp/pre-ctx256k-20261026T075110Z/`。
  c219 合并前的完整 Git 状态保留在本地 `refs/codex-backup/pre-workspace-cleanup-*`。

## 已知结果

- UTIL-KERNEL 已否决：四路 rotated IQ3_XXS codebook 保持输出，但 `474.5` 对 `474.1 us`
  （−0.084%），2/7 胜；NCU bank conflicts 反升 `2.5%`。
- UTIL-ACT 已否决：显式 activation reload 与 baseline 的 shared-load 指令完全相同，中位
  `475.2` 对 `475.1 us`、3/7 胜；编译器已经复用。
- UTIL-WEIGHT 已否决：方向性改善 `0.546%`、6/7 胜，但 shared/global load 指令完全不变
  且寄存器 73→80；未达 1%。首版四副本只覆盖八个 bank，下一项改用 padded stride 257。
- UTIL-GRID-PAD 已否决：四副本 stride 257 保持 hash，但 gate/up 中位 `489.3` 对
  `475.7 us`（−2.86%），仅 3/7 胜；剔除首组后仍慢约 1.2%。不扫描副本数或 stride。
- UTIL-WEIGHT-RO 已否决：默认 SASS 有 56 条 `LD.E.U16.SYS`；候选产生 48 条
  `LDG.E.U16.CONSTANT.SYS`、80 registers、无 stack。七组中位 `471.0` 对 `472.6 us`
  （+0.339%），6/7 胜；机器码变化真实，但未达 1%。
- UTIL-HEAD-SPLIT 已否决：独立 harness 的 T=1..8 logits/采样全 bitwise，并发 head 临界
  路径投影中位 `1.813–9.936%`；server 集成也命中 `1..8` 行 peer graph 且 greedy 全过。
  但端到端 4/6 路为 `-1.305%/-8.659%`，违反其他级不回退 >1% 的门，默认关闭。
- UTIL-HEAD-Q8-FUSE 已否决：5 个交替进程的 `T=1..8` direct/captured graph 共 80 个逐字
  比较全部失败；最大节省 `1.315 µs`，远低于 `182.7 µs` 的 1% 窗口门槛。graph 在 `T=4..8`
  慢约 `1–3 µs`，未构建、未重启。
- UTIL-POLL-TURNOVER 已否决：同编译选项 B2 通过启动、路径和 greedy parity，但相邻
  A2/A3 合并中位下 6/8 路回退 `3.241%/6.122%`，8 路对两个 A 臂分别回退
  `8.195%/4.080%`；2/4 路的 `+9.129%/+7.885%` 不能抵消其他级硬回退。
- UTIL-B6-ROWS 已否决：两卡 exactness 全过，但 B6 相对生产 IL 的 T=2 中位慢 `5.868%`，
  0/22 胜；仅有 4 列中位 `+0.586%` 且 duration-weighted 为 `-1.000%`。B6 也不在 IL 的
  production dispatch 上，不能靠 env 替换。没有 server build、重启或 A-B-A。
- UTIL-LFUSE-SHARED 已否决：两卡 parity 与 B/A2 冻结 greedy 全过，但 A1-B-A2 的
  2/4/6/8 路为 `-3.513%/-8.782%/-2.697%/+4.362%`、胜场 `1/6, 1/6, 2/6, 4/6`。仅 8 路
  名义过 1%，却未达到 5/6 胜且 A 漂移 `+4.053%`；2/4/6 回退超过 1%。A2 已恢复。
- UTIL-QFUSE-HC 已否决：生产 Q8 read+quantize 对 fused q8 read 在两卡全 bitwise，
  T=1/2/3 的完整局部路径达到 `+1.31–2.67%`，但仅省 `0.44–0.79 us/read`；按每 stage
  48 次 read 投影只有 `0.11–0.20%`，不足以进入 server A-B-A。未重启。
- UTIL-SIDE-MIXER 已否决：两卡 14 个独立进程覆盖 `T=1..8`，560 个输出 buffer 全 bitwise；
  T=3/4 的乐观整窗上界为 `+1.10–1.29%`。生产 batch capture 设置 `batch_rec_=true`，
  现有 side branch 的 `!batch_rec_` 条件使其命中率为零，因此未构建服务或重启。
- UTIL-PIPED-MTP 已否决：A1/A2 合并中位 `78.3/108.55/125.7 tok/s`，B 为
  `93.9/104.4/113.4`；2 路 `+19.92%`、5/6 胜，但 4/6 路 `-3.82%/-9.79%`。固定 greedy
  文本 B 两次不同；B 的 CUDA1 cache 比 A 少 169 slots，下一项先做 cache-neutral 隔离。
- UTIL-PIPED-MTP-CACHE 已在启动门否决：候选只把 B 的 `--expert-cache auto` 换成
  `10065` 并加 `--expert-cache-device1 7209`，后者被读成 helper cache，而双卡 layer split
  没有不跑 stage 的 GPU；engine 在读取权重前 exit 2，没有 parity/性能结果。
- UTIL-PIPED-MTP-ORDER 已在 parity 门否决：候选只加 `--expert-cache-cpu-order`，自然语言
  探针仍两次不同且与 A 不同，输出与上一轮 B 完全相同。源码中该开关接在
  `ExpertDispatch`，layer-split 的 `VerifyHits` 没有对应字段；候选未跑吞吐。
- UTIL-BATCH-GROUP-FIT 已在 parity 门否决：A3 8 路中位 `104.4 tok/s`，低于 6 路 `133.2`；
  单组候选正常启动且两次一致，但自然语言回答为 94-token `finish=stop`，A 为 96-token
  `finish=length`。只改组数也会改变数值路径，候选未跑吞吐。
- V3-1 容量预设已收口：4 路中位 `6×192K` 聚合 120.6、`8×128K` 133.9、`8×192K` 122.8；
  默认保持 `6×192K`。8×128K 的 +11% 以 192K→128K 上下文为代价。
- 256K 生产切换已完成：config 仅把 `max_context` 从 `192000` 改为 `262144`，同时保留
  `batch_slots=6` 与 `kv_resident=32768`。expert slots 从 `17728` 降到 `17248`（−2.7%），
  expert cache 从 `29010` 降到 `28222 MiB`；启动后两卡空闲 `1005/1073 MiB`。
- 256K 功能门通过：`222,322` token、50% needle 和 `253,633` token、90% needle 均命中，
  墙钟分别为 `147.2/152.7 s`；6 路短并发 `6/6` 成功，单请求墙钟 `2.0–3.1 s`。
  长读后 RAM 为 `56 GiB used / 5.8 GiB available`，swap 仍为 0；两卡实际空闲约 `856/950 MiB`。
- V3-4 已完成：MTP 草稿深度 2/4/6 在噪声内，保持 4。
- V2b fused prefill +0.61%，否决；V2c `STRATA_PIPED_CONCURRENT` 两臂无差异，S0b 仍需换机制。
- S0b 已否决：gate 修复后并发读 3/3 触发，但邻居静默中位 ON 7.09/7.59 s、OFF 7.41 s；
  机制无用户收益。L-082 已撤回“整个 0.1.51 吞吐回退”的临时概括。
- batch slot 共享 RoPE 已采纳：双卡 6×192K 省 562.4 MiB，expert slots +1.8%，吞吐差在噪声内，
  输出 parity 通过；生产源码补丁已固化。
- C1 已完成：6 槽第 7 请求 TTFT 中位 11.84 s、slot 等待 11.42 s；8 槽第 9 请求 14.38 s、
  13.89 s。邻居静默都低于 0.61 s；默认保持 6 槽。
- S2 已补 `slot_wait_ms`：原 `queue_ms=0` 不再掩盖 batch slot 等待；现网 server `de09cffd`。
- P1 完成第一轮 A-B-A：groups=2 的 4 路聚合为 127.2/120.4（合并中位 122.1），groups=1 为
  110.9，慢 11.6%；损失来自并发 prompt 与 decode，不是准入等待。生产已恢复 groups=2。
- groups=1 + `--batch-mtp` 也否决：聚合 91.3，接受率 80.71% 正常，但双行串行窗口把 decode
  每流压到 24.8 tok/s、wall 拉长到 17.5 s；生产再次恢复 groups=2。
- P1 受控 4 路目标已过线：恢复合并中丢失的 `STRATA_HC_Q8=1` 后，两轮 B 中位 130.6 tok/s，
  无 HC_Q8 的四个 A 中位 115.25；质量门两臂均 11/11。B2 为 128.7，仍需真实流量确认稳定性。
- P2 已通过：1 小时 622 请求、207 waves、0 错误、0 意外 length、0 needle 失败；4 路后置合成中位
  129.7，相对 130.6 为 −0.7%；swap 零使用，RSS 与 MemAvailable 进入平台期。
- P2 暴露下一瓶颈：96K 冷 prefill 邻居下，短 JSON 的 TTFT 0.63 s，但 wall 中位 40.83 s；
  32K/64K/96K 长档墙钟中位分别为 19.22/27.44/41.61 s。
- P3 已采纳：同 boot A-B-A 中，短 JSON wall 由 A 合并中位 40.21 s 降到 E 合并中位 3.40 s
  （−91.5%）；96K 长请求由 40.38 s 升到 41.28 s（+2.2%，门限内），两轮均 20/20 成功、
  needle 全对，lead-in 日志 10/10 触发。`STRATA_PIPED_READ_PIECE` 32768/65536 因长档
  +15.6%/+7.3% 被否决；1200 ms lead-in 也因代码请求未改善且长档超门被否决。
- P3 未覆盖：41-token 的 1.85K 代码请求仍为 41.51 s；当前机制只释放了 14-token 短响应，
  不能把“长读期间所有请求都公平”写成已解决。
- 新引擎自身 soak 已完成：618 请求、206 waves，0 错误、0 意外 length、needle 全对；
  short/medium/32K/64K/96K wall 中位为 6.025/6.794/19.297/27.222/41.780 s。
  RSS 后段稳定在约 51–52 GiB，swap 未使用；T-probe `problems=[]`。
- P4 历史候选 soak 不能替代本轮严格归因：旧引擎结果为 606 请求、202 waves、0 错误、25/25 needle；
  96K 邻居下代码 4.424 s、短 JSON 3.370 s。32K/96K 四路 wave 分别 +2.78%/+2.53%，
  但 96K `by_kind` 单请求中位 42.658 对 P2 40.96 s（+4.14%），相邻 400 ms D2 约 +3.21%。
- P4 新引擎严格 A-B-A 已通过：A1/A2 代码 41.173/41.202 s，B 为 5.074 s（−87.681%）；
  96K by_kind +2.761%、wave +2.202%，短 JSON 3.980 s；三臂各 20 请求、0 错误、needle 全对。
  该配置采纳，但短 JSON 距 4 s 仅约 20 ms，96K 距 3% 仅约 0.24 个百分点，后续变更必须保守。
- P5 已完成：96K timing 臂的 GPU timeline 中位 32.653 s、phase 总墙钟 35.441 s，host staging
  仅 0.341 s；主要 phase 为 `gemm gate/up` 5.807 s、`embed+steps` 3.692 s、`qsa attn`
  3.422 s、`gemm down` 3.031 s。timing/plain engine read 为 37.841/37.571 s（+0.72%），
  3/3 needle 正确、零执行错误，恢复后 T-probe `problems=[]`。
- P6 已按新 1% 门复验并采纳为当前默认：当前 v2 与 v1 fallback 的固定 96K A-B-A 中，
  A1/B/A2 engine read 中位 `40063.1/41022.6/40564.9 ms`，wall 中位
  `40.368/41.382/40.878 s`；A 合并中位相对 B 为 `+1.814%/+1.905%`，A2 漂移约 1.26%，
  parity/needle 全过。无需再设 `STRATA_PA_V2T=1`。
- QSA slots 已否决：4 路 off/on/off 为 125.7/114.6/115.5 tok/s；6 路首次 on/off
  151.9/146.8 的 +3.5% 未在交错臂复现，最终 B4 对 A4 反向 0.49%。生产保持 off。
- S2 已完成：流式最终块与非流式响应都有 per-request metrics，mock regression 2/2 通过。
- T-probe 已完成：版本戳从 `/proc/<pid>/cmdline` 解析实际 engine/server；现网 server hash 为
  port-v1 的 `de09cffd`，不再误读生产目录里的旧副本。
- 合并后 `0.1.51` 已通过测试、temperature 0 parity、版本戳与五臂 A-B-A：修复版 md5
  `a09d78b1`，单路合并中位 `71.3` 对旧 `68.8`（+3.6%），4 路 `130.0` 对 `130.4`
  （−0.3%），cold prefill +0.4%。CTest 为 151 passed / 8 skipped / 1 failed，唯一失败是
  服务器缺少 Q2_0 PLE 外部资产，不能写成全测试绿。
- `6×256K` 版本吞吐 A-B-A 已完成：新版单路 engine decode `80.45` 对旧版 `82.7 tok/s`
  （−2.72%），4 路 `136.05` 对 `139.6`（−2.54%），冷读 `2267` 对 `2273`（−0.26%）；
  均在 3% 门内。三臂 greedy 哈希一致，最终恢复新版后 T-probe `problems=[]`。
- `CTX256` 一小时 soak 已通过：414 请求、133 waves、0 错误、0 意外 length、74/74 needle；
  batch slots 始终 6、expert slots 始终 17248、version 始终 `0.1.51`，结束后 T-probe
  `problems=[]`。14 次约 245.9K 长读全部成功，wall 中位 `101.077 s`。
- 真实硬件计数首轮已验证：约 32K 冷 prefill 的 SMs Active 中位 `77%`、p90 `98%`，
  SM Issue `13–14%`、Tensor `10%`、Warps `28%`、DRAM read `15%`；两次同窗 NVML
  分别为 `95/98%` 与 `40/99%`，只表示采样活动，不能代表算术或 Tensor 利用率。
- 上游合并构建吞吐复测（数字序列、512-token 上限）：单路 `102.46/105.64 tok/s`，6 路聚合
  `163.70/165.66 tok/s`；自然语言单路 `67.24/70.21 tok/s`，6 路聚合
  `148.94/150.71 tok/s`。3,427-token 冷读为 `923.7/931.4 tok/s`。
- 显式 layer-major 已否决：16K host residual 路径确实执行，然而 150K/210K 六请求的
  prompt 吞吐中位从 A1 `2208.55` 降到 `1892.9 tok/s`（−14.29%）；冷读、210K 续读和
  两个改写场景均回退。150K warm 的墙钟优势来自更多前缀命中，不是可归因的算法收益。
- 并发凹点已复现：2 路数字序列聚合只有 `94.03/95.67 tok/s`，低于单路；
  8 路聚合为 `137.85/139.66 tok/s`，低于 6 路且平均 TTFT 升至约 `5.7–5.8 s`。
  当前 6 槽上限有效，但 2 路和 8 路都需要单独优化，不能只看峰值。
- 服务器独立构建目录 `/opt/ai/src/strata-027-picks/build75-027` 构建成功；终端最终测试
  `1224 passed / 56 skipped / 10894 subtests passed`。CTest 仍保留 6 个已知失败：
  `mmvq_il_parity`、`iq_multi_parity`、`gdn_rec_parity`、`native_grouped_parity`、
  `ple_parity`、`native_expert_parity_q8_0_q8_0`；其中 `ple_parity` 缺外部 Q2_0 PLE 文件，
  `gdn_rec_parity` 在部分断言通过后 OOM，不能写成全测试绿。
- 版本探针已改为从进程 argv 读取实际 engine/server/config 路径，不再依赖 `build/strata`；
  现网输出 `problems=[]`，`mixed-soak.py` 也能读取当前 engine 的 PID 与 RSS。工具联合回归
  `28 passed, 1 skipped`；本轮未重跑需要 root 的完整 Nsight GPU Metrics。
- 配置漂移门禁已采纳：`--expect-env-file production-env-2080ti.json` 精确核对 12 个关键运行态 env；
  缺键或错值会失败。现网一次实测 0 problems。
- 当前最大用户可感知短板仍是 96K 冷 prefill 本身约 37–42 s，以及满槽准入约 11.4–13.9 s；
  P4 已解决其中 41-token 中等请求被压到约 41 s 的公平性问题。

## 重启策略

- 服务与服务器可按实验、部署或恢复需要随时重启，不设次数上限，也不需要逐次批准。
- 次数、原因、停机时长和结果写入 `ITERATION_LOG.md`，用于成本判断，不作为额度。
- 生产修改必须先快照；一次只运行一个安装器；装后核对真实文件、配置和 health；失败立即恢复。

## 当前风险

- 4 路聚合跨 boot 噪声约 9%，同会话低于 3% 不下结论。
- `UTIL-TWO-STREAM-SERIAL` 的第二条请求 TTFT 约 `5.1 s`；它只适合“总完成时间优先”的
  两路部署。三路以上会自动恢复 batch，但取消竞态、真实工具调用和长时间 mixed soak 尚未覆盖。
- 上游合并构建在 2 路出现吞吐凹点，8 路没有相对 6 路的吞吐收益且 TTFT 明显变差；
  任何批处理或 kernel 改动都必须同时检查单路、2/4/6/8 路和 TTFT，不允许只优化峰值。
- CTest 的 6 个已知失败仍未清零；运行验收通过不能替代这些 parity/OOM 缺口。
- `6×256K` 的 4 路版本差为 −2.54%，距 3% 门限只有约 0.46 个百分点；后续任何调度或内核改动
  都必须重新做同配置 A-B-A，不能把本轮“无显著回退”扩大成“没有性能代价”。
- layer-major 的计算路径目前比现役 pipe 路径慢约 14%；不能通过继续缩小或放大 16K target
  来假设它会转为收益。下一候选必须同路径复验并同时核对 cached/read tokens。
- P6 的 1.8%/1.9% 收益只在三个固定冷 96K 单流 prompt 上复验；32K/64K、150K/210K 和并发
  尚未覆盖，不能扩大成所有负载收益。
- 性能 A/B 前必须先证明实验开关真的进入目标路径；否则 on/off 两臂可能完全相同。
- C1 的 6/8 槽受控边界已测；不同邻居长度和真实混合流量下的准入上限仍未覆盖。
- 新引擎进入 GPU A/B 前必须用 T-probe 证明 running engine/server hash；版本戳已不再从旧目录猜路径。
- RoPE 省显存已经过一小时混合 soak；只能宣称显存与 expert 容量收益，不能把噪声内吞吐说成提升。
- 满槽请求仍没有配置固定准入超时；6/8 槽上界只覆盖受控的 400-token 邻居负载。
- P3 的 lead-in 只针对 `piped && !STRATA_PIPED_READ_PIECE` 的并发冷读；读到新的长请求时
  仍会增加一次有界解码窗口。1200 ms 已证明超过旧长档门限，后续不能用任意加长代替调度。
- 短/中请求可能在冷读已占流水线前仍未取得控制行或槽；仅延长 lead-in 无法覆盖这类准入竞争。
- P4 已通过但余量小：短 JSON 3.980 s 接近 4 s，96K by_kind +2.761% 接近 3%；生产监控若靠近
  边界，应优先用同口径复测，不能继续无证据扩大 lead-in 或短请求优先范围。
- 新引擎 soak 的短 JSON 混合中位为 6.025 s，高于 fairness 的 4 s；混合 soak 与 fairness 是
  不同口径，不能互相替代。
- 当前公平性测试只覆盖 14/41-token 输出和 96K 冷读；32K/64K、多长读、取消与真实外部流量未覆盖。
- `STRATA_PA_V2T=1` 改变 prompt-attention 的求和/暂存顺序，即使性能提升也必须先通过 greedy parity
  与 needle；哈希变化不能自动判为正确或错误。
- P7 已搁置且没有有效样本；只有 NCU 证明 Tensor 管线是直接瓶颈时才恢复。恢复后必须新建完整
  A-B-A，不能复用被中断的首请求或把“实验已启动”当作部分结果。`STRATA_PF_WMMA=1`
  走 FP16 tensor-core prompt experts，输出可能变化；速度达标后仍必须有质量门。
- `CTX256` 已通过，但并发最高为 4，不能写成“6 槽满载稳定”；最长 prompt 为
  `245,856` token，仍比 `262144` 少 `16,288`，真实满窗口仍未覆盖。
- soak 的最低 available RAM 为 `2430.4 MiB`；swap 未用，但后续多长读或提高并发必须继续监控
  conversation cache 淘汰和 KV host read，不能只看末值或两卡显存。
- Nsight GPU Metrics 只能说明管线占用和吞吐，不提供 stall reason；不能把 SMs Active、SM Issue
  或 NVML 任一数字单独称为“真实利用率”。NCU 不能附加现役进程，必须使用受控实例。

- 最后更新：2026-11-01（第二十五版计划；质量与工具协议为硬门。Codex 目录供给已产品化；
  Schema 3 当前 Qwen/Strata 基础严格 `5/6`、测试 `6/6`、`apply_patch` 重复 `3/3`，
  DSH/DeepSeek 直连 `6/6`。生产未重启，health loaded、6 slots / 2 groups、
  systemd inactive）
