# AGENT-HOT-TURN-CPU-SHARE contract

日期：2026-11-01

状态：已执行。env-only 候选在现役 `--batch 6` 路径不可达；无 batch 隔离实例证明机制
有约 2% 信号，但不足以采纳生产。后续只允许带并发所有权保护的源码候选，见
[结果](AGENT_HOT_TURN_CPU_SHARE_REACH_RESULT-20261101.md)。

## 问题

热长轮 `T=865` 的 48 层中有 45 层全部驻留，剩余 layer `35/44/47` 仍需
`83–141` 个 non-resident expert，stage 2 `wait copy` 约 `173 ms`。把 copy 与 GPU
compute 再分批没有收益，说明 PCIe/搬入仍是瓶颈。

源码已有 `STRATA_PREFILL_CPU_SHARE`：把低于阈值的 staged chunk 中部分非驻留专家交给
空闲 CPU expert pool，减少 H2D copy 与 GPU expert work。它在单 GPU、无 batch 的路径上
按默认开启，但 layer split 当前明确关闭。源码记录的同类 512/600/1000-token 测量为：

- RTX 3090/P100/5070 的 512 和 1000-token pair `8/8` 胜，快 `20–35%`；
- RTX 5090 + 9950X3D，600-token Q2_0 `472–491 → 445–448 ms`；
- IQ2_XS 两 AVX2 worker `512 → 487 ms`；
- RX 7900 GRE + 5700X3D 全部失败，CPU 抢占了 host issue，使 streamed expert 成本
  `107 → 200 us`。

本服务器是 Xeon E5-2680 v4，28 threads；因此必须实测 host issue 竞争，不能直接外推
其他 CPU/GPU 组合。

## 候选

只增加两个 engine env，不改源码、量化、routing、slot 或 GPU 分配：

```text
STRATA_PREFILL_CPU_SHARE=auto
STRATA_PREFILL_CPU_SHARE_MAX=1024
STRATA_DBG_CPU_GATE=1
```

`MAX=1024` 保证只影响 staged `T < 1024` 的普通热续轮，不影响 `T=4236/6253/6400` 的
冷 prompt。`DBG_CPU_GATE` 只用于运行态证据，采纳时移除。两份 card 仍做原有 layer split；
同一时刻只有一个 stage 可取得 CPU pool。

CPU expert 算术与 GPU 不一定 bitwise 相同；源码记录同类默认 share 的平均 KL 约
`0.004`。因此不能用 GPU 逐字 parity 作为唯一门，必须使用真实代码/测试质量门。

## 路径门

- health loaded、262144、6 slots / 2 groups、两卡 layer split、systemd inactive；
- 实际 env 三个值正确；
- 热长轮和 hot-short 日志都有 CPU gate 读数与 `shared` 层，CPU 实际接管非驻留专家；
- cold `T>=1024` 不进入 CPU share；不能把冷 prompt 的行为混入结论；
- 零 OOM、零 engine 退出、零新 Xid、RAM/swap 安全。

没有真实 CPU share 命中时立即停止，不跑性能矩阵。

## 性能门

与当前生产 engine/源码一致，A1-B-A2 或独立候选/恢复：

- 1 路 hot-long wall 中位改善至少 `1%` 且至少 `0.015 s`；
- server prompt 与 wall 同向，不能由 decode 漂移单独贡献；
- hot-short 不反向超过 `1%`；cold、94K 不反向超过 `1%`；
- 2 路报告第二 TTFT、单请求 wall 和跨度，不得恶化 >1%；
- 4 路不退化超过 `1%`；6/8 零 HTTP/执行错误、health loaded、资源安全。

如果只在 1 路生效但 2 路明显回退，可按并发级选择 auto/off，但不能静默改变 4/6/8
默认路径。

## 质量门

性能过门后再跑真实 DSH coding fixtures：

- 代码和受保护测试不退化，最终 sentinel 不下降；
- 6 个基础 fixture 的测试保持 `6/6`；
- 事件/工具错误为零，finish 无意外 `length`；
- 至少重复一次，不能把单次随机通过当质量；
- 新增 Xid 为零。

任何正确性、工具协议或 final-message 回归都否决，即使 wall 改善超过 1%。

## 回滚与停止

移除 `STRATA_PREFILL_CPU_SHARE` / `STRATA_PREFILL_CPU_SHARE_MAX` /
`STRATA_DBG_CPU_GATE` 并恢复 A1 config 即可。失败后不扫描 `0.1/0.2/0.3...` 份额或替换
CPU worker 数；只有日志证明 auto 选择错误时才重新设计测量公式。

预计一次候选启动加一次恢复约 5–7 分钟；源码记录的上界远高于成本，允许本轮测试。
