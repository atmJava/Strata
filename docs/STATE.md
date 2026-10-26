# 当前状态

> 接手顺序：`RULES.md` → 本文件 → `ROADMAP.md` → `ITERATION_LOG.md` 最近两条。
> 本文件只写当前断点，不重复历史、规则或完整证据。

- 目标：持续优化用户可感知的推理性能、稳定性和资源效率；当前全局优化目标暂停。
- 当前阶段：生产已从 `6×192K` 切换到 `6×256K` 并完成功能验证；256K 长时间 soak 尚未开始。
- 当前项：CTX256（256K mixed soak）。
- 当前迭代：无运行中实验。切换后的两次长上下文 needle、一次基础请求和一次 6 路短并发已完成；
  这不等替一小时 mixed soak。
- 下一动作：用户恢复全局目标后，先跑 256K mixed soak；P7 之后必须从全新的 A1 起完整重跑 A-B-A，
  B 只增加 `STRATA_PF_WMMA=1`，并重新从 `/proc/<engine-pid>/environ` 证明运行态。
- 当前候选：P7 仍是未验证候选；engine read 与 wall 均需改善 ≥3%，A2 需回到 A 的 3% 内。
  256K 切换前生产快照为 `/opt/ai/tmp/pre-ctx256k-20261026T075110Z/`；更早的 P4 回滚基线为
  `/opt/ai/tmp/pre-P4-strict-20261026T054515Z/`。
- 未完成采纳项：16。
- 阻塞：DFlash2 drafter 权重缺失；该路线搁置，不阻塞主线。

## 生产

- `strata.service` 在 `192.168.2.223:8080`，health 200、`loaded=true`（256K 切换后实测）。
- **生产已部署合并引擎 `0.1.51` / `a09d78b1`**：server `de09cffd`、launcher `34271059`、
  config `ea1e2959`；引擎自身一小时 mixed soak 与 T-probe 均通过。
- 生产配置：`parallel=6`、`max_context=262144`、`STRATA_HC_Q8=1`，W3 staged read 开启，阈值 2048；
  lead-in 1600 ms、等待 750 ms、只处理 ≥4096-token 的冷读，短请求首准入阈值 2048。
- 256K 下 `batch_slots=6`、`kv_resident=32768`、`expert_slots=17248`、expert cache `28222 MiB`；
  启动后两卡自报空闲 `1005/1073 MiB`。
- `STRATA_ADMIT_SHORT_FIRST` 由 systemd service 环境提供；写进 config 的 `env` 只会传给引擎子进程。
- 回滚快照：当前生产优先使用 `/opt/ai/tmp/pre-ctx256k-20261026T075110Z/`；P4 严格基线仍保留在
  `/opt/ai/tmp/pre-P4-strict-20261026T054515Z/`。更早的 400 ms P3 配置仍保留在
  `/opt/ai/tmp/pre-P3-ABA-20261025T213040Z/strata-022.json`，仅用于历史回退。

## 已知结果

- V3-1 容量预设已收口：4 路中位 `6×192K` 聚合 120.6、`8×128K` 133.9、`8×192K` 122.8；
  默认保持 `6×192K`。8×128K 的 +11% 以 192K→128K 上下文为代价。
- 256K 生产切换已完成：config 仅把 `max_context` 从 `192000` 改为 `262144`，同时保留
  `batch_slots=6` 与 `kv_resident=32768`。expert slots 从 `17728` 降到 `17248`（−2.7%），
  expert cache 从 `29010` 降到 `28222 MiB`；启动后两卡空闲 `1005/1073 MiB`。
- 256K 功能门通过：`222,322` token、50% needle 和 `253,633` token、90% needle 均命中，
  墙钟分别为 `147.2/152.7 s`；6 路短并发 `6/6` 成功，单请求墙钟 `2.0–3.1 s`。
  长读后 RAM 为 `56 GiB used / 5.8 GiB available`，swap 仍为 0；两卡实际空闲约 `856/950 MiB`。
- V3-4 已完成：MTP 草稿深度 2/4/6 在噪声内，保持 4。
- V2b fused prefill +0.61%，否决；V2c `STRATA_PIPED_CONCURRENT` 两臂无差异，S0b 仍需换机制。
- S0b 已否决：gate 修复后并发读 3/3 触发，但邻居静默中位 ON 7.09/7.59 s、OFF 7.41 s；
  机制无用户收益。L-082 已撤回“整个 0.1.51 吞吐回退”的临时概括。
- batch slot 共享 RoPE 已采纳：双卡 6×192K 省 562.4 MiB，expert slots +1.8%，吞吐差在噪声内，
  输出 parity 通过；生产源码补丁已固化。
- C1 已完成：6 槽第 7 请求 TTFT 中位 11.84 s、slot 等待 11.42 s；8 槽第 9 请求 14.38 s、
  13.89 s。邻居静默都低于 0.61 s；默认保持 6 槽。
- S2 已补 `slot_wait_ms`：原 `queue_ms=0` 不再掩盖 batch slot 等待；现网 server `de09cffd`。
- P1 完成第一轮 A-B-A：groups=2 的 4 路聚合为 127.2/120.4（合并中位 122.1），groups=1 为
  110.9，慢 11.6%；损失来自并发 prompt 与 decode，不是准入等待。生产已恢复 groups=2。
- groups=1 + `--batch-mtp` 也否决：聚合 91.3，接受率 80.71% 正常，但双行串行窗口把 decode
  每流压到 24.8 tok/s、wall 拉长到 17.5 s；生产再次恢复 groups=2。
- P1 受控 4 路目标已过线：恢复合并中丢失的 `STRATA_HC_Q8=1` 后，两轮 B 中位 130.6 tok/s，
  无 HC_Q8 的四个 A 中位 115.25；质量门两臂均 11/11。B2 为 128.7，仍需真实流量确认稳定性。
- P2 已通过：1 小时 622 请求、207 waves、0 错误、0 意外 length、0 needle 失败；4 路后置合成中位
  129.7，相对 130.6 为 −0.7%；swap 零使用，RSS 与 MemAvailable 进入平台期。
- P2 暴露下一瓶颈：96K 冷 prefill 邻居下，短 JSON 的 TTFT 0.63 s，但 wall 中位 40.83 s；
  32K/64K/96K 长档墙钟中位分别为 19.22/27.44/41.61 s。
- P3 已采纳：同 boot A-B-A 中，短 JSON wall 由 A 合并中位 40.21 s 降到 E 合并中位 3.40 s
  （−91.5%）；96K 长请求由 40.38 s 升到 41.28 s（+2.2%，门限内），两轮均 20/20 成功、
  needle 全对，lead-in 日志 10/10 触发。`STRATA_PIPED_READ_PIECE` 32768/65536 因长档
  +15.6%/+7.3% 被否决；1200 ms lead-in 也因代码请求未改善且长档超门被否决。
- P3 未覆盖：41-token 的 1.85K 代码请求仍为 41.51 s；当前机制只释放了 14-token 短响应，
  不能把“长读期间所有请求都公平”写成已解决。
- 新引擎自身 soak 已完成：618 请求、206 waves，0 错误、0 意外 length、needle 全对；
  short/medium/32K/64K/96K wall 中位为 6.025/6.794/19.297/27.222/41.780 s。
  RSS 后段稳定在约 51–52 GiB，swap 未使用；T-probe `problems=[]`。
- P4 历史候选 soak 不能替代本轮严格归因：旧引擎结果为 606 请求、202 waves、0 错误、25/25 needle；
  96K 邻居下代码 4.424 s、短 JSON 3.370 s。32K/96K 四路 wave 分别 +2.78%/+2.53%，
  但 96K `by_kind` 单请求中位 42.658 对 P2 40.96 s（+4.14%），相邻 400 ms D2 约 +3.21%。
- P4 新引擎严格 A-B-A 已通过：A1/A2 代码 41.173/41.202 s，B 为 5.074 s（−87.681%）；
  96K by_kind +2.761%、wave +2.202%，短 JSON 3.980 s；三臂各 20 请求、0 错误、needle 全对。
  该配置采纳，但短 JSON 距 4 s 仅约 20 ms，96K 距 3% 仅约 0.24 个百分点，后续变更必须保守。
- P5 已完成：96K timing 臂的 GPU timeline 中位 32.653 s、phase 总墙钟 35.441 s，host staging
  仅 0.341 s；主要 phase 为 `gemm gate/up` 5.807 s、`embed+steps` 3.692 s、`qsa attn`
  3.422 s、`gemm down` 3.031 s。timing/plain engine read 为 37.841/37.571 s（+0.72%），
  3/3 needle 正确、零执行错误，恢复后 T-probe `problems=[]`。
- P6 已完成但未采纳：A1/B/A2 的 engine read 中位 37.3342/36.4583/37.5456 s，wall 中位
  37.609/36.745/37.856 s；B 相对 A 合并中位为 −2.622%/−2.617%，低于 3% 门槛；A2 漂移
  +0.57%，parity 与 needle 全过，生产配置已恢复。
- QSA slots 已否决：4 路 off/on/off 为 125.7/114.6/115.5 tok/s；6 路首次 on/off
  151.9/146.8 的 +3.5% 未在交错臂复现，最终 B4 对 A4 反向 0.49%。生产保持 off。
- S2 已完成：流式最终块与非流式响应都有 per-request metrics，mock regression 2/2 通过。
- T-probe 已完成：版本戳从 `/proc/<pid>/cmdline` 解析实际 engine/server；现网 server hash 为
  port-v1 的 `de09cffd`，不再误读生产目录里的旧副本。
- 合并后 `0.1.51` 已通过测试、temperature 0 parity、版本戳与五臂 A-B-A：修复版 md5
  `a09d78b1`，单路合并中位 `71.3` 对旧 `68.8`（+3.6%），4 路 `130.0` 对 `130.4`
  （−0.3%），cold prefill +0.4%。CTest 为 151 passed / 8 skipped / 1 failed，唯一失败是
  服务器缺少 Q2_0 PLE 外部资产，不能写成全测试绿。
- 配置漂移门禁已采纳：`--expect-env-file production-env-2080ti.json` 精确核对 12 个关键运行态 env；
  缺键或错值会失败。现网一次实测 0 problems。
- 当前最大用户可感知短板仍是 96K 冷 prefill 本身约 37–42 s，以及满槽准入约 11.4–13.9 s；
  P4 已解决其中 41-token 中等请求被压到约 41 s 的公平性问题。

## 重启策略

- 服务与服务器可按实验、部署或恢复需要随时重启，不设次数上限，也不需要逐次批准。
- 次数、原因、停机时长和结果写入 `ITERATION_LOG.md`，用于成本判断，不作为额度。
- 生产修改必须先快照；一次只运行一个安装器；装后核对真实文件、配置和 health；失败立即恢复。

## 当前风险

- 4 路聚合跨 boot 噪声约 9%，同会话低于 3% 不下结论。
- 性能 A/B 前必须先证明实验开关真的进入目标路径；否则 on/off 两臂可能完全相同。
- C1 的 6/8 槽受控边界已测；不同邻居长度和真实混合流量下的准入上限仍未覆盖。
- 新引擎进入 GPU A/B 前必须用 T-probe 证明 running engine/server hash；版本戳已不再从旧目录猜路径。
- RoPE 省显存已经过一小时混合 soak；只能宣称显存与 expert 容量收益，不能把噪声内吞吐说成提升。
- 满槽请求仍没有配置固定准入超时；6/8 槽上界只覆盖受控的 400-token 邻居负载。
- P3 的 lead-in 只针对 `piped && !STRATA_PIPED_READ_PIECE` 的并发冷读；读到新的长请求时
  仍会增加一次有界解码窗口。1200 ms 已证明超过旧长档门限，后续不能用任意加长代替调度。
- 短/中请求可能在冷读已占流水线前仍未取得控制行或槽；仅延长 lead-in 无法覆盖这类准入竞争。
- P4 已通过但余量小：短 JSON 3.980 s 接近 4 s，96K by_kind +2.761% 接近 3%；生产监控若靠近
  边界，应优先用同口径复测，不能继续无证据扩大 lead-in 或短请求优先范围。
- 新引擎 soak 的短 JSON 混合中位为 6.025 s，高于 fairness 的 4 s；混合 soak 与 fairness 是
  不同口径，不能互相替代。
- 当前公平性测试只覆盖 14/41-token 输出和 96K 冷读；32K/64K、多长读、取消与真实外部流量未覆盖。
- `STRATA_PA_V2T=1` 改变 prompt-attention 的求和/暂存顺序，即使性能提升也必须先通过 greedy parity
  与 needle；哈希变化不能自动判为正确或错误。
- P7 已暂停且没有有效样本；恢复后必须新建完整 A-B-A，不能复用被中断的首请求或把“实验已启动”
  当作部分结果。`STRATA_PF_WMMA=1` 走 FP16 tensor-core prompt experts，输出可能变化；
  速度达标后仍必须有质量门。
- 256K 只完成了两次单请求 needle 和一次 6 路短并发，尚未跑一小时 mixed soak；不能把当前结果
  写成“256K 全负载稳定”。
- `253,633` token 已覆盖上限的约 96.8%，但仍比 `262144` 少 `8,511` token；真实满窗口请求、
  多长读同时到达、取消和长时间 pinned RAM 压力仍未覆盖。
- 两次长读后可用 RAM 降到约 `5.8 GiB`；虽然 swap 为 0，后续 soak 必须监控最低可用内存、
  conversation cache 淘汰和 KV host read，而不是只看两卡显存。

- 最后更新：2026-10-26T16:05+08:00（生产切换为 `6×256K`；功能门通过，mixed soak 待做）
