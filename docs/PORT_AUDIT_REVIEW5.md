# 移植审查（第 5 轮）：`61c1bfc9` / `b8eb9a73` / `e7f35829` + 四份审查报告

> 评审对象：HEAD `e7f35829`（`61c1bfc9`、`b8eb9a73`、`8dbd89da`、`e7f35829`）与
> `docs/PORT_AUDIT_COVERAGE.md`、`PORT_AUDIT_DEFAULTS.md`、`PORT_AUDIT_MEASUREMENTS.md`、`PORT_AUDIT_TESTS_CPU.md`。
> 只读评审；未 commit / checkout / reset / stash / clean；未碰 `strata.service`、`/opt/ai/src/strata-0.2.2`、`strata-mp`；
> 未重启服务、未改服务配置。唯一新建文件就是本页。
>
> 行号约定：**一律按 HEAD 提交内容**，不按工作区。评审开始时工作区是 15 改 + 15 未跟踪；写本页期间工作区
> 又出现了新的未提交改动（`git status` 现在可见 `docs/DETAILS.md`、`docs/VLLM_PORT_PLAN.md`、`src/core/verify.cpp`、
> `tests/cuda/qsa_select_rows_parity_test.cpp` 等）。**本页评审的是三个提交，不把这些后续改动算作已修**；
> 引用它们的行号时以 HEAD 为准（`generate.cpp` 的 CLI 段工作区约 +123 行；`sm75_series.hpp` 的
> `STRATA_PF_FUSED_SM75` 从 HEAD:14 移到工作区:18）。
> 证据标记：**[实跑]** = 本次命令输出；**[nm]** = 目标文件符号；**[静态]** = 读码/逐行对比，未运行。

---

## 0. 结论

**不能合（按现状）。** 代码方向正确：不设新 env、不用层切分、不用 DFlash2 时，没有看到默认路径的数值、
显存或 stderr 回归；`61c1bfc9` 修的三件事（7.5 不请求时不再 setup/banner、无 `%d` 不再互相截断、taps 顺序不符时
关掉 DFlash2）本身成立。阻断的是**“声称修了但没修”**、**“注释/提交信息与代码相反”**、**“回归测试可以空跑”**
以及**文档数字与自己的实测报告对不上**：

1. 提交信息和 §6.1 说 `dflash2.hpp` 的 `load` 旧 3 参重载补回来了；HEAD 上 `dflash2.hpp:52` 仍是 4 参 + 默认实参，
   没有 3 参符号（`nm` 只有 4 参）。`session_bytes`/`session_init` 的旧符号确实在，这一半是真的。
2. 提交信息说无 `%d` 时“整模型（或末 stage）”写文件；代码 `verify.cpp:870-871` 只允许**整模型**
   （`lb_==0 && le_==g.n_layers`）。层切分的末 stage（`lb_>0, le_==n_layers`）既不写文件，也没有一行提示；
   `verify.hpp:575-582` 还写着“reaches the head 的 stage 追加”。
3. `qsa_select_rows_parity_test` 的两个子进程都只调用 `qsa_block_scores`（测试 `:441`），没有任何断言证明
   `STRATA_QSA_ROWS=1` 子进程真的进了 `block_scores_rows_kernel`；把 dispatch 里的 rows 分支删掉，测试仍会
   PASS（两个子进程都跑默认路径，memcmp 相同）。
4. `e7f35829` 的 P3b R=626 写 33.0/25.6，实测报告是 32.2/26.9；P4 冷启动 scores/topk 的 T=3/4 仍是旧值，
   摘要行还写“冷启 18.1 / scores +4.6”；§6.1 把“数字按重测校正”归给 `8dbd89da`，实际是 `e7f35829` 自己。
5. `61c1bfc9` 去掉了 7.5 默认 setup/banner，但 `include/strata/prefill/moe_fused_iq.hpp:4-6` 和
   `docs/DETAILS.md:2548` 仍写“7.5 卡默认 setup + 一行 banner”。

| 提交 | 判定 | 一句话 |
|---|---|---|
| `61c1bfc9` | 行为可接受，声明不实 | 三处默认/边界修复成立；`dflash2.hpp` 没动、verify 的“末 stage”没实现、用户文档没同步；`STRATA_PF_FUSED` 的解析与消费者不一致。 |
| `b8eb9a73` | 可跑，但有一处可空跑 | 三目标存在、数字属实（839,740 / 61,530，0.90 s，312/183）；CPU-only 模式是转写自测；QSA 回归没有“rows kernel 真的跑了”的正向证据。 |
| `e7f35829` | 部分正确，数字需重做 | R=160 与长上下文新事实正确；R=626、P4 冷启动分量、25% 的口径、§6.1 归属与报告不符。 |

**最小可合条件：M1–M5 全修（其中 M1、M2 是硬阻断）。**

> 写本页期间工作区出现了后续未提交修复（`docs/DETAILS.md`、`docs/VLLM_PORT_PLAN.md`、`src/core/verify.cpp`、
> `include/strata/core/verify.hpp`、`tests/cuda/qsa_select_rows_parity_test.cpp` 等，`git status` 可见），
> 方向对应 M2–M5。它们**不在本轮三个提交里**，本页不把它们算作已修；若要合入，请按新的 diff 重跑 M2–M5
> 的验收（尤其 M3 的 `%d`/警告和 M4 的 rows 计数）。

---

## 1. 必修（按严重度，≤6 条）

### M1【P0｜构建/声明】`dflash2.hpp` 的旧 3 参 `load` 从未补回，提交信息和报告却说补了

**证据**

- `include/strata/core/dflash2.hpp:52`：
  `bool load(const std::string& pack_path, int device, std::string& err, const Dflash2Drafter* shared = nullptr);`
  —— 只有一个 4 参声明，没有 3 参重载。
- `git show b009fab5^:include/strata/core/dflash2.hpp:45` 是 3 参 `load(..., std::string& err);`；
  `b009fab5` 加了 `shared` 默认实参后，3 参的 mangled 符号就没了。
- `git show 61c1bfc9 --numstat`：只改了 `session.hpp`、`session.cpp`、`verify.cpp`、`moe_fused_iq.cu`、
  `generate.cpp`；**`dflash2.hpp` 不在里面**，但提交信息第 4 条、`docs/VLLM_PORT_PLAN.md:399`、
  `8dbd89da` 的提交信息（“4 处需修……已在 `61c1bfc9` 修掉”）都说 `load` 补了。
- **[nm]** 服务器 build75 `libstrata_engine.a`：
  - `T strata::core::Dflash2Drafter::load(std::string const&, int, std::string&, Dflash2Drafter const*)`（4 参，仅此一个）
  - `T strata::core::session_bytes(..., long, long, long, long)` 与 `...llllb`（5/6 参都在）
  - `T strata::core::session_init(..., long, long)` 与 `...long, long, SessionState const*`（7/8 参都在）
- `docs/PORT_AUDIT_DEFAULTS.md:143-150` 的 3.4 把三处（`dflash2.hpp:52`、`session.hpp:118,129`）列为一组；
  实际只修了两处。

**问题**

- 旧 `.o` 若引用 `_ZN6strata4core14Dflash2Drafter4loadERKNSt7__cxx1112basic_string...EiRS7_`（3 参）仍会链接失败；
  声明“已修”会让下一次增量构建的失败被误判成新 bug。
- **即便补上 3 参符号，也不等于恢复 ABI**：`Dflash2Drafter` 在 `b009fab5` 里加了成员，`SessionState` 在
  `session.hpp:74` 加了 `rope_donor`，类布局已经变了。旧 `.o` 用旧布局 + 新库是未定义行为，比链接错误更糟。
  `docs/PORT_AUDIT_DEFAULTS.md:147-148` 自己也写了“类布局也变了”；报告和提交信息应把结论改成“陈旧 `.o` 只能删，
  不要指望重载救”。

**最小修法**

- 首选：**不改代码，改声明**。在 §6.1 / `PORT_AUDIT_DEFAULTS.md` 3.4 / 后续提交说明里写清：
  `session_bytes`/`session_init` 旧符号已恢复；`Dflash2Drafter::load` **未恢复**（且因类布局变化，恢复也不安全）；
  升级后必须清掉旧 `.o`/增量构建目录。
- 如果确实要补：在 `dflash2.hpp` 加
  `bool load(const std::string&, int, std::string&);`（3 参），把 4 参声明上的 `= nullptr` 去掉，并在
  `dflash2.cpp` **给出非 inline 的定义**（inline 放在头文件里不保证为旧 `.o` 发出符号）；
  同时仍在文档里保留“旧 `.o` 不安全”的警告。

---

### M2【P0｜文档/测量口径】`e7f35829` 的 P3b/P4 数字与 `PORT_AUDIT_MEASUREMENTS.md` 对不上

**证据（逐项）**

| 位置 | 文档写的 | `PORT_AUDIT_MEASUREMENTS.md` 原始输出/对照表 | 结论 |
|---|---|---|---|
| `VLLM_PORT_PLAN.md:138` | R=626 iq3_xxs down **33.0** | `:316` 32.2；`:329` 32.2；`:410` 32.2 | ✗ |
| `VLLM_PORT_PLAN.md:139` | R=626 iq4_nl **25.6** | `:314` 26.9；`:330` 26.9；`:410` 26.9 | ✗ |
| `VLLM_PORT_PLAN.md:137` | R=12 **9.3 / 9.8 / 8.2** | `:329-330` 8.0 / 8.5 / 7.5（gate/up 1905 MHz 10.4） | ✗（原值未改，也未标“旧值/未复现”） |
| `VLLM_PORT_PLAN.md:211` | 冷启动 scores **49.6 / 51.0，+4.6** | `:393` 44.8 / 45.0；`+2.6` | ✗ |
| `VLLM_PORT_PLAN.md:212` | 冷启动 topk **40.4 / 40.5** | `:394` 32.8 / 30.9 | ✗ |
| `VLLM_PORT_PLAN.md:226` | “冷启动口径 **+18.1**；scores 只有 **+4.6**” | 新 attn 行已改 68.3/77.6 ⇒ +12.1；scores +2.6 | ✗（改了一行，摘要没跟着改） |
| `VLLM_PORT_PLAN.md:368` | 状态表“冷启 **18.1** / scores **+4.6**” | 同上 | ✗ |
| `VLLM_PORT_PLAN.md:284` | “按 15.9–17.2 µs/行估 229K 会低估约 **25%**” | `:382` 层每行 15.3→17.3→20.3（**+33%**）；若按 15.9→20.3 是 +27.7% | ✗（基线/算式未写） |
| `VLLM_PORT_PLAN.md:406` | “`8dbd89da` 四份报告入库；本节 P3b/P4/P3c 的数字按重测校正” | `8dbd89da` 只加报告；数字校正在 `e7f35829` | ✗（归属错） |

**对得上的部分**（不是全盘否定）：P3b R=160 的 31.9/32.4/26.6（`:307-309`）、算力占比 7.0%（31.9/456）、
P3c 的 fused/MMQ 1.222e-4/1.213e-4 与 1.16/1.13/1.01×、P4 预热组 29.5/36.6/62.9/77.3 与
109.2/115.2/148.9/160.7（差 ≤0.3%）、冷/热 +39/+35/+9/+0.3%、长上下文 scores 6.1/35.9/68.0 与 attn 15.9/15.9/16.7、
256K 48% —— 这些都与报告一致。

**最小修法**

- R=626 两格改成 **32.2 / 26.9**；R=12 三格改成 **8.0 / 8.5 / 7.5（1905 MHz 时 10.4）**，或在表下明写
  “R=12 保留旧值，本次未复现”。
- 冷启动表 scores 改 37.2/39.9/44.8/45.0（+2.6），topk 改 39.2/40.0/32.8/30.9；`:226`、`:368` 的 18.1/4.6
  改成 12.1/2.6。
- `:284` 的 25% 改成报告里的“+33%（15.3→20.3）”，或写出 229K 的插值算式并说明基线是 15.9 还是 15.3；不要留一个无法复核的数。
- `:406` 的 `8dbd89da` 改成 `e7f35829`；“三个提交”里列出 `61c1bfc9`、`b8eb9a73`、`e7f35829`（或明确
  “8dbd89da 是报告入库，不是修复提交”）。
- 按 `CONTRIBUTING.md` 的“测量要给口径”，P3b 表下补一句“本表是 2080 Ti / build75 / 1350 MHz 的 5 次中位”。

---

### M3【P1｜verify tap 语义】无 `%d` 的层切分：不再截断，但也不写、不提示，且注释/提交信息说“末 stage 写”

**证据**

- `src/core/verify.cpp:870-871`：
  `const bool whole_model = lb_ == 0 && le_ == g.n_layers;`
  `if (tap_path != nullptr && (tap_path_per_stage_ || whole_model)) { ... fopen("wb") ... }`
- `61c1bfc9` **新增**的 `src/core/verify.cpp:867-869` 注释：“A layer split used to open the file on the stage that
  reaches the head only…… That rule stays for a path without `%d`”——这句话对上一版是错的（`0ab5bdd1` 之后是
  每个 stage 都 `fopen("wb")`，正是这次要修的 bug），而且它说的“那条规则”在代码里并不存在：`:870-871` 只允许
  `lb_==0 && le_==g.n_layers`，没有“reaches the head”。`src/core/verify.cpp:3242-3243` 的旧注释又写
  “Without `%d` ... only the stage that reaches the head appends”；`include/strata/core/verify.hpp:575-582` 同样写
  “only the stage that reaches the head appends”。三处都与 `:870-871` 的 `whole_model` 相反。
- 提交信息 `61c1bfc9` 第 2 条：“无 `%d` 时只有整模型（**或末 stage**）开文件”。
- 实际 `lb_`/`le_`：
  - 整模型：`lb_=0`、`le_=-1`，`init` 在 `verify.cpp:750` 归一成 `g.n_layers`；
  - 层切分 stage：`set_stage(...)`（`verify.hpp:173-175`）把每段设成 `[0,24)`、`[24,48)` 等，
    末 stage 的 `le_` 是 `-1`（`generate.cpp:9055`、`:9122`、`:9126`），`init` 后同样是 `n_layers`。
  - 所以末 stage 满足 `le_ == g.n_layers` 但 `lb_ != 0` ⇒ `whole_model == false` ⇒ **不打开文件**。
- 无 `%d` 且是切分时（`nt>0`）：`tap_on_` 仍在 `:884` 置真，`tap_dev_`/`h_tap_` 仍在 `:861-863` 分配，`record_window`
  仍在 `:2306-2312` 复制到 mapped，但 `:3244` / `:3281` 都因为 `tap_file_ == nullptr` 不写；用户连
  `:878` 的“`N taps ...`”也看不到（它在 `fopen` 成功之后才执行，`:880-882` 的 stage 行同理），
  **完全静默，没有任何“本 stage 不 dump”的提示**。
- 组合结论：
  - `%d` + 有 tap 的 stage：每 stage 一份，各自追加一次；无重复（`:2310` + `:3244` / `:3200`，`le_<n_layers` 只走一条）。
  - 无 `%d` + 整模型（非批窗）：一份，`record_window` 末尾 `:2420` 复制 + `:3281` 写一次。
  - 无 `%d` + 切分：**什么都不写**（不是“末 stage 写”）。不会互相截断，但“该写的没写”。
  - “`%d` 但 `tap_path==nullptr`”不可能：`tap_path_per_stage_` 只在 `tap_path != nullptr` 的块里
    （`:794-802`）赋值。
  - 批窗（`batch_rec_`）本来就不写 dump（`:3281` 的 `!batch_rec_`），这次没改。

**最小修法**

- 若保留“无 `%d` 只写整模型”：把 `verify.hpp:575-582`、`verify.cpp:867-869`、`61c1bfc9` 提交信息里的
  “末 stage / reaches the head”全部改成“只有整模型（`lb_==0 && le_==n_layers`）；层切分必须用 `%d`”，
  并在 `:884` 附近对 `tap_path && !tap_path_per_stage_ && !whole_model && nt>0` 加一行
  “this stage records no dump; use %d”（现在是完全静默：`:878` 只在开文件成功时才打）。
- 若确实要“末 stage 写”：把 `:871` 改成 `(tap_path_per_stage_ || le_ == g.n_layers)`，并说明前几个 stage
  的 taps 仍不会出现在这个文件里（只有末 stage 自己的）。
- 无论选哪个，别在 `tap_path && !tap_path_per_stage_ && !whole_model` 时分配 `tap_dev_`/`h_tap_`、置
  `tap_on_=true`（除非 `tap_req_` 非空，即 drafter 需要）；现在是为一个不会写的文件做完整的 GPU 记录。
  `PORT_AUDIT_DEFAULTS.md:129-130` 的最小修法本来就建议“不开文件、不分配”。

---

### M4【P1｜测试可信度】QSA parity 可以在 `block_scores_rows_kernel` 根本没跑时 PASS

**证据**

- 测试 `tests/cuda/qsa_select_rows_parity_test.cpp:304` 设置 `STRATA_QSA_ROWS=1`；
  `:330` / `:342` / `:476` 的 “rows” 标签只是由 **`rows_path` 参数**拼出来的字符串，不是观测到的 dispatch；
  `:386` 写进 dump 头的 `dispatch` 也是同一字符串。
- 子进程唯一的选块调用是 `:441` 的 `k::qsa_block_scores(...)`；没有直接调用
  `k::qsa_block_scores_rows(...)` 并断言返回 true 的地方（`shape_defense` 只测了 nq=0/5 的**拒绝**路径）。
- dispatch 在 `src/kernels/cuda/qsa_select.cu:1719-1721`：
  `if (rows_env && qsa_block_scores_rows(...)) return;` —— 如果 rows 入口返回 false（或以后有人删了这个分支），
  会自动落到默认 kernel，**不报错**。两个子进程就都跑默认路径，scores/ids 逐位相同，测试 PASS。
- 两个进程的设计本身是对的：`rows_env` 是 `qsa_select.cu:1719` 的函数内 static；driver 从不调用
  `qsa_block_scores`（只调 host 形状函数和 `qsa_block_scores_rows` 的拒绝路径），不会污染子进程；
  `run_child` 在第一个 qsa 调用前 `unsetenv(STRATA_SCORES_MULTI/HALVE)` / `setenv(STRATA_QSA_ROWS)`（`:298-306`）。
- 计数属实 **[实跑]**：12 case，839,740 score float + 61,530 id，0 differ，0.90 s，33.04 MiB；
  但“比了 839,740 个 float”不等于“比的是 rows kernel”。

**最小修法**

- 在 rows 子进程里，除 dispatch 调用外，再用同一组输入显式调用
  `k::qsa_block_scores_rows(...)` 到一个独立 buffer，要求返回 `true` 并与 dispatch 输出 memcmp；
  或在 `qsa_select.cu` 加一个只读的“上次走了 rows”计数/`qsa_rows_selected()`，子进程断言它。
  这样测试才会在 rows 入口被删/返回 false 时失败。
- 保留现在的两进程结构（static 的正确解法），不需要改 driver。
- 顺带：`qsa_rows_bench --rows` 在 `STRATA_QSA_ROWS=1` 下参照侧也走 rows 的发现，测试注释里已写明；
  这条发现本身是对的。

---

### M5【P1｜默认路径文档/组合语义】7.5 的“默认 setup+banner”文档与 `61c1bfc9` 相反；`STRATA_PF_FUSED` 解析与消费者不一致

**证据**

- 新代码：`src/prefill/moe_fused_iq.cu:1315-1320`
  `fused_asked = (v != nullptr && v[0] != '\0' && atoi(v) != 0)`；
  `sm75 = sm75_env > 0 || (sm75_env < 0 && major == 7 && minor == 5 && fused_asked)`；
  `if (major < 8 && !(major == 7 && minor == 5 && sm75)) return d;` —— 未设 `STRATA_PF_FUSED` 且未设 `STRATA_PF_FUSED_SM75` 时早退，
  不 `cudaFuncSetAttribute`、不打 banner。**这一条成立。**
- 但用户文档还停在旧行为：
  - `include/strata/prefill/moe_fused_iq.hpp:4-6`：“These kernels are set up by default on a 7.5 card……
    dev_info sets them up per kernel and prints one banner”；
  - `docs/DETAILS.md:2548`：“a 7.5 card sets its kernels up by default - its own switch”。
  两者都与 HEAD 代码相反；`61c1bfc9` 没有改它们。
- 解析不一致：`moe_fused_iq.cu:1315-1318` 用 `atoi != 0`；消费者 `native_supported` 在 `:1436-1438` 用
  `v[0] == '1' ? 1 : 0`；`moe_fused.cu:695-699` 的 `requested()` 也用 `v[0] == '1'`。
  于是 `STRATA_PF_FUSED=true` / `=2` / `=yes` 在 7.5 上会**进 Turing 分支、setup、打 banner**，
  但 `native_supported` 认为没请求（`env==0`）→ 仍 MMQ；这正是这次修复想消掉的副作用。
- 顺序：`fused_asked` 是函数内 static，在第一次 `dev_info()` 时求值。若某调用方在 `dev_info()` 之后才
  `setenv("STRATA_PF_FUSED","1")`，新的缓存会让 setup 不发生；旧代码只缓存 `sm75_env`（FUSED 不参与），
  所以这是**新加的一条顺序依赖**（`prefill_fused_iq_test` 在 `main` 开头设置，没踩到；外部嵌入者可能踩到）。
- `STRATA_PF_FUSED_SM75=1` 但未设 `STRATA_PF_FUSED`：`sm75_env>0` ⇒ 进 Turing 分支，setup + banner；
  随后 `native_supported` 的 env<0 路径在 `:1454` `if (d.t75) return false;` ⇒ **仍 MMQ**。
  也就是说 `sm75_series.hpp:14` / `DETAILS.md:2548` 把 `STRATA_PF_FUSED_SM75=1` 写成 fused int8 experts 的
  opt-in，但它单独**不能**启用任何 fused 路径（必须同时 `STRATA_PF_FUSED=1`）。这是文档承诺与代码的偏差，
  不是这次引入的，但这次正好在同一段代码上，应该一起写清。
- `d.t75`/`d.ok` 的使用者 **[静态]**：
  - `gu_covered` 的 Q2_0 分支 `:1368`（`dev_info().t75`）；
  - `native_supported` 的默认路径 `:1454`（`if (d.t75) return false`）与 `:1457`（`!d.t75`）、
    `:1466`（Turing 只允许 Q2_0/Q2_0 与 t75_iq 对）；
  - `experts_native` `:1483`（`!d.ok` → 报错退出；只有 native_supported 放行后才会到）；
  - `pick_ww` `:1568`（`d.t75 && !d.t75_iq_ww4` → WW=2）。
  未设 env 的 7.5 上 `d.t75=false, d.ok=false`，上述路径要么先被 `native_supported` 拒绝、要么不可达；
  `gu_covered` Q2_0 从 true 变 false，但结果仍是 `native_supported == false`。所以默认数值路径不变。

**最小修法**

- 改 `include/strata/prefill/moe_fused_iq.hpp:4-11` 与 `docs/DETAILS.md:2548`：7.5 上只有
  `STRATA_PF_FUSED=1` 或显式 `STRATA_PF_FUSED_SM75=1` 才 setup；不请求时没有 banner、没有
  `cudaFuncSetAttribute`；`STRATA_PF_FUSED_SM75=1` 单独仍走 MMQ，要 fused 必须 `STRATA_PF_FUSED=1`。
- 把 `fused_asked` 的解析改成与 `requested()`/`native_supported` 完全一致（`v[0]=='1'`），或直接复用一个
  accessor；至少让 `true`/`2` 不再触发“setup 了但不用”的副作用。
- 在 `moe_fused_iq.cu:1308-1318` 的注释/静态区里写明“读一次，必须在第一次 `dev_info()` 前设置”，或把
  `fused_asked` 改成在 `dev_info` 每次调用时读环境（与 `sm75_env` 的缓存策略一致地说明取舍）。

---

## 2. 建议（不阻断）

1. **CPU-only 的两个新测试是“转写自测”，CTest 名字却像在测生产代码**。`session_rope_share_test` 的
   `session_bytes_transcription`、`mtp_logits_width_test` 的 `bind_bytes_transcription` 在无 `strata_engine`
   时逐行复制公式，断言是测试自己算给自己（例如 `CHECK(full_bytes == logits_bytes(max_t, n_vocab))` 两边都是
   本文件的 helper）。报告已在文件头/`mode=transcription` 打印里写明，**不算隐瞒**；但建议给 CTest 加
   `LABELS transcription`（或 `SKIP_RETURN_CODE 77` 当没有 real engine），并在 CI 里确保另有一个 CUDA job
   跑 `mode=real`，否则本地 `ctest` 的 2/2 只证明转写算术自洽。
2. **`session_rope_share_test` 的 real 模式只比较差值**（`:153-157` 只断言 `real_owned >= real_borrowed`、`real_owned - real_borrowed == table`、
   `real_owned - real_borrowed == owned - borrowed`），没有 `real_owned == owned` / `real_borrowed == borrowed`。
   一个同时加到 owned/borrowed 上的常数错误会漏过。加两条绝对相等断言即可。
3. **DFlash2 顺序守卫放在 `bind()` 之后**（`generate.cpp:9206`、`:16350`）：taps 不匹配时已经 bind 了
   draft head（约 0.2 GiB 量级），再 `release()`，白做一次分配。把比较移到 `bind` 之前即可；`load()` 在
   `:5481` 已解析 `taps_`（`dflash2.cpp:223-227`），`bind`/`prepare` 不改变它，所以提前比较是安全的。
4. **守卫触发后 verifier 仍然在记 taps**：`ver.set_taps` 在 `init` 前已调用（`generate.cpp:9172`、`:16324`），
   `tap_on_` 仍为真；每窗仍做 `:2310`/`:2420` 的 mapped copy，直到进程结束。可加一个“drop taps”入口，
   或至少打一行“the verifier keeps recording taps; restart without --draft dflash2 to stop it”。
5. **CLI 的两条 DFlash2 失败路径没有 `release()`**：`generate.cpp:16336-16339`（`!ver.tapping()`）与
   `:16356-16360`（`prepare` 失败）只置 `use_df2=false`；serve 的对应路径（`:9191-9194`、`:9212-9217`）都
   `release()`。CLI 是短进程，影响小，但语义应统一（这条是既有代码，不是 `61c1bfc9` 新引入）。
6. **`set_source_files_properties(... LANGUAGE CUDA)` 的全局性**：它按“源文件路径”生效，不是按 target；
   当前只有 `qsa_select_rows_parity_test` 用这个文件，所以没有影响；以后若别的 target 复用同一 `.cpp`，会被
   一起当 CUDA 编译。更稳的做法是把 `__global__` 放到独立 `.cu`，或把 target 限制写清楚（现在已在
   `if(STRATA_BUILD_TESTS AND STRATA_ENABLE_CUDA)` 里，CPU/HIP 构建不会看到它——之前担心的 HIP 破坏不成立）。
7. **`#define private public`（`mtp_logits_width_test.cpp:40-48`）**：只影响本 TU 的访问控制，不改布局；
   依赖“先 include 依赖、再 define”的顺序，技术上是一处 ODR 偏差（访问说明符参与类定义）。当前可接受；
   若想干净，给 `MtpDrafter` 加一个只读的 width accessor（源改动，报告已提）。
8. **`PORT_AUDIT_COVERAGE.md` 的两处证据要修**：
   - `:135` #55 说“`sm75_series.hpp` 的 7.5 默认 banner”——该头文件没有任何 `printf`；唯一的 Turing banner 是
     `moe_fused_iq.cu:1332` 的 fused banner，且现在受 `61c1bfc9` 的条件控制。状态“已有等价实现”可能仍成立
     （启动行/相位表），但引用的证据是错的。
   - `:136` #56 说 tap_parity “14 例单测”；**[实跑]** `python -m pytest tools/server/tests/test_tap_parity.py -q`
     = **17 passed**。
9. **`PORT_AUDIT_DEFAULTS.md` 3.4 的“最小修法”应改写**：`inline` 转发重载不足以为旧 `.o` 提供符号（头文件 inline
   只在使用它的 TU 里发弱符号）；session 的修法用的是 `.cpp` 里的非 inline 定义，所以 `nm` 能看到 5/7 参。
   另外类布局已变，报告应把“旧 `.o` 能链接”降级成“建议删旧 `.o`”。
10. **`P4` 的 90% vs 92%、0.88 s vs 0.90 s**：`VLLM_PORT_PLAN.md:227` 写 90%，报告 `:157` 是 92%
    （15.9/17.3）；§6.1 的 0.88 s，本次 ctest 是 0.90 s（同一量级，非阻断）。
11. **`e7f35829` 一提交横跨 8 个主题**（P3b/P4/P3c/fork/A2/§1/§P6b/§6.1）：若按 `CONTRIBUTING.md`
    的“一个 PR 一个问题”，后续应拆分或至少在提交信息里标清“纯文档校正，无代码”。

---

## 3. 我核实不了的

1. **7.5 + fused 的运行时 A/B**：没有模型权重；没有跑 `prefill_fused_iq_test` 的 fused vs MMQ 计时，也没有
   端到端 45K prompt。A1 的“默认无副作用”是 **[静态]**（早退路径 + 无 `cudaFuncSetAttribute`/banner 调用），
   不是实测的 stderr/驱动调用计数。
2. **真实层切分/overlapped 的 tap dump**：没有权重、没有 `STRATA_TAP_FILE` 的多 stage 运行。M3 的“末 stage 不写、
   前 stage 也不写”是读 `:870-871` + `set_stage` 的静态结论；`%d` 分支的每 stage 一份也没有实跑。
3. **DFlash2 的顺序守卫**：没有 drafter pack；没有触发“pack taps 非升序”或 `STRATA_TAP_FILE` 覆盖的真实运行。
   守卫的“释放后 MTP 不会用残值”是读 `dflash2.cpp:47-100`（`release` 清空指针、`bound_=false`）+ `use_df2`
   门控的静态结论。
4. **旧 `.o` 的 ABI 实测**：只做了 `nm`（符号存在/不存在），没有真的拿旧目标文件链接；类布局变化导致的 UB
   是读 `session.hpp:74` 与 `b009fab5` 的成员增加得出的，未运行。
5. **HIP 构建**：本机无 `hipcc`。新 target 在 `if(STRATA_BUILD_TESTS AND STRATA_ENABLE_CUDA)` 内（`CMakeLists.txt:460-501`），
   静态看 CPU/HIP 不会配置它；没有实配 HIP。`LANGUAGE CUDA` 在 CUDA 构建里正常工作（build75 已编过该 target）。
6. **P3b R=626 的 33.0/25.6 出处**：在 `PORT_AUDIT_MEASUREMENTS.md` 的原始输出和对照表里都找不到这两个数；
   没有找到第三份运行记录能把它们对上。
7. **P4“25%”的推导**：报告只给层每行 15.3→17.3→20.3（+33%）和“会低估”，没有 229K 的插值/基线；
   25% 若按 15.9→20.3 是 +27.7%，按 15.3→20.3 是 +33%，两者都不是 25%，无法确认它的口径。
8. **生产环境**：未触碰 `strata.service`/`strata-0.2.2`/`strata-mp`；未重启服务。服务器上只做只读
   `ctest`/`nm`/`md5sum`/`grep`，峰值 <40 MiB（QSA test 33.04 MiB device buffers + 38 MiB memGetInfo delta），
   远低于 800 MiB。

---

## 4. 附录：A–E 逐问证据

### A. `61c1bfc9` 四个修复

#### A1 `moe_fused_iq.cu` 的 `fused_asked`

- **设 `STRATA_PF_FUSED=1` 与 `d8ba8593` 之后一致吗？** 是。`d8ba8593` 的表达式是
  `sm75_env > 0 || (sm75_env < 0 && major==7 && minor==5)`；HEAD `:1319` 多一个 `&& fused_asked`，而
  `fused_asked` 在 `STRATA_PF_FUSED=1` 时为 true，所以 7.5 上等价；后续 `:1324-1338` 的 setup/banner、
  `:1454`/`:1466` 的判定都没改。**[静态]** 逐行对比 `git show d8ba8593`。
- **未设时真的一点副作用都没有吗？** 7.5 上 `sm75=false`，`:1320` 早退，之后没有 `setup_ww`/`setup_t75`
  （`cudaFuncGetAttributes`/`cudaFuncSetAttribute`/`cudaOccupancy...`）也没有 `:1332` 的 banner；
  仍有的只有早退前的 `cudaGetDevice`/三次 `cudaDeviceGetAttribute`/`sm120_default`（d8ba8593 之前就有）。
  `d.t75==false, d.ok==false`；使用者见 A 段末尾，均为“先被 native_supported 拒绝”或不可达。
- **`d.t75`/`d.ok` 谁在用？** `gu_covered` Q2_0（`:1368`）、`native_supported` 默认路径（`:1454`/`:1457`）、
  Turing 对判定（`:1466`）、`pick_ww`（`:1568`）、`experts_native`（`:1483`，只有放行后才到）。
- **`STRATA_PF_FUSED_SM75=1` 但未设 `STRATA_PF_FUSED`？** `sm75_env=1` ⇒ `sm75=true` ⇒ 进 Turing 分支：
  4–8 次 `cudaFuncSetAttribute` + 一行 banner（`:1322-1338`）；但 `native_supported` 走 env<0，
  `:1454` `if (d.t75) return false` ⇒ **MMQ**。这是文档承诺过的用法（`sm75_series.hpp:14`、
  `DETAILS.md:2548` 把它列为 fused int8 experts 的 opt-in），但它单独从未能启用 fused；现在只是把
  “setup 了但不用”的副作用保留在这个组合里。要么在文档写清“必须同时 `STRATA_PF_FUSED=1`”，要么让
  `native_supported` 认 `SM75=1`（如果那是承诺）。
- **`STRATA_PF_FUSED=0` + `SM75=1`**：同样 setup + banner（`sm75_env>0` 优先），但 `native_supported` env=0
  直接 false。语义上“显式关 fused 却仍做 Turing setup”，可一并考虑。
- **解析/顺序**：见 M5（`atoi != 0` vs `v[0]=='1'`；`fused_asked` 静态求值顺序）。

#### A2 `verify.cpp` 的 `whole_model` 门

- **`lb_`/`le_`**：默认 `lb_=0, le_=-1`（`verify.hpp:490`）；`init` 在 `verify.cpp:750` 把 `le_` 归一成
  `g.n_layers`。整模型 = `0..n_layers`；层切分由 `set_stage` 设为 `[0,24)`、`[24,48)` 等（`verify.hpp:173-175`；
  `generate.cpp:9055`/`:9122`/`:9126`），末 stage 的 `le_` 传 `-1`，init 后同样是 `n_layers`。
- **`tap_path_per_stage_` 定义/置真**：`verify.hpp:583`；只在 `verify.cpp:800-802`
  （`tap_path != nullptr` 块内）按 `find("%d")` 置真。没有“`%d` 但 `tap_path==nullptr`”的组合。
- **组合**：见 M3。结论一句话：`%d` 时每 stage 一份、不重复；无 `%d` 时只有整模型写；层切分（含末 stage）
  不写、不提示；“带 `%d` 但 nullptr”不可能；“无 `%d` + 切分”会“该写的没写”，但不会互相截断。
- **批窗**：`run_slot_rows` 不调用 `run()`，`:3281` 又限 `!batch_rec_`；批窗只填 `tap_dev_`，不写 dump（既有行为）。
- 注释/提交信息与代码的三处矛盾见 M3；`verify.cpp:867-869` 那句“used to open on the head stage only”对上一版
  （0ab5bdd1 之后）也不成立——上一版是**每个 stage 都 fopen("wb")**，这正是这次要修的 bug。

#### A3 `generate.cpp` 的 tap 顺序守卫

- **`df2.tap_layers()` 在 bind/prepare 之前可用吗？** 可用。`load()` 在 `dflash2.cpp:223-227` 解析 pack 头里的
  `taps` 填充 `taps_`；`bind`/`prepare` 不改它（`bind` 只在 `:247` 与共享实例比对）。守卫在
  `generate.cpp:9206`/`:16350`，位于 `ver.init` 之后、`df2.prepare` 之前，顺序正确。
- **`ver.tap_layers()` 在 init 之后的语义**：整模型的 tap 层，升序、去重、过滤掉最后一层
  （`verify.cpp:835-857`）；`tap_dev_` 的全局槽序由它定义。若 `STRATA_TAP_FILE` 设置，init 用
  `STRATA_TAP_LAYERS`/默认列表而不是 `set_taps` 的列表（`:842-848`），所以研究 dump 会覆盖 drafter 的
  taps，守卫会因列表不等而关掉 DFlash2——安全，但诊断只打 size，不说是 env 覆盖，建议补。
- **CLI/serve 两处**：都加了同样的比较、`use_df2=false`、`df2.release()`（serve `:9206-9211`，CLI `:16350-16355`）；
  CLI 的 `strata generate` 文案正确。唯一差异是 CLI 的 `!ver.tapping()` 与 `prepare` 失败路径没有 release（建议 5）。
- **守卫触发后清干净了吗？** `release()`（`dflash2.cpp:47-100`）销毁 graph/stream/事件、`cudaFree` 所有 buffer，
  把指针置空、`bound_=false`、`graph_taps_=nullptr`、`pending_=false`、`device_=-1`；之后 `context()`/`draft()`
  会报“released”。`use_df2=false` 后每请求的 `df2_req` 初始就是 false（`:13120`），MTP 的 `mtp.draft`
  不读 df2 的 buffer，所以不会用残值。**但** verifier 的 taps 仍开着（建议 4），`taps_` 向量也仍在（没有调用方读，
  不构成越界）。
- **顺序守卫放在 bind 之后**的小浪费见建议 3。

#### A4 旧形参重载

- **5 参 vs 6 参**：`session.hpp:117-121` 的 6 参**没有默认实参**；`:120` 的 5 参有
  `layer_lo=0, layer_hi=-1`。重载决议：3/4/5 个实参 → 只有 5 参可行（6 参不可行）；6 个 → 6 参。
  没有二义（C++ 默认实参不参与“可行性”之外的平局；6 参需要 6 个实参）。**没有**“既留默认实参又加重载”
  的二义，因为两个重载的形参个数不同。
- **`session_init`**：8 参无默认（`:130-131`）；7 参最后两个有默认（`:132-134`）。5/6/7 个实参 → 7 参；
  8 个 → 8 参；无二义。
- **符号够不够**：**[nm]** 5 参 `_ZN...session_bytes...llll`、6 参 `...llllb`、7 参
  `_ZN...session_init...llPKS5_`、8 参 `...PKS5_` 都在 `libstrata_engine.a`。`session.cpp:213-220` 是**非 inline**
  的转发定义，所以旧 `.o` 能拿到符号；这是正确的做法（比头文件 inline 可靠）。
- **`dflash2.hpp` 的 3 参**：不存在（M1）。如果只加一个 `inline` 3 参到 `dflash2.hpp`，它只会在使用它的 TU 里
  发弱符号，旧 `.o` 仍可能找不到；要保证旧 `.o` 链接必须放到 `.cpp` 里非 inline 定义。**而且**类布局已变，
  链接成功也不代表能安全运行。
- 依据：`git show 61c1bfc9^:include/strata/core/session.hpp`（旧 6/7 参带默认）与 HEAD 的差异；
  `git show b009fab5^:include/strata/core/dflash2.hpp:45`（旧 3 参）。

### B. `b8eb9a73` 是不是真测试

| 问题 | 结论 |
|---|---|
| 断言是否可能恒真？ | **CPU-only 模式是**（转写自测，见建议 1）；**real 模式不是**，它调用 `strata_engine` 里的真函数并与转写比较（`mtp` 的 `bind_bytes_real`、`session` 的 `session_bytes`）。但 `session` 只比差值，不比绝对量（建议 2）。 |
| QSA 断言是否恒真？ | **可以空跑**：标签由参数拼出、没有“rows 真跑了”的断言（M4）；但两进程设计、static 清理、计数都正确。 |
| `#define private public` 影响 | 仅 `mtp_logits_width_test.cpp:40-48`；先 include 依赖再宏，避免破坏标准头；不改布局；`mtp.cpp` 正常编译；技术上访问说明符差异是 ODR 偏差，但实际无符号/布局影响。 |
| GPU 两进程设计覆盖 `STRATA_QSA_ROWS` 吗？ | 覆盖了“static 只能一个进程一种选择”的问题：`qsa_select.cu:1719` 的函数内 static，子进程各自 set/unset（测试 `:298-306`）；driver 不调 `qsa_block_scores`，不污染。**但**没有证明 rows kernel 被选中（M4）。 |
| `CMakeLists` 的 `set_source_files_properties` 全局影响？ | 按源文件路径全局（本目录），当前只有 `qsa_select_rows_parity_test` 用该文件；在 `if(STRATA_BUILD_TESTS AND STRATA_ENABLE_CUDA)` 内（`:460` 起），CPU/HIP 构建不会配置；没有把别的 target 的同名文件当 CUDA。未来复用该 `.cpp` 才会受影响（建议 6）。 |
| 测试数字 | **[实跑]** CPU-only `215` + `116`；build75 real `312` + `183`；QSA `12 cases / 839,740 floats / 61,530 ids / 0 differ / 0.90 s`；与 `PORT_AUDIT_TESTS_CPU.md:55,59,95,99` 和 §6.1 一致。 |

### C. `e7f35829` 的数字与 §6.1

- **P3b**：R=160 三格 31.9/32.4/26.6 ✓（`MEASUREMENTS:307-309`）；R=626 的 33.0/25.6 ✗（应是 32.2/26.9）；
  R=12 仍是旧值 ✗（报告 8.0/8.5/7.5，1905 MHz 时 10.4）；7.0% ✓（31.9/456）。
- **P4**：预热组 29.5/36.6/62.9/77.3 vs 报告 29.6/36.6/62.9/77.4（≤0.5%，可算复现）；层合计
  109.2/.../160.7 vs 109.3/.../161.2（≤0.3%）；冷/热 +39/+35/+9/+0.3 ✓；冷启动 attn 41.2/49.4/68.3/77.6 ✓ 且
  +12.1 ✓；**但** cold scores/topk T=3/4 仍是旧值，`:226`/`:368` 的 18.1/4.6 未跟着改；`:284` 的 25% 基线/算式未写。
- **P3c**：`26.6`（iq4_nl R=160）✓；命令补 `S20_E=16 --chunks=20,256,1002` ✓；`R64_E`/`S20_E` 是 env var 的警告 ✓；
  “该测试自设 `STRATA_PF_FUSED`、证明不了外部不设就 skip” ✓；fused/MMQ 1.222e-4/1.213e-4 ✓。
- **§6.1**：`61c1bfc9` / `b8eb9a73` / `8dbd89da` 都存在（`git log`）；三目标名在 build75 CTest 里存在；
  `ctest -R verify` 3/3 ✓；check 数 215/312/116/183 ✓；839,740/61,530 ✓；0.88 s 本次 0.90 s（同量级）。
  **但**“三个提交”里列的是 `8dbd89da`（报告入库），不是 `e7f35829`；数字校正归给 `8dbd89da` 是错的。
  另外 `:399` 说 `load` 补回旧形参重载——不实（M1）。
- **fork 增量 79/120/722** 与 `PORT_AUDIT_COVERAGE.md:12-13` 一致 ✓。

### D. 四份报告的可信度

**`PORT_AUDIT_COVERAGE.md`：59 项 / 4 缺口**

计数自洽：1 + 30 + 1 + 21 + 4 + 2 = 59（`:146-147`）；4 个缺口 #12/#16/#23/#42 也列得出来。抽查 6 条（要求 5 条，另加 #56 的计数）：

| 抽查 | 结论 |
|---|---|
| #1 TQ 4-bit ↔ `--kv q4_0`+FWHT / `k8v4` | **成立**：`generate.cpp:829-831` 的帮助文本 + `include/strata/kernels/kv_stream.hpp:44-48` 的 `kKvQ4`/`kKvHybrid`；没有“质心码本”，但“4-bit + Hadamard”是等价机制。 |
| #5 图稳定分裂数 ↔ `cap_` 固定 | **成立**：`verify.cpp:761` 用 `qsa_selection_width(kTopkMaxCells, s)`（常数）设 `cap_`，`qsa_decode_attn.cu:970` 的 `n_chunks=(cap+CHUNK-1)/CHUNK` 因而是常数；引用的 `verify.cpp:763` 是下一行 scratch，差 2 行。 |
| #16 验窗读量化池 vs 读原始 | **成立（机制）**：`verify.cpp:1820-1855` 的 append 与 `:1889-1935` 的 attention 都从量化 pool 读；这是本报告的 N4 缺口。 |
| #20 DFlash2 7 drafts + rank-256 selector | **成立**：`src/kernels/cuda/dflash2.cu:784` `if (rank != 256) throw`；`dflash2.hpp:39` `kRing=2064`。 |
| #55 “`sm75_series.hpp` 的 7.5 默认 banner” | **不成立（证据错）**：该头文件没有任何 `fprintf/printf`；唯一的 Turing banner 是 `moe_fused_iq.cu:1332` 的 fused banner（现在只在请求时打）。状态“已有等价实现”可另找启动行支撑，但引用应改。 |
| #56 tap_parity “14 例单测” | **不成立（计数错）**：`pytest -q` = **17 passed**；`PORT_AUDIT_DEFAULTS.md` 自己也写 17。 |

结论：**“59 项 / 4 缺口”的算术和覆盖方向大体可信，但这 6 条里 2 条证据行是错的**（#55、#56）；
另外 #5 的行号差 2 行，属于精度问题。报告自称“所有 status 判定都带行号”，这两条应修。

**基线要注意**：`PORT_AUDIT_COVERAGE.md:3` 写的是“HEAD `738c65d9` + 工作区 W3”，不是本轮的 `e7f35829`；
`PORT_AUDIT_DEFAULTS.md` 的标题也是 `HEAD 738c65d9`。所以其中“已修/未变”的 status 对 `61c1bfc9` 之后的代码
只是历史快照；引用它的行号时要注明基线，不能把它当成 HEAD 的实时结论。

**`PORT_AUDIT_DEFAULTS.md`：4 处需修是否被覆盖？**

| 项 | 覆盖 | 证据 |
|---|---|---|
| 3.1 7.5 默认 setup/banner | **代码覆盖**；用户文档没同步 | `moe_fused_iq.cu:1315-1320` 早退；`moe_fused_iq.hpp:4-6`/`DETAILS.md:2548` 仍写默认；解析 `atoi` vs `v[0]=='1'`（M5）。 |
| 3.2 无 `%d` 多 stage 截断 | **部分覆盖** | 截断没了（`:870-871`），但“末 stage”没实现、不打招呼、仍分配/记录（M3）；报告建议的“不分配 tap_dev_/h_tap_”没做。 |
| 3.3 `set_taps` 排序 | **覆盖** | `generate.cpp:9206`/`:16350` 两边都加了比较 + `release`；剩余问题见 A3 的建议 3/4。 |
| 3.4 三处默认参数 ABI | **部分覆盖** | `session_bytes`/`session_init` 旧符号已恢复（**[nm]**）；`Dflash2Drafter::load` 没有（`dflash2.hpp:52` 仍 4 参默认）；且类布局已变，旧 `.o` 链接成功也不安全（M1）。 |

**ABI 那条的实际影响**：不是“旧 `.o` 现在能用了”。即使补回 `load` 3 参符号，`SessionState` 在
`session.hpp:74` 多了 `rope_donor`、`Dflash2Drafter` 在 `b009fab5` 多了成员；旧 `.o` 用旧 sizeof/offset
构造/访问新库的对象是 UB。正确口径是：**跨版本增量构建不要复用旧 `.o`；重载只对“旧源码 + 新头”有 source
价值（而默认实参已经给了），对旧二进制没有安全价值**。报告应把 3.4 的结论从“修 ABI”改为“删旧 `.o`，
并说明为何不补 `load`”。

**`PORT_AUDIT_TESTS_CPU.md`：215/312/116/183 复核**

- **[实跑]** CPU-only（`/tmp/strata-build-cpu`，`STRATA_ENABLE_CUDA=OFF`）：`session_rope_share_test: ok (215 checks)`、
  `mtp_logits_width_test: ok (116 checks)`；`ctest -R "session_rope_share_test|mtp_logits_width_test"` 2/2。
- **[实跑]** 服务器 build75（`mode=real`）：312 / 183；2/2。
- 与报告 `:55,59,95,99` 和 §6.1 一致；数字可由源码/运行复核，**这一项可信**（但 CPU-only 的断言是转写自测，
  见建议 1；real 才是真代码）。

### E. 能不能合 / 还差什么

**不能合（按现状）**；修完 M1–M5 后可以。代码侧的“默认路径不变”没有看到反例：不设 env、单 stage、
不用 dflash2 时，`moe_fused_iq` 早退、verify 不分配 taps、session 默认 `borrow=false`、generate 不碰 df2；
`61c1bfc9` 的三处行为改动都在这些门后面；旧形参重载只改符号集，不改默认算式。阻断的是“说修了没修/测试可空跑/数字对不上”，不是默认路径的数值回归。

**最小清单**：M1（dflash2 ABI 声明）→ M2（P3b/P4 数字 + §6.1 归属）→ M3（verify 无 `%d` 切分语义/注释）→
M4（QSA rows 正向断言）→ M5（7.5 文档 + `STRATA_PF_FUSED` 解析）。

---

## 5. 本次实跑记录（只读）

```text
# 版本
$ git -C /home/kali/IdeaProjects/strata log --oneline -8
e7f35829 [Docs] ...
8dbd89da [Docs] 移植审查四份报告...
b8eb9a73 [Tests] ...
61c1bfc9 [Engine] ...
...

# CPU-only
$ cmake --build /tmp/strata-build-cpu -j4 --target session_rope_share_test mtp_logits_width_test   # EXIT=0
$ /tmp/strata-build-cpu/session_rope_share_test        # ok (215 checks)
$ /tmp/strata-build-cpu/mtp_logits_width_test          # ok (116 checks)
$ CUDA_VISIBLE_DEVICES=-1 ctest -R "session_rope_share_test|mtp_logits_width_test"  # 2/2

# 服务器 build75（只读；CUDA_VISIBLE_DEVICES=1）
$ CUDA_VISIBLE_DEVICES=-1 ctest -R "session_rope_share|mtp_logits_width" -V
  session_rope_share_test: mode=real (strata_engine)  -> ok (312 checks)
  mtp_logits_width_test:   mode=real (strata_engine)  -> ok (183 checks)   2/2
$ CUDA_VISIBLE_DEVICES=1 ctest -R qsa_select_rows_parity_test -V
  ... compared 839740 score floats and 61530 id ints in 12 cases; 0 differ
  ... per-child peak 33.04 MiB; wall 0.821 s; ctest 0.90 s; 1/1
$ CUDA_VISIBLE_DEVICES=1 ctest -R verify
  verify_parity / spec_verify_parity / verify_batch_parity   3/3, 6.58 s
$ ctest -N | grep -E "qsa_select_rows_parity_test|session_rope_share_test|mtp_logits_width_test"  # 三个都在
$ nm -C libstrata_engine.a | grep -E "session_bytes|session_init|Dflash2Drafter::load"
  ... session_bytes(..., long,long,long,long) / (..., bool)
  ... session_init(..., long,long) / (..., SessionState const*)
  ... Dflash2Drafter::load(..., Dflash2Drafter const*)     # 只有 4 参，没有 3 参

# 文档测试
$ python -m pytest tools/server/tests/test_tap_parity.py -q   # 17 passed
```

**服务器源码一致性**：`md5sum` 对比 build75 与本地 HEAD——`CMakeLists.txt`、`src/core/verify.cpp`、
`src/prefill/moe_fused_iq.cu`、`include/strata/core/session.hpp`、`src/core/session.cpp` 完全一致；
`src/program/generate.cpp` 因本地工作区有未提交 W3 改动而不同，但服务器副本包含本次的两处 tap 守卫
（`:9206`、`:16473`；后者在本地 HEAD 是 `:16350`），所以 ctest 覆盖到了新代码；`include/strata/core/dflash2.hpp` 与本地一致（仍是 4 参）。

**未做**：任何 commit/checkout/reset/stash/clean；未改服务器文件；未重启服务；未跑权重/模型；未跑 fused A/B。

---

## 9. 处理结果（作者补记，评审后）

本节的每一条都已在提交里处理；编号对应上文 §1。

| # | 结论 | 处理 | 提交 |
|---|---|---|---|
| M1 | **改声明、不改代码** | `session_bytes`(5 参)/`session_init`(7 参) 的旧符号确实已恢复；`Dflash2Drafter::load` 的 3 参符号**不补**——类布局已变（`b009fab5` 加成员、`SessionState` 加 `rope_donor`），补符号只会把链接失败换成静默 UB。`PORT_AUDIT_DEFAULTS.md` §3.4 与 `VLLM_PORT_PLAN.md` §6.1 都已按这个结论改写，并写明升级必须清旧 `.o`/增量目录 | `8dbd89da` 之后的 `PORT_AUDIT_DEFAULTS.md` 修订 + `VLLM_PORT_PLAN.md` §6.1 |
| M2 | **数字已按 `PORT_AUDIT_MEASUREMENTS.md` 逐格改** | P3b：R=12 → 8.0/8.5/7.5（1905 MHz 时 10.4）、R=626 → 36.5/32.2/26.9，并补测量口径；P4 冷启动 → scores 44.8/45.0（+2.6）、topk 32.8/30.9（−2.8）；摘要 18.1/4.6 → 12.1/2.6；"低估 25%"改为"层每行 15.3→17.3→20.3 µs（+33%）"；§6.1 的校正归属改成 `e7f35829` | `e7f35829`→本次文档修订 |
| M3 | **保留"只有整模型写"，补提示、改口径** | 代码行为不变（切分必须用 `%d`），该 stage 在 init 时打印"本 stage 不 dump，层切分要给 %d"；`verify.hpp`、`verify.cpp` 两处注释与 §6.1 的"末 stage"说法全部改掉 | `640471fe` |
| M4 | **加了三个 launch 计数器，测试有牙** | 每个 case 断言"rows 子进程恰好多 1 次 rows、halve 不变；默认子进程反之"；负向验证（把 rows 分支改成 `if (false && …)`）确实失败 | `de033a41` |
| M5 | **文档改回真话 + 解析对齐** | `moe_fused_iq.hpp` / `DETAILS.md` 改成"只在被请求时 setup、不请求时无 `cudaFuncSetAttribute`/无 banner"，并写明 `STRATA_PF_FUSED_SM75=1` 单独仍是 MMQ；`fused_asked` 从 `atoi != 0` 改成 `v[0]=='1'`，与 `requested()`/`native_supported` 一致 | `640471fe` |

此外，评审 §1 之外的两条也被采纳：`STRATA_PF_FUSED` 的"只在第一次 `dev_info()` 前设置"已写进头文件；
`select` 的三条路径"逐位相同所以只能靠计数器区分"这条方法论写进了测试注释与提交信息。

**没有采纳的**：把 `dflash2.hpp` 的 3 参 `load` 补成内联转发——理由同 M1（会掩盖类布局变化）。若上游确实需要
老二进制兼容，正确做法是升版本号并在构建脚本里强制清缓存，而不是补符号。
