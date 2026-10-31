# AGENT-SPLIT-MTP-BATCH result

日期：2026-10-31

判决：**否决。** batched draft K/V 路径真实命中，94K 热续轮 prompt 有
`2.359%` 改善，但用户 wall 反向 `0.046%`，没有达到预注册的至少 `0.05 s` 绝对收益。
按停止门不再运行 189K 和 2/4/6/8 全矩阵。

## 实验臂

- A1：生产 config `6ece1a13f4ac2d875bdafd903c7aba23fdce6be5ccc387e17b987a130a486811`；
- B：config `46faf400fe82a10a784cbb774bd1971050f76ac65d932760ab598aac90a6549c`，
  只增加 `STRATA_SPLIT_MTP_BATCH=1`；
- A2：恢复 A1 的字节相同 config。

三臂保持 engine
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`、模型、
pack、双卡 layer split 24、262144 context、六 slots、两 groups、MTP、采样、
cache 和启动参数不变。

## 路径门

B 启动日志真实出现：

```text
strata serve: draft layer prompt K/V batched on CUDA1
(E-9 on the layer split, K/V mode 2)
```

没有 `declined`，因此不是“env 被接受但没有执行”的假阳性。

## 结果

596 fresh-token 热续轮的三波 B 中位为 prompt `1373.6 ms`、wall `1.515669 s`。
相对前一次 A 的历史样本约为 `-3.1%/-1.1%`，但跨 boot 且 wall 只省约 `0.02 s`，
不能单独作为采纳依据。

94K 同形状热续轮：

| arm | prompt median | wall median |
|---|---:|---:|
| B | `1241.5 ms` | `1.434450 s` |
| A2 | `1271.5 ms` | `1.433790 s` |
| B relative to A2 | **`-2.359%`** | **`+0.046%`** |

prompt 改善没有转成用户等待改善。两者之间约 `0.00066 s` 的 wall 差落在噪声内，
且远低于预注册的 `0.05 s` 绝对门。所有 18 个 94K A/B 请求均为 HTTP 成功、
finish `stop`、无工具、零外来请求；B-596 的 9 个请求同样零错误。

## 生产恢复

A2 已恢复为字节相同的 config，server/vision/engine
`161266/161268/161308`；health loaded、262144、6 slots / 2 groups，
`strata.service` inactive。运行进程环境不含 `STRATA_SPLIT_MTP_BATCH`、
`STRATA_DECODE_TIMING` 或 `STRATA_VERIFY_PROFILE`。

## 证据

目录：`/opt/ai/logs/agent-split-mtp-batch-20261031T1252Z/`

```text
B-596 result        6ca607e9ac91fcfc2562c04a6c2167cf770ceba28e20058ef1553cb869bde22b
B-94K result        a4e2c6d852b0d397b92c8fa1d212675c41efc8a40a8bcf34aaeb69ecafc498b8
A2-94K result       01e6e765006bbdc14b2f1fef47d14adee0c763bf18d84acdac160ed9df8b3175
B server log        56d4902911ed6ea9c97616297466b24be2b154662cd24e98514eb2553dc1a240
A2 server log       1f40ccbe506791b04d2fc72da804976b4cd28906de076c7254ecc6c86d657072
```

## 后续

不扫邻近 MTP batching 参数，不把 prompt 局部收益写成体验收益。下一项先评估官方
`0.1.55` Linux CUDA 13 引擎是否在相同真实形状上降低 wall；版本无收益时，再评估已有
`STRATA_GDN_CONVL2=2` 的长 prompt 路径。
