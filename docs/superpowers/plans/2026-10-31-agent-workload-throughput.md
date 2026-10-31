# 真实 AI vibe coding 优化计划

日期：2026-10-31

状态：第二十一次评审后修订，可以继续执行。

## 全局目标

除非用户明确停止，持续缩短真实 AI vibe coding 任务从提交到产生可用代码、测试结果或
明确阻塞的时间，并减少完成同一任务所需的重复模型工作。当前执行主轴是通常 1 个主代理、
偶尔 2 个的编辑—测试循环；优先消除每一轮都会发生的 prompt/read 与 decode 等待。
每轮必须先用真实触发率和绝对秒数证明收益，不能把局部 kernel 改善或 token/s 当作结果。
当前 frontier 已从“猜相邻 kernel”收窄到 596/94K 普通热续轮 phase map 中最大的可动项；
任何围绕 `wait copy`、GEMM 或 dequant 的候选，都必须先证明生产 routing 的真实触发率和
保守整任务上界，再决定是否改源码或重启。

优先级按用户实际感受到的结果排列：

1. 正确、安全、可恢复；速度不能以错误修改、遗漏测试或工具结果为代价。
2. 普通单代理编辑—测试循环中占主导的模型请求等待，包括热缓存 prompt/read 与真实
   decode；工具执行和轮询先算清边界，不冒充满意收益。
3. 从任务提交到首次代码修改、首个工具结果和首次测试结果的时间。
4. 真实根任务的 p50、p75、p90 完成时间，以及重复读取、重复测试和纠错轮次。
5. 隔天旧会话恢复、长上下文 compaction、recovery、取消和长输出的 p90/p99 尾部。
6. 1 主 3 子代理时的主线程首结果、第二条关键请求 TTFT 和总完成跨度。

token/s、资源利用率和聚合吞吐只用于解释用户等待，不能替代用户任务时间。

关键约束：**没有进入用户实际路径的能力不计作产品收益。** 合成请求、局部微基准和
实验端口只能提供机制证据；要宣称体验改善，必须说明真实客户端、代理或配置的触发路径。

## 真实负载

用户通常运行 1 个主代理，偶尔 2 个，峰值 1 主 3 子代理。`2/4/6/8` 是同时进入模型
服务的在途请求数，不是打开的会话数。

- 1/2 路是主要优化场景。
- 4 路只要求不明显回退。
- 6/8 路只做健康、错误和安全回归。
- 工具执行、用户思考、等待子代理和客户端组装期间可能没有模型请求。

长上下文分成三个场景：

1. 当前会话约 210K token 的 compaction。
2. 放置超过一天后重新打开约 180K token 的旧会话。
3. compaction 后立即回读压缩前分支；真实本地 rollout 的立即触发为 0/109，降为低频诊断。

## 当前硬件与生产事实

TU102 22G 算力解锁是项目已有能力，不是待办项。项目文档记录两张卡的 cuBLAS FP16
性能从锁定参考 `62.40 TFLOPS` 提升到 `93.69/94.07 TFLOPS`，约 `1.51x`；当前 boot
两份日志均为 `unlocked=1`、`result=0 handoff_safe=1`，DKMS 与 initramfs 自动路径已固定。
满载时约 `278 W`，接近两张卡各自 `280 W` 的硬件上限，没有热降频。

解码、解锁和 initramfs 不再研究或修改。核实记录见
[`TU102_UNLOCK_VERIFY-20261031.md`](../../work/TU102_UNLOCK_VERIFY-20261031.md)。

当前 B4 是手动实例，`strata.service` 为 inactive。server cwd 与 engine 路径不同：

- server cwd：`/opt/ai/src/strata-two-stream-serial-20261031`
- engine：`/opt/ai/src/strata-freeze-throughput-20261029T0812Z/build75/strata`
- engine SHA-256：`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`
- config SHA-256：`6ece1a13f4ac2d875bdafd903c7aba23fdce6be5ccc387e17b987a130a486811`
- 当前 PID：server / vision / engine `166198 / 166200 / 166205`
- 形状：双卡 layer split、6 batch slots、2 groups、262144 context、INT8 KV、prompt cache。

生产启动与恢复约需 2–3 分钟。每次重启必须说明预期节省能否覆盖加载、缓存丢失和不可用
成本；能在一个 boot 内完成的动态 A/B 不重复重启。

## 真实成本证据

### 整任务

冻结的 35 文件 Codex 快照包含 152 root、14,768 请求、`10,343,967` output 和
`3,347,547` reasoning token：

| 成本 | 秒数 |
|---|---:|
| 模型区间 union | 69,236 |
| 工具区间 union | 123,693 |
| root wall union | 184,604 |
| 普通单路模型 | 66,228 / 14,653 请求 |
| 恰好两路模型 | 171 |

工具执行是根任务墙钟主体，无法由 Strata 直接消除；普通单路模型请求仍是第二大块。
恰好两路只占很小一部分，不能作为总体第一目标。

34 个有效会话中只有 1 个超过 24 小时间隔、2 个超过 12 小时。223K 旧会话是明显但低频
的 p99 问题，不应排在每轮都会发生的热续轮之前。

后续活动会话的 36 文件编辑—测试快照包含 170 root、34 thread、22,749 个工具调用、
2,318 次编辑和 576 次测试。只有 19 个 root 同时含编辑和测试，占 `11.176%`。首次编辑
前中位 9 个模型轮次、`71.280 s`；编辑到首次测试中位 19 个模型轮次、`137.529 s`。
这些 root 内，首次编辑前模型/工具为 `3,235.782/6,365.546 s`，编辑到首次测试为
`4,072.900/8,241.516 s`。工具仍是更大块，但约 `7.3k s` 的模型段仍直接属于 Strata
可以改善的用户等待。

### 热续轮

同一 B4 服务的 Agent 形状测试：

| 场景 | wall | prompt | decode |
|---|---:|---:|---:|
| 1 路冷首轮 | 7.382 s | 7.308 s | 0.054 s |
| 1 路热续轮 | 1.610 s | 1.522 s | 0.073 s |
| 2 路冷首轮 | 11.075 s | 7.304 s | 0.044 s |
| 2 路热续轮 | 2.290 s | 1.409 s | 0.051 s |

12K 左右上下文的普通热续轮已复用约 95%，短输出 decode 只有约 50–73 ms；用户等待绝大部分
仍在 prompt 路径。真实日志还出现过 `11,857 reused + 596 read`、prompt `1.5 s` 的
编辑—测试热续轮。

静态控制流显示，服务默认用 decode windows 读取最后不超过 64 个新文本 token，代码注释
记录该路径约 `16 ms/token`，而 batched prompt 路径即使很短也有约 `300 ms` 固定开销。
两者交叉点可能在几十个 token，而不是固定 64；因此 `AGENT-HOT-TURN-FIXED-COST` 先验证
同一引擎的 `short_read=64` 与 `short_read=0`，不先改内核。

### 长上下文

- 当前会话 210K compaction 的正常 prompt 为 `0.248–0.283 s`，缓存约 `99.987%`。
- B4 上三次真实 Codex compaction 复用约 `99.1%`，没有新的高价值修复。
- 真实 49 天旧会话为 223,114 prompt token、cached `0`、TTFT `113.014 s`。
- `--ple-inflight 1024` 在真实旧会话三组配对中把 prompt/wall 中位改善
  `2.87%/1.09%`，已作为显式配置保留。
- 4M PLE row cache 使 prompt 慢 `16.23%`、host PLE 慢 `60.49%`，已否决，不扫邻近容量。
- 现有 session file 不支持双卡 layer split、六 batch slots 和 prompt-cache 形状，
  不能直接解决跨重启旧会话恢复。

## 当前优先级

### P0：正确性与真实路径

保持生产健康、工具结果完整、测试正确和长输出不被静默截断。任何候选先证明能进入真实
客户端路径，再讨论性能收益。

### P1：`AGENT-HOT-TURN-FIXED-COST`（已否决）

目标：拆普通 Agent 热续轮约 `1.4–1.5 s` 的 prompt 成本，并验证短读窗口阈值是否让最后
几十个新 token 支付了不必要的固定等待。

静态复核发现动态 `STRATA_PIPELINE_SWITCH` 只存在于 `pipeline-windows` 分支，而生产
`--batch 6` 明确关闭该分支。因此本项改为同一 engine、同一 production config 的
`A64 → B0 → A64r` 三次受控 boot；A64 使用当前进程，B0 和 A64r 各一次手动重载：

- 控制：`short_read=64`
- 候选：`short_read=0`
- 1/2 路各至少 3 波，覆盖短续写和约 596 个新 token 的真实热续轮形状。

通过条件：

- 至少一个普通热续轮尺寸的 prompt 和 wall 配对中位改善至少 1%，绝对节省至少 `0.15 s`；
- 其他 1/2 路尺寸不回退超过 1%；
- 控制臂重复漂移不超过 3%；
- greedy 输出、finish、缓存 token、health、6 slots 和工具路径全过；
- 4 路只做非回退安全回归。

判决：B0 在所有热续轮尺寸都未取得 1% 收益，1 路短热续 prompt/wall 反而慢
`13.75%/8.14%`；保留默认 64，不扫描邻近阈值。见
[`AGENT_HOT_TURN_FIXED_COST_RESULT-20261031.md`](../../work/AGENT_HOT_TURN_FIXED_COST_RESULT-20261031.md)。

契约：
[`AGENT_HOT_TURN_FIXED_COST_CONTRACT-20261031.md`](../../work/AGENT_HOT_TURN_FIXED_COST_CONTRACT-20261031.md)。

### P2：`AGENT-EDIT-TEST-LOOP-TRACE`（已完成，重复测试方向否决）

先只读拆真实 Codex 事件，不改服务、不重启、不输出正文：

- 首个工具调用的额外模型轮次；
- 首次代码修改前的读取和思考；
- 同一测试的重复运行；
- 失败结果回传后的纠错轮次。

要求覆盖现有 35 文件真实 rollout，按会话和 root task 统计命令类别、重复次数、相邻模型
轮次、prompt/cached/output、1/2 路并发和绝对秒数。产物必须给出触发率、最重的两类循环
以及一个带真实触发依据的 >=1% 候选；如果最大成本在 Strata 之外，明确写边界，不把工具
执行或用户思考伪装成模型收益。优先处理每次编辑—测试都会发生、每轮可省数百毫秒的路径。

判决：576 次测试有 178 次命令签名重复，但 173 次之间有新编辑，5 次无编辑重复都落在
成功/失败状态不可判定的调用里；成功或失败后立即无编辑重复均为 0。重复测试、测试缓存和
自动跳过从优化队列剔除。失败回传后的 80 个模型请求共 `294.346 s`，属于正常纠错工作，
可优化部分仍是普通请求延迟。见
[`AGENT_EDIT_TEST_LOOP_RESULT-20261031.md`](../../work/AGENT_EDIT_TEST_LOOP_RESULT-20261031.md)。

### P2b：`AGENT-SPLIT-MTP-BATCH`（已否决）

当前双卡 layer split + MTP 默认由末级 drafter 自己逐 8 行填 prompt K/V。既有 opt-in
`STRATA_SPLIT_MTP_BATCH=1` 可在末级 prompt path 批量完成同一工作；另一台双卡机器的
prompt read 测量为 25K `+6.7%`、51K `+4.3%`、104K `+3.3%`。这与本机真实 94K/189K
热续轮中约 `93%` 的 prompt 墙钟直接对应。

先只读/启动日志确认 native、MTP、末级 drafter、paged/ring 和 scratch 条件真实命中；
拒绝则不跑吞吐。命中后 A1/B/A2 覆盖 596 fresh、94K 和 189K 热续轮，1 路为判决，2 路
报告第二 TTFT 与完成跨度，4 路非回退，6/8 只做安全回归。至少一个普通热续尺寸 prompt
和 wall 改善 `>=1%`、绝对 wall 至少 `0.05 s`，其他级不退 `>1%`，A1/A2 漂移 `<=3%`，
质量、finish、缓存、health 和资源门全过才采纳。

契约：
[`AGENT_SPLIT_MTP_BATCH_CONTRACT-20261031.md`](../../work/AGENT_SPLIT_MTP_BATCH_CONTRACT-20261031.md)。

判决：batched 路径在启动日志中真实命中，94K 热续 prompt 从 `1271.5 ms` 降到
`1241.5 ms`（`-2.359%`），但 wall 从 `1.433790 s` 升到 `1.434450 s`
（慢 `0.046%`），没有达到预注册的 `0.05 s` 绝对收益。按停止门未跑 189K 与并发矩阵；
生产已恢复 A2，进程环境不含该 env。见
[`AGENT_SPLIT_MTP_BATCH_RESULT-20261031.md`](../../work/AGENT_SPLIT_MTP_BATCH_RESULT-20261031.md)。

### P2c：`ENGINE-0.1.55-EVAL`（已否决）

官方 `v0.2.9` 的 Linux CUDA 13 引擎为 `0.1.55`，build
`99866a47a2b8+cuda13.0`，包含 `sm_75`，可能比当前 `0.1.51` 派生构建减少 GDN、批处理
和固定 launch 成本。归档 SHA-256 为
`ce86b23b26a4521a1871113057534b8a34c01df0ac7b916adccf9b1c8c068f92`；engine 和
`libstrata_kq.so` 已与本地/远端逐字核对，远端 `--version` 为 `0.1.55`。

先保持模型、pack、layer split、六 slots、两 groups、cache、采样和生产 env 不变，只把
隔离 config 的 executable 从当前 engine 指向官方包路径。版本级候选先过启动、路径、
health、三组固定 greedy 和 64-token smoke，再跑 A1/B0/A2 的 596 fresh 与 94K 热续轮。
至少一个代表尺寸 wall 改善 `>=1%` 且绝对 `>=0.05 s`、两个尺寸不同向相反、A 漂移
`<=3%` 才继续；94K 无 wall 方向收益立即停止，不跑 189K 或全矩阵。

版本级默认没有收益时，才单独评估官方引擎的 `STRATA_GDN_TC=1` /
`STRATA_GDN_CONVL2=2` Turing 路径；sm75 runtime gate 未命中的 env 不能算收益。契约：
[`ENGINE_0155_EVAL_CONTRACT-20261031.md`](../../work/ENGINE_0155_EVAL_CONTRACT-20261031.md)。

判决：官方 CLI 不接受本地 `--prompt-cache-long`，移除该 128K-only 选项后成功启动。
正确性全过，但 596/94K hot-long wall 相对 A1/A2 参考中位分别慢
`5.853%/4.259%`，绝对慢 `0.087835/0.059525 s`；A 漂移均在 3% 内。版本级候选否决，
不进入官方引擎的 GDN env、189K 或并发矩阵。见
[`ENGINE_0155_EVAL_RESULT-20261031.md`](../../work/ENGINE_0155_EVAL_RESULT-20261031.md)。

### P2d：`GDN-CONVL2`（已否决）

当前旧 engine 已包含 `STRATA_GDN_CONVL2`。在超过 64-token 的 GDN prompt chunk 中，
它把 conv 与 Q/K L2 norm 融进一个 block，省一次 launch 和 `h` 中间写回；V 通道、
recurrence、模型和 cache 不变。当前 `--prefill auto` 实际用 8192-token chunk，进程不含
`STRATA_GDN_CONV_SERIAL`，目标分支可达。

B 只在 A1 env 中增加 `"STRATA_GDN_CONVL2": "2"`。先过启动、实际 env、6/2、health、
三组固定 greedy 和 smoke；随后 A1/B/A2 测 596 fresh 与 94K。至少一个 hot-long wall
改善 `>=1%` 且绝对 `>=0.05 s`、两个尺寸不同向相反、A 漂移 `<=3%` 才继续；94K 无
wall 方向收益立即停止。契约：
[`GDN_CONVL2_CONTRACT-20261031.md`](../../work/GDN_CONVL2_CONTRACT-20261031.md)。

判决：B 通过启动、真实 env、六 slots / 两 groups、health、三组 fixed greedy、smoke 和
全部请求质量门；A2 prompt/wall 漂移为 596 `-0.2483%/-0.3875%`、94K
`+0.2461%/+0.0359%`。但 B 相对 A1 的 596/94K wall 只改善
`0.8245%/0.2051%`，绝对仅 `0.012476/0.002898 s`，没有达到任一性能门。候选否决，
不跑 189K、并发矩阵或邻近 GDN 参数；生产已恢复原 engine/config。见
[`GDN_CONVL2_RESULT-20261031.md`](../../work/GDN_CONVL2_RESULT-20261031.md)。

### P2e：`AGENT-HOT-TURN-PHASE-MAP`（已完成）

普通 596 fresh-token 与 94K 热续轮的 prompt 仍占 wall 约 `93%`，但连续四个局部候选
只得到 prompt 改善或很小的 wall 变化。下一项不再猜 kernel，直接用当前生产 engine
已有的 `STRATA_PREFILL_TIMING=1` 做一次 profiling boot，取得同口径 596/94K 的 21 个
有序 prefill phase、host staging 与主/对端 GPU 数值。

profiler 只用于定位，不计入性能收益。至少一个 phase 在代表请求中绝对成本
`>=0.05 s`、且源码能证明一个不改变每行算术、token 选择、finish 与工具语义的可移除部分，
并据其实测占比得到 wall `>=1%` 保守上界后，才预注册实现候选。若找不到这样的主相位，
停止 kernel 邻域微调，转向 server admission/cache 或客户端请求结构的下一个证据支持项。
契约：
[`AGENT_HOT_TURN_PHASE_MAP_CONTRACT-20261031.md`](../../work/AGENT_HOT_TURN_PHASE_MAP_CONTRACT-20261031.md)。

结果：同口径 596/94K 热长轮实际 fresh 读取都约 554 token。三波中位 stage 1 为
GPU timeline `459 ms`、host staging `501 ms`；stage 2 为 `647/788 ms`。stage 2
最大 phase 是 `wait copy 173 ms`，随后 `gemm gate/up 130 ms`、`dequant 106 ms`、
`gemm down 68 ms`。因此下一候选直接验证更深的 streamed expert ring，而不是继续
GDN/短读/版本邻域。

### P2f：`AGENT-HOT-TURN-STREAM-RING`（已否决）

554-token chunk 低于默认 `stream_all_min=1024`，当前走 32-slot staged walk，实际保持
8 个在线传输在前，stage 2 的 copy 等待达到 `173 ms`。B 只设置现有 env
`STRATA_PREFILL_STREAM_MIN=0`，让同一真实请求进入更大的 streamed ring。

乐观上限是把 stage 2 copy 等待隐藏到 stage 1 的 `13 ms`，约 `11%` wall；即使只隐藏
10%，仍超过 1%。A1/B/A2 必须覆盖同口径 596/94K、1 路三波；至少一个 wall 改善
`>=1%` 且绝对 `>=0.05 s`，两个形状不反向，A 漂移 `<=3%`，质量/health/资源全过。
1 路通过后测 2 路第二 TTFT 与完成跨度，4 路非回退，6/8 安全回归。失败立即恢复 A，
不扫描 stream threshold、ring 容量或邻近参数。契约：
[`AGENT_HOT_TURN_STREAM_RING_CONTRACT-20261031.md`](../../work/AGENT_HOT_TURN_STREAM_RING_CONTRACT-20261031.md)。

判决：B 通过路径、质量、健康与资源门，但 596 hot-long wall 从 A1 中位
`1.509808 s` 升到 `1.898041 s`，慢 `25.714%`，绝对多 `0.388233 s`；prompt 中位从
`1401.7 ms` 升到 `1787.9 ms`。该分支同时切换 whole-chunk stream、shared-expert defer
和 cross-layer issuer，整体明确反向，但不能单独归因给 ring 深度。按门未跑 94K/并发
矩阵，生产已恢复 A2。
见
[`AGENT_HOT_TURN_STREAM_RING_RESULT-20261031.md`](../../work/AGENT_HOT_TURN_STREAM_RING_RESULT-20261031.md)。

### P2g：`AGENT-HOT-TURN-ROUTING-PREFETCH-AUDIT`（已完成，关闭跨层推测）

phase map 仍显示 stage 2 有 `173 ms wait copy`，但 full-ring 已证明盲目加深预取会亏。
审计检查了以下问题：

- 554-token chunk 的 top-k routing 在 copy 开始前多早可用，是否完整；
- 当前 staged walk 为什么实际只保持 8 个在线传输在前，哪些等待来自 ring 容量、routing 可见性或
  host/GPU 同步；
- 只预取本 chunk 实际选中专家能否减少 copy，最坏路由密度和额外 bookkeeping 是什么；
- 以真实 596/94K 三波 phase 和绝对秒数计算，单次整请求保守上界能否同时达到
  wall `>=1%` 与 `>=0.05 s`；
- 改动是否保持每行算术、top-k 结果、token 选择、finish 和工具语义逐字不变，以及能否
  用 env 精确关闭。

审计结论：生产 staged walk 在每层 router 完成后读取该层 top-k，并按实际选中专家建序；
它已经只拷贝本层 routed、非驻留专家。后续层 routing 依赖当前层输出，源码没有提前获得整
chunk 所有层 top-k 的路径，因此“跨层只预取 selected experts”不是现有能力。554 x top-10
有 `5,540` 个 routed pairs、每层 512 个专家，也不是少量专家形状。full-ring B 同时切换
whole-chunk stream、shared-expert defer 和 cross-layer issuer，不能用它得出单一 ring
深度结论。跨层推测 routing 关闭，不构建。

### P2h：`AGENT-HOT-TURN-STAGED-PREFETCH`（已否决）

审计保留的唯一直接可动项是 grouped staged walk 的实际 lookahead：当前保持 8 个传输在前，
而 32-slot ring 与 16-expert gather 允许安全保持 15 个。候选只在默认 8 之外增加 env
`STRATA_PREFILL_STAGED_AHEAD=15`，保留同一 routed 顺序、shared-expert defer、group、
copy stream、release event 和 kernel 顺序。

stage 2 `wait copy` 中位 `173 ms`；只需隐藏 `15.1 ms` 就达到 596 wall 的 1%，隐藏
`50 ms` 达到绝对 `0.05 s`。A1/B/A2 覆盖同口径 596/94K；失败立即恢复 A2，不扫描邻近深度、
stager thread/batch/ring 或 stream threshold。判决：B 通过路径、质量、健康与资源门，
但 596 wall 只从 `1.510583 s` 变成 `1.505333 s`，改善 `0.3475%`、绝对 `5.25 ms`；
prompt 从 `1384.3 ms` 变成 `1392.6 ms`，慢 `0.5996%`。按停止门未跑 94K 和并发矩阵，
生产已恢复 A2。见
[`AGENT_HOT_TURN_STAGED_PREFETCH_RESULT-20261031.md`](../../work/AGENT_HOT_TURN_STAGED_PREFETCH_RESULT-20261031.md)。
契约：
[`AGENT_HOT_TURN_STAGED_PREFETCH_CONTRACT-20261031.md`](../../work/AGENT_HOT_TURN_STAGED_PREFETCH_CONTRACT-20261031.md)。

### P2i：`AGENT-HOT-TURN-DEQUANT-AUDIT`（当前，只读）

phase map 中 stage 2 下一大可证伪相位是 `dequant 106 ms`。先只读追踪 native IQ3_XXS
的热续轮 grouped staged path：同一 expert/layout 是否在 stage 1、stage 2 或 MMQ gather
前后被重复转换；生产实际 `T=554`、每层 `5,540` 个 routed pairs 下命中率是多少；哪些写回
是后续 kernel 必需，哪些只是中间格式重复。

只有源码和数据共同证明存在单一可移除重复工作，且按实测占比给出整请求 wall `>=1%` 与
绝对 `>=0.05 s` 的保守上界，才预注册实现候选。审计本身不改源码、不重启，也不扫描
dequant 参数、group 大小或 profiler 邻域。若不存在重复工作，关闭该方向并复盘下一个真实
主相位，不能把“看到 106 ms”当成收益。

### P3：旧会话跨重启状态驻留（低频 p99）

保留为独立可行性项：

- 多 stage KV/checkpoint 的可序列化布局；
- 180K/223K 状态大小、磁盘写放大、恢复时间和 RAM 峰值；
- 会话标识、模型/配置指纹、失效和安全删除；
- 只在空闲或淘汰时持久化，避免每轮写数 GB；
- 同进程 RAM cache、磁盘状态和重新 prefill 的成本边界。

只有真实旧会话等待减少至少 1%，且保存成本不转移到普通轮次，才允许实现。

### P4：并发角色感知

仅在真实 trace 显示 2 路触发率显著上升，或用户明确以并行子代理为主时继续。2 路串行
当前把第二条 TTFT 推到约 5.1 秒，必须同时报告第二请求 TTFT、单请求 wall 和完成跨度。

## 已否决与不再重复

- 上下文饮食真实客户端配对：wall 中位慢 `41.276%`，input 中位多 `38.190%`，否决产品化。
- 4M PLE row cache：prompt、host PLE、PLE phase 全部反向，否决并不扫邻近容量。
- PLE `mmap`：wall 慢到 `182.157 s`，否决。
- MTP draft-head 微调：全部消除的乐观 root 上界约 `0.8%`，关闭。
- 两路聚合吞吐作为总体主目标：恰好两路模型 union 只有 `171 s`，降为角色场景。
- compaction 后立即回读旧分支：真实触发 0/109，降为低频诊断。
- 已关闭的 kernel、head split、poll、LFUSE、cache 参数和同形组合不得重开，除非出现
  新的真实触发证据。

## 验收门

普通候选必须用真实触发率和单次绝对秒数证明：

```text
预期任务收益 =
  真实触发次数 x 单次关键路径节省
  + 减少的后续轮次 / compaction / 工具回流收益
  - 新增排队、TTFT、decode、磁盘或失败成本
```

- 1% 也算收益，但必须有绝对秒数和真实触发依据。
- 1 路不退化；2 路同时报告第二请求 TTFT、单请求 wall 和完成跨度。
- 4 路不得回退超过 1%；6/8 路零错误、health loaded、无严重资源退化。
- 新能力默认关闭、可精确回滚；未采纳时默认路径保持字节相同。
- 重启前证明收益能覆盖约 2–3 分钟加载、缓存丢失和不可用成本。
- 客户端观察、服务器 timing、静态阅读和推断值必须分开标注。
- 质量门至少覆盖输出、finish、工具调用、代码修改和测试结果。

## 每轮强制复盘

形成判决和选择下一项前，必须回答：

1. 是否命中最高频的用户等待，而不是低频局部现象？
2. 是否改善真实 vibe coding 的编辑—测试路径？
3. 预计收益是否至少 1%，绝对节省多少秒，触发频率依据是什么？
4. 是否仍是在局部参数或已否决机制附近打转？
5. 为什么高于另外两个候选？
6. 重启成本是否值得，能否在同一 boot 内完成？

## 当前断点

`AGENT-CONTEXT-DIET-REAL-YIELD` 已否决产品化；`AGENT-HISTORY-STATE-RESIDENCY` 降为
低频 p99 可行性项。

`AGENT-HOT-TURN-FIXED-COST` 已否决并恢复 A64。`AGENT-EDIT-TEST-LOOP-TRACE` 已完成：
没有可产品化的重复测试浪费，主要模型等待来自正常编辑—测试轮次。
`AGENT-SPLIT-MTP-BATCH` 也已完成：路径真实命中，94K prompt 改善 `2.359%`，但 wall
反向 `0.046%`，因此否决并恢复生产。`ENGINE-0.1.55-EVAL` 随后通过启动和正确性门，但
596/94K wall 慢 `5.853%/4.259%`，版本级候选否决并恢复旧引擎。`GDN-CONVL2` 随后
以真实 env、greedy 和质量门通过，但 596/94K wall 只改善 `0.8245%/0.2051%`，
绝对节省 `0.012476/0.002898 s`，因此也已否决并恢复 A2。当前执行
`AGENT-HOT-TURN-PHASE-MAP` 已完成：596/94K 热长轮都只约有 554 个 fresh token；
stage 2 的 `wait copy 173 ms` 是最大可移动相位。当前执行
`AGENT-HOT-TURN-STREAM-RING` 已否决：full ring 让 596 wall 慢 `25.714%`，没有把 stage 2
copy 等待转成用户收益。当前生产为 server/vision/engine
`169846/169849/169854`，health loaded、六 slots / 两 groups，`strata.service` inactive。

只读 routing 审计已完成：当前层 selected-only staged copy 已是生产路径，跨层推测 routing
没有源码支持；full-ring 还切换了多个调度机制。`AGENT-HOT-TURN-STAGED-PREFETCH` 随后把
grouped staged walk 的实际传输在前数从 8 提到安全上限 15，但 596 wall 只改善
`0.3475%`、绝对 `5.25 ms`，prompt 反慢 `0.5996%`，因此否决并关闭该参数轴。当前生产已
恢复 A2。下一项执行只读 `AGENT-HOT-TURN-DEQUANT-AUDIT`，检查 `dequant 106 ms` 是否有
同一 expert/layout 的重复转换；在给出真实触发、整请求保守上界和回滚前不改源码、不重启。
