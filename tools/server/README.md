# `tools/server/` — the box's tooling, one home

Everything that measures or changes `/opt/ai` on the Strata server lives here. `tools/` at the repository
root is upstream's directory; nothing of ours belongs there (the five copies that had accumulated there were
removed on 2026-10-24). `/opt/ai/bin/` is the **deployed** copy of this folder: source of truth is git, and
the two are compared with `ops-check-tool-sync.sh`.

## Conventions

Before changing a script or writing a new one, read [`../../docs/RULES.md`](../../docs/RULES.md) §3–§4
(source check first, resources and error paths, probes off by default, upstream style).

1. **One home, one deploy step.** Edit here, then `scp` to `/opt/ai/bin/` and `chmod 755`. After any change run
   `ops-check-tool-sync.sh` from the repository: it lists every file whose git and deployed copies differ.
2. **New files carry a role prefix.** `ops-` changes the box (deploy, restart, restore, verify), `exp-` is an
   A-B-A that costs restarts, `probe-` only reads and never restarts. Existing names predate the rule and keep
   their names so deployed paths and older ledger entries stay valid; the table below is the index.
3. **An experiment driver is a script, not a session.** Its shape is fixed:
   snapshot the live config with `cp -p` → build the variant in `/opt/ai/tmp/` → install it with `use-config.sh` →
   measure → restore the snapshot → `cmp -s` against the snapshot and say so → write
   `/opt/ai/logs/<ID>-<name>-<UTC ts>.log`. If an install fails, it restores before exiting: leaving the box on a
   half-tested config is not an option.
4. **A probe reads, it does not change.** No restart, no config edit; results go to `/opt/ai/logs/*.jsonl` (or
   `/opt/ai/runs/`). When it reads the shared engine log it must prove the line is *its own* (context length, log
   offset since its own request) — a concurrent request from another session otherwise gets attributed to the
   probe, which happened twice before the guards were added.
5. **Every measurement lands in `docs/EVIDENCE_LEDGER.md`** as an entry naming the script and the log path, with
   median + spread, the arm count and what was *not* measured. A number that is not in the ledger does not exist.
6. **Nothing here hardcodes a credential.** The two live passwords are in the server-only
   `restart-strata.sh` / `swap-engine.sh` and stay out of this repository; `restore-prod.sh` and `use-config.sh`
   read the sudo password from a file the box owns.

## The files

### ops — changes the box
| file | what it does | notes |
|---|---|---|
| `ops-check-tool-sync.sh` | compares this folder against `/opt/ai/bin`, lists drift | run from the repo, needs ssh |
| `use-config.sh <config.json>` | the only install/restart entry: snapshot → validate → install → wait for the PID to change → wait for `/health "loaded": true` | ~156 s per restart; prints the engine's env and args afterwards |
| `restore-prod.sh` | restores the production config and verifies the running process matches | |
| `verify-running-config.sh` | proves the **running** engine was started with exactly the config on disk (cmdline + environ + start time) | a config edit is not proof by itself |
| `check-session-warnings.sh` | judges low-VRAM warnings from the current engine session only | the whole-file grep counts history |
| `vram-perf-sample.sh` | cron `*/5`: VRAM headroom + the engine's own speed/hit readings → `/opt/ai/logs/vram-perf.csv` | logrotate note is in the file: a 0 after midnight means a fresh log, not a quiet night |
| `nvlink-crc-sample.sh` | hourly NVLink error counters → `/opt/ai/logs/nvlink-crc.log` | timer |

### probe — reads only, no restart
| file | question it answers | output |
|---|---|---|
| `turn-ttft.py` | DSH-shaped multi-turn conversation: real re-read per turn, first token, decode, draft acceptance | `/opt/ai/logs/turn-ttft.jsonl` |
| `passive_windows.py` | what real traffic did, per engine session (acceptance, hit rate, truncation, read sizes) | stdout |
| `bench-strata.py` | single stream + 4 concurrent, fixed corpus | `/opt/ai/logs/bench-results.jsonl` |
| `piped_scenario.py` | one stream decoding while another reads a cold prompt: stalls, char rates | stdout |
| `concurrent_reads.py` | what the concurrent read is worth, from the engine's own lines | stdout |
| `cold-prefill.py` | a never-seen prompt, so every token is fresh | `/opt/ai/logs/cold-prefill.jsonl` |
| `warm-measure.py` | warms the server, then measures cold reads | stdout |
| `read-gap.py` | one cold 8K prompt and the engine lines it produced | stdout |
| `turn-ttft.py`, `stage_repeat_probe.py` | the fixed cost of a small read: is it expert staging or per-run work | jsonl |
| `parity_window.py` | bitwise gate for a kernel or layout change | exit code + hashes |
| `probe_determinism.py` | is run-to-run spread sampling or the engine | `/opt/ai/runs/probe-determinism.json` |
| `agent_budget_ab.py` | agent-shaped A/B with a real tool loop, self-scoring tasks | `/opt/ai/runs/agent-budget-ab-*.json` |
| `quality_eval.py`, `quality_eval_budget.py` | small self-scoring quality eval (the quality gate) | stdout |
| `effort-cost.py`, `effort_lang_ab.py`, `replay_effort_ab.py` | what a thinking budget costs and changes | jsonl / stdout |
| `conv_cache_ab.py` | two long conversations alternating: parking and restore costs | stdout |
| `metrics-peek.py` | the conversation-cache and memory fields of `/metrics` | stdout |
| `soak-analyze.py` | soak log summary: errors, RSS and MemAvailable drift | stdout |
| `run-arm.sh`, `t2-run.sh`, `t2check.py`, `prepare-t2.py` | same-boot arm bookkeeping and the T2 bitwise arm | `/opt/ai/logs/arm-*` |
| `hitrate_before_after.py` | **legacy**, superseded by `passive_windows.py` | kept for the older ledger entries |
| `probe-version-stamp.py` | what the box is *running*: md5/sha256/mtime of the four deployed files, the running engine/server, its `STRATA_*` env, feature markers, `/health` | stdout + the JSON named by `--out` |
| `probe-instrument-cost.py` | what the S2 per-request timing probe itself costs (local micro-benchmark) | stdout |

### exp — costs restarts, always ends restored
| file | arms | ledger |
|---|---|---|
| `exp-mode-ab.sh` | `STRATA_EXP_MODE` production vs Turing (`--go` to run) | L-011 |
| `exp-mode8-quality.sh` | mode 8 on the agent-task metric (`--go` to run) | L-011 |
| `enable_api_monitor.sh` | server request monitor on, scenario, `/api/requests` dump | L-021 |
| `gate_prod_ab.sh` | `STRATA_PIPED_CONCURRENT` on/off in the production shape | L-018/L-019 |
| `gate_prod_streams.sh` | the same gate at 2 / 3 / 4 streams | L-020 |
| `thinking-budget-ab.sh` | thinking-budget arms on the self-scoring eval | L-002/L-005 |
| `trace_admission_phases.sh` | `STRATA_TRACE=1`: per-step read phases | L-027/L-028 |
| `prefill_phase_timing.sh` | `STRATA_PREFILL_TIMING=1`: the read as 21 GPU phases | L-030 |
| `mmq_direct_ab.sh` | MMQ pointer-table build, `STRATA_MMQ_DIRECT=1` on/off | L-031 |
| `park_gain_ab.sh` | `--conversation-cache-park-gain`, is parking on the hot path | L-032 |

### batch — one batch, one matrix run
| file | what it does | notes |
|---|---|---|
| `ops-v2-arm.sh` | A/B 臂切换：改 config 的 `env` 键（引擎环境来自 config，不是 launcher）→ `use-config.sh` 重启 → 从 `/proc/<pid>/environ` 读回；默认 dry-run |
| `exp-v2-prefill-ab.py` | V2b：一次 ~22K 读的 `prompt_ms`/wall + 贪心输出文本与 sha256（`--tag parity` 时两臂同 prompt，可做 parity） |
| `exp-v2-ab-summary.py` | 两臂 JSON → 中位/离散度/差值；≥3% 才下结论 |
| `exp-v2-staged-drill.py` | V2-1：跑 `probe_cancel_staged.py` 的场景并按阈值判（读者拿到首 token、关闭后邻居 gap < 2 s 且仍有 token、health 200），再补"长读完成 + 紧跟短请求" |
| `exp-v2-warmup.py` | V2-4：重启后头 N 个请求的 TTFT/总时长序列（冷/暖，测量不是阈值） |
| `exp-v2-vram.py` | V1-5：`/metrics` + `nvidia-smi` 的显存账单（两块卡、engine arena/expert-cache、free reserve） |
| `ops-v2-window.sh` | V2 窗口的安装/回滚驱动：`snapshot`（引擎 + serve/ + tokenizer + launcher + config，`md5sum -c` 核对）、`install --go`（`cp .new && mv` + `use-config.sh` + 核对）、`restore --go`、`check`；默认 dry-run |
| `run-matrix.py` | runs the whole batch matrix once: each item has its criterion, timeout and log; writes `summary.json` and one log per item | `--batch V1 --list` / `--dry-run` / `--only` / `--skip`; never restarts anything |
| `check_assert_lock.py` | V1-4: compiles the one decode-attention object with the default shape, then with `STRATA_QSA_ATTN_CHUNK=128` and requires the static_assert to reject it | uses the build dir's `make -n`; writes only `/tmp` |

### tests — black-box acceptance (pytest)
| file | test | notes |
|---|---|---|
| `tests/test_mp_api.py` | T1 API contract (health, models, usage, 400/401) | stdlib only; runs against the live server |
| `tests/test_mp_batch.py` | T2 greedy solo vs batch | exactness settings, else skip/xfail |
| `tests/test_mp_admission.py` | T3 4 streams + cold reader (≤2 s / ≤1 s) | xfail until W3; the message carries the numbers |
| `tests/test_mp_prefix.py` | T4 second read hits the prefix cache | `cached_tokens` from usage |

See [`tests/README.md`](tests/README.md). The tests are real requests: do not run them beside a measurement.

## Deployed-copy rule

`/opt/ai/bin` is what cron and the drivers actually execute, so a change that is only in git does nothing.
`ops-check-tool-sync.sh` answers "are they the same" in one line per file; run it after every edit and before
any experiment, and never edit a script on the box without bringing it back here.

## Usage signatures (the ones that take arguments)

```
probe-version-stamp.py [--host H | --local] [--out FILE]   # the four hashes measured, not quoted
run-matrix.py --batch V1 [--src DIR] [--only ID] [--skip ID] # one batch, one matrix run
check_assert_lock.py <source-tree> <build-dir>   # the 64/256 shape is locked by static_assert
use-config.sh <config.json>                      # the only install/restore entry, ~156 s
run-arm.sh <label> [context-lines]               # bench×3 + parity×2 + cold prefill×3 -> /opt/ai/logs/arm-<label>
parity_window.py [label] [lines] [max-tokens]    # bitwise gate
bench-strata.py / cold-prefill.py                # single+4 concurrent / cold prefill
piped_scenario.py [reps] [chars] [solo|coexist] [peers]
gate_prod_ab.sh [chars] [scenario-reps] [bench-reps]
gate_prod_streams.sh [peers] [reps] [chars]
enable_api_monitor.sh [peers] [peer-max-tokens] [reps]
exp-mode-ab.sh [--go] / exp-mode8-quality.sh [--go]
agent_budget_ab.py [long] [reason] <reps> <arms...>        # an arm is <budget>[@<effort>]
quality_eval_budget.py <outdir>                            # QE_BUDGET sends a budget
turn-ttft.py <tag> [sizes,comma] [reps] [ctx-tokens] [gen-tokens]
trace_admission_phases.sh [ctx] [tag suffix]
prefill_phase_timing.sh [ctx] [small,large blocks] [reps]
stage_repeat_probe.py [ctx] [block] [pairs]
park_gain_ab.sh [ctx] [sizes] [reps]
mmq_direct_ab.sh                                     # takes no arguments
```

## Deliberately **not** in git

- About 140 one-off scripts still live in `/opt/ai/bin` (vLLM-era sweeps, per-model install steps, ad-hoc probes).
  They are left out on purpose: bringing them in would bury the ~40 that are actually used.
- `restart-strata.sh` and `swap-engine.sh` stay on the box only: both contain the sudo password in plain text, and
  the fork is public. Until that is decided (HANDOVER §12), they are server-only.

## The second engine build (kept, unused)

`/opt/ai/src/strata-0.2.2/build-mmqtab/` is the pointer-table MMQ build that L-031 measured and rejected:

```
cmake -S . -B build-mmqtab -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-13.0/bin/nvcc -DCMAKE_CUDA_ARCHITECTURES=75 \
  -DSTRATA_ENABLE_CUDA=ON -DSTRATA_GGML_DIR=/opt/ai/src/llama.cpp-3cf03257 \
  -DSTRATA_NATIVE_EXPERTS=ON -DSTRATA_MMQ_KQUANTS=ON -DSTRATA_MMQ_TQ_OVERLAY=ON \
  -DSTRATA_MMQ_TAB=ON -DSTRATA_BUILD_TESTS=ON
cmake --build build-mmqtab --target strata prefill_mmq_tab_test -j6      # ~10.6 min
```

`build/` is the production build: do not reconfigure it. A new variant gets its own directory, and if it needs to
run under `use-config.sh` the binary is copied to a path containing `build/strata` (see the trap in HANDOVER §9.28).
