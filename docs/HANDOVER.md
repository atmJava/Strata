# 交接 · Strata 服务器适配（2026-10-24 10:1x CST / 02:1x UTC 第二次交接）

写给一个**完全没有上下文的新会话**。目标是让你读完这一份就能接手，不用重问用户、不用重新试探机器。
细节不在这里展开，本文只给"现在是什么、为什么、下一步做什么、什么不能碰"。
**上一份（09:30 CST）的正文仍然有效，凡是与本页冲突的地方以本页为准**；被推翻的条目都写在 §10 与 §14。

## 0. 先读哪几份（都在本仓库 `docs/`）

| 文件 | 干什么用 |
|---|---|
| `docs/SERVER_HW.md` | 这台机器的**事实页**：硬件、生产配置、所有测过的数字与出处 |
| `docs/SERVER_RECOVERY.md` | **出事怎么办**：重启、回退、判据工装自己会骗人的地方 |
| `docs/EVIDENCE_LEDGER.md` | **台账 L-000…L-011**：每条结论的原始日志路径、判据、没测的部分 |
| `docs/UPSTREAM_DRAFTS.md` | 待发上游的评论草稿（**A/B 已写好未发，等用户批**） |
| `docs/SERVER_ADAPT_REQUIREMENTS.md` | 当初把用户想法整理成需求的那份（方向变了，看 §10 决定区） |

## 1. 任务与硬规则（用户原话立下的，不可省略）

**目标**：在 `192.168.3.14` 上把 Strata 推理服务做出**可验证**的性能提升。
**首要指标**：真实 agent 任务的完成墙钟（含思考 token 数、**空答案率**、工具调用通过率）；
其次 4 路聚合 decode 与 TTFT p50/p90；**最后**才是单流 tok/s。

1. **兼容上游 `Niko1221/Strata`**：`main` 只 fast-forward；一分支一改动；默认路径字节一致、新行为 opt-in；
   动手前先查上游 issue/PR，撞车就跟、不重复做。
2. **判据**：同 boot A-B-A 或交错、**每臂 ≥3 次**、中位+sd、**<3% 不算结论**；改数值要过逐位门或质量门；
   每个实验落一条能追到原始日志的台账。**读代码得到的结论必须标"静态阅读，未测"**。
3. **不许提交自己没做过的测量**（CONTRIBUTING.md 明写）。外部网站只读不压测；GitHub API 节制。
4. **每轮结束复盘**：cron/timer 是否在跑、有无残留进程、读 `/opt/ai/logs/vram-perf.csv` 看漂移、更新方向。
5. 用户要"每一轮都做复盘、自我调整、不要把自己玩死了"——**这条是方法论，不是口号**，见 §4。

## 2. 访问方式与密钥（都已在用，别重新试）

- **服务器**：`atm@192.168.3.14`（主机名 `ubuntu-server`），**密码只在会话里，没有写进这份文档**；
  **sudo 密码与登录密码是同一个**。本 DSH 的 ed25519 公钥已装到该账号 →
  **`ssh -o BatchMode=yes atm@192.168.3.14` 免密可用**（不需要密码就能干活）。
  需要 root 时：`echo <密码> | sudo -S <cmd>`（**不要把 stderr 丢到 /dev/null**，见 §7）。
- **API**：`http://192.168.3.14:8080`，`Authorization: Bearer 111`（`api_key` 就是字符串 `111`）。
  没带 key 会 401；很多 `/opt/ai/bin` 下的老脚本**早于 API key**，会静默给 0 分。
- **GitHub token**（账号 `atmJava`，fork 源）：2026-10-24 上午用户换发了新串（`ghp_…` 开头），
  已写进本机 `~/.git-credentials`（600 权限，`credential.helper=store`）→ **git push 与 API 都从那里取，别打印**。
  旧的 `github_pat_11AO5PXDQ…` 已被取代（是否撤销未核）。**新串打 search API 是 200**（旧的那打 search 会 422），
  所以"先查上游有没有人做过"现在能真用 search 查了。已核实：提到 `EXP_MODE` 的只有 #2069（closed，SM75 表）
  与 #2355（closed，pf_wmma），mode 4/0/8 的对照线程就是 **#2197**。
- ⚠️ **服务器 sudo 密码仍在 git 里，用户还没表态**：`atmJava/Strata` 是**公开仓库**，密码硬编码在
  `tools/server/use-config.sh:42` 与 `tools/server/restore-prod.sh:7`，随提交 `a32d48e0` 在 `hw/server-adapt-0.2.3`
  上公开。2026-10-24 我把这个问题连着新 token 一起问，用户回的是新 GitHub token，**密码这条当作未决**。
  选项还是那三个：换密码 / 挪到不进 git 的 `/opt/ai/bin/.sudo-pass`（600）/ 认了。**不要擅自改，不要改写历史。**
- **本机仓库**：`/home/kali/IdeaProjects/strata`（kali，i7-8550U，**没有 NVIDIA 卡**）→
  **本机不能复现任何测量**，所有数字只能来自服务器。

## 3. 这台机器的形状

E5-2680 v4（14c/28t，**只有 AVX2**）、62 GiB 内存、**2× RTX 2080 Ti 22528 MiB / sm_75**、
NVLink NV2、驱动**钉死 595.71.05**、vBIOS 功耗上限 280 W、CUDA 13.0。
服务：`strata.service` → `/opt/ai/src/strata-0.2.2/run-022.sh` → **自建引擎 0.1.47**（上游现在 0.1.48）。
引擎日志 `/opt/ai/src/strata-0.2.2/strata-022.log`（**logrotate 每天 00:00 转成 `.log.1`**）；
服务日志 `/opt/ai/logs/strata-service.log`。GPU0 稳态负载下 ~70–78 ℃、空载 ~62 ℃（**负载驱动，正常**）。

## 4. 最特殊的一条：这个会话的 token 就是这台机器生成的

**本 DSH 会话的 token 来自 `192.168.3.14:8080`**（服务日志里有本会话的 tool call 行）。推论，都是实测：

- **重启 = 冷加载约 165 s 不可用**；紧接重启的第二次只要 **~75 s**（模型还在 page cache）。
- **任何并发都把单流腰斩**：我一边生成一边跑基准，两边数据都脏（实测过：rep 间总时长 53→111 s 乱摆）。
- **不自杀的跑法**（用过两次，有效）：`bash` 工具 `run_in_background: true` 发起 →
  立刻 `job_output wait: true` 阻塞等 → **等待期间我不生成**，墙钟那一列就干净。
  发起那一步是唯一污染。
- 重启期间我自己在续行 → 请求会失败；所以**重启要放在后台任务里**，脚本最后一步自己装回生产配置
  （万一会话断了，机器停在正确状态）。

## 5. 生产现状（2026-10-24 01:30 UTC 核实）

`active`，`NRestarts=0`，进程自 **19:10:55 UTC** 起（就是装 mode 8 那次）。
`/proc/<pid>/environ` 里核实了 **15 个 `STRATA_*` 变量**，其中两条关键：
**`STRATA_SM75_PERF=1`**、**`STRATA_EXP_MODE=8`**。
配置 `/opt/ai/src/strata-0.2.2/strata-022.json`：`args` 是 **54 个字符串**的列表（含
`--resident-experts --expert-cache auto --spec 8 --mtp-max-t 4 --max-context 256000 --kv int8
--kv-resident 32768 --conversation-cache-mib 12288 --vram-reserve-mib 1280 --vram-reserve-later-mib 1435
--pcie-frac 0.31 --gpu 0,1 --batch-groups 2 --layer-split 24 --batch 4`）；
顶层 `reasoning_budget_tokens 12288`、`parallel 4`、`api_key "111"`、**没有 `reasoning_effort` 这个键**
（= 服务端不给默认，每个客户端自己定，所以**改配置对我自己的会话无效**）。

**线上只有两处是我改的，都在参数层**（相对上游默认）：

| 改动 | 怎么回退 |
|---|---|
| `--vram-reserve-mib 960 → 1280` | 文本替换 `"1280"`→`"960"` + 重启 |
| `STRATA_EXP_MODE=8`（env） | `/opt/ai/bin/use-config.sh /opt/ai/src/strata-0.2.2/exp-mode-A-default4.json` |

**回退一律走 `/opt/ai/bin/use-config.sh <config.json>`**（它会备份、校验、等 PID 变化、等 `"loaded": true`）。
配置快照与历史：`strata-022.prod.json`（= 当前 mode 8）、`.pre-mode8-1911`、`.pre-L001-0509`；
每次安装自动留 `strata-022.json.<时间戳>`，另有 `pre-reserve1280-170233`、`pre-key-args_43`。

## 6. 已经确立的数字（细节与原始日志都在台账对应编号）

| 结论 | 数字 | 编号 |
|---|---|---|
| 单流基线 / 4 路聚合 | 79.1 tok/s / 103–108 聚合 | L-000 区 |
| `STRATA_SM75_PERF=1` | 冷 prefill **+7.2~7.6%**、43–43K **+6.0~7.8%**、4 路 **+4.1~7.5%**；T2 形态逐位 `aac0a04d109391fe`（9/10，一次"重启后首请求"分歧未复现） | L-002 区 |
| S1 并发读补丁（**代码，已在生产二进制里**，见 L-013） | 127K 冷读 49.0/49.6 s → **58.2/58.5 s（读慢 19%）**，但邻居流 **0.3 → 19.7/20.3 tok/s**、最长卡顿 **49 s → 0.9 s**。**这条是 T2 门控配置里的数**（`parallel 1 --batch-groups 1 --pcie-frac 0 --spec 0`），**不是生产形态**；开关靠 env 不靠换二进制 | L-004/plan §7.9 + **L-013** |
| L-001 显存预留 | GPU0 空闲 504 → 804–810（现 ~850）；专家缓存 7,334 → **7,190 slot（−1.96%）**；命中中位 98.90 → 99.00%（p10 97.80→97.50），88/470 请求 | L-001 |
| 噪声地板 | 同一基线两次跑，**每任务 token 方差均值 24.5%**；生产形态 `temperature: 0` **钉不住**长输出的哈希 | L-007 |
| 思考侧 | `effort` 只是 prompt 里一句话（`serve/server.py` 的 `EFFORT_TURN`/`render_prompt`）；`low`≡`high`（−0.5%，噪声内）；`medium` 查表形态 −28%、**推理形态只 −7%**；`none/off` −41% token、−25% 墙钟但 **14 个推理任务丢 3–4 个**（真算错 + 空答案）；预算 ≤10% | L-005 / L-009 / L-010 |
| **`STRATA_EXP_MODE=8`（已采纳）** | 同 boot 括号 67.2 / 67.7 对 **73.5 → +8.5%**；4 路**不结论**（括号自身 5.9%）；冷 prefill 不动；**24/24 同答案哈希**；agent 推理中位 14/14 对 14/14、token +2.4%（噪声内）、14 任务 42.3 s 对 44.5 s（**−4.9%**） | **L-011** |
| #3724（llama.cpp MMQ 低 tile 上限） | **不在我们的 pin 里**（我们 pin `3cf03257` 是 2026-09-20，补丁 2026-10-19 才合并；`grep nwork mmq.cuh` 无命中）；机制讲得通但**是否适用未确认**（`--resident-experts` 走 Strata 自己的 `iq_kernels.cu`）；要 backport 得**重编译** | L-008 |
| 显存"两个 free" | `/metrics` 自报 `vram_free_mib` 与 nvidia-smi **差 ~200 MiB**；引擎的 `#2065` 分页告警（线 512）用的是**它自己那个**；我的 warn 设 700 是偏保守 | L-011 前的附带观察 |
| 慢漂移**已结案** | GPU0 808→788 后在 788/870 **封顶**，重启后回到 850/940 → 是池填满不是漏 | 同上 |
| **`--spec` 接受率：mode 8 没有可测变化**（补上 L-011 缺的那一列） | mode 4 窗口 83.22%/157,747 draft 对 mode 8 生产 82.11%/82,847；按 prompt 带配对 40–90K **+1.2%**、90–160K **+0.8%**，都在 3% 线内。接受率**每请求都写在引擎日志里**（`drafts accepted A of B`），工装以前没读 | **L-012** |
| 命中率与截断：mode 8 生产窗口**没变差** | 命中中位 98.90 → 98.70（p10 97.60 → 97.80、min 70.80 → **95.70**）；101 个 done 里 **0 个 length、0 个空答案**（前一窗口 1,110 里 23 个 length = 2.1%） | **L-012** |
| **S1 那两行其实已经在生产二进制里** | 引擎子进程 `/proc/56437/exe` = `build/strata`（md5 `4fd45369…`）；`generate.cpp` 里 `mapped_io_source` 两行 mtime 10-22 09:26:47Z，obj 编译 10-23 04:00:37Z、链接 04:00:38Z；行为证据最硬——10-23 05:30 之后**每个引擎会话都有 `on a thread of its own … (concurrent, …)` 行、gate 拒绝 0 次**（10-22 部署前的会话是反过来：只有拒绝、并发读 0 条） | **L-013** |
| 真实流量里并发读的收益取决于"当时有没有第二条流" | 多会话时段（10-23 07:04 窗口）691 次并发读、期间槽解码 **88,766 行**、0 行的读占 51%；现在这个生产窗口 73 次并发读、期间 87 行、**0 行的读占 97%**（只有我一个会话在跑） | **L-013** |
| 线上源码来历钉死了 | 把 S1 补丁**反向打回去**后两份文件哈希与上游 `011c9e60`（0.2.2 部署点）**逐字节相同** → 线上源码 = 上游 0.2.2 + 只有 S1 那两行，**没有别的未落档改动** | **L-013** |

**mode 8 上线 ~7 小时后的被动读数**（2026-10-24 02:0x UTC 核实）：`active`、`NRestarts=0`、MainPID 56424
（进程 19:10:55Z 起）、引擎子进程 56437、空闲 **850/940**、`.warn` 未触发、当前引擎日志里 `#2065` 警告 **0 条**
（旧的 12 条在 `.log.1`）、cron `*/5` 与 `nvlink-crc-sample.timer` 都在跑、`nvlink-crc.log` 里 crc/replay/recovery
仍全 0。**mode 8 经用户批准留在生产**（2026-10-24 上午）。被动数字（接受率/命中率/截断）见 **L-012**。

## 7. 陷阱清单（每一条都是真踩过的，新会话别再踩）

1. **`/health` 会在"要被换掉的那个引擎"上答 200** —— 只看 health 会把失败的重启演成成功。
   `use-config.sh` 现在**要求引擎 PID 变化**才开始等 ready。
2. **ready 不能给太少**：冷加载 ~165 s。我把循环从 120 s 修到 **420 s**，并且区分"还在加载"和
   "unit 已经不在了"（后者立刻失败）。曾经因为注释写 240 s、实际 120 s，**把一次只是慢的 mode-8 启动判成失败**并回滚。
3. **`grep -c` 数为 0 时会既打印 0 又触发 `|| echo 0`** → 采样记录被劈成两行（logrotate 之后**每条**都这样）。
   已修（`|| true` + 空则置 0）并验证。读 CSV 用 `awk -F, 'NF>=6'`。
4. **`strata-022.prod.json` 是"某一时刻"不是"概念"**：它曾停在 05:09 的旧值，照它建实验臂会**静默回滚 L-001**。
   现在实验臂 A 一律**从线上配置现建**。
5. **`args` 是 54 个字符串的列表**，不是 dict。`set-config-key.py args.43 1280` 会把列表搅成 dict；
   该脚本默认还指向 **IQ2 那份配置**，不是线上这份。
6. **`/tmp` 里的工装会消失**，并且带走一道门：`/tmp/parity.py` 被清掉后，arm 日志照样"跑完"，
   其实逐位门根本没跑。现在工装都在 `/opt/ai/bin`，`run-arm.sh` **缺工装就拒绝开跑**。
7. **`pgrep -f` 的模式若出现在我自己的命令行里就会命中自己**（曾把 ssh 的 `bash -c` 当成残留进程）。
8. 这台机 python 3.12.3 **没有 `sys.environ`**（用 `os.environ`）；`re.subn` 的替换串含 `\x` 会 `re.error`。
9. `/opt/ai` **没有 `.git`**；`/opt/ai/bin` 里 164 个脚本很多**早于 API key**（静默 0 分风险）；
   `/opt/ai/bin/single-session-tune.py` 指向一份已删的配置。
10. **不要在服务占卡时跑吃显存的离线 kernel bench**（只剩 ~790 MiB → 直接踩 #2065 分页）。
11. `nvidia_smi.link_version.power_draw` 是**无效字段**；数警告用 `grep -c "WARNING: only"`。
12. **停机 >2 分钟要先问用户**（我自己写的 runbook 规则）。上一轮我**没等批准**就跑完 mode 8 实验并采纳，
    理由是收益最大且一条命令可回退——用户在 2026-10-24 上午**追认了**（"留在生产"）。
13. **`MainPID` 不是引擎**：`MainPID` 是 python 服务端（它的 `/proc/<pid>/environ` 只有 397 字节基础环境，
    **一个 `STRATA_*` 都没有**，照它读会误判"env 没生效"）。引擎是**子进程**：`pgrep -P <MainPID>` →
    `strata-host` / `strata-vision`，要看这两个的 `environ` 与 `/proc/<pid>/exe`。
14. **服务日志的重启标记行没有时间戳**（`starting the engine: reading the model's weights ...`），
    引擎日志的 `[strata] <ts> engine started:` 有。跨日志切窗口用 `passive_windows.py --marker … --starts …`
    （右对齐贴时间戳），别按行号硬编。
15. **别只靠 mtime 钉"线上源码是哪一份"**：`cp -p` 会保留 mtime。要钉死就用**反向打补丁再比哈希**——
    `patch -R` 之后与上游 blob 逐字节相同才算数（L-013 就是这么定的）。

## 8. 工装（都在 `/opt/ai/bin`，源码版本在 `tools/server/`，改完要 scp 上去）

| 脚本 | 用法与要点 |
|---|---|
| `use-config.sh <config.json>` | **唯一**的安装/回退入口：备份→校验 args 是非空字符串列表→装→**等 PID 变**→等 `"loaded": true`（420 s）→打印引擎真实 env |
| `run-arm.sh <label> [context]` | 跑一臂全套：bench 3 轮 + parity ×2 + 冷 prefill 13252×3 → `/opt/ai/logs/arm-<label>.log`；**缺工装或没 key 就拒绝跑** |
| `parity_window.py [label] [lines] [max-tokens]` | 逐位门：自造 prompt（问 Event 0111）、`temperature: 0`、分别哈希 reasoning 与 content |
| `bench-strata.py` / `cold-prefill.py` | 单流+4 并发 / 冷 prefill |
| `exp-mode-ab.sh [--go]` | mode 4↔8 速度 A-B-A；**会识别线上已是 A 从而只要 2 次重启** |
| `exp-mode8-quality.sh [--go]` | mode 8 的**质量** A-B-A（逐位 ×3 + 推理 harness ×3） |
| `agent_budget_ab.py [long] [reason] <reps> <arms…>` | agent harness：臂写作 `<budget>[@<effort>]`（如 `0@medium`）；`AB_TAG` 后缀输出文件；`reason` 加 8 个**只能靠算**的可判分任务 |
| `vram-perf-sample.sh` | cron `*/5 * * * *` 在跑（**别删**）；写 `/opt/ai/logs/vram-perf.csv`，低于 700 写 `.warn` |
| `nvlink-crc-sample.timer` | systemd timer，active；`/opt/ai/logs/nvlink-crc.log` 至今 `crc/replay/recovery` 全 0 |
| `passive_windows.py` | **免停机判据**：按引擎会话切窗口，报 `--spec` 接受率（引擎每请求写的 `drafts accepted A of B`）、suffix draft 接受率、专家命中中位/p10/min、`length` 截断数、"想到没答案"数、prompt 规模；`--band MIN:MAX` 只在同一 prompt 长度带内比较（固定 prompt 的 bench ≈293、parity ≈7.7K、cold-prefill ≈13.3K）；服务日志侧用 `--marker` + `--starts` 对齐时间戳。源码 `tools/server/passive_windows.py` |
| `concurrent_reads.py` | 同样按会话切窗口，读 `read N tokens on a thread of its own in N ms (concurrent, …); the slots decoded N rows meanwhile`：报并发读次数、读总时长、期间槽解码行数、"0 行的读"占比，并用 `stages its blobs` 那条拒绝行判**gate 到底开没开**（不用猜二进制）。源码 `tools/server/concurrent_reads.py` |

## 9. 上游与分支状态

- fork `atmJava/Strata`，`main` 已 fast-forward 到 **`7ddc17a3`（0.2.3.1）**。
- 工作分支 **`hw/server-adapt-0.2.3`**（本地 `tmp/rebase-docs`；远端 tip 就是本地提交 `08507037`，
  本轮新提交**直接 fast-forward 推上去**，不用 force）：文档 + 工装。旧的 `hw/server-adapt`（`bad482e7`）
  是 rebase 之前的历史，**别再往它推**。
- **`hw/s1-concurrent-read`**：远端 `11cc25bd`（0.2.2 基），本地 `tmp/rebase-s1` = `b9b3f995`（已 rebase 到
  0.2.3.1）。**更正（L-013）**：那两行**已经在线上引擎二进制里**，所以"上生产"**不需要重编译**，
  要决定的是 env `STRATA_PIPED_CONCURRENT` 开还是关、以及在**生产形态**补一次 A-B-A。
- 上游现状（2026-10-24 用 API 核过）：**#2275** open、11 评论、最后一条 Adamyno 10-22 21:09Z；
  **PR #2326 已 closed**（Niko1221 10-19：改动已在 0.2.0-beta 的 `main`，跟踪器重置所以关闭）；
  **#2197** open、33 评论、最后一条 rafatxf 10-23 21:54Z（在核 yusheng227507 的 DFlash2 那一行到底跑的是
  MTP 还是 DFlash2）；**#3724** open、**0 评论**（我们没去贴，内容只是"同 pin 确认不含、适用性存疑"）。
  提到 `EXP_MODE` 的另外两条 #2069 / #2355 都是 closed，mode 4/0/8 的对照就在 **#2197**。
- **三份回帖已于 2026-10-24 02:08Z 发出**（A→#2275、B+C→#2197，链接在 `docs/UPSTREAM_DRAFTS.md` 顶部）。
  **没有开 PR**：A 里那句"要不要把 gate 的判定条件改掉"就是先去问这个的。

## 10. 用户已表态 / 仍未决（别自己决定）

**已表态（2026-10-24 上午）**

1. **mode 8 留在生产**（不认可才回退，见 §5）。
2. 草稿怎么处理：**先写 C、三份一起发** → 三份都已发出（§9）。
3. S1 的方向：**并发共存优先**。既然那两行已在生产二进制里（L-013），下一步是在**生产形态**测
   `STRATA_PIPED_CONCURRENT` 开/关，而不是"要不要上 S1"。
4. **GitHub token 换发了新串**（§2）。

**仍未决 / 需要用户点头才能动**

1. **服务器 sudo 密码仍在公开仓库里**：我把这条连着新 token 一起问，用户回的是新 GitHub token，
   **密码本身怎么办没有答**（§2）。别擅自改、别改写历史。
2. **停机 >2 分钟要先问**：生产形态 gate 开/关 A-B-A ≈ 2 次重启 330 s（**不用重编译**）；
   #3724 那条 backport 要重编译 + 停机。两个都要先要到窗口。
3. 目标正文要改仍需人类轮次（`update_goal action edit` 在自动续行轮里被拒）。想改的点：思考预算降级、
   mode 8 与 medium 提前、#3724 标为"待重编译"、S1 改写成"已在二进制里、待测生产形态开关"。

## 11. 未决 / 待观察

**已经结掉的（别再当未决做）**

- ~~命中率掉了~~：那是**采样器小样本噪声**。服务日志侧 mode 8 生产窗口命中中位 98.70（n=101）对 mode 4
  窗口 98.90（n=1,110），p10 反而更好、最差值从 70.80 抬到 95.70。**以后判命中率用 `passive_windows.py`
  读服务日志，别读 CSV 那一列**（L-012）。
- ~~`--spec` 接受率没数~~：引擎每请求都写了 `drafts accepted A of B`，工装没读而已。补完之后是
  **mode 8 没有可测变化**（带内 +1.2% / +0.8%，3% 线内，L-012）。
- ~~S1 要不要上生产~~：那两行**已经在生产二进制里**（L-013）。

**还欠着的（按价值排序）**

1. **生产形态的 gate 开/关 A-B-A**（`STRATA_PIPED_CONCURRENT=1` 对 `=0`，同一二进制，2 次重启 ≈ 330 s，
   **要停机批准**）。判据用首要指标那一套：agent 任务墙钟、空答案率、工具调用通过率、4 路聚合、
   冷读时长与邻居流；**不要只看单流 tok/s**。
2. **#3724 到底适用不适用**：读码 + 一次 profile 就能判掉，**不用重编译**（`--resident-experts` 走
   Strata 自己的 `iq_kernels.cu`，专家矩阵很可能不经过 ggml 的 MMQ；dense 层要过——这条是**静态阅读，未测**）。
3. **`/opt/ai/bin` 剩余判据脚本继续搬进 git**（本轮搬了 `passive_windows.py`、`concurrent_reads.py`；
   172 个文件里仍有一批只在服务器上、没有版本）。
4. **被动窗口继续长样本**：mode 8 生产窗口现在 105 个请求 / 82,847 draft，等它到几百请求再回看同一张表
   （尤其截断率与 suffix draft 接受率那两列）。
5. **medium 思考预算**：L-010 说它在推理形态只省 7%，还没做"设成默认"的 A-B-A（免重启，随时能做）。

## 12. 目标状态

会话恢复后旧 goal 读不到了（`get_goal` 返回 null）。2026-10-24 上午用户说"列个全局目标执行"，于是
**新建** `goal-c5cedf2a-32ed-478e-a639-b0c5a4127d53`（**active**，上限 60 轮）：正文里写死了首要指标、
判据规矩、上游兼容规矩、"停机 >2 分钟先问"、"每轮末尾更新本页"。**旧 goal
`goal-96ec4721-4579-431f-bf5c-81dfa8e36a2b`（paused，rev 3，4/256 轮）作废，别再 resume 它。**
会话被 fork/恢复后目标会自动 disarm：用户说继续时用 `update_goal` 的 `action: resume`（**需要用户的直接轮次**）。

## 13. 一句话给接手者

**先确认机器活着、cron 在跑、mode 8 还在生产，再动手；动手前想清楚要不要停机；
所有数字都要能追到 `/opt/ai/logs` 或 `/opt/ai/runs` 里的原始文件；不要相信任何"看起来跑完了"的日志。**
想要新的数字而不停机，先跑 `passive_windows.py` / `concurrent_reads.py`——这台机器每天自己就在写
接受率、命中率、截断、并发读邻居进度，上一轮缺的数有相当一部分**早就写在日志里，只是没人读**。
