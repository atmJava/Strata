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

1. 重启：`ssh atm@192.168.3.14 'echo <sudo 密码> | sudo -S systemctl restart strata'`。
   普通用户直接 `systemctl restart` 会被 polkit 挡下（`Failed to restart strata.service: Interactive authentication required`）。
   **实测到可用要 165 s**（unit 起 17:02:39 → `/health` 返回 200 约 17:05:24）。journal 里那个"起 15:41:09 → 引擎起 15:41:29"
   只是 unit 起 → 引擎进程起，**不代表能服务**。所以：**重启 ≈ 3 分钟没有推理**，别在自己正需要 token 的时候随手重启。
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
- 引擎参数在 `args` 里是一个 **54 元素的字符串 list**：`--vram-reserve-mib` 在索引 42，它的值在索引 43。
  **不要用 `set-config-key.py args.43 1280`**——实测它把整个 list 换成了 `{"43": 1280}`，54 个引擎参数全丢，
  下次启动就是坏的（当时服务还在跑，只因为它启动时读过旧配置）。
  正确做法：**就地替换 JSON 文本里那一个值**，然后 `json.load` 断言 `args` 仍是 54 个字符串、目标值已生效，
  **检查通过才重启**。值必须是字符串（`"1280"`），写成数字会让 argv 构造出错。
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
- 无残留实验进程，磁盘剩 162 G。**用户 crontab 现在有一条**（2026-10-24 加的）：
  `*/5 * * * * /opt/ai/bin/vram-perf-sample.sh` → `/opt/ai/logs/vram-perf.csv`，零负载被动采样，
  低于 700 MiB 另写 `vram-perf.csv.warn`。恢复时别把它当垃圾删了。
- GPU0 温度那条已复核完毕：**是负载驱动的，不是热故障**。同一台机器上的读数是
  78 ℃（我自己的 eval 在跑）→ 75 ℃ → 62 ℃（负载停下），GPU1 48–60 ℃。
  调优记录里 45–52 ℃ 是轻载读数。真正的判据应该是"负载停下后能不能回到 60 ℃ 以下"。

## 6. 工装自己会骗人的三个地方（2026-10-24 实测撞出来的）

- **`/health` 答 200 不代表重启过**。老引擎在被换掉之前一直在答。判断重启要看**引擎 PID 变了**；
  `use-config.sh` 现在就是这么判的，并且 `sudo` 失败就直接退出，不再把 stderr 丢掉后照样报 READY。
- **`strata-022.prod.json` 只是某一刻的快照，不是"生产配置"这个概念**。它曾停在 05:09 那份
  （`--vram-reserve-mib` 还是 960），谁拿它当基准做 A/B，谁就把 L-001 静默回滚了。
  现在它是 17:02 那份（1280），旧的留成 `strata-022.prod.json.pre-L001-0509`。
  **动手前先 diff 一下快照与线上**，一行就够。
- **别把脚本放在 `/tmp`**。逐位门原来就是 `/tmp/parity.py`，`/tmp` 一清就没了，而 `run-arm.sh`
  照样跑完一臂、留下一份看起来完整的日志。现在门是 `/opt/ai/bin/parity_window.py`
  （repo 里 `tools/server/`），arm 脚本找不到它就拒绝开跑。
- **`args` 是 54 个字符串的列表**，不是字典。`set-config-key.py args.43 …` 会把整个列表换成
  `{"43": …}`；`use-config.sh` 现在会先断言形状再装。
