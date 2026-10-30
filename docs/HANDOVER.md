# AI 交接：Strata 2×2080 Ti 当前状态

> 整理时间：2026-10-31T01:10+08:00。
> 本页只描述当前可接手的生产、实验断点、风险和恢复入口。历史交接见
> [`archive/HANDOVER-through-2026-10-25.md`](archive/HANDOVER-through-2026-10-25.md)。
> 判断部署不能只看 health 200；必须以实际运行文件的 hash、进程环境和 T-probe 为准。

## 1. 接手顺序

1. [`RULES.md`](RULES.md)：自主优化协议和生产安全边界。
2. [`STATE.md`](STATE.md)：当前断点与生产形态。
3. [`ROADMAP.md`](ROADMAP.md)：指标、路线和验收门限。
4. [`WORK_QUEUE.md`](WORK_QUEUE.md)：待做、已完成、否决和搁置项。
5. [`ITERATION_LOG.md`](ITERATION_LOG.md) 最新两条：最近决策。
6. 需要原始数字时读 [`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md) 的 L-095–L-096。

## 2. 当前断点

- 用户已恢复“除非叫停否则持续执行”的全局优化循环；当前断点是 QSA-UNION-ATTN。
- QSA-DIAG 已完成：overlap 中位 `28.0%`，FP16 全选择一致率 `70.9207%`；半精度路线否决，
  candidate-sharing 路线按预注册上界进入一次最小 exact 实现。
- P7 状态是 **暂停/待做**，不是进行中。
- P7 的 A1 在首个请求期间被用户中断；没有有效 A1 JSONL/summary，没有 B/A2、parity、
  needle 或性能结论。
- 中断后只读核对到服务 active、health 200、生产配置已恢复，未发现残余 soak 或 P7 输出。
- 恢复 P7 时必须从全新的 A1 起跑完整同 boot A-B-A，不能复用中断前的首请求。
- 当前目标链路是：完成 QSA 候选共享实现或否决后，立即更新证据并创建下一目标；不等待用户批准。

## 3. 当前生产

生产 `strata.service` 当前按用户要求保持 inactive；候选手动实例监听 `127.0.0.1:8080`。

| 项 | 当前值 |
|---|---|
| service | `inactive/dead`、`MainPID=0`、`NRestarts=23` |
| 候选 health | 200，`loaded=true` |
| 候选 engine | sha256 `105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3` / `0.1.51` |
| 候选 PID | server `491072`、engine `491135` |
| candidate config | sha256 `88de996aeb0e7ae82e59937e3106473adf2f9eabd98aca8c711654fa254e443f` |
| 形状 | 双 RTX 2080 Ti、6×256K、groups=2、INT8 KV、parallel=6 |

关键生产开关：

```text
STRATA_ADMIT_SHORT_FIRST=2048
STRATA_BATCH_STAGED_READ=1
STRATA_HC_Q8=1
STRATA_MP_STAGED_READ=1
STRATA_MP_STAGED_READ_MIN_TOKENS=2048
STRATA_PIPED_CONCURRENT=1
STRATA_PIPED_DECODE_LEADIN_MIN_TOKENS=4096
STRATA_PIPED_DECODE_LEADIN_MS=1600
STRATA_PIPED_DECODE_LEADIN_WAIT_MS=750
STRATA_SHARED_SLOT_ROPE=1
STRATA_SM75_PERF=1
```

候选进程中当前没有 `STRATA_PF_WMMA`、`STRATA_PA_V2T`、`STRATA_SEL_OVERLAP`、
`STRATA_IDX_FP16_CHECK` 或 `STRATA_PREFILL_TIMING`。
`STRATA_ADMIT_SHORT_FIRST` 由 systemd service 环境提供；写进 engine config 的 `env` 只传给
引擎子进程，不能替代 service drop-in。

当前生产是“已通过稳定性、正确性和同 boot 归因的最佳基线”，不是数学意义上的全局最优。

## 4. 最近证据

### 生产 soak（192K 基线）

- 3600 秒 mixed soak：618 请求、206 waves。
- HTTP/执行错误 0、意外 `finish_reason=length` 0、needle 失败 0。
- RSS 后段约 51–52 GiB 并进入平台；swap 全程未使用。
- short/medium/32K/64K/96K wall 中位：
  `6.025/6.794/19.297/27.222/41.780 s`。
- T-probe `problems=[]`。

混合 soak 的 short JSON 中位 `6.025 s` 与固定 fairness 的约 `4 s` 口径不同，不能互相替代。

### P4 公平性已采纳

同 boot A-B-A 结果：

| 指标 | A1 | B | A2 | B 相对 A 合并 |
|---|---:|---:|---:|---:|
| 41-token 代码 wall | 41.173 s | **5.074 s** | 41.202 s | **−87.681%** |
| 短 JSON wall | 3.418 s | **3.980 s** | 3.396 s | +16.818% |
| 96K `by_kind` | 41.009 s | 42.092 s | 40.913 s | **+2.761%** |
| 96K wave | 41.176 s | 42.098 s | 41.206 s | **+2.202%** |

三臂各 20 请求，零错误且 needle 全对。P4 已采纳，但短 JSON 距 4 s 只有约 20 ms，
96K 单请求距 3% 只有约 0.24 个百分点。后续不能无证据扩大 lead-in 或短请求优先范围。

### P5 相位归因

96K timing 臂的 GPU timeline 中位 `32.653 s`、phase 总墙钟 `35.441 s`，host staging 仅
`0.341 s`。主要 phase：

| phase | 中位 |
|---|---:|
| `gemm gate/up` | 5.807 s |
| `embed+steps` | 3.692 s |
| `qsa attn` | 3.422 s |
| `gemm down` | 3.031 s |

timing/plain engine read 为 `37.841/37.571 s`，工装开销 `+0.72%`。结论是 GPU 计算主导，
host staging 不是当前长读主瓶颈。

### P6 已按 1% 门采纳

旧 L-086 在 3% 门槛下保留为“未定”。当前 `qsa_prompt_attn.cu` 已默认选择 v2，
`STRATA_PA_V2T=0` 才回退 v1。L-095 用同一组三个固定冷 96K prompt 做 A1(v2)/B(v1)/A2(v2)：
engine read 中位 `40063.1/41022.6/40564.9 ms`，wall 中位
`40.368/41.382/40.878 s`。A1+A2 合并相对 B 的 engine read/wall 改善
`1.814%/1.905%`，A2 漂移约 `1.26%`，parity 与 needle 全过。当前默认 v2 保留；
不要再设 `STRATA_PA_V2T=1` 做同形 A/B。

### QSA-DIAG 完成，FP16 否决

真实 `150,069` token 冷请求中，19 个 chunk 的 16-query selected-cell union 中位 `28.0%`；
最后 chunk 有 `37,516` active blocks、width `2,051`。FP16-rounded indexer keys 只保持
`1,203,932/1,697,576` 个完整选择（`70.9207%`），稀疏 selected cells 一致率 `99.9619%`，
因此不实现半精度 scorer。

clean `qsa select` 为 `3,929 ms`，占诊断 GPU timeline `4.0%`；同几何 scorer/top-k 为
`2.020/0.899 ms`。候选共享的预注册乐观上界为 `1.996%`，值得一次 exact A-B-A，但尚不等于
采纳。报告在 [`work/QSA_DIAGNOSTIC_RESULT-20261031.md`](work/QSA_DIAGNOSTIC_RESULT-20261031.md)，
原始证据在 `/opt/ai/logs/qsa-diag-20261031T1653Z/`。

### P7 无有效结果

- 预注册判据：
  `/opt/ai/logs/P7-fixed-criteria-20261026T065341Z.txt`，
  sha256 `1ae40a8142a7a056e5d36c03902533c3119b896297f5510e4de0ca822ea650b6`。
- P7 前快照：`/opt/ai/tmp/pre-P7-pf-wmma-20261026T065341Z`。
- A1 中断，没有有效 JSONL/summary；不能把启动行为写成部分收益。

## 5. 构建与测试状态

- QSA-DIAG 的诊断源码增量构建通过；`qsa_topk_active_parity`、`qsa_slots_parity`、
  `qsa_scores_parity`、`qsa_select_rows_parity_test`、`qsa_topk_parity` 为 `5/5` 通过。
- 本轮没有重跑完整 CTest；上游 v0.2.7 已知 6 个失败项仍按 TEST-027 处理，不能写成“全测试绿”。
- temperature 0 parity 通过；high-effort reasoning hash 在旧引擎 A/A2 间也会变化，
  当前生产形状下不能作为稳定 parity。

## 6. 当前主要风险

1. **96K 冷读仍约 37–42 s**。P5 证明成本主要在 GPU phase，继续调 host staging 或单纯延长
   lead-in 没有现有证据支持。
2. **P4 余量小**。短 JSON 和 96K 两项都接近预注册门槛，生产若靠近边界应先用同口径复测。
3. **满槽准入约 11.4–13.9 s**。现有上限只覆盖受控 400-token 邻居负载。
4. **公平性覆盖窄**。只覆盖 14/41-token 输出与 96K 冷读；32K/64K、多长读、取消和真实外部
   多租户流量仍未覆盖。
5. **外部资产例外**。Q2_0 PLE 夹具缺失；DFlash2 代码与服务接线已保留，但 drafter 权重也缺失，
   显式启用后应安全回落 MTP，相关性能路线暂不能验收。
6. **跨 boot 噪声**。4 路聚合跨 boot 曾漂移约 9%；同会话通常以 3% 为噪声地板。
7. **P7 未验证**。`STRATA_PF_WMMA=1` 可能改变输出，恢复后必须同时过速度与质量门。
8. **QSA overlap 尚不是实测收益**。1.996% 是乐观上界；union 管理、排序、shared-memory
   容量和双卡调度都可能吃掉它。没有 A-B-A 不得写成采纳。

## 7. 用户恢复目标后的入口

恢复“持续优化/24 小时循环”前，先做以下检查：

1. 确认用户已明确恢复目标；否则只做文档、只读审计或用户指定的工作。
2. 核对 service、health、engine/server/launcher/config hash，以及实际运行 env。
3. 确认没有残留 soak、安装器、bench 或其他竞争负载。
4. 若要继续 P7，新建时间戳快照和判据，从全新 A1 开始；A1/B/A2 使用相同 prompt 序列。
5. 每次切换后从 `/proc/<engine-pid>/environ` 证明 `STRATA_PF_WMMA` 状态，并跑 T-probe。
6. 速度门通过后仍执行 parity、needle 和质量检查；B 至少 engine read 与 wall 都改善 3%。

不要把已否决项原样重跑：

- S0b 并发读 gate 修复；
- 固定 `STRATA_PIPED_READ_PIECE=32768/65536`；
- `STRATA_QSA_SLOTS=1`；
- groups=1、groups=1 + batch-MTP、split25/26；
- 1200/1400/2000 ms 固定 lead-in。

完整判决见 [`WORK_QUEUE.md`](WORK_QUEUE.md) 和台账。

## 8. 生产安全与回滚

- P4 回滚：`/opt/ai/tmp/pre-P4-strict-20261026T054515Z/`。
- P7 前快照：`/opt/ai/tmp/pre-P7-pf-wmma-20261026T065341Z/`。
- 历史 400 ms P3 配置：`/opt/ai/tmp/pre-P3-ABA-20261025T213040Z/strata-022.json`。
- 生产变更前必须建立完整时间戳快照；一次只运行一个安装或恢复操作。
- 部署后必须核对真实运行文件、config、关键 env、health 和日志；失败立即恢复。
- API key 不得写入仓库、文档或命令输出。
- 上游仓库保持只读；绝不向 `Niko1221/Strata` 推送。

## 9. 仓库与工作区

- 当前本地收敛工作区：`/home/kali/IdeaProjects/strata`。
- 当前分支：`refactor/sm75-cli-server`。
- 本地收敛工作按可验证阶段提交并推送；接手时以 `git status` 和 `git log` 核对最新阶段与未完成改动。
- 服务器实验源码：`/opt/ai/src/strata-merged-a66698c6`。
- 工作区存在未提交源码、文档、实验说明和未跟踪文件；不得清理、回滚或覆盖用户改动。
- 只修改与当前任务有关的最小文件，并在交付前运行离线预检、diff check 和 UTF-8 BOM 检查。
