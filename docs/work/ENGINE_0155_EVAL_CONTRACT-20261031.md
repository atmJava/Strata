# ENGINE-0.1.55-EVAL contract

日期：2026-10-31

状态：已否决（2026-10-31）。`--prompt-cache-long` 不在官方 CLI；移除该 128K-only
本地扩展后，官方引擎成功启动并通过 parity，但 596/94K hot-long wall 分别慢
`5.853%/4.259%`。结果见
[`ENGINE_0155_EVAL_RESULT-20261031.md`](ENGINE_0155_EVAL_RESULT-20261031.md)。

## 问题

项目内现有生产引擎为 `0.1.51` 派生构建
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`。官方
`0.1.55` Linux CUDA 13 包包含新的 GDN、批处理和运行时 gating 代码，可能直接缩短
真实 vibe coding 的普通 prompt/decode 等待。先验证版本本身，不把多个新参数一起
混入。

## 包与指纹

- release：`v0.2.9`
- asset：`strata-engine-0.1.55-linux-x86_64-cuda-cuda13.tar.gz`
- archive SHA-256：
  `ce86b23b26a4521a1871113057534b8a34c01df0ac7b916adccf9b1c8c068f92`
- engine SHA-256：
  `e4406b474c292c8b52621c803f523ae18dc42c905deda60d5f055740c7ef8925`
- `libstrata_kq.so` SHA-256：
  `7ed527222a8c3f3cd1d232007cab06897425310b820d3c0c16f31aba3cbd7665`
- build：`99866a47a2b8+cuda13.0`，source `f-029-rel`，CUDA `13.0`，含 `sm_75`
- 隔离路径：
  `/opt/ai/logs/engine-0155-eval-20261031T1320Z/engine/`

本地与远端二进制和动态库 SHA-256 已逐项一致；远端 `ldd` 只使用服务器已有
CUDA 13 库，`./strata --version` 返回 `0.1.55`。

## Arms

- A1：当前旧 engine + config
  `6ece1a13f4ac2d875bdafd903c7aba23fdce6be5ccc387e17b987a130a486811`；
- B0：同一 config 的深拷贝，只把引擎可执行文件替换为隔离的官方 `0.1.55`，不增加
  实验 env；
- A2：恢复当前旧 engine，用同一 config 重跑，量跨 boot 漂移。

模型、pack、双卡 layer split 24、262144 context、六 slots、两 groups、MTP、采样、
prompt/conversation cache、`--ple-inflight 1024` 和生产 env 全部保持不变。运行进程
必须不含 `STRATA_SPLIT_MTP_BATCH`、`STRATA_DECODE_TIMING` 或
`STRATA_VERIFY_PROFILE`。

只有 B0 的版本级评价结束后，才允许单独评估新版本的 Turing 候选
`STRATA_GDN_TC=1` / `STRATA_GDN_CONVL2=2`。sm75 runtime gate 若拒绝某条路径，结果按
“官方版本默认”评价，不把未执行 env 写成收益。

## 启动与正确性门

B0 必须先满足：

- 官方 `0.1.55` 和两个 hash 从实际进程路径核对；
- health loaded、262144、6 slots / 2 groups、两张 2080 Ti 正常；
- prompt cache、MTP、native、layer split 和 GDN/QSA 启动标记正常，无缺失库或回退到
  非目标 backend；
- 三组固定 greedy 探针各两轮，候选内逐字重复且等于 A；
- 一次 64-token smoke 的 finish、tool 路径、零 HTTP/engine 错误通过。

任一门失败就先修复原因或否决，不测吞吐。

## 性能测量

使用现有 `tools/server/bench-hot-turn-fixed-cost.py`，A1/B0/A2 各跑三轮：

1. 596 fresh-token 普通热续轮；
2. 94K 热续轮。

记录 prompt/cached/uncached、TTFT、slot wait、wall、output、finish、reply hash、
HTTP/engine 错误和外来请求。1 路为主判决；2 路报告第二请求 TTFT、单请求 wall 和完成
跨度。先不跑 189K、4/6/8 全矩阵。

## 决策门

版本级候选只在全部满足时采纳：

- 至少一个普通热续尺寸的 wall 中位改善 `>=1%`，且绝对节省 `>=0.05 s`；
- 两个热续尺寸的 wall 方向不能相反，prompt 与 wall 必须一起报告；
- A1/A2 同版本漂移 `<=3%`；
- 2 路不退 `>1%`，或若 2 路本身有明显问题则明确不采纳；
- 输出、finish、cache、health、slots、资源、零外来请求全过；
- 失败可只改 config 的可执行文件路径恢复旧 engine，不需要重建源码。

94K 无方向性 wall 收益则停止，不支付 189K 和全矩阵成本。只有版本级过门才跑 4/6/8
安全回归。

## 重启预算

当前 A 在产。B0 与 A2 各需约 2–3 分钟加载，总计约 5–6 分钟；只有启动和 parity 门
通过才支付 B 的性能测量。若 94K 无 wall 收益，不追加新版本 env 组合或更大矩阵。
