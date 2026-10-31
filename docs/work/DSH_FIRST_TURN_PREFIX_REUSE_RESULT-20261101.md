# DSH 首轮共享前缀复用：结果

日期：2026-11-01

状态：`DSH-FIRST-TURN-PREFIX-REUSE` 采纳。

## 结论

DSH 新会话的主请求固定使用同一份 system message 和工具定义。原自动 checkpoint 停在
system 结束，约 `582` token；新的精确 shape gate 把 checkpoint 固定在 system + tools
之后，该前缀为 `5,463` token。连续新会话的后续请求因此只读取约 `600` 个 fresh token。

生产候选使用默认关闭的 `STRATA_PIN_DSH_TOOL_PREFIX=1`。它只匹配当前 DSH 主请求的完整
wire shape，不改 profile、prompt token ids、工具、采样、模型、量化或 engine；shape、
工具描述或显式 `strata_prefix` 变化时 fail open。

## 判决数据

测试方法使用一次干净 boot：先按顺序跑 A 组，再在同一 boot 跑 B 组。这里不是交错 ABBA，
因为 B 写入的 `5,463` token checkpoint 会跨臂保留，后续 A 会命中 B 的前缀。A1/B1 各是
该组写入 checkpoint 前的会话，单列为冷启动，不与稳态中位混合。

| 会话 | task wall | 首步 cached | 首步 model wall |
|---|---:|---:|---:|
| A1 冷启动 | `24.226 s` | `0` | `19.023 s` |
| A2 | `11.104 s` | `582` | `6.399 s` |
| A3 | `11.639 s` | `582` | `6.215 s` |
| B1 冷启动 | `10.613 s` | `582` | `7.480 s` |
| B2 | `8.519 s` | `5,463` | `2.711 s` |
| B3 | `6.114 s` | `5,463` | `2.995 s` |

A2/A3 稳态中位为 `11.3715 s`，B2/B3 为 `7.3165 s`；绝对节省 `4.055 s`，改善
`35.66%`。首步 model wall 从 `6.215–6.399 s` 降到 `2.711–2.995 s`。冷启动仍须读取
新 token，不能把 B1 的较低 wall 归因为已复用前缀。

## 质量与并发

- 6 个 DSH coding fixture 的代码、测试、受保护测试和严格 `RESULT=PASS` 全部通过，
  event error 为零。
- 首轮 `parser-edge` 出现一次可恢复工具参数错误，模型自行修正；该次计入原始统计，
  单独重跑为 `1/1` 通过且零工具错误。
- 双会话均完成，各两个工具结果 `completed`，最终严格 sentinel 通过，provider 标题
  `2/2`。双会话 wall 为 `27.018/28.263 s`；重启后的两路冷读不能作为加速证据。
- 随后两个连续单会话为 `7.985/7.936 s`，两个首步都命中 `5,463` cached token。

## 生产状态

- server / vision / engine：`232891 / 232893 / 232899`
- health：`loaded`
- context：`262144`
- batch：6 slots / 2 groups
- engine SHA-256：`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`
- config SHA-256：`6ece1a13f4ac2d875bdafd903c7aba23fdce6be5ccc387e17b987a130a486811`
- server SHA-256：`bd3e428932c0b9bb645b2ae9f37924b2e440b590333a0322cdd00cd28dd4ed82`
- server env：`STRATA_DEFER_TITLE_REQUESTS=1`、`STRATA_BATCH_SERIAL_TWO=1`、
  `STRATA_PIN_DSH_TOOL_PREFIX=1`
- `strata.service`：inactive；保持用户现有手动实例。

最终候选重启从启动到 ready 约 `190 s`。这是部署成本，不计入用户收益。测试窗口新增
NVRM Xid 为零，swap 约 `11 MiB`。生产备份为
`/opt/ai/backups/prod-dsh-prefix-final-20261031T200434Z/`。

## 测试方法修正

原先计划的同 boot ABBA 不适用于这类会写持久 conversation-cache checkpoint 的候选：

1. B 写入更长的 `5,463` checkpoint 后，后续 A 会命中它。
2. 代理显式指定较短的 `tokens=582` 仍可能被已有 conversation-cache checkpoint 覆盖。
3. 因此判决采用“干净 boot 顺序 A→B”，冷启动单列，并明确它不是交错 ABBA。
4. 以后比较缓存边界时，若任一臂会留下跨请求 checkpoint，必须使用独立 boot，或把同 boot
   顺序设计预注册为不可逆状态机并单独解释，不能事后把污染数据包装成平衡 A/B。

代理的 `--pin-tool-prefix` 是宽测试注入，只用于机制研究；产品安全 gate 在 server 内，
要求精确 DSH shape 和第一次接受 prefix 的 fingerprint 不变。

## 回滚

移除 `STRATA_PIN_DSH_TOOL_PREFIX=1` 并重启 server。默认路径保留原 system-boundary
checkpoint；engine、config、模型和量化不需要恢复或重建。

## 证据

- `/tmp/dsh-prefix-clean-cost.json`
- `/tmp/dsh-prefix-clean-20261101/`
- `/tmp/dsh-prefix-abba2-cost.json`：证明同 boot ABBA 被 conversation cache 污染，
  不用于判决
- `/tmp/strata-agent-quality-dsh-prefix-contract-20261101.json`
- `/tmp/strata-agent-quality-dsh-prefix-contract-parser-rerun-20261101.json`
- `/tmp/dsh-product-final-dual-cost.json`
- `/tmp/dsh-product-final-sequential-cost.json`

## 下一目标

转入 `AGENT-DSH-REQUEST-PATH-MAP`，拆解典型热续轮从 DSH step 到用户首 token 的固定成本：
模板渲染、PromptEncoder/cache lookup、admission、server 序列化、engine prompt 和 decode。
94K/189K 热续轮即使高度 cached，prompt 仍约 `2.09/2.43 s`；先建立同请求的 stage
边界和真实触发率，再选择至少 1% 的可实现机制，不直接跳到 kernel。
