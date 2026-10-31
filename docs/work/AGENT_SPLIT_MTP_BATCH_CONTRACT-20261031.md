# AGENT-SPLIT-MTP-BATCH contract

日期：2026-10-31

状态：已否决（2026-10-31）。启动日志证明 batched 路径真实命中；94K 热续轮 prompt
改善 `2.359%`，但 wall 反向 `0.046%`，未达到预注册的 `0.05 s` 绝对门，因此停止
189K 与全矩阵。结果见
[`AGENT_SPLIT_MTP_BATCH_RESULT-20261031.md`](AGENT_SPLIT_MTP_BATCH_RESULT-20261031.md)。

## 问题

当前 B4 是双卡 layer split、MTP 开启、六 batch slots / 两 groups，真实热续轮约
`99.4–99.7%` 命中缓存，但 94K/189K prompt 仍分别占 wall 的 `93.1%/92.3%`。默认路径
让末级 drafter 用自己的逐 8-row pass 填 prompt K/V。现有 opt-in
`STRATA_SPLIT_MTP_BATCH=1` 可让已运行的末级 prompt path 批量填同一份 draft K/V。

项目已有另一台双卡机器的测量：prompt read 在 25K/51K/104K 为 `+6.7%/+4.3%/+3.3%`。
这不是本机收益，只证明机制值得一次受控验证。

## 源码与运行前提

源码路径：

- `src/program/generate.cpp` 的 `sp.on_chunk` 读取该 env，并在 layer split 上调用末级
  `Prefill::draft_kv`；
- `src/prefill/prefill.cpp` 接受 paged 或 ring draft K/V，要求 native embedding、末级
  drafter 与 prompt path 在同一设备、Q8_0 drafter tensors 和至少 64-row scratch；
- 条件不满足时引擎打印
  `STRATA_SPLIT_MTP_BATCH=1 declined, the drafter's own pass runs: <reason>`；
- 命中时引擎打印
  `draft layer prompt K/V batched on CUDA1 (E-9 on the layer split, K/V mode N)`。

当前生产已从进程 cmdline 核对 `--native`、`--mtp`、`--spec 8`、`--layer-split 24`、
`--batch 6`。`--batch` 使 `mtp_first=false`，因此 drafter 位于末级，满足静态控制流。
实际 paged/ring 与 scratch 条件只在候选启动日志判定；拒绝就不跑吞吐。

该路径改变 draft 的 K/V 计算方式，因此 draft proposal 可以有小差异；目标模型自己的 token
选择仍由 verify window 决定。质量门不能用“draft 文本相同”作要求，但自由文本温度为零的
现有探针若本来可重复，仍要检查；采样正确性、工具调用、finish 和固定 greedy 对照必须通过。

## Arms

- `A1`：当前生产，进程 159219/159221/159226，config `6ece1a13...`；
- `B`：深拷贝 A 配置，只增加 `"STRATA_SPLIT_MTP_BATCH": "1"`；
- `A2`：恢复与 A1 字节相同的 config，检查跨 boot 漂移。

保持 engine `105a4171...`、模型、六 slots、两 groups、262144 context、MTP、sampling、
layer split 和所有 cache/launch 参数不变。

## 负载与测量

用现有 `tools/server/bench-hot-turn-fixed-cost.py`，先跑 1 路校准实际 tokenizer 尺寸：

- 596 fresh-token 普通热续轮；
- 94K-token 热续轮；
- 189K-token 热续轮。

每类至少 3 波；1 路是主判决，2 路同时报告第二请求 TTFT、单请求 wall 和完成跨度。
若 94K 没有同向收益，不跑 189K 和三臂全矩阵，直接按否决处理，避免把重启和冷读成本
扩大到无效方向。4 路做非回退；6/8 路只做零错误、health、resource 和 finish 安全回归。

每个请求记录 prompt/cached/uncached、server prompt/read/decode、TTFT、slot wait、wall、
output、finish、reply hash/expected、HTTP/引擎错误和外来请求。启动日志必须证明 batched
路径真实命中，不能只用 env 存在代替路径证据。

## 决策门

候选只在全部满足时采纳：

- 启动日志出现末级 batched draft K/V，且拒绝原因为空；
- 至少一个普通 1 路热续尺寸的 prompt 和 wall 配对中位改善 `>=1%`，绝对 wall 至少
  `0.05 s`；94K/189K 方向不能相反；
- 其他 1/2 路尺寸不退 `>1%`，A1/A2 漂移 `<=3%`；
- 4 路 wall/完成跨度不退 `>1%`；6/8 路零 HTTP/执行错误、health loaded、6 slots /
  2 groups、finish 合法、固定 greedy 质量门通过；
- 零外来请求，swap/VRAM/RAM 无新风险；
- 失败可删除一个 env 精确回滚。

训练/合成负载只能证明机制；结果文档必须把真实触发频率、单次绝对节省与客户端工具时间
分开。

## 重启预算

当前 A 已在产。候选 B 加载约 2–3 分钟，A2 恢复再加载约 2–3 分钟。只有静态启动日志先
证明路径命中，才支付 B 的加载成本；只有 94K 出现方向性收益，才跑完整 A2 矩阵。若路径
拒绝，不重启。
