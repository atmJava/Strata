# AGENT-COMPACTION-LARGE result

日期：2026-10-31

状态：验证通过，现役功能已在更大上下文确认。

## 结论

线程级 compaction 工具缓存不只适用于 8.3K fixture。在现役 B4 服务上，先冷读
`180,279` token 的长历史，再用同一 conversation 发送 Codex compaction：

| 请求 | Input | Cached | Reuse | Wall |
|---|---:|---:|---:|---:|
| Cold turn | 180,279 | 0 | 0% | `65.555 s` |
| Compaction | 180,301 | 180,272 | `99.984%` | `2.385 s` |

compaction 只重读 29 token，避免约 63.17 秒的重复冷 prefill。turn 因 `max_output_tokens`
停在 `incomplete`；compaction 正常 `completed`。

## 路径

- 服务：`http://127.0.0.1:8080/v1/responses`
- `codex_thread_titles: true`
- `codex_compaction_cache: true`
- `codex_auto_prefix_pin: true`
- `codex_system_before_tools: true`
- 6 slots / 2 groups，health loaded，262144 context

compaction 请求本身发送 `tools: []`。服务端按
`(session_id, thread_id)` 从上一轮取回该 conversation 的 tools，使 compaction prompt
与已缓存 turn 前缀一致。

## 正确性与资源

两个请求均为 HTTP 200，没有 HTTP、execution、CUDA、timeout 或 traceback 错误。
compaction 返回 24 output token，状态 `completed`。该验证只证明长历史复用和完整性，
不是 1/2/4 路吞吐声明。

## 证据

`/opt/ai/logs/agent-compaction-long-20261031T0530Z/result.json`

## 限制

- 目标为约 180K token，未覆盖 256K 边界。
- 单个 conversation、单线程；1 主 3 子代理交错已由 8.3K compaction replay 覆盖。
- 真实客户端日志中的 compaction 最大约 30 万 token，超过当前 Strata 262144 context；
  触发频率证据可用于选路，但不能把该绝对长度直接搬到当前服务。

## 回滚

关闭 `codex_compaction_cache` 并重启 Python server。该验证没有改变 engine。
