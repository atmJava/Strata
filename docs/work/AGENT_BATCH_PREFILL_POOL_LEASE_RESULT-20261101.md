# AGENT-BATCH-PREFILL-POOL-LEASE result

日期：2026-11-01

状态：**采纳为默认关闭、生产 opt-in**。生产和质量对照使用同一份按生产 CMake 选项构建的
engine；B0/B1/B0r 热轮门、7 个质量 fixture、6/8 安全回归和部署后 smoke 均通过。

## 结论

候选修复了 batch 模式下 CPU expert share 的 pool 所有权问题：

- batch/pipeline decode 在整个窗口登记 active；
- prefill 每层非阻塞取得一次 lease，有 decode 或无空闲 lease 时整层回退 GPU；
- lease 只覆盖一层，不持有 `run*` 派发锁等待 async CPU future；
- guard 开启时 `run*` 串行，默认关闭时原派发路径不加锁；
- 生产只启用三个非调试开关：

```text
STRATA_PREFILL_CPU_SHARE_BATCH_GUARD=1
STRATA_PREFILL_CPU_SHARE=auto
STRATA_PREFILL_CPU_SHARE_MAX=1024
```

`STRATA_DBG_CPU_GATE` 与 `STRATA_DBG_POOL_GUARD` 只用于实验，不进入生产。

## 生产选项构建

此前隔离构建没有显式记录 A1 的 MMQ/Q6K 选项。新目录
`/opt/ai/src/strata-batch-prefill-guard-20261101/build-guard-prod` 使用：

```text
CMAKE_BUILD_TYPE=Release
CMAKE_CUDA_ARCHITECTURES=75
STRATA_ENABLE_CUDA=ON
STRATA_MMQ_KQUANTS=ON
STRATA_MMQ_TQ_OVERLAY=ON
STRATA_Q6K_EXPERTS=ON
STRATA_PORTABLE=OFF
STRATA_NATIVE_EXPERTS=ON
STRATA_GGML_DIR=/opt/ai/src/llama.cpp-3cf03257
STRATA_BUILD_TESTS=ON
GGML_NATIVE=ON
BUILD_SHARED_LIBS=OFF
```

`cmake --build build-guard-prod --target strata -j16` 成功。用于 B0/B1/B0r 与质量矩阵的
engine SHA-256：

```text
e3d25b382bd3f32c8b5d1789fdb768f417028f742238e9549438e1679cecd5a8
```

性能矩阵后又修正了一个只在异步 future 已启动且早退时触发的 RAII 顺序：把
`PrefillLease` 声明在 `cpu_fut` 之前，使反向析构先等待 future，再释放单层 lease。
正常成功路径行为不变；最终部署 engine SHA-256：

```text
e60a334c0ca5c1974d79385256aaf90519bcb646e2626f7e66de818d104b0ab7
```

因此表中性能和 7/7 质量归因对应 `e3d25b38...`；`e60a334c...` 只重跑生产 smoke，
没有把异常早退修复写成新的性能收益。

完整 `STRATA_BUILD_TESTS=ON` 构建仍在既有 CUDA 测试目标失败：

1. `tests/cuda/prefill_fused_iq_test.cpp` 使用未声明的 `n_order`、`n_groups`；
2. MMQ test/bench 链接缺 `cublasGetStatusString`。

主 engine 已单独构建成功；不能用“全量构建通过”描述本结果。失败目标与候选源码无关，
本项没有删除测试或修改候选来绕过。

## 同二进制性能

B0、B1、B0r 使用同一 engine、同一 config 几何、1/2 路各 3 波。比较器要求候选至少赢
三分之二波，hot wall 至少一级达到 `>=1%` 且 `>=0.015 s`，并检查冷请求和控制漂移。

| 并发 / 热轮 | B0 wall | B1 wall | 改善 | 绝对节省 |
|---|---:|---:|---:|---:|
| 1 / hot-short | `0.601550 s` | `0.533364 s` | `11.825%` | `68.186 ms` |
| 1 / hot-long | `1.525526 s` | `1.363801 s` | `10.991%` | `161.725 ms` |
| 2 / hot-short | `1.710871 s` | `1.581258 s` | `7.896%` | `129.613 ms` |
| 2 / hot-long | `3.488948 s` | `3.221261 s` | `8.056%` | `267.686 ms` |

四个比较均 `3/3` 波候选胜；B0r 相对 B0 的最大控制漂移为 `1.528%`。1/2 路冷 wall
变化分别为 `+0.047%/-0.514%`。比较器判决 `pass`，无 problems。

生产 engine 的 B1 日志真实出现：

| 项目 | 数量 |
|---|---:|
| 精确 CPU gate | `3,698` |
| `shared,` | `2,814` |
| `not shared,` | `884` |
| prefill lease refusal | `0` |
| `run*` dispatch wait | `0` |

这说明 CPU share 在 batch=6 生产路径真实工作，但不能宣称 decode-active 拒绝分支已经在线
命中；专用重叠探针仍未触发 lease-refusal/dispatch-wait。

## 质量与测试方法修正

第一个 B1 生产选项全套结果是 `6/7`。失败 fixture 的代码、测试、受保护测试和 required
tool 全部正确；模型最终消息包含摘要和 `RESULT=PASS`，而旧评分只接受内容完全相等。
B0 同 fixture 的输出碰巧只有该 token，因此旧单次比较会把它误判成候选回归。

`bench-agent-quality.py` 已升级为 schema 5，所有 fixture 共用一个明确规则：

> 最终 assistant 消息必须只包含精确文本 `RESULT=PASS`，不得有摘要、解释、空行或额外标点。

升级后 B0/B1 均 `7/7`。共同身份为：

```text
fixture_suite_sha256=1295e2af17d1b7be3daa7111173cd7e1931b1c8058826ca17f330ad5c3a70bfd
model_catalog_sha256=b2810aade8d2d5698ca21ec2b9a17806fa6009cd242609691893d286055a4d1f
provider_url=http://127.0.0.1:18083/v1
reasoning_effort=high
```

比较器判决 `pass`：零身份不一致、零候选回归、零共享失败。报告 SHA-256：

- B0：`22313a7dbbf3243fd067101f55a3b82564be1c42e9c6e9558d10b40864d4c5cb`
- B1：`610afd5f9e036adc6f0c5431b36db93e7bbabf14101360adadbe0841dafd0000`

## 6/8 安全回归

性能归因对应的 B1 engine（`e3d25b38...`）在 6/8 路各 3 波，共 42 请求，零
HTTP/执行错误；每波只有一个输出哈希、512 token、finish 全为 `length`。

| 并发 | 完成跨度中位 | completion 聚合 |
|---|---:|---:|
| 6 | `17.330 s` | `177.263 tok/s` |
| 8 | `27.798 s` | `147.347 tok/s` |

结束后 health loaded、6 slots / 2 groups、新增 Xid 为 `0`。

最终 RAII 顺序修复后的 engine（`e60a334c...`）又用同一探针补跑 6/8 各 3 波，共 42
请求，零 HTTP/执行错误；所有请求均为 512 token、finish `length`，结束后 health loaded、
6 slots / 2 groups、新增 Xid `0`。这次最终 binary 的补跑中，6 路 wave 1 和 8 路 wave 2
各有一次首个请求输出另一个哈希；其余请求均为同一哈希。该探针只记录哈希，不以并发输出
逐字一致作为安全门，因此不能把“每波一个哈希”写成最终 binary 的结论，也不能拿哈希相同
代替质量门。候选质量由同 identity 的 B0/B1 七个 coding fixture 验证；两组均 `7/7`。

| 最终 binary 并发 | 完成跨度中位 | completion 聚合 |
|---|---:|---:|
| 6 | `17.108 s` | `179.567 tok/s` |
| 8 | `27.578 s` | `148.524 tok/s` |

## 生产部署

server 继续使用 A1 源码和 cwd，只替换 engine/config：

```text
server cwd=/opt/ai/src/strata-two-stream-serial-20261031
server SHA-256=bd3e428932c0b9bb645b2ae9f37924b2e440b590333a0322cdd00cd28dd4ed82
engine=/opt/ai/src/strata-batch-prefill-guard-20261101/build-guard-prod/strata
engine SHA-256=e60a334c0ca5c1974d79385256aaf90519bcb646e2626f7e66de818d104b0ab7
config SHA-256=2c4c7ac6c346c7b64d162502080fc3147b5da232de156a20a9d1db81b521985e
server/engine PID=292517/292525
```

server 实际 env 保留 A1 行为：

```text
STRATA_ADMIT_SHORT_FIRST=2048
STRATA_DEFER_TITLE_REQUESTS=1
STRATA_MP_STAGED_READ_MIN_TOKENS=2048
STRATA_PIN_DSH_TOOL_PREFIX=1
```

engine 实际 env 只有预期候选开关与既有 `STRATA_BATCH_SERIAL_TWO=1`，没有 debug 开关。
health loaded、262144、6 slots / 2 groups，部署窗口新增 Xid `0`。部署后 1 路 3 波 smoke
9/9 请求成功、零外来请求；最终 binary 的 cold/hot-short/hot-long wall 中位
`11.381591/0.538837/1.353223 s`。

## 回滚

停止当前 server/engine 后，从 A1 server cwd 用原 A1 config 和 build75 engine 启动，并
重新设置上面四个 server env。原始 A1 身份为：

```text
server/vision/engine=254299/254303/254308
engine SHA-256=105a4171af4d62ff0f3824a4ce2a35b7f0c3085b1290824ade1b288fb3799dc3
config SHA-256=6ece1a13f4ac2d875bdafd903c7aba23fdce6be5ccc387e17b987a130a486811
```

## 限制与证据

- 本轮没有用生产二进制重跑 4 路性能矩阵；4 路证据来自同源码隔离构建
  `1.341%/8.474%/1.453%`，不能写成生产二进制的独立测量。
- lease-refusal 与 `run*` 派发等待没有真实命中，因此时延上界仍由机制和单测支持，
  不能宣称已观测到最大等待。
- `pool_test` / `pool_stress` 在本机因缺 AVX-512 为 SKIPPED；`pool_lease_test` 和 async
  非死锁测试通过。
- 本地相关测试 `38 passed`；`serve.test_server` 为 `360 tests OK`；预检 PASS。

远端证据目录：
`/opt/ai/logs/agent-batch-prefill-guard-prod-20261101T2308Z/`

关键文件 SHA-256：

- `B0-prod-hot.json`：`c25b239d358e054357f14b8d1326203dfc9daf4a446035bd1bc8b59fbf410b99`
- `B1-prod-hot.json`：`7d597c3bf4233aee5ea405a89cc92fdd69a6ce09dc8bc65882cf83470b52ed65`
- `B0r-prod-hot.json`：`ce198456a555535890cc2f2c3a46455e35fc910787b5f738fba8228b552b3cc6`
- `B0-B1-B0r-prod-verdict.json`：`7fe076626f10271592f0d7ed173619081547b43a82fd4de5c278d860dc4f9881`
- `B1-prod-6-8.json`：`849916dd27c7817c91a02f71cb6445f48109add223582ba1a685ba60454ab950`
- `prod-r3-smoke-hot.json`：`c8db57dffc9b63e5d69e6dd0cd03f4fca99fbb1160d86a81069ae80f2b676b97`
- `prod-r3-6-8.json`：`eaff8bcd5d027edba65d1a3bd593a453a23619fb17fb0b6734ace493f9dc2239`

## 下一项

当前普通 cold 请求 wall 仍约 `11.37 s`，远大于 hot continuation 的
`0.52/1.35 s`；真实 trace 又显示模型 step 是总成本主体。下一项不继续扫 CPU share
邻域，而是用最新真实 DSH 会话和当前生产实例重排 cold/new-session、普通 hot step、
长输出 decode 的整任务成本，选当前最大且可用源码机制改善的一项。
