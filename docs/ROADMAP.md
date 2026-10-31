# 优化路线与指标

> 这是动态路线组合，不要求按表从上到下执行。AI 每轮按 `RULES.md` 重新比较价值、成本、信息量和风险。
> 最后更新：2026-11-01（恢复执行；质量与工具协议为硬门）。

## 用户目标

在不牺牲正确性、任务成功率和可恢复性的前提下，优化真实 AI vibe coding 工作流：通常
1 个主代理、偶尔 2 个、峰值 1 主 3 子代理。主目标是更短的任务完成时间、首个可执行结果、
更少的重复模型工作和更短的严重尾部；1/2 路为主，4 路非回退，6/8 只保留安全回归。

## 当前指标

| ID | 指标 | 最近基线 | 目标 | 状态 |
|---|---|---|---|---|
| M1 | 4 路聚合 tok/s | 数字 `139.92`、自然语言 `129.03`（`c219fbca`） | 不劣于同负载基线 3%，并保留单路/2 路效率 | 已建立新构建基线 |
| M2 | 单路 engine decode tok/s | 数字 `104.05`、自然语言 `68.73`（`c219fbca`） | 不劣于同负载基线 3% | 已建立新构建基线；2 路有凹点 |
| M3 | 22.7K 冷读首 token | 17.9 s | ≤读时间 + 2 s | 达成 |
| M4 | 长读时邻居最长静默 | 7.09–7.59 s（S0b ON 中位）；3.43 s（R11） | ≤1 s | 明显未达 |
| M5 | 满槽短读首 token | 6 槽 11.84 s（400-token 邻居填充） | 不继续恶化，并有准入策略 | 已改善，条件待扩展 |
| M6 | 投机接受率 | 82.0% | ≥79.5% | 达成 |
| M7 | 前缀缓存命中 | T4 PASS | ≥50% | 达成 |
| M8 | 满槽晚到请求 | 6 槽 11.84 s；8 槽 14.38 s | 给出上界与准入策略 | 已测 |
| M9 | 未预期错误/限流 | 未系统测 | 0 未预期错误 | 未测 |
| M10 | 96K prefill 邻居下短请求 wall | 3.40 s 中位（P3 A-B-A） | ≤36.75 s（−10%） | 达成 |
| M11 | 96K prefill 邻居下 41-token 代码请求 wall | P3 E 臂 41.51 s；P4 旧候选 4.424 s；新引擎 A-B-A 5.074 s | ≤10 s，且 96K 按预先固定的 wave 与单请求门限均不退 >3% | 达成：wave +2.202%，单请求 +2.761% |
| M12 | 96K 冷 prefill GPU/host 成本 | 当前默认 v2 的 engine read 合并中位 40.27825 s、wall 40.5935 s；v1 fallback 为 41.0226/41.382 s | 找到并验证稳定、可归因的 ≥1% GPU 路径改善 | P6 复验通过：read `+1.814%`、wall `+1.905%` |
| M13 | 32K 冷 prefill GPU 管线占用 | SMs Active 77%、SM Issue 13–14%、Tensor 10%、DRAM read 15%；NVML 40/99% 与 95/98% | 用 NCU 定位 stall 后改内核/调度；瓶颈计数器和端到端 wall 同向改善 | Nsight 双次已验证；NCU 后进入代码优化 |
| M14 | 多并发吞吐曲线 | window-profile 的 A1/A2 合并中位 2/4/6/8 路为 `80.010/108.214/122.703/107.214 tok/s` | 2 路高于单路，8 路不回退且 TTFT 可控 | 2 路与 8 路均未达；6 路为峰值，8 路尾部已归因 |
| M15 | Agent 会话前缀复用 | 同会话续轮约 95.2%；69% 共享前缀的新会话首轮 prompt 约 3.2 s | 新会话/续轮继续提高复用，且 1/2 路稳定改善 ≥1%、4 路不退 >1% | 缓存主体已工作；转入非正文 Codex 请求和 compaction 线程隔离 |
| M16 | Codex title 请求 | A2 每个 title 进入引擎，`1.978–10.292 s`，额外 146–184 output token | 本地 title 路径 0 引擎请求、0 prompt/output token，合法标题且普通请求不受影响 | 已采纳：B 为 `1.8–2.1 ms`、零请求/token，普通请求仍进引擎 |
| M17 | 多线程 Codex compaction 前缀 | 工具集合仅保留最后线程，1 主 3 子代理交错时早先线程可能重读整段历史 | 有界线程缓存使每个活跃线程保留自己的工具前缀；默认路径字节不变，LRU 淘汰和并发测试通过 | 已采纳：4 个线程各保留前缀，compaction 复用约 99.5%，墙钟 `0.69–0.89 s` |
| M18 | 兄弟 Agent 共享 system 前缀 | tools 位于共享 agent instructions 之前；工具集合不同会使 10K 级通用 system 前缀失配 | 顺序调整只改变 tool block 与 system block 的相对位置，不改变内容；真实工具调用/质量门通过，缓存复用带来端到端收益 | 已采纳：后续兄弟请求复用 `5,120` token，平均墙钟 `-24.56%`，后三请求 `-33.34%` |
| M19 | 固定共享 system 边界 | system-before-tools 已共享长前缀，但首个缓存 checkpoint 停在 `5,120` token | 验证现有 `strata_prefix` pin 能在同一真实负载上提高复用，并给出整任务 ≥1% 上界 | 已采纳：自动 pin 后兄弟复用 `97.34%`，后三墙钟 `-73.31%`；安全门全过 |
| M20 | 长历史合成 compaction 复用 | 客户端真实会话的 compaction 长期只有约 3.5–7.6K cached | 同一 Strata HTTP 路径下，构造的长历史 compaction 复用接近前一轮普通 turn，且正常 completed | 构造验证通过：180K 输入复用 `99.984%`，wall `2.385 s`；cold turn `65.555 s`；真实形状转 M27 |
| M21 | 真实 Codex session 成本结构 | 29 小时、3,614 请求；cached `97.22%`，uncached `17.82M`，output `2.77M` | 用真实请求形状筛掉低于 1% 整任务上界的局部候选，并定位下一大头 | 工具化完成：`probe-codex-sessions.py`，4 tests OK，不输出 prompt 正文 |
| M22 | 1/2 路热续轮成本 | 12K Agent prompt 续轮 cached 约 95%，wall `1.61/2.29 s`，prompt `1.52/1.41 s`，短输出 decode 仅 `73/51 ms` | 先把真实尺寸、频率和秒数合成成本地图，再选整任务 ≥1% 的下一机制 | 94K/189K 热续轮 `2.09/2.43 s`；精确 pin 合成分支验证成功但真实立即触发 0/109；先由整任务 trace 重排 |
| M23 | 恰好两路的总完成时间 | 512-token 完成跨度 `11.776 s`；64-token `1.692 s` | 两路至少改善 1%，1/4 路不退，3 路恢复 batch | **已采纳**：512-token `10.004 s`（`-15.05%`），64-token `1.554 s`（`-8.18%`）；6 路安全 |
| M24 | 隔天约 180K 旧会话恢复（分类基线） | 180K 新合成冷 turn `65.555 s`；仅作尺寸代理 | 必须由真实 aged session 覆盖，不能把合成 cold turn 当结论 | 已由 M32 的真实 49 天会话取代 |
| M25 | 真实 vibe coding 整任务时间账本 | 冻结快照 14 root、4,180 请求、6,297 工具、97.09% cached；output p50/p90/p99 `483/1,714/5,069`；最长 root `44,783 s` | 关联 root、模型、工具、compaction、recovery、取消和空档；明确 Strata 可优化上界 | **已完成**：`AGENT-END-TO-END-TRACE`；最长 root 模型 `14,855 s`、工具 `31,088 s`、空档 `15.843 s` |
| M26 | 真实 output/reasoning 形状 | 480-token 1 路 `4.682 s`；1708-token `16.385 s`；两路串行第二请求 TTFT `4.7/16.384 s` | 用真实分布重放 1/2/4 路，禁止只用 64-token 短输出判定 decode | 基础测量完成；完整 rollout 中恰好两路模型并发仅 `171.303 s`，角色调度降为后续 |
| M27 | 真实 compaction 形状对齐 | 冻结 29 小时快照 44 次 compaction、provider 为 DeepSeek 直连；B4 上 3 次真实 Codex compaction prompt `451.5–529.4 ms`、cached `99.1002–99.1304%` | 区分客户端 provider 与 Strata，并在 B4 上以真实 Codex 请求验证 kept-tool 路径 | **已完成**：当前缓存路径无需改动，不重启；见 `AGENT_COMPACTION_REAL_SHAPE_RESULT` |
| M28 | 大工具输出的上下文成本 | 26 个 rollout、14,531 请求；工具输出 7,974 万字符，`>=10K` 占 `63.985%`，`2K-10K` 占 `28.309%` | opt-in、保留错误/小输出和完整可重读文件；在真实 coding task 上缩短墙钟，同时保持代码/测试质量 | **已完成**：首次出现即折叠的三组配对墙钟改善 `13.63%/47.30%/20.28%`；延迟折叠因缓存前缀失效被否决；不重启 |
| M29 | 真实 decode 成本形状 | 35 文件、152 root、14,768 请求；output/reasoning `10,343,967/3,347,547` token；单路模型 union `69,064.894 s`、两路 `171.303 s`；416/1490/4483 单路 `4.240/14.347/45.451 s`，两路 `8.199/28.919/85.701 s` | 按请求类型、输入/输出尺寸和 1/2 路并发拆 decode；只有真实触发率后的整任务 `>=1%` 上界才实现一个可回滚机制 | **已完成**：`AGENT-REAL-DECODE-SHAPE`；4483 两路出现两条提前 `stop`，长输出稳定未全过门 |
| M30 | 生产 decode 剖析器成本 | A1-B-A2-B2；416/1490/4483 A 相邻中位 `4.188/14.395/42.322 s`，B `3.911/13.620/37.230 s` | 至少一个主要尺寸中位改善 >=1%，A2 漂移 <=3%，finish/hash/health/6 slots 全过 | **已采纳**：416/1490 改善 `6.61%/5.38%`，A2 漂移 `2.45%/0.66%`；4483 漂移 13.77% 不用于归因；B2 无仪器在产 |
| M31 | MTP draft 内部成本 | draft round `2.463 ms`；round graph `0.978 ms`、step GPU `1.451 ms`、host graph gap `0.017 ms`；draft-head 全消除的乐观 root 上界约 `0.8%` | 只有可移除组件能在真实触发后保守改善整任务 >=1% 且不改 draft/verify/sampling/finish 才实现 | **已完成并关闭**：host 可移除成本可忽略，head 全消除也不到 1%；不扫 depth、阈值、group、cache |
| M32 | 隔天旧会话恢复 | 真实 49 天会话，223,114 prompt token，cached `0`，TTFT `113.014 s`，wall `113.3 s`，decode `292.8 ms` | 记录真实年龄、input/cache、prompt/TTFT/wall、finish 和资源；不用合成 180K 或 compaction 代替 | **诊断完成**：等待时间约 99.7% 在首个 token 之前，p99 主成本是冷 prefill |
| M33 | 223K 冷 prefill 相位 | 256 在途的 prompt `135,073.4/132,645.8 ms`，A 漂移 1.80%；host PLE `50,362 ms`、主 PLE `39,291 ms` | 取得 21 相与 host 时间线，选一个单相保守节省 >=`1.13 s` 的机制 | **完成**：`mmap` 慢到 `182.157 s` 被否决；direct inflight 进入 M34 |
| M34 | PLE direct 在途读深度 | 20K token 生产表微基准 256/512/1,024 为 `786.9/768.3/695.9 us/token`；1,024 快 11.56% | 只改 256→1,024；profile 的 prompt/PLE 同向；三组非剖析 prompt 全同向，配对中位至少 1%；默认保持 256 | **已采纳为显式配置**：prompt 改善 `2.87%/1.10%/5.58%`，wall `0.05%/1.09%/5.21%`，配对中位 `2.87%/1.09%` |
| M35 | PLE row-cache 容量 | 默认 1,048,576 行约 95 MB；冻结语料同一长 prompt 内 20–34% 行重复 | 从 1,024 在途基线只改 `--ple-row-cache 4194304`；profile 三项同向，至少两组非剖析配对同向且中位 >=1%；失败不扫邻近容量 | **否决**：prompt 慢 `16.23%`、host PLE 慢 `60.49%`、PLE phase 慢 `24.07%`；恢复 1,024-inflight |
| M36 | 上下文饮食真实覆盖率与使用路径 | 真实 36 会话、15,338 请求、2,332 个可折叠大输出；选择门 29/36 会话估算 >=1%，总计 `12.9739%` input token。三组真实 Codex 配对 diet wall 慢 `17.365%/86.935%/41.276%`，input 中位多 `38.190%` | 至少 1% 墙钟改善且编辑/测试/artifact/finish/轮次全过；默认路径字节不变 | **否决产品化**：六臂最终测试/artifact 正确，但墙钟和轮次明确反向；转 M37 |
| M37 | 跨重启旧会话状态驻留可行性 | 真实 223K resume `113.014 s`、cached `0`；现有 session file 拒绝 layer split/batch/prompt-cache 形状 | 只读证明多 stage 状态可序列化、容量与恢复边界；真实旧会话至少减少 1%，保存成本不转移到每轮 | **搁置为低频 p99**：`AGENT-HISTORY-STATE-RESIDENCY`；34 个有效会话仅 1 个超过 24h |
| M38 | 普通 Agent 热续轮固定成本 | 12K 热续轮 cached 约 95%，prompt `1.4–1.5 s`，decode 仅 `50–73 ms`；真实有 `11,857 reused + 596 read` | 同一引擎/生产配置的 `A64/B0/A64r`；至少一个热续轮 prompt/wall 中位改善 1%、绝对节省 0.15 s；其他尺寸不退 >1%，A64r 漂移 <=3%，质量/health 全过 | **否决**：B0 无胜场；1 路短热续 prompt/wall 慢 `13.75%/8.14%`；保留默认 64，不扫邻近阈值 |
| M39 | 真实 Codex 编辑—测试循环 | 36 文件、170 root、34 thread、22,749 工具、2,318 编辑、576 测试；19 root 同时含编辑/测试 | 拆首工具、首修改、首测试、无编辑重复测试、失败纠错和阶段模型/工具秒数；形成真实触发支持的下一候选 | **已完成**：首修改前模型/工具 `3,235.782/6,365.546 s`，修改到首测试 `4,072.900/8,241.516 s`；178 次签名重复中 173 次之间有编辑，无编辑重复 5 次，成功/失败后立即重复 0；重复测试方向否决 |
| M40 | 双卡 MTP prompt draft K/V | 当前双卡 layer split、MTP、native pack、末级 drafter；94K A2 prompt/wall `1271.5 ms/1.433790 s` | 启动日志证明 batched 路径命中；普通代表尺寸 prompt/wall 改善 >=1%、绝对 wall >=0.05 s，其他 1/2 路不退 >1%，4 路不退，6/8 安全 | **否决**：路径真实命中；B prompt `1241.5 ms`（`-2.359%`），wall `1.434450 s`（`+0.046%`），未过 `0.05 s` 门；未跑 189K/全矩阵 |
| M41 | 官方 `0.1.55` 引擎版本级收益 | 当前 `0.1.51` 派生 engine `105a4171...`；官方 CUDA13 engine `e4406b47...`、build `99866a47a2b8+cuda13.0` | 同一生产 config 只替换 executable；启动/路径/health/greedy/smoke 通过；596 或 94K wall 改善 >=1% 且绝对 >=0.05 s，另一尺寸不同向相反，A 漂移 <=3% | **否决**：移除版本不支持的 128K-only `--prompt-cache-long` 后启动/parity/smoke 全过，但 596/94K wall 慢 `5.853%/4.259%`，A 漂移 `<3%` |
| M42 | prompt GDN conv + Q/K norm 融合 | A1 596/94K hot-long prompt/wall `1409.4 ms/1.514727 s`、`1259.4 ms/1.413078 s`；B 为 `1394.2 ms/1.502251 s`、`1258.6 ms/1.410180 s` | B 只增加 `STRATA_GDN_CONVL2=2`；启动、实际 env、6/2、greedy/smoke 全过；596/94K 至少一个 wall 改善 >=1% 且绝对 >=0.05 s，两个尺寸不反向，A 漂移 <=3% | **否决**：质量全过；wall 只改善 `0.8245%/0.2051%`，绝对 `0.012476/0.002898 s`；A2 漂移 `<0.4%`；生产已恢复 |
| M43 | 普通热续轮 prefill 主相位 | 596/94K prompt 仍占 wall 约 93%；连续 short-read、MTP batch、官方版本与 GDN conv 候选均未穿透到 1% wall | 一次 `STRATA_PREFILL_TIMING=1` profile 取得 21 相、host staging 与双卡数值；找到单相绝对 >=0.05 s、真实可移除且 wall 保守上界 >=1% 的机制 | **已完成**：596/94K 实际 fresh 均约 554 token；stage 1 GPU/host `459/501 ms`、stage 2 `647/788 ms`；stage 2 最大项 `wait copy 173 ms` |
| M44 | 554-token 热续轮加深 streamed expert ring | phase map 的 stage 2 `wait copy 173 ms` 占 GPU timeline `26.7%`；乐观 wall 上界约 11% | B 只设 `STRATA_PREFILL_STREAM_MIN=0`；路径、质量、健康与资源门通过后，596/94K 至少一个 wall 改善 >=1% 且绝对 >=0.05 s，两形状不反向，A 漂移 <=3% | **否决**：596 wall `1.509808→1.898041 s`，慢 `25.714%`、绝对多 `0.388233 s`；whole-chunk 分支同时改变 shared defer 与 issuer，不能单独归因 ring 深度；未跑 94K/并发 |
| M45 | routing-aware expert prefetch 可行性 | staged walk 已在每层 router 后按实际 routed、非驻留专家建序；后续层 routing 依赖当前层输出 | 只读确认 routing 可用时点与跨层可预见性，判断是否已有 selected-only 路径、能否更早预取，并给出整请求上界与回滚 | **已完成并关闭跨层推测**：没有提前获知后续层 routing 的源码路径；554 x top-10 为 `5,540` pairs、每层 512 专家，不是少量专家 |
| M46 | grouped staged expert lookahead | 当前 grouped staged path 保持 8 个实际传输在前；32-slot ring 与 16-expert gather 的安全上限为 15；stage 2 `wait copy` 中位 `173 ms` | 默认 8 不变，B 只设 `STRATA_PREFILL_STAGED_AHEAD=15`；质量/路径门全过；596/94K 至少一个 wall 改善 >=1% 且绝对 >=0.05 s，两形状不反向，A 漂移 <=3% | **否决**：路径/质量全过；596 wall `1.510583→1.505333 s`，只快 `0.3475%`、绝对 `5.25 ms`；prompt 慢 `0.5996%`；未跑 94K/并发，A2 已恢复 |
| M47 | 热续轮 dequant 重复工作审计 | stage 2 `dequant 106 ms`；production 为 native IQ3_XXS、554 fresh token、grouped staged path | 只读证明同一 expert/layout 是否重复转换、真实触发率和生产路径；单一可移除部分须给整请求 wall >=1% 且绝对 >=0.05 s 上界、数值/回滚边界；未过门不构建不重启 | **否决重复假设**：该 phase 实为 native gather 字节搬运；每层只处理唯一 selected expert，每 16 个一次 gather；stage 1/2 不同层，无重复转换；MMQ 固定 stride 不能直读任意 cache slot。未改源码/未重启，不扫参数 |
| M48 | resident expert 绕开 MMQ gather | stage 1/2 gather 合计约 `183 ms`；group 级 direct path `7.848` 对 MMQ `11.671 ms/layer`（`1.49x`）；596 1 路 wall 改善 `1.862%`（相对 A1 `2.145%`、`32.667 ms`），三波全胜 | group 级上界、路径命中、greedy 和 1/2 路测量已完成；原契约绝对 `0.05 s` 未过，94K prompt 无对应改善，4/6/8 未完成 | **暂停验收，未采纳**：用户的 1% 价值原则记录为正向信号，但不静默放宽契约；候选默认关闭，已恢复 A1 生产。恢复后先审计 48 层 direct-path 覆盖/fallback，而非重跑同形 A-B-A |
| M49 | Codex 模型目录与 coding 质量 | 无目录时 9 工具且无 `apply_patch`；目录后 14 工具且为 `custom`。Schema 3 Qwen/Strata 基础严格 `5/6`、测试 `6/6`、`159.777 s`；`apply_patch` 独立重复 `3/3` | 产品化幂等目录供给；真实工具面无错；基础 fixture 测试不变、最终 sentinel、无事件错误；不改变引擎和量化 | **目录供给完成，质量门仍开放**：`strata codex catalog [--apply]` 已产品化；唯一基础失败是最终回复多写说明。Schema 1/2 的 `7/7`、`3/4` 已被取代 |
| M50 | Codex/Strata 与 DSH 终端质量对照 | DSH `0.2.1-rc.3`；Schema 3 同为 6 个基础 fixture，DSH/DeepSeek 直连 `6/6`、测试 `6/6`、`71.391 s`；Qwen/Strata `5/6`、`159.777 s` | 报告不混淆 runner/model/provider；判断 DSH 能否经 Strata endpoint 运行；至少重复一次并保留失败类型 | **基础对照完成，配置路径待查**：当前 DSH 是 DeepSeek 直连；串行耗时不可归因 Strata 引擎 |

数字的条件、证据和“没测什么”以 [`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md) 为准。

## 当前主要矛盾

用户反馈的“明显更快、质量略降”先按质量事故处理，而不是继续追局部 1%：Codex 模型目录
缺失是已复现的确定性协议问题，但不是完整解释。目录供给已经进入产品命令；Schema 3
Qwen/Strata 的代码和测试都是 `6/6`，剩余基础失败集中为“任务正确但没有严格只回复
sentinel”。DSH/DeepSeek 直连为 `6/6`，但模型、量化、provider 和 hosting 同时改变，
只能作为终端路径对照。当前目标是把 DSH 正式纳入同口径、消除报告标签歧义，再处理严格
指令遵循；只有质量门收口后才恢复速度候选。`medium` 只快约 7% 却把严格通过从 4/4 降到
2/4，因此继续使用 `high`。

第十八次复盘修正了优先级。整任务 trace 的工具 union `123,693 s` 是 35 文件快照的根任务
主体，普通单路模型 union `66,228 s` 是第二大块；恰好两路只有 `171 s`。普通热续轮
`short_read=64/0` 已实测否决：B0 没有胜场，1 路短热续 prompt/wall 反而慢
`13.75%/8.14%`。编辑—测试 trace 紧接着否决了重复测试方向：178 次相同测试命令中 173 次
之间有新编辑，成功或失败后立即无编辑重复均为 0。双卡 MTP prompt draft K/V batching
也已否决：94K prompt 虽然改善 `2.359%`，wall 反而慢 `0.046%`，没有转换成用户等待。
官方 `0.1.55` 版本级验证也已否决：正确性全过，但 596/94K wall 慢
`5.853%/4.259%`，所以不混入它的 GDN env。随后同一旧引擎的
`STRATA_GDN_CONVL2=2` 也通过质量门，但 596/94K wall 只改善
`0.8245%/0.2051%`，绝对仅 `0.012476/0.002898 s`，已按门关闭。phase map 随后确认
596/94K 都是约 554 fresh token，stage 2 最大相位为 `wait copy 173 ms`。更深的
full streamed ring 因此进入端到端验证，但 596 wall 反而从 `1.509808 s` 升到
`1.898041 s`，慢 `25.714%`。该分支同时切换 whole-chunk stream、shared-expert defer
和 cross-layer issuer，不能单独归因 ring 深度。只读审计又确认 staged 路径已经只拷贝
本层实际选中、非驻留专家，后续层 routing 无法提前获知，跨层推测预取关闭。grouped staged
lookahead 8→15 随后通过质量门，但 596 wall 只改善 `0.3475%`、绝对 `5.25 ms`，prompt
反慢 `0.5996%`，因此该参数轴关闭。随后只读审计确认 `dequant 106 ms` 实际是 native
group gather：每层只处理唯一 selected expert，stage 1/2 是不同层，路径互斥，没有同一
expert/layout 的重复转换；MMQ 固定 stride 也不能直接读取任意 cache slot。重复工作假设
关闭。`AGENT-HOT-TURN-RESIDENT-PATH-AUDIT` 随后证明 group 级 direct path 快 `1.49x`，
596 的 1 路 wall 有 `1.86%` 正向信号，但绝对节省只有 `32.667 ms`，94K prompt 没有
对应改善，4/6/8 未完成。按用户要求暂停，候选保持默认关闭；恢复时先审计 direct-path
的逐层覆盖率和 fallback 原因。

隔天旧会话仍是 p99 尾部：223,114 prompt token、cached `0`、TTFT `113.014 s`。但 34 个
有效会话只有 1 个超过 24h，现有 session file 又不支持 layer split 和 batch。该方向保留
为 `AGENT-HISTORY-STATE-RESIDENCY` 低谷可行性项，不得挤占普通热续轮。

以下 GPU 与并发结果继续作为已建立的底层基线和防重复记录：

修复版 `0.1.51` 已完成部署、运行路径 T-probe 和自身一小时 soak，618 请求的混合流量零错误、
swap 未用且 RSS 后段进入平台；staged 2048 与 P4 组合均已进入生产。新引擎同 boot A-B-A 中，
代码请求从 A 合并中位 41.1875 s 降到 5.074 s（−87.681%），96K by_kind/wave 为
+2.761%/+2.202%，短 JSON 3.980 s，全部在预注册门限内。`6×256K` 的版本 A-B-A 进一步显示
单路 decode −2.72%、4 路 −2.54%、冷读 −0.26%，均未越过 3% 门；4 路余量只有约 0.46 个百分点。
上游 v0.2.7 的 37 个提交随后 fast-forward 到 `c219fbca` 并部署；新构建的数字序列 6 路聚合
约为 `164.7 tok/s`，自然语言 6 路约为 `149.8 tok/s`，3,427-token 冷读约为 `928 tok/s`。
同轮发现 2 路数字序列低于单路、8 路低于 6 路且 TTFT 明显变差，后续优化必须保留完整并发曲线，
不能只拿 6 路峰值作为成功标准。
2026-10-31 的新测量把普通两路凹点单独收口：`UTIL-TWO-STREAM-SERIAL` 让恰好两条低并发
请求依次走 solo path，第三条出现时恢复 batch。512-token 两请求完成跨度由 `11.776 s`
降到 `10.004 s`（`-15.05%`），64-token 由 `1.692 s` 降到 `1.554 s`（`-8.18%`）；
1 路相对 A1/A2 合并中位 `-0.38%`，4 路完成/吞吐基本持平，6 路安全烟测
`163.07 tok/s`。代价是第二条请求 TTFT 约 `5.1 s`。`UTIL-MTP-LOWCONC` 的 2 路
`+3.25%` 被 4 路 `-28.99%` 否决，A2 已恢复后执行了本轮采纳。
当前性能路线的主要矛盾转为：
96K 冷读仍需约 42 s，满槽晚到请求约 11–14 s，长读期间更广泛请求的尾延迟尚未覆盖。P5 已把
96K 冷读拆到 GPU event：phase 总墙钟 35.441 s，其中 `gemm gate/up` 5.807 s、`embed+steps`
3.692 s、`qsa attn` 3.422 s、`gemm down` 3.031 s；host staging 仅 0.341 s。P6 的
当前源码已默认使用 v2，本轮以 `STRATA_PA_V2T=0` 回退 v1 作对照：固定 96K prompt 的
engine read 改善 `1.814%`、wall 改善 `1.905%`，用户 1% 门复验通过。P7 原本用于验证
`STRATA_PF_WMMA=1` 对最大 `gemm gate/up` phase 的影响，但 A1 首个请求期间被用户中断，
没有有效样本。`CTX256` 一小时 soak 已通过并冻结 `0.1.51`，Nsight 双次显示 32K 冷 prefill
不是 DRAM 饱和，而是多数 SM 参与但 issue、Tensor 与 warp residency 不高。后续重点转为提高
真实资源利用率：NCU 只用于定位 stall，随后必须落到内核或调度代码改动。显式 layer-major
虽然真实执行，但六请求中位慢 14.29%，已否决。P6 已按 1% 门复验通过。真实 150K QSA 诊断
已完成：16-query overlap 中位 28.0%，FP16 全选择一致率仅 70.92% 因而否决；按预注册上界，
候选 attention 读取共享值得做一次最小实现。checkpoint move 与 GDN recurrence 合并也都没有
改善完整并发曲线；后者虽然 parity 精确，但 4 路慢 8.011%，所有级别的 A3/A4 漂移都超过
3%。window-profile 已把 8 路尾部拆开：活跃行/window 下降 9.07%，slot tail 与 wait 显著上升，
padding 和 KV resolve 都不是原因；按活跃行计 stage 2 固定成本上升 4.54%。跨 group 延迟合并
head 已在源码门否决：末级每级只有一个 verifier 和一套 head 缓冲，等齐两组会制造大于
合并收益的 10–19 ms 流水线空窗。vocabulary-row head split 的 harness 与 server parity 均通过，
但 4/6 路端到端回退 `1.305%/8.659%`，已否决。随后 Q8 末层 write/mixer fusion 在 GPU 门测得
80 个 direct/graph 逐字比较全部失败，最大节省 `1.315 µs`，远低于 `182.7 µs` 的 1% 门槛；
没有构建或重启。`UTIL-POLL-TURNOVER` 随后完成：首个 B1 因漏掉 A 的 MMQ/Q6K 编译开关而
作废；同选项 B2 通过启动、路径和 greedy parity，但 6/8 路相对 A2/A3 合并中位回退
`3.241%/6.122%`，2/4 路的增益没能保住其他并发级，因此否决并移除源码。`UTIL-B6-ROWS`
随后在 kernel screening/reach 门否决：两卡 exactness 全过，但相对生产 IL 的 T=2 中位慢
`5.868%`、0/22 胜，Q5_K/Q6_K 单点回退 `15.96-38.63%`，而且 B6 不能替换当前 IL dispatch。
随后 `UTIL-LFUSE-SHARED` 用现成共享专家 launch fusion 验证了共享门/router 与
sigmoid/combine 融合。两卡独立 parity 和 B/A2 冻结 greedy 全过，但 A1-B-A2 的
2/4/6/8 路为 `-3.513%/-8.782%/-2.697%/+4.362%`，胜场 `1/6, 1/6, 2/6, 4/6`；只有 8 路
名义过 1%，未过 5/6 胜，A 漂移还达 `+4.053%`。2/4/6 回退超过 1%，因此否决并恢复 A2。
`UTIL-Q5-HEAD-IL-GRID` 随后只限制生产 Q5_K head 的 persistent CTA grid；全部
`T=1..8`/rows `1/2/4` parity 通过，但 `T=2` 的 capped grid 均不如默认，`T=3` 最佳
只比默认快 `0.182` 个百分点，已在性能筛选门否决。`UTIL-BATCH-FAIR-SERVE` 随后验证跨
两个 stage 公平服务 doorbell：启动、路径、parity 与零错误全过，但 2/4/6/8 路相对 A
合并中位为 `+8.901%/-5.121%/+2.114%/-2.612%`，4/8 回退超过 1%，2 路 A 漂移
`-7.067%`，6 路仅 4/6 胜，因此否决并恢复 A2。下一项继续拆末级约 `8.5%` 的 `waitA`
内部组成：host expert-plan 计算、flag 发布后的 spin，以及 GPU consume；不再尝试改变
跨 stage 服务顺序。
P7 只有 NCU 证明 Tensor 管线是直接瓶颈时才恢复，恢复后必须从全新 A1 重跑。

## 候选路线

| 候选 | 目标 | 预期收益 | 置信度 | 成本/风险 | 判断 |
|---|---|---|---|---|---|
| S2：验证每请求 metrics | M4/M5 可观测性 | 不直接提速，但区分 queue、TTFT、ITL、wall | 高，API 已有字段 | 低 | **已完成**：2/2 mock regression |
| S0b：长读与解码共存 | M4、M5 | 高；当前停顿约等于读时长 | 已证伪 | 中高，涉及引擎调度 | **否决**：机制执行但无收益；整体候选回退结论已由 L-082 撤回 |
| batch slot 共享 RoPE | 显存、expert 容量 | 双卡 6×192K 省 562.4 MiB，expert slots +1.8% | 高 | 低 | **采纳**；L-077 真实流量 soak 通过，吞吐不宣称提升 |
| C1 收尾：满槽晚到与邻居 | M5、M8、容量选择 | 6 槽 TTFT 11.84 s，8 槽 14.38 s；邻居静默 <0.61 s | 高 | 低中 | **完成**：默认保持 6 槽 |
| P1 聚合吞吐瓶颈 | M1 | 调度和层切分收口；恢复 HC_Q8 后受控中位 130.6 | 高 | 低 | **完成**：质量 11/11 |
| P2 HC_Q8/RoPE soak | M1/M3/M6 | 真实流量吞吐、缓存命中、长上下文和配置漂移均通过 | 高 | 低中 | **完成**：622 请求 0 错误 |
| P3 长 prefill 公平性 | M5/M10 | 短 JSON 由 40.21 s 降到 3.40 s，96K 只 +2.2% | 高 | 中 | **完成**：400 ms lead-in + 750 ms 等待 |
| P4 中请求公平性 | M11 | 让 41-token 代码请求也尽早完成，同时守住长档 | 高 | 中高，涉及两个重叠调度开关 | **采纳**：新引擎 A-B-A 代码 −87.681%，96K +2.761%/+2.202%；余量小 |
| P5 96K 相位归因 | M12 | 区分 GPU、host 与固定开销，给下一项实验定目标 | 高 | 低 | **完成**：GPU 主导，host staging 0.341 s |
| P6 Turing prompt-attention v2 | M12 | 直接替换 `qsa attn` 的 v1 路径，尝试减少 96K 冷读 | 中 | 低，env-only 且可回滚 | **采纳（当前默认）**：L-095 read/wall `+1.814%/+1.905%`，parity 与 needle 均过 |
| QSA-DIAG | 150K selection 诊断 | 读取 16-query overlap、FP16 一致率、差异 cells 和 select 成本 | 高信息量 | 低；优先只读诊断 | **完成**：overlap 中位 28.0%；FP16 一致率 70.92%，否决；候选复用上界 1.996%，进入最小实现 |
| QSA-UNION-ATTN | 相邻 query 共享选中 K/V | opt-in 且保持每 query 的 selection/attention 输出；长读 A-B-A 的 wall 与 `qsa attn` 同向改善 ≥1%，质量与资源门通过 | 已证伪 | 低 | **否决**：三 query 同 CTA parity/资源达标，但普通选择 +20.05%、最强共享 `QA_SAME=4` 仍 +20.75% |
| P7 Turing FP16 tensor-core prompt experts | M12/M13 | 用 tensor-core prompt experts 攻击最大 `gemm gate/up` 5.807 s | 中 | 低中，输出可能变化 | **搁置**：A1 中断；只有 NCU 证明 Tensor 是直接瓶颈时才恢复 |
| GPU-UTIL | M13 | 用硬件计数器替代单看 NVML；先验证采集口径，再做内核调优 | 高 | 低；只读、不重启服务 | **完成**：两次 Nsight 双卡 GPU Metrics + NVML 交叉核对可复现 |
| UTIL-NCU | M13 | 在独立受控 engine 上取得 SM throughput、occupancy、Tensor、DRAM 和 stall reason | 高 | 中；需短暂停生产、独占 GPU，完成后恢复 | **完成**：MIO `29.75%` 为唯一主因；Tensor `0.04%` |
| UTIL-KERNEL | M13 | 按 NCU 证据只改一个瓶颈内核或调度路径，提高 issue/Tensor/warp 占用并缩短 wall | 高 | 中；需要代码级验证 | **否决**：四副本 codebook `−0.084%`，仅 2/7 胜，bank conflicts 反升 |
| UTIL-ACT | M13 | 每个 k 只加载一次 q8_1 activation words/scale，供 gate/up 两个 row dot 复用 | 中高 | 中；隔离 GPU、7 组进程与 NCU | **否决**：编译器已复用，shared-load 指令完全不变 |
| UTIL-WEIGHT | M13 | 每个 k 只解码一次 weight-side codebook/aux/scale，复用于 NTC chunk 的多个 token | 高 | 中；隔离 GPU、7 组进程与 NCU | **否决**：`+0.546%`、6/7 胜，但 load 指令完全相同且寄存器升高 |
| UTIL-GRID-PAD | M13 | 四副本 codebook 使用 stride 257，使每个 replica 覆盖全部 32 banks | 已证伪 | 中；隔离 GPU、7 组进程 | **否决**：`489.3` 对 `475.7 us`（−2.86%），3/7 胜；不再扫副本或 stride |
| UTIL-WEIGHT-RO | M13 | IQ3 weight `qs`/aux 使用显式只读加载，降低 long-scoreboard stall | 已证伪 | 低中；SASS 检查、7 组进程 | **否决**：机器码已变为 `LDG.E.U16.CONSTANT.SYS`，但只改善 `0.339%` |
| UTIL-PIPED-MTP | M14 | 保留两组流水，用每槽 MTP 草稿修复低并发吞吐凹点 | 已证伪（当前形状） | 中；两次候选切换、2/4/6 路 A-B-A、显存检查 | **否决**：2 路 `+19.92%`/5-of-6，但 4/6 路 `-3.82%/-9.79%`，greedy 输出不重复 |
| UTIL-PIPED-MTP-CACHE | M14 | 只消除 slot drafters 对 expert-cache 的挤压，隔离 parity 混淆 | 已证伪（参数语义） | 中；一次 candidate 启动和一次恢复，先后做 parity 与 2/4/6 路 | **否决**：`--expert-cache-device1` 是额外 helper cache；双卡 layer split 在读取权重前 exit 2 |
| UTIL-PIPED-MTP-ORDER | M14 | 用 CPU-order expert 算术隔离 GPU/CPU expert 位置造成的小数舍入 | 已证伪 | 中；一次 candidate 启动和一次恢复，先后做 parity 与 2/4/6 路 | **否决**：6/2 正常 loaded，但自然语言探针仍两次不同且与 A 不同；未跑候选吞吐 |
| UTIL-BATCH-GROUP-FIT | M14 | 隔离 2 路吞吐凹点的 1-vs-2 group 调度成本 | 已证伪（当前配置） | 中；补 A3 八路，一次 candidate 启动和一次 A4 恢复 | **否决**：A3 8 路 `104.4 tok/s` 低于 6 路；单组候选正常 loaded 但自然语言 parity 不同，未跑吞吐 |
| UTIL-EXPDEC-GU | M13/M14 | 用既有 opt-in EXPDEC 内核接管 mode-8 gate/up | 已证伪（启动前路径） | 低；只读源码与进程环境 | **否决**：fused/LDS 分支先完成 gate/up，down 直接 return；`STRATA_EXPDEC=1` 不可达，未重启 |
| UTIL-SLOT-CKPT-MOVE | M14 | 准入时移动而不是复制约 112 MiB turn checkpoint | 已证伪 | 中；parity 与 2/4/6/8 A3-B-A4 | **否决**：parity 全过，但 8 路 `-3.014%`/胜 2/6；2/4 胜率不足，6 仅 `+0.122%` |
| UTIL-GDN-GROUPS | M14 | 把窗口内各 slot 的 GDN recurrence 合并为每层一次 launch，并保留 self-commit 状态 | 已证伪 | 中；源码 parity、一次候选启动与 A3-B-A4 | **否决**：输出/状态 bitwise 全过，但 4 路 `-8.011%`，最佳 6 路仅 `+0.913%` 且 3/6 胜；漂移全超 3% |
| UTIL-WINDOW-PROFILE | M14 | 用现有 stage/BSTAT 拆分 2/4/6/8 路 | 已完成 | 低；A1-P-A2，每级 3 波 | 8 路 occupancy/tail/wait 与 active rows/window 均退化；padding、KV resolve 否决 |
| UTIL-BATCH-HEAD | M14 | 合并两个 pipeline group 的 final-stage head rows，一次读 output projection | 已证伪（源码控制流） | 高；源码重构、GPU harness、A1-B-A2 | **否决**：两个 group 在末级严格串行；延迟 A 的 verdict 会制造一个 16.9–19.2 ms 空窗，远大于约 1.04 ms head 收益 |
| UTIL-HEAD-SPLIT | M14 | vocabulary rows 分到两张卡并并发各算一半 head | 已证伪（server 编排） | 高；双卡事件、单/batch window、最终 A1-B-A2 | **否决**：harness 与 server parity 全过，但 4/6 路端到端 `-1.305%/-8.659%`；默认关闭 |
| UTIL-HEAD-Q8-FUSE | M14 | 在现有 Q8 mixer 中把末层 pending write 与最终 mixer 合并 | 已证伪（GPU/正确性） | 中；独立 harness 后可有一次 A1-B-A2 | **否决**：5 个交替进程的 80 个 direct/graph 逐字比较全失败；最大节省 `1.315 µs`，门槛 `182.7 µs`；未构建、未重启 |
| UTIL-POLL-TURNOVER | M14 | 降低完成 poll、group hand-off 与下一 stage launch 的串行周转成本 | 已证伪（高并发） | 中；一次候选启动和恢复，附首次构建控制失败 | **否决**：同选项 B2 parity 全过，但 6/8 路回退 `3.241%/6.122%`，最佳级胜场也不足；源码已移除 |
| UTIL-B6-ROWS | M13/M14 | Q4_K/Q5_K/IQ4_XS 多列 MMVQ 跨 rows 复用 activation fragment | 已证伪（生产 reach） | 低；现有精确 opt-in，仅本机 kernel screening | **否决**：全 parity，但相对 IL T=2 中位 `-5.868%`、0/22 胜，Q5_K/Q6_K 回退最高 `38.63%`；未构建、未重启 |
| UTIL-LFUSE-SHARED | M14 | 共享专家标量 gate 并入 router，sigmoid+scale 并入 resident combine | 已证伪（吞吐/漂移） | 中；两卡 harness、A1-B-A2 各 12 waves | **否决**：parity 与 greedy 全过，但 2/4/6/8 路 `-3.513%/-8.782%/-2.697%/+4.362%`、胜场 `1/6, 1/6, 2/6, 4/6`；8 路 A 漂移 `+4.053%`，A2 已恢复 |
| UTIL-QFUSE-HC | M14 | 生产 HC Q8 up read 同读写出 consumer q8_1，省掉独立量化 launch | 已证伪（整窗绝对收益） | 低；两卡独立 harness，未重启 | **否决**：两卡全 bitwise，T=1/2/3 完整局部路径 `+1.31–2.67%`；但每 read 仅省 `0.44–0.79 us`，每 stage 48 次后只有 `0.11–0.20%` |
| UTIL-SIDE-MIXER | M14 | 用 side stream 重叠 qkv/gate 与 QSA q/k/v | 已证伪（生产可达性） | 低；两卡独立 harness，未重启 | **否决**：T=3/4 整窗上界 `+1.10–1.29%`，560 个 buffer 全 bitwise；但生产 batch window 含 `!batch_rec_` 排除，命中率为零 |
| UTIL-BATCH-SIDE-MIXER | M14 | 只对 `batch_rec_ && T==2` 重叠 GDN qkv/gate 与 QSA q/K/V | production graph 两卡 7/7 胜、全 bitwise；服务端至少一级 `+1%`、5/6 胜、其他级不回退 >1%、漂移 ≤3% | 中；两卡 GPU 预筛、B parity、三次同 boot sweep | **否决**：2/4/6/8 路 `-2.755%/-6.766%/+0.378%/+4.969%`，只有 8 路过双门；2 路漂移 `-7.067%`，A2 已恢复 |
| UTIL-Q5-HEAD-IL-GRID | M14 | 只限制生产 Q5_K output head 的 persistent CTA grid | 全 `T=1..8`/rows `1/2/4` bitwise；head 改善至少 18.5% 或约 `0.192 ms`，两卡同向、5/7 胜后才进入 server | 低；独立构建与 kernel screening，未重启 | **否决（performance screening）**：全 parity；`T=2` 所有 capped grid 慢于默认，`T=3` 最佳只比默认快 `0.182` 个百分点，源码已移除 |
| UTIL-BATCH-FAIR-SERVE | M14 | 每个 stage 每轮只服务一个已到达 layer，让另一 stage 先发布 plan | B 多行 window 真实命中、冻结 greedy 等于 A；A1-B-A2 至少一级 `+1%`/5-of-6，其他级不回退 >1%，漂移 ≤3%，零错误/外来请求 | 中；一次候选启动和一次恢复，约 6 分钟加载成本 | **否决**：2 路 `+8.901%`/5-of-6 但 A 漂移 `-7.067%`；4/8 路 `-5.121%/-2.612%`，6 路仅 4/6 胜；A2 已恢复 |
| LM-EXPLICIT | 长上下文 prefill | 显式 layer-major 用 96 MiB 余量启动 16K host-residual super-chunk；真实 150K/210K 出现稳定、可归因的 ≥1% 改善 | 中 | 中；每轮重启约 2.5–3 分钟 | **否决**：路径真实执行，但六请求中位 −14.29%；不再扫相邻 target |
| QSA slots 合并 launch | 4/6 路聚合吞吐 | 减少 batch window 固定 launch 开销 | 已证伪 | 低 | **否决**：4 路无收益，6 路首次收益不复现 |
| T-probe 版本戳 | 可观测性/部署正确性 | 避免继续读到旧 server | 高 | 低 | **已完成**：hash 与 markers 来自运行路径 |
| S8/S1/S7 回归测试 | 稳定性 | 主要防止已有能力回归 | 高 | 低中 | 作为同批低风险补充 |
| V3-2/V3-3 DFlash2 | 吞吐 | 未知 | 低 | 需要外部权重 | 阻塞，不空等 |

执行计划：[资源利用率优化计划](superpowers/plans/2026-10-27-resource-utilization-optimization.md)。
先完成 Task 1–6 的 NCU 根因与门限预注册；Task 7–9 必须等唯一根因产出后再进入。

## 当前路线决定

1. **S2 已完成**：流式与非流式响应都有可回归的 per-request metrics。
2. **S0b 已否决**：并发读 3/3 触发但没有改善邻居停顿；L-082 撤回“整个 0.1.51 回退”的临时概括。
3. **RoPE 共享已采纳**：先拿确定的显存收益，再用真实流量验证 expert hit、RAM exchange 和长上下文稳定性。
4. **C1 已完成**：同时测通 6/8 槽晚到请求和 slot-wait metrics；默认继续 6×192K。
5. **P1 已完成**：groups=2、split24/24 保留；合并丢失的 HC_Q8 已恢复，受控 4 路中位 130.6。
6. **P2 已完成**：一小时混合 soak 零错误，长档重复无热衰减，4 路合成中位 129.7。
7. **P3 已完成**：固定分片 32768/65536 分别把长档拖慢 15.6%/7.3%，否决；400 ms concurrent-read
   lead-in 加 750 ms 等待槽进入可解码状态，经 A-B-A 将短 JSON 降到 3.40 s，长档只 +2.2%，采纳。
8. **P4 参数探索（历史）**：1200 ms 固定 lead-in 只多推进约 23 个槽行，代码请求仍 41.93 s、长档 42.13 s，
   否决；2000 ms 虽在首次运行出现 5.25 s，重复 A-B-A 又回到 41.80 s，也否决。
9. **P4 旧引擎候选（历史）**：`STRATA_ADMIT_SHORT_FIRST=2048` + 1600 ms lead-in 的 1 小时 soak 零错误，
   代码 4.424 s、短 JSON 3.370 s；96K wave +2.53% 在门限内，但单请求中位 +4.14%、相邻 D2
   +3.21% 越界。该旧引擎结果不能直接定论，后来由第 13 项的新引擎严格 A-B-A 取代。
10. **QSA slots 已否决**：首次 6 路 +3.5% 未复现，最终相邻 on 臂还慢 0.49%；不再重复同配置。
11. **合并引擎门禁已通过**：修复版 `0.1.51` / `a09d78b1` 的 CTest、temperature 0 parity、
    版本 marker 与五臂 A-B-A 均满足门禁；唯一测试失败是服务器缺少 Q2_0 PLE 外部资产。
12. **新引擎与 staged 2048 已部署并 soak**：`a09d78b1` / `0.1.51` 完成自身一小时 mixed soak，
    618 请求零错误、零意外 length、needle 全对；RSS 后段平台、swap 未用，T-probe 通过。
13. **P4 严格归因通过**：新引擎 A-B-A 中代码 `41.1875 → 5.074 s`（`−87.681%`），
    96K `+2.761%/+2.202%`，短 JSON `3.980 s`；采纳 1600 ms + 2048 优先，保留回滚快照。
14. **P5 已完成**：96K timing 相位归因显示 GPU 计算主导，host staging 只有 0.341 s；timing 工装
    开销 +0.72%，不启用为生产默认。
15. **P6 按 1% 门复验通过**：当前源码已默认 v2，复验用 `STRATA_PA_V2T=0` 回退 v1。
    A1/B/A2 engine read 为 `40063.1/41022.6/40564.9 ms`，wall 为
    `40.368/41.382/40.878 s`；A 合并中位相对 B 改善 `1.814%/1.905%`，A2 漂移约 1.26%，
    parity/needle 全过。保留当前默认，不再重复 env-on A/B。
16. **P7 搁置**：预注册判据和 P7 前快照已存在，但 A1 首个请求期间被用户中断，
    没有 JSONL/summary、parity 或质量结果。只有 NCU 证明 Tensor 管线是直接瓶颈时才恢复；
    恢复后只增加 `STRATA_PF_WMMA=1`，从全新 A1 起重跑同 boot A-B-A，并追加输出质量门。
17. DFlash2 权重缺失不阻塞主线；获得权重后再重新评估收益和成本。
18. **256K 门禁已全部通过**：版本吞吐 A-B-A 和一小时 `CTX256` mixed soak 均通过，当前
    `0.1.51` / `a09d78b1` 冻结为生产基线。soak 覆盖最高 4 路和约 245.9K prompt，不能扩大成
    6 路满载或 262144 满窗口证明。
19. **资源利用率优先，停止独立参数探索**：Nsight 已证明 NVML 高值不等于计算饱和，但还不能说明
    stall reason。先保存快照、短暂停生产，在快照启动的受控 engine 上独占 GPU 跑 NCU，结束后先
    恢复生产；随后只选一个能提高瓶颈管线占用的代码切片，不把 P7 或 env 参数当作默认下一步。
20. **代码优化验证顺序固定**：先记录 NCU 根因和目标计数器，再改最小内核/调度路径；候选必须在
    同一 32K 冷 prefill 上同时改善目标硬件计数和端到端 wall，并保持输出与稳定性门。参数 A/B
    只在候选已经实现后用于部署验证，不用于寻找候选。
21. **layer-major 配置路线已否决**：只移除 `--prefill-pipe 1` 被 512 MiB 容量余量挡住；
    显式 96 MiB、16K host-residual 虽真实执行 10/13 个 super-chunk，六请求 prompt 吞吐中位
    仍慢 `14.29%`。不再重复同路径或扫描邻近 target。
22. **QSA-DIAG 已完成**：真实 150K 冷读的 overlap 中位 28.0%，尾部 37,516 active blocks；
    FP16 全选择一致率 70.92%，半精度 scorer 路线否决。clean `qsa select` 占诊断 GPU timeline
    4.0%，与同几何 scorer/top-k `2.020/0.899 ms` 组合得到候选共享 1.996% 的乐观上界。
23. **QSA-UNION-ATTN 已否决**：三 query 同 CTA 保持 parity、168 registers/thread 和无 spill，
    但普通选择慢 `20.05%`，`QA_SAME=4` 最强共享上界仍慢 `20.75%`。按预注册内核门停止，
    不消耗 150K A-B-A，也不重复同形 cache co-residency。
24. **UTIL-GRID-PAD 已否决**：四副本 stride 257 保持 hash，但 gate/up 中位 `489.3` 对
    `475.7 us`（−2.86%），仅 3/7 胜；剔除第一组冷时钟离群后仍慢约 1.2%。地址旋转与复制
    成本超过任何 bank 冲突收益；不扫描副本数或邻近 stride。
25. **UTIL-WEIGHT-RO 已否决**：默认 SASS 的 56 条 `LD.E.U16.SYS` 在候选中变为 48 条
    `LDG.E.U16.CONSTANT.SYS`，80 registers、无 stack；但 gate/up 中位只从 `472.6` 降到
    `471.0 us`（`0.339%`），6/7 胜，未达 1%。只看机器码变化不足以采纳。
26. **UTIL-PIPED-MTP 已否决**：六 slots / 两组 drafters 真实启动；A 合并中位
    `78.3/108.55/125.7 tok/s`，B 为 `93.9/104.4/113.4`。2 路 `+19.92%` 且 5/6 胜，但
    4/6 路 `-3.82%/-9.79%`，固定 greedy 文本在 B 内也不可重复。不能只看低并发峰值。
27. **UTIL-PIPED-MTP-CACHE 在启动门否决**：B 的 CUDA1 expert cache 少 169 slots
    （`7040` 对 `7209`），但 `--expert-cache-device1` 被定义为额外 helper GPU 的 cache；
    双卡都跑 layer-split stage 时没有可用 helper，候选在读取权重前 exit 2，未 loaded。
28. **UTIL-PIPED-MTP-ORDER 已否决**：原 B 配置只追加
    `--expert-cache-cpu-order`，候选保持 6 slots / 2 groups 并正常 loaded，但自然语言
    greedy 探针仍两次不同且不同于 A。固定 hard gate 不因 2 路曾有 19.92% 峰值而放宽；不跑
    候选吞吐，也不扫描 cache、顺序、草稿深度、槽数或组数。
29. **UTIL-BATCH-GROUP-FIT 已否决**：A3 的 2/4/6/8 路中位为
    `81.0/116.1/133.2/104.4 tok/s`，确认 8 路低于 6 路。B 只把 groups 2→1，正常以
    6 slots / 1 group loaded，但自然语言探针在 B 内可重复、与 A 不同且提前以
    `finish=stop` 结束。parity 门失败后未跑吞吐；只改 group 数也会改变数值路径，不扫描其他
    固定组数。
30. **下一项先短路验证 UTIL-EXPDEC-GU**：`STRATA_EXPDEC=1` 是独立 opt-in 路径，现有 A
    未启用。候选只有在两张卡的 gate/up 都真实进入 `expdec_gu_kernel`、down 保持默认，并通过
    三组冻结 greedy parity 后，才允许预注册 2/4/6/8 路性能 A-B-A。模拟/局部微内核数字不能
    替代端到端 1% 门。
31. **UTIL-EXPDEC-GU 已在启动前路径门否决**：A 的进程和 config 都固定
    `STRATA_EXP_MODE=8`。源码中 mode-8 fused/LDS gate/up 先完成，mode-8 down 也直接返回，
    EXPDEC 调用位于这些分支之后。候选即使增加 `STRATA_EXPDEC=1` 也不能到达目标内核，因此
    没有花约 3-7 分钟重启或运行无效 parity/吞吐。
32. **当前转向 8 路流水固定成本**：A3 的 8 路中位 `104.4 tok/s`，低于 6 路 `133.2`；
    8 路每请求约 183 engine tokens，slot wait 中位 `1468.5 ms`、decode rate `21.9 tok/s`。
    下一轮只读检查 `PGroup`、`pump()`、`GS` 和最后不足窗口是否重复 handoff、padding 或
    replay，并用固定分组负载隔离；任何候选都必须先保持 token 选择和数值 parity。
33. **波次审计转向 checkpoint move**：A 的 self-commit、direct slot copy、batch sync 与
    checkpoint pool/threads 均已启用。唯一新的单位置候选是 `STRATA_SLOT_CKPT_MOVE=1`：
    将约 112 MiB 的准入 checkpoint 移入槽位而非复制，源码称恢复字节不变。先过三组冻结
    parity，再测 2/4/6/8 路；至少一级 ≥1% 且其余不回退 >1% 才采纳。
34. **UTIL-SLOT-CKPT-MOVE 已否决**：三组 parity 两次均与 A 逐字相同。同周期候选为
    `81.5/110.9/123.1/107.8 tok/s`；只有 2/4 路名义增益，分别胜 4/6 与 3/6，6 路仅
    `+0.122%`，8 路 `-3.014%` 且胜 2/6。A3/A4 在 2/4/6 路的漂移为
    `-8.944%/-6.166%/-7.055%`，同样越过 3% 门。A 已恢复，不扫描 checkpoint 邻近设置。
35. **下一项审计 one-row GDN batch 与流水窗口状态**：8 路仍是六槽加两排队请求，单行窗口
    仍做 scalar/barrier/launch 序列。下一候选必须尝试不同机制，保持每行算术与 token 选择；
    不重跑 self-commit 开关、checkpoint move、固定 groups 或 piped MTP。
36. **UTIL-LFUSE-SHARED 已否决**：两卡独立 parity、B 启动路径与 B/A2 冻结 greedy 全过；
    A1-B-A2 的 2/4/6/8 路为 `-3.513%/-8.782%/-2.697%/+4.362%`，胜场
    `1/6, 1/6, 2/6, 4/6`。8 路虽名义 `+4.362%`，但 A 漂移 `+4.053%` 且胜场不足；
    2/4/6 超过 1% 回退线。A2 已恢复，下一项重新做源码/阶段成本审计。
37. **UTIL-QFUSE-HC 已在生产投影门否决**：完整 Q8 A/B 路径在两卡 `T=1..8` 逐字相等，
    T=1/2/3 的局部路径达到 `+1.312–2.674%` 且 6/7 或 7/7 胜，但绝对节省只有
    `0.44–0.79 us/read`。每 stage 24 层 × 2 次 HC read =48 次，最快 stage 只有
    `18.27 ms`，单卡投影 `0.11–0.20%`；即使不扣两卡 overlap 也不到 0.6%。没有构建
    server 或重启。下一候选必须按真实触发次数证明整窗绝对节省 ≥1%。
38. **UTIL-SIDE-MIXER 已在可达性门否决，转 batch 专用实现**：两卡 14 个进程覆盖
    `T=1..8`，196 条结果行的 560 个 buffer 全 bitwise，零 CUDA 错误。按每 stage 18 个 GDN
    和 6 个 QSA 层投影，T=3 的 GPU0/1 乐观整窗节省为 `229.6/235.0 us`（`+1.257%/+1.286%`），
    T=4 为 `201.6/214.9 us`（`+1.104%/+1.176%`）；T=1 回退，T=2 的 GPU1 只有 `0.924%`。
    但生产 `capture_batch()` 设置 `batch_rec_=true`，现有 `side_proj/sidem` 都要求
    `!batch_rec_`，所以 env-only A-B-A 会测到零命中。下一步只为三/四行 batch window 实现
    窄范围 qkv/gate 分支，保持一/二行串行，并用 production-shaped graph parity 与 batch
    harness 先证明 ≥`0.183 ms`。
39. **UTIL-BATCH-SIDE-MIXER 已过预筛/parity，但在同 boot 端到端门否决**：第一次 production-shape
    harness 把每个 GDN layer 都重复执行 QSA，数据已修正。真实 18 GDN + 6 QSA 层序下，
    24 层 mixed graph 在两卡各 7 个独立进程中全 bitwise、全 7/7 胜，中位节省 GPU0
    `177.0208 us`（`0.9689%` stage）、GPU1 `191.7025 us`（`1.0493%`）；18 GDN 与 6 QSA
    组成图也无回退。B 以 6 slots / 2 groups、health loaded 启动，三组冻结 greedy 各两轮
    逐字一致，`busy=2, decoding=2` 的双槽探针触发两个 2 行 batch capture。
    主机重启后，A1-B-A2 在同一 boot 完成 36 波，2/4/6/8 路相对 A 合并中位为
    `-2.755%/-6.766%/+0.378%/+4.969%`，胜场 `4/6, 1/6, 2/6, 5/6`。只有 8 路同时过
    1% 与 5/6，2/4 路回退超过 1%，2 路 A1/A2 漂移为 `-7.067%`。候选否决，A2 已恢复；
    不扫描 T=2 邻近窗口或按并发级套用条件。
40. **UTIL-Q5-HEAD-IL-GRID 在性能筛选门否决**：候选仅对 `n_out > 100000` 的 Q5_K
    head 设置 persistent CTA 上限，保留现有精确 IL 每行算术和 reduction。
    `mmvq_il_parity` 对 `T=1..8`、rows `1/2/4` 和真实 head table 全部 bitwise；
    有效 `screen2-gpu0-*` 中 `T=2` 的 capped grid 全不如默认，`T=3` 最佳 grid `2560`
    相对 plain 为 `+1.272%`，仅比默认高 `0.182` 个百分点，距 18.5% head/约
    `0.192 ms` 整 stage 门槛超过 15 个百分点。未跑七进程确认或 server A-B-A，源码已移除。
    下一项只读审计 `waitA`，与已否决的 poll 无 ring 快返回严格区分。

41. **UTIL-BATCH-FAIR-SERVE 在端到端并发门否决**：候选在每个 stage 服务一个真实 ring 后
    返回 `pump()`，避免同 stage 连续消费 doorbell 而饿死另一 stage。B 的 12 个 captured
    window 中 8 个为多行，启动/parity 与零错误均通过；但 A1-B-A2 的 2/4/6/8 路为
    `+8.901%/-5.121%/+2.114%/-2.612%`，胜场 `5/6, 3/6, 4/6, 2/6`。4/8 回退超过 1%，
    2 路 A 漂移 `-7.067%`，6 路不足 5/6。A2 已恢复；下一步只读区分 `pool()` 的 host
    plan 计算、发布后的 spin 和 GPU consume，不重试跨 stage 公平服务。

42. **UTIL-TWO-STREAM-SERIAL 已采纳**：恰好两路时第一条保持 solo、第二条等待后也走
    solo；第三条出现时按原机制进入 batch，所以 4/6 路窗口不变。A1/A2/B 的 1/2/4 路
    结果为 1 路 `104.20/102.75/103.08 tok/s`、2 路完成跨度
    `11.716/11.776/10.004 s`、4 路 `136.01/135.81/135.96 tok/s`。两路 512-token
    `-14.83%`（对合并中位），64-token `-8.18%`，6 路安全。下一项回到长上下文成本地图。

43. **长上下文目标已校正**：当前会话 compaction、隔天旧会话恢复、压缩后旧历史分支是
    三个独立场景。210K compaction 的前缀复用已经成立；两次正常运行的 prompt 为
    `248–283 ms`，recovery 运行的 `prompt_ms=75.474 s` 包含无进展尾部；精确
    `strata_prefix {"tokens": 208528}` 可把 208K 压缩后旧分支从约 9 秒降到 `0.274 s`，
    但真实 14,183 请求/109 次 compaction 中立即触发为 0/109，因此 exact pin / `cap=9`
    降为低频诊断。用户所说的约 180K“重读历史”是放置超过一天后重新打开旧会话；现有
    180K 冷 turn 只是新合成会话代理，不能写成已覆盖。

44. **全局目标第五次重排并恢复执行**：整任务 trace 表明开发会话的真实 output 远超
    64-token 探针，工具轮转和大型输出也占据大量事件跨度。先执行只读
    `AGENT-END-TO-END-TRACE`，区分模型、工具、compaction、recovery、取消和空档；
    当时把 `AGENT-OLD-SESSION-RESUME` 保留为 p99 必测项，不预判为总体 ROI 第一名；
    后续真实 49 天会话已由 M32 收口。
    本项不重启服务。

45. **整任务 trace 完成并转真实 compaction 形状**：冻结快照为 14 root、4,180 请求、
    6,297 工具调用、根墙钟 union `54,324 s`。最长 root `44,783 s`，模型/工具 union
    `14,855/31,088 s`，空档仅 `15.843 s`。真实 44 次 compaction 中 43 次缓存率低于
    50%，合计占全部 uncached input `54.32%`，约 `1,186 s` 推断模型跨度，为总墙钟的
    `2.18%`；消除一半即可过 1%。下一项 `AGENT-COMPACTION-REAL-SHAPE` 先找回合成请求与
    真实请求的第一个结构分歧，不重启服务。

46. **真实 compaction 已在 B4 收口，转向大工具输出**：冻结 29 小时快照的 provider 是
    DeepSeek 直连，不是 Strata。新增隐私安全 shape proxy，让 Codex 0.164 经 B4 发起三次
    真实 compaction：原始 `tools=[]`，保留工具路径分别复用
    `11,124/11,225`、`11,349/11,452`、`11,627/11,729`，prompt `451.5–529.4 ms`，
    全部 completed。当前 compaction cache 不实现新候选、不重启。全量 26 个 rollout 显示
    工具输出 `>=2K` 占 `92.294%`，恰好两路模型并发仅 `143.727 s`，因此下一主项改为
    `AGENT-TOOL-CONTEXT-DIET`，角色感知两路降到后续。

47. **上下文饮食完成，全局目标第八次修订**：延迟折叠会在每轮移动早期边界，使缓存从
    `25K+` 跌到 `4,546` token，前九请求 `116.177 s`，已否决。大型成功输出在首次出现时
    保存完整文件并替换为稳定引用；三组配对墙钟 `38.106→32.914 s`、
    `58.307→30.727 s`、`41.002→32.686 s`，早期 needle 可通过精确 `rg` 找回，真实
    编辑/测试 `2 passed`。能力保持 opt-in，服务与配置未改、未重启。当前转到
    `AGENT-REAL-DECODE-SHAPE`，先从 35 文件/152 root 快照拆真实 output/reasoning、
    请求类型和 1/2 路成本；只有整任务 `>=1%` 保守上界才实现代码。
48. **decode 形状完成，全局目标第九次修订**：最新 35 文件、152 root、14,768 请求中，
    单路模型 union `69,064.894 s`，恰好两路仅 `171.303 s`；416/1490/4483 token 的
    单路跨度为 `4.240/14.347/45.451 s`，两路为 `8.199/28.919/85.701 s`。两路
    aggregate decode 的计时重叠不是用户收益，4483 还出现两条提前 `stop`。进一步核对
    发现现役 engine 实际带着 `STRATA_DECODE_TIMING=1` 和 `STRATA_VERIFY_PROFILE=1`；
    历史同机剖析器成本为单路 6.4%、四路 10.7%。当前项改为 `AGENT-PROFILE-HYGIENE`：
    用 A1-B-A2-B2 只移除两个 env，在 416/1490/4483 上复核 1% 门、hash/finish、健康
    和 6 slots；通过后以无仪器配置留在生产，再按干净相图选择单相机制。
49. **profiling 移除采纳**：416/1490/4483 的 B 中位为
    `3.911/13.620/37.230 s`；416/1490 相对 A1/A2 相邻中位改善 `6.61%/5.38%`，
    A2 漂移 `2.45%/0.66%`。4483 漂移 13.77%，不用于加速归因；B/A2 都有提前 `stop`，
    作为独立质量风险保留。B2 已留在生产，health loaded、6 slots、smoke 通过。当前项
    `AGENT-DRAFT-BREAKDOWN`：只拆约 5.2–5.5% 的 draft，找到至少消掉一半且整任务
    `>=1%` 的组件；否则关闭 MTP 微调。
50. **draft 关闭，旧会话实测重排为 p99 主项，全局目标第十一次修订**：draft round
    收敛到 `2.463 ms`，host graph gap 只有 `0.017 ms`；draft-head 全消除的乐观 root
    上界仍约 `0.8%`，关闭 depth、阈值、group、cache 和 tree 变体。真实 2026-09-12
    会话在 49 天后经 B4 resume：223,114 prompt token、cached `0`、TTFT `113.014 s`、
    wall `113.3 s`、decode `292.8 ms`，输出 sentinel、`stop`、零工具。当前项改为
    `AGENT-OLD-SESSION-PREFILL-PROFILE`：B2 只增加 `STRATA_PREFILL_TIMING=1`，一次
    冷请求取得 21 相；单相保守节省 >=`1.13 s` 才允许实现，否则比较 state persistence、
    cache residency、prefetch 和客户端历史处理。
51. **223K PLE 路线已验证，1,024 在途采纳，目标第十二次修订**：默认 direct 256 的
    prompt 为 `135,073.4/132,645.8 ms`，host PLE `50,362 ms`、主 PLE `39,291 ms`；
    mmap wall `182.157 s` 否决。只把在途读提高到 1,024 后，profile prompt 为
    `128,303.5 ms`，三组非剖析冷配对 prompt 改善 `2.87%/1.10%/5.58%`、wall
    `0.05%/1.09%/5.21%`，配对中位 `2.87%/1.09%`。参数已显式保留，源码默认仍为 256。
    当前只测默认 1M 行缓存到 4M 行，先过 profile 三指标同向门，再做至少两组非剖析配对。
52. **row-cache 否决，复盘把真实上下文收益提升为当前项，目标第十三次修订**：4M 行
    profile 的 prompt 为 `136,549.2 ms` 对 `117,477.3 ms`，慢 `16.23%`；host PLE
    `51,905 ms` 对 `32,341 ms`，慢 `60.49%`；PLE phase 慢 `24.07%`。按契约恢复
    1,024-inflight，不扫邻近容量。整任务 trace 的工具 union `31,088 s` 高于模型
    `14,855 s`，工具输出 `92.294%` 来自 `>=2K` 单次输出；当前改为
    `AGENT-CONTEXT-DIET-REAL-YIELD`。真实 36 会话审计显示 30/36 有覆盖、29/36 估算
    至少节省 1%，总计约 `12.9739%` input token；下一轮做实际 codex 配对。
    `113.014 s` 旧会话 tail 继续保留；session file 不支持 layer split/batch，跨重启
    驻留必须另立多 stage 可行性项。
53. **上下文饮食真实客户端配对反向否决**：三组 control/diet
    Codex 0.164 编辑—测试配对中，diet wall 为 `25.338/180.964/181.097 s` 对
    `21.589/96.806/128.187 s`，中位慢 `41.276%`；input 中位多 `38.190%`。两组请求
    增加，三组工具调用增加；六臂最终测试/artifact 正确，失败来自额外重读/复跑。
    不产品化该代理。
54. **目标第十五次修订，按真实热路径重排**：TU102 文档与当前 boot 均确认解锁生效，
    双卡 `93.69/94.07 TFLOPS` 对锁定 `62.40`，约 `1.51x`，功耗墙 280W，不再研究。
    日常普通热续轮 prompt `1.4–1.5 s` 明显高于 decode；当前改为
    `AGENT-HOT-TURN-FIXED-COST`。223K 旧会话仅 1/34 超过 24h，降为 p99 可行性项。
55. **短读固定成本否决，转向编辑—测试循环**：A64/B0/A64r 各 63 请求。B0 没有热续轮
    胜场；1 路短热续 prompt/wall 为 `520.8 ms/0.643 s` 对 `458.0 ms/0.596 s`，
    慢 `13.75%/8.14%`；1 路长热续慢 `3.96%/3.55%`，2/4 路同样反向。A64r 已恢复，
    默认 64 不变，不扫邻近阈值。当时转入 `AGENT-EDIT-TEST-LOOP-TRACE`。
56. **编辑—测试 trace 完成，重复测试是伪方向**：36 文件、170 root、22,749 工具、
    2,318 编辑、576 测试。只有 19/170 root 同时含编辑和测试；首修改前模型/工具为
    `3,235.782/6,365.546 s`，修改到首测试为 `4,072.900/8,241.516 s`。178 次相同命令
    中 173 次之间有新编辑，无编辑重复只有 5 次且状态不可判定，成功/失败后立即重复
    均为 0。因此不实现测试去重或缓存。全局目标第二十次修订；双卡 MTP prompt draft
    K/V batching 已真实命中，94K prompt 改善 `2.359%`，但 wall 反向 `0.046%`，未过
    `0.05 s` 绝对门，已否决并停止 189K/全矩阵。
57. **验证官方引擎版本级收益**：官方 `0.1.55` CUDA13 包的 archive/engine/KQ SHA、
    `ldd` 和 `--version` 已通过。当前项 `ENGINE-0.1.55-EVAL` 保持模型/config/env 不变，
    只替换隔离 config 的 executable；启动、路径、health、三组固定 greedy 与 smoke
    通过后跑 596/94K 的 A1/B0/A2。94K 无 wall 方向收益即停止，不跑 189K/全矩阵。

路线在每次复盘后允许改变。改变时必须写清新证据和取舍，而不是因为队列顺序。

## 发现与转向

- 核心指标连续三轮没有实质提升：暂停当前局部路线，做代码/性能审计。
- 同一假设连续两轮失败：换机制，不重复同形实验。
- 出现质量或可靠性回归：优先修复，暂停性能推进。
- 候选少于两个：主动从代码、日志、上游差异、配置项和用户负载中补候选。
