# AGENT-HOT-TURN-STAGED-STREAM-PIPE contract

日期：2026-11-01

状态：否决。默认关闭；路径和正确性门通过，但同源码 hot-long wall 慢 `0.721%`，
prompt 慢 `0.136%`，未达 1%。候选代码已移除，见
[`AGENT_HOT_TURN_STAGED_STREAM_PIPE_RESULT-20261101.md`](AGENT_HOT_TURN_STAGED_STREAM_PIPE_RESULT-20261101.md)。

## 问题

真实 DSH 热长轮的 `T=865` chunk 中，48 层里只有 layer `35/44/47` 支持 native
IQ3_XXS/IQ4_NL fused kernel。三波审计的 non-resident expert 数分别为：

| layer | 三波 staged |
|---:|---:|
| 35 | `117/123/114` |
| 44 | `130/132/141` |
| 47 | `92/80/90` |

其余 45 层 staged 全部为 `0`。当前 grouped staged 路径的 stage 2 `wait copy` 约
`173 ms`；full-layer native fused compute 只有 `7.848 ms/layer`。现有
`STRATA_PREFILL_FUSED_STAGED=1` 要求所有 non-resident 专家一次放入 32-slot ring，
所以这些真实层全部回退。

## 候选

新增默认关闭的独立环境变量：

```text
STRATA_PREFILL_FUSED_STAGED_PIPE=1
```

只在现有 native、无 peer/CPU 分摊、无 resident sort、`T < stream_all_min()` 且 pack
受 native fused 支持的层生效。使用现有 `32` 个 staging slot 做双缓冲，把该层 routed
experts 按 expert id 切成至多 `16` 个 non-resident expert 的小批，每批：

1. `stage_one()` 发出本批 copy；
2. compute stream 只等待本批最后一份 copy；
3. 调用一次 `fused::experts_native`；
4. 在该批 fused launch 后记录 ring release event，供后续批复用。

copy stream 可按批继续排队；上一批 compute 与下一批 copy 可以重叠。resident expert
仍直接使用 cache pointer。不得改变 routing、expert id 顺序、expert 算术、row 归属、
combine、token 选择或 finish。

该开关不改变 `STRATA_PREFILL_FUSED_STAGED=1` 原来的 `<=32` 行为。没有新开关时源码和
运行路径保持现状。若三批中的总专家数接近 512，批次数只由实际 routed/non-resident
集合决定；不支持 native pack 的 45 层仍走原路径。

## 启动与路径门

- 两卡 layer split、6 slots / 2 groups、262144、health loaded；
- 实际 engine env 含 `STRATA_PF_FUSED=1`、`STRATA_PREFILL_FUSED_STAGED=1` 和
  `STRATA_PREFILL_FUSED_STAGED_PIPE=1`；
- 热长轮日志证明 layer `35/44/47` 进入新小批路径，且每次 batch 的 staged 数不超过
  `STAGE_GRP / 2 = 16`；
- layer release event 在该批 fused launch 后才记录，后批复用 slot 时 copy stream
  等待前批 compute；
- 无 peer/CPU/sort 或 native unsupported 的层路径不变；
- 零 OOM、零 engine 退出、零新 Xid。

任何一项失败都停止，不跑性能矩阵。

## 同源码对照

- B0：候选源码，`STRATA_PF_FUSED=1` + `STRATA_PREFILL_FUSED_STAGED=1`，pipe 关闭；
- B1：同一源码，只增加 `STRATA_PREFILL_FUSED_STAGED_PIPE=1`；
- A：生产源码/配置，只用于回归和外部有效性，不承担 B1-B0 的机制归因；
- B0/B1 使用相同 build 选项、同一 config、独立端口；比较使用三波 hot-long。

## 正确性与性能门

同源码 B0/B1：

- 三组固定 greedy 各重复两次，逐字相等；finish 全 `stop`；
- 64-token smoke 和请求期望回复全过；
- 热长轮 wall 配对中位至少改善 `1%` 且绝对至少 `0.015 s`；
- 热短轮、94K 和 cold 不反向超过 `1%`；
- B0/B1 的引擎 prompt 和 wall 同向，不能只靠 decode 漂移；
- 1 路通过后补 2 路第二 TTFT/单请求 wall/跨度；4 路不回退超过 `1%`；
  6/8 零 HTTP/执行错误、health loaded、资源安全；
- 请求期间不改 systemd、生产 config 或用户 DSH profile。

## 上界与停止条件

保守收益只计可重叠的 fused compute：`3 layers × 7.848 ms = 23.544 ms`，相对
`1.51 s` wall 约 `1.55%`。copy 时间仍是关键路径，不把 `173 ms` 全算成可回收。

如果 runtime 超过 32 的层无法安全分批、copy stream 已无可重叠空间、日志没有命中
三层、或者 B1-B0 中位低于 `1%`/`0.015 s`，立即否决。失败后不扫描 16/24/48/64
批大小、ring、stager thread/batch 或邻近阈值。

## 回滚

移除 `STRATA_PREFILL_FUSED_STAGED_PIPE=1` 即可回到 B0；生产恢复 A1 config、原 engine
和原 server，核对 engine/config/server hash、health loaded、262144、6 slots /
2 groups、systemd inactive 及新增 Xid。
