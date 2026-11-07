# sm_75 上游移植执行记录（2026-11-07）

对照文档：[SM75_UPSTREAM_PORT_PLAN-20261107.md](SM75_UPSTREAM_PORT_PLAN-20261107.md)；基线：[SM75_TAG_BASELINE-20261107.md](SM75_TAG_BASELINE-20261107.md)。分支 `perf/sm75-upstream-port`。

## 结论先行

| 候选 | 处置 | 依据 |
|---|---|---|
| **C1** chunk-major 默认 | **不改默认，留作测量项** | 零代码；上游在 2×A6000 上 32K 快 37–55%，但本机未测。按仓库规则，默认值只在测过的地方改。 |
| **C2** FP16 dense 权重缓存 | **已移植，opt-in** | 代码忠实；`gemm_w16_parity` 在 2×2080Ti 上 100 组全 bitwise 一致。但**在本机稳态是严格 no-op**（见下）。 |
| **C3** PCIe miss share | **暂缓** | 生产显式 `--pcie-frac 0.31`，自动规则被绕过；上游提交没有吞吐数字；移植了也是死代码。 |
| **C4** DFlash2 batch slots | **不适用** | 生产用 MTP（`--spec 8 --mtp`），没有 `--draft dflash2`。 |
| **C5** MMQ group scratch | **不适用** | 本树 `gemm.cu` 是 CUDA-only 裁剪版，`native_mmq` 只有声明、没有实现，prompt 路径不走 MMQ。 |

## C2：为什么默认保持关闭

生产稳态日志：`1011 MiB of VRAM free`（CUDA0）/ `1075 MiB`（CUDA1）。缓存的分配条件是 `free > weight_bytes + reserve`，默认 `auto` 的 reserve 是 3 GiB；`STRATA_PF_W16_CACHE_MIB=N` 也要 2 GiB。也就是说：

- 默认（`auto` 规则）需要 ≈3,132 MiB，实际 1,011 MiB，**差 2.07 GiB**；
- 显式 `=N` 需要 ≈2,108 MiB，**差约 1.1 GiB**；
- 因此在本机当前配置下，缓存**即使强制也分配不出来**，默认开启只会改变数值而不产生任何加速。

要让它真正生效，必须先腾出 ≥3 GiB 显存（例如缩小 expert cache 或上下文），再用同一二进制作 `STRATA_PF_W16_CACHE_MIB=0` vs `=auto` 的 A/B。这是配置/测量决策，不是代码决策。

## 独立审查（两个子代理，只读）

1. **忠实度审查**：逐行对照 `d8c94b45/e3189102/88a79a7e/62853e78`。结论：机制移植忠实；发现默认翻转的 parity 证据不成立（测试的 Y 缓冲被 248320 行输出头撑到 3.8 GiB/块，缓存根本不会命中却打印 OK）、缺 `N*K <= scratch` 门、负值 env 反复解析、失败后永久关闭且无日志。已全部修正（默认翻转同时撤掉）。
2. **sm_75 风险审查**：按生产显存算出默认严格 no-op（与上一节数字一致）；确认 dense 形状都 ≤32M scratch，缓存与未缓存走同一次 full-N GEMM，数值主张成立；多卡每 stage 独立 `Gemm`，key 不会跨卡；建议先补观测再谈默认。

## 已验证

- 构建：`/opt/ai/src/strata-c2/build-c2`，nvcc 13.0，sm_75，`strata` 与 `gemm_w16_parity` 编译通过。
- `gemm_w16_parity`（2×RTX 2080 Ti）：**100 个 production (shape, type, T) 组合，0 差异，0 未命中缓存，OK**；缓存 `auto` 对 `0`，引擎 32M scratch，逐位一致。
- 生产在 parity 期间短暂停止，之后已恢复（`systemctl start strata`）。

## 下一步（按收益排序）

1. **C1 A/B**：同一二进制（本分支构建，缓存默认关），`STRATA_PREFILL_LAYER_MAJOR=0` vs `=1`，跑基线四类形态（长上下文冷读、并发、压缩、DSH 多轮）+ suite 1。这是唯一不受显存余量限制、且上游有明确 prompt 收益的候选。
2. C1 若胜出：改默认（一行）+ KL/一致性检查，再考虑合并。
3. C2 若要做：先降 expert cache/上下文腾出 ≥3 GiB，再测缓存 on/off；本机预期收益 <1%（prompt 主要耗在 expert DMA）。
4. C3 只有在决定放弃显式 `--pcie-frac`、改用自动规则时才重新评估。

## 复现

```sh
# 构建
cmake -S . -B build-c2 -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.0/bin/nvcc -DCMAKE_CUDA_ARCHITECTURES=75 \
  -DSTRATA_GGML_DIR=/opt/ai/src/llama.cpp-3cf03257 -DSTRATA_BUILD_TESTS=ON
cmake --build build-c2 -j 24 --target strata gemm_w16_parity
# parity（需停生产腾显存）
./build-c2/gemm_w16_parity
```
