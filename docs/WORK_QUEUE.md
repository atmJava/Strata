# 工作队列

> 这是候选组合，不是执行顺序。AI 每轮根据 `ROADMAP.md` 的收益、置信度、信息量、成本和风险重新选择。
> 状态：`待做` / `进行中` / `已完成` / `否决` / `搁置`。AI 可以新增、合并、搁置或重新排序候选，
> 但必须记录理由，不能篡改已有验收证据。
> `未完成采纳项` 由预检与表格自动核对；已完成和否决项不再占据当前队列。

## 当前决策前沿

1. **P4 已采纳**：同一新引擎 boot 上，代码由 A 合并中位 41.1875 s 降到 5.074 s（−87.681%）；
   96K by_kind +2.761%、wave +2.202%，短 JSON 3.980 s，三臂零错误且 needle 全对。
2. **余量风险**：短 JSON 距 4 s 约 20 ms，96K 距 3% 约 0.24 个百分点；生产指标靠近边界时
   优先做同口径复测，不再无证据延长 lead-in。
3. **P5 已完成**：96K 的 GPU timeline 中位 32.653 s、phase 总墙钟 35.441 s，host staging 仅
   0.341 s；主要成本在 `gemm gate/up`、`embed+steps`、`qsa attn` 与 `gemm down`。timing 工装
   开销 +0.72%，不进入生产默认。
4. **P6 已完成但未采纳**：A1/B/A2 的 engine read 中位为 37.3342/36.4583/37.5456 s，B 相对
   A 的合并中位只改善 2.622%；A2 漂移 +0.57%，parity 和 needle 都通过，但未达 3% 门槛。
5. **P7 已搁置**：A1 首个请求期间被用户中断，没有有效性能或质量判决。只有 NCU 证明 Tensor
   管线是直接瓶颈时才恢复；恢复后仍需从全新 A1 起重跑同 boot A-B-A。
6. **旧部署与 soak 已完成（历史）**：`a09d78b1` / `0.1.51`、server `de09cffd`、
   launcher `34271059`、config `ea1e2959`；618 请求零错误，RSS 后段平台，swap 未用。
7. **256K 版本吞吐门已通过**：`new → old → new` 的 engine decode 为 `80.45` 对 `82.7 tok/s`
   （−2.72%），4 路 `136.05` 对 `139.6`（−2.54%），冷读 −0.26%；三臂 greedy 哈希一致，
   最终 T-probe `problems=[]`。4 路距 3% 门限仅约 0.46 个百分点。
8. **CTX256 已通过并冻结 `0.1.51`**：414 请求、133 waves、0 错误、0 意外 length、
   74/74 needle；batch slots 始终 6，约 245.9K 长读 14/14。最低 available RAM
   `2430.4 MiB`，swap 未用，结束后 T-probe `problems=[]`。
9. **真实硬件计数双次完成**：32K 冷 prefill 的 SMs Active 中位 77%、SM Issue 13–14%、
   Tensor 10%、DRAM read 15%；同窗 NVML 曾为 95/98% 和 40/99%，不能代表真实计算利用率。
   下一步是 NCU stall 归因，不是直接根据该计数改内核。
10. **用户裁定资源利用率优先**：停止独立参数探索。NCU 只用于找根因，随后改一个内核或调度路径；
    参数 A/B 只在代码候选实现后作为部署门，不能反过来代替优化。
11. **执行边界固定**：按[资源利用率优化计划](superpowers/plans/2026-10-27-resource-utilization-optimization.md)
    先保存快照、停生产、独占 GPU 完成 NCU，并恢复生产；Task 7–9 只有在唯一根因和计数/wall
    门限预注册后才进入。
12. **上游 37 提交已部署**：`c219fbca`、engine md5 `0e4a9596`、server `11fd33f0`、
    config `023663ba`，health loaded、`NRestarts=0`。数字序列 6 路约 `164.7 tok/s`，
    自然语言 6 路约 `149.8 tok/s`；2 路和 8 路存在独立凹点。
13. **L0 layer-major 直改已否决**：只移除 `--prefill-pipe 1` 后，首个请求因最小
    super-chunk 所需显存不足而回退；候选已恢复 A 配置。下一项只验证显式 layer-major 的
    96 MiB 余量策略和 16K host-residual super-chunk。

## 候选

| ID | 项 | 验收条件 | 状态 | 证据 | 备注 |
|---|---|---|---|---|---|
| S0b | 长读时邻居停顿 | 同 boot A-B 证明机制有效，停顿 ≤1 s 或给出经证据支持的上界 | 否决 | L-068：路径 3/3 触发但无用户收益；L-082 撤回“整个 0.1.51 回退”的临时概括 | 不再重复同形实验 |
| C1 | N×CTX 容量边界收尾 | 补 6 槽晚到首 token、queue/TTFT/wall；补 8 槽邻居停顿并给出上界 | 已完成 | L-070/L-071 | 6 槽默认；8 槽保留容量预设 |
| P1 | 4 路聚合吞吐瓶颈 | 把 113.6 tok/s 拆成 batch/group、decode、expert exchange 等成本，并验证至少一个低风险改动 | 已完成 | L-072–L-075：流水线保留；恢复 HC_Q8 后受控中位 130.6 | 调度开关收口；HC_Q8 采纳 |
| P2 | HC_Q8 / RoPE 真实流量 soak | ≥1 小时真实流量无新增错误；4 路中位不比受控回归恶化 >3%；记录 expert hit、RAM 交换与长读延迟 | 已完成 | L-077：622 请求 0 错误；4 路合成中位 129.7（−0.7%）；swap 未用 | 稳定性通过；暴露短 decode 被长 prefill 挤压 |
| P3 | 长 prefill 下短 decode 公平性 | 96K 邻居下短请求 wall 降 ≥10%；96K wave 不退 >3%；零错误且 needle 全对 | 已完成 | L-078：A 40.21 s → E 3.40 s（−91.5%）；96K +2.2%；A-B-A 通过 | 400 ms lead-in + 750 ms 等待已部署 |
| P4 | 长 prefill 下中请求 decode 公平性 | 96K 邻居下 41-token 代码请求 wall ≤10 s；96K 按 wave 与单请求中位均不退 >3%；零错误 | 已完成 | L-078–L-080、L-083–L-084：新引擎 A-B-A 代码 −87.681%，96K +2.761%/+2.202%，短 JSON 3.980 s | **采纳但余量小**；生产保持 1600 ms + 2048 优先 |
| P5 | 96K 冷 prefill 相位归因 | 3 个唯一 96K prompt 可解析 21 相与 host 成本；3 个同形状 plain prompt 能量化工装开销；零执行错误，恢复后 T-probe 无 problems | 已完成 | L-085：GPU timeline 中位 32.653 s、phase 总墙钟 35.441 s、host staging 0.341 s；timing 开销 +0.72% | 结论是 GPU 计算主导，不直接改生产 |
| P6 | Turing prompt-attention v2 A-B-A | 同 boot A-B-A；每臂 3 个唯一 96K prompt；B 的 engine read 与 wall 中位均改善 ≥3%；A2 回到 A 的 3% 内；零执行错误、needle 3/3；greedy parity 一致或过质量门 | 已完成 | L-086：B 相对 A1 −2.346%/−2.297%，A2 漂移 +0.57%，parity 一致 | 方向和正确性正常但低于 3%，生产不采纳 |
| P7 | Turing FP16 tensor-core prompt experts | 同 boot A-B-A；每臂 3 个唯一 96K prompt；B 的 engine read 与 wall 中位均改善 ≥3%；A2 回到 3% 内；零错误、needle 3/3；输出变化时必须过质量门 | 搁置 | L-087：A1 被中断，无有效 JSONL/summary | 只有 NCU 证明 Tensor 是直接瓶颈时才恢复；否则不作为后续方向 |
| CTX256 | 256K 生产混合负载 soak | 至少 1 小时 mixed traffic；覆盖 32K/64K/96K、接近 256K 的长读与短请求并发；零意外错误、零 needle 失败、6 槽不降级；记录 TTFT/wall、KV host read、expert hit、RAM/swap 与最低显存余量 | 已完成 | L-090：414 请求、133 waves、0 错误、0 意外 length、74/74 needle；14 次约 245.9K 长读全通过；slots 始终 6 | 已冻结 `0.1.51` 基线；最高并发 4、最长 245,856 token，不能写成 6 路或满窗口 |
| GPU-UTIL | 真实 GPU 利用率探测 | Nsight 双卡计数器完整、请求质量门通过；结果能区分 SM 活动、issue、Tensor、warp 与 DRAM；失败样本不得进入汇总 | 已完成 | L-091：两次 32K prefill 的 SMs Active 77%、SM Issue 13–14%、Tensor 10%、DRAM read 15%；NVML 95/98% 与 40/99% 均只表示有活动 | 最终脚本 `tools/server/probe-gpu-utilization.py` 完整 run `verified`，15 项本地测试通过 |
| UTIL-NCU | 独立实例 stall 归因 | 停服前保存并校验快照；用受控 engine 实例采集 SM throughput、occupancy、Tensor pipe、DRAM 和 stall reason；目标 prompt 与热态固定；结束后恢复生产并 T-probe 无问题 | 待做 | L-091 只给出管线占用，不能指定 stall reason | 只做根因归因；不能附加现役进程，不得用 Nsight GPU Metrics 替代 |
| UTIL-KERNEL | 按根因提高资源利用率 | 预先固定目标计数器和 wall 门限；只改一个瓶颈内核或调度路径；同负载下目标硬件计数与端到端 wall 同向改善；输出和稳定性门通过 | 待做 | NCU 根因待补 | 下一代码项；参数 A/B 只作部署验证，不做独立参数探索 |
| UTIL-MULTI | 2/8 路吞吐凹点 | 2 路聚合不得低于单路；8 路相对 6 路不退且 TTFT 有明确上界；同时记录 ITL 与每路吞吐 | 待做 | L-092：2 路数字 `94.85 tok/s` 低于单路 `104.05`；8 路 `138.76` 低于 6 路 `164.68`，TTFT 约 `5.8 s` | 先查调度/批窗口固定成本，不先做参数扫描 |
| TEST-027 | 上游合并测试缺口 | 6 个 CTest 失败逐项归因；允许外部资产 skip，但不能把 OOM 或位差吞成跳过 | 待做 | L-092：`mmvq_il`、`iq_multi`、`gdn_rec`、`native_grouped`、`ple`、`native_expert_q8_0_q8_0` | 与性能优化分开收口 |
| T-PROBE-PATH | 版本探针识别新部署路径 | 能从 server 子进程识别 `build75-027/strata`，并拒绝 hash 与运行路径不一致 | 已完成 | 2026-10-28 现网返回 `problems=[]`；engine/server/config 均来自运行 argv | 工具联合回归 `28 passed, 1 skipped`；完整 root GPU Metrics 本轮未重跑 |
| QSA-SLOTS | 合并 batch window 的 QSA launch | 4/6 路交错 A-B 均稳定改善聚合吞吐 | 否决 | L-081：4 路无收益，6 路首次 +3.5% 不复现，最终相邻 −0.49% | 同配置不再重试 |
| HEAD-ENGINE | 旧合并源码的 sm_75 可部署验证 | 从当次 HEAD 独立构建；测试、输出 parity、版本 marker、运行路径 hash 与一小时 soak 通过 | 已完成（历史基线） | L-082/L-083/L-089/L-090：当次 256K 变化在 3% 内，一小时 soak 通过 | 记录属于 `a09d78b1`，不能当作 c219 的测试结论 |
| UPSTREAM-027 | v0.2.7 37 提交的服务器验收 | 独立构建；真实代理接口、工具/图片、并发 health 通过；记录吞吐曲线和测试缺口 | 已完成（测试缺口） | L-092：6 路数字 `164.68`、自然语言 `149.82 tok/s`；CTest 仍有 6 项失败 | 运行可用，不代表测试全绿；2/8 路另立 UTIL-MULTI |
| LM-L0 | 只移除 pipe 启动 layer-major | 日志进入真实 super-chunk，随后测 150K/210K；质量与内存门通过 | 否决 | L-093：`no VRAM for a super-chunk (888 MiB free)`，未进入性能阶段 | 不重复同形实验 |
| LM-EXPLICIT | 显式 layer-major 的容量策略 | 96 MiB 余量下 16K host-residual super-chunk 实际执行；150K/210K A-B-A 出现稳定且可归因的 ≥1% 改善，内存与质量不退化 | 待做 | L-093 只证明默认余量路径被容量阻断 | 先确认路径启动，再跑长请求 |
| S0d | staged 取消不吞他人控制行 | 客户端断开时其它流不丢 token，有回归测试 | 待做 | V2-1 PASS，待固化 | 只差测试与复核 |
| S8 | SSE 保活 | 空闲 ≥10 s 的流收到保活；有测试 | 待做 | `serve/server.py` 已有实现 | 已有功能，补测试 |
| S1 | 准入上限 | 超限返回稳定 429/503 与错误体；有测试 | 待做 | `serve/server.py` 已有实现 | 已有功能，补测试 |
| S7 | 断连取消 | 断开后 ≤2 s 释放槽；有测试 | 待做 | `serve/server.py` 已有实现 | 已有功能，补测试 |
| S12 | 优雅停机 | SIGTERM 后按上限排空或中止，退出码正确；有测试 | 待做 | `serve/server.py` 已有实现 | 已有功能，补测试 |
| S14 | stop 缓冲 | 部分 stop 串不泄漏；有测试 | 待做 | `serve/server.py` 已有实现 | 已有功能，补测试 |
| S3 | 每请求投机接受率 | 响应或 `/metrics` 可读 accepted/offered；有测试 | 待做 | | 与 M6 相关 |
| S9 | 流式背压 | 慢客户端不拖慢同批其它流；有测试 | 待做 | | 需明确验收负载 |
| S10 | 阶段指标 | `/metrics` 含 queue/prefill/decode 计数或直方图；有测试 | 待做 | | 与 S2 可合并设计 |
| S11 | 全 ISL 准入 | 超长 prompt 在占槽前被拒或排队；有测试 | 待做 | | 256K 风险 |
| S13 | profile 契约 | 校验预设命名和必填键，负例被拒 | 待做 | | 已有部分实现 |
| F1F2 | 多路准入判据 | 由 S0b/S2 的证据决定可接受上界；不允许首 token 后静默数十秒 | 待做 | R11 旧提案 | AI 根据测量裁决，不等待用户 |
| cache_salt | 前缀缓存隔离 | 不同 salt 不共享前缀；有测试 | 待做 | | |

未完成采纳项：19

## 搁置

| ID | 项 | 触发条件 |
|---|---|---|
| V3-DF2 | DFlash2 A1 与吞吐 | 外部 drafter 权重到位后重新评估 |
| F5 | SSD 前缀 KV 持久化 | 冷读占比或重启成本成为主要瓶颈 |
| F11 | 抢占与优先级 | 出现真实多租户或优先级需求 |
| S5/S6/S15 | 结构化输出/语法后端/工具结构标签 | 有对应模型、需求和可测收益 |
