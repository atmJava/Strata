# AGENT-CODEX-MODEL-CATALOG-QUALITY result

日期：2026-11-01

状态：目录供给已产品化；质量门仍因最终回复未严格收口而开放。

## 结论

观察到的一个确定性质量问题是 Codex 模型目录缺失。模型 slug
`qwen3.8-flash-next-iq3_xxs-abliterated` 不在目录时，Codex 请求只发送 9 个工具，
`apply_patch` 不在其中；加入兼容目录条目后，同一条请求发送 14 个工具，其中
`apply_patch` 的类型为 `custom`。这不是 GPU 内核或量化路径的证据。

生产服务日志
`/opt/ai/logs/agent-resident-path-20261031T2300Z/A1-restored-server.log`
（SHA-256 `c8b03dfb31e9de8d703f114ee9ac1f851ecdc6e580cb414f3e55c03036130d6b`）
在已观察窗口内有 6 条“请求未声明 `apply_patch`，模型写了该工具而退回文本”的记录。
该日志跨越目录修复前后的客户端请求，因此 6 是服务日志中的累计现象，不是修复后残留数。

## Schema 3 当前基线

Schema 3 使用真实声明工具，不再要求模型绕开 `apply_patch`。当前结果如下：

| runner / model | 基础严格 | 测试 | 测试字节 | wall |
|---|---:|---:|---:|---:|
| Codex + Qwen/Strata high | `5/6` | `6/6` | `6/6` | `159.777 s` |
| DSH `0.2.1-rc.3` + DeepSeek 直连 | `6/6` | `6/6` | `6/6` | `71.391 s` |

Qwen 唯一基础失败是 `transaction-rollback`：代码和测试正确，最终回复在解释后追加
`RESULT=PASS`，不满足严格 sentinel。`apply-patch` 独立重复 `3/3` 全部通过，wall 为
`17.499/16.598/11.840 s`，每次都真实调用 `apply_patch`。DSH 的 `6/6` 是 DeepSeek
直连参考，不能归因 Strata 引擎；串行 wall 也未随机化，不作通用速度结论。

目录工具已是产品命令：`strata codex catalog` 默认只检查，`--apply` 原子写入并先备份，
重复运行幂等，模板缺 freeform `apply_patch` 时拒绝。真实本地目录检查为
`action=unchanged`。

## 工具面审计

| 条件 | 请求中的工具数 | `apply_patch` | 结果 |
|---|---:|---|---|
| slug 不在模型目录 | 9 | 无 | 模型写未声明工具；fixture 测试失败 |
| 加入模型目录条目 | 14 | `custom` | fixture 测试通过；工具正常进入协议 |
| 应用共享 `temperature=0.2` | 14 | `custom` | `apply_patch` 连续 3 次仅 2 次严格通过；没有证明改善，设置已清除 |

模型目录本地 JSON 位于 `~/.codex/models.json`。应用前的备份为
`~/.codex/models.json.bak-20261101T004130`。新增条目沿用本地 `deepseek-flash` 的兼容
元数据，只改模型身份和上下文：

- slug：`qwen3.8-flash-next-iq3_xxs-abliterated`
- context / max context：`262144`
- auto compact limit：`210000`
- `apply_patch_tool_type`：`freeform`
- 优先级：`100`

这不是官方模型能力声明；它是一个本地兼容条目。产品化前仍需验证安装/连接流程、
Codex 升级兼容和幂等回滚。

## Schema 1/2 历史记录（不得作为当前通过率）

> 下面的 `7/7`、`3/4` 来自已废弃 harness：早期 prompt 禁止 `apply_patch` 并要求用 shell
> 编辑，后来又混入了不同的目录与重复口径。保留用于审计历史，不能与 Schema 3 合并。

新增 `tools/server/bench-agent-quality.py`，从全新临时仓库运行 Codex，只保存任务名、
工具计数、哈希、usage、测试和严格收口结果，不把 prompt、模型正文、reasoning 或完整
工具输出写入报告。

| 运行 | 模型 / effort | 严格通过 | 测试通过 | 串行 wall 合计 | 条件 |
|---|---|---:|---:|---:|---|
| 6 个基础 fixture，无目录 | Qwen / high | 5/6 | 6/6 | 149.475 s | 每个请求都有 metadata fallback warning |
| 6 个基础 fixture，参考 | DeepSeek / high | 6/6 | 6/6 | 77.243 s | 直连参考；模型/hosting 同时不同 |
| 7 个 fixture，含 `apply-patch` | Qwen / high | 7/7 | 7/7 | 211.625 s | 目录已加载；长上下文一项 95.046 s |
| 4 个基础 fixture | Qwen / medium | 2/4 | 4/4 | 75.200 s | 比 high 约快 7%，但两项严格收口失败 |
| `apply-patch` 单任务 | Qwen / high | 1/1 | 1/1 | 14.842 s | 首次目录验证 |
| `apply-patch` 三次重复 | Qwen / high | 2/3 | 3/3 | 53.223 s | 一次测试通过但最终回复不是严格 sentinel |

`apply-patch` 无目录时为 0/1，测试失败且没有合法最终 sentinel；有目录时首次通过，随后
三次重复为 2/3。合计 3/4 通过，仍有一个最终收口缺口，因此还不能把目录修复写成稳定
质量恢复。共享 `temperature=0.2` 的三次结果是 2/3，与目录-only 没有可证明的改善，已恢复
`/settings` 的无共享覆盖状态。

## 解释边界

- 6 个基础任务的 Qwen 与 DeepSeek 差异同时包含模型、量化、provider、hosting 和客户端
  元数据，不能据此计算“Strata 引擎导致多少质量下降”。
- 串行 wall 只作记录；顺序、缓存和服务状态没有随机化，不是通用延迟结论。
- `high` 比 `medium` 约快 7% 的结论不稳定；但 `medium` 的严格收口只有 2/4，而 `high`
  为 4/4，因此保留 `high`，不为了约 7% 冒任务成功风险。
- 目录修复只证明工具协议和基本 fixture 改善；真实用户任务仍需按同一模型做配对复测。

## 证据文件

本地报告（均为隐私安全的 fixture 摘要）：

- `/tmp/strata-agent-quality-qwen-six-20261101.json`
  `b90ddb005e5be267ca3f06fcbed6689fac050cbca936b38a03927b3def269beb`
- `/tmp/strata-agent-quality-reference-deepseek-six-20261101.json`
  `2d89df20177fe8216bb7f6d027ab72af11357fb1e2a0ad9d92ea461e62c4cc4e`
- `/tmp/strata-agent-quality-qwen-catalog-20261101.json`
  `44834a46a4fc244d42708b2c9811f38b13b2c4f4f72bd1dd0f4ed34b45d91f01`
- `/tmp/strata-agent-quality-high-20261101.json`
  `533096da82ca540ba6514ace40c1f56cd643b7ef3332e70f300447f37c35af74`
- `/tmp/strata-agent-quality-medium-20261101.json`
  `138049204ccd9dda82a22850fe4b30fa9ff18466c049c6b8273e9ff6df6a4c10`
- `/tmp/strata-agent-quality-shape2-20261101.jsonl`
  只记录请求结构、工具类型和响应元数据，不保存正文。

工具代码：

- `tools/server/codex-model-catalog.py`
- `tools/server/bench-agent-quality.py`
- `tools/server/agent-shape-proxy.py`

## 下一目标

`AGENT-TERMINAL-QUALITY-COMPARISON`：修正 DSH 报告的模型/provider 标签，重复 DSH
对照，并检查 DSH 的 provider 配置能否指向 Strata endpoint；不能时永久标注“DeepSeek
直连”。随后 `AGENT-STRICT-RESPONSE-CONTRACT` 只处理最终收口缺口。不改引擎，不重启生产。
