# AGENT-WORKLOAD-TRACE：真实 Codex 会话成本

日期：2026-10-31

状态：只读诊断完成，用于重拟优化计划。

## 数据范围

读取本机一个约 29 小时的 Codex 会话日志：

`/home/kali/.codex/sessions/2026/10/30/rollout-2026-10-30T08-26-31-01a18a33-7a8d-7c31-977d-bffbd181991f.jsonl`

日志共 3,614 条 `token_usage_record`，10 个 root turn、10 个 turn。该数据记录客户端模型
的 token usage 和事件时间，不包含远端 Strata server 的每请求 `prompt_ms/decode_ms`，
因此只能用于识别请求形状与重复工作，不能直接归因远端 engine 性能。

## 总量

| 项目 | 数量 |
|---|---:|
| Input | `640,411,228` |
| Cached input | `622,593,536` |
| 缓存率 | `97.22%` |
| Uncached input | `17,817,692` |
| Output | `2,769,364` |
| Reasoning output | `794,887` |
| 请求数 | `3,614` |

单请求 input 中位 `188,562`、p90 `283,539`、p99 `298,935`、最大 `302,405`。
Uncached input 中位只有 `265`，但 p90 `7,907`、p99 `42,631`、最大 `293,524`。
成本明显由少数长上下文事件主导，而不是普通续轮的均匀增量。

## Compaction

会话中有 36 个 `compacted` 事件。每次 compaction 前的大请求输入约 `26–30 万` token，
缓存通常只有 `3.5–7.6K`。按相邻 `token_usage_record` 计算，36 次 compaction 合计约
`970 万` uncached input，约占全部 uncached input 的一半。

典型事件：

| UTC | Input | Cached | Uncached |
|---|---:|---:|---:|
| 16:47:10 | 283,282 | 3,456 | 279,826 |
| 23:53:32 | 280,020 | 7,424 | 272,596 |
| 02:38:15 | 301,204 | 7,680 | 293,524 |
| 05:21:14 | 296,336 | 7,808 | 288,528 |

compaction 前一轮普通 turn 通常保持约 99% 缓存。例如 05:20:46 的 `299,060` 输入只重读
`1,076` token，下一分钟 compaction 却重读 `288,528`。这说明 trigger 来自 compaction
请求结构变化，而不是长历史本身每轮都必然冷读。

客户端记录的 provider 不保证与远端 Strata 配置相同；这是请求触发率证据。Strata 上的
实际消除量由 [`AGENT_COMPACTION_LARGE_RESULT-20261031.md`](AGENT_COMPACTION_LARGE_RESULT-20261031.md)
的真实 HTTP 180K 验证给出。

## 后续用途

下一轮把本统计固化为只读工具，支持按 root turn、compaction、缓存率分位数和 top
uncached 事件输出。工具不输出 prompt 正文、API key 或密码。选择性能候选时必须把
客户端 token 形状与远端 server timing 分开记录。
