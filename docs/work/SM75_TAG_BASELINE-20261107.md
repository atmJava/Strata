# sm_75 tag v0.2.7 固定基线（2026-11-07）

**用途**：后续所有移植候选的对照基准。测量在 `ubuntu-server`（2 × RTX 2080 Ti，sm_75）上完成，生产服务在测量期间停止，测量后已恢复。本文只记录速度和缓存行为，**没有做正确性/质量检查**。

## 1. 构建与运行条件

| 项 | 值 |
|---|---|
| 源码 | tag `v0.2.7`（`git archive`，tgz sha256 `349c92c6…`） |
| 构建 | `-DCMAKE_CUDA_ARCHITECTURES=75 -DCMAKE_BUILD_TYPE=Release -DSTRATA_GGML_DIR=/opt/ai/src/llama.cpp-3cf03257` |
| 引擎 | `/opt/ai/src/strata-v0.2.7-tag/build-sm75/strata`，sha256 `4d7890984deb7a7f…` |
| GPU | 2 × RTX 2080 Ti 22 GB，驱动 595.71.05，SM 时钟 1350 MHz，功耗约 100–110 W，温度 52–64 °C |
| CPU / RAM | Xeon E5-2680 v4（14C/28T）/ 62.7 GiB |
| 运行配置 | 复制 `/opt/ai/etc/strata-production.json`，仅改 `exe`、`cwd`、`log`、`port`（18080）；其余 args/env 不变：`--layer-split 24`、`--parallel 4`、262144 上下文、INT8 KV、`--resident-experts`、conversation cache 12288 MiB |
| 取数 | HTTP API（OpenAI 兼容），`temperature 0`，thinking off |

## 2. 冷读（唯一填充，零缓存）

填充词每词唯一，确保没有前缀复用；实际 token 数由 `usage.prompt_tokens` 给出。

| 档位（目标） | 实际 prompt token | 墙钟 | prompt tok/s |
|---|---:|---:|---:|
| 32K | 31,576 / 34,003 | 14.27 / 15.16 s | 2,213 / 2,243 |
| 64K | 58,273 / 63,127 | 22.80 / 24.12 s | 2,556 / 2,617 |
| 128K | 145,660 / 145,660 | 50.87 / 51.81 s | 2,811 / 2,863 |
| 150K | 134,737 / 168,415 | 48.57 / 60.66 s | 2,774 / 2,776 |

每档 2 次。注意：填充词的 tokenizer 结果与目标不完全一致（尤其 150K 档两次相差约 25%），比较时应按实际 token 数归一。

## 3. 并发（decode，256 token/请求，3 波）

| 并发 | decode 聚合 tok/s（中位） | completion 聚合 tok/s | 墙钟（中位） |
|---:|---:|---:|---:|
| 1 路 | 106.7 | 103.6 | 2.47 s |
| 2 路 | 98.3 | 95.7 | 5.35 s |
| 4 路 | 146.1 | 142.2 | 7.18 s |

2 路低于 1 路，是这台机器已知的凹点（与 `docs/ROADMAP.md` 的 M14/M23 记录一致）；4 路才超过 1 路。原始逐请求记录见 `decode-concurrency.json`。

## 4. 压缩（约 20.8 万 token 历史）

`probe-long-compaction.py --history-words 145000`：

| 阶段 | input | cached | output | 墙钟 | finish / status |
|---|---:|---:|---:|---:|---|
| 历史轮 | 208,536 | 0 | 8 | 73.63 s | `max_output_tokens` / incomplete |
| 压缩轮 | 208,558 | 208,531（99.99%） | 256 | 5.64 s | `max_output_tokens` / incomplete |

**限制**：两轮的输出都被 `max_output_tokens` 截断，探针标为 incomplete。这里的数字只能用于比较缓存复用、首字和墙钟，不能比较答案质量。

## 5. DSH 形状多轮（45K 上下文）

`turn-ttft.py`，每档 3 轮，报告重读 token、首字、decode 中位：

| 每轮新增 | 重读 token | 首字 | decode tok/s |
|---|---:|---:|---:|
| 300 | 468 | 1.35 s | 71.5 |
| 1,200 | 1,835 | 2.21 s | 75.1 |
| 3,000 | 4,535 | 4.60 s | 35.7 |

## 6. 套件 1（`tools/strata_bench.py`）

| 项 | 中位（范围） |
|---|---|
| chat decode | 74.2 tok/s（53.6–101.2） |
| chat 首字 | 0.40 s |
| 4K prompt | 1,384.3 tok/s，首字 2.93 s |
| 16K prompt | 1,821.8 tok/s，首字 8.85 s |
| 32K prompt | 2,222.3 tok/s，首字 14.48 s |
| agent 缓存命中 | 70.61% |
| agent decode | 70.0 tok/s，首字 1.93 s |
| 4 客户端聚合 | 89.38 tok/s |

## 7. 限制与复现

- 测量期间生产服务停止；没有记录其它后台负载（除 GPU 状态）。
- 冷读的目标 token 数由填充词近似，实际值以表内为准。
- 压缩轮输出被截断，不能作为质量基线。
- 原始文件在 `bench/results/2026-11-07-sm75-tag-baseline/`（含 `run2.sh`、`cold_run.sh`、机器记录与各探针原始输出）。

复现（在服务器上，生产停止后）：

```sh
cd /opt/ai/src/strata-v0.2.7-tag
/opt/ai/venvs/test-027/bin/python -u strata.py serve --config strata-production-tag.json &
# 冷读
python3 /tmp/cold-prefill-18080.py cold-32k 32768 2
# 并发
python3 tools/server/probe-decode-concurrency.py --base-url http://127.0.0.1:18080 \
  --config strata-production-tag.json --levels 1,2,4 --waves 3 --max-tokens 256 --output out.json
# 压缩
python3 tools/server/probe-long-compaction.py --base-url http://127.0.0.1:18080 \
  --config strata-production-tag.json --history-words 145000 --output compact.json
# DSH 多轮
TTFT_LOG=/opt/ai/logs/strata-engine-tag.log TTFT_CFG=.../strata-production-tag.json \
  python3 /tmp/turn-ttft-18080.py run 300,1200,3000 3 45000 300
```
