# Flash-Next 运行时重构交接（2026-10-30）

**状态：BLOCKED，等待不挤占生产实例的硬件或维护窗口。**

这份文档是当前工作的续跑入口。生产 Strata 没有重启，没有发送生产合成生成负载，
也没有修改生产默认配置。不要把隔离 fake-runtime 结果写成生产实测结果。

## 1. 目标

按 `docs/superpowers/plans/2026-10-27-resource-utilization-optimization.md` v7.1
执行 Flash-Next 单模型运行时重构，从 G0A 观测闭环开始逐切片实现、验证、提交和推送。

硬约束：

- G0A Gate 和 LKG/自动回滚验证完成前，不重启当前生产 Strata。
- 不向生产实例发送合成生成负载。
- 不修改生产默认配置。
- 冻结区内规则不得改动。
- 只推送 `origin https://github.com/atmJava/Strata.git`，不推送 upstream。

## 2. 工作区与外部环境

| 项目 | 当前值 |
| --- | --- |
| Windows 工作区 | `D:\temp\strata` |
| 分支 | `hw/server-adapt-0.2.3` |
| 交接起点 HEAD | `b105ce2579b222663e3ad1e2a3ec12c2f34d6425` |
| 交接起点 origin | 与该 HEAD 一致 |
| 目标服务器 | `atm@192.168.9.103`，Ubuntu 24.04.5 |
| GPU | 2 x RTX 2080 Ti 22 GB，NVLink NV2 |
| Python 测试环境 | `/home/atm/verify-20261027/venv/bin/python` |
| 当前隔离测试树 | `/tmp/strata-runtime-release-20261030` |
| G0A 隔离构建树 | `/tmp/strata-g0a-20261030151231-v2`，`build75-g0a2` |

临时目录可能被清理。若不存在，应从当前 HEAD 重建隔离树，再覆盖未提交文件；不要直接
在当前生产目录运行测试或覆盖运行中的 engine。

本文档提交后，仓库 HEAD 会继续前进；“交接起点 HEAD”指本文档记录验证结果时的代码。

## 3. 已推送提交

| 提交 | 内容 |
| --- | --- |
| `a9b679a031511a977e7c28adf8076297a80afb42` | 启动验收增加显式 health 门禁 |
| `8e51da3c9e0c3c82266339cf7816065a04e89135` | 增加 LKG 制品身份门禁 |
| `ccb254c2df33b89bb7af436cc647289ffba8ab0b` | 固化不可变 LKG release |
| `3c870ad12f0dc283d00abd4e4cccce81d0bc383e` | 记录原生协议演练资源门禁 |
| `04a6b850f8df29d417990e3799eb6cb3b18f137d` | 记录 systemd 监督边界 |
| `b105ce2579b222663e3ad1e2a3ec12c2f34d6425` | 增加 systemd 服务入口的 LKG 回滚 |

## 4. 当前实现

### 4.1 制品与 release 身份

- `artifact_id` 只表示 engine、服务 Python 代码、模板、tokenizer 和 server 静态资产。
- `release_id` 同时绑定 `artifact_id`、精确 source config 摘要和 launcher 摘要。
- 相同代码配不同配置会生成不同 release；相同代码和配置只复用经过完整校验的 release。
- release 在 owner-private `runtime/releases/<release_id>/` 中保存：
  - engine；
  - `serve/` 和 `strata_control/` Python 文件；
  - chat template、tokenizer 和 `serve/web/` 资产；
  - 改写为 release engine 路径的 `run.json`；
  - 固定 launcher；
  - manifest 和全部摘要。
- 发布过程使用 staging、fsync、原子 rename、owner-only 权限和 manifest/path/hash 校验。

实现位置：

- `strata_control/artifacts.py`
- `strata_control/releases.py`
- `strata_control/runtime.py`

### 4.2 LKG 与自动回滚

- LKG schema 为 3，记录 source bytes、source digest、launch config、server argv、
  artifact、release ID、验证 instance 和 readiness state。
- 成功候选只有通过 telemetry 身份、launch attestation、`/health` 和 generation
  readiness 后才替换 LKG。
- restart 在 drain 前校验 LKG、release、运行中 artifact 和候选 source。
- 候选启动失败、ready 前退出或 generation probe 失败时，从 LKG 指定的不可变 release
  恢复旧 engine、旧 Python 代码、旧资产和旧配置。
- 回滚不会保留正在传输的 HTTP/SSE 请求；它恢复服务，不保证当前请求不断线。

### 4.3 systemd 服务入口

- 新增隐藏入口：

```text
python -m strata_control.runtime --host PROFILE --service
```

- 普通 `strata runtime start` 路径没有改变；`--service` 是显式 opt-in。
- 服务入口在 profile 锁内先清理已验证的 stale owner/server 记录，再绑定 socket 和发布
  state lease，避免 systemd 重启绕过 CLI 的 stale recovery。
- 演练 unit 使用 `Restart=on-failure`、`RestartSec=1` 和
  `KillMode=control-group`。
- `systemd --user` fake-engine 演练通过：
  - owner 被 `SIGKILL`；
  - unit 自动重启；
  - 新 owner 清理 stale state；
  - 坏的候选 engine 在 generation probe 失败；
  - 从旧 release 回滚；
  - unit 返回 `active`；
  - `artifact_id` 和 `release_id` 保持不变。

演练 unit：`strata-rollback-drill-87a0a3de07`。

## 5. 已验证结果

在 Linux、Python 3.12.3、`MP_BASE_URL=http://127.0.0.1:9` 下：

| 套件 | 结果 |
| --- | --- |
| `tests/terminal/test_runtime.py` | `77 passed, 11 subtests passed` |
| `tests/terminal` | `272 passed, 1 skipped, 189 subtests passed` |
| serve lifecycle / lifeline / ready-timeout | `20 passed, 6 subtests passed` |
| systemd user fake-engine drill | PASS |
| `py_compile` | PASS |
| JSON、UTF-8 无 BOM、`git diff --check` | PASS |

复跑 runtime：

```sh
ssh ubuntu-server
cd /tmp/strata-runtime-release-20261030
env PYTHONPATH="$PWD" MP_BASE_URL=http://127.0.0.1:9 \
  /home/atm/verify-20261027/venv/bin/python -m pytest -q tests/terminal/test_runtime.py
```

复跑完整 Terminal：

```sh
env PYTHONPATH="$PWD" MP_BASE_URL=http://127.0.0.1:9 \
  /home/atm/verify-20261027/venv/bin/python -m pytest -q tests/terminal
```

## 6. 生产状态

最后一次资源复测为 2026-10-30T11:03:02Z：

| GPU | 总显存 | 已用 | 空闲 | 利用率 |
| --- | ---: | ---: | ---: | ---: |
| 0 | 22528 MiB | 21182 MiB | 819 MiB | 0% |
| 1 | 22528 MiB | 21082 MiB | 920 MiB | 0% |

生产进程仍占用主要 GPU 显存和约 6.5 GiB available RAM。起第二个真实 Flash-Next
实例会挤占或驱逐生产，因此没有执行原生协议演练。

`systemd --user` 当前为 `running`，但 `atm` 的 `Linger=no`：

- 用户级 service 在用户管理器存活时可以自动恢复；
- 尚未证明注销、reboot 或登录 session 结束后仍能自动恢复；
- 当前生产实例不是已切换到的 schema 3 owner runtime。

## 7. Gate 状态

| Gate | 状态 | 说明 |
| --- | --- | --- |
| G0A 实现 | PASS | 隔离 fake/unit/HTTP 测试和 C++ 编译通过 |
| G0A 生产观测 | CLOSED | 新二进制未部署，未跑真实模型 request ID/BTRACE 演练 |
| 隔离持久 LKG | PASS | fake runtime |
| 隔离不可变 release | PASS | fake runtime |
| 隔离 systemd owner 恢复 | PASS | fake engine |
| 生产自动回滚 | CLOSED | 无真实模型、生产 systemd 或会话恢复证据 |
| G0B | NOT_STARTED | 资源不足，不能起第二真实实例 |

证据：

- `docs/work/evidence/flash-next-runtime-g0-20261030.json`
- `docs/work/evidence/flash-next-runtime-lkg-20261030.json`

## 8. 不能误判的边界

### 8.1 schema 2 不能自动迁移

LKG schema 2 必须继续拒绝，不能自动升级：

- schema 2 记录了 engine 和 `serve/`、`strata_control/` 服务代码的 artifact 摘要；
- 实现 schema 2 到 schema 3 迁移需要修改 `strata_control/runtime.py`；
- 修改后当前 artifact 摘要必然不同于旧 schema 2 artifact；
- 没有旧不可变 release 时，无法证明迁移出来的 release 与旧运行实例完全一致。

首次采用 schema 3 必须通过受控切换，并在完整 health/generation 验证后生成新 LKG。

### 8.2 release 还不是整机快照

当前 release 不固化：

- 模型分片；
- 动态库；
- Python 解释器；
- site-packages；
- GPU 驱动；
- GPU 或进程内存状态。

release store 也还没有 retention 或 GC。不要把它写成完整主机回滚。

### 8.3 回滚不等于零中断

- 当前请求在 restart/drain 时会断开；
- startup fallback 只覆盖候选在 ready 前失败的路径；
- 候选 ready 后崩溃的生产 systemd 恢复仍需真实演练；
- 用户级 systemd drill 使用 fake engine，不是性能或质量证据。

## 9. 下一步安全起点

恢复工作时先重新确认，不能沿用本文档中的旧资源数字：

1. 查询生产 PID、端口、GPU/RAM、systemd session 和替代访问。
2. 如果生产资源仍不足，不启动第二真实实例，不重启生产。
3. 如果获得维护窗口或替代硬件，先跑隔离原生协议演练：
   `X-Request-ID -> DONE/BDONE -> BTRACE`。
4. 原生协议演练通过后，再进入 G0B 的单路基线、双路并发、冷预填、取消和恢复矩阵。
5. 真正切换生产前，必须有：
   - 当前会话的替代访问；
   - 受控生成 schema 3 LKG 的路径；
   - health、generation、API smoke 和 rollback 门；
   - 明确的回滚时限和失败处理人。

不要因为 fake runtime 或 systemd user drill 通过就进入性能实验。

## 10. 常用只读检查

```sh
git status --short --branch
git log -8 --oneline --decorate
git rev-parse HEAD origin/hw/server-adapt-0.2.3

ssh ubuntu-server nvidia-smi \
  --query-gpu=timestamp,index,memory.total,memory.used,memory.free,utilization.gpu \
  --format=csv,noheader

ssh ubuntu-server systemctl --user is-system-running
ssh ubuntu-server loginctl show-user atm -p Linger -p State
```

## 11. Git 推送注意事项

当前主机曾因 Mihomo fake-IP 导致 GitHub TLS 握手失败。一次成功推送使用的是：

```sh
git -c http.curloptResolve=github.com:443:140.82.112.3 \
  push origin hw/server-adapt-0.2.3
```

这是临时网络绕过，不是永久配置。复用前先验证该 GitHub IP 可访问，并确认远端仍是
`atmJava/Strata`。永远不要推 upstream。

## 12. 交接结论

控制面和回滚能力已经有可审计的隔离证据，下一步真正缺的是不挤占生产的真实模型运行
窗口。当前不应继续扩写部署平台，也不应反复重启生产来换取不可复核的观测。恢复工作的
第一件事是重新测量资源并取得维护窗口或替代硬件。
