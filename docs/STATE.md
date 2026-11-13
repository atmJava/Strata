# 当前状态

> 2026-11-13 运行模式：**用户已重新授权本地代码与测试收敛；不推送、不部署、不重启服务**。
> 全局目标是把双 RTX 2080 Ti 上的默认路径做可靠：admission → reuse → decode。
> 当前进度、已完成提交、验证与未决项见
> [`work/DEFAULT_PATH_PROGRESS-20261113.md`](work/DEFAULT_PATH_PROGRESS-20261113.md)。

> 接手顺序：`RULES.md` → 本文件 → `ROADMAP.md` → `ITERATION_LOG.md` 最近一条。
> 完整证据在 `EVIDENCE_LEDGER.md`；候选和回滚在 `WORK_QUEUE.md`。

- 当前全局目标：**让默认路径本身成为适配路径**。优先消除 admission 等待和可复用却重读的
  prompt；decode 最后处理。代码与测试是主要工作，只有真实 GPU 行为无法由源码和 fake-engine
  测试区分时才启动 Strata。
- 本轮断点：分支 `docs/gpu-xid13-incident-20261112` 仅在本地领先；工作区干净，
  `stash@{0}` 必须保留。最近完成 admission 重入复用、staged-read 默认化、双路串行默认化、
  solo 回退常量化及 30 秒 admission fallback 常量化；conversation cache 在确认新快照能放下
  之前不再删除 superseded 旧副本，已完成的 staged read 按零待读 token 参与 admission。
- 未决默认值：`--conversation-cache-min-tokens 4096` 使实测 3,894-token 会话没有 parking。
  交接建议改为 2,048，但尚无新的五会话验证；这是 owner 决策，不把推测写进默认。
- 历史“优化已停止、仅维护稳定”说明保留在本文件后部，只作为 2026-11-01 至 2026-11-12
  的运行状态记录，不再约束当前代码工作。
- 代码树：`freeze/server-adapt-0.2.7` 已由 `331f30bf`（2026-10-27）收敛为 sm_75 CLI +
  server 发行树，删除 `4,091` 个文件、约 `994,000` 行。其后 2026-11-04..07 的 32 个提交
  清理了裁剪残留（死导入/未用变量、指向已删文件的引用、测试与文档标注），末提交
  `ad297d35` 已推送到 `origin`。
- 清理门禁已复核：2026-11-07 在本机（无 GPU、无 `nvcc`）`pytest tools` 为
  `402 passed / 51 skipped / 0 errors`、`52.62 s`；`pytest serve tests/terminal
  tools/server/tests` 为 `1,457 passed / 64 skipped / 10,910 subtests`、`514.49 s`，两轮
  均 exit 0。详见 [清理门禁结果](work/FREEZE_027_CLEANUP_GATE_RESULT-20261107.md)。
  CUDA 构建、`ctest` 与真实服务器 smoke 未在本机验证。本轮已从 `97925527` 的独立归档在远端通过 CUDA
  13.0.88、`sm_75`、Release 组装构建：退出码 0，产物报告 `strata 0.1.51`，SHA-256
  `6cdf18f1...`。该验证只覆盖组装和入口，不包含 `ctest`、CUDA runtime smoke 或部署。
- 未完成采纳项：21
- 当前项：AGENT-CURRENT-PROD-COST-REMAP（暂停，无主动执行）。模型/槽数比较及所有新
  候选均停止；未完成采纳项只表示历史队列，不授权继续执行。
- 上一项完成：`AGENT-PROMPT-STAGING-CONCURRENT` 已采纳。冻结约 154K cold read 下，
  两个 decode 邻居最长静默 `82.059→1.713 s`，整场景 wall `104.843→83.118 s`
  （`20.721%`），joiner TTFT `84.058→72.384 s`；4 路 5 波 `14.0→13.1 s`。
  真实日志出现独立读线程且三轮分别 decode `1,795/1,552/1,554` 行。
- 前一项完成：`AGENT-BATCH-PREFILL-CPU-SHARE-GUARD` 已采纳为默认关闭、生产 opt-in。
  生产选项 engine `e3d25b38...` 的 B0/B1/B0r 判决 `pass`，控制漂移最大 `1.53%`；
  1 路 hot-short/hot-long wall 改善 `11.825%/10.991%`，2 路
  `7.896%/8.056%`，全部 `3/3` 波胜。4 路同源码隔离证据仍为
  `1.341%/8.474%/1.453%`；生产二进制未单跑 4 路。
- 质量门已过：fixture schema 5 修正最终消息歧义后，B0/B1 均 `7/7`，比较器 `pass`，
  零身份不一致、零候选回归、零共享失败。旧 B1 `5/7` 与首轮 `6/7` 分别是 XML tool-call
  重复采样波动和最终消息摘要格式歧义，不再作为质量回退证据。
- 当前断点：冷长 prompt 冻结邻居的生产 gate 已修复。此前 `STRATA_PIPED_CONCURRENT=1`
  被 `experts.bin + STRATA_IO_PREFETCH` 的粗粒度 staged-source 判断挡住；现在只有
  已证明 prompt copy 独立的 page-fill `experts.bin` source 才放行，其他 staged/direct/
  GGUF role assembly/helper source 仍关闭。若 JSON 等轻量输出出现错误，仍优先于继续提速。
- HC_SPLIT smoke：1/2 路各 3 波、共 27 请求，零 HTTP/执行错误、零外来请求、期望回复
  `27/27`、finish 全 `stop`。相对旧生产 B1 的六种形状变化为
  `+0.048%/+0.897%/+0.163%/+0.889%/+0.124%/-0.057%`（正数表示更快），都在约 1% 噪声内。
  两卡日志均确认 split 路径真实运行并逐位自检。该结果只证明路径可达、短 smoke 正确且
  性能未明显退化，不证明低频 Xid 已修复。
- 测试方法已修正：`probe-dsh-session-cost.py` 过去只统计成功的 `assistant/message`，
  会把上述 5 步失败会话报成 4 步。现在 `assistant/attempt` 也进入 step/model wall，并只
  保留 `failed`、finish kind 和 error code，不保留推理或错误正文；专项 `9 passed`。
- 已停止项：`AGENT-CURRENT-PROD-COST-REMAP`。最新长输出会话 `task_wall_s=202.486 s`，
  单个 `15,036` output-token step 占 `173.323 s`，模型总计 `96.799%`；这确认长输出
  decode 是后续性能大头，但在 GPU 用户可见失败收敛前不继续调它。
- 标题方向已关闭：end-of-turn 会让 DSH 在最终主回复后取消标题客户端，真实代理记录
  `BrokenPipeError/502`；该轮 task wall `9.598 s`、runner wall `11.628 s`。
  128/64 output cap 仍跨两个主 step；固定前缀连续两次 `cached=0`。详见
  [标题调度结果](work/DSH_TITLE_SCHEDULING_RESULT-20261101.md)。
- 已采纳的标题隔离只等当前主请求：生产单会话主 turn `19.629→16.078 s`
  （`18.091%`），双会话 fail-open。
- 已采纳的 DSH 首轮前缀复用：干净 boot 顺序 A→B 的稳态 task 中位
  `11.3715→7.3165 s`（`35.66%`、`4.055 s`）；首步 cached `582→5,463`。
- 当前成本：真实 DSH 模型 step 占 `93.202%`，工具并集 `5.312%`。普通 step 首 token
  只比 engine prompt 多约 `25–26 ms`；6K/94K/189K host prepare 中位
  `6.439/11.527/18.345 ms`，host 路径已判 null。
- 质量：DSH Web 走 Qwen/Strata relay；6 个基础 fixture 代码/测试 `6/6`，明确最终消息
  契约后严格 sentinel `3/6→6/6`。Codex CPU guard 在 schema 5 下 B0/B1 均 `7/7`；
  fixture 现在要求最终消息只含 `RESULT=PASS`，消除“摘要后附 sentinel”的误判。
- GPU 抢占项：`GPU-XID13-STABILITY`。DSH 最终请求曾在 GPU0 触发 8×Xid 13 + Xid 43；
  约 60 次重放未复现。固定 prompt 不是硬件稳定证明。每个真实窗口从 UTC 起点统计新增
  NVRM Xid；任何新 Xid 立即抢占。
- 长上下文：真实 49 天 223K 旧会话 cached `0`、TTFT `113.014 s`，是低频 p99；
  210K compaction 和当前 Codex compaction 已高度缓存。compaction 后立即回读旧分支在
  109 次中触发 0 次，只保留低频诊断。
- 硬件：两张 RTX 2080 Ti 的算力解锁已固定；cuBLAS FP16
  `62.40→93.69/94.07 TFLOPS`，当前 boot `unlocked=1`。不重新研究或修改解锁。
- 运行状态：自 2026-11-01 起生产由已启用的 `strata.service` 管理，不再依赖登录会话中的
  手动实例。service 使用 `Restart=always`、`RestartSec=15`、无限启动重试和
  `KillSignal=SIGTERM`；稳定 launcher 为
  `/opt/ai/bin/run-strata-production.sh`，稳定 config 为
  `/opt/ai/etc/strata-production.json`（SHA-256 `d76772e7...`，由 A1 config 只改稳定
  engine 日志路径）。当前 engine `e1208e51...`、稳定 config `d76772e7...`、server
  `bd3e4289...`；server cwd
  `/opt/ai/src/strata-two-stream-serial-20261031`，端口 `8080`，health loaded、
  262144、6 slots / 2 groups。最终验证时 service/engine PID `41769/41789`。
  普通 `systemctl restart` 通过；受控 `SIGKILL` 后 systemd 自动生成新 PID，
  `NRestarts=1`，随后再次 ready。A1 回滚身份仍是
  server/vision/engine `254299/254303/254308`，engine `105a4171...`、config
  `6ece1a13...`。生产 server 实测 env 为
  `STRATA_ADMIT_SHORT_FIRST=2048`、`STRATA_DEFER_TITLE_REQUESTS=1`、
  `STRATA_MP_STAGED_READ_MIN_TOKENS=2048`、`STRATA_PIN_DSH_TOOL_PREFIX=1`。
  engine env 另有 `STRATA_BATCH_SERIAL_TWO=1`、三个 CPU-share 生产开关和
  `STRATA_HC_SPLIT=1`；`STRATA_PIPED_CONCURRENT=1` 现已真实进入独立读线程路径。
  没有 DEBUG。
- 本机到生产：`127.0.0.1:18080 -> 127.0.0.1:8080`，SSH relay 端口 `8686`。
  不改用户 DSH profile；实验使用显式 overlay/proxy。
- 主要验收门：真实触发；1 路不退化，2 路报告第二 TTFT/跨度，4 路不回退 >1%，
  6/8 零错误和资源安全；输出、finish、工具、修改和测试全过；默认路径可回滚；
  跨 boot 或持久 checkpoint 污染必须单列。
- 测试口径：主指标是 DSH `task_wall_s`；runner wall 包含 turn 后辅助工作，只作次指标。
  辅助请求必须记录到达、释放、上游开始/完成、turn 结束和客户端断开；不能只看 task wall。
  路径审计、机制增量与生产回归分开；性能主比较必须同源码、同构建选项、同 config，旧生产
  臂不能承担候选内部增量的归因。热轮比较器必须参数化臂名和 `1% + 绝对秒数`门，要求
  三臂记录的全部波齐全、候选至少赢三分之二波，并同时检查 1/2 路 cold 与 hot 非回退；
  不能用历史脚本写死的 `A64/B0/A64r` 或 `0.15 s` 代替当前契约。
- 质量配对必须保存并核对 runner、模型、reasoning、模型目录 SHA-256、fixture suite
  SHA-256、prompt suffix SHA-256 和 provider URL。比较器把候选从通过变失败判为回归，
  把双方共享失败判为 `blocked_shared_failures`；后者不能进入性能矩阵或生产。
- 当前文档：旧总计划已归档为
  [`2026-10-31-agent-workload-throughput.md`](archive/2026-10-31-agent-workload-throughput.md)；
  本文件是当前运行状态的权威入口。旧平台全局目标仍为 paused，接口不能改写正文。
