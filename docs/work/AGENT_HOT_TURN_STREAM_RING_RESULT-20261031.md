# AGENT-HOT-TURN-STREAM-RING result

日期：2026-10-31

判决：**否决。**

## 结论

B 只把 `STRATA_PREFILL_STREAM_MIN` 从生产默认值改为 `0`，真实 554-token 热续轮因此进入
更深的 streamed expert ring。启动、实际 env、健康、资源、固定 greedy、smoke 和请求质量门
全部通过；但 596 fresh-token 热长轮的 wall 反而从 A1 中位 `1.509808 s` 升到
`1.898041 s`，慢 `25.714%`，绝对多 `0.388233 s`。

这不支持“更早 copy 会隐藏 stage 2 的 `wait copy 173 ms`”的假设。554-token chunk 只路由
少量专家，而 full ring 按全部专家的顺序预取；额外 copy 量远大于少数专家更早到达带来的
收益。按预注册停止门，没有运行 B 的 94K、2 路或并发矩阵。

## Arms 与路径

- A1：恢复后的生产 engine/config；596 与 94K 均作为同周期参考。
- B：深拷贝 A1 config，只在 engine env 增加：

```text
STRATA_PREFILL_STREAM_MIN=0
```

- A2：恢复与 A1 字节相同的 config。

实际 engine 进程核实到候选 env；源码控制流显示 `stream_all_min()==0` 时，满足
`T>=stream_min` 的 554-token chunk 直接进入 streamed ring。B 没有 profiler env。

B 通过六 slots / 两 groups、health loaded、262144 context、无 OOM、无 CUDA/engine/HTTP
错误，资源在安全边界内。三组 fixed greedy 各两轮在 B 内逐字重复且等于 A；64-token smoke
正常。

## 实测

596 hot-long：

| arm | wall repeats (s) | wall median (s) | prompt repeats (ms) | prompt median (ms) |
|---|---|---:|---|---:|
| A1 | `1.521472 / 1.509808 / 1.489629` | `1.509808` | `1410.2 / 1401.7 / 1380.7` | `1401.7` |
| B | `1.913422 / 1.898041 / 1.888786` | `1.898041` | `1806.6 / 1787.9 / 1780.1` | `1787.9` |

B 的 wall 慢 `25.714%`（`+0.388233 s`），prompt 中位慢约 `27.55%`。方向明确且绝对成本
远超噪声，不再测 94K 或并发。

## 生产恢复

生产已恢复 A1 原 config。最终 server / vision / engine 为
`166198 / 166200 / 166205`；engine
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`，config
`6ece1a13f4ac2d875bdafd903c7aba23fdce6be5ccc387e17b987a130a486811`。health loaded、
262144、六 slots / 两 groups，`strata.service` inactive、`MainPID=0`、`NRestarts=8`。
engine 实际 env 不含 `STRATA_PREFILL_STREAM_MIN`、`STRATA_PREFILL_TIMING`、
`STRATA_PREFILL_RING` 或 `STRATA_GDN_CONVL2`。A2 三组 fixed greedy parity 全部
repeatable/exact，finish 为 `stop`。

## 证据

目录：

```text
/opt/ai/logs/agent-hot-turn-stream-ring-20261031T1418Z/
```

SHA-256：

```text
A1-596.json       3e7fcf444fd7283731b50f413156c0c853b9f73d6b220c20e8ecd13a2a0d8d32
A1-94K.json       74a770326c0dd250eb566d6077d9a94413455365a4ea1d417d46c9910281dbad
B-596.json        c3de7142493c96ffc6316d8a0e06cba2c7463b5d2d91c7cc7765abcd9cd13652
B-parity.json     e3ebf22d49242a06465b901d9d5c0c65096f0ed22a86e7174d2ac87ac4c54a8c
B-smoke.json      d02b5472e1ea9da8fc64cae005fd6e961822061108c19b0c56b0b9bec22eb50a
B-server.log      41906f4b68706a65717348fcaea926a8efe47195428ce2315180ad232bfa5c31
B-config.json     a126f832ccff7dadf79eefa46405035a05e2fb026332a71eea4fd6636646b91a
A2-parity.json    5fbad3c0df3db2c8a454092fe1c6ed59bd502415aa0dd149540ab8eba798c917
```

## 复盘

phase map 指出的 173 ms 仍是真实大头，但“更深 ring”不是正确的利用方式；只有在能证明
当前 routing 已足够早确定选中专家、并且只对选中专家预取时，才值得继续这条机制。下一项先
做只读源码审计，不直接改 kernel，也不重开阈值、ring 容量或 profiler 参数扫描。
