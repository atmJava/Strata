# AGENT-HOT-TURN-STAGED-PREFETCH contract

日期：2026-10-31

状态：**已否决。** B 的 596 wall 只改善 `0.3475%`、绝对 `5.25 ms`，prompt 慢
`0.5996%`；按停止门未跑 B 的 94K 和并发矩阵。生产已恢复 A2。完整结果见
[`AGENT_HOT_TURN_STAGED_PREFETCH_RESULT-20261031.md`](AGENT_HOT_TURN_STAGED_PREFETCH_RESULT-20261031.md)。

## 只读审计结论

上一项 full-ring 测试不能证明“只预取 routing 选中专家”是一个尚未启用的机制：

- 生产 staged walk 在每层 router 完成后下载 top-k ids，按实际选中专家建立 `order`，再启动
  本层 stager；它已经只拷贝本层 routed、非驻留专家。
- 后续层的 routing 依赖当前层输出，源码没有提前得到整 chunk 所有层 top-k 的路径。因此跨层
  按 selected expert 预取不成立，除非另外实现 speculative routing，超出本轮 scope。
- 554 fresh token x top-10 是 `5,540` 个 routed pairs，而每层只有 512 个专家，平均每个
  专家约 `10.8` 行；这不是“少量专家”形状。
- `STRATA_PREFILL_STREAM_MIN=0` 不只加深 ring：它切到 whole-chunk streamed walk，关闭
  staged 路径的 shared-expert defer，并改用 cross-layer stream/issuer。B 慢 `25.714%`
  证明该 whole-chunk 分支不适合这个形状，不能归因成单一 ring 深度结论。

当前 staged walk 的真实可动项是 lookahead。源码默认在 MMQ grouped staged path 中保持
**8 个实际传输**在前，而不是 15 个专家位置；stage 2 phase map 的 `wait copy` 三波为
`173/179/164 ms`，中位 `173 ms`。

## 假设与上界

staged ring 有 `STAGE_GRP=32` 个 slot。MMQ 当前 group 最多持有一组的 16 个 staged slot，
直到 gather 和 release；因此最多还能安全保持 `32-16-1=15` 个后续实际传输。

候选只把该路径的已在前传输数从 8 提到 15，保持：

- 同一层的 routed id 顺序与 resident/streamed 选择；
- 同一 MMQ group、gather、copy stream、release event 和 kernel 顺序；
- 同一 shared-expert defer；
- 默认仍为 8；仅显式 env 打开 15。

这是调度重叠改动，不改变每行算术、top-k、token 选择、finish 或工具语义。理论上只要隐藏
`173 ms` 中的 `15.1 ms`（`8.7%`）就达到 596 wall 的 1%；隐藏 `50 ms`（`28.9%`）
达到绝对 `0.05 s` 门。若深度提高没有降低 `wait copy` 或 wall，这条 staged 参数线停止。

## Arms

- A1：当前生产 engine/config；
- B：候选 engine，只增加
  `STRATA_PREFILL_STAGED_AHEAD=15`。源码默认 8，不设置 env 的路径字节相同；
- A2：恢复 A1 的 engine/config，量跨 boot 漂移。

模型、pack、双卡 layer split 24、262144 context、六 slots、两 groups、MTP、cache、
`--ple-inflight 1024` 和生产 env 不变。B 不启用 profiling env。

## 启动与正确性门

- B 的实际进程路径指向候选 binary，engine/config hash 与记录一致；
- 实际 env 只有该调度 delta，源码路径在 native IQ3_XXS、554-token、`T<1024`、
  grouped staged walk 上可达；
- health loaded、262144、六 slots / 两 groups、systemd inactive；
- 无 OOM、CUDA/engine/HTTP 错误，RAM/VRAM/swap 安全；
- 三组 fixed greedy 各两轮，B 内逐字重复且等于 A；
- 64-token smoke 正常。

## 性能门

同口径 596/94K hot-long，每臂三波：

- 至少一个 shape 的 wall 中位改善 `>=1%` 且绝对 `>=0.05 s`；
- 两个 shape wall 不反向；
- A1/A2 漂移 `<=3%`；
- prompt、cached tokens、finish、health 和资源门全过。

1 路通过后测 2 路的单请求 wall、第二条 TTFT 和完成跨度；4 路非回退，6/8 只做安全回归。

## 停止、成本与回滚

构建与 A1/B/A2 启动合计约 10–15 分钟，另加三臂测量。若 B 的 596 wall 没有 `>=0.05 s`
改善，立即停止，不扫 9/10/12/16 等邻近深度、stager thread/batch/ring 或 stream threshold。
若实际 `wait copy` 不降而 wall 反向，也直接否决。

候选默认关闭；删除 `STRATA_PREFILL_STAGED_AHEAD` 或恢复原 binary 即可精确回滚。完成后必须
恢复 A2，核对 binary/config hash、实际 env、health、六 slots / 两 groups、systemd inactive
和 fixed greedy parity。
