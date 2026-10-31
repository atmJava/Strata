# DSH 标题请求隔离：产品候选契约

日期：2026-11-01

## 问题

DSH 的首轮标题请求没有把内部 `purpose: "session-title"` 发到 HTTP。线上可识别的形状是：
`POST /v1/responses`、流式、`max_output_tokens=4096`、无工具、无 instructions、恰好两个
input item（developer 后接 user）。它与主模型请求并发，会在首步争用 GPU 与 prompt cache。

## 候选

在 Strata HTTP admission 层增加默认关闭的 `STRATA_DEFER_TITLE_REQUESTS=1`。请求仍由模型
生成，标题语义不变；只有恰好一个普通 inference admission 已占用时才等待。当前主请求
结束释放 admission 后，标题立即进入正常路径。两个会话同时新建时标题 fail open，因为
HTTP 形状没有 session id，无法安全判断哪个标题属于哪个主请求。

该候选只改变排队顺序，不改 profile、prompt、engine、GPU、量化或标题 fallback。

## 精确形状

只有以下全部成立才进入等待：

- path 为 `/v1/responses`；
- `stream` 为 `true`；
- `max_output_tokens` 为 `4096`；
- `tools` 缺失或为空；
- `instructions` 缺失或为空；
- `input` 是两个对象：第一个 role 为 `developer`，第二个 role 为 `user`；
- 没有 Codex 的 `client_metadata`，避免影响已有 thread-title/compaction 路径。

不从 prompt 文本、模型输出或 user 内容做启发式判断。

## 排队行为

- 默认关闭；关闭时路径和计时字节不变。
- 等待 250 ms 让同时到达的主请求先登记 admission。
- 单独跟踪活跃标题 admission；用“总 admission - 活跃标题”计算普通请求，避免两个标题
  互等。
- 恰好一个普通 admission 存在时最多等待，直到主请求释放或 60 s 保护上限。
- 两个或更多普通 admission 存在时立即 fail open，恢复原有并发；否则短命 headless
  客户端可能在标题完成前退出并只留下 fallback。
- 只有标题请求时，250 ms 后正常执行。
- 失败、超时或不匹配时 fail open，不关闭标题，不伪造标题。
- 日志只记录延迟秒数和匹配计数，不记录 prompt、标题或请求正文。

## 验收

单元与集成：

- 精确形状正例、每种字段不匹配负例；
- main 已占用时 auxiliary 阻塞，release 后继续；
- 只有 auxiliary 时在 grace 内继续；
- 两个 auxiliary 同时启动时不互相等待；
- 一个 main 加两个 auxiliary 时，两个 auxiliary 只等 main，不等彼此；
- 两个 main 加两个 auxiliary 时两组都 fail open，不等待到主任务结束；
- disabled 时无等待；
- HTTP `/v1/responses` 真实入口触发，普通请求不触发；
- 全量 `serve/test_server.py` 与 offline preflight 通过。

真实 DSH：

- 至少 6 对短任务 ABBA、2 对长任务；
- 主指标为 `task_wall_s`；短任务中位 >=1%，胜场 >=5/6；
- 长任务不明显回退；
- 标题最终 source 为 provider，不能只留 fallback；
- 代码/测试/最终 sentinel 全过，工具/事件错误为零；
- 没有新 Xid、engine 意外退出或资源异常；
- 失败时恢复未启用环境并保留原 server 进程。

## 回滚

移除 `STRATA_DEFER_TITLE_REQUESTS=1` 并重启 server 即回到原调度。若需要回滚源码，恢复
安装前的 `serve/server.py` 备份；engine 二进制、GPU 和模型配置不变。

## 生产判决

**opt-in 采纳。** 生产单会话主 turn 中位 `19.629 → 16.078 s`（`18.091%`），runner
wall `21.484 → 18.065 s`（`15.914%`），3/3 质量、provider 标题和错误门通过。双会话
2/2 质量、provider 标题通过，日志 defer 增量为 0；双会话恢复原并发，不把其视为加速。

生产 A 备份为 `/opt/ai/backups/prod-title-defer-20261031T191327Z/`，第一版双会话不通过
的候选备份为 `/opt/ai/backups/prod-title-failopen-20261031T192532Z/`。最终 server hash
为 `8f03c745ec3d8341fe3fac8e1cba8f7842c04d2ad33fd479caf96582902cb9e0`。

