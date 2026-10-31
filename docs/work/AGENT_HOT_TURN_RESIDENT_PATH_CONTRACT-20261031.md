# AGENT-HOT-TURN-RESIDENT-PATH contract

日期：2026-10-31

状态：暂停验收。默认关闭；构建、路径和正确性门通过，但预注册的绝对 `0.05 s`
性能门未过，4/6/8 非回退门未完成。按用户要求暂停优化，保留实验证据但不进入生产。

## 问题与证据

生产 596/94K 热长轮的 554-token fresh chunk 走 staged MMQ。当前每个 MoE 层把约
512 个唯一 routed expert 按 16 个一组 gather 到固定 stride 的 group buffer；phase map
中两 stage 的 `dequant` 实际就是该 gather，分别约 `77 / 106 ms`。

在现役服务占用的 GPU 上，用 16 个真实 blob 承载 512 个 pointer 位置的 group 级
harness，保留生产 `554 x top-10` 的 512-expert 路由和行数分布：

| 单层路径 | 中位 |
|---|---:|
| 生产同形 16-expert group gather + MMQ + fused SwiGLU/H quantize | `11.671 ms` |
| `fused::experts_native` per-expert direct pointer + 4 个 128-expert launches | `7.848 ms` |

单层差 `3.823 ms`，48 层乐观上界 `183.5 ms`；按 A1 约 `1.51 s` wall，为 `12.1%`。
这与 phase map 中 gather `183 ms` 的整请求量级一致，因此不是 microbenchmark 噪声。
fused 对 MMQ 的 rel RMS 为 `2.020e-4`、worst row `3.096e-3`，落在现有
`prefill_fused_iq_test` 门内。

## 候选

只给短 chunk staged 路径增加两个显式 env：

```text
STRATA_PF_FUSED=1
STRATA_PREFILL_FUSED_STAGED=1
```

默认均关闭。候选只在一个层同时满足以下条件时走 native fused：

1. `T < stream_all_min()`，当前生产为 `554 < 1024`；
2. pack 为 native IQ3_XXS gate/up、IQ4_NL down，且当前 GPU 已通过 native setup；
3. 无 peer/helper expert、无 CPU-share expert；
4. 所有 routed expert 都有 resident cache slot 或可 staged source；
5. 本层唯一 non-resident expert 数不超过现有 `STAGE_GRP=32`。

满足时先把该层 non-resident experts 全部装入现有 32-slot staging ring，只等最后
一次 copy；输入激活每 token 量化一次，GPU grouping 一次；按至多 128 个 expert 一个
launch 直接把 resident/staged pointer 交给 `fused::experts_native`，在最后一个
fused launch 后统一释放 ring。否则该层完整回退原 MMQ 路径，不部分混用。

不改变 expert cache  residency、host staging 顺序之外的选择、routing、combine、
token 选择或 finish；不增加默认 VRAM。

## Arm

- A1：现役 server/vision/engine `169846/169849/169854`，无候选 env。
- B：同一 config 深拷贝，只增加上述两个 engine env；使用隔离构建的新 engine。
- A2：同一新 engine，移除两个 env，核对默认路径与 A1 的固定负载行为。

启动后核对实际 env、engine/config hash、health loaded、262144、六 slots / 两 groups、
无 systemd 接管、无 peer/helper、资源余量。B 日志必须出现 native fused 层计数，且
fallback 原因可计数；若 596/94K 的层全部 fallback，停止，不跑性能。

## 固定负载与门

使用现有 `bench-hot-turn-fixed-cost.py` 的同口径 596/94K hot-long，每臂三波：

- A1/B/A2 的请求错误为零，finish、tool、代码修改和测试语义不变；
- 至少两组固定 greedy 在候选内逐字一致，并等于 A；64-token smoke 通过；
- 至少一个代表形状 wall 中位改善 `>=1%` 且绝对 `>=0.05 s`；
- 596 与 94K 都不得反向超过 `1%`，A2 对 A1 漂移不超过 `3%`；
- 1 路通过后报告 2 路第二 TTFT、单请求 wall 和跨度；4 路不得回退超过 `1%`；
  6/8 路零 HTTP/执行错误、health loaded、无 OOM/严重资源退化；
- 请求期间不改 systemd production config，不写 prompt、回复、reasoning、工具正文或
  API key。

如果单层/单请求 smoke 已经出现错误输出、fallback、资源越界或 `wait copy` 反向，
立即停止并恢复 A1，不跑并发矩阵。

## 重启成本与回滚

一次候选启动加一次 A2/生产恢复约 4–6 分钟；隔夜 page cache 冷启可能更久。只有
group 级 `12.1%` 上界和真实路径命中足以覆盖该成本才支付。

失败时从 A 的 config 快照逐字节恢复，SIGTERM 手动实例并从原 config 启动；核对
engine/config hash、health、六 slots / 两 groups、systemd inactive，以及 engine env
不含候选变量。远端用户 dirty/untracked 文件保持不动，禁止 reset/clean/checkout。

## 暂停记录

596 的 1 路 wall 相对 A1 改善 `2.145%`、绝对 `32.667 ms`，三波全胜；相对 A1/A2
合并基线为 `1.862%`。按用户“1% 多也算”的原则这是正向信号，但没有静默放宽本契约
原先的绝对 `0.05 s` 门。94K wall 的差值没有对应 prompt 改善，不能归因给 prefill。
4 路 A2 测量在约 `1:44` 时因用户暂停而中止，没有结果文件，6/8 未运行；随后已恢复
原 A1 生产。完整结果见
[`AGENT_HOT_TURN_RESIDENT_PATH_RESULT-20261031.md`](AGENT_HOT_TURN_RESIDENT_PATH_RESULT-20261031.md)。
