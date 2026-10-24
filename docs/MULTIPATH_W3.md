# W3 · 无槽冷读 + 暂存 + 准入（引擎补丁设计）

> 状态：设计稿（R3 起）。基线：生产 `strata-0.2.2` / engine `0.1.47`（服务器 `/opt/ai/src/strata-mp`，
> 与生产同一份源码的副本）。所有行号都是 **0.1.47** 的，和仓库里的 0.1.48 差约 150 行。
> 判据见 [`MULTIPATH_STANDARD.md`](MULTIPATH_STANDARD.md)：读者首 token ≤2 s、邻居停顿 ≤1 s、
> 聚合 ≥ 基线 −3%；基线 27.2 s / 0.68 s / 120.5 tok/s（L-046）。

## 1. 为什么必须动引擎

生产的 `--batch N` 是**槽位模型**：每个请求占一个槽，槽里装会话状态（`ss`/`bs[b]`）和 KV。
`BGEN` 准入要求槽空闲（`generate.cpp:11847-11856`：`bs[b].active` 为真就回 `ERR BGEN`），
所以一个请求的**读**（prompt prefill）必须等某个槽**整条流结束**才能开始——满槽时排队时间就是
"最长那条流还剩多久"，实测 27.2 s（1200-token 流）/ 36–43 s（L-020/L-021 的长批）。

fork 的解法是连续批处理 + 分页 KV：新请求的 prefill 可以切块插进正在解码的步里，准入不要求整槽。
那是一次重写；W3 只做**最小手术**：让读**先不占槽**地发生，读完之后再"收养"进一个刚空出来的槽。

## 2. 机制：暂存读（staged read）+ 收养（adopt）

关键事实（已核对）：`BGEN` 的读其实发生在**主会话** `ss` 上，读完、出第一个 token 之后，
`copy_to_slot` 才把 `ss` 的 KV 拷进槽（`generate.cpp:11275-11335`、`15247-15270`）。
所以"读"本身不需要槽，**只是 `BGEN` 的协议把槽作为参数**。把这两件事拆开即可：

```
客户端/服务器                     引擎
  BREAD <max_new> <ids...>   →   把 ids 读进主会话 ss，生成第 1 个 token（TOK ...），
                                 把准入所需的参数暂存，回 BRDY <n>（不碰任何槽）
  （槽满，等某个流结束；解码头继续跑，邻居不受影响）
  BADOPT <slot>              →   copy_to_slot(slot, ss) + 初始化槽 + 回 BADM <slot> 1
                                 （此后与普通 BGEN 准入完全一样：解码、BSTOP、DONE）
```

- **准入时延** = 读到第一个 token 的时间（几百 token 的读约 1–2 s；22.7K 的冷读约 14 s，见 L-046），
  不再等于"最长流剩余时长"。
- **邻居** 在暂存期间照常解码（`ss` 只被读占用；解码走槽的 KV），抖动只来自读本身的 GPU 时间（与现在 BYIELD 同量级）。
- **FCFS**：服务器在"有槽空出"时第一件事是 `BADOPT`，暂存请求优先入槽；新到的请求排在它后面。

### 2.1 协议

| 命令 | 方向 | 语义 |
|---|---|---|
| `BREAD <max_new> <ids...>` | 服务器→引擎 | 与 `BGEN` 同样的参数（`max_new`、ids、可选的采样/图片参数），但不占槽；读、出第 1 token，回 `BRDY` |
| `BRDY <n> <max_new> <cont>` | 引擎→服务器 | 暂存完成：`ss` 里已有 n 个 token 的状态；`cont=1` 表示还能在槽里继续（`max_new>1` 且没被截断） |
| `BADOPT <slot>` | 服务器→引擎 | 把暂存状态收进槽；成功回 `BADM <slot> 1`（失败 `ERR BADOPT ...`） |
| `BCANCEL` | 服务器→引擎 | 丢弃暂存（客户端断开/取消），回 `BRDY 0 0 0` 确认；`ss` 在下一次读时被覆盖 |

**不变量**：
1. 同一时刻**最多一个**暂存；暂存期间再收到 `BGEN`/`BREAD` 回 `ERR staged read pending`（服务器不会这样发，
   它把新请求排队）。
2. `BADOPT` 只接受空闲槽；槽不空回 `ERR BADOPT: slot busy`（服务器重试下一个）。
3. `BCANCEL`/`QUIT` 清空暂存；`QUIT` 时不需要额外的 KV 清理（`ss` 随进程走）。
4. 暂存不影响 `BSTOP`/`BYIELD`/`BADM` 的既有语义。

### 2.2 状态机（服务器侧）

```
新请求 → 有空槽? ── 是 → BGEN（现状路径）
                └─ 否 → BREAD → 等 BRDY
                          ├─ cont=1: 请求进入 "staged" 队列（队首）
                          └─ cont=0: 直接用已收到的 token 结束（不再占槽）
staged 队首 + 任意槽空出（DONE/BSTOP） → BADOPT → BADM 1 → 普通解码
staged 队首 + 客户端断开 → BCANCEL
```

## 3. 引擎改动（0.1.47，全部在 `src/program/generate.cpp`）

原则：**现有路径一行不动**，新行为只在 `STRATA_BATCH_STAGED_READ=1` 时可达；默认关时逐字节同旧行为。

1. **全局开关与暂存结构**（放在 `admit_slot` 声明附近，`11225`）：
   ```cpp
   static const bool staged_read_env = std::getenv("STRATA_BATCH_STAGED_READ") != nullptr;
   struct StagedAdmit {
       bool pending = false;
       std::vector<int32_t> live, imgs;
       strata::kernels::SamplerParams sp;
       int64_t max_new = 0, x = 0, p = 0, turn_at = 0, moved_in = 0, resume = 0;
       int lent_slot = -1, cvec = 0;
       bool cont = false;
       double drain_ms = 0.0;
   } staged;
   ```
2. **`BREAD` 解析**：在 `BGEN` 分支（`11847-11873`）后加同构分支：不要求空槽，设 `staged_read = true`、
   `admit_max_new = mn`，其余把行改写成 `GEN 1 <rest>`（与 BGEN 完全一样，走同一个读路径）。
   `admit_slot` 保持 `-1`（这样现有准入块不会跑）。
3. **GEN 末尾**（`15247` 的 `if (admit_slot >= 0)` 之前）：若 `staged_read`：
   - 计算 `cont`（同 BADM 的公式：`!cancelled && produced_n==1 && admit_max_new>1 && finish=="length" && live.size()==p`）；
   - 把 `live/live_imgs/req_sp/x/p/turn_at/moved_in/resume/lent_slot/cvec_cached/cont/adm_drain_ms` 存进 `staged`，`pending=true`；
   - 打印 `BRDY <live.size()> <admit_max_new> <cont>`，`fflush`，`staged_read=false; admit_max_new=0;`；
   - **不打印 BADM**，不进 `if (admit_slot >= 0)` 块。
4. **`BADOPT <slot>` 处理**（命令循环里、`BGEN` 之前）：校验 `staged.pending` 与槽空闲，然后
   `copy_to_slot(slot, staged.live)` → `ver.set_slot_sampling(slot, staged.sp)` → 初始化 `BSlot`
   （`active/x/p/produced=1/max_new/t0/ids/cvec/img`）→ `batch_mtp` 时 `slot_mtp[slot]->draft_first(...)`
   → 打印 `BADM <slot> 1`，清 `pending`。**v1 不带 `checks`**（暂存请求的对话检查点不进缓存；见 §5）。
5. **`BCANCEL`**：清 `pending`，回 `BRDY 0 0 0`。
6. **MTP**：暂存读按普通 `GEN` 走（`admit_slot=-1`），`batch_mtp` 时 `copy_to_slot` 会一并拷 draft KV
   （`11275` 里的 `if (batch_mtp)` 分支），`BADOPT` 里补 `draft_first`。

改动量估计：+120 行左右，无现有行修改（除新增解析分支），风险集中在 `BADOPT` 的槽初始化要与
`15258-15280` 的 BADM 路径保持一致。

## 4. 服务端改动（0.2.2 `serve/server.py`）

- `pick_slot`（`2104`）返回 `None` 时，不阻塞等待，而是进入暂存路径：把请求的 ids 记下来，
  在"控制行"循环里先发 `BREAD`（读仍是控制行，`self.ctl` 语义不变，`1179`→`2003`）。
- 收到 `BRDY n 1`：请求进入 `staged` 队首；收到任意槽的 `DONE`/`BSTOP` 后，下一次控制行发 `BADOPT <slot>`。
- 收到 `BADM <slot> 1` 后与普通准入完全同路径（把该请求挂到 slot）。
- 超时/断连：发 `BCANCEL`。
- 默认关：`STRATA_MP_STAGED_READ=1`（服务端 env）才启用；否则走原 `pick_slot` 等待。

**部署注意（R4 踩到的坑）**：`strata-022.json` 的 `env` 只进**引擎子进程**，服务端进程看不到
（`/proc/<server>/environ` 里没有）。所以服务端开关要在 `run-022.sh` 里 `export STRATA_MP_STAGED_READ=1`
（备份原脚本，测完恢复），而不是只加进配置 `env`。

## 5. v1 限制（写进 FEATURE_PARITY 判决）

1. 暂存请求的对话检查点（`checks`）不搬进槽：这一路的会话缓存收益会少一点（不影响正确性、不影响 T4 的
   前缀缓存——T4 无满槽场景）。v2 再把 `checks` 带上（需要把 `AdmitStage` 的 `checks` 在 `BADOPT` 时 move）。
2. 单暂存：同一时刻只暂存一个请求；后续请求排队（FCFS，符合判据）。
3. 图片请求（`GENI`）v1 不走暂存（`req_imgs.empty()` 才发 `BREAD`）。
4. 不看 `--prompt-cache` 的 slot 复用/检查点移动（暂存请求从 0 读）；这对"冷读"是正确形状，对"接着会话读"
   会少一次复用（可接受，v2 再说）。

## 6. 门与验收

| 门 | 内容 | 判据 |
|---|---|---|
| G0 构建 | `build-mp/` 从 0.1.47 全量构建 | 链接成功，`md5sum` 记录；不改生产 `build/` |
| G0' 逐位 | 开关**关**：同一请求的输出/`DONE` 计数与旧二进制一致 | 文本完全相同（贪心） |
| G1 准入 | T3（4×1200 流 + 452 冷读） | 读者首 token **≤2 s**（基线 27.2 s） |
| G2 邻居 | 同上，邻居最长 chunk 间隔 | **≤1 s**（基线 0.68 s，不能变坏） |
| G3 聚合 | bench 4 路 ×3 | ≥ 120.5 × 0.97 = **116.9 tok/s** |
| G4 质量 | T1/T2/T4 + 一次 14 题 agent 评测 | API 契约全过；exact 臂 batch 不变；agent ≥13/14、0 畸形 |
| G5 重启 | 安装/恢复 | ≤2 次；恢复 `cmp -s` 一致 |

## 7. 回滚

- 引擎：恢复 `build/strata` 的备份二进制（md5 对照），`systemctl restart strata`；
- 服务端：`serve/server.py` 备份 + 恢复；或只把 `STRATA_MP_STAGED_READ` 从配置里去掉（默认关即旧行为）。

## 8. 仓库交付

- 补丁文件：`tools/server/patches/mp-w3-0.1.47.patch`（`diff -u` 于 `/opt/ai/src/strata-mp` 与生产源码副本）；
- 同一补丁在 0.1.48（本仓库）上重放，附 `git apply --check` 结果；
- 台账：L-048（构建与逐位）、L-049（准入/邻居/聚合）、L-050（质量）；
- 日志：`MP-R3-build.log`、`MP-R4-*`（A-B-A）。
