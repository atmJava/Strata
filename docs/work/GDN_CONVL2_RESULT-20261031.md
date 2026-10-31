# GDN-CONVL2 result

日期：2026-10-31

判决：**否决。** `STRATA_GDN_CONVL2=2` 的真实路径、固定 greedy 和请求质量门均通过，
但两个代表热续形状的 wall 都没有达到 `>=1%` 且绝对 `>=0.05 s` 的收益门。生产已恢复
原始 engine/config，运行进程不含该候选 env。

## 实验

B 只给 A1 config 增加：

```text
STRATA_GDN_CONVL2=2
```

engine、模型、pack、双卡 layer split、262144 context、六 slots、两 groups、MTP、
prompt cache、采样和其余生产 env 不变。`--prefill auto` 的 8192-token chunk 满足
`T > 64`，进程不含 `STRATA_GDN_CONV_SERIAL`。实际启动后核对了 engine/config hash、
候选 env、health loaded、六 slots / 两 groups 和 `strata.service=inactive`。

## 正确性与质量

- A1/B/A2 三组固定 greedy 各自重复一致，归一化文本、completion token、hash 与
  finish 完全相同；
- B 的 64-token smoke 正常，finish 为 `stop`；
- 596/94K 每组 9 个请求零 HTTP/engine 错误、零外来请求、回复符合期望；
- A2 engine/config hash 恢复到 A1 原值，运行环境无 `STRATA_GDN_CONVL2`；
- 最终 health loaded、262144、六 slots / 两 groups，`strata.service=inactive`。

## 性能

每臂三波 `hot-long` 中位：

| shape | arm | prompt | wall |
|---|---|---:|---:|
| 596 | A1-GDN | `1409.4 ms` | `1.514727 s` |
| 596 | GDN-B | `1394.2 ms` | `1.502251 s` |
| 596 | GDN-A2 | `1405.9 ms` | `1.508857 s` |
| 94K | A1-GDN | `1259.4 ms` | `1.413078 s` |
| 94K | GDN-B | `1258.6 ms` | `1.410180 s` |
| 94K | GDN-A2 | `1262.5 ms` | `1.413586 s` |

相对 A1：

| shape | prompt gain | wall gain | absolute wall gain |
|---|---:|---:|---:|
| 596 | `1.0785%` | `0.8245%` | `0.012476 s` |
| 94K | `0.0635%` | `0.2051%` | `0.002898 s` |

相对 A1 的 A2 漂移：

| shape | prompt drift | wall drift |
|---|---:|---:|
| 596 | `-0.2483%` | `-0.3875%` |
| 94K | `+0.2461%` | `+0.0359%` |

A2 稳定，但 B 的两个 wall 收益都远低于 `1%`，绝对收益也低于 `0.05 s`。按预注册停止门，
不跑 189K、2/4/6/8 或邻近 GDN 参数。局部 launch/写回确实被删掉了，但它只占用户等待的
很小一部分；继续围绕该 kernel 调参不符合“先处理大头”的优先级。

## 生产状态

- server / vision / engine：`164043 / 164045 / 164050`
- engine SHA-256：
  `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`
- config SHA-256：
  `6ece1a13f4ac2d875bdafd903c7aba23fdce6be5ccc387e17b987a130a486811`
- 进程环境不含 `STRATA_GDN_CONVL2`
- health loaded、262144、六 slots / 两 groups
- `strata.service` inactive，`NRestarts=8`

B config SHA-256：
`24c85a446b9b7d0ca13ee0791baa8da4db7b203218f25fe202bbdb5bfc6da3b3`。

## 证据

目录：`/opt/ai/logs/engine-0155-eval-20261031T1320Z/`

```text
B-596          f3c81b5ad28fb2c365e483602355380d39c7e389fd4f1ecbe2dd088908706578
B-94K          47e8240176184e803032bd975f8f560c150240eac72aad50d45aa210054dc253
B-log          01fa20733d8f5c8a1682072e48216d982a27d4779d60fb07e2ec5970a050362f
B-parity       e5107d69d42f79b5cbe08fb5fc11abf3fe991f1ec1025bd674a839dc607d86fe
B-smoke        06f0a47d610ef0184356bfadfd2a325aef2340f1831fd2a5d96c3bdcbecf3b02
A2-596         ce91ba39a4cb95a45ecb26f2e9217fe2efa300c9d73dfeca4cae17e7da5a50b9
A2-94K         8c2d60e8c202a438c0f5d081f831ffb04722251e3110355d306e79bf868257a1
A2-parity      fcd5e83a86998169e5b299ef05b7bcef99d87a186a7acf5d7e79533a2d0b3e50
A2-log         370f7f4b3080d6d64e0a5cd9da96d29d4f2e0cd5bec4c7370a93ae81c99795b4
```

## 复盘与下一步

这轮再次证明“源码里存在一个局部融合”不等于用户会明显变快。普通热续轮 wall 仍由
prompt 路径主导，但已连续否决 short-read、MTP batching、官方版本和 GDN conv。
下一项改为 `AGENT-HOT-TURN-PHASE-MAP`：用同一生产 engine 的现有 phase profiler
拆 596 与 94K 热续轮，先找出至少能在单次用户请求中省下 `0.05 s` 且达到 wall `1%`
的真实主相位，再实现最小候选。
