# ENGINE-0.1.55-EVAL result

日期：2026-10-31

判决：**否决。** 官方 `0.1.55` 引擎在相同模型、pack、六 slots、两 groups、MTP、
cache、采样和生产 env 下启动与输出均正确，但代表热续轮 wall 明确变慢，不能替换当前
`0.1.51` 派生构建。

## 包与启动

官方 `v0.2.9` CUDA13 包通过完整性检查：

- archive `ce86b23b26a4521a1871113057534b8a34c01df0ac7b916adccf9b1c8c068f92`
- engine `e4406b474c292c8b52621c803f523ae18dc42c905deda60d5f055740c7ef8925`
- KQ library `7ed527222a8c3f3cd1d232007cab06897425310b820d3c0c16f31aba3cbd7665`
- build `99866a47a2b8+cuda13.0`，source `f-029-rel`，CUDA `13.0`，含 `sm_75`

首次启动在参数门以 exit 2 退出：官方 CLI 不接受本地扩展 `--prompt-cache-long`。
该选项只影响 128K 以上 prompt，本次 596/94K 形状不进入其长上下文分支。B0 因此只移除
这一项并替换 executable；其余 args、env、cwd、模型和 cache 不变。成功 config
SHA-256 为 `ad99eb8082c9e633cfb8acdbf1eae86cd711d9ec632710483539a12bad2cfb21`。

成功 B0 的实际进程路径为官方 engine，health loaded、262144、6 slots / 2 groups；
systemd inactive。B0 启动时按官方自身规则分配 16,583 expert slots
（9,600 primary + 6,983 layer-split），A1/A2 为 17,274（10,065 + 7,209）。这是版本
启动行为的差异；本实验不把 slot 数变化解释成因果，只按完整版本结果判决。

## 正确性

- 三组固定 greedy probe 各跑两轮：B0 内可重复、全部等于期望值；
- A1/B0/A2 的归一化 probe 文本、finish、completion token 与 hash 完全相同；
- B0 的 64-token smoke 正常返回、`finish=stop`、有非空内容；
- 54 个性能请求全部成功、finish `stop`、回复符合期望、零外来请求；
- A1/B0/A2 的 596/94K `cached_tokens` 每波完全相同。

## 性能

hot-long 每臂三波中位：

| shape | A1 prompt / wall | B0 prompt / wall | A2 prompt / wall | B wall vs A ref | absolute wall |
|---|---:|---:|---:|---:|---:|
| 596 | `1376.8 ms / 1.486586 s` | `1488.8 ms / 1.588491 s` | `1409.4 ms / 1.514727 s` | **`-5.853%`** | **慢 `0.087835 s`** |
| 94K | `1232.3 ms / 1.381990 s` | `1314.8 ms / 1.457059 s` | `1259.4 ms / 1.413078 s` | **`-4.259%`** | **慢 `0.059525 s`** |

A1/A2 跨 boot 漂移：596 prompt/wall `2.368%/1.893%`，94K `2.199%/2.250%`，均在
3% 内。两个代表尺寸同向变慢，按预注册规则不进入 189K、4/6/8 或官方引擎的 GDN env
组合。`--prompt-cache-long` 的本地能力也不能由该版本替代。

## 生产恢复

A2 已恢复旧 engine `105a4171...` 和字节相同的 config `6ece1a13...`。
server/vision/engine `162903/162905/162910`，health loaded、262144、
6 slots / 2 groups，systemd inactive；运行 env 不含官方实验开关。

## 证据

目录：`/opt/ai/logs/engine-0155-eval-20261031T1320Z/`

```text
A1-596.json     09dc5765e7030e0ba8a7e19f1b2f8082105354e5dcefad08f9c9ed021fe02882
B0-596.json     2ca3a605c13c22dce1edddacab3617aa7a8f996195977468320f6d503fc5724f
A2-596.json     33946bdce28a95aee385c1d8add2ac442880682398cf23f0d277bbf39592d4be
A1-94K.json     d52200b1227bb0e0857b9a5d180f30c4656b1d60192d5d2c839db64155eed40c
B0-94K.json     a226ba0d1e3177c821682b20750d484bee830987a30b59472cc3315f5fd6396f
A2-94K.json     edb3f4bb8b8fa19ef893e37d00a191da2f161aef0d10666ad79107c0ce227a17
B0 server log   07a401ab0e8dc04c878c23e6b0435082fe9ae70d66fd14e7f2341ebb7cda43bf
A2 server log   898c6d30d9a2692364249ae52bef954374ebc1ff3fa81dbd6d381b2d9e2bbbbf
```

## 后续

不重复官方版本默认或相邻 release。下一项独立评估当前旧引擎已经包含的
`STRATA_GDN_CONVL2=2`：它在超过 64-token 的 prompt chunk 中融合 conv 与 Q/K norm，
仍用同一每元素算术，可直接验证 prompt 路径的一次 launch 与中间写回是否能转成 wall。
