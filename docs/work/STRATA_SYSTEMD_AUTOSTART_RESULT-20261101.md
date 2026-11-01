# Strata systemd 自启动修复结果（2026-11-01）

## 结论

生产 Strata 已从 detached 手动实例切换为 enabled systemd service。普通重启和主进程
`SIGKILL` 故障注入都通过，最终 health loaded。该修复解决“机器重启后 8080 不自动恢复”
和“engine 异常退出后只能人工重拉”，不宣称 GPU Xid 已修复。

## 根因

旧 `/etc/systemd/system/strata.service` 调用
`/opt/ai/bin/run-strata-official-027.sh`，后者使用旧的
`/opt/ai/src/strata-0.2.7/strata-live.json`。该 config 指向不存在的
`/opt/ai/src/strata-0.2.7/build-sm75/strata`，因此每次启动约 2 秒后以 exit 1 退出。
生产实际运行的是另一个 detached 手动实例，unit 失败不会自动恢复用户路径。

## 修复

稳定入口：

```text
/etc/systemd/system/strata.service
/opt/ai/bin/run-strata-production.sh
/opt/ai/etc/strata-production.json
```

仓库源文件：

```text
tools/server/strata-production.service
tools/server/run-strata-production.sh
```

关键 unit 属性：

```text
Restart=always
RestartSec=15
StartLimitIntervalSec=0
KillSignal=SIGTERM
TimeoutStopSec=240
```

稳定 config 从 A1 config 复制，只改 `log` 为 `/opt/ai/logs/strata-engine.log`。engine、
模型、双卡、262144 context、6 slots / 2 groups、HC_SPLIT 和 CPU-share 参数不变。

## 验证

2026-11-01：

1. 首次 systemd 冷启动失败，日志为稳定 config 对 `atm` 用户不可读；修正为
   `atm:atm 0600` 后启动成功。
2. 普通 `systemctl restart` 从 PID `4631` 切到 `5172`，停止没有拖满超时，最终 health
   loaded。
3. 对 PID `5172` 执行受控 `SIGKILL`；2 秒时 service 为 `activating/auto-restart`，
   15 秒后新 PID `5836`、`NRestarts=1`，随后 health loaded。
4. 最终 health：`loaded=true`、`max_context=262144`、6 slots / 2 groups，端口 8080。
5. server env 实测含 `STRATA_ADMIT_SHORT_FIRST=2048`、
   `STRATA_DEFER_TITLE_REQUESTS=1`、`STRATA_MP_STAGED_READ_MIN_TOKENS=2048`、
   `STRATA_PIN_DSH_TOOL_PREFIX=1`；engine env 含 `STRATA_HC_SPLIT=1`、
   `STRATA_BATCH_SERIAL_TWO=1`、CPU-share batch guard 和 `auto` share。
6. 本轮 production restart/crash-recovery 窗口没有新的 `NVRM: Xid`。

## 身份与回滚

```text
stable config d76772e7089c18af38c0a8932588d99674a8b6bd8208f386b6c8599fd81c863a
launcher      a1e5619e9112a15272bdc233aa97f9d15e2efd8eaa92f37a2e67b468f287999b
unit          893f8d0595ee6657a3e8e5a414870b99272cbb57c4e6fc4f4118d08f818276e1
engine        e60a334c0ca5c1974d79385256aaf90519bcb646e2626f7e66de818d104b0ab7
A1 config     c956f280afaeae0847b48caed315bcc90fb7a6c74364494a8e25d62590b23312
```

旧 unit 备份：

```text
/opt/ai/logs/strata.service.pre-autorestart-20261101
```

## 限制

- 冷加载仍约 2–3 分钟；自动重启恢复的是进程，不会保留 RAM expert cache 和
  conversation cache。
- 本次没有重启整机。开机路径由 `enabled + multi-user.target + stable absolute paths`
  保证；普通 restart 与进程崩溃自动拉起已实测。
- GPU0 低频 Xid 13/43 仍是独立 P0 风险，自启动修复只减少故障后的不可用时间。
