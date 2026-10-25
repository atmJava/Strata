# 文档地图（先看这张表，再决定读哪一份）

**先读 [`RULES.md`](RULES.md)（怎么干活），接手入口是 [`HANDOVER.md`](HANDOVER.md)**；数字与证据在 [`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md)
（顶部有 55 条判决索引）。其余文档按下面的状态读——**标"历史/存档"的不要当现状**。

| 文档 | 状态 | 用途 | 什么时候读 |
|---|---|---|---|
| [`RULES.md`](RULES.md) | **工作章程 v3**（怎么干活，先读） | 决策权（执行自主、目标收口由用户确认）、每轮五步、写代码/证据/记录/停机规则 | 接手第一件事 |
| [`HANDOVER.md`](HANDOVER.md) | **现行入口**（每轮更新；**今日 18:31 之后的 staged read 落地尚未写进来**，先看 `STATE.md` 与 `VLLM_PORT_PLAN.md` §6.3） | 决策表、生产现状、坑、未决与下一轮 | 每次接手的第一份 |
| [`CODE_PLAN.md`](CODE_PLAN.md) | **代码计划 v2**（写什么、怎么证明、怎么回滚） | P0 定位病因（三点剖析/字节账）→ P1-A/B/C 三选一 → P2 head 分卡 → P3 结构级 + 不做清单 + 测量协议 + 六条复核 | 要动代码之前 |
| [`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md) | **现行台账**（L-000…L-054） | 每条结论 + 原始日志路径 + 判决（采纳/否决/结论/参考） | 要数字、要判据、要"这条试过没有" |
| [`../tools/server/README.md`](../tools/server/README.md) | **现行工装手册** | 每个脚本干什么、怎么部署、实验脚本的固定形状、构建 | 跑任何测量之前 |
| [`SERVER_ADAPT_REQUIREMENTS.md`](SERVER_ADAPT_REQUIREMENTS.md) | 草案（2026-10-24） | 适配需求清单，§10 有 8 项待裁决 | 想知道"用户当初要什么" |
| [`MULTIPATH_PLAN.md`](MULTIPATH_PLAN.md) | **草案**（2026-10-24） | 多路并发推理融合计划：W1–W4、验收门、风险回滚、轮次排期 | 推进"多路"这件事之前 |
| [`MULTIPATH_W3.md`](MULTIPATH_W3.md) | **设计稿**（2026-10-24） | W3 无槽冷读 + 暂存 + 收养：协议、引擎/服务端改动点、v1 限制、门 | R3–R4 动引擎之前 |
| [`MULTIPATH_STANDARD.md`](MULTIPATH_STANDARD.md) | **草案**（2026-10-24） | 多路服务标准：术语、"多路就绪"四条、定容式、命名与台账模板 | 定容/验收/命名任何一条多路预设之前 |
| [`PORTING_RULES.md`](PORTING_RULES.md) | **通用规则 v1.1**（2026-10-25） | DoD、每轮 Gate 11 字段、禁止清单、测量证据、阻塞、收口需用户确认；§12 24/7 值守协议 | 任何一轮移植/集成/测量之前 |
| [`WORK_QUEUE.md`](WORK_QUEUE.md) | **24/7 单一事实源**（2026-10-25） | 待做/进行中/已完成/否决；每项带验收判据；未完成计数与 Gate 核对 | 每轮取项、收口核对 |
| [`STATE.md`](STATE.md) | **运行状态**（每轮更新） | 目标、当前轮/项、下一动作、环境、备份、未完成数 | 上下文丢失/接手时先读 |
| [`EVERGREEN_LOOP.md`](EVERGREEN_LOOP.md) | **现行**（循环协议，2026-10-25） | 目标从"完成清单"改成"北向指标持续变好直到用户叫停"：每轮怎么选、没得做怎么办、什么时候停 | 每轮开始选项之前 |
| [`VLLM_PORT_PLAN.md`](VLLM_PORT_PLAN.md) | **现行**（移植优化层单一事实源；2026-10-25 20:16 同步到 `46d12fa9`） | fork 优化项的移植判定 P1–P6、§5 状态表、§6 本轮交付与刻意留下的边界 | 要动引擎/服务端代码之前 |
| [`SM75_PORT_PLAN.md`](SM75_PORT_PLAN.md) | **决策记录**（默认化未采纳，回到 opt-in） | SM75 规则 R1–R7 的出处、W0–W8 当时改了什么、为什么回滚 | 想知道 7.5 系列为什么还是 `STRATA_SM75_PERF=1` |
| [`PORT_AUDIT_COVERAGE.md`](PORT_AUDIT_COVERAGE.md) | 审查报告（2026-10-25） | fork 59 个优化项逐条覆盖矩阵；§2 是按收益÷难度排的未做候选 | 想知道"还有什么没做" |
| [`PORT_AUDIT_DEFAULTS.md`](PORT_AUDIT_DEFAULTS.md) | 审查报告（2026-10-25） | 默认路径不变性核查（含 §3.4"升级后必须删旧 `.o`"） | 改默认、或换构建目录之前 |
| [`PORT_AUDIT_MEASUREMENTS.md`](PORT_AUDIT_MEASUREMENTS.md) | 审查报告（2026-10-25） | GPU1 重测的原始输出、被校正的数字、服务器全量 CTest 的逐条归因 | 要引用移植数字之前 |
| [`PORT_AUDIT_TESTS_CPU.md`](PORT_AUDIT_TESTS_CPU.md) | 审查报告（2026-10-25） | 本轮新增的 CPU 可回归测试清单 | 找现成回归测试 |
| [`PORT_AUDIT_REVIEW5.md`](PORT_AUDIT_REVIEW5.md) | 审查报告（第 5 轮，2026-10-25） | 逐条评审意见与处置 | 想知道某条为什么这样改 |
| [`../tools/server/patches/README.md`](../tools/server/patches/README.md) | **现行**（生产补丁） | W3 两份补丁怎么打、修了什么、与仓库的关系（补丁比仓库少 `46d12fa9`；两条 harness 测试在本树红） | 要打补丁或部署之前 |
| [`tu102-unlock/README.md`](tu102-unlock/README.md) | 操作手册（2026-10-25） | TU102 22G 每次开机自动解锁：脚本、initramfs hook、profile | 换卡 / 重装系统时 |
| 其余 60+ 份（`DETAILS.md`、`TERMINAL*.md`、`MAC.md`、`AMD_HIP.md`、`QUANTS.md`、release notes…） | **上游产品文档**（不在本地图里） | 引擎能力、量化、各硬件、Terminal 用法 | 按根 [`../README.md`](../README.md) 找；本地图只管服务器适配这一摊 |
| [`MULTIPATH_RULES.md`](MULTIPATH_RULES.md) | **项目适配 v0.3**（2026-10-25） | 本项目的硬件/部署事实、重启与构建预算、真实脚本名、文档落点、四问 | 开始本项目任一轮之前 |
| [`MULTIPATH_LOG.md`](MULTIPATH_LOG.md) | **现行**（每轮追加） | 多路每轮复盘记录（最新在上，R0 起） | 每轮结束时写；接手时先看 |
| [`FEATURE_PARITY.md`](FEATURE_PARITY.md) | **草案**（2026-10-24） | 功能清单 F1–F11、验收标准 G0–G4、测试用例 T1–T7、与 fork 测试的对照 | 决定"移植什么、怎么验收"之前 |
| [`SERVER_HW.md`](SERVER_HW.md) | 背景，**部分过期** | 这台机器与这个 fork 的由来 | 只作背景；现状以 HANDOVER §4/§6 为准 |
| [`SERVER_RECOVERY.md`](SERVER_RECOVERY.md) | 操作手册 | 服务不可用时的恢复步骤 | 服务挂了 |
| [`TUNING_PLAN_0.2.2-20261022.md`](TUNING_PLAN_0.2.2-20261022.md) | **历史**（10-22） | 当时的调参假设与估算 | 台账里引用它的章节时 |
| [`UPSTREAM_DRAFTS.md`](UPSTREAM_DRAFTS.md) | **存档** | 原本要发上游的英文草稿（已定：不再发布） | 只是想看我们当时怎么论证 |

## 三条写作规矩（延续这份文档体系）

1. **结论进台账、状态进 HANDOVER、细节进工装手册**——同一件事不要在三处各写一半。
2. **每条台账条目**：一句话结论 + 臂数/中位 + 原始日志路径 + 脚本名 + "没测什么"；读代码得到的标 *静态阅读，未测*。
3. **过期的写法**：不要删旧文档，在开头加一行状态（"历史/存档/部分过期"）并指向现行的那份——这样引用它的旧条目仍然成立。
