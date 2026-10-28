# 资源利用率优化计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 NCU 找到 32K cold prefill 和 decode 的真实 stall，改造一个瓶颈内核或调度路径，
提高硬件管线利用率并缩短端到端任务时间。

**Architecture:** 先冻结当前 `0.1.51` / `c219fbca` 生产基线并从已有 Nsight run 选出顶层 kernel，再短暂停止生产，
用快照启动受控 server/engine 并独占 GPU 运行 NCU，结束后无条件恢复生产。只有在 stall 证据、
目标计数器和 wall 门限全部预注册后，才允许在独立源码树改动一个代码切片。参数 A/B 仅验证代码
候选，不作为探索手段。

**Tech Stack:** Ubuntu、systemd、CUDA 13、sm_75、Nsight Systems、Nsight Compute、CMake、
Python 3、Strata `tools/server` 探针、JSON 证据。

---

## 任务定约

Objective:

- 找到 32K cold prefill 中低 SM Issue、低 Tensor 和低 warp residency 的直接原因。
- 只改一个能缓解主要 stall 的内核或调度路径。
- 同时改善目标硬件计数器和端到端 wall，不把计数器上升误当成用户收益。

Non-goals:

- 不做 env、batch、slot、lead-in、expert slot 等独立参数扫描。
- 不在 NCU 证明 Tensor 是直接瓶颈前恢复 P7。
- 不重构无关模块，不一次修改多个 kernel，不改变输出语义。
- 不把 NVML、`SMs Active` 或任一单项计数单独称为“真实利用率”。

Scope:

- Allowed: `src/prefill/`、`src/kernels/cuda/` 中的单个瓶颈路径，必要的最小调度代码，
  对应 parity 测试和 `tools/server` 观测工具。
- Out of scope: 模型格式、tokenizer、API 行为、生产配置和无关性能开关。

Verification:

- 编译、目标测试、greedy parity、needle 和资源稳定性门通过。
- 同一 32K cold prompt 的 NCU/Nsys 计数与端到端 wall 同向改善。
- 候选通过一小时 mixed soak 后才能进入生产冻结点。

Risks / Unknowns:

- 单实例几乎占满两卡显存，NCU 需要停止生产并启动受控 server/engine。
- NCU replay 会改变执行时序，必须关闭时钟锁定和缓存清空，并保留原始 Nsys 对照。
- stall 类型决定代码路径；在没有 NCU 证据前不预设 kernel 结论。

**阶段边界：**

- **第一阶段（Task 1–6）先完成只读准备，停服采样必须再次获得用户明确授权**：完成基线、快照、
  Nsight kernel 选择、受控 NCU、唯一根因判决和代码门限预注册；结束时生产必须已恢复。
- **第二阶段（Task 7–9）只能在 Task 6 产出唯一根因和已保存门限后启动**。具体改哪个函数、怎样
  重排 tile、同步或拷贝，必须在 NCU 报告到达后另写实施切片；不得提前猜测或把多个机制绑在一起。

## 当前事实

- 生产源码：`/opt/ai/src/strata-027-picks`，干净停在 `c219fbca`。
- 生产 engine：`/opt/ai/src/strata-027-picks/build75-027/strata`，`0.1.51`，
  md5 `0e4a959662205d3fd18e6283c3b8f2e5`。
- 生产 server：`/opt/ai/src/strata-027-picks/serve/server.py`，
  md5 `11fd33f0a7bbc2738cc3104fb959ae19`。
- 生产 config：`/opt/ai/src/strata-027-picks/strata-live.json`，
  md5 `023663ba9d96f7e3e26821bf9eeb0fdf`。
- 生产形态：`6×256K`、`parallel=6`、INT8 KV、双 RTX 2080 Ti、sm_75、
  `batch_slots=6`、`expert_slots=17280`。
- 32K prefill 基线：SMs Active `77%`、SM Issue `13–14%`、Tensor `10%`、
  Warps `28%`、DRAM read `15%`、最大 `41%`。
- 有效 Nsys 证据：`/opt/ai/logs/gpu-util-final-20261027T0848Z/`。
- 较早对照：`/opt/ai/logs/gpu-util-20261027T0850Z/`。
- 当前工具：`probe-gpu-utilization.py`、`mixed-soak.py` 与 `probe-version-stamp.py` 已不再依赖
  `build/strata` 路径；版本探针从运行中的 server argv 读取 engine、server 和 config 的实际路径。
  工具联合回归为 `28 passed, 1 skipped`，并已在现网得到 `problems=[]`。

## 文件边界

Read:

- `tools/server/probe-gpu-utilization.py`
- `tools/server/probe-version-stamp.py`
- `tools/server/cold-prefill.py`
- `tools/server/bench-strata.py`
- `tools/server/parity_window.py`
- `tools/server/mixed-soak.py`
- `tools/server/use-config.sh`
- `tools/server/restore-prod.sh`
- `docs/STATE.md`
- `docs/ROADMAP.md`
- `docs/WORK_QUEUE.md`
- `docs/EVIDENCE_LEDGER.md`

Create:

- `tools/server/ncu-exec.py`
- `tools/server/tests/test_ncu_exec.py`
- `/opt/ai/logs/ncu-selection-$TS.json`
- `/opt/ai/logs/utilization-top-kernels-$TS.txt`
- `/opt/ai/logs/utilization-trace-$TS.*`（仅报告缺少 kernel 表时）
- `/opt/ai/logs/utilization-decision-$TS.json`
- `/opt/ai/logs/ncu-util-$TS.*`
- `/opt/ai/tmp/pre-util-ncu-$TS/`
- `/opt/ai/src/strata-util-$TS/`（第二阶段独立源码树）
- `/opt/ai/engines/util-$TS/build/strata`（第二阶段独立部署二进制）
- `/opt/ai/tmp/util-candidate-$TS.json`（第二阶段候选配置）

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

- `/opt/ai/src/strata-027-picks/build75-027/strata`（冻结生产二进制）
- `/opt/ai/src/strata-027-picks/strata-live.json`（只允许通过 `use-config.sh` 安装已保存候选）
- `/etc/systemd/system/strata.service`

## Task 1：复核冻结基线和工具

**Files:**
- Read: `tools/server/probe-gpu-utilization.py`
- Read: `tools/server/probe-version-stamp.py`
- Read: `tools/server/mixed-soak.py`
- Test: `tools/server/tests/test_probe_gpu_utilization.py`
- Test: `tools/server/tests/test_probe_version_stamp.py`
- Test: `tools/server/tests/test_mixed_soak_ctx256.py`
- Create: `/opt/ai/logs/T-probe-util-baseline-$TS.json`

- [x] **Step 1: 运行本地工具回归**

```powershell
python -m pytest tools/server/tests/test_probe_version_stamp.py `
  tools/server/tests/test_ncu_exec.py `
  tools/server/tests/test_probe_gpu_utilization.py `
  tools/server/tests/test_mixed_soak_ctx256.py -q
python -m py_compile tools/server/probe-gpu-utilization.py
python -m py_compile tools/server/probe-version-stamp.py
python -m py_compile tools/server/mixed-soak.py
```

Expected: `28 passed, 1 skipped`，`py_compile` exit 0。

- [x] **Step 2: 核对运行中的 engine/server/config**

```sh
TS=$(date -u +%Y%m%dT%H%M%SZ)
python3 /opt/ai/bin/probe-version-stamp.py --local \
  --out "/opt/ai/logs/T-probe-util-baseline-$TS.json"
```

Expected: `problems=[]`；engine `0.1.51`、md5 `0e4a9596...`、batch slots 6、
expert slots 17280、max context 262144。2026-10-28 已实测通过：探针识别
`build75-027/strata`，并从 server argv 取到 `/opt/ai/src/strata-027-picks/strata-live.json`；
输出保留在 `/opt/ai/logs/T-probe-path-20261028.json`。

- [x] **Step 3: 记录当前服务状态**

```sh
systemctl show strata.service \
  -p ActiveState -p SubState -p NRestarts -p MainPID
pgrep -af '[s]trata --serve|serve/[s]erver.py --engine'
nsys sessions list
```

Expected: service `active/running`，仅一个 server 和一个 engine，无 active Nsight session。

## Task 2：建立可回滚快照

**Files:**
- Read: `/opt/ai/src/strata-027-picks/build75-027/strata`
- Read: `/opt/ai/src/strata-027-picks/serve/server.py`
- Read: `/opt/ai/src/strata-027-picks/strata-live.json`
- Create: `/opt/ai/tmp/pre-util-ncu-$TS/`
- Create: `/tmp/strata-util-run-ts`

- [ ] **Step 1: 由用户完成 sudo 授权**

```sh
sudo -v
```

Expected: 授权成功；口令不写入脚本、日志或仓库。

- [ ] **Step 2: 快照运行文件和进程参数**

```sh
TS=$(date -u +%Y%m%dT%H%M%SZ)
SNAP="/opt/ai/tmp/pre-util-ncu-$TS"
sudo install -d -o "$(id -u)" -g "$(id -g)" -m 0750 "$SNAP"
sudo cp -p /opt/ai/src/strata-027-picks/build75-027/strata "$SNAP/engine"
sudo cp -p /opt/ai/src/strata-027-picks/serve/server.py "$SNAP/server.py"
sudo cp -p /opt/ai/src/strata-027-picks/strata-live.json "$SNAP/strata-live.json"
sudo systemctl cat strata.service > "$SNAP/strata.service.txt"
sha256sum "$SNAP/engine" "$SNAP/server.py" "$SNAP/strata-live.json" \
  > "$SNAP/MANIFEST.sha256"
printf '%s\n' "$TS" > /tmp/strata-util-run-ts
chmod 600 /tmp/strata-util-run-ts
printf '%s\n' "$SNAP" > /tmp/strata-util-snapshot-path
chmod 600 /tmp/strata-util-snapshot-path
```

Expected: `sha256sum -c "$SNAP/MANIFEST.sha256"` 全项 OK；后续 Task 3–9 都从这两个
临时指针读取同一个 `TS` 和 `SNAP`，不能重新生成时间戳。

- [ ] **Step 3: 保存 server 的真实 argv 和受限 env**

```sh
SERVER_PID=$(pgrep -f 'serve/[s]erver.py --engine' | head -1)
python3 - "$SNAP" "$SERVER_PID" <<'PY'
import json
import pathlib
import sys

snap = pathlib.Path(sys.argv[1])
pid = sys.argv[2]
argv = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
env = pathlib.Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
runtime = {
    "argv": [part.decode("utf-8", "replace") for part in argv if part],
    "env": {
        key.decode("utf-8", "replace"): value.decode("utf-8", "replace")
        for key, value in (item.split(b"=", 1) for item in env if b"=" in item)
    },
}
(snap / "server-runtime.json").write_text(
    json.dumps(runtime, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY
chmod 600 "$SNAP/server-runtime.json"
```

Expected: `server-runtime.json` 非空且 mode `0600`。该文件可能含 API key 等服务环境，只允许留在
服务器临时快照中；不得 `cat`、打印、上传、提交或复制进日志。Task 4 的启动器必须拒绝组/其他用户
可读的 runtime 文件。

## Task 3：从已有 Nsight 报告选出顶层 kernel

**Files:**
- Read: `/opt/ai/logs/gpu-util-final-20261027T0848Z/probe.nsys-rep`
- Create: `/opt/ai/logs/utilization-top-kernels-$TS.txt`
- Create: `/opt/ai/logs/ncu-selection-$TS.json`
- Copy: `/opt/ai/tmp/pre-util-ncu-$TS/selection.json`

- [ ] **Step 1: 导出 CUDA kernel 汇总**

```sh
TS=$(cat /tmp/strata-util-run-ts)
nsys stats --report cuda_gpu_kern_sum \
  /opt/ai/logs/gpu-util-final-20261027T0848Z/probe.nsys-rep \
  > "/opt/ai/logs/utilization-top-kernels-$TS.txt"
```

Expected: 文件包含按 GPU 时间排序的 kernel 表。

- [ ] **Step 2: 如果报告没有 CUDA kernel 表，补采带 trace 的 Nsys**

现有 `probe-gpu-utilization.py` 启动的是 GPU Metrics session，不保证报告含 CUDA kernel trace；
不能把 `nsys profile` 包在探针外面冒充 engine trace。若 Step 1 没有 kernel 表，先完成 Task 4，
再执行 Task 4 Step 5 的受控 Nsys trace，然后回到本 Task 的 Step 3 选择 kernel。

Expected: 只有受控 trace 生成的 `cuda_gpu_kern_sum` 能作为候选依据；失败时停止，不进入 NCU。

- [ ] **Step 3: 选择前三个候选 kernel**

选择标准依次为：

1. GPU 时间占比最高。
2. 与 `gemm gate/up`、`qsa attn`、`gemm down` 或 decode expert GEMM 对应。
3. 能由现有 parity 测试覆盖，或能增加最小确定性测试。

把人工裁决写成以下结构，`kernel_regex` 必须是能唯一匹配所选 demangled kernel 的最小安全正则：

```json
{
  "run_ts": "20261027T...Z",
  "source_report": "/opt/ai/logs/gpu-util-final-20261027T0848Z/probe.nsys-rep",
  "top_kernels": [
    {
      "name": "完整 demangled kernel 名称",
      "duration_ns": 0,
      "phase": "prefill",
      "parity_test": "对应测试或待补测试"
    }
  ],
  "selected_index": 0,
  "kernel_regex": "唯一匹配选中所选 kernel 的最小正则",
  "selection_reason": "为何先诊断这一个 kernel"
}
```

```sh
TS=$(cat /tmp/strata-util-run-ts)
SNAP=$(cat /tmp/strata-util-snapshot-path)
SELECTION="/opt/ai/logs/ncu-selection-$TS.json"
python3 - "$SELECTION" "$TS" <<'PY'
import json
import pathlib
import sys

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
sha256sum "$SELECTION" "$SNAP/selection.json"
```

Expected: 顶部 kernel 汇总非空；selection 含 1–3 个候选且只选中一个；日志文件与快照副本
SHA-256 一致。第一轮只让 `kernel_regex` 对应的 kernel 进入 NCU。

## Task 4：实现受控 NCU 启动器

**Files:**
- Create: `tools/server/ncu-exec.py`
- Create: `tools/server/tests/test_ncu_exec.py`
- Deploy: `/opt/ai/bin/ncu-exec.py`

- [ ] **Step 1: 先写失败测试**

```python
import importlib.util
import os
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "ncu-exec.py"


def load_ncu_exec():
    spec = importlib.util.spec_from_file_location("ncu_exec", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_runtime_rejects_missing_argv(tmp_path: Path):
    ncu_exec = load_ncu_exec()
    path = tmp_path / "runtime.json"
    path.write_text('{"argv": [], "env": {}}', encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(ValueError, match="argv"):
        ncu_exec.load_runtime(path)


def test_load_runtime_keeps_argv_and_environment(tmp_path: Path):
    ncu_exec = load_ncu_exec()
    path = tmp_path / "runtime.json"
    path.write_text(
        '{"argv": ["/bin/true"], "env": {"A": "1"}}',
        encoding="utf-8",
    )
    path.chmod(0o600)

    argv, env = ncu_exec.load_runtime(path)

    assert argv == ["/bin/true"]
    assert env == {"A": "1"}


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_load_runtime_rejects_shared_file_permissions(tmp_path: Path):
    ncu_exec = load_ncu_exec()
    path = tmp_path / "runtime.json"
    path.write_text(
        '{"argv": ["/bin/true"], "env": {"A": "1"}}',
        encoding="utf-8",
    )
    path.chmod(0o644)

    with pytest.raises(ValueError, match="permissions"):
        ncu_exec.load_runtime(path)
```

- [ ] **Step 2: 运行测试确认失败**

```powershell
python -m pytest tools/server/tests/test_ncu_exec.py -q
```

Expected: FAIL，`tools/server/ncu-exec.py` 尚不存在。

- [ ] **Step 3: 实现最小的 JSON-to-exec 启动器**

```python
#!/usr/bin/env python3
"""Launch a captured server argv/env under Nsight Compute."""
from __future__ import annotations

import json
import os
import pathlib
import stat
import sys


def load_runtime(path: pathlib.Path) -> tuple[list[str], dict[str, str]]:
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("runtime file permissions must be 0600 or stricter")
    data = json.loads(path.read_text(encoding="utf-8"))
    argv = data.get("argv")
    env = data.get("env")
    if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
        raise ValueError("argv must be a non-empty string list")
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise ValueError("env must be a string mapping")
    return argv, env


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: ncu-exec.py RUNTIME.json", file=sys.stderr)
        return 2
    command, environment = load_runtime(pathlib.Path(argv[0]))
    os.execvpe(command[0], command, environment)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: 复跑测试并部署**

```powershell
python -m pytest tools/server/tests/test_ncu_exec.py -q
python -m py_compile tools/server/ncu-exec.py
```

Expected: Linux `3 passed`；Windows `2 passed, 1 skipped`；语法检查 exit 0。

```sh
scp tools/server/ncu-exec.py atm@192.168.2.223:/opt/ai/bin/ncu-exec.py
ssh atm@192.168.2.223 'chmod 755 /opt/ai/bin/ncu-exec.py'
```

Expected: 本地和远端 SHA-256 一致。

- [ ] **Step 5: 仅在缺少 CUDA kernel 表时，用受控 engine 补采 Nsys trace**

本步骤会短暂停生产。它必须在已有快照和 `server-runtime.json` 之后执行，并复用与 Task 5 相同的
受控进程清理和生产恢复原则。

```sh
set -eu
TS=$(cat /tmp/strata-util-run-ts)
SNAP=$(cat /tmp/strata-util-snapshot-path)
TRACE="/opt/ai/logs/utilization-trace-$TS"
NSYS_PID=""

restore_prod() {
  rc=$?
  trap - EXIT INT TERM
  set +e
  if [ -n "${NSYS_PID:-}" ]; then
    kill -TERM "$NSYS_PID" 2>/dev/null
  fi
  pkill -TERM -f '/opt/ai/bin/[n]cu-exec.py' 2>/dev/null
  pkill -TERM -f 'serve/[s]erver.py --engine' 2>/dev/null
  pkill -TERM -f '[s]trata --serve' 2>/dev/null
  for _ in $(seq 1 30); do
    pgrep -f '/opt/ai/bin/[n]cu-exec.py|serve/[s]erver.py --engine|[s]trata --serve' \
      >/dev/null || break
    sleep 1
  done
  pkill -KILL -f '/opt/ai/bin/[n]cu-exec.py' 2>/dev/null
  pkill -KILL -f 'serve/[s]erver.py --engine' 2>/dev/null
  pkill -KILL -f '[s]trata --serve' 2>/dev/null
  sudo systemctl start strata.service
  restored=0
  for i in $(seq 1 140); do
    H=$(curl -fsS -m 5 http://127.0.0.1:8080/health 2>/dev/null || true)
    case "$H" in
      *'"loaded": true'*) restored=1; break ;;
    esac
    systemctl is-active --quiet strata.service || break
    sleep 3
  done
  if [ "$restored" -ne 1 ]; then
    echo "PRODUCTION RESTORE FAILED - inspect the service before continuing" >&2
    exit 1
  fi
  exit "$rc"
}
trap restore_prod EXIT INT TERM

sudo systemctl stop strata.service
pkill -TERM -f 'serve/[s]erver.py --engine' 2>/dev/null || true
pkill -TERM -f '[s]trata --serve' 2>/dev/null || true
sleep 5

nsys profile \
  --trace=cuda,nvtx,osrt \
  --target-processes all \
  --output="$TRACE" \
  --force-overwrite=true \
  python3 /opt/ai/bin/ncu-exec.py "$SNAP/server-runtime.json" \
  > "/opt/ai/logs/utilization-trace-$TS.log" 2>&1 &
NSYS_PID=$!

ready=0
for i in $(seq 1 140); do
  kill -0 "$NSYS_PID" 2>/dev/null || break
  H=$(curl -fsS -m 5 http://127.0.0.1:8080/health 2>/dev/null || true)
  case "$H" in
    *'"loaded": true'*) ready=1; break ;;
  esac
  sleep 3
done
if [ "$ready" -ne 1 ]; then
  tail -n 80 "/opt/ai/logs/utilization-trace-$TS.log" >&2
  exit 1
fi

python3 /opt/ai/bin/cold-prefill.py util-trace 32768 1
pkill -TERM -f 'serve/[s]erver.py --engine' 2>/dev/null || true
pkill -TERM -f '[s]trata --serve' 2>/dev/null || true
for i in $(seq 1 60); do
  kill -0 "$NSYS_PID" 2>/dev/null || break
  sleep 1
done
if kill -0 "$NSYS_PID" 2>/dev/null; then
  kill -TERM "$NSYS_PID" 2>/dev/null || true
fi
wait "$NSYS_PID" || true
test -s "$TRACE.nsys-rep"
nsys stats --report cuda_gpu_kern_sum "$TRACE.nsys-rep" \
  > "/opt/ai/logs/utilization-top-kernels-$TS.txt"
```

Expected: 受控 server 就绪后只发送一个 32K 冷 prefill；报告含 CUDA kernel 汇总；退出时无条件
清理 trace/server/engine 并恢复生产。失败时停止，不得进入 NCU。

## Task 5：独占窗口运行 NCU

**Files:**
- Read: `/opt/ai/tmp/pre-util-ncu-$TS/server-runtime.json`
- Read: `/opt/ai/tmp/pre-util-ncu-$TS/selection.json`
- Create: `/opt/ai/logs/ncu-util-$TS.ncu-rep`
- Create: `/opt/ai/logs/ncu-util-$TS.log`
- Create: `/opt/ai/logs/ncu-util-$TS.csv`

- [ ] **Step 1: 确认当前用户可采集硬件计数器**

```sh
ncu --version
ncu --list-sets
```

Expected: `ncu` 可运行且至少列出 `SpeedOfLight`、`Occupancy`、`WarpStateStats`。
不能用 root 运行生产 server 来绕过权限；权限不足时停止并先修复 profiling 权限。

- [ ] **Step 2: 停止生产并证明 GPU 已释放**

```sh
sudo systemctl stop strata.service
systemctl is-active strata.service
pgrep -af '[s]trata --serve|serve/[s]erver.py --engine' || true
nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv
```

Expected: service `inactive`；无 server/engine；两卡显存占用显著下降。

- [ ] **Step 3: 在可自动恢复的包装中启动 NCU、等待就绪并发送唯一请求**

```sh
set -eu
TS=$(cat /tmp/strata-util-run-ts)
SNAP=$(cat /tmp/strata-util-snapshot-path)
TARGET_KERNEL=$(python3 - "$SNAP/selection.json" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["kernel_regex"])
PY
)

restore_prod() {
  rc=$?
  trap - EXIT INT TERM
  set +e
  pkill -TERM -f '/opt/ai/bin/[n]cu-exec.py' 2>/dev/null
  pkill -TERM -f 'serve/[s]erver.py --engine' 2>/dev/null
  pkill -TERM -f '[s]trata --serve' 2>/dev/null
  for _ in $(seq 1 30); do
    pgrep -f '/opt/ai/bin/[n]cu-exec.py|serve/[s]erver.py --engine|[s]trata --serve' \
      >/dev/null || break
    sleep 1
  done
  pkill -KILL -f '/opt/ai/bin/[n]cu-exec.py' 2>/dev/null
  pkill -KILL -f 'serve/[s]erver.py --engine' 2>/dev/null
  pkill -KILL -f '[s]trata --serve' 2>/dev/null

  sudo systemctl start strata.service
  restored=0
  for i in $(seq 1 140); do
    H=$(curl -fsS -m 5 http://127.0.0.1:8080/health 2>/dev/null || true)
    case "$H" in
      *'"loaded": true'*) restored=1; break ;;
    esac
    systemctl is-active --quiet strata.service || break
    sleep 3
  done
  systemctl show strata.service -p ActiveState -p SubState -p MainPID
  if [ "$restored" -ne 1 ]; then
    echo "PRODUCTION RESTORE FAILED - inspect the service before continuing" >&2
    exit 1
  fi
  exit "$rc"
}
trap restore_prod EXIT INT TERM

ncu \
  --target-processes all \
  --replay-mode kernel \
  --clock-control none \
  --cache-control none \
  --section SpeedOfLight \
  --section Occupancy \
  --section WarpStateStats \
  --section MemoryWorkloadAnalysis \
  --kernel-name-base demangled \
  --kernel-name "regex:$TARGET_KERNEL" \
  --launch-skip 1 \
  --launch-count 1 \
  --export "/opt/ai/logs/ncu-util-$TS" \
  --force-overwrite \
  python3 /opt/ai/bin/ncu-exec.py "$SNAP/server-runtime.json" \
  > "/opt/ai/logs/ncu-util-$TS.log" 2>&1 &
NCU_PID=$!

ready=0
for i in $(seq 1 140); do
  kill -0 "$NCU_PID" 2>/dev/null || {
    tail -n 80 "/opt/ai/logs/ncu-util-$TS.log" >&2
    exit 1
  }
  H=$(curl -fsS -m 5 http://127.0.0.1:8080/health 2>/dev/null || true)
  case "$H" in
    *'"loaded": true'*) ready=1; break ;;
  esac
  sleep 3
done
[ "$ready" -eq 1 ] || {
  tail -n 80 "/opt/ai/logs/ncu-util-$TS.log" >&2
  exit 1
}

python3 /opt/ai/bin/cold-prefill.py ncu-util 32768 1
pkill -TERM -f 'serve/[s]erver.py --engine' 2>/dev/null || true
pkill -TERM -f '[s]trata --serve' 2>/dev/null || true
for i in $(seq 1 60); do
  kill -0 "$NCU_PID" 2>/dev/null || break
  sleep 1
done
if kill -0 "$NCU_PID" 2>/dev/null; then
  kill -TERM "$NCU_PID" 2>/dev/null || true
fi
wait "$NCU_PID" || true
test -s "/opt/ai/logs/ncu-util-$TS.ncu-rep"
test -s "/opt/ai/logs/ncu-util-$TS.log"
```

Expected: 受控 server 在 420 秒内返回 `loaded=true`；NCU 进程在请求期间存活；请求成功；退出时
包装脚本先终止受控 engine/server 让 NCU 完成导出，再清理剩余 profiler 进程，最后启动生产并在
420 秒内等到 `loaded=true`。

- [ ] **Step 4: 核对报告和生产已经恢复**

```sh
TS=$(cat /tmp/strata-util-run-ts)
test -s "/opt/ai/logs/ncu-util-$TS.ncu-rep"
ncu --import "/opt/ai/logs/ncu-util-$TS.ncu-rep" \
  --page details --csv \
  > "/opt/ai/logs/ncu-util-$TS.csv"
systemctl show strata.service -p ActiveState -p SubState -p NRestarts -p MainPID
curl -fsS http://127.0.0.1:8080/health
python3 /opt/ai/bin/probe-version-stamp.py --local \
  --out "/opt/ai/logs/T-probe-util-post-ncu-$TS.json"
nsys sessions list
```

Expected: `.ncu-rep` 非空；CSV 可导出；service `active/running`；health `loaded=true`；
T-probe `problems=[]`；无 active profiler session。

- [ ] **Step 5: 异常中断后的兜底恢复**

只有在 Step 3 被外部强杀、没有执行 `EXIT` trap，或 Step 4 发现生产未恢复时执行：

```sh
pkill -TERM -f '/opt/ai/bin/[n]cu-exec.py' 2>/dev/null || true
pkill -TERM -f 'serve/[s]erver.py --engine' 2>/dev/null || true
pkill -TERM -f '[s]trata --serve' 2>/dev/null || true
sleep 5
sudo systemctl start strata.service
for i in $(seq 1 140); do
  H=$(curl -fsS -m 5 http://127.0.0.1:8080/health 2>/dev/null || true)
  case "$H" in
    *'"loaded": true'*) echo "production ready"; exit 0 ;;
  esac
  sleep 3
done
echo "production did not become ready" >&2
exit 1
```

Expected: 生产恢复后才允许进入 Task 6。若兜底仍失败，停止全部优化工作，按
`docs/SERVER_RECOVERY.md` 排查。

## Task 6：形成唯一根因决策

**Files:**
- Read: `/opt/ai/logs/ncu-util-$TS.ncu-rep`
- Create: `/opt/ai/logs/utilization-decision-$TS.json`
- Modify: `docs/EVIDENCE_LEDGER.md`
- Modify: `docs/ITERATION_LOG.md`

- [ ] **Step 1: 导出 NCU 明细**

```sh
TS=$(cat /tmp/strata-util-run-ts)
ncu --import "/opt/ai/logs/ncu-util-$TS.ncu-rep" \
  --page details --csv \
  > "/opt/ai/logs/ncu-util-$TS.csv"
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
  "run_ts": "20261027T...Z",
  "decision": "long_scoreboard|barrier_mio|low_occupancy|tensor_pipe|launch_gap|decode_latency|undetermined",
  "ready_for_implementation": false,
  "selection_file": "/opt/ai/logs/ncu-selection-20261027T...Z.json",
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
TS=$(cat /tmp/strata-util-run-ts)
SNAP=$(cat /tmp/strata-util-snapshot-path)
DECISION="/opt/ai/logs/utilization-decision-$TS.json"
python3 - "$DECISION" "$TS" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
ts = sys.argv[2]
data = json.loads(path.read_text(encoding="utf-8"))
allowed = {
    "long_scoreboard",
    "barrier_mio",
    "low_occupancy",
    "tensor_pipe",
    "launch_gap",
    "decode_latency",
}
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
chmod 0440 "$DECISION"
install -m 0440 "$DECISION" "$SNAP/decision.json"
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
- 生产已恢复为冻结的 `0.1.51`，T-probe 为 `problems=[]`。

## Task 7：实现一个代码切片（第二阶段）

**Files:**
- Modify: 仅修改 Task 6 `selected_file` 指向的一项源码
- Test: 对应的 parity 测试和最小新增回归测试
- Create: `/opt/ai/src/strata-util-$TS/`
- Create: `/opt/ai/engines/util-$TS/build/strata`
- Create: `/opt/ai/tmp/util-candidate-$TS.json`

- [ ] **Step 1: 通过第二阶段启动门并复制独立源码树**

```sh
set -eu
TS=$(cat /tmp/strata-util-run-ts)
SNAP=$(cat /tmp/strata-util-snapshot-path)
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
  /opt/ai/src/strata-027-picks/ "$UTIL/"
printf '%s\n' "$UTIL" > /tmp/strata-util-source-path
chmod 600 /tmp/strata-util-source-path
sha256sum /opt/ai/src/strata-027-picks/build75-027/strata > /tmp/strata-util-prod-engine.sha256
```

Expected: 启动门全部通过；独立树存在；冻结生产二进制未改动；后续构建命令不出现
`/opt/ai/src/strata-027-picks/build75-027`。

- [ ] **Step 2: 只加入一个测试和一个源码改动**

先在本地写最小测试，再实现一个直接对应 Task 6 stall 的机制。把且只把选中的源码和测试文件同步
到独立树；文件名由决策文件决定，不能整树覆盖：

```sh
# 在本地仓库执行；尖括号中的路径由 decision.json 决定。
git diff --check
tar -C . -cf /tmp/strata-util-slice.tar "<selected_file>" "<test_file>"
scp /tmp/strata-util-slice.tar atm@192.168.2.223:/tmp/strata-util-slice.tar
ssh atm@192.168.2.223 \
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

- [ ] **Step 5: 通过 use-config.sh 安装独立候选**

```sh
set -eu
TS=$(cat /tmp/strata-util-run-ts)
SNAP=$(cat /tmp/strata-util-snapshot-path)
UTIL=$(cat /tmp/strata-util-source-path)
ENGINE_DIR="/opt/ai/engines/util-$TS/build"
CAND_CFG="/opt/ai/tmp/util-candidate-$TS.json"
sudo install -d -o "$(id -u)" -g "$(id -g)" -m 0755 "$ENGINE_DIR"
install -m 0755 "$UTIL/build/strata" "$ENGINE_DIR/strata.new"
mv -f "$ENGINE_DIR/strata.new" "$ENGINE_DIR/strata"
sha256sum "$UTIL/build/strata" "$ENGINE_DIR/strata"

python3 - "$SNAP/strata-live.json" "$ENGINE_DIR/strata" "$CAND_CFG" <<'PY'
import json
import pathlib
import sys

base_path = pathlib.Path(sys.argv[1])
exe = sys.argv[2]
out_path = pathlib.Path(sys.argv[3])
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

if ! /opt/ai/bin/use-config.sh "$CAND_CFG"; then
  /opt/ai/bin/use-config.sh "$SNAP/strata-live.json" || true
  exit 1
fi
python3 /opt/ai/bin/probe-version-stamp.py --local \
  --out "/opt/ai/logs/T-probe-util-candidate-$TS.json"
sha256sum -c /tmp/strata-util-prod-engine.sha256
```

Expected: 候选二进制只出现在 `/opt/ai/engines/util-$TS/build/strata`；候选配置除绝对 `exe`
外与生产快照一致；`use-config.sh` 看到 PID 变化并以 `loaded=true` 返回；T-probe 的 running engine
hash 等于候选；冻结的 `/opt/ai/src/strata-027-picks/build75-027/strata` hash 未变。失败时立即装回
`$SNAP/strata-live.json`，不得带着未知状态继续测量。

## Task 8：硬件与端到端验收

**Files:**
- Read: `/opt/ai/logs/ncu-util-$TS.ncu-rep`
- Read: `/opt/ai/logs/T-probe-util-candidate-$TS.json`
- Create: `/opt/ai/logs/gpu-util-util-$TS/`
- Create: `/opt/ai/logs/cold-prefill.jsonl`
- Create: `/opt/ai/logs/P2-mixed-soak-util-$TS.*`

- [ ] **Step 0: 确认测量对象是已安装候选**

```sh
TS=$(cat /tmp/strata-util-run-ts)
ENGINE="/opt/ai/engines/util-$TS/build/strata"
python3 - "/opt/ai/logs/T-probe-util-candidate-$TS.json" "$ENGINE" <<'PY'
import json
import sys

stamp = json.load(open(sys.argv[1], encoding="utf-8"))
assert stamp["problems"] == []
assert stamp["health"]["loaded"] is True
assert stamp["running"]["engine"]["binary"] == sys.argv[2]
PY
```

Expected: T-probe 无问题，health loaded，running engine path 精确指向独立候选。路径不符时不得开始
测量，先重新安装候选。

- [ ] **Step 1: 同负载复测硬件计数器**

```sh
TS=$(cat /tmp/strata-util-run-ts)
sudo /opt/ai/bin/probe-gpu-utilization.py \
  --profile mixed --long-tokens 32768 \
  --run-id "util-$TS"
```

Expected: `status=verified`、`problems=[]`；目标计数器达到预注册值，且不是只有单次峰值改善。

- [ ] **Step 2: 比较 3 次 cold prefill**

```sh
python3 /opt/ai/bin/cold-prefill.py util-candidate 32768 3
```

Expected: 中位 wall 或 engine read 改善 ≥3%；若硬件计数改善但 wall 没改善，候选不采纳。

- [ ] **Step 3: 检查 decode 与并发回归**

```sh
python3 /opt/ai/bin/bench-strata.py util-candidate
```

Expected: 单路 decode 和 4 路聚合均不劣于基线 3%；接受率和输出质量不退化。

- [ ] **Step 4: 运行一小时 mixed soak**

```sh
TS=$(cat /tmp/strata-util-run-ts)
python3 /opt/ai/bin/mixed-soak.py \
  --profile ctx256 --duration 3600 \
  --out "/opt/ai/logs/P2-mixed-soak-util-$TS.jsonl"
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
- 哪些负载、kernel 或硬件状态没有测。

- [ ] **Step 2: 决定候选去留**

候选必须同时满足硬件计数、32K wall、decode/4 路回归门、parity/needle 和一小时 soak，才把
`ACCEPT` 设为 `1`；任一失败都设为 `0`。只改善计数器但任务更慢时判为否决。

- [ ] **Step 3: 按判决保留候选或装回冻结生产**

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
  /opt/ai/bin/use-config.sh "$SNAP/strata-live.json"
fi

if [ "$ACCEPT" -eq 1 ]; then
  EXPECT_ENGINE=$(md5sum "/opt/ai/engines/util-$TS/build/strata" | awk '{print $1}')
  cmp -s /opt/ai/src/strata-027-picks/strata-live.json "$SNAP/candidate-config.json"
else
  EXPECT_ENGINE=$(md5sum "$SNAP/engine" | awk '{print $1}')
  cmp -s /opt/ai/src/strata-027-picks/strata-live.json "$SNAP/strata-live.json"
fi
sha256sum -c "$SNAP/MANIFEST.sha256"
python3 /opt/ai/bin/probe-version-stamp.py --local \
  --expect-engine-md5 "$EXPECT_ENGINE" \
  --out "/opt/ai/logs/T-probe-util-final-$TS.json"
systemctl show strata.service -p ActiveState -p SubState -p NRestarts -p MainPID
curl -fsS http://127.0.0.1:8080/health
nsys sessions list
```

Expected: `ACCEPT=1` 时配置与 running engine 都指向独立候选；`ACCEPT=0` 时配置与 running engine
都回到冻结 `0.1.51`。两种情况都必须 health `loaded=true`、T-probe `problems=[]`、快照 manifest
全项 OK、无残留 profiler session；冻结的 `/opt/ai/src/strata-027-picks/build75-027/strata` 始终未变。

- [ ] **Step 4: 清除临时运行环境和路径指针**

只有在 Task 5–9 全部结束、没有后续 profiler 需求时执行：

```sh
SNAP=$(cat /tmp/strata-util-snapshot-path)
test ! -e "$SNAP/server-runtime.json" || shred -u "$SNAP/server-runtime.json"
rm -f /tmp/strata-util-run-ts /tmp/strata-util-snapshot-path \
  /tmp/strata-util-source-path /tmp/strata-util-prod-engine.sha256
```

Expected: 含运行环境值的 `server-runtime.json` 被安全删除；快照中的 engine/server/config、manifest、
selection 和 decision 保留，供审计与回滚。

## 停止条件

- NCU 不能取得完整计数器或需要 root 运行生产 server：停止，先修 profiling 权限。
- 顶层 kernel 无法超过 3% 的证据噪声：停止，不进入代码修改。
- stall 证据指向多个同等主因：只保留一个，其余另立计划。
- 代码候选只提高计数器但 wall 无改善：回退，不继续叠改。
- 出现输出变化、needle 失败、OOM、swap 或槽位降级：立即回退生产。

## 最终验收门

- 选定 kernel 的主计数器中位改善超过两次基线之间的噪声。
- 同一 32K cold prefill 的 wall 或 engine read 改善 ≥3%。
- 单路 decode 和 4 路聚合不劣于冻结基线 3%。
- parity、needle、目标测试和一小时 `ctx256` soak 通过。
- 文档明确区分已验证收益、未验证项和残余风险。
