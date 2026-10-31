# AGENT-HOT-TURN-DEQUANT-AUDIT result

日期：2026-10-31

判决：**否决“重复反量化/gather”假设。** 没有发现可以按单次请求安全删除的重复
expert/layout 转换，因此不构建、不重启，也不扫描 dequant、group 或 profiler 参数。

## 结论

phase map 把这一段命名为 `dequant`，但它不是 native IQ3_XXS 的 FP16 反量化。生产
554-token、`T<1024` grouped staged path 在
`src/prefill/prefill.cpp:4304-4321` 进入 `mmq::gather_native_group()`：

- gate/up/down 的 GGUF 量化字节按最多 16 个 expert 一组，从各自 blob 复制到连续组
  buffer；
- `src/prefill/moe_mmq.cu:339-357` 的 `copy16_group_kernel` 只做字节搬运和 tail 清零；
- 后续 MMQ `Product` 只用固定 `w + expert * expert_bytes` 访问连续权重
  (`src/prefill/moe_mmq.cu:257-281`)，不读取任意 per-expert 指针表。

因此这个 phase 的主要成本是 resident/cache 或 staged blob 到 MMQ 连续布局的搬运，
不是同一份数值被反复反量化。

## 重复性审计

源码控制流给出以下确定性结论：

1. 每层 routed `order` 从 `e = 0..511` 扫描一次，仅把 `cnt[e] > 0` 的 expert 放入；
   所以同一层每个 selected expert 只出现一次 (`prefill.cpp:3793-3800`)。
2. 每个 `order` 位置调用一次 `compute()`；native grouped path 每 16 个唯一 expert 执行
   一次 `flush()`，生产 native blob 的指针、offset 和 stride 都满足 16-byte 对齐，
   因而每层 gather 调用数是 `ceil(unique_routed_experts / 16)`，不会按 routed pair 重复。
3. stage 1 和 stage 2 分别处理 layer split 的不同半模型；同一 expert ID 在不同层的
   layout/权重不同，不是同一份可复用权重。
4. staged、streamed、fused、peer 路径互斥。当前 `stream_all_min=1024`、
   `T=554`，实际是 staged；`T<stream_all_min` 还使 stage-helper/peer-stream 路径不成立。
5. 同一层只有一个 554-token chunk，group buffer 按 stream 顺序复用；后续 MMQ 已读取后
   才覆写，不存在并行生命周期重叠。

554 x top-10 共有 `5,540` 个 routed pairs，但 gather 按唯一 expert 分组。生产 hot-long
的三波 expert-cache hit 为 596 `95.1% / 97.8% / 98.1%`、94K
`98.4% / 98.7% / 98.8%`；高命中意味着大多数输入来自 resident cache，但这仍需要转换成
MMQ 的连续 gate/up/down 组布局，不能当作“重复转换”删除。

## 上界与未采纳项

stage 1 / stage 2 的 `dequant` 中位分别为 `77 / 106 ms`。若整个 phase 都能消失，理论上
合计 `183 ms`，足以超过 `1% + 0.05 s` 门；但这需要新的 direct-pointer MMQ 接口或把
resident experts 改走原生 fused kernel，属于独立机制，不是删除已验证的重复工作。

现有 cache slot 也不能直接代替组 buffer：native slot 有按层变化的 offset，gate、up、
down 在 blob 内分开存放，而 MMQ 的 gate/up 与 down product 各自要求连续、固定 stride。
在不改 MMQ 权重寻址契约的前提下，无法安全地把任意 cache slot 指针替换进 `Product::w`。

作为低成本预筛，尝试用现成 `prefill_fused_iq_test --chunks=554 --no-ref` 比较完整
MMQ 路径与 native direct-pointer 路径；该测试需要为 512 个 expert 分配约一整层权重，
在现役生产占用下 `cudaMalloc` OOM。该失败不构成性能结论，也没有为它停服或重启。

## 下一步

`AGENT-HOT-TURN-RESIDENT-PATH-AUDIT`：

- 先在不分配完整层的 group 级 harness 中测 native direct-pointer kernel 与
  gather+MMQ 的单位 expert/行成本；
- 统计 representative 554-token routing 的每层唯一 expert、group 与 resident/staged
  比例，避免再用 pair 命中率代替唯一权重命中率；
- 只有 direct path 按真实 resident 比例给出整请求 wall `>=1%` 且绝对 `>=0.05 s`，
  并通过数值、finish、工具语义和资源门，才预注册实现；
- 若 group 级速度或质量不成立，关闭 resident-gather 路径，不再改 dequant/group 参数。

## 生产状态

本项全程只读和独立 GPU harness，未改源码、未重启。生产仍为
server / vision / engine `169846 / 169849 / 169854`；health loaded、262144、
6 slots / 2 groups；engine `105a4171...`、config `6ece1a13...`；`strata.service`
inactive、`MainPID=0`、`NRestarts=8`，engine env 不含 prefill/dequant profiling 或
staged-prefetch 变量。
