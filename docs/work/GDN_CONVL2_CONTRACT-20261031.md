# GDN-CONVL2 contract

日期：2026-10-31

状态：预注册；先做源码/路径、启动和 greedy 门，再跑 A1/B/A2。

## 问题

普通 94K 热续轮的 prompt 仍约 `1.2–1.4 s`。当前旧 engine 的 GDN prompt 路径先写
卷积结果 `h`，再单独运行 Q/K L2 norm。已有 opt-in
`STRATA_GDN_CONVL2=2` 让 `gdn_conv_l2_kernel` 在一个 block 中完成同一 conv 与 Q/K
norm，避免一次 kernel launch 和 `h` 的中间重读；V 通道和 recurrence 保持原路径。

## 源码与触发条件

源码 `src/prefill/kernels.cu` 的 `gdn_conv()`：

- `T <= 64` 或 `STRATA_GDN_CONV_SERIAL` 存在时走旧串行路径；
- 否则 `T > 64` 且 `STRATA_GDN_CONVL2` 非零时进入 `gdn_conv_l2_kernel`；
- 该 kernel 按注释逐元素复用相同 conv 表达式和相同 L2 reduction 顺序，只改变融合边界。

当前生产使用 `--prefill auto` 的 8192-token chunk，实际远大于 64；进程不含
`STRATA_GDN_CONV_SERIAL`。因此候选 env 能进入目标分支。启动与请求日志没有专门 marker，
路径证据由实际进程 env、引擎中两个目标 kernel 的存在、`T>64` 的 prompt chunk 和
default/fused 运行的 phase/对时差共同组成；不把“env 存在”单独写成执行证明。

## 源码风险

这是数值边界改动，必须先用同一引擎的 A/B 固定 greedy 证明输出与 finish 不变。若输出
不同，候选直接否决，不进入性能测量。当前注释声称同 bit，但该声明属于静态阅读，采纳前
必须由本机实测验证。

## Arms

- A1：当前生产 engine/config，进程 `162903/162905/162910`；
- B：深拷贝 A1 config，只在 `env` 增加 `"STRATA_GDN_CONVL2": "2"`；
- A2：恢复与 A1 字节相同的 config，检查 A 漂移与运行路径。

模型、pack、双卡 layer split 24、262144 context、六 slots、两 groups、MTP、采样、
cache、`--ple-inflight 1024` 和全部生产 env 不变。B 的 engine SHA 仍为
`105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3`。

## 门

B 启动后必须：

- 从实际进程路径核对 engine/config hash，进程 env 含唯一候选 delta；
- health loaded、262144、6 slots / 2 groups，systemd inactive；
- 三组固定 greedy 各两轮，候选内逐字重复且等于 A1；finish 全 `stop`；
- 64-token smoke 和 596/94K benchmark 零 HTTP/engine 错误、零外来请求；
- A1/A2 漂移 `<=3%`。

性能使用同参 596 fresh 与 94K 热续，每臂三波：

- 至少一个代表尺寸 hot-long wall 中位改善 `>=1%`，绝对节省 `>=0.05 s`；
- 两个尺寸 wall 不得反向；
- prompt 与 wall 同时报告，不能把局部 phase 改善写成用户收益；
- 不跑 189K 或 4/6/8；94K 无 wall 方向收益立即停止；
- 失败可删除一个 env 精确回滚。

## 重启预算

B 和 A2 各约 2–3 分钟加载，总成本约 4–6 分钟。只有启动、路径和 greedy 门全过才支付
性能测量；若 94K 无 wall 收益，不做邻近阈值、chunk 或 GDN 参数扫描。
