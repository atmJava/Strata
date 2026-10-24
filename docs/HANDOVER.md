# 交接 · Strata 服务器适配（2026-10-24 09:30 CST / 01:30 UTC）

写给一个**完全没有上下文的新会话**。目标是让你读完这一份就能接手，不用重问用户、不用重新试探机器。
细节不在这里展开，本文只给"现在是什么、为什么、下一步做什么、什么不能碰"。

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
- **GitHub PAT**（账号 `atmJava`，fork 源）：以 `github_pat_11AO5PXDQ…` 开头，**只在会话记录里，没有进 git**。
  这个 PAT 打 GitHub **search** API 会 422，只能用分页的 `/repos/…/issues`、`/repos/…/issues/…/comments`。
- ⚠️ **一条已存在的凭据暴露，需要用户决定怎么处理**：`atmJava/Strata` 是**公开仓库**，而服务器密码
  被硬编码在 `tools/server/use-config.sh:42` 与 `tools/server/restore-prod.sh:7`，已随提交 `a32d48e0`
  推到 `hw/server-adapt-0.2.3` 分支上。选项：换密码 / 把密码挪到不进 git 的文件（`/opt/ai/bin/.sudo-pass`，
  600 权限）/ 认了这个暴露不管。**我没有擅自改，也没有改写历史。**
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
| S1 并发读补丁（**代码**，未上生产） | 127K 冷读 49.0/49.6 s → **58.2/58.5 s（读慢 19%）**，但邻居流 **0.3 → 19.7/20.3 tok/s**、最长卡顿 **49 s → 0.9 s** | L-004/plan §7.9 |
| L-001 显存预留 | GPU0 空闲 504 → 804–810（现 ~850）；专家缓存 7,334 → **7,190 slot（−1.96%）**；命中中位 98.90 → 99.00%（p10 97.80→97.50），88/470 请求 | L-001 |
| 噪声地板 | 同一基线两次跑，**每任务 token 方差均值 24.5%**；生产形态 `temperature: 0` **钉不住**长输出的哈希 | L-007 |
| 思考侧 | `effort` 只是 prompt 里一句话（`serve/server.py` 的 `EFFORT_TURN`/`render_prompt`）；`low`≡`high`（−0.5%，噪声内）；`medium` 查表形态 −28%、**推理形态只 −7%**；`none/off` −41% token、−25% 墙钟但 **14 个推理任务丢 3–4 个**（真算错 + 空答案）；预算 ≤10% | L-005 / L-009 / L-010 |
| **`STRATA_EXP_MODE=8`（已采纳）** | 同 boot 括号 67.2 / 67.7 对 **73.5 → +8.5%**；4 路**不结论**（括号自身 5.9%）；冷 prefill 不动；**24/24 同答案哈希**；agent 推理中位 14/14 对 14/14、token +2.4%（噪声内）、14 任务 42.3 s 对 44.5 s（**−4.9%**） | **L-011** |
| #3724（llama.cpp MMQ 低 tile 上限） | **不在我们的 pin 里**（我们 pin `3cf03257` 是 2026-09-20，补丁 2026-10-19 才合并；`grep nwork mmq.cuh` 无命中）；机制讲得通但**是否适用未确认**（`--resident-experts` 走 Strata 自己的 `iq_kernels.cu`）；要 backport 得**重编译** | L-008 |
| 显存"两个 free" | `/metrics` 自报 `vram_free_mib` 与 nvidia-smi **差 ~200 MiB**；引擎的 `#2065` 分页告警（线 512）用的是**它自己那个**；我的 warn 设 700 是偏保守 | L-011 前的附带观察 |
| 慢漂移**已结案** | GPU0 808→788 后在 788/870 **封顶**，重启后回到 850/940 → 是池填满不是漏 | 同上 |

**mode 8 上线 14 小时后的被动读数**（交接前刚看）：`active`、`NRestarts=0`、空闲 **850/940**、
`.warn` 未触发、**轮转后的新日志里 `#2065` 警告 0 条**（旧的 12 条在 `.log.1`）→ 没有新警告。

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
    理由是收益最大且一条命令可回退——**用户还没表态**（见 §10）。

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

## 9. 上游与分支状态

- fork `atmJava/Strata`，`main` 已 fast-forward 到 **`7ddc17a3`（0.2.3.1）**。
- 工作分支 `hw/server-adapt-0.2.3`（本地 `tmp/rebase-docs`，最新提交 **`6ff15234`**）：文档 + 工装。
- **`hw/s1-concurrent-read`**（commit `b9b3f995`，2 文件 +20/−3，`batch_read.hpp` + `generate.cpp:13577`）：
  已 rebase 到 0.2.3.1，**测过但没上生产**（上生产要重编译引擎）。
- 上游 thread：**#2275**（并发读，11 条，SuperDistance 的 PR #2326 已在做，我们的独特角度是 S1 那个
  `staged_source` 门 + 实测权衡）；**#2197**（sm_75，基本被六位同行关掉，我们的 +7% 已冗余，
  **但 mode 8 的 +8.5% 是新数据**）；**#3724**（open、0 评论）。
- `docs/UPSTREAM_DRAFTS.md`：**Draft A（#2275）、Draft B（#2197）已写好、尚未发、等用户批**；
  **Draft C（mode 8 数据）还没写**，素材就是 L-011。

## 10. 等用户表态的三件事（都别自己决定）

1. **mode 8 留在生产**用户是否认可（不认可 → 一条命令装回，见 §5）。
2. **Draft A / B 发不发**，以及要不要我把 **Draft C** 写出来给他看。
3. **目标正文需要人类轮次**才能改：`update_goal action edit` 在自动续行轮里被拒。
   要改的点已想好：思考预算降级、mode 8 与 medium 提前、#3724 标为"待重编译"。

## 11. 未决 / 待观察（都不需要停机就能做）

- mode 8 上线后采样里命中率 **96.4–99.3%**，之前是 98.9–99.8% —— **样本太小，不下结论**，继续被动看。
- **`--spec` 接受率有没有被 mode 8 改变**：那次 bench 的 `draft_n` 没报出来，要补。
- **S1 要不要上生产**：它不是"更快"，是"把独占变共存"（读慢 19%、邻居 0.3→19.7 tok/s）。
  要用户先说清要单流长读还是要并发共存。
- **#3724 到底适用不适用**：可以用读代码 + 一次 profile 判掉，**不用重编译**。
- 把 `/opt/ai/bin` 剩余判据脚本继续搬进 git（`vram-perf-sample.sh` 本轮已搬）。

## 12. 目标状态

`goal-96ec4721-4579-431f-bf5c-81dfa8e36a2b`，**已暂停（paused，revision 3）**，已起 4/256 轮。
用户说继续时：`update_goal` 用 `action: resume`（**需要用户的直接轮次**）。
会话被 fork/恢复后目标会自动 disarm，同样要 resume 重新武装。

## 13. 一句话给接手者

**先确认机器活着、cron 在跑、mode 8 还在生产，再动手；动手前想清楚要不要停机；
所有数字都要能追到 `/opt/ai/logs` 或 `/opt/ai/runs` 里的原始文件；不要相信任何"看起来跑完了"的日志。**
