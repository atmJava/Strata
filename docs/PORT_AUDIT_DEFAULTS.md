# 默认路径不变性审计（HEAD `738c65d9`）

对象：`d8ba8593`、`ca4d6e85`、`b009fab5`、`0ab5bdd1`+`1820894f`+`935a319f`、`ab7b682a`、
`40125aa4`+`2682c9b0`+`738c65d9`、`55c802a5`+`d71b7311`、`869c1224`+`483c53f5`+`5ac5e47b`（Python 工具部分）。
只读审计，唯一新文件就是本页。没有 commit / checkout / reset / stash / clean。

## 审计环境（先说清楚证据等级）

- HEAD 确认：`git rev-parse HEAD` → `738c65d95678c527f35ce7c66d07cdd374821d88`。
- 工作区在开工前就是脏的（15 个已修改 + 15 个未跟踪，含 `src/program/generate.cpp`、`src/prefill/moe_fused.cu`、
  `include/strata/core/sm75_series.hpp`）。本页的 diff 结论一律用 `git show <commit>` 的提交内容；
  `generate.cpp` 的行号取自 `git show HEAD:` 版本（工作区该文件不同，别拿工作区行号对）。
- CPU 构建：`/tmp/strata-build-cpu` 已配置（`STRATA_ENABLE_CUDA=OFF`、`STRATA_ENABLE_HIP=OFF`、
  `STRATA_BUILD_TESTS=OFF`、Release）。直接 `cmake --build /tmp/strata-build-cpu -j4`：
  **EXIT=0，0 s（全部 up to date），13 个 target**（strata-plan、strata-gguf、strata-dequant、strata_spec、
  ggml-base、ggml-cpu、strata_kernels_cpu、router_dot_parity、iq_avx1_parity、iq_avx2_parity、kq_cpu_bench、
  q8k_quant_parity、nvfp4_avx512_parity）。
  **但这台机器没有 nvcc / CUDA 头文件**（`which nvcc` 为空），而 `strata_engine`、`strata` 以及所有 `.cu`
  都在 `CMakeLists.txt:335` 的 `if(STRATA_ENABLE_CUDA OR STRATA_ENABLE_HIP)` 里。也就是说
  `session.cpp` / `verify.cpp` / `mtp.cpp` / `dflash2.cpp` / `generate.cpp` 这次**一行都没编到**。
  这个 EXIT=0 只证明 plan/gguf/dequant/CPU kernel 没被弄坏，不证明被审代码能编译。
- 没有 GPU、没有权重、没有 7.5 卡。所以本页没有一条"跑出来的逐位对比"。结论来自 `git show` 双侧逐字对比、
  `grep` 到的读取点，和读代码。凡是我推断而没有实测的，在第 5 节列出来。
- 本地没有引擎二进制：`bin/strata` 是 `file` 认出来的 shell 启动器（5.7 KB），CPU 构建不产 `strata`。
  所以这轮没有 `--help` / `strings` 证据可用；新文案（"DFlash2 drafter on"、`sm_75` banner）只按源码条件核对。
- 唯一跑起来的与本文有关的代码：`python -m pytest tools/server/tests/test_tap_parity.py -q` → **17 passed**。

## 0. 判定口径

"默认路径不变"指同时满足下面四条。不设任何 env、不传新增参数、不加新 flag 时：

1. 输出的 token / 打分 / residual 逐位相同（数值路径）；
2. 写出的文件（tap dump）和默认输出（含 stderr）逐字节相同；
3. 不再新增设备侧分配、launch、同步点（否则 VRAM 账、图捕获顺序会变）；
4. 函数签名兼容，旧的编译单元仍能链接（构建期；它不改变运行期字节，但会挡住"老目标文件 + 新头"的增量构建）。

判定分三档：
- **未变**：1–4 都成立。
- **变了-可接受**：有变化，但只在 opt-in 内；或者默认下数值/文件字节相同、只是显存账或文档口径变了。
- **变了-需修**：默认（或文档承诺的不变路径）出现可观察差异，或会静默产生错数据。

"逐字节"的具体对拍方式：把 `git show <commit>^:<path>` 与 `git show <commit>:<path>` 里的函数体抽出来
`diff`；对声明"旧算式"的，把两侧表达式抄进第 2.1 节的表逐项对齐。

## 1. 开关表

| 变量 / 参数 | 未设时 | =0 | =1 | 读取点 | 默认是否旧路径 |
|---|---|---|---|---|---|
| `STRATA_PF_FUSED` | `-1`（CUDA：仅 sm120+80SM 默认开；7.5 仍 MMQ。HIP 的 gfx12 默认另一套，本次未改） | 关 | 开。7.5 上现在同时放行 Q2_0/Q2_0 与 IQ3_XXS/IQ4_NL 两对 | `moe_fused_iq.cu:1430`、`moe_fused.cu:697` | 默认结果同旧；`=1` 在 7.5 上的含义变了（见 §4） |
| `STRATA_PF_FUSED_SM75` | **7.5 卡上按开处理（新）**；非 7.5 不读 | 关（早退，与旧版同） | 开 | `moe_fused_iq.cu:1310-1314` | **否**：7.5 默认进 Turing 分支（setup + 1 行 banner） |
| `STRATA_TAP_FILE` | 关：不分配 `tap_dev_`、不记录 | 空串按未设 | 路径；含 `%d` 时替换成该 verifier 的 `layer_lo` | `verify.cpp:794`、`:801-830` | 单 stage 是；多 stage 否（见 §3） |
| `STRATA_TAP_LAYERS` | 字面量 `"5,15,25,35,45"` | 空串同未设 | 逗号列表 | `verify.cpp:843` | 是（顺序/内容不变） |
| `STRATA_QSA_ATTN_CHUNK` / `STRATA_QSA_ATTN_THREADS` | 编译期 64 / 256（= 旧 `constexpr`） | 非 64/256 直接编译失败 | 同上（`static_assert` 拒绝其它值） | `qsa_decode_attn.cu:27-46` | 是（见 §2 的文本级说明） |
| 新增函数参数（非 env） | — | — | — | — | — |
| `session_bytes(..., bool borrow_rope = false)` | `false` → 旧算式 | 旧算式 | 少一张表 | `session.hpp:118`、`session.cpp:57` | 是（默认值） |
| `session_init(..., const SessionState* share_rope_from = nullptr)` | `nullptr` → 自建表 | — | 借 donor | `session.hpp:129`、`session.cpp:103` | 是（默认值；目前**没有任何调用方**传 donor） |
| `Dflash2Drafter::load(..., const Dflash2Drafter* shared = nullptr)` | `nullptr` → 旧上传路径 | — | 共享 pack/head | `dflash2.hpp:52`、`dflash2.cpp:189` | 是（但旧 3 参符号消失，见 §3） |
| `set_lazy_capture(bool)` | 默认 `true` → 旧行为（缺图就地捕获） | `false`：缺图报错 | — | `dflash2.hpp:86` | 是 |
| `draft_launch()` / `draft_collect()` | 没人调 → 旧路径 | — | — | `dflash2.hpp:75,78` | 是 |

已存在、本轮没改方向的开关（只列本次碰到的）：`STRATA_DF2_PRIO`、`STRATA_DF2_FORK`、`STRATA_DF2_PROFILE`、
`STRATA_MTP_FULL_HEAD`、`STRATA_MTP_TOP2`、`STRATA_ATTN_V3`。

## 2. 逐提交判定

| 提交 | 默认会走哪条路 | 挡住它的条件 | 判定 | 证据 |
|---|---|---|---|---|
| `d8ba8593` fused_iq Turing | 7.5 + native（IQ 专家）包：`dev_info()` 进 Turing 分支，跑 4–8 个 kernel 的 `cudaFuncSetAttribute`/占用率查询，打 1 行 banner；随后 `native_supported` 仍 `return false` → 仍 MMQ。非 7.5：`major<8` 早退、`major>=8` 走原 sm120 分支，都与旧版一字不差 | 无。7.5 上没有任何 env 挡住 setup/banner；只有 `STRATA_PF_FUSED_SM75=0` 能退回早退 | **变了-需修**（仅 7.5；数值未变，stderr/启动副作用变了） | `moe_fused_iq.cu:1310-1345`（sm75 默认与 banner）、`:1449`（`if (d.t75) return false;`）、`:1363`（`gu_covered` 也调 `dev_info`） |
| `ca4d6e85` session RoPE 借用 | `borrow_rope=false`、`share_rope_from=nullptr`：QSA 项与旧算式逐字相同（`!false == true`）；`release` 改用 `qsa_states[qsa_ord0+j]`（整模型 `qsa_ord0=0`，同旧） | 新参数；默认值挡住 | **未变**（默认）；split range 的释放**变了-可接受**（修泄漏） | §2.1 表；`session.cpp:76,182,217`；`generate.cpp:5219/5252/5400` 都只传旧参数个数（session_init 7 个），没人传 donor；session_bytes 的 6 处调用（如 `:4211`、`:4579`）也都不传 `borrow_rope` |
| `b009fab5` dflash2 共享/异步 | `load` 无 shared → 同一上传循环；`draft()` = launch+collect，同步点、读回顺序、`-1` 预填、计时起点都同；`lazy_capture_=true` | `shared`、`set_lazy_capture(false)`、直接调 `draft_launch` | **未变**（运行期）；`load` 的旧 3 参符号消失 → **变了-需修**（构建期，见 §3） | `dflash2.cpp:189,653-730`、`dflash2.hpp:52` |
| `0ab5bdd1` verify 多 stage | 无 taps：整块不执行。单 stage 且请求升序无重复：`tap_layers_all_` 排序去重后与请求列表相同，`tap_dev_=[T][nt][N]` 与旧同 | `tap_path==nullptr && tap_req_.empty()` | 单 stage **未变**；乱序/重复的 `set_taps` **变了-需修**（低危） | `verify.cpp:839-860` |
| `1820894f` `%d` | 不含 `%d`：路径原样、`"wb"`、头与记录同旧 | `tap_path_per_stage_` | 单 stage **未变**；多 stage 无 `%d` **变了-需修** | `verify.cpp:801-830` |
| `935a319f` 重名去重 | 只在 `%d` 且同一路径已被本进程用过时触发 | `tap_path_per_stage_` + `used` 表 | **未变** | `verify.cpp:805-830` |
| `ab7b682a` mtp logits 宽度 | 无子词表：`n_dvocab_=n_vocab_`，`[max_t][n_vocab]` 与旧同；有子词表：缓冲变窄，但所有读者本来就按 `n_dvocab_` 步进 | `dhead_`/`vocab_file()` | **变了-可接受** | `mtp.cpp:545-575,759-775,1226-1250` |
| `40125aa4` serve 接 DF2 | `use_df2=false`：`serve_max_t` 与旧实参逐字相同；`set_taps`/`bind`/`prepare`/banner 全在 `use_df2` 内 | `--draft dflash2` | **未变** | `generate.cpp:9172-9212`；§2.1 表 |
| `2682c9b0` 加固 | 同上；新增的 gate 只拒 `--draft dflash2` + 层切分 | `o.draft`/`use_df2` | **未变** | `generate.cpp:5478` |
| `738c65d9` 四轮修复 | `from_df2` 与 `on_taps` 早退都在 `df2_req` 内；dump 改名只在 `%d` + 重名 | `df2_req`、`%d` | **未变** | `generate.cpp:13138,15324`；`verify.cpp:817-830` |
| `55c802a5`+`d71b7311` qsa 宏 | 宏默认 64/256，就是旧 `constexpr` 的值；只多一条 `static_assert`（默认通过） | 编译期 | **未变**（文本级，无法本地编译） | `qsa_decode_attn.cu:27-46` |
| `869c1224`+`483c53f5`+`5ac5e47b`(py) | 纯工具/测试：`tap_parity.py` 只被 `tests/test_tap_parity.py` 引用，引擎/serve 不 import | — | **未变** | `grep -rn tap_parity serve tools strata_control strata_cli tests` 只命中它自己和测试；17 passed |

### 2.1 声明"逐字节一致"的三处，新旧算式对齐

**(a) `session_bytes` 的 QSA 项**（`ca4d6e85^` vs `ca4d6e85`，抽函数体 `diff`）

| | 表达式 |
|---|---|
| 旧 | `qsa_state_bytes(g, max_cells, true) + (q_n - 1) * qsa_state_bytes(g, max_cells, false)` |
| 新 | `qsa_state_bytes(g, max_cells, !borrow_rope) + (q_n - 1) * qsa_state_bytes(g, max_cells, false)` |
| 对齐 | `borrow_rope = false` 时 `!borrow_rope == true`，是同一个编译期常数；同行其它项（`gdn_*`、`qsa_buffers`、`moe_buffers`、`block_buffers`、`align_up(n, SESSION_STATE_ALIGN) + 4096`）`diff` 无改动。`diff` 里剩下的只有函数签名、注释和一处换行 |

**(b) `tap_write` 的记录格式**（`0ab5bdd1^` vs `HEAD`，抽函数体 `diff`）

| | 内容 |
|---|---|
| 旧 | 每行 `int32 pos, int32 token`，再 `tap_layers_.size() * n_embd` 个 float；`fflush` |
| 新 | 同上，**函数体 `diff` 为空**（`diff -u` 无输出） |
| 对齐 | 头部 `{0x50415453, 1, N, nt}` + 每层一个 `int32` 也未改；`nt` 在单 stage 升序输入时与旧相等 |

**(c) `serve_max_t`**（`40125aa4^` vs `HEAD`，`generate.cpp:9176`）

| | 表达式 |
|---|---|
| 旧（`ver.init` 的 max_t 实参） | `batch_mtp ? kVerifyMaxT : std::max(o.spec, std::min(o.batch, (int) kVerifyMaxT))` |
| 新 | `batch_mtp ? kVerifyMaxT : use_df2 ? std::max(o.spec, (int) kVerifyMaxT) : std::max(o.spec, std::min(o.batch, (int) kVerifyMaxT))` |
| 对齐 | `use_df2 == false` 时新式的第三支与旧式逐字相同；`kVerifyMaxT` 的类型转换也保留 |

## 3. 变了且需要修（含最小修法）

**3.1 7.5 卡默认进 Turing 分支：多一次 setup + 一行 banner** — `src/prefill/moe_fused_iq.cu:1314-1345`

- 触发：`native_supported()` 的 `env<0` CUDA 分支调 `dev_info()`（`:1443`）；`gu_covered()`（`:1363`）在
  `T_Q2_0` 上也会调。7.5 卡上只要有一个 native（IQ 专家）pack 启动，`dev_info()` 就会跑
  `setup_one<T_Q2_0>*` 与 `setup_t75<IQ3_XXS/IQ4_NL>` 并 `fprintf` 一行。
- 为什么算变了：默认（不设 env）**数值仍是 MMQ**（`:1449` 在 `env < 0` 分支里 `if (d.t75) return false;`），
  但 stderr 多一行，且在第一个 token 之前多做 4–8 次 `cudaFuncSetAttribute` + 占用率查询。按第 0 节第 2 条，
  这不是逐字节一致。作者在 `docs/DETAILS.md:2549` 是**有意**这么写的（"a 7.5 card sets its kernels up by default"），
  所以也可以把它写成规则的显式例外；但若坚持第 0 节，这里要修。
- 最小修法：把 `if (major < 8)` 的 Turing 分支改成"只有 `requested()`（`STRATA_PF_FUSED=1`）或
  `STRATA_PF_FUSED_SM75` 显式设置时才进"；否则走旧的 `return d;`。`native_supported` 默认本来就返回 false，
  setup 结果默认无人使用。`STRATA_PF_FUSED=1` 时行为不变。

**3.2 多 stage + `STRATA_TAP_FILE` 不含 `%d`：各 stage 互相截断，只留部分层** — `src/core/verify.cpp:801-830`

- 旧版有总门 `lb_ == 0 && le_ == g.n_layers`；`0ab5bdd1` 删掉后，层切分的每个 stage 都会
  `fopen(同一路径, "wb")`，后 init 的 stage 截断先 init 的。不含 `%d` 时：serial 切分只有 head stage 走到
  `:3277` 写记录（非 head stage 的写点在 `:3240`，被 `%d` 挡住）；overlapped 的 `chain` 分支在 `:3196` 就 return，
  那里的写同样被 `%d` 挡住，于是文件只有头、没有记录。
- 提交信息里"不含 `%d` 时与原来逐字节一致"只在**单 stage**成立。
- 最小修法：`!tap_path_per_stage_ && (lb_ != 0 || le_ != g.n_layers)` 时保持旧行为（不开文件、不分配
  `tap_dev_`/`h_tap_`，可选打一行"give %d for a layer split"），即把旧总门只加回"无 `%d`"这一支。

**3.3 `set_taps` 对乱序/重复层排序去重，静默改变槽序与槽数** — `src/core/verify.cpp:853-854`

- `tap_dev_` 的槽序现在恒为升序；重复层被去掉。drafter 侧按 `taps_` 的**原顺序**读（`dflash2.cpp:498,640`：
  `tap_n = taps_.size() * N_`），并且按 `5*N` 一行读。所以 pack 的 `taps`（`target_layer_ids`）不是升序时，
  槽序错位；有重复层时 `nt < taps_.size()`，drafter 仍按 `taps_.size()*N` 一行读一个只有 `nt*N` 一行的缓冲，尾部越界读。两者都**不报错**。
- 现状：唯一调用方 `generate.cpp:9172` 传的是 `df2.tap_layers()`，测试 fixture 是 `[1,2,3]` 升序，所以默认
  不受影响；这是"语义边界"，不是当前默认路径的回归。
- 最小修法（不动 verifier 的排序）：`generate.cpp` 在 `set_taps` 之后加一句
  `if (use_df2 && ver.tap_layers() != df2.tap_layers()) { 说明一行; use_df2 = false; }`（`tap_layers()` 是
  排序去重后的并集，正好能判出乱序/重复）；或者让 `set_taps` 拒绝非严格升序。

**3.4 三处默认参数改了符号（ABI）** — `dflash2.hpp:52`、`session.hpp:118,129`

- `load(3 参)`、`session_bytes(5 参)`、`session_init(7 参)` 的旧 mangled 符号都不存在了。源码兼容（调用方
  重编即可），但"老 `.o` + 新头"会在链接期报未定义；`b009fab5` 的提交信息自己记了这件事
  （"首次链接需清陈旧 generate.cpp.o"）。另外 `SessionState` 多了 `rope_donor`、`Dflash2Drafter` 多了若干成员，
  类布局也变了。
- 最小修法：去掉默认实参，另留旧参数个数的 `inline` 转发重载（不能既留默认实参又加重载，会二义）。
  这样旧符号仍在，新代码照样传 4/6/8 参。

## 4. 判定为"变了但可接受"的理由

- **`ab7b682a` mtp logits 按子词表宽度**：无子词表时 `n_dvocab_ = n_vocab_`（`mtp.cpp:759`），宽度与旧分配相同；
  有子词表时缓冲变窄，但 head gemv / argmax / sample / row_top_prob 全部用 `nv = n_dvocab_`
  （`mtp.cpp:1226-1250`），读到的值不变。`bind_bytes` 同步改成预测宽度（`:560-566`）：有子词表时旧的
  `(size/4)*row_bytes + size` 与新 `n_subset*row_bytes + n_subset*4` 逐项相等，logits 项变小正是要省的
  （每 drafter 4.334 MiB）。默认输出不变，显存账变小。
- **`ca4d6e85` split range 的 release**：整模型 `qsa_ord0=0`，新旧完全一致；`layer_lo>0` 从"漏释放一张表 +
  留下 #280 注册项"变成"正常释放"（`session.cpp:217-220`）。这是修 bug，不是回归。
- **`d8ba8593` 让 `STRATA_PF_FUSED=1` 在 7.5 上不再需要 `STRATA_PF_FUSED_SM75=1`**：仍是 opt-in，
  默认（不设 env）不触发；但这是**已有开关语义的变化**，`sm75_series.hpp:13-14` 还把 `STRATA_PF_FUSED_SM75`
  写成"explicit opt-in"。建议在 `DETAILS.md` / `sm75_series.hpp` 里把"7.5 上 `STRATA_PF_FUSED=1` 即开"
  写明白。
- **DFlash2 opt-in 内的行为变化**（失败回退 MTP、`release()`、gate 拒层切分、`draft_launch/collect`）：
  全部在 `use_df2` / `df2_req` / `o.draft == "dflash2"` 内，默认一条都不走（`dflash2` 对象只在
  `#if STRATA_HAVE_DFLASH2` 里声明，默认不 load）。批窗往 `tap_dev_` 里填 taps 也只发生在 `tap_on_` 为真时
  （`set_taps` 或 `STRATA_TAP_FILE`），默认 taps 关；而且批窗那条路径仍然 `!batch_rec_` 才写 dump（`verify.cpp:3277`）。

## 5. 我没能验证的

1. **一台能编译被测代码的机器**：没有 nvcc / CUDA 头；CPU 构建不含 `strata_engine` / `strata` / 任何 `.cu`。
   所以"这些文件能编译"在本机无证据（提交信息说服务器 `strata` EXIT=0，那不是我的测量）。
2. **任何数值逐位对比**：没有 GPU、没有权重、没有 7.5 卡。第 2 节里"数值未变"全部是源码对比的结论，
   不是跑出来的。没跑 fused vs MMQ、没跑 `dflash2_parity`、没跑 `ctest -R verify`（本机没配测试）。
3. **7.5 上 banner 的实际行数/顺序/耗时**：只按代码推断"一次、在第一次 `dev_info()` 时"。
4. **`%d` 的 `.v1` / `tap-<pid>-<n>.stap` 分支**：没实测；另外 240 字节回退名会落在**当前工作目录**，
   而不是原路径所在目录，这一点只读了代码，没验证。
5. **多 stage / overlapped 的真实 dump**：提交信息自己说没跑（无权重），我也没跑。
6. **`session_bytes` 的"逐字节一致"是代数论证**（`!false == true` + 其余项 `diff` 为空），不是数值对拍；
   本机无法链接 `session.cpp` 做对拍。
7. **`--batch-mtp` 共享 drafter**：`ab7b682a` 提交信息里提到的既有隐患（共享 slot 的 `dvocab_host_` 为空，
   `STRATA_MTP_TOP2=1` + `--batch-mtp` 会读它）我只确认了代码现状（`mtp.cpp:542` 读 `dvocab_host_`，共享 bind 在 `:698-700` 不填它），默认关，没跑。
8. **Python 部分只跑了 `tap_parity` 17 例**；`5ac5e47b` 提到的 staged / probe 用例没跑（与本轮默认路径无关）。
