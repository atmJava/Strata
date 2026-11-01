# AGENT-HOT-TURN-STAGED-STREAM-PIPE result

日期：2026-11-01

状态：否决。路径和正确性门通过，但同源码热长轮没有收益；候选代码已从工作树移除，
生产已恢复 A1。

## 判定

`STRATA_PREFILL_FUSED_STAGED_PIPE=1` 确实让 layer `35/44/47` 使用 16-expert 小批和
双缓冲，但机制没有穿透到 prompt/wall：

| 1 路 hot-long | B0 | B1 | B1 相对 B0 |
|---|---:|---:|---:|
| wall 中位 | `1.511603 s` | `1.522504 s` | 慢 `0.721%` |
| prompt 中位 | `1395.3 ms` | `1397.2 ms` | 慢 `0.136%` |
| decode 中位 | `98.0 ms` | `107.4 ms` | 慢 `9.592%` |

三波配对 wall 变化为 `+0.283%/+0.721%/-0.767%`，只赢一波。理论约 `23.5 ms` 的
compute 可重叠空间没有实现；分批 launch、事件等待和更碎的 copy 已经抵消收益。

hot-short 的 wall 名义改善 `1.367%`，但 prompt 只改善 `0.766%`，decode 改善
`3.468%`；这不是本候选的同步证据，不能据此采纳。cold wall/prompt 名义改善约
`0.53%`，同样低于门槛且不是目标形状。

## 路径与正确性

B1 共记录 `18` 条 pipe 路径日志，三波 hot-short/hot-long 都覆盖三层：

| turn | staged 范围 | batches |
|---|---:|---:|
| hot-short | `27–38` | `4–5` |
| hot-long | `83–141` | `6–9` |

每批实际最多 16 个 staged expert。9/9 请求 HTTP/engine 成功、期望回复全过、finish
全 `stop`、零外来请求、窗口新增 Xid `0`。B0 没有 pipe 日志，实际 env 不含新开关。

## 对照与顺序

两臂使用同一 candidate 源码与构建，只有 `STRATA_PREFILL_FUSED_STAGED_PIPE` 不同：

- B1 先加载并跑三波，随后 B0 加载并跑三波；
- 该顺序可能冷启动偏差不利于 B1，但 B1 仍没有 prompt 改善，且方向不是单次噪声；
- 生产 A 不承担机制归因。

按契约的停止门，B1 热长轮没有达到 `1%`/`0.015 s`，因此不跑 94K、2/4/6/8 或质量矩阵，
也不扫描 8/24/32/48 等 batch 大小。

## 证据

远端目录：`/opt/ai/logs/agent-staged-pipe-20261101T2122Z/`

- candidate binary
  `d343e54c22f76b92ddde29e7157d5f66bdf63b294f38919efb2039a6288414d9`
- candidate `prefill.cpp`
  `dd77cbdb5f44c33b514f05875c0f817588e6794243e618be7626712f054b656f`
- B0 JSON `50f593050d69a72b76c549b74b61c9d8cd9644177f70403028f86f5d64d2c732`
- B1 JSON `f72008441c380dbbd6169c4015d9c549c335716cf6c8bb215b04a5d29c21a0e9`
- B0 engine log `f5d5c23769d0e8914b73670dc72ef263134289fd607bc0f6f6cc01741b3b94a9`
- B1 engine log `51b43db8bd37d70a8e831a313948102ab64e713245149d1a67e56b7e764db144`
- candidate source snapshot `candidate-prefill.cpp`，与上面 source hash 相同

## 恢复

A2 生产 manual instance 为 server/vision/engine `249822/249829/249834`；health loaded、
262144、6 slots / 2 groups、systemd inactive。engine
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`、config
`6ece1a13f4ac2d875bdafd903c7aba23fdce6be5ccc387e17b987a130a486811`、server
`bd3e428932c0b9bb645b2ae9f37924b2e440b590333a0322cdd00cd28dd4ed82`。
恢复窗口新增 Xid `0`。
