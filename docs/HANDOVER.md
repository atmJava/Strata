# AI 交接：Strata 2×2080 Ti 融合与性能优化

> 这份文档面向直接接手仓库的 AI。先读完本页，再按下面的入口读规则、状态和最近复盘。
> 交接时不要仅凭 health 200 或文件名判断部署版本；运行路径 hash 才算证据。

## 1. 先读什么

1. [`RULES.md`](RULES.md)：自主决策、实验、复盘和生产安全规则。
2. [`STATE.md`](STATE.md)：当前断点、生产形态和下一动作。
3. [`ROADMAP.md`](ROADMAP.md)：指标、候选路线和验收门限。
4. [`WORK_QUEUE.md`](WORK_QUEUE.md)：可做的候选及“不要再试”的项。
5. [`ITERATION_LOG.md`](ITERATION_LOG.md) 的最新两条：最近为什么这样决策。
6. 需要数字和原始日志时读 [`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md) 的 L-078–L-081。

历史交接和旧战役记录在 [`archive/`](archive/)，只用于追溯。

## 2. 仓库与融合状态

- 工作目录：`/home/kali/IdeaProjects/strata`
- 工作分支：`hw/server-adapt-0.2.3`
- 推送目标：`origin`，即 `https://github.com/atmJava/Strata.git`
- 上游：`Niko1221/Strata`；push URL 已禁用。**绝不向上游推送。**
- 上游 v0.2.6 已合并，merge commit 为 `daf51469`，合并基线为 `4b9e05a2`。
- 冲突文档按用户要求采用上游版本。
- 保留的本地性能代码提交包括：
  - `2a5f46e7`：P3 bounded decode lead-in。
  - `e81d7555`：自主优化记录整理。
- 参考仓库 `/home/kali/IdeaProjects/vLLM-2080Ti-Definitive` 未修改；仍为
  `main`、`93c9013`、tag `v0.2.2-post3`。

**最重要的事实：源码已融合，不等于生产引擎已重组。** 当前生产引擎仍是从旧代码构建的
0.1.49.1；从 merge 后 HEAD 构建的 sm_75 CUDA 引擎还没有完成测试和部署验证。后续不得把旧
生产二进制的结果写成“v0.2.6 融合引擎已验证”。

### 静态融合审计

合并后的 Qwen 主路径新增了 0.1.51 的 adaptive-async 自测选择：
`include/strata/core/adapt_async_default.hpp` 和 `src/core/adapt_async_default_test.cpp`。
按源码静态阅读（未做 GPU 实测），该自动路径要求 CUDA、`--serve`、expert arena、无多卡层切分、
无 pipeline windows、无 batch slots。当前生产是双卡层切分且 `parallel=6`，因此不会自动启用它。
这不能替代对新引擎的构建、测试和 A/B。

## 3. 当前生产

`strata.service` 在 `192.168.3.14:8080`。交接时最后一次 T-probe 记录：

| 项 | 值 |
|---|---|
| health | 200 |
| engine md5 | `8d680c695f1a6a3d9d202c61d17879f2` |
| server md5 | `de09cffdac39ff150a689bd3a44cc242` |
| launcher md5 | `0eeee1603ab6008261700fa183d20a79` |
| config md5 | `05c9d2539cd92b6c8e0b93ff8a48ce0e` |
| 最后记录进程 | server `253305`，engine `253321` |

运行形状：2×RTX 2080 Ti 22 GB、6×192K、groups=2、split24、`parallel=6`、INT8 KV。
关键环境：

```text
STRATA_ADMIT_SHORT_FIRST=2048
STRATA_BATCH_STAGED_READ=1
STRATA_EXP_MODE=8
STRATA_HC_Q8=1
STRATA_MP_STAGED_READ=1
STRATA_MP_STAGED_READ_MIN_TOKENS=4096
STRATA_PIPED_CONCURRENT=1
STRATA_PIPED_DECODE_LEADIN_MIN_TOKENS=4096
STRATA_PIPED_DECODE_LEADIN_MS=1600
STRATA_PIPED_DECODE_LEADIN_WAIT_MS=750
STRATA_SHARED_SLOT_ROPE=1
STRATA_SM75_PERF=1
```

`STRATA_ADMIT_SHORT_FIRST` 由 systemd service drop-in 提供，必须存在
`/etc/systemd/system/strata.service.d/admit-short-first.conf`。写进 engine config 的 `env`
只会进入引擎子进程，不会改变 Python server 的准入顺序。模板在
[`tools/server/experiments/strata.service.admit-short-first.conf`](../tools/server/experiments/strata.service.admit-short-first.conf)。

## 4. P4 候选的真实结论

P4 当前不是“完全验收”，而是**已部署候选**。候选组合是：

```text
STRATA_ADMIT_SHORT_FIRST=2048
STRATA_PIPED_DECODE_LEADIN_MS=1600
STRATA_PIPED_DECODE_LEADIN_WAIT_MS=750
STRATA_PIPED_DECODE_LEADIN_MIN_TOKENS=4096
```

一小时混合 soak（606 请求、202 waves）结果：

| 指标 | 基线 | 候选 | 变化 |
|---|---:|---:|---:|
| 96K 邻居下 41-token 代码 wall | 41.61 s | **4.424 s** | 大幅下降 |
| 96K 邻居下短 JSON wall | 40.83 s | **3.370 s** | 大幅下降 |
| 32K 四路 wave | 22.188 s | 22.804 s | +2.78% |
| 96K 四路 wave | 41.613 s | 42.666 s | +2.53% |
| 96K `by_kind` 单请求中位 | 40.96 s | 42.658 s | **+4.14%** |
| 相邻 400 ms D2 | — | — | **约 +3.21%** |

正确性/稳定性：HTTP 或执行错误 0、意外 `finish_reason=length` 0、96K needle 25/25、
swap 未使用。按 wave 口径通过 3% 门限；按单请求中位和相邻 D2 口径越界。这个歧义必须在下一轮
实验前固定，不能事后选择更宽的口径。

证据：

```text
/opt/ai/logs/P4-candidate-mixed-soak-20261025T2320Z.jsonl
sha256 27a5d6f2efda807c49fb3480b155b0f256b95c9ef7df653a22545d1af1285f15

/opt/ai/logs/P4-candidate-mixed-soak-20261025T2320Z.summary.json
sha256 c46339838e17f9734ee645afbf02ef226480715a5c70a735ee95fb01262dc72d

/opt/ai/logs/T-probe-handoff-20261026T0102Z.json
sha256 945903f5a735e80f005afb84f21af4726109b3cdb9b0f34c7bd7e4053a89cb19
```

## 5. 已否决，不要重复

| 路线 | 结论 |
|---|---|
| P4 lead-in 1200 ms | 代码请求没有改善，96K 超门限 |
| P4 lead-in 2000 ms | 首次代码 5.254 s，重复中位 41.800 s；不稳定 |
| QSA slots `STRATA_QSA_SLOTS=1` | 4 路无收益；6 路首次 +3.5% 不复现，最终相邻 on 还慢 0.49% |
| 固定 read piece 32768/65536 | 96K 长档分别慢 15.6%/7.3% |
| S0b 并发读修 gate | 路径触发但邻居停顿无收益，0.1.51 单路和聚合回退 |
| groups=1、groups=1+batch-MTP、split25/26 | 已测，均不采纳 |

QSA 证据：`/opt/ai/logs/bench-results.jsonl`
（sha256 `22cd1164f4e171aa3cc260c0bcd1b85362339105be88d2c0f15c096a42da8804`）以及
`/opt/ai/tmp/QSA-SLOTS-20261026T0023Z/` 两份 config。不要把首次高样本当结论。

## 6. 测试与构建状态

已在本地/离线树通过：

- 交接前最终回归：`tools/server/tests` + AMD/DeepSeek setup 为 **211 passed、10 skipped、
  2 subtests passed**；admission/staged 三个 Python 测试文件为 **19 passed**；
  `py_compile`、JSON 解析与 `git diff --check` 通过。
- 独立 CPU 构建与 `ctest`：59 passed，4 skipped。
- `tools/server/tests`：73 passed，10 skipped。
- staged/admission 专项：19 passed。
- 工具链 + AMD setup + DeepSeek setup：211 passed，10 skipped。
- `tools/test_setup_amd.py`：52 passed。
- `tools/test_setup_dsv41.py`：86 passed；该文件的 engine 安装路径已 mock，避免测试真实联网下载。

全量 setup 测试仍为 **833 passed、52 failed、1 skipped、208 subtests passed**。失败集中在：

- 48 个 golden 子用例：上游 GPU 角色编号/默认顺序变化、Linux vision 可执行文件名后缀和黄金配置漂移；
- 4 个 Unsloth 用例：GPU 角色顺序预期漂移。

这还没有被证明是产品回归还是测试夹具陈旧。**不要运行 `--write` 覆盖 golden。** 应逐项比较
fixture 与生成结果，先判断是上游预期变化还是本分支融合错误。

## 7. 接下来按优先级做

### P0：从当前 HEAD 构建并验证 sm_75 引擎

在服务器独立目录同步当前提交，不复用生产树，不覆盖 `/opt/ai/src/strata-0.2.2`。建议：

```sh
cmake -S <merged-src> -B <merged-src>/build75-new \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.0/bin/nvcc \
  -DCMAKE_CUDA_ARCHITECTURES=75 \
  -DSTRATA_ENABLE_CUDA=ON \
  -DSTRATA_GGML_DIR=/opt/ai/src/llama.cpp-3cf03257 \
  -DSTRATA_NATIVE_EXPERTS=ON \
  -DSTRATA_MMQ_KQUANTS=ON \
  -DSTRATA_MMQ_TQ_OVERLAY=ON \
  -DSTRATA_BUILD_TESTS=ON
cmake --build <merged-src>/build75-new --target strata -j6
ctest --test-dir <merged-src>/build75-new --output-on-failure
<merged-src>/build75-new/strata --version
```

如果 CMake 对新上游选项有变化，以实际 `CMakeLists.txt` 为准。构建后先跑 CPU/离线测试、greedy
parity、模型加载和短 bench；通过后再部署到独立副本并做 T-probe。不能直接替换生产二进制。

### P1：固定 P4 判据并测边界

先写下并冻结：

1. 96K 四路 wave 不超过相邻基线 3%；
2. 96K `by_kind` 单请求中位也不超过相邻基线 3%；
3. 代码 wall ≤10 s、短 JSON wall ≤4 s；
4. 零错误、零意外 length、needle 全对；
5. 同一 boot，1500/1550/1600 ms 交错 A-B-A，不只取一次最好样本。

完成前保留 1600 ms 为候选；若两口径不能同时通过，回滚到 400 ms P3 E 配置。

### P1：验证 staged 阈值 4096 → 约 2048

已加入：

```sh
python3 tools/server/probe_slot_admission.py \
  --slots 6 --rounds 3 --neighbour-tokens 400 \
  --reader-prompt-tokens 2048 --out /opt/ai/logs/<name>.json
```

先让 6 个约 400-token 邻居占满槽，再发约 2K reader；记录 reader latency、邻居停顿、内存、
health 和回滚。不要改阈值后只发短 prompt，因为那不会走 staged-read 路径。

### 后续

- 补 S0d/S8/S1/S7 等低风险回归测试。
- 只有 DFlash2 drafter 权重到位后恢复 V3-DF2；不要空等。
- 0.1.51 若继续追查，只在离线树 bisect 具体回退，不整包替换生产。

## 8. 安全部署与回滚

任何生产修改前先快照 service、launcher、server、config 和环境。一次只运行一个安装器；改后必须
用 T-probe 从 `/proc/<pid>/cmdline` 和 `/proc/<pid>/environ` 核对实际运行文件与 env。

400 ms P3 E 完整配置回滚：

```sh
/opt/ai/bin/use-config.sh \
  /opt/ai/tmp/pre-P3-ABA-20261025T213040Z/strata-022.json
```

还要移除候选 service drop-in：

```sh
sudo mv /etc/systemd/system/strata.service.d/admit-short-first.conf \
  /opt/ai/tmp/P4-ADMIT-<UTC>/admit-short-first.conf
sudo systemctl daemon-reload
```

部署门禁：

```sh
python3 tools/server/probe-version-stamp.py --local \
  --base-url http://127.0.0.1:8080 --api-key "$MP_API_KEY" \
  --expect-engine-md5 <expected-md5> \
  --expect-env-file tools/server/production-env-2080ti.json \
  --out /opt/ai/logs/T-probe-<UTC>.json
```

不要把 API key 写入仓库或日志。上游地址保持只读；只推送到 `origin`。
