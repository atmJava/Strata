# 自主迭代复盘

> 每次形成判决后，在最上方追加一条复盘。格式见 [`RULES.md`](RULES.md) §6。
> 完整证据在 [`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md)；当前断点在 [`STATE.md`](STATE.md)。

## I-20261031-87 · Head split is exact and fast in isolation, but loses at server concurrency

- 目标 / 指标：在独立 GPU harness 通过后，把 vocabulary-row head split 接入单请求与 batch
  window，并验证 A1-B-A2 的 2/4/6/8 路端到端门。
- 假设与预期：双卡并发 head 的理论临界路径收益足够大，独立 peer graph 和事件同步不会吃掉
  收益；至少一级达到 1%、5/6 胜，其他级不回退 >1%。
- 为什么选这条路线：window profile 证明 final-stage head 是真实固定成本；vocabulary 分区
  不延迟任何 pipeline group 的 verdict，避开了 `UTIL-BATCH-HEAD` 的流水气泡。
- 改动与实验：实现 opt-in `STRATA_HEAD_SPLIT=1`，直接加载两个不相交 vocabulary 区间；
  增加私有 peer graph、共享 Q8_1 激活、half logits 合并与所有 row-count graph。过程中
  修复 MTP 默认 stream、双卡 `stages.size()==2` 启动误判和 peer graph capture
  `cudaMemcpyPeerAsync` 三处缺陷，最终由 peer 直接读取主卡激活。
- 结果：候选实际命中 `1..8` 行 peer graph；6 slots / 2 groups、health loaded、资源安全。
  三组固定 greedy 各两轮逐字等于期望值，B 内部重复，A2 同样通过。A1-B-A2 的
  2/4/6/8 路相对 A 合并中位为 `+3.99%/-1.31%/-8.66%/-0.09%`，A1→A2 漂移均在 3% 内；
  吞吐汇总无外来请求，请求层面的执行错误计数为零。
- 判决：**UTIL-HEAD-SPLIT 服务端集成正确性通过，吞吐门否决。** 4/6 路回退超过 1%，
  不能因为 2 路 `+3.99%` 就采纳；A 二进制与配置已恢复，split 默认关闭。
- 预期部分推翻：GPU 微基准的临界路径收益真实，但 peer graph、事件和每窗口编排没有在
  4/6 路转成稳定端到端收益，反而放大了低占用窗口成本。
- 生产状态与回滚：A2 为 server/engine/vision `573407/573418/573413`，engine sha256
  `105a4171...`、config `88de996a...`、health loaded、6 slots / 2 groups；systemd
  inactive/dead。运行环境无 `STRATA_HEAD_SPLIT` 或 `STRATA_FUSE_HEAD_GR`。
- 证据：台账 L-114、`UTIL_HEAD_SPLIT_RESULT-20261031.md`、远端
  `/opt/ai/logs/util-head-split-server-20261031T0800CST/`；七进程日志 sha256
  `2b32c31d7db7a39cb99fa7103d9936f0b3ab973867f54a3c29707c68c1a89ed4`，
  `ab-summary.json` sha256 `8319e5c72b4342eb7c2f0151470f24d62b334dca1b66c1de2c3ccf7e0c18d28b`。
- 学到什么：正确性门和微基准临界路径门都不能替代端到端并发门；约 1% 的空间很容易被
  peer graph 编排与不同 window 活跃行数吃掉。
- 下一步与理由：预注册 `UTIL-HEAD-Q8-FUSE`。现有 Q8 mixer 内核在代码中已经支持
  `apply=true`，可以先在独立 GPU harness 中把末层 `gr_write_multi + apply=false` 与单次
  `apply=true` 做 bitwise 和计时；若省去的写回不足以达到 1% 临界路径，就不构建服务器候选。

## I-20261031-86 · Head split is byte exact and clears the critical-path gate

- 目标 / 指标：证明按 vocabulary rows 双卡并发计算 output projection 不改变 logits、
  argmax、采样和 graph 语义，并证明临界路径按快速 stage-2 窗口投影至少端到端 1%。
- 假设与预期：vocabulary rows 互相独立；两个半区应逐字节等于完整 call，并消除约一半
  Q5_K head 时间，给低占用窗口带来超过 1% 的端到端空间。
- 为什么选这条路线：window profile 找到 final-stage head 的固定成本；跨 group 延迟合并
  因 pipeline bubble 在源码门否决，而 vocabulary partition 不延迟任何 group verdict。
- 改动与实验：新增 `src/kernels/head_split_parity.cu` 与 CTest 目标，在独立远端源码目录
  构建；对 Q5_K `2560→248320` 跑全量参考、两个 124,160-row 半区、串行/并发/peer 合并、
  两个独立 graph、改变输入重放、greedy、固定种子和 draw counter 17/10017。
- 结果：七个交替进程全部 `failures=0`，每个进程 `T=1..8` 全过。全量中位
  `1094.779/1932.903/2364.746/2517.119/2806.073/3083.637/3479.548/3881.892 us`，
  并发中位 `763.241/1071.851/1243.243/1347.023/1494.654/1643.123/1833.882/2066.048 us`；
  投影中位 `1.813–9.936%`，最差单次也都超过 1%。split 峰值 `453,189,152 B`，低于
  `461,577,760 B` 上界，两半权重之和严格等于 `437,043,200 B` 全量。
- 判决：**UTIL-HEAD-SPLIT harness 通过，允许进入 server integration。** 这还不是端到端
  采纳；必须证明 production graph 编排成本没有吃掉收益，并通过 A1-B-A2。
- 预期部分确认：GPU head 层收益足够大；但生产主图不能直接捕获 peer kernel，server 路径
  仍需独立 peer graph、事件同步和所有 window 启动点覆盖。
- 生产状态与回滚：没有构建或启动 Strata 候选，没有重启。A2 仍为
  server/engine/vision `557432/557442/557436`，health loaded、6 slots / 2 groups；
  systemd inactive/dead，无需回滚。
- 证据：台账 L-113、`UTIL_HEAD_SPLIT_RESULT-20261031.md`、远端
  `/opt/ai/logs/util-head-split-20261031T0625CST/`。
- 学到什么：在 2080 Ti 上让另一张卡承担半个 head，收益跨过用户 1% 门所需的条件已经
  具备；下一步风险从数值正确性转到 graph/事件集成和每窗口 host/driver 成本。
- 下一步与理由：实现默认关闭的 `STRATA_HEAD_SPLIT=1` server 路径，直接加载两个 vocabulary
  权重区间，单请求与 batch window 都在私有 peer stream 上发 peer graph，主图只等待本次
  head 的两个半区。通过逐字 parity 后才消耗一次 A-B-A 的约五分钟加载成本。

## I-20261031-85 · Cross-group head batching loses to its own pipeline bubble

- 目标 / 指标：验证 profile 暴露的 final-stage head 固定成本能否通过合并两个 pipeline
  group 的 rows 降到端到端至少 1%，并在构建前证明所有权、顺序与临界路径。
- 假设与预期：如果末级可同时持有两个 group 的 mixed rows，一次 native output projection
  可替掉两次约 `1.04 ms` 的 head 调用。
- 为什么选这条路线：window profile 已排除 padding 与 KV resolve，并把 8 路尾部定位到
  underfilled groups 的固定每窗口工作。
- 改动与实验：只读审计 `PGroup`、`stage_group`、`pump()`、`record_window()` 与
  `batch_poll()`。没有实现候选、构建或重启。
- 结果：两个逻辑 group 并不在末级并行；每级只有一个 verifier 和一套
  `head_mixed_/head_logits_/m_out_`。`stage_group[k]` 一次只绑定一个 group，graph
  完成并由 `batch_poll()` 返回 picks 后才释放末级。
- 判决：**UTIL-BATCH-HEAD 源码门否决。** 要等齐两组，第一组 verdict 必须延迟一个
  16.9–19.2 ms 的末级窗口；这会阻塞同组下一窗口并阻止另一级换入，远大于合并省下的
  约 1.04 ms。双缓冲不能消除 verdict 依赖。
- 预期部分推翻：head 固定成本真实，但“两个 group 同时 ready”不是现有控制流事实。
- 生产状态与回滚：A2 保持 server/engine/vision `557432/557442/557436`，health loaded、
  6 slots / 2 groups；没有候选二进制或配置改变，无需回滚。
- 证据：台账 L-112、`UTIL_BATCH_HEAD_RESULT-20261031.md` 与源码位置。
- 学到什么：不能只看 kernel 叠加上限；在流水线里，延迟 verdict 的代价必须与被替换的
  串行段比较，而不是与一次 launch 比较。
- 下一步与理由：预注册 `UTIL-HEAD-SPLIT`。vocabulary rows 天然独立，可让两张卡并发各算
  一半且不延迟任何 group。先做独立 GPU harness，证明半区 logits bitwise、合并顺序、
  sampler/counter 不变和并发临界路径超过 1%，过门后才接 server。

## I-20261031-84 · The eight-stream tail is an underfilled six-slot turnover

- 目标 / 指标：在同 boot 的 A1/P/A2 中用现有 stage stamps 与 batch telemetry 跑 2/4/6/8 路，
  每级 3 波；至少两个独立指标必须区分 6 路峰值和 8 路尾部，再据此预注册一个不同机制候选。
- 假设与预期：如果 8 路仍下降，active rows/window、slot occupancy、最大 slot wait 或 per-request
  tail 中至少两项会显示六槽轮换留下的 underfilled pipeline；padding 与 KV resolve 应能被排除。
- 为什么选这条路线：此前 GDN、checkpoint、固定分组、MTP 与 QSA slot 合并都已失败；继续猜
  同类 launch 成本没有证据。
- 改动与实验：P 深拷贝 A，只加 `STRATA_VERIFY_PROFILE=1` 与
  `STRATA_BATCH_TELEMETRY=1`。每波用 barrier 同时释放固定 400-token prompts，并采样
  `/v1/telemetry`；A1/P/A2 之间停服约 9 秒、加载 130/148 秒。
- 结果：P 的 6/8 路为 `113.352/103.373 tok/s`；8 路占用 `5.010`（6 路 `5.333`）、
  slowest/median tail `1.1349`（`1.0026`）、最大 slot wait `12,596 ms`（`1,859 ms`）。
  BSTAT active rows/window 从 `2.9515` 降到 `2.6837`，padding 反而更低；按活跃行计
  stage 1/2 成本分别升 `4.70%/4.54%`。KV resolve 为 `0.000 ms`。
- 判决：**UTIL-WINDOW-PROFILE 完成。** 8 路尾部由六槽轮换后的 underfilled group 与固定
  每窗口成本解释；两个 pipeline group 各自支付 final-stage head，是该轮证据支持的新靶子。
- 预期部分推翻：没有发现 padding replay 或 streamed-KV resolve 热点；昂贵项是低占用时仍
  按 group 执行的 stage/head 固定工作。
- 生产状态与回滚：A2 server/engine/vision `557432/557442/557436` 已恢复，engine/config hash
  正确，6 slots / 2 groups，两个 profiler env 均不存在；systemd inactive/dead、
  `MainPID=0`、`NRestarts=23`。
- 证据：`docs/work/UTIL_WINDOW_PROFILE_RESULT-20261031.md`、台账 L-111、A1/P/A2 JSON 与
  stage/BSTAT 原始日志。
- 学到什么：6 路峰值是容量峰值，不存在“保持 8 路都活跃”的简单调度修复；低占用尾部会用
  更少的 active rows 为同样的 head/窗口固定工作付费。profiler 会改变融合路径并带来
  3.6–7.6% 成本，只能用于阶段归因。
- 下一步与理由：预注册 `UTIL-BATCH-HEAD`，合并两个 ready pipeline group 的 final-stage
  head rows，使最多 6 行只读一次 output projection。先做源码所有权/顺序门与
  separate-vs-merged GPU bitwise，再允许重启。

## I-20261031-83 · GDN grouped recurrence is exact but slower at four streams

- 目标 / 指标：在 A 的 `STRATA_BATCH_SELF_COMMIT=1` 下，把每个 GDN layer 的各槽
  recurrence 合并为一个 launch；先证明每行输出和每槽状态 bitwise 不变，再以 2/4/6/8 路
  A3-B-A4 验证至少一级 ≥1%、5/6 胜、其他级不回退 >1%。
- 假设与预期：六行窗口可把最多六次 recurrence launch 降为一次，节省每层固定成本，且不改变
  每槽 arithmetic。预计至少 6 路可能越过 1%，其余级保持稳定。
- 为什么选这条路线：A 已启用 self-commit，默认关闭的 grouped recurrence 恰好覆盖 one-row
  window；`STRATA_BATCH_ATTN_ROWS` 因真实 `kv_mode=1` 不可达，因此 GDN 是唯一直接可达且
  不需要扫相邻参数的候选。
- 改动与实验：静态审计发现 grouped window 在 self-commit 下丢掉 keep pointer，候选先补状态
  写回，并新增 1/4/8 组输出+state `memcmp` harness。独立构建后再加唯一 env
  `STRATA_BATCH_GDN_GROUPS=1`，保持 6 slots / 2 groups、KV streaming 和其他参数不变。
- 结果：完整 GPU harness `batch_rows_parity: ok`；3 个固定 greedy 探针两轮都等于 A。
  2/4/6/8 路候选为 `77.8/102.2/121.6/106.7 tok/s`，相对 A3/A4 合并中位
  `+0.777%/-8.011%/+0.913%/-1.386%`，胜场 `2/6/2/6/3/6/2/6`。
- 判决：**UTIL-GDN-GROUPS 否决。** 没有一级同时满足 ≥1% 与 5/6 胜；4 路越过 1% 回退门。
  A3/A4 在 2/4/6/8 路漂移为 `-4.822%/-7.278%/-3.018%/+3.412%`，全部超过 3%。
- 预期被推翻：减少约 180 个 recurrence graph node 没有转化为端到端收益，4 路反而明显回退；
  当前瓶颈不是可被同类 launch 合并消除的固定开销，或 grouped kernel 的 occupancy/调度成本
  抵消了节省。
- 生产状态与回滚：候选源码已反向，A 原二进制恢复；server/engine/vision PID
  `548696/548707/548699`，engine/config hash 正确、health loaded、6 slots / 2 groups，
  systemd inactive/dead、`MainPID=0`、`NRestarts=23`。两次停服各约 9 秒，加载各 150 秒。
- 证据：`docs/work/UTIL_GDN_GROUPS_CONTRACT-20261031.md`、
  `docs/work/UTIL_GDN_GROUPS_RESULT-20261031.md`、台账 L-110、GPU harness/parity/A3/B/A4。
- 学到什么：parity 补丁正确不代表性能机制有效；即使能精确消除固定 launch，也必须先证明
  当前 stage/window 成本主要由该序列构成。完整 A-B-A 漂移继续要求保留因果边界，不能用单级
  名义正差值抵消。
- 下一步与理由：开设 `UTIL-WINDOW-PROFILE`，用现有 stage stamps 与 batch telemetry 同 boot
  采 2/4/6/8 路，先分离 active rows、stage hand-off、streamed-KV resolve 和 slot turnover。
  不再重跑 GDN/checkpoint/固定 group 或其它同类 launch 聚合。

## I-20261031-82 · checkpoint move preserves text but fails multi-concurrency throughput

- 目标 / 指标：只给 A 增加 `STRATA_SLOT_CKPT_MOVE=1`，验证移动准入 checkpoint 能否减少
  2/4/6/8 路准入成本；三组 greedy 两次逐字等于 A 后，至少一级 ≥1% 且 5/6 胜，其他级不回退
  >1%，A3/A4 漂移 ≤3%。
- 假设与预期：移动与复制恢复同一 checkpoint 字节，模型算术不变；8 路六槽加排队换槽会反复
  触发 112 MiB 复制，因此 move 有机会把任务完成时间缩短超过 1%。
- 为什么选这条路线：A 已有 self-commit、direct slot copy、batch sync 和 checkpoint pool/
  threads，move 是源码中独立且尚未启用的单位置路径，不需要重编译。
- 改动与实验：深拷贝 A 后只增加该 env；移除该键可精确恢复 A。B 以 6 slots / 2 groups、
  engine hash、health 和资源门启动。先用 A 验证夹具稳定性，原始自由文本因 A 自身不重复
  被替换为第三组 exact-copy 探针；最终 A/B 各两组探针全部逐字相同。
- 结果：候选同周期 2/4/6/8 路为 `81.5/110.9/123.1/107.8 tok/s`；A3/A4 合并中位为
  `79.05/108.45/122.95/111.15`。2/4 路名义 `+3.099%/+2.259%`，但只胜 `4/6` 与
  `3/6`；6 路 `+0.122%`；8 路 `-3.014%`、仅胜 `2/6`。
- 判决：**UTIL-SLOT-CKPT-MOVE 否决。** 没有任何一级同时达到 ≥1% 与 5/6 胜；8 路明确
  回退超过 1%。A3/A4 自身在 2/4/6 路漂移 `8.944%/6.166%/7.055%`，也不能支撑较小正差值。
- 预期被推翻：减少约 112 MiB 复制没有转化为稳定的多流收益，也没有改善 8 路尾部；复制路径
  不是当前瓶颈，或 move 的跨槽 ownership 成本抵消了节省。
- 生产状态与回滚：A 已恢复，server/engine/vision PID `541714/541773/541717`，health
  loaded，6 slots / 2 groups，engine/config hash 正确、engine env 无 move；systemd 仍
  inactive/dead、`MainPID=0`、`NRestarts=23`。最终 RAM available `13.5 GiB`、swap
  12 MiB、两卡 idle。
- 证据：`docs/work/UTIL_CKPT_MOVE_RESULT-20261031.md`、台账 L-109、同周期 A3/B2/A4 logs。
- 学到什么：先验证基线夹具可重复，再启动候选；自由文本探针不适合作为唯一质量门。吞吐判决
  必须看配对胜率和 A-B-A 漂移，不能只看某一级的中位正差值。
- 下一步与理由：回到 `UTIL-MULTI` 的 one-row GDN batch 与流水窗口状态审计。8 路仍是六槽
  加两个排队请求；下一步寻找不同机制减少单行窗口的 scalar/barrier/launch 序列，必须保持每行
  算术与 token 选择，不再扫描 checkpoint/cache/copy、self-commit、group 或 MTP 开关。

## I-20261031-81 · EXPDEC is unreachable behind the fixed mode-8 kernels

- 目标 / 指标：在重启前确认 `STRATA_EXPDEC=1` 是否真的能接管当前两卡的 gate/up；只有两张卡
  的目标内核可达，才允许创建候选并进入冻结 parity 与 2/4/6/8 路 1% 门。
- 假设与预期：既有 opt-in 文档表明 EXPDEC 覆盖 IQ3_XXS gate/up，若当前 `gu_type` 和几何满足，
  增加单一 env 就应在启动日志出现 `expdec_gu_kernel`。
- 为什么选这条路线：它只需只读源码和进程环境，若可达则有现成内核可做低风险端到端验证；若
  不可达，应在支付一次 3-7 分钟加载成本前停止。
- 改动与实验：没有启动候选或改配置。核对运行 engine/config hash、进程环境和部署源码，追踪
  `native_expert_grouped` 的 gate/up 与 down 控制流，并保存只读证据文件与 sha256。
- 结果：进程和 config 都是 `STRATA_EXP_MODE=8`，`STRATA_EXPDEC` 不存在。mode 8 +
  `gu_type=18` 先进入 `native_gu_fused_kernel<18>`，否则进入 mode-8 LDS gate/up；两者都在
  `expdec_gu()` 之前完成。down 进入 `native_down_lds_kernel` 后直接 return，
  `expdec_down()` 同样不可达。
- 判决：**UTIL-EXPDEC-GU 在启动前路径门否决。** 未创建候选、未重启，也未运行 parity 或
  候选吞吐；A 的 server/engine/vision PID 仍为 `529088/529112/529097`。
- 预期被推翻：已有 EXPDEC 内核“可用”不等于当前部署分支“可达”；只在源码里找到 kernel 和
  opt-in 开关不足以证明生产 mode 会调用它。
- 生产状态与回滚：无状态变化。engine `105a4171...`、config `88de996a...`、health loaded，
  production systemd 仍 inactive/dead、`MainPID=0`、`NRestarts=23`。
- 证据：`docs/work/UTIL_EXPDEC_GU_RESULT-20261031.md`、台账 L-108、
  `/opt/ai/logs/util-expdec-gu-static-20261030T2008Z.txt`。
- 学到什么：调度/内核候选必须先做“当前分支是否可达”的短路门；一个静态不可达候选即使文档
  声称与模型几何兼容，也不值得占用重启和 parity 窗口。
- 下一步与理由：转 `UTIL-MULTI` 的 8 路流水波次审计。A3 8 路 `104.4 tok/s` 明显低于 6 路
  `133.2`，且每请求只实际生成约 183 engine tokens；先查最后不足窗口的 padding/replay、
  重复 handoff 和草稿分配，必须找到不改变模型算术的新机制后才允许候选启动。

## I-20261031-80 · 单组调度既未修复 8 路平台，也未保持 greedy parity

- 目标 / 指标：先补当前 A 的 8 路基线，再把 groups 2→1 作为唯一候选变化；B 必须逐字等于
  A 后，才测 2/4/6/8 路并守住 2 路 +1%/5-of-6、高并发不退 >1% 和资源门。
- 假设与预期：若 2 路凹点来自两组各只携带一行，单组可减少跨卡交接且不改变模型算术；
  预期固定单组至少隔离低并发成本，并可能以配置方式改善吞吐。
- 为什么选这条路线：A3 的 2 路只到 `81.0 tok/s`，而历史单组只在高并发被否决；先补当前
  8 路能同时验证并发平台，并只需一次候选启动和一次恢复。
- 改动与实验：A3 测得 8 路三次 `103.4/115.4/104.4 tok/s`，中位 `104.4`，确认 8 路低于
  6 路 `133.2`。候选从 A 深拷贝，只把 `--batch-groups` 从 JSON 字符串 `"2"` 改为数字 `1`；
  6 slots / 1 group、health loaded、engine/config hash 和资源均正确，随后跑三组固定探针各两次。
- 结果：B 两次生成同一 94-token 文本并以 `finish=stop` 结束；A 可重复生成不同的 96-token
  文本并以 `finish=length` 结束。探针 0 和算术探针均等于 A，失败集中在自然语言序列。
- 判决：**UTIL-BATCH-GROUP-FIT 在 parity 门否决，候选吞吐未运行。** 契约“只改调度、不改
  模型算术”的假设被推翻；不扫描其他固定 group 数。
- 预期被推翻：group 数会改变真实层上的数值结果。候选内部可重复只证明漂移稳定，不能替代
  与基线逐字一致。
- 生产状态与回滚：A 已恢复，server/engine/vision PID `529088/529112/529097`，health loaded，
  6 slots / 2 groups、`expert_slots=17274`、`expert_cache_mib=28267`，engine/config hash 正确；
  production systemd 仍 inactive/dead、`MainPID=0`、`NRestarts=23`。A→B 停机 8 秒、启动至
  loaded 206 秒；B→A 停机 9 秒、启动至 loaded 180 秒，总中断 `3:10`。
- 证据：`docs/work/UTIL_BATCH_GROUP_FIT_RESULT-20261031.md`、台账 L-107。
- 学到什么：配置级并发实验也必须先冻结自然语言 parity。一次重启成本虽低，但性能数据不能
  补偿质量路径变化。
- 下一步与理由：先对 `UTIL-EXPDEC-GU` 做短路验证。源码和既有微内核证据表明
  `STRATA_EXPDEC=1` 只接管 IQ3_XXS gate/up，而 down 保持默认，且当前模型几何满足条件；
  启动日志若不能证明两张卡都进入 gate/up 路径就立即否决，通过后再做 parity 和端到端 1% 门。

## I-20261031-79 · CPU-order arithmetic leaves piped-MTP text drift unchanged

- 目标 / 指标：只给已否决的 piped-MTP B 增加 `--expert-cache-cpu-order`，保持六槽、两组、
  MTP 和 cache 设置不变；两次固定 greedy 探针必须逐字重复且等于 A，才允许跑候选吞吐。
- 假设与预期：若偏差来自 GPU/CPU expert 累加顺序，CPU-order 路径应消除自然语言探针的
  两次差异，并让输出等于 A。
- 为什么选这条路线：它直接隔离上一轮 cache-neutral 路线未能测试的算术混淆，不需第三块
  GPU，也不扩大为参数扫描。
- 改动与实验：从 B config 复制独立候选，只追加 `--expert-cache-cpu-order`。启动仍为
  6 slots / 2 groups、engine hash 正确且 health loaded；先用三组固定 greedy 探针各跑两次。
- 结果：探针 0 和算术探针 1 可重复且等于 A；探针 2 两次仍是不同文本，并与上一轮 B 逐字
  相同，而 A 的回答可重复并 `finish=length`。因此没有运行候选吞吐。
- 判决：**UTIL-PIPED-MTP-ORDER 在 parity 门否决。** 不扫描其他算术、cache、草稿深度、
  槽数或组数。
- 预期被推翻：现有开关只进入 `ExpertDispatch::hit_cpu_order`，layer-split 的
  `VerifyHits` 路径没有对应字段；源码不足以证明波及实际 dispatch，但两次运行与静态分析
  方向一致。
- 生产状态与回滚：A 已恢复，server/engine/vision PID `525579/525603/525588`，health
  loaded，6/2 slots/groups、`expert_slots=17274`、engine/config hash 正确；production
  systemd 仍 inactive/dead、`MainPID=0`、`NRestarts=23`。A→候选停机 8 秒、启动 146 秒；
  候选→A4 停机 9 秒、启动 171 秒，总中断 `6:51`。
- 证据：`docs/work/UTIL_PIPED_MTP_ORDER_RESULT-20261031.md`、台账 L-106、
  `/opt/ai/logs/util-piped-mtp-order-parity-D-20261030T1945Z.json`。
- 学到什么：质量门必须按它冻结的两个维度同时检查“候选内可重复”和“等于基线”；一个探针
  通过不能代表自然语言生成可重复。
- 下一步与理由：`UTIL-BATCH-GROUP-FIT`。A3 的 2 路 `81.0 tok/s` 仍明显低于 4/6 路，而
  一组在当前二进制上的 2 路成本尚未隔离。先补 A3 八路，再只比较 groups=1 与 2；2 路须
  +1%/5-of-6，4/6/8 不退 >1%。若只有低并发胜，就把 crossover 作为后续
  concurrency-aware 调度的实测输入。契约见
  `docs/work/UTIL_BATCH_GROUP_FIT_CONTRACT-20261031.md`。

## I-20261031-78 · cache-neutral piped MTP cannot start on two layer-split GPUs

- 目标 / 指标：从已否决的 B 配置只恢复 A 的 primary/CUDA1 expert-cache 槽数，保持 6 slots /
  2 groups 和 piped MTP；先过逐字 parity，再测 2/4/6 路并守住 1% 门。
- 假设与预期：显式 `10065 + 7209` 会消除 cache 差异；若 parity 仍失败则停止该分支。
- 为什么选这条路线：上一轮 B 的 CUDA1 cache 少 169 slots，且上游文档要求同 cache 才能做文本等价比。
- 改动与实验：从 B 复制独立 config，只把 `--expert-cache auto` 换成 `10065` 并追加
  `--expert-cache-device1 7209`；结构化 diff 证明没有其他参数变化。
- 结果：引擎把 device1 解释为额外 helper GPU 的 cache，报
  `--expert-cache-remote with a layer split needs a GPU that runs no stage` 并以 exit 2
  退出。候选在读取权重前失败，没有 loaded、parity 或性能结果。
- 判决：**UTIL-PIPED-MTP-CACHE 在启动门否决。** 原契约描述的 CUDA1 槽位恢复方法在当前
  双卡 layer-split 形状不可实施；不扫描其他 cache 数值。
- 预期被推翻：`--expert-cache-device1` 的 CLI 简称容易被读成“第二块 stage 的 cache”，实际
  代码路径是 CUDA1..3 helper cache，要求另有一块不跑 stage 的 GPU。
- 生产状态与回滚：A 已恢复，server/engine/vision PID `522908/522922/522917`，health loaded，
  6/2 slots/groups，`expert_slots=17274`，engine/config hash 正确；production systemd 仍
  inactive/dead、`MainPID=0`、`NRestarts=23`。A→失败候选停机 8 秒，至 A loaded 总中断 `3:47`。
- 学到什么：预注册不能绕过真实参数语义；启动门应先解析同一二进制源码或用一秒钟 help 检查，
  不应把错误参数带进 3–4 分钟的加载窗口。
- 下一步与理由：`UTIL-PIPED-MTP-ORDER`。现有 `--expert-cache-cpu-order` 直接针对 GPU/CPU
  expert 浮点顺序差异，不需要强制 CUDA1 槽数；只加这一个开关，先跑相同逐字 parity，通过后
  才允许 2/4/6 路性能 A-B-A。契约见
  `docs/work/UTIL_PIPED_MTP_ORDER_CONTRACT-20261031.md`。

## I-20261031-77 · piped MTP wins two streams by 19.92% but fails six streams and repeatable parity

- 目标 / 指标：保留 6 slots / 2 groups，启用 `--batch-mtp` + `STRATA_PIPED_MTP=1`；
  A1-B-A2 固定测 2/4/6 路。2 路须相对 A 合并中位改善 ≥1%、至少 5/6 胜；4/6 路不得
  回退 >1%；固定 greedy 输出、health、RAM/VRAM 和生产 systemd 必须通过。
- 假设与预期：B 启动标记和六槽 drafters 在同 cache 条件下应恢复低并发吞吐，并保持目标模型
  验证后的输出；2 路应接近上游 A6000 的两位数收益。
- 为什么选这条路线：这是前五次 fused gate/up 微调均低于 1% 后，已有实现中最大且直接面向
  用户可感知并发凹点的机制；只需配置切换，不需要改源码。
- 改动与实验：从 A 复制独立 config，只加 `--batch-mtp`、`STRATA_PIPED_MTP=1` 并把
  `STRATA_BATCH_SELF_COMMIT=0`。启动日志确认 2 组 × 3 slots 和 6 个 drafters；按
  `tools/server/bench-strata.py` 跑 A1-B-A2，每档 3 reps、400 output tokens，并做三组固定
  greedy 探针各两次。
- 结果：A1/A2 合并中位 `78.3/108.55/125.7 tok/s`，B 为 `93.9/104.4/113.4`。2 路
  `+19.92%` 且 5/6 胜；4/6 路分别 `-3.82%/-9.79%`。135 个基准请求全部完成且无外来请求。
  精确文本和算术探针一致；自然语言探针 A 两次相同，B 两次生成不同文本。
- 判决：**UTIL-PIPED-MTP 否决。** 当前六槽两组配置不能采纳；不扫描草稿深度、槽数或组数。
- 预期被推翻：低并发收益真实且很大，但不能推广到 4/6 路；同时“目标验证”没有让磁盘上的
  文本输出在 cache 变化后保持可重复。B 的 CUDA1 cache 为 `7040` slots，A 为 `7209`；
  总 cache 为 `17104` 对 `17274`，与上游要求的同 cache 条件不符。
- 生产状态与回滚：A 已恢复，server/engine/vision PID `520066/520075/520070`，health loaded，
  engine `105a4171...`，config `88de996a...`，6/2 slots/groups，vram free `1006 MiB`。
  A→B 停机 8 秒、总中断约 `3:01`；B→A 停机 9 秒、总中断约 `4:01`。production systemd
  仍 inactive/dead、`MainPID=0`、`NRestarts=23`。
- 预期被推翻的边界：没有证据把所有 piped MTP 判为错误。上游文档明确说相同 expert cache
  下文本可一致，因此下一项只隔离 cache；若恢复 cache 后 parity 仍失败，再关闭该分支。
- 证据：`docs/work/UTIL_PIPED_MTP_RESULT-20261031.md`、台账 L-104、
  `/opt/ai/logs/util-piped-mtp-summary-20261031T1923Z.json`。
- 学到什么：并发优化必须同时过低并发峰值、高并发回退和逐字质量门；A 的恢复冷态会让 A2
  2 路明显偏低，因此契约要求的 A1/A2 合并中位和原始 reps 都必须保留。
- 下一步与理由：`UTIL-PIPED-MTP-CACHE`。只把 `--expert-cache auto` 替换为 A 的
  `10065`，并加 `--expert-cache-device1 7209`，然后先跑相同 parity；失败不消耗性能 A-B-A，
  通过后再确认 4/6 路是否由 cache 挤压造成。契约见
  `docs/work/UTIL_PIPED_MTP_CACHE_CONTRACT-20261031.md`。

## I-20261031-76 · read-only loads changed SASS but stopped at 0.339%; turn to pipelined MTP

- 目标 / 指标：只把 fused IQ3 gate/up 的 weight `qs`、aux/sign word 和 scale 改走显式只读
  加载；要求 principal 16-bit load 的 SASS 变化，7 组 gate/up 中位至少快 1%、5/7 胜，
  再以 long-scoreboard≤16.5%、global loads≤296,960、active warps≥69%、register≤85 裁决。
- 假设与预期：UTIL-NCU 的 long-scoreboard 占 `17.50%`，而当前 weight 块是
  `LD.E.U16.SYS`；若只读缓存路径降低该依赖延迟，时间应出现可测量改善。
- 为什么选这条路线：前四次 shared-memory 重组均失败；read-only path 不改变 codebook、
  activation、算术或输出，是 long-scoreboard 假设最便宜的直接测试。
- 改动与实验：默认关闭 `STRATA_IQ3_WEIGHT_RO`；最终用独立
  `native_gu_fused_kernel_ro` 和 `__launch_bounds__(256, 3)` 消除早先版本的 8-byte stack
  frame。默认 kernel 保持 73 registers，候选 80 registers；跑默认/分组 parity、SASS 核对和
  7 组双顺序交替进程。
- 结果：56 条 `LD.E.U16.SYS` 变为 48 条 `LDG.E.U16.CONSTANT.SYS`，两臂无 stack，14 个 hash
  都是 `9a055a5a94e15c87`。baseline/candidate 中位 `472.6/471.0 us`（+0.339%），candidate
  6/7 胜，p75 `471.8 us` 低于 baseline 中位；剔除第一组后约 `+0.317%`，方向稳定但未达 1%。
- 判决：**UTIL-WEIGHT-RO 否决。** 时间门先失败，不运行 NCU 或 engine A/B；不扫描 cache
  operator、row-pair、NTC 或邻近几何。
- 预期被推翻：long-scoreboard 的一部分可由显式只读路径改善，但真实机器码变化只带来约
  0.3% 收益；继续在 fused gate/up 上做单指令/load-policy 微调已无足够信息收益。
- 生产状态与回滚：源码、bench/parity 从
  `/opt/ai/tmp/pre-util-weight-ro-20261030T1845Z/` 恢复；source 回到 `47009da533...`，
  bench `8255d36e...`，engine `105a4171...`，两组 parity 0 failure。停机 8 秒、
  恢复 loaded health 162 秒，总中断约 `2:50`；最终 server/engine PID `514867/514949`，
  production systemd 不变。
- 证据：`docs/work/UTIL_WEIGHT_RO_RESULT-20261031.md`、台账 L-103、
  `/opt/ai/logs/util-weight-ro-sass-20261031T2048Z.txt` 和
  `/opt/ai/logs/util-weight-ro-ab-20261031T2050Z.log`。
- 学到什么：源码改动必须用 SASS 证明，但 SASS 变化也只证明机制生效，不证明用户收益。
  五次 fused gate/up 局部实验均未过 1%，应关闭这个局部方向，转向已被上游测量但本机尚未
  开启的结构性并发机会。
- 下一步与理由：`UTIL-PIPED-MTP`。v0.2.7 的两卡基准记录 `--batch-mtp` +
  `STRATA_PIPED_MTP=1` 在 2 路改善 `16.0%`、4 路改善 `10.2%`，且每槽输出一致；当前
  2080 Ti 六槽只差配置验证和显存风险。按 A1-B-A2 跑 2/4/6 路，先保住 6 slots / 2 groups。

## I-20261031-75 · 32-bank padded codebook still regressed; rotate to read-only weight loads

- 目标 / 指标：四副本 IQ3_XXS codebook 使用 stride 257，使每个 replica 的物理 bank 为
  `index + replica (mod 32)`；要求 7 组 kernel 中位至少快 1%、5/7 胜、p75 低于 baseline
  中位，并由 NCU 门确认 MIO/wavefronts 改善后才允许 engine A/B。
- 假设与预期：首版 `4*index+replica` 每副本只覆盖八个 bank；真正覆盖 32 banks 后，随机
  codebook 访问的 bank conflict 应下降，足以抵消三份额外 staging 和地址运算。
- 为什么选这条路线：它只修正预注册候选的已知地址缺陷，不改变任何索引、符号、dp4a、
  scale、row sum、SwiGLU 或 q8_1 算术，是当时最低风险的 MIO 实验。
- 改动与实验：默认关闭 `STRATA_IQ3_GRID_PAD`；固定 `4 * 257` shared words，地址
  `257 * replica + index`，replica 为 `lane & 3`。先修 shared 数组最大索引 1026 的越界，
  重建 bench/parity，确认两个 hash 后跑 7 组双顺序交替进程。
- 结果：所有 14 个 hash 均为 `9a055a5a94e15c87`，0 differing；baseline/candidate 中位
  `475.7/489.3 us`（−2.86%），candidate 仅胜 3/7，p75 `489.5 us` 高于 baseline 中位。
  剔除第一组冷时钟离群后仍约慢 1.2%，不是单个 outlier。
- 判决：**UTIL-GRID-PAD 否决。** 时间门在 NCU 前失败，不运行 counter 或 engine A/B；
  不扫描副本数或邻近 stride。
- 预期被推翻：即使每副本覆盖全部 32 banks，逐次选择 replica 的地址成本和复制工作仍超过
  随机 bank conflict 的减少；此前“覆盖 32 banks 就会改善”的静态推断不成立。
- 生产状态与回滚：源码、bench/parity 从
  `/opt/ai/tmp/pre-util-grid-pad-20261031T2032Z/` 恢复；source 回到 `47009da533...`，
  engine 保持 `105a4171...`，两组 parity 0 failure。停机 12 秒，恢复 loaded health 161 秒，
  总中断约 `2:53`；最终 server/engine PID `511649/511741`，production systemd 不变。
- 证据：结果见 `docs/work/UTIL_GRID_PAD_RESULT-20261031.md`，台账 L-102；原始恢复日志
  `/opt/ai/logs/qsa-diag-20261031T1653Z/restore-util-grid-pad.out`。
- 学到什么：共享访问的物理 bank 覆盖只是必要条件；每访问增加的 replica 选择、复制和寄存器/
  指令成本必须同时计算。连续四次 shared-memory 重组没有跨过 1%，这条局部路线应关闭。
- 下一步与理由：改用 NCU 中 `17.50%` long-scoreboard 这一独立证据，检查 fused IQ3 weight
  的 SASS。当前静态读取显示 weight 16-bit loads 为 `LD.E.U16.SYS`，只读参数为
  `LDG.E.CONSTANT.SYS`；预注册 `UTIL-WEIGHT-RO`，只改变 weight `qs`/aux/scale 的读取路径，
  先要求机器码确实变化，再跑同一 1% 时间门。

## I-20261031-74 · weight decode 复用只有 0.546%，编译器已消除源码重复，按 1% 门否决

- 目标 / 指标：每个 k 只解码一次 IQ3_XXS weight grid/aux/scale，供 NTC chunk 内多个 token
  应用；要求 7 组 kernel 中位至少快 1%、5/7 胜、MIO≤28.75%、wavefronts 下降后再接 engine A/B。
- 假设与预期：6-entry chunk 先执行 4-token 再执行 2-token，若 weight decode 按源码重复，
  显式复用可使 codebook lookup 总数约降 2.5 倍，应产生明显 MIO 与时间收益。
- 为什么选这条路线：UTIL-ACT 已确认 activation 被编译器复用；weight side 是该 counter 下
  剩余最具体、可形成独立开关且不改 arithmetic 的结构性假设。
- 改动与实验：opt-in `STRATA_IQ3_WEIGHT_REUSE=1` 单行工作集先 load gate k 并对 chunk 内
  tokens apply，再处理 up k；控制寄存器增长。真实权重层 36、`G=64, T=6` 做 7 组交替进程。
- 结果：baseline/candidate gate/up 中位 `475.8/473.2 us`，改善 `0.546%`；candidate
  6/7 胜，p75 `473.85 us` 低于 baseline 中位，所有 hash 都是 `9a055a5a94e15c87`。
  方向稳定，但未达到预注册 1%，不能采纳。
- counter：两臂 shared/global load 指令都精确为 `10,583,040/296,960`；wavefronts
  `17,698,290/17,698,304`，bank conflicts 基本相同；寄存器 73→80，MIO ratio
  `3.872857→4.069270`。编译器已把 weight-side 重复也消除，源码 working set 只改变调度。
- 判决：**UTIL-WEIGHT 否决。** 不接受无法跨过 1% 且增加寄存器的改动，不进入 engine A/B；
  不扫描邻近 NTC/chunk 或 row-pair 形状。
- 预期被推翻：当前源码中的重复 load 是表面现象，NVCC 已对成对 row/token 做 CSE。源码级
  工作集不能减少机器指令；需要改变 shared 地址算法本身。
- 恢复与成本：源码 hash 回到 `47009da533...`，parity/grouped parity 0 failure，engine
  `105a4171...`。为微基准停候选一次，恢复冷启动实测 `2:50`；生产 systemd 未改。
- 证据：`/opt/ai/logs/util-weight-ab-20261031T2010Z.log`；baseline/candidate counter
  `util-weight-*-counters-20261031T2013Z.ncu-rep`；结果见
  `docs/work/UTIL_WEIGHT_RESULT-20261031.md`，台账 L-101。
- 学到什么：连续三次给出相同/无收益的源码重排说明不能继续与编译器抢调度。四副本首版失败
  也找到了具体地址布局错误：`4*index+replica` 每个 replica 只覆盖 8 个 bank。
- 下一步与理由：执行预注册 `UTIL-GRID-PAD`，固定四副本、stride 257，使每个 replica 覆盖
  全部 32 个 bank；这是针对已证实的首版布局缺陷，而不是扫描副本数。

## I-20261031-73 · 显式 activation reload 与编译器生成代码等价，UTIL-ACT 否决

- 目标 / 指标：消除 gate/up 两个 row dot 对同一 q8_1 activation 的重复 shared load；
  要求 7 组过程 kernel 中位至少快 1%、5/7 胜、wavefronts 下降、MIO≤28.75%，再接 engine A/B。
- 假设与预期：counter 显示 shared-load 指令比 global-load 多约 35 倍，显式把 8 个 activation
  words 和 scale 装一次再传给两行，应减少一倍的 activation 读取和 MIO 压力。
- 为什么选这条路线：它不改 arithmetic、不增加 shared memory、作用域仅 mode-8 fused
  gate/up；比立即改 CTA/row 几何更便宜，也能直接验证编译器是否已做公共子表达式消除。
- 改动与实验：opt-in `STRATA_IQ3_ACT_RELOAD=1` 增加一个寄存器 activation record，供两个
  row dot 复用；默认路径保留。真实权重层 36、`G=64, T=6` 做 7 组交替进程，再跑同 kernel
  NCU counter。
- 结果：baseline/candidate gate/up 中位 `475.1/475.2 us`（−0.021%），candidate 3/7 胜，
  p75 `475.3 us` 高于 baseline 中位；所有 14 个 hash 均为 `9a055a5a94e15c87`。
- counter：两臂 shared-load instructions 都精确为 `10,583,040`，寄存器都为 73；wavefronts
  `17,698,354/17,698,418`，bank conflicts `7,115,314/7,115,378`。MIO ratio 的
  `<1%` 差异不伴随实际指令减少，不能宣称收益。
- 判决：**UTIL-ACT 否决。** 源码改动等价于编译器已有调度，不进入 engine A/B；不改变
  7 个激活 token 或行几何来重试同一假设。
- 预期被推翻：activation 在成对 row dot 间已经被编译器复用。剩余可消除的 shared work 是
  weight-side decode 在每个 NTC token 上重复执行。
- 恢复与成本：源码 hash 回到 `47009da533...`，parity/grouped parity 0 failure，engine
  `105a4171...`。为独占微基准停候选一次，事后恢复冷启动一次；本次实测 `3:50`，
  生产 systemd 未改。
- 证据：`/opt/ai/logs/util-act-ab-20261031T1945Z.log`；baseline/candidate counter 报告
  `util-act-*-counters-20261031T1948Z.ncu-rep`；结果见
  `docs/work/UTIL_ACT_RESULT-20261031.md`，台账 L-100。
- 学到什么：源码变量是否跨调用复用不等于 PTX/SASS 是否重复 load；counter 必须比较指令总数，
  不能只凭源码生命周期判断。下一步直接复用 weight decode，而不是继续搬 activation。
- 下一步与理由：执行预注册 `UTIL-WEIGHT`：每个 k 只解码一次 grid/aux/scale，对该 chunk
  最多四个 token 反复 apply；目标代码书读取约降 2.5 倍，仍以同一 1% 和 counter 门裁决。

## I-20261031-72 · 四路 codebook 复制不降 bank conflict，UTIL-KERNEL 按时间门否决

- 目标 / 指标：只改 `native_gu_fused_kernel<18>` 的 IQ3_XXS codebook 访问，保持输出算术；
  kernel 中位至少快 1%、MIO≤28.75%、active warps≥69%，通过后才允许一次 engine A/B。
- 假设与预期：NCU 的 MIO 主因来自随机 codebook 的 shared-bank conflict；四副本按 lane
  rotation 应把同时访问分散到更多 bank，至少抵消复制成本并降低 MIO。
- 为什么选这条路线：它只改 shared 地址、不改任何数值算术，风险低、回滚清晰，也是 UTIL-NCU
  预注册的唯一候选；相比之下直接改 CTA/row 几何会同时改变多个变量。
- 改动与预注册：opt-in `STRATA_IQ3_GRID_REPL=1` 把 256-entry codebook 以
  `4 * index + lane_rotation` 复制四份；未设置时保留一份路径。真实权重、层 36、
  `G=64, T=6`，先做 7 组交替进程 A/B。
- 正确性：14 个 baseline/candidate 进程的 hash 均为 `9a055a5a94e15c87`，0 differing；
  candidate 与 baseline 的输出算术一致。
- 时间：baseline 中位 `474.1 us`，candidate `474.5 us`，改善 `-0.084%`；candidate
  仅胜 2/7，p75 `475.35 us` 高于 baseline 中位。第一组 baseline 冷时钟 `664.1 us`
  不改变中位判决。
- 计数器：一次有界 NCU 解释否决原因。shared-load bank conflicts 从 `7,115,366` 升到
  `7,293,965`，wavefronts 从 `17,698,406` 升到 `17,877,005`，寄存器 73→80，静态 shared
  memory 2,048→5,120 B。随机索引下复制只改变地址置换，没有减少实际冲突，还增加 staging。
- 判决：**UTIL-KERNEL 否决。** 不扫描 2/8/16 副本。新增 counter 证据表明 kernel 共执行
  `10,583,040` 条 shared load、仅 `296,960` 条 global load；下一机制改为消除 gate/up 两行
  对同一 q8_1 activation block 的重复 shared-memory 读取。
- 预期被推翻：MIO 不是可继续压低的 codebook bank-conflict 余量；原布局已接近随机访问下限。
  真正仍可删除的是同一 activation 被两个 row dot 重复读取的 shared-load 指令。
- 恢复与成本：停候选一次用于独占 GPU 微基准；恢复源码 hash
  `47009da533...`、engine hash `105a4171...`，重建后 `native_expert_parity` 和
  `iq_multi_parity` 均 0 failure。候选冷启动实测 `2:45`，新 PID server/engine
  `507164/507178`，health loaded；生产 systemd 不变。
- 证据：`/opt/ai/logs/util-kernel-ab-20261031T1914Z.log`；baseline/candidate counter
  `.ncu-rep` 分别为 `util-lds-counters-20261031T1918Z` 与
  `util-lds-repl-counters-20261031T1921Z`。详情见
  `docs/work/UTIL_KERNEL_RESULT-20261031.md`，台账 L-099。
- 学到什么：MIO 是症状，不代表 codebook bank conflict 可继续下降。实测原始 256-entry
  layout 已接近随机访问的 bank 下限；优化应减少 shared load 指令/重复读取，而不是复制同一表。
- 下一步与理由：执行预注册 `UTIL-ACT`，在每次 k 迭代把 q8_1 activation words 和 scale
  显式装入寄存器一次，供 gate/up 两个 row dot 复用；所有门限不变，失败则不扫描相邻形状。

## I-20261031-71 · NCU 根因定为 MIO throttle，完整请求 replay 因等待成本失败

- 目标 / 指标：获取真实长 prompt 热点 kernel 的 SM、occupancy、Tensor、DRAM/L2 和 warp stall，
  只选择一个代码根因，并预注册一个 UTIL-KERNEL 候选。
- 假设与预期：此前只有阶段时间和 NVML，不能区分算术、内存、同步或 occupancy；NCU 应给出
  唯一可执行根因。
- 审计与失败样本：Nsys CUDA 小程序自检通过，但服务的三次 fork/exec trace 都没有 kernel
  表。新增 direct-engine wrapper 后，全 section NCU 在 `98,329` token 请求的尾部 verify
  window 等待 expert copies 超过 60 秒；引擎 watchdog 在 `920 s` 结束请求，没有报告。
  这次重启和学习成本保留，不用完整请求 NCU 继续迭代。
- 回退测量：改用同一候选二进制、mode 8、真实 GGUF 权重和 `G=64, T=6` 的
  `native_expert_bench` 层 36 几何。输出 hash `9a055a5a94e15c87`，两臂 bitwise 相同；
  NCU 13-pass 与 9-pass 报告均在数秒内完成。
- 结果：`native_gu_fused_kernel<18>` 的 SM throughput `58.07%`、active warps `71.59%`、
  FMA `41.15%`、Tensor `0.04%`、DRAM `38.09%`、L2 `15.76%`；stall 依次为 MIO
  `29.75%`、long scoreboard `17.50%`、short scoreboard `12.80%`、not-selected `9.98%`、
  wait `9.15%`、math pipe `4.31%`、barrier `3.76%`。
- 判决：**MIO throttle 为唯一主因；Tensor 路线不恢复。** 开一个 opt-in
  `native_gu_fused_kernel<18>` shared-codebook bank-rotation 实验；kernel median 至少快 1%，
  且 MIO 至少降到 28.75%，才允许进入一次 engine A/B。
- 恢复：原候选已手动恢复 8080，server/engine PID `504632/504639`，engine
  `105a4171...`，health loaded，无 profiler/QSA 实验 env；生产 systemd 仍
  inactive/dead、`MainPID=0`、`NRestarts=23`。
- 证据：`/opt/ai/logs/ncu-micro-gu-metrics-20261030T180641Z.ncu-rep`；result 与 contract
  见 `docs/work/UTIL_NCU_RESULT-20261031.md`、`docs/work/UTIL_KERNEL_CONTRACT-20261031.md`；
  台账 L-098。
- 学到什么：全 section kernel replay 会与双卡流水和 resident-expert 状态冲突，不能把“能启动
  profiler”当作“profiler 可用于迭代”。真实权重微内核可以低成本区分 MIO、Tensor 和 DRAM；
  但完整请求结论仍必须由后续 engine A/B 取得。
- 下一步与理由：实现 4-way bank-rotated shared codebook，先过输出 hash、7 次交替进程时间、
  MIO 和资源门。默认路径保持不变，失败就回滚并换一个与 shared-memory 冲突不同的机制。

## I-20261031-70 · QSA 三 query 同 CTA 共享缓存慢 20%，按预注册门否决

- 目标 / 指标：实现一个 opt-in、输出精确的相邻 query selected K/V 共享路径；先过 kernel
  预注册门，只有 wall 与 `qsa attn` 在同冷 150K A-B-A 中稳定同向改善 ≥1% 才采纳。
- 假设与预期：默认 int8 attention 每个 query 一个 128-thread CTA；把三个相邻 query 放进一个
  384-thread CTA，可让它们的同 chunk K/V 请求进入同一 SM cache，同时保持每 SM 3 份计算状态、
  12 个 warp 和每 query 原算术。
- 审计与预注册：基线 kernel 为 168 registers/thread、19,712 B dynamic shared memory，恰好
  3 CTA/SM。三 query CTA 也编译为 168 registers/thread、无 stack/local，使用 64,512 registers
  和 59,136 B shared memory，仍在单 CTA 容量内。契约要求 output hash 不变、不得 spill、kernel
  至少不慢，任何一项失败便不进入长请求。
- 改动与实验：只在实验构建中把 v2 int8 kernel 模板化为 1/3 query 同 CTA，三组各自保留原
  `Smem2T`、online softmax 和 p.v；用 `QA_SAME=4` 提供比真实相邻 query 更强的共享上界。
  运行默认/分组 parity 和一半交替微基准。
- 结果：六组 parity 输出 hash 全部相同。普通 selection 的 32K int8 由 `9.716` 升到
  `11.664 ms`（+20.05%）；`QA_SAME=4` 从 `9.815` 升到 `11.852 ms`（+20.75%）。
- 判决：**QSA-UNION-ATTN 否决**。连“四个 query 完全同选集”的最好情况都慢 20%，CTA 相位
  对齐和尾组工作损失大于缓存复用；按契约不消耗 150K cold A-B-A 与重启预算。
- 恢复：实验源码与候选 engine 分离，只有 `pa_bench`/parity 被重建；源码恢复为 sha256
  `1d1181f...`，运行中 engine 始终为 `105a4171...`。候选 server/engine PID
  `491072/491135`、health loaded、无实验 env；生产 systemd 仍 inactive/dead。
- 验证：Python `294 passed, 56 skipped, 93 subtests passed`；远端 7 个 QSA CTest
  `7/7 passed`；offscreen preflight PASS。
- 证据：`/opt/ai/logs/qsa-union-attn-20261030T171808Z/`；contract sha256
  `d09cef6589200a3986e7af05b50f99f54d9071506ee4cd3f593467a5ffb00b5f`，result sha256
  `c53c49e3a24319b30a111468db7653b69c9569999ec5db068fb7e5b38ea61700`。台账见 L-097。
- 学到什么：共享机会本身不等于可兑现收益；同 SM 缓存复用必须同时守住 warp 调度自由度，
  把独立 CTA 锁进一个 block-wide 相位会放大同步成本。最强重叠合成样本必须先过 kernel 门。
- 下一步与理由：进入 UTIL-NCU，获取长 prompt 热点 kernel 的 occupancy、issue、Tensor、DRAM
  和 stall reason。QSA 的两条低风险路线（FP16 scorer、cache co-residency）都已用证据关闭，
  继续猜内核前先补硬件根因。

## I-20261031-69 · 150K QSA 诊断：overlap 中位 28%，FP16 一致率 70.92%，只保留候选共享方向

- 目标 / 指标：用一个真实约 150K 冷 prompt，读取 16-query selected-cell union、FP16/FP32
  选择一致率、差异 cells、clean `qsa select` 成本和尾部 active blocks；按预注册门决定是否做
  一个最小实现。
- 审计发现：两个既有 hook 都由 env 是否存在触发；overlap hook 只在每个 chunk 的第一个 QSA
  layer 运行。既有 FP16 hook 的缓冲区是进程级单例，在双卡 layer split 上会跨设备传指针。
- 改动与实验：诊断 config 只增加 `STRATA_SEL_OVERLAP=1`、`STRATA_IDX_FP16_CHECK=1` 和
  `STRATA_PREFILL_TIMING=1`；在诊断 copy 前补计时边界，使 `qsa select` 不包含复算/同步，
  overlap 行补尾部 active blocks/width，FP16 行补差异 cells。同几何先跑
  `qsa_select_bench 150000 256 10 262144`。
- 失败样本：第一次 150K 请求在首个 chunk 后 illegal memory access；根因是 FP16 检查缓冲在
  第二张卡仍使用第一张卡的指针。改为每 GPU 独立缓冲、增量重建后重跑；失败 JSON/summary/日志
  保留，不计入性能或诊断判决。
- 结果：正式请求 `150,069` prompt tokens、0 reused、0 错误、needle 正确。19 个 chunk 的
  overlap 中位 `28.0%`、均值 `26.84%`；最后 chunk T `7,214`、active blocks `37,516`、
  width `2,051`。FP16 全选择相同 `1,203,932/1,697,576`（`70.9207%`），稀疏 cells 相同
  `99.9619%`，差异 cells `1,306,065`。
- 成本与判据：诊断 `qsa select` 为 `3,929 ms`，占 diagnostic GPU timeline `98,075 ms` 的
  `4.0%`；同几何生产 scorer/top-k 为 `2.020/0.899 ms`，scorer 占 `69.20%`。以 overlap
  `0.28` 计算候选共享乐观上界 `4.0061% × 69.20% × 72% = 1.996%`，过预注册 `1.25%` 门；
  FP16 的全选择一致率远低于 `99.9%`，路线否决。
- 正确性与资源：正式诊断 warm wall `105.864 s`（诊断同步成本高，不作性能结论）；最低
  MemAvailable `12,853.2 MiB`，swap 未增长，两卡峰值约 `21,228/21,126 MiB`。
- 判决：**QSA-DIAG 完成；QSA-FP16 否决；QSA-CANDIDATES 通过诊断门，允许一次 exact
  attention-side K/V 共享读取实现**。低 overlap 不能用于跳过或近似 indexer scorer/top-k。
- 生产状态与回滚：候选执行了诊断重启、失败后 engine 重载和恢复重启；基线二进制已逐字节恢复，
  server/engine PID `491072/491135`，engine `105a4171...`、health loaded、诊断 env 不存在。
  生产 systemd/config 未改，仍为 inactive/dead、`MainPID=0`、`NRestarts=23`。
- 证据：`/opt/ai/logs/qsa-diag-20261031T1653Z/`；contract sha256
  `4945d3c327e1cc6273f707d93bd899c7a64bfe5df590e5e7b81ad3cbd2ede104`。台账见 L-096，
  报告见 `docs/work/QSA_DIAGNOSTIC_RESULT-20261031.md`。
- 学到什么：低 selected-cell union 是 attention K/V 读取共享的机会，不是“知道了 final top-k
  就能跳过 score”的理由；双卡调试 hook 的状态必须按 device 分配。诊断必须把自身同步和干净
  phase timing 分开，否则会把 98 s 诊断成本误当成选择成本。
- 下一步与理由：进入 QSA-UNION-ATTN，实现一个 opt-in exact 路径并做同负载 A-B-A。它必须同时
  改善 wall 与 `qsa attn` 至少 1% 且保持 parity；若 union 管理或 shared-memory 开销吃掉收益，
  回滚并转向 UTIL-NCU。

## I-20261031-68 · 当前默认 attention v2 在 1% 门下复验通过：read +1.814%，wall +1.905%

- 目标 / 指标：按用户新的约 1% 采纳门复验 P6。当前源码已把 prompt-attention v2 设为默认，
  因此比较当前默认 v2 与 `STRATA_PA_V2T=0` 回退 v1；要求 engine read 和 wall 均改善 ≥1%。
- 审计发现：`src/kernels/cuda/qsa_prompt_attn.cu` 只在
  `STRATA_PA_V2T=0` 或 `STRATA_PROMPT_ATTN_V1` 有值时选择 v1；候选 engine 的实际环境里
  两者都未设置。当前 engine sha256 仍为
  `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`。
- 改动与实验：新增可复用的 `tools/server/long-context-sequential.py`，支持固定 salt 生成
  同一组冷 prompt 并同步采样 metrics、RAM、swap 与 GPU。A1(v2) → B(v1) → A2(v2)；
  B 和 A2 各干净重启一次，每轮约 2.5–3 分钟。
- 输入：每臂三个顺序、唯一、重启后冷读的 96K prompt，token 数为
  `98353/98350/98353`；三臂使用完全相同的文本。
- 结果：A1 engine read 中位 `40063.1 ms`、wall `40.368 s`；B 为 `41022.6 ms`、
  `41.382 s`；A2 为 `40564.9 ms`、`40.878 s`。A1+A2 合并 read 中位
  `40278.25 ms`、wall `40.5935 s`。
- 收益与稳定性：当前 v2 相对 v1 的 engine read 改善 `1.814%`、wall 改善 `1.905%`，
  两项均跨过 1% 门；A2 相对 A1 漂移 `+1.253%/+1.263%`，均在 3% 内。
- 正确性与资源：三臂各 0 HTTP/执行错误、0 意外 length、needle 3/3；greedy parity
  text sha 均为 `b17bc92de6ba8a97`。MemAvailable 最低 `9745.5 MiB`，SwapFree 始终
  `4088.4 MiB`，两卡占用峰值约 `21128/21028 MiB`，没有 OOM 或槽位下降。
- 判决：**P6 在当前版本按 1% 门复验通过，当前默认 v2 保留并视为已采纳**。这项收益已经在
  候选默认路径中，不需要再添加 `STRATA_PA_V2T=1`，也不新增生产配置变更。
- 生产状态与回滚：生产 systemd 未修改且保持 inactive。候选 8080 已恢复 A 配置，
  server/engine PID `486207/486247`，health `loaded=true`、6 槽、INT8 KV、17274 expert slots。
- 证据：`/opt/ai/logs/P6-current-20261030T162623Z/`；三臂 JSONL/summary、三份 parity、
  两次启动输出、预注册 contract、比较表和工具源码均保留。台账见 L-095。
- 学到什么：旧实验的 env-on A/B 不能直接复用到已把候选设默认的新版本；每次复验都要先核对
  当前 branch/default，再设计 on/off 方向，否则两臂会相同。
- 下一步与理由：进入 QSA-DIAG。在真实 150K prompt 上同时读取现有
  `STRATA_SEL_OVERLAP=1` 与 `STRATA_IDX_FP16_CHECK=1` 输出，先判断 selected-cell overlap
  和 FP16 一致性；诊断不支持时不实现选择复用或半精度 kernel。

## I-20261031-67 · 显式 layer-major 真实执行，但长上下文吞吐整体回退约 14%，否决

- 目标 / 指标：验证显式 `STRATA_PREFILL_LAYER_MAJOR=1` 能否在默认 512 MiB 余量之外，以
  16K target、host residual 安全执行；若真实进入 super-chunk，则用同一固定 seed 比较
  150K/210K 冷读、续读和 25% 改写，保留稳定、可归因的 ≥1% 改善。
- 假设与预期：把 layer-major 默认余量从 512 MiB 降到 96 MiB，小到 16K 的 super-chunk
  应能在现役 batch/expert cache 形状内分配，并减少专家重复 staging 与短 launch。
- 为什么选这条路线：L0 已证明只移除 `--prefill-pipe 1` 会在容量门槛直接回退，因此本轮先
  验证一个明确容量策略；只有路径实际执行后，长请求数字才有归因价值。
- 改动与实验：候选源码和二进制不变；从 A1 配置移除 `--prefill-pipe 1`，增加
  `STRATA_PREFILL_LAYER_MAJOR=1`、16K target 和 `STRATA_PREFILL_LM_R=host`。C 配置各重启
  一次，测试完成后又重启一次恢复 A，两次启动各约 2.5–3 分钟；运行路径始终是候选 8080。
- 结果：路径真实执行。150K 请求记录 `149133 tokens in 10 super-chunk(s)`，210K 记录
  `208934 tokens in 13 super-chunk(s)`，均打印 `R in pinned RAM`，两个层段都走 layer-major。
  六个请求全部成功，没有 OOM，swap 保持未增长；未另做 needle/parity 专用质量集。
- 性能：C 相对 A1 的 prompt 吞吐为 150K cold `1909.5` 对 `2104.1`（−9.25%）、rewrite25
  `1884.0` 对 `2348.9`（−19.79%）；210K cold `2008.2` 对 `2373.1`（−15.38%）、warm
  `807.0` 对 `1097.7`（−26.48%）、rewrite25 `1901.8` 对 `2313.0`（−17.78%）。六项中位
  `1892.9` 对 `2208.55 tok/s`（−14.29%）。
- 异常与归因：150K warm 的 wall 从 `7.641 s` 降到 `3.559 s`，但 cached tokens 同时从
  `142848` 增到 `147456`，read tokens 从约 6297 降到 1689；这是本次缓存命中差异，不是
  layer-major 计算的独立收益。其余五个吞吐样本均明显回退，不能用这一项抵消。
- 判决：**否决显式 16K、host-residual layer-major 作为当前生产候选**。路径和容量假设成立，
  但端到端性能不成立；不再重复相同或邻近 target 的参数扫描。
- 生产状态与回滚：生产 systemd 未修改且保持 inactive。候选 8080 已恢复 A 配置，health 200、
  `loaded=true`，server/engine PID `481955/481964`；恢复进程显存和 swap 状态正常。
- 证据：`/opt/ai/logs/lm-explicit-20261030T161831Z/`；六请求 JSON、路径日志、engine 日志、
  脱敏配置与 A/C 比较均保留。台账见 L-094。
- 学到什么：容量开关能打开路径，不等于跨过性能门；比较 warm 必须同时核对 cached/read
  tokens，不能把缓存差异写成算法收益。
- 下一步与理由：用户已把采纳门改为稳定、可归因的约 1%；P6 Turing prompt-attention v2
  已有 A-B-A、parity 和 needle 证据，engine read/wall 约改善 2.3%，应优先按新门槛复验并在
  候选配置评估采纳。之后再做真实 150K prompt 的 QSA overlap/FP16 诊断。

## I-20261031-66 · 直接移除 `--prefill-pipe 1` 无法启动 layer-major：最小 super-chunk 也缺显存

- 目标 / 指标：验证交接计划中的 L0：保持二进制、模型、环境和缓存配置不变，只移除
  `--prefill-pipe 1`，让长 prompt 进入已有的 layer-major prefill；比较 150K/210K 冷读、
  续读和 25% 改写的 prompt 吞吐、wall、错误与内存边界。
- 假设与预期：pipe plan 是当前拒绝 layer-major 的直接原因；移除后应进入 16K/32K
  super-chunk，减少专家重复 staging 和短 MMQ launch。用户已明确稳定、可归因的约 1% 改善
  也值得保留，因此采纳线不再机械限定 3%。
- 为什么选这条路线：这是交接计划中预期收益最大、改动最小且最容易回滚的 L0 配置实验；
  先在候选 8080 上判断路径能否启动，比直接修改调度代码或继续调 kernel 更有信息价值。
- 改动与实验：候选源码未改；新建 A1（保留 pipe）和 B（只移除 pipe 一项）配置。重启后确认
  health、实际 engine hash、GPU/内存和路径日志。A1 使用固定 seed 跑完 150K/210K 六请求；
  B 在首个请求日志确认回退后立即停止，未生成性能结论。每轮启动耗时约 2.5–3 分钟。
- 结果：A1 六项全部成功。prompt 吞吐为 150K cold `2104.1`、warm `843.8`、rewrite25
  `2348.9` tok/s；210K cold `2373.1`、warm `1097.7`、rewrite25 `2313.0` tok/s，中位
  `2208.55 tok/s`。B 的路径日志为
  `layer-major off for this prompt: no VRAM for a super-chunk (888 MiB free)`；默认
  by-default 模式保留 512 MiB 安全余量，现役 batch/expert cache 配置下最小 super-chunk
  也无法分配。
- 判决：**否决 L0 的“仅移除 pipe 即获益”假设**。这不是 layer-major 的计算性能结论，
  而是路径未执行的配置/容量结论；继续跑 B 不产生可归因数据。
- 预期被推翻：pipe plan 不是唯一门槛；显存余量先一步阻止了 layer-major。B 还未来到
  super-chunk 计算和输出质量阶段。
- 生产状态与回滚：生产 systemd 未修改；实验前后均为 inactive。候选 8080 已恢复 A 配置，
  health 200、`loaded=true`，server/engine PID 为 `479876/479879`，engine sha256
  `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`。
- 证据：`/opt/ai/logs/layer-major-l0-20261030T160244Z/`；A1 结果、
  `b-path.txt`、两轮路径日志、启动日志和脱敏配置均保留。台账见 L-093。
- 学到什么：必须先证明实验开关进入目标路径，再投入长请求测量；容量门槛和调度开关要分开
  归因。当前应该验证“显式 layer-major 的 96 MiB 余量 + 较小 super-chunk”能否安全启动，
  而不是把本轮写成 layer-major 本身无效。
- 下一步与理由：新目标只测一个容量策略：显式 `STRATA_PREFILL_LAYER_MAJOR=1`，先尝试
  16K target、host residual；日志必须实际打印 super-chunk 执行，若仍回退则记录原因并停止。
  只有路径启动且质量/内存安全，才做 150K/210K A-B-A。

## I-20261028-65 · 上游 37 提交部署并整理工作区：保留有效工具，清理冲突与文档噪音

- 目标 / 指标：把上游 v0.2.7 中有用的改动完整合入并部署，同时处理本地暂存区、未暂存修改和
  未跟踪文件；保留能提高观测与验证能力的内容，不把冲突半成品或无效链接改写带进主线。
- 依据：暂存区原本只有 4 个文档重命名；真正有独立价值的是 GPU 利用率探针、NCU 启动工具、
  `ctx256` soak profile、对应测试、资源利用率计划和实测台账。`generate.cpp` 的 629 行本地差异
  含 8 处冲突标记，并且缺少上游已加入的 logprobs、piped MTP 等功能，不能提交。
- 改动与实验：从最新 `c219fbca` 建独立 worktree，把本地 tracked 改动三方合入；丢弃损坏的
  `generate.cpp` 半成品和一组把有效链接改成普通代码的文档噪音；保留归档重命名、历史审计状态、
  实测证据和新工具。服务器从干净树 `/opt/ai/src/strata-027-picks` 独立构建到
  `build75-027`，没有复用运行中的二进制。
- 结果：现役 engine `0.1.51` / md5 `0e4a9596`、server `11fd33f0`、config `023663ba`，health
  `loaded=true`、`NRestarts=0`。数字序列 6 路聚合 `163.70/165.66 tok/s`，自然语言 6 路
  `148.94/150.71 tok/s`；3,427-token 冷读 `923.7/931.4 tok/s`。
- 并发结论：2 路数字序列只有 `94.03/95.67 tok/s`，低于单路 `102.46/105.64`，存在吞吐凹点；
  8 路为 `137.85/139.66 tok/s`，低于 6 路且 TTFT 约 `5.7–5.8 s`。6 路是当前可接受峰值，
  但不能据此认定 2 路或 8 路已经优化。
- 工具验证：新增 NCU 启动器、GPU 利用率探针、版本探针路径修复和 `ctx256` profile 的联合测试为
  `28 passed, 1 skipped`，脚本 `py_compile` 通过；版本探针在现网识别 `build75-027/strata`
  并从 server argv 读取实际 config，返回 `problems=[]`。服务器终端最终测试为
  `1224 passed, 56 skipped, 10894 subtests passed`。
- 测试缺口：CTest 仍有 6 项失败（Q6_K 表、Q8_0 grouped、OOM、缺 Q2_0 PLE 资产等），
  因此判决为“运行验收采纳、测试未全绿”。
- 生产状态与回滚：服务保持 `active/running`、`NRestarts=0`；旧二进制仍在
  `/opt/ai/src/strata-0.2.2/build/strata`。本地整理前的 tracked 状态保存在
  `refs/codex-backup/pre-workspace-cleanup-20261028T131909Z`，未跟踪文件另有归档。
- 学到什么：判断本地改动必须相对最新远端重新分类；旧分支上的大 diff 可能只是上游新代码的
  反向差异。性能“更好”也不能只看最高聚合值，必须同时看低并发凹点、TTFT 和测试门。
- 下一步与理由：先完成本轮分支提交与远端同步；后续性能工作从 2 路凹点和 NCU 根因入手，
  同时补掉已知 CTest 缺口，不直接开始新的参数扫描。

## I-20261027-64 · 资源利用率优先，停止独立参数探索

- 目标 / 指标：把后续主线从“再试一个参数”改为“提高真实资源利用率”，并以瓶颈管线计数和
  端到端任务时间同时改善作为代码改动门。
- 依据：L-091 已证明 NVML 高值不可靠；32K 冷 prefill 中多数 SM 参与，但 SM Issue、Tensor
  和 warp residency 不高，DRAM 也未饱和。仅靠现有计数尚不能指出具体 stall reason。
- 决策：NCU 继续用于根因归因，但它不是交付物；得到证据后必须落在一个内核或调度代码路径的
  最小改动上，不能用参数 A/B 代替优化。
- 路线影响：P7 从“暂停/待做”改为“搁置”，只有 NCU 证明 Tensor 管线是直接瓶颈时才恢复；
  env 参数、批次大小和既有开关不再作为独立探索方向。代码候选完成后，参数 A/B 只用于部署门。
- 验证协议：改代码前先固定同一模型、同一 32K 冷 prompt、目标硬件计数和 wall 门限；候选必须在
  同一负载下改善目标计数，同时不恶化端到端 wall、输出正确性和资源稳定性。
- 生产状态与回滚：本条只调整路线，未改生产、未重启服务；冻结基线仍为
  `0.1.51` / `a09d78b1`。
- 下一步与理由：在独立 engine 实例上用 NCU 找到真实 stall，再提交一个资源利用率代码切片。

## I-20261027-63 · 用 Nsight 硬件计数纠正 NVML 利用率口径

- 目标 / 指标：解释现役 32K 冷 prefill 在 NVML 已显示 95/98% 时，真实 SM、issue、Tensor、
  warp 和 DRAM 管线各有多少占用，并为后续内核调优建立不误判的观测入口。
- 假设与预期：`utilization.gpu` 只表示时间窗口内有活动；若真实计算管线也接近饱和，应同时看到
  高 SM Issue、Tensor Active 和稳定的 DRAM 压力，而不是只有 NVML 高值。
- 为什么选这条路线：NVML 无法区分算术、Tensor、指令发射和等待；Nsight Systems GPU Metrics
  可在不重启现役服务的情况下采双卡硬件计数器。NCU 无法安全附加到现役 engine，因此本轮不强行使用。
- 改动与实验：新增 `probe-gpu-utilization.py` 与 strict pytest；root 启动系统级 Nsight GPU Metrics，
  1 Hz NVML 交叉核对，执行短 decode 和约 32K 冷 prefill。请求前检查服务 idle、PID、文件和 config，
  请求后复查 PID/hash/health/idle；HTTP、finish、needle、长度、prompt 尺寸或 cache 重用不合格即拒绝。
- 结果：两次有效 run 的 32K prefill 中位均为 SMs Active `77%`、Tensor `10%`、Warps `28%`、
  DRAM read `15%`，SM Issue 为 `13–14%`；最终部署脚本的同窗 NVML 为 `40/99%`，较早 run 为
  `95/98%`。SMs Active p90 为 `98%`，但 DRAM 最大仅 `41%`，没有证据支持 DRAM 饱和。
- 判决：**结论**。NVML 95/98% 不是真实计算利用率；当前更像是多数 SM 被调度参与，但 issue、
  Tensor 和 warp residency 不高，仍不能在没有 NCU stall reason 时指定具体原因。
- 预期被推翻：SMs Active 并不低，不能把结果写成“GPU 实际只有 14% 在用”；14% 是 SM Issue，
  与占用 SM 的比例不是同一口径。
- 生产状态与回滚：探测只读，未重启或替换 engine/server。最终 run 前后 service loaded/idle，
  PIDs 为 server `186221`、engine `186232`，结束后无 active Nsight session。失败 run
  `0827Z/0837Z/0844Z` 分别被长度、needle 和截断质量门拒绝。收尾时发现一个遗留约 2 小时 26 分、
  停在密码提示且从未执行停止的 `sudo systemctl stop` 进程，已单独终止；服务 PID 未变。
- 证据：L-091；`/opt/ai/logs/gpu-util-final-20261027T0848Z/`、较早的
  `/opt/ai/logs/gpu-util-20261027T0850Z/` 与
  `tools/server/tests/test_probe_gpu_utilization.py`。
- 学到什么：先确认指标口径，再谈优化。单一利用率数字不足以定位瓶颈，失败样本必须保留以证明门禁有效。
- 下一步与理由：在独立受控 engine 实例上用 NCU 检查 SM throughput、occupancy、Tensor pipe、
  DRAM throughput 和 warp stall；随后改对应代码，提高资源利用率。没有这层证据前不根据
  `SMs Active` 或 NVML 改动内核，也不转入独立参数探索。

## I-20261027-62 · CTX256 一小时混合 soak 通过，冻结 `0.1.51` 生产基线

- 目标 / 指标：验证现役 `0.1.51` / `a09d78b1` 在 `6×256K`、6 槽下持续一小时混合负载时，
  请求正确性、长上下文、needle、槽位配置和 RAM/swap 是否稳定；零意外错误、零意外 length、
  零 needle 失败后才能冻结。
- 假设与预期：版本吞吐门已通过，但一次 240K 单请求与一次短并发不足以证明长时间资源稳定；
  soak 应暴露 RAM 交换、槽位降级或长读回归。
- 为什么选这条路线：用户要求先验证 256K 的吞吐，再在通过后执行一小时混合 soak；只有现场
  生产配置和真实请求路径能覆盖 conversation cache、expert cache 与冷读共同作用。
- 改动与实验：`mixed-soak.py` 增加 `ctx256` profile，在原 mixed wave 后加入约 240K cold read；
  运行 `3607 s`，覆盖短 JSON、代码、agent tool、32K/64K/96K 和约 240K。
- 结果：414 请求、133 waves、HTTP 200 414/414、零执行错误、零意外 length；needle 74/74；
  14 次约 245.9K 长读全部成功，wall 中位 `101.077 s`。batch slots 始终 6、expert slots 始终
  17248、version 始终 `0.1.51`。RSS 末值 `51,151.7 MiB`，最低 available RAM `2430.4 MiB`，
  swap 始终未用；GPU 温度/功率最大 `81 °C / 310.87 W`。
- 判决：**采纳**。`CTX256` 稳定性门通过，当前 engine/server/config 冻结为生产基线；这不是
  262144 满窗口或六路满载证明。
- 预期被推翻：持续负载下最低 available RAM 到 `2430.4 MiB`，虽然未触发 swap，但余量仍低，
  后续多长读或提高并发必须继续监控。
- 生产状态与回滚：run 结束后 service `active/running`、`NRestarts=0`，engine PID `186232`；
  T-probe `problems=[]`。当前回滚快照仍为 `/opt/ai/tmp/pre-ctx256k-20261026T075110Z/`，
  后续实验不得直接覆盖冻结基线。
- 证据：L-090；`/opt/ai/logs/CTX256-mixed-soak-20261027T065554Z.*` 与
  `/opt/ai/logs/T-probe-ctx256-post-20261027T075803Z.json`。
- 学到什么：长期资源结论必须看最低 available RAM、swap、RSS 峰值与槽位数组，不能只看末值；
  高并发 soak 仍需和“配置未降级”分开表述。
- 下一步与理由：恢复 P7 前先从真实 GPU 管线证据确定优化方向；P7 仍必须从全新 A1 完整重跑，
  不复用被中断样本。

## I-20261027-61 · 256K 生产配置下新版吞吐无显著回退，下一步先做长期 soak

- 目标 / 指标：在现役 `6×256K` 生产配置上确认 `0.1.51` / `a09d78b1` 相对 `0.1.49.1` / `8d680c69`
  是否存在明显吞吐变化；按项目既有门限，单路 decode、4 路聚合和冷读均以 ±3% 判断噪声。
- 假设与预期：256K 比 192K 少约 2.7% 专家槽，可能带来小幅吞吐代价；若仍落在 3% 内，就不应把
  版本切换本身当成当前主要瓶颈。
- 为什么选这条路线：已有五臂 A-B-A 只覆盖 192K，不能直接代表当前 256K 生产形态；先测当前版本影响，
  避免在错误基线上安排 kernel 或调度实验。
- 改动与实验：保持同一 server、launcher、config 和 24 个运行态 env，只替换 engine 二进制，执行
  `新版 B1 → 旧版 A → 新版 B2`。每臂运行固定单路/4 路基准 3 次、greedy parity、thinking parity
  和 3 次无重复长读；机器上无其他 benchmark 或 soak 进程，三个基准窗口的 `foreign_requests` 都是 0。
- 结果：旧版单路 decode 中位 `82.7 tok/s`、4 路聚合 `139.6 tok/s`、冷读 `2273 tok/s`；新版依次为
  `80.45 tok/s`、`136.05 tok/s`、`2267 tok/s`，变化 `−2.72% / −2.54% / −0.26%`，全部在 3% 门内。
  wall 口径的单路为 `78.95` 对 `72.2 tok/s`（`+9.35%`），但它包含首请求固定开销，不作为吞吐主指标。
  三个臂的 greedy 输出哈希都是 `b17bc92de6ba8a97`；所有测试请求正常完成，没有 HTTP 或执行错误。
- 判决：**未发现明显吞吐回退，不接受“新版更快”或“新版整体回退”的概括**；版本吞吐门通过，
  当前剩余重点转为 256K 长期稳定性。
- 预期被推翻：4 路没有沿用旧版五臂结果的 `−0.3%`，在 256K 降到 `−2.54%`；方向仍低于 3%，
  但余量只有约 0.46 个百分点，后续不能忽略配置与 boot 噪声。
- 生产状态与回滚：已恢复新版 `a09d78b1`，service active、health 200；T-probe `problems=[]`，
  engine `0.1.51`、`batch_slots=6`、`expert_slots=17248`、`max_context=262144`。切换前新版快照为
  `/opt/ai/tmp/pre-throughput-ab-20261027T061947Z/`，旧版基线快照为
  `/opt/ai/tmp/pre-v026-20261026T025109Z/`。
- 证据：L-089；`/opt/ai/logs/arm-v026-256-{new-B1,old-A,new-B2}.log`、
  `/opt/ai/logs/T-probe-throughput-final-20261027.json` 与 `/opt/ai/logs/bench-results.jsonl`。
- 学到什么：比较版本吞吐必须用当前生产上下文和缓存形态；192K 结论只能作为先前证据，
  不能替代 256K 的现场验收。
- 下一步与理由：先完成 `CTX256` 一小时混合 soak，覆盖长读、短请求并发、内存与槽位稳定性。
  soak 通过后再恢复 P7；吞吐门已经通过，不应先做无证据的版本回退或重复 A-B-A。

## I-20261027-60 · 恢复生产 pack 的 IQ2/IQ3_S 专家格式并通过真实模型验证

- 目标 / 指标：让收敛后的源码构建出的引擎直接跑生产 pack `sc117_iq3_xxs`（262144 上下文、parallel 6），
  完成整树替换前的真实模型端到端验证，而不是只靠单元/合成测试。
- 假设与预期：收敛提交 `331f30bf` 把 native expert gate/up 裁到 IQ3_XXS(18)/Q8_0(8)，但生产 pack 的
  `native_experts.txt` 实际使用 16/17/18/21/22（IQ2_XXS、IQ2_XS、IQ3_XXS、IQ3_S、IQ2_S）；生产源码的同类内核
  支持这些格式，预期可按同一模板补回，不需要重写内核架构。
- 为什么选这条路线：生产引擎 0.2.2 用同一 pack 能正常 READY，新引擎在专家布局检查处 exit 1；CUDA 侧没有通用
  兜底，必须补格式。先做最小正确切片（主内核 + 白名单 + dequant 路由），CPU/native_mmvq/prefill 已有回退，
  性能优化留作后续切片，避免一次性铺开。
- 改动与实验：`iq_kernels.cu` 新增 IQ2_XXS/IQ2_XS/IQ2_S/IQ3_S 的 dot、Fmt、dq、分派、行字节与白名单，并把
  四类加入 GU/MMVQ 格式宏；`native_expert.cpp` 与 `dequant_bf16.cu` 同步格式白名单；`native_mmvq.cu` 去掉
  `native_q8_1_il_bytes` 误加的 2..4 列校验（verify 会按 5–8 列申请缓冲，生产版无此校验）。所有改动均可在
  服务器验证树增量编译。
- 结果：服务器 `cmake --build -j20` 全绿；新引擎加载真实 pack 成功，`/health` loaded=true、262144 上下文；
  chat 返回“正常”、tool call 返回 `get_weather({"city":"北京"})`、流式 SSE 与 256 token 长输出正常；6 路并发全部
  HTTP 200 且内容正确。`native_expert_parity --synthetic` 9 组格式组合 0 failures；真实权重 layer 0/1/8/12/18
  （覆盖 17/16/21/22）0 failures。`mmvq_il_parity` 仍报 sm_75 Q6_K 表项过期（源码表返回 0、测试期望 2），
  `iq_multi_parity` 的 3 个失败全在 Q8_0 gate/up grouped（本轮未改路径，生产 pack 无 Q8_0 gate/up 专家）。
- 判决：采纳为“功能可用”的整树替换候选；性能替换前还需补齐 Split<16/17/21/22>、CPU AVX2 专家行、
  native_mmvq 与 prefill fused 路径。预期被推翻处：收敛树的“只保留 IQ3_XXS”假设不适用于本项目生产 pack。
- 预期被推翻：见上；另发现 verify 的 5–8 列 IL 缓冲与 bytes 函数的 2..4 校验互相矛盾，属收敛基线回归，已修复。
- 生产状态与回滚：生产 `strata.service` 仍为 stopped（用户授权本时段用于验证）；8080 当前运行验证树
  （新 serve + 新引擎）。回滚：停止手动实例后 `systemctl start strata` 即回到 0.2.2 生产引擎。
- 证据：服务器 `~/verify-20261027/`（build-iq2*.log、serve-newengine/、native_expert_parity 输出）；
  构建命令沿用 `docs/SERVER_HW.md`（CUDA sm_75、`-j20`、`STRATA_GGML_DIR=/opt/ai/src/llama.cpp-3cf03257`）。
- 学到什么：MoE 模型文件名上的量化标签不等于每个专家张量的类型；替换内核前应先读 `native_experts.txt` 与
  GGUF tensor type，而不是按文件名推断。
- 下一步与理由：先补性能路径并做 A/B 吞吐对照，再决定是否把生产 systemd 指向新树；A-A 吞吐与
  preflight/four-questions 仍未运行，继续保留为未验证项。

## I-20261027-59 · 合入上游收敛提交并通过服务器构建门与全量套件

- 目标 / 指标：把 origin 的 sm_75 CLI+server 收敛提交 `331f30bf`（约 -86 万行）与本地 A1a 提交 `ee170ab3` 合并落盘，
  并在服务器（2× RTX 2080 Ti, sm_75）通过构建门与全部可跑套件，补上上游约 50 个加固提交只做主机侧 `-fsyntax-only`
  而缺失的 GPU/serve 回归。全程不重启生产 `strata.service`。
- 假设与预期：收敛提交自称只保留 CUDA/sm_75 路径，默认行为不变；预期合并后服务器构建与套件应直接全绿。
- 为什么选这条路线：收敛提交与本地 A1a 在 6 个 tests/terminal 测试上 modify/delete 冲突，接受删除符合"桌面产品层退役"
  的既定范围；验证放在服务器是因为本机缺 jinja2/textual 且无 CUDA，服务器是唯一权威验证位。
- 改动与实验：合并提交 `a9bc18e7` 接受 6 处测试删除；构建两轮失败暴露 5 处收敛遗留（均为主机侧语法检查测不到的
  CUDA 专有代码），由 `4e327a08` 修复：`native_mmvq.cu` 恢复 `il_q3k_on`（默认开，`STRATA_MMVQ_IL_Q3K=0` 关）、
  `moe_fused_iq.cu` 补 include、`iq_kernels.cu` 修块边界与 `dq_q8_0` 模板头、`qsa_prompt_attn.cu` 补 namespace 闭括号；
  `a7c62825` 把 `serve/test_ready_timeout.py:133` 断言改为 `eng.log is None or eng.log.closed` 以匹配 `4ded23b4`
  的句柄释放语义。验证在 `~/verify-20261027` 独立目录 + venv 内做，不碰 /opt/ai 部署树。
- 结果：第三轮构建全绿，`build/strata --version` = `strata 0.1.51`。tests/terminal 245 通过 / 1 跳过 / 185 子测试；
  serve 976 通过 / 16 跳过 / 10789 子测试；tools/server/tests 78 通过 / 1 xfailed（`test_mp_prefix` 首轮失败系生产
  服务并发所致，空闲复跑通过）；strata_control 16 通过。生产服务全程 active、/health 200，引擎 hash 前缀
  `b46fc8895717f59a` 与 A0 锚点一致。预期被推翻处：收敛提交并非开箱即编译，CUDA 专有代码需要 GPU 构建门才算验证。
- 判决：采纳。合并与修复进入 HEAD `a7c62825`（领先 origin 5 个提交，未推送）。
- 预期被推翻：见结果条；另确认 `strata update`、`bench_facts.py`、apps/packaging/metal/sycl 树均已不存在，
  A1–A4/A6 实际由上游收敛完成，计划文档 §3 已按事实勾选。
- 生产状态与回滚：生产服务与 /opt/ai 部署树未动；回滚即放弃本地 5 个提交回到 `331f30bf`。
- 学到什么：主机侧 `-fsyntax-only` 对 `.cu` 专有代码几乎无检出力，收敛类大提交必须过真实 GPU 构建门；venv 里
  追加 gguf-py 的 PYTHONPATH 会遮蔽仓库 `tests` 包造成假失败。
- 证据：服务器 `~/verify-20261027/`（build.log、t*/t2-*/t3-* 测试日志、pip*.log）；打包用 `git archive` HEAD，
  构建命令按 `docs/SERVER_HW.md`（CUDA sm_75、`-j20`、`STRATA_GGML_DIR=/opt/ai/src/llama.cpp-3cf03257`）。
- 下一步与理由：按计划 §6 开 A5（ISA_FLOOR/PORTABLE 构建层与 sm<80 回退）；A-A 吞吐对照、`preflight.py --offline`、
  `four-questions.py`、线程失败注入仍未跑，已在 `4e327a08` 提交正文声明为未验证项。

## I-20261027-58 · spec adaptive helper 线程失败跳过本轮

- 目标 / 指标：batch/serial/pipelined spec 路径的 adaptive tier helper 被系统拒绝时，生成与 commit 必须继续；不能因一个
  可选的专家驻留调整异常中止请求。
- 假设与预期：四处 `std::thread` 直接构造在 decode 热路径内。`adapt()` 只更新驻留表和排队复制，不决定当前窗口数值；
  构造异常会越过原有 join/`adapt_ok` 检查。
- 为什么选这条路线：跳过该轮自适应比在 commit/draft 旁同步执行更保守，保持当前窗口、输出和已排队复制不动。pipeline
  维护一次性的 `pl_adapt_unavailable`，不在后续 round 重复尝试；其余路径记录一次并继续。
- 改动与实验：batch、pipeline、serve serial 和 generate serial 四处均捕获线程构造异常；失败时线程保持 nonjoinable，
  `adapt_ok/done` 保持可继续状态。成功路径线程体、join、commit、draft 和 pending 应用不变。
- 结果：`generate.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim；跳过该配置原有的未使用
  变量告警）。`git diff --check` 通过。未运行真实线程配额失败、spec decode、adaptive swap 或 RTX 2080 Ti。
- 判决：采纳。可选自适应 helper 失败只减少驻留调整频率，不影响生成正确性或请求寿命。
- 生产状态与回滚：未改成功路径和 adaptive 数据；单提交可回退。
- 学到什么：热路径中的可选线程必须明确“未启动”是允许状态，相关 join 与完成标志要按 nonjoinable 分支保持自洽。
- 下一步与理由：继续审查剩余的 detach/短生命周期线程，并等待全量 serve 回归结果。

## I-20261027-57 · 并发 prompt reader 线程失败同步读取

- 目标 / 指标：`STRATA_PIPED_CONCURRENT` 的 prompt reader 线程被拒绝时，当前 prompt 仍须通过同一 `sp.run()` 完成；
  不能遗漏 `ReadEnd` 清理，也不能让 `in_prompt_read` 或 `stop_at_chunk` 保持悬挂。
- 假设与预期：reader 的直接构造位于 `in_prompt_read=true` 和 stop hook 设置之后；线程创建异常会在 `ReadEnd` 构造前展开，
  可能绕过本轮请求状态的成对复位。
- 为什么选这条路线：读取与窗口 overlap 是调度优化，而读取本身可以同步执行。抽出同一 `read_prompt` lambda，线程创建成功时
  保持并发；失败时立即在调用线程执行同一 lambda，随后现有 `ReadEnd`、drain 和错误处理照常运行。
- 改动与实验：reader 先默认构造，再在异常边界内启动；失败记录后同步调用原 lambda。job 内容、done 发布、`sp.run()`
  参数、窗口 pump 和后续提交路径不变。
- 结果：`generate.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim；跳过该配置原有的未使用
  变量告警）。`git diff --check` 通过。未运行真实线程配额失败、并发长 prompt、双卡 stage 或 CUDA sm_75。
- 判决：采纳。并发 reader 失败退化为同步读取，prompt 不丢失，状态仍成对清理。
- 生产状态与回滚：未改成功路径线程、读取或 pipeline gate；单提交可回退。
- 学到什么：在线程启动前提交的任务状态必须能被同步兜底复用，任务体应是可调用的 lambda 而不是线程构造函数里的重复逻辑。
- 下一步与理由：处理 spec adaptive helper 的直接线程构造并继续全量 serve 回归。

## I-20261027-56 · 并发读 doorbell 线程失败交还 pump

- 目标 / 指标：`STRATA_PIPED_CONCURRENT` 的 prompt reader 把各 stage doorbell 交给独立线程服务；该线程被拒绝时，
  当前读取必须恢复 `pump()` 内的普通服务，不能留下 `conc_serving=true` 而无服务者。
- 假设与预期：`Doorbells` 先对所有 verifier 设置 `external_serve=true`，再直接构造线程。构造异常会让请求展开；即使
  RAII 析构最终复位，当前读取也会丢失并发服务。
- 为什么选这条路线：doorbell 外部服务是延迟优化；线程不可用时调用现有的 `bells.off()`，恢复各 verifier 和
  `conc_serving` 的普通路径，读取、窗口和输出保持原样。
- 改动与实验：线程构造放在异常边界内；失败记录后立即 `off()`，不进行 join，也不设置等待标志。成功路径的服务循环、
  wake/等待和每 2 ms 轮询不变。
- 结果：`generate.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim；跳过该配置原有的未使用
  变量告警）。`git diff --check` 通过。未运行真实线程配额失败、并发 prompt read、双卡 pump 或 CUDA sm_75。
- 判决：采纳。可选 doorbell 并发失败退化为普通 pump 服务，不遗失窗口 hand-off。
- 生产状态与回滚：未改成功路径线程和窗口调度；单提交可回退。
- 学到什么：设置全局服务所有权后启动服务者，必须把“服务者已启动”纳入状态，失败时回滚所有权。
- 下一步与理由：继续检查 prompt reader 和 spec-adaptive helper 的直接线程构造，再做下一轮综合测试。

## I-20261027-55 · checkpoint stage 线程失败串行保存

- 目标 / 指标：`STRATA_CKPT_THREADS` 启动多 stage checkpoint 线程时，任一线程构造失败都必须由请求线程补齐全部 stage；
  已启动的等待线程不能遗留为 joinable 局部状态，请求也不能因可选并行保存而中止。
- 假设与预期：`StageThreads::run()` 在发布 round 前逐个 `emplace_back`。部分成功后再失败会抛异常，既没有执行任何
  stage job，也没有阻断后续线程继续等待；`checkpoint_at` 没有捕获这一构造异常。
- 为什么选这条路线：每个 stage 的 checkpoint part 已设计为可串行保存，job 自身捕获并记录异常。线程失败时直接按 stage
  顺序在调用线程执行全部 job，数据、同步和写入同一 checkpoint 的约束不变。
- 改动与实验：启动前 reserve；构造失败记录一次并永久标记 `threading_failed_`；当前及后续 round 都走串行。已有部分 worker
  保持等待并由析构统一 stop/join；成功路径 round 发布和并行 job 不变。
- 结果：`generate.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA 调用由 shim 模拟；跳过该配置原有
  的未使用变量告警）。`git diff --check` 通过。没有真实线程配额失败、checkpoint、双卡 layer split 或长上下文验证。
- 判决：采纳。可选 checkpoint 并行失败只降低保存速度，不丢 stage、不泄漏等待线程、不结束请求。
- 生产状态与回滚：未改成功路径线程/job；单提交可回退。
- 学到什么：线程组发布状态必须晚于整组启动，启动失败后调用线程应完成整组副作用，而不是只完成一部分。
- 下一步与理由：进行本轮 Python 与 CPU 综合回归，之后只处理回归暴露的问题。

## I-20261027-54 · adaptive copy worker 失败退化请求线程

- 目标 / 指标：可选 `--adapt-async` 的复制 worker 被系统拒绝时，请求仍应完成 adaptive refill；不能因线程构造异常退出
  serve，也不能向日志虚报异步执行。
- 假设与预期：`JobThread` 构造时直接启动线程，`make_unique<JobThread>` 的异常会穿过主循环。该 worker 只承担专家换入
  复制，调用线程可以同步执行同一 job。
- 为什么选这条路线：记录失败并令 `post()` 在请求线程同步执行 job，`idle()` 随后为真。调用方现有状态机无需改写，
  复制内容、顺序和 fence 保持；只失去后台重叠。
- 改动与实验：线程创建失败后保存 `threaded_=false`，跳过无关的 quit/join；`post()` 同步运行 job 并恢复 idle。启动日志和
  batch 统计区分 async 与 request-thread fallback。成功路径仍使用原后台线程。
- 结果：`generate.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA 调用由同步 shim 模拟；跳过该配置
  原有的未使用变量告警）。`git diff --check` 通过。没有真实线程配额失败、serve 请求、RTX 2080 Ti 或双卡 adaptive swap
  验证。
- 判决：采纳。可选异步复制失败只降低重叠，不让请求丢失 adaptive refill 或终止进程。
- 生产状态与回滚：未改成功路径线程、复制 job、事件或状态机；单提交可回退。
- 学到什么：线程对象需要显式区分“已启动”和“可用”，否则构造失败后所有 `post/idle` 调用方都会建立在错误前提上。
- 下一步与理由：继续审查 checkpoint stage 线程组，再开始本轮综合 Python/CPU 回归。

## I-20261027-53 · graph launch 线程失败返回可清理错误

- 目标 / 指标：异步 verify graph launch 的 helper 线程创建失败时，constructor 不能把异常抛出所有权边界，也不能让
  `pending` 永久为真；含 CPU doorbell 的 graph 不能冒险改走同步 launch。
- 假设与预期：`HostGraphLaunch` 在线程模式先设置 `pending=true` 再构造 `std::thread`。构造失败时对象尚未完成，
  异常会穿过 `verify.cpp`，且没有受控 launch error。
- 为什么选这条路线：此 graph 可能在 `cudaGraphLaunch` 中等待调用线程敲 doorbell，constructor 返回前无法提供该服务；
  同步兜底会死锁。正确策略是 fail-closed：清除 `pending`，返回 `cudaErrorLaunchFailure`，让现有状态机报错并析构清理。
- 改动与实验：捕获线程构造异常并记录日志；增加 `STRATA_TEST_GRAPH_LAUNCH_THREAD_FAIL=1`，扩展 CUDA 测试的错误臂。
  主机 shim 构造/运行测试为 `host_graph_launch_thread_failure: PASS`，`verify.cpp` 通过 C++20 `-Wall -Wextra -Werror`
  主机侧语法检查。
- 结果：`git diff --check` 通过。未运行真实 CUDA graph、doorbell、RTX 2080 Ti 或双卡 layer split；本机没有 `nvcc`，
  `host_graph_launch_test.cu` 的新增错误臂未执行。
- 判决：采纳。无法启动 launch helper 时请求可诊断地失败，不泄漏异常、不挂起等待、不伪造成功。
- 生产状态与回滚：未改成功路径线程、graph 或 doorbell；单提交可回退。目标机必须补跑真实 graph 错误测试。
- 学到什么：对可能等待调用线程的异步 API，失败兜底不能只追求“仍然启动”，还要避免构造期间的同步死锁。
- 下一步与理由：处理可选 adaptive copy worker 的同形启动失败，再进入综合回归。

## I-20261027-52 · router look-ahead 线程失败关闭预取

- 目标 / 指标：`RouterLookahead` 的辅助线程被系统拒绝时，模型初始化必须继续，路由感知预取退回关闭；不能因一个可选
  prefetch 线程异常逃出初始化并吞掉错误。
- 假设与预期：`start()` 直接把线程构造函数放在返回 true 之前。`std::system_error` 会越过 `generate.cpp` 的“启动失败
  则打印预取关闭”分支，使主流程提前展开；该线程只预测下一层专家并预热页面，不参与数值正确性。
- 为什么选这条路线：捕获构造异常、填写现有 `err` 并返回 false，让调用方沿用既有关闭预取路径。成功路径线程函数、预测
  数据和调度不变；失败路径明确、可诊断，也不需要冒险同步预测整个请求。
- 改动与实验：增加确定性故障注入 `STRATA_TEST_LOOKAHEAD_THREAD_FAIL=1`，补充 `file_expert_source_test` 断言 `start()`
  返回 false 且错误说明线程未启动。标准构建与 ASan/UBSan 构建均为 `file_expert_source_test: PASS`。
- 结果：`expert_source.cpp` 与测试文件通过 C++20 主机侧 `-Wall -Wextra -Werror` 语法检查；`git diff --check` 通过。
  未在真实线程配额耗尽、RTX 2080 Ti、CUDA sm_75 或真实 Qwen pack 上运行。
- 判决：采纳。可选 look-ahead 失败只关闭预取，不终止模型启动。
- 生产状态与回滚：未改预测、warm/madvise、Foresight 或成功路径线程；单提交可回退。
- 学到什么：可选后台功能必须在启动 API 内捕获线程创建错误；让异常穿过主初始化会把“降级”变成“启动失败”。
- 下一步与理由：继续检查其他直接线程构造点，再转入 Python 与 CPU 综合回归。

## I-20261027-51 · verify 下一窗口线程失败回退同步预启动

- 目标 / 指标：layer-split verify 链启动下一 stage 的窗口 helper 时，如果系统拒绝新线程，当前窗口必须继续按原协议推进，
  下一窗口由调用线程同步 `prelaunch`；不能因构造异常跳过 hand-off，也不能留下无人处理的 GPU wait。
- 假设与预期：`nl.th = std::thread(...)` 直接构造；失败会从 `run()` 抛出，现有 `NextLaunch` 析构只能释放 GPU wait，
  不会执行下一窗口的预启动，因此一次临时线程配额错误会中止整条验证链。
- 为什么选这条路线：helper 只把下一窗口的 staging/launch 提前与当前窗口重叠；同步执行 `prelaunch` 保持相同 GPU 顺序、
  flag hand-off、错误传播和最终 `join`/`run` 协议，只牺牲 overlap。
- 改动与实验：捕获线程构造异常并记录一次降级信息，随后由调用线程执行同一 `prelaunch`，把结果写入现有 `nl.ok/err`。
  正常路径仍启动 helper，线程函数、窗口顺序、graph 和 GPU 同步都不变。
- 结果：`verify.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA 调用由同步 shim 模拟；
  DFlash2 未启用，跳过该配置原有的未使用变量告警）。`git diff --check` 通过。没有真实线程创建失败注入、CUDA graph、
  RTX 2080 Ti、双卡 layer split 或长上下文验证。
- 判决：采纳。辅助 overlap 失败退化为同步预启动，核心验证协议保持可用。
- 生产状态与回滚：未改成功路径线程和 graph；单提交可回退。目标机仍需验证真实线程配额耗尽与 graph wait 释放。
- 学到什么：异步辅助启动失败时，如果后续状态机依赖其副作用，应同步补做副作用，而不是只捕获并继续。
- 下一步与理由：继续审查 n-gram 与预取线程组，随后集中运行 CPU/Python 回归。

## I-20261027-50 · sm_75 prefill 恢复缺失的 gfx11 回退符号

- 目标 / 指标：专用 sm_75 CUDA 树必须能编译 `prefill.cpp`。收敛提交删除 gfx11 WMMA 源和头文件后，残留的两个调用不能
  再造成未声明符号，同时默认路径不能悄悄启用 gfx11 专用算法。
- 假设与预期：`strata_pf_padded_gemm_available()` 与 `strata_pf_hcdown_bf16()` 只由已删除的 `wmma_gemm.h` 声明；当前树
  没有 `nvcc` 时，主机编译器在 `prefill.cpp` 的 430、2978 行左右直接报未声明。
- 为什么选这条路线：目标发行版只支持 sm_75，gfx11 WMMA 产品不属于发行边界。恢复与旧 CUDA 构建相同的 `false` 回退，
  比删除整个调用方更小，也保留 `STRATA_PF_PAD` 的安全拒绝和 `STRATA_PF_HCDOWN` 的显式错误。
- 改动与实验：在非 HIP 构建中提供 `false` 内联回退；`STRATA_PF_PAD` 不填充激活行，`STRATA_PF_HCDOWN` 在请求不存在
  的产品时仍旧失败并给出原有错误。默认环境和成功请求路径不变。
- 结果：`prefill.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA 调用由同步 shim 模拟；DFlash2 未启用，
  跳过该配置原有的未使用变量告警）。`git diff --check` 通过。未运行 `nvcc`、CUDA sm_75、RTX 2080 Ti 或真实 prefill；
  没有验证 gfx11，因为该后端已不在发行版中。
- 判决：采纳。修复当前树中可复现的编译阻断，不扩大发行边界。
- 生产状态与回滚：默认路径、字节、模型装载和 prefill 分区不变；单提交可回退。目标机仍需用 CUDA toolkit 完成真实编译。
- 学到什么：删除后端源文件时必须同时处理宿主侧回退声明；仅删除 CMake 目标和实现不足以保持编译闭包。
- 下一步与理由：继续用主机侧编译和静态审查查找其他收敛遗留，再集中跑 Python 与 CPU 合成回归。

## I-20261027-49 · prefill 与 native dense 辅助线程失败保持装载可用

- 目标 / 指标：prefill stager 的复制 worker、expert issuer 以及 native dense 的 BF16→Q8_0 转换 helper 在系统拒绝新线程时，
  必须 join 已启动线程并由调用线程完成所需工作；不能留下 joinable 局部线程导致 `std::terminate()`，也不能让 issuer 失败跳过
  expert 拷贝。
- 假设与预期：三处均直接构造线程。stager/转换线程中途失败会让局部线程析构终止；issuer 失败会让 `threaded_issue` 保持为真，
  后续消费者等待一个从未启动的生产者。
- 为什么选这条路线：三者都是并行加速，调用线程能执行同一不可变输入/工作队列。成功路径的线程数、分区、ring 顺序和输出字节不变。
- 改动与实验：stager 先 reserve 并记录成功数量，部分成功继续、全失败明确返回；issuer 创建失败切回内联 `issue_until`；native
  Q8 helper 失败时先 join 已启动者，再由调用线程转换全部块。新增注释明确接管边界。
- 结果：`native_dense.cpp` 与 `prefill.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror` 检查；CUDA 调用由临时
  同步 shim 模拟，且 DFlash2 未启用、跳过了该构建配置原有的未使用变量告警。`git diff --check` 通过。未运行线程创建失败注入、
  `nvcc`、CUDA toolkit、RTX 2080 Ti、真实 prefill 或模型装载。
- 判决：采纳。辅助并发失败只降低吞吐或启动速度，不损坏请求、装载字节或进程寿命。
- 生产状态与回滚：未改成功路径的分区、stream/event、expert 顺序、量化结果或缓存；单提交可回退。当前没有真实 `nvcc`、CUDA
  toolkit、RTX 2080 Ti、真实 prefill 或 native Qwen pack，静态结论仍需目标机确认。
- 学到什么：等待者与生产者之间的“已启用”标志必须在生产者真正启动后才保持为真。
- 下一步与理由：继续审查 verify 的异步下一窗口启动，随后转入综合测试与目标机未验证风险整理。

## I-20261027-48 · NUMA toucher 线程失败回退调用线程

- 目标 / 指标：complement 的两次 NUMA slice 触碰分别启动 pinned toucher；任一线程创建失败时，另一个已启动线程必须 join，
  失败 slice 由调用线程无 pin 地触碰，不能因 joinable 局部线程析构触发进程终止。
- 假设与预期：旧代码用函数局部 `std::thread a(...), b(...)`。若第二个构造失败，第一个仍 joinable，栈展开会调用
  `std::terminate()`；源码注释已明确记录这一缺口。
- 为什么选这条路线：NUMA 触碰是装载加速与放置优化，失败仍可完成相同的页写入；正常两线程路径的 core、range 顺序、pin 行为
  和调用线程补洞逻辑不变。
- 改动与实验：拆出无 pin 的 `touch_ranges()`；两个 worker 独立启动和记录状态，先 join 已启动者，再由调用线程补齐未启动 slice。
  失败信息区分具体 slice，且不改变自动 NUMA balancing 下“已触碰 slice 不重复触碰”的约束。
- 结果：`expert_source.cpp` 以 `-Wall -Wextra -Werror` 主机侧编译通过，合成 `file_expert_source_test: PASS`；
  `git diff --check` 通过。该路径需要 NUMA 拓扑与 page
  placement，当前环境未做实机 NUMA 测试，结论属于控制流静态确认。
- 判决：采纳。线程配额或资源错误只让对应 slice 改由调用线程完成，不再终止模型装载进程。
- 生产状态与回滚：未改成功路径的 slice 划分、CPU pin、madvise/mlock 或页字节；单提交可回退。未验证真实双路 NUMA、RTX 2080 Ti、
  CUDA sm_75 或真实 Qwen expert arena。
- 学到什么：相邻声明的多个 `std::thread` 并不构成异常安全线程组；每个线程都必须单独记录、join 并准备调用线程接管。
- 下一步与理由：继续处理 prefill/native 路径的裸局部线程组，并在结束前重跑 CPU 合成与 Python 综合套件。

## I-20261027-47 · 托管启动回滚清理子进程 ready 文件

- 目标 / 指标：托管服务器进程启动后，如果所有权记录或 owner marker 发布失败，abort 路径必须 kill/reap 子进程、关闭 owner pipe，
  并删除子进程已经发布的 ready 文件；失败实例不能留下可被客户端误认的持久化端点。
- 假设与预期：旧 `_launch()` 只在记录发布成功后才设置 `self.ready_path`。子进程可能在记录写入窗口内创建 ready 文件；随后
  `_abort_launch()` 虽结束进程，却因 `self.ready_path is None` 永远不删除该文件。
- 为什么选这条路线：这是托管启动的单一事务边界，现有 fake child 测试可直接让 Popen 写出 ready 并令记录写入失败；正常成功
  路径的 ready 路径、owner marker、attestation 和状态字段不变。
- 改动与实验：在 Popen 前登记本次 ready 路径，使后续所有失败都有清理 owner；`_abort_launch()` 在关闭 pipe 后显式清除 ready。
  扩展记录失败测试，断言 child 被 reap、pipe/ready 状态清空且 runtime 目录无 `*.ready.json`。
- 结果：`tests/terminal/test_runtime.py` 为 `51 passed / 11 subtests`；相关文件 `py_compile` 与 `git diff --check` 通过。
- 判决：采纳。所有权发布失败不会留下子进程或已发布 ready 文件。
- 生产状态与回滚：未改成功启动、ready 内容、server 参数、owner marker 或 probe；单提交可回退。未在真实 server/Python 启停竞争、
  RTX 2080 Ti 或 CUDA sm_75 上运行。
- 学到什么：子进程能先于父进程所有权发布创建资源；父进程必须在启动前登记这些资源的清理路径，而不是成功后再登记。
- 下一步与理由：继续审查托管 runtime finally 的独立清理，并在结束前跑完整 terminal 与服务端回归。

## I-20261027-46 · owner-loss guard 线程失败关闭继承描述符

- 目标 / 指标：托管服务器启动 owner-loss guard 时，如果系统拒绝新线程，必须关闭传入的继承 pipe 描述符，并以正常的启动
  拒绝错误返回；不能留下无所有者描述符或让 `RuntimeError` 变成未处理 traceback。
- 假设与预期：`owner_guard.start(fd)` 直接调用 `Thread.start()`。失败时 main 只捕获 `OSError/ValueError`，因此既不关 fd，
  也不会走统一的参数错误路径。
- 为什么选这条路线：guard 是托管服务器父进程消失后的安全边界，不能降级缺失；正确行为是 fail-closed。pid/pgrp 和 FIFO 检查保留，
  只增加线程启动失败的显式清理与错误归一化。
- 改动与实验：任何线程启动异常都先关闭 fd；`RuntimeError` 转成带原因的 `ValueError`，其他异常原样抛出。用真实 pipe 和 mock
  pid/pgrp、线程启动失败断言 fd 已关闭且错误可用于启动拒绝。
- 结果：`serve/test_lifeline.py` 为 `8 passed`；相关文件 `py_compile` 与 `git diff --check` 通过。
- 判决：采纳。guard 无法启动时服务器明确拒绝启动，并释放继承描述符。
- 生产状态与回滚：未改 guard 正常读取、SIGTERM/SIGKILL 升级、grace 或进程组要求；单提交可回退。未在真实线程配额耗尽或
  SIGKILL 托管主机上运行。
- 学到什么：核心安全监视器失败可以 fail-closed，但失败路径仍必须释放调用方刚刚移交的资源。
- 下一步与理由：继续审查构造函数和启动事务中的描述符/临时目录所有者，再做服务生命周期综合回归。

## I-20261027-45 · 视觉编码器构造失败清理临时工作目录

- 目标 / 指标：`Vision.__init__` 创建私有 scratch 目录后，只要配置转换或 encoder 启动失败，构造过程必须删除该目录和其中
  已生成的缓存/输入文件；失败对象不会返回给调用方，不能依赖之后的 `shutdown()`。
- 假设与预期：旧实现先 `mkdtemp`，再处理 elastic 数值并调用 `_start()`。错误配置的 `int/float` 转换或 encoder 启动异常会直接
  离开构造函数，私有目录没有所有者，服务器反复失败启动会留下空目录或半成品。
- 为什么选这条路线：目录生命周期完全由 `Vision` 所有，且正常启动、unload 保留缓存、显式 shutdown 删除目录的规则不变；可用
  mock `_start` 失败和一个确定路径快速覆盖。
- 改动与实验：把构造函数中目录创建后的初始化整体纳入异常边界，任何 `BaseException` 都以 `ignore_errors` 删除本次 scratch
  目录后原样抛出。新增构造失败后目录不存在的断言。
- 结果：`VisionShutdown` 为 `6 passed / 2 subtests`；相关文件 `py_compile` 与 `git diff --check` 通过。
- 判决：采纳。未发布对象的临时目录在构造失败时立即回收。
- 生产状态与回滚：未改成功构造、缓存复用、unload、shutdown 或 encoder 参数；单提交可回退。未运行真实视觉模型或 CUDA 编码器。
- 学到什么：构造函数获取的资源不能被构造失败的对象拥有；清理责任必须落在构造边界内。
- 下一步与理由：继续审查其余临时目录和进程所有者在构造/启动失败时的回收，然后做服务端综合回归。

## I-20261027-44 · expert 区间加载线程失败回退调用线程

- 目标 / 指标：`load_experts_ranges()` 启动额外 reader 失败时，调用线程必须继续消费共享 layer 队列，join 已启动线程，读取全部
  字节且内容不变；不能让局部 `std::thread` 析构触发 `std::terminate()`。
- 假设与预期：该函数是 arena expert 和 GGUF expert 装载共用入口。旧实现直接循环 `pool.emplace_back(worker)`，线程创建在中途
  失败后，已成功线程仍 joinable，栈展开时进程终止。
- 为什么选这条路线：读取器只增加吞吐，不改变字段布局；共享 atomic layer 队列允许调用线程无缝接管。使用小文件、短 chunk 和
  合成环境变量可精确注入首个 helper 启动失败，不依赖 GPU。
- 改动与实验：先 reserve，再启动 helper；捕获线程启动异常后记录诊断，由调用线程执行同一 worker，并 join 已启动线程。新增
  三区间合成文件在 4 个请求 reader、首个 helper 失败时仍完整读出预期字节的断言。
- 结果：主机侧 `file_expert_source_test` 通过；`-Wall -Wextra -Werror`、ASan/UBSan 与 `git diff --check` 通过。
- 判决：采纳。线程配额耗尽只降低装载并行度，不会因 joinable thread 析构终止进程或损坏加载结果。
- 生产状态与回滚：未改成功路径的线程数、chunk、seek、hash、pad 映射或错误文本；单提交可回退。同步 CUDA shim 只验证主机侧
  控制流，未验证真实 `nvcc`、CUDA sm_75、双 RTX 2080 Ti 数据路径或真实专家 pack。
- 学到什么：局部线程池必须先 reserve 并把启动失败纳入同一接管/join 边界；正常读写逻辑应继续复用同一个 worker。
- 下一步与理由：继续排查真实模型装载路径中剩余的裸局部线程组，再做一次 CPU 合成回归。

## I-20261027-43 · HTTP 循环线程失败时关闭已绑定端口

- 目标 / 指标：`serve()` 先构造并绑定 `ThreadingHTTPServer`，再启动 `serve_forever` 线程；线程创建失败时必须关闭监听 socket
  后传播原始错误，不能让端口保持绑定却没有服务循环。
- 假设与预期：旧代码直接调用 `Thread.start()`。线程配额耗尽时函数在 `return httpd` 前抛异常，调用方没有服务器对象可关闭，
  socket 只能等待垃圾回收或进程退出。
- 为什么选这条路线：HTTP 监听线程是服务核心，不能降级运行；正确行为是 fail-closed 且显式释放已获取资源。用 mock HTTP server
  和线程启动故障可确定性证明 `server_close()`，不改变正常监听地址或线程参数。
- 改动与实验：把循环线程启动纳入 `try`，`RuntimeError` 时调用 `server_close()` 后原样抛出。新增失败时关闭监听器一次且不返回
  半启动服务器的断言。
- 结果：`serve/test_server.py` 为 `346 passed / 502 subtests`；相关文件 `py_compile` 与 `git diff --check` 通过。
- 判决：采纳。核心监听能力失败时服务不发布，但已经绑定的端口和 socket 会被立即释放。
- 生产状态与回滚：未改成功启动、监听 backlog、handler、telemetry 或关闭顺序；单提交可回退。未在真实端口耗尽或真实线程配额
  耗尽环境运行。
- 学到什么：资源在核心依赖启动前已经获取时，核心依赖失败必须负责释放，而不能等待调用方或 GC。
- 下一步与理由：继续检查 C++ 模型加载中直接启动局部线程组、失败后可能 `std::terminate()` 的路径。

## I-20261027-42 · batch slot 释放线程失败不泄漏服务容量

- 目标 / 指标：消费者提前离开后，释放线程必须先完成 `BSTOP`/`BDONE` 所有权收敛；若系统拒绝新线程，slot 仍应最终恢复为
  可调度状态。BGEN 被引擎拒绝后用于延迟归还 slot 的 Timer 创建失败，也不能把异常抛给请求或永久占用 slot。
- 假设与预期：`_release_slot_when_done()` 先登记 `slot_releasing` 再启动线程。线程创建失败会留下 busy+releasing，而 orphan
  reaper 明确跳过 releasing，后续请求永久少一个 slot。`_park_slot()` 的 Timer 失败则会让当前请求异常，但 orphan reaper 可在
  `ORPHAN_SLOT_S` 后恢复。
- 为什么选这条路线：两处都在 batch 并发主路径，fake engine/queue 可确定复现，不依赖 GPU；正常成功路径仍由后台线程或 Timer
  完成，队列清理与缓存记账不变。
- 改动与实验：释放线程启动失败时在已经结束响应的请求线程执行同一有界 `BDONE` 等待；Timer 启动失败只记录诊断，保留无主 busy
  标记交给 orphan reaper。新增队列预置 BDONE 的同步清理断言和 Timer 失败的 reaper 恢复断言。
- 结果：`serve/test_parallel.py` 为 `24 passed`；相关文件 `py_compile` 与 `git diff --check` 通过。
- 判决：采纳。线程配额耗尽可以降低并发释放的及时性，但不能永久泄漏 slot 或把可恢复的 BGEN 拒绝变成 500。
- 生产状态与回滚：未改正常 BDONE 缓存、slot 选择、BGEN 重试或 Timer 延迟；单提交可回退。未在真实引擎长卡死、线程配额耗尽、
  CUDA sm_75 双卡并发或真实 Qwen 批处理上运行。
- 学到什么：busy/releasing 这类互斥所有权状态必须在任何线程启动异常后仍有确定的收敛路径。
- 下一步与理由：继续审查“登记状态后才启动辅助线程/Timer”的其他位置，并跑并发回归。

## I-20261027-41 · 周期性空闲任务与浏览器线程失败不中止服务

- 目标 / 指标：服务已经绑定 HTTP 端口后，空闲模型卸载、弹性视觉编码器回收或自动打开浏览器的后台线程若无法创建，服务仍应
  提供 API；失败只关闭对应的可选周期任务并给出诊断，不能留下已发布 ready 状态后异常退出。
- 假设与预期：三处直接调用 `Thread.start()`。操作系统线程配额耗尽产生的 `RuntimeError` 会从启动主流程抛出；此时 HTTP
  服务线程可能已经运行，但 main 尚未进入正常关闭循环，客户端得到的是一个没有生命周期所有者的半启动服务。
- 为什么选这条路线：三项都是定时清理或便利功能，不影响当前请求、推理或显存策略；使用现有 fake engine 和线程启动故障即可
  验证，成功路径的周期、阈值和浏览器 URL 保持字节不变。
- 改动与实验：新增统一 `start_auxiliary_thread()`，记录线程启动失败并返回空线程；空闲卸载、视觉 idle 回收和打开浏览器统一
  使用它。新增两个 idle worker 同时失败后健康端点仍返回 200，以及可选线程失败文本的断言。
- 结果：`serve/test_server.py` 为 `345 passed / 502 subtests`；相关文件 `py_compile` 与 `git diff --check` 通过。
- 判决：采纳。后台便利能力不能成为已绑定服务可用性的前置条件。
- 生产状态与回滚：未改正常空闲卸载、弹性显存回收、浏览器打开或关闭顺序；单提交可回退。未在真实线程配额耗尽、真实浏览器
  启动器或双 RTX 2080 Ti 服务上运行。
- 学到什么：HTTP 监听成功后出现的非必要线程失败，应有统一的降级与诊断入口，避免形成半启动服务。
- 下一步与理由：检查启动事务中“资源已发布、后续辅助步骤失败”的更多顺序问题，再跑服务端相关全量回归。

## I-20261027-40 · 生成探针线程失败不拖垮托管主机

- 目标 / 指标：`Runtime.Host` 启动 `strata-generation-probe` 时若进程无法再创建线程，只能暂缓生成就绪验证；控制通道、
  托管服务器和活动租约必须继续存在，后续状态请求应能重新尝试探针。
- 假设与预期：旧实现先发布 `probe_key` 后直接调用 `Thread.start()`。线程配额耗尽产生的 `RuntimeError` 不在控制循环的
  `OSError/ValueError` 处理范围内，会离开 `serve()` 并触发托管子进程停止；即使捕获，残留 `probe_key` 也会永久阻止重试。
- 为什么选这条路线：探针负责 API 身份和一次生成验证，属于辅助能力；可用 fake child/ready 文件和线程启动故障确定性覆盖，
  不影响成功路径的探针参数、超时或恢复状态。
- 改动与实验：捕获探针启动的 `RuntimeError`，清除本次 `probe_key/probe_result`，让状态保持 `verifying_generation`，下次轮询重试；
  新增线程启动失败下 `_status()` 不抛异常且可重试的断言。
- 结果：`tests/terminal/test_runtime.py` 为 `51 passed / 11 subtests`；相关文件 `py_compile` 与 `git diff --check` 通过。
- 判决：采纳。生成验证线程创建失败不再关闭控制主机或服务器，并保留后续恢复机会。
- 生产状态与回滚：未改正常探针的鉴权、HTTP 请求、超时或就绪判定；单提交可回退。未在真实操作系统线程配额耗尽或双
  RTX 2080 Ti 加载进程上运行。
- 学到什么：保存“已开始”状态必须与真正启动成功绑定；辅助线程的失败还必须有可重试状态，而不能只是吞掉异常。
- 下一步与理由：继续审查控制主机 finally 的顺序与失败后仍可恢复的进程/文件所有权。

## I-20261027-39 · sd-cli 等待 OS 错误仍终止并释放子进程

- 目标 / 指标：图像子进程的 `poll()`/`wait()` 抛普通 `OSError` 时，也必须 kill、做一次有界 wait、join/关闭输出 reader，
  删除本次 job 目录，并返回 `ImageJobFailed`，不能把仍运行的 sd-cli 与管道留在后台。
- 假设与预期：旧代码只处理第一次 `wait(timeout=30)` 的 `TimeoutExpired`。普通 `OSError` 会跳出处理块，跳过 kill、二次 wait
  和显式 stdout 关闭；异常离开 `_run()` 后进程与 reader 的所有权无人继续处理。
- 为什么选这条路线：图像进程生命周期已有 mock 故障测试和无 GPU fake sd-cli；修改不改变正常输出、超时文本、进度解析或
  生成图片字节。
- 改动与实验：把轮询与首次 wait 纳入同一个 `try`，`OSError` 分支记录原因、kill、有界 wait；若二次 wait 仍失败则保留
  “after being killed” 语义。随后统一 join reader 并关闭 stdout。新增首次等待 OSError 的 kill/wait/close/job-dir 断言。
- 结果：`serve/test_images.py` 为 `25 passed / 20 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。等待系统调用失败不再绕过图像子进程的终止和输出管道清理。
- 生产状态与回滚：未改成功作业、取消、超时、输出解析或 GPU 策略；单提交可回退。未运行真实 sd-cli、Qwen Image 或 CUDA GPU。
- 学到什么：等待错误是清理的开始，不是清理的终点；所有 wait 失败分支都必须汇入同一个进程与 pipe 释放边界。
- 下一步与理由：继续检查长生命周期 HTTP/进程封装中的 finally 边界，并回到 C++ 主加载路径做一次静态与合成回归。

## I-20261027-38 · telemetry 采样线程失败不阻止服务启动

- 目标 / 指标：硬件 telemetry 的后台采样线程无法创建时，服务仍应启动；保留静态 GPU/CPU 信息与按需 `sample()`，不得留下
  半启动对象，`close()` 仍可安全重复调用。
- 假设与预期：`Telemetry.__init__()` 直接启动 daemon 线程，线程配额限制或运行时拒绝会把异常抛到 `Service.start_telemetry()`，
  仅影响监控图表却中止整个服务器初始化。
- 为什么选这条路线：采样器是纯辅助设施，可用 mock GPU reader 和线程启动故障完全验证；正常采样周期与 `/metrics` 快照不变。
- 改动与实验：线程启动失败后记录告警并把 `_thread` 置空，保留静态信息和按需采样；新增双卡静态/按需读数断言、空历史快照
  和失败对象安全关闭测试。
- 结果：`NvidiaTelemetry` 与 `TelemetryNoDisks` 目标测试为 `3 passed`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。采样线程只会影响周期历史，不能成为服务可用性的前置条件。
- 生产状态与回滚：未改成功启动的采样、GPU 聚合、磁盘/CPU 内存字段或 `/metrics` 格式；单提交可回退。未在真实线程配额
  耗尽或双 RTX 2080 Ti 服务器上运行。
- 学到什么：构造函数中的后台监控线程应像其他辅助 worker 一样有降级路径；按需采样接口可以继续为状态检查提供当前值。
- 下一步与理由：继续审计构造函数中启动线程的组件和可失败进程清理，优先找会让主服务不可用的辅助路径。

## I-20261027-37 · PLE issue 队列与 ticket 提交保持一致

- 目标 / 指标：`PleReader::issue()` 在 jobs 的 vector/unordered_map/deque 或 ticket 表分配失败时，必须回滚本次已追加
  的队列项；不能先发布一张调用方永远拿不到的 ticket，也不能让后续 reader 继承 pending 或半队列状态。
- 假设与预期：旧实现把 `TicketState&` 插入 `tickets` 后才逐项构建和入队 job。任一分配异常都会抛穿，但 ticket 已占用
  id；部分 job 已进入 queue，另一些没有，ticket 的 pending 永远无法归零，后续 collect/close 只能依赖整体清理。
- 为什么选这条路线：这是直接 PLE 读取和 prefill 共用的事务边界，可用 CPU 合成表与确定性注入覆盖；正常表布局、行宽、
  job 排序和提交后的读取字节不变。
- 改动与实验：先在局部 jobs 完成去重、排序，再追加到 deque；每次追加成功计数，ticket 只在队列完整提交后插入。捕获异常时
  移除本次追加的队尾并重新抛出。新增 `STRATA_TEST_PLE_QUEUE_FAIL=1`，在首个 job 后失败，断言异常发生且下一次读取正确。
- 结果：`ple_reader_test --selftest` 通过全部八种格式和 90/110 字节行宽；ASan/UBSan（泄漏检测开启、遇错停止）与
  TSan（遇错停止、记录第二把锁）均通过；`-Wall -Wextra -Werror` 和 `git diff --check` 通过。
- 判决：采纳。队列项和 ticket 在同一个提交边界内发布，分配失败恢复成 issue 前的 reader 状态。
- 生产状态与回滚：未改成功路径的 job 顺序、去重、缓存或 notify 时机；单提交可回退。未验证真实地址空间耗尽的 OOM killer、
  真实 Qwen PLE 分片或 CUDA sm_75 预填。
- 学到什么：调用方以返回 ticket 为所有权的资源必须在返回值可构造前最后提交；先发布所有权再填充数据会留下不可达状态。
- 下一步与理由：继续审查 hot path 中“先改对象状态、后执行可失败分配/队列操作”的提交顺序。

## I-20261027-36 · MCP 关闭错误响应与配置对象校验

- 目标 / 指标：HTTP MCP 关闭会话时，DELETE 的 4xx/5xx `HTTPError` 必须在归一化后关闭；配置中的 `mcp` 字段不是对象时
  必须返回清晰 `SystemExit`；诊断入口读取配置也要显式关闭文件。
- 假设与预期：`HttpTransport.close()` 用 `urlopen(...).close()` 启动 DELETE，但 4xx/5xx 在赋值前变成异常，其响应体
  进入 `except OSError` 后未关闭。`settings_from()` 对 `"mcp": "fast"` 调用 `.items()`，以未处理的 `AttributeError` 终止。
  `python -m serve.mcp config.json` 的 `json.load(open(...))` 还依赖引用计数关闭配置。
- 为什么选这条路线：三处都有无网络 mock 或无副作用的配置测试；修复不改变 MCP 请求、成功 DELETE、设置默认值或协议字段。
- 改动与实验：成功 DELETE 使用上下文管理；`HTTPError` 单独用上下文关闭；`mcp` 块先验证为对象；诊断入口改用 `with open`
  并保留原有文件/JSON 错误文本。新增 DELETE 405 关闭和字符串 `mcp` 配置拒绝两项断言。
- 结果：`serve/test_mcp.py` 为 `36 passed / 9 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。MCP 关闭的每个 HTTP 结果都有显式响应 owner，错误配置在读取字段前被拒绝，诊断入口不再依赖 GC 关文件。
- 生产状态与回滚：未改正常 initialize/tools/call、会话 DELETE 或运行配置中的合法 `mcp` 对象；单提交可回退。未访问真实
  MCP 服务、代理、TLS 中断或企业鉴权端点。
- 学到什么：HTTP 的异常也是响应；配置容器类型校验必须发生在 `.items()` 等迭代操作前。
- 下一步与理由：继续检查其余产品入口的 JSON/文件读取和 HTTP 错误分支，再做一轮高价值 C++ 装载资源审查。

## I-20261027-35 · 统一 expert source 辅助线程的失败与回收

- 目标 / 指标：`expert_source.cpp` 中共享 atomic 工作队列的批读、缓存补集构建、补集校验、GGUF 专装载、Arena 读取和
  Linux 文件层探测，都要在辅助线程创建/预留失败时由调用线程完成剩余工作，并 join 已启动线程。
- 假设与预期：这些路径各自重复“启动 N-1 个线程、调用线程也工作、逐项 join”。部分路径仍直接 `emplace_back`，失败时
  局部线程组析构终止进程；其余路径通过复制异常处理维持正确性，但失败语义容易再次漂移。
- 为什么选这条路线：这是纯代码精简与异常安全收敛，不改变工作队列、批次、线程数量或正常并发；已有 34 专家填充注入
  可直接验证共享 helper，CPU 合成测试覆盖模型加载主适配器。
- 改动与实验：新增 `run_parallel_workers(total, work, label, inject_env)`；先 reserve，再启动 helper，失败时记录并让
  调用线程执行同一工作，最后 join 已有线程。六个使用点统一迁入 helper；测试注入环境仍驱动批量填充的失败分支。
- 结果：主机侧 `file_expert_source_test`（合成 pack、CPU 库和同步 CUDA shim）`PASS`，实际输出
  `batch readers fall back to the caller (injected thread start failure)`；ASan/UBSan（泄漏检测开启、遇错停止）同样
  `PASS`；`-Wall -Wextra -Werror` 与 `git diff --check` 通过。
- 判决：采纳。六条辅助线程路径共享同一失败语义和回收边界，代码减少重复，正常 work queue 与字节路径不变。
- 生产状态与回滚：未改正常装载、补集、探测或读取策略；单提交可回退。未验证真实线程配额耗尽、真实 GGUF 双卡装载、
  RTX 2080 Ti 或 CUDA sm_75。
- 学到什么：可并行且调用线程能接管的工作，应由一个 RAII/统一 helper 管理线程表；否则每个复制点都会重新引入 terminate 或漏 join。
- 下一步与理由：继续把相同原则集中到仍有局部线程组的加载和测试工具，并审查 Python 配置文件与响应本体的生命周期。

## I-20261027-34 · 服务端辅助线程失败时保持请求与副本可用

- 目标 / 指标：MCP 工具心跳线程、客户端断开监视线程、副本并行重启/启动及后台自愈线程都是辅助设施；系统拒绝新线程时，
  工具仍要执行、正常请求仍要完成，存活副本仍要继续路由，失败的副本操作要延后而不是抛穿。
- 假设与预期：`run_with_mcp()`、`_watch_client()`、`ReplicaEngine.restart()/heal()` 和 `start_replicas()` 直接调用
  `Thread.start()`。现有实现会在工具未运行时结束流、在请求尚未开始前返回异常、让副本重启失败冒到调用方，或留下未 join 的
  Python 线程集合。
- 为什么选这条路线：这些是普通 HTTP mock 和 fake replica 可确定覆盖的线程资源边界，与双卡双副本发行配置直接相关；修复
  不改变并发成功时的时序、请求内容、超时或路由成本。
- 改动与实验：MCP 心跳线程启动失败后同步执行同一工具调用；断开监视失败时撤销本次 `active_cancels` 注册并继续请求；
  副本并发启动/重启增加“已启动先 join、剩余工作串行完成”的后备；自愈线程失败时清除 `healing` 标记并等待冷却后重试；
  `start_replicas` 复用同一后备。新增工具执行、普通请求、并行重启和自愈路由四项故障注入测试。
- 结果：目标测试 `23 passed`；完整 `serve/test_server.py serve/test_mcp.py serve/test_replicas.py` 为
  `398 passed / 510 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。辅助线程创建失败只降低并发、保活或即时自愈能力，不改变工具是否执行、请求是否完成或存活副本是否服务。
- 生产状态与回滚：未改正常多线程启动、MCP 轮次、断开取消或副本成本模型；单提交可回退。未在真实线程配额耗尽、双
  RTX 2080 Ti 进程启动或真实客户端半关闭连接上运行。
- 学到什么：辅助线程的失败策略必须与它承担的功能相匹配；若主功能可在调用线程完成，就应降级执行而不是返回错误。
- 下一步与理由：继续检查普通 Python 进程/文件资源与 C++ 局部线程组，优先处理会在正常请求或模型加载中漏资源的路径。

## I-20261027-33 · 批量 expert 填充线程启动失败回退到调用线程

- 目标 / 指标：`FileExpertSource::fill_many()` 的 O_DIRECT 批读和映射读取在 worker 创建或预留失败时，必须由调用线程完成
  全部剩余 blob，join 已启动 worker，发布每个 claimed stage，且不能让局部 `std::thread` 析构调用 `std::terminate()`。
- 假设与预期：两个分支直接对局部 `std::vector<std::thread>` 执行 `emplace_back`。线程资源耗尽、内存不足或任一 worker
  启动失败会抛出，此时已启动线程仍 joinable；局部容器随栈展开析构，C++ 标准直接终止进程，模型加载也随之退出。
- 为什么选这条路线：批量 expert 填充是每层和 profile load 的主路径，可在 CPU 合成 pack 上精确触发多线程分支；成功路径的
  批次、字节与并发数无需改变。
- 改动与实验：两条分支先预留线程表，再逐项启动；失败记录后由调用线程执行同一个 atomic work queue，最后 join 已启动
  worker。新增 `STRATA_TEST_FILL_THREAD_FAIL` 注入和 34 个合成专家用例，检查跨批次边界与末尾专家字节。
- 结果：主机侧 `file_expert_source_test`（合成 pack、CPU 库和同步 CUDA shim）`PASS`，实际输出
  `batch readers fall back to the caller (injected thread start failure)`；ASan/UBSan（泄漏检测开启、遇错停止）同样
  `PASS`；`-Wall -Wextra -Werror` 与 `git diff --check` 通过。
- 判决：采纳。辅助 worker 只负责并行度；任一线程不能创建时，调用线程仍完成同一 work queue，stage 所有权正常发布。
- 生产状态与回滚：未改正常批次大小、O_DIRECT 对齐或映射读取；单提交可回退。未验证真实进程线程配额耗尽、真实 NVMe、
  RTX 2080 Ti 或 CUDA 数据路径。
- 学到什么：局部线程组必须先有失败时 join 的 owner；`try` 包住 `emplace_back` 不足以避免 terminate，已启动线程仍需执行。
- 下一步与理由：继续排查相同的局部线程容器启动模式，优先修产品加载/推理路径，再跑相关 CPU 回归并推送。

## I-20261027-32 · PLE 直接读表范围的溢出安全校验

- 目标 / 指标：`PleReader::open()` 必须在乘法或加法前确认 `[table_offset, table_offset + n_rows * row_bytes)` 落在文件内；
  损坏的 GGUF 头不得借助 `uint64_t` 回绕变成合法范围，也不得在失败后留下打开的文件或无主 worker。
- 假设与预期：旧检查先计算 `table_offset + n_rows * row_bytes > file.size()`。当乘法或加法回绕到较小值时，非法表会被接受，
  后续行偏移同样回绕并产生错误读取或难以解释的短读。
- 为什么选这条路线：低层直接表读取是 PLE 所有格式共同经过的边界；CPU 合成表能覆盖极端头信息、全部格式、同步/worker I/O，
  不需要模型或 GPU，而且正常 offset、行宽和字节完全不变。
- 改动与实验：先用 `table_offset > file_size` 拒绝起点，再用
  `n_rows > (file_size - table_offset) / row_bytes` 比较可用行数，避免任何可能溢出的乘法/加法。新增 `UINT64_MAX` offset 与
  row count 两个拒绝用例，并断言错误信息和关闭状态。
- 结果：直接编译的 `ple_reader_test --selftest` 通过全部八种格式和 90/110 字节行宽的 Direct/Mmap 对拍；ASan/UBSan
  （泄漏检测开启、遇错停止）和 TSan（遇错停止、记录第二把锁）同样通过；`-Wall -Wextra -Werror` 与 `git diff --check` 通过。
- 判决：采纳。表范围验证在第一次算术前 fail-closed，损坏头不会进入文件缓存、slab 或 worker 资源分配。
- 生产状态与回滚：未改正常表布局或读取行为；单提交可回退。未使用真实 Qwen PLE 分片、真实 NVMe 故障或 CUDA sm_75 数据。
- 学到什么：验证“区间在文件内”时，用文件剩余长度做除法比较，比先算结束偏移更直接，也天然规避无符号回绕。
- 下一步与理由：继续审查同一读取栈的异常路径和 probe 资源清理，再做下一轮 CPU/终端回归并重试远端推送。

## I-20261027-31 · MCP 传输线程启动失败与关闭/启动竞争

- 目标 / 指标：MCP HTTP 请求线程无法创建时必须返回统一的 `McpError`；超时后取消通知线程再失败不能覆盖原始超时；
  stdio 传输关闭后或关闭与启动交错时，不得继续发布一个无人拥有的子进程。
- 假设与预期：`HttpTransport.request()` 直接调用 `Thread.start()`，线程资源耗尽时会让 `RuntimeError` 穿出工具循环；
  取消通知是尽力而为，却可能用第二个线程启动异常替换 `McpTimeout`/`McpCancelled`。stdio `start()` 原先先检查加锁外的
  `proc`，`close()` 查不到进程就返回，随后启动仍可发布子进程。
- 为什么选这条路线：三个分支都可用线程启动故障和关闭后启动精确复现，不需要 MCP SDK、网络或模型；修复不改变协议消息、
  超时值或工具结果文本。
- 改动与实验：HTTP 请求线程启动失败归一化为 `McpError`；取消通知线程启动失败被隔离，原始超时/停止结果继续抛出。
  stdio 增加生命周期锁，进程发布与关闭读取位于同一所有权切换；`ended` 在构造时定义，关闭后的启动在 `popen` 前失败。
  新增 HTTP 首次/第二次线程启动失败和关闭后 stdio 启动三项测试。
- 结果：`serve/test_mcp.py` 为 `34 passed / 8 subtests`；服务端相关套件
  `serve/test_server.py serve/test_images.py serve/test_lifeline.py serve/test_mcp.py` 为 `406 passed / 530 subtests`；
  `compileall` 与 `git diff --check` 通过。
- 判决：采纳。MCP 传输在本地线程资源耗尽和关闭竞争下保持明确错误与单一进程所有权，尽力通知不再改变主调用判决。
- 生产状态与回滚：未改服务配置或正常协议路径；单提交可回退。未运行真实 MCP 市场包、企业网络代理或操作系统线程配额耗尽。
- 学到什么：附属线程启动也是启动事务的一部分；但附属通知失败不能覆盖主操作已经形成的超时/取消结果。
- 下一步与理由：继续检查 HTTP 请求在超时后的连接回收，以及产品入口中未关闭的配置文件/响应对象。

## I-20261027-30 · strata_bench 的 HTTPError 正文读取后关闭

- 目标 / 指标：流式基准请求收到 4xx/5xx 时，保留 `HTTP <code>: <body>` 错误文本，并在返回结果前关闭
  `HTTPError` 的响应和连接。
- 假设与预期：`chat()` 读取 `HTTPError` 正文后只生成字符串，未显式关闭；连续采样失败端点会把 socket/TLS 的释放
  推迟到 GC，和此前 Terminal telemetry、MCP HTTP 修正是同类问题。
- 为什么选这条路线：基准工具有现成 HTTP fake 和无 GPU 测试；修改只影响错误归一化，不改变成功流解析、计时或报告。
- 改动与实验：`HTTPError` 分支在 `try/finally` 中读取最多 200 字节并 `close()`。新增 mock 503 响应，断言错误文本
  仍含 `HTTP 503`，且 close 被调用。
- 结果：`python -m pytest -q tools/test_strata_bench.py` 为 `18 passed`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。基准请求的成功体由上下文关闭，失败体也在归一化后显式关闭。
- 生产状态与回滚：未改服务；仅改维护型基准工具的错误路径，单提交可回退。未访问真实慢速/中断的 benchmark 端点。
- 学到什么：错误正文也属于响应资源；转成展示字符串和释放连接应放在同一 finally 边界。
- 下一步与理由：继续检查其他维护工具中 `urlopen(...).read()`、未关闭 key/output 文件，以及图片/模型下载临时文件；
  保持产品入口优先，再回到 C++ RAM promotion 与 prefill 审查。

## I-20261027-29 · quant_formats 的 HTTP 响应显式关闭

- 目标 / 指标：Hugging Face 模型树 JSON 和每个 GGUF range 请求都要在读取完成、空响应或解析异常时关闭
  `HTTPResponse`/连接；工具失败后不能留下 socket 或 TLS 资源。
- 假设与预期：`RangeFile._fill()` 对 `urlopen(...).read()` 的临时响应没有显式 owner，`hf_headers()` 直接
  `json.load(urlopen(...))`；完整读取通常会释放连接，但异常、短读和策略差异不应依赖垃圾回收。
- 为什么选这条路线：它是只读检查工具，HTTP 行为可完全 mock；修复不改变 range 大小、重试语义、输出 JSON 或
  GGUF 解析。
- 改动与实验：两处改为 `with urllib.request.urlopen(...) as response`，分别读取 bytes 和 JSON。新增 mock
  响应测试，断言连续两次 range 请求都已关闭，并验证 tree 响应在 header 构造前关闭。
- 结果：`python -m pytest -q tools/test_quant_formats.py` 为 `2 passed`；`compileall` 与 `git diff --check`
  通过。没有访问真实 Hugging Face 网络。
- 判决：采纳。所有该工具的 HTTP 响应都有明确的上下文所有权。
- 生产状态与回滚：未改服务；仅维护工具的网络读取方式，单提交可回退。未验证真实 TLS reset、重定向或大型模型树。
- 学到什么：HTTP 响应不是 JSON/bytes 本身；即使 `.read()` 成功返回，也应由请求点显式结束传输。
- 下一步与理由：检查同类基准/下载工具中对 `HTTPError` 和本地文件句柄的隐式关闭，并继续补充资源回归。

## I-20261027-28 · 非透传图片规范化的 Pillow 图像释放

- 目标 / 指标：`Vision.normalize()` 处理 EXIF 旋转、模式转换和透明图像扁平化时，源图、旋转副本、拆分 band、
  转换结果和临时背景图都必须在成功或异常路径关闭。
- 假设与预期：旧实现用 `with Image.open(...)` 只关闭源图，随后 `ImageOps.exif_transpose()` 返回的新图和
  `convert()` 返回的新图都没有显式关闭；透明分支的 `im.split()` band 也未关闭。连续直接图片请求会依赖 Pillow
  对象回收，极端情况下保留临时缓冲或文件资源。
- 为什么选这条路线：图片规范化是纯 Pillow 路径，可用无 GPU 的合成 JPEG/TIFF 精确覆盖；修复不改变输出格式、
  像素方向或透明区白色背景规则。
- 改动与实验：把源图、旋转和转换放进同一所有权范围；转换替换前关闭旧图，band 使用后逐个关闭，背景图和输出
  `BytesIO` 使用上下文，最终副本在 `finally` 关闭。新增 Pillow `close` 调用计数断言，以及透明 TIFF 转 RGB PNG
  的方向/尺寸/白底测试；测试构造的图片也显式关闭。
- 结果：`serve/test_exif_orientation.py serve/test_images.py` 为 `29 passed / 20 subtests`；`compileall` 与
  `git diff --check` 通过。首次测试确认旧计数阈值设置过高，调整为只验证旋转副本的显式关闭，普通源图由既有上下文
  负责。
- 判决：采纳。每个 Pillow 图像/band 在所有规范化分支都有明确 close；失败仍归一化为原有 `ValueError`。
- 生产状态与回滚：未改生产；JPEG/PNG/BMP/GIF 透传、EXIF 方向、透明白底和错误文本行为由测试保持，单提交可回退。
  未运行真实视觉编码器或 GPU。
- 学到什么：`with Image.open()` 只覆盖源图，图像变换返回的新对象仍需要独立所有权；颜色 band 也是图像对象。
- 下一步与理由：继续检查 HTTP 下载和图片缓存的响应/临时文件收尾，再做一次完整服务端回归。

## I-20261027-27 · 驻留预算、NUMA 与 stage lend 计划的溢出边界

- 目标 / 指标：pin 判断不得因 `bytes + reserve` / `step + reserve` 溢出而错误放行；NUMA 估算不得因节点数乘法
  溢出；stage lend 计划不得在负几何、乘法溢出或 `bytes > cap` 时越界或下溢。
- 假设与预期：旧 `pin_depends_on_reclaim()`、`pin_step_fits()` 直接相加，极端值会绕回小数；
  `numa_pin_free()` 的 `smallest * node_count` 也会溢出。`append_stage_lend_regions()` 在参数校验前计算
  `n_layers * n_expert`，且用 `cap_bytes - bytes` 比较，调用方状态已超过 cap 时会产生无符号下溢。
- 为什么选这条路线：这些都是纯主机算术，可在现有合成测试中精确覆盖；正常 2080 Ti 几何和真实内存数值不受影响，
  修复可防止损坏的 profile/容器读数或错误调用把安全检查变成放行。
- 改动与实验：pin 判断改用先比较 bytes/step、再比较剩余量的无溢出形式；NUMA 乘法在超过全局可用量时直接由全局量
  封顶；lend 计划先验证正几何和 `size_t` 乘法，再分配索引，并在 `bytes >= cap` 时保留整段文件回退。新增溢出 reserve、
  NUMA 乘法、lend 几何和已超 cap 四种断言。
- 结果：主机侧 `file_expert_source_test`（合成 pack、CPU 库和同步 CUDA shim）`PASS`；`-Wall -Wextra -Werror` 和
  `git diff --check` 通过；同一测试的 ASan/UBSan（泄漏检测开启、遇错停止）也通过。完整
  `serve/test_server.py serve/test_images.py serve/test_lifeline.py serve/test_mcp.py` 为
  `403 passed / 530 subtests`；`tests/terminal` 为 `199 passed / 46 skipped / 93 subtests`（跳过项均为平台/环境条件）。
- 判决：采纳。所有安全预算判断对 `uint64_t` 极值保持 fail-closed（无法证明安全时不进入预取/驻留路径）。
- 生产状态与回滚：未改生产；常规内存值、NUMA 节点选择和合法 lend 偏移逐一由既有测试保持，单提交可回退。
  未验证真实 NUMA 双路机、RTX 2080 Ti 或伪造 cgroup 的内核竞态。
- 学到什么：安全余量检查不能先做可能溢出的加法；“剩余空间”必须先确认当前用量没有超过上限。
- 下一步与理由：补充真实服务端全量回归结果，检查 RAM promotion/HTTP 下载的资源收尾，再集中重试远端的全部本地提交。

## I-20261027-26 · 后台 expert 读线程启动失败回退到调用线程

- 目标 / 指标：`prefetch_begin()` 已 claim 的 stage buffer 在任一后台读线程创建失败时仍必须被发布；不能抛穿调用方、
  遗留 joinable 线程、让 `wait_landed()` 永久等待，或在 direct read 分配异常时留下 busy buffer。
- 假设与预期：旧代码直接 `async_th_.emplace_back(work)`。若 `reserve` 或线程构造失败，函数抛出，stage buffer 的
  `busy` 标记和 `pending` 已由 `claim_todo()` 写入；调用方若只收到异常而继续使用 source，后续 blob 会等待一个没有
  worker 的 fill。
- 为什么选这条路线：它位于 cold expert 读取的异步边界，可复用已有 unbuffered 合成 pack；用明确的测试注入拒绝首个
  worker 启动，能覆盖真实线程构造失败所走的后备路径，不需要 GPU 或模型。
- 改动与实验：`prefetch_begin()` 先 reserve worker 表；任一线程启动失败时由调用线程执行同一 `work()`，随后
  `prefetch_end()` join 已完成启动的 worker，把所有已 claim blob 标记为不再 pending。direct 批量读的分配/异常改为该批
  stage 逐项失败发布。新增 `STRATA_TEST_COLD_THREAD_FAIL=1` 注入，断言回退后三个 blob 内容正确且 pending 清零。
- 结果：主机侧 `file_expert_source_test`（合成 pack、CPU 库和同步 CUDA shim）`PASS`，实际打印
  `background reads fall back to the caller (injected thread start failure)`；`-Wall -Wextra -Werror` 与
  `git diff --check` 通过；同一测试的 ASan/UBSan（泄漏检测开启、遇错停止）通过。
- 判决：采纳。后台读 worker 是加速器而不是正确性依赖；启动失败后由调用线程完成同一批读取并恢复 pending 状态。
- 生产状态与回滚：未改生产；正常线程启动与 direct batch 字节/顺序不变，单提交可回退。未验证真实 O_DIRECT
  设备错误、真实线程资源耗尽、CUDA sm_75 或吞吐。
- 学到什么：提交 pending=1 就必须同时保证有一个会把它清零的执行者；线程创建失败后，调用线程是最可靠的后备执行者。
- 下一步与理由：检查 RAM promotion worker 的同类后备路径与 `HostGraphLaunch` join 约定，再运行 Python 全量回归。

## I-20261027-25 · ForesightSwap 初始化校验与 copy 队列提交边界

- 目标 / 指标：`ForesightSwap::init()` 拒绝空几何、层/专家表乘法溢出、缺少或越界的 card 映射，并只允许一次初始化；
  filler 线程创建失败时返回错误而非抛穿；`take/note_miss` 的越界参数不能索引 `where`；copy 队列分配失败不能留下
  指向尚未入队 job 的 slot 或泄漏预算计数。
- 假设与预期：旧 init 在验证 `card_of_layer` 长度前就用 layer 索引它，并把 `layers * slots/experts` 转成 `size_t`，
  错误输入可越界或溢出。`request_locked()` 先将 `where`、slot 和预算计数改成 loading，再向 deque 入队；若入队分配失败，
  该 slot 永远等不到 filler，容量与计数也已被占用。
- 为什么选这条路线：初始化几何和队列提交都是主机侧控制流，可在没有 GPU 的路径上验证错误输入；成功路径的 slot
  状态恢复可由代码审查确认，不改变正常 copy 顺序或 device 地址。
- 改动与实验：init 在任何资源分配前校验正整数、两处 size 乘法、card 映射和重复初始化；每个 device 的字节数检查
  乘法/加法溢出；filler `std::thread` 创建异常转成 `err`。`take`、`note_miss` 和内部 request 统一校验参数。copy
  queue 入队失败时恢复旧 expert 的 `where`、slot 状态、source 和计数，并计为 busy。新增 6 类非法 init 和未初始化
  `take` 断言。
- 结果：主机侧 `file_expert_source_test`（合成 pack、CPU 库和同步 CUDA shim）`PASS`，其中
  `foresight init validation: PASS`；ASan/UBSan（泄漏检测开启、遇错停止）同样 `PASS`；`-Wall -Wextra -Werror`
  与 `git diff --check` 通过。
- 判决：采纳。非法几何在访问 vector 或分配显存前失败，queue 分配失败可回滚 slot 所有权；线程启动失败有明确错误。
- 生产状态与回滚：未改生产；合法初始化的 device 分配、copy 顺序和 slot 选择未变，单提交可回退。没有真实 CUDA，
  因此未运行 filler 成功路径、真实 copy queue OOM 注入、foresight 命中或双卡交换。
- 学到什么：`push_back` 是 slot 状态提交的一部分，不是 slot 改变后的附属通知；若 job 没有可靠进入队列，必须撤销
  slot 与预算的所有权。
- 下一步与理由：检查 `HostGraphLaunch` 的 join/release 约定与 `prefill` 的异步链，再做一轮 Python 全量回归并重试推送。

## I-20261027-24 · FileExpertSource 的 I/O 启停与 stage 缓冲区异常安全

- 目标 / 指标：I/O 预取关闭后不得残留 `io_pf/io_sched/io_stats/gate` 状态；worker 或相关表部分创建失败时不得泄漏
  文件描述符或遗留可 join 线程；读缓冲和 RAM 提升分配异常不得让 worker 调用 `std::terminate`，也不能把 stage buffer
  永久留在 busy。
- 假设与预期：旧 `io_stop()` 只关闭线程和 fd，不重置调度与统计开关，因此关闭再启动会继承上一轮配置；worker 创建
  失败会让异常穿过 `set_io_prefetch()`，而已启动 worker 只能在对象析构时回收。`claim_stage()` 先拿 buffer 再扩容
  多个并行表，任何一次分配失败既可能泄漏原始缓冲区，也可能留下长度不一致的表；`copy_from_files()` 抛异常时
  `stage_busy_` 不会清零。
- 为什么选这条路线：这些状态都局限在 `FileExpertSource` 的预取生命周期，可用合成 `experts.bin`、CPU 侧 CUDA
  shim 和重复启停覆盖，不需要真实模型；修复不改变正常读取字节、预取顺序或公开配置。
- 改动与实验：`set_io_prefetch()` 将 fd 准备、辅助表分配和 worker 创建纳入回滚，失败后调用 `io_stop()`；后者重置
  `io_pf/io_fill/io_sched/io_stats/io_ahead/gate`。`claim_stage()` 在获取原始缓冲前预留所有并行表；I/O worker 的
  stage 选择、fill scratch 和最终 read 均捕获异常并按失败发布 buffer；RAM 提升排队失败会撤销未启动的 job，读取
  分配错误会标记 failed 而不是终止线程。新增预取关闭、重开和重开后读取断言。
- 结果：主机侧 `file_expert_source_test`（合成 pack、CPU 库和同步 CUDA shim）`PASS`；`-Wall -Wextra -Werror`
  编译通过；同一测试的 ASan/UBSan（泄漏检测开启、遇错停止）`PASS`；`git diff --check` 通过。
- 判决：采纳。I/O 预取配置在禁用后没有残留，worker 启动失败有明确回滚，stage buffer 的分配/读取异常有失败发布
  路径。
- 生产状态与回滚：未改生产；正常 `STRATA_IO_PREFETCH` 读取与 stage 字节不变，单提交可回退。本机没有 CUDA
  toolkit、RTX 2080 Ti 或真实模型，未验证 CUDA sm_75、真实 page-fault/worker 竞争或实际吞吐。
- 学到什么：禁用后台设施必须同时撤销它的调度状态；“线程可 join”不等于启动事务成功，分配失败的 stage 元数据也
  必须与数据 buffer 同一事务提交。
- 下一步与理由：继续检查 `ForesightSwap` 和 `HostGraphLaunch` 的队列/线程失败边界，再做主机侧 sanitizer 与完整
  Python 回归。

## I-20261027-23 · Terminal telemetry 的 HTTPError 响应显式关闭

- 目标 / 指标：只读 telemetry client 收到 401/403、重定向或不支持的状态码时，在转换成 `ClientError` 前关闭底层
  `HTTPError` 响应及连接。
- 假设与预期：旧分支直接读取状态码并 raise，`HTTPError` 依赖垃圾回收；频繁轮询失败端点会短期保留 socket/file 对象。
- 为什么选这条路线：终端监控是长期运行的只读路径，修复只涉及一个响应 owner，且可用 `HTTPError.close` 精确验证。
- 改动与实验：`Endpoint.get()` 用 `with e:` 包住所有 HTTP 状态分支；新增 500 响应关闭和 `http_status` 保留测试。
- 结果：connections、terminal、drain 目标回归为 `36 passed / 12 subtests`（另有 28 个平台/环境跳过）；
  `compileall` 与 `git diff --check` 通过。
- 判决：采纳。telemetry 的每个 HTTPError 在错误归一化前都显式关闭。
- 生产状态与回滚：未改生产；状态码映射、重定向拒绝和错误文本不变，单提交可回退。
- 学到什么：错误响应仍是响应对象；返回简化 `ClientError` 前必须先结束其传输资源。
- 下一步与理由：继续把相同原则应用到实际发行包会调用的下载/探测工具，并区分维护用脚本与产品入口。

## I-20261027-22 · runtime stop 在 wait 异常与强制终止后完成 ready/owner 释放

- 目标 / 指标：runtime host 停止 child 时，普通 wait、TimeoutExpired、OSError 和已退出四种情况都必须关闭 owner pipe、
  删除本次 ready 文件；进程组 SIGKILL 失败时仍需直接 kill child。
- 假设与预期：旧 `_stop_child()` 只在正常停止和“child 已退出”路径删除 ready；SIGTERM 后超时或 wait 抛 OSError 时
  只关闭 owner pipe，ready 文件可能残留。`_kill_child_tree()` 仅处理 `ProcessLookupError`，进程组权限/状态异常时
  不会尝试直接 kill。
- 为什么选这条路线：这些分支都可在本地 runtime 单元测试中故障注入，不需要模型；它们直接影响下一次 start 的
  ready 文件辨识和 owner pipe 生命周期。
- 改动与实验：新增 `_clear_ready()`，所有停止终点统一删除本次 ready；首次 wait 的 OSError 与 TimeoutExpired 走同一
  强制终止路径；进程组 kill 的普通 OSError 回退到 `child.kill()`。新增直接 kill 回退、wait OSError、强制停止后
  ready 删除与 owner fd 关闭测试。
- 结果：`tests/terminal/test_runtime.py` 为 `50 passed / 11 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。runtime stop 无论正常或强制结束，都不留下本次 ready 所有权标记或 owner pipe。
- 生产状态与回滚：未改生产；正常 SIGTERM 停止、state/socket 协议和重启回滚行为由现有集成测试保持，单提交可回退。
- 学到什么：停止结果“失败”仍必须完成清理；报告 stop 失败与释放本次尝试的资源不是同一件事。
- 下一步与理由：运行完整 Terminal/Control 回归，随后检查 CLI 和工具脚本中未显式关闭的 HTTP/process 资源。

## I-20261027-21 · MCP stdio 进程组升级终止并有界回收，HTTPError 显式关闭

- 目标 / 指标：stdio MCP 忽略 QUIT/SIGTERM 时，close 必须升级到 SIGKILL、再次有界 wait，并分别关闭 stdin/stdout/stderr；
  HTTP MCP 的 4xx/5xx 响应读取错误正文后必须关闭 `HTTPError`。
- 假设与预期：旧 `_kill_tree()` 只对进程组发送 SIGKILL，不再 wait；`HTTPError` 的正文被读取但没有关闭，连接和底层
  socket 依赖垃圾回收。close 的首次 wait 只捕获 TimeoutExpired，发生 OSError 时会跳过终止和管道释放。
- 为什么选这条路线：两者都是可由 mock 精确复现的资源所有权缺口，不依赖真实 MCP 服务或网络。
- 改动与实验：reader 线程显式命名；close 对 stdin 与两条输出管道逐项关闭；`_kill_tree()` 在 SIGTERM 后等待，超时再
  SIGKILL 进程组并直接 kill child，最后再次有界 wait；HTTPError 使用上下文管理关闭。新增 SIGTERM→SIGKILL→reap
  和 HTTPError.close 断言。
- 结果：`serve/test_mcp.py` 为 `31 passed / 8 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。MCP 子进程组和失败 HTTP 响应在正常关闭、超时和强制终止路径上都有明确释放。
- 生产状态与回滚：未改生产；正常 MCP initialize/tools/call 行为和配置格式未变，单提交可回退。
- 学到什么：发送 SIGKILL 不等于回收完成；HTTPError 也是拥有底层连接的响应对象，也必须显式关闭。
- 下一步与理由：继续审查 runtime host 启动和控制通道，查找子进程启动、ready 文件和控制 socket 的部分失败路径。

## I-20261027-20 · 视觉编码器超时、非 READY 与 unload 统一关闭管道

- 目标 / 指标：视觉编码器 READY/ENC 读取超时、回答非 READY、读取线程启动失败或 unload 时，都必须 kill/wait 进程并
  显式关闭 stdin/stdout；下一次请求不能继承半关闭或仍占用显存/管道的 reader。
- 假设与预期：旧 READY 超时路径只把 `proc` 置空，其他失败路径只 kill/wait；所有路径都依赖垃圾回收关闭管道。
  `close()` 正常 QUIT 和强制 kill 两条路径也没有显式关闭 stdin/stdout。
- 为什么选这条路线：视觉 reader 的 READY、ENC、卸载是同一单进程生命周期；线程和等待故障可注入，不需要图像模型。
- 改动与实验：新增内部读超时类型和统一的 `_discard_reader()/_stop_reader()`；`_start_reader()` 对超时、线程失败和
  非 READY 回答统一回滚；`encode()` 超时与 `close()` 都通过同一 helper 终止、wait 和关闭两条管道。
  `_readline()` 只负责有界等待并向调用方报告超时。新增非 READY、读线程失败、QUIT 超时强制终止和正常 unload
  关闭管道的断言。
- 结果：视觉目标测试 `37 passed / 5 subtests`；完整 `serve/test_server.py` 为 `341 passed / 502 subtests`；
  lifecycle、lifeline、images、MCP 合计 `69 passed / 30 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。视觉 reader 的失败、超时、正常卸载和进程替换共用一个释放边界。
- 生产状态与回滚：未改生产；正常 READY、编码结果和缓存格式未变，单提交可回退。未运行真实视觉模型或 GPU。
- 学到什么：把“kill”当成释放完成会漏掉 pipe buffer 和文件对象；进程回收与管道关闭必须同时由 owner 完成。
- 下一步与理由：继续检查 MCP、runtime 与工具脚本中的进程/文件 owner，优先选择有现成无 GPU 测试入口的路径。

## I-20261027-19 · 引擎启动的旁路线程和槽位初始化纳入统一回滚

- 目标 / 指标：日志 narrator/echo、READY reader、槽位队列或主 stdout pump 任一启动失败时，不能遗留引擎进程、
  stdin/stdout、日志句柄或错误的 echo 注册；READY 前失败与 READY 后初始化失败必须走同一清理边界。
- 假设与预期：旧实现只处理 READY 超时后的部分关闭，且 wait 超时会跳过 stdin/stdout/log 的后续关闭；READY pump
  和主 pump 的 `Thread.start()` 失败则直接抛穿，已创建的大模型引擎仍占用 GPU/RAM。
- 为什么选这条路线：这些是同一启动事务的附属资源，可用真实短命假引擎、定向线程启动失败和队列分配失败复现，
  不需要 GPU 或模型。
- 改动与实验：启动前把日志旁白与 echo 线程纳入回滚，echo 注册失败会撤销；READY reader 和后续槽位/pump 初始化
  统一调用 `_abort_start()`，kill/wait 子进程并分别关闭 stdin、stdout、日志。READY 后的批量槽初始化移入
  `_start_after_ready()`，使异常能被同一事务捕获。新增 narrator、echo、READY reader、主输出 pump 和槽队列
  `MemoryError` 五类故障测试；`restart()` 允许清理后 `proc is None`。
- 结果：启动回滚目标 `9 passed`；完整 `serve/test_server.py` 为 `338 passed / 502 subtests`；lifecycle、lifeline、
  images、MCP 合计 `69 passed / 30 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。READY 前、READY 后和重试路径共享进程、管道、日志与辅助线程的清理契约。
- 生产状态与回滚：未改生产；正常 READY、槽位布局、stdout 路由和重启行为由完整测试保持，单提交可回退。
- 学到什么：构造函数的正常返回点就是启动事务提交点；在其前创建的任何进程或线程都应由同一个 rollback 负责。
- 下一步与理由：继续做跨模块 Python 资源审查，优先找仍用“单次 try 包多个 close”导致首错跳过后续释放的路径。

## I-20261027-18 · 子进程所有权记录与 reaper 线程失败保持同一回收边界

- 目标 / 指标：`lifeline.popen()` 已创建 child 后，若所有权记录失败或 reaper 线程无法启动，正常推理启动不得失败；
  child 仍须进入统一退出清理。`end_children()` 还要清理已结束 child 的记录，并在 SIGTERM 超时、SIGKILL 后再次有界
  wait，不能只发信号就返回。
- 假设与预期：旧实现把记录写入和 reaper 线程启动放在同一个静默 `try` 中。线程启动失败时 child 虽未必失活，但没有
  owner 关联，无法安全删除其记录；`end_children()` kill 后立即返回也可能留下尚未被 wait 的进程资源。
- 为什么选这条路线：child 是否可关闭、记录是否可删除和线程启动是否成功可以独立验证；故障注入无需 GPU。
- 改动与实验：维护 child 到 record token 的 owner 映射；reaper 成功、下一次 `popen()` 或 `end_children()` 都可回收已结束
  child。record 写入与线程启动分别 best-effort，失败不影响返回的可用 child。`end_children()` 先清理已结束项，
  SIGTERM 超时后 SIGKILL 并额外 `wait(1s)`，最后只删除已验证 token 的记录。新增 reaper 启动失败和忽略 SIGTERM
  的 child 测试。
- 结果：`serve/test_lifeline.py serve/test_images.py` 合计 `31 passed / 20 subtests`；`compileall` 与
  `git diff --check` 通过。
- 判决：采纳。进程跟踪不再依赖 reaper 线程一定创建成功，退出路径在 kill 后完成有界回收。
- 生产状态与回滚：未改生产；正常 child 启动、PDEATHSIG 和记录格式不变，单提交可回退。未验证真实引擎和 GPU。
- 学到什么：best-effort 的附属记录不能成为进程回收的唯一索引；主跟踪表和记录 token 必须分开维护。
- 下一步与理由：继续审查 `serve/server.py::StrataEngine` 在日志、READY 与 stdout pump 线程部分启动失败时的回滚。

## I-20261027-17 · 图片任务的读线程、子进程和临时目录统一收尾

- 目标 / 指标：`sd-cli` 创建成功后，读线程启动失败、进程不退出或 kill 后仍等待超时时，必须终止并等待子进程、
  关闭 stdout，且只在输出排空或管道已关闭后删除 job 目录；后台 pump 不得因日志写入失败留下未处理异常。
- 假设与预期：旧实现直接 `reader.start()`，线程创建失败会逃出并遗留子进程；`reader.join(10)` 后无论线程是否
  仍存活，`run()` 的 `finally` 都会删除工作目录，日志句柄也可能在 pump 仍写入时关闭。
- 为什么选这条路线：这是单请求进程生命周期的集中边界，可用 mock `Thread.start`、`Popen.wait` 故障注入验证，
  不需要 GPU、模型或 sd-cli 二进制。
- 改动与实验：pump 改为使用 `select` 和原始管道 fd 排空输出，日志写入失败后继续 drain 以防子进程阻塞；读线程
  启动失败时 kill/wait、关闭 stdout 并转成 `ImageJobFailed`；正常和双重超时路径都在 cleanup 前关闭管道。
  新增读线程启动失败与 kill 后仍等待超时测试。
- 结果：`serve/test_images.py` 为 `24 passed / 20 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。读线程启动、子进程结束、管道关闭和 job 删除现在有统一失败收尾。
- 生产状态与回滚：未改生产；正常 HTTP 请求和图片字节不变，单提交可回退。未运行真实 CUDA sd-cli 或图片生成。
- 学到什么：进程已创建不等于启动事务已完成；附属读线程失败与进程等待超时都必须经过同一资源回收路径。
- 下一步与理由：检查 `lifeline.popen()` 在所有权记录或 reaper 线程注册失败时是否仍能由统一退出路径回收 child。

## I-20261027-16 · MCP 启动线程失败回滚子进程，Hub 批量启动不留半活服务器

- 目标 / 指标：stdio MCP 子进程启动后，stderr 或 stdout 读线程任一创建失败时，必须终止子进程、关闭三条管道并
  join 已启动线程；Hub 批量启动中某个线程失败时，必须关闭已启动服务器，且 close/wait 不能 join 未启动线程。
- 假设与预期：旧路径只捕获 `Popen` 的 `OSError`。若第一个读线程已启动而第二个线程因资源限制失败，异常会逃出
  `start()`，子进程继续运行；Hub 的列表中也包含未启动线程，统一 close 会在 `join()` 处再次失败并掩盖原错误。
- 为什么选这条路线：这是同一事务边界的真实部分构造问题，可用 mock 进程与线程启动注入复现，不需要真实 MCP 服务。
- 改动与实验：`StdioTransport.start()` 将两个 reader 的启动纳入回滚，失败时调用统一 `close()`；close 仅 join 确实
  启动过的线程。`McpHub.start()` 任一线程启动失败即关闭全部服务器并传播；`wait()` 跳过尚未启动的线程。
  新增第二个 reader 失败、Hub 第二个线程失败和既有 close 中断启动测试。
- 结果：目标测试 `3 passed`；完整 `serve/test_mcp.py` 为 `29 passed / 8 subtests`；`compileall` 与 `git diff --check`
  通过。
- 判决：采纳。MCP 启动失败要么留下可重试关闭态，要么完全清理，不保留半活子进程或未启动 join。
- 生产状态与回滚：未改生产；正常启动和关闭路径不变，单提交可回退。
- 学到什么：进程创建成功不等于启动事务成功；其附属线程、管道和注册记录必须与进程一起回滚。
- 下一步与理由：继续检查 server/lifeline 的进程启动与线程注册失败组合，随后进行较广 Python 回归。

## I-20261027-15 · PLE 初始化回滚与在途 I/O 显式计数，消除 close 等待和缓存竞争

- 目标 / 指标：`PleReader::open()` 在 slab、向量或行缓存分配失败时不能抛穿或留下半开状态；`close()` 只等待确实
  提交给 DirectFile 的完成事件；缓存统计查询与 worker 更新之间不得存在数据竞争。
- 假设与预期：旧 `close()` 用“空闲槽少于 max_inflight”推断在途 I/O，初始化在填满空闲槽之前失败时会误判并
  永久等待。故障注入延迟产生的 completion 已离开 DirectFile 队列，也必须从等待计数中移除。预取缓存 worker 会
  更新 `used`，主线程直接读取会造成真实竞争。
- 为什么选这条路线：这些问题都集中在无 GPU 的 SSD/PLE 边界，可用合成表、真实 O_DIRECT 和小范围 sanitizer 复现；
  修复不改变行格式、读取顺序或公开配置。
- 改动与实验：新增显式 `outstanding` 计数，只在 `DirectFile::submit()` 成功后增加、每个 completion 仅减少一次；
  初始化分配异常统一返回错误并回滚，close 丢弃 fault-injected completion 后按真实在途数等待；错误 ticket 在 collect
  失败时移除。缓存容量/用量查询纳入 mutex。mmap 预热线程池部分创建失败时由调用线程补做剩余工作并 join 已启动线程。
  selftest 新增不可能的缓存分配与未 collect 即 close 的路径，并让并发测试进程使用独立目录。
- 结果：普通 `-Wall -Wextra -Werror`、ASan/UBSan、TSan 三套构建和 `ple_reader_test --selftest` 均通过 90/110 字节行、
  Direct/Mmap、八种 PLE 格式。首次 TSan 运行发现 `cache_size()` 与 worker `cache.insert()` 的竞争；修复后 TSan 复验通过。
- 判决：采纳。在途 I/O、失败回滚和缓存查询均有明确同步边界。
- 生产状态与回滚：未改生产；单提交可回退。mmap 预热线程创建失败分支仅完成编译和静态审查，未做故障注入。
- 学到什么：“所有槽都不空闲”不等于“所有槽都有在途 I/O”；资源释放必须依据正向提交计数，不能从空闲池反推。
- 下一步与理由：继续审查其他线程池的部分构造与 close 同步，优先选择能由主机侧 sanitizer 覆盖的路径。

## I-20261027-14 · 描述符所有权转移失败统一关闭，覆盖 runtime、编辑草稿、锁与临时图片

- 目标 / 指标：`os.open()` 成功后，若 `fdopen()` 在包装既有描述符时失败，调用方仍必须关闭原 fd；
  runtime 启动日志、保留 lease、profile 编辑草稿、connection 锁和组合图片文件不能泄漏描述符或残留临时路径。
- 假设与预期：这些入口都直接调用 `os.fdopen(fd)`，而 fd 在 `fdopen` 抛错时尚未进入文件对象所有权；
  Python 不会替调用方关闭它。把失败关闭收敛到共享 helper 后，各入口应只负责路径和更高层事务清理。
- 为什么选这条路线：它们共享同一个资源所有权边界，能以一个小 helper 消除重复且不改变正常文件格式或并发语义；
  失败注入可用真实 `fstat` 断言，不需要 GPU 或模型。
- 改动与实验：`atomic.fdopen_owned()` 统一包装 fd，转移失败即关闭；原子写入复用该边界。runtime 启动日志、
  runtime lease 写副本、profile 编辑器草稿、connection 锁与 `serve.server.write_temporary()` 均改用该 helper。
  新增 helper、runtime 启动/记录、profile 编辑、connection 锁和组合图片的 `fdopen` 失败测试。
- 结果：atomic/profile/connection `37 passed / 5 subtests`，runtime `48 passed / 11 subtests`，
  组合图片目标测试 `3 passed`，合计 `88 passed / 16 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。描述符从打开到成功包装或失败关闭只有一个共享契约。
- 生产状态与回滚：未改生产；正常文件内容、权限、锁顺序和日志写入方式不变，可按单个提交回退。
- 学到什么：`fdopen` 不是一个普通构造调用，而是资源所有权转移点；即使调用处在 `with` 语句中，构造失败也需单独兜底。
- 下一步与理由：继续审查 C++ 线程/进程和缓冲区的部分构造失败路径；这些与 Python fd 路径独立，适合下一阶段集中验证。

## I-20261027-13 · Exclusive-create 记录统一走不泄漏 fd 的原子创建

- 目标 / 指标：connections、operation journal 和 ready file 的新建路径必须使用同一套 owner-only、
  `O_EXCL`/`O_NOFOLLOW` 语义；`fdopen` 或写入失败时不能留下文件描述符或半成品记录。
- 假设与预期：三处此前各自 `os.open` 后直接 `with os.fdopen`，而 `fdopen` 自身抛错时原 fd 不在上下文
  管理范围内；三处也有重复的 flags、权限和清理代码。
- 为什么选这条路线：这些文件分别承载连接配置、操作审计和 readiness 证明，失败语义必须一致，且能用
  真实 fd 检查和 mock 故障验证。
- 改动与实验：新增 `atomic.write_new()`，集中处理 `O_EXCL`、可用的 `O_NOFOLLOW`、0600、flush/fsync、
  父目录同步和失败删除；connections、journal、`serve.ready.publish()` 全部委托该实现。共享的
  descriptor 写入 helper 无论 `fdopen` 是否成功都会关闭 fd。新增 owner-only、不覆盖已有文件、fdopen
  失败无文件/fd 残留测试。
- 结果：atomic、connections、profiles、ready、ready-timeout 和 runconfig 合计
  `48 passed / 30 subtests passed`；ready 的真实 mock server SIGTERM 集成测试也通过，
  `compileall` 与 `git diff --check` 通过。
- 判决：采纳。exclusive-create 的创建和失败清理只有一个实现。
- 生产状态与回滚：未改生产；可用单个提交回退，既有记录格式与权限不变。
- 学到什么：`fdopen` 是资源所有权转移点，失败时仍由调用方负责关闭；把转移封装在 helper 内最容易证明。
- 下一步与理由：继续处理需要保留锁 fd 的 runtime state 写入，以及重启回滚中的错误边界。

## I-20261027-12 · 原子记录写入统一，失败路径关闭描述符并删除临时文件

- 目标 / 指标：profile、connection 和 Web run config 的整文件替换必须共用同一实现；写临时文件、
  `fdopen`、`fsync` 或 `replace` 任一步失败时，不能泄漏描述符、覆盖旧记录或遗留临时文件。
- 假设与预期：旧 profiles 与 connections 各自复制了同一段原子替换逻辑，且 `os.fdopen(fd)` 自身失败时
  没有关闭原始 fd；run config 还使用固定 `<name>.tmp`，并发保存会碰撞。
- 为什么选这条路线：这是跨三个持久化入口的重复实现和同一个失败缺口，合并后能同时减少代码并扩大故障覆盖。
- 改动与实验：在 `strata_control.atomic` 增加 `write_bytes()`，统一使用同目录随机临时文件、flush/fsync、
  replace 和父目录 fsync；profiles 与 connections 改为委托该实现；Web run config 改用同一原子写入，
  删除固定 `.tmp` 路径；`write_text()` 也复用字节实现。新增 fdopen 失败、replace 失败、父目录 fsync
  失败和正常模式/残留文件检查。
- 结果：`test_atomic.py`、profiles、connections、runconfig 合计
  `41 passed / 26 subtests passed`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。三个入口现在共享一个失败语义，旧记录在提交点之前保持不变。
- 生产状态与回滚：未改生产；仅收紧本地 profile、连接与 run config 的保存路径，可用单个提交回退。
- 学到什么：原子替换不仅是 `os.replace`；临时 fd 的所有权、清理点和父目录同步都必须放在同一个事务边界里。
- 下一步与理由：继续收敛 exclusive-create 写入（ready/state/journal）的 fd 所有权，并补充失败注入。

## I-20261027-11 · Runtime stop 正确处理 SIGTERM 前 fast-exit，强杀等待保持有界

- 目标 / 指标：`Host._stop_child()` 不能在 `poll()` 与 `send_signal(SIGTERM)` 之间子进程自行退出时误报失败；
  SIGKILL 之后的第二次 `wait()` 也必须有界，且任何失败结果都先关闭 owner pipe。
- 假设与预期：`poll()` 返回活动只代表检查瞬间；`send_signal()` 可合法抛出 `ProcessLookupError`。旧代码让
  这个正常竞争冒泡成 `invalid_control_request`，并使调用方看不到真实停止状态。
- 为什么选这条路线：这是 stop/restart 的真实竞争窗口，可用假 child 精确覆盖；不需要模型或真实服务。
- 改动与实验：将 `ProcessLookupError` 视为“已经退出”，仍由随后的 `wait()` 回收退出码；SIGKILL 后的
  `wait(timeout=10)` 若仍超时则关闭 owner pipe、返回 `server_stop_deadline_expired`，不再向控制循环抛异常。
  新增 fast-exit 与双重超时两个回归。
- 结果：新增用例 `2 passed`；完整 `tests/terminal/test_runtime.py` 为
  `46 passed / 11 subtests passed`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。stop 竞争被归一为 stopped 或有明确错误码的 failed，不再变成协议解析错误。
- 生产状态与回滚：未改生产；只影响 supervisor 的停止边界，可用单个提交回退。
- 学到什么：进程状态是随时间变化的外部状态；每次基于 `pid` 的操作都必须准备目标刚刚消失。
- 下一步与理由：继续检查 restart 回滚和 server 持久化写入的失败原子性。

## I-20261027-10 · Runtime supervisor 启动失败不泄漏日志、管道或服务器子进程

- 目标 / 指标：`Host._launch()` 在打开日志、创建 owner pipe、启动 `Popen`、写入进程记录或 child marker
  任一步失败时，都必须关闭所有已取得的描述符；`Popen` 已成功但 ownership 记录失败时，必须终止并
  `wait()` 子进程，避免留下无人管理的服务器。
- 假设与预期：原实现只在 `Popen` 周围关闭 reader/writer；`os.pipe()` 本身失败会漏掉已打开的日志 fd，
  而 `Popen` 成功后的 `procs.write_record()` 异常不在清理边界内，可能留下运行中的子进程。
- 为什么选这条路线：这是本地 supervisor 的真实资源所有权问题，可用 mock 子进程和真实 fd 检查稳定复现，
  不需要启动模型或 GPU。
- 改动与实验：pipe 创建失败时关闭日志；`fdopen` 前记录所有权并在失败时补关；把进程记录和 marker
  发布放进同一个启动回滚边界；新增无异常泄漏的 `_abort_launch()`；owner pipe 关闭改为幂等 best-effort；
  已退出或刚消失的子进程不再被当作可 killpg 的活动进程。新增 pipe 失败、`Popen` 失败、
  ownership 记录失败和已退出 child 四个回归。
- 结果：新增 4 个用例通过；完整 `tests/terminal/test_runtime.py` 为
  `44 passed / 11 subtests passed`，`compileall` 与 `git diff --check` 通过。
- 判决：采纳。启动成功前不留下任何仍归 supervisor 所有的 fd 或子进程。
- 生产状态与回滚：未改生产；只影响 managed runtime 的启动失败路径，可用单个提交回退。
- 学到什么：`Popen` 返回后、ownership 文件落盘前仍处于“未完成构造”状态，必须按失败处理。
- 下一步与理由：继续检查停止/重启流程中 SIGTERM 与 fast-exit 的竞争，以及 server 端持久化写入的原子性。

## I-20261027-09 · CPU 池部分线程启动失败可回滚，真实任务一致性通过

- 目标 / 指标：`ExpertPool` 构造期间若创建到一半的 worker 抛出异常，必须停止并 join 已启动线程、清理全局
  NUMA/诊断状态，再向调用方传播错误；不得因 joinable `std::thread` 在栈展开中析构而触发进程终止。
- 假设与预期：现有正常析构会停止并 join，但构造函数直接连续 `emplace_back`；后续线程创建失败时构造函数
  尚未完成，析构函数不会运行，部分已创建线程会留在成员 vector 中。
- 为什么选这条路线：这是 `std::thread` 资源获取失败的确定性生命周期缺口。仓库固定了完整 ggml 上游提交，
  可在 `/tmp` 检出并真实链接 CPU 池，不需要 GPU 或模型。
- 改动与实验：抽出幂等的 `stop_workers()`，构造与析构共用；把可能抛异常的全局指针发布和线程创建放进
  同一回滚边界；移除 Linux 发行版永远走不到的 MSVC `UMWAIT` 分支与相关环境读取；增加
  `STRATA_TEST_POOL_THREAD_FAIL_AT` 故障注入，先启动两个 worker、在第三个前失败，并验证 NUMA 全局状态
  已清除、随后可以构造替代池。用固定提交
  `3cf03257f219afbe7334045ff7c6a06ac68c627d` 的 ggml 构建静态库，只写入 `/tmp`。
- 结果：`pool_cores_test`、`pool_affinity_test` 通过；`pool_tasks_test` 完成
  `360` 项 bitwise 比较，其中 `192` 项走 NUMA split；故障注入路径通过。普通
  `-Wall -Wextra -Werror`、AddressSanitizer + UndefinedBehaviorSanitizer、ThreadSanitizer 构建均通过。
  `pool_test`、`expert_multi_test`、`pool_stress` 可编译，但当前 CPU 缺 AVX-512 VNNI/VBMI，按测试协议跳过
  `modes 0-4` 和真实 expert pack 路径。
- 判决：采纳。构造失败现在是“停止、join、清空全局状态、rethrow”，不再依赖正常析构。
- 生产状态与回滚：未改生产；故障注入只在设置测试环境变量时触发，可用单个提交回退。
- 学到什么：构造函数也必须具备失败自动清理；资源按顺序获取后，任何后续步骤抛异常都要按逆序释放。
- 下一步与理由：继续审计其他“先创建线程/句柄，再完成剩余初始化”的构造器，并运行 CPU 池压力与真实载荷。

## I-20261027-08 · 直接文件 I/O 拒绝关闭态请求，并安全串行化打开、关闭和提交

- 目标 / 指标：`DirectFile` 在未打开或关闭后不能接受请求；打开/关闭与并发提交不能留下无人处理的队列、
  陈旧完成或数据竞争；I/O 工作线程创建失败时必须关闭文件描述符并回收已创建线程。
- 假设与预期：原测试覆盖了排队读取、短 EOF、唤醒、关闭排空和重开，但旧 `submit()` 不检查句柄状态；
  关闭后排队会违反“成功排队恰好产生一个完成”的约定。打开阶段的线程创建也没有异常边界。
- 为什么选这条路线：这是可直接在 Linux 上复现的资源与竞争问题；同时可运行 CPU 池规划、NUMA 决策和
  直接文件测试，不需要 GPU。
- 改动与实验：新增生命周期互斥量，串行化 `open()/close()`；以受锁的 `accepting` 状态和文件描述符共同
  决定 `submit()`/`is_open()`；关闭时先拒绝新请求，再排空已接受队列并 join 工作线程；线程创建异常会
  走同一关闭路径并返回错误；空缓冲也不再被当作合法请求。新增关闭态、失败打开和并发 submit/close 覆盖。
- 结果：`pool_plan_test`、`numa_pool_test` 和 `direct_file_async_test` 均通过。`direct_file_async_test`
  在普通 `-Wall -Wextra -Werror`、AddressSanitizer + UndefinedBehaviorSanitizer、ThreadSanitizer 三种
  构建下均通过，包含 4096 个并发排队尝试与关闭竞争，无 sanitizer 报告。
- 判决：采纳。关闭态请求现在明确失败；成功进入队列的请求仍由关闭排空；生命周期不再依赖调用方恰好单线程。
- 生产状态与回滚：未改生产；只影响 n-gram 表直接读取路径的边界处理，可用单个提交回退。
- 学到什么：“队列入参合法”不够，必须先确认句柄仍可接收；资源启动失败也必须经过正常停止/释放路径。
- 下一步与理由：继续检查其他线程池的构造失败和重复关闭，并尝试运行 CPU 池的真实任务一致性测试。

## I-20261027-07 · 会话、并发与推测模块的主机侧回归扩大，内存锁定测试适配系统限额

- 目标 / 指标：继续把不依赖 CUDA 运行时的 C++ 目标变成当前机器上的真实编译和运行结果，重点覆盖
  会话缓存/落盘/回收、内存保护、交换存储并发、批处理遥测以及 speculation 的边界与统计行为。
- 假设与预期：这些目标要么只依赖 `include/`，要么只需要少量主机 `.cpp`；如果测试固定要求系统无法
  提供的锁定内存，应修正测试的环境假设，而不是把宿主机策略误报为产品逻辑失败。
- 为什么选这条路线：上一阶段证明解析和规划测试可运行后，继续覆盖资源生命周期与并发逻辑的收益更高；
  同时 `platform_memory_test` 在默认 8 MiB `RLIMIT_MEMLOCK` 下暴露了可移植性缺口。
- 改动与实验：按 CMake 的目标定义直接使用 `g++ 15.2.0 -std=c++20 -O2 -pthread -Iinclude` 编译运行
  17 个纯头文件测试、4 个 speculation 测试（链接 `src/spec/*.cpp`）和 7 个会话/内存测试。
  `platform_memory_test` 改为读取当前 `RLIMIT_MEMLOCK`，在限额内锁定页对齐区域；若限额不足一页则返回
  CMake 的 skip 码 77。修改后的测试另用 `-Wall -Wextra -Werror` 编译通过。
- 结果：28 个主机侧测试全部编译并以退出码 0 通过。受限机器上 `lock_resident` 实际锁定 4 MiB、返回
  `ok=1`、`locked=4 MiB`，纯文件缓存/直读规则断言也全部通过。`pin_budget_test` 因缺少
  `cuda_runtime.h` 和 `nvcc` 无法编译，未计入通过。
- 判决：采纳测试适配；它保留真实的 `mlock/munlock` 成功路径，同时不再要求 CI 管理员提高系统锁定上限。
- 生产状态与回滚：只改测试及其 CTest 跳过码，不改运行时内存策略；可用单个提交回退。
- 学到什么：系统资源上限是测试环境的一部分；测试应验证产品在限额内的行为，并用明确的 skip 表示
  无法构成的硬件/内核前置条件。
- 下一步与理由：转向可在 Linux 上直接验证的 CPU 池、会话快照和平台文件 I/O，并继续检查关闭与失败释放。

## I-20261027-06 · 主机侧 C++ 解析、规划与采样测试可独立编译通过

- 目标 / 指标：在没有 CUDA 编译器和 GPU 的当前机器上，实际编译并运行不依赖设备运行时的 C++ 单元测试，
  覆盖模型族识别、GGUF 分片与几何校验、prefill/split/batch 规划、共享规则和 logit bias。
- 假设与预期：这些测试只使用 `include/` 中的主机头文件和标准库；用同一编译器直接编译后，应能暴露
  平台无关的编译错误和纯逻辑回归，即使不能证明 CUDA sm_75 目标可用。
- 为什么选这条路线：当前没有 `nvcc`，但仓库同时包含主机侧纯逻辑测试；先把所有可验证项转成真实编译结果，
  能把“静态看起来正确”和“当前机器确实通过”分开记录，成本也低。
- 改动与实验：使用 `g++ (Debian 15.2.0-17) 15.2.0 -std=c++20 -O2 -pthread -Iinclude`，逐项编译并运行
  `tests/core/model_family_test.cpp`、`tests/core/gguf_split_test.cpp`、`tests/core/gguf_geometry_test.cpp`、
  `tests/program/split_rules_test.cpp`、`tests/program/prefill_plan_test.cpp`、
  `tests/program/batch_read_test.cpp`、`tests/prefill/share_rules_test.cpp` 和
  `src/core/logit_bias_test.cpp`。构建产物只写入 `/tmp`。
- 结果：8 个测试全部编译并运行通过；模型族和 GGUF 测试输出 `ALL PASSED`/`all passed`，split、prefill、
  batch-read、share-rules 输出 `ok`，logit-bias 输出 `passed, including 103215 bans`。
- 判决：采纳为当前机器的主机侧验证证据。不得推广为 CUDA、GPU 或端到端推理验证。
- 生产状态与回滚：未改生产；本阶段无源码变更，只记录可复现证据。
- 学到什么：发行面收敛后仍有一组高价值的主机侧测试；它们能在无 GPU 环境中守住格式、边界和规划逻辑。
- 下一步与理由：继续筛选同样不依赖 CUDA 运行时的测试，优先覆盖线程、存储、会话和多线程资源生命周期。

## I-20261027-05 · 关闭后的 MCP HTTP 响应不会写回旧会话

- 目标 / 指标：`HttpTransport.close()` 执行时，已在 `urlopen` 中等待的响应即使随后返回，也不能更新
  session 或完成调用槽；关闭后的新 POST 必须立即失败。
- 假设与预期：上一个阶段中断了启动等待，但 `_post()` 在 `urlopen` 返回后没有重新检查关闭状态，
  因此迟到的响应仍可能写入结果，形成旧会话复活。
- 为什么选这条路线：这是关闭竞争的第二半，可用阻塞假 `urlopen` 精确复现，不需要外部 MCP server。
- 改动与实验：`_post()` 在进入请求、`urlopen` 返回和确认结果三处检查关闭状态；关闭后的迟到答案转为
  `McpError("the MCP server is closed")`；新增迟到响应回归。
- 结果：`python3 -m pytest serve/test_mcp.py -q` 为 `27 passed / 8 subtests passed`；新增用例断言关闭后
  worker 结束、等待槽被唤醒、没有写入 `msg`，错误为明确的 closed；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。关闭是单向状态：迟到的成功结果不能覆盖关闭结果。
- 生产状态与回滚：未改生产；MCP 可选功能内的关闭竞争修复。
- 学到什么：取消只能缩短等待，不能自动丢弃已经发起的 I/O；异步结果写回前必须再次验证生命周期状态。
- 下一步与理由：继续检查其他“后台线程完成后写回对象”的路径，尤其是 HTTP、会话保存和重载。

## I-20261027-04 · MCP 启动与关闭竞争可中断，stdio 读线程随进程退出

- 目标 / 指标：服务器关闭时，尚在 `initialize` 或工具列表阶段的 MCP 启动线程不能继续等待完整启动超时；
  stdio 子进程关闭后，stdout/stderr 读取线程必须结束；关闭后的 HTTP 会话不能再次被调用。
- 假设与预期：旧 `McpServer.close()` 需要先取得启动线程持有的锁，因此可能阻塞最长
  `start_timeout_s`；stdio 传输虽关闭管道，但没有保存和 join stdout 读线程。
- 为什么选这条路线：这是明确的关闭竞争和线程泄漏，能用阻塞假传输在无网络、无 MCP SDK 的环境中复现。
- 改动与实验：给每个 MCP server 增加关闭事件；关闭直接打断当前 transport，不等待启动锁；启动完成或失败后
  再检查关闭状态；stdio 保存两个读线程并在关闭后有界 join；HTTP transport 关闭后标记为不可用；
  `McpHub.close()` 在关闭所有 server 后对启动线程统一执行 3 秒 join；新增“启动阻塞时关闭”回归。
- 结果：`python3 -m pytest serve/test_mcp.py -q` 为 `26 passed / 8 subtests passed`；新增用例证明
  `close()` 在阻塞 transport 下 2 秒内返回、启动线程结束且状态为 `stopped`；`compileall` 与
  `git diff --check` 通过。
- 判决：采纳。关闭仍保持 best-effort，但不再被长启动超时或启动锁拖住。
- 生产状态与回滚：未改生产；MCP 仍是可选功能，可用单个提交回退。
- 学到什么：关闭路径不能先获取由长时间启动任务持有的互斥锁；“先发取消信号，再有界等待”才能保证退出。
- 下一步与理由：继续检查 HTTP/stdio 子进程的独立后台请求，确认关闭后不会重新启动或写回旧会话。

## I-20261027-03 · 遥测采样线程在关闭时等待退出

- 目标 / 指标：服务器关闭后，后台 NVML/CPU/RAM 采样线程必须收到停止信号并完成退出，而不只依赖
  `daemon=True` 随解释器结束；重复关闭仍应安全。
- 假设与预期：`Telemetry.close()` 原有实现只设置 `Event`，调用方无法确认采样线程已经结束；在连续
  创建和关闭测试服务器时会留下短暂重叠的采样线程。
- 为什么选这条路线：这是纯 CPU 可验证的线程生命周期问题，改动局部，能直接改善测试隔离和退出确定性。
- 改动与实验：保存采样线程句柄；`close()` 设置停止事件后，在非线程自身调用时最多等待 2 秒；测试在
  关闭后直接断言线程已不再存活。
- 结果：`NvidiaTelemetry` 与 `TelemetryNoDisks` 为 `2 passed`，`NoLeakedSampler` 为 `1 passed`；
  `compileall` 和 `git diff --check` 通过。
- 判决：采纳。停止事件仍是非阻塞主路径，join 只给正常采样周期一个明确上限。
- 生产状态与回滚：未改生产；关闭时最多多等待一个采样周期，可用单个提交回退。
- 学到什么：后台守护线程也需要明确的生命周期终点；仅设置停止标志只能证明“已请求停止”，不能证明
  “已经停止”。
- 下一步与理由：继续检查持锁的后台任务和等待队列，优先处理可导致退出延迟或资源继续被占用的路径。

## I-20261027-02 · 看图服务的退出释放与 Linux 临时目录收敛

- 目标 / 指标：核查 `strata-vision` 的日志、工作目录和反复卸载/重启生命周期，删除发行树中已经失效的
  Windows `SystemDrive` 临时目录分支；验证失败时不能把日志句柄留给进程退出处理。
- 假设与预期：`Vision.close()` 必须保留日志和工作目录供 `restart()` 使用；只有最终 `shutdown()` 才能关闭
  自有日志并删除临时目录。服务启动时打开的日志若在编码器构造失败后没有关闭，则形成确定的句柄泄漏。
- 为什么选这条路线：它与上一阶段的图像任务清理属于同一类生命周期问题，可用纯本地单元测试验证，
  且不需要 GPU、模型或生产重启。
- 改动与实验：为 `Vision` 增加显式日志所有权；只有服务器打开的日志在 `shutdown()` 时关闭，卸载时仍保留；
  看图编码器或图像服务构造失败时关闭刚打开的日志；错误退出路径也调用图像服务 `shutdown()`；
  临时目录候选从 Windows 的 `SystemDrive` 改为 Linux `/tmp` 与 `/var/tmp`，并保持“路径无空格”回退。
- 结果：`serve/test_server.py -k 'VisionShutdown or VisionTempFiles'` 为 `6 passed / 2 subtests passed`；
  `serve/test_images.py` 为 `22 passed / 20 subtests passed`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。`close()`/`unload()` 保留重启所需资源，`shutdown()` 负责最终释放；不再创建旧的 Windows
  系统盘候选路径。
- 生产状态与回滚：未改生产；纯本地服务代码、测试和文档变更。
- 学到什么：同一个日志句柄在“暂时卸载”和“服务退出”两个生命周期中的所有权不同，测试必须分别覆盖，
  不能用一个 `close()` 同时承担两种语义。
- 下一步与理由：继续按“临时目录、日志、子进程、退出路径”审计剩余服务代码，再转向后台线程和并发锁。

## I-20261027-01 · 图像任务失败与服务器退出时不再遗留进程、目录和日志句柄

- 目标 / 指标：核查图像生成子进程的启动、失败、超时和服务器退出路径，确保没有残留任务目录、日志句柄
  或仍占用 GPU 的 `sd-cli`；不改变成功请求的 API 行为。
- 假设与预期：现有成功路径会清理任务目录，但 `Popen` 启动失败发生在清理块之前；服务退出也没有调用
  `ImageGen` 的释放逻辑。
- 为什么选这条路线：这是不依赖目标 GPU 的真实资源泄漏，能用假 `sd-cli` 和本地文件系统稳定复现，
  比继续做静态边界删除更容易验证。
- 改动与实验：把每个图像任务放进统一的 `try/finally` 清理边界；启动失败、超时和取消都回到清理；
  `sd-cli` 在等待超时后补发 kill 并等待；新增 `ImageGen.shutdown()`，关闭自有日志并在临时输出目录时
  删除目录，显式 `out_dir` 保持不动；服务器退出列表接入该释放方法。
- 结果：`python3 -m pytest serve/test_images.py -q` 为 `22 passed / 20 subtests passed`；新增覆盖自有
  临时目录、显式输出目录、重复关闭和启动失败无残留任务目录。
- 判决：采纳。成功请求的字节级约定和 HTTP 测试未改变；修复只收紧异常与退出生命周期。
- 生产状态与回滚：未改生产；纯本地服务代码和测试变更，可用单个提交回退。
- 学到什么：资源清理必须包围整个“创建临时目录 → 启动子进程 → 读取输出”序列；只在子进程启动后
  进入 `try` 会漏掉最常见的失败分支。
- 下一步与理由：继续审计同类 `mkdtemp`、日志句柄和长期后台线程，优先处理能用当前离线测试证明的问题。

## I-20261026-21 · 生产切换到 `6×256K`，容量与长上下文功能门通过

- 目标 / 指标：把生产上下文从 `192000` 提到 `262144`，同时保持 6 个 batch slot、INT8 KV、
  `kv_resident=32768` 和现有调度开关不变；用真实长请求与 6 路并发验证切换。
- 假设与预期：KV streaming 会把显存 K/V 窗口固定住，新增上下文主要增加 indexer、RoPE 和
  pinned host KV；expert cache 应小幅缩小，但 6 槽仍能启动。
- 为什么选这条路线：这是用户明确要求的容量升级，不需要改代码；先做最小 config diff 和真实请求，
  比直接扩大 expert cache 或其他性能开关更能隔离风险。
- 改动与实验：建立 `/opt/ai/tmp/pre-ctx256k-20261026T075110Z/` 完整快照；只把 config 的
  `max_context` 改为 `262144`，预设检查通过后原子安装并重启。服务在约 190 s 后 loaded；
  随后跑 `222,322` token 与 `253,633` token 两次 needle，以及 6 路短并发。
- 结果：两次 needle 均命中，墙钟 `147.2/152.7 s`；6 路短并发 `6/6` 成功。生产保持
  `batch_slots=6`、`kv_resident=32768`；expert slots `17728→17248`，expert cache
  `29010→28222 MiB`。启动后两卡空闲 `1005/1073 MiB`；长读后 RAM 可用约 `5.8 GiB`、swap 0。
- 判决：**采纳并已上线，mixed soak 待做**。功能门足以证明 256K 上下文实际可服务，但不足以证明
  长时间混合流量稳定。
- 预期被推翻：没有；expert cache 实际只缩小约 788 MiB（双卡合计），6 槽未因显存不足降级。
- 生产状态与回滚：health 200、`loaded=true`、engine `a09d78b1`、server `de09cffd`、
  config `ea1e2959`；回滚快照完整并通过 SHA-256 清单校验。
- 证据：L-088；仓库内 `docs/work/evidence/ctx256k-needle-222k-20261026.json`、
  `docs/work/evidence/ctx256k-needle-253k-20261026.json`；快照中的 `metrics-after.json`、
  `metrics-final.json`、`nvidia-smi-final.txt` 与 `free-final.txt`。
- 学到什么：`--kv-resident` 下上下文扩张不等价于整段 KV 进 VRAM；真实瓶颈需要同时看 expert cache、
  pinned RAM 和 conversation cache，不能只按显存估算。
- 下一步与理由：先跑 256K mixed soak，覆盖 32K/64K/96K/长读与短请求并发；P7 等该基线稳定后再从
  全新 A1 重跑。

## I-20261026-20 · P7 在 A1 首个请求期间中断，暂停而非进行中

- 目标 / 指标：验证 `STRATA_PF_WMMA=1` 能否让 96K 冷读的 engine read 与 wall 均改善 ≥3%，
  同时 A2 回到 A1 的 3% 内，并通过 parity、needle 与质量门。
- 假设与预期：P5 显示 `gemm gate/up` 是最大 phase；若 FP16 tensor-core prompt experts 在该
  形状更有效，完整请求应出现超过 3% 的同 boot 改善。
- 为什么选这条路线：P6 仅改善约 2.3%，继续调 attention 的收益不足；P7 是现有二进制中的
  env-only 开关，回滚成本低，并直接针对最大 GEMM phase。
- 改动与实验：预先写入判据并建立 `/opt/ai/tmp/pre-P7-pf-wmma-20261026T065341Z` 快照，随后
  启动 A1。用户在该臂首个请求期间中断，未产生有效 A1 JSONL/summary，B 与 A2 未执行。
- 结果：没有可比较的 engine read、wall、parity、needle 或质量结果；因此没有采纳/否决判决。
- 判决：**暂停/待做**。P7 不是进行中，也不是部分通过。
- 预期被推翻：没有；本次只形成“执行被中断”的事实，不能推断开关速度或正确性。
- 生产状态与回滚：只读核对 `strata.service` active、health 200，生产配置已恢复，未发现残余
  soak 或 P7 输出；P7 前快照保留。
- 证据：L-087；预注册判据 sha256
  `1ae40a8142a7a056e5d36c03902533c3119b896297f5510e4de0ca822ea650b6`。
- 学到什么：中断的 A1 不能作为部分样本复用；恢复实验必须重新建立干净的 A 基线。
- 下一步与理由：当前不自动恢复 P7。用户明确恢复目标后，从全新 A1 重跑完整同 boot A-B-A；
  在此之前只维护文档、记录和生产基线。

## I-20261026-19 · P6 Turing attention v2 只快约 2.3%，未过 3% 门槛

- 目标 / 指标：在同 boot A-B-A 中验证 `STRATA_PA_V2T=1` 是否能把 96K 冷读的 engine read 和
  request wall 都改善至少 3%，同时 A2 回到 A1 的 3% 内且正确性通过。
- 假设与预期：P5 的 `qsa attn` 中位为 3.422 s（10.5%）；替换 Turing 的 prompt-attention
  v1 暂存路径若有效，整段 96K wall 应出现可跨过噪声地板的改善。
- 为什么选这条路线：它是现有二进制中的 env-only 开关，只影响目标 phase，回滚成本低；
  比继续修改 host staging 或 lead-in 更贴近 P5 证据。
- 改动与实验：预注册 3 臂、每臂 3 个唯一 96K prompt，B 只增加 `STRATA_PA_V2T=1`。切换后从
  `/proc` 和 T-probe 证明运行环境，A1/B/A2 各跑同一顺序生成器；B 与 A2 运行后恢复生产。
- 结果：A1/B/A2 的 engine read 中位为 `37.3342/36.4583/37.5456 s`，wall 中位为
  `37.609/36.745/37.856 s`。B 相对 A1 分别为 `−2.346%/−2.297%`，相对 A1/A2 合并中位为
  `−2.622%/−2.617%`；A2 相对 A1 只漂移 `+0.566%/+0.657%`。三臂均 0 错误、needle 3/3，
  greedy parity 文本 sha 都是 `b17bc92de6ba8a97`。
- 判决：**未定，生产不采纳**。方向和正确性都正常，但未达到预先写下的 3% 改善门槛；
  没有足够证据把它写成稳定生产收益。
- 预期被推翻：即使 parity 完全一致，2.3% 的方向性收益仍可能是平台内波动；固定阈值必须
  在结果出来前保留，不能因为“看着更快”事后放宽。
- 生产状态与回滚：已恢复原配置并验证 T-probe `problems=[]`；engine `a09d78b1`、server
  `de09cffd`、launcher `34271059`、config `05c9d253`，health 200、`loaded=true`。
- 证据：L-086；预注册判据、三臂 JSONL/summary、三份 parity、B/A2/最终 T-probe。
- 学到什么：低风险开关能给出方向性信息，但不到 3% 就不应进入生产；下一步应直接瞄准 P5 中
  最大的 `gemm gate/up`，而不是在同一 1 s 范围内继续调 attention。
- 下一步与理由：P7 测试 `STRATA_PF_WMMA=1`，用相同 96K A-B-A 判断 FP16 tensor-core prompt
  experts 是否能触及最大 GEMM phase；若输出变化则必须追加质量门。

## I-20261026-18 · P5 把 96K 冷读定位到 GPU 计算，host staging 不是主瓶颈

- 目标 / 指标：解释 96K 冷 prefill 的约 38–42 s 墙钟，量化 21 个 phase、host staging 和 timing
  工装开销；要求 3 个唯一 prompt、零执行错误、needle 全对，并在恢复后保持 T-probe 无问题。
- 假设与预期：如果 host staging 是主瓶颈，phase 之外应有大量拷贝或等待；否则 GPU event timeline
  应占据大部分墙钟，成本集中在 attention、专家 GEMM 与固定 step。
- 为什么选这条路线：P4 已解决中短请求公平性，但 96K 冷读本身仍约 42 s；不先区分 host 与 GPU，
  继续调 staging/lead-in 只会重复已经否决的窗口实验。
- 改动与实验：临时启用 `STRATA_PREFILL_TIMING=1`，用唯一 96K prompt 跑 timing/plain A-B；旧
  `cold-prefill.py --96K` 生成 312,025 tokens 被 HTTP 400 拒绝，未计入结果，正式数据改用
  `mixed-soak.task_long`。恢复原配置后再跑 3 个 plain prompt 和 T-probe。
- 结果：timing 的 GPU timeline 中位 `32.653 s`、phase 总墙钟 `35.441 s`、host staging
  `0.341 s`；最大 phase 为 `gemm gate/up` 5.807 s（17.8%）、`embed+steps` 3.692 s（11.4%）、
  `qsa attn` 3.422 s（10.5%）、`gemm down` 3.031 s（9.3%）。timing/plain 的 engine read
  中位为 37.841/37.571 s，工装 `+0.72%`；两臂 3/3 needle 正确、零执行错误。
- 判决：**P5 结论成立**；96K 主成本在 GPU 计算，host staging 不是当前长读主瓶颈。timing 保留为
  诊断工装，不进入生产默认。
- 预期被推翻：原计划担心 timing 本身可能带来 2.5–8% 开销；同形状对照只有 `+0.72%`，在 3%
  噪声内，足以用于相位排序。
- 生产状态与回滚：已恢复生产配置；T-probe `problems=[]`，engine `a09d78b1`、server
  `de09cffd`、launcher `34271059`、config `05c9d253`，health 200、`loaded=true`。
  P5 前快照为 `/opt/ai/tmp/pre-P5-timing-20261026T061726Z/`。
- 证据：L-085；P5 timing JSONL/summary、plain JSONL 与恢复 T-probe，哈希见台账。
- 学到什么：96K 的固定成本不是单一“读写慢”，而是多个 GPU phase 叠加；优化必须命中真实 phase，
  并同时观察 wall、质量与恢复证据。
- 下一步与理由：启动 P6，只开 `STRATA_PA_V2T=1`，用同 boot A-B-A 验证 Turing prompt-attention
  v2 寄存器暂存能否降低 96K 的 `qsa attn` 成本；它是现有二进制 env-only 开关，回滚成本低。

## I-20261026-17 · P4 严格 A-B-A 通过：代码请求降 87.7%，长档代价低于 3%

- 目标 / 指标：在新引擎同一 boot 上确认 `1600 ms lead-in + STRATA_ADMIT_SHORT_FIRST=2048`
  是否真实改善 96K 邻居下的中等请求，同时让代码 ≤10 s、短 JSON ≤4 s，并守住 96K wave 与
  by_kind 单请求中位相对 A 臂均 ≤+3%。
- 假设与预期：A 臂没有短请求优先时，代码请求会被 96K 冷读压到约 41 s；B 臂应复制历史
  4–6 s 结果。代价是 96K 略慢，但必须同时低于 3% 的 wave 和单请求门限。
- 为什么选这条路线：新引擎自身 soak 与 staged 2048 已通过，但历史 P4 数据来自旧引擎；
  不做同一 boot 的 400 ms 无障碍对照，就无法把收益归因给 P4 而不是版本或 boot 噪声。
- 改动与实验：在 `/opt/ai/tmp/pre-P4-strict-20261026T054515Z` 建完整快照，固定判据文件
  sha256 `c3a208a6f37ba957153577159b360be1b0e33a792e932a67efba50f4caf56663`。
  A1/A2 移除短请求优先 drop-in，设 lead-in 400 ms；B1 恢复 `STRATA_ADMIT_SHORT_FIRST=2048`
  并设 lead-in 1600 ms；三臂都保持 staged 2048，各跑 5 个 fairness wave。每次切换都从
  `/proc` 核对实际 env、engine md5 和 health。
- 结果：

| 臂 | 代码 wall | 短 JSON wall | 96K by_kind | 96K wave |
|---|---:|---:|---:|---:|
| A1 | 41.173 s | 3.418 s | 41.009 s | 41.176 s |
| B1 | **5.074 s** | **3.980 s** | 42.092 s | 42.098 s |
| A2 | 41.202 s | 3.396 s | 40.913 s | 41.206 s |
| A 合并 / B 变化 | 41.1875 s / **−87.681%** | 3.407 s / +16.818% | 40.961 s / **+2.761%** | 41.191 s / **+2.202%** |

  三臂各 20 请求、5 波，HTTP/执行错误 0、意外 `length` 0、needle 失败 0。
- 判决：**采纳 P4 组合**。代码收益巨大，短 JSON 未越过 4 s，96K 两种口径都未越过 3%；
  但 96K 余量约 0.24 个百分点、短 JSON 余量约 20 ms，属于“通过但不应继续加码”的配置。
- 预期被推翻：短 JSON 并非继续下降，而是从 A 的 3.407 s 升到 3.980 s；它仍满足绝对门槛，
  但说明 P4 用短 JSON 尾延迟余量换了中请求完成时间，不能写成所有请求都加速。
- 生产状态与回滚：已恢复 B 配置；T-probe `problems=[]`，engine `a09d78b1` / `0.1.51`、
  staged 2048、lead-in 1600、短请求优先 2048、health 200。回滚快照
  `/opt/ai/tmp/pre-P4-strict-20261026T054515Z/` 的 6 个文件清单全部 OK。
- 证据：L-084；三臂 JSONL/summary 与最终 T-probe，哈希见台账。
- 学到什么：公平性组合的价值必须用“无障碍 A 臂”直接证明；只看候选自身绝对延迟会把
  boot 基线缺失误当成机制收益。配置已经接近两个门槛，后续变更需要更保守。
- 下一步与理由：结束 P4 参数线，进入任务 8 的发现轮。汇总本次 soak/fairness 的 queue、TTFT、
  decode、wall、expert cache 和 VRAM headroom，列出前三项用户可感知成本后只选一个实验；
  不重复已经否决的 QSA、groups=1、固定 read piece、S0b 或更长 lead-in。

## I-20261026-16 · 修复版 `0.1.51` 通过自身一小时 soak，P4 组合仍需严格归因

- 目标 / 指标：验证实际部署的修复版 `0.1.51` 能否在 1600 ms lead-in、staged 2048 和短请求
  优先 2048 的组合下连续运行一小时；要求零 HTTP/执行错误、零意外 `length`、needle 全对、
  swap 未使用，并且 RSS 与可用内存最终进入平台而不是持续恶化。
- 假设与预期：`a09d78b1` 已通过测试、输出 parity 和五臂 A-B-A；在真实 2/3/4 路混合流量下，
  它应保持与旧基线相同的稳定性，同时让 staged 读取和短/中请求公平性开关实际进入运行路径。
- 为什么选这条路线：旧引擎和旧 P4 候选的 soak 不能证明新二进制；部署后如果缺少自身 soak，
  就无法把生产基线与候选调度开关安全分开。
- 改动与实验：原子部署 engine `a09d78b1`，保持 server `de09cffd`、launcher `34271059`、
  config `05c9d253`；运行态启用 `STRATA_MP_STAGED_READ_MIN_TOKENS=2048`、
  `STRATA_PIPED_DECODE_LEADIN_MS=1600` 与 `STRATA_ADMIT_SHORT_FIRST=2048`。执行 3600 秒
  `mixed-soak.py`，随后跑 post-soak health 与 T-probe。第一次 T-probe 正确发现远端期望 profile
  仍是 4096；只同步这份无密钥 profile 后重跑，未放宽探针。
- 结果：618 请求、206 waves，HTTP/执行错误 0，意外 `finish_reason=length` 0，needle 失败 0；
  finish 为 530 个 `stop`、88 个 `tool_calls`。357 个资源样本中最大 running 与 outside slots
  都是 4。RSS 从启动期 `38.612` GiB 增至末值 `51.706` GiB，后三段中位约
  `51.33/51.65/51.81` GiB，最大值 `52.026` GiB 后回落；MemAvailable 末值 `7.477` GiB、
  最小值 `7.159` GiB，swap 全程 4096 MiB 未使用。分档 wall 中位：短 JSON `6.025 s`、
  1.85K 代码 `6.794 s`、32K/64K/96K `19.297/27.222/41.780 s`。
- 判决：**修复版引擎与 staged 2048 基线通过一小时稳定性门，可作为新生产基线**；
  P4 的 lead-in + 短请求优先组合仍是候选，必须在同一新引擎 boot 上补 400 ms 无障碍对照后
  才能写成无争议采纳。
- 预期被推翻：一小时混合 soak 的短 JSON 中位为 `6.025 s`，不能拿它替代已固定判据的 fairness
  arm 中约 4 秒结果；soak 证明稳定性，不自动证明单请求性能门槛。
- 生产状态与回滚：health 200、`loaded=true`；T-probe `problems=[]`，engine
  `a09d78b13c39378d1532a024b983d286`、version `0.1.51`，server
  `de09cffdac39ff150a689bd3a44cc242`、launcher `342710595d34936f40e40233f5b62837`、
  config `05c9d2539cd92b6c8e0b93ff8a48ce0e`。回滚快照仍为
  `/opt/ai/tmp/pre-v026-20261026T025109Z/`，清单已通过。
- 证据：L-083；`P4-newengine-soak2048-20261026T044100Z.*`、
  `T-probe-v026-postsoak-synced-20261026T054252Z.json` 与 profile 备份。
- 学到什么：启动后资源会先进入工作集，随后稳定在约 52 GiB RSS；判断泄漏必须看后段趋势，
  不能只看首尾差。部署门禁也需要把“运行态正确”和“期望 profile 正确”分别验证。
- 下一步与理由：同 boot 比较 A=`400 ms`、不启用短请求优先，B=`1600 ms` + 2048 短请求优先，
  两臂都保持 staged 2048；预注册代码 ≤10 s、短 JSON ≤4 s、96K wave 与单请求中位均不退 >3%。
  只有 A-B-A 通过才正式收口 P4，否则恢复 A 并保留已通过的引擎/阈值基线。

## I-20261026-15 · sm_75 合并引擎通过测试、正确性和五臂 A-B-A，进入部署与独立 soak

- 目标 / 指标：把已完成源码合并的 `0.1.51` 从“构建成功”推进到可部署候选；测试只允许外部资产缺失
  造成的显式例外，输出 parity、版本戳、运行路径和性能门必须通过。
- 假设与预期：先前 `0.1.51` 的吞吐回退来自具体 sm_75、TQ overlay 测试和构建脚本问题，不是整个
  合并版本不可用；修复后应恢复单路吞吐，并至少不伤 4 路聚合和 cold prefill。
- 为什么选这条路线：生产仍运行旧 `0.1.49.1`，继续在旧二进制上追 P4 会把结论锁在即将淘汰的基线上。
  先完成合并引擎门禁，才能把后续优化迁到维护中的源码。
- 改动与实验：修复 `pf_wmma_parity` 对 sm_75 共享内存容量的误判，查询真实
  `cudaDevAttrMaxSharedMemoryPerBlockOptin` 并拒绝不支持的 `WK=256` tile；修复 IQ tile 设置失败后
  污染 prompt 路径的 CUDA sticky error；为 `ggml_tq_overlay` 独立 CMake 脚本启用 `CMP0057`。
  构建后运行全量 CTest、server 离线测试、旧/新/旧/新/旧五臂 A-B-A、temperature 0 答案 parity
  和 high-effort reasoning 对照。
- 结果：CTest 为 `151 passed / 8 skipped / 1 failed`，唯一失败 `ple_parity` 是因为服务器缺少 Q2_0 PLE
  模型和捕获夹具，不是本轮代码失败；离线 server 为 `73 passed / 10 skipped`。五臂 A-B-A 合并中位：
  单路旧 `68.8`、新 `71.3`（`+3.6%`）；4 路旧 `130.4`、新 `130.0`（`-0.3%`）；
  cold prefill 约 `+0.4%`。temperature 0 的 answer hash 五次均为 `b17bc92de6ba8a97`；
  修复后引擎 md5 为 `a09d78b13c39378d1532a024b983d286`。
- 判决：**合并后 `0.1.51` 可部署候选通过测试、正确性与性能门禁**；`ple_parity` 与 8 个 skip
  作为外部资产/平台覆盖例外保留，不能写成“全测试绿”。先前把所有 0.1.51 吞吐回退归因于整个候选的
  结论撤回，具体历史回退仍需由本轮修复后的证据替代。
- 预期被推翻：high-effort reasoning hash 在旧引擎 A/A2 之间本身也会变化，因此不能作为该版本的
  正确性失败依据；temperature 0 答案 hash 和数值 parity 才是本轮可用的稳定门。
- 生产状态与回滚：本轮验证结束时仍恢复旧生产 `8d680c69`，health 200；随后再原子部署修复版。
  部署前快照 `/opt/ai/tmp/pre-v026-20261026T025109Z` 的 `MANIFEST.sha256` 全部通过。
- 证据：L-082；全量 CTest 日志、五臂 `arm-v026-*.log`、最终 A2 T-probe、答案 parity 输出。
- 学到什么：sm_75 的 opt-in shared memory 上限必须按设备查询，不能在启动时无条件为 `WK=256`
  设置 83 KiB；失败路径也要消费 CUDA error，否则会在后续 prompt 阶段制造看似无关的故障。
- 下一步与理由：先部署 md5 `a09d78b1` 并用运行路径 T-probe 证明版本，再在新基线上复测 P4
  1500/1550/1600 ms 与 staged 阈值，最后运行该二进制自己的 mixed soak。现有旧引擎/P4 历史 soak
  不能替代这一步。

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
