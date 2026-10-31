# AGENT-HOT-TURN-STREAM-RING contract

日期：2026-10-31

状态：**已否决。** B 的 596 wall 慢 `25.714%`，按停止门未跑 94K 和并发矩阵。
完整结果见
[`AGENT_HOT_TURN_STREAM_RING_RESULT-20261031.md`](AGENT_HOT_TURN_STREAM_RING_RESULT-20261031.md)。

## 证据与问题

同口径 596/94K 热长轮的实际 fresh 读取都约 554 token。`STRATA_PREFILL_TIMING=1`
三波中位显示：

| stage | GPU timeline | host staging | 最大 phase |
|---|---:|---:|---|
| 1 | `459 ms` | `501 ms` | `gemm gate/up 131 ms` |
| 2 | `647 ms` | `788 ms` | **`wait copy 173 ms`** |

stage 2 的 `wait copy` 占其 GPU timeline `26.7%`，第二大为 `gemm gate/up 130 ms`、
`dequant 106 ms`、`gemm down 68 ms`。用户可见 prompt 仍约 `1.29–1.51 s`，因此
仅 copy 等待就有最多约 `11–13%` 的 wall 改善空间，远高于 1% 门。

源码中的原因是该 554-token chunk 低于默认 `stream_all_min=1024`，走
`STAGE_GRP=32` 的 staged walk，只提前 `32 - 16 - 1 = 15` 个专家；stage 2 的
非驻留专家 copy 因而不能被更深地隐藏。默认阈值以上的 chunk 走更大的 streamed ring。

## Arm

- A1：恢复后的当前生产 engine/config；
- B：深拷贝 A1 config，只在 engine env 增加：

```text
STRATA_PREFILL_STREAM_MIN=0
```

- A2：恢复与 A1 字节相同的 config。

模型、pack、双卡 layer split 24、262144 context、六 slots、两 groups、MTP、采样、
prompt cache、`--ple-inflight 1024` 和所有其他生产 env 不变。B 不启用
`STRATA_PREFILL_TIMING` 的性能臂；route 证据来自实际 env、源码中
`stream_all_min()==0` 对 `T>=stream_min` 的直接分支、真实 554-token 请求和启动后的
资源/健康状态。

## 门

B 必须先通过：

- actual process env 只有该候选 delta，engine/config hash 与本轮一致；
- health loaded、262144、六 slots / 两 groups、systemd inactive；
- 无 OOM、CUDA/engine/HTTP 错误，显存和 RAM 在安全边界内；
- 三组 fixed greedy 各两轮，B 内逐字重复且等于 A；完成 token、hash、finish 一致；
- 64-token smoke 正常。

性能使用同口径 596/94K hot-long，每臂三波：

- 至少一个形状 wall 中位改善 `>=1%` 且绝对 `>=0.05 s`；
- 两个代表形状 wall 不得反向；
- A1/A2 漂移 `<=3%`；
- 质量、cached tokens、finish、health 和资源门全过。

1 路通过后，再测 2 路同形状：

- 报告单请求 wall、第二条请求 TTFT 和完成跨度；
- 至少一项关键完成指标改善 `>=1%`，且另一项不回退 `>1%`；
- 4 路完成时间/吞吐不回退 `>1%`；
- 6/8 路只做零错误、health 和资源安全回归。

## 预期与停止

若 streamed ring 能把 stage 2 的 copy 等待隐藏到接近 stage 1 的 `13 ms`，乐观 wall
收益约 `11%`；即使只隐藏 10%，仍超过 1%。若 B 的 596 wall 不快 `>=0.05 s`，立即停止，
不扫描 `STRATA_PREFILL_STREAM_MIN` 邻近值、ring 容量或其他参数。若质量或资源门失败，
也直接否决并恢复 A。

## 成本与回滚

A1 在同 boot 完成基准后，B 与 A2 各需一次约 3 分钟加载，合计约 6–7 分钟。候选只是
一个 env；失败时删除该 env 即可精确回滚。完成 B 判决后必须恢复 A2、核对
engine/config hash、实际 env、health、六 slots / 两 groups、systemd inactive 和 parity。

## 判决

B 的 596 hot-long wall 从 A1 中位 `1.509808 s` 变为 `1.898041 s`，慢 `25.714%`，
绝对多 `0.388233 s`；prompt 中位从 `1401.7 ms` 变为 `1787.9 ms`。原因是 full ring
按全部专家顺序预取，而 554-token chunk 只路由少量专家，额外 copy 远大于更早预取收益。
质量、路径、健康、资源和资源门均通过，但这不能抵消明确的端到端回退。

生产已恢复 A2，运行 env 不含该候选。不扫 threshold、ring 容量或邻近参数。下一项改为
只读审计“路由结果何时可用、能否仅预取选中专家”，在给出真实命中率、绝对秒数上界、
bitwise/数值影响和回滚前不改源码、不重启。
