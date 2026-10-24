# `patches/local-strata/` — 本机自己写过的改动，留档，未合并

这里放的是**这台机器上曾经写出来、但没有进上游也没有进本分支树**的代码改动。它们对应的工作树
（0.1.45、0.1.45-tp、0.2.0-beta、0.2.0-tp、上游 main 快照）已于 2026-10-24 从服务器删除，
只保留这些补丁和实验配置（见 [`MANIFEST-server-cleanup.md`](MANIFEST-server-cleanup.md)）。

## S1（并发读）—— **已在生产，已在本仓库分支**

不在这里：它在 `hw/s1-concurrent-read` 分支的 `11cc25bd`（`src/program/generate.cpp` +
`include/strata/program/batch_read.hpp`，+16/−1），并且已经在这个 fork 部署的 0.2.2 二进制里跑着
（`STRATA_PIPED_CONCURRENT=1`）。效果：读 84K 冷提示词时邻居流最长停顿 35.5 s → 0.8 s、
吞吐 0.3 → 21–22 tok/s；代价是读的人慢约 20%（台账 L-019/L-020）。

## TP（张量并行 GDN）—— **留档，暂不合并**

| 文件 | 是什么 |
|---|---|
| `tp-0.2.0-beta.patch` | 张量并行 GDN 移植到 0.2.0-beta 的版本（较新，87 KB） |
| `tp-0.1.45.patch` | 最早在 0.1.45 上做的版本（20 KB，只含头文件与新增文件） |

改动范围（0.2.0-beta 版）：11 个文件、约 1.1k 行新增——`src/core/verify.cpp`（+301）、
`src/program/generate.cpp`（+174）、`src/kernels/cuda/native_mmvq_split.cu`（+105）、
`src/core/tp_dense.cpp`（+90）、一个 286 行的 bench，其余是 `verify.hpp` / `tp_dense.hpp` /
`weights.hpp` / `weights.cpp` / `verify_kernels.cu` / `native_mmvq.hpp`。

实测（0.1.45 时代）：**solo verify window 上 +3% 解码**，每卡多占约 **150 MiB** 显存。

### 现在不合并的理由（台账 L-033）

1. **只作用在单流窗口**：生产是 `parallel 4` + `--batch-groups 2` 的批处理形态，批槽里按设计不跑 TP，
   所以它在**并发**场景（判据②，也是这台机器的主要用法）没有收益；单流是判据③，权重最低。
2. **+3% 落在噪声线内**：跨 boot 比单流 decode 的波动就有 ±2%，要证明 3% 需要 ≥3 臂 × 多轮 A-B-A。
3. **移植成本不小**：0.2.2 的 `verify.cpp` / `generate.cpp` 相对 0.2.0-beta 又改过（38 / 155 行），
   重做一遍约 11 个文件，且改数值必须过质量门（逐位或自评分）。
4. 显存余量只有 0.7–1.0 GiB，+150 MiB/卡可行但不宽裕。

### 真要做的话，最小路径

1. 以 `build-mmqtab` 的方式另开构建目录，把这 11 个文件的改动移植到 0.2.2（`git apply -3` 起手，逐段手工对）；
2. `--target strata` 构建，先跑 `parity_window.py` + `quality_eval.py`（TP 会改求和顺序，不是逐位相等）；
3. 开机后 A-B-A：单流 decode ≥3 次 ×3 轮，同 boot；4 路聚合各 3 次；
4. 只有单流 ≥5% 且质量门通过才留（否则按台账的习惯，<3% 不算结论）。
