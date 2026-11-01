# GPU Xid 13 incident and stability contract

日期：2026-11-01

状态：偶发 GPU 基础设施故障已确认；固定请求不能复现；根因仍是
“高度怀疑改装卡显存时序/板级稳定性”，不是已证实的芯片损坏，也不是已完成的修复。

## 触发

用户通过 DSH 执行“商户端登录过期配置检查”。最后一次 Responses 流式请求在已经产生
`76` 个 reasoning token 后失败：

```text
server_error: verify: layer 11 never rang (an illegal instruction was encountered)
```

请求形状：

- Qwen3.8-Flash-Next IQ3_XXS；
- OpenAI Responses，`stream=true`；
- `input_tokens=15,419`；
- 31 个工具、24 个输入项；
- `max_output_tokens=32,000`；
- DSH `high` 正确映射到 Strata `xhigh`。

这不是 DSH 的答案检查。Strata 的验证窗口在等待一个前置 CUDA kernel 时发现它已失败。

完整 DSH v4 会话后来重新核算为 5 个 step、`task_wall_s=36.31 s`。前 4 个成功步骤的
模型墙合计 `32.853 s`；第 5 步从开始到 error 结束共 `2.296 s`，其中首个流事件
`1.190 s` 后仍因 GPU 错误终止。此 turn 最后的 reason 是 error，不是普通 finish。
此前的会话探针只读取成功的 `assistant/message`，会漏掉这个 `assistant/attempt`，现已
修复并加回归测试。

## GPU 证据

同一秒内核记录了 8 条 Xid 13 和 1 条 Xid 43：

```text
2026-10-31T17:27:28+00:00 PCI 0000:03:00
Xid 13: Graphics SM Warp Exception / Illegal Instruction Encoding
ESR ... 0x50c728=0x4c1eb72 ...
Xid 43: pid=172641, name=strata
```

`0000:03:00` 是 GPU 0。生产日志在
`/opt/ai/logs/agent-resident-path-20261031T2300Z/A1-restored-server.log:2234`
记录同一个 `verify: layer 11 never rang`，引擎随后退出并由 server 自动重启。

统一探针生成的当前证据：

- `/opt/ai/logs/gpu-stability-current-20261101.json`
- 当前观测：9 条 Xid 行，合并为 1 次同时间/同设备事故；
- Xid 13 `8` 条、Xid 43 `1` 条；
- 当前无 `GPU Reset Required`；
- 本次事故后没有新的 Xid。

## 精确重放

从 DSH 会话导出重建了完整请求，并确认服务端也计为 `input_tokens=15,419`。

已执行：

- 首次精确重放；
- 20 次连续最终请求；
- 6 路并发精确最终请求；
- 3 次完整五回合会话；
- 标题请求与主任务并发。

合计约 60 个流式请求，其中 30 个最终回答请求全部 HTTP 200；所有会话正常结束。
重放后 health `loaded=true`、6 slots / 2 groups，没有新 Xid。GPU 空闲温度约
`49/39 °C`，显存占用约 `21.2/21.1 GiB`。

因此，当前证据排除：

- 这个固定 prompt、工具定义或 reasoning 内容必然触发；
- DSH 请求结构必然触发；
- DSH 降低了 reasoning effort；
- 单路或 6 路并发必然触发；
- 普通业务逻辑/会话检查本身就是根因。

不能排除低频竞态、特定 GPU 微架构路径或板级/显存时序异常。

## 同一设备的历史故障

同一张 GPU 0 在 2026-09-25 曾记录不同软件栈下的 Xid 62/154，并由驱动标记
`GPU Reset Required`：

- `/opt/ai/logs/incident-gpu0-xid62-20260925-054029.txt`
- 当时是 vLLM + `595.91.07`；
- 本次是 Strata + `595.71.05`。

两次故障的软件栈和 Xid code 不同，不能据此证明同一根因，但说明固定
`595.71.05` 并不是“不会再出现 GPU fault”的保证。原有恢复文档把 `595.91.07`
直接写成 Xid 62/154 的唯一原因，表述过强，已修正。

上游 issue <https://github.com/Niko1221/Strata/issues/4190> 报告了相同
RTX 2080 Ti 改装 22 GB、相同 ESR `0x4c1eb72`、大量 Xid 13，并指出：

- 换驱动、换 Strata 版本和大量配置仍可触发；
- 干净 llama.cpp 也能触发；
- ESR 值不是单一 kernel 指纹；同一字段在 Illegal Instruction、Out Of Range
  Register/Address 和 MMU Fault 下都能出现；
- VRAM 批量读写、计算压力和 CUDA Graph 压测可以干净，不能排除持续负载下的时序余量；
- 另一台同型号改装板运行 74 个 26.5K–47.2K token 请求、两个引擎版本、相同 flags，
  为 0 非 200 / 0 Xid；
- issue 最终按 GDDR6 时序余量/板级问题关闭。报告者尝试了多个 offset，但各次使用的
  请求长度和压力并不相同，因此下表不是可直接横比的单调剂量曲线：

| 显存 offset | 报告结果（负载不同，不可横比） |
|---|---|
| `-400 MHz` | 约第 7 个短请求 |
| `-600 MHz` | 10 个请求中 6 个 |
| `-800 MHz` | 第 1 个 256-token 请求 |
| `-1000 MHz` | 第 1 个 1.8K-token 长读 |
| `-1200 MHz` | 持续 soak |
| `-1400 MHz` + fan 100% | 94 个请求 / 约 780 s 无 Xid；同时改变了风扇条件 |

另一位报告者只需约 `-300 MHz` 就稳定，说明 offset 是单板参数，不能照搬。上表只证明
“显存时序余量可能相关”，不能用来给本机选择安全 offset，也不能把一次短 soak 当作修复。

这是支持“硬件/显存稳定性优先”的强外部证据，不是对当前设备损坏的最终诊断。

## 复盘：测试方法哪里错了

固定 prompt 重放适合回答“这个请求是否稳定触发代码错误”，不适合证明 GPU 是否稳定。
一次低频 Xid 可能在几十次普通请求后才出现，固定重放通过不能降低硬件故障优先级。

后续测试必须同时记录：

- 请求开始/结束时间、并发度、输入/output/reasoning 尺寸、finish 和 HTTP 状态；
- 每个请求期间的 GPU index、SM/memory clock、温度、功耗、VRAM 和 throttle reason；
- NVRM Xid code、PCI bus、时间相邻事故簇、PID/process 和是否需要 reset；
- server/engine PID、health、6 slots / 2 groups 和重启耗时；
- 测试前后的 `nvidia-smi` 与 kernel-log 游标。

会话成本探针必须把失败 `assistant/attempt` 计入 `steps` 和 `model_wall_s`，同时只保留
`failed`、finish kind 与 error code；不能保存 reasoning 或错误正文。否则“最后一步失败”
会被报成少一步，直接低估用户等待并掩盖 P0。

`NVRM Xid` 行数不等于事故数。一次设备异常可同时产生多条 Xid 13，再接一条 Xid 43；
汇总必须同时报告原始事件数、事故簇数和原始行。探针默认把同一 PCI 上 5 秒内的事件聚为
同一事故；这个窗口只用于关联，不用于推断根因。时间缺失的事件不互相合并。

## 下一轮合同

抢占项：`GPU-XID13-STABILITY`；没有新 Xid 时，主动流程继续执行质量对照，不空等。

第一阶段只读、无需重启：

1. 用 `tools/server/probe-gpu-stability.py --boot` 保存整个当前 boot 的 Xid 基线；
   `--boot` 不叠加默认的 24 小时窗口，避免长 boot 里的旧事故被静默漏掉；
2. 每个测试窗口记录 UTC 开始时间、client/runner、并发、请求形状、server/engine PID、
   health 和设备状态；正常真实使用中周期采样。每次出现 Xid 立即保存事故窗口，停止把
   同期性能数字作为加速证据；
3. 运行代表性 1/2 路混合负载时同步采集 GPU 状态；固定 prompt、单路、6 路并发和完整
   DSH 回合只用于不同代码路径，不作为硬件稳定性结论。窗口结束后用开始时间作 `--since`
   只读新增事件，避免把历史 Xid 混入本轮；
4. 当前已有用户可见 Xid 失败，下一次维护窗口先验证单变量
   `STRATA_HC_SPLIT=1` 是否绕开默认 staged cp.async 放大器；若只把 Xid 变成静默
   wedge，仍判失败。随后测试可精确回滚的显存频率降低，最后才考虑 GPU0 单卡隔离；
5. 不一次性关闭多个 Strata pipeline 开关。它们最多可能是放大器，没有证据支持当前就
   关闭；每次只改变一个变量。

稳定性判据：

- 一次新 Xid 立即判为失败，记录发生前至少 60 秒和发生后 60 秒的 workload/GPU 状态；
- 不允许把“连续跑通 60 个固定请求”写成问题已修复；
- 若以后出现重复 Xid，降频实验必须报告吞吐、延迟、质量、温度/功耗和回滚结果；
- 无 Xid 只表示该窗口未观测到故障。两次短窗口无 Xid、固定请求 soak 或单纯的短时
  94 请求样本都不能证明修复；
- 要声称“候选缓解有效”，必须有比基线更长、可审计的同口径曝光时间/负载混合和新区间
  Xid 计数，且回滚路径已演练；如果无法建立可复现压力或足够曝光，只能写“风险缓解候选”；
- 没有重复 fault 时，结论保持“低频、未复现、硬件风险保留”，不伪造修复。

## 用户可见诊断修正

`serve/server.py` 新增只针对明确 CUDA 执行错误的说明：

- 匹配 `never rang` + illegal instruction/memory access、GPU fell off the bus、uncorrectable ECC；
- 保留引擎原始错误；
- 明确这是 GPU 未完成请求，不是 prompt/request validation 错误；
- 提示保存时间并检查 `NVRM: Xid`，再在 engine 重启后重试。

该修改不改变 HTTP 错误类型、模型输出、控制流或自动重试行为。`serve/test_server.py` 全量
`352 passed / 510 subtests passed`；探针专项 `7 passed`。当前生产 server 尚未替换，
该诊断会在下一次必要重启时一并部署。

## 当前生产状态

- server / vision / engine：`172634 / 172636 / 193790`；
- engine 在 Xid 后已自动重启，当前 health loaded；
- 262144 context、6 slots / 2 groups、INT8 KV；
- `strata.service` 仍为 inactive；
- 本轮文档和探针修改未要求重启。

## 证据与工具

- 只读探针：`tools/server/probe-gpu-stability.py`
- 单测：`tools/server/tests/test_probe_gpu_stability.py`
- 部署副本 SHA-256：
  `d80a34fb686edf0639831c2e0423e6d3cff5bfcff09ee1ab7a25985fad51e5b9`
- 生产日志：
  `/opt/ai/logs/agent-resident-path-20261031T2300Z/A1-restored-server.log`
  SHA-256 `64e606bd89157647d99c3e7a8ba54b52a5e15aedadbc28bdb1a7afb7c242292c`
- 当前 GPU/Xid JSON：
  `/opt/ai/logs/gpu-stability-current-20261101.json`
- 历史 GPU0 事故：
  `/opt/ai/logs/incident-gpu0-xid62-20260925-054029.txt`
- DSH 原始会话：
  `/home/kali/下载/dsh-session-session-da011bbb-afae-4633-944c-01e0eb254800.zip`
  SHA-256 `237b04436f6738674b21f02305c454660ff71ac9d2272d979e188a6fc9d6cb06`
- 修复后成本报告：`task_wall_s=36.31 s`、`steps=5`、`model_wall_s=35.149 s`；
  `/tmp/dsh-da011bbb-cost-r2.json`
  SHA-256 `ae430af0fe1d6a0b3ba9a538161c685cea40d30ee8a21e199ee3a3432d4e0ab6`

## 没测什么

- 没有证明 GPU 已永久损坏；
- 没有证明某个 Strata kernel 是唯一根因；
- 没有制造出可重复 Xid；
- 没有测试显存降频、单卡隔离、换卡或换驱动；
- 没有运行数小时以上并捕获下一次自然发生的 Xid；
- 没有修改 DSH。
