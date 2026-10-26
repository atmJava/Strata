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
5. **P7 暂停/待做**：A1 首个请求期间被用户中断，没有有效 JSONL/summary，也没有性能或质量
   判决。恢复后只增加 `STRATA_PF_WMMA=1`，从全新 A1 起重跑同 boot A-B-A；engine read 与
   wall 均需改善 ≥3%，A2 回到 3% 内。
6. **部署与 soak 已完成**：生产 engine `a09d78b1` / `0.1.51`、server `de09cffd`、
   launcher `34271059`、config `05c9d253`；618 请求零错误，RSS 后段平台，swap 未用。

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
| P7 | Turing FP16 tensor-core prompt experts | 同 boot A-B-A；每臂 3 个唯一 96K prompt；B 的 engine read 与 wall 中位均改善 ≥3%；A2 回到 3% 内；零错误、needle 3/3；输出变化时必须过质量门 | 待做 | L-087：A1 被中断，无有效 JSONL/summary | 用户中断后暂停；恢复时必须从全新 A1 重跑，不复用中断样本 |
| CTX256 | 256K 生产混合负载 soak | 至少 1 小时 mixed traffic；覆盖 32K/64K/96K、接近 256K 的长读与短请求并发；零意外错误、零 needle 失败、6 槽不降级；记录 TTFT/wall、KV host read、expert hit、RAM/swap 与最低显存余量 | 待做 | L-088：功能门通过，长期 soak 未做 | 用户恢复全局目标后优先执行；不得用两次单请求 needle 替代 |
| QSA-SLOTS | 合并 batch window 的 QSA launch | 4/6 路交错 A-B 均稳定改善聚合吞吐 | 否决 | L-081：4 路无收益，6 路首次 +3.5% 不复现，最终相邻 −0.49% | 同配置不再重试 |
| HEAD-ENGINE | 合并源码的 sm_75 可部署验证 | 从当前 HEAD 独立构建；测试、输出 parity、版本 marker、运行路径 hash 与一小时 soak 通过 | 已完成（外部资产例外） | L-082/L-083：CTest 151/8/1；唯一失败缺 Q2_0 PLE 资产；单路 +3.6%、4 路 −0.3%；`a09d78b1` 已部署并 soak | 外部资产例外保留 |
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

未完成采纳项：16

## 搁置

| ID | 项 | 触发条件 |
|---|---|---|
| V3-DF2 | DFlash2 A1 与吞吐 | 外部 drafter 权重到位后重新评估 |
| F5 | SSD 前缀 KV 持久化 | 冷读占比或重启成本成为主要瓶颈 |
| F11 | 抢占与优先级 | 出现真实多租户或优先级需求 |
| S5/S6/S15 | 结构化输出/语法后端/工具结构标签 | 有对应模型、需求和可测收益 |
