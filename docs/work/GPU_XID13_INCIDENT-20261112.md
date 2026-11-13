# GPU Xid 13 复发事故（2026-11-12）

日期：2026-11-12

状态：复发事故已确认。`STRATA_HC_SPLIT=1` 在跑仍未阻止；根因仍**高度怀疑**
改装卡的显存时序/板级稳定性，不是已证实的芯片损坏。只读取证完成，未改 config、
未重启服务、未降频、未隔离。

## 触发

2026-11-12 16:19:42 CST（`2026-11-12T08:19:42Z`），DSH 通过
`http://192.168.9.100:8080/v1` 请求 Qwen3.8-Flash-Next。用户看到：

```text
server_error: the engine stopped unexpectedly (exit code 1); the next request restarts it
```

这条文案不是 DSH 生成的。它来自 `serve/server.py` 的 `EngineDied`
（`serve/server.py:7335` 拼成 503 `server_error`）。服务器日志给出的引擎原因：

```text
[strata] the engine stopped unexpectedly (exit code 1). The engine exited (code 1) after it
reported: verify batch: layer 19 never rang (an illegal instruction was encountered) -
please report it at github.com/Niko1221/Strata/issues with the log. The next request starts
the engine again. Its log: /opt/ai/logs/strata-swift-iq3s-engine.log
```

该行在 `/opt/ai/logs/strata-swift-iq3s-service.log:33425`。

**注意**：消息指向的 engine 日志里没有这一行（`grep -i 'never rang|illegal'` 为空），
崩溃原因只写在 service 日志。下次修这条指针，否则排查会先扑错文件。

## 生产身份

| 项 | 值 |
|---|---|
| 主机 / 系统 | `ubuntu-server`，Ubuntu 24.04（`7.0.0-42-generic`） |
| 驱动 / CUDA | `595.71.05` / `13.2` |
| GPU | 2 × RTX 2080 Ti 改装 22 GB（`22,528 MiB`/卡），PCI `0000:03:00`、`0000:04:00` |
| server | `/opt/ai/venvs/test-027/bin/python -u strata.py serve --config /opt/ai/config/strata-swift-iq3s-trial.json`，`0.0.0.0:8080`，`parallel=2` |
| engine | `/opt/ai/src/strata-batch-prefill-guard-20261101/build-guard-prod/strata` |
| engine SHA-256 | `0e50583547aa355b12dc05583b4b3a18d8dc40c314ac35d425920a122fcaab0e`（mtime 2026-11-02 13:23） |
| config SHA-256 | `8b7aaae7ad961c4cc61926b54fd3aa25c7f811be8dc896eff1e858eeb3cc6445`（mtime 2026-11-11 09:23:51Z） |
| 构建架构 | `CMAKE_CUDA_ARCHITECTURES:STRING=75` |
| 模型 / 上下文 | `swift-1.5-qwen3.8-flash-next-iq3_s`，`262144`，INT8 KV |
| 关键参数 | `--spec 8 --mtp-max-t 4 --batch-groups 2 --conversation-cache-mib 12288 --conversation-cache-slots 12` |
| engine env | `STRATA_HC_SPLIT=1`、`STRATA_BATCH_SERIAL_TWO=1`、`STRATA_PREFILL_CPU_SHARE=auto`（`/proc/1064881/environ` 核对） |
| 重启后 health | `{"status":"ok","max_context":262144,"loaded":true,"api_key":true}` |

## GPU 证据

同一秒（08:19:42Z）内核记录 142 条 NVRM 行：

- **141 条 Xid 13**，全部 `PCI:0000:03:00`，
  `Graphics SM Warp Exception ... Illegal Instruction Encoding`，
  覆盖 `GPC 0–5 / TPC 0–5 / SM 0–1`，ESR 均为 `0x4c1eb72`；
- **1 条 Xid 43**，`pid=952073, name=strata, channel 0x0000000b`——被驱动终止的正是引擎；
- **GPU1（`0000:04:00`）0 条**；
- 重启后采样（08:31Z）：两卡 `21,192 / 21,134 MiB` 占用、`39/33 °C`、`47.8/48.4 W`、P8；
- `vram-perf.csv`：08:25:01Z 空闲 `808/868 MiB`；08:20:01Z（引擎刚退出）`18,014/17,526 MiB`。
  **事故瞬间的精确空闲值没有单独采样**，不能写成一个精确数字。

## 请求形状与时间线

| 时间 (UTC) | 事件 |
|---|---|
| 03:20:42 | 引擎 pid 952073 启动（`engine.log:24048`） |
| 08:19:42 | GPU0 141×Xid 13 + 1×Xid 43；引擎报 `verify batch: layer 19 never rang`，退出 1 |
| 08:19:48 | server 开始自动重启引擎 |
| ~08:21:45 | `engine is running again`（重启约 `117 s`） |
| 16:24 CST | 用户就 `server_error` 提问（事故约 5 分钟前） |

事故时在跑的请求：

- `98,975` token prompt → 281 token 后 error，`15 s`（`21.8 tok/s`）；
- `99,894` token prompt → 1173 token 后 error，`45 s`（`42.9 tok/s`）；
- 另有一路 `11,504` token 的请求。

引擎从 03:20:42 到 08:19:42 共存活 `4 h 59 min`。

## 与 2026-11-01 事故对照

| 项 | 2026-11-01 | 2026-11-12 |
|---|---|---|
| 时间 | `2026-10-31T17:27:28Z` | `2026-11-12T08:19:42Z` |
| Xid 行 | 8×Xid 13 + 1×Xid 43 | **141×Xid 13 + 1×Xid 43** |
| PCI | `0000:03:00` | `0000:03:00` |
| ESR | `0x4c1eb72` | `0x4c1eb72` |
| engine SHA-256 | `e60a334c0ca5c1974d79385256aaf90519bcb646e2626f7e66de818d104b0ab7` | `0e50583…` |
| 上下文 | `input_tokens=15,419` | 两路 ~`98,975` / `99,894` |
| env | 无 `HC_SPLIT` | **有 `HC_SPLIT=1`** |
| 结果 | exit 1 + 自动重启 | 同 |

## 判断

1. **不是 DSH、也不是请求校验。** 错误由 server 的 `EngineDied` 生成，引擎自己报的是
   CUDA `illegal instruction`；`never rang` 是验证窗口等不到前置 kernel。
2. **不是编错架构。** 构建目标 `sm_75`，与本机一致。
3. **硬件/驱动执行风险优先。** 两次事故是**两套不同 engine 二进制**、同一张 GPU0、同一
   ESR、同一签名；GPU1 零 Xid；本机 GPU0 另有 2026-09-25 的 Xid 62/154 与
   `GPU Reset Required`（vLLM + 595.91.07）；上游 issue #4190 的同型号改装卡同 ESR，
   以 GDDR6 时序余量/板级问题收尾。
4. **全 SM 同时 Illegal Instruction Encoding** 更像指令取指/译码层面的位错误，而不是某条
   业务代码分支。但本轮 `99k × 2` 并发是新形状（同一构建此前只完成到 36k 级），
   所以“特定形状下的内核路径”仍是未排除的备选解释。
5. **`HC_SPLIT=1` 未阻止复发**，它没有通过真实长曝光，只能继续算风险缓解候选。

## 影响

- 两个真实请求在 15 s / 45 s 后失败，DSH 整个 turn 以 `server_error` 结束；
- 约 `117 s` 服务空窗，期间新请求会 503；
- 事故后到 08:32Z 采样无新 Xid，`/health` loaded。

## 已做（只读）

事故快照：`/opt/ai/logs/incident-20261112T081942Z-xid13/`

| 文件 | 大小 | 内容 |
|---|---:|---|
| `kernel-xid.log` | 23,449 B | 08:19–08:25Z 的内核日志 |
| `service-tail.log` | 4,456 B | service 日志第 33400–33450 行 |
| `config.json` | 3,854 B | 事故时 config 副本 |
| `gpu.csv` | 366 B | 事故后 `nvidia-smi` 采样 |
| `sha256.txt` | 248 B | engine 与 config 的 SHA-256 |

另已核对 engine env、GPU1 的 Xid 计数、重启后 health。**取证阶段未改 config、未重启
服务；随后的功率上限改动见下节。**

## 已执行的缓解（2026-11-12）

按下一轮合同先做成本最低的两项，结果：

| 项 | 状态 | 说明 |
|---|---|---|
| 功率上限 280 → 250 W（两卡） | **已上线并固化** | `systemctl is-enabled strata-gpu-tune` = enabled、active |
| 显存 −300 MHz | **未能执行** | 驱动/显卡不开放锁显存时钟，见下 |

### 功率上限（已固化）

- 单元 `/etc/systemd/system/strata-gpu-tune.service`：oneshot，
  `ConditionPathExists=/dev/nvidia0`、`After=nvidia-persistenced.service`、`RemainAfterExit=yes`；
- 脚本 `/usr/local/sbin/strata-gpu-tune.sh`：幂等，每次启动写 `/var/log/strata-gpu-tune.log`；
- 当前 `nvidia-smi --query-gpu=power.limit` → `250.00 W / 250.00 W`（默认 250、最大 280）；
- 回滚：`systemctl disable --now strata-gpu-tune`，再
  `nvidia-smi -i 0 -pl 280` 与 `nvidia-smi -i 1 -pl 280`；
- 重启后由单元重新应用；改单元文件后要 `systemctl daemon-reload`。

### 显存降频（不可用）

`595.71.05` + GeForce 上 `nvidia-smi -lmc` 与 `-lmcd` 都返回
`Setting locked Memory clocks is not supported`；连加载态实际值 `6800,6800` 也被拒，
`-ac` 已弃用。驱动报告的显存档位只有 `405 / 810 / 5000 / 6800 / 7000 MHz`，加载态本来就跑
`6800`，所以“−300 MHz”在这台机器上没有对应档位，只剩 `5000`（−26%）这一档。

可走的替代路线（都需要单独决定，本轮未做）：

1. 功率再降到 220 W（上游 workaround 用过 220 W），单变量、无需重启；
2. Xorg + Coolbits 的 `GPUMemoryTransferRateOffset`（真正的 −300 偏移，但要在生产机起 X）；
3. 配置层降内存压力（`--pcie-frac`、conversation cache、上下文），代价是重启引擎和吞吐。

### 吞吐与功耗

- 改动前真实流量 8 个样本：`71.1–79.1 tok/s`，中位 `76.0`（输出 100–2,600 token）；
- 改动后三组 `max_tokens=256`、实际 93–94 token：`50.8 / 73.1 / 85.5 tok/s`，中位 `73.1`；
  形状与基线不同，**不能据此宣称回退**；
- 解码期间功耗 `112.7 / 108.6 W`，远低于 250 W，说明 250 W 主要约束 prefill 峰值，
  不是解码。

## 下一轮合同

1. **只读告警**：跟 `journalctl -k` 的 Xid 13/43，命中立即保存前后 60 s 的请求形状与
   GPU 状态；现在只能等用户发现，这次就是 5 分钟后才知道。
2. **显存降频**：NVML/GeForce 不支持（见上），改用 220 W 或 Coolbits 偏移，仍保持单变量；
   记录吞吐、延迟、质量、温度、功耗，曝光必须比基线长，才允许写“有限窗口风险缓解”。
3. **仍复发再评估 GPU0 隔离**：当前每卡已占 `21.2 GiB`，单卡放不下同一 config；
   要缩 pack 或上下文，先算清代价与回滚再动。
4. **最后才换卡/换驱动。**
5. 判据沿用 2026-11-01：新 Xid 即失败；固定请求 soak 不算修复；回滚路径先演练。

## 没测什么

- 没有复现；没有做显存降频、GPU0 隔离、换卡、换驱动；
- 没有证明 GPU 永久损坏，也没有证明某个 kernel 是唯一根因；
- 没有采集事故瞬间的精确 VRAM/时钟/温度（只有 08:20 退出后与 08:25 的采样）；
- 没有在 GPU1 上验证 `99k × 2` 形状（单卡放不下当前 config）；
- 没有在 250 W 下做长曝光，也没有验证 250 W 是否降低了 Xid 频率；
- 没有评估 DSH 侧对 503 的重试。

## 附录：固化文件

`/etc/systemd/system/strata-gpu-tune.service`：

```ini
[Unit]
Description=Strata GPU tuning: cap power at 250 W (GPU0 Xid 13 mitigation)
Documentation=file:/opt/ai/logs/incident-20261112T081942Z-xid13/
After=nvidia-persistenced.service
Wants=nvidia-persistenced.service
ConditionPathExists=/dev/nvidia0

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/strata-gpu-tune.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

`/usr/local/sbin/strata-gpu-tune.sh`：

```sh
#!/bin/sh
# Strata GPU tuning, born from the 2026-11-12 GPU0 Xid 13 recurrence.
# Caps both RTX 2080 Ti cards at their default 250 W power limit
# (the cards had been raised to 280 W). Idempotent; safe to re-run.
# Rollback:
#   systemctl disable --now strata-gpu-tune
#   nvidia-smi -i 0 -pl 280 && nvidia-smi -i 1 -pl 280
set -u

LOG=/var/log/strata-gpu-tune.log
{
  echo "=== $(date -u +%FT%TZ) apply ==="
  for i in 0 1; do
    nvidia-smi -i "$i" -pl 250 || echo "WARN: -pl 250 failed on GPU $i"
  done
  nvidia-smi --query-gpu=index,power.limit,power.default_limit --format=csv
} >>"$LOG" 2>&1
```
