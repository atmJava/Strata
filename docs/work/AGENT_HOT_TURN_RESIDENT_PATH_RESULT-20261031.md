# AGENT-HOT-TURN-RESIDENT-PATH result

日期：2026-10-31

状态：覆盖审计完成，机制不采纳。原 4 路非回退门未运行；审计证明当前
`fused_staged <= 32` 路径没有足够真实覆盖，不能把组合 A/B 的名义差归因给该路径。

## 结论

native resident direct-pointer 路径真实减少了短 staged chunk 的 expert 布局成本。596
fresh-token 热长轮相对 A1 的 wall 从 `1.522684 s` 降到 `1.490017 s`，改善 `2.145%`、
绝对 `32.667 ms`，三波全胜；相对 A1/A2 合并基线改善 `1.862%`。这满足用户“1% 多也算”
的价值判断，但没有满足契约预注册的绝对 `0.05 s` 门。

因此本项不能写成完整通过：

- 1 路 596 是唯一可明确归因到候选 prompt 路径的正向结果；
- 94K 的 wall 虽从 A2 `1.438884 s` 降到 `1.401311 s`，但 prompt
  `1251.5 → 1251.2 ms` 基本不变，差值主要落在 decode 波动，不能扩大成 prefill 收益；
- 2 路 596 hot-long wall 无回退，但只胜 2/3 波；
- 4 路 A2 测量在用户暂停时中止，没有 `A2-4.json`，6/8 也未运行；
- 候选保持默认关闭，没有进入生产配置。

## Group 级证据

在现役服务占用 GPU 时，用少量真实 blob 承载 512 个 expert pointer，保留生产
`554 x top-10 over 512 experts` 的 routing 和行数分布：

| 路径 | 中位 |
|---|---:|
| 生产同形 16-expert group gather + MMQ + fused SwiGLU/H quantize | `11.671 ms/layer` |
| `fused::experts_native` direct pointer，4 个 128-expert launches | `7.848 ms/layer` |

单层差 `3.823 ms`，48 层乐观上界 `183.5 ms`；fused 对 MMQ 的 rel RMS
`2.020e-4`、worst row `3.096e-3`。这解释的是机会上限，不是整请求已实现收益。

## 端到端

596 fresh-token、1 路、三波：

| arm | prompt median | wall median |
|---|---:|---:|
| A1，原 engine，无候选 env | `1396.1 ms` | `1.522684 s` |
| B，候选 engine + 两个 env | `1381.0 ms` | `1.490017 s` |
| A2，候选 engine，无候选 env | `1405.9 ms` | `1.513878 s` |

2 路 596 hot-long 的 A1/B/A2 wall 中位为 `3.466643/3.477050/3.490913 s`；B
相对 A1/A2 合并比较为 `+0.705%`，方向未回退，但只胜 2/3 波。2 路 hot-short
的控制项 wall 为 `-0.332%`，prompt 为 `-1.304%`，作为噪声/覆盖风险保留。

94K fresh-token、1 路、三波：

| arm | prompt median | wall median |
|---|---:|---:|
| A2，候选 engine，无候选 env | `1251.5 ms` | `1.438884 s` |
| B，候选 engine + 两个 env | `1251.2 ms` | `1.401311 s` |

B 的 wall 名义改善 `2.612%`，三波全胜，但 prompt 只改善 `0.024%`；不能把这
`37.573 ms` 写成 resident prefill 收益。94K cold 的 B/A2 wall 为
`37.662558/37.710150 s`，没有明显回退。

596 cold 的 A1/B/A2 为 `11.331828/7.984507/11.392315 s`，但 A1/A2 cold
cached 为 `0`，B cold cached 中位为 `4236`。该差值是缓存状态污染，不是
`29.8%` 加速，禁止引用为候选收益。

## 质量与路径

- B 配置实际包含 `STRATA_PF_FUSED=1` 和 `STRATA_PREFILL_FUSED_STAGED=1`；
- 观察到短 staged native 路径命中日志：
  `151 routed, 26 staged`；
- A1/B/A2 三组固定 greedy 均逐字一致，finish 全为 `stop`；
- 596 A2 共 27 个请求，零 HTTP/engine 错误、零外来请求；
- 候选代码要求 native IQ3_XXS/IQ4_NL、无 peer/CPU 分摊、非驻留 expert 不超过
  `STAGE_GRP=32`；否则整层回退 MMQ。

## 中断与恢复

4 路 A2 基线在 `172364` 进程运行约 `1:44` 后被用户暂停；没有生成 result JSON。
随后停止 A2 server/engine `171648/171656`，切回原 A1 config 和 engine
`105a4171af4d62ff0c3085b1290824ade1b288fb3799dc3`。恢复后的 PID 与 health
记录在 `STATE.md`；恢复过程没有继续跑优化实验。

## 证据

远端目录：
`/opt/ai/logs/agent-resident-path-20261031T2300Z/`

- `A1-596.json` SHA-256
  `45c01c5d64f19bf57b8f939f9b63935f1f34c1661273f5b1a5ba88fe65c42395`
- `B-596.json` SHA-256
  `08e80d0cf9458c54ef1362dfe64ecba45af46f5d38934737902592e4c21d6bc3`
- `A2-596.json` SHA-256
  `d9e76979a228bf4457f9f9058ca6269b072d703bee6eef38009a650cc21e4ac6c`
- `B-94K.json` SHA-256
  `676fa5b9f3e70d60bc38195ef58e7c85cfd738398c784f9ead8c8444fe544fa9`
- `A2-94K.json` SHA-256
  `96d4798f5ae37b50858bf9c2e7c6e99139f5d80a7c680e4379419c2aa6bb16b6`
- `A1-parity.json`、`B-parity.json`、`A2-parity.json` 均 `passed=true`

## 恢复后的下一步

不要自动采纳或重跑本项。恢复优化时先做两件互不混淆的事：

1. 统计 48 层中实际进入 `fused_staged` 的层数和 fallback 原因，确认
   `1.86%` 距 group 级 `12.1%` 上界之间是否还有真实覆盖缺口；
2. 只有在覆盖缺口能给出新的整请求 `>=1%` 保守收益时，才补 4/6/8 非回退与安全门。

如果覆盖审计不能解释差距，关闭 resident direct 路线，不再扫描 staging 数量或
邻近参数。

## 2026-11-01 逐层覆盖审计

审计使用当前仓库 HEAD 的临时构建，仅在 `prefill.cpp` 增加默认关闭的逐层日志；它不是
性能候选。二进制 SHA-256 为
`38c54ce27ee81e0666a3232b8692c2dcf416ba56fd882b26e66d7b0d6d09e814`，实际运行 env 为：

```text
STRATA_PF_FUSED=1
STRATA_PREFILL_FUSED_STAGED=1
STRATA_PREFILL_FUSED_STAGED_AUDIT=1
```

在隔离端口 `18081` 跑 `1` 路、`3` 波 cold/hot-short/hot-long，共 `9` 个请求；全部
HTTP/engine 成功、期望回复 `9/9`、finish 全 `stop`、零外来请求，窗口新增 Xid `0`。
审计日志共 `693` 行，覆盖每次 prefill 的所有实际 stage。

热长轮 `T=865` 的 `48/48` 层都有记录：

| 结果 | 层数 | 说明 |
|---|---:|---|
| native pack 可支持 | `3` | layer `35/44/47` |
| `fused_staged` 命中 | `0` | 三层非驻留专家均超过 32 |
| 其余层 | `45` | native pack 不支持，保持原 MMQ/fused 路径 |

三波的热长轮中，`35/44/47` 的非驻留专家数分别为
`117/123/114`、`130/132/141`、`92/80/90`。其余 45 层 staged 全部为 `0`，说明
`wait copy` 集中在三层，不能靠普适调度改动解决。

热短轮 `T=68` 同样每波记录 `48/48` 层，但三波中只有两次命中且每次仅 `1` 层：
wave 1 的 layer `44` 为 `32` 个 staged，wave 2 的 layer `47` 为 `31` 个，wave 3
零命中。按 group 级单层 `3.823 ms` 差，单次最大证据只有约 `3.8 ms`，远低于
热长轮整请求的 1% 门槛，也解释不了此前组合 A/B 的 `32.667 ms`。因此原
`STRATA_PREFILL_FUSED_STAGED=1` 候选关闭，不补 4/6/8；此前名义差保留为未归因信号。

冷 prefill 的实际块为 `T=4236/6253/6400`，每波 45 层显式记录为 native unsupported；
另外 3 个 supported 层走现有自动 native fused 路径。没有发现新的冷块覆盖缺口。

这次审计不改变生产源码或默认路径。生产已按 A1 config 恢复为
server/vision/engine `246832/246839/246862`，health loaded、262144、6 slots /
2 groups、systemd inactive；engine/config/server hash 与审计前一致，新增 Xid `0`。

## 下一项

三层真实搬入量为 `80–141` 个专家，而 full-layer native fused compute 只有约
`7.848 ms/layer`。当前 grouped staged 路径中 copy 仍留下约 `173 ms` 的
`wait copy`。下一项不是放宽原 `<=32` gate，而是单独预注册
`AGENT-HOT-TURN-STAGED-STREAM-PIPE`：只为这 3 个 native 层按小批 copy、当前批 wait、
fused compute 和批后释放做流水。保守重叠上界约 `23.5 ms`，即热长轮 wall 的
`1.55%`；必须由同源码 B0/B1 实测，不能引用审计结果作为加速。
