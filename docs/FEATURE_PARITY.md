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
| **F1** | 多路 continuous batching 的**步契约**：每请求是 cursor（`computed` vs `target`）；每步先喂饱 running decodes，剩余预算给 waiting prefill；晚到 prefill 只推迟"还没吐 token"的请求（`num_output_tokens==0`） | `scheduler.py:606-615, 727, 1185-1235, 734-740`；CHANGELOG v0.2.2-post1 | **是（核心）** | 槽一起解码、`BGEN` 中途入批；**无统一 per-step 预算**；满槽时读根本发不出去 | W2（配置）→ W3（代码） | T2/T3 | **采纳·已落地·验收未过**（W3 已生产启用，但 T3 xfail：第二 token 38.65 s > 2 s；L-053/L-054） |
| **F2** | 单步 token 预算混批：所有 shipped profile 都是 `MAX_BATCHED_TOKENS=2048`；`request_token_budget = min(token_budget, input_budget - draft_slots)` | `scheduler.py:126-130, 624-627, 946`；`launcher.sh:6035` | **是** | `--prefill` 分块 + `STRATA_BATCH_DECODE_SHARE`，没有覆盖 decode+prefill 的统一预算 | W2 → W3 | T3 | **采纳·已落地·验收未过**（同 F1；W2 配置臂否决见 L-047；统一 per-step 预算未移植） |
| **F3** | 前缀缓存语义：refcount 共享 + CoW、命中块只读、最后一个 prompt token 重算取 logits、`num_cached_tokens` 计数 | `single_type_kv_cache_manager.py:300-322, 448-468`；`kv_cache_manager.py:275-285`；`tests/v1/core/prefix_cache/*` | **是** | 有 prompt cache / conversation cache；缺"缓存计数"的对外口径与测试 | 暴露统计 + 测试 | T4 | **采纳·已实现·验收通过**（T4 PASS） |
| **F4** | 投机解码（MTP/DFlash2）与**接受率验证实践**：混批里 MTP 在 chunk 边界留 lookahead；验证器 KV 精确；接受率有测试 | `tests/v1/spec_decode/*`；`scheduler.py:583-602`；CHANGELOG v0.2.2 | **是（验证方法）** | 已有 `--spec 8 --mtp-max-t 4`，热接受率 ~99%（L-026） | 验证实践（不开关，只补测试） | T5 | **采纳·已实现·验收通过**（T5 PASS） |
| **F5** | SSD 前缀 KV 持久化 #243：`OffloadingConnector` + FS tier（`checkpoint-<sha256>`、每 block 一个 `.bin`、`PYTHONHASHSEED=0`），命中异步装入 `WAITING_FOR_REMOTE_KVS` | `launcher.sh:1748-1807`；`kv_offload/file_mapper.py:121-148`；`offloading/scheduler.py:1036-1209`；验证 1,568/1,929（重启）等 | **是（重启场景）** | 无；重启冷加载 ~165 s | 暂缓（P3，触发条件：重启频率或冷读占比升高） | T6 | **搁置·待用户确认**（触发条件未出现；T6 未建） |
| **F6** | profile 定容与启动门：命名 `<decoder>-<kv>-<N>x<CTX>-<msg>`（N=`MAX_NUM_SEQS`，CTX=`floor(MAX_MODEL_LEN/1000)`）；launcher 启动后强制 `N×CTX ≤ GPU KV tokens`；health + smoke + 审计 | `profiles/README.md:21-24`；`launcher.sh:176-205, 5803-5809, 5337-5391` | **是** | 有参数（`--kv-resident`/会话槽/预留），**无预设、无启动门** | W4 预设 + 校验脚本 | T7 | **采纳·已实现·验收通过**（T7 PASS；N×CTX 矩阵另列 §10 待测） |
| **F7** | OpenAI（及 Anthropic）API 契约：chat/completions、流式、usage、错误码 | `tests/entrypoints/openai/`（70 文件） | **是** | Strata 已有 serve（自研 + 单测） | 抽样黑盒测试 | T1 | **采纳·已实现·验收通过**（T1 6/6 PASS） |
| **F8** | TP/PP 混合拓扑 | launcher TP/PP 支持 | **部分**：层切分已有；TP 已存档（L-033，单流 +3% 噪声内） | 不移植 TP；PP 不存在于 Strata | — | — | 否决（TP） |
| **F9** | FP8/NVFP4 权重路线 | README 的模型表 | **否**：权重栈完全不同（Strata 是 IQ pack，`--native`） | — | — | — | 否决 |
| **F10** | vLLM 内核（Marlin/FlashInfer/TurboQuant/FlashQLA） | csrc/、vllm/attention | **否**：引擎不同，不可移植 | — | — | — | 否决 |
| **F11** | KV 压力下的抢占与优先级（`fcfs`/`priority`，preempt 后回队首、重算） | `request_queue.py:75-158`；`scheduler.py:883-933` | **暂缓**：Strata 满槽排队在服务端（W3 先解决准入），多路下是否需要优先级未测 | — | W3 之后再评 | — | **搁置·待用户确认**（W3 已交付 TTFT；优先级/抢占无需求证据） |

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

**R4 修正（L-048）**：T3 曾经在“有空槽”的瞬间假通过（读者 0.25 s，引擎日志却是正常的 `slot 0 takes 452 tokens`）。
现在 T3 先等 `/metrics` 报 4 槽全忙，再要求读者**首 token 与第二 token 都在 2 s 内**（首 token 之后静默 27 s 不算准入）；
前提不满足时 xfail 并写明“测的是空槽”。A 上真基线：46.2 s / 0.97 s。 **R5（L-049）**：B 上首 token 1.73 s / 第二 token 32.91 s、长读 17.2 s / 34.4 s → T3 仍 xfail（第二 token 判据），W3 部分采纳。

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

## 9. 清单外的漏项（2026-10-25 重新盘点，子代理只读审计）

对 fork 的 serving / 多路功能做了一次清单外审计（静态阅读，未跑服务）：15 项新候选 S1–S15。
判决按“可移植性 × 对 2×2080 Ti 的价值 × 改动面”分级；**采纳 ≠ 已实现**，P1 排进后续轮次并补验收测试。

| ID | 项 | 判决 | 理由（一句话） |
|---|---|---|---|
| S1 | API 侧准入上限（`max_num_queued_*`，503） | **采纳·已有实现·缺测试** | `max_queue` + 429 `admission_full`（`serve/server.py:3963,6772`） |
| S2 | 响应体带每请求时延（TTFT/generation/queue/ITL） | **采纳·待实现 P1** | 服务端已有全部时间戳，补字段即可 |
| S3 | 每请求投机接受率（summary/detailed） | **采纳·待实现 P1** | 引擎已算 accepted/proposed，暴露即可调 `--spec` |
| S4 | 思考 token 预算（sampler 强制） | **否决** | Strata 已有 `reasoning_budget_tokens`（生产 12288），重复 |
| S5 | reasoning 感知的结构化输出 + 异步语法编译 | **搁置** | 需要语法后端 + sampler bitmask，改动面大；有需求再评 |
| S6 | 延迟等待队列（grammar/remote-KV 等） | **搁置** | 依附 S5；Strata 无这些等待态 |
| S7 | 断开 → 取消 → 引擎 abort | **采纳·已有实现·缺测试** | cancel/STOP 已存在（`serve/server.py:363,373`） |
| S8 | SSE 保活（`--sse-keep-alive-interval`） | **采纳·已有实现·缺测试** | `QUEUE_BEAT_S=10` 心跳（`serve/server.py:270,5198`） |
| S9 | 流式输出合并（背压） | **采纳·待实现 P2** | 慢客户端不拖引擎；服务端 merge 语义 |
| S10 | 阶段时延 + 等待原因指标 | **采纳·待实现 P2** | 引擎已有时间戳/状态，`/metrics` 暴露 |
| S11 | 全 ISL 准入 + KV 水位 | **采纳·待实现 P2** | 256K 下防过准入；全量检查很便宜 |
| S12 | 优雅停机（drain/abort） | **采纳·已有实现·缺测试** | `--shutdown-timeout` + drain（`serve/server.py:4031,8675`） |
| S13 | profile schema 契约（命名/校验） | **采纳·待实现 P2** | 纯 launcher/CI，和 F6 的预设命名同源 |
| S14 | 增量 detokenizer 的 stop 缓冲 | **采纳·已有实现·缺测试** | `StopMatcher`（`serve/server.py:3743`） |
| S15 | 结构化工具调用（structural tag） | **搁置** | 依赖 S5 的语法后端 |
| — | prefill 批屏障（fork 的 `--prefill-batch-barrier`） | **搁置** | 与 F1 的晚 prefill 策略重叠，先做实验再定 |
| — | warmup/JIT 冷编译预热 | **搁置** | 只在运行时重编译/捕获时有用，未证实 Strata 需要（静态，未验证） |
| — | 批量 chat 端点 `/v1/chat/completions/batch` | **搁置** | 全有或全无语义，对 4 槽收益不明 |
| — | `cache_salt` 前缀缓存命名空间 | **采纳·待实现 P2** | 小改动；多租户会话隔离用得上 |
| — | KV 驻留/前缀命中窗口指标 | **搁置** | 观测项，调缓存淘汰时再上 |

**审计口径**：以上来自对 `/home/kali/IdeaProjects/vLLM-2080Ti-Definitive`（93c9013）的只读审计，
证据是代码行号；**运行时行为均未在本机验证**（static, unconfirmed）。P1 项排进 R11+，每项落地时补测试。

## 8. 判决总表（2026-10-24 收口）

| 项 | 判决 | 证据 | 交付形态 |
|---|---|---|---|
| F1 步契约（decode 优先 + 残余预算 + 晚 prefill） | **部分采纳** | L-049：长读 TTFT 47.3→17.2 s、短读首 token 46.2→1.7 s；第二 token 仍等槽 31–34 s | W3 暂存读补丁（默认关，`STRATA_BATCH_STAGED_READ` + `STRATA_MP_STAGED_READ`）+ 服务端补丁 + T3 |
| F2 单步 token 预算混批 | **部分采纳** | 同 F1；W2 配置臂否决（L-047） | 同上（W3 是当前最小近似） |
| F3 前缀缓存语义 | **采纳** | T4：`cached_tokens` ≥50%，第二次读不慢（L-046/L-050） | 现有 prompt/conversation cache + T4 |
| F4 投机接受率验证实践 | **采纳** | T5：窗口 69.3%、累计 70.5%；bench 78–82%（L-050） | T5 门槛（offer≥20、≥50%） |
| F5 SSD 前缀 KV 持久化 | **搁置** | 触发条件（重启频率/冷读占比）未出现；T6 未建（L-051） | 无（P3，条件触发再评） |
| F6 定容/预设/启动门 | **部分采纳** | `check_preset.py` 对生产 OK；T7 负例 6/6（L-051/L-052）；fork 的 `N×CTX ≤ KV tokens` 在 KV 流式下不适用 | 预设名 `strata-022.mp-4x256.json` + `check_preset.py` + T7 |
| F7 OpenAI API 契约 | **采纳** | T1 6/6（health、models、usage、流式、400、401） | `tools/server/tests/test_mp_api.py` |
| F8 TP | **否决** | L-033：TP +3% 在噪声内；生产 2 卡层切分已够 | — |
| F9 FP8/NVFP4 权重 | **否决** | 权重栈不同（IQ pack / `--native`），不可移植 | — |
| F10 vLLM 内核 | **否决** | 引擎不同（CUDA/HIP/Metal 自研），不可移植 | — |
| F11 抢占与优先级 | **搁置** | W3 已交付 TTFT；满槽优先级/抢占无需求证据（L-051） | 无（有需求再评） |

**"完整可用"的自检**：每项有判决（上表 11/11）；采纳项都有可运行测试（T1/T3/T4/T5/T7）；
代码项默认关、可回退（W3 补丁 + 服务端补丁 + `check_preset.py`）；所有数字来自生产机 A-B-A（L-046…L-052）。
**未做**（明确记录）：T6（F5 搁置）；N×CTX 矩阵扫描（F6 只做静态/运行中对账 + 负例）；
W3 的 3 流形状与聚合吞吐（B 的 bench 未跑）。

## 8. 版本行

- v0.1 · 2026-10-24 · 初稿：官方测试盘点（1,877 测试 / 70 API / 30 spec）、F1–F11 清单与判决位、G0–G4 门槛、
  T1–T7 用例、R1–R8 执行顺序、与 fork 测试的对照。

## 10. v0.2 规则下的状态复位（2026-10-25）

按 `MULTIPATH_RULES.md` v0.2 的 DoD（落地 + 测试 PASS + 生产生效 + 可回退），本清单的**真实状态**：

| 状态 | 项 |
|---|---|
| 采纳·已实现·验收通过 | F3（T4）、F4（T5）、F6（T7）、F7（T1） |
| **采纳·已落地·验收未过** | **F1、F2**（W3 生产启用，但 T3 仍 xfail：第二 token 38.65 s） |
| 采纳·待实现 | S1、S2、S3、S7、S8（P1）；S9、S10、S11、S12、S13、S14、`cache_salt`（P2） |
| 搁置·待用户确认 | F5（T6 未建）、F11（优先级/抢占） |
| 否决 | F8、F9、F10、S4 |
| 待测 | N×CTX 容量矩阵（6×192K、8×128K），决定 F1/F2 能否**真正**达到“第二 token ≤2 s” |

**未完成计数以 `WORK_QUEUE.md` 为准（现 15 项：F1F2、C1 + 13 个 S 项，含 S0 发现项）**；另有 2 项搁置待确认。
按 v0.2 规则，这些全部 PASS 之前，目标不得标完成、不得停工。
