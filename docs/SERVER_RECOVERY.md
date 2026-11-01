# 恢复手册：这台机器就是我自己

> **状态：操作手册（现行）。** 2026-11-01 起生产由 enabled `strata.service` 管理；
> 只在服务不可用时使用。稳定入口是 `/opt/ai/bin/run-strata-production.sh` 和
> `/opt/ai/etc/strata-production.json`，仓库源文件在 `tools/server/`。

**为什么单列一份**：用户通过 SSH relay 使用生产 Strata。动服务会直接影响正在进行的
vibe coding 会话，所以恢复必须先看进程、实际 config 和 health，再决定是否重启。

## 1. 30 秒健康检查

```sh
ssh -p 8686 atm@129.28.27.137 'systemctl is-enabled strata; \
  systemctl is-active strata; \
  systemctl show strata -p MainPID -p NRestarts -p Restart -p RestartUSec --no-pager; \
  nvidia-smi --query-gpu=index,memory.free,temperature.gpu --format=csv,noheader; \
  curl -fsS --max-time 5 http://127.0.0.1:8080/health; \
  journalctl -k -b --no-pager | grep "NVRM: Xid" | tail -n 20'
```

判读：

| 看到 | 含义 | 动作 |
|---|---|---|
| `loaded=true`、262144 | 服务已加载 | 可继续使用 |
| `active/running` 但 health 未 ready | 正在冷加载 | 冷启动约 2–3 分钟，继续等待，不要叠加重启 |
| `NRestarts` 增加且 MainPID 变化 | systemd 已自动拉起 | 看 `strata-service.log` 与 `strata-engine.log` 找退出原因 |
| 新 `NVRM: Xid`、`never rang` 或错误 turn | GPU/驱动执行风险 | 保存时间、请求和日志；不要只重试同一请求 |
| `is-enabled` 不是 enabled | 开机不会自动启动 | 恢复 unit enable 状态 |

## 2. 恢复顺序（从轻到重，一步不行再下一步）

1. 重启：`ssh -tt -p 8686 atm@129.28.27.137 'sudo systemctl restart strata'`。
   普通用户直接 restart 会被 polkit 挡下。**重启到 health ready 约 2–3 分钟，且会丢失
   RAM expert cache 与 conversation cache**；不在正需要 token 时随手重启。
2. 看 systemd：`systemctl show strata -p ActiveState -p SubState -p MainPID -p NRestarts`。
   `Restart=always` 应在 15 秒后拉起；若持续失败，读
   `/opt/ai/logs/strata-service.log`、`/opt/ai/logs/strata-engine.log` 和
   `journalctl -u strata -b -n 100`。
3. 确认没有第二实例抢 8080：

   ```sh
   ss -ltnp | grep :8080
   ps -eo pid,ppid,etime,args | grep -E 'strata.py serve|/strata --serve' | grep -v grep
   nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader
   ```

4. 配置或二进制坏掉时，先恢复最近已验证的稳定 config，再 restart。当前已验证 config
   SHA-256 为 `d76772e7...`、engine `e60a334c...`、launcher `a1e5619e...`。
5. 服务 ready 后跑一次最小 completion，确认端到端可用，再继续实验。

## 3. 改配置的规矩

- 生产 config 是 `/opt/ai/etc/strata-production.json`，服务用户为 `atm`，文件应为
  `atm:atm 0600`。不要把含 API key 的完整 config 打印或提交。
- 改前复制一份带 UTC 时间戳和 SHA-256 的备份；改后先 `jq -e` 检查 JSON、engine
  `args` 形状、`port=8080` 和关键 env，再 restart。
- 改 env 用 `env.STRATA_XXX`；`STRATA_HC_SPLIT=1`、CPU-share guard、双卡和
  262144 context 属于当前生产身份，除非明确实验，不得顺带丢失。
- 改完必须 restart 才生效。验收看实际 `/proc/<pid>/environ`、engine/config hash、
  health、6 slots / 2 groups 和新增 Xid，不能只看 old process 的 health 200。

## 4. 禁令（不可谈判）

- 不换驱动：钉在 `595.71.05`（apt 包固定，禁止自动升回 `595.91.07`——那套栈有 Xid
  62/154 历史）。但 `595.71.05` 在 2026-10-31 也发生过 GPU0 Xid 13；固定版本不等于不会
  Xid，不能再把它写成硬件稳定性的保证。
- 不 `-lgc` 锁频（本机扫描全负：1700 MHz −4.8%，1600 MHz −8.8%）。
- 不动 ggml pin `3cf03257`，除非同时给出本机 A/B。
- 不在生产 boot 里跑需要 >2 分钟停机的实验，除非已获批准。
- 不耗尽 RAM / 显存 / 磁盘：开工前记 free 值，收工后复测。
- 外部网站只读不压测。

## 5. 已知事实（2026-10-24 复核）

- 显存只被一个进程占：Strata 引擎本体（GPU0 21.4 GiB / GPU1 21.0 GiB）。**余量问题是我们自己造成的**，
  不是外部进程抢的。
- GPU0 `0000:03:00` 有两次跨软件栈事故：2026-09-25 Xid 62/154，2026-10-31 Xid 13/43。
  2026-10-31 的固定请求约 60 次重放未复现；上游同型号改装 22GB 卡在降低显存频率后不再
  出现同 Xid。当前按低频硬件风险处理，尚未证明器件损坏。
- 定时器 `nvlink-crc-sample.timer` 每小时在跑，最新读数两卡两 link 的 `crc/replay/recovery` 全 0。
- 无残留实验进程，磁盘剩 162 G。**用户 crontab 现在有一条**（2026-10-24 加的）：
  `*/5 * * * * /opt/ai/bin/vram-perf-sample.sh` → `/opt/ai/logs/vram-perf.csv`，零负载被动采样，
  低于 700 MiB 另写 `vram-perf.csv.warn`。恢复时别把它当垃圾删了。
- GPU0 温度那条已复核完毕：**是负载驱动的，不是热故障**。同一台机器上的读数是
  78 ℃（我自己的 eval 在跑）→ 75 ℃ → 62 ℃（负载停下），GPU1 48–60 ℃。
  调优记录里 45–52 ℃ 是轻载读数。真正的判据应该是"负载停下后能不能回到 60 ℃ 以下"。

## 6. 工装自己会骗人的三个地方（2026-10-24 实测撞出来的）

- **`/health` 答 200 不代表重启过**。旧引擎在退出前仍可能答 200；判断重启必须同时看
  systemd `ExecMainStartTimestamp`、MainPID、engine PID 和实际 `/proc` argv/env。
- **稳定 config 是运行身份，不是任意历史实验快照**。动手前先 hash 并与
  `/opt/ai/etc/strata-production.json` 对照，确认没有把 HC_SPLIT、CPU-share 或双卡
  参数静默改回旧值。
- **别把脚本放在 `/tmp`**。逐位门原来就是 `/tmp/parity.py`，`/tmp` 一清就没了，而 `run-arm.sh`
  照样跑完一臂、留下一份看起来完整的日志。现在门是 `/opt/ai/bin/parity_window.py`
  （repo 里 `tools/server/`），arm 脚本找不到它就拒绝开跑。
- **`args` 必须是字符串列表，不是字典**。任何点号 setter 改走 `args.<index>` 后都要
  `jq -e '.args | type == "array" and length > 50 and all(.[]; type == "string")'`；
  形状不对就不 restart。
