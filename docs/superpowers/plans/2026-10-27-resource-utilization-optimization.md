# 资源利用率优化计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Version:** v2，2026-10-30 重写。v1 假设生产跑在 `strata.service` 上，现网不是这个形状；
本版按 2026-10-30 的实测事实重写"停服—采样—恢复"这条链，并把恢复做成服务器侧的自愈流程。
Task 1–3 的结论与门限沿用 v1，Task 4 起按新拓扑改写。

**Goal:** 用 NCU 找到 32K cold prefill 和 decode 的真实 stall，改造一个瓶颈内核或调度路径，
提高硬件管线利用率并缩短端到端任务时间。

**Architecture:** 先复核当前冻结候选
（`/opt/ai/src/strata-freeze-throughput-20261029T0812Z`，版本以 Task 1 的探针输出为准）并建立
可回滚快照，再从 Nsight 证据选出顶层 kernel，然后开停服窗口：用快照在冻结树里启动受控
server/engine，独占 GPU 跑 Nsys trace 或 NCU，窗口结束无条件恢复生产。恢复由服务器侧脚本和
看门狗负责，不依赖 agent，也不依赖被停掉的 strata。只有在 stall 证据、目标计数器和 wall
门限全部预注册后，才允许在独立源码树改动一个代码切片。参数 A/B 只用于验证代码候选，
不作为探索手段。

**Tech Stack:** Ubuntu、systemd（仅兜底）、CUDA 13、sm_75、Nsight Systems、Nsight Compute、
CMake、Python 3、Strata `tools/server` 探针、JSON 证据。

---

## v2 与 v1 的差别

- v1 把 `systemctl stop strata.service` 当停服步骤。现网该单元 `inactive`，真正在服务的是
  手动启动的 server+engine 进程对；停服和恢复都必须按进程对来做。
- v1 的恢复手段是 `use-config.sh` 和 `restore-prod.sh`，二者控制 `strata-0.2.2` 树加 systemd
  重启，与当前冻结候选树不同，不能当现网回滚路径。
- v1 没有回答"窗口失败后谁来恢复生产"。本版把恢复写进服务器：`util-restore.sh` 分层恢复，
  `util-watchdog.sh` 在驱动被强杀时接管，两者都不经过 strata，因此不需要 token。
- 当前会话的模型调用就跑在这台机器上。任何停服窗口都会让本会话在窗口期间拿不到 token，
  所以恢复路径必须先演练过一次，才允许开真实窗口。
- 代码块约定：标 `powershell` 的在本机 Windows 执行；标 `sh` 的按 bash 语义书写。只包在
  `ssh atm@... '...'` 里的块可直接在 PowerShell 跑，其余用到 `install`、`sha256sum`、`python3`
  的块应整段在服务器上或 Git Bash 中执行。

---

## 任务定约

Objective:

- 找到 32K cold prefill 中低 SM Issue、低 Tensor 和低 warp residency 的直接原因。
- 只改一个能缓解主要 stall 的内核或调度路径。
- 同时改善目标硬件计数器和端到端 wall，不把计数器上升误当成用户收益。
- 每个停服窗口都能在没有 agent、没有 token 的条件下把生产带回健康状态。

Non-goals:

- 不做 env、batch、slot、lead-in、expert slot 等独立参数扫描。
- 不在 NCU 证明 Tensor 是直接瓶颈前恢复 P7。
- 不重构无关模块，不一次修改多个 kernel，不改变输出语义。
- 不把 NVML、`SMs Active` 或任一单项计数单独称为"真实利用率"。
- 不重启机器来探索；重启只在 GPU 显存不释放或驱动异常时作为兜底。
- 不把 systemd 兜底当成等价恢复：它起的是另一棵树和另一份配置。

Scope:

- Allowed: `src/prefill/`、`src/kernels/cuda/` 中的单个瓶颈路径，必要的最小调度代码，
  对应 parity 测试和 `tools/server` 观测工具。
- Out of scope: 模型格式、tokenizer、API 行为、生产配置和无关性能开关。

Verification:

- 编译、目标测试、greedy parity、needle 和资源稳定性门通过。
- 同一 32K cold prompt 的 NCU/Nsys 计数与端到端 wall 同向改善。
- 候选通过一小时 mixed soak 后才能进入生产冻结点。
- 每个窗口以 `WINDOW RESTORED layer=relaunch` 收尾，停服时长与恢复耗时记录在案。

Risks / Unknowns:

- 单实例几乎占满两卡显存，NCU 需要停止生产并启动受控 server/engine。
- NCU replay 会改变执行时序，必须关闭时钟锁定和缓存清空，并保留原始 Nsys 对照。
- stall 类型决定代码路径；在没有 NCU 证据前不预设 kernel 结论。
- 生产配置在 `/tmp` 下，清理 `/tmp` 或重启机器都可能让它消失；快照必须带它。
- engine 的 `STRATA_*` 开关不在 server 进程环境里，只看 server 会漏掉真实运行参数。
- 可用内存约 3 GB，窗口内同时跑受控实例和 profiler 时不能再起第二个大进程。

**阶段边界：**

- **Task 0 是前置门**：自愈脚本没练过一次，就不允许开任何真实停服窗口。
- **第一阶段（Task 1–6）先完成只读准备，停服采样必须再次获得用户明确授权**：完成基线、快照、
  Nsight kernel 选择、受控 NCU、唯一根因判决和代码门限预注册；结束时生产必须已恢复。
- **第二阶段（Task 7–9）只能在 Task 6 产出唯一根因和已保存门限后启动**。具体改哪个函数、怎样
  重排 tile、同步或拷贝，必须在 NCU 报告到达后另写实施切片；不得提前猜测或把多个机制绑在一起。

## 停机预算与时间成本

当前会话的模型调用就跑在这台服务器上，停服窗口期间本会话拿不到 token。预算如下：

- 实测：从停服到 `/health` 回 `loaded: true` 约 165 s（engine 冷启动）；整机重启到可用同样约
  165 s。窗口成本按停服分钟计，不按"是否重启"计。
- 判据一：profiling 只需要停进程对，不需要重启机器。只有当 GPU 显存不释放、`nvidia-smi` 挂死
  或驱动报错时，才考虑重启。
- 判据二：`strata.service` 是 `enabled`，机器重启后 systemd 会自动起 `strata-0.2.7` 树的生产。
  服务不会丢，但跑的不是当前冻结候选——重启能保住"有服务"，保不住"同一个二进制"。
- 判据三：窗口硬截止 1800 s，看门狗提前接管点 900 s；目标单次停服 ≤ 15 分钟；第一阶段最多
  2 个窗口，第二阶段最多 3 个，超出即停止并复盘。
- 判据四：需要模型推理的准备工作必须在开窗前做完。窗口内的等待一律用 SSH + `curl` + 读日志，
  绝不通过 strata 轮询 strata。

## 当前事实（2026-10-30 实测）

- 服务形态：`strata.service` 为 `inactive`（`enabled`，`ExecStart=/opt/ai/bin/run-strata-official-027.sh`，
  起的是 `strata-0.2.7` 树加 `strata-0.2.7/strata-live.json`）。真正在服务的是手动启动的进程对：
  - server：`serve/server.py --engine strata --config /tmp/strata-freeze-candidate-long.json --port 8080`；
  - engine：它的子进程，argv 为 `strata --serve --pack ... --batch 6`，含 `--gpu 0,1`、
    `--layer-split 24`、`--kv int8`、`--max-context 262144`、`--prompt-cache-long`。
- 两个进程的 cwd 都是 `/opt/ai/src/strata-freeze-throughput-20261029T0812Z`，argv 里的
  `serve/server.py` 是相对路径。不先 `chdir` 就起不来，这就是 `ncu-exec.py` 必须补 cwd 的原因。
- 运行文件（md5）：
  - engine `/opt/ai/src/strata-freeze-throughput-20261029T0812Z/build75/strata` `8539fef58b8364b1451722fadf997a4b`
  - server `.../serve/server.py` `e23cd4b2d52617f9ac01f19eb79faa51`
  - config `/tmp/strata-freeze-candidate-long.json` `ebe04efaeacd880e54898d2ce4ae6175`（mode `0600`）
- engine 进程环境有 24 个 `STRATA_*` 开关（`STRATA_HC_Q8=1`、`STRATA_BF16_TC=1`、
  `STRATA_PIPED_CONCURRENT=1`、`STRATA_SM75_PERF=1`、`STRATA_EXP_MODE=8` 等），server 进程环境
  一个都没有。这些开关来自配置的 `env` 字段，由 server 注入 engine，所以快照必须同时抓两个进程。
- 机器：28 核、62 GB 内存（可用约 3 GB）、双 RTX 2080 Ti（sm_75，各用约 21 GB 显存）、
  Python 3.12.3、`nsys` 2025.3.2 在 PATH、`ncu` 只在 `/usr/local/cuda-13.0/bin/ncu`。
- profiling 权限：`/proc/sys/kernel/perf_event_paranoid=4`，`ncu` 属 root 且可执行。不用 root
  能否取到完整计数器由 Task 5 Step 1 现场判定，不预设结论。
- 目录 `/opt/ai/bin`、`/opt/ai/logs`、`/opt/ai/tmp`、`/opt/ai/engines` 均为 `atm` 可写，Task 0–5
  的脚本、日志和快照都不需要 sudo；sudo 只在重启兜底时用到，沿用既有脚本的 `sudo -S` 约定。
- 旧 Nsys 证据 `/opt/ai/logs/gpu-util-final-20261027T0848Z/probe.nsys-rep` 只有 GPU metrics，
  `nsys stats --report cuda_gpu_kern_sum` 取不到 kernel 表（2026-10-30 复核），所以受控 trace 是必经步骤。
- 32K prefill 基线（2026-10-27 记录）：SMs Active `77%`、SM Issue `13–14%`、Tensor `10%`、
  Warps `28%`、DRAM read `15%`、最大 `41%`。
- 工具：`probe-gpu-utilization.py`、`probe-version-stamp.py`、`mixed-soak.py` 已不依赖 `build/strata`
  路径，版本探针从运行中的 server argv 读真实路径；联合回归 `28 passed, 1 skipped`，现网 `problems=[]`。
- 已知坑：`cold-prefill.py` 取 engine 输出行的日志路径写死为
  `/opt/ai/src/strata-0.2.2/strata-022.log`，与冻结树的日志不同。用它的 "engine read" 数字前
  必须先核对日志路径，否则只能信 API 侧计时。
- 已知坑：`use-config.sh` 与 `restore-prod.sh` 的控制面是 `strata-0.2.2` 树加 systemd 重启，
  不适用于当前进程对拓扑，本计划不用它们做回滚。

## 恢复契约（自愈）

所有停服窗口共用同一套契约，脚本不依赖 agent 在线，也不依赖被停掉的 strata：

- `tools/server/util-restore.sh`：分层恢复。第一层 `relaunch` 用快照里的 runtime JSON 原样
  重启进程对；第二层 `systemd` 起 `strata.service` 兜底（另一棵树，只算"有服务"）；都不行则
  `none`。每次尝试都打印 `WINDOW RESTORED ts=... layer=relaunch|systemd|none`，并把层级写进
  `RESTORE_LAYER` 供调用方读取。
- `tools/server/util-watchdog.sh`：看门狗，参数 `RUN_TS DEADLINE_EPOCH GRACE_EPOCH`，默认
  硬截止 +1800 s、接管点 +900 s。它只认三件事：标记文件是否还在、`/health` 是否健康、
  profiler 或受控进程是否还在。驱动被 SIGKILL 时，看门狗在接管点之后主动恢复，最多 3 次。
- 标记文件 `/tmp/strata-util-window-in-progress` 由驱动创建、由驱动或看门狗删除；它不在就等于
  窗口已结束，看门狗随之退出。
- 快照里的 `server-runtime.json` 是冻结候选的启动参数，除 Task 2 外任何步骤都只读；
  `live-runtime.json` 是"当前应视为生产"的额外指针，只在 Task 7 候选安装成功后写入。
  回滚到冻结候选时要先删掉它，否则恢复脚本会继续重启候选。
- 只有 `/health` 回 `loaded: true` 且版本探针 `problems=[]` 才算恢复完成；层级不是 `relaunch`
  的恢复一律视为异常，必须先修自愈路径再继续测量。
- 人工兜底顺序见 `docs/SERVER_RECOVERY.md`；看门狗也救不回来时停止全部优化工作。

## 文件边界

Read:

- `tools/server/probe-gpu-utilization.py`
- `tools/server/probe-version-stamp.py`
- `tools/server/cold-prefill.py`
- `tools/server/bench-strata.py`
- `tools/server/parity_window.py`
- `tools/server/mixed-soak.py`
- `docs/SERVER_RECOVERY.md`
- `docs/STATE.md`
- `docs/ROADMAP.md`
- `docs/WORK_QUEUE.md`
- `docs/EVIDENCE_LEDGER.md`

Create:

- `tools/server/dump-runtime.py`
- `tools/server/tests/test_dump_runtime.py`
- `tools/server/util-restore.sh`
- `tools/server/util-watchdog.sh`
- `/opt/ai/logs/utilization-drill-$TS.log`（Task 0 演练记录）
- `/opt/ai/logs/ncu-selection-$TS.json`
- `/opt/ai/logs/utilization-top-kernels-$TS.txt`
- `/opt/ai/logs/utilization-trace-$TS.*`（仅报告缺少 kernel 表时）
- `/opt/ai/logs/utilization-decision-$TS.json`
- `/opt/ai/logs/ncu-util-$TS.*`
- `/opt/ai/tmp/pre-util-ncu-$TS/`
- `/opt/ai/src/strata-util-$TS/`（第二阶段独立源码树）
- `/opt/ai/engines/util-$TS/build/strata`（第二阶段独立部署二进制）
- `/opt/ai/tmp/util-candidate-$TS.json`（第二阶段候选配置）

Modify:

- `tools/server/ncu-exec.py`（补 cwd 还原，Task 0 Step 1）
- `tools/server/tests/test_ncu_exec.py`

Modify only after the decision gate selects one target:

- `src/prefill/gemm.cu`
- `src/prefill/moe_fused_iq.cu`
- `src/prefill/moe_fused.cu`
- `src/prefill/moe_mmq.cu`
- `src/prefill/prefill.cpp`
- `src/kernels/cuda/iq_kernels.cu`
- `src/kernels/cuda/qsa_prompt_attn.cu`
- `src/kernels/cuda/qsa_decode_attn.cu`
- `src/kernels/cuda/s2_expert_grouped.cu`
- `src/kernels/cuda/shared_expert.cu`

Tests to run according to the selected target:

- `src/prefill/gemm_bf16_parity.cu`
- `src/kernels/qsa_prompt_attn_parity.cpp`
- `src/kernels/qsa_decode_attn_parity.cpp`
- `src/kernels/iq_parity.cpp`
- `src/kernels/iq_multi_parity.cpp`
- `src/kernels/s2_expert_grouped_parity.cpp`
- `src/kernels/shared_expert_parity.cpp`
- `tools/server/tests/test_probe_gpu_utilization.py`

Never modify during NCU or candidate validation:

- `/opt/ai/src/strata-freeze-throughput-20261029T0812Z/build75/strata`（冻结生产二进制）
- `/tmp/strata-freeze-candidate-long.json`（只允许由 `util-restore.sh` 在缺失时按快照补回）
- `/etc/systemd/system/strata.service`

## Task 0：先把生产带回可自愈

**Files:**
- Modify: `tools/server/ncu-exec.py`
- Modify: `tools/server/tests/test_ncu_exec.py`
- Create: `tools/server/dump-runtime.py`、`tools/server/tests/test_dump_runtime.py`
- Create: `tools/server/util-restore.sh`、`tools/server/util-watchdog.sh`
- Create: `/opt/ai/bin/util-restore.sh`、`/opt/ai/bin/util-watchdog.sh`、`/opt/ai/bin/dump-runtime.py`
- Create: `/opt/ai/logs/utilization-drill-$TS.log`

这一步不做任何优化，只把"停服之后谁把生产带回来"这件事变成服务器上的独立进程。

- [ ] **Step 1: 先补 `ncu-exec.py` 的 cwd 缺口**

生产 argv 里的 `serve/server.py` 是相对路径，cwd 是冻结树。启动器不 `chdir` 就起不来，
而恢复脚本正是靠它重启进程对，所以这个缺口属于自愈路径，不属于采样阶段。

先在 `tools/server/tests/test_ncu_exec.py` 加失败测试：

```python
def test_load_runtime_validates_cwd(tmp_path: Path):
    ncu_exec = load_ncu_exec()
    path = tmp_path / "runtime.json"
    path.write_text(
        '{"argv": ["/bin/true"], "env": {"A": "1"}, "cwd": 42}',
        encoding="utf-8",
    )
    path.chmod(0o600)

    with pytest.raises(ValueError, match="cwd"):
        ncu_exec.load_runtime(path)


def test_main_restores_cwd_before_execvpe():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'os.chdir(data["cwd"])' in source
    assert source.index('os.chdir(data["cwd"])') < source.index("os.execvpe(")
```

```powershell
python -m pytest tools/server/tests/test_ncu_exec.py -q
```

Expected: 新断言 FAIL。

再改实现：`load_runtime` 返回 `(argv, env, cwd)` 三元组，并对非字符串且非 null 的 `cwd`
抛 `ValueError("cwd must be a string or null")`；`main` 在 `execvpe` 之前 `os.chdir(cwd)`：

```python
    command, environment, cwd = load_runtime(pathlib.Path(argv[0]))
    if cwd:
        os.chdir(cwd)
    os.execvpe(command[0], command, environment)
```

```powershell
python -m pytest tools/server/tests -q
python -m py_compile tools/server/ncu-exec.py
```

Expected: 全绿（Windows 允许 skip）；`py_compile` exit 0。

- [ ] **Step 2: 写运行参数抓取工具**

`tools/server/dump-runtime.py` 只做一件事：按 argv 正则找到进程，把它的 argv、env、cwd 落成
`0600` 的 JSON，形状与 `ncu-exec.py` 的输入一致。解析部分写成纯函数便于测试：

```python
def runtime_from(cmdline: bytes, environ: bytes, cwd: str) -> dict:
    """把 /proc 的原始字节转成 ncu-exec.py 需要的启动参数。"""
    argv = [part.decode("utf-8", "replace") for part in cmdline.split(b"\0") if part]
    env = {
        key.decode("utf-8", "replace"): value.decode("utf-8", "replace")
        for key, value in (item.split(b"=", 1) for item in environ.split(b"\0") if b"=" in item)
    }
    if not argv:
        raise ValueError("argv must be a non-empty string list")
    return {"argv": argv, "env": env, "cwd": cwd}
```

`tools/server/tests/test_dump_runtime.py` 至少覆盖：空 cmdline 抛错、`cwd` 落进 JSON、
输出文件权限为 `0600`。CLI 形如 `dump-runtime.py --pattern <正则> --out <路径>`。

- [ ] **Step 3: 写分层恢复脚本**

`tools/server/util-restore.sh`：

```bash
#!/bin/bash
# 现网恢复入口：无条件把生产带回健康状态，先按快照重启冻结候选，失败再退回 systemd。
#
# 为什么存在：停服窗口里跑的可能是受控 server/engine，驱动脚本也可能被 SIGKILL。
# 恢复不能依赖 agent 在线，也不能依赖被停掉的 strata，所以它必须是服务器上的独立脚本。
#
# 用法: util-restore.sh [SNAP_DIR]；省略时读 /tmp/strata-util-snapshot-path。
# 输出: WINDOW RESTORED ts=<TS> layer=relaunch|systemd|none
# 退出: 0 表示 /health 已 loaded=true；1 表示两层都没救回来。
set -u

TS=$(cat /tmp/strata-util-run-ts 2>/dev/null || true)
SNAP="${1:-$(cat /tmp/strata-util-snapshot-path 2>/dev/null || true)}"
CFG="/tmp/strata-freeze-candidate-long.json"
export RESTORE_LAYER=none
if [ -z "$SNAP" ]; then
  echo "WINDOW RESTORED ts=${TS:-?} layer=none reason=no_snapshot"; exit 1
fi

log() { printf '%s restore %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

wait_health() {
  for _ in $(seq 1 140); do
    H=$(curl -fsS -m 5 http://127.0.0.1:8080/health 2>/dev/null || true)
    case "$H" in *'"loaded": true'*) return 0 ;; esac
    sleep 3
  done
  return 1
}

clear_pair() {
  pkill -TERM -f "serve/[s]erver.py --engine" 2>/dev/null
  pkill -TERM -f "[s]trata --serve" 2>/dev/null
  sleep 5
  pkill -KILL -f "serve/[s]erver.py --engine" 2>/dev/null
  pkill -KILL -f "[s]trata --serve" 2>/dev/null
  return 0
}

relaunch() {
  [ -x "$SNAP/engine" ] || return 1
  # 冻结 runtime 永远保留作回滚；live-runtime 只在候选安装成功后出现，优先使用。
  RT="$SNAP/server-runtime.json"
  [ -f "$SNAP/live-runtime.json" ] && RT="$SNAP/live-runtime.json"
  [ -f "$RT" ] || return 1
  clear_pair
  # 配置在 /tmp 下，重启或清理后可能已经不在；缺了就按快照补回原路径。
  if [ ! -f "$CFG" ]; then
    [ -f "$SNAP/config.json" ] || return 1
    install -m 0600 "$SNAP/config.json" "$CFG" || return 1
  fi
  cd "$SNAP" || return 1
  nohup python3 /opt/ai/bin/ncu-exec.py "$RT" \
    >>/opt/ai/logs/utilization-restore.log 2>&1 &
  wait_health
}

systemd_fallback() {
  clear_pair
  echo 'p99832586' | sudo -S systemctl start strata.service >/dev/null 2>&1
  wait_health
}

if relaunch; then
  RESTORE_LAYER=relaunch
elif systemd_fallback; then
  RESTORE_LAYER=systemd
fi
log "layer=$RESTORE_LAYER ts=${TS:-?}"
echo "WINDOW RESTORED ts=${TS:-?} layer=$RESTORE_LAYER"
[ "$RESTORE_LAYER" = none ] && exit 1
exit 0
```

`relaunch` 只依赖快照里的三样东西：可执行的 engine、server 的 runtime JSON、以及必要时补回的
配置。它不猜参数、不猜 cwd、不猜那 24 个 `STRATA_*` 开关，全部照抓下来的样子重放。

- [ ] **Step 4: 写看门狗**

`tools/server/util-watchdog.sh`：

```bash
#!/bin/bash
# 停服窗口的独立看门狗：驱动脚本可能被 SIGKILL，恢复不能只靠驱动自己的 trap。
#
# 用法: util-watchdog.sh RUN_TS DEADLINE_EPOCH [TAKEOVER_EPOCH]
#   RUN_TS          窗口时间戳，同时是标记文件的内容
#   DEADLINE_EPOCH  硬截止（epoch 秒），到点仍未健康就强制恢复
#   TAKEOVER_EPOCH  接管点（epoch 秒），驱动被强杀时不早于此点抢恢复
set -u
TS="${1:-}"; DEADLINE="${2:-0}"; TAKEOVER="${3:-$(( $(date +%s) + 900 ))}"
[ -n "$TS" ] || exit 2
MARK="/tmp/strata-util-window-in-progress"

log() { printf '%s watchdog ts=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TS" "$*"; }

healthy() {
  H=$(curl -fsS -m 5 http://127.0.0.1:8080/health 2>/dev/null || true)
  case "$H" in *'"loaded": true'*) return 0 ;; esac
  return 1
}

busy() {
  pgrep -f "/opt/ai/bin/[n]cu-exec.py" >/dev/null 2>&1 && return 0
  pgrep -f "[n]sys profile" >/dev/null 2>&1 && return 0
  pgrep -f "/usr/local/cuda-13.0/bin/[n]cu " >/dev/null 2>&1 && return 0
  return 1
}

attempts=0
miss=0
while :; do
  NOW=$(date +%s)
  [ -f "$MARK" ] || { log "marker gone, exit"; exit 0; }
  if healthy; then
    [ "$NOW" -ge "$DEADLINE" ] && { log "healthy at deadline"; exit 0; }
    miss=0
  elif busy; then
    miss=0
  else
    miss=$((miss + 1))
    if [ "$NOW" -ge "$DEADLINE" ] || { [ "$NOW" -ge "$TAKEOVER" ] && [ "$miss" -ge 2 ]; }; then
      attempts=$((attempts + 1))
      if [ "$attempts" -gt 3 ]; then log "give up after $attempts attempts"; exit 1; fi
      log "force restore attempt=$attempts now=$NOW deadline=$DEADLINE"
      bash /opt/ai/bin/util-restore.sh "$(cat /tmp/strata-util-snapshot-path 2>/dev/null || true)"
      log "restore rc=$?"
      miss=0
    fi
  fi
  sleep 15
done
```

`busy` 只在确实看到受控进程时才返回 0，末尾必须是 `return 1`；否则看门狗会把"没人干活"
当成"还在干活"，永远不会接管。窗口刚停服、profiler 还没起来的那几秒也没有受控进程，
所以判定要连着两轮（30 s）落空且已过接管点才动手，见 `miss -ge 2`。

- [ ] **Step 5: 部署并核对哈希**

```sh
scp tools/server/ncu-exec.py tools/server/dump-runtime.py \
  tools/server/util-restore.sh tools/server/util-watchdog.sh \
  atm@192.168.9.103:/tmp/
ssh atm@192.168.9.103 'install -m 755 /tmp/ncu-exec.py /tmp/dump-runtime.py \
  /tmp/util-restore.sh /tmp/util-watchdog.sh /opt/ai/bin/ \
  && sha256sum /opt/ai/bin/ncu-exec.py /opt/ai/bin/dump-runtime.py \
     /opt/ai/bin/util-restore.sh /opt/ai/bin/util-watchdog.sh'
```

```powershell
Get-FileHash tools\server\ncu-exec.py, tools\server\dump-runtime.py, `
  tools\server\util-restore.sh, tools\server\util-watchdog.sh -Algorithm SHA256
```

Expected: 本地与远端 SHA-256 一致；`/opt/ai/bin` 与仓库的漂移由 `ops-check-tool-sync.sh` 兜底核对。

- [ ] **Step 6: 练习窗口（真实停服，必须用户授权）**

演练用一份一次性快照，练完就删；正式快照在 Task 2 再建。全程只通过 SSH 观察，不通过 strata：

```sh
ssh atm@192.168.9.103 'TS=$(date -u +%Y%m%dT%H%M%SZ)
SNAP=/opt/ai/tmp/util-drill-$TS; install -d -m 750 "$SNAP"
TREE=/opt/ai/src/strata-freeze-throughput-20261029T0812Z
cp -p "$TREE/build75/strata" "$SNAP/engine"
cp -p "$TREE/serve/server.py" "$SNAP/server.py"
install -m 600 /tmp/strata-freeze-candidate-long.json "$SNAP/config.json"
python3 /opt/ai/bin/dump-runtime.py --pattern "serve/[s]erver.py --engine" \
  --out "$SNAP/server-runtime.json"
python3 /opt/ai/bin/dump-runtime.py --pattern "[s]trata --serve --pack" \
  --out "$SNAP/engine-runtime.json"
sha256sum "$SNAP/engine" "$SNAP/server.py" "$SNAP/config.json" > "$SNAP/MANIFEST.sha256"
printf "%s\n" "$TS" > /tmp/strata-util-run-ts; chmod 600 /tmp/strata-util-run-ts
printf "%s\n" "$SNAP" > /tmp/strata-util-snapshot-path; chmod 600 /tmp/strata-util-snapshot-path
START=$(date +%s)
pkill -TERM -f "serve/[s]erver.py --engine"; pkill -TERM -f "[s]trata --serve"; sleep 5
echo "STOPPED at $(( $(date +%s) - START ))s"
bash /opt/ai/bin/util-restore.sh "$SNAP"; echo "restore rc=$?"
echo "RESTORED after $(( $(date +%s) - START ))s total"
python3 /opt/ai/bin/probe-version-stamp.py --local --out "/opt/ai/logs/utilization-drill-$TS.json"
curl -fsS -m 5 http://127.0.0.1:8080/health'
```

Expected: `WINDOW RESTORED ts=... layer=relaunch`；`/health` 回 `loaded: true`；版本探针
`problems=[]` 且 engine 路径仍是冻结树；总停服时长接近 165 s 量级。任一条不满足都不许开真实窗口。

- [ ] **Step 7: 演练看门狗救援**

同一次演练里再验一次"驱动被强杀"这条路：

```sh
ssh atm@192.168.9.103 'TS=$(cat /tmp/strata-util-run-ts)
printf "%s\n" "$TS" > /tmp/strata-util-window-in-progress; chmod 600 /tmp/strata-util-window-in-progress
NOW=$(date +%s)
setsid nohup bash /opt/ai/bin/util-watchdog.sh "$TS" $((NOW + 300)) $((NOW + 30)) \
  >>/opt/ai/logs/utilization-drill-$TS.log 2>&1 &
pkill -TERM -f "serve/[s]erver.py --engine"; pkill -TERM -f "[s]trata --serve"; sleep 5
sleep 45
echo "--- watchdog log ---"; tail -20 /opt/ai/logs/utilization-drill-$TS.log
curl -fsS -m 5 http://127.0.0.1:8080/health'
```

Expected: 看门狗在接管点之后、硬截止之前主动恢复，日志出现 `force restore attempt=1` 与
`WINDOW RESTORED ... layer=relaunch`；`/health` 回 `loaded: true`。演练结束后删掉标记文件，
让看门狗自行退出，并记录本次停服时长与恢复耗时。

- [ ] **Step 8: 落一份演练结论**

把停服秒数、恢复秒数、恢复层级、看门狗接管耗时写进 `docs/ITERATION_LOG.md`，并据实回答：

- 自愈路径是否已在现网跑通过一次（必须是 `layer=relaunch`）。
- 单次窗口成本是否落在预算内（目标 ≤ 15 分钟）。
- 是否需要重启机器才能继续（默认答案是不需要）。

任何一条答不上来，就停在 Task 0，不开真实窗口。

## Task 1：复核冻结基线和工具

**Files:**
- Read: `tools/server/probe-gpu-utilization.py`
- Read: `tools/server/probe-version-stamp.py`
- Read: `tools/server/mixed-soak.py`
- Test: `tools/server/tests/test_probe_gpu_utilization.py`
- Test: `tools/server/tests/test_probe_version_stamp.py`
- Test: `tools/server/tests/test_mixed_soak_ctx256.py`
- Test: `tools/server/tests/test_ncu_exec.py`
- Test: `tools/server/tests/test_dump_runtime.py`
- Create: `/opt/ai/logs/T-probe-util-baseline-$TS.json`

- [ ] **Step 1: 运行本地工具回归**

```powershell
python -m pytest tools/server/tests/test_probe_version_stamp.py `
  tools/server/tests/test_ncu_exec.py `
  tools/server/tests/test_dump_runtime.py `
  tools/server/tests/test_probe_gpu_utilization.py `
  tools/server/tests/test_mixed_soak_ctx256.py -q
python -m py_compile tools/server/probe-gpu-utilization.py
python -m py_compile tools/server/probe-version-stamp.py
python -m py_compile tools/server/mixed-soak.py
python -m py_compile tools/server/dump-runtime.py
```

Expected: 全绿（Windows 允许 skip，`serve/test_responses.py` 在 Windows 收集期即失败，属既有现象，
Linux 侧补跑）；`py_compile` exit 0。计数以实际输出为准，不沿用旧文档里的 `28 passed, 1 skipped`。

- [ ] **Step 2: 核对运行中的 engine/server/config**

```sh
TS=$(date -u +%Y%m%dT%H%M%SZ)
ssh atm@192.168.9.103 "python3 /opt/ai/bin/probe-version-stamp.py --local \
  --out \"/opt/ai/logs/T-probe-util-baseline-\$TS.json\""
```

Expected: `problems=[]`；engine 路径为
`/opt/ai/src/strata-freeze-throughput-20261029T0812Z/build75/strata`、md5 `8539fef5...`、
max context 262144、batch 6；config 取自 server argv 而不是猜出来的路径。

- [ ] **Step 3: 记录当前服务状态**

```sh
ssh atm@192.168.9.103 'systemctl show strata.service -p ActiveState -p UnitFileState -p NRestarts
pgrep -af "[s]trata --serve|serve/[s]erver.py --engine"
nsys sessions list
nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv,noheader'
```

Expected: service `inactive`（`enabled`，仅作兜底），恰好一个 server 和一个 engine，无 active
Nsight session。基线记录必须写清"服务由进程对承载"，不得写 `systemctl is-active` 为 active。

## Task 2：建立可回滚快照

**Files:**
- Read: `/opt/ai/src/strata-freeze-throughput-20261029T0812Z/build75/strata`
- Read: `/opt/ai/src/strata-freeze-throughput-20261029T0812Z/serve/server.py`
- Read: `/tmp/strata-freeze-candidate-long.json`
- Create: `/opt/ai/tmp/pre-util-ncu-$TS/`
- Create: `/tmp/strata-util-run-ts`、`/tmp/strata-util-snapshot-path`

- [ ] **Step 1: 快照运行文件和进程参数**

```sh
ssh atm@192.168.9.103 'TS=$(date -u +%Y%m%dT%H%M%SZ)
TREE=/opt/ai/src/strata-freeze-throughput-20261029T0812Z
SNAP="/opt/ai/tmp/pre-util-ncu-$TS"; install -d -m 750 "$SNAP"
cp -p "$TREE/build75/strata" "$SNAP/engine"
cp -p "$TREE/serve/server.py" "$SNAP/server.py"
install -m 600 /tmp/strata-freeze-candidate-long.json "$SNAP/config.json"
systemctl cat strata.service > "$SNAP/strata.service.txt"
python3 /opt/ai/bin/dump-runtime.py --pattern "serve/[s]erver.py --engine" \
  --out "$SNAP/server-runtime.json"
python3 /opt/ai/bin/dump-runtime.py --pattern "[s]trata --serve --pack" \
  --out "$SNAP/engine-runtime.json"
sha256sum "$SNAP/engine" "$SNAP/server.py" "$SNAP/config.json" > "$SNAP/MANIFEST.sha256"
printf "%s\n" "$TS" > /tmp/strata-util-run-ts; chmod 600 /tmp/strata-util-run-ts
printf "%s\n" "$SNAP" > /tmp/strata-util-snapshot-path; chmod 600 /tmp/strata-util-snapshot-path
sha256sum -c "$SNAP/MANIFEST.sha256"
ls -l "$SNAP"'
```

Expected: manifest 全项 OK；两个 runtime JSON 均为 `0600`；后续 Task 3–9 都从这两个临时指针读
同一个 `TS` 和 `SNAP`，不能重新生成时间戳。

- [ ] **Step 2: 核对快照能重放出当前进程对**

```sh
ssh atm@192.168.9.103 'SNAP=$(cat /tmp/strata-util-snapshot-path)
python3 - "$SNAP" <<PY
import json, pathlib, sys
snap = pathlib.Path(sys.argv[1])
for name in ("server-runtime.json", "engine-runtime.json"):
    data = json.loads((snap / name).read_text(encoding="utf-8"))
    assert data["argv"], name
    assert isinstance(data["env"], dict) and data["env"], name
    assert data["cwd"] == "/opt/ai/src/strata-freeze-throughput-20261029T0812Z", name
server = json.loads((snap / "server-runtime.json").read_text(encoding="utf-8"))
assert "--config" in server["argv"]
assert "/tmp/strata-freeze-candidate-long.json" in server["argv"]
engine = json.loads((snap / "engine-runtime.json").read_text(encoding="utf-8"))
assert any(k.startswith("STRATA_") for k in engine["env"]), "engine env 缺开关"
assert not (snap / "live-runtime.json").exists(), "阶段一生产就是冻结候选"
PY'
```

Expected: 断言全过。快照此时不应有 `live-runtime.json`。runtime JSON 可能含 API key 等服务
环境，只允许留在服务器临时快照里，不得 `cat`、打印、上传、提交或复制进日志；`ncu-exec.py`
会拒绝组/其他用户可读的 runtime 文件。

- [ ] **Step 3: 记录基线负载数字**

```sh
ssh atm@192.168.9.103 'python3 /opt/ai/bin/cold-prefill.py util-baseline 32768 3
python3 /opt/ai/bin/bench-strata.py util-baseline'
```

Expected: 拿到 3 次 32K cold prefill 的 wall 中位与 decode/并发数字，作为 Task 8 的比较基线。
engine 侧 "read" 数字只有在日志路径核对过之后才可用，否则只记录 API 侧计时并注明未验证。

## Task 3：从已有 Nsight 报告选出顶层 kernel

**Files:**
- Read: `/opt/ai/logs/gpu-util-final-20261027T0848Z/probe.nsys-rep`
- Create: `/opt/ai/logs/utilization-top-kernels-$TS.txt`
- Create: `/opt/ai/logs/ncu-selection-$TS.json`
- Copy: `/opt/ai/tmp/pre-util-ncu-$TS/selection.json`

- [ ] **Step 1: 导出 CUDA kernel 汇总**

```sh
ssh atm@192.168.9.103 'TS=$(cat /tmp/strata-util-run-ts)
nsys stats --report cuda_gpu_kern_sum \
  /opt/ai/logs/gpu-util-final-20261027T0848Z/probe.nsys-rep \
  > "/opt/ai/logs/utilization-top-kernels-$TS.txt"
wc -l "/opt/ai/logs/utilization-top-kernels-$TS.txt"
head -20 "/opt/ai/logs/utilization-top-kernels-$TS.txt"'
```

Expected: 2026-10-30 复核结果是这份报告不含 CUDA kernel 表，因此本步骤预期落空，直接进 Step 2。

- [ ] **Step 2: 补采带 trace 的 Nsys（必经）**

现有 `probe-gpu-utilization.py` 启动的是 GPU Metrics session，报告不保证含 CUDA kernel trace；
不能把 `nsys profile` 包在探针外面冒充 engine trace。先完成 Task 0 与 Task 4 的启动器改造，
再执行 Task 4 Step 2 的受控 trace 窗口，然后回到本 Task 的 Step 3 选择 kernel。

Expected: 只有受控 trace 生成的 `cuda_gpu_kern_sum` 能作为候选依据；trace 窗口失败时停止，
不进入 NCU。

- [ ] **Step 3: 选择前三个候选 kernel**

选择标准依次为：

1. GPU 时间占比最高。
2. 与 `gemm gate/up`、`qsa attn`、`gemm down` 或 decode expert GEMM 对应。
3. 能由现有 parity 测试覆盖，或能增加最小确定性测试。

把人工裁决写成以下结构，`kernel_regex` 必须是能唯一匹配所选 demangled kernel 的最小安全正则：

```json
{
  "run_ts": "20261030T...Z",
  "source_report": "/opt/ai/logs/utilization-trace-<TS>.nsys-rep",
  "top_kernels": [
    {
      "name": "完整 demangled kernel 名称",
      "duration_ns": 0,
      "phase": "prefill",
      "parity_test": "对应测试或待补测试"
    }
  ],
  "selected_index": 0,
  "kernel_regex": "唯一匹配所选 kernel 的最小正则",
  "selection_reason": "为何先诊断这一个 kernel"
}
```

```sh
ssh atm@192.168.9.103 'TS=$(cat /tmp/strata-util-run-ts); SNAP=$(cat /tmp/strata-util-snapshot-path)
SELECTION="/opt/ai/logs/ncu-selection-$TS.json"
python3 - "$SELECTION" "$TS" <<PY
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
ts = sys.argv[2]
data = json.loads(path.read_text(encoding="utf-8"))
candidates = data.get("top_kernels")
selected = data.get("selected_index")
regex = data.get("kernel_regex")
assert data.get("run_ts") == ts
assert isinstance(candidates, list) and 1 <= len(candidates) <= 3
assert isinstance(selected, int) and 0 <= selected < len(candidates)
assert isinstance(regex, str) and regex.strip()
PY
install -m 0640 "$SELECTION" "$SNAP/selection.json"
sha256sum "$SELECTION" "$SNAP/selection.json"'
```

Expected: 顶部 kernel 汇总非空；selection 含 1–3 个候选且只选中一个；日志文件与快照副本
SHA-256 一致。第一轮只让 `kernel_regex` 对应的 kernel 进入 NCU。

## Task 4：受控启动器与补采 trace

**Files:**
- Create: `/opt/ai/bin/util-trace-$TS.sh`
- Create: `/opt/ai/logs/utilization-trace-$TS.*`

旧版 Task 4 的启动器已在 `70fe75ed` 落地并部署。cwd 还原属于自愈路径，已在 Task 0 Step 1
用 TDD 完成；`util-restore.sh`、`util-watchdog.sh`、`dump-runtime.py` 也已在 Task 0 部署。
本 Task 不重复实现，只在开 trace 窗口前复核。

- [ ] **Step 1: 复核启动器与自愈工具已就位**

```powershell
python -m pytest tools/server/tests/test_ncu_exec.py `
  tools/server/tests/test_dump_runtime.py -q
python -m py_compile tools/server/ncu-exec.py tools/server/dump-runtime.py
```

```sh
ssh atm@192.168.9.103 'grep -c "os.chdir" /opt/ai/bin/ncu-exec.py
sha256sum /opt/ai/bin/ncu-exec.py /opt/ai/bin/dump-runtime.py \
  /opt/ai/bin/util-restore.sh /opt/ai/bin/util-watchdog.sh
systemctl is-active strata.service || true'
```

Expected: 测试全绿；`grep -c` 至少为 1，说明远端启动器已带 cwd 还原；四个工具的 SHA-256 与
本地 `Get-FileHash` 一致；`strata.service` 仍是 `inactive`，现网由进程对承载。

- [ ] **Step 2: 补采 trace 窗口（必经，见 Task 3 Step 2）**

前提：Task 0 练习窗口已通过；用户已授权本窗口。驱动 `/opt/ai/bin/util-trace-$TS.sh`：

```sh
ssh atm@192.168.9.103 'TS=$(cat /tmp/strata-util-run-ts)
cat > /opt/ai/bin/util-trace-$TS.sh <<EOF
#!/bin/bash
set -u
TS=\$(cat /tmp/strata-util-run-ts); SNAP=\$(cat /tmp/strata-util-snapshot-path)
TRACE="/opt/ai/logs/utilization-trace-\$TS"
exec >>"\$TRACE.log" 2>&1
echo "WINDOW STARTED ts=\$TS kind=trace"
NSYS_PID=""; NCU_PID=""; restore_layer=none
on_exit() {
  rc=\$?
  trap - EXIT
  set +e
  [ -n "\${NSYS_PID:-}" ] && kill -TERM "\$NSYS_PID" 2>/dev/null
  [ -n "\${NCU_PID:-}" ] && kill -TERM "\$NCU_PID" 2>/dev/null
  pkill -TERM -f "nsys profile"; pkill -TERM -f "/opt/ai/bin/[n]cu-exec.py"
  pkill -TERM -f "serve/[s]erver.py --engine"; pkill -TERM -f "[s]trata --serve"; sleep 5
  pkill -KILL -f "serve/[s]erver.py --engine"; pkill -KILL -f "[s]trata --serve"
  echo "RESTORE ATTEMPT ts=\$TS rc=\$rc"
  bash /opt/ai/bin/util-restore.sh "\$SNAP"; restore_layer=\${RESTORE_LAYER:-none}
  rm -f /tmp/strata-util-window-in-progress
  echo "WINDOW DONE ts=\$TS rc=\$rc layer=\$restore_layer"
  exit "\$rc"
}
trap on_exit EXIT
NOW=\$(date +%s)
setsid nohup bash /opt/ai/bin/util-watchdog.sh "\$TS" \$((NOW + 1800)) \$((NOW + 900)) \\
  >>"/opt/ai/logs/utilization-watchdog-\$TS.log" 2>&1 &
printf "%s\n" "\$TS" > /tmp/strata-util-window-in-progress
chmod 600 /tmp/strata-util-window-in-progress
pkill -TERM -f "serve/[s]erver.py --engine"
pkill -TERM -f "[s]trata --serve"
sleep 5
echo "GPU FREED"; nvidia-smi --query-gpu=index,memory.used --format=csv,noheader
nsys profile --trace=cuda,nvtx,osrt --target-processes all \\
  --output="\$TRACE" --force-overwrite=true \\
  python3 /opt/ai/bin/ncu-exec.py "\$SNAP/server-runtime.json" \\
  > "\$TRACE.nsys.log" 2>&1 &
NSYS_PID=\$!
ready=0
for i in \$(seq 1 140); do
  kill -0 "\$NSYS_PID" 2>/dev/null || break
  H=\$(curl -fsS -m 5 http://127.0.0.1:8080/health 2>/dev/null || true)
  case "\$H" in *\\\"loaded\\\": true*) ready=1; break ;; esac
  sleep 3
done
echo "CONTROLLED READY ready=\$ready"
[ "\$ready" -eq 1 ] || exit 1
python3 /opt/ai/bin/cold-prefill.py util-trace 32768 1 && echo "REQUEST DONE"
pkill -TERM -f "serve/[s]erver.py --engine" || true
pkill -TERM -f "[s]trata --serve" || true
for i in \$(seq 1 60); do kill -0 "\$NSYS_PID" 2>/dev/null || break; sleep 1; done
kill -TERM "\$NSYS_PID" 2>/dev/null || true
wait "\$NSYS_PID" 2>/dev/null || true
echo "PROFILER DONE"
test -s "\$TRACE.nsys-rep"
nsys stats --report cuda_gpu_kern_sum "\$TRACE.nsys-rep" \\
  > "/opt/ai/logs/utilization-top-kernels-\$TS.txt"
EOF
chmod 755 /opt/ai/bin/util-trace-$TS.sh
setsid nohup bash /opt/ai/bin/util-trace-$TS.sh >/dev/null 2>&1 </dev/null &'
```

窗口结束后核对：

```sh
ssh atm@192.168.9.103 'TS=$(cat /tmp/strata-util-run-ts)
grep -E "WINDOW (STARTED|DONE)|CONTROLLED READY|REQUEST DONE|PROFILER DONE|WINDOW RESTORED" \
  "/opt/ai/logs/utilization-trace-$TS.log"
head -20 "/opt/ai/logs/utilization-top-kernels-$TS.txt"
curl -fsS -m 5 http://127.0.0.1:8080/health'
```

Expected: 受控 server 就绪后只发送一个 32K 冷 prefill；trace 报告含 CUDA kernel 汇总；窗口以
`WINDOW RESTORED layer=relaunch` 与 `WINDOW DONE ... rc=0` 收尾；health `loaded=true`。恢复层级
不是 `relaunch`、或 trace 窗口失败时：停止，不得进入 NCU；失败时看门狗负责兜底。

## Task 5：独占窗口运行 NCU

**Files:**
- Read: `/opt/ai/tmp/pre-util-ncu-$TS/server-runtime.json`
- Read: `/opt/ai/tmp/pre-util-ncu-$TS/selection.json`
- Create: `/opt/ai/bin/util-ncu-$TS.sh`
- Create: `/opt/ai/logs/ncu-util-$TS.ncu-rep`、`.log`、`.csv`、`.driver.log`
- Create: `/opt/ai/logs/T-probe-util-post-ncu-$TS.json`

- [ ] **Step 1: 确认当前用户可采集硬件计数器**

```sh
ssh atm@192.168.9.103 '/usr/local/cuda-13.0/bin/ncu --version | tail -1
/usr/local/cuda-13.0/bin/ncu --list-sets | grep -Ei "SpeedOfLight|Occupancy|WarpStateStats"'
```

Expected: `ncu` 可运行且至少列出 `SpeedOfLight`、`Occupancy`、`WarpStateStats`。
`ncu` 不在默认 PATH，本 Task 一律用绝对路径。不能用 root 运行生产 server 来绕过权限；
权限不足时停止并先修复 profiling 权限。本步骤不停服。

- [ ] **Step 2: 获得用户授权，停服并证明 GPU 已释放**

停服 = 杀掉当前 serving pair（`systemctl stop strata.service` 对现网什么都不做，不得当停服步骤）：

```sh
ssh atm@192.168.9.103 'pkill -TERM -f "serve/[s]erver.py --engine"; pkill -TERM -f "[s]trata --serve"; sleep 5
pkill -KILL -f "serve/[s]erver.py --engine" 2>/dev/null || true
pkill -KILL -f "[s]trata --serve" 2>/dev/null || true
pgrep -af "[s]trata --serve|serve/[s]erver.py --engine" || echo "no serving pair left"
nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv'
```

Expected（只读核对，窗口本体在 Step 3）：无 server/engine；两卡显存占用显著下降。

- [ ] **Step 3: 分离驱动运行 NCU、等待就绪并发送唯一请求**

驱动 `/opt/ai/bin/util-ncu-$TS.sh`（与 trace 驱动同一契约，profiler 换成 NCU）：

```sh
ssh atm@192.168.9.103 'TS=$(cat /tmp/strata-util-run-ts)
TARGET_KERNEL=$(python3 - "$(cat /tmp/strata-util-snapshot-path)/selection.json" <<PY
import json
print(json.load(open("/opt/ai/tmp/pre-util-ncu-$TS/selection.json", encoding="utf-8"))["kernel_regex"])
PY
)
cat > /opt/ai/bin/util-ncu-$TS.sh <<EOF
#!/bin/bash
set -u
TS=\$(cat /tmp/strata-util-run-ts); SNAP=\$(cat /tmp/strata-util-snapshot-path)
TARGET_KERNEL="$TARGET_KERNEL"
exec >>"/opt/ai/logs/ncu-util-\$TS.log" 2>&1
echo "WINDOW STARTED ts=\$TS kind=ncu"
NCU_PID=""; restore_layer=none
on_exit() {
  rc=\$?
  trap - EXIT
  set +e
  [ -n "\${NCU_PID:-}" ] && kill -TERM "\$NCU_PID" 2>/dev/null
  pkill -TERM -f "/usr/local/cuda-13.0/bin/[n]cu " 2>/dev/null
  pkill -TERM -f "/opt/ai/bin/[n]cu-exec.py" 2>/dev/null
  pkill -TERM -f "serve/[s]erver.py --engine" 2>/dev/null
  pkill -TERM -f "[s]trata --serve" 2>/dev/null
  sleep 5
  pkill -KILL -f "/opt/ai/bin/[n]cu-exec.py" 2>/dev/null
  pkill -KILL -f "serve/[s]erver.py --engine" 2>/dev/null
  pkill -KILL -f "[s]trata --serve" 2>/dev/null
  echo "RESTORE ATTEMPT ts=\$TS rc=\$rc"
  bash /opt/ai/bin/util-restore.sh "\$SNAP"; restore_layer=\${RESTORE_LAYER:-none}
  rm -f /tmp/strata-util-window-in-progress
  echo "WINDOW DONE ts=\$TS rc=\$rc layer=\$restore_layer"
  exit "\$rc"
}
trap on_exit EXIT
NOW=\$(date +%s)
setsid nohup bash /opt/ai/bin/util-watchdog.sh "\$TS" \$((NOW + 1800)) \$((NOW + 900)) \\
  >>"/opt/ai/logs/utilization-watchdog-\$TS.log" 2>&1 &
printf "%s\n" "\$TS" > /tmp/strata-util-window-in-progress
chmod 600 /tmp/strata-util-window-in-progress
pkill -TERM -f "serve/[s]erver.py --engine"
pkill -TERM -f "[s]trata --serve"
sleep 5
echo "GPU FREED"; nvidia-smi --query-gpu=index,memory.used --format=csv,noheader
/usr/local/cuda-13.0/bin/ncu \\
  --target-processes all \\
  --replay-mode kernel \\
  --clock-control none \\
  --cache-control none \\
  --section SpeedOfLight \\
  --section Occupancy \\
  --section WarpStateStats \\
  --section MemoryWorkloadAnalysis \\
  --kernel-name-base demangled \\
  --kernel-name "regex:\$TARGET_KERNEL" \\
  --launch-skip 1 \\
  --launch-count 1 \\
  --export "/opt/ai/logs/ncu-util-\$TS" \\
  --force-overwrite \\
  python3 /opt/ai/bin/ncu-exec.py "\$SNAP/server-runtime.json" &
NCU_PID=\$!
ready=0
for i in \$(seq 1 140); do
  kill -0 "\$NCU_PID" 2>/dev/null || { tail -n 80 "/opt/ai/logs/ncu-util-\$TS.log"; exit 1; }
  H=\$(curl -fsS -m 5 http://127.0.0.1:8080/health 2>/dev/null || true)
  case "\$H" in *\\\"loaded\\\": true*) ready=1; break ;; esac
  sleep 3
done
echo "CONTROLLED READY ready=\$ready"
[ "\$ready" -eq 1 ] || exit 1
python3 /opt/ai/bin/cold-prefill.py ncu-util 32768 1 && echo "REQUEST DONE"
pkill -TERM -f "serve/[s]erver.py --engine" || true
pkill -TERM -f "[s]trata --serve" || true
for i in \$(seq 1 60); do kill -0 "\$NCU_PID" 2>/dev/null || break; sleep 1; done
kill -TERM "\$NCU_PID" 2>/dev/null || true
wait "\$NCU_PID" 2>/dev/null || true
echo "PROFILER DONE"
test -s "/opt/ai/logs/ncu-util-\$TS.ncu-rep"
EOF
chmod 755 /opt/ai/bin/util-ncu-$TS.sh
setsid nohup bash /opt/ai/bin/util-ncu-$TS.sh > "/opt/ai/logs/ncu-util-$TS.driver.log" 2>&1 </dev/null &'
```

禁止在前台 SSH 会话里直接运行驱动；SSH 断开不得影响窗口。

Expected: 受控 server 在 420 s 内 `loaded=true`；NCU 进程在请求期间存活；请求成功；驱动先终止
受控 engine/server 让 NCU 完成导出，再清理残留 profiler 进程，最后由 `util-restore.sh` 分层恢复
生产并落 `WINDOW RESTORED layer=...` 与 `WINDOW DONE`。看门狗同窗启动：驱动被强杀或 SSH 断连时，
看门狗在硬截止后接管恢复。

- [ ] **Step 4: token-free 轮询，核对报告和生产已经恢复**

轮询只用 SSH + curl + 读日志，绝不通过 strata 本身：

```sh
ssh atm@192.168.9.103 'TS=$(cat /tmp/strata-util-run-ts)
grep -E "WINDOW DONE|WINDOW RESTORED" "/opt/ai/logs/ncu-util-$TS.log" /opt/ai/logs/utilization-watchdog-$TS.log 2>/dev/null
curl -fsS -m 5 http://127.0.0.1:8080/health'
```

生产恢复后执行：

```sh
ssh atm@192.168.9.103 'TS=$(cat /tmp/strata-util-run-ts)
test -s "/opt/ai/logs/ncu-util-$TS.ncu-rep"
/usr/local/cuda-13.0/bin/ncu --import "/opt/ai/logs/ncu-util-$TS.ncu-rep" \
  --page details --csv > "/opt/ai/logs/ncu-util-$TS.csv"
python3 /opt/ai/bin/probe-version-stamp.py --local \
  --out "/opt/ai/logs/T-probe-util-post-ncu-$TS.json"
nsys sessions list'
```

Expected: 窗口或看门狗日志出现 `WINDOW RESTORED layer=relaunch` 且 `WINDOW DONE ... rc=0`；
`.ncu-rep` 非空；CSV 可导出；health `loaded=true`；T-probe `problems=[]`；无 active profiler session。

- [ ] **Step 5: 异常中断后的兜底恢复**

驱动被 SIGKILL、SSH 断连或看门狗尚未到硬截止时，生产可能仍停着——此时**不依赖 token 的看门狗
就是主恢复路径**，不是事后补救：

- 等待硬截止（默认 30 分钟）：看门狗自行清理受控进程并分层恢复；agent 只通过 SSH 轮询
  `/health` 与看门狗日志，不通过 strata 轮询；
- 硬截止已过且日志出现 `WINDOW RESTORED layer=relaunch|systemd`：生产已恢复，继续 Step 4；
- 出现 `layer=none` 或看门狗进程也不在了：执行下面的人工兜底；仍失败就停止全部优化工作，
  按 `docs/SERVER_RECOVERY.md` 排查。

```sh
ssh atm@192.168.9.103 'pkill -TERM -f "/opt/ai/bin/[n]cu-exec.py" 2>/dev/null || true
pkill -TERM -f "serve/[s]erver.py --engine" 2>/dev/null || true
pkill -TERM -f "[s]trata --serve" 2>/dev/null || true
sleep 5
bash /opt/ai/bin/util-restore.sh "$(cat /tmp/strata-util-snapshot-path)"'
```

Expected: 生产恢复（`RESTORE_LAYER` 非 none）后才允许进入 Task 6。

## Task 6：形成唯一根因决策

**Files:**
- Read: `/opt/ai/logs/ncu-util-$TS.ncu-rep`
- Create: `/opt/ai/logs/utilization-decision-$TS.json`
- Modify: `docs/EVIDENCE_LEDGER.md`
- Modify: `docs/ITERATION_LOG.md`

- [ ] **Step 1: 导出 NCU 明细**

```sh
ssh atm@192.168.9.103 'TS=$(cat /tmp/strata-util-run-ts)
/usr/local/cuda-13.0/bin/ncu --import "/opt/ai/logs/ncu-util-$TS.ncu-rep" \
  --page details --csv > "/opt/ai/logs/ncu-util-$TS.csv"'
```

Expected: CSV 含 kernel duration、SM throughput、occupancy、Tensor pipe、DRAM throughput
和 warp stall 分布。

- [ ] **Step 2: 只选择一个主因**

决策表：

| 主因 | 第一候选文件 | 允许的代码方向 |
|---|---|---|
| long scoreboard / memory dependency | `src/prefill/gemm.cu`、`src/prefill/moe_fused_iq.cu` | 预取、ILP、合并/向量访问、dequant+GEMM 融合 |
| barrier / MIO throttle | `src/kernels/cuda/qsa_prompt_attn.cu` | 减少同步、warp 分工、软件流水 |
| not selected / 低 occupancy | 对应 kernel 文件 | 降寄存器/共享内存压力，改善 tile 内并行 |
| Tensor pipe 为直接瓶颈 | P7 相关路径 | 仅在证据充分时恢复 P7，不直接进入 |
| launch gap / stage tail | `src/prefill/prefill.cpp` | 减少阶段串行、合并 launch、受控重叠 |
| decode expert latency | `src/kernels/cuda/iq_kernels.cu`、`qsa_decode_attn.cu` | 提高 decode 小 batch 的 ILP 和 locality |

Expected: 只勾选一个主因。若证据仍不足，判决为“未定”，不进入代码修改。

- [ ] **Step 3: 预注册代码门限**

```json
{
  "run_ts": "20261030T...Z",
  "decision": "long_scoreboard|barrier_mio|low_occupancy|tensor_pipe|launch_gap|decode_latency|undetermined",
  "ready_for_implementation": false,
  "selection_file": "/opt/ai/logs/ncu-selection-20261030T...Z.json",
  "target_kernel": "profiled kernel name",
  "target_counter": "primary NCU metric",
  "counter_baseline": 0.0,
  "counter_target": 0.0,
  "wall_baseline_s": 0.0,
  "wall_target_s": 0.0,
  "repetitions": 3,
  "regression_limit_pct": 3.0,
  "selected_file": "仓库相对路径，只能指向文件边界中的一项",
  "stall_evidence": "one paragraph copied from the NCU report"
}
```

```sh
ssh atm@192.168.9.103 'TS=$(cat /tmp/strata-util-run-ts); SNAP=$(cat /tmp/strata-util-snapshot-path)
DECISION="/opt/ai/logs/utilization-decision-$TS.json"
python3 - "$DECISION" "$TS" <<PY
import json, pathlib, sys
path, ts = pathlib.Path(sys.argv[1]), sys.argv[2]
data = json.loads(path.read_text(encoding="utf-8"))
allowed = {"long_scoreboard", "barrier_mio", "low_occupancy", "tensor_pipe", "launch_gap", "decode_latency"}
assert data.get("run_ts") == ts
assert data.get("decision") in allowed
assert data.get("ready_for_implementation") is True
assert data.get("target_kernel") and data.get("target_counter")
assert data.get("counter_target") != data.get("counter_baseline")
assert data.get("wall_baseline_s", 0) > 0
assert data.get("wall_target_s", 0) > 0
assert data.get("wall_target_s") <= data.get("wall_baseline_s") * 0.97
assert data.get("repetitions") == 3
assert data.get("selected_file")
assert data.get("stall_evidence")
PY
chmod 0440 "/opt/ai/logs/utilization-decision-$TS.json"
install -m 0440 "/opt/ai/logs/utilization-decision-$TS.json" "$SNAP/decision.json"'
```

Expected: 所有字段由 NCU 证据填写；`undetermined` 不能设置 `ready_for_implementation=true`，
不能进入 Task 7。门限必须在修改代码前生成，并同时保存在日志目录和本次快照中只读留存。

## 第二阶段启动门

Task 7–9 不在当前 NCU 前预写具体 kernel 逻辑。只有同时满足以下条件，才允许另写“NCU 后实施切片”
并修改代码：

- Task 5 的 NCU 报告、CSV 和唯一请求日志存在。
- Task 6 的判决不是 `undetermined`，且 `ready_for_implementation=true`。
- `target_counter`、`counter_baseline`、`counter_target`、`wall_baseline_s`、`wall_target_s` 已预注册。
- `selected_file` 只指向一个允许文件，预计改动只对应一个 stall 机制。
- 生产已恢复为当前冻结候选（`WINDOW RESTORED layer=relaunch`），T-probe 为 `problems=[]`。

## Task 7：实现一个代码切片（第二阶段）

**Files:**
- Modify: 仅修改 Task 6 `selected_file` 指向的一项源码
- Test: 对应的 parity 测试和最小新增回归测试
- Create: `/opt/ai/src/strata-util-$TS/`
- Create: `/opt/ai/engines/util-$TS/build/strata`
- Create: `/opt/ai/tmp/util-candidate-$TS.json`

- [ ] **Step 1: 通过第二阶段启动门并复制独立源码树**

独立树从**当前冻结候选树**复制，与现网 engine 同源，归因才可比：

```sh
set -eu
TS=$(cat /tmp/strata-util-run-ts)
SNAP=$(cat /tmp/strata-util-snapshot-path)
FREEZE=/opt/ai/src/strata-freeze-throughput-20261029T0812Z
DECISION="$SNAP/decision.json"
UTIL="/opt/ai/src/strata-util-$TS"
python3 - "$DECISION" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["ready_for_implementation"] is True
assert data["decision"] != "undetermined"
print(data["selected_file"])
PY
test ! -e "$UTIL"
rsync -a \
  --exclude '/build/' \
  --exclude '/build-*/' \
  --exclude '/build*/' \
  "$FREEZE/" "$UTIL/"
printf '%s\n' "$UTIL" > /tmp/strata-util-source-path
chmod 600 /tmp/strata-util-source-path
sha256sum "$FREEZE/build75/strata" > /tmp/strata-util-prod-engine.sha256
```

Expected: 启动门全部通过；独立树存在；冻结 engine 未改动；后续构建命令不出现
`$FREEZE/build75`。

- [ ] **Step 2: 只加入一个测试和一个源码改动**

先在本地写最小测试，再实现一个直接对应 Task 6 stall 的机制。把且只把选中的源码和测试文件同步
到独立树；文件名由决策文件决定，不能整树覆盖：

```sh
# 在本地仓库执行；尖括号中的路径由 decision.json 决定。
git diff --check
tar -C . -cf /tmp/strata-util-slice.tar "<selected_file>" "<test_file>"
scp /tmp/strata-util-slice.tar atm@192.168.9.103:/tmp/strata-util-slice.tar
ssh atm@192.168.9.103 \
  'UTIL=$(cat /tmp/strata-util-source-path); tar -C "$UTIL" -xf /tmp/strata-util-slice.tar'
```

Expected: diff 只含一个源码机制和对应测试。若测试检查的是当前缺失行为，构建后必须先看到该断言
失败；若只是性能改动而无法构造失败断言，必须在台账明确写“性能候选，parity 为行为门”，不能
伪造 TDD 红灯。

- [ ] **Step 3: 在独立树配置并构建 sm_75**

```sh
UTIL=$(cat /tmp/strata-util-source-path)
cmake -S "$UTIL" -B "$UTIL/build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES=75 \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.0/bin/nvcc \
  -DSTRATA_ENABLE_CUDA=ON \
  -DSTRATA_NATIVE_EXPERTS=ON \
  -DSTRATA_MMQ_KQUANTS=ON \
  -DSTRATA_Q6K_EXPERTS=ON \
  -DSTRATA_MMQ_TQ_OVERLAY=ON \
  -DSTRATA_BUILD_TESTS=ON \
  -DSTRATA_BUILD_CONVERSATION_TESTS=OFF \
  -DSTRATA_GGML_DIR=/opt/ai/src/llama.cpp-3cf03257
cmake --build "$UTIL/build" --target strata "<target_parity>" -j20
"$UTIL/build/strata" --version
sha256sum "$UTIL/build/strata"
```

Expected: Release / sm_75 构建 exit 0；版本 marker 与源码一致；记录构建产物 SHA-256。
构建与测试都不停服。

- [ ] **Step 4: 跑目标测试和全套 CTest**

```sh
UTIL=$(cat /tmp/strata-util-source-path)
ctest --test-dir "$UTIL/build" \
  --output-on-failure -R "<target_parity>" -j1
ctest --test-dir "$UTIL/build" \
  --output-on-failure -j1
```

Expected: 目标 parity 通过；全套无新增失败。已知缺少外部 Q2_0 PLE 资产的例外必须单独说明，
不能写成全测试绿。

- [ ] **Step 5: 按 pair 拓扑安装独立候选（一个停服窗口）**

`use-config.sh` 的控制面是 `strata-0.2.2` 树加 systemd 重启，不适用于当前 pair；候选安装改用
runtime JSON 换 config 后原样重启，仍由看门狗兜底：

```sh
set -eu
TS=$(cat /tmp/strata-util-run-ts)
SNAP=$(cat /tmp/strata-util-snapshot-path)
UTIL=$(cat /tmp/strata-util-source-path)
ENGINE_DIR="/opt/ai/engines/util-$TS/build"
CAND_CFG="/opt/ai/tmp/util-candidate-$TS.json"
install -d -m 0755 "$ENGINE_DIR"
install -m 0755 "$UTIL/build/strata" "$ENGINE_DIR/strata.new"
mv -f "$ENGINE_DIR/strata.new" "$ENGINE_DIR/strata"
sha256sum "$UTIL/build/strata" "$ENGINE_DIR/strata"

python3 - "$SNAP/config.json" "$ENGINE_DIR/strata" "$CAND_CFG" <<'PY'
import json
import pathlib
import sys

base_path, exe, out_path = map(pathlib.Path, (sys.argv[1], sys.argv[2], sys.argv[3]))
base = json.loads(base_path.read_text(encoding="utf-8"))
candidate = json.loads(base_path.read_text(encoding="utf-8"))
candidate["exe"] = exe
assert {
    key: value for key, value in candidate.items() if key != "exe"
} == {
    key: value for key, value in base.items() if key != "exe"
}
out_path.write_text(
    json.dumps(candidate, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY
install -m 0640 "$CAND_CFG" "$SNAP/candidate-config.json"

ssh atm@192.168.9.103 "TS=\$(cat /tmp/strata-util-run-ts); SNAP=\$(cat /tmp/strata-util-snapshot-path)
cat > /opt/ai/bin/util-candidate-install-\$TS.sh <<EOF
#!/bin/bash
set -u
TS=\$TS; SNAP=\$SNAP; CAND_CFG=\"/opt/ai/tmp/util-candidate-\$TS.json\"
exec >>\"/opt/ai/logs/util-candidate-install-\$TS.log\" 2>&1
echo \"WINDOW STARTED ts=\$TS kind=candidate-install\"
restore_layer=none
on_exit() {
  rc=\$?
  trap - EXIT
  set +e
  pkill -TERM -f \"serve/[s]erver.py --engine\"; pkill -TERM -f \"[s]trata --serve\"; sleep 5
  pkill -KILL -f \"serve/[s]erver.py --engine\"; pkill -KILL -f \"[s]trata --serve\"
  echo \"RESTORE ATTEMPT ts=\$TS rc=\$rc\"
  bash /opt/ai/bin/util-restore.sh \"\$SNAP\"; restore_layer=\${RESTORE_LAYER:-none}
  rm -f /tmp/strata-util-window-in-progress
  echo \"WINDOW DONE ts=\$TS rc=\$rc layer=\$restore_layer\"
  exit \"\$rc\"
}
trap on_exit EXIT
NOW=\$(date +%s)
setsid nohup bash /opt/ai/bin/util-watchdog.sh \"\$TS\" \$((NOW + 1800)) \$((NOW + 900)) \\
  >>\"/opt/ai/logs/utilization-watchdog-\$TS.log\" 2>&1 &
printf \"%s\n\" \"\$TS\" > /tmp/strata-util-window-in-progress
chmod 600 /tmp/strata-util-window-in-progress
pkill -TERM -f \"serve/[s]erver.py --engine\"; pkill -TERM -f \"[s]trata --serve\"; sleep 5
python3 - \"\$SNAP/server-runtime.json\" \"\$CAND_CFG\" <<'EOP'
import json, sys
runtime = json.load(open(sys.argv[1], encoding=\"utf-8\"))
runtime[\"argv\"][runtime[\"argv\"].index(\"--config\") + 1] = sys.argv[2]
json.dump(runtime, open(\"/tmp/strata-util-candidate-runtime.json\", \"w\", encoding=\"utf-8\"), ensure_ascii=False)
EOP
chmod 600 /tmp/strata-util-candidate-runtime.json
python3 /opt/ai/bin/ncu-exec.py /tmp/strata-util-candidate-runtime.json &
CONTROLLED_PID=\$!
ready=0
for i in \$(seq 1 140); do
  kill -0 \"\$CONTROLLED_PID\" 2>/dev/null || break
  H=\$(curl -fsS -m 5 http://127.0.0.1:8080/health 2>/dev/null || true)
  case \"\$H\" in *\\\"loaded\\\": true*) ready=1; break ;; esac
  sleep 3
done
echo \"CANDIDATE READY ready=\$ready\"
EOF
chmod 755 /opt/ai/bin/util-candidate-install-\$TS.sh
setsid nohup bash /opt/ai/bin/util-candidate-install-\$TS.sh >/dev/null 2>&1 </dev/null &"
```

安装后核对（成功后把候选记成 `live-runtime.json` 并重新拉起，作为后续窗口的恢复目标）：

```sh
ssh atm@192.168.9.103 'TS=$(cat /tmp/strata-util-run-ts)
grep -E "CANDIDATE READY|WINDOW (STARTED|DONE)|WINDOW RESTORED" "/opt/ai/logs/util-candidate-install-$TS.log"
sha256sum -c /tmp/strata-util-prod-engine.sha256'
```

```sh
ssh atm@192.168.9.103 'TS=$(cat /tmp/strata-util-run-ts); SNAP=$(cat /tmp/strata-util-snapshot-path)
ENGINE=/opt/ai/engines/util-$TS/build/strata
if ! grep -q "CANDIDATE READY ready=1" "/opt/ai/logs/util-candidate-install-$TS.log"; then
  echo "candidate install failed; watchdog already restored the frozen pair"
  exit 1
fi
cp -p /tmp/strata-util-candidate-runtime.json "$SNAP/live-runtime.json"
chmod 600 "$SNAP/live-runtime.json"
pkill -TERM -f "serve/[s]erver.py --engine" || true
pkill -TERM -f "[s]trata --serve" || true
sleep 5
bash /opt/ai/bin/util-restore.sh "$SNAP"
[ "${RESTORE_LAYER:-none}" = relaunch ] || { echo "candidate relaunch failed"; exit 1; }
python3 /opt/ai/bin/probe-version-stamp.py --local \
  --out "/opt/ai/logs/T-probe-util-candidate-$TS.json"
python3 - "/opt/ai/logs/T-probe-util-candidate-$TS.json" "$ENGINE" <<PY
import json, sys
stamp = json.load(open(sys.argv[1], encoding="utf-8"))
assert stamp["problems"] == []
assert stamp["health"]["loaded"] is True
assert "/opt/ai/engines/util-" in stamp["running"]["engine"]["binary"]
assert stamp["running"]["engine"]["binary"].endswith("/build/strata")
PY
echo "candidate is live; watchdog now restores the candidate"'
```

Expected: 候选二进制只出现在 `/opt/ai/engines/util-$TS/build/strata`；候选配置除绝对 `exe` 外
与生产快照一致；`server-runtime.json` 仍是冻结候选的参数，候选只写进 `live-runtime.json`；
重新拉起后 T-probe 的 running engine 精确指向候选；冻结 engine 的 SHA-256 未变。安装窗口失败时
看门狗/恢复脚本已把生产带回冻结候选，不得带着未知状态继续测量。重拉阶段若 SSH 断开，
`live-runtime.json` 已经写好，重跑 `bash /opt/ai/bin/util-restore.sh "$SNAP"` 即可拉回候选。

## Task 8：硬件与端到端验收

**Files:**
- Read: `/opt/ai/logs/ncu-util-$TS.ncu-rep`
- Read: `/opt/ai/logs/T-probe-util-candidate-$TS.json`
- Create: `/opt/ai/logs/gpu-util-util-$TS/`
- Create: `/opt/ai/logs/cold-prefill.jsonl`
- Create: `/opt/ai/logs/P2-mixed-soak-util-$TS.*`

- [ ] **Step 0: 确认测量对象是已安装候选**

```sh
ssh atm@192.168.9.103 'TS=$(cat /tmp/strata-util-run-ts)
python3 - "/opt/ai/logs/T-probe-util-candidate-$TS.json" <<PY
import json, sys
stamp = json.load(open(sys.argv[1], encoding="utf-8"))
assert stamp["problems"] == []
assert stamp["health"]["loaded"] is True
assert "/opt/ai/engines/util-" in stamp["running"]["engine"]["binary"]
PY'
```

Expected: T-probe 无问题，health loaded，running engine path 精确指向独立候选。路径不符时不得
开始测量，先重新安装候选。

- [ ] **Step 1: 同负载复测硬件计数器**

```sh
TS=$(cat /tmp/strata-util-run-ts)
ssh atm@192.168.9.103 "sudo /opt/ai/bin/probe-gpu-utilization.py \
  --profile mixed --long-tokens 32768 --run-id util-$TS"
```

Expected: `status=verified`、`problems=[]`；目标计数器达到预注册值，且不是只有单次峰值改善。

- [ ] **Step 2: 比较 3 次 cold prefill**

```sh
ssh atm@192.168.9.103 'python3 /opt/ai/bin/cold-prefill.py util-candidate 32768 3'
```

Expected: 中位 wall 或 engine read 改善 ≥3%；若硬件计数改善但 wall 没改善，候选不采纳。

- [ ] **Step 3: 检查 decode 与并发回归**

```sh
ssh atm@192.168.9.103 'python3 /opt/ai/bin/bench-strata.py util-candidate'
```

Expected: 单路 decode 和 4 路聚合均不劣于基线 3%；接受率和输出质量不退化。2/8 路凹点属于
UTIL-MULTI，不在本计划范围，但不得因本候选恶化。

- [ ] **Step 4: 运行一小时 mixed soak**

```sh
TS=$(cat /tmp/strata-util-run-ts)
ssh atm@192.168.9.103 "python3 /opt/ai/bin/mixed-soak.py \
  --profile ctx256 --duration 3600 \
  --out /opt/ai/logs/P2-mixed-soak-util-$TS.jsonl"
```

Expected: 0 错误、0 意外 length、needle 全对、6 槽不降级、swap 未用，RAM 与显存不低于既有安全线。

## Task 9：判决、恢复与落盘

**Files:**
- Modify: `docs/EVIDENCE_LEDGER.md`
- Modify: `docs/ITERATION_LOG.md`
- Modify: `docs/STATE.md`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/WORK_QUEUE.md`

- [ ] **Step 1: 记录通过、失败和未验证项**

台账必须分别写清：

- 目标计数器是否改善。
- 32K wall 是否改善。
- decode/4 路是否回退。
- parity、needle、soak 是否通过。
- 每个窗口的停服时长、恢复耗时与恢复层级（`relaunch|systemd|none`）。
- 哪些负载、kernel 或硬件状态没有测。

- [ ] **Step 2: 决定候选去留**

候选必须同时满足硬件计数、32K wall、decode/4 路回归门、parity/needle 和一小时 soak，才把
`ACCEPT` 设为 `1`；任一失败都设为 `0`。只改善计数器但任务更慢时判为否决。

- [ ] **Step 3: 按判决保留候选或恢复冻结候选**

```sh
set -eu
TS=$(cat /tmp/strata-util-run-ts)
SNAP=$(cat /tmp/strata-util-snapshot-path)
ACCEPT=${ACCEPT:?set ACCEPT to 0 or 1 after the gates}
case "$ACCEPT" in
  0|1) ;;
  *) echo "ACCEPT must be 0 or 1" >&2; exit 2 ;;
esac

if [ "$ACCEPT" -eq 0 ]; then
  ssh atm@192.168.9.103 "rm -f '$SNAP/live-runtime.json'
  pkill -TERM -f 'serve/[s]erver.py --engine' || true
  pkill -TERM -f '[s]trata --serve' || true; sleep 5
  bash /opt/ai/bin/util-restore.sh '$SNAP'"
fi

if [ "$ACCEPT" -eq 1 ]; then
  EXPECT_ENGINE=$(ssh atm@192.168.9.103 'md5sum "/opt/ai/engines/util-'"$TS"'/build/strata" | awk "{print \$1}"')
else
  EXPECT_ENGINE=$(ssh atm@192.168.9.103 'md5sum "$(cat /tmp/strata-util-snapshot-path)/engine"' | awk '{print $1}')
fi
ssh atm@192.168.9.103 "TS=\$(cat /tmp/strata-util-run-ts); SNAP=\$(cat /tmp/strata-util-snapshot-path)
sha256sum -c \"\$SNAP/MANIFEST.sha256\"
python3 /opt/ai/bin/probe-version-stamp.py --local \
  --expect-engine-md5 '$EXPECT_ENGINE' \
  --out \"/opt/ai/logs/T-probe-util-final-\$TS.json\"
curl -fsS -m 5 http://127.0.0.1:8080/health
nsys sessions list"
```

Expected: `ACCEPT=1` 时 running engine 指向独立候选；`ACCEPT=0` 时回到冻结候选。两种情况都
必须 health `loaded=true`、T-probe `problems=[]`、快照 manifest 全项 OK、无残留 profiler session；
冻结 engine 与回退资产（`strata-freeze-throughput-20261029T0812Z` 树）始终未变。

- [ ] **Step 4: 清除临时运行环境和路径指针**

只有在 Task 0/4/5/7–9 全部结束、没有后续 profiler 需求时执行：

```sh
ssh atm@192.168.9.103 'SNAP=$(cat /tmp/strata-util-snapshot-path)
shred -u "$SNAP/server-runtime.json" "$SNAP/engine-runtime.json" \
  "$SNAP/live-runtime.json" 2>/dev/null || true
shred -u /tmp/strata-util-candidate-runtime.json 2>/dev/null || true
rm -f /tmp/strata-util-run-ts /tmp/strata-util-snapshot-path \
  /tmp/strata-util-source-path /tmp/strata-util-prod-engine.sha256 \
  /tmp/strata-util-window-in-progress'
```

Expected: 含运行环境值的 runtime JSON 被安全删除；快照中的 engine/server/config、manifest、
selection 和 decision 保留，供审计与回滚。

## 停止条件

- NCU 不能取得完整计数器或需要 root 运行生产 server：停止，先修 profiling 权限。
- 顶层 kernel 无法超过 3% 的证据噪声：停止，不进入代码修改。
- stall 证据指向多个同等主因：只保留一个，其余另立计划。
- 代码候选只提高计数器但 wall 无改善：回退，不继续叠改。
- 出现输出变化、needle 失败、OOM、swap 或槽位降级：立即回退生产。
- 看门狗触发过恢复、恢复层级落到 `systemd`/`none`，或练习窗口没有以 `layer=relaunch` 收尾：
  停止，先修自愈路径，再开真实窗口。
- 窗口被强杀后看门狗也没能把生产带回健康：停止全部优化工作，按 `docs/SERVER_RECOVERY.md` 排查。

## 最终验收门

- 选定 kernel 的主计数器中位改善超过两次基线之间的噪声。
- 同一 32K cold prefill 的 wall 或 engine read 改善 ≥3%。
- 单路 decode 和 4 路聚合不劣于冻结基线 3%。
- parity、needle、目标测试和一小时 `ctx256` soak 通过。
- 每个停服窗口都以 `layer=relaunch` 恢复收尾；停服时长与恢复耗时记录在案。
- 文档明确区分已验证收益、未验证项和残余风险。
