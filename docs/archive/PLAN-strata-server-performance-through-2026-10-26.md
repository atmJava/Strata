# Strata 服务器性能与可靠性全局计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保证输出正确、服务可恢复和回滚可执行的前提下，把合并后的 Strata 引擎安全推进到生产，
并持续降低真实用户任务完成时间、首 token 延迟和并发尾延迟。

**Architecture:** 先建立可信的旧生产基线，再用独立目录中的新引擎完成构建、测试、一致性验证和
同 boot A-B-A，最后才在已验证引擎上继续 P4 公平性和下一轮瓶颈优化。任何性能结论都必须同时具备
运行路径证明、正确性证据、同口径性能数据和明确回滚点。

**Tech Stack:** Ubuntu、systemd、CMake、CUDA 13、sm_75、Python 3、Strata 自带的
`tools/server` 探针、A-B-A 测试脚本、JSON/JSONL 证据。

---

## 当前事实

以下事实已在 2026-10-26 02:44 UTC 只读核对：

- 源码树：`/opt/ai/src/strata-merged-a66698c6`
- 源码 HEAD：`a66698c6a8d1656fe7b59c8a7561361c35e18209`
- 源码分支：`hw/server-adapt-0.2.3`
- 今天的新构建：`/opt/ai/src/strata-merged-a66698c6/build75-new/strata`
- 新引擎 md5：`a09d78b13c39378d1532a024b983d286`
- 新引擎版本：`0.1.51`
- 新引擎构建与修复时间：2026-10-26 03:12 UTC 前完成最终测试构建
- 新引擎测试状态：`151 passed / 8 skipped / 1 failed`；唯一失败 `ple_parity` 缺少 Q2_0 PLE
  模型和捕获夹具，不是代码回归；离线 server 为 `73 passed / 10 skipped`
- 生产引擎：`/opt/ai/src/strata-0.2.2/build/strata`
- 生产引擎 md5：`8d680c695f1a6a3d9d202c61d17879f2`
- 生产引擎版本：`0.1.49.1`
- 生产 server md5：`de09cffdac39ff150a689bd3a44cc242`
- 生产 launcher md5：`0eeee1603ab6008261700fa183d20a79`
- 生产 config md5：`05c9d2539cd92b6c8e0b93ff8a48ce0e`
- 生产 health：200，`loaded=true`
- 只读基线证据：`/opt/ai/logs/T-probe-baseline-20261026T024450Z.json`
- GPU 0：RTX 2080 Ti 22528 MiB，采样时已用 21152 MiB，空闲 848 MiB，利用率 0%
- GPU 1：RTX 2080 Ti 22528 MiB，采样时已用 21056 MiB，空闲 946 MiB，利用率 0%
- 主机内存：62 GiB 中已用 48 GiB，available 14 GiB；swap 0 B
- 已发现运维缺口：`/opt/ai/bin/probe-version-stamp.py` 不存在，当前需从源码树执行
- CTest 注册测试：160 项；当前只构建了 `strata` 目标，测试二进制尚未构建

## 路线选择

### 路线 A：先验证新引擎，再在新基线上继续优化

先把 `build75-new` 从“构建成功”推进到“可测试、可加载、输出一致、性能不退化”，再继续 P4 和
下一轮优化。

优点：

- 解决源码已融合但生产仍为旧 `0.1.49.1` 的核心断点。
- 后续所有性能结论都建立在正在维护的代码上。
- 新引擎先过门禁，失败时可继续使用旧生产，不影响当前服务。

缺点：

- 需要构建全部测试目标，并安排一次独占 GPU 的验证窗口。
- 需要完成旧引擎、新引擎、旧引擎的 A-B-A，耗时高于直接调配置。

### 路线 B：继续在旧生产上做 P4 参数边界

直接在旧 `0.1.49.1` 上测试 1500/1550/1600 ms 和 staged 阈值。

优点：

- 重启面较小，可以更快得到当前用户的延迟收益。

缺点：

- 不能证明合并后的源码可用。
- 结果可能无法迁移到新引擎，后续还要重测。
- 继续在旧二进制上投入，会扩大两套运行基线的维护成本。

### 路线 C：直接修改调度器或内核

跳过新引擎部署验证，直接处理更大结构性改动。

优点：

- 潜在收益上限最高。

缺点：

- 当前还没有新引擎的正确性和性能基线，无法判断回归来源。
- 与 `RULES.md` 的“先验证、再扩展”和“先最小闭环”冲突。

**采用路线 A。** 先完成新引擎门禁，再决定是否替换生产；P4 和结构性优化都排在新引擎基线之后。

## 全局阶段

| 阶段 | 目标 | 生产影响 | 完成门 |
|---|---|---|---|
| 0 | 固定生产基线与工具同步 | 只读或短暂写入日志 | T-probe 无 problems，快照可回滚 |
| 1 | 构建并执行新引擎测试 | 构建时无停机 | 160 项测试结果已解释，无未定位失败 |
| 2 | 新引擎加载与正确性验证 | 需要独占 GPU 窗口 | 新引擎可加载，greedy parity 与质量门通过 |
| 3 | 新引擎性能 A-B-A | 需要多次重启 | 单路、4 路和冷读均不退化超过 3%，或明确收益 |
| 4 | P4 公平性边界与 staged 阈值 | 需要多次重启 | wave 与单请求两种口径同时通过 |
| 5 | 下一轮瓶颈发现 | 只读为主 | 用证据确定前三项成本并选一项实验 |
| 6 | 生产采纳与长时 soak | 正式部署 | 1 小时混合 soak 零错误，可一键回滚 |
| 7 | 持续优化循环 | 按实验需要 | 每轮形成采纳/否决/未定判决并更新台账 |

## Task 1：修复工具同步并让部署门禁可执行

**Files:**
- Verify: `tools/server/probe-version-stamp.py`
- Verify: `tools/server/production-env-2080ti.json`
- Verify: `tools/server/ops-check-tool-sync.sh`
- Verify: `tools/server/run-arm.sh`
- Verify: `tools/server/parity_window.py`
- Verify: `tools/server/bench-strata.py`
- Verify: `tools/server/cold-prefill.py`
- Verify: `tools/server/mixed-soak.py`
- Verify: `tools/server/probe_slot_admission.py`
- Verify: `tools/server/ops-v2-arm.sh`
- Modify after deployment: `/opt/ai/bin/probe-version-stamp.py`
- Modify after deployment: `/opt/ai/bin/production-env-2080ti.json`
- Modify after deployment: `/opt/ai/bin/run-arm.sh`
- Modify after deployment: `/opt/ai/bin/parity_window.py`
- Modify after deployment: `/opt/ai/bin/bench-strata.py`
- Modify after deployment: `/opt/ai/bin/cold-prefill.py`
- Modify after deployment: `/opt/ai/bin/mixed-soak.py`
- Modify after deployment: `/opt/ai/bin/probe_slot_admission.py`
- Modify after deployment: `/opt/ai/bin/ops-v2-arm.sh`

- [ ] **Step 1: 运行工具同步检查**

在服务器源码树执行：

```sh
cd /opt/ai/src/strata-merged-a66698c6
bash tools/server/ops-check-tool-sync.sh
```

Expected: 输出每个差异文件；至少确认 `probe-version-stamp.py` 和
`production-env-2080ti.json` 缺失或漂移，且没有未识别的安全文件变化。

- [ ] **Step 2: 部署计划依赖的无密钥工具**

```sh
cd /opt/ai/src/strata-merged-a66698c6
install -m 0755 tools/server/probe-version-stamp.py /opt/ai/bin/probe-version-stamp.py
install -m 0644 tools/server/production-env-2080ti.json /opt/ai/bin/production-env-2080ti.json
for tool in run-arm.sh parity_window.py bench-strata.py cold-prefill.py mixed-soak.py \
  probe_slot_admission.py ops-v2-arm.sh; do
  install -m 0755 "tools/server/$tool" "/opt/ai/bin/$tool"
done
```

Expected: Python/shell 工具为 `0755`，env profile 为 `0644`；部署内容来自当前 HEAD。

- [ ] **Step 3: 用部署路径重新采集基线**

```sh
TS=$(date -u +%Y%m%dT%H%M%SZ)
python3 /opt/ai/bin/probe-version-stamp.py --local \
  --base-url http://127.0.0.1:8080 \
  --api-key 111 \
  --expect-engine-md5 8d680c695f1a6a3d9d202c61d17879f2 \
  --expect-env-file /opt/ai/bin/production-env-2080ti.json \
  --out "/opt/ai/logs/T-probe-toolsync-$TS.json"
```

Expected: exit 0，`problems` 为空，运行 engine 仍为 `8d680c695f1a6a3d9d202c61d17879f2`。

## Task 2：冻结可回滚的生产快照

**Files:**
- Read: `/opt/ai/src/strata-0.2.2/build/strata`
- Read: `/opt/ai/src/strata-port-v1/serve/server.py`
- Read: `/opt/ai/src/strata-0.2.2/run-022.sh`
- Read: `/opt/ai/src/strata-0.2.2/strata-022.json`
- Read: `/etc/systemd/system/strata.service.d/admit-short-first.conf`
- Create: `/opt/ai/tmp/pre-v026-$TS/MANIFEST.sha256`

- [ ] **Step 1: 建立带时间戳的快照目录**

```sh
TS=$(date -u +%Y%m%dT%H%M%SZ)
SNAP="/opt/ai/tmp/pre-v026-$TS"
install -d -m 0755 "$SNAP"
cp -p /opt/ai/src/strata-0.2.2/build/strata "$SNAP/strata"
cp -p /opt/ai/src/strata-port-v1/serve/server.py "$SNAP/server.py"
cp -p /opt/ai/src/strata-0.2.2/run-022.sh "$SNAP/run-022.sh"
cp -p /opt/ai/src/strata-0.2.2/strata-022.json "$SNAP/strata-022.json"
```

Expected: 四个生产文件都存在且大小非零。

- [ ] **Step 2: 保存 service drop-in 与清单**

```sh
cp -p /etc/systemd/system/strata.service.d/admit-short-first.conf \
  "$SNAP/admit-short-first.conf"
(
  cd "$SNAP"
  sha256sum strata server.py run-022.sh strata-022.json admit-short-first.conf \
    > MANIFEST.sha256
  sha256sum -c MANIFEST.sha256
)
printf '%s\n' "$SNAP"
```

Expected: 五个文件全部显示 `OK`，最后一行是新快照绝对路径。

## Task 3：构建全部 CTest 目标

**Files:**
- Read: `/opt/ai/src/strata-merged-a66698c6/build75-new/CMakeCache.txt`
- Modify: `/opt/ai/src/strata-merged-a66698c6/build75-new/` 下的测试产物
- Create: `/opt/ai/logs/build75-new-all-$TS.log`

- [ ] **Step 1: 在不干扰测速的前提下构建全部目标**

```sh
BUILD=/opt/ai/src/strata-merged-a66698c6/build75-new
TS=$(date -u +%Y%m%dT%H%M%SZ)
nice -n 10 ionice -c2 -n7 cmake --build "$BUILD" -j4 \
  2>&1 | tee "/opt/ai/logs/build75-new-all-$TS.log"
```

Expected: exit 0；日志中存在 `Built target strata`，没有 `Error` 或 `FAILED`。

- [ ] **Step 2: 确认 160 项测试都能找到可执行文件**

```sh
BUILD=/opt/ai/src/strata-merged-a66698c6/build75-new
ctest --test-dir "$BUILD" -N 2>&1 | tee /tmp/build75-new-ctest-list.txt
! grep -q 'Could not find executable' /tmp/build75-new-ctest-list.txt
```

Expected: `Total Tests: 160`，命令 exit 0，没有 `Could not find executable`。

## Task 4：先跑不依赖 GPU 的回归

**Files:**
- Read: `split_lend_complement_test`
- Read: `session_rope_share_test`
- Read: `mtp_logits_width_test`
- Read: `tools/server/tests`

- [ ] **Step 1: 运行三个显式禁用 CUDA 的 CTest**

```sh
BUILD=/opt/ai/src/strata-merged-a66698c6/build75-new
ctest --test-dir "$BUILD" --output-on-failure \
  -R '^(split_lend_complement_test|session_rope_share_test|mtp_logits_width_test)$'
```

Expected: 3/3 passed；这些测试的 CTest 属性包含 `CUDA_VISIBLE_DEVICES=-1`。

- [ ] **Step 2: 运行离线 server 测试**

```sh
cd /opt/ai/src/strata-merged-a66698c6
MP_BASE_URL=http://127.0.0.1:9 python3 -m pytest tools/server/tests -q \
  | tee /opt/ai/logs/server-tests-offline-$(date -u +%Y%m%dT%H%M%SZ).log
```

Expected: 以现有交接基线为准，至少出现 `61 passed, 10 skipped`，且没有 failed。

## Task 5：在独占 GPU 窗口执行完整 CTest

**Files:**
- Read: `/opt/ai/src/strata-merged-a66698c6/build75-new`
- Create: `/opt/ai/logs/build75-new-ctest-$TS.log`
- Restore: Task 2 中的生产快照

- [ ] **Step 1: 先证明生产空闲且没有并发构建**

```sh
uptime
nvidia-smi --query-gpu=index,utilization.gpu,memory.used --format=csv,noheader,nounits
ps -eo pid,args | grep -E '[n]vcc|[c]make --build|[p]ytest|[m]ixed-soak|[b]ench-strata'
```

Expected: GPU 利用率接近 0，没有构建、基准或 soak 进程。若存在测量进程，停止本轮并等待其结束。

- [ ] **Step 2: 停机、执行完整 CTest、无论结果都恢复生产**

```sh
BUILD=/opt/ai/src/strata-merged-a66698c6/build75-new
TS=$(date -u +%Y%m%dT%H%M%SZ)
LOG="/opt/ai/logs/build75-new-ctest-$TS.log"
sudo systemctl stop strata.service
ctest --test-dir "$BUILD" --output-on-failure -j1 2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}
sudo systemctl start strata.service
python3 /opt/ai/bin/probe-version-stamp.py --local \
  --base-url http://127.0.0.1:8080 --api-key 111 \
  --expect-engine-md5 8d680c695f1a6a3d9d202c61d17879f2 \
  --expect-env-file /opt/ai/bin/production-env-2080ti.json \
  --out "/opt/ai/logs/T-probe-after-ctest-$TS.json"
exit "$RC"
```

Expected: 生产 service 恢复为 active，health 200，旧引擎 md5 和关键 env 不变。

- [ ] **Step 3: 对失败分类**

```sh
LOG=$(ls -1t /opt/ai/logs/build75-new-ctest-*.log | head -1)
grep -E 'Failed|Passed|Not Run|Total Test time' "$LOG"
```

Expected: 每个失败被归为“新代码回归”“测试夹具/环境缺失”或“尚未定位”之一；未定位失败不得进入
Task 6。

## Task 6：新引擎模型加载与正确性门

**Files:**
- Replace temporarily: `/opt/ai/src/strata-0.2.2/build/strata`
- Read: `/opt/ai/src/strata-merged-a66698c6/build75-new/strata`
- Create: `/opt/ai/logs/arm-v026-{A,B,A2}.log`
- Create: `/opt/ai/logs/T-probe-v026-{A,B,A2}.json`

- [ ] **Step 1: 记录旧生产 A 臂**

```sh
/opt/ai/bin/run-arm.sh v026-A 200
```

Expected: bench 完成 3 轮，单路/4 路中位数写入日志，两个 parity 检查和 cold prefill 均完成。

- [ ] **Step 2: 原子替换新引擎并重启**

```sh
PROD=/opt/ai/src/strata-0.2.2
NEW=/opt/ai/src/strata-merged-a66698c6/build75-new/strata
cp -p "$PROD/strata-022.json" /tmp/v026-restart-config.json
cp "$NEW" "$PROD/build/strata.new"
mv -f "$PROD/build/strata.new" "$PROD/build/strata"
/opt/ai/bin/use-config.sh /tmp/v026-restart-config.json
```

Expected: `READY`，新 engine pid 改变，health 200，`strata --version` 为 `0.1.51`。

- [ ] **Step 3: 用运行路径证明新引擎实际生效**

```sh
TS=$(date -u +%Y%m%dT%H%M%SZ)
python3 /opt/ai/bin/probe-version-stamp.py --local \
  --base-url http://127.0.0.1:8080 --api-key 111 \
  --expect-engine-md5 a09d78b13c39378d1532a024b983d286 \
  --expect-env-file /opt/ai/bin/production-env-2080ti.json \
  --out "/opt/ai/logs/T-probe-v026-B-$TS.json"
```

Expected: exit 0，运行 engine md5 为 `a09d78b13c39378d1532a024b983d286`，`problems` 为空。

- [ ] **Step 4: 记录新引擎 B 臂**

```sh
/opt/ai/bin/run-arm.sh v026-B 200
```

Expected: 与 A 相同的基准、parity 和 cold prefill 流程全部完成，没有 HTTP 或执行错误。

- [ ] **Step 5: 恢复旧生产并记录 A2 臂**

```sh
SNAP=$(ls -1d /opt/ai/tmp/pre-v026-* | sort | tail -1)
PROD=/opt/ai/src/strata-0.2.2
cp -p "$SNAP/strata-022.json" /tmp/v026-restore-config.json
cp "$SNAP/strata" "$PROD/build/strata.new"
mv -f "$PROD/build/strata.new" "$PROD/build/strata"
/opt/ai/bin/use-config.sh /tmp/v026-restore-config.json
/opt/ai/bin/run-arm.sh v026-A2 200
```

Expected: 旧引擎 md5 恢复为 `8d680c695f1a6a3d9d202c61d17879f2`，A2 完成，production health 200。

- [ ] **Step 6: 执行正确性和性能判决**

```sh
for arm in A B A2; do
  echo "=== $arm ==="
  grep -E '单请求 .*tok/s|4 并发 .*聚合|text_sha|答案' "/opt/ai/logs/arm-v026-$arm.log"
done
```

Expected: A 与 A2 的同口径中位差小于 3%；A/B/A2 的固定 greedy parity 文本哈希一致；
新引擎质量门不低于旧引擎，且 HTTP/执行错误为 0。

若新引擎任一正确性门失败，判决为“否决部署”，保留旧生产并回到代码定位。
若正确性通过但单路或 4 路任一指标退化超过 3%，判决为“未定”，先用同一 A-B-A 复测并定位回归。
只有正确性通过且性能无显著退化，才进入 Task 7。

## Task 7：在选定引擎上收口 P4

**Files:**
- Read: `tools/server/mixed-soak.py`
- Read: `tools/server/probe_slot_admission.py`
- Create: `/opt/ai/logs/P4-*`
- Modify after decision: `docs/STATE.md`
- Modify after decision: `docs/WORK_QUEUE.md`
- Modify after decision: `docs/ITERATION_LOG.md`
- Modify after decision: `docs/EVIDENCE_LEDGER.md`

- [ ] **Step 1: 固定 P4 判据**

在开始测量前把以下判据写入本轮日志头：

```text
代码请求 wall <= 10 s
短 JSON wall <= 4 s
96K 四路 wave 相对相邻基线 <= +3%
96K by_kind 单请求中位相对相邻基线 <= +3%
HTTP/执行错误 = 0
意外 finish_reason=length = 0
96K needle 全对
```

Expected: 判据文件在本轮实验开始前生成，测量后不得修改。

- [ ] **Step 2: 同 boot 交替测试 1500/1550/1600 ms**

```sh
TS=$(date -u +%Y%m%dT%H%M%SZ)
for ms in 1500 1550 1600; do
  /opt/ai/bin/ops-v2-arm.sh set STRATA_PIPED_DECODE_LEADIN_MS "$ms" --go
  PID=$(pgrep -f 'build/strata --serve' | head -1)
  tr '\0' '\n' < "/proc/$PID/environ" \
    | grep -Fx "STRATA_PIPED_DECODE_LEADIN_MS=$ms"
  python3 /opt/ai/bin/mixed-soak.py --profile fairness --waves 5 \
    --out "/opt/ai/logs/P4-boundary-$ms-$TS.jsonl"
done
```

Expected: 每个臂的运行态 env 与目标毫秒数完全一致，且都保留原始 JSONL 和 summary；同一臂至少出现
3 个独立 wave，不能按单次最好样本裁决。

- [ ] **Step 3: 在 4096 阈值下运行 reader probe**

```sh
MP_BASE_URL=http://127.0.0.1:8080 MP_API_KEY=111 \
  MP_MODEL=qwen3.8-flash-next-iq3_xxs-abliterated \
  python3 /opt/ai/bin/probe_slot_admission.py \
    --slots 6 --rounds 3 --neighbour-tokens 400 \
    --reader-prompt-tokens 2048 \
    --out "/opt/ai/logs/P4-staged-reader-4096-$(date -u +%Y%m%dT%H%M%SZ).json"
```

Expected: 记录 reader 的 queue、TTFT、wall、邻居停顿、内存和 health。

- [ ] **Step 4: 临时把 server 的 staged 阈值改为 2048 并复测**

阈值由 launcher 导出到 Python server，只修改 config 的 `env` 不会改变 server 准入。执行：

```sh
PROD=/opt/ai/src/strata-0.2.2
SNAP=$(ls -1d /opt/ai/tmp/pre-v026-* | sort | tail -1)
cp -p "$SNAP/run-022.sh" /tmp/v026-launcher-base.sh
sed 's/STRATA_MP_STAGED_READ_MIN_TOKENS=4096/STRATA_MP_STAGED_READ_MIN_TOKENS=2048/' \
  "$SNAP/run-022.sh" > "$PROD/run-022.sh.new"
mv -f "$PROD/run-022.sh.new" "$PROD/run-022.sh"
cp -p "$SNAP/strata-022.json" /tmp/v026-staged-2048-config.json
/opt/ai/bin/use-config.sh /tmp/v026-staged-2048-config.json
tr '\0' '\n' < "/proc/$(pgrep -f 'serve/server.py --engine' | head -1)/environ" \
  | grep -Fx 'STRATA_MP_STAGED_READ_MIN_TOKENS=2048'
MP_BASE_URL=http://127.0.0.1:8080 MP_API_KEY=111 \
  MP_MODEL=qwen3.8-flash-next-iq3_xxs-abliterated \
  python3 /opt/ai/bin/probe_slot_admission.py \
    --slots 6 --rounds 3 --neighbour-tokens 400 \
    --reader-prompt-tokens 2048 \
    --out "/opt/ai/logs/P4-staged-reader-2048-$(date -u +%Y%m%dT%H%M%SZ).json"
```

Expected: server 实际 env 为 2048；记录与 4096 臂相同字段。只发短 prompt 不算覆盖 staged-read 路径。

- [ ] **Step 5: 判决并恢复生产**

```sh
SNAP=$(ls -1d /opt/ai/tmp/pre-v026-* | sort | tail -1)
PROD=/opt/ai/src/strata-0.2.2
cp -p "$SNAP/run-022.sh" "$PROD/run-022.sh.new"
mv -f "$PROD/run-022.sh.new" "$PROD/run-022.sh"
cp -p "$SNAP/strata-022.json" /tmp/v026-final-restore-config.json
/opt/ai/bin/use-config.sh /tmp/v026-final-restore-config.json
curl -sS http://127.0.0.1:8080/health
python3 /opt/ai/bin/probe-version-stamp.py --local \
  --base-url http://127.0.0.1:8080 --api-key 111 \
  --expect-engine-md5 "$(md5sum "$SNAP/strata" | cut -d' ' -f1)" \
  --expect-env-file /opt/ai/bin/production-env-2080ti.json \
  --out "/opt/ai/logs/T-probe-after-p4-$(date -u +%Y%m%dT%H%M%SZ).json"
```

Expected: 无论采纳还是否决，生产恢复到已批准的配置，health 200，T-probe 无 problems。

## Task 8：用指标找下一轮真正瓶颈

**Files:**
- Read: `/opt/ai/logs/P4-*`
- Read: `/opt/ai/logs/strata-service.log`
- Read: `/opt/ai/logs/vram-perf.csv`
- Read: `tools/server/passive_windows.py`
- Read: `tools/server/metrics-peek.py`
- Modify after discovery: `docs/WORK_QUEUE.md`

- [ ] **Step 1: 汇总上一轮的真实成本**

```sh
python3 /opt/ai/bin/soak-analyze.py /opt/ai/logs/P4-boundary-1600-*.jsonl
python3 /opt/ai/bin/passive_windows.py
tail -n 120 /opt/ai/logs/vram-perf.csv
```

Expected: 输出 queue、TTFT、decode、wall、接受率、VRAM headroom 和错误数据，而不是只看聚合 tok/s。

- [ ] **Step 2: 列出前三项用户可感知成本**

```sh
python3 /opt/ai/bin/metrics-peek.py
```

Expected: 至少形成三条带证据的成本排序。候选来源只从准入等待、prefill/decode 交错、expert cache、
conversation cache、VRAM headroom、启动或窗口固定开销中选择。

- [ ] **Step 3: 选一项下轮实验**

在 `docs/WORK_QUEUE.md` 增加一个候选，必须写明：

- 用户收益；
- 成功概率；
- 信息价值；
- 时间与停机成本；
- 风险与回滚；
- 预注册成功、停止和否决阈值。

Expected: 新候选与已否决路线不重复；若收益、信息价值和回滚条件不优于现有候选，则不启动实验。

## Task 9：生产采纳与一小时 soak

**Files:**
- Create: `/opt/ai/tmp/pre-adopt-$TS/`
- Create: `/opt/ai/logs/adopt-soak-$TS.jsonl`
- Create: `/opt/ai/logs/adopt-soak-$TS.summary.json`
- Create: `/opt/ai/logs/T-probe-adopt-$TS.json`

- [ ] **Step 1: 建立最终生产快照**

```sh
TS=$(date -u +%Y%m%dT%H%M%SZ)
SNAP="/opt/ai/tmp/pre-adopt-$TS"
install -d -m 0755 "$SNAP"
cp -p /opt/ai/src/strata-0.2.2/build/strata "$SNAP/strata"
cp -p /opt/ai/src/strata-port-v1/serve/server.py "$SNAP/server.py"
cp -p /opt/ai/src/strata-0.2.2/run-022.sh "$SNAP/run-022.sh"
cp -p /opt/ai/src/strata-0.2.2/strata-022.json "$SNAP/strata-022.json"
cp -p /etc/systemd/system/strata.service.d/admit-short-first.conf \
  "$SNAP/admit-short-first.conf"
(
  cd "$SNAP"
  sha256sum strata server.py run-022.sh strata-022.json admit-short-first.conf \
    > MANIFEST.sha256
  sha256sum -c MANIFEST.sha256
)
printf '%s\n' "$SNAP"
```

Expected: 快照清单校验通过，回滚路径已记录到本轮计划执行日志。

- [ ] **Step 2: 运行一小时混合 soak**

```sh
python3 /opt/ai/bin/mixed-soak.py --duration 3600 \
  --out "/opt/ai/logs/adopt-soak-$TS.jsonl" \
  --summary "/opt/ai/logs/adopt-soak-$TS.summary.json"
```

Expected: HTTP/执行错误 0，意外 length 0，needle 全对，swap 未使用；4 路聚合和单路指标不低于基线 3%。

- [ ] **Step 3: 部署后证明运行路径**

```sh
ADOPTED_ENGINE=/opt/ai/src/strata-0.2.2/build/strata
ADOPTED_MD5=$(md5sum "$ADOPTED_ENGINE" | cut -d' ' -f1)
python3 /opt/ai/bin/probe-version-stamp.py --local \
  --base-url http://127.0.0.1:8080 --api-key 111 \
  --expect-engine-md5 "$ADOPTED_MD5" \
  --expect-env-file /opt/ai/bin/production-env-2080ti.json \
  --out "/opt/ai/logs/T-probe-adopt-$TS.json"
```

Expected: 运行 engine md5、server md5、launcher md5、config md5 和 12 项关键 env 全部符合预期。

## Task 10：落盘、复盘和继续

**Files:**
- Modify: `docs/ITERATION_LOG.md`
- Modify: `docs/EVIDENCE_LEDGER.md`
- Modify: `docs/WORK_QUEUE.md`
- Modify: `docs/STATE.md`
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: 按 `RULES.md` §6 写本轮复盘**

复盘必须包含：目标/指标、假设与预期、为什么选这条路线、改动与实验、基线/变化/样本数/噪声/正确性、
采纳/否决/未定、预期被推翻、生产状态与回滚、证据、学到什么、下一步与理由。

- [ ] **Step 2: 更新唯一事实来源**

```sh
cd /opt/ai/src/strata-merged-a66698c6
git diff --check
git status --short --branch
```

Expected: 没有空白错误；文档修改范围只包含本轮判决、证据、状态和下一步。

- [ ] **Step 3: 提交本轮改动**

```sh
git add docs/ITERATION_LOG.md docs/EVIDENCE_LEDGER.md docs/WORK_QUEUE.md \
  docs/STATE.md docs/ROADMAP.md
git commit -m "docs(server): 收口本轮服务器性能实验"
```

Expected: 提交成功，提交信息采用项目规定的中文、多行格式；若实际改动超出文档，按真实改动重写提交信息和验证正文。

- [ ] **Step 4: 立即选择下一轮**

从 `docs/WORK_QUEUE.md` 中按用户收益、成功概率、信息价值、时间、部署成本和风险重新排序。

Expected: 选择一项新迭代；目标没有完成，不因计划执行完而停止。

## 全局停止与回滚条件

出现以下任一情况时立即停止性能实验并恢复生产：

- health 非 200 或 `loaded!=true`；
- 输出 parity 不一致或质量门低于基线；
- 出现未解释的 HTTP/执行错误、意外 `length` 或 needle 失败；
- 单路或 4 路同口径性能退化超过 3%，且 A-B-A 不能证明噪声；
- 运行路径 hash 与预期不一致；
- 出现 swap 使用、持续内存下降或 VRAM 不足导致低内存告警；
- 无法在 10 分钟内回答“当前运行的是哪个 engine/server/config”。

恢复顺序固定为：先恢复旧生产，再检查 health，再用 T-probe 证明运行路径，再分析日志。分析不得以
保持故障服务在线为代价。

## 继续约束

- 不重复已经否决的 1200/2000 ms lead-in、QSA slots、固定 read piece 32768/65536、S0b、
  groups=1、groups=1+batch-MTP、split25/26。
- 不向上游 `Niko1221/Strata` 推送。
- 不把 API key 写入仓库、计划、日志或提交信息。
- 不在测速窗口运行构建、soak、pytest 或其他竞争负载。
- 不把 health 200 当作部署成功；必须用运行路径 hash 和实际 env 证明。
- 不把 wave 聚合收益替代单请求尾延迟，也不把单次最好样本当稳定收益。
- 不因为没有测试目标、没有日志或缺少外部权重而宣布目标完成。
