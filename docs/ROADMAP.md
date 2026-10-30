# 优化路线与指标

> 这是动态路线组合，不要求按表从上到下执行。AI 每轮按 `RULES.md` 重新比较价值、成本、信息量和风险。
> 最后更新：2026-10-31T21:50+08:00。

## 用户目标

在不牺牲正确性和可恢复性的前提下，提高真实硬件资源利用率，并把它转化为更短的任务完成时间、
更低的并发尾延迟和稳定吞吐。参数调整只作为代码改动的验证手段，不作为独立探索方向。

## 当前指标

| ID | 指标 | 最近基线 | 目标 | 状态 |
|---|---|---|---|---|
| M1 | 4 路聚合 tok/s | 数字 `139.92`、自然语言 `129.03`（`c219fbca`） | 不劣于同负载基线 3%，并保留单路/2 路效率 | 已建立新构建基线 |
| M2 | 单路 engine decode tok/s | 数字 `104.05`、自然语言 `68.73`（`c219fbca`） | 不劣于同负载基线 3% | 已建立新构建基线；2 路有凹点 |
| M3 | 22.7K 冷读首 token | 17.9 s | ≤读时间 + 2 s | 达成 |
| M4 | 长读时邻居最长静默 | 7.09–7.59 s（S0b ON 中位）；3.43 s（R11） | ≤1 s | 明显未达 |
| M5 | 满槽短读首 token | 6 槽 11.84 s（400-token 邻居填充） | 不继续恶化，并有准入策略 | 已改善，条件待扩展 |
| M6 | 投机接受率 | 82.0% | ≥79.5% | 达成 |
| M7 | 前缀缓存命中 | T4 PASS | ≥50% | 达成 |
| M8 | 满槽晚到请求 | 6 槽 11.84 s；8 槽 14.38 s | 给出上界与准入策略 | 已测 |
| M9 | 未预期错误/限流 | 未系统测 | 0 未预期错误 | 未测 |
| M10 | 96K prefill 邻居下短请求 wall | 3.40 s 中位（P3 A-B-A） | ≤36.75 s（−10%） | 达成 |
| M11 | 96K prefill 邻居下 41-token 代码请求 wall | P3 E 臂 41.51 s；P4 旧候选 4.424 s；新引擎 A-B-A 5.074 s | ≤10 s，且 96K 按预先固定的 wave 与单请求门限均不退 >3% | 达成：wave +2.202%，单请求 +2.761% |
| M12 | 96K 冷 prefill GPU/host 成本 | 当前默认 v2 的 engine read 合并中位 40.27825 s、wall 40.5935 s；v1 fallback 为 41.0226/41.382 s | 找到并验证稳定、可归因的 ≥1% GPU 路径改善 | P6 复验通过：read `+1.814%`、wall `+1.905%` |
| M13 | 32K 冷 prefill GPU 管线占用 | SMs Active 77%、SM Issue 13–14%、Tensor 10%、DRAM read 15%；NVML 40/99% 与 95/98% | 用 NCU 定位 stall 后改内核/调度；瓶颈计数器和端到端 wall 同向改善 | Nsight 双次已验证；NCU 后进入代码优化 |
| M14 | 多并发吞吐曲线 | 数字 1/2/4/6/8 路中位约 `104.05/94.85/139.92/164.68/138.76 tok/s` | 2 路高于单路，8 路不回退且 TTFT 可控 | 2 路与 8 路均未达；6 路为峰值 |

数字的条件、证据和“没测什么”以 [`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md) 为准。

## 当前主要矛盾

修复版 `0.1.51` 已完成部署、运行路径 T-probe 和自身一小时 soak，618 请求的混合流量零错误、
swap 未用且 RSS 后段进入平台；staged 2048 与 P4 组合均已进入生产。新引擎同 boot A-B-A 中，
代码请求从 A 合并中位 41.1875 s 降到 5.074 s（−87.681%），96K by_kind/wave 为
+2.761%/+2.202%，短 JSON 3.980 s，全部在预注册门限内。`6×256K` 的版本 A-B-A 进一步显示
单路 decode −2.72%、4 路 −2.54%、冷读 −0.26%，均未越过 3% 门；4 路余量只有约 0.46 个百分点。
上游 v0.2.7 的 37 个提交随后 fast-forward 到 `c219fbca` 并部署；新构建的数字序列 6 路聚合
约为 `164.7 tok/s`，自然语言 6 路约为 `149.8 tok/s`，3,427-token 冷读约为 `928 tok/s`。
同轮发现 2 路数字序列低于单路、8 路低于 6 路且 TTFT 明显变差，后续优化必须保留完整并发曲线，
不能只拿 6 路峰值作为成功标准。
当前性能路线的主要矛盾转为：
96K 冷读仍需约 42 s，满槽晚到请求约 11–14 s，长读期间更广泛请求的尾延迟尚未覆盖。P5 已把
96K 冷读拆到 GPU event：phase 总墙钟 35.441 s，其中 `gemm gate/up` 5.807 s、`embed+steps`
3.692 s、`qsa attn` 3.422 s、`gemm down` 3.031 s；host staging 仅 0.341 s。P6 的
当前源码已默认使用 v2，本轮以 `STRATA_PA_V2T=0` 回退 v1 作对照：固定 96K prompt 的
engine read 改善 `1.814%`、wall 改善 `1.905%`，用户 1% 门复验通过。P7 原本用于验证
`STRATA_PF_WMMA=1` 对最大 `gemm gate/up` phase 的影响，但 A1 首个请求期间被用户中断，
没有有效样本。`CTX256` 一小时 soak 已通过并冻结 `0.1.51`，Nsight 双次显示 32K 冷 prefill
不是 DRAM 饱和，而是多数 SM 参与但 issue、Tensor 与 warp residency 不高。后续重点转为提高
真实资源利用率：NCU 只用于定位 stall，随后必须落到内核或调度代码改动。显式 layer-major
虽然真实执行，但六请求中位慢 14.29%，已否决。P6 已按 1% 门复验通过。真实 150K QSA 诊断
已完成：16-query overlap 中位 28.0%，FP16 全选择一致率仅 70.92% 因而否决；按预注册上界，
候选 attention 读取共享值得做一次最小实现。当前无运行中实验。P7 只有 NCU 证明 Tensor 管线
是直接瓶颈时才恢复，恢复后必须从全新 A1 重跑。

## 候选路线

| 候选 | 目标 | 预期收益 | 置信度 | 成本/风险 | 判断 |
|---|---|---|---|---|---|
| S2：验证每请求 metrics | M4/M5 可观测性 | 不直接提速，但区分 queue、TTFT、ITL、wall | 高，API 已有字段 | 低 | **已完成**：2/2 mock regression |
| S0b：长读与解码共存 | M4、M5 | 高；当前停顿约等于读时长 | 已证伪 | 中高，涉及引擎调度 | **否决**：机制执行但无收益；整体候选回退结论已由 L-082 撤回 |
| batch slot 共享 RoPE | 显存、expert 容量 | 双卡 6×192K 省 562.4 MiB，expert slots +1.8% | 高 | 低 | **采纳**；L-077 真实流量 soak 通过，吞吐不宣称提升 |
| C1 收尾：满槽晚到与邻居 | M5、M8、容量选择 | 6 槽 TTFT 11.84 s，8 槽 14.38 s；邻居静默 <0.61 s | 高 | 低中 | **完成**：默认保持 6 槽 |
| P1 聚合吞吐瓶颈 | M1 | 调度和层切分收口；恢复 HC_Q8 后受控中位 130.6 | 高 | 低 | **完成**：质量 11/11 |
| P2 HC_Q8/RoPE soak | M1/M3/M6 | 真实流量吞吐、缓存命中、长上下文和配置漂移均通过 | 高 | 低中 | **完成**：622 请求 0 错误 |
| P3 长 prefill 公平性 | M5/M10 | 短 JSON 由 40.21 s 降到 3.40 s，96K 只 +2.2% | 高 | 中 | **完成**：400 ms lead-in + 750 ms 等待 |
| P4 中请求公平性 | M11 | 让 41-token 代码请求也尽早完成，同时守住长档 | 高 | 中高，涉及两个重叠调度开关 | **采纳**：新引擎 A-B-A 代码 −87.681%，96K +2.761%/+2.202%；余量小 |
| P5 96K 相位归因 | M12 | 区分 GPU、host 与固定开销，给下一项实验定目标 | 高 | 低 | **完成**：GPU 主导，host staging 0.341 s |
| P6 Turing prompt-attention v2 | M12 | 直接替换 `qsa attn` 的 v1 路径，尝试减少 96K 冷读 | 中 | 低，env-only 且可回滚 | **采纳（当前默认）**：L-095 read/wall `+1.814%/+1.905%`，parity 与 needle 均过 |
| QSA-DIAG | 150K selection 诊断 | 读取 16-query overlap、FP16 一致率、差异 cells 和 select 成本 | 高信息量 | 低；优先只读诊断 | **完成**：overlap 中位 28.0%；FP16 一致率 70.92%，否决；候选复用上界 1.996%，进入最小实现 |
| QSA-UNION-ATTN | 相邻 query 共享选中 K/V | opt-in 且保持每 query 的 selection/attention 输出；长读 A-B-A 的 wall 与 `qsa attn` 同向改善 ≥1%，质量与资源门通过 | 已证伪 | 低 | **否决**：三 query 同 CTA parity/资源达标，但普通选择 +20.05%、最强共享 `QA_SAME=4` 仍 +20.75% |
| P7 Turing FP16 tensor-core prompt experts | M12/M13 | 用 tensor-core prompt experts 攻击最大 `gemm gate/up` 5.807 s | 中 | 低中，输出可能变化 | **搁置**：A1 中断；只有 NCU 证明 Tensor 是直接瓶颈时才恢复 |
| GPU-UTIL | M13 | 用硬件计数器替代单看 NVML；先验证采集口径，再做内核调优 | 高 | 低；只读、不重启服务 | **完成**：两次 Nsight 双卡 GPU Metrics + NVML 交叉核对可复现 |
| UTIL-NCU | M13 | 在独立受控 engine 上取得 SM throughput、occupancy、Tensor、DRAM 和 stall reason | 高 | 中；需短暂停生产、独占 GPU，完成后恢复 | **完成**：MIO `29.75%` 为唯一主因；Tensor `0.04%` |
| UTIL-KERNEL | M13 | 按 NCU 证据只改一个瓶颈内核或调度路径，提高 issue/Tensor/warp 占用并缩短 wall | 高 | 中；需要代码级验证 | **否决**：四副本 codebook `−0.084%`，仅 2/7 胜，bank conflicts 反升 |
| UTIL-ACT | M13 | 每个 k 只加载一次 q8_1 activation words/scale，供 gate/up 两个 row dot 复用 | 中高 | 中；隔离 GPU、7 组进程与 NCU | **否决**：编译器已复用，shared-load 指令完全不变 |
| UTIL-WEIGHT | M13 | 每个 k 只解码一次 weight-side codebook/aux/scale，复用于 NTC chunk 的多个 token | 高 | 中；隔离 GPU、7 组进程与 NCU | **否决**：`+0.546%`、6/7 胜，但 load 指令完全相同且寄存器升高 |
| UTIL-GRID-PAD | M13 | 四副本 codebook 使用 stride 257，使每个 replica 覆盖全部 32 banks | 已证伪 | 中；隔离 GPU、7 组进程 | **否决**：`489.3` 对 `475.7 us`（−2.86%），3/7 胜；不再扫副本或 stride |
| UTIL-WEIGHT-RO | M13 | IQ3 weight `qs`/aux 使用显式只读加载，降低 long-scoreboard stall | 已证伪 | 低中；SASS 检查、7 组进程 | **否决**：机器码已变为 `LDG.E.U16.CONSTANT.SYS`，但只改善 `0.339%` |
| UTIL-PIPED-MTP | M14 | 保留两组流水，用每槽 MTP 草稿修复低并发吞吐凹点 | 已证伪（当前形状） | 中；两次候选切换、2/4/6 路 A-B-A、显存检查 | **否决**：2 路 `+19.92%`/5-of-6，但 4/6 路 `-3.82%/-9.79%`，greedy 输出不重复 |
| UTIL-PIPED-MTP-CACHE | M14 | 只消除 slot drafters 对 expert-cache 的挤压，隔离 parity 混淆 | 已证伪（参数语义） | 中；一次 candidate 启动和一次恢复，先后做 parity 与 2/4/6 路 | **否决**：`--expert-cache-device1` 是额外 helper cache；双卡 layer split 在读取权重前 exit 2 |
| UTIL-PIPED-MTP-ORDER | M14 | 用 CPU-order expert 算术隔离 GPU/CPU expert 位置造成的小数舍入 | 已证伪 | 中；一次 candidate 启动和一次恢复，先后做 parity 与 2/4/6 路 | **否决**：6/2 正常 loaded，但自然语言探针仍两次不同且与 A 不同；未跑候选吞吐 |
| UTIL-BATCH-GROUP-FIT | M14 | 隔离 2 路吞吐凹点的 1-vs-2 group 调度成本 | 高信息量 | 中；补 A3 八路，一次 candidate 启动和一次 A4 恢复 | **下一项**：A 补 8 路；B 只把 groups 2→1；先 parity，再测 2/4/6/8；若仅低并发胜则否决固定配置 |
| LM-EXPLICIT | 长上下文 prefill | 显式 layer-major 用 96 MiB 余量启动 16K host-residual super-chunk；真实 150K/210K 出现稳定、可归因的 ≥1% 改善 | 中 | 中；每轮重启约 2.5–3 分钟 | **否决**：路径真实执行，但六请求中位 −14.29%；不再扫相邻 target |
| QSA slots 合并 launch | 4/6 路聚合吞吐 | 减少 batch window 固定 launch 开销 | 已证伪 | 低 | **否决**：4 路无收益，6 路首次收益不复现 |
| T-probe 版本戳 | 可观测性/部署正确性 | 避免继续读到旧 server | 高 | 低 | **已完成**：hash 与 markers 来自运行路径 |
| S8/S1/S7 回归测试 | 稳定性 | 主要防止已有能力回归 | 高 | 低中 | 作为同批低风险补充 |
| V3-2/V3-3 DFlash2 | 吞吐 | 未知 | 低 | 需要外部权重 | 阻塞，不空等 |

执行计划：[资源利用率优化计划](superpowers/plans/2026-10-27-resource-utilization-optimization.md)。
先完成 Task 1–6 的 NCU 根因与门限预注册；Task 7–9 必须等唯一根因产出后再进入。

## 当前路线决定

1. **S2 已完成**：流式与非流式响应都有可回归的 per-request metrics。
2. **S0b 已否决**：并发读 3/3 触发但没有改善邻居停顿；L-082 撤回“整个 0.1.51 回退”的临时概括。
3. **RoPE 共享已采纳**：先拿确定的显存收益，再用真实流量验证 expert hit、RAM exchange 和长上下文稳定性。
4. **C1 已完成**：同时测通 6/8 槽晚到请求和 slot-wait metrics；默认继续 6×192K。
5. **P1 已完成**：groups=2、split24/24 保留；合并丢失的 HC_Q8 已恢复，受控 4 路中位 130.6。
6. **P2 已完成**：一小时混合 soak 零错误，长档重复无热衰减，4 路合成中位 129.7。
7. **P3 已完成**：固定分片 32768/65536 分别把长档拖慢 15.6%/7.3%，否决；400 ms concurrent-read
   lead-in 加 750 ms 等待槽进入可解码状态，经 A-B-A 将短 JSON 降到 3.40 s，长档只 +2.2%，采纳。
8. **P4 参数探索（历史）**：1200 ms 固定 lead-in 只多推进约 23 个槽行，代码请求仍 41.93 s、长档 42.13 s，
   否决；2000 ms 虽在首次运行出现 5.25 s，重复 A-B-A 又回到 41.80 s，也否决。
9. **P4 旧引擎候选（历史）**：`STRATA_ADMIT_SHORT_FIRST=2048` + 1600 ms lead-in 的 1 小时 soak 零错误，
   代码 4.424 s、短 JSON 3.370 s；96K wave +2.53% 在门限内，但单请求中位 +4.14%、相邻 D2
   +3.21% 越界。该旧引擎结果不能直接定论，后来由第 13 项的新引擎严格 A-B-A 取代。
10. **QSA slots 已否决**：首次 6 路 +3.5% 未复现，最终相邻 on 臂还慢 0.49%；不再重复同配置。
11. **合并引擎门禁已通过**：修复版 `0.1.51` / `a09d78b1` 的 CTest、temperature 0 parity、
    版本 marker 与五臂 A-B-A 均满足门禁；唯一测试失败是服务器缺少 Q2_0 PLE 外部资产。
12. **新引擎与 staged 2048 已部署并 soak**：`a09d78b1` / `0.1.51` 完成自身一小时 mixed soak，
    618 请求零错误、零意外 length、needle 全对；RSS 后段平台、swap 未用，T-probe 通过。
13. **P4 严格归因通过**：新引擎 A-B-A 中代码 `41.1875 → 5.074 s`（`−87.681%`），
    96K `+2.761%/+2.202%`，短 JSON `3.980 s`；采纳 1600 ms + 2048 优先，保留回滚快照。
14. **P5 已完成**：96K timing 相位归因显示 GPU 计算主导，host staging 只有 0.341 s；timing 工装
    开销 +0.72%，不启用为生产默认。
15. **P6 按 1% 门复验通过**：当前源码已默认 v2，复验用 `STRATA_PA_V2T=0` 回退 v1。
    A1/B/A2 engine read 为 `40063.1/41022.6/40564.9 ms`，wall 为
    `40.368/41.382/40.878 s`；A 合并中位相对 B 改善 `1.814%/1.905%`，A2 漂移约 1.26%，
    parity/needle 全过。保留当前默认，不再重复 env-on A/B。
16. **P7 搁置**：预注册判据和 P7 前快照已存在，但 A1 首个请求期间被用户中断，
    没有 JSONL/summary、parity 或质量结果。只有 NCU 证明 Tensor 管线是直接瓶颈时才恢复；
    恢复后只增加 `STRATA_PF_WMMA=1`，从全新 A1 起重跑同 boot A-B-A，并追加输出质量门。
17. DFlash2 权重缺失不阻塞主线；获得权重后再重新评估收益和成本。
18. **256K 门禁已全部通过**：版本吞吐 A-B-A 和一小时 `CTX256` mixed soak 均通过，当前
    `0.1.51` / `a09d78b1` 冻结为生产基线。soak 覆盖最高 4 路和约 245.9K prompt，不能扩大成
    6 路满载或 262144 满窗口证明。
19. **资源利用率优先，停止独立参数探索**：Nsight 已证明 NVML 高值不等于计算饱和，但还不能说明
    stall reason。先保存快照、短暂停生产，在快照启动的受控 engine 上独占 GPU 跑 NCU，结束后先
    恢复生产；随后只选一个能提高瓶颈管线占用的代码切片，不把 P7 或 env 参数当作默认下一步。
20. **代码优化验证顺序固定**：先记录 NCU 根因和目标计数器，再改最小内核/调度路径；候选必须在
    同一 32K 冷 prefill 上同时改善目标硬件计数和端到端 wall，并保持输出与稳定性门。参数 A/B
    只在候选已经实现后用于部署验证，不用于寻找候选。
21. **layer-major 配置路线已否决**：只移除 `--prefill-pipe 1` 被 512 MiB 容量余量挡住；
    显式 96 MiB、16K host-residual 虽真实执行 10/13 个 super-chunk，六请求 prompt 吞吐中位
    仍慢 `14.29%`。不再重复同路径或扫描邻近 target。
22. **QSA-DIAG 已完成**：真实 150K 冷读的 overlap 中位 28.0%，尾部 37,516 active blocks；
    FP16 全选择一致率 70.92%，半精度 scorer 路线否决。clean `qsa select` 占诊断 GPU timeline
    4.0%，与同几何 scorer/top-k `2.020/0.899 ms` 组合得到候选共享 1.996% 的乐观上界。
23. **QSA-UNION-ATTN 已否决**：三 query 同 CTA 保持 parity、168 registers/thread 和无 spill，
    但普通选择慢 `20.05%`，`QA_SAME=4` 最强共享上界仍慢 `20.75%`。按预注册内核门停止，
    不消耗 150K A-B-A，也不重复同形 cache co-residency。
24. **UTIL-GRID-PAD 已否决**：四副本 stride 257 保持 hash，但 gate/up 中位 `489.3` 对
    `475.7 us`（−2.86%），仅 3/7 胜；剔除第一组冷时钟离群后仍慢约 1.2%。地址旋转与复制
    成本超过任何 bank 冲突收益；不扫描副本数或邻近 stride。
25. **UTIL-WEIGHT-RO 已否决**：默认 SASS 的 56 条 `LD.E.U16.SYS` 在候选中变为 48 条
    `LDG.E.U16.CONSTANT.SYS`，80 registers、无 stack；但 gate/up 中位只从 `472.6` 降到
    `471.0 us`（`0.339%`），6/7 胜，未达 1%。只看机器码变化不足以采纳。
26. **UTIL-PIPED-MTP 已否决**：六 slots / 两组 drafters 真实启动；A 合并中位
    `78.3/108.55/125.7 tok/s`，B 为 `93.9/104.4/113.4`。2 路 `+19.92%` 且 5/6 胜，但
    4/6 路 `-3.82%/-9.79%`，固定 greedy 文本在 B 内也不可重复。不能只看低并发峰值。
27. **UTIL-PIPED-MTP-CACHE 在启动门否决**：B 的 CUDA1 expert cache 少 169 slots
    （`7040` 对 `7209`），但 `--expert-cache-device1` 被定义为额外 helper GPU 的 cache；
    双卡都跑 layer-split stage 时没有可用 helper，候选在读取权重前 exit 2，未 loaded。
28. **UTIL-PIPED-MTP-ORDER 已否决**：原 B 配置只追加
    `--expert-cache-cpu-order`，候选保持 6 slots / 2 groups 并正常 loaded，但自然语言
    greedy 探针仍两次不同且不同于 A。固定 hard gate 不因 2 路曾有 19.92% 峰值而放宽；不跑
    候选吞吐，也不扫描 cache、顺序、草稿深度、槽数或组数。
29. **下一项 UTIL-BATCH-GROUP-FIT**：先补当前 A 的 8 路 A3，再从同一 config 只把
    `--batch-groups 2` 改为 `1`。候选先过相同 parity，再测 2/4/6/8 路并恢复 A4。2 路改善
    至少 1% 且 5/6 胜，4/6/8 路退不超过 1%。若只赢低并发，否决固定配置并转
    concurrency-aware 调度。

路线在每次复盘后允许改变。改变时必须写清新证据和取舍，而不是因为队列顺序。

## 发现与转向

- 核心指标连续三轮没有实质提升：暂停当前局部路线，做代码/性能审计。
- 同一假设连续两轮失败：换机制，不重复同形实验。
- 出现质量或可靠性回归：优先修复，暂停性能推进。
- 候选少于两个：主动从代码、日志、上游差异、配置项和用户负载中补候选。
