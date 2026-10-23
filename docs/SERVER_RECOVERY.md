# 恢复手册：这台机器就是我自己

**为什么单列一份**：本 DSH 会话生成的 token 来自 `192.168.3.14:8080` 的 Strata 服务。
证据：服务日志里出现本会话的行——`writing a tool call: bash: 755 tokens in 14 s (82.0 tok/s) ... expert cache 98.0% hit`。
所以**动服务 = 动我自己**。这份手册的存在意义是：即使我的会话断了，下一个我还能把自己救回来。

## 1. 30 秒健康检查

```sh
ssh atm@192.168.3.14 'systemctl is-active strata; \
  nvidia-smi --query-gpu=index,memory.free,temperature.gpu --format=csv,noheader; \
  curl -s -m 5 -o /dev/null -w "http=%{http_code} t=%{time_total}s\n" http://127.0.0.1:8080/health; \
  grep -c "MiB of VRAM free" /opt/ai/src/strata-0.2.2/strata-022.log'
```

判读：

| 看到 | 含义 | 动作 |
|---|---|---|
| `http=200` 且 <0.01 s | 服务活着 | 继续 |
| `only N MiB of VRAM free ... (#2065)` 且 N < 512 | 驱动可能把显存换页到 RAM，decode 掉到几 tok/s，**一个窗口可能永远跑不完** | 提高 `--vram-reserve-mib` 或降低 `--max-context` / `--batch`（见台账） |
| `is-active` 不是 active | 服务死了 | §2 恢复 |
| 请求挂住 >60 s 且 GPU 利用率 100% 不动 | 窗口卡死 | §2 恢复（重启是唯一已知手段） |

## 2. 恢复顺序（从轻到重，一步不行再下一步）

1. `ssh atm@192.168.3.14 'systemctl restart strata'`，等 30–60 s（实测本 boot：unit 起 15:41:09 → 引擎起 15:41:29，约 20 s）。
2. 起不来 → 回退配置：`ls -t /opt/ai/backups/*/strata-022.json.* | head -3`，取最近一份
   `cp` 回 `/opt/ai/src/strata-0.2.2/strata-022.json`，再 restart。
3. 还起不来 → 看 `/opt/ai/logs/strata-service.log` 尾部与 `journalctl -u strata -n 30`；
   已知失败形态：`libstrata_kq.so did not start on GPU 1 (CUDA error 2)` + `cudaMalloc(...) for the weight arena failed`
   （上游 #3797：多为有进程占着显存，不是分配器 bug）→ 先确认没有第二个进程占显存：
   `nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader`。
4. 我的会话断了 → 目标 `goal-96ec4721-4579-431f-bf5c-81dfa8e36a2b` 是持久化的；服务恢复后从台账最后一条继续即可。
   恢复后第一件事：跑一次最小 completion 确认端到端通，再决定要不要继续实验。

## 3. 改配置的规矩

- **只用** `/opt/ai/bin/set-config-key.py <点号路径> <JSON值> --config /opt/ai/src/strata-0.2.2/strata-022.json`。
  它自动备份到 `/opt/ai/backups/<日期>/`。**必须显式给 `--config`**——它的默认值是 IQ2 那份配置，
  在这台机器上是个陷阱（脚本自己写了这句警告）。
- 引擎参数在 `args` 里是一个 list：`--vram-reserve-mib` 在索引 42，它的值在索引 43。改索引 43，别改 42。
- 改 env 用 `env.STRATA_XXX`。改 args 里的值用 `args.<索引>`。
- **改完必须重启才生效**（"The server reads the config when it starts"）。重启前先想清楚 §1 的判读和 §2 的回退。

## 4. 禁令（不可谈判）

- 不换驱动：钉在 `595.71.05`（apt 包固定，禁止自动升回 `595.91.07`——那次产生 Xid 62/154）。
- 不 `-lgc` 锁频（本机扫描全负：1700 MHz −4.8%，1600 MHz −8.8%）。
- 不动 ggml pin `3cf03257`，除非同时给出本机 A/B。
- 不在生产 boot 里跑需要 >2 分钟停机的实验，除非已获批准。
- 不耗尽 RAM / 显存 / 磁盘：开工前记 free 值，收工后复测。
- 外部网站只读不压测。

## 5. 已知事实（2026-10-24 复核）

- 显存只被一个进程占：Strata 引擎本体（GPU0 21.4 GiB / GPU1 21.0 GiB）。**余量问题是我们自己造成的**，
  不是外部进程抢的。
- 定时器 `nvlink-crc-sample.timer` 每小时在跑，最新读数两卡两 link 的 `crc/replay/recovery` 全 0。
- 无残留实验进程，无用户 crontab，磁盘剩 162 G。
- GPU0 温度 75 ℃ / GPU1 58 ℃（负载 156 W / 132 W）——比调优记录里写的 45–52 ℃ 高，**待复核**。
