# 自主迭代复盘

> 每次形成判决后，在最上方追加一条复盘。格式见 [`RULES.md`](RULES.md) §6。
> 完整证据在 [`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md)；当前断点在 [`STATE.md`](STATE.md)。

## I-20261026-14 · `STRATA_QSA_SLOTS=1` 的交错复测不复现首次收益，否决

- 目标 / 指标：在 4/6 路真实 batch window 中确认 QSA slots 合并 launch 是否能稳定提高聚合 tok/s。
- 假设与预期：当前每行一套 launch 有固定开销，合并成一个窗口 launch 应减少 decode 调度成本；
  首次 6 路 on 对 off 为 151.9 对 146.8（+3.5%），值得继续验证。
- 为什么选这条路线：它是现成开关，不需要新 kernel；先用低成本 A-B-A 排除参数级误判，再决定是否投入内核工作。
- 改动与实验：同 boot、生产 6×192K 形状，A 为 off、B 为 `STRATA_QSA_SLOTS=1`。先做 4 路
  A-B-A，再按 6 路 B-A-B-A 交错；最终按相邻臂和合并方向判断，不按首次样本判断。
- 结果：4 路 off/on/off 为 125.7/114.6/115.5 tok/s，两轮都不支持 on；6 路
  B2/A3/B3/A4/B4 为 151.9/146.8/149.1/143.5/142.8。首次 +3.5% 未复现，B4 对 A4 反向 0.49%。
- 判决：**否决**在当前双卡 6×192K 形状默认开启 `STRATA_QSA_SLOTS=1`，同配置不再重试。
- 预期被推翻：4 路反而明显变慢；6 路收益只在第一个交错样本出现，属于 boot/采样顺序噪声。
- 生产状态与回滚：实验后已恢复 off 配置，QSA 未留在生产；A/B 快照在
  `/opt/ai/tmp/QSA-SLOTS-20261026T0023Z/`。
- 证据：L-081；`bench-results.jsonl` 和两份逐字节配置快照。
- 学到什么：并发越高不自动意味着合并 launch 更值钱；相邻交错臂能拆穿只出现一次的“提升”。
- 下一步与理由：停止 QSA slots 参数线，先处理合并结果的可部署性和 P4 公平性边界。

## I-20261026-13 · P4 一小时候选 soak：用户可见延迟大幅改善，但长档单请求门限仍有争议

- 目标 / 指标：把 96K 冷读邻居下的 41-token 代码请求从约 41.5 s 降到 ≤10 s，同时守住短 JSON、
  零错误和 96K 长档不超过基线 3%。
- 假设与预期：短请求首准入 2048 tokens 与 1600 ms lead-in 组合，可让中/短请求在长读前取得
  可解码槽；代价是 96K wave 略慢，但应落在 3% 内。
- 为什么选这条路线：P4-2000 ms 已证明单纯延长窗口不稳定；2048-token 准入优先直接处理日志中
  约 39 s 的 `slot_wait_ms`，1600 ms 只给已准入请求一个有界完成窗口。
- 改动与实验：systemd service 增加 `STRATA_ADMIT_SHORT_FIRST=2048`；生产 config 将 lead-in
  从 400 调到 1600，保留 750 ms 等待和 4096-token 最小冷读。运行 1 小时混合 2/3/4 路 soak，
  覆盖短 JSON、1.85K 代码、agent tool turn 和 32K/64K/96K needle。
- 结果：606 请求、202 waves，HTTP/执行错误 0、意外 length 0、needle 25/25。96K 邻居下代码
  wall 从 41.61 s 降到 4.424 s，短 JSON 从 40.83 s 降到 3.370 s。32K 四路 wave
  22.804 对 22.188 s（+2.78%），96K 四路 42.666 对 41.613 s（+2.53%）。
- 判决：**生产候选**。按 wave 判据，96K 在 3% 内；按 `by_kind` 单请求中位，候选 42.658 对
  P2 40.96 s（+4.14%），相邻 400 ms D2 也约 +3.21%。因此不能写成无争议采纳。
- 预期被推翻：短/中请求改善远大于预期，但长档没有同时落在所有合理口径的 3% 以内。当前门限
  对“按 wave”与“按单请求中位”给出不同裁决。
- 生产状态与回滚：候选已部署，health 200；T-probe 记录 engine `8d680c69`、server `de09cffd`、
  launcher `0eeee160`、config `05c9d253`。400 ms 完整回滚快照在
  `/opt/ai/tmp/pre-P3-ABA-20261025T213040Z/strata-022.json`。
- 证据：L-080；P4 candidate JSONL/summary、T-probe 和 P2/P3 相邻对照。
- 学到什么：wave 聚合会稀释单请求长档代价；公平性方案必须预先固定聚合与 per-request 两种判据，
  不能事后挑较宽的一种。
- 下一步与理由：先在合并后 HEAD 构建并验证新的 sm_75 引擎；随后同 boot 做 1500/1550/1600 ms
  边界 A-B-A，同时固定 96K 单请求中位门限，再决定候选保留或回滚。

## I-20261026-12 · 2000 ms lead-in 出现一次 5.3 秒代码响应，但重复运行不稳定，否决

- 目标 / 指标：在不把 96K wall 拖出 3% 门限的前提下，把 41-token 代码请求从约 41.5 秒降到
  ≤10 秒。
- 假设与预期：P3 的 400 ms 只推进约 30 个槽行，41-token 响应仍需更多 decode；把窗口增到
  2000 ms 应让已经进入槽的代码请求完成，并在其完成时自然提前结束。
- 为什么选这条路线：先验证最便宜的参数级假设，再决定是否改引擎调度。2000 ms 不改变读取长度，
  若稳定有效，回滚面小于新的剩余工作感知机制。
- 改动与实验：候选仅把 `STRATA_PIPED_DECODE_LEADIN_MS` 从 400 调到 2000。首次 B 运行后，
  在同 boot 做 400 ms A3 → 2000 ms B2 反向 A-B-A；两臂各 5 个相同的 4 路 fairness wave。
- 结果：首次 2000 ms 的代码请求为 5.254 秒，但 A-B-A 的 B2 中位回到 **41.800 秒**；5 波中第
  1/2/3/4 波代码 TTFT 约 41 秒，`slot_wait_ms` 中位 38.902 秒。B2 的短 JSON 中位 3.670 秒，
  96K 中位 41.749 秒；20/20 成功、0 错误、needle 5/5，5 次 lead-in 均触发。
- 判决：**否决 2000 ms 作为生产默认**。一次 5.25 秒来自请求先获得可解码槽的准入顺序，
  不是窗口长度带来的可重复收益；不能继续用固定加长窗口掩盖准入竞争。
- 预期被推翻：B2 第 0 波代码请求 5.288 秒，第 1/2/3/4 波约 41.9/42.1/41.9/41.6 秒。日志中
  同时存在 `slot_wait_ms` 与 TTFT 一起接近读时长的请求，说明部分短/中请求在长读结束前没有获得槽。
- 生产状态与回滚：已恢复 400 ms E 配置并 health 200；T-probe 复核 engine `8d680c69`、
  server `de09cffd`、launcher `0eeee160`、config `f82d1cfd` 和 11 项运行态 env。
- 证据：L-079；P4 首次 2000 ms、A3 400 ms、B2 2000 ms 的 JSONL/summary 与最终 T-probe。
- 学到什么：P3 的 400 ms 已稳定解决 14-token 输出；剩余问题首先是准入顺序，其次才是单次
  decode 窗口的完成判据。
- 下一步与理由：先做配置级 A-B-A，验证 `STRATA_ADMIT_SHORT_FIRST=2048` 能否让 73/1850-token
  请求先于 98K 冷读取得控制行；只有当配置不能稳定改善时才改服务端/引擎。

## I-20261026-11 · 400 ms lead-in 经 A-B-A 把短 JSON 从 40 秒降到 3.4 秒，代码请求仍待解

- 目标 / 指标：M10 短请求 wall ≤36.75 s，同时 96K 长请求不退 >3%、零错误且 needle 全对。
- 假设与预期：长 prefill 一开始就独占流水线；让已准入的槽先做一小段 decode，应能完成短响应，
  且 400 ms 有界等待不应显著拖慢读。
- 为什么选这条路线：P3 的固定 read piece 虽能让出，但 B/C 两臂已显示长档 +15.6%/+7.3%；
  concurrent-read lead-in 不改变读取长度，只调整开始顺序，风险和归因更可控。
- 改动与实验：先测无 lead-in A1/A2，再筛 400/1200 ms 与 read piece 32K/64K，最后做 A3→E2
  同 boot A-B-A。每臂 5 个 4 路 fairness wave，混合两条短 JSON、一条 1.85K 代码和一条 96K
  needle；T-probe 核对实际 engine `8d680c69`、server `de09cffd` 和 11 个运行态 env。
- 结果：A1/A2/A3 的短 JSON 中位 40.707/40.206/40.154 s，合并 40.206；E1/E2 为 3.404/3.400 s，
  **−91.5%**。96K 的 A 合并中位 40.381，E 为 41.2845，**+2.2%**。E 两臂共 40 请求，0 错误、
  0 length、needle 10/10；日志 10/10 证明 lead-in 实际执行。41-token 代码请求 E 中位 41.51 s，
  只比 A 的 41.218 s 慢 0.7%，没有获得公平性收益。
- 判决：**采纳 400 ms lead-in + 750 ms 等待槽进入可解码状态**；固定 read piece 与 1200 ms
  lead-in 否决。
- 预期被推翻：D 臂只有 400 ms lead-in、缺 750 ms 等待时一次都没触发；长读开始得比槽 decode
  更早，等待进入 `batch_on()` 是机制生效的必要条件。1200 ms 只把槽行从约 29 增到约 51，
  没有完成 41-token 代码请求，长档却升到 42.126 s。
- 生产状态与回滚：已恢复 E 配置并 health 200；engine `8d680c69`、server `de09cffd`、
  config `f82d1cfd`、launcher `0eeee160`。切换前快照
  `/opt/ai/tmp/pre-P3-ABA-20261025T213040Z/`。
- 证据：L-078；P3 A1/A2/B/C/D/E、A3/B2/F 的 JSONL/summary、五个 T-probe 和
  `T-probe-P3-adopted-final.json`。
- 学到什么：有界“先完成已有工作”比把长读切碎更便宜；但固定时间窗只对短输出公平，必须以
  剩余工作而不是单纯延长时间作为下一机制。
- 下一步与理由：进入 P4，比较按在途请求剩余 token/可完成性选择 yield 点；不重复 1200 ms 固定窗。

## I-20261026-10 · 一小时混合 soak 通过稳定性，下一个瓶颈转为长 prefill 挤压短 decode

- 目标 / 指标：验证 HC_Q8 + 共享 RoPE 在 2–4 路混合、32K/64K/96K 长上下文下无错误、无持续资源恶化，
  且 4 路固定吞吐不相对 130.6 回退超过 3%。
- 假设与预期：受控短 bench 的收益应在混合流量中保持；长时间长读若无泄漏，RSS/MemAvailable 会进入平台期。
- 为什么选这条路线：L-075 只证明干净 4 路 bench；RoPE 也一直没有跨小时缓存、RAM exchange 和长上下文证据。
- 改动与实验：新增 `mixed-soak.py`，3600 秒内交替 2/3/4 路，混合短 JSON、1.85K 代码、agent tool
  turn 和 32K/64K/96K 冷 needle；10 秒采样 metrics/RSS/GPU/engine log。前后各跑固定 4 路 n=6，
  后置因首块离散补跑第二块。
- 结果：622 请求、207 waves、0 错误、0 意外 `length`、0 needle 失败；524 stop、98 tool_calls。
  26 次 96K 中位 41.613 秒、最大 41.980。RSS 49.6→53.3 GiB，MemAvailable 11.0→7.31 GiB
  （最低 7.04），swap 全程未用；expert hit 中位 100%。4 路前置 123.4；后置两块 125.2/134.2，
  合成 129.7，相对 130.6 为 −0.7%；单路后置合成 74.9，相对前置 76.8 为 −2.5%。
- 判决：P2 稳定性通过，HC_Q8 + RoPE 保留。混合吞吐不能单看 completion tok/s，因为冷 prefill 主导。
- 预期被推翻：真实混合里最刺眼的问题不是平均吞吐，而是 96K prefill 邻居把短请求 wall 中位从
  短波形的约数秒拉到 40.83 秒；TTFT 仍只 0.63 秒，说明是 decode 被挤压而非准入。
- 生产状态与回滚：未重启、未改部署；engine `cbed3c31`、server `de09cffd`、config `6630c920`，health 200。
- 证据：L-077；P2 JSONL/summary、三块 bench、T-probe 与工具源码哈希。
- 学到什么：吞吐、准入和已完成请求的尾延迟必须分开看；长 prefill 共存是下一项主指标。
- 下一步与理由：P3 静态核实发现 `STRATA_PIPED_READ_PIECE` 才是在 piped groups 中每 piece 后让槽 decode
  的开关；单独降 `--prefill` 不等价。下一轮用同 boot A-B-A 验证 32768-token piece + 400/100 ms budget。

## I-20261026-09 · 把已采纳 env 变成运行态部署门禁

- 目标 / 指标：合并后再次丢失 HC_Q8 这类优化时，部署检查必须失败，而不是继续 health 200。
- 假设与预期：固定关键环境 profile，并从 `/proc/<engine-pid>/environ` 精确核对，能低成本发现配置漂移。
- 为什么选这条路线：P1 已证明单个 env 可造成 13.3% 的受控吞吐差，且这次漂移没有被旧 health/hash 检查发现。
- 改动与实验：新增 `production-env-2080ti.json` 和 `--expect-env-file`；缺键、错值均失败；补两正三负回归。
- 结果：专项 6 passed、相关 32 passed、offline preflight PASS；现网门禁精确匹配 7 个关键开关，0 problems；
  engine `cbed3c31`、6×192K、17728 expert slots、health 200。
- 判决：采纳为后续每次部署/实验恢复后的运行态检查；文档、状态和证据台账已同步。
- 预期被推翻：无；门禁只覆盖运行态，模板文件本身仍需由版本控制保护。
- 生产状态与回滚：未重启、未改服务；工具改动可单独回退，不影响现网推理。
- 证据：L-076；`/opt/ai/logs/T-probe-P1-env-gate-final.json`
  sha256 `3413a47b9669c1905c799986c691a384f746b209af0fa1636251ae7928d75cf1`。
- 学到什么：性能优化证据必须同时收口到可执行门禁，否则下一次合并仍可能静默回退。
- 下一步与理由：进入 P2 真实混合流量与受控长上下文 soak，观察 HC_Q8/RoPE 的吞吐、hit、交换和错误。

## I-20261026-07 · 找回合并丢失的 HC_Q8：受控 4 路越过 130，质量门通过

- 目标 / 指标：M1 的 4 路聚合 ≥130，同时确认上游融合没有丢失既有生产优化。
- 假设与预期：现网 env 缺 `STRATA_HC_Q8=1`，但旧台账和旧快照证明它曾在生产；恢复后应回到 +3–7% 区间。
- 为什么选这条路线：这是配置漂移而非新调参，风险可回滚，且直接命中旧的两对独立 A-B 证据。
- 改动与实验：A4→B1→A5→B2→A6 交错，每臂同 6×192K、groups=2、4 路 n=6；T-probe 验证 env 和运行 hash；
  启动日志确认每卡 0.31 GiB Q8 投影；另用相同 13 项自动质量集跑无 HC/HC 两臂。
- 结果：A 四臂聚合 114.5/116.0/128.5/111.7（中位 115.25），B 两臂 132.5/128.7（中位 130.6，+13.3%）。
  单路 A 中位 73.0、B 中位 72.2（−1.1%）；质量两臂都 11/11，全部 `stop`，无格式或循环退化。
  expert slots 18194→17728（−2.6%），6×192K 仍成立。
- 判决：恢复并采纳 HC_Q8；P1 受控目标达到，但 B2 为 128.7，仍标记真实流量待验。
- 预期被推翻：上游合并不一定保留生产 config 的已采纳 env；必须把生产配置漂移纳入回归。
- 生产状态与回滚：HC_Q8、groups=2、split24、6 slots、engine `cbed3c31`、server `de09cffd`，health 200；
  无 HC 配置快照和候选配置均保留。
- 证据：L-075；四轮 n=6 JSON、六份 T-probe、两臂 `responses.json`。
- 学到什么：性能数据必须与生产配置来源一起审计；相同二进制和 1–2 个 env 差就能造成 13% 级别差异。
- 下一步与理由：进入 P2，先为已采纳 env 补回归测试，再做真实流量/长上下文 soak，避免只凭干净 4 路 bench。

## I-20261026-08 · profile 定位双卡长杆；层切分 25/26 再次否决

- 目标 / 指标：把受控 4 路剩余成本拆成每阶段 GPU event 时间，验证是否值得重新配平层切分。
- 假设与预期：stage 2 更重约 15%，把一层从 CUDA1 移到 CUDA0 可能收回 3–5%。
- 为什么选这条路线：这是现有诊断开关和单个配置字段，不需要构建；若成功可直接影响所有并发请求。
- 改动与实验：`STRATA_VERIFY_PROFILE=1` 跑 n=6；随后 A3 split24 → split25 → A4 split24 → split26。
- 结果：stage 1 为 11.4–13.4 ms/window，stage 2 为 13.2–15.4；stage 2 主项是 VRAM experts、waitA、
  head、out-proj 和 q8+qkv。split25 对 A 中位仅 +2.5%，split26 为 −4.5%。
- 判决：profile 采纳为诊断结论；split24 保留，不再扫切分。
- 预期被推翻：stage 2 的 waitA 是流水线交接空档，简单搬层会增加其他成本，不是可回收纯利润。
- 生产状态与回滚：profile env 已移除；split24 和原 config 已恢复。
- 证据：L-074；profile 日志和 split25/26 四臂 JSON/T-probe。
- 学到什么：同类历史结论仍可被新生产形态快速复核，但低于 3% 不做方向性采纳。
- 下一步与理由：不再调层切分，转入 HC_Q8 恢复与质量/真实流量验证。

## I-20261026-06 · batch-MTP 没补回串行窗口，流水线默认进一步坐实

- 目标 / 指标：验证 groups=1 的每槽 MTP 是否能补偿并发吞吐，并保护 6×192K 显存。
- 假设与预期：每槽各生成一个 MTP proposal，若接受率约 80%，可能补回 groups=1 相比流水线损失的 11.6%。
- 为什么选这条路线：它只改配置且代码明确支持 groups=1 + batch-MTP；这是串行路径最后一个高价值开关。
- 改动与实验：生产 groups=2 → groups=1 + `--batch-mtp` n=6；T-probe 验证运行 hash、groups、6 slots，
  启动日志验证 6 个槽 drafter 与显存；结束后恢复 groups=2 并再次 T-probe。
- 结果：聚合仅 91.3 tok/s，比 groups=1 再低 17.7%、比 groups=2 约低 25%；TTFT 392 ms，但 prompt 851.5 ms、
  每流 decode 24.8 tok/s、wall 17.5 s。draft 接受率 80.71%，experts slots 18194→18027。
- 判决：否决；流水线 groups=2 是明确更强的生产默认。
- 预期被推翻：接受率正常不代表窗口吞吐提高；串行双行验证、提交和阶段切换吞掉了 MTP 的收益。
- 生产状态与回滚：groups=2、6×192K、engine `cbed3c31`、server `de09cffd` 已恢复；候选配置与两份 T-probe 保留。
- 证据：L-073；`P1-groups1-batchmtp-B2-n6`。
- 学到什么：性能开关必须按端到端 wall 和窗口吞吐验收；acceptance 只是必要条件，不是吞吐结论。
- 下一步与理由：启用 groups=2 的低采样 batch telemetry，先验证采样无 >3% 回退，再定位流水线内部空隙。

## I-20261026-05 · batch-groups 串行窗口单路略快，但 4 路吞吐回退 11.6%

- 目标 / 指标：M1 的 4 路聚合吞吐和每请求 prompt/decode/TTFT/wall 成本。
- 假设与预期：当前显式 groups=2 走两卡流水线；groups=1 可能靠更完整窗口和更短准入等待提高吞吐。
- 为什么选这条路线：它是 P1 第一个只改配置、可快速回滚的结构变量，且新旧引擎同二进制，不引入代码风险。
- 改动与实验：增强 bench 保存逐请求 metrics/timings；A1 groups=2 n=6 → groups=1 n=6 → A2 groups=2 n=6。
  每臂 T-probe 核对实际 engine/server、slots 和 groups；三臂都是 30 个本基准请求、0 个外来请求。
- 结果：A1/A2 的 4 路聚合 127.2/120.4（合并中位 122.1），groups=1 为 110.9，低 11.6%。groups=1
  的 TTFT 340.5 ms、slot 等待 168.0 ms 更低，但 prompt 686.6 ms（A 约 510）、每流 decode 30.4 tok/s
  （A 为 32.8–33.8）、wall 14.4 s（A 为 12.6/13.3）全面变差；单路 79.7 对 A 的 76.8/76.0。
- 判决：groups=1 否决；生产恢复 groups=2。
- 预期被推翻：更低的准入等待不等于更短任务完成时间；双卡串行窗口在单路略快，但并发损失远大于收益。
- 生产状态与回滚：engine `cbed3c31`、server `de09cffd`、6 slots、192K、RoPE 共享、groups=2；
  `pre-P1-groups1-20261025T173608Z` 快照和 groups=1 候选配置均保留。
- 证据：L-072；`P1-groups2-A1-n6`、`P1-groups1-B1-n6`、`P1-groups2-A2-n6` 三份 JSON 记录及两份 T-probe。
- 学到什么：bench 必须同时看 TTFT 与 wall/decode；只盯准入等待会选错调度。历史 A4000 上的串行收益
  不能外推到 2080 Ti + 本工作负载。
- 下一步与理由：测试 groups=1 + `--batch-mtp`，确认每槽投机能否补回串行窗口损失；若不赢，现有流水线
  已是更强默认，继续做窗口级成本剖析而不是重复同形开关。

## I-20261026-04 · C1 边界收口：满槽损失在新请求等待，不在邻居长静默

- 目标 / 指标：M5 满槽首 token、M8 晚到请求准入，并明确 6/8 槽容量取舍。
- 假设与预期：满槽时新请求会等待一个 slot；邻居解码应继续，若邻居也停顿则优先查调度。
- 为什么选这条路线：C1 是当前最直接的用户等待问题，且不需要改引擎；先测边界能决定默认槽数和下一步。
- 改动与实验：新增 `probe_slot_admission.py`，门控全部 slot 非 idle 后发唯一短请求；同时记录响应 metrics、
  `running/outside_slots`、客户端首/第二 token 和邻居静默。另补 `slot_wait_ms` 并部署 server 补丁。
- 结果：6 槽 3/3 满槽，slot 等待中位 11.422 s、TTFT 11.839 s；8 槽为 13.885/14.375 s。
  邻居静默 6 槽 0.481 s、8 槽 0.552 s；第二 token 都约 0.06 s。默认保持 6 槽。
- 判决：C1 采纳完成；`slot_wait_ms` 采纳并部署；8 槽只保留容量预设。
- 预期被推翻：原 `queue_ms=0` 不是无等待，而是 schema 只统计 server 排队；真实 slot 等待为 11–15 s。
- 生产状态与回滚：恢复 6×192K，server `de09cffd`、engine `cbed3c31`、health 200。server/config 快照及补丁完整。
- 证据：L-070/L-071；C1 6/8 槽 JSON、两臂 T-probe 和 `C1-s2-slot-wait-verify`。
- 学到什么：并发容量增加会拉长新请求准入；延迟归因必须把 server queue、slot wait、prefill、decode 分开。
- 下一步与理由：进入 P1；4 路聚合 113.6 tok/s 仍低于 130 目标，吞吐差距比继续重复满槽测试更有用户价值。

## I-20261026-03 · S0b 负结果与 RoPE 共享采纳：吞吐目标转向准入延迟

- 目标 / 指标：M4 长读邻居停顿、M1/M2 吞吐，以及显存和 expert cache 容量。
- 假设与预期：修复 `staged_source` gate 后，读块会让出解码，邻居静默应显著下降；共享只读 RoPE 应省显存
  且不改变 token 算术。
- 为什么选这条路线：S0b 是当前用户损失最大的候选；RoPE 已有底层接口，接线成本低、生命周期可证明、
  可独立回滚，是同期最高置信度的资源优化。
- 改动与实验：用真实 `concurrent_reads` 和静默统计做 A-B-A；0.1.51 做 n=6 吞吐；在现役 0.1.49.1
  打 opt-in batch slot RoPE 共享，双卡 6×192K 测吞吐、parity、冷 prefill，再用 T-probe 验证运行文件。
- 结果：S0b ON 邻居静默中位 7.09/7.59 s，OFF 7.41 s，旧引擎 7.08 s；路径 3/3 触发但没有用户收益。
  0.1.51 单路 68.0 tok/s、聚合 113.1，低于旧引擎 74.7/115.55。RoPE 双卡省 562.4 MiB，expert slots
  17872→18194；6 槽 4 路单路 75.1、聚合 113.6，差在噪声内；parity 和冷 prefill 通过。
- 判决：S0b 否决并回滚；0.1.51 不替换生产；RoPE 共享采纳为 opt-in 并部署。
- 预期被推翻：并发读“执行”不等于“用户等待下降”；省显存也未自动换来短测 tok/s。
- 生产状态与回滚：现役 engine `cbed3c31`，server `b6b695a1`，health 200；回滚到 `84a0d410` 的快照完整。
  补丁、原始源码和哈希已固化到 `tools/server/patches/rope-share-0.1.49.1.patch`。
- 证据：L-068、L-069；S0b 四份 JSON、RoPE 吞吐/parity/cold 日志和
  `T-probe-rope-share-20261025T170108Z.json`。
- 学到什么：性能实验必须同时回答“机制是否执行”和“用户是否少等”；资源收益与吞吐收益要分开判定。
- 下一步与理由：进入 C1，先用同一生产 boot 测 6 槽满槽的第 7 个请求，明确 queue、TTFT、wall 和邻居静默。

## I-20261026-02 · T-probe 改为读取实际运行文件，消除假部署证据

- 目标 / 指标：版本戳中的 server hash 与 markers 必须来自正在运行的 `serve/server.py`。
- 假设与预期：生产目录和 launcher 实际运行的 port-v1 目录不同；按固定生产路径 hash 会读错对象。
- 为什么选这条路线：S0b 即将部署新引擎，错误的部署证据会让后续性能结论不可信；成本低、风险低。
- 改动与实验：从 `/proc/<pid>/cmdline` 解析 running engine 与 server 路径，再 hash 并提取 markers；
  `check()` 断言 running path 与 hashed path 一致。新增 3 个负例/正例测试。
- 结果：`3 passed`；现网输出从生产目录的旧 server `a3c7df58` 改为实际运行的 port-v1 `b6b695a1`，
  `BCANCEL_DRAIN_S`、strict BRDY 等 markers 均命中。
- 判决：采纳。
- 预期被推翻：无；证实现网 server 并非生产目录文件。
- 生产状态与回滚：只读探针；未重启、未改生产。
- 证据：L-067；`/opt/ai/logs/T-probe-after-20261026T0010Z.json`。
- 学到什么：部署验证必须从 `/proc` 的实际 argv 出发，不能从“默认目录”推断。
- 下一步与理由：继续 S0b；新引擎构建完成后先用 T-probe 证明运行 hash，再做 A-B-A。

## I-20261026-01 · S2 每请求时延指标采纳，长读分析获得统一口径

- 目标 / 指标：确认流式与非流式响应都携带 `queue_ms / ttft_ms / itl_ms_mean / wall_ms / tokens`，且时间关系单调。
- 假设与预期：现有实现已存在；缺的是回归证据，补测成本远低于直接改调度。
- 为什么选这条路线：它是 S0b 的必要观测工装，且 mock 测试不占 GPU、不需重启生产。
- 改动与实验：运行 `tools/server/tests/test_mp_timings.py`，覆盖流式最终 chunk 和非流式 body；断言
  `queue <= ttft <= wall`、token 数一致、多 token 时有正的 ITL。
- 结果：2 passed；另跑 staged cancel 回归 1 passed。
- 判决：采纳。
- 预期被推翻：无；不需要修改 server 实现。
- 生产状态与回滚：未改生产；纯测试验证。
- 证据：L-065；`python3 -m pytest tools/server/tests/test_mp_timings.py -q`。
- 学到什么：S2 已不是工程缺口；继续改 metrics 会挤占真正的用户延迟优化。
- 下一步与理由：进入 S0b，先验证并发读实际触发，再比较邻居停顿与吞吐。

## I-20261025-01 · 规则系统从流程驱动改为自主优化闭环

- 目标 / 指标：让 AI 能独立选择最优路线、执行实验、复盘并继续，而不是逐项填 Gate。
- 假设与预期：当前问题不是规则数量不足，而是同一规则散落在多份文档；合并后决策速度和一致性应提高。
- 为什么选这条路线：规则冲突已经直接影响状态、队列和重启策略，继续增加条款会放大噪声。
- 改动与实验：以 `RULES.md` 为唯一协议；新增 `ROADMAP.md` 和 `ITERATION_LOG.md`；把旧计划、长交接和旧日志移入
  `archive/`；把预检限制收敛到状态/队列一致性。
- 结果：主入口剩下一套规则、一个当前状态、一个路线组合、一个队列、一个复盘入口；历史证据仍可追溯。
  活跃路由由约 800 行压缩为约 150 行；状态/队列预检 PASS；相关单元测试 25 passed；活跃文档链接缺失 0。
- 判决：采纳。
- 预期被推翻：原规则试图靠 11 字段 Gate 和固定轮次保证质量，实际制造了重复、冲突和维护成本。
- 生产状态与回滚：未改生产；文档变更可在 git 中整体回退。
- 证据：`RULES.md`、`ROADMAP.md`、`STATE.md`、`WORK_QUEUE.md`、`archive/`；
  `python3 tools/server/preflight.py --offline` → PASS；
  `python3 -m pytest tools/server/tests/test_mp_preflight.py tools/server/tests/test_mp_round_gate.py -q` → 25 passed。
- 学到什么：约束应放在结果、安全和真实证据上；过程模板只在能降低决策成本时使用。
- 下一步与理由：验证 S2 的每请求 metrics。它成本低、现有实现已存在，并能给 S0b 的长读停顿提供清晰归因。
