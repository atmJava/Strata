# GPU Xid 13 HC_SPLIT 有限 smoke 结果

日期：2026-11-01

状态：风险缓解候选 smoke 通过；稳定性、完整质量和长曝光未证明。

## 目的

验证只增加 `STRATA_HC_SPLIT=1` 时：

1. 生产 engine 的真实运行环境包含该变量；
2. 两张卡实际进入 split hyper-connection read，而不是只读源码；
3. 短 1/2 路烟雾测试没有错误或明显性能回退。

这不是显存降频、GPU 隔离或硬件修复实验。

## 生产身份

| 项 | 值 |
|---|---|
| server / vision / engine | `295944 / 295950 / 295955` |
| server cwd | `/opt/ai/src/strata-two-stream-serial-20261031` |
| engine | `/opt/ai/src/strata-batch-prefill-guard-20261101/build-guard-prod/strata` |
| engine SHA-256 | `e60a334c0ca5c1974d79385256aaf90519bcb646e2626f7e66de818d104b0ab7` |
| config | `/opt/ai/logs/gpu-xid13-hc-split-20261101T000258Z/A1-HC-SPLIT1.json` |
| config SHA-256 | `c956f280afaeae0847b48caed315bcc90fb7a6c74364494a8e25d62590b23312` |
| health | loaded、262144、6 slots / 2 groups |
| launch 到 ready | 约 `138 s` |

实际 engine env 包含 `STRATA_HC_SPLIT=1`、`STRATA_BATCH_SERIAL_TWO=1` 和三个 CPU-share
生产开关；没有 debug 开关。server 进程保留
`STRATA_ADMIT_SHORT_FIRST=2048`、`STRATA_DEFER_TITLE_REQUESTS=1`、
`STRATA_MP_STAGED_READ_MIN_TOKENS=2048`、`STRATA_PIN_DSH_TOOL_PREFIX=1`。

回滚 config 为
`/opt/ai/logs/agent-batch-prefill-guard-prod-20261101T2308Z/A1-CPU-SHARE-prod-r3-20261101.json`，
SHA-256 `2c4c7ac6c346c7b64d162502080fc3147b5da232de156a20a9d1db81b521985e`。

## 路径证明

engine 日志中 CUDA0/CUDA1 均出现：

```text
the hyper-connection read runs as split
... checked bit for bit against the plain read on this card
```

两张卡各有一条 split 路径和一条逐位自检，排除“env 存在但路径未触发”。

## Smoke

报告：
`/opt/ai/logs/gpu-xid13-hc-split-20261101T000258Z/HC1-hot.json`

报告 SHA-256：
`f291a15e4b6a119d20b318b8f3081062fc15c6aed2159c3f2d2bb9e9321b7756`

参数：1/2 路各 3 波，12K context，短/长 fresh 分别为 `24/596`，输出上限 8 token。

| 形状 | HC_SPLIT wall | 旧生产 B1 wall | 变化 |
|---|---:|---:|---:|
| 1 / cold | `11.422605 s` | `11.428047 s` | 快 `0.048%` |
| 1 / hot-short | `0.528578 s` | `0.533364 s` | 快 `0.897%` |
| 1 / hot-long | `1.361584 s` | `1.363801 s` | 快 `0.163%` |
| 2 / cold | `11.920471 s` | `12.027414 s` | 快 `0.889%` |
| 2 / hot-short | `1.582567 s` | `1.584538 s` | 快 `0.124%` |
| 2 / hot-long | `3.218846 s` | `3.217002 s` | 慢 `0.057%` |

全部 `27/27` 请求零 HTTP/执行错误、零外来请求、期望回复全部匹配，finish 全 `stop`。
六种形状变化都在约 1% 噪声内，不能据此宣称提速。

## 判决与剩余门

`HC_SPLIT=1` 是**可回滚的风险缓解候选**，短 smoke 通过。它没有被证明能修复低频 Xid，
也不是新的性能收益。P0 仍需完成：

- 完整 7 项 coding quality fixture 与旧生产身份对照，不得有候选回归；
- 1/2 路 cold/hot 无超过 1% 的明确回退；
- 4 路不回退超过 1%，6/8 零错误和资源安全；
- 至少 60 分钟、覆盖真实 1/2 会话编辑—测试和长输出的混合曝光无新 Xid；
- 任何新 Xid、错误 turn 或质量回退立即恢复回滚 config。

达到以上条件后只标记“风险缓解候选通过有限窗口”，继续被动监测，并恢复
`AGENT-CURRENT-PROD-COST-REMAP`。若出现新 Xid，才进入单变量显存降频；最后才考虑
GPU0 隔离。不要在 HC_SPLIT 邻近 env 上无限试参。
