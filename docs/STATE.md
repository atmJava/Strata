# 当前状态

> 接手顺序：`RULES.md` → 本文件 → `ROADMAP.md` → `ITERATION_LOG.md` 最近两条。
> 本文件只写当前断点，不重复历史、规则或完整证据。

- 目标：持续优化用户可感知的推理性能、稳定性和资源效率。
- 当前项：P4
- 当前迭代：**长 prefill 下 1.85K 代码请求的 decode 公平性**。
- 下一动作：保留已采纳的 400 ms concurrent-read lead-in，审计能否按在途请求剩余 token 或
  可完成性选择 yield 点；目标是不用整段固定等待，让 41-token 代码请求也尽早完成。
- 默认候选：400 ms lead-in + 750 ms 等待槽进入可解码状态的 E 臂；1200 ms 已否决。
- 未完成采纳项：14。
- 阻塞：DFlash2 drafter 权重缺失；该路线搁置，不阻塞主线。

## 生产

- `strata.service` 在 `192.168.3.14:8080`，health 200（2026-10-26 HC_Q8 恢复后实测）。
- 引擎 0.1.49.1 + 共享 RoPE + concurrent-read lead-in / `8d680c69`；server `de09cffd`；
  launcher `0eeee160`；config `f82d1cfd`。
- 生产配置：`parallel=6`、`max_context=192000`、`STRATA_HC_Q8=1`，W3 staged read 开启，阈值 4096；
  lead-in 400 ms、等待 750 ms、只处理 ≥4096-token 的冷读。
- 回滚快照：`/opt/ai/tmp/pre-S0b-20261025T161013Z/`（原始 engine `84a0d410`）与
  `/opt/ai/tmp/pre-rope-share-20261025T164500Z/`。

## 已知结果

- V3-1 容量预设已收口：4 路中位 `6×192K` 聚合 120.6、`8×128K` 133.9、`8×192K` 122.8；
  默认保持 `6×192K`。8×128K 的 +11% 以 192K→128K 上下文为代价。
- V3-4 已完成：MTP 草稿深度 2/4/6 在噪声内，保持 4。
- V2b fused prefill +0.61%，否决；V2c `STRATA_PIPED_CONCURRENT` 两臂无差异，S0b 仍需换机制。
- S0b 已否决：gate 修复后并发读 3/3 触发，但邻居静默中位 ON 7.09/7.59 s、OFF 7.41 s；0.1.51
  单路和聚合同时回退。旧引擎继续生产。
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
- S2 已完成：流式最终块与非流式响应都有 per-request metrics，mock regression 2/2 通过。
- T-probe 已完成：版本戳从 `/proc/<pid>/cmdline` 解析实际 engine/server；现网 server hash 为
  port-v1 的 `de09cffd`，不再误读生产目录里的旧副本。
- 配置漂移门禁已采纳：`--expect-env-file production-env-2080ti.json` 精确核对 7 个关键运行态 env；
  缺键或错值会失败。现网一次实测 0 problems。
- 当前最大用户可感知短板是长读期间中等输出请求仍被压到约 41.5 s，以及满槽准入约
  11.4–13.9 s。

## 重启策略

- 服务与服务器可按实验、部署或恢复需要随时重启，不设次数上限，也不需要逐次批准。
- 次数、原因、停机时长和结果写入 `ITERATION_LOG.md`，用于成本判断，不作为额度。
- 生产修改必须先快照；一次只运行一个安装器；装后核对真实文件、配置和 health；失败立即恢复。

## 当前风险

- 4 路聚合跨 boot 噪声约 9%，同会话低于 3% 不下结论。
- 性能 A/B 前必须先证明实验开关真的进入目标路径；否则 on/off 两臂可能完全相同。
- C1 的 6/8 槽受控边界已测；不同邻居长度和真实混合流量下的准入上限仍未覆盖。
- 新引擎进入 GPU A/B 前必须用 T-probe 证明 running engine/server hash；版本戳已不再从旧目录猜路径。
- RoPE 省显存已有短测证据，但真实流量 soak 尚未完成；不得把噪声内吞吐说成提升。
- 满槽请求仍没有配置固定准入超时；6/8 槽上界只覆盖受控的 400-token 邻居负载。
- P3 的 lead-in 只针对 `piped && !STRATA_PIPED_READ_PIECE` 的并发冷读；读到新的长请求时
  仍会增加一次有界解码窗口。1200 ms 已证明超过旧长档门限，后续不能用任意加长代替调度。
- 当前公平性测试只覆盖 14/41-token 输出和 96K 冷读；32K/64K、多长读、取消与真实外部流量未覆盖。

- 最后更新：2026-10-26T05:54+08:00（P3 A-B-A 采纳；转入中请求公平性 P4）
