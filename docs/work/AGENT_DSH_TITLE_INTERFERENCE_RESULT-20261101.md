# DSH 标题请求争用：成本归因与下一候选

日期：2026-11-01

状态：`AGENT-DSH-REAL-PATH-COST` 完成；标题延后代理 A/B 通过；server 内产品候选已实现，
等待生产验证。

## 结论

7 个基础/fixture 会话共 `197.776 s` 客户端墙钟，其中模型 step `184.332 s`，占
`93.202%`；工具区间并集 `10.505 s`，占 `5.312%`。40/40 个主模型 step 都通过 token
数和时间关联到服务端请求。7 个未关联请求正好是每个新 DSH 会话一次的隐藏标题请求。

标题请求在线上没有 DSH 内部的 `purpose: "session-title"`：大约 266 prompt token、
118 output token、`max_output_tokens=4096`、零工具，单次中位 `9.8 s`。它虽然不在主
turn 内，却和主请求争用 GPU 与 prompt cache。

因此，继续优先拆工具执行或局部 kernel 不能解释用户的大部分等待。当前唯一有真实触发
证据、且保留产品语义的下一候选是：标题仍在主任务后异步生成，但实验路径先等主请求
结束。不能关闭标题，也不能修改用户本地 DSH profile。

## A/B 结果

同一 `single-file` fixture，标题开启为 A，关闭为 B：

| 口径 | A 中位 | B 中位 | B 节省 | A/B |
|---|---:|---:|---:|---:|
| runner 进程 wall | `16.854 s` | `14.901 s` | `1.803 s`，`10.647%` | B `5/6` 胜 |
| 主任务 turn wall | `14.929 s` | `13.001 s` | `1.780 s`，`11.843%` | B `5/6` 胜 |

长任务没有支持通用关闭标题：

| 长任务 | 有标题 | 无标题 |
|---|---:|---:|
| pair 1 | `43.083 s` | `45.202 s` |
| pair 2 | `44.140 s` | `46.201 s` |

无标题在长任务 `0/2` 胜。首组 prompt cache 状态还受到上一臂污染：A 首步通常只复用
`582` token，B 首组复用 `5,463` token。短任务的正向结果足以提出隔离实验，但不足以
产品化，也不足以说明标题本身应关闭。

## 标题延后 ABBA

最终实验比较 A（当前标题并发）与 B（标题等当前主模型请求结束后再运行）。短任务顺序为
`A B B A A B B A A B B A`，统一使用相同的最终消息契约。一次探索性运行因模型输出在
sentinel 前多写解释而整轮作废，没有计入结果。

短任务 6 对：

| 口径 | A | B | B 改善 | 胜场 |
|---|---:|---:|---:|---:|
| 主任务 `task_wall_s` 合并中位 | `17.637 s` | `15.238 s` | `13.602%` | 5/6 |
| 配对改善中位 |  |  | `13.465%` | 5/6 |

12/12 次运行代码、测试、受保护测试和最终 sentinel 全过；12/12 最终标题都由 provider
模型生成，不是 fallback。B 的标题请求在代理中实际等待 `2.5–6.3 s` 后进入，证明改的是
调度顺序，不是关闭标题。

长任务采用 `A B B A`，每臂都通过质量门和 provider 标题：

| 臂 | task wall | 模型 steps |
|---|---:|---:|
| A1 | `46.411 s` | 8 |
| B1 | `37.943 s` | 7 |
| B2 | `41.297 s` | 7 |
| A2 | `82.438 s` | 11 |

B 没有明显回退。A2 的步数和耗时相对 A1 明显漂移，因此长任务只判非回退，不把更大的
名义差值归因给标题隔离。

## 测试方法修正

- `bench-agent-quality.py` 的 `wall_s` 明确标为 `runner_process`，包含 turn 结束后的
  客户端辅助工作；它不能单独代表主任务完成时间。
- `probe-dsh-session-cost.py` 现在同时报告 `task_wall_s`、`turn_union_s` 和
  `post_turn_s`，主任务判定使用首个 `turn/start` 到最后一个 `turn/end`。
- `agent-shape-proxy.py` 已改为逐块 `read1()` 转发，不再先读完整个上游响应；否则
  TTFT 和流式体验会被代理自身破坏。
- 标题识别只接受已观测的精确形状：`max_output_tokens=4096` 且无 tools。该开关只用于
  显式实验代理，不做 prompt 文本猜测，不进入生产默认。
- 代理已用短任务 ABBA 验证真实流式路径；12 个短任务和 4 个长任务没有 HTTP/事件/工具
  错误，也没有新增 Xid。

## 下一候选契约

`DSH-TITLE-INTERFERENCE`：

- 保留标题语义，标题在主任务完成后生成；
- 至少 6 对短任务，采用 ABBA 平衡顺序，至少 2 对长任务；
- 主指标是 `task_wall_s`，次指标是 runner 进程 wall；
- 短任务配对中位至少改善 `1%`；长任务不得明显回退；
- 标题文本质量不退化，不能接受 fallback 或语义空泛标题；
- 同时记录模型 prompt/decode、cache 复用、工具时间和零错误；
- 任何新 Xid、engine 意外退出立即抢占实验。

代理 A/B 已通过该契约。为了不改用户 profile，下一步在 Strata HTTP admission 层实现
默认关闭的 `STRATA_DEFER_TITLE_REQUESTS=1`：只匹配同一精确 wire shape，等待当前主
inference admission 释放后继续。设计、失败开放、60 s 上限和回滚见
[`DSH_TITLE_INTERFERENCE_PRODUCT_CONTRACT-20261101.md`](DSH_TITLE_INTERFERENCE_PRODUCT_CONTRACT-20261101.md)。

## 证据

- `/opt/ai/logs/dsh-session-cost-contract-20261101.json`
  SHA-256 `06e1efccc673c558dac697819c8f5e9a859ad7cea3c8761b9364730156f952a0`
- `/opt/ai/logs/strata-title-ab-a-cost-final.json`
  SHA-256 `7867f78798ca05293d52c689ac2dce5199e967d2ea7f7bd6e8aacd1f52aa000d`
- `/opt/ai/logs/strata-title-ab-b-cost-final.json`
  SHA-256 `0a22caa10144e9725e42e52a53909469e1e08776411fa5671fefaa2c1448527b`
- `/opt/ai/logs/dsh-title-defer-abba-20261101/`
  包含本轮 16 个 runner 报告、成本/ABBA 汇总和隐私安全 shape；目录文件 SHA-256 由保存时
  `sha256sum` 输出固定。

