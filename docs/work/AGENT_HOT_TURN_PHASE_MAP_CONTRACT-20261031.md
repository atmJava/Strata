# AGENT-HOT-TURN-PHASE-MAP contract

日期：2026-10-31

状态：预注册；只做一次 profiling boot，随后恢复生产。

## 问题

普通 Agent 热续轮 wall 主要由 prompt 路径组成，但 short-read、MTP prompt batching、
官方 `0.1.55` 与 GDN conv/L2 融合都没有形成至少 `1%` 且 `0.05 s` 的用户收益。
继续从源码猜局部 kernel 会重复局部优化。先用生产 engine 已有的
`STRATA_PREFILL_TIMING=1` 对两个真实代表形状取得有序 phase 图：

- 596 fresh-token 编辑—测试热续轮；
- 94K 热续轮。

目标不是证明 profiler 更快，而是定位下一个可移除主相位，并给出来自实测绝对秒数的候选
上界。

## Arm 与固定负载

A1 是当前无 profiler 的 `GDN-A2`：

- server / vision / engine `164043 / 164045 / 164050`
- engine `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`
- config `6ece1a13f4ac2d875bdafd903c7aba23fdce6be5ccc387e17b987a130a486811`
- health loaded、262144、六 slots / 两 groups、systemd inactive

B 深拷贝 A1 config，只在 engine env 增加：

```text
STRATA_PREFILL_TIMING=1
```

模型、pack、双卡 layer split 24、cache、MTP、采样、`--ple-inflight 1024` 和其他生产 env
不变。启动后从实际进程核对 engine/config hash、唯一候选 delta、六 slots / 两 groups、
health 和独立 engine log。

596 使用 `context=12000`、short fresh `24`、long fresh `596`；94K 使用
`context=66720`、short fresh `15`、long fresh `369`。两形状各三波，判决只看
`hot-long`，与 GDN-CONVL2 的 A1 数据同口径。

## 记录

- engine 日志中的 21 个有序 prefill phase、host staging、主/对端 GPU 值和 timing 行数；
- 每个请求的 prompt/wall/TTFT/cached/completion/finish/error；
- 回复是否与 A1 相同，或者至少 profiler 内逐轮稳定且 finish/tool 语义不变；
- 当前 engine/config hash、实际 env、health、六 slots / 两 groups；
- 只保存 metadata 和数值，不输出或提交 prompt、回复、reasoning 或工具正文。

profiler 会改变执行时序，因此 B 的 wall 不作为性能收益或回退证据。A1 的 GDN 三波数据
只用于确认负载形状；phase 图用于提出下一个候选。

## 选择门

形成实现候选前必须满足：

1. 至少一个 phase 在 596 或 94K 热长轮中绝对成本 `>=0.05 s`；
2. 源码控制流证明该 phase 在生产路径真实命中，且存在不改变每行算术、token 选择、
   finish 和工具语义的单一可移除部分；
3. 按该部分保守可省比例计算，至少一个代表 wall 的理论收益 `>=1%`，并在进入构建前
   预注册独立 A1/B/A2 门；
4. 如果最大 phase 无法被单一机制安全移除，或收益上界低于 1%，不实现邻近参数或
   kernel 微调，转向 server admission/cache 或客户端请求结构的下一个证据支持项。

## 成本与回滚

只支付一次 profiling boot 和一次生产恢复，约 4–6 分钟。完成后删除唯一
`STRATA_PREFILL_TIMING` env，恢复字节相同的 config 与无 profiler 进程；核对 engine/
config hash、health、六 slots / 两 groups、systemd inactive 和无意外错误。失败时直接
SIGTERM 当前手动实例并从原 config 重启。
