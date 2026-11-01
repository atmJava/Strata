# AGENT-HOT-TURN-CPU-SHARE reachability result

日期：2026-11-01

状态：env-only 候选否决。机制在无 batch 的隔离实例有约 2% 信号，但现役生产
`--batch 6` 使该开关不可达，不能进入生产性能或质量矩阵。

## 判决

现役生产 engine 实际包含 `--batch 6`。源码创建 CPU share pool 的条件是：

```cpp
const bool share_pool = o.batch <= 0 && !o.no_pool;
```

因此只设置 `STRATA_PREFILL_CPU_SHARE=auto`、`STRATA_PREFILL_CPU_SHARE_MAX=1024`
不会给 A1 的 prefill stage 挂上 CPU pool。原计划“生产 env-only 重启后测 1%”没有
可达路径，已取消，没有为它重启生产。

隔离实例仍回答了“机制值不值得源码接线”：同 engine、同源码、同 layer split 24，
`batch=0`、1 路；B0 为 `STRATA_PREFILL_CPU_SHARE=0`，B1 为 `auto`、max `1024`、
`STRATA_DBG_CPU_GATE=1`。两臂 expert cache 同为 `20,693` slots。

## 结果

3 波 hot 配对：

| 1 路 | B0 | B1 | 改善 |
|---|---:|---:|---:|
| hot-short prompt | `395.0 ms` | `380.0 ms` | `3.797%` |
| hot-short wall | `0.507168 s` | `0.492020 s` | `2.987%`，`15.148 ms` |
| hot-long prompt | `1207.3 ms` | `1179.2 ms` | `2.328%` |
| hot-long wall | `1.316394 s` | `1.290809 s` | `1.944%`，`25.585 ms` |
| hot-long decode | `91.7 ms` | `91.3 ms` | `0.436%` |

配对稳定性：hot-short 三波为 `-1.433%/+2.987%/+3.424%`，hot-long 为
`-4.249%/+1.944%/+3.371%`。首波反向，后两波同向；这只是机制信号，不是生产判决。

质量门：B1 9/9 请求成功，期望回复全对，finish 全 `stop`，零外来请求，窗口新增
Xid `0`。两臂 config 只差 CPU share 相关开关。

## 路径证据

共享 engine 日志第一次截取写错，外层 shell 提前展开了偏移，两个 `*-engine.log` 都复制了
整份 live log。该文件不能用作路径证据。随后按保存的字节偏移重新截取：

- `B1-engine-tail.log`：`44,345` bytes、264 条 `cpu gate`、128 条精确 `shared,`、
  136 条 `not shared,`；证明 B1 真实走到 CPU gate 和共享层。
- `B0-engine-tail.log`：`22,628` bytes、0 条 `cpu gate`；证明对照没有 share 日志。

以后必须使用远端不展开的偏移、记录截取大小和 hash，并核对 `not shared` 不会被
`grep ' shared,'` 误计为共享。正式正则使用：

```text
^cpu gate: layer [0-9]+ shared,
^cpu gate: layer [0-9]+ not shared,
```

## 为什么不能直接改生产

生产在 `--batch 6` 时可能在独立线程读取新 prompt，同时在其他 slot decode。两边都使用
同一个 `ExpertPool`；现有 `g_cpu_pool_busy` 只串行 layer split 的两个 prefill stage，
不保护 batch decode 的 pool call。直接把 `o.batch <= 0` 放宽会让两个调用者同时改
`ExpertPool` 的 batch、jobs 和 scratch 状态。

下一候选必须先证明所有权协议：

- 无并发 decode 时，prompt chunk 可以取得 CPU pool；
- 有并发 decode 时，prefill 不抢 pool；
- prefill 持有 pool 时，新 decode window 延后或走纯 GPU，不能阻塞数百毫秒；
- 所有 pool 调用都有明确 owner，失败和取消路径释放 lease；
- 未取得 lease 时退化为原 GPU 路径，输出与 `CPU_SHARE=0` 等价。

做不到无阻塞仲裁就关闭该方向，不重试 env 份额。

## 证据

远端目录：`/opt/ai/logs/agent-cpu-share-reach-20261031T213602Z/`

- B0 config SHA-256
  `8f68960509675ac2dd01b20c1b9a55b6d0cdbf0abd5af6802efb64d080a87775`
- B1 config SHA-256
  `b6da134f665832763c38198e05dbf4c3a437df9f30f51638cddd955e48fd44b7`
- engine SHA-256
  `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`
- B1 tail SHA-256
  `dd5e701270d9d229404e98b52885cc6bd1b73792a881d39640b984addb9027b8`
- B0 tail SHA-256
  `e6be2f29dd515ef565cb9c8e8ffe8e8902dc8b8a95d978c813bc0757b7d1ee66`

## 未测

- 没有把无 batch 收益外推到生产 `batch=6`；
- 没有实现或运行 batch CPU-pool ownership guard；
- 没有跑 2/4/6/8 路生产矩阵或真实 DSH 质量；
- 没有扫描固定 share、CPU worker 数或 `CPU_SHARE_MAX` 邻近值。
