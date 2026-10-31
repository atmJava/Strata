# AGENT-HOT-TURN-STAGED-PREFETCH result

日期：2026-10-31

判决：**否决。**

## 结论

B 把 grouped staged walk 的实际传输 lookahead 从默认 8 提到安全上限 15，真实路径、实际
env、二进制/config hash、health、六 slots / 两 groups、资源、A1/B/A2 固定 greedy 和
64-token smoke 全部通过。但 596 hot-long wall 从 A1 中位 `1.510583 s` 变为
`1.505333 s`，只快 `0.3475%`、绝对 `5.25 ms`；prompt 从 `1384.3 ms` 变为
`1392.6 ms`，反而慢 `0.5996%`。

这不足以证明更深 lookahead 把 copy 等待转化成了用户收益。按预注册停止门，未测 B 的
94K、2 路或 4/6/8，也不扫描 9/10/12/16、stager thread/batch/ring 或 stream threshold。

## Arms 与路径

- A1：原生产 engine/config；596 wall 三波
  `1.516392 / 1.510583 / 1.486344 s`。
- B：隔离源码副本重新构建的 engine，config 只替换 `exe` 并增加
  `STRATA_PREFILL_STAGED_AHEAD=15`；596 wall 三波
  `1.519241 / 1.505333 / 1.498708 s`。
- A2：恢复字节相同的原生产 config/engine。

B 实际进程路径为隔离 build 的 `strata`，实际 env 与 config 一致，不含
`STRATA_PREFILL_TIMING`、`STRATA_VERIFY_PROFILE` 或 `STRATA_DECODE_TIMING`。
health loaded、262144、六 slots / 两 groups、systemd inactive；无 OOM、CUDA、engine
或 HTTP 错误，RAM/VRAM/swap 安全。

A1/B/A2 的三组 fixed greedy 各两轮均逐字重复、逐字相等，token 数和 finish 一致；
64-token smoke 为 `stop`。

## 实测

1 路 596 hot-long：

| arm | wall repeats (s) | wall median (s) | prompt repeats (ms) | prompt median (ms) |
|---|---|---:|---|---:|
| A1 | `1.516392 / 1.510583 / 1.486344` | `1.510583` | `1384.3 / 1385.7 / 1376.8` | `1384.3` |
| B | `1.519241 / 1.505333 / 1.498708` | `1.505333` | `1394.9 / 1392.6 / 1388.4` | `1392.6` |

wall 改善 `0.3475%`（`0.005250 s`），prompt 慢 `0.5996%`（`8.3 ms`）。两个门均未
达到；A1 的 94K 已在同 boot 记录为 wall 中位 `1.416019 s`、prompt `1227.3 ms`，但 B
未跑 94K，因此不作跨臂结论。

## 生产恢复

生产已恢复原 config/engine。最终 server / vision / engine 为
`169846 / 169849 / 169854`；engine
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`，config
`6ece1a13f4ac2d875bdafd903c7aba23fdce6be5ccc387e17b987a130a486811`。health loaded、
262144、六 slots / 两 groups，`strata.service` inactive、`MainPID=0`、`NRestarts=8`；
engine 实际 env 不含本候选或 profiling 变量。A2 三组 fixed greedy 全部
repeatable/exact，finish `stop`。

## 证据

目录：

```text
/opt/ai/logs/agent-hot-turn-staged-prefetch-20261031T1445Z/
```

关键 SHA-256：

```text
A1-596.json         ffd5c768c143fe24a4d4a46f58f2bae30d31752ff1855d1ebe6e894445f3e1a3
A1-94K.json         9a81b493ec6891d20bc6e4df88430c3b0e5316cd0a70c6367cca6d079aabe94b
B-596.json          3c460094d02618b5a4d74d4633129cb141df2b674ac608e6d21035326f917eb7
B-config.json       96d4d0a3a5867a7d4355807dac14df62d0c82857d9b46d11c41630c89f4a720a
B-parity.json       1c293c0980b37323534c8d70af6d22fbb92f1f4fab7e9fcce21846a5c88f6664
B-smoke.json        787508dd13e2b639779d740eb04df3cc1b4f2f3f635ed09817c1190025933943
A2-parity.json      bd7a3c1391aa567e39f8bedcb60821af99d3966d88e07699f7e5e4996c5f2963
```

候选 binary：

```text
a72f678d36fe3d0aa3f5791d61c40939ea91c605021d498a0eb19bd6dffa1382
```

## 复盘

增大 lookahead 没有减少 prompt，说明 `wait copy 173 ms` 不能仅靠多排几个传输隐藏，
该参数轴关闭。下一项回到 phase map 的下一个可证伪问题：`dequant 106 ms` 是否存在同一
expert/layout 被重复转换，以及删除重复工作能否给出至少 `1%` 且 `0.05 s` 的保守上界。
该审计只读，不改服务、不重启；未证明真实触发和整请求上界前不构建。
