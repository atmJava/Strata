> **先读**：`docs/CODE_CONCLUSIONS.md` —— 一页写下这个夜里已经封掉的路、封它们的数字，以及唯一还开着的方向（并发）。

# 交接 · Strata 服务器适配（持续更新的唯一入口）

> 这份文件是接手入口：**先读它**，需要数字时按 `docs/EVIDENCE_LEDGER.md` 顶部的判决索引跳条目。
> 文档地图：[`docs/README.md`](README.md)（哪些现行、哪些是历史存档）。工装手册：[`tools/server/README.md`](../tools/server/README.md)。
> 最后更新：2026-10-24 06:45 UTC（文档与工装规范化那一轮）。


## 0. 十分钟接手

1. **先读章程 [`RULES.md`](RULES.md)**（怎么干活、决策权、写代码与证据规则），再读这一页：§3 的决策表
   （现在生产是什么、为什么、怎么回滚）、§6 的生产现状（PID、配置、最近重启）。
2. **看下一轮做什么**：§13 顶部的"下一轮先做"。
3. **扫一遍 §9 的坑**：每一条都真踩过并付过代价，重踩等于白烧重启。
4. **要动任何数值**：先看 §2 的规矩；工装清单和"怎么加一个实验脚本"在 `tools/server/README.md`。
5. **动生产只有一条路**：`use-config.sh <config.json>`（快照 → 校验 → 安装 → 等 PID 变化 → 等 `/health "loaded": true`，
   每次约 156 s）。它成对存在于 git 和 `/opt/ai/bin`；改完用 `tools/server/ops-check-tool-sync.sh` 对齐两边。
6. 当前生产的一切都在 §3 表里，**不在表里的东西不要假设它在生产里跑**。

---

## 1. 任务与硬规则（用户原话立下的，不可省略）


**目标**：在 `192.168.3.14` 上把 Strata 推理服务做出**可验证**的性能提升。
**首要指标**：真实 agent 任务的完成墙钟（含思考 token 数、**空答案率**、工具调用通过率）；
其次 4 路聚合 decode 与 TTFT p50/p90；**最后**才是单流 tok/s。

1. **兼容上游 `Niko1221/Strata`**：`main` 只 fast-forward；一分支一改动；默认路径字节一致、新行为 opt-in；
   动手前先查上游 issue/PR，撞车就跟、不重复做。
   **2026-10-24 用户加了硬规矩：上游只读——不回帖、不开 PR、不提交任何东西。** 此前发过的三条评论
   **已按此令删除**（§11）；写好的英文正文只作为台账留档放在 `docs/UPSTREAM_DRAFTS.md`，不再发布。
2. **判据**：同 boot A-B-A 或交错、**每臂 ≥3 次**、中位+sd、**<3% 不算结论**；改数值要过逐位门或质量门；
   每个实验落一条能追到原始日志的台账。**读代码得到的结论必须标"静态阅读，未测"**。
3. **不许提交自己没做过的测量**（CONTRIBUTING.md 明写）。外部网站只读不压测；GitHub API 节制。
4. **每轮结束复盘**：cron/timer 是否在跑、有无残留进程、读 `/opt/ai/logs/vram-perf.csv` 看漂移、更新方向。
5. 用户要"每一轮都做复盘、自我调整、不要把自己玩死了"——**这条是方法论，不是口号**，见 §2。
6. **停机 >2 分钟**：原来要先问用户，**2026-10-24 用户把决定权交给我**（原话"你可以自己做决策，不要问我"）。
   仍然要守：低峰时段做、先备份配置（`use-config.sh` 自带备份）、测完恢复原状态、台账写清停了多久。


## 2. 判据与实验规则（不遵守的结论不要写进台账）

- **判据优先级**（用户定的）：① 真实 agent 任务的完成墙钟（`GET /api/requests` 的 `wallclock_s` / `first_token_s`，
  含思考 token 数、空答案率、工具调用通过率）> ② 4 路聚合 decode 与 TTFT p50/p90 > ③ 单流 tok/s（最低，别拿它当理由）。
- **实验形态**：同 boot A-B-A 或交错；每臂 ≥3 次；报中位数与离散度。**<3% 不算结论**；跨 boot 比"小读/首字"这类
  小量时按 **~7%** 看待（L-031 实测同形状三臂跨度 1,023–1,097 ms）。
- **改数值**：过逐位门（`tools/server/parity_window.py`）或质量门（`quality_eval.py` / `agent_budget_ab.py`）。
- **只读代码得到的结论**一律标 *静态阅读，未测*。
- **绝不提交没做过的测量**；每条台账条目必须写原始日志路径与脚本名。
- **停机**（>2 min）决定权在自己：低峰跑、先 `cp -p` 快照配置、测完逐字节恢复并用 `cmp -s` 验证、台账写清停了多久。
- **本会话与子代理的 token 都由这台机器生成**（§7）：有别的流在跑就别测速度。
- **外部站点只读不压测**；GitHub API 节制；上游只读（§11）。

---


## 3. 已定案：一页决策表

生产 = `strata-022.json`（`exe=build/strata`，54 个 args，`parallel: 4`，`--max-context 256000`）。
**回滚一律是"装回上一份快照"**：`use-config.sh /opt/ai/tmp/<snapshot>.json`（每次实验前 `cp -p` 存一份）。

| 决定 | 证据 | 状态 |
|---|---|---|
| `STRATA_EXP_MODE=8` 进生产 | L-011（单流 decode +8%，质量门过；L-012 被动窗口确认） | **在生产**（env） |
| `STRATA_PIPED_CONCURRENT=1` 保持开 | L-019（读的人 +21.6%，邻居停顿 11.6 → 0.70 s）、L-020（3 流时 +44.5%，邻居 22–23 倍） | **在生产**（env） |
| `api_monitor: true` 保持开 | L-021（开销测不出；真实流量首个 TTFT 中位 0.80 s） | **在生产**（config） |
| `--vram-reserve-mib 1280`（原 960） | L-001 判决（保留，不回退） | **在生产**（args） |
| `--kv int8`、`--kv-resident 32768`、`--conversation-cache-*`、`--prompt-cache 3`、`--spec 8`、`--pcie-frac 0.31`、`--batch 4` / `--batch-groups 2` | 基线形态（L-000），后续实验都在这套形态上做 | **在生产**（args，别单独改） |
| #3724（llama.cpp #30553）backport | L-016 静态判定：本机只有一部分路径走到，收益噪声级 | **否决**（不花重编译） |
| 思考预算默认改 `medium` | L-023：agent 形态测不出差别，且我们的客户端自己发 `high` | **否决** |
| MMQ tab 构建 + `STRATA_MMQ_DIRECT=1` | L-031：开关生效（48/48 层），端到端 −2.7%/−0.6%/−0.1%，对同二进制关着的那臂还慢 +4.3% | **否决**（`build-mmqtab/` 留盘备用） |
| `--conversation-cache-park-gain` | L-032：parking 是每次会话切换 350 ms（1–2%），且该旋钮在此形状下没跳过它 | **否决** |
| `STRATA_HC_Q8=1`（hc 投影读 Q8_0 字节） | L-036：4 并发聚合 **+3.8…+7.1%**（两对独立 A-B-A）、单流 −0.5%、agent 质量 14/14 相等、0 畸形调用；文本有近并列改写 | **在生产**（env，2026-10-24 起） |
| read-ahead / 服务端先发后挂 | L-022/L-024/L-025：位置在 `pick_slot`，无"少准入"旋钮，中等工程；真实流量很少撞满槽 | **未做**（降级，见 §13） |

---

## 4. 这台机器的形状


E5-2680 v4（14c/28t，**只有 AVX2**）、62 GiB 内存、**2× RTX 2080 Ti 22528 MiB / sm_75**、
NVLink NV2、驱动**钉死 595.71.05**、vBIOS 功耗上限 280 W、CUDA 13.0。
**NVLink 实测**（2026-10-24）：`topo -m` = NV2，`nvlink -s` = 每卡 2 条 × 25.781 GB/s（单向 ~51.6 GB/s，
本地 HBM 的 8.4%），`cudaDeviceCanAccessPeer` 双向 = 1；PCIe 是 3.0 x16。
**实测（`/tmp/p2p_bw.cu`，64 MiB `cudaMemcpyPeerAsync`，12 次取中位）：P2P 46.8 GB/s，主机方向 11.0 GB/s，
比值 4.25×**（spec 的 91%）。
它在 decode 里没有位置（跨卡只有 stage 交接 ~410 KB/窗口，改 P2P 也只省 0.18%），**在 prefill 里有**：
`set_stage_helper` 让空闲的下一段 GPU 分担一部分专家的"流 + 算"，收益来自第二条 PCIe 链路、代价是跨卡交换——
而 `split_help_frac` 的默认值（T>3.3k 不帮、且只在单 chunk）是在 **无 P2P 的 3090 对**上定的，我们的 4.5k
提示词正好落在关掉的一侧。测量与代码项见 [`CODE_PLAN.md`](CODE_PLAN.md) §8 / P0d。
服务：`strata.service` → `/opt/ai/src/strata-0.2.2/run-022.sh` → **自建引擎 0.1.47**（上游现在 0.1.48）。
引擎日志 `/opt/ai/src/strata-0.2.2/strata-022.log`（**logrotate 每天 00:00 转成 `.log.1`**）；
服务日志 `/opt/ai/logs/strata-service.log`。GPU0 稳态负载下 ~70–78 ℃、空载 ~62 ℃（**负载驱动，正常**）。

## 5. 访问方式与密钥（都已在用，别重新试）


- **服务器**：`atm@192.168.3.14`（主机名 `ubuntu-server`），**密码只在会话里，没有写进这份文档**；
  **sudo 密码与登录密码是同一个**。本 DSH 的 ed25519 公钥已装到该账号 →
  **`ssh -o BatchMode=yes atm@192.168.3.14` 免密可用**（不需要密码就能干活）。
  需要 root 时：`echo <密码> | sudo -S <cmd>`（**不要把 stderr 丢到 /dev/null**，见 §9）。
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

## 6. 生产现状与运维纪律

**今天一共重启 20 次**（引擎会话 03:11:00、03:15:25、03:29:11、03:34:17、03:53:15、03:57:17、04:12:08、04:59:41、
05:02:30、05:07:13、05:10:53、05:31:12、05:38:40、05:50:43（MMQ 臂，因路径坑白起一次）、05:52:47、05:58:42、06:03:01、
06:07:15、06:16:09、06:20:16 UTC；合计约 3,100 s 冷加载）。所以现在：`active`、`NRestarts=0`、
MainPID **147049**（服务进程 06:20:13Z 起）、引擎子进程 **147064**（06:20:15Z 起）、vision **147059**；
`exe` 是 `build/strata`，env 里**没有** `STRATA_MMQ_DIRECT`、args 里**没有** `--conversation-cache-park-gain`
（见 §9 第 13 条）。空闲显存 **856 / 954 MiB**（06:30Z 的 CSV 行，两张卡各 21.1 / 22.5 GiB used），`.warn` 未触发。
**19:10:55Z 那个 mode 8 会话被这些重启切断了**——被动窗口要从 **06:20:16Z 这个会话**重新攒（§13 第 4 条）。
在**引擎子进程**的 `/proc/<pid>/environ` 里核实了 **15 个 `STRATA_*` 变量**，其中三条关键：
**`STRATA_SM75_PERF=1`**、**`STRATA_EXP_MODE=8`**、**`STRATA_PIPED_CONCURRENT=1`**（开/关今天实测过，**L-019**）。
配置 `/opt/ai/src/strata-0.2.2/strata-022.json`：`args` 是 **54 个字符串**的列表（含
`--resident-experts --expert-cache auto --spec 8 --mtp-max-t 4 --max-context 256000 --kv int8
--kv-resident 32768 --conversation-cache-mib 12288 --vram-reserve-mib 1280 --vram-reserve-later-mib 1435
--pcie-frac 0.31 --gpu 0,1 --batch-groups 2 --layer-split 24 --batch 4`）；
顶层 `reasoning_budget_tokens 12288`、`parallel 4`、`api_key "111"`、**没有 `reasoning_effort` 这个键**
（= 服务端不给默认，每个客户端自己定，所以**改配置对我自己的会话无效**）。

引擎 cmdline 今天完整读了一遍，两条值得记：`--prefill auto --prefill-pipe 1`（chunk 是自动算的，53/53 次启动
都是 8192 tokens / 96-slot ring，见 L-017），以及 **`--expert-profile` 指的是
`/opt/ai/src/strata-0.1.40.1/data/expert-profile.bin`**——**"退役"的 0.1.40.1 那棵树仍被生产依赖，别删它**。

**线上只有三处是我改的，都在参数/配置层**（相对上游默认）：

| 改动 | 怎么回退 |
|---|---|
| `--vram-reserve-mib 960 → 1280` | 文本替换 `"1280"`→`"960"` + 重启 |
| `STRATA_EXP_MODE=8`（env） | `/opt/ai/bin/use-config.sh /opt/ai/src/strata-0.2.2/exp-mode-A-default4.json` |
| `"api_monitor": true`（顶层配置键，上游默认关；每条请求的墙钟/TTFT 从此免费，见 L-021） | `/opt/ai/bin/use-config.sh /opt/ai/tmp/monitor-off-20261024T041201Z.json` |

**回退一律走 `/opt/ai/bin/use-config.sh <config.json>`**（它会备份、校验、等 PID 变化、等 `"loaded": true`）。
配置快照与历史：`strata-022.prod.json`（= 当前 mode 8）、`.pre-mode8-1911`、`.pre-L001-0509`；
每次安装自动留 `strata-022.json.<时间戳>`，另有 `pre-reserve1280-170233`、`pre-key-args_43`。

## 7. 最特殊的一条：这个会话的 token 就是这台机器生成的


**本 DSH 会话的 token 来自 `192.168.3.14:8080`**（服务日志里有本会话的 tool call 行）。推论，都是实测：

- **重启 = 冷加载约 165 s 不可用**；紧接重启的第二次只要 **~75 s**（模型还在 page cache）。
- **任何并发都把单流腰斩**：我一边生成一边跑基准，两边数据都脏（实测过：rep 间总时长 53→111 s 乱摆）。
- **不自杀的跑法**（用过两次，有效）：`bash` 工具 `run_in_background: true` 发起 →
  立刻 `job_output wait: true` 阻塞等 → **等待期间我不生成**，墙钟那一列就干净。
  发起那一步是唯一污染。
- 重启期间我自己在续行 → 请求会失败；所以**重启要放在后台任务里**，脚本最后一步自己装回生产配置
  （万一会话断了，机器停在正确状态）。

## 8. 已经确立的数字（细节与原始日志都在台账对应编号）


| 结论 | 数字 | 编号 |
|---|---|---|
| 单流基线 / 4 路聚合 | 79.1 tok/s / 103–108 聚合 | L-000 区 |
| `STRATA_SM75_PERF=1` | 冷 prefill **+7.2~7.6%**、43–43K **+6.0~7.8%**、4 路 **+4.1~7.5%**；T2 形态逐位 `aac0a04d109391fe`（9/10，一次"重启后首请求"分歧未复现） | L-002 区 |
| S1 并发读补丁（**代码，已在生产二进制里**，见 L-013） | 127K 冷读 49.0/49.6 s → **58.2/58.5 s（读慢 19%）**，但邻居流 **0.3 → 19.7/20.3 tok/s**、最长卡顿 **49 s → 0.9 s**。**这条是 T2 门控配置里的数**（`parallel 1 --batch-groups 1 --pcie-frac 0 --spec 0`），**不是生产形态**；开关靠 env 不靠换二进制。**生产形态的开/关已于 2026-10-24 实测，见下一行与 L-019** | L-004/plan §9.9 + **L-013** |
| **生产形态 gate 开/关（同一二进制，只换 `env.STRATA_PIPED_CONCURRENT`）** | 冷读（一条全新 ~22,700 token prompt）墙钟 ON **14.35 s** 对 OFF **11.8 s** → **读的人多等 21.6%**；旁边那条解码流在冷读窗口内 **47.0 对 2.0 字符/s**、最长 chunk 间隔 **0.70 s 对 11.64 s**；单流 73.0/74.4/74.2、4 路聚合 125.0/124.4/121.2、接受率 78.13/82.33/80.95% **三项都落在噪声里（无结论）** | **L-019** |
| **流越多，gate 开着的账越划算（3 条流做了开/关，4 条只测了开着）** | 3 条流：读的人 17.2 对 11.9 s（**+44.5%**），每条邻居流 **28.3 对 1.3 字符/s**、停顿 **0.76 对 11.72 s**。4 条流（生产满配、只测开着）：读的人墙钟 21.4 s 而引擎只花 **11.3 s** → **大头是排队**；邻居每条 35.8–44.1 字符/s、间隔 ≤0.3 s。**引擎那行 "slots decoded N rows meanwhile" 不能当邻居进度用**（4 流时停在 4，见 §9 第 22 条） | **L-020** |
| **请求监视器打开（`api_monitor`）+ 满槽时排队的机制钉死** | 开销**测不出**（bench 单流 73.8 / 聚合 120.2 / 接受率 79.81%，都在 L-019 三臂带内；`json.dumps` 800K 字符只 2.9 ms）。**满 4 槽时读的人先等空槽**：引擎三行日志的先后（46.3 s 的批次 → 读 14.8 s → `slot 0 takes 22702 tokens`）证明读是在批次收尾后才开始。**真实流量第一批**：墙钟中位 13.2 s、TTFT 中位 0.80 s / p90 2.46 s / 最长 83.82 s（n=34，本会话的 agent 请求） | **L-021** |
| **effort `medium` 不设成默认** | agent 形态 A-B-A 3 轮（14 任务、temp 0、只有这一条流）：墙钟中位 **+0.4%**、completion token **+2.2%**、做对题数两臂中位都 14/14、畸形工具调用 0/9 → **全在 <3% 线内，测不出**；而且**我们自己的客户端显式发 `reasoning_effort: high`**（监视器存了我这条会话的请求体），服务端默认**轮不到**，改了只影响不写 effort 的第三方 | **L-023** |
| **read-ahead 的改动点（静态阅读，未测）** | 引擎中途收新活（只排在飞的毫秒级窗口），读本身跑在自己线程上、最后才 42.6 ms 复制进槽；**唯一障碍是 `BGEN` 下命令那一刻就要有空槽**。改动 = 加一条不要槽的读命令（opt-in）。值：满槽时那条冷读的 **36–43 s**；代价：读改在 4 条流旁边跑（邻居今天已掉 35–60%，多一路掉多少**未测**） | **L-024** |
| **read-ahead 是中等工程，不是一条命令（静态阅读，未测）** | 读本身跟槽无关（两个调用点 13482 / 13710），**卡在 K/V 落地**：准入要先 `kvg_ensure(…, quiesce)`，而 quiesce 要求设备安静（12542 / 15079 / 10514）；会话状态最后才复制（42.6 ms），checkpoint 在槽间搬的机制已经有一半（15436-15448）；parked conversation 是**按槽记账**的（起点不是现成件）。→ **要不要花重编译先过一个判据**：真实流量里有多少请求真撞上"冷读 + 满槽" | **L-025** |
| **DSH 形状每轮首字 1–4 s，其中约 0.7 s 是每次读的固定成本** | `turn-ttft.py` 单流干净跑：冷读 45,017 token = **19.7–19.8 s**（2,275 tok/s）；重读 ≈460 → **1.01–1.09 s**、≈1,835 → **2.2 s**、≈4,535 → **4.0 s**。拟合 `读 ms ≈ 711 + 0.728×token`（**固定 0.7 s + 1,370 tok/s**；45K 大读是 2,275 tok/s，另一个区制）。**客户端首字只比引擎读多 0.05–0.10 s** → 那 0.7 s 在引擎里，不在服务端。热起来后 draft 接受率 ~99%、生成 108–134 tok/s（冷读后第一次只有 66–67 tok/s） | **L-026** |
| **`STRATA_TRACE=1` 拆不到那 0.7 s（单流）** | 两臂（trace 开 / 关）的读时间逐档对得上（1,474/1,466、1,012/1,010、1,081/1,091、2,200/2,205、3,997/3,984 ms）→ **开 trace 的代价测不出**；但 `strata trace: admission` **一行都没打**，因为那条打印在**批量准入**里，而单流走的是 solo `GEN` 路径。**L-028 更正**：每步 trace 行单流也有（`read … (batched) in … ms` 等），只是没有 `admission` 那条——**别只 grep `admission`** | **L-027** |
| **那 0.7–0.9 s 全在 `Prefill::run` 里面（实测拆段）** | 461 token 重读 = `read (batched)` **977 ms** + refill 74 + windows 42 + checkpoint 10 ≈ 引擎自报 1,012 ms；4,528 = 3,664 + 318 + 38；45,010 冷 = 19,213（6 个 7,680 chunk 流水）。**准入 drain 只有 0.2–0.3 ms、首 token windows 恒定 37–42 ms、refill 在读之后** → 都不是。**拟合 ≈788 + 0.409×token**（固定 0.79 s、线性段 2,443 tok/s；小段拟合 947 + 0.600×token → 越小越吃亏）。**与上下文长度无关** → "会话 K/V 要从 RAM 流回来"这条猜测**被实测否掉** | **L-028** |
| **重发同一块 token 不变快 → 固定成本不是专家 staging** | `stage_repeat_probe.py`（45K 上下文，每对"唯一块 + 同样的块再来一次"，块实测 529 token）：唯一干净的一对 **1,226 → 1,246 ms（+1.6%，<3%）**；另两对一行被我自己会话的 200K 请求污染、一行 `read=1` 可疑，**已剔除**。限制：只有一对、两次读之间生成过 ~300 token（专家槽可能已换出）、探针靠 grep 共享日志取数 | **L-029** |
| **那 1 s 的相图：成本在专家权重那几相，唯一能拿掉的是 stage 1 的 `wait copy`** | `STRATA_PREFILL_TIMING=1` 的 21 相记账（先量了工装自己的代价：**+2.5–8%**）。461 token：stage 0 GPU 370 ms（gate/up 111=30%、dequant 63=17%、down 52=14%、kv stage 26、gdn 23、attn 15）、stage 1 GPU 610 ms（**wait copy 185=30%**、down 119、dequant 107）。每 token 效率 461 vs 4,524：gate/up **2.75×**、dequant **6.8×**、down 2.8×、kv stage 9×，而 **qsa attn 不变** → **固定成本=小批次也要把专家权重读/反量化/乘一遍**。冷读 45,010：GPU 16,305 ms，**PLE 1,014 ms 是一次性的**（之后 0）。可拿掉项 = stage 1 的 `wait copy`（≈18% 上界），但它要 `STRATA_MMQ_TAB=ON` + `MMQ_DIRECT=1`，**本机二进制没有**（§9 第 27 条） | **L-030** |
| **`STRATA_MMQ_DIRECT=1`（MMQ tab 构建）买不到东西 → 不采纳** | A-B-A 三臂（现役 / tab+开关 / tab+关），引擎自报小读中位 **1,097 / 1,067 / 1,023 ms**、大读 **3,895 / 3,873 / 3,949**、冷读 19,7xx 三家一样 → **B 对 A −2.7%/−0.6%/−0.1%（<3%，不算结论），B 对同二进制关着的那臂还更慢 +4.3%**。开关确实生效（`48 of 48 layers`）。质量门：3 道贪心题里 2 道三臂逐字相同、1 道自由文本措辞不同。生产保持原二进制（已逐字节恢复） | **L-031** |
| **会话 parking 是"每次切换一次 ~350 ms"，不是每轮 → 不是杠杆** | `park_gain_ab.sh` A-B-A：现役 / `--conversation-cache-park-gain 4096` / 恢复，各 1 次重启。一个 6 轮探针里 park **只有 1–2 次**、每次 **354 / 353 / 639 ms**（1.16 GB 快照），整轮 ~30 s → **1–2%**；gain 4096 在这个形状里**没跳过它**（会话一次就长过 45K）。⇒ 我原先"每轮付 0.35 s"的猜测被否，旋钮不采纳（已逐字节恢复） | **L-032** |
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
| 并发读的收益**取决于当时有没有第二条流**（同一二进制、同一窗口口径） | 10-23 07:04 多路时段 691 次并发读、期间槽解码 **88,766 行**、0 行的读 51%；10-23 19:10 只有主会话 73 次、期间 87 行、**0 行的读 97%**；**10-24 00:00–02:35Z 尾段（mode 8 生产会话内、有子代理在跑）203 次、821 s、期间 **10,062 行**、0 行的读 **46%**、每次活的读中位 57 行（p10 31、max 1,261）** | **L-013 + L-015** |
| 线上源码来历钉死了 | 把 S1 补丁**反向打回去**后两份文件哈希与上游 `011c9e60`（0.2.2 部署点）**逐字节相同** → 线上源码 = 上游 0.2.2 + 只有 S1 那两行，**没有别的未落档改动** | **L-013** |

**mode 8 上线 ~7 小时后的被动读数**（2026-10-24 02:0x UTC 核实）：`active`、`NRestarts=0`、MainPID 56424
（进程 19:10:55Z 起）、引擎子进程 56437、空闲 **850/940**、`.warn` 未触发、当前引擎日志里 `#2065` 警告 **0 条**
（旧的 12 条在 `.log.1`）、cron `*/5` 与 `nvlink-crc-sample.timer` 都在跑、`nvlink-crc.log` 里 crc/replay/recovery
仍全 0。**mode 8 经用户批准留在生产**（2026-10-24 上午）。被动数字（接受率/命中率/截断）见 **L-012**。

## 9. 陷阱清单（每一条都是真踩过的，新会话别再踩）


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
12. **停机 >2 分钟：决定权已在 2026-10-24 交给我**（用户原话"你可以自己做决策，不要问我"）。纪律照旧：低峰跑、
    先 `cp -p` 快照配置、测完逐字节恢复、台账写清停了多久。上一轮 mode 8 那次是没等批准跑完的，用户追认了。
13. **`MainPID` 不是引擎**：`MainPID` 是 python 服务端（它的 `/proc/<pid>/environ` 只有 397 字节基础环境，
    **一个 `STRATA_*` 都没有**，照它读会误判"env 没生效"）。引擎是**子进程**：`pgrep -P <MainPID>` →
    `strata-host` / `strata-vision`，要看这两个的 `environ` 与 `/proc/<pid>/exe`。
14. **服务日志的重启标记行没有时间戳**（`starting the engine: reading the model's weights ...`），
    引擎日志的 `[strata] <ts> engine started:` 有。跨日志切窗口用 `passive_windows.py --marker … --starts …`
    （右对齐贴时间戳），别按行号硬编。
15. **别只靠 mtime 钉"线上源码是哪一份"**：`cp -p` 会保留 mtime。要钉死就用**反向打补丁再比哈希**——
    `patch -R` 之后与上游 blob 逐字节相同才算数（L-013 就是这么定的）。
16. **子代理也是这台机器上的第二路流**（本轮踩到）。本会话的 token 来自这台机器，派出去的子代理打的
    也是同一台。"发起后台任务后立刻 `job_output wait`、等待期间我不生成"这条**只保护主会话**：
    子代理活着的时候它就是那一路并发，单流腰斩照旧（L-014 那次 bench 单流中位 37.4 tok/s，
    比 L-011 的 73.5 低一半，原因就是当时有个子代理在跑）。**规矩：有子代理在跑，别在这台机器上测速度。**
17. **`ggml_cuda_init: failed to initialize CUDA: no CUDA-capable device is detected` 是正常行**：
    2026-10-22→23 那 71 次引擎启动里 **70 次都有**，它来自 **vision 子进程**（CPU、14 线程），**不是 GPU 掉了**。
    看到这句别去查驱动。
18. **native pack 的配置缺 `--prefill` 会被引擎自己拒掉，然后 systemd 每 18 秒撞一次**：
    引擎打 `is a native (IQ) pack: it needs --native SHARD1, --spec T (T >= 2) and --prefill CHUNK` 就退出，
    连撞 11 次（2026-10-23 05:17:56–05:20:54）看起来完全像崩溃循环。**判据：看有没有那句 "it needs ..."**
    （有=配置被拒；没有=真出事）。同一条也说明 `--spec`/`--prefill` 在 native pack 上是硬要求，不是可调项。
19. **A-B-A 里 `--since` 不能传"开始测量的时刻"**：窗口是按 **engine 启动**切的，引擎在你开始测之前 2~3 分钟就起来了，
    于是那个臂的窗口被 `--since` 滤掉、工具**输出为空**，看着像"这一臂什么都没发生"（第一轮就这么丢了两臂的引擎侧数字）。
    要传**重启之前**的时刻。轮转尾文件那种 "start unknown" 窗口不受 `--since` 过滤，别指望它帮你滤。
20. **流式正文在 `choices[0]["delta"]["content"]`**，不在 `choices[0]["content"]`（照后者读会读到 `None`，
    整条流看着像 0 token）。而且 `len()` 数的是**字符**不是 token：这条中文提示词约 2 字符/token，400 token 报成 ~800。
    要真 token 数就看流里有没有带 `usage.completion_tokens`（带了就用它），别把字符/s 当 tok/s 写进结论。
21. **gate 关掉时引擎一行都不打**：OFF 那一整窗是 `reads 0 / refusals 0`——**"没有拒绝行"不能当"gate 开着"的证据**，
    那条路径整个没走也是这个形状。判开关只读 `/proc/<引擎pid>/environ`。
22. **引擎那行 "the slots decoded N rows meanwhile" 不是邻居进度**：计数器 `pipe_rows` 只在 **pipelined slot group
    打出 `BT` 时**才 +1（`src/program/generate.cpp:11826`）。实测：4 条流同时解码时它一直是 **4 行**，
    而客户端同一时刻量到四条流各 39–49 字符/s、chunk 间隔 0.12–0.18 s（L-020）。**判邻居有没有在跑，看客户端
    每条流的字符/s 和 chunk 间隔**，别看这列。（为什么 4 流时它不动，未查清，标"静态阅读，未测"。）
    **L-021 已答**：满槽时读要等空槽，读是在批次窗口收尾后才开始的，所以"meanwhile"只剩尾巴。
23. **`/api/requests` 里的 `queue_s` / `load_s` 在这条路上恒为 0**：它们只累计"控制行 / fifo 那段等待"
    （`serve/server.py:5134-5138`），**槽等待不在里面**。L-021 那轮 26 条请求全写 0.0，而其中一条墙钟 47–59 s。
    判排队看引擎日志行的先后（`strata batch (…): … rows in …` → `read … tokens …` → `slot N takes …`）或 `first_token_s`。
24. **`first_token_s` 从请求到达起算**（`server.py:5203-5205`），**含排队 + 读 + 首 token**：它是用户感知的 TTFT，
    不是引擎 prefill。监视器只在内存里留 100 条、不落盘 → **跑完立刻 dump**（`enable_api_monitor.sh` 就是这么做的）。
25. **`STRATA_TRACE=1` 只在批量准入那段打印**（`generate.cpp:15465` 的 `strata trace: admission …`，紧挨 `slot N takes`
    和 `BADM`）：**单流顺序请求走的是 solo `GEN` 路径，一行 trace 都不会有**（L-027 就是这么空手而归的）。
    想拆单流的开销，要么让请求走 BGEN（并发），要么去读 solo 路径自己的分段。判断"这次跑的是不是批量路径"：
    看日志里有没有 `slot N takes … tokens`。
    **更正（L-028）**：其实 `STRATA_TRACE=1` 在单流下也打**每步**的行（`strata trace: read N tokens (batched) in X ms`、
    `refilled … slots`、`lent …`、`checkpoint at …`、`window …`）——**只是没有 `admission of slot` 那条**。
    所以拆单流的读**不用并发也能拆**，别只 grep `admission`。
26. **`strata serve: KV streaming: 98.5% of … block reads hit VRAM, 149.3 MiB read from RAM` 是解码期注意力的块读统计**
    （每生成一步都要读全部前缀的块），**不是提示词读的时间**。别用它解释"读为什么慢"——L-028 实测：重读 1,835 token 在
    ctx 49K 与 53K 都是 2.20–2.23 s，重读 4,535 token 在 58K/62K/67K 都是 3.98–4.05 s，**与上下文长度无关**。
27. **`STRATA_MMQ_DIRECT=1` 在线上那份二进制里是空操作**：build 没开 `STRATA_MMQ_TAB`，凭据是它的字符串里有
    `STRATA_MMQ_DIRECT=%d is ignored: this build has no pointer-table MMQ`（说明 `tab_built()` 是 false）。
    **别再用 `strings | grep prefill_mmq_tab` 判这件事**——那个字符串两份二进制里都没有（它只是测试程序的名字），
    我第一次就是这么误判的。要它得重编译：`cmake -DSTRATA_MMQ_TAB=ON`（`build-mmqtab/` 已经建好并通过，
    二进制 95,188,296 B，比原版 **+16.1 MB**；每卡多 36–38 MiB VRAM）。**注意 `prefill_mmq_tab_test` 在 sm_75 上
    直接 skip**（`needs a CUDA device of sm_80 or newer`）→ 逐位门在本机做不了，只能用同题贪心输出比对代替。
28. **换引擎二进制时，路径里必须含 `build/strata`**：`use-config.sh` 靠 `pgrep -f 'build/strata --serve'` 找引擎，
    把新二进制放在 `build-mmqtab/strata` 这种路径下，装完会等 120 s 后报 `RESTART NOT VISIBLE`（我这么白重启过一次）。
    现在的做法：`/opt/ai/engines/mmq-tab/build/strata`。
29. **`STRATA_HC_PERSIST=1` 和 `STRATA_HC_Q8=1` 互斥**（L-042）：`fused_gr_read_persist` 见到 `q8_down/q8_up`
    非空就返回 false，而 `HC_Q8` 正是把它们填上的那个开关。我们采纳了 `HC_Q8`，所以**持久化 hc 内核对我们是死的**——
    别再单独测它；要比只能换基线（`HC_Q8=0 + HC_PERSIST=1`）整体对比。



## 10. 工装（源码在 `tools/server/`，部署在 `/opt/ai/bin`）

**手册在 [`tools/server/README.md`](../tools/server/README.md)**：每个脚本干什么、属于哪一类（`ops-` 改机器 /
`exp-` 要重启 / `probe-` 只读）、写到哪里、对应哪条台账。改脚本的规矩也写在那份文件里。

三条必须记住的：

1. **git 是源码，`/opt/ai/bin` 是部署副本**，两边会静默漂移；动之前先跑 `tools/server/ops-check-tool-sync.sh`
   （41 个文件逐个比 md5，漂移会明说）。
2. **实验驱动脚本必须是"快照 → 安装 → 测量 → 逐字节恢复 → `cmp -s` 验证"**，并且在装失败时先恢复再退出。
3. **换引擎二进制时路径里必须含 `build/strata`**（§9 第 28 条），否则 `use-config.sh` 找不到引擎。

**服务器自己的文档（2026-10-24 重写过，别按旧路径找）**：`/opt/ai/docs/` 现在是四份现行文档 + `archive/`——
`README.md`（索引与当前状态）、`RUNBOOK.md`（起停/回滚/核实/日志/资源红线/故障处置）、
`PARAMETERS.md`（当前参数值与理由、请求级四键、**不要再捡的项**、15 个 `STRATA_*`）、`HISTORY.md`（10-11 起的时间线）；
`archive/` 里是逐日旧文档（含 8 份 `.bak`、vLLM 时代、旧索引），只读。旧 `/opt/ai/README.md` 那 282 行也进了 archive，
顶层现在是 43 行的指针。systemd 单元的 `Documentation=` 已指向 `HISTORY.md`。

**服务器上的源码树（2026-10-24 清理过）**：`/opt/ai/src` 现在只有 `strata-0.2.2`（在线）、`llama.cpp-3cf03257`
（ggml 依赖）、`strata-0.1.40.1`（**运行时依赖**：`.venv` / `strata-vision` / `expert-profile.bin`，别删）、
`server-dashboard-0.2.6`。13 个过时树（0.1.45、0.1.45-tp、0.2.0-beta、0.2.0-tp、上游 main 快照、
llama.cpp-v0.4.1、dashboard 旧版）已删，释放 4.8 GB；抢救出来的东西在
`/opt/ai/backups/outdated-src-20261024/`（`MANIFEST.md` 说清删了什么、为什么、留了什么）。
**本机唯一一处本机代码优化（TP 张量并行）已归档到 [`patches/local-strata/`](../patches/local-strata/README.md)，
判定不合并**（L-033：只作用单流窗口、+3% 在噪声线内、移植要跨两个上游版本）。

---

## 11. 上游与分支状态


- fork `atmJava/Strata`，`main` 与 **`upstream/main` 同一个提交**：**`8183c6b6`**（= `v0.2.4.1` `c33fa7ba` 之后**一个 docs 提交**，14 行；引擎 0.1.49.1），2026-10-24 合并。
  中间跳过了 `v0.2.3` / `v0.2.3.1` / `v0.2.4`：`v0.2.4.1` 的 259 个提交里，**我们跑的 Qwen 路径的引擎文件一个都没动**
  （`src/program/generate.cpp`、`src/prefill/prefill.cpp`、`src/core/verify.cpp`、`src/core/weights.cpp`、
  `include/strata/program/batch_read.hpp` 全部零改动），新东西是 DeepSeek-V4.1（`src/dsv41`、`tools/dsv41`）、trellis 工具、
  Terminal 测试与打包；`serve/server.py` 的 +119/−9 也只有 DeepSeek 分支（`arch == "deepseek41"`）与新的
  `engine_ready_s` 配置键。
  **唯一碰到我们路径的是 CPU 专家/kernel**：`src/kernels/cpu` + `include/strata/kernels/cpu` 13 个文件 +920/−86
  （AVX2/AVX512 的 IQ 与 TQ kernel、`expert_layout`、`native_expert`）——这是我们 `--native` + CPU 专家池走的路，
  **部署 0.2.4.1 可能有解码收益，但没测**（见 §13）。
- 工作分支 **`hw/server-adapt-0.2.3`**（远端 tip `c6a34f67` = 两个 merge commit：`v0.2.4.1`、再对齐 `upstream/main`；
  之前是 `4b3811dc`）：文档 + 工装 + `patches/local-strata/`。**用 merge 而不是 rebase**（rebase 要 force push，不允许）。
  旧的 `hw/server-adapt`（`bad482e7`）是 rebase 之前的历史，**别再往它推**。
- **`hw/s1-concurrent-read`**：远端 `11cc25bd`（0.2.2 基），本地 `tmp/rebase-s1` = `b9b3f995`（已 rebase 到
  0.2.3.1）。**更正（L-013）**：那两行**已经在线上引擎二进制里**，所以"上生产"**不需要重编译**，
  要决定的是 env `STRATA_PIPED_CONCURRENT` 开还是关、以及在**生产形态**补一次 A-B-A。
- 上游现状（2026-10-24 用 API 核过）：**#2275** open、11 评论、最后一条 Adamyno 10-22 21:09Z；
  **PR #2326 已 closed**（Niko1221 10-19：改动已在 0.2.0-beta 的 `main`，跟踪器重置所以关闭）；
  **#2197** open、33 评论、最后一条 rafatxf 10-23 21:54Z（在核 yusheng227507 的 DFlash2 那一行到底跑的是
  MTP 还是 DFlash2）；**#3724** open、**0 评论**（我们没去贴，内容只是"同 pin 确认不含、适用性存疑"）。
  提到 `EXP_MODE` 的另外两条 #2069 / #2355 都是 closed，mode 4/0/8 的对照就在 **#2197**。
- **2026-10-24 上午按用户批准发过三份评论（A→#2275、B+C→#2197），随后用户改了规矩：
  上游只读，不回帖、不开 PR、不提交。三条已于 03:0xZ 删除**（DELETE 都回 204，再 GET 都是 404；
  证据见台账 L-018）。**以后对上游只查不发**：查 issue/PR 避免重复做，结论一律落在我们自己的台账里。
- rafatxf 2026-10-24 02:19:34Z 在我们 B/C 底下回过一条
  [#2197](https://github.com/Niko1221/Strata/issues/2197#issuecomment-6378674185)：这台机器是新 CPU 级别、
  两条都收进 body、**mode 8 的 +8.5%/+7.9%（对 0.7% A/A2 抖动）是他目前见过最大增益**、并接受我们
  "`--spec` 在 native pack 上关不掉、门控的其实是 spec 窗口"那条。**他反问两个便宜检查**：
  (a) 比那次分歧启动与正常启动的 `prompt chunk auto` 行；(b) 钉 `--prefill N` 再重启几次。
  **(a) 已做完（L-017）：chunk 从来没变过（53/53 都是 8192 tokens / 96-slot ring）。**(b) 要重启。
  **他那条评论是他的，我们不动**；我们不再回（草稿 E 留在文档里不发）。

## 12. 用户已表态 / 仍未决（别自己决定）


**已表态（2026-10-24 上午，按时间顺序）**

1. **mode 8 留在生产**（不认可才回退，见 §6）。
2. 草稿怎么处理：**先写 C、三份一起发** → 三份都发了。
3. **随后改了规矩：上游只读**——不回帖、不开 PR、不提交任何东西；**已发的三条已删**（§11、台账 L-018）。
   草稿 D/E 因此**不再发**，只留档。
4. S1 的方向：**并发共存优先**。既然那两行已在生产二进制里（L-013），下一步是在**生产形态**测
   `STRATA_PIPED_CONCURRENT` 开/关，而不是"要不要上 S1"。
5. **GitHub token 换发了新串**（§5）。
6. **停机 >2 分钟的决定权交给我**（原话"你可以自己做决策，不要问我"）。仍按纪律做：低峰、先备份、
   测完恢复原状态、台账写清停了多久（§1 第 6 条）。
7. **服务器上那两个 root 所有的旧脚本：批准用 sudo 换**。已于 03:02Z 换好（旧的备份成
   `*.pre-20261024`），换完与 `tools/server/` 里 md5 一致、两份都能跑。

**仍未决 / 需要用户点头才能动**

1. **服务器 sudo 密码仍在公开仓库里**：用户回新 token 时没答这条。本轮已获准**用它办事**，
   但"密码要不要换掉 / 挪出 git / 改写历史"**仍未定**。别擅自改、别改写历史。
2. **停机 >2 分钟**这条已经交给我了，剩下要重编译的（例如 #3724 那条 backport）也归我决定——
   但 L-016 的静态判定是"收益预计噪声级"，**不打算为它花重编译**。
3. 目标正文要改仍需人类轮次（`update_goal action edit` 在自动续行轮里被拒）。想改的点：思考预算降级、
   mode 8 与 medium 提前、#3724 改成"已判、不花重编译"、S1 改写成"已在二进制里、待测生产形态开关"、
   以及**新加的"上游只读"**要不要写进目标正文。

## 13. 未决 / 待观察

**（2026-10-24 夜，多路融合立项）** 计划见 [`MULTIPATH_PLAN.md`](MULTIPATH_PLAN.md)（W1–W4），
功能清单与验收见 [`FEATURE_PARITY.md`](FEATURE_PARITY.md)，口径见 [`MULTIPATH_STANDARD.md`](MULTIPATH_STANDARD.md)，
执行规则与每轮复盘见 [`MULTIPATH_RULES.md`](MULTIPATH_RULES.md) / [`MULTIPATH_LOG.md`](MULTIPATH_LOG.md)。
目标 `goal-402218d9-57d7-4af1-aea0-391a1494acd9`（active，上限 1000 轮）。
**当前轮：R2 完成（W2 读分片臂否决，L-047；已恢复 A 并逐字节一致）；下一轮 R3 = W3 构建（独立 `build-mp/`，默认关补丁 + 逐位/质量门）。**

**（2026-10-24 夜更新）helper 这条线已经判完并关闭**：`--prefill-pipe 1` 时真实请求不进 `bind_stage_helper`
（`planned_C < n` → `single_chunk=false`），`--prefill-pipe 0` 时才进；而启动时 `split_help_env()` 让 **flag 文件优先于
env**，我自己的 `prefill_help=0` 把它关了 —— 两个条件同时满足后 helper 真的把行分给了对端。
同 boot 配对结果：**1.5k −3.0/−3.9%（4/4 胜，但 <5% 不算结论）**、**4.5k +4.8/+18.9/+8.1%（0/6 胜）** → 我们的负载
（4.5k+ 新增）没有收益，**helper 关闭**，`prefill_help=0` 保持。

**（2026-10-24 夜，S3–S5 已做完）** 窗口时间的账结清了：每窗口 11–14 ms 里 **9.25 ms 是"等 GPU"（65–73%）**、
2.8 ms 是 CPU 池、**1.2–2.0 ms 是 host staging（8–14%）**，其余 commit。
`host staging` 的现成重叠机制（Foresight swap space）要 **2.27 GiB 显存**，而这台机器的 44 GB 已全给驻留专家 → **不成立**。
"等 GPU" 用新探针量出是 **48 层 × 0.37 ms、完全均匀**（最慢 L1 0.50 ms），**没有热点** → 调层数/调参都没用。

> **（2026-10-24 夜，S6 之后）decode 的调度线也关掉了**：`STRATA_SPLIT_OVERLAP=1`（源码里现成的共享 flag 交接）
> 实测每层等待 **17.63 → 19.3 ms（+9%）**、decode 在噪声内 → 不采纳。至此 **decode 的四条调度候选全部有实测且全部否决**
> （helper、显存换重叠、调层数、overlap 交接）。
>
> **（2026-10-24 夜，并发实测 —— 这是今晚对部署最有用的结论）** 同一台机器、同一份配置：
> **单流 63 tok/s；三流并发 136 tok/s（每流 45，聚合翻倍有余）**。单流 22–30 ms/窗口里
> `GPU-reach wait` 只有 8.5–9.3 ms，其余是**每 token 的固定开销**（CPU 池 + 主机环节 + 帧粒度）。
> 所以 **`waitCPU` 1.56 ms 是轻载假象**，不是稳态损失；`--batch 4 / parallel 4` 早就准备好了，
> **缺的是客户端并发**。→ 部署侧（并发请求）能立刻拿到的收益，比任何单流内核改动都大。
>
> **代码侧现状**：单流延迟由架构固定开销主导（每 token 一次窗口、每层一次交接），
> 今晚 6 条候选全部实测否决后，**没有一条 ≥5% 且可安全落地的代码改动** —— 这是结论，不是没做完。

**代码计划见 [`CODE_PLAN.md`](CODE_PLAN.md)**（v2：P0 定位病因 → P1-A/B/C 三选一 → P2 head 分卡 → P3 结构级；含测量协议与不做清单）。

**代码优化的现状（2026-10-24 第 4 轮结束时）**

- **已关掉的路**（都有实测）：hc 三个开关（`HC_Q8` 采纳 +3.8…+7.1%；`HC_PERSIST` 源码互斥；`QFUSE` −8…−11%）、
  层切分配平（24 最优，23 是 −13%，25 是 −0.5%）、专家内核微基准（只到 29–40% 峰值带宽，现成 EXPDEC 在 T=4 只省 4–6%、
  T=1 反而慢 23%）、TP 补丁（只单流 +3%）、MMQ 指针表（端到端 0）、parking（1–2%）、read-ahead（真流量很少撞满槽）、
  换 0.2.4.1（无收益）。
- **还剩的候选与各自的上限**：head 的 vocab 劈到两张卡（~3%，**低于我们的噪声**）；给 decode 专家写 IMMA 内核
  （相图里那一相 12.6%，就算内核翻倍也只有 ~6%，工作量大）；`--kv q4_0`（KV 字节减半，但有精度风险，属判据②）；
  `STRATA_PF_FUSED_SM75`（本机型不适用，L-041）。
- **量级现实**：同一配置跨 boot 的 4 并发漂移量到过 **9%**（L-040）——**小于 ~5% 的改动我们根本证明不了**。
  所以要么找 ≥8% 的结构级改动（`--pipeline-windows` 是目前唯一一个），要么就把力气转到降低噪声（更多臂/更长测量）
  或别的判据上。

**已经结掉的（别再当未决做）**

- ~~命中率掉了~~：那是**采样器小样本噪声**。服务日志侧 mode 8 生产窗口命中中位 98.70（n=101）对 mode 4
  窗口 98.90（n=1,110），p10 反而更好、最差值从 70.80 抬到 95.70。**以后判命中率用 `passive_windows.py`
  读服务日志，别读 CSV 那一列**（L-012）。
- ~~`--spec` 接受率没数~~：引擎每请求都写了 `drafts accepted A of B`，工装没读而已。补完之后是
  **mode 8 没有可测变化**（带内 +1.2% / +0.8%，3% 线内，L-012）。
- ~~S1 要不要上生产~~：那两行**已经在生产二进制里**（L-013）。

- ~~S1 要不要上生产~~：那两行**已经在生产二进制里**（L-013）。
- ~~生产形态 gate 开/关值不值~~：**已测（L-019，2026-10-24，同一二进制只换 env）**。代价落在**读的人**
  （22.7K 冷读墙钟 14.35 对 11.8 s = **+21.6%**），收益落在**旁边每一条正在解码的流**（最长停顿
  **11.64 s → 0.70 s**，2.0 → 47.0 字符/s）；单流、4 路聚合、draft 接受率**三项都测不出差别**（臂间抖动盖过臂间差）。
  **gate 保持开着**——这也正是用户定的"并发共存优先"。
- ~~gate 开着在多条流下还划不划算~~：**3 条流做了开/关（L-020）**：读的人 +44.5%（17.2 对 11.9 s），
  每条邻居流 28.3 对 1.3 字符/s、停顿 0.76 对 11.72 s → **划得来，而且流越多越划得来**。4 条流只测了开着的形状
  （邻居 35.8–44.1 字符/s、间隔 ≤0.3 s；读的人墙钟大头是**排队**不是读，见 §13 第 5 条）。
- ~~medium 思考预算要不要设成默认~~：**不做（L-023）**。agent 形态 A-B-A 3 轮：墙钟 +0.4%、token +2.2%、
  做对题数两臂都 14/14、畸形调用 0/9 → 全在 <3% 线内；而且我们自己的客户端显式发 `high`，服务端默认轮不到。
- ~~`pipe_rows` 为什么 4 条流时停在 4~~：**已答（L-021）**。引擎日志的先后是 `strata batch (pipelined, 2 groups of 2):
  4795 rows in 46303 ms` → `read 22695 tokens … in 14787 ms` → `slot 0 takes 22702 tokens`：**满槽时读要等空槽**，
  读是在批次窗口收尾后才开始的，所以"读期间槽还在解码"只剩尾巴 4 行。这列在有空槽时（≤3 条流）照样能用。

**还欠着的（按价值排序）**
1. **#3724 到底适用不适用** **已判掉（L-016，整条是"静态阅读，未测"）**：那两行在本机**只被
   prompt 专家（gather 路径）和 drafter 的 Q8_0 投影走到**；**decode 专家不进 MMQ**（走
   `iq_kernels.cu` 的 `native_expert_grouped`）、**dense 层也不进**——我原先在 §13 旧版里猜"dense 要过 MMQ"
   **是错的**，dense 的 MMQ 入口是 **HIP-only**（`gemm.cu` 里 `#if ... && defined(__HIPCC__)`）。
   形状推算 `nwork ≥ 100 > nsm(68)` → 与今天完全一致，只有 down 的"1 专家尾组 + `ntx=1`"会少开 8 个 block。
   **结论：改动会被走到，但收益预计噪声级 → 不为它花重编译 + 停机**（停机的决定权用户已交给我，§12 已表态 6）。
   真要花一次重启，值的是 `STRATA_PREFILL_MMQ=0` 对默认（证明 prompt 到底走不走 MMQ、多大），不是 backport。
   另记一条 build 坑：Strata 把这个调度器**镜像**在 `moe_mmq_tab.cuh:427`，CMake 对 `mmq.cuh:875..1571`
   做指纹，backport 会让 `STRATA_MMQ_TAB` 自动关掉（今天它 OFF，所以暂无影响）。
   **Draft D 写好了，按"上游只读"的新规矩不再发**（`docs/UPSTREAM_DRAFTS.md`）。
2. **`/opt/ai/bin` 剩余脚本继续搬进 git**：本轮搬了 `passive_windows.py`、`concurrent_reads.py`，外加 9 个
   判据脚本（`nvlink-crc-sample.sh`、`t2check.py`、`thinking-budget-ab.sh`、`soak-analyze.py`、`warm-measure.py`、
   `read-gap.py`、`metrics-peek.py`、`verify-running-config.sh`、`check-session-warnings.sh`）。
   **(a) 已结**：服务器上那两个 root 所有的 `verify-running-config.sh`、`check-session-warnings.sh` 已于 03:02Z
   用 sudo 换成 git 里修好的版本（用户批准），旧的留成 `*.pre-20261024`，换完两边 **md5 一致**且两份都能跑。
   **(b) 还留着**：`restart-strata.sh`、`swap-engine.sh` 里各有一句明文 sudo 密码，
   **在密码那条定下来之前别搬进公开仓库**（§12 未决 1）。
   **(c) 本轮又搬了 7 份**：`turn-ttft.py`、`conv_cache_ab.py`、`probe_determinism.py`、`quality_eval.py`、
   `effort-cost.py`、`effort_lang_ab.py`、`replay_effort_ab.py`（为什么选这 7 份、哪些历史脚本故意不搬，见 `tools/server/README.md` 的 Deliberately not in git 一节）。
3. **`verify-running-config.sh` 现在是"线上到底跑的是哪份配置"的标准答案**：它比 cmdline/env/进程时间三样。
   本轮跑出来的事实是——引擎 cmdline **59 个 arg**、配置里 **54 个**，多的三个是服务端注入的
   `--serve`、自动算的 `--layer-split 24`、以及从 `parallel: 4` 推出来的 `--batch 4`；ENV 一致、进程比配置新
   → **线上跑的就是磁盘上这份**。以前那份脚本只认前两个注入项，会把正确状态报成 DIFFERENT。
4. **被动窗口继续长样本**：mode 8 那个 19:10:55Z 的会话**已被今天十一次重启切断**；当前会话从 **05:10:53Z** 起，
   里面目前基本是实验自己的流量（上一个窗 03:34:17Z：45 个请求、接受率 78.25% of 901——小样本，别当结论）。
   等它攒够几百条**真实**请求再回看同一张表（截断率、suffix draft 接受率、命中率那三列）。
   注：L-026 已经把 DSH 形状的首字/重读/接受率单独量过一遍（热起来后接受率 ~99%），被动窗口值在**跨天趋势**。
5. **读路径的固定成本：已拆完、已试过一个补丁、**不采纳**；下一步换方向（不在这条路上继续花重启）。**
   - **读的相图（L-030）**：461 token 的读 = **1,008–1,024 ms**；stage 0（370 ms）= gate/up 111 + dequant 63 + gemm down 52；
     stage 1（610 ms）= **wait copy 185** + gate/up 119 + dequant 107。每 token 效率 461 对 4,524：gate/up 2.75×、dequant 6.8×、
     kv stage 9×，**qsa attn 完全不变** → 固定成本就是"几十个 token 也得把专家权重读/反量化/乘一遍"。PLE（908–1,014 ms）是一次性的。
   - **唯一能拿掉的 185 ms 已经试过（L-031）**：重建了 MMQ tab 二进制（`build-mmqtab/`，+16.1 MB），`STRATA_MMQ_DIRECT=1`
     生效（48/48 层），**但端到端没有收益**（小读 −2.7% <3%；对同二进制关着的那臂反而更慢 +4.3%）→ **生产保持原二进制**，
     `build-mmqtab/` 与 `/opt/ai/engines/mmq-tab/build/strata` 留盘备用。**这条路到此为止，别再花重启。**
   - **方法论教训（L-031）**：同形状同二进制、三个臂的小读中位跨度 **7%**（1,023–1,097）→ 跨 boot 比小读时，
     "<3% 不算结论"应当按 **~7%** 看待。
   - **read-ahead（仍降级，L-024 / L-025）**：排队在服务端 `pick_slot`，引擎也不接无槽的 BGEN（12007），没有"少准入"的旋钮；
     改动是中等工程，而且排队只在"满槽 + 冷读"时咬人（真实流量 TTFT 中位 0.80 s）→ **不优先**。

6. **4 条流的"关着"对照**：L-020 只有 3 条流做了开/关；4 条流（生产满配）只测了开着。`gate_prod_streams.sh 4 3 60000`
   直接能跑（要 2 次重启 ≈ 300 s，决定权在我，§12 已表态 6）。**注意**：L-021 已经说明满槽时读基本不等重叠，
   所以这条对照的预期差别比 3 流那轮小——做之前先想清楚它还能回答什么。
7. **`t_zhfinal` 那种"中文答到半句就停"**：L-023 里 high 臂 6 次运行栽 2 次（答到 `这台机器的驱动版本是 595.` 就收住，
   completion 只有 81–87 token，**不是 max_tokens 截的**）。n 太小，**别下结论**；以后跑 agent harness 时顺手看它出不出。
8. **上游要的第二项检查 (b)**：钉 `--prefill N`（N 大于被测提示词）再重复几次重启。今天确实重启了 7 次，
   但**没有钉 `--prefill`**（还是 `auto`），所以 **(b) 仍未做**；下次要重编译或调参数时并到同一个停机窗口最省。
   第一项 (a) 已做完（L-017：chunk 53/53 都是 8192/96-slot；今天这七次重启也照样都打 auto）。
   顺带那个小尾巴仍未查：71 次启动里有 **7 次既没打 chunk-auto 也没打配置拒绝那句**
   （`10-22 04:48:13`、`06:55:29`、`10-23 05:21:12`、`05:24:24`、`05:27:44`、`06:57:28`、`18:38:40`），
   **为什么这 7 次没打，不知道**。

## 14. 目标状态


会话恢复后旧 goal 读不到了（`get_goal` 返回 null）。2026-10-24 上午用户说"列个全局目标执行"，于是
**新建** `goal-c5cedf2a-32ed-478e-a639-b0c5a4127d53`（**active**，上限 60 轮）：正文里写死了首要指标、
判据规矩、上游兼容规矩、"停机 >2 分钟先问"、"每轮末尾更新本页"。**旧 goal
`goal-96ec4721-4579-431f-bf5c-81dfa8e36a2b`（paused，rev 3，4/256 轮）作废，别再 resume 它。**
会话被 fork/恢复后目标会自动 disarm：用户说继续时用 `update_goal` 的 `action: resume`（**需要用户的直接轮次**）。

**目标正文里有两条已经被用户后来的话改掉了，但正文我改不了**（`update_goal action edit` 在自动续行轮里被拒，
要人类轮次）：(1) 上游从"兼容地回帖"变成**只读、不回帖、不开 PR**；(2) **"停机 >2 分钟先问"变成用户交给我自己决定**。
下一个用户轮次顺手把这两条写进正文（顺带可以决定要不要把"思考预算降级 / mode 8 与 medium 提前 / #3724 已判不重编译"
这些也写进去）。在那之前按本页 §1 与 §12 的写法执行，它们是最新的。

## 15. 一句话给接手者


**先确认机器活着、cron 在跑、mode 8 还在生产，再动手；动手前想清楚要不要停机；
所有数字都要能追到 `/opt/ai/logs` 或 `/opt/ai/runs` 里的原始文件；不要相信任何"看起来跑完了"的日志。**
想要新的数字而不停机，先跑 `passive_windows.py` / `concurrent_reads.py`——这台机器每天自己就在写
接受率、命中率、截断、并发读邻居进度，上一轮缺的数有相当一部分**早就写在日志里，只是没人读**。
