# AGENT-BATCH-PREFILL-POOL-LEASE contract

日期：2026-11-01

状态：已通过并采纳为生产 opt-in。最终证据见
[`AGENT_BATCH_PREFILL_POOL_LEASE_RESULT-20261101.md`](AGENT_BATCH_PREFILL_POOL_LEASE_RESULT-20261101.md)。

## 问题

CPU expert share 在无 batch 的同源码隔离实例中，`hot-short/hot-long` wall 快
`2.987%/1.944%`，9/9 请求正确。但生产 `--batch 6` 被
`share_pool = o.batch <= 0 && !o.no_pool` 挡住。

不能直接删除该条件。batch prompt reader 在独立线程运行，batch decode 可以同时推进；
两者最终调用同一个 `ExpertPool`。pool 的 `jobs_`、`mode_`、`head_`、`done_`、split
缓冲和 worker scratch 都只描述一个正在执行的 batch，不能并发重入。现有
`g_cpu_pool_busy` 只允许一个 prefill stage 持有 pool，不保护 decode。

## 默认关闭候选

只有同时设置以下环境变量才进入 batch guard：

```text
STRATA_PREFILL_CPU_SHARE_BATCH_GUARD=1
STRATA_PREFILL_CPU_SHARE=auto
STRATA_PREFILL_CPU_SHARE_MAX=1024
```

未设置 batch guard，或没有显式开启 CPU share 时，行为与当前生产一致。候选只改变
`T < 1024` 的 staged prompt chunk。

## 所有权协议

1. decode 在整个 batch/pipeline window 内登记为 active；只有显式开启 batch guard 才登记，
   默认路径不增加计数或派发锁。
2. prefill 在每层选择 CPU experts 前尝试取得 lease；已有 prefill lease 或 decode active 时
   立即放弃本层 CPU share，整层走原 GPU 路径。
3. prefill 的 lease 只覆盖一层，CPU future 完成后立即释放，不跨层、不跨 chunk。
4. 若 prefill 已持有某层 lease，新 decode 可以等待这一层的 CPU call，但不得等待后续
   prefill 层；decode active 后，prefill 不能再次取得 lease。
5. 所有成功、错误、取消和异常路径都由 RAII 释放 lease；没有半持有状态。
6. batch guard 开启时，所有 `ExpertPool::run*` 在一个 pool-level 串行区内，防止遗漏
   调用者重入。lease 本身不持有该串行区；prefill future 会在另一线程调用 `run*`，
   若 lease 持锁等待 future 会死锁。默认关闭 batch guard 时 `run*` 保持原派发路径。

## 验收门

先做代码与无 GPU 单测：

- decode active 时 prefill lease 立即失败；
- decode 结束后 prefill 可取得 lease；
- 已持有的 prefill lease 使第二个 prefill lease 失败；
- lease 析构后恢复可用；
- 默认关闭 guard 时 decode 标记不阻塞 solo prefill；
- lease 持有期间，async `run()` 在 2 秒内完成且不因 pool 锁自锁；
- 现有 pool selftest/stress 无回归。

通过后构建隔离 engine，先证明真实路径：

- batch guard 与 CPU gate 日志命中；
- 设置 `STRATA_DBG_POOL_GUARD=1` 时，日志记录 lease 因 decode active 被拒绝，以及
  `run*` 派发实际等待毫秒数；该调试默认关闭；
- decode 与 prompt 并发时 prefill 在 decode active 后不再取得 pool；
- 新 decode 等待一层 prefill pool call 的最大值 `< 100 ms`；任何 `>=100 ms` 都停止，
  不等邻近参数；
- 1 路 hot-short/hot-long 同源码 B0/B1 wall 至少一个达到 `>=1%` 且 `>=0.015 s`，
  prompt 同向；
- 9/9 输出、finish、HTTP/engine 状态和零外来请求正确。

通过后再跑质量与 2/4/6/8：

- 1 路不退化，2 路无错误并报告第二请求 TTFT/完成跨度；
- 4 路不回退超过 1%；6/8 零错误、health loaded、资源安全；
- 质量必须使用同源码 B0/B1 配对，并固定 runner/profile、模型、reasoning、模型目录
  SHA-256、fixture suite SHA-256、prompt suffix SHA-256 和 provider URL；
- 候选从通过变失败即否决；B0/B1 共同失败记为 `blocked_shared_failures`，只证明不是
  候选回归，不能算质量通过；
- 输出、finish、工具/编辑/测试质量不降；
- 零新增 Xid，生产文件/config/systemd 状态不变。

未过任一门即移除候选源码或保持默认关闭，不扫描 share、worker、cap、chunk 或
lease 等待时间。
