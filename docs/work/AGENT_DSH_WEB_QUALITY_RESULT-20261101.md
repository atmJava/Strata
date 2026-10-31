# DSH 实际 provider 路径与严格回复契约

日期：2026-11-01

状态：Provider/profile 混淆已排除；同一 Qwen/Strata relay 路径的 6 个基础 coding fixture
从严格 `3/6` 提升到 `6/6`，全部代码和测试保持正确。修复是调用方明确最终回复契约，不是
引擎、GPU、量化或 DSH 本地配置修改。

## 为什么旧对照不可用

只读执行 `dsh --profile web --dump-config` 和 `dsh --profile headless --dump-config` 得到：

| Profile | provider | model | endpoint |
|---|---|---|---|
| 用户正在运行的 `web` | `atm` | `qwen3.8-flash-next-iq3_xxs-abliterated` | `http://129.28.27.137:8888/v1` |
| 旧 benchmark 使用的 `headless` | `deepseek-official` | `deepseek-flash` | DeepSeek 官方 |

因此，之前把 `dsh headless` 的 `6/6` 写成当前 DSH Web/Strata 对照是错的：runner profile、
provider、model 和 hosting 全部不同。该历史结果只能作为另一条终端的参考，不能归因给
当前 Qwen/Strata 路径。

本轮没有修改用户的 DSH profile。新增
`tools/server/dsh-strata-headless.patch.yml`，通过 DSH 的 `--patch` 启动参数在临时
benchmark 进程上把 `headless` 的 provider/model 对齐到用户的 `web` 路径。
`bench-agent-quality.py --dsh-patch` 显式记录该 overlay，并把
`runner_profile=dsh-headless` 与 model label 写入报告。

## 测试方法

- 7 个 Schema 3 fixture：6 个基础 coding 任务和 1 个 `apply_patch` 专用任务。
- DSH `headless`、Qwen3.8-Flash-Next IQ3_XXS、`high -> xhigh`、Strata relay。
- 每个 fixture 从新 Git 仓库开始；测试文件哈希必须不变。
- 代码质量以测试结果判定，最终质量门额外要求回复严格等于 `RESULT=PASS`。
- 失败不按模型/provider 猜测；同时记录工具调用、测试、最终回复、事件错误和 wall。
- 测试窗口前后读取新增 NVRM Xid；本次窗口为
  `2026-10-31T18:08:22Z` 之后，新增 Xid `0`。

## 当前契约基线

生产候选前后的完整测试：

| 指标 | 结果 |
|---|---:|
| 基础 coding 测试 | `6/6` 通过 |
| 受保护测试未改 | `6/6` |
| 基础严格 sentinel | `3/6` |
| 全部 7 项测试通过 | `7/7` |
| 全部 7 项严格质量 | `3/7` |
| 事件错误 / 工具错误 | `0 / 0` |
| wall 合计 | `243.865 s` |

三个基础失败（`single-file`、`diagnose-retry`、`transaction-rollback`）都完成了正确
修改并运行测试，但最终消息先写解释，再追加 `RESULT=PASS`。这不是“没有检查”，也不能
归咎 GPU。

`apply-patch` fixture 的代码和测试正确，最终回复也严格，但 DSH 的工具面提供 `edit/write`
而不是 `apply_patch`，所以按 Codex 专用工具门判失败。该结果不能说明 DSH 未编辑文件；
DSH 的后续质量统计应把 6 个基础 fixture 和 Codex `apply_patch` 协议分开。

## 严格回复契约

在同一基础 task 后追加以下测试后缀：

```text
FINAL RESPONSE CONTRACT: After all tool calls and tests are complete, your final assistant message must contain exactly the single line `RESULT=PASS` and nothing else. Do not include a summary, explanation, markdown, or progress report in that final message.
```

后缀 SHA-256：
`fd794e1a9d678526c14c8ef66560309f713c4ffce50e5d87b81615643f8ff321`。

先在 `transaction-rollback` 同一 fixture 连续重复五次：

| 臂 | 严格通过 | 测试通过 | 工具错误 |
|---|---:|---:|---:|
| 当前 prompt | `4/5` | `5/5` | `0` |
| 明确最终回复契约 | `5/5` | `5/5` | `0` |

随后跑完整集合：

| 指标 | 结果 |
|---|---:|
| 基础 coding 测试 | `6/6` 通过 |
| 受保护测试未改 | `6/6` |
| 基础严格 sentinel | **`6/6`** |
| 全部 7 项测试通过 | `7/7` |
| 全部 7 项严格质量 | `6/7` |
| 事件错误 / 工具错误 | `0 / 0` |
| wall 合计 | `212.823 s` |

唯一剩余失败仍是 DSH 不提供 `apply_patch`。完整测试窗口开始于
`2026-10-31T18:18:43Z`，之后新增 NVRM Xid 为 `0`。

## 判决

**通过：严格最终消息契约对当前 Qwen/Strata DSH 路径有效。** 它解决的是指令遵循，不是
代码能力；不能在客户端静默改写模型文本冒充模型服从。真实使用时应由调用方或 profile
instruction 明确“最终消息只能是什么”，而不是只笼统要求“回复 RESULT=PASS”。

未采纳的项：

- 不修改用户本地 DSH profile；overlay 只用于显式 benchmark。
- 不把 DSH 的 `edit/write` 冒充 `apply_patch`；工具协议必须按 runner 真实声明。
- 不把一次 wall 变化归因为性能收益；本轮 wall 只证明测试总耗时，没有配对的性能契约。
- 不把无新增 Xid 写成 GPU 已修复；GPU-XID13-STABILITY 仍是抢占风险。

## 证据

- 当前契约：
  `/opt/ai/logs/strata-agent-quality-webpath-full-20261101.json`
  SHA-256 `98fa3b38fb600180e7e2d4824dd23cdf5b483e54080f203a767005feae2f5b2d`
- 严格契约：
  `/opt/ai/logs/strata-agent-quality-webpath-contract-20261101.json`
  SHA-256 `e03686d0c24c31df88fc29d050ac60f91b6a9fdde5e8ac32107d45d6968f8c86`
- 连续复测：`/opt/ai/logs/strata-strict-{base,candidate}-{1..5}-20261101.json`
- GPU 窗口：
  `/opt/ai/logs/gpu-stability-after-dsh-quality-20261101.json` 与
  `/opt/ai/logs/gpu-stability-after-strict-contract-20261101.json`

## 没测什么

- 没有自动驱动真实 `dsh web` UI；headless overlay 对齐了 provider/model，但工具 preset
  仍可能与 web 不完全相同。
- 没有在复杂多文件任务上重复五次；连续五次只覆盖 `transaction-rollback`，完整集合各一次。
- 没有证明 wall 改善；性能需要单独配对实验。
- 没有修改 DSH profile，也没有解决 Codex 专用 `apply_patch` 在 DSH 工具面上的语义差异。
