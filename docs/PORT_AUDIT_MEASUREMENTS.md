# 移植文档关键数字：独立重测（只读审计）

> 目的：把 `docs/VLLM_PORT_PLAN.md` §P3b / §P3c / §P4 与 verify 三件套的数字，在服务器上**重测一遍**，
> 只记录、不改代码、不 commit。本文件是唯一新建的文件。
>
> 结论一句话：**§P4 预热组、§P3c 的 parity 与 timing、verify 3/3 复现；§P3b 的表（R=160/626 的 TOPS）不复现
> （实测低 11–20%），而 §P3c 引用的同一个 bench 的 31.9 TOPS 复现；§P3c "不设 `STRATA_PF_FUSED` 就 skip"
> 这一条用该测试二进制**测不出来**（它自己 `setenv`）。另外拿到一条新性能事实：长上下文下 QSA 层每步的
> 固定成本由 `qsa_block_scores` 主导（256K 时 68 µs/次，已占 T=1 层时间的 48%），而"对选择集做注意力"
> 的每行增量几乎不随 ctx 变。**

---

## 0. 环境

### 0.1 时间与卡

| 项 | 值 |
|---|---|
| 测量窗口 | 2026-10-25 09:14Z – 09:21Z（Asia/Shanghai 17:14 – 17:21） |
| 卡 | 2× NVIDIA GeForce RTX 2080 Ti 22 GiB（sm_75）；除 §3.4 披露的一次误跑外，全部 `CUDA_VISIBLE_DEVICES=1` |
| GPU1 UUID | `GPU-079483e5-1081-10ff-6ee7-dcf21fcfc66a` |
| 驱动 / CUDA | 595.71.05 / CUDA 13.0.88（`/usr/local/cuda-13.0/bin/nvcc`） |
| 时钟 | 空闲 300–435 MHz SM / 405–810 MHz mem；满载 1905–1920 MHz SM / 6800 MHz mem（max 2100/7000） |
| 功耗上限 | 280 W（两张卡相同），persistence mode 关 |
| 温度 | 31–37 °C |

### 0.2 生产服务占用（全程未触碰）

```
$ nvidia-smi --query-compute-apps=pid,used_memory,gpu_uuid --format=csv
pid, used_gpu_memory [MiB], gpu_uuid
334191, 21136 MiB, GPU-7cfb54d5-77b7-8f9d-ba3a-61d4d18d8759   # GPU0
334191, 21058 MiB, GPU-079483e5-1081-10ff-6ee7-dcf21fcfc66a   # GPU1

$ ps -o pid,etime,cmd -p 334191
    PID     ELAPSED CMD
 334191    04:12:45 /opt/ai/src/strata-0.2.2/build/strata --serve --pack /opt/ai/strata-data/packs/sc117_iq3_xxs ...
```

测量前后 GPU1 `memory.used` 均为 **21134 MiB**（free 868 MiB），PID 334191 的 ELAPSED 从 4:12:45 单调涨到
4:20:21 —— 服务没有重启。注意：**两张卡都被这一个进程占着**（GPU0 21212 MiB / free 788 MiB），所以任何时候
都不设 `CUDA_VISIBLE_DEVICES` 都会落到 GPU0（见 §3.4 的一次误跑）。

### 0.3 构建树与版本（**没有 .git**）

```
$ git -C /opt/ai/src/strata-port-20261025 rev-parse --short HEAD
fatal: not a git repository (or any of the parent directories): .git
```

构建树是源码拷贝，不是 git 仓库，**拿不到 commit**。能给的版本证据是 md5：

| 文件 | 远端树 | 本地工作区（`738c65d9`） |
|---|---|---|
| `src/kernels/cuda/qsa_decode_attn.cu` | `a3e849b78ebf` | `a3e849b78ebf` |
| `tests/cuda/prefill_fused_iq_test.cpp` | `bb933b95085c` | `bb933b95085c` |
| `src/prefill/mmq_expert_bench.cpp` | `020ff1530b6d` | `020ff1530b6d`（本地 HEAD 上是 M 状态） |
| `src/kernels/qsa_rows_bench.cpp` | `c86129cf539a` | `c86129cf539a` |
| `src/prefill/moe_fused_iq.cu` | `6b95e9eb243d` | `6b95e9eb243d` |
| `docs/VLLM_PORT_PLAN.md` | `d8c0d152cb00` | `227de8de4b58` |

即：**构建树 = 本地工作区（含未提交修改）**，只有 `docs/VLLM_PORT_PLAN.md` 是旧副本（远端那份没有 §P4 的
29.5/36.6/62.9/77.3 预热表）。本文所有对照都用本地 `docs/VLLM_PORT_PLAN.md`（`227de8de4b58`）。

构建参数（`build75/CMakeCache.txt`）：`CMAKE_BUILD_TYPE=Release`、`CMAKE_CUDA_ARCHITECTURES=75`、
`CMAKE_CUDA_COMPILER=/usr/local/cuda-13.0/bin/nvcc`、`CMAKE_GENERATOR=Unix Makefiles`。

**构建动作**：先 `--target help` 确认目标名（`qsa_rows_bench` / `mmq_expert_bench` / `prefill_fused_iq_test` /
`verify_parity` / `spec_verify_parity` / `verify_batch_parity` 都在），dry-run 显示只有两个目标是陈旧的
（`mmq_expert_bench` 二进制 07:24 比 `libstrata_mmq.a` 08:37 旧；`spec_verify_parity` 要重链），于是：

```
$ nice -n 19 cmake --build build75 --target mmq_expert_bench spec_verify_parity -j4
[ 87%] Linking CXX executable mmq_expert_bench
[100%] Built target mmq_expert_bench
[100%] Linking CXX executable spec_verify_parity
[100%] Built target spec_verify_parity
```

重链后 `mmq_expert_bench` 与 `ctest -R verify` 都重测过（§1.3、§1.4），**数字不变**。之后
`make -n qsa_rows_bench prefill_fused_iq_test mmq_expert_bench` 无待执行步骤，三个 bench 与源码同步。

### 0.4 显存纪律（每次运行轮询峰值，全部 < 800 MiB）

基线 = 服务占用 21134 MiB。峰值用 `nvidia-smi -i 1 --query-gpu=memory.used` 0.3 s 轮询取最大：

| 运行 | 峰值 used (MiB) | Δ vs 21134 |
|---|---:|---:|
| `qsa_rows_bench --ctx 32768` | 21390 | **+256** |
| `qsa_rows_bench --ctx 131072` | 21506 | **+372** |
| `qsa_rows_bench --ctx 262144` | 21656 | **+522** |
| `prefill_fused_iq_test`（仅 parity，`R64_E=16`） | 21410 | **+276** |
| `prefill_fused_iq_test`（parity + timing，`S20_E=16`） | 21712 | **+578** |
| `mmq_expert_bench 12 160 626` | 21514 | **+380** |
| `ctest -R verify` | 21134 | ≈0 |
| `prefill_fused_iq_test`（默认 `S20_E=512`） | — | **OOM**（§1.2） |

### 0.5 测量协议

- 预热：每个配置前连续 5 次同参运行（`qsa_rows_bench` 每次自身还会对每个测量做 4 次暖调 + 3 次计时取中位）。
- 单进程内：`qsa_rows_bench` 内部 3 次取中位；`mmq_expert_bench` 内部 25 次取中位；`prefill_fused_iq_test`
  内部交替 MMQ/fused 取中位。跨进程至少 3 轮，报中位。
- 交错 A/B：P4 的 131072 / 32768 / 262144 三轮按 A-B-C、C-B-A、A-B-C 交错；P3c 的 `STRATA_PF_FUSED=1`
  与不设 env 的两种跑法 3+3 交错；P3b 连跑 5 次。
- 每次运行前后记 `nvidia-smi --query-gpu=clocks.sm,clocks.mem`。
- **口径警告（本次最重要的方法学发现）**：`qsa_rows_bench` 一次只跑 1.6 s，空闲 3 s 卡就掉回 300–435 MHz。
  "先跑 5 次预热"并不能保证**下一个进程**在升频态启动 —— 只有在进程启动时 SM 已经在 1905/1920 MHz，
  才拿到文档的"预热组"。落到哪个组，由 `before=` 的时钟直接标出（§1.1 有逐轮读数）。

---

## 1. 逐条测量

### 1.1 P4 · decode-attn（`qsa_rows_bench`，ctx 131072）

命令原文（与任务/文档一致）：

```
CUDA_VISIBLE_DEVICES=1 ./build75/qsa_rows_bench --ctx 131072 --max-context 131072 --iters 30 --rows
```

**原始输出（预热态，`before=1920 MHz, 6800 MHz`，完整一块）**：

```
qsa_rows_bench on device 0 (NVIDIA GeForce RTX 2080 Ti, 7.5), 30 iterations: context 131072, capacity 131072 cells (max_blocks 32770), selection cap 2051, int8 pools over 131140 cells
  selections: width 2051 a row; union of 2 rows 2475 cells (1.21x one row) union of 3 rows 2715 cells (1.32x one row) union of 4 rows 2882 cells (1.41x one row)
-- a QSA layer's decode kernels at the verification window's shape (us a call, median of 3 runs)
  scores  qsa_block_scores (multi)             T=1     35.9 T=2     37.8 T=3     43.1 T=4     44.9  | +3.0 us a row
  topk    qsa_block_topk                       T=1     28.1 T=2     28.4 T=3     29.7 T=4     30.7  | +0.9 us a row
  attn    qsa_decode_attn_batch cap 2051       T=1     29.4 T=2     36.5 T=3     62.8 T=4     78.1  | +16.2 us a row
  append  native_qsa_indexer_append_steps      T=1      2.6 T=2      2.6 T=3      2.5 T=4      2.5  | +-0.0 us a row
  layer   append + scores + topk + attn        T=1    108.8 T=2    115.1 T=3    149.3 T=4    162.4  | +17.9 us a row
-- the drafter's dense window (32768 cells a row): the shape the hypothesis's ~240 us a query comes from
  dense   qsa_decode_attn_batch cap 32768      T=1    461.2 T=2    736.0 T=3   1020.9 T=4   1308.8  | +282.6 us a row
-- per row: 17.9 us a QSA layer -> x4 layers (stage 0) 0.07 ms, x8 layers (stage 1) 0.14 ms
-- STRATA_QSA_ROWS: qsa_block_scores_rows against qsa_block_scores (multi), scores and ids bit for bit
  parity: 8 cases, 0 differ -> bit-identical
  scores  qsa_block_scores (multi, reference)  T=1     35.9 T=2     37.8 T=3     43.1 T=4     44.9  | +3.0 us a row
  scores  qsa_block_scores_rows                T=1     35.6 T=2     37.4 T=3     49.3 T=4     55.8  | +6.7 us a row
qsa_rows_bench: done
### after=1905 MHz, 6800 MHz, 113.30 W
```

**跨进程 4 个预热轮（每轮都是"启动时 1905/1920 MHz"）**：

| 轮 | 启动时钟 | attn T=1..4 | layer T=1..4 |
|---|---|---:|---:|
| R2C | 1905/6800 | 29.7 / 36.6 / 63.6 / 76.8 | 109.4 / 115.5 / 150.5 / 159.7 |
| R3A | 1905/6800 | 29.7 / 36.7 / 63.0 / 77.2 | 109.4 / 115.7 / 149.2 / 160.4 |
| R5A | 1905/6800 | 29.0 / 36.0 / 61.2 / 77.6 | 109.1 / 115.4 / 148.3 / 161.9 |
| R7（上块全文） | 1920/6800 | 29.4 / 36.5 / 62.8 / 78.1 | 108.8 / 115.1 / 149.3 / 162.4 |
| **中位** |  | **29.6 / 36.6 / 62.9 / 77.4** | **109.3 / 115.5 / 149.3 / 161.2** |

每行增量：attn **+15.9 µs**（(77.4−29.6)/3），层合计 **+17.3 µs**（(161.2−109.3)/3）。
分量中位：scores 35.9 / 37.8 / 43.1 / 44.9，topk 28.3 / 28.6 / 29.6 / 30.8，append ≈2.5–2.6。
attn 占层每行增量的比例：15.9/17.3 = **92%**。

**冷启动轮（启动时 300–435 MHz，同一条命令）**，3 轮中位：

| 阶段 | T=1 | T=2 | T=3 | T=4 |
|---|---:|---:|---:|---:|
| scores | 37.2 | 39.9 | 44.8 | 45.0 |
| topk | 39.2 | 40.0 | 32.8 | 30.9 |
| attn | 41.2 | 49.4 | 68.3 | 77.6 |
| layer | 133.9 | 143.2 | 157.1 | 161.0 |

冷/热比（attn）：**+39% / +35% / +9% / +0.3%** 于 T=1..4（文档写 +39.7/+35.2/+29.4/+23.4）。

**结论：复现（预热组）。** 与文档预热组 29.5/36.6/62.9/77.3 与 109.2/115.2/148.9/160.7 逐项差
≤0.5%（attn +0.3/+0.0/+0.0/+0.1%，层合计 +0.1/+0.3/+0.3/+0.3%），每行增量 15.9/17.3 对文档 15.9/17.2。
冷启动组的 T=1/T=2 也复现（41.2/49.4 对 41.2/49.5），**但 T=3/T=4 不复现**：文档冷表 attn 81.4/95.4、
scores 49.6/51.0、topk 40.4/40.5，本次冷表 attn 68.3/77.6、scores 44.8/45.0、topk 32.8/30.9。
另外 `STRATA_QSA_ROWS` 的方向复现：T=1 35.6 vs 35.9（快 0.3），T=3/T=4 49.3/55.8 vs 43.1/44.9（慢 6.2/10.9），
即"T≥3 反向"仍成立（文档记的 −1.4/−2.3/+0.7/+7.4 幅度不同，但方向一致）。

### 1.2 P3c · fused IQ（IQ3_XXS / IQ4_NL）

#### (a) 任务给的命令原文

```
CUDA_VISIBLE_DEVICES=1 R64_E=16 STRATA_PF_FUSED=1 ./build75/prefill_fused_iq_test --only="IQ3_XXS / IQ4_NL"
```

**原始输出（全部）**：

```
strata: prompt fused experts (sm_75): Q2_0 / Q2_0 set up, IQ3_XXS / IQ4_NL set up at both shapes (WW=2 46592 B, WW=4 60672 B fits)
IQ3_XXS / IQ4_NL - reference part: 256 tokens, 16 experts, rows per expert: max 252, experts without rows 4
  fused vs the int8-rounded model (same activation / H rounding, double sums): rel RMS 1.222e-04  worst 3.635e-03
  MMQ   vs FP32 reference: rel RMS 1.428e-02  worst row max rel 2.678e-02
  fused vs FP32 reference: rel RMS 1.428e-02  worst row max rel 2.678e-02
  fused vs MMQ           : rel RMS 1.213e-04  worst row max rel 3.635e-03
prefill fused MoE (native formats) parity failed: cudaMalloc: out of memory
EXIT=1 secs=4
```

parity 四行的 **rel RMS / worst**：`1.222e-04 / 3.635e-03`（fused vs 自身 int8 模型）、`1.428e-02 / 2.678e-02`
（MMQ vs FP32）、`1.428e-02 / 2.678e-02`（fused vs FP32）、`1.213e-04 / 3.635e-03`（fused vs MMQ）。

**结论（parity）：复现，逐位对上。** 文档 1.213e-4 / 1.222e-4 / 3.635e-3（与 MMQ vs FP32 1.43e-2）全中；
smem banner 的 46,592 B / 60,672 B 也与 §P3c 的 WW=2 / WW=4 数字一致。

**但 timing 段 OOM**：该命令不带 `S20_E`，timing 段按默认 **512** 专家组分配，在只剩 868 MiB 的卡上
`cudaMalloc` 失败（退出码 1，消息误写作 "parity failed"）。这是**环境限制导致的部分复现**，不是数字不符。
顺带确认：`R64_E` / `S20_E` 确实**只从环境读**（`tests/cuda/prefill_fused_iq_test.cpp:315` 与 `:435` 都是
`std::getenv`），写成位置参数会被忽略、退回 E=64（约 1 GB，本卡放不下）——任务说明里的这条提醒是对的。

#### (b) 能跑通 timing 的参数（文档 §P3c 表用的就是这组）

```
CUDA_VISIBLE_DEVICES=1 R64_E=16 S20_E=16 STRATA_PF_FUSED=1 ./build75/prefill_fused_iq_test \
    --only="IQ3_XXS / IQ4_NL" --chunks=20,256,1002
```

**原始输出（一次完整跑，节选 timing 段）**：

```
  (MMQ's products alone, no gathers: 0.233 ms - fused 0.71x)
  (fused back to back: median 0.325 ms, min 0.322; alternating min 0.324; MMQ min 0.381)
  fused output bits hash 8dec9a3566ac78b2
IQ3_XXS / IQ4_NL - timing part: 20 tokens x top 10 over 16 experts, one layer: MMQ path 0.384 ms, fused 0.327 ms (1.17x); fused vs MMQ rel RMS 2.118e-07 worst row 4.828e-07
  sample of 96 pairs vs FP32 reference: MMQ rel RMS 1.519e-02 worst 2.098e-02, fused rel RMS 1.519e-02 worst 2.098e-02
  (MMQ's products alone, no gathers: 0.915 ms - fused 0.99x)
  (fused back to back: median 0.921 ms, min 0.918; alternating min 0.917; MMQ min 1.059)
  fused output bits hash 7f1217b0a1e7890c
IQ3_XXS / IQ4_NL - timing part: 256 tokens x top 10 over 16 experts, one layer: MMQ path 1.065 ms, fused 0.922 ms (1.16x); fused vs MMQ rel RMS 2.416e-04 worst row 3.025e-03
  sample of 96 pairs vs FP32 reference: MMQ rel RMS 1.474e-02 worst 2.128e-02, fused rel RMS 1.474e-02 worst 2.128e-02
  (MMQ's products alone, no gathers: 3.006 ms - fused 0.98x)
  (fused back to back: median 3.115 ms, min 3.107; alternating min 3.003; MMQ min 3.038)
  fused output bits hash 5e807f6c6a4021b4
IQ3_XXS / IQ4_NL - timing part: 1002 tokens x top 10 over 16 experts, one layer: MMQ path 3.110 ms, fused 3.080 ms (1.01x); fused vs MMQ rel RMS 1.838e-04 worst row 3.096e-03
  sample of 96 pairs vs FP32 reference: MMQ rel RMS 1.425e-02 worst 2.097e-02, fused rel RMS 1.425e-02 worst 2.097e-02
prefill fused MoE (native formats) parity passed
EXIT=0 secs=13
```

**跨进程 3 轮（`STRATA_PF_FUSED=1`，中位）**：

| R（行/专家） | MMQ 全路 | fused | fused/MMQ | MMQ 仅积（无 gather） | 文档（MMQ / fused / 倍数） |
|---|---:|---:|---:|---:|---|
| ≈12（20 tok） | 0.387 ms | 0.332 ms | **1.17×** | 0.238 ms | 0.400 / 0.344 / 1.16× |
| 160（256 tok） | 1.076 ms | 0.942 ms | **1.14×** | 0.928 ms | 1.111 / 0.986 / 1.13× |
| ≈626（1002 tok） | 3.145 ms | 3.113 ms | **1.01×** | 3.050 ms | 3.269 / 3.228 / 1.01× |

**结论：复现。** 三条倍数 1.17 / 1.14 / 1.01 对文档 1.16 / 1.13 / 1.01；绝对时间比文档低 3–5%
（本次卡在 1350 MHz，见 §1.3 的时钟讨论）。fused 输出 hash 在 3 轮里稳定（8dec9a35… / 7f1217b0… / 5e807f6c…），
parity 在 timing 段也全绿（rel RMS 2.1e-07 / 2.4e-04 / 1.8e-04，worst ≤3.1e-03）。

#### (c) "不设 `STRATA_PF_FUSED` 应当 skip" —— **测不出来**

```
CUDA_VISIBLE_DEVICES=1 R64_E=16 S20_E=16 ./build75/prefill_fused_iq_test \
    --only="IQ3_XXS / IQ4_NL" --chunks=20,256,1002     # 注意：没有 STRATA_PF_FUSED
```

原始输出（3 轮，只摘 timing 首行与 fused 交替行）：

```
### C1 STRATA_PF_FUSED=<unset> before=1350 MHz
  (fused back to back: median 0.330 ms, min 0.328; alternating min 0.328; MMQ min 0.383)
IQ3_XXS / IQ4_NL - timing part: 20 tokens x top 10 over 16 experts, one layer: MMQ path 0.388 ms, fused 0.331 ms (1.17x)
### C2 ...
IQ3_XXS / IQ4_NL - timing part: 256 tokens x top 10 over 16 experts, one layer: MMQ path 1.077 ms, fused 0.940 ms (1.15x)
### C3 ...
IQ3_XXS / IQ4_NL - timing part: 1002 tokens x top 10 over 16 experts, one layer: MMQ path 3.154 ms, fused 3.092 ms (1.02x)
```

与设置 `STRATA_PF_FUSED=1` 的 3 轮（20 tok 中位 0.387/0.332、256 tok 1.076/0.942、1002 tok 3.145/3.113）**在噪声内完全相同**，
而且仍有 `fused back to back` 段 —— 即 **fused 路径照跑，没有 skip**。

原因在测试源码本身（`tests/cuda/prefill_fused_iq_test.cpp:649-656`）：`main()` 一进来就无条件执行

```cpp
setenv("STRATA_PF_FUSED", "1", 1);      // overwrite = 1，外部 unset 无效
setenv("STRATA_PF_FUSED_KQ", "1", 1);
```

所以外部"不设 env"对这个二进制不构成任何差别。**这条判定为"无法测"**（不是"不符"）。要测默认路径，
需要另写一个不 pre-set env 的探针，或从引擎启动日志看 `native_supported` 的返回（代码上是
`env<0 → false`，属**未经本次实测的静态读码**）。

### 1.3 P3b · MMQ 专家速率（`mmq_expert_bench`）

`--target help` 里的目标名是 `mmq_expert_bench`（不是 `mmq_expert_bench_test`）。它**不接受 `--help`**：
`--help` 会被 `atoi` 成 0，打印一行 R=0 的假 0.0 TOPS（这也是我第一次误跑的形式）。R 是位置参数：

```
CUDA_VISIBLE_DEVICES=1 ./build75/mmq_expert_bench 12 160 626
```

**原始输出（一次完整跑）**：

```
mmq_expert_bench: mode w4a8 (FP4 activations: no), 16 experts a launch, medians of 25
  R   12  nvfp4 gate/up     0.172 ms      7.3 TOPS    171.4 GB/s weights
  R   12  nvfp4 down        0.119 ms      5.3 TOPS    124.1 GB/s weights
  R   12  q8_0 down         0.104 ms      6.1 TOPS    268.7 GB/s weights
  R   12  iq3_s gate/up     0.181 ms      6.9 TOPS    124.2 GB/s weights
  R   12  iq4_nl down       0.084 ms      7.5 TOPS    175.7 GB/s weights
  R   12  iq3_xxs gate/up    0.158 ms      8.0 TOPS    126.9 GB/s weights
  R   12  iq3_xxs down      0.074 ms      8.5 TOPS    108.1 GB/s weights
  R  160  nvfp4 gate/up     0.734 ms     22.9 TOPS     40.2 GB/s weights
  R  160  nvfp4 down        0.530 ms     15.8 TOPS     27.8 GB/s weights
  R  160  q8_0 down         0.371 ms     22.6 TOPS     75.1 GB/s weights
  R  160  iq3_s gate/up     0.576 ms     29.1 TOPS     39.1 GB/s weights
  R  160  iq4_nl down       0.315 ms     26.6 TOPS     46.8 GB/s weights
  R  160  iq3_xxs gate/up    0.526 ms     31.9 TOPS     38.1 GB/s weights
  R  160  iq3_xxs down      0.259 ms     32.4 TOPS     31.0 GB/s weights
  R  626  nvfp4 gate/up     2.603 ms     25.2 TOPS     11.3 GB/s weights
  R  626  nvfp4 down        2.102 ms     15.6 TOPS      7.0 GB/s weights
  R  626  q8_0 down         1.225 ms     26.8 TOPS     22.7 GB/s weights
  R  626  iq3_s gate/up     1.782 ms     36.8 TOPS     12.6 GB/s weights
  R  626  iq4_nl down       1.222 ms     26.9 TOPS     12.1 GB/s weights
  R  626  iq3_xxs gate/up    1.796 ms     36.5 TOPS     11.2 GB/s weights
  R  626  iq3_xxs down      1.020 ms     32.2 TOPS      7.9 GB/s weights
EXIT=0 secs=6
```

**stability / 时钟检查**：连跑 5 次，`iq3_xxs gate/up R=160` = 31.9 / 31.8 / 31.8 / 31.9 / 31.9 TOPS，
`iq4_nl down R=160` = 26.7 / 26.6 / 26.6 / 26.6 / 26.6；`after` 时钟一直是 1350 MHz（这个 bench 是
"kernel 突发 + 大量 CPU 计时"，卡停在 1350，不像 `qsa_rows_bench` 那样升到 1920）。
专门做了一次"先跑 qsa 把卡顶到 1905 再跑"的对照：`R=12` 从 8.0 升到 **10.4 TOPS**（文档 9.3），
但 `R=160` 仍是 **31.7**、`R=626` 仍是 **35.8** —— **R≥160 的数字对时钟不敏感**，不是升频就能补到 39.0。

| 案例 | 文档 §P3b 表 R=12 | 本次 R=12 | 文档 R=160 | 本次 R=160（中位） | 文档 R=626 | 本次 R=626 |
|---|---:|---:|---:|---:|---:|---:|
| iq3_xxs gate/up 1280×2560 | 9.3 | 8.0（1905 MHz 时 10.4） | **39.0** | **31.9** | 45.8 | 36.5 |
| iq3_xxs down 2560×640 | 9.8 | 8.5 | **38.3** | **32.4** | 37.4 | 32.2 |
| iq4_nl down | 8.2 | 7.5 | **31.1** | **26.6** | 30.2 | 26.9 |

**结论：§P3c 引用的 31.9 / 25.9 复现（31.9 / 26.6）；§P3b 表不符**（R=160 低 15–18%，R=626 低 11–20%，
R=12 低 9–14%，且 R≥160 的差不能用时钟解释）。同一份文档里两张表对**同一个 case** 给出 39.0 和 31.9，
差 22%，本次实测站在 31.9 一边。附带：§P3b 用 39.0 算的"int8 峰值 456 TOPS 的 8.5%"按本次应是 **7.0%**。

### 1.4 verify 三件套

```
cd build75 && CUDA_VISIBLE_DEVICES=1 ctest -R verify
```

**原始输出**：

```
Test project /opt/ai/src/strata-port-20261025/build75
    Start 53: verify_parity
1/3 Test #53: verify_parity ....................   Passed    0.53 sec
    Start 59: spec_verify_parity
2/3 Test #59: spec_verify_parity ...............   Passed    5.70 sec
    Start 88: verify_batch_parity
3/3 Test #88: verify_batch_parity ..............   Passed    0.39 sec

100% tests passed, 0 tests failed out of 3

Total Test time (real) =   6.63 sec
CTEST_EXIT=0 secs=7
```

**结论：复现，3/3 通过，6.63 s。** `spec_verify_parity` 在重链（§0.3）后重跑仍 3/3、6.65 s，
且它确实是耗时主项（5.69 s / 6.65 s）。卡上 `memory.used` 前后都是 21134 MiB（这套测试几乎不占显存）。

### 1.5 额外：ctx 32768 / 131072 / 262144（长上下文是否更贵）

命令（三段只改 ctx/max-context，其余同 §1.1）：`--ctx {32768,131072,262144} --max-context <same> --iters 30 --rows`。
三个配置按 A-B-C / C-B-A / A-B-C 交错 3 轮，全部在升频态（`before` 1905/1920 MHz），取中位：

| ctx | scores T=1..4 | topk T=1..4 | attn T=1..4 | layer T=1..4 | attn 每行 | layer 每行 |
|---|---:|---:|---:|---:|---:|---:|
| 32768 | 6.1 / 7.9 / 12.8 / 13.7 | 21.0 / 21.1 / 22.1 / 22.9 | 28.9 / 35.6 / 59.7 / 76.5 | 78.3 / 82.3 / 112.2 / 124.2 | **+15.9** | **+15.3** |
| 131072 | 35.9 / 37.8 / 43.1 / 44.9 | 28.3 / 28.6 / 29.6 / 30.8 | 29.6 / 36.6 / 62.9 / 77.4 | 109.3 / 115.5 / 149.3 / 161.2 | **+15.9** | **+17.3** |
| 262144 | 68.0 / 70.9 / 78.8 / 81.3 | 28.1 / 28.6 / 31.9 / 33.7 | 29.1 / 36.9 / 63.0 / 79.1 | 141.7 / 149.6 / 188.7 / 202.5 | **+16.7** | **+20.3** |

单次调用（T=1）的分量（单独计时，不是层合计的加项）：32768 → scores 6.1 + topk 21.0 + attn 28.9 + append 3.4；
131072 → 35.9 + 28.3 + 29.6 + 2.6；262144 → 68.0 + 28.1 + 29.1 + 2.6。层合计是合并调用的实测值
（78.3 / 108.8 / 141.7），比分项之和（59.4 / 96.4 / 127.8）多 12–19 µs，差在每次调用各自的 launch/同步开销。

**结论：能测，"更长上下文 decode 更贵"成立，但贵在 `qsa_block_scores` 而不是"对选择集做注意力"。**
从 32K→256K：`scores` 的 T=1 成本 6.1 → 35.9 → 68.0 µs（≈线性于块数：8194 / 32770 / 65538 块），
占 T=1 层时间的 8% → 33% → 48%；而 attn 每次调用的 T=1 成本 28.9 → 29.6 → 29.1 µs **基本不变**
（选择集宽度固定 2051，4 行并集只从 1.35× 涨到 1.46×），每行增量 15.9 → 15.9 → 16.7 µs（+5%）。
层每行增量 15.3 → 17.3 → 20.3 µs（+33%），涨的全是 `scores` 的固定项。这一条对 §P4 "每行大头是 attn over
selection" 的表述是一个**限定**：在 128K 口径下成立（92%），到 256K 时 T=1 的最大单项已经变成 `scores`。

---

## 2. 与文档数字对照表

| # | 文档值（`docs/VLLM_PORT_PLAN.md`） | 本次值（2080 Ti / build75，3–5 轮中位） | 差异 | 判断 |
|---|---|---|---|---|
| P4 | attn 29.5 / 36.6 / 62.9 / 77.3（预热，T=1..4） | **29.6 / 36.6 / 62.9 / 77.4** | +0.3 / 0 / 0 / +0.1 % | **复现** |
| P4 | 层合计 109.2 / 115.2 / 148.9 / 160.7 | **109.3 / 115.5 / 149.3 / 161.2** | +0.1 / +0.3 / +0.3 / +0.3 % | **复现** |
| P4 | 每行增量 attn 15.9 / 层 17.2 µs | **15.9 / 17.3** | ≤1% | **复现** |
| P4 | attn 占层每行增量 ~90% | **92%** | — | 复现 |
| P4 | 冷启动 scores 37.2/39.9/49.6/51.0 | 37.2 / 39.9 / 44.8 / 45.0 | T=3/4 −10 / −12% | **部分复现**（T=1/2 中，T=3/4 不符） |
| P4 | 冷启动 topk 39.2/40.0/40.4/40.5 | 39.2 / 40.0 / 32.8 / 30.9 | T=3/4 −19 / −24% | **部分复现** |
| P4 | 冷启动 attn 41.2/49.5/81.4/95.4 | 41.2 / 49.4 / 68.3 / 77.6 | T=3 −16%, T=4 −19% | **部分复现** |
| P4 | 冷启动比预热慢 +39.7/+35.2/+29.4/+23.4% | **+39 / +35 / +9 / +0.3%** | T=3/4 差 20 个百分点 | **不符**（T=3/4 的冷惩罚已消失） |
| P4 | QSAROWS T≥3 反向（−1.4/−2.3/+0.7/+7.4） | −0.3 / −0.4 / +6.2 / +10.9（单轮，R7） | 方向一致、幅度更大 | 方向复现 |
| P3c | fused vs int8 rel RMS 1.222e-4 / worst 3.635e-3 | **1.222e-04 / 3.635e-03** | 0 | **复现（逐位）** |
| P3c | fused vs MMQ rel RMS 1.213e-4 / worst 3.635e-3 | **1.213e-04 / 3.635e-03** | 0 | **复现（逐位）** |
| P3c | MMQ vs FP32 1.43e-2 | **1.428e-02 / 2.678e-02** | 0 | **复现** |
| P3c | WW=2 46,592 B / WW=4 60,672 B smem | 一致（stderr banner） | 0 | **复现** |
| P3c | 20 tok：MMQ 0.400 / fused 0.344（1.16×） | **0.387 / 0.332（1.17×）** | −3 / −4% | **复现** |
| P3c | 256 tok：MMQ 1.111 / fused 0.986（1.13×） | **1.076 / 0.942（1.14×）** | −3 / −4% | **复现** |
| P3c | 1002 tok：MMQ 3.269 / fused 3.228（1.01×） | **3.145 / 3.113（1.01×）** | −4 / −4% | **复现** |
| P3c | "不设 `STRATA_PF_FUSED` 时仍是 MMQ"（默认路径） | 二进制自设 env，测不出差异 | — | **无法测** |
| P3b | iq3_xxs gate/up R=160 = **39.0** TOPS | **31.9** | **−18%** | **不符** |
| P3b | iq3_xxs down R=160 = 38.3 TOPS | **32.4** | −15% | **不符** |
| P3b | iq4_nl down R=160 = 31.1 TOPS | **26.6** | −14% | **不符** |
| P3b | R=626：45.8 / 37.4 / 30.2 TOPS | **36.5 / 32.2 / 26.9** | −20 / −14 / −11% | **不符** |
| P3b | R=12：9.3 / 9.8 / 8.2 TOPS | 8.0 / 8.5 / 7.5（1905 MHz 时 10.4） | −9…−14%（时钟敏感） | **部分复现** |
| P3c | "当日对照 mmq_expert_bench：gate/up R=160 = 31.9、iq4_nl down 25.9" | **31.9 / 26.6** | 0 / +2.7% | **复现** |
| verify | 3/3 | **3/3，6.63 s** | — | **复现** |

---

## 3. 无法测 / 受限项

1. **"不设 `STRATA_PF_FUSED` → skip（走 MMQ）"测不出来**：`tests/cuda/prefill_fused_iq_test.cpp:649-656`
   在 `main()` 入口就 `setenv(..., "1", 1)`，外部 unset 无效，实测两种跑法输出一致（§1.2c）。
   这需要在测试里加一个不 pre-set 的分支，或从引擎启动日志/`native_supported` 探针验证；代码读解是
   `env<0 → false`，属**未实测的静态分析**。
2. **端到端（引擎/token 吞吐）全部没测**：没有权重、不能重启 `strata.service`、不能碰
   `/opt/ai/src/strata-0.2.2`。§P3c 的"prompt +X%"、§P3b 的"45K prompt 的 gemm 3005 ms"、§P1 的
   `220.84 vs 116.04 tok/s` 都只能在部署窗口里测。
3. **§P3b 原始表的时钟/协议不可考**：构建树没有 `.git`，也没有当日日志；只能报"今天这个二进制、
   这个 bench、5 次中位"的值（31.9/32.4/26.6）。39.0 的来源无法从现场还原，只能报告不一致。
4. **一次误跑（已披露，未用于任何对照数字）**：我用 `--help` 探 `prefill_fused_iq_test` 的用法，
   该二进制不认 `--help`，直接按默认 `R64_E=64` 跑全流程，且**没设 `CUDA_VISIBLE_DEVICES`**，于是落到
   GPU0（788 MiB free）并在 `cudaMalloc` 处 OOM（消息即"parity failed: cudaMalloc: out of memory"），
   随后它继续跑完参考段。服务未受影响（PID 334191 未变、两卡占用不变、ELAPSED 连续）。这次输出
   （E=64 的 1.929e-4 / 2.259e-4 等）**没有被本文任何数字引用**；它正好印证了文档"64 专家组约 1 GB、
   小卡放不下"的警告。
5. **262144 只覆盖 bench 口径**：identity page table、int8 池、单个 QSA 层；真实生产 229K 的
   QSA 步成本（含 page table 跳转、多 slot 并集、KV 布局）没测。
6. **`nvidia-smi` 采样粒度**：显存峰值 0.3 s 轮询，短于 0.3 s 的尖峰可能漏记；上表 Δ 是下界。

---

## 4. 建议

**应当改文档的数字**

1. **§P3b 的表**（R=160 的 39.0/38.3/31.1、R=626 的 45.8/37.4/30.2、R=12 的 9.3/9.8/8.2）与今日可复现值差
   11–20%，且与同一文档 §P3c 的 31.9 自相矛盾。建议改成今天这组（31.9 / 32.4 / 26.6 @ R=160；
   36.5 / 32.2 / 26.9 @ R=626；8.0 / 8.5 / 7.5 @ R=12，或注明 R=12 要 1905 MHz 才到 10.4），
   或明确标出原始那组的时钟/构建/`STRATA_MMQ_TAB` 条件。连带把"算力 8.5%"改成 **7.0%**
   （31.9 / 456 = 7.0%），否则"两头都不到 15%"的论证虽然仍成立，但数字对不上。
2. **§P4 的 (a) 冷启动分量表**：T=3/T=4 的 49.6/51.0 与 40.4/40.5、attn 81.4/95.4 不可复现
   （本次 44.8/45.0、32.8/30.9、68.3/77.6）。既然该表只用于"定位开销在哪"，建议把 T=3/T=4 换成
   可复现的中位，或明确写"单次冷跑，T=3/4 仅供定性"。
3. **§P4 的"冷启动慢 23–40%"**：本次冷/热差是 **+39/+35/+9/+0.3%**（T=1..4），T=4 已经没有冷惩罚。
   建议改成"T=1/2 约 +35–40%，T=3 约 +10%，T=4 在噪声内"，否则读者会按 T=4 也慢 23% 去估账。
4. **§P3c 的命令要带 `S20_E`**：照文档字面跑 `--only="IQ3_XXS / IQ4_NL"` 会在 timing 段按 512 专家组 OOM
   （本机 868 MiB free）。建议把 §P3c 表的口径（`S20_E=16 --chunks=20,256,1002`）写进命令旁边。
5. **§P3c 的"不设 env → MMQ"**：措辞上补一句"该测试二进制自设 `STRATA_PF_FUSED=1`，默认路径需另测"，
   否则读者会以为用这条命令验证过默认路径。

**应当保留/加强的**

6. §P4 预热组、§P3c parity 与 timing、verify 3/3 都可以原样保留 —— 本次逐项复现。
7. §P4 的时钟态警告（"必须预热 + 交错 A/B"）值得再补一句可执行的判据：**启动时 SM ≥1905 MHz 才算预热态**；
   本文实测"先跑 5 次预热"并不够（下一次进程仍可能从 300 MHz 启动，见 R1A/R4A/R6A），
   掉回 300 MHz 只需 ~3 s 空闲。

**需要部署窗口的**

8. fused 的端到端收益（真实 IQ3_XXS pack 的 prompt 时间）、§P3b 的生产 45K prompt gemm 实测、
   §P1 的 `220.84 vs 116.04 tok/s`、以及**真实 229K 上下文下每步的 QSA 成本**。
   本次新增的 bench 事实（`scores` 68 µs/次、层 141.7 µs @ T=1 @ 256K）说明：按 §P4 的
   "15.9–17.2 µs/行 × 层数"去估 229K 的 decode 账会**低估**（少算 `scores` 的上下文相关固定项，
   在 256K 时它单次就占 48%）。
