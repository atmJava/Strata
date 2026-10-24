# 功能移植与验收（FEATURE_PARITY）· v0.1 草案 · 2026-10-24

> 配套：[`MULTIPATH_PLAN.md`](MULTIPATH_PLAN.md)（做什么、顺序）、[`MULTIPATH_STANDARD.md`](MULTIPATH_STANDARD.md)（口径）、
> [`MULTIPATH_RULES.md`](MULTIPATH_RULES.md)（执行规则与复盘）、[`MULTIPATH_LOG.md`](MULTIPATH_LOG.md)（每轮复盘）。
> 参考仓库：`/home/kali/IdeaProjects/vLLM-2080Ti-Definitive`（fork `main` = `93c9013`，v0.2.2-post3）。
>
> **目标**：把 fork 里对多路推理与 serving **有用**的功能完整、可用地融合进 Strata。**"完整"的定义见 §0**：
> 清单每项都有判决；采纳项有可运行、strict 通过的测试；否决/搁置项写明证据与"没测什么"。

## 0. "完成"的定义

1. 本文件 §2 的 **F1–F11 每项都有判决**：采纳 / 否决 / 搁置（搁置必须写触发条件）。
2. **采纳项**：对应测试（§4）在服务器上跑过并 strict 通过；性能项过同 boot A-B-A（≥3 臂、中位、<3% 不结论）；
   质量项过逐位/质量门；生产变化进 `HANDOVER.md` §3 决策表（含回滚）。
3. **否决/搁置项**：写明证据、"没测什么"、重新评估的条件。
4. 文档收口：`MULTIPATH_LOG` 有每轮复盘；`EVIDENCE_LEDGER` 有对应条目；`HANDOVER` §13 更新。

## 1. 官方项目里有什么（2026-10-24 盘点）

- `tests/`：**1,877 个 pytest 文件**（上游 vLLM 全集 + fork 的修复测试）。与多路/serving 相关：
  - `tests/entrypoints/openai/`：**70 个文件**，覆盖 chat/completions、completions、流式、usage、错误码；
  - `tests/v1/core/test_scheduler_e2e.py`：并发部分 prefill + 前缀缓存计数（用 `LLM` 类直接跑引擎）；
  - `tests/v1/core/prefix_cache/*`：部分前缀命中的原语与语义；
  - `tests/v1/spec_decode/`：**30 个文件**，含 `test_acceptance_length.py`、`test_request_acceptance.py`（接受率/正确性）；
  - `tests/v1/engine/test_llm_engine.py`：引擎级调度行为。
- `benchmarks/`：136 个文件；`benchmark_serving.py` **已废弃**（提示改用 `vllm bench serve`，实现在
  `vllm/benchmarks/serve.py`）。
- **结论**：这些测试跑在 **vLLM 引擎内部**（`LLM` 类、block manager），**不能直接跑在 Strata**。
  能移植的是它们定义的**行为契约**——我们把它写成针对 Strata OpenAI 兼容接口的**黑盒测试**（§4）。
  基准工具同理：借它的度量口径（并发—吞吐—TTFT），不借它的进程内 API。

## 2. 有用功能清单（F1–F11）

| ID | 功能 | fork 证据（文件:行 / 版本） | 对 Strata 有用？ | Strata 现状 | 移植形态 | 测试 | 判决（待） |
|---|---|---|---|---|---|---|---|
| **F1** | 多路 continuous batching 的**步契约**：每请求是 cursor（`computed` vs `target`）；每步先喂饱 running decodes，剩余预算给 waiting prefill；晚到 prefill 只推迟"还没吐 token"的请求（`num_output_tokens==0`） | `scheduler.py:606-615, 727, 1185-1235, 734-740`；CHANGELOG v0.2.2-post1 | **是（核心）** | 槽一起解码、`BGEN` 中途入批；**无统一 per-step 预算**；满槽时读根本发不出去 | W2（配置）→ W3（代码） | T2/T3 | W3 补丁已编译（R3，默认关 `STRATA_BATCH_STAGED_READ`）；运行时未验收（R4） |
| **F2** | 单步 token 预算混批：所有 shipped profile 都是 `MAX_BATCHED_TOKENS=2048`；`request_token_budget = min(token_budget, input_budget - draft_slots)` | `scheduler.py:126-130, 624-627, 946`；`launcher.sh:6035` | **是** | `--prefill` 分块 + `STRATA_BATCH_DECODE_SHARE`，没有覆盖 decode+prefill 的统一预算 | W2 → W3 | T3 | 在办（W2 配置臂已否决：READ_PIECE 在 gate 下不触发，L-047；W3 补丁已编译，R4 验收） |
| **F3** | 前缀缓存语义：refcount 共享 + CoW、命中块只读、最后一个 prompt token 重算取 logits、`num_cached_tokens` 计数 | `single_type_kv_cache_manager.py:300-322, 448-468`；`kv_cache_manager.py:275-285`；`tests/v1/core/prefix_cache/*` | **是** | 有 prompt cache / conversation cache；缺"缓存计数"的对外口径与测试 | 暴露统计 + 测试 | T4 | 在办 |
| **F4** | 投机解码（MTP/DFlash2）与**接受率验证实践**：混批里 MTP 在 chunk 边界留 lookahead；验证器 KV 精确；接受率有测试 | `tests/v1/spec_decode/*`；`scheduler.py:583-602`；CHANGELOG v0.2.2 | **是（验证方法）** | 已有 `--spec 8 --mtp-max-t 4`，热接受率 ~99%（L-026） | 验证实践（不开关，只补测试） | T5 | 在办 |
| **F5** | SSD 前缀 KV 持久化 #243：`OffloadingConnector` + FS tier（`checkpoint-<sha256>`、每 block 一个 `.bin`、`PYTHONHASHSEED=0`），命中异步装入 `WAITING_FOR_REMOTE_KVS` | `launcher.sh:1748-1807`；`kv_offload/file_mapper.py:121-148`；`offloading/scheduler.py:1036-1209`；验证 1,568/1,929（重启）等 | **是（重启场景）** | 无；重启冷加载 ~165 s | 暂缓（P3，触发条件：重启频率或冷读占比升高） | T6 | 暂缓 |
| **F6** | profile 定容与启动门：命名 `<decoder>-<kv>-<N>x<CTX>-<msg>`（N=`MAX_NUM_SEQS`，CTX=`floor(MAX_MODEL_LEN/1000)`）；launcher 启动后强制 `N×CTX ≤ GPU KV tokens`；health + smoke + 审计 | `profiles/README.md:21-24`；`launcher.sh:176-205, 5803-5809, 5337-5391` | **是** | 有参数（`--kv-resident`/会话槽/预留），**无预设、无启动门** | W4 预设 + 校验脚本 | T7 | 在办 |
| **F7** | OpenAI（及 Anthropic）API 契约：chat/completions、流式、usage、错误码 | `tests/entrypoints/openai/`（70 文件） | **是** | Strata 已有 serve（自研 + 单测） | 抽样黑盒测试 | T1 | 在办 |
| **F8** | TP/PP 混合拓扑 | launcher TP/PP 支持 | **部分**：层切分已有；TP 已存档（L-033，单流 +3% 噪声内） | 不移植 TP；PP 不存在于 Strata | — | — | 否决（TP） |
| **F9** | FP8/NVFP4 权重路线 | README 的模型表 | **否**：权重栈完全不同（Strata 是 IQ pack，`--native`） | — | — | — | 否决 |
| **F10** | vLLM 内核（Marlin/FlashInfer/TurboQuant/FlashQLA） | csrc/、vllm/attention | **否**：引擎不同，不可移植 | — | — | — | 否决 |
| **F11** | KV 压力下的抢占与优先级（`fcfs`/`priority`，preempt 后回队首、重算） | `request_queue.py:75-158`；`scheduler.py:883-933` | **暂缓**：Strata 满槽排队在服务端（W3 先解决准入），多路下是否需要优先级未测 | — | W3 之后再评 | — | 搁置 |

**判据**（与 `MULTIPATH_STANDARD.md` 一致）：F1/F2 看 T3；F3 看 T4；F4 看 T5；F6 看 T7；
F7 看 T1；性能项同 boot A-B-A、每臂 ≥3、中位、<3% 不算结论。

## 3. 验收标准（每项"可用"的门槛）

| 门 | 内容 | 通过线 |
|---|---|---|
| **G0 护栏** | `/health`、显存余量、温度、NVLink crc | 全绿；`free ≥ 700 MiB` |
| **G1 正确性** | 逐位门（`parity_window.py`）或质量门（`quality_eval.py`） | 过；batch 不变性逐 token 相等（exactness 设置下） |
| **G2 性能** | 1/2/3/4 路 × 冷/热读 | 4 路聚合 ≥ 基线 −3%；准入 ≤2 s；邻居停顿 ≤1 s |
| **G3 任务级** | `agent_budget_ab.py` | 墙钟中位不劣、空答案率 0、畸形工具调用 0 |
| **G4 API** | T1 抽样契约 | 全过（非流式/流式/usage/400/401） |

**F1/F2 的"可用"** = T3 全绿（四条：准入 ≤2 s、邻居停顿 ≤1 s、聚合 ≥ 基线 −3%、无截断/空答案/质量不劣）。
**F3 的"可用"** = T4 通过：同一 prompt 第二次读的命中计数 ≥ 设定值（或读时间下降 ≥30%），且贪心输出一致。
**F4 的"可用"** = T5 通过：接受率不低于基线、开/关贪心逐位相等。
**F6 的"可用"** = T7 矩阵全绿 + `N×CTX ≤ KV tokens` 的启动门脚本通过。
**F7 的"可用"** = T1 全过。

## 4. 测试用例（T1–T7）

运行环境：在服务器上对 `127.0.0.1:8080` 跑，或从本机对 `192.168.3.14:8080` 跑；key 从环境变量取
（`MP_API_KEY`，默认 `111`，不写死）。套件位置：`tools/server/tests/`。

| ID | 测什么 | 脚本 | 判据 | 依赖 |
|---|---|---|---|---|
| **T1** | API 契约：非流式/流式 chat、usage 计数、400/401 | `test_mp_api.py` | 全过 | 服务在跑 |
| **T2** | batch 不变性：同题贪心单发 vs 批 2/4，逐 token 比对 | `test_mp_batch.py` | 完全相等 | exactness 设置（否则 xfail） |
| **T3** | 准入与邻居停顿：4 路在飞 + 1 条冷读 | `test_mp_admission.py` | 准入 ≤2 s、邻居停顿 ≤1 s、聚合 ≥ 基线 −3% | `api_monitor` 或客户端计时 |
| **T4** | 前缀缓存：同 prompt 两次 | `test_mp_prefix.py` | 命中计数/读时间、输出一致 | `/metrics` 计数 |
| **T5** | 投机接受率：开/关对照（换 env 需重启 → exp-） | `test_mp_spec.py` | 接受率 ≥ 基线、逐位相等 | 引擎日志 `drafts accepted A of B` |
| **T6** | SSD 前缀 KV：重启后同 prompt TTFT | `test_mp_persist.py`（暂缓） | 重启后首请求 TTFT 达标 | F5 落地后 |
| **T7** | 定容矩阵：1/2/3/4 路 | `test_mp_matrix.py` 或 `bench-strata.py` | 健康/无截断/聚合 | 服务器 |

**R1 基线（2026-10-24，L-046）**：T3 在 4×1200-token 流 + 452-token 冷读下，读者首 token **27.23 s**、邻居停顿
0.68 s；bench 单流 72.7 / 4 路聚合 120.5 tok/s；piped 冷读墙钟 14.1 s。T3 在 W3 前保持 xfail，xfail 消息带当时数字。

**xfail 纪律**：功能未落地时，测试可以 `@pytest.mark.xfail(strict=False, reason=...)`，但 reason 必须写清
"等哪个 W 项 / 哪条台账"；功能落地后**改 strict 并删掉 xfail**，否则视为未验收。

## 5. 执行顺序（R1–R9，每轮六步 + 复盘）

| 轮 | 内容 | 重启 | 出口 |
|---|---|---|---|
| R1 | W1 基线 + 跑 T1/T3 当前值（0 重启） | 0 | 基线落 `/opt/ai/logs/MP-*`，T3 记录当前失败值 |
| R2 | W2 配置臂（读分片）+ T3 对比 | ≤2 | 采纳/否决/未定 |
| R3 | W3 构建 + G1 门 | ≤2 | 补丁过门 |
| R4 | W3 验收：T3 strict + T2 | ≤1 | 多路四条全绿 |
| R5 | W4 定容 + T7 + F6 门脚本 | ≤2 | 矩阵全绿 |
| R6 | T4（前缀缓存）、T5（投机接受率） | ≤1 | 两项通过 |
| R7 | F5 判决（做/不做 SSD 持久化） | 0 | 判决 + 理由 |
| R8 | 清单收口：F1–F11 全部判决、文档更新 | 0 | 每项一条台账/复盘 |

## 6. 与 fork 测试的对照（为什么不能直接跑）

| fork 测试 | 能否在 Strata 跑 | 我们怎么用 |
|---|---|---|
| `tests/entrypoints/openai/*` | 否（要 vLLM server 进程） | 当 **API 契约**；T1 抽样重写 |
| `tests/v1/core/test_scheduler_e2e.py` | 否（`LLM` 类跑引擎） | 当**调度性质**参考：部分 prefill、前缀计数；T2/T3 |
| `tests/v1/core/prefix_cache/*` | 否（block manager 内部） | 当**缓存语义**参考；T4 |
| `tests/v1/spec_decode/*` | 否（引擎内部） | 当**接受率/正确性**参考；T5 |
| `vllm bench serve`（原 `benchmark_serving.py`） | 否（vLLM CLI） | 借**度量口径**（并发扫描、TTFT）；T7 |

## 7. 不做清单（清单内的"否决"）

- 不移植 F8 TP（L-033 已存档）、F9 权重路线、F10 vLLM 内核。
- 不为了"功能齐全"做引擎级重写；每个代码改动默认关、独立 build、可回退。
- 不把 vLLM 的调度器数据结构照搬；只落"步契约"与验收测试（§3）。

## 8. 版本行

- v0.1 · 2026-10-24 · 初稿：官方测试盘点（1,877 测试 / 70 API / 30 spec）、F1–F11 清单与判决位、G0–G4 门槛、
  T1–T7 用例、R1–R8 执行顺序、与 fork 测试的对照。
