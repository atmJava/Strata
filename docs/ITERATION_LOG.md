# 自主迭代复盘

> 每次形成判决后，在最上方追加一条复盘。格式见 [`RULES.md`](RULES.md) §6。
> 完整证据在 [`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md)；当前断点在 [`STATE.md`](STATE.md)。

## I-20261027-130 · 清理已删除入口的孤儿测试与夹具

- 目标 / 指标：工具测试必须只覆盖仍存在的 CLI/server 代码；不得因 `setup.py`、Docker entrypoint、SYCL launcher
  或旧 MCP 模块已删除而在收集阶段导入失败。
- 假设与预期：主线收敛时已删除这些生产入口，但仍留下直接导入它们的测试和两份大型 setup 快照夹具；它们既不能提供
  当前行为证据，又会掩盖真正回归。
- 为什么选这条路线：先只裁剪已无被测对象的用例，保留 `user_args`、`calibrate`、`strata_pack` 仍有效的断言，并为
  `user_args` 的 `apply/stamp/set_flag/dropped_by_setup` 增加当前入口测试。
- 改动与实验：删除 7 个孤儿测试/夹具文件，裁剪 `test_calibrate`、`test_shards`、`test_mtp_fetch` 中旧 setup/容器
  断言，重写 `test_user_args` 为当前 server/runconfig 用例。工具集 `206 passed, 9 skipped, 116 subtests passed`；
  `tools/test_iq_pack.py` 因缺少外部 `llama.cpp/gguf-py` 未运行。
- 判决：采纳。工具测试收集不再依赖已删除模块，静态夹具减少约 3.5k 行。
- 生产状态与回滚：不改变运行时代码；单提交可回退。
- 未验证：缺少 `gguf-py` 的外部量化用例仍未运行。
- 下一步与理由：检查文档中的已删除入口引用，再做最终 Python/C++ 回归和远端 SHA 复核。

## I-20261027-129 · PinnedRing release 失败保留可重试所有权

- 目标 / 指标：`PinnedRing::release()` 在无法进入 slot 所属 device，或 event/slot 清理失败时，不得把未释放资源标成已释放；
  故障解除后同一对象应能完成清理。
- 假设与预期：旧实现无条件清空 device 与 slot 状态；即使用户已发现跨设备风险，一次临时驱动失败仍会造成 permanent leak，
  析构也无法重试。
- 为什么选这条路线：在现有 `pinned_ring_test` 的两个 device 和 CUDA wrappers 上注入切换/free 失败，直接验证 owner 状态机，
  不改变正常上传或事件路径。
- 改动与实验：`release()` 返回清理是否完整；进入 device 失败时保持全部状态，逐项清理只在成功时清空对应句柄，并在部分失败时
  保留剩余资源。新增 device-switch 失败、free 失败与成功重试用例。
- 判决：采纳。release 报告的完成状态现在与资源所有权一致。
- 生产状态与回滚：正常单卡释放只增加成功状态判断；单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动或 GPU；故障由 link wrapper 注入，尚未在真实多卡 context 中制造。
- 下一步与理由：完成全量回归、文档检查和远端 SHA 复核；07:50 后不再开始新改动。

## I-20261027-128 · GpuEventPairs release 失败保留事件所有权

- 目标 / 指标：`GpuEventPairs::release()` 无法切回事件所属 device 时不得清空事件句柄；后续 `ensure()` 也不得覆盖旧组。
  单个 destroy 或恢复调用者 device 失败应向调用者报告。
- 假设与预期：旧 release 只在切换成功时销毁，却无条件 `clear()` 并把 device 置为 -1；一次 device switch 失败会永久丢失
  全部事件，随后 ensure 会创建新组。
- 为什么选这条路线：复用已经登记的 `gpu_event_pairs_test`，通过真实 failure wrappers 验证 retain/retry，改动集中在 owner
  自己的状态机。
- 改动与实验：`release()` 返回成功状态；进入 device 失败时保留整组，destroy 失败时保留未销毁句柄，恢复失败时报告但所有
  已销毁事件不重复释放；`ensure()` 在旧组清理失败时停止。`ready()` 同时检查所有句柄非空。新增 device-switch retain/retry
  和 restore-failure 用例，普通及 ASan/UBSan 通过，clang analyzer 通过。
- 判决：采纳。release 的返回值与实际资源所有权一致。
- 生产状态与回滚：正常 release/析构路径不增加驱动调用，只增加状态检查；单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动或 GPU；switch/destroy failure 由 link wrapper 注入，未在真实多卡 context 中制造。
- 下一步与理由：完成全量回归、文档检查和远端 SHA 复核；07:50 后不再开始新改动。

## I-20261027-127 · Peer close 清空旧 pending 与 resident 视图

- 目标 / 指标：`PeerExperts::close()` 成功后，旧 tier 的 pending swap、上次 launch 和 resident 计数不得泄漏到下一次
  `open()` 或对外状态查询。
- 假设与预期：旧 close 同步并释放 stream/cache，但保留 `pending_`、`row_of_` 与 `resident_`。同一对象重开新 tier 时，
  旧 pending 可能在 apply 阶段写入新 residency 表；`resident()` 在已 close 状态也仍报告旧数量。
- 为什么选这条路线：close 已经确认设备与 stream 可用并完成同步，此时清空纯 host 状态无需额外驱动调用，也不改变首次
  open 的生命周期。
- 改动与实验：close 清空 pending/row/res，重置 `resident_`、`launched_rows_` 与 direct 标志；peer 严格主机语法检查和
  clang analyzer 通过。
- 判决：采纳。关闭后的对象不再保留旧 tier 的可发布状态。
- 生产状态与回滚：正常运行只在销毁或显式 close 时执行；单提交可回退。
- 未验证：当前无真实 CUDA 环境，无法执行同一 object 的多轮成功 open/close；该路径由状态审查和严格编译检查覆盖。
- 下一步与理由：完成最终 C++ 主机回归、Python 全量与文档一致性检查，07:50 后停止新改动。

## I-20261027-126 · 新增设备与远端初始化测试注册到 CTest

- 目标 / 指标：`on_device_test` 与 `remote_expert_opt_init_test` 除手工主机编译外，也应进入 Linux CUDA 构建的
  CTest 测试清单。
- 假设与预期：两个测试都通过 link wrapper 替换所需 CUDA 调用，可在 `CUDA_VISIBLE_DEVICES=-1` 下运行；Python 或
  独立主机二进制通过不等于标准构建会执行它们。
- 为什么选这条路线：复用现有 `STRATA_BUILD_TESTS`、GNU/Clang 主机测试分组和 wrapper 模式，不新增构建系统分支。
- 改动与实验：`on_device_test` 仅链接 `strata_warnings`；`remote_expert_opt_init_test` 链接 `strata_engine` 以取得
  `RemoteExpertOpt` 实现，并注册相同的 CUDA failure wrappers、环境与 30 秒超时。两个测试的独立普通及 ASan/UBSan
  构建已通过。
- 判决：采纳。新增回归可由 `ctest` 发现。
- 生产状态与回滚：仅改测试构建，不影响发行二进制；单提交可回退。
- 未验证：本机没有 CUDA toolkit，无法重新 configure/build CTest；CMake 变更只按现有同类 target 模式做静态核对。
- 下一步与理由：网络恢复后推送待发提交，再做最终全量测试和截止报告。

## I-20261027-125 · Peer 轮询不把 NotReady 留成后续操作的错误

- 目标 / 指标：`cudaStreamQuery` / `cudaEventQuery` 返回期望的 `cudaErrorNotReady` 后，不得让它作为 sticky last error
  影响后续 kernel launch 或资源操作。
- 假设与预期：PeerExperts 的 finish 轮询与异步 adapt apply 都直接处理返回码，但没有消费驱动记录的 NotReady；后续
  `cudaGetLastError` 路径可能把一次正常等待误报成故障。
- 为什么选这条路线：只在最终成功或继续等待时消费期望错误，不改变 polling 的循环与热路径时序。
- 改动与实验：`finish` 的流轮询结束后若最终成功则清一次 last error；`apply_pending(false)` 把 NotReady 作为正常等待，
  消费该错误后保留 pending。严格主机语法检查与 clang analyzer 通过。
- 判决：采纳。正常未完成状态不再污染后续 CUDA 错误状态。
- 生产状态与回滚：成功轮询路径多一次 `cudaGetLastError`，仅用于清除已观察到的 NotReady；单提交可回退。
- 未验证：当前无 CUDA 驱动可观察真实 sticky error 行为；结论依据 CUDA runtime 的 polling 语义与静态控制流。
- 下一步与理由：完成 close/free 失败语义审查后进入最终全量回归，避免在没有驱动证据时扩大释放行为改动。

## I-20261027-124 · Peer adapt 只发布完整完成的复制批次

- 目标 / 指标：`PeerExperts::adapt()` 的 copy + event 批次只有全部成功并等到 event 成功后才能更新 residency；
  copy、event record 或 event wait 失败时，`pending_` 不得在后续 `apply_pending()` 中被误发布。
- 假设与预期：旧代码在批次中途 copy 失败后保留此前 pending；event record 失败也保留 pending。若调用者没有立即退出，
  后续 apply 可能把尚未确认完成的副本标成 resident。wait 返回错误时旧实现同样继续发布。
- 为什么选这条路线：目的地已经先标为 non-resident，失败后丢弃 pending 最多浪费本轮 slot，不会让后续 token 读取未确认
  的 expert 字节；这是故障路径上优先保证正确性的最小改动。
- 改动与实验：cudaMemcpyAsync 或 cudaEventRecord 失败时清空本批 pending；`apply_pending()` 只有 event 成功才发布，
  NotReady 保持等待，其他错误清空 pending 并消费 driver error。严格主机语法检查与 clang analyzer 通过。
- 判决：采纳。residency 只跟随完整完成的复制批次。
- 生产状态与回滚：成功路径不变；只改变 CUDA 故障后的状态处理，单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动或 GPU；copy/event 故障路径由静态控制流审查验证，尚无真实驱动故障注入。
- 下一步与理由：继续检查 close 中逐个 free/destroy 失败后的所有权记录，并运行最终全量回归。

## I-20261027-119 · Pin reserve 释放失败不丢所有权

- 目标 / 指标：soft/hard pin reserves 只有 `cudaFreeHost` 成功后才能清空指针、字节数和预算；失败后保留原记录，
  后续 `pin_reserve_release_*` 仍可重试。
- 假设与预期：旧 `free_locked()` 不返回驱动结果，reserve 调用方无论成功失败都立即把指针和字节归零；释放失败后
  既失去重试入口，也让“仍占用的 pin 内存”在预算中消失。
- 为什么选这条路线：让 `free_locked()` 返回 `cudaError_t`，两个 reserve 释放函数仅在成功时 clear；
  `host_alloc_guarded()` 在释放 headroom 后也只对成功释放的 reserve 清状态；普通 guarded free 仍保持接口不变。
- 改动与实验：`mapped_host_test` 模拟 800 MiB reserves，注入 soft free 失败并断言指针/字节保留，重试后清空；
  hard reserve 成功释放后 `sim_used == 0`。普通及 ASan/UBSan 通过，MTP clang static analyzer 无告警。
- 判决：采纳。reserve 的驱动所有权与模拟预算现在只在成功释放后同步消失。
- 生产状态与回滚：reserve 默认在 Linux 关闭；成功路径不变，只在驱动释放失败时保留状态，单提交可回退。
- 未验证：当前无 CUDA 工具链或驱动，free failure 由 link wrapper 注入；未测试真实 WDDM 下 reserve free 失败。
- 下一步与理由：完成最终回归、远端 SHA 复核和 08:00 截止报告。

## I-20261027-120 · PinnedRing 事件测试兼容真实 CUDA 句柄类型

- 目标 / 指标：主机侧 `pinned_ring_test` 用 stub event 验证设备归属时，转换必须兼容真实 CUDA
  `cudaEvent_t` 的不透明指针定义。
- 假设与预期：测试把 `Event*` 与 `cudaEvent_t` 直接 `static_cast`，只在当前 shim 将两者都定义成兼容指针时成立；
  在真实 CUDA 头文件下可能直接编译失败。
- 为什么选这条路线：只改测试桩的指针往返转换，不改被测生产代码或运行时语义，风险最低且可直接由主机测试验证。
- 改动与实验：`cudaEvent_t` 的创建与销毁改用 `reinterpret_cast` 在 stub `Event*` 和不透明句柄之间转换；
  `pinned_ring_test` 普通构建以及 ASan/UBSan 构建均通过，验证 release 在 ring 自有 device 上执行并恢复调用者 device。
- 判决：采纳。测试桩不再依赖当前临时 CUDA shim 的句柄可转换性假设。
- 生产状态与回滚：不修改生产路径；单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动或 GPU，因此尚未用真实 CUDA 头文件编译该测试。
- 下一步与理由：继续检查 peer/remote expert 与 prefill 的多块 pinned host 分配、失败清理和跨设备释放。

## I-20261027-121 · RemoteExpertOpt 初始化失败完整回滚并可重试

- 目标 / 指标：`RemoteExpertOpt::init()` 的 host staging、mapped alias 与每个 helper 的 reduction sum 必须全有或全无；
  任一步骤失败后立即释放前序资源，失败对象仍可再次 `init()`。
- 假设与预期：旧实现按短路顺序获取资源，失败直接返回；这部分资源要等到对象最终析构才释放，重复初始化还会覆盖
  已有指针。
- 为什么选这条路线：初始化只在启动时执行，用统一 `release()` 收尾不会改变热路径；同时记录每个 peer 分配时的 device，
  清理不再依赖 `RemoteExperts` 此刻仍处于 open 状态。
- 改动与实验：`init()` 先清理旧状态，mapped alias 或任一 `cudaMalloc` 失败时回滚全部 host/device 资源；析构与显式
  release 共用幂等清理。新增 `remote_expert_opt_init_test` 注入 mapping 失败和第二个 peer allocation 失败，随后重试并
  核对 host/device 分配与释放次数。普通及 ASan/UBSan 通过；CUDA host-only 严格语法检查通过，clang analyzer 未在
  host 初始化/释放路径发现告警（仅报告 kernel 内 `threadIdx` 未建模导致的 `skip` 未初始化假阳性）。
- 判决：采纳。初始化失败不再保留半套 staging，资源释放有直接故障测试。
- 生产状态与回滚：成功初始化后的布局、数据与 kernel 参数不变；单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动或 GPU；测试使用 declaration-only CUDA shim 和 link wrapper 注入失败，尚未在真实
  多卡 context 中制造 `cudaHostGetDevicePointer` / `cudaMalloc` 失败。
- 下一步与理由：继续检查 peer/remote 的 device switch 失败、adapt 批次失败和跨设备析构；优先只改可静态证明的资源状态。

## I-20261027-123 · RemoteExpertOpt 清理失败不覆盖保留的 peer sum

- 目标 / 指标：`release()` 遇到 device switch 或 `cudaFree` 失败时必须保留对应 peer sum；此后的 `init()` 不得向非空
  指针再次 `cudaMalloc`，必须先确认旧资源已全部释放。
- 假设与预期：在 I-121 的“切换失败保留状态”分支上，`release()` 只返回 `void`，这让 `init()` 无法知道旧 sum 仍在；
  若继续初始化会覆盖指针并永久泄漏前一份 device 内存。
- 为什么选这条路线：把清理结果显式返回给唯一的初始化入口，失败时直接报告并保持对象可重试；独立 host staging 仍可在
  peer device 清理失败时先安全释放。
- 改动与实验：`release()` 返回是否全部成功，并仅在 `cudaFree` / `cudaFreeHost` 成功后清空对应指针；`init()` 在旧资源
  未完全释放时停止。扩展故障测试：注入 `cudaFree` 失败，断言 host 已回收、两个 peer sum 仍在且没有新分配；解除故障后
  重试，断言旧 sum 被释放并由完整新资源集替换。普通及 ASan/UBSan 通过。
- 判决：采纳。初始化不会覆盖尚未释放的 device 所有权。
- 生产状态与回滚：正常成功路径只多一次布尔检查；单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动或 GPU；`cudaFree` 失败由 link wrapper 注入，尚未验证真实 driver/context 丢失后的
  可恢复性。
- 下一步与理由：继续处理 `PeerExperts::adapt()` 批次中途失败，避免未记录完成的异步复制被后续 apply 误发布。

## I-20261027-122 · device 切换失败不再丢失 peer/remote 资源

- 目标 / 指标：`OnDevice` 必须报告 `cudaGetDevice` / `cudaSetDevice` 的失败；PeerExperts 和 RemoteExperts 在无法切回
  所属 device 时，不得把仍由该 device 持有的句柄清空或当成已释放。
- 假设与预期：旧 `OnDevice` 静默吞掉切换错误，peer 的 `close()` 随后会在错误 context 上 free/destroy 并把成员清零；
  RemoteExperts 虽有 `DeviceScope`，也仍会在切换失败后清空成员，导致所有权丢失。
- 为什么选这条路线：device 归属是资源释放的前提；先让作用域显式返回状态，再让 close 在失败时保留状态供重试，调用路径
  的成功分支不变。同时把 P2P 自检中的硬编码 device 0 改为实际 primary device。
- 改动与实验：`OnDevice` 增加 `ok/error`；peer 的 open/launch/finish/adapt/apply 与 remote 的 close/open 检查切换状态。
  切换失败时 close 保留原成员，后续 open 明确拒绝覆盖未释放资源。新增 `on_device_test` 覆盖成功切换与恢复、同设备 no-op、
  `cudaSetDevice` 失败、`cudaGetDevice` 失败和负 device；普通及 ASan/UBSan 通过。peer/remote 严格语法检查和 clang
  analyzer 通过，新增回滚逻辑未报告告警。
- 判决：采纳。资源释放现在以成功进入所属设备为前提，切换失败不会再伪造释放完成。
- 生产状态与回滚：正常切换路径行为不变；P2P 自检从固定 device 0 改为 `primary_device()`，只在非零 primary ordinal
  下修正原错误。单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动或 GPU；peer/remote 的真实 close 切换失败路径只做严格静态检查，故障注入测试只覆盖
  `OnDevice` 本体，未在双 RTX 2080 Ti context 中制造 driver 切换失败。
- 下一步与理由：检查 peer `adapt()` 的批次中途失败，避免未记录的异步复制状态被后续 `apply_pending()` 误发布。

## I-20261027-118 · PinnedRing 在自有 device 上释放

- 目标 / 指标：PinnedRing 的 event 与 pinned slots 必须在其首次使用的 CUDA device 上释放；成员析构发生在调用者的
  `OnDevice` 作用域结束之后时，也必须恢复调用者原来的 device。
- 假设与预期：MTP 的 `pf_ring_` 是对象成员，`MtpDrafter::release()` 只能覆盖自身函数体；成员析构晚于 `OnDevice`
  析构，多卡 layer split 时可能在默认 device 0 上销毁属于另一张卡的 event。Prefill 代码虽手动调用 release，但其
  资源安全不应依赖每个调用者的析构顺序。
- 为什么选这条路线：让 Ring 自己保存并切回 `dev_`，清理后恢复 caller device；同时在 `MtpDrafter::release()` 的
  设备作用域内提前 release。两层保证正常析构、显式释放和未来调用者都不会跨设备销毁句柄。
- 改动与实验：新增 `pinned_ring_test`，在 device 1 acquire 后切到 device 0，断言 release 在 device 1 销毁 event/free
  slot、随后恢复 device 0，并验证二次 release 幂等。普通及 ASan/UBSan 通过；`mtp.cpp` 严格语法检查通过。
- 判决：采纳。PinnedRing 的设备归属由类型自身维护，不依赖析构调用栈。
- 生产状态与回滚：单卡下 device 相同，release 行为不变；多卡只修正上下文顺序，不改上传或事件语义，单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动或双 RTX 2080 Ti，未在真实 CUDA context 中触发跨卡析构；主机 link-wrap 验证了
  device 切换、event 销毁、slot 释放和恢复顺序。
- 下一步与理由：继续检查其他成员析构类（profiler、event pair、共享 owner）是否同样需要自带 device 上下文。

## I-20261027-117 · free 失败保留映射与预算

- 目标 / 指标：`cudaFreeHost` 失败时，guarded free 不能把指针当成已释放，也不能提前归还模拟 pin 预算；同一指针
  在驱动恢复后再次释放应成功并完成扣账。
- 假设与预期：新的 iterator 扣账只在驱动成功时执行，但缺少直接故障测试；若 wrapper 先计数或预算先减，仍可能
  在失败时产生假释放。
- 为什么选这条路线：在现有 `mapped_host_test` 注入一次 free failure，检查 free 次数与 `sim_used` 都不变，再关闭
  注入重试同一 host 块，验证只释放一次并归零。
- 改动与实验：普通及 ASan/UBSan `mapped_host_test` 通过，覆盖 successful free、failed free、retry、register 与
  failed unregister。
- 判决：采纳。host free 的失败和重试语义已有主机侧直接测试。
- 生产状态与回滚：只增加故障测试，不修改生产释放逻辑；单提交可回退。
- 未验证：当前无 CUDA 工具链或驱动，`cudaFreeHost` 失败由 link wrapper 注入，不替代驱动级错误注入。
- 下一步与理由：继续运行全量 Python 与主机测试，网络恢复后推送。

## I-20261027-116 · 注销失败保留 pin 预算

- 目标 / 指标：`pin_unregister()` 只有 `cudaHostUnregister` 成功后才归还模拟 pin 预算；失败时必须保留原记录，后续
  重试仍能看到同一笔占用。
- 假设与预期：旧实现先在 map 中 uncharge，再调用 unregister。驱动注销失败时内存仍在 pin 状态，但测试预算已经
  归还，后续分配会错误地超过真实上限。
- 为什么选这条路线：与 free 路径统一成“先找记录、调用驱动、成功后按 iterator 扣账并 erase”。同时删除已不再使用的
  `sim_uncharge()`，把扣账收敛到两个实际驱动操作旁边。
- 改动与实验：`mapped_host_test` 新增 host-register 预算、注入 unregister 失败、失败后预算不变、重试成功归零的
  检查；CMake link-wrap 增加 register/unregister。普通及 ASan/UBSan 通过。
- 判决：采纳。注册与注销失败都不会伪造预算归还。
- 生产状态与回滚：模拟预算关闭时直接调用 `cudaHostUnregister`，生产成功路径与返回值不变；单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动或 RTX 2080 Ti，未用真实驱动拒绝 unregister；主机 link-wrap 覆盖失败分支和
  模拟预算。
- 下一步与理由：完成剩余全量回归并重试推送；若无网络则整理本地领先提交和明确风险。

## I-20261027-115 · Pin 预算释放避免 free 后地址查表

- 目标 / 指标：`host_free_guarded()` 必须在 `cudaFreeHost` 成功后准确归还模拟 pin 预算；释放失败时预算计数不能提前
  扣减，释放后的指针值也不能再用于 map 查找。
- 假设与预期：旧 `free_locked()` 先 free，再把同一个地址传给 `sim_uncharge()` 查 map。clang 静态分析在 MTP、
  DFlash2 和 Verifier 上都报告 `unix.Malloc` use-after-free；即使只比较地址值，也把记账绑在失效指针上。
- 为什么选这条路线：free 前保存 map iterator 和字节数，free 成功后才按 iterator 扣计数并 erase。map 节点与 CUDA
  分配生命周期独立，迭代器不依赖已释放地址；失败则保留原记录。
- 改动与实验：`mapped_host_test` 增加真实模拟预算检查：分配后 `sim_used == 64`，guarded free 后归零，普通及
  ASan/UBSan 均通过；`mtp.cpp` 的 clang static analyzer 不再报告该路径。测试通过 declaration-only CUDA shim 运行
  link-wrap，避免 inline stub 绕过 wrap。
- 判决：采纳。pin 预算记账不再使用已释放指针，并覆盖 free 失败时的一致性。
- 生产状态与回滚：未启用 `STRATA_TEST_PIN_BUDGET_MIB` 时仍只调用原生 `cudaFreeHost`；正常分配/释放行为不变，单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动或 RTX 2080 Ti，未真实注入 `cudaFreeHost` 失败；当前验证覆盖模拟预算与
  link-wrap 释放路径。
- 下一步与理由：重新跑 clang 静态分析和全量回归，确认没有新的 owner/生命周期告警。

## I-20261027-114 · DFlash2 bind 状态与 vocab 所有权事务化

- 目标 / 指标：DFlash2 `bind_raw()` 只能在 vocab 上传、宽高检查和 logits 分配全部成功后发布 embed/head/宽度；
  rebind 从 subset 切到 whole head（或反向）必须释放旧的自有缓冲，`vram_bytes()` 只统计本实例真实持有的字节。
- 假设与预期：旧实现先写 `embed_/head_w_/head_type_/n_dvocab_` 再分配，失败会留下与 `bound_` 不一致的字段；
  rebind 到 whole head 时旧 `dvocab_/dhead_` 也不会清空，可能继续把旧 subset 映射传给 kernel，VRAM 计数则会重复。
- 为什么选这条路线：`alloc_logits()` 改为显式接收目标宽度，先把所有候选资源建好，再一次性发布；新增
  `dvocab_bytes_/dhead_bytes_` 记录本实例自有（而非 shared alias）的字节。单卡 subset、whole head、shared slot 和
  两种重绑都在同一路径更新 owner、指针、host 副本和 VRAM。`release()` 同时清掉 owner 字节与 embed callback。
- 改动与实验：保持首次 bind 的 head row 内容、vocab 顺序、logits 宽度和 graph 输入不变；补齐 rebind 的旧资源释放与
  count 调整。`dflash2.cpp` 通过 C++20 主机侧严格语法检查（仅跳过仓库既有未使用/缩进告警）；
  `tools/test_dflash2_pack.py` 通过（3 passed、1 skipped）。
- 判决：采纳。DFlash2 的绑定状态、vocab/head 指针、所有权和显存统计现在同进同退。
- 生产状态与回滚：不改 pack 格式、选择算法或默认首次绑定；只影响 rebind/shared 组合，单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动、真实 DFlash2 pack 或 RTX 2080 Ti，未在 GPU 上执行 own/shared/whole/subset
  四种 rebind 组合，也未注入 logits 分配失败。
- 下一步与理由：继续检查 MTP/DFlash2 的 opt-in profiler 和共享 slot 生命周期，寻找跨设备的释放与并发捕捉窗口。

## I-20261027-113 · quant format 测试支持直接执行

- 目标 / 指标：`python3 tools/test_quant_formats.py` 与 `python3 -m tools.test_quant_formats` 都应运行同一 2 个测试。
- 假设与预期：脚本使用 `import tools.quant_formats`，但直接执行时 Python 只把 `tools/` 放进 `sys.path`，仓库根目录
  不在导入路径，因而在任何测试执行前报 `ModuleNotFoundError: tools`。
- 为什么选这条路线：测试脚本可以像其他直接运行的 tools 测试一样，用自身位置推导仓库根目录并加入 `sys.path`；
  不需要改包结构或生产 `quant_formats.py`。
- 改动与实验：增加基于 `__file__` 的根目录插入；两种调用方式都通过 2 tests，`compileall` 与 `git diff --check` 通过。
- 判决：采纳。该测试不再依赖调用者的当前目录或 `PYTHONPATH`。
- 生产状态与回滚：未修改量化读取逻辑；只改测试的导入入口，单提交可回退。
- 未验证：仅在 Linux x86_64、Python 3.13 上验证；未覆盖 Windows 路径分隔符，但 `pathlib.Path` 与现有测试模式一致。
- 下一步与理由：继续运行其余非 GPU 工具测试，并把测试入口差异收敛到最小。

## I-20261027-112 · DFlash2 重绑定保留旧 logits

- 目标 / 指标：DFlash2 在重新绑定向更宽的 draft vocabulary 时，只有新 logits 缓冲成功分配后才能释放旧缓冲和
  调整 VRAM 计数；alloc failure 不得破坏仍可用的旧实例。
- 假设与预期：`alloc_logits()` 旧路径先 free 旧缓冲、清容量并从 `vram_` 减去尺寸，再向成员分配新块。`cudaMalloc`
  失败时对象失去旧 logits，且容量/VRAM 已经是“未分配”状态。
- 为什么选这条路线：与 MTP 的修复采用同一事务顺序：局部新块成功后，才 free 旧块并替换指针，随后一次性更新容量和
  `vram_`。槽位共享、同宽重绑定和缩窄重绑定的成功语义不变。
- 改动与实验：不改变 logits 宽度公式、行数、使用的 kernel 或成功路径容量；只改失败路径。`dflash2.cpp` 通过
  C++20 主机侧严格语法检查（仅跳过仓库既有未使用/缩进告警）；DFlash2 Python pack 测试通过。
- 判决：采纳。DFlash2 logits 重绑定不再先破坏旧资源。
- 生产状态与回滚：默认首次 bind 的行为完全不变；仅重绑定且新分配失败时保留旧缓冲，单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动、DFlash2 pack 或 RTX 2080 Ti，未真实注入 logits allocation failure。
- 下一步与理由：继续检查 DFlash2 bind 的成员发布顺序，重点看失败后 `bound_` 与 head 指针是否一致。

## I-20261027-111 · MTP 重绑定保留旧 logits 与 draft head

- 目标 / 指标：MTP `bind()` 需要更宽的 logits 时必须先成功取得新缓冲，再释放旧缓冲；`--mtp-q4=head` 转换失败时，
  原来的 draft-head rows 必须保持完整可用。两个失败分支都不能覆盖仍有效的成员。
- 假设与预期：旧 logits 扩容先 `cudaFree(head_logits_)` 再向成员 `cudaMalloc`，分配失败会同时丢失旧缓冲和容量。
  Q4 head 直接在 `dhead_` 上前半段覆盖，H2D copy 中途失败会留下 Q4/原格式混合的数据，而 `dhead_type_` 仍是旧格式。
- 为什么选这条路线：logits 先在局部 `replacement` 分配，成功后才 free/swap；Q4 rows 先量化到 host，再上传到新的
  private device 块，上传成功后才替换 `dhead_` 并更新类型。旧缓冲因此保留到新资源完整可用。
- 改动与实验：成功路径的 logits 宽度、Q4 行内容、gather 输入、显存上限与保守 VRAM 记账不变；失败路径不再改动
  旧成员。`mtp.cpp` 通过 C++20 主机侧严格语法检查（仅跳过仓库既有未使用/缩进告警）；`mtp_logits_width_test` 通过
  116 checks。
- 判决：采纳。MTP 重绑定与 Q4 转换改为“先构建、后发布”。
- 生产状态与回滚：默认无 Q4 的路径只改变 logits 扩容失败分支；成功推理数值不变，单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动、MTP pack 或 RTX 2080 Ti，未真实注入 logits/Q4 allocation 与 H2D copy 失败。
- 下一步与理由：继续查找加载阶段直接写成员后仍未统一回滚的多资源初始化，并补可主机验证的边界测试。

## I-20261027-110 · MTP profiler 初始化与设备释放补齐

- 目标 / 指标：`STRATA_MTP_PROFILE` 的 device stamp 缓冲必须在分配和清零都成功后启用，读取失败后停止采样，
  并在进程退出时回到创建它的 device 上释放。
- 假设与预期：旧 `MtpProf::init()` 不检查 `cudaMemset`；清零失败仍保留 `on=true`，随后把未初始化 stamp 当时间。
  `collect()` 忽略 D2H copy 失败，可能继续累计过期数据；全局缓冲也没有析构释放。
- 为什么选这条路线：初始化先在局部记录当前 device，allocation/memset 合成一个成功条件，失败立即 free 并关闭诊断；
  collect 失败也关闭。析构使用 `OnDevice` 切回分配卡后 free，避免在默认卡上释放另一张卡的指针。
- 改动与实验：不启用 `STRATA_MTP_PROFILE` 时行为不变；启用时成功路径的 buffer 大小、stamp 位置和报告内容不变。
  `mtp.cpp` 通过 C++20 主机侧严格语法检查（仅跳过仓库既有未使用/缩进告警）；`mtp_logits_width_test` 通过
  116 checks。
- 判决：采纳。诊断路径不再使用未初始化缓冲，也不再依赖进程回收 device 分配。
- 生产状态与回滚：只影响 opt-in profiler；默认推理路径和数值不变，单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动或 RTX 2080 Ti，未真实注入 memset/D2H 失败，也未验证 CUDA runtime 在进程
  静态析构阶段的关闭顺序。
- 下一步与理由：继续检查 MTP bind 的重新分配与 Q4 head 转换，避免失败时覆盖仍有效的旧缓冲。

## I-20261027-109 · 修复 GGUF 路径测试的过期 mock

- 目标 / 指标：`python3 -m unittest tools.test_gguf_paths -v` 必须全部通过，并继续验证环境变量提示。
- 假设与预期：`_paths.py` 已收敛为 `gguf_py()` 内部构造候选列表，测试仍在 patch 已不存在的 `_install_dirs`，因此
  mock 在进入上下文时直接抛 `AttributeError`，没有执行路径查找。
- 为什么选这条路线：这是测试与当前 API 脱节，不需要改生产路径解析；删除过期 mock 后，临时不存在的 `ENGINE` 已足以
  强制走缺失分支并检查异常文本。
- 改动与实验：`test_env_wins_and_message_names_it` 继续清除 `STRATA_GGUF_PY` / `STRATA_INSTALL_DIR`，并把 `ENGINE`
  指向临时目录；移除不存在属性的 patch。`python3 -m unittest tools.test_gguf_paths -v` 通过 2 tests。
- 判决：采纳。该测试恢复为有效的黑盒契约检查。
- 生产状态与回滚：未修改 `_paths.py` 或安装路径行为；只修测试，单提交可回退。
- 未验证：只在当前 Linux x86_64、Python 3.13 环境运行；未在 Windows 安装目录布局上重跑。
- 下一步与理由：继续做剩余可运行故障测试与静态资源审查，并定期重试 origin 推送。

## I-20261027-108 · Mapped staging 按 pin 预算统一释放

- 目标 / 指标：通过 `alloc_mapped_pinned()` / `host_alloc_guarded()` 取得的 mapped-pinned host 内存，必须通过
  `host_free_guarded()` 释放；失败回滚和正常析构都要把同一笔 pin 预算归还。Verifier 的 trace 映射区也必须有所有者。
- 假设与预期：MTP、DFlash2 和 Verifier 的分配已经走 guarded allocator，但析构仍直接调用 `cudaFreeHost`。在
  `STRATA_TEST_PIN_BUDGET_MIB` 模拟耗尽时，内存已经释放而记账不归零，后续分配会误报耗尽；Verifier trace 的
  `trace_h_` 甚至不在析构释放列表里。
- 为什么选这条路线：只替换同一所有权链的释放入口，并在 Verifier 现有 host 指针列表加入 `trace_h_`；直接
  `cudaHostAlloc` 的 profiler 缓冲不属于 guarded allocator，继续走原生释放，语义保持清楚。
- 改动与实验：MTP 的 coupled/load mapped staging、DFlash2 的 metadata/output staging、Verifier 的窗口、batch、
  hand-off、trace 和 tap mapped staging 全部走 guarded free；buffer 大小、内容、初始化和成功路径不变。三个文件均通过
  C++20 主机侧严格语法检查（仅跳过仓库既有未使用/缩进告警）；普通及 ASan/UBSan mapped ownership 测试通过。
- 判决：采纳。mapped-pinned 的分配与释放重新对称，Verifier trace 不再泄漏 host 映射。
- 生产状态与回滚：不改 API、布局、数值或 CUDA 分配 flags；只改释放路径和补漏，单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动或 RTX 2080 Ti，未用真实 pin 耗尽触发整条 Verifier/MTP/DFlash2 生命周期，也未
  测量预算计数在长期多槽切换后的最终值。
- 下一步与理由：修复 `tools/test_gguf_paths.py` 对已不存在 `_paths._install_dirs` 的 mock，使该可运行测试恢复绿色。

## I-20261027-107 · Coupled draft sampler 初始化事务化

- 目标 / 指标：MTP 的 coupled draft sampler 参数、penalty ring、scratch、mapped staging、可选 q 分布缓冲和 token map
  只能在全部分配、清零、上传与 device sync 成功后发布；任何失败返回前必须释放本轮已取得资源。
- 假设与预期：旧实现按顺序直接写 `cparams_`、`cring_`、`cscratch_`、mapped 指针和 `dinv_`。中途一次
  allocation/alias/upload/memset/sync 失败会留下部分成员，调用者即使继续运行并重试也可能覆盖旧句柄。
- 为什么选这条路线：所有候选资源先持有在局部变量，统一 discard 负责释放；成功末尾一次性复制到成员并计入 VRAM，
  再标记 `coupled_ok_`。失败时 `coupled_ok_` 保持关闭，后续请求不会进入半初始化 sampler。
- 改动与实验：采样参数内容、ring 初值、history 初值、token-map 语义、buffer 大小和成功路径 VRAM 统计不变；
  `mtp.cpp` 通过 C++20 主机侧严格语法检查（仅跳过仓库既有未使用/缩进告警），`mtp_logits_width_test` 通过
  116 checks。
- 判决：采纳。coupled sampler 不再发布半初始化状态。
- 生产状态与回滚：只影响 opt-in `STRATA_SPEC_COUPLED` / `STRATA_SPEC_PROB` 的失败路径；成功路径和 draft 数值不变，
  单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动或 RTX 2080 Ti，未真实注入每类 allocation、alias、memset、upload 和 sync 失败；
  结论来自严格静态审查与主机侧编译检查。
- 下一步与理由：核对 mapped-pinned 分配与释放是否全部经过同一 pin 预算记账，避免分配计入而释放漏记。

## I-20261027-106 · MTP load 失败整体回滚

- 目标 / 指标：`MtpDrafter::load()` 无论在哪一步失败，返回前都必须释放本次取得的 device、host、mapped、stream、
  event 与 K/V 状态，不依赖调用者随后恰好销毁对象；同一对象不能重复加载并覆盖旧资源。
- 假设与预期：旧实现把 dense、experts、arena、pinned staging 和 streams 直接发布到成员，后续文件校验、状态初始化、
  buffer 初始化或 stream 创建失败时只返回 `false`，资源会保留到析构。若调用者保留失败对象，显存和 pin 预算会被
  长期占住；重复调用还会覆盖旧指针。
- 为什么选这条路线：把析构清理抽成幂等 `release()`，先同步并释放 graph、CUDA/host 分配、stream/event 和 K/V 状态，
  再清空 arena 内的所有别名并恢复未加载标量；`load()` 用作用域 rollback 在失败时立即调用，成功末尾才 commit。
  处理顺序保持 K/V host 所有权、共享权重所有权和 `OnDevice` 设备上下文不变。
- 改动与实验：`load()` 拒绝已加载对象，读取当前 device 失败时给出明确错误；成功路径的分配大小、上传内容和对象布局
  不变。`mtp.cpp` 通过 C++20 主机侧严格语法检查（仅跳过仓库既有未使用/缩进告警）；新增 resource 工具的普通及
  ASan/UBSan 故障测试继续通过。
- 判决：采纳。MTP 加载失败不再向调用者留下半加载资源。
- 生产状态与回滚：不改模型格式、draft 数值、共享权重语义或 VRAM 预算；只改失败清理和重复加载保护，单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动或 RTX 2080 Ti，未真实注入 dense/experts/state/buffer/stream 创建失败，也未测量
  失败后显存与 pin 预算的实际归零；当前结论来自严格静态审查和主机侧编译检查。
- 下一步与理由：检查 `setup_coupled()` 的多块分配，它仍在部分成功后直接发布成员，失败会留下到对象析构为止的资源。

## I-20261027-105 · Verifier 初始化检查必需 memset

- 目标 / 指标：Verifier 的主 device arena 与 overlapped hand-off counter 是正确运行所需资源，`cudaMemset` 失败必须
  终止 init。route-stats 与 profiler 是诊断资源，分配或清零失败必须释放并关闭，不能保留未初始化 device buffer。
- 假设与预期：旧实现忽略 arena/hand-off memset 返回值，GPU 上未清零的重用状态可能污染首窗口；route-stats 在
  allocation 成功后不检查 memset，profiler 也只检查 allocation，导致 `rs_on_/prof_on_` 与可读数据不一致。
- 为什么选这条路线：必需 arena 和 counter 在 memset 失败时返回具体 init 错误，由现有对象析构释放已分配资源；诊断
  buffer 使用 allocation+memset 联合成功条件，失败分支释放指针、清空状态并清除驱动错误，其他推理路径继续。成功
  路径的 arena 布局、counter 内容、诊断开关和采样不变。
- 改动与实验：`verify.cpp` 通过 C++20 主机侧严格语法检查（仅跳过仓库既有未使用/缩进告警）；
  `python3 -m pytest tests/terminal -q` 通过（200 passed、46 skipped、93 subtests passed）。
- 判决：采纳。必需 buffer 不再带着未知初值进入首窗口，可选诊断资源也只在完整初始化后启用。
- 生产状态与回滚：未改 buffer 大小、graph、窗口顺序或推理数值；正常初始化行为不变；单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动和 RTX 2080 Ti，未真实注入 `cudaMemset` 失败，也未验证 route-stats/profiler
  在驱动错误后的降级日志。此阶段为严格静态审查与全量 Terminal/Control 回归。
- 下一步与理由：运行跨工具 Python 测试与所有可执行主机故障测试，整理最终未验证风险与远端提交范围。

## I-20261027-104 · DFlash2 load 失败整体回滚

- 目标 / 指标：DFlash2 `load()` 从 pack 解析、blob 上传、streams/events、profiler 到 private buffer 分配，任一步失败都
  必须回收本次已取得资源并恢复为空闲实例；重复加载必须拒绝，不能覆盖已有 blob/stream。
- 假设与预期：旧 `load()` 在解析早期就写 `device_`，之后分配 blob、streams、events、profiler 和 buffers；每个
  early return 只返回错误，资源保留到外层析构。若对象仍存活或再次 load，后续分配会覆盖旧句柄。
- 为什么选这条路线：进入 load 前拒绝已加载实例；取得 `device_` 后立即建立 load scope rollback。所有失败路径由
  析构调用已有幂等 `release()`，它会同步并释放 graph、device/mapped buffers、shared owners、streams/events 和
  profiler，最后设 `device_=-1`。最后一个成功点显式 commit，正常路径不触发回滚；返回错误字符串由调用者保留。
- 改动与实验：成功路径的 pack 内容、tensor 上传顺序、stream priority、event flags、profiler/timing 与 private buffer
  状态不变。`dflash2.cpp` 通过 C++20 主机侧严格语法检查（仅跳过仓库既有未使用/缩进告警）；
  `python3 -m pytest tests/terminal -q` 通过（200 passed、46 skipped、93 subtests passed）。
- 判决：采纳。DFlash2 的 load 失败边界现在也是资源所有权边界，重复加载不会覆盖已有对象。
- 生产状态与回滚：未改 pack 格式、推理数据和 graph；仅增加失败回滚与重复加载拒绝；单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动、DFlash2 pack 或 RTX 2080 Ti，未真实注入 pack 截断、stream/event 创建失败、
  alloc 失败或 profiler 异常。没有 `Dflash2Drafter::load` 的 link-wrap 回归；本阶段是严格静态审查和全量 Python 回归。
- 下一步与理由：对 MTP load 采用同类整体回滚，再进行最终资源扫描、全套可运行测试和远端 SHA 核验。

## I-20261027-103 · DFlash2 private buffers 事务分配

- 目标 / 指标：DFlash2 的三十余块 private device buffer、每层 K/V ring、两块 mapped staging/output 必须整体成功
  后才发布；中途 `cudaMalloc`、device alias、memset 或最终同步失败时，本次已取得的所有资源必须释放并把成员清空。
  `vram_` 只计算最终保留的 private buffer。
- 假设与预期：旧 `alloc()` 使用长 `&&` 链，并把每个成功 allocation 立即写入成员；中途失败会留下前半个 drafter，
  依赖调用方随后 release。mapped meta 成功后 output 失败也会提前发布 meta；memset 与最终 synchronize 返回值未检查，
  可能把未初始化 buffer 报告为可用。
- 为什么选这条路线：先构造 `(成员指针, 字节数)` 请求表并预留本地结果数组，再逐块 `cudaMalloc` 到局部；mapped
  input/output 也先在局部完成。全部成功后才写成员并增加 `vram_`。memset 组和最终 synchronize 失败时，统一释放
  请求表中的 device 块、两块 mapped host，并清空对应成员/`vram_`。成功路径的 buffer 大小、顺序、清零范围和持久
  member 地址不变。
- 改动与实验：`dflash2.cpp` 通过 C++20 主机侧严格语法检查（仅跳过仓库既有未使用/缩进告警）；
  `python3 -m pytest tests/terminal -q` 通过（200 passed、46 skipped、93 subtests passed）。
- 判决：采纳。DFlash2 private buffer 不再以半分配状态发布，初始化失败能自行回滚。
- 生产状态与回滚：未改 pack/head 权重、ring 大小、缓存布局、graph 或 draft 数值；只改分配发布和失败清理；单提交
  可回退。
- 未验证：当前无 CUDA 工具链、驱动、DFlash2 pack 或 RTX 2080 Ti，未真实注入中途 `cudaMalloc`、alias、memset 或
  synchronize 失败。主机侧没有 DFlash2 `alloc()` 故障测试；本阶段是严格静态审查加全量 Terminal/Control 回归。
- 下一步与理由：检查 DFlash2 pack load 的 blob/tensor 上传失败回滚，然后运行最终跨模块验证并审计残余风险。

## I-20261027-102 · MTP bulk 加载检查上传完成

- 目标 / 指标：MTP 的 Q4 dense、dense、experts、K/V zero、arena、identity table 和 coupled sampling 初始化必须
  检查 H2D、memset 与 synchronize 结果。临时 device buffer 在复制成功前不能作为成员发布；失败要释放本轮已取得的
  buffer，并阻止加载报告成功。
- 假设与预期：旧实现忽略 dense/expert/Q4 dense 的上传、K/V zero 后同步、arena memset、identity 上传和 coupled
  ring/token-map 拷贝结果。驱动或显存错误可能让权重内容不完整，但 `load()`/`bind()` 仍返回 true；专家读取中途
  截断时，已分配的大块 experts 也只取决于后续对象析构。
- 为什么选这条路线：Q4 dense/offsets、dense 和 experts 先用局部 device buffer，复制成功后才写成员；experts
  使用作用域 owner 并在完整读取后移交。所有关键 CUDA 返回值现在转换成具体加载错误。K/V zero 同步失败、arena
  memset 失败、identity 上传失败和 coupled ring/map 失败都会停止初始化。成功路径的权重字节、偏移、布局、图表和
  推理顺序不变。
- 改动与实验：`mtp.cpp` 通过 C++20 主机侧严格语法检查（仅跳过仓库既有未使用/缩进告警）；
  `python3 -m pytest tests/terminal -q` 通过（200 passed、46 skipped、93 subtests passed）。
- 判决：采纳。MTP 的 bulk 数据加载现在以复制/初始化完成为成功边界，失败不会静默使用不完整权重。
- 生产状态与回滚：未改量化格式、tensor 顺序、expert 布局、dense/Q4 数值、K/V 模式或采样参数；正常成功路径行为
  不变；单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动、MTP pack 或 RTX 2080 Ti，未真实注入 dense/expert/Q4 H2D、K/V sync、arena
  memset 或 coupled setup 故障。主机侧没有 MTP pack 级测试；本阶段是生产控制流静态审查加全量 Terminal/Control
  回归。
- 下一步与理由：继续检查 DFlash2 bulk upload/alloc 的失败所有权与同步，再做一次全仓资源模式扫描，准备最终验证。

## I-20261027-101 · DFlash2 bind 只发布完整权重

- 目标 / 指标：DFlash2 的 draft vocabulary、gathered draft-head rows、`bind_raw` 元数据和 logits 必须全部成功后
  才向 drafter 发布 owner。vocab H2D、gather 后的 device synchronize 或 `bind_raw` 失败时，本轮临时 vocab/head-row
  device buffer 必须自动释放；不能把未上传完成或仅部分有效的 head 报告为可用。
- 假设与预期：旧 `bind()` 在 gather 前就发布 `dhead_owner_/dhead_`，随后忽略 vocab 上传与 device synchronize
  的结果；`bind_raw()` 也在 logits 分配结果尚不确定时提前发布 dvocab，并忽略其 H2D 结果。同对象重试或失败回退会
  留下成员，后续 owner 替换时存在泄漏风险。
- 为什么选这条路线：draft vocab 使用明确临时 owner，gather 目标使用本地 `CudaPtr`；vocab H2D 和 gather 同步都
  检查返回值。只有 `bind_raw` 成功后，head owner 才 move 到成员，`vram_` 才增加。`bind_raw` 的 dvocab 同样先由
  局部 `shared_ptr` 持有，在 top-k 限制和 logits 分配通过后再发布。
- 改动与实验：成功路径的 vocab 顺序、head rows、gather kernel、logits 宽度、共享权重分支和 VRAM 计数不变。
  `dflash2.cpp` 通过 C++20 主机侧严格语法检查（仅跳过仓库既有未使用/缩进告警）；
  `python3 -m pytest tests/terminal -q` 通过（200 passed、46 skipped、93 subtests passed）。
- 判决：采纳。DFlash2 bind 的可见状态现在以完整上传和 logits 准备完成为边界，失败不会留下未完成的权重 owner。
- 生产状态与回滚：未改 token mapping、gather 数值、head 类型、graph 或 draft 输出；正常绑定顺序不变；单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动、DFlash2 pack 或 RTX 2080 Ti，未真实注入 vocab H2D、gather synchronize 或
  logits 分配失败。没有 `Dflash2Drafter::bind` 的主机侧故障测试；本阶段为生产控制流静态审查与全量回归。
- 下一步与理由：继续收口 MTP/DFlash2 的 bulk weight upload 与 sync 检查，并检查多卡 hand-off 初始化失败后的资源回收。

## I-20261027-100 · MTP draft head 绑定失败可回滚

- 目标 / 指标：非共享 MTP draft head 的 vocab 缓冲、head-row 缓冲、vocab H2D、gather 同步与跨卡 D2D 必须全部
  成功后才发布 `n_dvocab_`、`dvocab_`、`dhead_` 和 host vocab。任一步失败要释放本轮已取得的两个 device buffer，
  不能留下同一对象重试时会被覆盖的成员。
- 假设与预期：旧实现直接对 `dvocab_/dhead_` 做两次 `cudaMalloc`，第二次失败时第一块已成为成员；vocab 上传、
  gather 后同步和跨卡复制也未检查。失败后仍可能以旧宽度或部分 head 继续，或在下一次 bind 中覆盖泄漏。
- 为什么选这条路线：draft head 的全部候选资源放在局部变量；失败清理 lambda 只释放本轮两块的组合；错误路径先
  清理再构造字符串。单卡 gather 后检查 `cudaDeviceSynchronize`，跨卡路径继续使用原有临时 head-row 与 vocab
  缓冲，并在 D2D 成功后才发布。成功路径的 tensor 行、类型、vocab 顺序、显存计数和后续 Q4/Q6 处理顺序不变。
- 改动与实验：`mtp.cpp` 通过 C++20 主机侧严格语法检查（仅跳过仓库既有未使用/缩进告警）；
  `python3 -m pytest tests/terminal -q` 通过（200 passed、46 skipped、93 subtests passed）。
- 判决：采纳。draft head 绑定只在完整上传和同步后可见，失败不会发布部分权重或丢失本轮另一块分配。
- 生产状态与回滚：正常加载的 draft token、logits 宽度、head 类型、gather 结果和显存占用不变；单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动、MTP pack、双 RTX 2080 Ti 或 layer-split head，未真实注入第二次
  `cudaMalloc`、gather 或跨卡 D2D 失败。主机侧没有 `MtpDrafter::bind` 级故障测试，此阶段为严格静态审查加全量
  Terminal/Control 回归，不能替代 GPU 权重加载验证。
- 下一步与理由：继续检查 MTP 的 dense/expert/ident 上传与初始化同步返回值，并处理 DFlash2 bind 中同样的复制失败
  后仍可能发布 head 的情况。

## I-20261027-99 · Batch 初始化资源事务化

- 目标 / 指标：Verifier 的 `init_slots()` 必须先验证 slot 数、session 形状和重复初始化，再一次性发布 mapped
  commit staging、device arena、slot 指针与 sampling 状态。device arena 分配失败时，已取得的 mapped host 块必须
  释放；任何失败都不能留下可被后续调用覆盖的半初始化字段。
- 假设与预期：旧实现把 `h_commitb_/m_commitb_` 直接作为 mapped 输出，随后才 `cudaMalloc` device arena。后者失败
  会留下 host 块；重复调用会把两块已有资源覆盖，导致旧对象不可追踪。文档要求每个 batch window 最多
  `max_t` 个 slot，但入口没有验证。
- 为什么选这条路线：先拒绝已初始化对象和超过 window 容量的 slot 集；在分配前构造新的 slot/sampling 向量，避免
  GPU 资源取得后发生 vector 分配异常。mapped host 与 device arena 先写局部变量，`cudaMalloc` 失败调用 guarded
  host free；全部成功后再一次性发布。成功路径的 arena 对齐、布局、stride、采样初值和日志保持不变。
- 改动与实验：`verify.cpp` 通过 C++20 主机侧严格语法检查（仅跳过仓库既有未使用/缩进告警）；共享 mapped 分配器
  的普通/ASan/UBSan 故障测试继续通过；Terminal/Control 全量通过（200 passed、46 skipped、93 subtests passed）。
- 判决：采纳。batch 初始化的资源可见性收敛到最后一个成功点，失败不会丢弃已取得 host 块的唯一地址。
- 生产状态与回滚：未改 batch graph、commit 顺序、slot 布局或推理数值；正常单次初始化行为不变；单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动和 RTX 2080 Ti，未在真实 batch serve 中注入 device arena 分配失败，也未运行
  `init_slots` 级的 link-wrap 测试。现有 mapped 分配器测试不覆盖 Verifier 成员发布，GPU batch 行为仍是静态审查。
- 下一步与理由：继续检查 MTP/DFlash2 加载中的多块资源发布与 CUDA copy/sync 返回值，优先修复会“报告成功但数据
  未完整上传”的路径。

## I-20261027-98 · Native embedding 加载事务化

- 目标 / 指标：`NativeEmbed::load()` 只能在模型检查、payload 边界检查、mapped-pinned 分配、device alias 和内容
  复制全部成功后发布资源与几何信息。alias 失败必须释放 host 块；同一对象不能以半加载状态被重试。guarded pin
  分配必须由对应 guarded free 释放。
- 假设与预期：旧实现一开始就写 `device_`，随后提前写 `row_/bytes_`；host 分配成功后 alias 失败会留下 `host_`
  非空但 `type_` 仍无效的半状态。再次调用会覆盖该指针。改成 guarded 分配后若析构仍直接 `cudaFreeHost`，测试 pin
  预算会保留已释放地址的计数。
- 为什么选这条路线：所有候选资源先放在局部变量，成功末尾一次性发布；已有资源时拒绝重复加载。mapped 表不再先
  清零再被整表 memcpy 覆盖，共享分配器增加 `clear=false` 路径；alias 失败会自动走原有 VRAM fallback。析构使用
  `host_free_guarded()`，与分配及模拟预算保持同一路径。
- 改动与实验：成功加载后的 tensor 字节、portable/mapped flags、VRAM fallback 条件、embedding gather 指针和模型
  数值不变。`mapped_host_test` 增加跳过清零路径的检查；普通及 ASan/UBSan 通过。`native_head.cpp`、`mtp.cpp`、
  `dflash2.cpp` 通过 C++20 主机侧严格语法检查（仅跳过仓库既有未使用/缩进告警）；Terminal/Control 全量通过
  （200 passed、46 skipped、93 subtests passed）。
- 判决：采纳。native embedding 不再发布部分加载状态，host 分配类型与释放路径一致。
- 生产状态与回滚：未改 embedding 量化格式、row 字节数、gather kernel 或 VRAM fallback 内容；只改发布和释放
  顺序；单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动、实际 GGUF pack 或 RTX 2080 Ti，未真实触发大表 pin 失败、alias 失败和 VRAM
  fallback，也未测量 skippable memset 的时间收益。测试覆盖共享分配器的失败路径，不替代真实模型加载。
- 下一步与理由：继续检查 native head 与 peer/remote staging 的加载失败路径，以及多卡初始化时逐项发布句柄的顺序。

## I-20261027-97 · Mapped-pinned 分配在 alias 失败时释放

- 目标 / 指标：`cudaHostAlloc(..., Mapped)` 成功后，如果 `cudaHostGetDevicePointer` 失败，必须立即释放 host
  分配；不能把半成功结果写入调用者字段，也不能让重试覆盖并丢失前一块。Verifier、MTP 和 DFlash2 应使用同一所有权
  语义。
- 假设与预期：Verifier 与 MTP 的本地 `mapped()` 在 device alias 失败时直接返回，host 指针已经写入成员；当前调用
  通常随后终止加载，但同一对象重试时会覆盖旧指针。DFlash2 已释放该分支，却使用另一套直接 `cudaHostAlloc`
  实现，容易再次分叉。
- 为什么选这条路线：新增 `alloc_mapped_pinned()`，先在局部分配 host 并用现有 guarded pin 预算，获取 device alias
  成功后才发布两个输出。alias 失败调用 guarded host free 并保持输出不变。三个后端入口都改为该函数；Verifier/MTP
  仍只在 peer 可用时附加 portable flag。
- 改动与实验：不改变 buffer 大小、清零、mapped/portable flags、成员类型或调用顺序；只统一失败所有权。新增
  `mapped_host_test`，检查成功分配、清零与 flags，device alias 失败后的 host 释放和输出不变，以及 host 分配失败
  不触碰任何资源。普通及 ASan/UBSan 通过；`mtp.cpp`、`dflash2.cpp`、`verify.cpp` 通过 C++20 主机侧严格语法检查
  （仅跳过仓库既有未使用/缩进告警）；`python3 -m pytest tests/terminal -q` 通过（200 passed、46 skipped、
  93 subtests passed）。
- 判决：采纳。mapped host 块在 alias 失败和重试时都有确定所有者，三个调用方不再复制这一段恢复逻辑。
- 生产状态与回滚：成功路径指针、清零结果和 flags 不变，推理与 graph 内容不变；单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动和 RTX 2080 Ti，未真实触发 `cudaHostGetDevicePointer` 失败或 pin 预算耗尽；
  link-wrap 验证字节清零、flags、释放与输出发布，不替代驱动级 mapped 内存测试。
- 下一步与理由：继续检查直接调用 `cudaHostAlloc` 后获取 device alias 的其他入口，特别是 native embedding 与
  peer/remote staging，确认失败路径是否拥有已取得的 host 块。

## I-20261027-96 · Capture 异常退出恢复录制状态

- 目标 / 指标：Verifier 的普通窗口和 batch 窗口在 `record_window` 返回失败或抛出异常时，都必须结束 stream
  capture 并销毁 partial graph；batch 的 `batch_rec_`、`row_base_` 必须恢复。MTP 与 DFlash2 的未知异常也不能
  绕过 capture 收尾。重复的临时值恢复逻辑应收敛为一个可测试实现。
- 假设与预期：旧的普通窗口没有录制异常的防护；batch 路径在异常抛出时不会执行手动清零，后续调用会误以为仍在
  录制 batch，并读取不属于当前调用的 `brow_`。DFlash2 的 fork 事件错误或未知异常也可能留下错误录制状态。
- 为什么选这条路线：新增共享 `ScopedValueRestore<T>`，显式 `restore()` 后析构不再覆盖后续值，未显式恢复时由
  栈展开恢复。Verifier 两个窗口 capture 将录制包在 `std::exception` 与 unknown-exception 处理器中，再统一调用
  `cudaStreamEndCapture` 和部分 graph 清理。MTP round/step 与 DFlash2 使用同一个恢复器；DFlash2 检查 fork/join
  事件错误，并在 context 录制抛出后仍尝试把侧流汇合回 capture 主流。
- 改动与实验：正常录制顺序、节点、stream、upload 和图形内容不变；batch 成功路径在 end capture 前恢复旧标志。
  新增 `scoped_value_restore_test`，覆盖显式恢复、作用域恢复、异常展开恢复和显式恢复后的析构幂等性；普通及
  ASan/UBSan 通过。`mtp.cpp`、`dflash2.cpp`、`verify.cpp` 通过严格 C++20 主机侧语法检查（仅跳过仓库既有
  未使用/缩进告警）；`graph_capture_test` 与 Terminal/Control 全量通过（200 passed、46 skipped、
  93 subtests passed）。
- 判决：采纳。录制状态不再因失败或异常污染后续 capture，清理逻辑有单点实现和主机侧回归。
- 生产状态与回滚：成功路径行为和数值不变；异常路径更严格收尾；单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动和 RTX 2080 Ti，未真实让 kernel 录制函数抛异常，也未在驱动中注入
  event-record/wait 或 end-capture 故障。主机侧测试验证恢复器，C++ 语法检查验证生产调用接线，不替代 GPU
  capture 级测试。
- 下一步与理由：继续检查 graph 之外的初始化失败回滚，优先处理仍把多块 host/device 资源直接写入成员后再失败的入口。

## I-20261027-95 · Graph 实例化与上传事务化

- 目标 / 指标：CUDA graph 的 instantiate 与 upload/sync 必须遵循同一发布规则。失败时不能覆盖调用者已有的
  executable，不能留下已发布但未上传成功的 executable，并须销毁已经取得的 graph/exec；MTP、DFlash2 与 Verifier
  的 capture 临时录制标志在早退或异常后必须恢复。
- 假设与预期：直接调用 `cudaGraphInstantiate(&field, ...)` 时，驱动失败可能修改输出参数；upload 或同步失败后，
  调用者若仍看到非空 handle，会把未就绪 graph 当作可用。MTP 的 `coupled_rec_`/`coupled_j_`、profiler graph
  base 以及 DFlash2 的 `fork_` 只在正常路径恢复，异常会污染后续 capture。
- 为什么选这条路线：新增 `instantiate_graph_exec()` 先在局部 handle 中实例化并始终消费输入 graph，只在成功且
  handle 非空时发布；新增 `upload_graph_exec()` 在 upload/sync 失败时销毁并清空 executable。MTP/DFlash2 和
  Verifier 的普通窗口复用这两个事务器；Verifier 的显存不足 LRU 重试保留 graph 到成功，但每次调用仍只写局部
  handle，最终成功才替换目标。capture 期间的标志和 profiler base 使用作用域恢复器。
- 改动与实验：batch commit capture 改为事务实例化；batch/window capture 的 upload 错误不再被忽略；MTP 的
  recording 失败会保留 body 错误并附带 end-capture 错误，DFlash2/ MTP 的异常路径仍会结束 stream capture。
  `graph_capture_test` 增加 null graph、空 executable、upload 失败和 sync 失败故障注入，并检查旧 handle、
  graph 与 executable 的销毁次数。普通及 ASan/UBSan 构建通过；`graph_capture.cpp`、`mtp.cpp`、
  `dflash2.cpp`、`verify.cpp` 通过 C++20 主机侧严格语法检查（仅跳过仓库既有未使用/缩进告警）。
  `python3 -m pytest tests/terminal -q` 通过：200 passed、46 skipped、93 subtests passed。
- 判决：采纳。graph executable 的可见性与可执行性现在一致，失败不会留下半发布的句柄。
- 生产状态与回滚：成功路径的 graph 节点、instantiate 参数、capture 顺序、upload stream 和推理数值不变；只在
  失败路径增加销毁与错误传播；单提交可回退。
- 未验证：当前无 CUDA 工具链、CUDA 驱动和 RTX 2080 Ti，未在真实驱动上注入 `cudaGraphInstantiate`、
  `cudaGraphUpload` 或 stream synchronization 故障，也未量测 LRU 驱逐重试时的真实 VRAM 回收。主机侧 link-wrap
  测试验证控制流与所有权，不替代真实 CUDA capture/upload 状态检查。
- 下一步与理由：继续检查 Verifier batch 录制标志在异常路径的恢复，以及其他 graph-only 入口是否仍有直接写字段的
  实例化和未检查上传。

## I-20261027-94 · MTP drafter 跟随 QSA 事务所有权

- 目标 / 指标：`qsa_state_init` 改为失败不发布后，MTP 的 streaming-to-resident 回退仍须在 host K/V 初始化失败时
  启动；drafter 析构必须释放自己的 `QsaState` host 资源；为 drafter 临时切换的 KV 格式全局开关必须在任一
  早退或异常路径恢复，并且成功路径原来的恢复时点不变。
- 假设与预期：旧 MTP 用 `st_.kv_mode` 区分 streaming 失败和普通失败；事务化初始化不再写这个 out 参数，因此
  会把本应回退到全 VRAM 的失败误报为终止。`MtpDrafter` 还只释放 `state_arena_`，新增的 host staging/KV owner
  需要显式 release。KV 格式开关在后续分配失败时会留在 drafter 的临时模式。
- 为什么选这条路线：首次 QSA init 仅在计划了 ring (`ring > 0`) 时才允许重试 fully-resident；普通失败直接返回。
  `MtpDrafter` 析构在所属设备内调用 `qsa_state_release(st_)` 后再释放 arena。KV 模式使用一个仅负责早退恢复的
  guard，成功后立即按原位置恢复并关闭 guard，因此后续 load 代码看到的全局状态与旧版本一致。
- 改动与实验：成功路径的 ring 大小、回退条件、K/V 格式、state arena 布局、恢复时点和初始化顺序不变。
  `mtp.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim；仅跳过源码既有未使用/缩进告警）。
- 判决：采纳。MTP 回退与新 QSA 所有权语义一致，临时全局配置不会因早退泄漏到后续加载。
- 生产状态与回滚：未改 draft token、窗口、graph、权重或采样；单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动、MTP pack 或 RTX 2080 Ti，未真实触发 drafts 的 pinned-host 耗尽和
  fully-resident 重试，也未测量销毁后的驱动资源计数。
- 下一步与理由：继续检查同一 QsaState 事务语义的其他调用方，并收尾未验证项与最终验证报告。

## I-20261027-93 · QSA host 资源有明确所有者并随 Session 释放

- 目标 / 指标：每个 QSA 状态的 `host_step`、`host_pos` 和 KV streaming 的 pinned host 副本必须有可匹配的
  释放路径；`qsa_state_init` 的第二次 host 分配、device mapping、RoPE/page-table 上传或最终同步失败时，不能
  发布部分状态；`session_init` 中途失败也不能留下前面已初始化的状态或 `QsaState` 数组。
- 假设与预期：旧成功路径只释放 RoPE registry，`QsaState[]`、两块 pinned staging 和 streaming KV host 副本都
  依赖进程退出。KV 分配只保留 device alias，不保存可 free 的原始 host 地址；第二个 `cudaHostAlloc` 失败会丢掉
  第一块。初始化中的多个 `cudaMemcpy` 与最终 `cudaDeviceSynchronize` 也没有检查结果。
- 为什么选这条路线：`QsaState` 保存原始 KV host allocation、字节数和是否经 `cudaHostRegister` 创建；
  新增 `qsa_state_release()` 按分配类型调用 `cudaFreeHost` 或 `pin_unregister + munmap`，同时释放两块固定地址
  staging、已拥有的 RoPE registry 并更新 host-byte 计数。`qsa_state_init` 在局部 candidate 上构建，guard 负责
  失败清理，全部步骤成功后才赋值；elastic pool 也延后到最终成功才进入全局 registry。Session 初始化失败调用
  `session_release()`，正常释放路径现在释放状态数组本身。
- 改动与实验：成功路径的 arena 布局、KV 格式和大小、RoPE 内容、page table 内容、host 指针和 graph 可见地址
  不变；只增加失败检查和 teardown。新增 `qsa_state_resource_test`，覆盖 registered 与 allocated 两种 KV host
  分配、两块 staging、host-byte 计数、重复 release、借用/拥有 RoPE 两种情况。普通及 ASan/UBSan 构建通过；
  `layer.cpp` 与 `session.cpp` 通过严格的 C++20 主机侧语法检查（仅跳过既有告警）。CLI 的 graph-only 与正常
  退出路径先释放 batch borrower，再释放 stage session，最后释放主 session，保持共享 RoPE 的 donor 后释放。
- 判决：采纳。QSA 的 arena 外资源由状态显式拥有，初始化晚失败和 Session 退出都有确定释放路径。
- 生产状态与回滚：默认不启用 KV streaming 时只释放两块 staging 和状态数组；启用时释放真实 host 副本；未改
  推理数值、KV 布局、streaming 策略或 graph 内容；单提交可回退。
- 未验证：当前没有 CUDA 工具链、CUDA 驱动或 RTX 2080 Ti，未在真实 pinned 内存、`cudaHostRegister` 或 VMM
  elastic K/V 上测量释放后的资源计数，也未注入真实 `cudaMemcpy`/`cudaDeviceSynchronize` 失败。主机侧测试只
  验证释放分支和计数，不替代驱动级故障注入。
- 下一步与理由：核对生产入口是否在成功与失败退出时调用 `session_release()`，并继续清理仍只靠进程退出兜底的
  多 session/batch 资源。

## I-20261027-92 · CUDA graph capture 失败不遗留 capture 状态或半成品

- 目标 / 指标：`session_capture()` 为每层记录 pre/post 及可选的 stage graph。capture body、end-capture 或
  instantiate 任一步失败时，必须结束 stream capture、销毁 stream 和未发布 graph；完整 graph 数组及所有 executable
  只在全部成功后才交给 `SessionGraphs`。Token graph 使用同一规则。
- 假设与预期：旧 lambda 在 `block_layer_pre/post` 返回 false 时直接退出，此时 stream 仍处于 capture mode，既没有
  `cudaStreamEndCapture` 也没有销毁；捕获 late layer 失败时，当前层的 graph executable 已经写入数组，但 `gr.n`
  还没递增，随后 `session_graphs_free()` 不会覆盖这一格。数组逐个 `new` 发生异常时，前面分配的数组也会丢失。
- 为什么选这条路线：新增独立 `capture_graph_exec()`，由它创建 stream、开始 capture，并保证 body 异常或失败后仍
  结束 capture，再销毁 partial graph、stream；instantiate 先写局部句柄，成功才发布。`session_capture()` 在局部
  `SessionGraphs replacement` 中分配并记录全部 graph，当前层开始前就计入 `n`，任一层失败由作用域回滚清理；
  全部层完成后才把数组和状态复制到调用者对象。`session_capture_token()` 复用同一事务器。
- 改动与实验：成功路径的 graph 数量、层顺序、stream 顺序、instantiate 参数和 `parts_dev` 地址不变。新增
  `graph_capture_test`，覆盖成功、body 返回失败、body 抛异常、stream 创建失败、begin/end capture 失败和
  instantiate 失败；每臂检查旧 executable 不被覆盖、capture mode 已退出、stream/graph 恰释放一次。
- 判决：采纳。capture 失败有唯一清理路径，公共 `SessionGraphs` 不再暴露半构建资源。
- 生产状态与回滚：未改 graph 内容、节点顺序、launch 顺序、推理输出或数值；单提交可回退。
- 未验证：当前无 CUDA 工具链、驱动和 RTX 2080 Ti，未真实注入 `cudaStreamEndCapture` 或
  `cudaGraphInstantiate` 故障；link-wrap 验证调用顺序，不能替代驱动的 capture 状态检查。
- 下一步与理由：继续处理 `session_init()` 的成功所有权以及 QSA 初始化中途失败后的 pinned host/KV 资源。

## I-20261027-91 · Doorbell 初始化失败整体回滚

- 目标 / 指标：`doorbell_init()` 为 embedding、token ids、routing weights、sequence 和 acknowledgement flag
  建立五块 mapped-pinned 内存。任一 host 分配或 device alias 建立失败时，必须释放本轮已经取得的全部内存，
  且不能向调用者发布半完成句柄或改变已有对象。
- 假设与预期：旧实现逐字段直接写入调用者的 `Doorbell`。第一次 `cudaHostAlloc` 成功而
  `cudaHostGetDevicePointer` 失败时，host 指针尚未进入返回对象；后续字段失败时，前面已发布的四块内存也不会
  被调用者立即清理。尺寸乘法也没有独立的溢出边界。
- 为什么选这条路线：把 Doorbell 生命周期从巨大的 `layer.cpp` 拆到独立实现。初始化先校验全部尺寸并计算总字节，
  在局部 `replacement` 中逐块获取 host/device 视图；每块 mapping 失败立即释放当前 host 区，任一后续失败由
  `doorbell_free(replacement)` 统一回滚，五块全部完成后才整体赋值给 `db`。已初始化对象拒绝再次初始化，避免
  静默覆盖旧所有权。
- 改动与实验：成功路径的五个区域大小、零初始化、mapped 标志、字段顺序和返回字节数不变。新增
  `doorbell_transaction_test`，逐一让五次 host 分配和五次 device mapping 失败，检查调用者对象保持为空且每个
  已取得区域恰好释放一次；另覆盖成功发布、零初始化、旧对象保护、重复初始化和非法尺寸。
- 判决：采纳。Doorbell 初始化具备明确的事务边界，晚到失败不再泄漏未发布 pinned 内存。
- 生产状态与回滚：未改 ring 写入、poll、reset、显存布局或 graph 捕获；单提交可回退。
- 未验证：当前没有 CUDA 工具链、驱动或 RTX 2080 Ti，未让真实 `cudaHostGetDevicePointer` 在指定序号失败；
  新测试用 link-wrap 验证控制流和释放次数，不能替代真实驱动的 pinned 内存统计。
- 下一步与理由：继续检查 Session 初始化与 graph capture 的多资源早退路径，优先处理可由单个 owner 统一回滚
  且能用主机侧故障注入覆盖的代码。

## I-20261027-90 · MTP prefill 记录缓冲事务式扩容

- 目标 / 指标：`MtpDrafter` 的 prompt 输入记录缓冲在 `prepare_prefill`、`reserve_prefill` 和实际 `prefill`
  三条路径扩容时，必须对 `rows * per_row * sizeof(int32_t)` 做溢出检查；新分配失败时要保留旧容量，旧缓冲释放
  失败时也不能覆盖仍有效的指针。
- 假设与预期：原实现三处重复“先 free 旧缓冲，再 malloc 新缓冲”。新分配失败会同时丢掉旧缓冲和容量；乘法也
  在查过头文件提示的合理输入范围之外可能溢出。
- 为什么选这条路线：抽出一个 `ensure_prefill_records(rows, per_row)`：检查维度乘法、字节数、先分配 replacement，
  旧缓冲成功释放后才发布新指针。三处调用只保留同一策略，减少重复错误路径。
- 改动与实验：成功路径的容量、布局、上传起点和 graph staging 不变；不用扩容时仍直接返回。
  `mtp.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim）。
- 判决：采纳。扩容失败保持原有可用缓冲，运算前后均有明确的溢出边界。
- 生产状态与回滚：未改 prompt token、窗口、K/V、图捕获或上传顺序；单提交可回退。
- 未验证：当前没有 CUDA 工具链和 RTX 2080 Ti，未在真实显存耗尽时注入 replacement 失败；该结论来自控制流和
  主机侧编译。
- 下一步与理由：继续把重复设备缓冲扩容路径收敛到“先分配、后替换”的事务边界。

## I-20261027-89 · DFlash2 诊断 event 失败即降级并完整释放

- 目标 / 指标：`STRATA_DF2_PROFILE` 和 `STRATA_DF2_TIMING` 是可选诊断，任何 event 创建、记录、同步或计时失败
  都只能关闭该诊断，不能继续使用空句柄，也不能让进程泄漏已创建 event；析构必须在 DFlash2 所属设备执行。
- 假设与预期：profiler 的 event vector 由 `delete Prof` 直接丢弃，最后一轮未 collect 的 event 不销毁；
  `stamp()` 忽略 `cudaEventCreate` 失败并把空句柄放进 vector；timing 的两个 event 创建失败仍设置
  `timing_=true`。`release()` 又在进入所属设备前释放 profiler。
- 为什么选这条路线：`Prof` 析构统一销毁残留 event；每个 stamp 和 round 的创建/记录/同步/计时失败都永久关闭
  profiler 并清空本轮句柄，下一轮回到已捕获 graph。timing 先创建到局部句柄，两个都成功才发布；release 先进入
  所属设备、同步主 stream，再报告和销毁诊断资源。
- 改动与实验：成功的 stage 名称、event 时点、计时公式、报告频率和 graph 路径不变；失败路径只降级诊断。
  `dflash2.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim）。
- 判决：采纳。诊断失败不再留下空句柄或未销毁 event。
- 生产状态与回滚：两个开关默认关闭；关闭时无行为变化；单提交可回退。
- 未验证：当前没有 CUDA 工具链、驱动、DFlash2 权重或 RTX 2080 Ti，未在真实 profiler 中途失败时观察降级；
  该结论来自控制流与主机侧编译，未做故障注入。
- 下一步与理由：继续审查可选诊断中“失败后仍发布半完成状态”的所有权路径。

## I-20261027-88 · NativeDense scratch 随引用释放并有事务式创建

- 目标 / 指标：`STRATA_HC_Q8` 的四个 BF16 反量化 scratch 和两个跨 stream 排序 event 必须属于使用它们的
  tensor 表；任何分配/event 创建失败都要回收已取得资源，最后一个引用释放时在所属设备同步、销毁和释放。
- 假设与预期：原实现把 scratch 指针放在 `[device][class][slot]` 全局数组，并把四个 buffer 混入第一个
  `NativeDense::weights_`。该实例析构后全局 event 和 `event_set` 仍保留；同一设备上的后续实例会等待可能已
  失效的 event。多个实例的 event 状态也没有独立所有权。
- 为什么选这条路线：新增 `NativeDenseScratch`，由 `WeightRef::native_dense_scratch` 共享持有。每张权重表有
  自己的四槽 scratch；五个句柄由同一个事务式构造器创建，失败时整体回滚，析构在创建设备同步后销毁 event、
  释放 buffer。成功路径的 Q8 反量化、stream 顺序、槽编号和等待规则不变。
- 改动与实验：新增无 GPU link-wrap 回归，覆盖四个 buffer/两个 event 的成功所有权、prompt slot 的重复使用
  等待、第三个 buffer 失败、第二个 event 失败和析构设备恢复。
- 判决：采纳。scratch/event 生命周期不再由进程级数组或第一个实例的权重列表承担。
- 生产状态与回滚：`STRATA_HC_Q8=0` 时完全不走此路径；启用时只改 scratch/event 所有权和失败清理；
  单提交可回退。
- 未验证：本机没有 CUDA 工具链、驱动或 RTX 2080 Ti，未在真实多 stream、多实例表和多卡场景执行。
- 下一步与理由：严格编译和 sanitizer 验证后再处理相同形状的全局资源。

## I-20261027-87 · dense 权重加载只在完整成功后发布

- 目标 / 指标：`WeightTable::load()` 在 staging 分配、文件读取、宽度转换、设备上传或最终同步任一步失败时，
  不能留下部分 tensor 表、半更新报告或旧表被清空后的悬空状态；pinned staging 和当前文件句柄在普通返回与
  异常展开时都必须释放。
- 假设与预期：原实现先清空 `table_/report_/pack_dir_`，随后逐 tensor 直接发布。晚到失败会留下部分模型视图；
  多个显式 `fopen/fclose/cudaFreeHost` 分支也无法覆盖 `std::map/std::string` 分配异常。
- 为什么选这条路线：加载写入局部 map 和局部报告，最后一步设备同步成功后才整体移动发布；pinned staging 和
  文件改用作用域 owner。已加载的表拒绝第二次加载，避免复用同一 arena 时旧引用被新内容覆盖。成功路径的读取
  顺序、转换公式、字节布局、上传顺序和 arena 内容不变。
- 改动与实验：新增无 GPU link-wrap 回归，覆盖成功发布、上传失败、第二次 staging 分配失败和最终同步失败；
  每臂检查旧 tensor 指针/报告不变，失败 staging 全部释放。
- 判决：采纳。失败加载不再发布部分模型视图，作用域 owner 覆盖普通返回和异常展开。
- 生产状态与回滚：失败时不再产生部分表；成功字节和数值不变；单提交可回退。
- 未验证：本机无 CUDA 工具链和真实模型 pack，未在 RTX 2080 Ti 上运行完整 5 GB 加载。
- 下一步与理由：完成严格编译与故障注入，然后继续检查 NativeDense 的跨实例 scratch/event 生命周期。

## I-20261027-86 · MTP 与 commit 计时 event 归实例和所属设备所有

- 目标 / 指标：`STRATA_DRAFT_TIMING` 与 `STRATA_COMMIT_TIMING` 的 CUDA event 必须使用可计时事件，按所属设备
  创建、记录、同步和销毁；多 drafter/verifier 不能共享全局计时单例，创建中途失败也不得留下句柄。
- 假设与预期：两个诊断原来把 event 放在函数静态或匿名命名空间单例中，生命周期只到进程退出；分层运行时
  event 可能在错误设备销毁。MTP 的共享单例还会把并发实例的计数混在一起。初次抽取 owner 时又误用了
  `cudaEventDisableTiming`，会让 `cudaEventElapsedTime` 在真实 CUDA 上失败。
- 为什么选这条路线：新增 `GpuEventPairs`，一次事务式创建 N 个计时 event 对，析构在创建设备释放；
  drafter 与 verifier 各持一个实例。无效容量请求先校验、不破坏已有 owner；设备恢复失败时销毁本次新事件
  且不发布半成品。计时关闭时仍不创建 event，恢复时点、采样公式和报告频率不变。
- 改动与实验：MTP 的 16 对和 commit 的 1 对改用同一 owner；owner 测试覆盖成功/失败创建、无效请求保留
  现有资源、析构和恢复失败清理。用 CUDA runtime link-wrap 在无 GPU 环境运行；普通和 ASan/UBSan 构建均
  通过。`mtp.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim）；`verify.cpp`
  通过同一检查，仅跳过源码原有的 `raise_flag` 未使用变量告警。
- 判决：采纳。两个诊断不再共享跨实例状态，计时 event 按设备事务式创建和释放，且使用可测时事件。
- 生产状态与回滚：两个开关默认关闭，不影响推理路径；单提交可回退。
- 未验证：当前环境没有 CUDA 工具链、驱动或 RTX 2080 Ti，不能执行真实 sm_75 计时。
- 下一步与理由：批量编译当前改动，检查真实 CUDA 事件语义不能由 link-wrap 覆盖的部分，再继续审计诊断单例。

## I-20261027-85 · 会话复制的线程私有 CUDA 资源在退出时释放

- 目标 / 指标：`conversation_copy.hpp` 为每个调用线程、每张 CUDA 卡延迟创建的非阻塞复制 stream 和排序 event，
  必须在构造失败路径正确回收，并在线程退出时于所属设备同步、销毁，不能依赖整个进程退出。
- 假设与预期：原实现把两个 thread-local vector 标注为“never destroyed: process lifetime”。检查点阶段线程和
  临时图启动线程正常退出后，其每卡 stream/event 仍占用驱动资源；构造失败时也存在半发布风险。
- 为什么选这条路线：把非模板实现移到 `conversation_copy.cpp`，用线程本地 RAII 对象统一持有每张卡的
  stream/event；析构按设备切换、同步并销毁两项句柄。成功路径仍复用同一组句柄，复制顺序、同步语义和
  `order_after_queued()` 的 legacy-stream 依赖不变。
- 改动与实验：新增无 GPU 的 link-wrap 回归，记录创建、同步、排序和销毁调用；工作线程连续执行同步复制、
  queued 复制和 legacy-stream 排序，退出后验证每卡只创建一个 stream/event 且各销毁一次。同步检查使用
  C++20、`-Wall -Wextra -Werror`。
- 判决：采纳。线程私有复制资源现在归线程所有，正常退出和短生命周期 helper 都会回收。
- 生产状态与回滚：未改复制目标、K/V/checkpoint 字节、同步时点、CUDA 流属性或请求行为；单提交可回退。
- 未验证：本机没有 `nvcc`、CUDA 驱动或 RTX 2080 Ti，未在真实多卡上下文触发线程退出和驱动销毁；主机
  link-wrap 只验证所有权调用序列，不能替代设备上的资源计数。
- 下一步与理由：继续检查正常路径由进程退出兜底的所有 CUDA stream/event/graph，优先处理可按线程或对象
  明确归属的资源。

## I-20261027-84 · stage 计时 event 的失败与退出清理

- 目标 / 指标：session 分层 stage 回放和 `--stage-timing` 诊断在 event 创建中途失败时，必须回收已创建
  event；成功创建的全局 2,560 个 event 在退出时也必须在创建设备销毁。
- 假设与预期：per-layer 回放的 RAII 清理器在创建循环之后才构造，前 N 个 event 创建后第 N+1 个失败会直接
  泄漏；stage sweep 在第二个 event 失败时泄漏第一个；`StageTimer` 没有析构函数，成功路径也一直占用 event。
- 为什么选这条路线：把 per-layer 清理器移到创建循环之前；第二个 event 失败时显式销毁第一个；给
  `StageTimer` 增加设备记录、`clear()` 和析构，创建失败立即走同一清理路径。测量、记录时点和输出不变。
- 改动与实验：`session.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim）；
  `layer.cpp` 通过同一检查，仅排除源码原有的未使用变量和 misleading-indentation 告警。
- 判决：采纳。诊断 event 的构造失败和正常退出都有完整所有权闭环。
- 生产状态与回滚：诊断关闭时无行为变化；启用时只改资源回收，不改 event 对、时点或计时公式；单提交可回退。
- 未验证：当前无法让真实 `cudaEventCreate` 在指定序号失败，也未在 RTX 2080 Ti 上运行 stage timing；
  主机 shim 不能验证真实 event 所属设备。
- 下一步与理由：继续检查正常成功后仍只依赖进程退出的设备资源，以及全局诊断单例的析构顺序。

## I-20261027-83 · HC Q8 上传事务化并由设备所有者释放

- 目标 / 指标：`STRATA_HC_REQ8` 的 BF16→Q8 上传在任一文件打开、读取或 `cudaMemcpy` 失败时，不得泄漏设备
  分配，也不得让已经处理的 `WeightRef` 指向随后释放的 VRAM；成功分配在 `WeightTable` 销毁时释放。
- 假设与预期：旧实现一次 `cudaMalloc` 后仅在循环中逐张量发布指针，任何晚到失败都会同时丢失裸设备指针并留下
  指向已释放内存的引用；成功路径也没有显式所有者，依赖进程退出。
- 为什么选这条路线：预计算全部 offset，上传期间只使用局部 `shared_ptr` 设备所有者；全部成功后一次性发布
  `data/q8_scale/resident/bytes`，再把所有者交给 `WeightTable`。删除器记录分配设备并在该上下文 `cudaFree`。
- 改动与实验：新回归用两个 synthetic HC 张量覆盖成功发布、重复上传拒绝，以及第二个张量截断时的失败事务；
  `weights_hc_q8_test` 以 C++20、`-O2 -Wall -Wextra -Werror` 通过，ASan/UBSan + leak detection 通过。
  `weights.cpp` 通过严格主机侧语法编译；另给 `DeviceArena` 析构补上 `ordinal_` 设备作用域。
- 判决：采纳。HC Q8 上传具备事务式发布和明确所有权，late failure 不泄漏、不发布悬空引用。
- 生产状态与回滚：Q8 量化公式、segment 布局、上传顺序和成功字节数不变；单提交可回退。
- 未验证：未在真实 CUDA 上运行 HC pack，也未注入 `cudaMalloc`/`cudaMemcpy` 驱动失败；`DeviceArena` 属于 CUDA
  编译单元，当前环境只能静态审查其析构设备作用域。
- 下一步与理由：继续处理“裸所有者只在进程退出释放”与“失败前已发布引用”的同类模式。

## I-20261027-82 · foresight event 与 embedding 回退按设备释放

- 目标 / 指标：`ForesightSwap` 在每张 helper 卡申请的 copy event 必须在该卡上下文销毁；`NativeEmbed` 因
  pinned 分配失败而落到 VRAM 时，析构必须在分配该表的设备释放。
- 假设与预期：foresight 析构先逐卡同步、释放 arena 和 stream，随后才统一销毁 event；此时当前设备只等于最后
  一张卡，前面卡的 event 可能销毁失败。`NativeEmbed` 保存 VRAM 指针却不保存其设备，后续 teardown 可能落在
  CUDA0。
- 为什么选这条路线：把 event 销毁移入每张卡原有的 `OnDevice` 作用域，放在同步后、arena/stream 释放前；
  给 `NativeEmbed` 记录 load 时的设备，并在析构进入同一上下文。不改变 swap 队列、拷贝或 embedding 数据。
- 改动与实验：初始化部分失败留下的非空 arena/stream/event 仍由析构逐卡回收；成功路径的所有对象归属一致。
  `expert_source.cpp` 与 `native_head.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`
  （CUDA shim）。
- 判决：采纳。多卡 foresight 资源和 embedding VRAM 回退现在都能在所属设备完成 teardown。
- 生产状态与回滚：未改 swap 策略、复制线程、embedding 布局或内存大小；单提交可回退。
- 未验证：当前无多卡 CUDA 驱动，未真实构造 event 跨设备、注入 foresight 初始化失败或运行 VRAM-fallback
  embedding；shim 不校验 event/设备的真实绑定。
- 下一步与理由：继续核对全局诊断对象和一次性初始化失败时的 event/device memory 所有权。

## I-20261027-81 · CLI 放弃 DFlash2 时真正释放显存并回退

- 目标 / 指标：CLI 在 verifier 不保留 DFlash2 taps，或 round graph 捕获失败时，必须关闭 `use_df2`，
  同时释放已加载的 pack、ring、draft head 和已捕获 graph，之后才由 MTP 接管。
- 假设与预期：这两条 CLI 分支只设置 `use_df2 = false`，没有调用 `df2.release()`；serve 的同类分支已经释放。
  因此 CLI 虽改用 MTP，DFlash2 的约 1.2 GiB 仍占用到对象析构，可能让本应成功的回退缺少预分配空间。
- 为什么选这条路线：与 serve 已验证的清理顺序对齐，在关闭标志后立即释放；不改变 DFlash2 成功路径、MTP
  回退行为或任何模型输出。
- 改动与实验：两个 CLI 放弃 DFlash2 的早退分支补 `df2.release()`；静态核对所有 `use_df2 = false` 回退点都有
  对应释放。`generate.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim；仅跳过源码
  原有未使用变量告警）。
- 判决：采纳。CLI 与 serve 的资源回退语义一致，放弃 DFlash2 后不会继续占用其显存。
- 生产状态与回滚：未改成功绑定、graph 捕获、tap 顺序或 draft 算法；单提交可回退。
- 未验证：当前无 CUDA 驱动和真实 DFlash2 pack，未实际触发 graph 捕获失败或测量回退后 VRAM 增量；该结论
  来自调用路径的静态审计。
- 下一步与理由：继续对齐 CLI 与 serve 的失败清理路径，优先检查“功能已关闭但资源仍持有”的同形分支。

## I-20261027-80 · mapped 失败时立即回收未发布的 pinned 内存

- 目标 / 指标：`Dflash2Drafter::alloc()` 从 pinned host memory 建立 mapped staging/output 时，如果
  `cudaHostGetDevicePointer` 失败，刚获得的 host 指针必须立即释放；成功时两个指针才一起发布。
- 假设与预期：旧 helper 把 `cudaHostAlloc` 的输出直接写进调用者的局部指针，随后才请求设备别名。第二次
  mapping 的 host 分配成功而别名查询失败时，该局部指针尚未赋给成员，析构无法看到，形成永久 pinned 泄漏。
- 为什么选这条路线：helper 先用两个局部指针完成全部步骤，任一步失败都不修改调用者状态；失败时只释放已成功
  分配且从未发布的 host 块。成功路径得到的 host/device 指针和分配标志保持不变。
- 改动与实验：`dflash2.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim）；
  Terminal/Control 全量回归仍为 `216 passed, 46 skipped, 93 subtests passed`。
- 判决：采纳。mapped 初始化现在具有提交式所有权转移，失败不会遗留未记录的 pinned 内存。
- 生产状态与回滚：未改 mapped 标志、缓冲区大小、布局、零初始化或成功路径；单提交可回退。
- 未验证：当前环境不能要求真实 `cudaHostGetDevicePointer` 在第二次调用返回失败，也没有 CUDA 驱动和
  RTX 2080 Ti；该失败分支只经过代码审查和主机侧编译。
- 下一步与理由：继续搜索“API 直接写入局部指针、失败后未加入所有者”的同形资源路径。

## I-20261027-79 · 多卡对象在自己的 CUDA 设备上释放资源

- 目标 / 指标：层切分中分配在 CUDA1..3 的 drafter、verifier、prefill、dense、head 和 expert cache，
  必须由其所属设备上下文同步、销毁 stream/event/graph 并释放内存；调用方销毁对象时的当前设备不能影响清理。
- 假设与预期：这些对象大多把 `device_` 或 stage 设备用于运行期切换，却没有在析构和 `close()`/`release()`
  中切回该设备。主线程完成一次 stage 调用后通常已恢复 CUDA0，因此后续 stage 的 teardown 可能对错误设备执行
  CUDA destroy/free，留下句柄或让释放失败。
- 为什么选这条路线：复用现有 `OnDevice`，只在资源所有者的同步和释放作用域进入分配设备；不给每个句柄引入
  新包装，也不改变运行期调用、分配顺序、算法或数值。
- 改动与实验：`MtpDrafter`、`Verifier`、`NativeHead`、`NativeDense`、`ExpertCache` 和 `Prefill` 记录或复用
  自己的设备，并在析构、`close()` 或 `release()` 中切换；prefill 的 tap 清理也使用同一设备。
- 结果：六个实现文件通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim；仅跳过源码原有
  未使用变量告警）。`expert_cache_per_layer_test` 用模拟 CUDA 重建并运行通过，
  `expert_cache_segmented_test` 因驱动 shim 不支持 VMM 按原约定跳过，`mtp_logits_width_test` 的 116 项检查
  通过；Terminal/Control 全量回归 `216 passed, 46 skipped, 93 subtests passed`。
- 判决：采纳。多卡对象的资源生命周期与分配设备绑定，正常调用路径不变。
- 生产状态与回滚：未改 CUDA 工作、内存布局、请求行为或默认配置；单提交可回退。
- 未验证：当前没有 CUDA 工具链、驱动和 RTX 2080 Ti，未真实销毁 CUDA1..3 上的 stage、未执行阻塞运行中的
  `cudaStreamSynchronize` 或验证 VMM 路径；shim 仅验证主机侧接口和控制流。
- 下一步与理由：继续检查失败分支中“分配后尚未交给所有者”的裸设备或 pinned 指针。

## I-20261027-78 · NativeDense 发布失败不丢设备内存

- 目标 / 指标：`NativeDense::load()` 在 HC scratch 的 vector 扩容、packed Q8 注册或后续所有权转移失败时，
  不得泄漏刚分配的 `cudaMalloc` 内存，不得把即将释放的指针发布到 `WeightRef`，也不得留下指向已释放 packed
  内存的全局注册项。
- 假设与预期：旧路径在若干 `cudaMalloc` 后立即 `weights_.push_back()`；push 分配失败会丢失裸指针。packed
  路径则先注册、后记录 key 和转移所有权；注册 map 或 vector 分配失败会让引用/注册项悬空。
- 为什么选这条路线：逐项 HC 分配先用现有 `DevicePtr` 持有，成功放入 `weights_` 后才发布到 `WeightRef`；
  packed 路径先计算总数并预留全部 vector，再逐个注册并记录回滚 key，最后在容量足够的不可分配阶段统一发布。
- 改动与实验：任何异常都会注销本轮新注册的 packed 项，`pending`/`DevicePtr` 仍持有尚未发布的内存；成功路径的
  上传字节、packed 内容、引用顺序和统计值不变。另补 `catch (...)` 的注册回滚。
- 结果：`native_dense.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim）；
  `mtp_logits_width_test` 的 116 项 CPU 检查通过，`git diff --check` 通过。
- 判决：采纳。加载失败不再产生设备内存泄漏或悬空模型指针，成功路径保持原分配与计算结果。
- 生产状态与回滚：未改数值、矩阵布局、packed 注册格式或 CUDA 调用顺序；单提交可回退。
- 未验证：当前无 CUDA 工具链和 RTX 2080 Ti，未注入 vector/map 分配失败，也未加载真实 Qwen3.8-Flash-Next
  native pack；主机侧 shim 不验证真实 `cudaMalloc`/Q8 packing 行为。
- 下一步与理由：继续审查 `cudaMalloc`/CUDA event 创建后先发布指针、后加入所有权容器的路径。

## I-20261027-77 · 延迟 pin 失败不泄漏注册且唤醒装载者

- 目标 / 指标：`PinnedArena::register_slices()` 在逐层注册 page-locked 内存时，如果记录 vector 扩容、状态文本
  或工作集锁定抛异常，线程不能终止进程；装载方必须从 `ready` 等待中退出，已成功注册的范围必须保留记录供
  析构注销。
- 假设与预期：旧顺序是“先注册，再 `slice_starts.push_back()`”。若 push 分配失败，注册已生效但没有记录，
  析构不会注销；异常还会离开普通 `std::thread`，触发 `std::terminate()`，且 `ready` 永远等不到 `INT_MAX`。
- 为什么选这条路线：在注册前一次预留全部 slice 记录，消除循环中的扩容失败；作用域最终对象无条件发布
  `INT_MAX`；线程入口复用 `worker_guard`，把任何剩余异常限制在可选 pin 优化内。
- 改动与实验：成功 slice 仍按原顺序注册并逐项发布；部分注册后失败仍由析构按 `slice_starts` 注销。装载数据、
  页锁定成功路径、工作集锁定和 GPU 拷贝顺序不变。
- 结果：`expert_source.cpp` 与 `pinned.cu` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA
  shim）。`worker_guard_test` 与 `progress_wait_test` 以 10 秒超时重新运行通过。
- 判决：采纳。pin 优化失败只降低后续 DMA 能力，不再挂死装载，也不会遗留匿名注册。
- 生产状态与回滚：未改页锁定请求范围、部分 pin 选择、数据读取或模型结果；单提交可回退。
- 未验证：当前无 `nvcc`/GPU，未真实注入 vector 分配失败，也未验证 33 GiB 双卡 arena 在 RTX 2080 Ti 上的
  `cudaHostRegister` 失败与注销。
- 下一步与理由：继续检查“注册/插入资源后才记录所有权”的顺序，优先找异常时可回收的同类路径。

## I-20261027-76 · prefill 与窗口辅助线程失败不再挂死

- 目标 / 指标：可选 prefill 专家 issuer 和 verifier 下一窗口预启动线程发生任意异常时，进程不能触发
  `std::terminate()`；消费者必须从无限等待中醒来，返回可诊断错误，并由现有 RAII 路径 join 线程。
- 假设与预期：两个线程此前只保护构造，线程体中的命名/亲和性设置、源等待、预启动和错误字符串分配仍可能
  抛异常。issuer 抛异常后 `a_issued` 永不推进，专家消费者会永久自旋；verifier helper 抛异常则会直接结束进程。
- 为什么选这条路线：新增 `worker_guard` 在线程体边界内捕获全部异常并原子发布失败；新增 `progress_wait`
  同时观察计数与失败位。成功路径仍按原顺序发布计数、拷贝和预启动，停止时仍由既有 RAII 对象 join。
- 改动与实验：prefill issuer 的每个完成项仍以 release 发布；等待方看到失败即返回
  `"prefill: the expert issuer stopped"`。verifier 预启动失败保持 `ok=false`，join 后转换为明确错误；线程启动
  失败仍按原路径在调用线程同步预启动。新增成功/失败等待与线程异常回归。
- 结果：`progress_wait_test`、`worker_guard_test` 以 C++20、`-O2 -Wall -Wextra -Werror` 和 10 秒超时运行
  通过；ASan/UBSan、ThreadSanitizer 均通过。`prefill.cpp` 与 `verify.cpp` 通过主机侧
  `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim；仅跳过两处源码原有未使用变量告警）。
- 判决：采纳。并发优化内部失败退化为受控请求错误，不终止进程，也不让消费者永久等待。
- 生产状态与回滚：未改 GPU 指令、拷贝顺序、窗口数值、预启动成功路径或线程数；单提交可回退。
- 未验证：当前无 CUDA 工具链，未运行真实 issuer 异常注入、双卡窗口重叠、RTX 2080 Ti 或
  Qwen3.8-Flash-Next 请求；CUDA shim 不能替代 sm_75 链接和 GPU 执行。
- 下一步与理由：继续检查其余普通 worker 的线程体异常、失败可见性和 join 路径。

## I-20261027-75 · PLE 统计读取不再与 worker 竞争

- 目标 / 指标：线程模式下的 `PleReader` 允许 I/O worker 更新读延迟、读量和 keep-alive 计数；任何公开统计读取
  都必须返回一致副本，不能向调用方暴露 worker 正在写入的内部引用。
- 假设与预期：`stats()` 返回 `const ReaderStats&`，而 `snapshot()` 已在 worker 模式下持锁复制。前者会把一个
  没有锁的引用交给调用方；如果 keep-alive 或读完成同时发生，调用方与 worker 会并发读写普通字段和
  `read_us`，构成数据竞争。
- 为什么选这条路线：保留已有入口名称和字段语义，把返回值改为 `snapshot()` 的副本。生产读取路径、统计公式、
  worker 调度、keep-alive 周期和计数时机均不变，只消除无锁别名。
- 改动与实验：新增 `ple_reader_stats_snapshot_test`，启用 2 ms keep-alive 后持续通过旧 `stats()` 入口读取
  快照，检查计数单调且延迟环不超过 65536，同时确认 keep-alive 确实更新了统计。
- 结果：新测试以 C++20、`-O2 -Wall -Wextra -Werror` 编译运行通过；ASan/UBSan 与 ThreadSanitizer 均通过；
  原 `ple_reader_worker_failure_test` 也重新编译运行通过。
- 判决：采纳。统计调用方获得锁内一致副本，worker 不再与观察者共享可变引用。
- 生产状态与回滚：只改统计访问的返回值与文档，不读写用户数据；单提交可回退。
- 未验证：当前没有真实 Qwen3.8-Flash-Next PLE 表、NVMe 压力和完整 CUDA sm_75 构建；未在 RTX 2080 Ti
  双卡推理过程中采样生产统计。
- 下一步与理由：继续审查公开 getter 是否返回后台线程会修改的内部引用，并补齐同类并发回归。

## I-20261027-74 · 分片路径格式化不再有截断可能

- 目标 / 指标：`gguf_split_paths()` 按文件声明的分片数生成五位数文件名时，局部格式化缓冲区必须容纳任意
  `int` 的最坏输出；合法的 `00001-of-NNNNN` 路径解析结果保持不变。
- 假设与预期：分片编号实际最多五位，但 `snprintf("%05d")` 的编译器契约允许完整 `int` 范围；原来的
  `char num[8]` 无法容纳 10 位数字和终止符，`-Wformat-truncation` 会在 `-Werror` 构建中阻断所有包含该
  头文件的测试。
- 为什么选这条路线：路径替换位置和宽度由已验证的五位后缀固定；只把局部缓冲区扩大到 16 字节，不改变
  `%05d`、数字解析、分片顺序、文件存在检查或异常文本。
- 改动与实验：扩大 `num` 并注明五位文件名与 `int` 最坏输出两个约束；以 C++20、`-O2 -Wall -Wextra
  -Werror` 编译运行 `model_family_test` 和 `gguf_split_test`。
- 结果：两个测试均通过。`model_family_test` 覆盖 Flash-Next 元数据与布局；`gguf_split_test` 覆盖四片
  路径解析、缺失分片、重复张量、分片编号不一致、截断文件等错误路径。
- 判决：采纳。五位数命名语义不变，编译器可证明局部缓冲区不会截断。
- 生产状态与回滚：只改内联头文件的局部缓冲区；单提交可回退。
- 未验证：当前没有真实 Qwen3.8-Flash-Next 多分片 pack，尚未以 `--real` 读取生产模型；该改动不参与
  分片内容解析或 GPU 执行。
- 下一步与理由：继续批量编译其余不依赖 CUDA 的核心测试，优先处理可在主机上明确验证的资源与边界问题。

## I-20261027-73 · watchdog 线程体异常只关闭诊断

- 目标 / 指标：`STRATA_WATCHDOG_S` 与 `STRATA_SPEC_WATCH` 使用 detached 诊断线程；线程启动失败或
  `stage_text()/stall_report()` 等诊断分配异常时，只关闭对应 watchdog，正常请求循环继续。
- 假设与预期：两个调用点只捕获线程构造异常。线程体本身没有异常边界；一旦报告路径抛异常，detached
  thread 会触发 `std::terminate()`，把可选诊断升级为服务崩溃。
- 为什么选这条路线：抽出通用
  [`strata/platform/diagnostic_thread.hpp`](../include/strata/platform/diagnostic_thread.hpp)，同时封装线程构造
  和线程体异常；两个 watchdog 只传自己的循环体。真正检测到停滞时仍保留原 `stall_report`、GPU wait
  释放和 `abort()` 行为。
- 改动与实验：新增独立回归，在线程体中先发出已进入信号再抛异常，验证进程没有终止；watchdog 的检查周期、
  I/O 宽限、进度条件和停机动作均保持不变。
- 结果：`generate.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim；跳过源码原有的
  一个未使用变量告警）。新测试以 `-O2 -Wall -Wextra -Werror` 构建，普通构建重复 100 次通过；ASan/UBSan 和
  ThreadSanitizer 均通过。`git diff --check` 通过。
- 判决：采纳。可选诊断失败不再终止引擎，真正停滞的 watchdog 行为不变。
- 生产状态与回滚：未改 watchdog 参数、时限、输出内容或 abort 条件；单提交可回退。
- 未验证：无 CUDA 工具链，未运行完整 `strata generate`、真实 GPU 停滞、`stall_report()` 异常或双卡路径。
- 下一步与理由：继续将可复用线程/资源边界抽成可单测组件，并检查其余 detached 线程。

## I-20261027-72 · 路由统计评分热路径零分配

- 目标 / 指标：启用 `STRATA_LOOKAHEAD_STATS` 后，每层 `stats_score()` 不再进行 vector 分配，避免可选诊断在
  解码路径抛出内存异常；启用失败仍是可诊断的“statistics off”，成功路径统计值不变。
- 假设与预期：现有统计在每次评分时构造 `act` 和 `order_miss`。它们的最大尺寸由已经固定的 `words_` 和
  `n_expert_` 决定，可以随统计表一起分配；保留在局部会导致热路径分配，也可能留下半初始化状态。
- 为什么选这条路线：`stats_score()` 由单一 dispatch 线程调用。`enable_stats()` 在局部完成全部原子表和两块
  scratch 的分配后才一次性发布；评分只清零并清除已有缓冲，保留原 bitmask、首次出现顺序和统计公式。
- 改动与实验：新增 dispatch scratch 成员；启用统计时一并预留，之后 `stats_score()` 只做 `fill/clear`。回归覆盖
  统计分配失败后关闭、解除失败后重新启用，并运行一次真实前瞻预测后的评分路径。
- 结果：`expert_source.cpp` 与 `file_expert_source_test.cpp` 通过 C++20 主机侧
  `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim）。独立用例以 `-O2 -Wall -Wextra -Werror` 构建并重复
  20 次通过；ASan/UBSan 通过；ThreadSanitizer 通过，仅输出源码原有 fence 提示。
- 判决：采纳。统计成功路径去除了逐层分配，失败路径不改变前瞻或推理。
- 生产状态与回滚：统计未启用时无行为变化；启用时统计公式与输出不变。单提交可回退。
- 未验证：无 CUDA 工具链，未运行完整链接测试或真实 Qwen pack 的 100-window 统计输出；scratch 的单线程调用
  约束仍由既有 dispatch 架构保证。
- 下一步与理由：继续寻找热路径中可预分配但尚未预分配的可选诊断结构。

## I-20261027-71 · 路由统计分配失败只关闭可选诊断

- 目标 / 指标：`STRATA_LOOKAHEAD_STATS` 的三组原子统计数组无法分配时，不得让启动路径收到半初始化状态
  或抛出内存异常；前瞻仍可工作，统计明确关闭，之后内存恢复仍可重新启用。
- 假设与预期：`enable_stats()` 依次重置 `pmask_/pstamp_/pvote_`；第二或第三次分配失败会留下部分成员，
  且调用方在启动成功后才调用它，没有覆盖该异常的本地边界。
- 为什么选这条路线：统计不参与预测或输出。先在局部 `unique_ptr` 中完整分配和清零，再一次性转移成员；
  任何异常只保持或关闭 `stats_on_`，不会暴露部分表。几何溢出也在分配前拒绝。
- 改动与实验：增加确定性统计分配失败注入；前瞻 worker 失败回归随后验证注入后 `stats_on()==false`，
  解除注入后再次启用成功。
- 结果：`expert_source.cpp` 与 `file_expert_source_test.cpp` 通过 C++20 主机侧
  `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim）。独立失败用例以 `-O2 -Wall -Wextra -Werror`
  构建并重复 20 次通过；ASan/UBSan 通过；ThreadSanitizer 通过，仅输出原有 fence 提示；`git diff --check`
  通过。
- 判决：采纳。可选统计失败不影响前瞻、解码、路由或输出。
- 生产状态与回滚：未改统计成功路径的索引、清零、评分或输出；单提交可回退。
- 未验证：当前无 CUDA 工具链，未链接运行完整测试，也未在真实 Qwen pack 上启用统计复测。
- 下一步与理由：继续审查启动阶段的可选诊断与持久线程资源，优先处理会导致半初始化状态的路径。

## I-20261027-70 · 并行辅助 worker 异常后始终 join 再传播

- 目标 / 指标：由 `run_parallel_workers()` 启动的装载、填充和 checksum 辅助线程，只要 `work` 在任一线程或
  调用线程抛出异常，所有已启动线程都必须先 join，再把首个异常交回调用方；不能因局部 `std::thread` 仍
  joinable 而触发 `std::terminate()`。
- 假设与预期：此前只限制了“线程构造失败”，随后直接在线程中运行 `work`、再在调用线程运行同一 `work`。
  后者若抛异常会跳过 join；任一线程体抛异常则直接终止进程。
- 为什么选这条路线：所有调用者共享同一个原子工作队列，单个 worker 异常退出后其余 worker 和调用线程仍能
  完成各自尚未领取的工作。统一 `guarded()` 边界在互斥量下保存首个 `exception_ptr`，无条件 join 后再
  `rethrow_exception`；成功路径线程数、抢取顺序与副作用不变。
- 改动与实验：并行 worker 与调用线程使用同一异常边界；新增确定性 work 异常注入。fill-thread 回归先确认
  异常在 join 后传回，再运行原有线程启动失败同步回退并校验 blob 字节。
- 结果：`expert_source.cpp` 与 `file_expert_source_test.cpp` 通过 C++20 主机侧
  `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim）。独立 helper 回归以 `-O2 -Wall -Wextra -Werror`
  构建并重复 50 次通过；ASan/UBSan 通过；ThreadSanitizer 通过，仅输出源码原有 fence 不受 TSan 跟踪提示。
  `git diff --check` 通过。
- 判决：采纳。辅助线程内部错误不再终止进程，调用方仍能看到真实失败。
- 生产状态与回滚：未改成功路径 job、队列、数据顺序、线程数或日志；单提交可回退。
- 未验证：当前无 CUDA 工具链，未链接运行完整 `file_expert_source_test`，也未在真实装载、NVMe、双卡或
  RTX 2080 Ti 上注入异常。
- 下一步与理由：继续清理其余“线程体/线程组异常后跳过 join”的模式，并检查可选路径是否应吞掉错误。

## I-20261027-69 · I/O 预取排队失败退化为同步读取

- 目标 / 指标：`FileExpertSource` 的可选 I/O 预取队列在 deque 分配失败或内部入队异常时，不得把异常抛入
  解码线程；被丢弃的 demand hint 仍须由 `blob()` 同步完成，读到相同字节。
- 假设与预期：`io_enqueue()` 在锁内直接 `emplace_back()`；虽然队列以 512 项为上限，单次节点/块分配仍可失败。
  demand hint 只是提前启动读取，真正数据路径有 `io_blob()` 的同步回退，因此不需要把失败升级为模型读取失败。
- 为什么选这条路线：在 `io_enqueue()` 内捕获全部入队异常，增加既有的 `pf_dropped` 计数并返回；成功路径仍
  保持锁、512 项淘汰、LIFO prediction 与 worker 通知不变。
- 改动与实验：新增确定性入队失败注入；`io_prefetch` 回归在注入后请求同一 expert，验证 `prefetch()` 不抛出且
  随后 `blob()` 返回文件中的完整正确数据。
- 结果：`expert_source.cpp` 与 `file_expert_source_test.cpp` 均通过 C++20 主机侧
  `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim）；`git diff --check` 通过。
- 判决：采纳。预取队列内存压力只减少提前读取，不改变推理结果或错误边界。
- 生产状态与回滚：未改读取数据、worker、队列成功路径或提前量；单提交可回退。
- 未验证：当前无 CUDA 工具链，未链接运行完整 `file_expert_source_test`，也未在真实 NVMe、Linux direct I/O、
  RTX 2080 Ti 或双卡路径上注入内存压力。
- 下一步与理由：继续检查其他由热路径调用的“可选优化”容器分配，并保持每个修复独立提交。

## I-20261027-68 · RouterLookahead worker 异常只关闭前瞻

- 目标 / 指标：可选路由前瞻线程在初始化缓冲区、预测窗口或预热队列发生任意异常时，不能触发
  `std::terminate()`；解码线程应继续，后续预测明确跳过，析构必须能立即 join 已停止线程。
- 假设与预期：线程构造失败已由 `start()` 处理，但 `run()` 内的 `logits/order/want/wconf/sm/votes` 分配和
  source 预热仍无异常边界。`busy_` 若异常退出时未复位，还会让 `set_foresight(nullptr)` 永等。
- 为什么选这条路线：前瞻只影响预取，不参与路由或输出。线程体捕获异常后在锁内清 `pending_/busy_`、标记
  `failed_` 并退出；`submit()` 在同一把锁下检查该标记，因此不会把新预测遗留给已退出的线程。
- 改动与实验：`start()` 的模型状态与线程构造统一纳入失败返回；新增异步 body 故障注入和 `failed()` 状态。
  同时拒绝空/非正 router geometry、尺寸乘法溢出和负 layer，避免预测线程越界访问。
- 结果：`expert_source.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim）。独立
  `RouterLookahead` 失败用例以 `-O2 -Wall -Wextra -Werror` 构建并重复 20 次通过；ASan/UBSan 通过；
  ThreadSanitizer 通过，唯一输出是源码原有 `atomic_thread_fence` 不受 TSan 跟踪的编译器提示。
- 判决：采纳。预测线程内部失败退化为本会话关闭前瞻，正常解码、路由和输出路径不变。
- 生产状态与回滚：成功路径的线程、预测深度、router dot、预热顺序与统计均未改；单提交可回退。
- 未验证：因无 `nvcc`/GPU，未构建完整 CUDA sm_75 引擎，也未在 RTX 2080 Ti、双卡和真实 Qwen pack
  上运行成功预测、Foresight 预测提交或性能路径。
- 下一步与理由：继续审查其余持久 worker 的异常边界与停止条件，并定期重试推送。

## I-20261027-67 · DirectFile 完成队列失败不再终止 worker

- 目标 / 指标：已由 `submit()` 接受的无缓冲读取，即使完成队列或 `std::deque` 节点分配失败，也必须恰好产生一个
  可消费 completion，不能从 worker 抛出 `std::bad_alloc` 触发进程终止；`close()` 必须唤醒永久等待者，
  `wake()` 也不能从已关闭句柄泄漏到下一次 `open()`。
- 假设与预期：`submit()` 已为排队分配失败建立错误路径，但读取完成后的 `done.push_back()` 仍位于无异常边界的
  worker 中。分配失败会跳过 `cv_done` 通知并结束进程；没有请求时，`wait(...,-1)` 也没有观察关闭状态。
- 为什么选这条路线：读已经完成，结果只差一个可分配容器槽。每个 worker 保留一个内联 completion 后备槽，
  分配失败时发布该槽并等待调用方取走；成功路径仍使用原 deque、锁和 FIFO completion 集合。关闭把 `fd=-1`
  加入等待条件并通知，已关闭句柄上的 `wake()` 直接忽略。
- 改动与实验：worker 改为携带固定 id 的内联后备槽；新增仅测试用的确定性完成分配失败注入。回归覆盖后备结果、
  数据完整性、阻塞等待被 close 唤醒、关闭后 wake 不污染 reopen，以及原队列失败、EOF、双 wake 和并发 close。
- 结果：C++20 `-O2 -Wall -Wextra -Werror` 编译和运行通过；ASan/UBSan 通过；ThreadSanitizer 通过。
  普通构建重复 50 次、TSan 构建重复 5 次均通过。`git diff --check` 通过。
- 判决：采纳。排队后的完成结果在内存压力下仍可交付，等待者与句柄生命周期有确定出口。
- 生产状态与回滚：未改 `pread`、I/O 线程数、成功完成队列顺序或请求格式；新增的固定后备存储为每句柄
  64 个 completion。未运行真实 NVMe、CUDA sm_75 或 RTX 2080 Ti。
- 下一步与理由：继续审查其余持久后台 worker 的结果发布与停机唤醒；网络恢复后批量推送本地提交。

## I-20261027-66 · 后台线程失败改动通过 Python 全量回归

- 目标 / 指标：确认本轮引擎异常边界、DirectFile/PLE 失败路径和 server 启动回滚没有破坏既有 Python 行为；
  Terminal 与 Control 的独立入口保持可测试。
- 为什么选这条路线：前 65 条迭代已分别完成定向主机侧编译或窄回归，但最近的 server 日志所有权修正尚未用全量套件
  验证；在先提交文档前跑完三个可直接执行的 Python 边界，能把“局部通过”升级为清晰的回归门。
- 改动与实验：未改代码。运行 `python -m pytest -q serve`、`python -m pytest -q tests/terminal strata_control` 和
  `python -m compileall -q serve strata_cli strata_control tests/terminal`。
- 结果：`serve` 为 `984 passed, 8 skipped, 10797 subtests passed`；Terminal/Control 为
  `216 passed, 46 skipped, 93 subtests passed`；`compileall` 通过。
- 判决：通过。当前 Python 回归没有暴露本轮改动造成的回归。
- 生产状态与回滚：仅追加验证证据，不改运行时行为。
- 未验证：当前环境没有 `nvidia-smi`/`nvcc`，未运行 CUDA sm_75 构建、GPU 单元测试、真实双卡请求或
  Qwen3.8-Flash-Next pack；主机侧 C++ 编译结论也不能替代真实 GPU 运行。
- 下一步与理由：继续做静态资源生命周期审查并按阶段提交；网络恢复后重试推送。

## I-20261027-65 · generate 线程体异常转成受控失败

- 目标 / 指标：adaptive helper、stdin reader 和 checksum helper 的线程体即使发生分配或内部异常，也不能触发
  `std::terminate()`；自适应失败沿既有错误返回，stdin 失败发布 EOF，checksum 诊断保留“READ-BACK FAILED”状态。
- 假设与预期：此前只处理了线程构造失败。线程内 `adapt()`、队列 `push_back`、checksum buffer `resize` 仍可能抛出，
  detached thread 或普通 thread 上的未捕获异常会结束整个进程；checksum 的部分 worker 也可能留下未处理槽。
- 为什么选这条路线：三类工作都有明确降级结果。`adapt_safely()` 把异常变成 false，由现有 join 后检查结束请求；stdin
  reader 捕获后统一发布 `eof`；checksum 捕获后停止该 worker、保留共享工作队列给其他 worker/调用线程完成，并标记读回失败。
- 改动与实验：两条 generate/serve serial adaptive 作用域各新增异常边界并替换所有 helper 调用；stdin 的读/队列操作放入
  异常边界并始终在退出前发布 EOF；cache checksum per-worker 循环捕获全部异常。
- 结果：`generate.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim；跳过该配置原有的未使用变量
  告警）。`git diff --check` 通过。未运行真实 adaptive copy、stdin 队列 OOM、checksum read-back、CUDA sm_75 或 RTX 2080 Ti。
- 判决：采纳。可选自适应/诊断异常不再终止引擎，必需 stdin 异常变成可诊断的干净退出。
- 生产状态与回滚：未改成功路径的 adaptive 决策、stdin 字节、checksum 顺序或 GPU 调度；单提交可回退。
- 学到什么：线程构造与线程体是两个异常边界；detached 线程尤其必须在线程体内部捕获所有异常。
- 下一步与理由：等待完整 Python 回归并重试远端推送，之后更新状态与未验证风险。

## I-20261027-64 · 区分 spawn 失败与启动线程失败的日志所有权

- 目标 / 指标：缺失/不可执行的引擎程序失败时保留 `eng.log.closed` 供调用方确认句柄归还；narrator、echo、READY reader、
  output thread 或 slot queue 失败时继续要求 `eng.log is None`，不能在启动未完成后保留看似可用的对象。
- 假设与预期：`I-20261027-59` 为避免日志引用被过早清空，移除了所有启动失败路径上的 `self.log = None`。完整 `serve`
  回归随后在 `EngineStartRollback` 的 5 个子用例失败，说明不同失败阶段的所有权契约并不相同。
- 为什么选这条路线：只有真正进入 `popen` 的失败属于“外部可执行程序无法启动”；其后的线程/状态构造失败应由
  `_abort_start()` 完整回滚。用 `spawn_attempted` 精确区分两者，同时保留两条既有测试契约。
- 改动与实验：narrator/echo 等在 `popen` 前失败时关闭并清空日志；`popen` 本身失败时关闭但保留对象；READY reader、
  output thread 和 post-READY 构造失败继续在 `_abort_start()` 关闭并清空日志。
- 结果：`python -m pytest -q serve/test_ready_timeout.py serve/test_server.py::EngineStartRollback` →
  `8 passed, 4 subtests passed`。完整 `serve` 复跑随后仍须确认全部通过。未验证 Windows 上 `CreateProcess` 的不同错误类型；
  分支只依赖“是否调用过 `popen`”，不依赖异常子类。
- 判决：采纳。启动失败对象的可观察状态与失败阶段一致，成功路径不变。
- 生产状态与回滚：未改正常启动、请求或子进程控制；单提交可回退。
- 学到什么：资源清理契约要按事务阶段区分；把所有失败路径统一成一种对象状态会破坏另一阶段已有的回归。
- 下一步与理由：重跑完整 `serve`，随后汇总本轮 C++ 与 Python 验证并更新状态文档。

## I-20261027-63 · PLE I/O worker 异常结束全部等待 ticket

- 目标 / 指标：PLE persistent I/O worker 若在运行中抛出未预期异常，`collect()` 不能永久等待；所有已存在和新提交的 ticket
  都应看到同一 worker 错误，随后 `close()` 仍要 drain DirectFile 中已发出的读并释放缓冲区。
- 假设与预期：线程启动失败已有处理，但线程体没有异常边界。worker 若在提交/收割中展开，异常会终止进程；即使捕获，只写
  `error` 也不会唤醒等待 `pending == 0` 的 collector。
- 为什么选这条路线：worker 停止后不能安全继续服务。`fail_worker()` 在锁内记录首个错误、停止循环、取消未提交队列，并把
  所有 ticket 的 pending 清零；`issue()` 在错误已存在时返回 ready ticket。collector 沿既有错误路径失败，close 沿既有
  outstanding drain 路径回收 DirectFile 读。
- 改动与实验：worker 外层捕获全部异常并执行 `fail_worker()`；增加 `STRATA_TEST_PLE_WORKER_FAIL=1` 注入。新增无 GPU 的
  `ple_reader_worker_failure_test`，验证 worker 异常后一个 pending ticket 在 30 秒上限内返回错误而非挂起。
- 结果：新测试以 C++20 `-O2 -Wall -Wextra -Werror` 编译，实际运行输出
  `ple_reader_worker_failure: pending ticket completed with the worker error`；`git diff --check` 通过。未运行真实 NVMe 掉盘、
  线程栈耗尽或多个并发 ticket 同时受 worker 异常影响的实测。
- 判决：采纳。worker 的内部致命异常变为可诊断的 reader 错误，等待者与资源清理都有确定出口。
- 生产状态与回滚：未改成功路径 worker、DirectFile 读顺序、行缓存或 completion 数据；CMake 仅新增测试目标，单提交可回退。
- 学到什么：后台线程的异常边界必须同时更新“任务完成”条件，而不只是记录错误字符串。
- 下一步与理由：等待完整 `serve` 回归，并继续审查仍有裸线程构造的持久 worker。

## I-20261027-62 · DirectFile 排队失败可报告，wake 不再分配

- 目标 / 指标：`DirectFile::submit()` 在队列分配失败时必须返回 false 并留下可诊断错误；`wake()` 不能因 `deque` 分配失败丢失
  唤醒或抛入调用方；未排队请求不产生 completion，已排队请求仍恰好产生一个 completion。
- 假设与预期：旧 `submit()` 的 `queue.push_back` 直接抛异常，绕过返回 `err` 的 API；旧 `wake()` 把唤醒写进同一个会分配的
  deque，失败时同样没有受控错误路径。
- 为什么选这条路线：wake 只需要一个待消费计数，不需要保存 payload。改为锁内 `uint32_t wake_pending` 后，`wait()` 把它呈现为
  原有 `WAKE_TAG` completion，既不分配又保持公开类型和消费语义；`submit()` 捕获排队失败并明确返回 false。
- 改动与实验：wake 计数饱和防止回绕，关闭时清零；等待条件同时检查 completion 与待消费 wake。`submit()` 增加确定性注入
  `STRATA_TEST_DIRECT_QUEUE_FAIL`，回归验证失败报告、随后正常排队、单/双 wake、关闭重开和并发 close。
- 结果：`direct_file_async_test` 以 C++20 `-O2 -Wall -Wextra -Werror` 编译通过并运行通过。`git diff --check` 通过。未在真实
  NVMe 掉盘、进程地址空间耗尽或 64 个 I/O worker 全部受压时运行。
- 判决：采纳。wake 在内存分配失败下仍可送达；请求排队失败由调用方处理，不跨越 API 边界。
- 生产状态与回滚：未改 worker 的 pread、队列顺序、completion 数据或 close drain；单提交可回退。
- 学到什么：控制信号若只需要“发生过”，用计数即可；复用数据队列会把一个无 payload 的通知变成可失败的分配路径。
- 下一步与理由：继续检查 PLE 持久 worker 的运行时异常边界，并完成当前完整 `serve` 回归。

## I-20261027-61 · prefill 异步任务失败由调用线程完成

- 目标 / 指标：PLE 行读取、CPU expert 计算、layer-split 下一 stage 和 hand-off worker 在系统拒绝新线程时必须继续完成原工作；
  不能因可选重叠失败而丢掉 prompt chunk、让 future 失效或让异常中止模型读取。
- 假设与预期：四处 `std::async(std::launch::async)`/`std::thread` 直接启动没有构造异常边界。hand-off worker 的构造函数
  在首次启用 `STRATA_HANDOFF_PIPE` 时抛错会使整次 prefill 展开；其余 future 同样没有“线程未启动”的 ready 状态。
- 为什么选这条路线：这些任务都已有确定的同步实现，且结果只依赖不可变输入。新增局部 `async_or_inline()`：线程成功时保留原
  future，失败时在调用线程执行同一 job，并用 ready promise 返回同一 `bool`/异常；hand-off worker 启动失败时 `submit()`
  直接在调用线程运行 packaged task。成功路径的并发、数据顺序和调用方 `get()` 协议不变。
- 改动与实验：PLE 两个预读点、CPU expert share、下一 stage 启动和 hand-off worker 均接入该降级；CPU job 原线程体抽成
  可复用 lambda，不改变量化、job 顺序、输出位置或计时。`<exception>` 已显式引入。
- 结果：`prefill.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim；未启用 DFlash2，跳过该配置原有的
  未使用变量告警）。`git diff --check` 通过。未运行真实线程创建失败注入、`nvcc`、CUDA sm_75、RTX 2080 Ti、真实 PLE SSD read、
  CPU expert share 或双卡 layer split。
- 判决：采纳。线程不可用时退化到调用线程，保持 token/专家计算与 stage hand-off 完整；只失去 overlap 或增加调用线程耗时。
- 生产状态与回滚：未改成功路径线程、流/event、专家顺序、数值或 pipeline gate；单提交可回退。目标机仍需在真实 Qwen pack 与
  双卡上验证降级后的正确性和时延。
- 学到什么：异步辅助的 future 不只表示结果，还隐含“任务已经启动”；启动失败时必须构造等价的 ready/exception future，
  调用方才能保持原错误路径。
- 下一步与理由：检查 prefill 剩余的持久 worker 与 thread group 异常安全，并在网络恢复后推送当前提交。

## I-20261027-60 · generate 剩余短生命周期线程失败可诊断降级

- 目标 / 指标：profile fill 的 64 对预读、cache checksum helper、serve stdin reader 和两个 watchdog 在系统拒绝新线程时，
  不能让异常越过主流程触发 `std::terminate()`；可选诊断应降级，必需输入线程应明确结束启动并释放已有资源。
- 假设与预期：预读 `std::async(std::launch::async)` 和 checksum `emplace_back` 都位于无异常边界；部分 checksum worker
  启动后若 vector 析构，joinable 线程会直接终止进程。stdin reader 和 watchdog 的直接 `.detach()` 同样会让构造异常逃出。
- 为什么选这条路线：预读失败后可让下一批在调用线程同步读取；checksum worker 失败后由调用线程消费同一原子工作队列，并 join
  已启动者；watchdog 是可选诊断，记录后继续；stdin 是 serve 必需输入，启动失败时返回 1 让上层得到干净退出而非崩溃。
- 改动与实验：覆盖上述五处线程启动；checksum 先 `reserve(7)`，异常后调用线程完成剩余索引。成功路径的线程数、预读批次、
  checksum 顺序、watchdog 参数和 stdin 读取逻辑保持不变。
- 结果：`generate.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim；跳过该配置原有的未使用变量
  告警）。`git diff --check` 通过。未运行真实线程配额耗尽、CUDA sm_75、RTX 2080 Ti、真实 profile fill、serve stdin 或
  watchdog 注入。
- 判决：采纳。可选并发失败只降低预读/诊断/哈希并行度；必需 stdin reader 无法启动时 fail-closed，避免半启动服务器。
- 生产状态与回滚：未改正常线程体、prompt/模型字节、GPU 顺序或 watchdog 触发条件；单提交可回退。
- 学到什么：detached 线程的启动错误与线程体内的错误是两个边界；可选服务应捕获构造失败，必需服务应把失败转成受控退出。
- 下一步与理由：重跑完整 `serve` 套件确认启动失败日志修复，并继续静态审查 prefill 中剩余的 `std::async`。

## I-20261027-59 · 启动失败的日志句柄保持可观察且已关闭

- 目标 / 指标：引擎程序无法启动时，请求仍返回 503；同时关闭服务器持有的 engine log 句柄，并保留已关闭对象的可观察状态，
  满足现有启动失败回归对资源归还的检查。
- 假设与预期：`4ded23b4` 在启动异常和 `_abort_start()` 中先关闭 `self.log`，随后把它设为 `None`。资源确实已释放，但
  `serve/test_ready_timeout.py` 依赖 `eng.log.closed` 判断句柄归还，因此完整 `serve` 套件出现 4 个同源子用例失败。
- 为什么选这条路线：保留已关闭的文件对象不会占用描述符；下一次 `__init__` 会把它替换为新的日志句柄。相比改动测试或在上层
  额外保存句柄，这恢复启动失败路径原有的可观察语义，也避免对外部调用方产生新的空值行为。
- 改动与实验：启动异常、无子进程和子进程启动后失败三条清理路径均关闭日志但不再清空引用；子进程、READY reader、stdout/stdin
  和运行状态的清理保持不变。
- 结果：`python -m pytest -q serve/test_ready_timeout.py` → `3 passed, 4 subtests passed`。完整 `serve` 套件此前为
  `984 passed, 8 skipped, 10793 subtests passed`，唯一失败组即本次修复对象；完整套件将在本轮收尾重跑。
- 判决：采纳。启动失败仍为可诊断 503，日志描述符归还，且资源状态可由测试和运维代码检查。
- 生产状态与回滚：未改成功启动路径、请求响应或子进程控制；单提交可回退。
- 学到什么：释放资源与清空对象引用是两件事；若调用方以对象状态验证释放，清理代码不能无条件擦除该状态。
- 下一步与理由：继续完成 `generate.cpp` 剩余 detached/read-ahead 线程的故障路径，然后重跑综合套件。

## I-20261027-58 · spec adaptive helper 线程失败跳过本轮

- 目标 / 指标：batch/serial/pipelined spec 路径的 adaptive tier helper 被系统拒绝时，生成与 commit 必须继续；不能因一个
  可选的专家驻留调整异常中止请求。
- 假设与预期：四处 `std::thread` 直接构造在 decode 热路径内。`adapt()` 只更新驻留表和排队复制，不决定当前窗口数值；
  构造异常会越过原有 join/`adapt_ok` 检查。
- 为什么选这条路线：跳过该轮自适应比在 commit/draft 旁同步执行更保守，保持当前窗口、输出和已排队复制不动。pipeline
  维护一次性的 `pl_adapt_unavailable`，不在后续 round 重复尝试；其余路径记录一次并继续。
- 改动与实验：batch、pipeline、serve serial 和 generate serial 四处均捕获线程构造异常；失败时线程保持 nonjoinable，
  `adapt_ok/done` 保持可继续状态。成功路径线程体、join、commit、draft 和 pending 应用不变。
- 结果：`generate.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim；跳过该配置原有的未使用
  变量告警）。`git diff --check` 通过。未运行真实线程配额失败、spec decode、adaptive swap 或 RTX 2080 Ti。
- 判决：采纳。可选自适应 helper 失败只减少驻留调整频率，不影响生成正确性或请求寿命。
- 生产状态与回滚：未改成功路径和 adaptive 数据；单提交可回退。
- 学到什么：热路径中的可选线程必须明确“未启动”是允许状态，相关 join 与完成标志要按 nonjoinable 分支保持自洽。
- 下一步与理由：继续审查剩余的 detach/短生命周期线程，并等待全量 serve 回归结果。

## I-20261027-57 · 并发 prompt reader 线程失败同步读取

- 目标 / 指标：`STRATA_PIPED_CONCURRENT` 的 prompt reader 线程被拒绝时，当前 prompt 仍须通过同一 `sp.run()` 完成；
  不能遗漏 `ReadEnd` 清理，也不能让 `in_prompt_read` 或 `stop_at_chunk` 保持悬挂。
- 假设与预期：reader 的直接构造位于 `in_prompt_read=true` 和 stop hook 设置之后；线程创建异常会在 `ReadEnd` 构造前展开，
  可能绕过本轮请求状态的成对复位。
- 为什么选这条路线：读取与窗口 overlap 是调度优化，而读取本身可以同步执行。抽出同一 `read_prompt` lambda，线程创建成功时
  保持并发；失败时立即在调用线程执行同一 lambda，随后现有 `ReadEnd`、drain 和错误处理照常运行。
- 改动与实验：reader 先默认构造，再在异常边界内启动；失败记录后同步调用原 lambda。job 内容、done 发布、`sp.run()`
  参数、窗口 pump 和后续提交路径不变。
- 结果：`generate.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim；跳过该配置原有的未使用
  变量告警）。`git diff --check` 通过。未运行真实线程配额失败、并发长 prompt、双卡 stage 或 CUDA sm_75。
- 判决：采纳。并发 reader 失败退化为同步读取，prompt 不丢失，状态仍成对清理。
- 生产状态与回滚：未改成功路径线程、读取或 pipeline gate；单提交可回退。
- 学到什么：在线程启动前提交的任务状态必须能被同步兜底复用，任务体应是可调用的 lambda 而不是线程构造函数里的重复逻辑。
- 下一步与理由：处理 spec adaptive helper 的直接线程构造并继续全量 serve 回归。

## I-20261027-56 · 并发读 doorbell 线程失败交还 pump

- 目标 / 指标：`STRATA_PIPED_CONCURRENT` 的 prompt reader 把各 stage doorbell 交给独立线程服务；该线程被拒绝时，
  当前读取必须恢复 `pump()` 内的普通服务，不能留下 `conc_serving=true` 而无服务者。
- 假设与预期：`Doorbells` 先对所有 verifier 设置 `external_serve=true`，再直接构造线程。构造异常会让请求展开；即使
  RAII 析构最终复位，当前读取也会丢失并发服务。
- 为什么选这条路线：doorbell 外部服务是延迟优化；线程不可用时调用现有的 `bells.off()`，恢复各 verifier 和
  `conc_serving` 的普通路径，读取、窗口和输出保持原样。
- 改动与实验：线程构造放在异常边界内；失败记录后立即 `off()`，不进行 join，也不设置等待标志。成功路径的服务循环、
  wake/等待和每 2 ms 轮询不变。
- 结果：`generate.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA shim；跳过该配置原有的未使用
  变量告警）。`git diff --check` 通过。未运行真实线程配额失败、并发 prompt read、双卡 pump 或 CUDA sm_75。
- 判决：采纳。可选 doorbell 并发失败退化为普通 pump 服务，不遗失窗口 hand-off。
- 生产状态与回滚：未改成功路径线程和窗口调度；单提交可回退。
- 学到什么：设置全局服务所有权后启动服务者，必须把“服务者已启动”纳入状态，失败时回滚所有权。
- 下一步与理由：继续检查 prompt reader 和 spec-adaptive helper 的直接线程构造，再做下一轮综合测试。

## I-20261027-55 · checkpoint stage 线程失败串行保存

- 目标 / 指标：`STRATA_CKPT_THREADS` 启动多 stage checkpoint 线程时，任一线程构造失败都必须由请求线程补齐全部 stage；
  已启动的等待线程不能遗留为 joinable 局部状态，请求也不能因可选并行保存而中止。
- 假设与预期：`StageThreads::run()` 在发布 round 前逐个 `emplace_back`。部分成功后再失败会抛异常，既没有执行任何
  stage job，也没有阻断后续线程继续等待；`checkpoint_at` 没有捕获这一构造异常。
- 为什么选这条路线：每个 stage 的 checkpoint part 已设计为可串行保存，job 自身捕获并记录异常。线程失败时直接按 stage
  顺序在调用线程执行全部 job，数据、同步和写入同一 checkpoint 的约束不变。
- 改动与实验：启动前 reserve；构造失败记录一次并永久标记 `threading_failed_`；当前及后续 round 都走串行。已有部分 worker
  保持等待并由析构统一 stop/join；成功路径 round 发布和并行 job 不变。
- 结果：`generate.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA 调用由 shim 模拟；跳过该配置原有
  的未使用变量告警）。`git diff --check` 通过。没有真实线程配额失败、checkpoint、双卡 layer split 或长上下文验证。
- 判决：采纳。可选 checkpoint 并行失败只降低保存速度，不丢 stage、不泄漏等待线程、不结束请求。
- 生产状态与回滚：未改成功路径线程/job；单提交可回退。
- 学到什么：线程组发布状态必须晚于整组启动，启动失败后调用线程应完成整组副作用，而不是只完成一部分。
- 下一步与理由：进行本轮 Python 与 CPU 综合回归，之后只处理回归暴露的问题。

## I-20261027-54 · adaptive copy worker 失败退化请求线程

- 目标 / 指标：可选 `--adapt-async` 的复制 worker 被系统拒绝时，请求仍应完成 adaptive refill；不能因线程构造异常退出
  serve，也不能向日志虚报异步执行。
- 假设与预期：`JobThread` 构造时直接启动线程，`make_unique<JobThread>` 的异常会穿过主循环。该 worker 只承担专家换入
  复制，调用线程可以同步执行同一 job。
- 为什么选这条路线：记录失败并令 `post()` 在请求线程同步执行 job，`idle()` 随后为真。调用方现有状态机无需改写，
  复制内容、顺序和 fence 保持；只失去后台重叠。
- 改动与实验：线程创建失败后保存 `threaded_=false`，跳过无关的 quit/join；`post()` 同步运行 job 并恢复 idle。启动日志和
  batch 统计区分 async 与 request-thread fallback。成功路径仍使用原后台线程。
- 结果：`generate.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA 调用由同步 shim 模拟；跳过该配置
  原有的未使用变量告警）。`git diff --check` 通过。没有真实线程配额失败、serve 请求、RTX 2080 Ti 或双卡 adaptive swap
  验证。
- 判决：采纳。可选异步复制失败只降低重叠，不让请求丢失 adaptive refill 或终止进程。
- 生产状态与回滚：未改成功路径线程、复制 job、事件或状态机；单提交可回退。
- 学到什么：线程对象需要显式区分“已启动”和“可用”，否则构造失败后所有 `post/idle` 调用方都会建立在错误前提上。
- 下一步与理由：继续审查 checkpoint stage 线程组，再开始本轮综合 Python/CPU 回归。

## I-20261027-53 · graph launch 线程失败返回可清理错误

- 目标 / 指标：异步 verify graph launch 的 helper 线程创建失败时，constructor 不能把异常抛出所有权边界，也不能让
  `pending` 永久为真；含 CPU doorbell 的 graph 不能冒险改走同步 launch。
- 假设与预期：`HostGraphLaunch` 在线程模式先设置 `pending=true` 再构造 `std::thread`。构造失败时对象尚未完成，
  异常会穿过 `verify.cpp`，且没有受控 launch error。
- 为什么选这条路线：此 graph 可能在 `cudaGraphLaunch` 中等待调用线程敲 doorbell，constructor 返回前无法提供该服务；
  同步兜底会死锁。正确策略是 fail-closed：清除 `pending`，返回 `cudaErrorLaunchFailure`，让现有状态机报错并析构清理。
- 改动与实验：捕获线程构造异常并记录日志；增加 `STRATA_TEST_GRAPH_LAUNCH_THREAD_FAIL=1`，扩展 CUDA 测试的错误臂。
  主机 shim 构造/运行测试为 `host_graph_launch_thread_failure: PASS`，`verify.cpp` 通过 C++20 `-Wall -Wextra -Werror`
  主机侧语法检查。
- 结果：`git diff --check` 通过。未运行真实 CUDA graph、doorbell、RTX 2080 Ti 或双卡 layer split；本机没有 `nvcc`，
  `host_graph_launch_test.cu` 的新增错误臂未执行。
- 判决：采纳。无法启动 launch helper 时请求可诊断地失败，不泄漏异常、不挂起等待、不伪造成功。
- 生产状态与回滚：未改成功路径线程、graph 或 doorbell；单提交可回退。目标机必须补跑真实 graph 错误测试。
- 学到什么：对可能等待调用线程的异步 API，失败兜底不能只追求“仍然启动”，还要避免构造期间的同步死锁。
- 下一步与理由：处理可选 adaptive copy worker 的同形启动失败，再进入综合回归。

## I-20261027-52 · router look-ahead 线程失败关闭预取

- 目标 / 指标：`RouterLookahead` 的辅助线程被系统拒绝时，模型初始化必须继续，路由感知预取退回关闭；不能因一个可选
  prefetch 线程异常逃出初始化并吞掉错误。
- 假设与预期：`start()` 直接把线程构造函数放在返回 true 之前。`std::system_error` 会越过 `generate.cpp` 的“启动失败
  则打印预取关闭”分支，使主流程提前展开；该线程只预测下一层专家并预热页面，不参与数值正确性。
- 为什么选这条路线：捕获构造异常、填写现有 `err` 并返回 false，让调用方沿用既有关闭预取路径。成功路径线程函数、预测
  数据和调度不变；失败路径明确、可诊断，也不需要冒险同步预测整个请求。
- 改动与实验：增加确定性故障注入 `STRATA_TEST_LOOKAHEAD_THREAD_FAIL=1`，补充 `file_expert_source_test` 断言 `start()`
  返回 false 且错误说明线程未启动。标准构建与 ASan/UBSan 构建均为 `file_expert_source_test: PASS`。
- 结果：`expert_source.cpp` 与测试文件通过 C++20 主机侧 `-Wall -Wextra -Werror` 语法检查；`git diff --check` 通过。
  未在真实线程配额耗尽、RTX 2080 Ti、CUDA sm_75 或真实 Qwen pack 上运行。
- 判决：采纳。可选 look-ahead 失败只关闭预取，不终止模型启动。
- 生产状态与回滚：未改预测、warm/madvise、Foresight 或成功路径线程；单提交可回退。
- 学到什么：可选后台功能必须在启动 API 内捕获线程创建错误；让异常穿过主初始化会把“降级”变成“启动失败”。
- 下一步与理由：继续检查其他直接线程构造点，再转入 Python 与 CPU 综合回归。

## I-20261027-51 · verify 下一窗口线程失败回退同步预启动

- 目标 / 指标：layer-split verify 链启动下一 stage 的窗口 helper 时，如果系统拒绝新线程，当前窗口必须继续按原协议推进，
  下一窗口由调用线程同步 `prelaunch`；不能因构造异常跳过 hand-off，也不能留下无人处理的 GPU wait。
- 假设与预期：`nl.th = std::thread(...)` 直接构造；失败会从 `run()` 抛出，现有 `NextLaunch` 析构只能释放 GPU wait，
  不会执行下一窗口的预启动，因此一次临时线程配额错误会中止整条验证链。
- 为什么选这条路线：helper 只把下一窗口的 staging/launch 提前与当前窗口重叠；同步执行 `prelaunch` 保持相同 GPU 顺序、
  flag hand-off、错误传播和最终 `join`/`run` 协议，只牺牲 overlap。
- 改动与实验：捕获线程构造异常并记录一次降级信息，随后由调用线程执行同一 `prelaunch`，把结果写入现有 `nl.ok/err`。
  正常路径仍启动 helper，线程函数、窗口顺序、graph 和 GPU 同步都不变。
- 结果：`verify.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA 调用由同步 shim 模拟；
  DFlash2 未启用，跳过该配置原有的未使用变量告警）。`git diff --check` 通过。没有真实线程创建失败注入、CUDA graph、
  RTX 2080 Ti、双卡 layer split 或长上下文验证。
- 判决：采纳。辅助 overlap 失败退化为同步预启动，核心验证协议保持可用。
- 生产状态与回滚：未改成功路径线程和 graph；单提交可回退。目标机仍需验证真实线程配额耗尽与 graph wait 释放。
- 学到什么：异步辅助启动失败时，如果后续状态机依赖其副作用，应同步补做副作用，而不是只捕获并继续。
- 下一步与理由：继续审查 n-gram 与预取线程组，随后集中运行 CPU/Python 回归。

## I-20261027-50 · sm_75 prefill 恢复缺失的 gfx11 回退符号

- 目标 / 指标：专用 sm_75 CUDA 树必须能编译 `prefill.cpp`。收敛提交删除 gfx11 WMMA 源和头文件后，残留的两个调用不能
  再造成未声明符号，同时默认路径不能悄悄启用 gfx11 专用算法。
- 假设与预期：`strata_pf_padded_gemm_available()` 与 `strata_pf_hcdown_bf16()` 只由已删除的 `wmma_gemm.h` 声明；当前树
  没有 `nvcc` 时，主机编译器在 `prefill.cpp` 的 430、2978 行左右直接报未声明。
- 为什么选这条路线：目标发行版只支持 sm_75，gfx11 WMMA 产品不属于发行边界。恢复与旧 CUDA 构建相同的 `false` 回退，
  比删除整个调用方更小，也保留 `STRATA_PF_PAD` 的安全拒绝和 `STRATA_PF_HCDOWN` 的显式错误。
- 改动与实验：在非 HIP 构建中提供 `false` 内联回退；`STRATA_PF_PAD` 不填充激活行，`STRATA_PF_HCDOWN` 在请求不存在
  的产品时仍旧失败并给出原有错误。默认环境和成功请求路径不变。
- 结果：`prefill.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror`（CUDA 调用由同步 shim 模拟；DFlash2 未启用，
  跳过该配置原有的未使用变量告警）。`git diff --check` 通过。未运行 `nvcc`、CUDA sm_75、RTX 2080 Ti 或真实 prefill；
  没有验证 gfx11，因为该后端已不在发行版中。
- 判决：采纳。修复当前树中可复现的编译阻断，不扩大发行边界。
- 生产状态与回滚：默认路径、字节、模型装载和 prefill 分区不变；单提交可回退。目标机仍需用 CUDA toolkit 完成真实编译。
- 学到什么：删除后端源文件时必须同时处理宿主侧回退声明；仅删除 CMake 目标和实现不足以保持编译闭包。
- 下一步与理由：继续用主机侧编译和静态审查查找其他收敛遗留，再集中跑 Python 与 CPU 合成回归。

## I-20261027-49 · prefill 与 native dense 辅助线程失败保持装载可用

- 目标 / 指标：prefill stager 的复制 worker、expert issuer 以及 native dense 的 BF16→Q8_0 转换 helper 在系统拒绝新线程时，
  必须 join 已启动线程并由调用线程完成所需工作；不能留下 joinable 局部线程导致 `std::terminate()`，也不能让 issuer 失败跳过
  expert 拷贝。
- 假设与预期：三处均直接构造线程。stager/转换线程中途失败会让局部线程析构终止；issuer 失败会让 `threaded_issue` 保持为真，
  后续消费者等待一个从未启动的生产者。
- 为什么选这条路线：三者都是并行加速，调用线程能执行同一不可变输入/工作队列。成功路径的线程数、分区、ring 顺序和输出字节不变。
- 改动与实验：stager 先 reserve 并记录成功数量，部分成功继续、全失败明确返回；issuer 创建失败切回内联 `issue_until`；native
  Q8 helper 失败时先 join 已启动者，再由调用线程转换全部块。新增注释明确接管边界。
- 结果：`native_dense.cpp` 与 `prefill.cpp` 通过 C++20 主机侧 `-fsyntax-only -Wall -Wextra -Werror` 检查；CUDA 调用由临时
  同步 shim 模拟，且 DFlash2 未启用、跳过了该构建配置原有的未使用变量告警。`git diff --check` 通过。未运行线程创建失败注入、
  `nvcc`、CUDA toolkit、RTX 2080 Ti、真实 prefill 或模型装载。
- 判决：采纳。辅助并发失败只降低吞吐或启动速度，不损坏请求、装载字节或进程寿命。
- 生产状态与回滚：未改成功路径的分区、stream/event、expert 顺序、量化结果或缓存；单提交可回退。当前没有真实 `nvcc`、CUDA
  toolkit、RTX 2080 Ti、真实 prefill 或 native Qwen pack，静态结论仍需目标机确认。
- 学到什么：等待者与生产者之间的“已启用”标志必须在生产者真正启动后才保持为真。
- 下一步与理由：继续审查 verify 的异步下一窗口启动，随后转入综合测试与目标机未验证风险整理。

## I-20261027-48 · NUMA toucher 线程失败回退调用线程

- 目标 / 指标：complement 的两次 NUMA slice 触碰分别启动 pinned toucher；任一线程创建失败时，另一个已启动线程必须 join，
  失败 slice 由调用线程无 pin 地触碰，不能因 joinable 局部线程析构触发进程终止。
- 假设与预期：旧代码用函数局部 `std::thread a(...), b(...)`。若第二个构造失败，第一个仍 joinable，栈展开会调用
  `std::terminate()`；源码注释已明确记录这一缺口。
- 为什么选这条路线：NUMA 触碰是装载加速与放置优化，失败仍可完成相同的页写入；正常两线程路径的 core、range 顺序、pin 行为
  和调用线程补洞逻辑不变。
- 改动与实验：拆出无 pin 的 `touch_ranges()`；两个 worker 独立启动和记录状态，先 join 已启动者，再由调用线程补齐未启动 slice。
  失败信息区分具体 slice，且不改变自动 NUMA balancing 下“已触碰 slice 不重复触碰”的约束。
- 结果：`expert_source.cpp` 以 `-Wall -Wextra -Werror` 主机侧编译通过，合成 `file_expert_source_test: PASS`；
  `git diff --check` 通过。该路径需要 NUMA 拓扑与 page
  placement，当前环境未做实机 NUMA 测试，结论属于控制流静态确认。
- 判决：采纳。线程配额或资源错误只让对应 slice 改由调用线程完成，不再终止模型装载进程。
- 生产状态与回滚：未改成功路径的 slice 划分、CPU pin、madvise/mlock 或页字节；单提交可回退。未验证真实双路 NUMA、RTX 2080 Ti、
  CUDA sm_75 或真实 Qwen expert arena。
- 学到什么：相邻声明的多个 `std::thread` 并不构成异常安全线程组；每个线程都必须单独记录、join 并准备调用线程接管。
- 下一步与理由：继续处理 prefill/native 路径的裸局部线程组，并在结束前重跑 CPU 合成与 Python 综合套件。

## I-20261027-47 · 托管启动回滚清理子进程 ready 文件

- 目标 / 指标：托管服务器进程启动后，如果所有权记录或 owner marker 发布失败，abort 路径必须 kill/reap 子进程、关闭 owner pipe，
  并删除子进程已经发布的 ready 文件；失败实例不能留下可被客户端误认的持久化端点。
- 假设与预期：旧 `_launch()` 只在记录发布成功后才设置 `self.ready_path`。子进程可能在记录写入窗口内创建 ready 文件；随后
  `_abort_launch()` 虽结束进程，却因 `self.ready_path is None` 永远不删除该文件。
- 为什么选这条路线：这是托管启动的单一事务边界，现有 fake child 测试可直接让 Popen 写出 ready 并令记录写入失败；正常成功
  路径的 ready 路径、owner marker、attestation 和状态字段不变。
- 改动与实验：在 Popen 前登记本次 ready 路径，使后续所有失败都有清理 owner；`_abort_launch()` 在关闭 pipe 后显式清除 ready。
  扩展记录失败测试，断言 child 被 reap、pipe/ready 状态清空且 runtime 目录无 `*.ready.json`。
- 结果：`tests/terminal/test_runtime.py` 为 `51 passed / 11 subtests`；相关文件 `py_compile` 与 `git diff --check` 通过。
- 判决：采纳。所有权发布失败不会留下子进程或已发布 ready 文件。
- 生产状态与回滚：未改成功启动、ready 内容、server 参数、owner marker 或 probe；单提交可回退。未在真实 server/Python 启停竞争、
  RTX 2080 Ti 或 CUDA sm_75 上运行。
- 学到什么：子进程能先于父进程所有权发布创建资源；父进程必须在启动前登记这些资源的清理路径，而不是成功后再登记。
- 下一步与理由：继续审查托管 runtime finally 的独立清理，并在结束前跑完整 terminal 与服务端回归。

## I-20261027-46 · owner-loss guard 线程失败关闭继承描述符

- 目标 / 指标：托管服务器启动 owner-loss guard 时，如果系统拒绝新线程，必须关闭传入的继承 pipe 描述符，并以正常的启动
  拒绝错误返回；不能留下无所有者描述符或让 `RuntimeError` 变成未处理 traceback。
- 假设与预期：`owner_guard.start(fd)` 直接调用 `Thread.start()`。失败时 main 只捕获 `OSError/ValueError`，因此既不关 fd，
  也不会走统一的参数错误路径。
- 为什么选这条路线：guard 是托管服务器父进程消失后的安全边界，不能降级缺失；正确行为是 fail-closed。pid/pgrp 和 FIFO 检查保留，
  只增加线程启动失败的显式清理与错误归一化。
- 改动与实验：任何线程启动异常都先关闭 fd；`RuntimeError` 转成带原因的 `ValueError`，其他异常原样抛出。用真实 pipe 和 mock
  pid/pgrp、线程启动失败断言 fd 已关闭且错误可用于启动拒绝。
- 结果：`serve/test_lifeline.py` 为 `8 passed`；相关文件 `py_compile` 与 `git diff --check` 通过。
- 判决：采纳。guard 无法启动时服务器明确拒绝启动，并释放继承描述符。
- 生产状态与回滚：未改 guard 正常读取、SIGTERM/SIGKILL 升级、grace 或进程组要求；单提交可回退。未在真实线程配额耗尽或
  SIGKILL 托管主机上运行。
- 学到什么：核心安全监视器失败可以 fail-closed，但失败路径仍必须释放调用方刚刚移交的资源。
- 下一步与理由：继续审查构造函数和启动事务中的描述符/临时目录所有者，再做服务生命周期综合回归。

## I-20261027-45 · 视觉编码器构造失败清理临时工作目录

- 目标 / 指标：`Vision.__init__` 创建私有 scratch 目录后，只要配置转换或 encoder 启动失败，构造过程必须删除该目录和其中
  已生成的缓存/输入文件；失败对象不会返回给调用方，不能依赖之后的 `shutdown()`。
- 假设与预期：旧实现先 `mkdtemp`，再处理 elastic 数值并调用 `_start()`。错误配置的 `int/float` 转换或 encoder 启动异常会直接
  离开构造函数，私有目录没有所有者，服务器反复失败启动会留下空目录或半成品。
- 为什么选这条路线：目录生命周期完全由 `Vision` 所有，且正常启动、unload 保留缓存、显式 shutdown 删除目录的规则不变；可用
  mock `_start` 失败和一个确定路径快速覆盖。
- 改动与实验：把构造函数中目录创建后的初始化整体纳入异常边界，任何 `BaseException` 都以 `ignore_errors` 删除本次 scratch
  目录后原样抛出。新增构造失败后目录不存在的断言。
- 结果：`VisionShutdown` 为 `6 passed / 2 subtests`；相关文件 `py_compile` 与 `git diff --check` 通过。
- 判决：采纳。未发布对象的临时目录在构造失败时立即回收。
- 生产状态与回滚：未改成功构造、缓存复用、unload、shutdown 或 encoder 参数；单提交可回退。未运行真实视觉模型或 CUDA 编码器。
- 学到什么：构造函数获取的资源不能被构造失败的对象拥有；清理责任必须落在构造边界内。
- 下一步与理由：继续审查其余临时目录和进程所有者在构造/启动失败时的回收，然后做服务端综合回归。

## I-20261027-44 · expert 区间加载线程失败回退调用线程

- 目标 / 指标：`load_experts_ranges()` 启动额外 reader 失败时，调用线程必须继续消费共享 layer 队列，join 已启动线程，读取全部
  字节且内容不变；不能让局部 `std::thread` 析构触发 `std::terminate()`。
- 假设与预期：该函数是 arena expert 和 GGUF expert 装载共用入口。旧实现直接循环 `pool.emplace_back(worker)`，线程创建在中途
  失败后，已成功线程仍 joinable，栈展开时进程终止。
- 为什么选这条路线：读取器只增加吞吐，不改变字段布局；共享 atomic layer 队列允许调用线程无缝接管。使用小文件、短 chunk 和
  合成环境变量可精确注入首个 helper 启动失败，不依赖 GPU。
- 改动与实验：先 reserve，再启动 helper；捕获线程启动异常后记录诊断，由调用线程执行同一 worker，并 join 已启动线程。新增
  三区间合成文件在 4 个请求 reader、首个 helper 失败时仍完整读出预期字节的断言。
- 结果：主机侧 `file_expert_source_test` 通过；`-Wall -Wextra -Werror`、ASan/UBSan 与 `git diff --check` 通过。
- 判决：采纳。线程配额耗尽只降低装载并行度，不会因 joinable thread 析构终止进程或损坏加载结果。
- 生产状态与回滚：未改成功路径的线程数、chunk、seek、hash、pad 映射或错误文本；单提交可回退。同步 CUDA shim 只验证主机侧
  控制流，未验证真实 `nvcc`、CUDA sm_75、双 RTX 2080 Ti 数据路径或真实专家 pack。
- 学到什么：局部线程池必须先 reserve 并把启动失败纳入同一接管/join 边界；正常读写逻辑应继续复用同一个 worker。
- 下一步与理由：继续排查真实模型装载路径中剩余的裸局部线程组，再做一次 CPU 合成回归。

## I-20261027-43 · HTTP 循环线程失败时关闭已绑定端口

- 目标 / 指标：`serve()` 先构造并绑定 `ThreadingHTTPServer`，再启动 `serve_forever` 线程；线程创建失败时必须关闭监听 socket
  后传播原始错误，不能让端口保持绑定却没有服务循环。
- 假设与预期：旧代码直接调用 `Thread.start()`。线程配额耗尽时函数在 `return httpd` 前抛异常，调用方没有服务器对象可关闭，
  socket 只能等待垃圾回收或进程退出。
- 为什么选这条路线：HTTP 监听线程是服务核心，不能降级运行；正确行为是 fail-closed 且显式释放已获取资源。用 mock HTTP server
  和线程启动故障可确定性证明 `server_close()`，不改变正常监听地址或线程参数。
- 改动与实验：把循环线程启动纳入 `try`，`RuntimeError` 时调用 `server_close()` 后原样抛出。新增失败时关闭监听器一次且不返回
  半启动服务器的断言。
- 结果：`serve/test_server.py` 为 `346 passed / 502 subtests`；相关文件 `py_compile` 与 `git diff --check` 通过。
- 判决：采纳。核心监听能力失败时服务不发布，但已经绑定的端口和 socket 会被立即释放。
- 生产状态与回滚：未改成功启动、监听 backlog、handler、telemetry 或关闭顺序；单提交可回退。未在真实端口耗尽或真实线程配额
  耗尽环境运行。
- 学到什么：资源在核心依赖启动前已经获取时，核心依赖失败必须负责释放，而不能等待调用方或 GC。
- 下一步与理由：继续检查 C++ 模型加载中直接启动局部线程组、失败后可能 `std::terminate()` 的路径。

## I-20261027-42 · batch slot 释放线程失败不泄漏服务容量

- 目标 / 指标：消费者提前离开后，释放线程必须先完成 `BSTOP`/`BDONE` 所有权收敛；若系统拒绝新线程，slot 仍应最终恢复为
  可调度状态。BGEN 被引擎拒绝后用于延迟归还 slot 的 Timer 创建失败，也不能把异常抛给请求或永久占用 slot。
- 假设与预期：`_release_slot_when_done()` 先登记 `slot_releasing` 再启动线程。线程创建失败会留下 busy+releasing，而 orphan
  reaper 明确跳过 releasing，后续请求永久少一个 slot。`_park_slot()` 的 Timer 失败则会让当前请求异常，但 orphan reaper 可在
  `ORPHAN_SLOT_S` 后恢复。
- 为什么选这条路线：两处都在 batch 并发主路径，fake engine/queue 可确定复现，不依赖 GPU；正常成功路径仍由后台线程或 Timer
  完成，队列清理与缓存记账不变。
- 改动与实验：释放线程启动失败时在已经结束响应的请求线程执行同一有界 `BDONE` 等待；Timer 启动失败只记录诊断，保留无主 busy
  标记交给 orphan reaper。新增队列预置 BDONE 的同步清理断言和 Timer 失败的 reaper 恢复断言。
- 结果：`serve/test_parallel.py` 为 `24 passed`；相关文件 `py_compile` 与 `git diff --check` 通过。
- 判决：采纳。线程配额耗尽可以降低并发释放的及时性，但不能永久泄漏 slot 或把可恢复的 BGEN 拒绝变成 500。
- 生产状态与回滚：未改正常 BDONE 缓存、slot 选择、BGEN 重试或 Timer 延迟；单提交可回退。未在真实引擎长卡死、线程配额耗尽、
  CUDA sm_75 双卡并发或真实 Qwen 批处理上运行。
- 学到什么：busy/releasing 这类互斥所有权状态必须在任何线程启动异常后仍有确定的收敛路径。
- 下一步与理由：继续审查“登记状态后才启动辅助线程/Timer”的其他位置，并跑并发回归。

## I-20261027-41 · 周期性空闲任务与浏览器线程失败不中止服务

- 目标 / 指标：服务已经绑定 HTTP 端口后，空闲模型卸载、弹性视觉编码器回收或自动打开浏览器的后台线程若无法创建，服务仍应
  提供 API；失败只关闭对应的可选周期任务并给出诊断，不能留下已发布 ready 状态后异常退出。
- 假设与预期：三处直接调用 `Thread.start()`。操作系统线程配额耗尽产生的 `RuntimeError` 会从启动主流程抛出；此时 HTTP
  服务线程可能已经运行，但 main 尚未进入正常关闭循环，客户端得到的是一个没有生命周期所有者的半启动服务。
- 为什么选这条路线：三项都是定时清理或便利功能，不影响当前请求、推理或显存策略；使用现有 fake engine 和线程启动故障即可
  验证，成功路径的周期、阈值和浏览器 URL 保持字节不变。
- 改动与实验：新增统一 `start_auxiliary_thread()`，记录线程启动失败并返回空线程；空闲卸载、视觉 idle 回收和打开浏览器统一
  使用它。新增两个 idle worker 同时失败后健康端点仍返回 200，以及可选线程失败文本的断言。
- 结果：`serve/test_server.py` 为 `345 passed / 502 subtests`；相关文件 `py_compile` 与 `git diff --check` 通过。
- 判决：采纳。后台便利能力不能成为已绑定服务可用性的前置条件。
- 生产状态与回滚：未改正常空闲卸载、弹性显存回收、浏览器打开或关闭顺序；单提交可回退。未在真实线程配额耗尽、真实浏览器
  启动器或双 RTX 2080 Ti 服务上运行。
- 学到什么：HTTP 监听成功后出现的非必要线程失败，应有统一的降级与诊断入口，避免形成半启动服务。
- 下一步与理由：检查启动事务中“资源已发布、后续辅助步骤失败”的更多顺序问题，再跑服务端相关全量回归。

## I-20261027-40 · 生成探针线程失败不拖垮托管主机

- 目标 / 指标：`Runtime.Host` 启动 `strata-generation-probe` 时若进程无法再创建线程，只能暂缓生成就绪验证；控制通道、
  托管服务器和活动租约必须继续存在，后续状态请求应能重新尝试探针。
- 假设与预期：旧实现先发布 `probe_key` 后直接调用 `Thread.start()`。线程配额耗尽产生的 `RuntimeError` 不在控制循环的
  `OSError/ValueError` 处理范围内，会离开 `serve()` 并触发托管子进程停止；即使捕获，残留 `probe_key` 也会永久阻止重试。
- 为什么选这条路线：探针负责 API 身份和一次生成验证，属于辅助能力；可用 fake child/ready 文件和线程启动故障确定性覆盖，
  不影响成功路径的探针参数、超时或恢复状态。
- 改动与实验：捕获探针启动的 `RuntimeError`，清除本次 `probe_key/probe_result`，让状态保持 `verifying_generation`，下次轮询重试；
  新增线程启动失败下 `_status()` 不抛异常且可重试的断言。
- 结果：`tests/terminal/test_runtime.py` 为 `51 passed / 11 subtests`；相关文件 `py_compile` 与 `git diff --check` 通过。
- 判决：采纳。生成验证线程创建失败不再关闭控制主机或服务器，并保留后续恢复机会。
- 生产状态与回滚：未改正常探针的鉴权、HTTP 请求、超时或就绪判定；单提交可回退。未在真实操作系统线程配额耗尽或双
  RTX 2080 Ti 加载进程上运行。
- 学到什么：保存“已开始”状态必须与真正启动成功绑定；辅助线程的失败还必须有可重试状态，而不能只是吞掉异常。
- 下一步与理由：继续审查控制主机 finally 的顺序与失败后仍可恢复的进程/文件所有权。

## I-20261027-39 · sd-cli 等待 OS 错误仍终止并释放子进程

- 目标 / 指标：图像子进程的 `poll()`/`wait()` 抛普通 `OSError` 时，也必须 kill、做一次有界 wait、join/关闭输出 reader，
  删除本次 job 目录，并返回 `ImageJobFailed`，不能把仍运行的 sd-cli 与管道留在后台。
- 假设与预期：旧代码只处理第一次 `wait(timeout=30)` 的 `TimeoutExpired`。普通 `OSError` 会跳出处理块，跳过 kill、二次 wait
  和显式 stdout 关闭；异常离开 `_run()` 后进程与 reader 的所有权无人继续处理。
- 为什么选这条路线：图像进程生命周期已有 mock 故障测试和无 GPU fake sd-cli；修改不改变正常输出、超时文本、进度解析或
  生成图片字节。
- 改动与实验：把轮询与首次 wait 纳入同一个 `try`，`OSError` 分支记录原因、kill、有界 wait；若二次 wait 仍失败则保留
  “after being killed” 语义。随后统一 join reader 并关闭 stdout。新增首次等待 OSError 的 kill/wait/close/job-dir 断言。
- 结果：`serve/test_images.py` 为 `25 passed / 20 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。等待系统调用失败不再绕过图像子进程的终止和输出管道清理。
- 生产状态与回滚：未改成功作业、取消、超时、输出解析或 GPU 策略；单提交可回退。未运行真实 sd-cli、Qwen Image 或 CUDA GPU。
- 学到什么：等待错误是清理的开始，不是清理的终点；所有 wait 失败分支都必须汇入同一个进程与 pipe 释放边界。
- 下一步与理由：继续检查长生命周期 HTTP/进程封装中的 finally 边界，并回到 C++ 主加载路径做一次静态与合成回归。

## I-20261027-38 · telemetry 采样线程失败不阻止服务启动

- 目标 / 指标：硬件 telemetry 的后台采样线程无法创建时，服务仍应启动；保留静态 GPU/CPU 信息与按需 `sample()`，不得留下
  半启动对象，`close()` 仍可安全重复调用。
- 假设与预期：`Telemetry.__init__()` 直接启动 daemon 线程，线程配额限制或运行时拒绝会把异常抛到 `Service.start_telemetry()`，
  仅影响监控图表却中止整个服务器初始化。
- 为什么选这条路线：采样器是纯辅助设施，可用 mock GPU reader 和线程启动故障完全验证；正常采样周期与 `/metrics` 快照不变。
- 改动与实验：线程启动失败后记录告警并把 `_thread` 置空，保留静态信息和按需采样；新增双卡静态/按需读数断言、空历史快照
  和失败对象安全关闭测试。
- 结果：`NvidiaTelemetry` 与 `TelemetryNoDisks` 目标测试为 `3 passed`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。采样线程只会影响周期历史，不能成为服务可用性的前置条件。
- 生产状态与回滚：未改成功启动的采样、GPU 聚合、磁盘/CPU 内存字段或 `/metrics` 格式；单提交可回退。未在真实线程配额
  耗尽或双 RTX 2080 Ti 服务器上运行。
- 学到什么：构造函数中的后台监控线程应像其他辅助 worker 一样有降级路径；按需采样接口可以继续为状态检查提供当前值。
- 下一步与理由：继续审计构造函数中启动线程的组件和可失败进程清理，优先找会让主服务不可用的辅助路径。

## I-20261027-37 · PLE issue 队列与 ticket 提交保持一致

- 目标 / 指标：`PleReader::issue()` 在 jobs 的 vector/unordered_map/deque 或 ticket 表分配失败时，必须回滚本次已追加
  的队列项；不能先发布一张调用方永远拿不到的 ticket，也不能让后续 reader 继承 pending 或半队列状态。
- 假设与预期：旧实现把 `TicketState&` 插入 `tickets` 后才逐项构建和入队 job。任一分配异常都会抛穿，但 ticket 已占用
  id；部分 job 已进入 queue，另一些没有，ticket 的 pending 永远无法归零，后续 collect/close 只能依赖整体清理。
- 为什么选这条路线：这是直接 PLE 读取和 prefill 共用的事务边界，可用 CPU 合成表与确定性注入覆盖；正常表布局、行宽、
  job 排序和提交后的读取字节不变。
- 改动与实验：先在局部 jobs 完成去重、排序，再追加到 deque；每次追加成功计数，ticket 只在队列完整提交后插入。捕获异常时
  移除本次追加的队尾并重新抛出。新增 `STRATA_TEST_PLE_QUEUE_FAIL=1`，在首个 job 后失败，断言异常发生且下一次读取正确。
- 结果：`ple_reader_test --selftest` 通过全部八种格式和 90/110 字节行宽；ASan/UBSan（泄漏检测开启、遇错停止）与
  TSan（遇错停止、记录第二把锁）均通过；`-Wall -Wextra -Werror` 和 `git diff --check` 通过。
- 判决：采纳。队列项和 ticket 在同一个提交边界内发布，分配失败恢复成 issue 前的 reader 状态。
- 生产状态与回滚：未改成功路径的 job 顺序、去重、缓存或 notify 时机；单提交可回退。未验证真实地址空间耗尽的 OOM killer、
  真实 Qwen PLE 分片或 CUDA sm_75 预填。
- 学到什么：调用方以返回 ticket 为所有权的资源必须在返回值可构造前最后提交；先发布所有权再填充数据会留下不可达状态。
- 下一步与理由：继续审查 hot path 中“先改对象状态、后执行可失败分配/队列操作”的提交顺序。

## I-20261027-36 · MCP 关闭错误响应与配置对象校验

- 目标 / 指标：HTTP MCP 关闭会话时，DELETE 的 4xx/5xx `HTTPError` 必须在归一化后关闭；配置中的 `mcp` 字段不是对象时
  必须返回清晰 `SystemExit`；诊断入口读取配置也要显式关闭文件。
- 假设与预期：`HttpTransport.close()` 用 `urlopen(...).close()` 启动 DELETE，但 4xx/5xx 在赋值前变成异常，其响应体
  进入 `except OSError` 后未关闭。`settings_from()` 对 `"mcp": "fast"` 调用 `.items()`，以未处理的 `AttributeError` 终止。
  `python -m serve.mcp config.json` 的 `json.load(open(...))` 还依赖引用计数关闭配置。
- 为什么选这条路线：三处都有无网络 mock 或无副作用的配置测试；修复不改变 MCP 请求、成功 DELETE、设置默认值或协议字段。
- 改动与实验：成功 DELETE 使用上下文管理；`HTTPError` 单独用上下文关闭；`mcp` 块先验证为对象；诊断入口改用 `with open`
  并保留原有文件/JSON 错误文本。新增 DELETE 405 关闭和字符串 `mcp` 配置拒绝两项断言。
- 结果：`serve/test_mcp.py` 为 `36 passed / 9 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。MCP 关闭的每个 HTTP 结果都有显式响应 owner，错误配置在读取字段前被拒绝，诊断入口不再依赖 GC 关文件。
- 生产状态与回滚：未改正常 initialize/tools/call、会话 DELETE 或运行配置中的合法 `mcp` 对象；单提交可回退。未访问真实
  MCP 服务、代理、TLS 中断或企业鉴权端点。
- 学到什么：HTTP 的异常也是响应；配置容器类型校验必须发生在 `.items()` 等迭代操作前。
- 下一步与理由：继续检查其余产品入口的 JSON/文件读取和 HTTP 错误分支，再做一轮高价值 C++ 装载资源审查。

## I-20261027-35 · 统一 expert source 辅助线程的失败与回收

- 目标 / 指标：`expert_source.cpp` 中共享 atomic 工作队列的批读、缓存补集构建、补集校验、GGUF 专装载、Arena 读取和
  Linux 文件层探测，都要在辅助线程创建/预留失败时由调用线程完成剩余工作，并 join 已启动线程。
- 假设与预期：这些路径各自重复“启动 N-1 个线程、调用线程也工作、逐项 join”。部分路径仍直接 `emplace_back`，失败时
  局部线程组析构终止进程；其余路径通过复制异常处理维持正确性，但失败语义容易再次漂移。
- 为什么选这条路线：这是纯代码精简与异常安全收敛，不改变工作队列、批次、线程数量或正常并发；已有 34 专家填充注入
  可直接验证共享 helper，CPU 合成测试覆盖模型加载主适配器。
- 改动与实验：新增 `run_parallel_workers(total, work, label, inject_env)`；先 reserve，再启动 helper，失败时记录并让
  调用线程执行同一工作，最后 join 已有线程。六个使用点统一迁入 helper；测试注入环境仍驱动批量填充的失败分支。
- 结果：主机侧 `file_expert_source_test`（合成 pack、CPU 库和同步 CUDA shim）`PASS`，实际输出
  `batch readers fall back to the caller (injected thread start failure)`；ASan/UBSan（泄漏检测开启、遇错停止）同样
  `PASS`；`-Wall -Wextra -Werror` 与 `git diff --check` 通过。
- 判决：采纳。六条辅助线程路径共享同一失败语义和回收边界，代码减少重复，正常 work queue 与字节路径不变。
- 生产状态与回滚：未改正常装载、补集、探测或读取策略；单提交可回退。未验证真实线程配额耗尽、真实 GGUF 双卡装载、
  RTX 2080 Ti 或 CUDA sm_75。
- 学到什么：可并行且调用线程能接管的工作，应由一个 RAII/统一 helper 管理线程表；否则每个复制点都会重新引入 terminate 或漏 join。
- 下一步与理由：继续把相同原则集中到仍有局部线程组的加载和测试工具，并审查 Python 配置文件与响应本体的生命周期。

## I-20261027-34 · 服务端辅助线程失败时保持请求与副本可用

- 目标 / 指标：MCP 工具心跳线程、客户端断开监视线程、副本并行重启/启动及后台自愈线程都是辅助设施；系统拒绝新线程时，
  工具仍要执行、正常请求仍要完成，存活副本仍要继续路由，失败的副本操作要延后而不是抛穿。
- 假设与预期：`run_with_mcp()`、`_watch_client()`、`ReplicaEngine.restart()/heal()` 和 `start_replicas()` 直接调用
  `Thread.start()`。现有实现会在工具未运行时结束流、在请求尚未开始前返回异常、让副本重启失败冒到调用方，或留下未 join 的
  Python 线程集合。
- 为什么选这条路线：这些是普通 HTTP mock 和 fake replica 可确定覆盖的线程资源边界，与双卡双副本发行配置直接相关；修复
  不改变并发成功时的时序、请求内容、超时或路由成本。
- 改动与实验：MCP 心跳线程启动失败后同步执行同一工具调用；断开监视失败时撤销本次 `active_cancels` 注册并继续请求；
  副本并发启动/重启增加“已启动先 join、剩余工作串行完成”的后备；自愈线程失败时清除 `healing` 标记并等待冷却后重试；
  `start_replicas` 复用同一后备。新增工具执行、普通请求、并行重启和自愈路由四项故障注入测试。
- 结果：目标测试 `23 passed`；完整 `serve/test_server.py serve/test_mcp.py serve/test_replicas.py` 为
  `398 passed / 510 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。辅助线程创建失败只降低并发、保活或即时自愈能力，不改变工具是否执行、请求是否完成或存活副本是否服务。
- 生产状态与回滚：未改正常多线程启动、MCP 轮次、断开取消或副本成本模型；单提交可回退。未在真实线程配额耗尽、双
  RTX 2080 Ti 进程启动或真实客户端半关闭连接上运行。
- 学到什么：辅助线程的失败策略必须与它承担的功能相匹配；若主功能可在调用线程完成，就应降级执行而不是返回错误。
- 下一步与理由：继续检查普通 Python 进程/文件资源与 C++ 局部线程组，优先处理会在正常请求或模型加载中漏资源的路径。

## I-20261027-33 · 批量 expert 填充线程启动失败回退到调用线程

- 目标 / 指标：`FileExpertSource::fill_many()` 的 O_DIRECT 批读和映射读取在 worker 创建或预留失败时，必须由调用线程完成
  全部剩余 blob，join 已启动 worker，发布每个 claimed stage，且不能让局部 `std::thread` 析构调用 `std::terminate()`。
- 假设与预期：两个分支直接对局部 `std::vector<std::thread>` 执行 `emplace_back`。线程资源耗尽、内存不足或任一 worker
  启动失败会抛出，此时已启动线程仍 joinable；局部容器随栈展开析构，C++ 标准直接终止进程，模型加载也随之退出。
- 为什么选这条路线：批量 expert 填充是每层和 profile load 的主路径，可在 CPU 合成 pack 上精确触发多线程分支；成功路径的
  批次、字节与并发数无需改变。
- 改动与实验：两条分支先预留线程表，再逐项启动；失败记录后由调用线程执行同一个 atomic work queue，最后 join 已启动
  worker。新增 `STRATA_TEST_FILL_THREAD_FAIL` 注入和 34 个合成专家用例，检查跨批次边界与末尾专家字节。
- 结果：主机侧 `file_expert_source_test`（合成 pack、CPU 库和同步 CUDA shim）`PASS`，实际输出
  `batch readers fall back to the caller (injected thread start failure)`；ASan/UBSan（泄漏检测开启、遇错停止）同样
  `PASS`；`-Wall -Wextra -Werror` 与 `git diff --check` 通过。
- 判决：采纳。辅助 worker 只负责并行度；任一线程不能创建时，调用线程仍完成同一 work queue，stage 所有权正常发布。
- 生产状态与回滚：未改正常批次大小、O_DIRECT 对齐或映射读取；单提交可回退。未验证真实进程线程配额耗尽、真实 NVMe、
  RTX 2080 Ti 或 CUDA 数据路径。
- 学到什么：局部线程组必须先有失败时 join 的 owner；`try` 包住 `emplace_back` 不足以避免 terminate，已启动线程仍需执行。
- 下一步与理由：继续排查相同的局部线程容器启动模式，优先修产品加载/推理路径，再跑相关 CPU 回归并推送。

## I-20261027-32 · PLE 直接读表范围的溢出安全校验

- 目标 / 指标：`PleReader::open()` 必须在乘法或加法前确认 `[table_offset, table_offset + n_rows * row_bytes)` 落在文件内；
  损坏的 GGUF 头不得借助 `uint64_t` 回绕变成合法范围，也不得在失败后留下打开的文件或无主 worker。
- 假设与预期：旧检查先计算 `table_offset + n_rows * row_bytes > file.size()`。当乘法或加法回绕到较小值时，非法表会被接受，
  后续行偏移同样回绕并产生错误读取或难以解释的短读。
- 为什么选这条路线：低层直接表读取是 PLE 所有格式共同经过的边界；CPU 合成表能覆盖极端头信息、全部格式、同步/worker I/O，
  不需要模型或 GPU，而且正常 offset、行宽和字节完全不变。
- 改动与实验：先用 `table_offset > file_size` 拒绝起点，再用
  `n_rows > (file_size - table_offset) / row_bytes` 比较可用行数，避免任何可能溢出的乘法/加法。新增 `UINT64_MAX` offset 与
  row count 两个拒绝用例，并断言错误信息和关闭状态。
- 结果：直接编译的 `ple_reader_test --selftest` 通过全部八种格式和 90/110 字节行宽的 Direct/Mmap 对拍；ASan/UBSan
  （泄漏检测开启、遇错停止）和 TSan（遇错停止、记录第二把锁）同样通过；`-Wall -Wextra -Werror` 与 `git diff --check` 通过。
- 判决：采纳。表范围验证在第一次算术前 fail-closed，损坏头不会进入文件缓存、slab 或 worker 资源分配。
- 生产状态与回滚：未改正常表布局或读取行为；单提交可回退。未使用真实 Qwen PLE 分片、真实 NVMe 故障或 CUDA sm_75 数据。
- 学到什么：验证“区间在文件内”时，用文件剩余长度做除法比较，比先算结束偏移更直接，也天然规避无符号回绕。
- 下一步与理由：继续审查同一读取栈的异常路径和 probe 资源清理，再做下一轮 CPU/终端回归并重试远端推送。

## I-20261027-31 · MCP 传输线程启动失败与关闭/启动竞争

- 目标 / 指标：MCP HTTP 请求线程无法创建时必须返回统一的 `McpError`；超时后取消通知线程再失败不能覆盖原始超时；
  stdio 传输关闭后或关闭与启动交错时，不得继续发布一个无人拥有的子进程。
- 假设与预期：`HttpTransport.request()` 直接调用 `Thread.start()`，线程资源耗尽时会让 `RuntimeError` 穿出工具循环；
  取消通知是尽力而为，却可能用第二个线程启动异常替换 `McpTimeout`/`McpCancelled`。stdio `start()` 原先先检查加锁外的
  `proc`，`close()` 查不到进程就返回，随后启动仍可发布子进程。
- 为什么选这条路线：三个分支都可用线程启动故障和关闭后启动精确复现，不需要 MCP SDK、网络或模型；修复不改变协议消息、
  超时值或工具结果文本。
- 改动与实验：HTTP 请求线程启动失败归一化为 `McpError`；取消通知线程启动失败被隔离，原始超时/停止结果继续抛出。
  stdio 增加生命周期锁，进程发布与关闭读取位于同一所有权切换；`ended` 在构造时定义，关闭后的启动在 `popen` 前失败。
  新增 HTTP 首次/第二次线程启动失败和关闭后 stdio 启动三项测试。
- 结果：`serve/test_mcp.py` 为 `34 passed / 8 subtests`；服务端相关套件
  `serve/test_server.py serve/test_images.py serve/test_lifeline.py serve/test_mcp.py` 为 `406 passed / 530 subtests`；
  `compileall` 与 `git diff --check` 通过。
- 判决：采纳。MCP 传输在本地线程资源耗尽和关闭竞争下保持明确错误与单一进程所有权，尽力通知不再改变主调用判决。
- 生产状态与回滚：未改服务配置或正常协议路径；单提交可回退。未运行真实 MCP 市场包、企业网络代理或操作系统线程配额耗尽。
- 学到什么：附属线程启动也是启动事务的一部分；但附属通知失败不能覆盖主操作已经形成的超时/取消结果。
- 下一步与理由：继续检查 HTTP 请求在超时后的连接回收，以及产品入口中未关闭的配置文件/响应对象。

## I-20261027-30 · strata_bench 的 HTTPError 正文读取后关闭

- 目标 / 指标：流式基准请求收到 4xx/5xx 时，保留 `HTTP <code>: <body>` 错误文本，并在返回结果前关闭
  `HTTPError` 的响应和连接。
- 假设与预期：`chat()` 读取 `HTTPError` 正文后只生成字符串，未显式关闭；连续采样失败端点会把 socket/TLS 的释放
  推迟到 GC，和此前 Terminal telemetry、MCP HTTP 修正是同类问题。
- 为什么选这条路线：基准工具有现成 HTTP fake 和无 GPU 测试；修改只影响错误归一化，不改变成功流解析、计时或报告。
- 改动与实验：`HTTPError` 分支在 `try/finally` 中读取最多 200 字节并 `close()`。新增 mock 503 响应，断言错误文本
  仍含 `HTTP 503`，且 close 被调用。
- 结果：`python -m pytest -q tools/test_strata_bench.py` 为 `18 passed`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。基准请求的成功体由上下文关闭，失败体也在归一化后显式关闭。
- 生产状态与回滚：未改服务；仅改维护型基准工具的错误路径，单提交可回退。未访问真实慢速/中断的 benchmark 端点。
- 学到什么：错误正文也属于响应资源；转成展示字符串和释放连接应放在同一 finally 边界。
- 下一步与理由：继续检查其他维护工具中 `urlopen(...).read()`、未关闭 key/output 文件，以及图片/模型下载临时文件；
  保持产品入口优先，再回到 C++ RAM promotion 与 prefill 审查。

## I-20261027-29 · quant_formats 的 HTTP 响应显式关闭

- 目标 / 指标：Hugging Face 模型树 JSON 和每个 GGUF range 请求都要在读取完成、空响应或解析异常时关闭
  `HTTPResponse`/连接；工具失败后不能留下 socket 或 TLS 资源。
- 假设与预期：`RangeFile._fill()` 对 `urlopen(...).read()` 的临时响应没有显式 owner，`hf_headers()` 直接
  `json.load(urlopen(...))`；完整读取通常会释放连接，但异常、短读和策略差异不应依赖垃圾回收。
- 为什么选这条路线：它是只读检查工具，HTTP 行为可完全 mock；修复不改变 range 大小、重试语义、输出 JSON 或
  GGUF 解析。
- 改动与实验：两处改为 `with urllib.request.urlopen(...) as response`，分别读取 bytes 和 JSON。新增 mock
  响应测试，断言连续两次 range 请求都已关闭，并验证 tree 响应在 header 构造前关闭。
- 结果：`python -m pytest -q tools/test_quant_formats.py` 为 `2 passed`；`compileall` 与 `git diff --check`
  通过。没有访问真实 Hugging Face 网络。
- 判决：采纳。所有该工具的 HTTP 响应都有明确的上下文所有权。
- 生产状态与回滚：未改服务；仅维护工具的网络读取方式，单提交可回退。未验证真实 TLS reset、重定向或大型模型树。
- 学到什么：HTTP 响应不是 JSON/bytes 本身；即使 `.read()` 成功返回，也应由请求点显式结束传输。
- 下一步与理由：检查同类基准/下载工具中对 `HTTPError` 和本地文件句柄的隐式关闭，并继续补充资源回归。

## I-20261027-28 · 非透传图片规范化的 Pillow 图像释放

- 目标 / 指标：`Vision.normalize()` 处理 EXIF 旋转、模式转换和透明图像扁平化时，源图、旋转副本、拆分 band、
  转换结果和临时背景图都必须在成功或异常路径关闭。
- 假设与预期：旧实现用 `with Image.open(...)` 只关闭源图，随后 `ImageOps.exif_transpose()` 返回的新图和
  `convert()` 返回的新图都没有显式关闭；透明分支的 `im.split()` band 也未关闭。连续直接图片请求会依赖 Pillow
  对象回收，极端情况下保留临时缓冲或文件资源。
- 为什么选这条路线：图片规范化是纯 Pillow 路径，可用无 GPU 的合成 JPEG/TIFF 精确覆盖；修复不改变输出格式、
  像素方向或透明区白色背景规则。
- 改动与实验：把源图、旋转和转换放进同一所有权范围；转换替换前关闭旧图，band 使用后逐个关闭，背景图和输出
  `BytesIO` 使用上下文，最终副本在 `finally` 关闭。新增 Pillow `close` 调用计数断言，以及透明 TIFF 转 RGB PNG
  的方向/尺寸/白底测试；测试构造的图片也显式关闭。
- 结果：`serve/test_exif_orientation.py serve/test_images.py` 为 `29 passed / 20 subtests`；`compileall` 与
  `git diff --check` 通过。首次测试确认旧计数阈值设置过高，调整为只验证旋转副本的显式关闭，普通源图由既有上下文
  负责。
- 判决：采纳。每个 Pillow 图像/band 在所有规范化分支都有明确 close；失败仍归一化为原有 `ValueError`。
- 生产状态与回滚：未改生产；JPEG/PNG/BMP/GIF 透传、EXIF 方向、透明白底和错误文本行为由测试保持，单提交可回退。
  未运行真实视觉编码器或 GPU。
- 学到什么：`with Image.open()` 只覆盖源图，图像变换返回的新对象仍需要独立所有权；颜色 band 也是图像对象。
- 下一步与理由：继续检查 HTTP 下载和图片缓存的响应/临时文件收尾，再做一次完整服务端回归。

## I-20261027-27 · 驻留预算、NUMA 与 stage lend 计划的溢出边界

- 目标 / 指标：pin 判断不得因 `bytes + reserve` / `step + reserve` 溢出而错误放行；NUMA 估算不得因节点数乘法
  溢出；stage lend 计划不得在负几何、乘法溢出或 `bytes > cap` 时越界或下溢。
- 假设与预期：旧 `pin_depends_on_reclaim()`、`pin_step_fits()` 直接相加，极端值会绕回小数；
  `numa_pin_free()` 的 `smallest * node_count` 也会溢出。`append_stage_lend_regions()` 在参数校验前计算
  `n_layers * n_expert`，且用 `cap_bytes - bytes` 比较，调用方状态已超过 cap 时会产生无符号下溢。
- 为什么选这条路线：这些都是纯主机算术，可在现有合成测试中精确覆盖；正常 2080 Ti 几何和真实内存数值不受影响，
  修复可防止损坏的 profile/容器读数或错误调用把安全检查变成放行。
- 改动与实验：pin 判断改用先比较 bytes/step、再比较剩余量的无溢出形式；NUMA 乘法在超过全局可用量时直接由全局量
  封顶；lend 计划先验证正几何和 `size_t` 乘法，再分配索引，并在 `bytes >= cap` 时保留整段文件回退。新增溢出 reserve、
  NUMA 乘法、lend 几何和已超 cap 四种断言。
- 结果：主机侧 `file_expert_source_test`（合成 pack、CPU 库和同步 CUDA shim）`PASS`；`-Wall -Wextra -Werror` 和
  `git diff --check` 通过；同一测试的 ASan/UBSan（泄漏检测开启、遇错停止）也通过。完整
  `serve/test_server.py serve/test_images.py serve/test_lifeline.py serve/test_mcp.py` 为
  `403 passed / 530 subtests`；`tests/terminal` 为 `199 passed / 46 skipped / 93 subtests`（跳过项均为平台/环境条件）。
- 判决：采纳。所有安全预算判断对 `uint64_t` 极值保持 fail-closed（无法证明安全时不进入预取/驻留路径）。
- 生产状态与回滚：未改生产；常规内存值、NUMA 节点选择和合法 lend 偏移逐一由既有测试保持，单提交可回退。
  未验证真实 NUMA 双路机、RTX 2080 Ti 或伪造 cgroup 的内核竞态。
- 学到什么：安全余量检查不能先做可能溢出的加法；“剩余空间”必须先确认当前用量没有超过上限。
- 下一步与理由：补充真实服务端全量回归结果，检查 RAM promotion/HTTP 下载的资源收尾，再集中重试远端的全部本地提交。

## I-20261027-26 · 后台 expert 读线程启动失败回退到调用线程

- 目标 / 指标：`prefetch_begin()` 已 claim 的 stage buffer 在任一后台读线程创建失败时仍必须被发布；不能抛穿调用方、
  遗留 joinable 线程、让 `wait_landed()` 永久等待，或在 direct read 分配异常时留下 busy buffer。
- 假设与预期：旧代码直接 `async_th_.emplace_back(work)`。若 `reserve` 或线程构造失败，函数抛出，stage buffer 的
  `busy` 标记和 `pending` 已由 `claim_todo()` 写入；调用方若只收到异常而继续使用 source，后续 blob 会等待一个没有
  worker 的 fill。
- 为什么选这条路线：它位于 cold expert 读取的异步边界，可复用已有 unbuffered 合成 pack；用明确的测试注入拒绝首个
  worker 启动，能覆盖真实线程构造失败所走的后备路径，不需要 GPU 或模型。
- 改动与实验：`prefetch_begin()` 先 reserve worker 表；任一线程启动失败时由调用线程执行同一 `work()`，随后
  `prefetch_end()` join 已完成启动的 worker，把所有已 claim blob 标记为不再 pending。direct 批量读的分配/异常改为该批
  stage 逐项失败发布。新增 `STRATA_TEST_COLD_THREAD_FAIL=1` 注入，断言回退后三个 blob 内容正确且 pending 清零。
- 结果：主机侧 `file_expert_source_test`（合成 pack、CPU 库和同步 CUDA shim）`PASS`，实际打印
  `background reads fall back to the caller (injected thread start failure)`；`-Wall -Wextra -Werror` 与
  `git diff --check` 通过；同一测试的 ASan/UBSan（泄漏检测开启、遇错停止）通过。
- 判决：采纳。后台读 worker 是加速器而不是正确性依赖；启动失败后由调用线程完成同一批读取并恢复 pending 状态。
- 生产状态与回滚：未改生产；正常线程启动与 direct batch 字节/顺序不变，单提交可回退。未验证真实 O_DIRECT
  设备错误、真实线程资源耗尽、CUDA sm_75 或吞吐。
- 学到什么：提交 pending=1 就必须同时保证有一个会把它清零的执行者；线程创建失败后，调用线程是最可靠的后备执行者。
- 下一步与理由：检查 RAM promotion worker 的同类后备路径与 `HostGraphLaunch` join 约定，再运行 Python 全量回归。

## I-20261027-25 · ForesightSwap 初始化校验与 copy 队列提交边界

- 目标 / 指标：`ForesightSwap::init()` 拒绝空几何、层/专家表乘法溢出、缺少或越界的 card 映射，并只允许一次初始化；
  filler 线程创建失败时返回错误而非抛穿；`take/note_miss` 的越界参数不能索引 `where`；copy 队列分配失败不能留下
  指向尚未入队 job 的 slot 或泄漏预算计数。
- 假设与预期：旧 init 在验证 `card_of_layer` 长度前就用 layer 索引它，并把 `layers * slots/experts` 转成 `size_t`，
  错误输入可越界或溢出。`request_locked()` 先将 `where`、slot 和预算计数改成 loading，再向 deque 入队；若入队分配失败，
  该 slot 永远等不到 filler，容量与计数也已被占用。
- 为什么选这条路线：初始化几何和队列提交都是主机侧控制流，可在没有 GPU 的路径上验证错误输入；成功路径的 slot
  状态恢复可由代码审查确认，不改变正常 copy 顺序或 device 地址。
- 改动与实验：init 在任何资源分配前校验正整数、两处 size 乘法、card 映射和重复初始化；每个 device 的字节数检查
  乘法/加法溢出；filler `std::thread` 创建异常转成 `err`。`take`、`note_miss` 和内部 request 统一校验参数。copy
  queue 入队失败时恢复旧 expert 的 `where`、slot 状态、source 和计数，并计为 busy。新增 6 类非法 init 和未初始化
  `take` 断言。
- 结果：主机侧 `file_expert_source_test`（合成 pack、CPU 库和同步 CUDA shim）`PASS`，其中
  `foresight init validation: PASS`；ASan/UBSan（泄漏检测开启、遇错停止）同样 `PASS`；`-Wall -Wextra -Werror`
  与 `git diff --check` 通过。
- 判决：采纳。非法几何在访问 vector 或分配显存前失败，queue 分配失败可回滚 slot 所有权；线程启动失败有明确错误。
- 生产状态与回滚：未改生产；合法初始化的 device 分配、copy 顺序和 slot 选择未变，单提交可回退。没有真实 CUDA，
  因此未运行 filler 成功路径、真实 copy queue OOM 注入、foresight 命中或双卡交换。
- 学到什么：`push_back` 是 slot 状态提交的一部分，不是 slot 改变后的附属通知；若 job 没有可靠进入队列，必须撤销
  slot 与预算的所有权。
- 下一步与理由：检查 `HostGraphLaunch` 的 join/release 约定与 `prefill` 的异步链，再做一轮 Python 全量回归并重试推送。

## I-20261027-24 · FileExpertSource 的 I/O 启停与 stage 缓冲区异常安全

- 目标 / 指标：I/O 预取关闭后不得残留 `io_pf/io_sched/io_stats/gate` 状态；worker 或相关表部分创建失败时不得泄漏
  文件描述符或遗留可 join 线程；读缓冲和 RAM 提升分配异常不得让 worker 调用 `std::terminate`，也不能把 stage buffer
  永久留在 busy。
- 假设与预期：旧 `io_stop()` 只关闭线程和 fd，不重置调度与统计开关，因此关闭再启动会继承上一轮配置；worker 创建
  失败会让异常穿过 `set_io_prefetch()`，而已启动 worker 只能在对象析构时回收。`claim_stage()` 先拿 buffer 再扩容
  多个并行表，任何一次分配失败既可能泄漏原始缓冲区，也可能留下长度不一致的表；`copy_from_files()` 抛异常时
  `stage_busy_` 不会清零。
- 为什么选这条路线：这些状态都局限在 `FileExpertSource` 的预取生命周期，可用合成 `experts.bin`、CPU 侧 CUDA
  shim 和重复启停覆盖，不需要真实模型；修复不改变正常读取字节、预取顺序或公开配置。
- 改动与实验：`set_io_prefetch()` 将 fd 准备、辅助表分配和 worker 创建纳入回滚，失败后调用 `io_stop()`；后者重置
  `io_pf/io_fill/io_sched/io_stats/io_ahead/gate`。`claim_stage()` 在获取原始缓冲前预留所有并行表；I/O worker 的
  stage 选择、fill scratch 和最终 read 均捕获异常并按失败发布 buffer；RAM 提升排队失败会撤销未启动的 job，读取
  分配错误会标记 failed 而不是终止线程。新增预取关闭、重开和重开后读取断言。
- 结果：主机侧 `file_expert_source_test`（合成 pack、CPU 库和同步 CUDA shim）`PASS`；`-Wall -Wextra -Werror`
  编译通过；同一测试的 ASan/UBSan（泄漏检测开启、遇错停止）`PASS`；`git diff --check` 通过。
- 判决：采纳。I/O 预取配置在禁用后没有残留，worker 启动失败有明确回滚，stage buffer 的分配/读取异常有失败发布
  路径。
- 生产状态与回滚：未改生产；正常 `STRATA_IO_PREFETCH` 读取与 stage 字节不变，单提交可回退。本机没有 CUDA
  toolkit、RTX 2080 Ti 或真实模型，未验证 CUDA sm_75、真实 page-fault/worker 竞争或实际吞吐。
- 学到什么：禁用后台设施必须同时撤销它的调度状态；“线程可 join”不等于启动事务成功，分配失败的 stage 元数据也
  必须与数据 buffer 同一事务提交。
- 下一步与理由：继续检查 `ForesightSwap` 和 `HostGraphLaunch` 的队列/线程失败边界，再做主机侧 sanitizer 与完整
  Python 回归。

## I-20261027-23 · Terminal telemetry 的 HTTPError 响应显式关闭

- 目标 / 指标：只读 telemetry client 收到 401/403、重定向或不支持的状态码时，在转换成 `ClientError` 前关闭底层
  `HTTPError` 响应及连接。
- 假设与预期：旧分支直接读取状态码并 raise，`HTTPError` 依赖垃圾回收；频繁轮询失败端点会短期保留 socket/file 对象。
- 为什么选这条路线：终端监控是长期运行的只读路径，修复只涉及一个响应 owner，且可用 `HTTPError.close` 精确验证。
- 改动与实验：`Endpoint.get()` 用 `with e:` 包住所有 HTTP 状态分支；新增 500 响应关闭和 `http_status` 保留测试。
- 结果：connections、terminal、drain 目标回归为 `36 passed / 12 subtests`（另有 28 个平台/环境跳过）；
  `compileall` 与 `git diff --check` 通过。
- 判决：采纳。telemetry 的每个 HTTPError 在错误归一化前都显式关闭。
- 生产状态与回滚：未改生产；状态码映射、重定向拒绝和错误文本不变，单提交可回退。
- 学到什么：错误响应仍是响应对象；返回简化 `ClientError` 前必须先结束其传输资源。
- 下一步与理由：继续把相同原则应用到实际发行包会调用的下载/探测工具，并区分维护用脚本与产品入口。

## I-20261027-22 · runtime stop 在 wait 异常与强制终止后完成 ready/owner 释放

- 目标 / 指标：runtime host 停止 child 时，普通 wait、TimeoutExpired、OSError 和已退出四种情况都必须关闭 owner pipe、
  删除本次 ready 文件；进程组 SIGKILL 失败时仍需直接 kill child。
- 假设与预期：旧 `_stop_child()` 只在正常停止和“child 已退出”路径删除 ready；SIGTERM 后超时或 wait 抛 OSError 时
  只关闭 owner pipe，ready 文件可能残留。`_kill_child_tree()` 仅处理 `ProcessLookupError`，进程组权限/状态异常时
  不会尝试直接 kill。
- 为什么选这条路线：这些分支都可在本地 runtime 单元测试中故障注入，不需要模型；它们直接影响下一次 start 的
  ready 文件辨识和 owner pipe 生命周期。
- 改动与实验：新增 `_clear_ready()`，所有停止终点统一删除本次 ready；首次 wait 的 OSError 与 TimeoutExpired 走同一
  强制终止路径；进程组 kill 的普通 OSError 回退到 `child.kill()`。新增直接 kill 回退、wait OSError、强制停止后
  ready 删除与 owner fd 关闭测试。
- 结果：`tests/terminal/test_runtime.py` 为 `50 passed / 11 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。runtime stop 无论正常或强制结束，都不留下本次 ready 所有权标记或 owner pipe。
- 生产状态与回滚：未改生产；正常 SIGTERM 停止、state/socket 协议和重启回滚行为由现有集成测试保持，单提交可回退。
- 学到什么：停止结果“失败”仍必须完成清理；报告 stop 失败与释放本次尝试的资源不是同一件事。
- 下一步与理由：运行完整 Terminal/Control 回归，随后检查 CLI 和工具脚本中未显式关闭的 HTTP/process 资源。

## I-20261027-21 · MCP stdio 进程组升级终止并有界回收，HTTPError 显式关闭

- 目标 / 指标：stdio MCP 忽略 QUIT/SIGTERM 时，close 必须升级到 SIGKILL、再次有界 wait，并分别关闭 stdin/stdout/stderr；
  HTTP MCP 的 4xx/5xx 响应读取错误正文后必须关闭 `HTTPError`。
- 假设与预期：旧 `_kill_tree()` 只对进程组发送 SIGKILL，不再 wait；`HTTPError` 的正文被读取但没有关闭，连接和底层
  socket 依赖垃圾回收。close 的首次 wait 只捕获 TimeoutExpired，发生 OSError 时会跳过终止和管道释放。
- 为什么选这条路线：两者都是可由 mock 精确复现的资源所有权缺口，不依赖真实 MCP 服务或网络。
- 改动与实验：reader 线程显式命名；close 对 stdin 与两条输出管道逐项关闭；`_kill_tree()` 在 SIGTERM 后等待，超时再
  SIGKILL 进程组并直接 kill child，最后再次有界 wait；HTTPError 使用上下文管理关闭。新增 SIGTERM→SIGKILL→reap
  和 HTTPError.close 断言。
- 结果：`serve/test_mcp.py` 为 `31 passed / 8 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。MCP 子进程组和失败 HTTP 响应在正常关闭、超时和强制终止路径上都有明确释放。
- 生产状态与回滚：未改生产；正常 MCP initialize/tools/call 行为和配置格式未变，单提交可回退。
- 学到什么：发送 SIGKILL 不等于回收完成；HTTPError 也是拥有底层连接的响应对象，也必须显式关闭。
- 下一步与理由：继续审查 runtime host 启动和控制通道，查找子进程启动、ready 文件和控制 socket 的部分失败路径。

## I-20261027-20 · 视觉编码器超时、非 READY 与 unload 统一关闭管道

- 目标 / 指标：视觉编码器 READY/ENC 读取超时、回答非 READY、读取线程启动失败或 unload 时，都必须 kill/wait 进程并
  显式关闭 stdin/stdout；下一次请求不能继承半关闭或仍占用显存/管道的 reader。
- 假设与预期：旧 READY 超时路径只把 `proc` 置空，其他失败路径只 kill/wait；所有路径都依赖垃圾回收关闭管道。
  `close()` 正常 QUIT 和强制 kill 两条路径也没有显式关闭 stdin/stdout。
- 为什么选这条路线：视觉 reader 的 READY、ENC、卸载是同一单进程生命周期；线程和等待故障可注入，不需要图像模型。
- 改动与实验：新增内部读超时类型和统一的 `_discard_reader()/_stop_reader()`；`_start_reader()` 对超时、线程失败和
  非 READY 回答统一回滚；`encode()` 超时与 `close()` 都通过同一 helper 终止、wait 和关闭两条管道。
  `_readline()` 只负责有界等待并向调用方报告超时。新增非 READY、读线程失败、QUIT 超时强制终止和正常 unload
  关闭管道的断言。
- 结果：视觉目标测试 `37 passed / 5 subtests`；完整 `serve/test_server.py` 为 `341 passed / 502 subtests`；
  lifecycle、lifeline、images、MCP 合计 `69 passed / 30 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。视觉 reader 的失败、超时、正常卸载和进程替换共用一个释放边界。
- 生产状态与回滚：未改生产；正常 READY、编码结果和缓存格式未变，单提交可回退。未运行真实视觉模型或 GPU。
- 学到什么：把“kill”当成释放完成会漏掉 pipe buffer 和文件对象；进程回收与管道关闭必须同时由 owner 完成。
- 下一步与理由：继续检查 MCP、runtime 与工具脚本中的进程/文件 owner，优先选择有现成无 GPU 测试入口的路径。

## I-20261027-19 · 引擎启动的旁路线程和槽位初始化纳入统一回滚

- 目标 / 指标：日志 narrator/echo、READY reader、槽位队列或主 stdout pump 任一启动失败时，不能遗留引擎进程、
  stdin/stdout、日志句柄或错误的 echo 注册；READY 前失败与 READY 后初始化失败必须走同一清理边界。
- 假设与预期：旧实现只处理 READY 超时后的部分关闭，且 wait 超时会跳过 stdin/stdout/log 的后续关闭；READY pump
  和主 pump 的 `Thread.start()` 失败则直接抛穿，已创建的大模型引擎仍占用 GPU/RAM。
- 为什么选这条路线：这些是同一启动事务的附属资源，可用真实短命假引擎、定向线程启动失败和队列分配失败复现，
  不需要 GPU 或模型。
- 改动与实验：启动前把日志旁白与 echo 线程纳入回滚，echo 注册失败会撤销；READY reader 和后续槽位/pump 初始化
  统一调用 `_abort_start()`，kill/wait 子进程并分别关闭 stdin、stdout、日志。READY 后的批量槽初始化移入
  `_start_after_ready()`，使异常能被同一事务捕获。新增 narrator、echo、READY reader、主输出 pump 和槽队列
  `MemoryError` 五类故障测试；`restart()` 允许清理后 `proc is None`。
- 结果：启动回滚目标 `9 passed`；完整 `serve/test_server.py` 为 `338 passed / 502 subtests`；lifecycle、lifeline、
  images、MCP 合计 `69 passed / 30 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。READY 前、READY 后和重试路径共享进程、管道、日志与辅助线程的清理契约。
- 生产状态与回滚：未改生产；正常 READY、槽位布局、stdout 路由和重启行为由完整测试保持，单提交可回退。
- 学到什么：构造函数的正常返回点就是启动事务提交点；在其前创建的任何进程或线程都应由同一个 rollback 负责。
- 下一步与理由：继续做跨模块 Python 资源审查，优先找仍用“单次 try 包多个 close”导致首错跳过后续释放的路径。

## I-20261027-18 · 子进程所有权记录与 reaper 线程失败保持同一回收边界

- 目标 / 指标：`lifeline.popen()` 已创建 child 后，若所有权记录失败或 reaper 线程无法启动，正常推理启动不得失败；
  child 仍须进入统一退出清理。`end_children()` 还要清理已结束 child 的记录，并在 SIGTERM 超时、SIGKILL 后再次有界
  wait，不能只发信号就返回。
- 假设与预期：旧实现把记录写入和 reaper 线程启动放在同一个静默 `try` 中。线程启动失败时 child 虽未必失活，但没有
  owner 关联，无法安全删除其记录；`end_children()` kill 后立即返回也可能留下尚未被 wait 的进程资源。
- 为什么选这条路线：child 是否可关闭、记录是否可删除和线程启动是否成功可以独立验证；故障注入无需 GPU。
- 改动与实验：维护 child 到 record token 的 owner 映射；reaper 成功、下一次 `popen()` 或 `end_children()` 都可回收已结束
  child。record 写入与线程启动分别 best-effort，失败不影响返回的可用 child。`end_children()` 先清理已结束项，
  SIGTERM 超时后 SIGKILL 并额外 `wait(1s)`，最后只删除已验证 token 的记录。新增 reaper 启动失败和忽略 SIGTERM
  的 child 测试。
- 结果：`serve/test_lifeline.py serve/test_images.py` 合计 `31 passed / 20 subtests`；`compileall` 与
  `git diff --check` 通过。
- 判决：采纳。进程跟踪不再依赖 reaper 线程一定创建成功，退出路径在 kill 后完成有界回收。
- 生产状态与回滚：未改生产；正常 child 启动、PDEATHSIG 和记录格式不变，单提交可回退。未验证真实引擎和 GPU。
- 学到什么：best-effort 的附属记录不能成为进程回收的唯一索引；主跟踪表和记录 token 必须分开维护。
- 下一步与理由：继续审查 `serve/server.py::StrataEngine` 在日志、READY 与 stdout pump 线程部分启动失败时的回滚。

## I-20261027-17 · 图片任务的读线程、子进程和临时目录统一收尾

- 目标 / 指标：`sd-cli` 创建成功后，读线程启动失败、进程不退出或 kill 后仍等待超时时，必须终止并等待子进程、
  关闭 stdout，且只在输出排空或管道已关闭后删除 job 目录；后台 pump 不得因日志写入失败留下未处理异常。
- 假设与预期：旧实现直接 `reader.start()`，线程创建失败会逃出并遗留子进程；`reader.join(10)` 后无论线程是否
  仍存活，`run()` 的 `finally` 都会删除工作目录，日志句柄也可能在 pump 仍写入时关闭。
- 为什么选这条路线：这是单请求进程生命周期的集中边界，可用 mock `Thread.start`、`Popen.wait` 故障注入验证，
  不需要 GPU、模型或 sd-cli 二进制。
- 改动与实验：pump 改为使用 `select` 和原始管道 fd 排空输出，日志写入失败后继续 drain 以防子进程阻塞；读线程
  启动失败时 kill/wait、关闭 stdout 并转成 `ImageJobFailed`；正常和双重超时路径都在 cleanup 前关闭管道。
  新增读线程启动失败与 kill 后仍等待超时测试。
- 结果：`serve/test_images.py` 为 `24 passed / 20 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。读线程启动、子进程结束、管道关闭和 job 删除现在有统一失败收尾。
- 生产状态与回滚：未改生产；正常 HTTP 请求和图片字节不变，单提交可回退。未运行真实 CUDA sd-cli 或图片生成。
- 学到什么：进程已创建不等于启动事务已完成；附属读线程失败与进程等待超时都必须经过同一资源回收路径。
- 下一步与理由：检查 `lifeline.popen()` 在所有权记录或 reaper 线程注册失败时是否仍能由统一退出路径回收 child。

## I-20261027-16 · MCP 启动线程失败回滚子进程，Hub 批量启动不留半活服务器

- 目标 / 指标：stdio MCP 子进程启动后，stderr 或 stdout 读线程任一创建失败时，必须终止子进程、关闭三条管道并
  join 已启动线程；Hub 批量启动中某个线程失败时，必须关闭已启动服务器，且 close/wait 不能 join 未启动线程。
- 假设与预期：旧路径只捕获 `Popen` 的 `OSError`。若第一个读线程已启动而第二个线程因资源限制失败，异常会逃出
  `start()`，子进程继续运行；Hub 的列表中也包含未启动线程，统一 close 会在 `join()` 处再次失败并掩盖原错误。
- 为什么选这条路线：这是同一事务边界的真实部分构造问题，可用 mock 进程与线程启动注入复现，不需要真实 MCP 服务。
- 改动与实验：`StdioTransport.start()` 将两个 reader 的启动纳入回滚，失败时调用统一 `close()`；close 仅 join 确实
  启动过的线程。`McpHub.start()` 任一线程启动失败即关闭全部服务器并传播；`wait()` 跳过尚未启动的线程。
  新增第二个 reader 失败、Hub 第二个线程失败和既有 close 中断启动测试。
- 结果：目标测试 `3 passed`；完整 `serve/test_mcp.py` 为 `29 passed / 8 subtests`；`compileall` 与 `git diff --check`
  通过。
- 判决：采纳。MCP 启动失败要么留下可重试关闭态，要么完全清理，不保留半活子进程或未启动 join。
- 生产状态与回滚：未改生产；正常启动和关闭路径不变，单提交可回退。
- 学到什么：进程创建成功不等于启动事务成功；其附属线程、管道和注册记录必须与进程一起回滚。
- 下一步与理由：继续检查 server/lifeline 的进程启动与线程注册失败组合，随后进行较广 Python 回归。

## I-20261027-15 · PLE 初始化回滚与在途 I/O 显式计数，消除 close 等待和缓存竞争

- 目标 / 指标：`PleReader::open()` 在 slab、向量或行缓存分配失败时不能抛穿或留下半开状态；`close()` 只等待确实
  提交给 DirectFile 的完成事件；缓存统计查询与 worker 更新之间不得存在数据竞争。
- 假设与预期：旧 `close()` 用“空闲槽少于 max_inflight”推断在途 I/O，初始化在填满空闲槽之前失败时会误判并
  永久等待。故障注入延迟产生的 completion 已离开 DirectFile 队列，也必须从等待计数中移除。预取缓存 worker 会
  更新 `used`，主线程直接读取会造成真实竞争。
- 为什么选这条路线：这些问题都集中在无 GPU 的 SSD/PLE 边界，可用合成表、真实 O_DIRECT 和小范围 sanitizer 复现；
  修复不改变行格式、读取顺序或公开配置。
- 改动与实验：新增显式 `outstanding` 计数，只在 `DirectFile::submit()` 成功后增加、每个 completion 仅减少一次；
  初始化分配异常统一返回错误并回滚，close 丢弃 fault-injected completion 后按真实在途数等待；错误 ticket 在 collect
  失败时移除。缓存容量/用量查询纳入 mutex。mmap 预热线程池部分创建失败时由调用线程补做剩余工作并 join 已启动线程。
  selftest 新增不可能的缓存分配与未 collect 即 close 的路径，并让并发测试进程使用独立目录。
- 结果：普通 `-Wall -Wextra -Werror`、ASan/UBSan、TSan 三套构建和 `ple_reader_test --selftest` 均通过 90/110 字节行、
  Direct/Mmap、八种 PLE 格式。首次 TSan 运行发现 `cache_size()` 与 worker `cache.insert()` 的竞争；修复后 TSan 复验通过。
- 判决：采纳。在途 I/O、失败回滚和缓存查询均有明确同步边界。
- 生产状态与回滚：未改生产；单提交可回退。mmap 预热线程创建失败分支仅完成编译和静态审查，未做故障注入。
- 学到什么：“所有槽都不空闲”不等于“所有槽都有在途 I/O”；资源释放必须依据正向提交计数，不能从空闲池反推。
- 下一步与理由：继续审查其他线程池的部分构造与 close 同步，优先选择能由主机侧 sanitizer 覆盖的路径。

## I-20261027-14 · 描述符所有权转移失败统一关闭，覆盖 runtime、编辑草稿、锁与临时图片

- 目标 / 指标：`os.open()` 成功后，若 `fdopen()` 在包装既有描述符时失败，调用方仍必须关闭原 fd；
  runtime 启动日志、保留 lease、profile 编辑草稿、connection 锁和组合图片文件不能泄漏描述符或残留临时路径。
- 假设与预期：这些入口都直接调用 `os.fdopen(fd)`，而 fd 在 `fdopen` 抛错时尚未进入文件对象所有权；
  Python 不会替调用方关闭它。把失败关闭收敛到共享 helper 后，各入口应只负责路径和更高层事务清理。
- 为什么选这条路线：它们共享同一个资源所有权边界，能以一个小 helper 消除重复且不改变正常文件格式或并发语义；
  失败注入可用真实 `fstat` 断言，不需要 GPU 或模型。
- 改动与实验：`atomic.fdopen_owned()` 统一包装 fd，转移失败即关闭；原子写入复用该边界。runtime 启动日志、
  runtime lease 写副本、profile 编辑器草稿、connection 锁与 `serve.server.write_temporary()` 均改用该 helper。
  新增 helper、runtime 启动/记录、profile 编辑、connection 锁和组合图片的 `fdopen` 失败测试。
- 结果：atomic/profile/connection `37 passed / 5 subtests`，runtime `48 passed / 11 subtests`，
  组合图片目标测试 `3 passed`，合计 `88 passed / 16 subtests`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。描述符从打开到成功包装或失败关闭只有一个共享契约。
- 生产状态与回滚：未改生产；正常文件内容、权限、锁顺序和日志写入方式不变，可按单个提交回退。
- 学到什么：`fdopen` 不是一个普通构造调用，而是资源所有权转移点；即使调用处在 `with` 语句中，构造失败也需单独兜底。
- 下一步与理由：继续审查 C++ 线程/进程和缓冲区的部分构造失败路径；这些与 Python fd 路径独立，适合下一阶段集中验证。

## I-20261027-13 · Exclusive-create 记录统一走不泄漏 fd 的原子创建

- 目标 / 指标：connections、operation journal 和 ready file 的新建路径必须使用同一套 owner-only、
  `O_EXCL`/`O_NOFOLLOW` 语义；`fdopen` 或写入失败时不能留下文件描述符或半成品记录。
- 假设与预期：三处此前各自 `os.open` 后直接 `with os.fdopen`，而 `fdopen` 自身抛错时原 fd 不在上下文
  管理范围内；三处也有重复的 flags、权限和清理代码。
- 为什么选这条路线：这些文件分别承载连接配置、操作审计和 readiness 证明，失败语义必须一致，且能用
  真实 fd 检查和 mock 故障验证。
- 改动与实验：新增 `atomic.write_new()`，集中处理 `O_EXCL`、可用的 `O_NOFOLLOW`、0600、flush/fsync、
  父目录同步和失败删除；connections、journal、`serve.ready.publish()` 全部委托该实现。共享的
  descriptor 写入 helper 无论 `fdopen` 是否成功都会关闭 fd。新增 owner-only、不覆盖已有文件、fdopen
  失败无文件/fd 残留测试。
- 结果：atomic、connections、profiles、ready、ready-timeout 和 runconfig 合计
  `48 passed / 30 subtests passed`；ready 的真实 mock server SIGTERM 集成测试也通过，
  `compileall` 与 `git diff --check` 通过。
- 判决：采纳。exclusive-create 的创建和失败清理只有一个实现。
- 生产状态与回滚：未改生产；可用单个提交回退，既有记录格式与权限不变。
- 学到什么：`fdopen` 是资源所有权转移点，失败时仍由调用方负责关闭；把转移封装在 helper 内最容易证明。
- 下一步与理由：继续处理需要保留锁 fd 的 runtime state 写入，以及重启回滚中的错误边界。

## I-20261027-12 · 原子记录写入统一，失败路径关闭描述符并删除临时文件

- 目标 / 指标：profile、connection 和 Web run config 的整文件替换必须共用同一实现；写临时文件、
  `fdopen`、`fsync` 或 `replace` 任一步失败时，不能泄漏描述符、覆盖旧记录或遗留临时文件。
- 假设与预期：旧 profiles 与 connections 各自复制了同一段原子替换逻辑，且 `os.fdopen(fd)` 自身失败时
  没有关闭原始 fd；run config 还使用固定 `<name>.tmp`，并发保存会碰撞。
- 为什么选这条路线：这是跨三个持久化入口的重复实现和同一个失败缺口，合并后能同时减少代码并扩大故障覆盖。
- 改动与实验：在 `strata_control.atomic` 增加 `write_bytes()`，统一使用同目录随机临时文件、flush/fsync、
  replace 和父目录 fsync；profiles 与 connections 改为委托该实现；Web run config 改用同一原子写入，
  删除固定 `.tmp` 路径；`write_text()` 也复用字节实现。新增 fdopen 失败、replace 失败、父目录 fsync
  失败和正常模式/残留文件检查。
- 结果：`test_atomic.py`、profiles、connections、runconfig 合计
  `41 passed / 26 subtests passed`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。三个入口现在共享一个失败语义，旧记录在提交点之前保持不变。
- 生产状态与回滚：未改生产；仅收紧本地 profile、连接与 run config 的保存路径，可用单个提交回退。
- 学到什么：原子替换不仅是 `os.replace`；临时 fd 的所有权、清理点和父目录同步都必须放在同一个事务边界里。
- 下一步与理由：继续收敛 exclusive-create 写入（ready/state/journal）的 fd 所有权，并补充失败注入。

## I-20261027-11 · Runtime stop 正确处理 SIGTERM 前 fast-exit，强杀等待保持有界

- 目标 / 指标：`Host._stop_child()` 不能在 `poll()` 与 `send_signal(SIGTERM)` 之间子进程自行退出时误报失败；
  SIGKILL 之后的第二次 `wait()` 也必须有界，且任何失败结果都先关闭 owner pipe。
- 假设与预期：`poll()` 返回活动只代表检查瞬间；`send_signal()` 可合法抛出 `ProcessLookupError`。旧代码让
  这个正常竞争冒泡成 `invalid_control_request`，并使调用方看不到真实停止状态。
- 为什么选这条路线：这是 stop/restart 的真实竞争窗口，可用假 child 精确覆盖；不需要模型或真实服务。
- 改动与实验：将 `ProcessLookupError` 视为“已经退出”，仍由随后的 `wait()` 回收退出码；SIGKILL 后的
  `wait(timeout=10)` 若仍超时则关闭 owner pipe、返回 `server_stop_deadline_expired`，不再向控制循环抛异常。
  新增 fast-exit 与双重超时两个回归。
- 结果：新增用例 `2 passed`；完整 `tests/terminal/test_runtime.py` 为
  `46 passed / 11 subtests passed`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。stop 竞争被归一为 stopped 或有明确错误码的 failed，不再变成协议解析错误。
- 生产状态与回滚：未改生产；只影响 supervisor 的停止边界，可用单个提交回退。
- 学到什么：进程状态是随时间变化的外部状态；每次基于 `pid` 的操作都必须准备目标刚刚消失。
- 下一步与理由：继续检查 restart 回滚和 server 持久化写入的失败原子性。

## I-20261027-10 · Runtime supervisor 启动失败不泄漏日志、管道或服务器子进程

- 目标 / 指标：`Host._launch()` 在打开日志、创建 owner pipe、启动 `Popen`、写入进程记录或 child marker
  任一步失败时，都必须关闭所有已取得的描述符；`Popen` 已成功但 ownership 记录失败时，必须终止并
  `wait()` 子进程，避免留下无人管理的服务器。
- 假设与预期：原实现只在 `Popen` 周围关闭 reader/writer；`os.pipe()` 本身失败会漏掉已打开的日志 fd，
  而 `Popen` 成功后的 `procs.write_record()` 异常不在清理边界内，可能留下运行中的子进程。
- 为什么选这条路线：这是本地 supervisor 的真实资源所有权问题，可用 mock 子进程和真实 fd 检查稳定复现，
  不需要启动模型或 GPU。
- 改动与实验：pipe 创建失败时关闭日志；`fdopen` 前记录所有权并在失败时补关；把进程记录和 marker
  发布放进同一个启动回滚边界；新增无异常泄漏的 `_abort_launch()`；owner pipe 关闭改为幂等 best-effort；
  已退出或刚消失的子进程不再被当作可 killpg 的活动进程。新增 pipe 失败、`Popen` 失败、
  ownership 记录失败和已退出 child 四个回归。
- 结果：新增 4 个用例通过；完整 `tests/terminal/test_runtime.py` 为
  `44 passed / 11 subtests passed`，`compileall` 与 `git diff --check` 通过。
- 判决：采纳。启动成功前不留下任何仍归 supervisor 所有的 fd 或子进程。
- 生产状态与回滚：未改生产；只影响 managed runtime 的启动失败路径，可用单个提交回退。
- 学到什么：`Popen` 返回后、ownership 文件落盘前仍处于“未完成构造”状态，必须按失败处理。
- 下一步与理由：继续检查停止/重启流程中 SIGTERM 与 fast-exit 的竞争，以及 server 端持久化写入的原子性。

## I-20261027-09 · CPU 池部分线程启动失败可回滚，真实任务一致性通过

- 目标 / 指标：`ExpertPool` 构造期间若创建到一半的 worker 抛出异常，必须停止并 join 已启动线程、清理全局
  NUMA/诊断状态，再向调用方传播错误；不得因 joinable `std::thread` 在栈展开中析构而触发进程终止。
- 假设与预期：现有正常析构会停止并 join，但构造函数直接连续 `emplace_back`；后续线程创建失败时构造函数
  尚未完成，析构函数不会运行，部分已创建线程会留在成员 vector 中。
- 为什么选这条路线：这是 `std::thread` 资源获取失败的确定性生命周期缺口。仓库固定了完整 ggml 上游提交，
  可在 `/tmp` 检出并真实链接 CPU 池，不需要 GPU 或模型。
- 改动与实验：抽出幂等的 `stop_workers()`，构造与析构共用；把可能抛异常的全局指针发布和线程创建放进
  同一回滚边界；移除 Linux 发行版永远走不到的 MSVC `UMWAIT` 分支与相关环境读取；增加
  `STRATA_TEST_POOL_THREAD_FAIL_AT` 故障注入，先启动两个 worker、在第三个前失败，并验证 NUMA 全局状态
  已清除、随后可以构造替代池。用固定提交
  `3cf03257f219afbe7334045ff7c6a06ac68c627d` 的 ggml 构建静态库，只写入 `/tmp`。
- 结果：`pool_cores_test`、`pool_affinity_test` 通过；`pool_tasks_test` 完成
  `360` 项 bitwise 比较，其中 `192` 项走 NUMA split；故障注入路径通过。普通
  `-Wall -Wextra -Werror`、AddressSanitizer + UndefinedBehaviorSanitizer、ThreadSanitizer 构建均通过。
  `pool_test`、`expert_multi_test`、`pool_stress` 可编译，但当前 CPU 缺 AVX-512 VNNI/VBMI，按测试协议跳过
  `modes 0-4` 和真实 expert pack 路径。
- 判决：采纳。构造失败现在是“停止、join、清空全局状态、rethrow”，不再依赖正常析构。
- 生产状态与回滚：未改生产；故障注入只在设置测试环境变量时触发，可用单个提交回退。
- 学到什么：构造函数也必须具备失败自动清理；资源按顺序获取后，任何后续步骤抛异常都要按逆序释放。
- 下一步与理由：继续审计其他“先创建线程/句柄，再完成剩余初始化”的构造器，并运行 CPU 池压力与真实载荷。

## I-20261027-08 · 直接文件 I/O 拒绝关闭态请求，并安全串行化打开、关闭和提交

- 目标 / 指标：`DirectFile` 在未打开或关闭后不能接受请求；打开/关闭与并发提交不能留下无人处理的队列、
  陈旧完成或数据竞争；I/O 工作线程创建失败时必须关闭文件描述符并回收已创建线程。
- 假设与预期：原测试覆盖了排队读取、短 EOF、唤醒、关闭排空和重开，但旧 `submit()` 不检查句柄状态；
  关闭后排队会违反“成功排队恰好产生一个完成”的约定。打开阶段的线程创建也没有异常边界。
- 为什么选这条路线：这是可直接在 Linux 上复现的资源与竞争问题；同时可运行 CPU 池规划、NUMA 决策和
  直接文件测试，不需要 GPU。
- 改动与实验：新增生命周期互斥量，串行化 `open()/close()`；以受锁的 `accepting` 状态和文件描述符共同
  决定 `submit()`/`is_open()`；关闭时先拒绝新请求，再排空已接受队列并 join 工作线程；线程创建异常会
  走同一关闭路径并返回错误；空缓冲也不再被当作合法请求。新增关闭态、失败打开和并发 submit/close 覆盖。
- 结果：`pool_plan_test`、`numa_pool_test` 和 `direct_file_async_test` 均通过。`direct_file_async_test`
  在普通 `-Wall -Wextra -Werror`、AddressSanitizer + UndefinedBehaviorSanitizer、ThreadSanitizer 三种
  构建下均通过，包含 4096 个并发排队尝试与关闭竞争，无 sanitizer 报告。
- 判决：采纳。关闭态请求现在明确失败；成功进入队列的请求仍由关闭排空；生命周期不再依赖调用方恰好单线程。
- 生产状态与回滚：未改生产；只影响 n-gram 表直接读取路径的边界处理，可用单个提交回退。
- 学到什么：“队列入参合法”不够，必须先确认句柄仍可接收；资源启动失败也必须经过正常停止/释放路径。
- 下一步与理由：继续检查其他线程池的构造失败和重复关闭，并尝试运行 CPU 池的真实任务一致性测试。

## I-20261027-07 · 会话、并发与推测模块的主机侧回归扩大，内存锁定测试适配系统限额

- 目标 / 指标：继续把不依赖 CUDA 运行时的 C++ 目标变成当前机器上的真实编译和运行结果，重点覆盖
  会话缓存/落盘/回收、内存保护、交换存储并发、批处理遥测以及 speculation 的边界与统计行为。
- 假设与预期：这些目标要么只依赖 `include/`，要么只需要少量主机 `.cpp`；如果测试固定要求系统无法
  提供的锁定内存，应修正测试的环境假设，而不是把宿主机策略误报为产品逻辑失败。
- 为什么选这条路线：上一阶段证明解析和规划测试可运行后，继续覆盖资源生命周期与并发逻辑的收益更高；
  同时 `platform_memory_test` 在默认 8 MiB `RLIMIT_MEMLOCK` 下暴露了可移植性缺口。
- 改动与实验：按 CMake 的目标定义直接使用 `g++ 15.2.0 -std=c++20 -O2 -pthread -Iinclude` 编译运行
  17 个纯头文件测试、4 个 speculation 测试（链接 `src/spec/*.cpp`）和 7 个会话/内存测试。
  `platform_memory_test` 改为读取当前 `RLIMIT_MEMLOCK`，在限额内锁定页对齐区域；若限额不足一页则返回
  CMake 的 skip 码 77。修改后的测试另用 `-Wall -Wextra -Werror` 编译通过。
- 结果：28 个主机侧测试全部编译并以退出码 0 通过。受限机器上 `lock_resident` 实际锁定 4 MiB、返回
  `ok=1`、`locked=4 MiB`，纯文件缓存/直读规则断言也全部通过。`pin_budget_test` 因缺少
  `cuda_runtime.h` 和 `nvcc` 无法编译，未计入通过。
- 判决：采纳测试适配；它保留真实的 `mlock/munlock` 成功路径，同时不再要求 CI 管理员提高系统锁定上限。
- 生产状态与回滚：只改测试及其 CTest 跳过码，不改运行时内存策略；可用单个提交回退。
- 学到什么：系统资源上限是测试环境的一部分；测试应验证产品在限额内的行为，并用明确的 skip 表示
  无法构成的硬件/内核前置条件。
- 下一步与理由：转向可在 Linux 上直接验证的 CPU 池、会话快照和平台文件 I/O，并继续检查关闭与失败释放。

## I-20261027-06 · 主机侧 C++ 解析、规划与采样测试可独立编译通过

- 目标 / 指标：在没有 CUDA 编译器和 GPU 的当前机器上，实际编译并运行不依赖设备运行时的 C++ 单元测试，
  覆盖模型族识别、GGUF 分片与几何校验、prefill/split/batch 规划、共享规则和 logit bias。
- 假设与预期：这些测试只使用 `include/` 中的主机头文件和标准库；用同一编译器直接编译后，应能暴露
  平台无关的编译错误和纯逻辑回归，即使不能证明 CUDA sm_75 目标可用。
- 为什么选这条路线：当前没有 `nvcc`，但仓库同时包含主机侧纯逻辑测试；先把所有可验证项转成真实编译结果，
  能把“静态看起来正确”和“当前机器确实通过”分开记录，成本也低。
- 改动与实验：使用 `g++ (Debian 15.2.0-17) 15.2.0 -std=c++20 -O2 -pthread -Iinclude`，逐项编译并运行
  `tests/core/model_family_test.cpp`、`tests/core/gguf_split_test.cpp`、`tests/core/gguf_geometry_test.cpp`、
  `tests/program/split_rules_test.cpp`、`tests/program/prefill_plan_test.cpp`、
  `tests/program/batch_read_test.cpp`、`tests/prefill/share_rules_test.cpp` 和
  `src/core/logit_bias_test.cpp`。构建产物只写入 `/tmp`。
- 结果：8 个测试全部编译并运行通过；模型族和 GGUF 测试输出 `ALL PASSED`/`all passed`，split、prefill、
  batch-read、share-rules 输出 `ok`，logit-bias 输出 `passed, including 103215 bans`。
- 判决：采纳为当前机器的主机侧验证证据。不得推广为 CUDA、GPU 或端到端推理验证。
- 生产状态与回滚：未改生产；本阶段无源码变更，只记录可复现证据。
- 学到什么：发行面收敛后仍有一组高价值的主机侧测试；它们能在无 GPU 环境中守住格式、边界和规划逻辑。
- 下一步与理由：继续筛选同样不依赖 CUDA 运行时的测试，优先覆盖线程、存储、会话和多线程资源生命周期。

## I-20261027-05 · 关闭后的 MCP HTTP 响应不会写回旧会话

- 目标 / 指标：`HttpTransport.close()` 执行时，已在 `urlopen` 中等待的响应即使随后返回，也不能更新
  session 或完成调用槽；关闭后的新 POST 必须立即失败。
- 假设与预期：上一个阶段中断了启动等待，但 `_post()` 在 `urlopen` 返回后没有重新检查关闭状态，
  因此迟到的响应仍可能写入结果，形成旧会话复活。
- 为什么选这条路线：这是关闭竞争的第二半，可用阻塞假 `urlopen` 精确复现，不需要外部 MCP server。
- 改动与实验：`_post()` 在进入请求、`urlopen` 返回和确认结果三处检查关闭状态；关闭后的迟到答案转为
  `McpError("the MCP server is closed")`；新增迟到响应回归。
- 结果：`python3 -m pytest serve/test_mcp.py -q` 为 `27 passed / 8 subtests passed`；新增用例断言关闭后
  worker 结束、等待槽被唤醒、没有写入 `msg`，错误为明确的 closed；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。关闭是单向状态：迟到的成功结果不能覆盖关闭结果。
- 生产状态与回滚：未改生产；MCP 可选功能内的关闭竞争修复。
- 学到什么：取消只能缩短等待，不能自动丢弃已经发起的 I/O；异步结果写回前必须再次验证生命周期状态。
- 下一步与理由：继续检查其他“后台线程完成后写回对象”的路径，尤其是 HTTP、会话保存和重载。

## I-20261027-04 · MCP 启动与关闭竞争可中断，stdio 读线程随进程退出

- 目标 / 指标：服务器关闭时，尚在 `initialize` 或工具列表阶段的 MCP 启动线程不能继续等待完整启动超时；
  stdio 子进程关闭后，stdout/stderr 读取线程必须结束；关闭后的 HTTP 会话不能再次被调用。
- 假设与预期：旧 `McpServer.close()` 需要先取得启动线程持有的锁，因此可能阻塞最长
  `start_timeout_s`；stdio 传输虽关闭管道，但没有保存和 join stdout 读线程。
- 为什么选这条路线：这是明确的关闭竞争和线程泄漏，能用阻塞假传输在无网络、无 MCP SDK 的环境中复现。
- 改动与实验：给每个 MCP server 增加关闭事件；关闭直接打断当前 transport，不等待启动锁；启动完成或失败后
  再检查关闭状态；stdio 保存两个读线程并在关闭后有界 join；HTTP transport 关闭后标记为不可用；
  `McpHub.close()` 在关闭所有 server 后对启动线程统一执行 3 秒 join；新增“启动阻塞时关闭”回归。
- 结果：`python3 -m pytest serve/test_mcp.py -q` 为 `26 passed / 8 subtests passed`；新增用例证明
  `close()` 在阻塞 transport 下 2 秒内返回、启动线程结束且状态为 `stopped`；`compileall` 与
  `git diff --check` 通过。
- 判决：采纳。关闭仍保持 best-effort，但不再被长启动超时或启动锁拖住。
- 生产状态与回滚：未改生产；MCP 仍是可选功能，可用单个提交回退。
- 学到什么：关闭路径不能先获取由长时间启动任务持有的互斥锁；“先发取消信号，再有界等待”才能保证退出。
- 下一步与理由：继续检查 HTTP/stdio 子进程的独立后台请求，确认关闭后不会重新启动或写回旧会话。

## I-20261027-03 · 遥测采样线程在关闭时等待退出

- 目标 / 指标：服务器关闭后，后台 NVML/CPU/RAM 采样线程必须收到停止信号并完成退出，而不只依赖
  `daemon=True` 随解释器结束；重复关闭仍应安全。
- 假设与预期：`Telemetry.close()` 原有实现只设置 `Event`，调用方无法确认采样线程已经结束；在连续
  创建和关闭测试服务器时会留下短暂重叠的采样线程。
- 为什么选这条路线：这是纯 CPU 可验证的线程生命周期问题，改动局部，能直接改善测试隔离和退出确定性。
- 改动与实验：保存采样线程句柄；`close()` 设置停止事件后，在非线程自身调用时最多等待 2 秒；测试在
  关闭后直接断言线程已不再存活。
- 结果：`NvidiaTelemetry` 与 `TelemetryNoDisks` 为 `2 passed`，`NoLeakedSampler` 为 `1 passed`；
  `compileall` 和 `git diff --check` 通过。
- 判决：采纳。停止事件仍是非阻塞主路径，join 只给正常采样周期一个明确上限。
- 生产状态与回滚：未改生产；关闭时最多多等待一个采样周期，可用单个提交回退。
- 学到什么：后台守护线程也需要明确的生命周期终点；仅设置停止标志只能证明“已请求停止”，不能证明
  “已经停止”。
- 下一步与理由：继续检查持锁的后台任务和等待队列，优先处理可导致退出延迟或资源继续被占用的路径。

## I-20261027-02 · 看图服务的退出释放与 Linux 临时目录收敛

- 目标 / 指标：核查 `strata-vision` 的日志、工作目录和反复卸载/重启生命周期，删除发行树中已经失效的
  Windows `SystemDrive` 临时目录分支；验证失败时不能把日志句柄留给进程退出处理。
- 假设与预期：`Vision.close()` 必须保留日志和工作目录供 `restart()` 使用；只有最终 `shutdown()` 才能关闭
  自有日志并删除临时目录。服务启动时打开的日志若在编码器构造失败后没有关闭，则形成确定的句柄泄漏。
- 为什么选这条路线：它与上一阶段的图像任务清理属于同一类生命周期问题，可用纯本地单元测试验证，
  且不需要 GPU、模型或生产重启。
- 改动与实验：为 `Vision` 增加显式日志所有权；只有服务器打开的日志在 `shutdown()` 时关闭，卸载时仍保留；
  看图编码器或图像服务构造失败时关闭刚打开的日志；错误退出路径也调用图像服务 `shutdown()`；
  临时目录候选从 Windows 的 `SystemDrive` 改为 Linux `/tmp` 与 `/var/tmp`，并保持“路径无空格”回退。
- 结果：`serve/test_server.py -k 'VisionShutdown or VisionTempFiles'` 为 `6 passed / 2 subtests passed`；
  `serve/test_images.py` 为 `22 passed / 20 subtests passed`；`compileall` 与 `git diff --check` 通过。
- 判决：采纳。`close()`/`unload()` 保留重启所需资源，`shutdown()` 负责最终释放；不再创建旧的 Windows
  系统盘候选路径。
- 生产状态与回滚：未改生产；纯本地服务代码、测试和文档变更。
- 学到什么：同一个日志句柄在“暂时卸载”和“服务退出”两个生命周期中的所有权不同，测试必须分别覆盖，
  不能用一个 `close()` 同时承担两种语义。
- 下一步与理由：继续按“临时目录、日志、子进程、退出路径”审计剩余服务代码，再转向后台线程和并发锁。

## I-20261027-01 · 图像任务失败与服务器退出时不再遗留进程、目录和日志句柄

- 目标 / 指标：核查图像生成子进程的启动、失败、超时和服务器退出路径，确保没有残留任务目录、日志句柄
  或仍占用 GPU 的 `sd-cli`；不改变成功请求的 API 行为。
- 假设与预期：现有成功路径会清理任务目录，但 `Popen` 启动失败发生在清理块之前；服务退出也没有调用
  `ImageGen` 的释放逻辑。
- 为什么选这条路线：这是不依赖目标 GPU 的真实资源泄漏，能用假 `sd-cli` 和本地文件系统稳定复现，
  比继续做静态边界删除更容易验证。
- 改动与实验：把每个图像任务放进统一的 `try/finally` 清理边界；启动失败、超时和取消都回到清理；
  `sd-cli` 在等待超时后补发 kill 并等待；新增 `ImageGen.shutdown()`，关闭自有日志并在临时输出目录时
  删除目录，显式 `out_dir` 保持不动；服务器退出列表接入该释放方法。
- 结果：`python3 -m pytest serve/test_images.py -q` 为 `22 passed / 20 subtests passed`；新增覆盖自有
  临时目录、显式输出目录、重复关闭和启动失败无残留任务目录。
- 判决：采纳。成功请求的字节级约定和 HTTP 测试未改变；修复只收紧异常与退出生命周期。
- 生产状态与回滚：未改生产；纯本地服务代码和测试变更，可用单个提交回退。
- 学到什么：资源清理必须包围整个“创建临时目录 → 启动子进程 → 读取输出”序列；只在子进程启动后
  进入 `try` 会漏掉最常见的失败分支。
- 下一步与理由：继续审计同类 `mkdtemp`、日志句柄和长期后台线程，优先处理能用当前离线测试证明的问题。

## I-20261026-21 · 生产切换到 `6×256K`，容量与长上下文功能门通过

- 目标 / 指标：把生产上下文从 `192000` 提到 `262144`，同时保持 6 个 batch slot、INT8 KV、
  `kv_resident=32768` 和现有调度开关不变；用真实长请求与 6 路并发验证切换。
- 假设与预期：KV streaming 会把显存 K/V 窗口固定住，新增上下文主要增加 indexer、RoPE 和
  pinned host KV；expert cache 应小幅缩小，但 6 槽仍能启动。
- 为什么选这条路线：这是用户明确要求的容量升级，不需要改代码；先做最小 config diff 和真实请求，
  比直接扩大 expert cache 或其他性能开关更能隔离风险。
- 改动与实验：建立 `/opt/ai/tmp/pre-ctx256k-20261026T075110Z/` 完整快照；只把 config 的
  `max_context` 改为 `262144`，预设检查通过后原子安装并重启。服务在约 190 s 后 loaded；
  随后跑 `222,322` token 与 `253,633` token 两次 needle，以及 6 路短并发。
- 结果：两次 needle 均命中，墙钟 `147.2/152.7 s`；6 路短并发 `6/6` 成功。生产保持
  `batch_slots=6`、`kv_resident=32768`；expert slots `17728→17248`，expert cache
  `29010→28222 MiB`。启动后两卡空闲 `1005/1073 MiB`；长读后 RAM 可用约 `5.8 GiB`、swap 0。
- 判决：**采纳并已上线，mixed soak 待做**。功能门足以证明 256K 上下文实际可服务，但不足以证明
  长时间混合流量稳定。
- 预期被推翻：没有；expert cache 实际只缩小约 788 MiB（双卡合计），6 槽未因显存不足降级。
- 生产状态与回滚：health 200、`loaded=true`、engine `a09d78b1`、server `de09cffd`、
  config `ea1e2959`；回滚快照完整并通过 SHA-256 清单校验。
- 证据：L-088；仓库内 `docs/work/evidence/ctx256k-needle-222k-20261026.json`、
  `docs/work/evidence/ctx256k-needle-253k-20261026.json`；快照中的 `metrics-after.json`、
  `metrics-final.json`、`nvidia-smi-final.txt` 与 `free-final.txt`。
- 学到什么：`--kv-resident` 下上下文扩张不等价于整段 KV 进 VRAM；真实瓶颈需要同时看 expert cache、
  pinned RAM 和 conversation cache，不能只按显存估算。
- 下一步与理由：先跑 256K mixed soak，覆盖 32K/64K/96K/长读与短请求并发；P7 等该基线稳定后再从
  全新 A1 重跑。

## I-20261026-20 · P7 在 A1 首个请求期间中断，暂停而非进行中

- 目标 / 指标：验证 `STRATA_PF_WMMA=1` 能否让 96K 冷读的 engine read 与 wall 均改善 ≥3%，
  同时 A2 回到 A1 的 3% 内，并通过 parity、needle 与质量门。
- 假设与预期：P5 显示 `gemm gate/up` 是最大 phase；若 FP16 tensor-core prompt experts 在该
  形状更有效，完整请求应出现超过 3% 的同 boot 改善。
- 为什么选这条路线：P6 仅改善约 2.3%，继续调 attention 的收益不足；P7 是现有二进制中的
  env-only 开关，回滚成本低，并直接针对最大 GEMM phase。
- 改动与实验：预先写入判据并建立 `/opt/ai/tmp/pre-P7-pf-wmma-20261026T065341Z` 快照，随后
  启动 A1。用户在该臂首个请求期间中断，未产生有效 A1 JSONL/summary，B 与 A2 未执行。
- 结果：没有可比较的 engine read、wall、parity、needle 或质量结果；因此没有采纳/否决判决。
- 判决：**暂停/待做**。P7 不是进行中，也不是部分通过。
- 预期被推翻：没有；本次只形成“执行被中断”的事实，不能推断开关速度或正确性。
- 生产状态与回滚：只读核对 `strata.service` active、health 200，生产配置已恢复，未发现残余
  soak 或 P7 输出；P7 前快照保留。
- 证据：L-087；预注册判据 sha256
  `1ae40a8142a7a056e5d36c03902533c3119b896297f5510e4de0ca822ea650b6`。
- 学到什么：中断的 A1 不能作为部分样本复用；恢复实验必须重新建立干净的 A 基线。
- 下一步与理由：当前不自动恢复 P7。用户明确恢复目标后，从全新 A1 重跑完整同 boot A-B-A；
  在此之前只维护文档、记录和生产基线。

## I-20261026-19 · P6 Turing attention v2 只快约 2.3%，未过 3% 门槛

- 目标 / 指标：在同 boot A-B-A 中验证 `STRATA_PA_V2T=1` 是否能把 96K 冷读的 engine read 和
  request wall 都改善至少 3%，同时 A2 回到 A1 的 3% 内且正确性通过。
- 假设与预期：P5 的 `qsa attn` 中位为 3.422 s（10.5%）；替换 Turing 的 prompt-attention
  v1 暂存路径若有效，整段 96K wall 应出现可跨过噪声地板的改善。
- 为什么选这条路线：它是现有二进制中的 env-only 开关，只影响目标 phase，回滚成本低；
  比继续修改 host staging 或 lead-in 更贴近 P5 证据。
- 改动与实验：预注册 3 臂、每臂 3 个唯一 96K prompt，B 只增加 `STRATA_PA_V2T=1`。切换后从
  `/proc` 和 T-probe 证明运行环境，A1/B/A2 各跑同一顺序生成器；B 与 A2 运行后恢复生产。
- 结果：A1/B/A2 的 engine read 中位为 `37.3342/36.4583/37.5456 s`，wall 中位为
  `37.609/36.745/37.856 s`。B 相对 A1 分别为 `−2.346%/−2.297%`，相对 A1/A2 合并中位为
  `−2.622%/−2.617%`；A2 相对 A1 只漂移 `+0.566%/+0.657%`。三臂均 0 错误、needle 3/3，
  greedy parity 文本 sha 都是 `b17bc92de6ba8a97`。
- 判决：**未定，生产不采纳**。方向和正确性都正常，但未达到预先写下的 3% 改善门槛；
  没有足够证据把它写成稳定生产收益。
- 预期被推翻：即使 parity 完全一致，2.3% 的方向性收益仍可能是平台内波动；固定阈值必须
  在结果出来前保留，不能因为“看着更快”事后放宽。
- 生产状态与回滚：已恢复原配置并验证 T-probe `problems=[]`；engine `a09d78b1`、server
  `de09cffd`、launcher `34271059`、config `05c9d253`，health 200、`loaded=true`。
- 证据：L-086；预注册判据、三臂 JSONL/summary、三份 parity、B/A2/最终 T-probe。
- 学到什么：低风险开关能给出方向性信息，但不到 3% 就不应进入生产；下一步应直接瞄准 P5 中
  最大的 `gemm gate/up`，而不是在同一 1 s 范围内继续调 attention。
- 下一步与理由：P7 测试 `STRATA_PF_WMMA=1`，用相同 96K A-B-A 判断 FP16 tensor-core prompt
  experts 是否能触及最大 GEMM phase；若输出变化则必须追加质量门。

## I-20261026-18 · P5 把 96K 冷读定位到 GPU 计算，host staging 不是主瓶颈

- 目标 / 指标：解释 96K 冷 prefill 的约 38–42 s 墙钟，量化 21 个 phase、host staging 和 timing
  工装开销；要求 3 个唯一 prompt、零执行错误、needle 全对，并在恢复后保持 T-probe 无问题。
- 假设与预期：如果 host staging 是主瓶颈，phase 之外应有大量拷贝或等待；否则 GPU event timeline
  应占据大部分墙钟，成本集中在 attention、专家 GEMM 与固定 step。
- 为什么选这条路线：P4 已解决中短请求公平性，但 96K 冷读本身仍约 42 s；不先区分 host 与 GPU，
  继续调 staging/lead-in 只会重复已经否决的窗口实验。
- 改动与实验：临时启用 `STRATA_PREFILL_TIMING=1`，用唯一 96K prompt 跑 timing/plain A-B；旧
  `cold-prefill.py --96K` 生成 312,025 tokens 被 HTTP 400 拒绝，未计入结果，正式数据改用
  `mixed-soak.task_long`。恢复原配置后再跑 3 个 plain prompt 和 T-probe。
- 结果：timing 的 GPU timeline 中位 `32.653 s`、phase 总墙钟 `35.441 s`、host staging
  `0.341 s`；最大 phase 为 `gemm gate/up` 5.807 s（17.8%）、`embed+steps` 3.692 s（11.4%）、
  `qsa attn` 3.422 s（10.5%）、`gemm down` 3.031 s（9.3%）。timing/plain 的 engine read
  中位为 37.841/37.571 s，工装 `+0.72%`；两臂 3/3 needle 正确、零执行错误。
- 判决：**P5 结论成立**；96K 主成本在 GPU 计算，host staging 不是当前长读主瓶颈。timing 保留为
  诊断工装，不进入生产默认。
- 预期被推翻：原计划担心 timing 本身可能带来 2.5–8% 开销；同形状对照只有 `+0.72%`，在 3%
  噪声内，足以用于相位排序。
- 生产状态与回滚：已恢复生产配置；T-probe `problems=[]`，engine `a09d78b1`、server
  `de09cffd`、launcher `34271059`、config `05c9d253`，health 200、`loaded=true`。
  P5 前快照为 `/opt/ai/tmp/pre-P5-timing-20261026T061726Z/`。
- 证据：L-085；P5 timing JSONL/summary、plain JSONL 与恢复 T-probe，哈希见台账。
- 学到什么：96K 的固定成本不是单一“读写慢”，而是多个 GPU phase 叠加；优化必须命中真实 phase，
  并同时观察 wall、质量与恢复证据。
- 下一步与理由：启动 P6，只开 `STRATA_PA_V2T=1`，用同 boot A-B-A 验证 Turing prompt-attention
  v2 寄存器暂存能否降低 96K 的 `qsa attn` 成本；它是现有二进制 env-only 开关，回滚成本低。

## I-20261026-17 · P4 严格 A-B-A 通过：代码请求降 87.7%，长档代价低于 3%

- 目标 / 指标：在新引擎同一 boot 上确认 `1600 ms lead-in + STRATA_ADMIT_SHORT_FIRST=2048`
  是否真实改善 96K 邻居下的中等请求，同时让代码 ≤10 s、短 JSON ≤4 s，并守住 96K wave 与
  by_kind 单请求中位相对 A 臂均 ≤+3%。
- 假设与预期：A 臂没有短请求优先时，代码请求会被 96K 冷读压到约 41 s；B 臂应复制历史
  4–6 s 结果。代价是 96K 略慢，但必须同时低于 3% 的 wave 和单请求门限。
- 为什么选这条路线：新引擎自身 soak 与 staged 2048 已通过，但历史 P4 数据来自旧引擎；
  不做同一 boot 的 400 ms 无障碍对照，就无法把收益归因给 P4 而不是版本或 boot 噪声。
- 改动与实验：在 `/opt/ai/tmp/pre-P4-strict-20261026T054515Z` 建完整快照，固定判据文件
  sha256 `c3a208a6f37ba957153577159b360be1b0e33a792e932a67efba50f4caf56663`。
  A1/A2 移除短请求优先 drop-in，设 lead-in 400 ms；B1 恢复 `STRATA_ADMIT_SHORT_FIRST=2048`
  并设 lead-in 1600 ms；三臂都保持 staged 2048，各跑 5 个 fairness wave。每次切换都从
  `/proc` 核对实际 env、engine md5 和 health。
- 结果：

| 臂 | 代码 wall | 短 JSON wall | 96K by_kind | 96K wave |
|---|---:|---:|---:|---:|
| A1 | 41.173 s | 3.418 s | 41.009 s | 41.176 s |
| B1 | **5.074 s** | **3.980 s** | 42.092 s | 42.098 s |
| A2 | 41.202 s | 3.396 s | 40.913 s | 41.206 s |
| A 合并 / B 变化 | 41.1875 s / **−87.681%** | 3.407 s / +16.818% | 40.961 s / **+2.761%** | 41.191 s / **+2.202%** |

  三臂各 20 请求、5 波，HTTP/执行错误 0、意外 `length` 0、needle 失败 0。
- 判决：**采纳 P4 组合**。代码收益巨大，短 JSON 未越过 4 s，96K 两种口径都未越过 3%；
  但 96K 余量约 0.24 个百分点、短 JSON 余量约 20 ms，属于“通过但不应继续加码”的配置。
- 预期被推翻：短 JSON 并非继续下降，而是从 A 的 3.407 s 升到 3.980 s；它仍满足绝对门槛，
  但说明 P4 用短 JSON 尾延迟余量换了中请求完成时间，不能写成所有请求都加速。
- 生产状态与回滚：已恢复 B 配置；T-probe `problems=[]`，engine `a09d78b1` / `0.1.51`、
  staged 2048、lead-in 1600、短请求优先 2048、health 200。回滚快照
  `/opt/ai/tmp/pre-P4-strict-20261026T054515Z/` 的 6 个文件清单全部 OK。
- 证据：L-084；三臂 JSONL/summary 与最终 T-probe，哈希见台账。
- 学到什么：公平性组合的价值必须用“无障碍 A 臂”直接证明；只看候选自身绝对延迟会把
  boot 基线缺失误当成机制收益。配置已经接近两个门槛，后续变更需要更保守。
- 下一步与理由：结束 P4 参数线，进入任务 8 的发现轮。汇总本次 soak/fairness 的 queue、TTFT、
  decode、wall、expert cache 和 VRAM headroom，列出前三项用户可感知成本后只选一个实验；
  不重复已经否决的 QSA、groups=1、固定 read piece、S0b 或更长 lead-in。

## I-20261026-16 · 修复版 `0.1.51` 通过自身一小时 soak，P4 组合仍需严格归因

- 目标 / 指标：验证实际部署的修复版 `0.1.51` 能否在 1600 ms lead-in、staged 2048 和短请求
  优先 2048 的组合下连续运行一小时；要求零 HTTP/执行错误、零意外 `length`、needle 全对、
  swap 未使用，并且 RSS 与可用内存最终进入平台而不是持续恶化。
- 假设与预期：`a09d78b1` 已通过测试、输出 parity 和五臂 A-B-A；在真实 2/3/4 路混合流量下，
  它应保持与旧基线相同的稳定性，同时让 staged 读取和短/中请求公平性开关实际进入运行路径。
- 为什么选这条路线：旧引擎和旧 P4 候选的 soak 不能证明新二进制；部署后如果缺少自身 soak，
  就无法把生产基线与候选调度开关安全分开。
- 改动与实验：原子部署 engine `a09d78b1`，保持 server `de09cffd`、launcher `34271059`、
  config `05c9d253`；运行态启用 `STRATA_MP_STAGED_READ_MIN_TOKENS=2048`、
  `STRATA_PIPED_DECODE_LEADIN_MS=1600` 与 `STRATA_ADMIT_SHORT_FIRST=2048`。执行 3600 秒
  `mixed-soak.py`，随后跑 post-soak health 与 T-probe。第一次 T-probe 正确发现远端期望 profile
  仍是 4096；只同步这份无密钥 profile 后重跑，未放宽探针。
- 结果：618 请求、206 waves，HTTP/执行错误 0，意外 `finish_reason=length` 0，needle 失败 0；
  finish 为 530 个 `stop`、88 个 `tool_calls`。357 个资源样本中最大 running 与 outside slots
  都是 4。RSS 从启动期 `38.612` GiB 增至末值 `51.706` GiB，后三段中位约
  `51.33/51.65/51.81` GiB，最大值 `52.026` GiB 后回落；MemAvailable 末值 `7.477` GiB、
  最小值 `7.159` GiB，swap 全程 4096 MiB 未使用。分档 wall 中位：短 JSON `6.025 s`、
  1.85K 代码 `6.794 s`、32K/64K/96K `19.297/27.222/41.780 s`。
- 判决：**修复版引擎与 staged 2048 基线通过一小时稳定性门，可作为新生产基线**；
  P4 的 lead-in + 短请求优先组合仍是候选，必须在同一新引擎 boot 上补 400 ms 无障碍对照后
  才能写成无争议采纳。
- 预期被推翻：一小时混合 soak 的短 JSON 中位为 `6.025 s`，不能拿它替代已固定判据的 fairness
  arm 中约 4 秒结果；soak 证明稳定性，不自动证明单请求性能门槛。
- 生产状态与回滚：health 200、`loaded=true`；T-probe `problems=[]`，engine
  `a09d78b13c39378d1532a024b983d286`、version `0.1.51`，server
  `de09cffdac39ff150a689bd3a44cc242`、launcher `342710595d34936f40e40233f5b62837`、
  config `05c9d2539cd92b6c8e0b93ff8a48ce0e`。回滚快照仍为
  `/opt/ai/tmp/pre-v026-20261026T025109Z/`，清单已通过。
- 证据：L-083；`P4-newengine-soak2048-20261026T044100Z.*`、
  `T-probe-v026-postsoak-synced-20261026T054252Z.json` 与 profile 备份。
- 学到什么：启动后资源会先进入工作集，随后稳定在约 52 GiB RSS；判断泄漏必须看后段趋势，
  不能只看首尾差。部署门禁也需要把“运行态正确”和“期望 profile 正确”分别验证。
- 下一步与理由：同 boot 比较 A=`400 ms`、不启用短请求优先，B=`1600 ms` + 2048 短请求优先，
  两臂都保持 staged 2048；预注册代码 ≤10 s、短 JSON ≤4 s、96K wave 与单请求中位均不退 >3%。
  只有 A-B-A 通过才正式收口 P4，否则恢复 A 并保留已通过的引擎/阈值基线。

## I-20261026-15 · sm_75 合并引擎通过测试、正确性和五臂 A-B-A，进入部署与独立 soak

- 目标 / 指标：把已完成源码合并的 `0.1.51` 从“构建成功”推进到可部署候选；测试只允许外部资产缺失
  造成的显式例外，输出 parity、版本戳、运行路径和性能门必须通过。
- 假设与预期：先前 `0.1.51` 的吞吐回退来自具体 sm_75、TQ overlay 测试和构建脚本问题，不是整个
  合并版本不可用；修复后应恢复单路吞吐，并至少不伤 4 路聚合和 cold prefill。
- 为什么选这条路线：生产仍运行旧 `0.1.49.1`，继续在旧二进制上追 P4 会把结论锁在即将淘汰的基线上。
  先完成合并引擎门禁，才能把后续优化迁到维护中的源码。
- 改动与实验：修复 `pf_wmma_parity` 对 sm_75 共享内存容量的误判，查询真实
  `cudaDevAttrMaxSharedMemoryPerBlockOptin` 并拒绝不支持的 `WK=256` tile；修复 IQ tile 设置失败后
  污染 prompt 路径的 CUDA sticky error；为 `ggml_tq_overlay` 独立 CMake 脚本启用 `CMP0057`。
  构建后运行全量 CTest、server 离线测试、旧/新/旧/新/旧五臂 A-B-A、temperature 0 答案 parity
  和 high-effort reasoning 对照。
- 结果：CTest 为 `151 passed / 8 skipped / 1 failed`，唯一失败 `ple_parity` 是因为服务器缺少 Q2_0 PLE
  模型和捕获夹具，不是本轮代码失败；离线 server 为 `73 passed / 10 skipped`。五臂 A-B-A 合并中位：
  单路旧 `68.8`、新 `71.3`（`+3.6%`）；4 路旧 `130.4`、新 `130.0`（`-0.3%`）；
  cold prefill 约 `+0.4%`。temperature 0 的 answer hash 五次均为 `b17bc92de6ba8a97`；
  修复后引擎 md5 为 `a09d78b13c39378d1532a024b983d286`。
- 判决：**合并后 `0.1.51` 可部署候选通过测试、正确性与性能门禁**；`ple_parity` 与 8 个 skip
  作为外部资产/平台覆盖例外保留，不能写成“全测试绿”。先前把所有 0.1.51 吞吐回退归因于整个候选的
  结论撤回，具体历史回退仍需由本轮修复后的证据替代。
- 预期被推翻：high-effort reasoning hash 在旧引擎 A/A2 之间本身也会变化，因此不能作为该版本的
  正确性失败依据；temperature 0 答案 hash 和数值 parity 才是本轮可用的稳定门。
- 生产状态与回滚：本轮验证结束时仍恢复旧生产 `8d680c69`，health 200；随后再原子部署修复版。
  部署前快照 `/opt/ai/tmp/pre-v026-20261026T025109Z` 的 `MANIFEST.sha256` 全部通过。
- 证据：L-082；全量 CTest 日志、五臂 `arm-v026-*.log`、最终 A2 T-probe、答案 parity 输出。
- 学到什么：sm_75 的 opt-in shared memory 上限必须按设备查询，不能在启动时无条件为 `WK=256`
  设置 83 KiB；失败路径也要消费 CUDA error，否则会在后续 prompt 阶段制造看似无关的故障。
- 下一步与理由：先部署 md5 `a09d78b1` 并用运行路径 T-probe 证明版本，再在新基线上复测 P4
  1500/1550/1600 ms 与 staged 阈值，最后运行该二进制自己的 mixed soak。现有旧引擎/P4 历史 soak
  不能替代这一步。

## I-20261026-14 · `STRATA_QSA_SLOTS=1` 的交错复测不复现首次收益，否决

- 目标 / 指标：在 4/6 路真实 batch window 中确认 QSA slots 合并 launch 是否能稳定提高聚合 tok/s。
- 假设与预期：当前每行一套 launch 有固定开销，合并成一个窗口 launch 应减少 decode 调度成本；
  首次 6 路 on 对 off 为 151.9 对 146.8（+3.5%），值得继续验证。
- 为什么选这条路线：它是现成开关，不需要新 kernel；先用低成本 A-B-A 排除参数级误判，再决定是否投入内核工作。
- 改动与实验：同 boot、生产 6×192K 形状，A 为 off、B 为 `STRATA_QSA_SLOTS=1`。先做 4 路
  A-B-A，再按 6 路 B-A-B-A 交错；最终按相邻臂和合并方向判断，不按首次样本判断。
- 结果：4 路 off/on/off 为 125.7/114.6/115.5 tok/s，两轮都不支持 on；6 路
  B2/A3/B3/A4/B4 为 151.9/146.8/149.1/143.5/142.8。首次 +3.5% 未复现，B4 对 A4 反向 0.49%。
- 判决：**否决**在当前双卡 6×192K 形状默认开启 `STRATA_QSA_SLOTS=1`，同配置不再重试。
- 预期被推翻：4 路反而明显变慢；6 路收益只在第一个交错样本出现，属于 boot/采样顺序噪声。
- 生产状态与回滚：实验后已恢复 off 配置，QSA 未留在生产；A/B 快照在
  `/opt/ai/tmp/QSA-SLOTS-20261026T0023Z/`。
- 证据：L-081；`bench-results.jsonl` 和两份逐字节配置快照。
- 学到什么：并发越高不自动意味着合并 launch 更值钱；相邻交错臂能拆穿只出现一次的“提升”。
- 下一步与理由：停止 QSA slots 参数线，先处理合并结果的可部署性和 P4 公平性边界。

## I-20261026-13 · P4 一小时候选 soak：用户可见延迟大幅改善，但长档单请求门限仍有争议

- 目标 / 指标：把 96K 冷读邻居下的 41-token 代码请求从约 41.5 s 降到 ≤10 s，同时守住短 JSON、
  零错误和 96K 长档不超过基线 3%。
- 假设与预期：短请求首准入 2048 tokens 与 1600 ms lead-in 组合，可让中/短请求在长读前取得
  可解码槽；代价是 96K wave 略慢，但应落在 3% 内。
- 为什么选这条路线：P4-2000 ms 已证明单纯延长窗口不稳定；2048-token 准入优先直接处理日志中
  约 39 s 的 `slot_wait_ms`，1600 ms 只给已准入请求一个有界完成窗口。
- 改动与实验：systemd service 增加 `STRATA_ADMIT_SHORT_FIRST=2048`；生产 config 将 lead-in
  从 400 调到 1600，保留 750 ms 等待和 4096-token 最小冷读。运行 1 小时混合 2/3/4 路 soak，
  覆盖短 JSON、1.85K 代码、agent tool turn 和 32K/64K/96K needle。
- 结果：606 请求、202 waves，HTTP/执行错误 0、意外 length 0、needle 25/25。96K 邻居下代码
  wall 从 41.61 s 降到 4.424 s，短 JSON 从 40.83 s 降到 3.370 s。32K 四路 wave
  22.804 对 22.188 s（+2.78%），96K 四路 42.666 对 41.613 s（+2.53%）。
- 判决：**生产候选**。按 wave 判据，96K 在 3% 内；按 `by_kind` 单请求中位，候选 42.658 对
  P2 40.96 s（+4.14%），相邻 400 ms D2 也约 +3.21%。因此不能写成无争议采纳。
- 预期被推翻：短/中请求改善远大于预期，但长档没有同时落在所有合理口径的 3% 以内。当前门限
  对“按 wave”与“按单请求中位”给出不同裁决。
- 生产状态与回滚：候选已部署，health 200；T-probe 记录 engine `8d680c69`、server `de09cffd`、
  launcher `0eeee160`、config `05c9d253`。400 ms 完整回滚快照在
  `/opt/ai/tmp/pre-P3-ABA-20261025T213040Z/strata-022.json`。
- 证据：L-080；P4 candidate JSONL/summary、T-probe 和 P2/P3 相邻对照。
- 学到什么：wave 聚合会稀释单请求长档代价；公平性方案必须预先固定聚合与 per-request 两种判据，
  不能事后挑较宽的一种。
- 下一步与理由：先在合并后 HEAD 构建并验证新的 sm_75 引擎；随后同 boot 做 1500/1550/1600 ms
  边界 A-B-A，同时固定 96K 单请求中位门限，再决定候选保留或回滚。

## I-20261026-12 · 2000 ms lead-in 出现一次 5.3 秒代码响应，但重复运行不稳定，否决

- 目标 / 指标：在不把 96K wall 拖出 3% 门限的前提下，把 41-token 代码请求从约 41.5 秒降到
  ≤10 秒。
- 假设与预期：P3 的 400 ms 只推进约 30 个槽行，41-token 响应仍需更多 decode；把窗口增到
  2000 ms 应让已经进入槽的代码请求完成，并在其完成时自然提前结束。
- 为什么选这条路线：先验证最便宜的参数级假设，再决定是否改引擎调度。2000 ms 不改变读取长度，
  若稳定有效，回滚面小于新的剩余工作感知机制。
- 改动与实验：候选仅把 `STRATA_PIPED_DECODE_LEADIN_MS` 从 400 调到 2000。首次 B 运行后，
  在同 boot 做 400 ms A3 → 2000 ms B2 反向 A-B-A；两臂各 5 个相同的 4 路 fairness wave。
- 结果：首次 2000 ms 的代码请求为 5.254 秒，但 A-B-A 的 B2 中位回到 **41.800 秒**；5 波中第
  1/2/3/4 波代码 TTFT 约 41 秒，`slot_wait_ms` 中位 38.902 秒。B2 的短 JSON 中位 3.670 秒，
  96K 中位 41.749 秒；20/20 成功、0 错误、needle 5/5，5 次 lead-in 均触发。
- 判决：**否决 2000 ms 作为生产默认**。一次 5.25 秒来自请求先获得可解码槽的准入顺序，
  不是窗口长度带来的可重复收益；不能继续用固定加长窗口掩盖准入竞争。
- 预期被推翻：B2 第 0 波代码请求 5.288 秒，第 1/2/3/4 波约 41.9/42.1/41.9/41.6 秒。日志中
  同时存在 `slot_wait_ms` 与 TTFT 一起接近读时长的请求，说明部分短/中请求在长读结束前没有获得槽。
- 生产状态与回滚：已恢复 400 ms E 配置并 health 200；T-probe 复核 engine `8d680c69`、
  server `de09cffd`、launcher `0eeee160`、config `f82d1cfd` 和 11 项运行态 env。
- 证据：L-079；P4 首次 2000 ms、A3 400 ms、B2 2000 ms 的 JSONL/summary 与最终 T-probe。
- 学到什么：P3 的 400 ms 已稳定解决 14-token 输出；剩余问题首先是准入顺序，其次才是单次
  decode 窗口的完成判据。
- 下一步与理由：先做配置级 A-B-A，验证 `STRATA_ADMIT_SHORT_FIRST=2048` 能否让 73/1850-token
  请求先于 98K 冷读取得控制行；只有当配置不能稳定改善时才改服务端/引擎。

## I-20261026-11 · 400 ms lead-in 经 A-B-A 把短 JSON 从 40 秒降到 3.4 秒，代码请求仍待解

- 目标 / 指标：M10 短请求 wall ≤36.75 s，同时 96K 长请求不退 >3%、零错误且 needle 全对。
- 假设与预期：长 prefill 一开始就独占流水线；让已准入的槽先做一小段 decode，应能完成短响应，
  且 400 ms 有界等待不应显著拖慢读。
- 为什么选这条路线：P3 的固定 read piece 虽能让出，但 B/C 两臂已显示长档 +15.6%/+7.3%；
  concurrent-read lead-in 不改变读取长度，只调整开始顺序，风险和归因更可控。
- 改动与实验：先测无 lead-in A1/A2，再筛 400/1200 ms 与 read piece 32K/64K，最后做 A3→E2
  同 boot A-B-A。每臂 5 个 4 路 fairness wave，混合两条短 JSON、一条 1.85K 代码和一条 96K
  needle；T-probe 核对实际 engine `8d680c69`、server `de09cffd` 和 11 个运行态 env。
- 结果：A1/A2/A3 的短 JSON 中位 40.707/40.206/40.154 s，合并 40.206；E1/E2 为 3.404/3.400 s，
  **−91.5%**。96K 的 A 合并中位 40.381，E 为 41.2845，**+2.2%**。E 两臂共 40 请求，0 错误、
  0 length、needle 10/10；日志 10/10 证明 lead-in 实际执行。41-token 代码请求 E 中位 41.51 s，
  只比 A 的 41.218 s 慢 0.7%，没有获得公平性收益。
- 判决：**采纳 400 ms lead-in + 750 ms 等待槽进入可解码状态**；固定 read piece 与 1200 ms
  lead-in 否决。
- 预期被推翻：D 臂只有 400 ms lead-in、缺 750 ms 等待时一次都没触发；长读开始得比槽 decode
  更早，等待进入 `batch_on()` 是机制生效的必要条件。1200 ms 只把槽行从约 29 增到约 51，
  没有完成 41-token 代码请求，长档却升到 42.126 s。
- 生产状态与回滚：已恢复 E 配置并 health 200；engine `8d680c69`、server `de09cffd`、
  config `f82d1cfd`、launcher `0eeee160`。切换前快照
  `/opt/ai/tmp/pre-P3-ABA-20261025T213040Z/`。
- 证据：L-078；P3 A1/A2/B/C/D/E、A3/B2/F 的 JSONL/summary、五个 T-probe 和
  `T-probe-P3-adopted-final.json`。
- 学到什么：有界“先完成已有工作”比把长读切碎更便宜；但固定时间窗只对短输出公平，必须以
  剩余工作而不是单纯延长时间作为下一机制。
- 下一步与理由：进入 P4，比较按在途请求剩余 token/可完成性选择 yield 点；不重复 1200 ms 固定窗。

## I-20261026-10 · 一小时混合 soak 通过稳定性，下一个瓶颈转为长 prefill 挤压短 decode

- 目标 / 指标：验证 HC_Q8 + 共享 RoPE 在 2–4 路混合、32K/64K/96K 长上下文下无错误、无持续资源恶化，
  且 4 路固定吞吐不相对 130.6 回退超过 3%。
- 假设与预期：受控短 bench 的收益应在混合流量中保持；长时间长读若无泄漏，RSS/MemAvailable 会进入平台期。
- 为什么选这条路线：L-075 只证明干净 4 路 bench；RoPE 也一直没有跨小时缓存、RAM exchange 和长上下文证据。
- 改动与实验：新增 `mixed-soak.py`，3600 秒内交替 2/3/4 路，混合短 JSON、1.85K 代码、agent tool
  turn 和 32K/64K/96K 冷 needle；10 秒采样 metrics/RSS/GPU/engine log。前后各跑固定 4 路 n=6，
  后置因首块离散补跑第二块。
- 结果：622 请求、207 waves、0 错误、0 意外 `length`、0 needle 失败；524 stop、98 tool_calls。
  26 次 96K 中位 41.613 秒、最大 41.980。RSS 49.6→53.3 GiB，MemAvailable 11.0→7.31 GiB
  （最低 7.04），swap 全程未用；expert hit 中位 100%。4 路前置 123.4；后置两块 125.2/134.2，
  合成 129.7，相对 130.6 为 −0.7%；单路后置合成 74.9，相对前置 76.8 为 −2.5%。
- 判决：P2 稳定性通过，HC_Q8 + RoPE 保留。混合吞吐不能单看 completion tok/s，因为冷 prefill 主导。
- 预期被推翻：真实混合里最刺眼的问题不是平均吞吐，而是 96K prefill 邻居把短请求 wall 中位从
  短波形的约数秒拉到 40.83 秒；TTFT 仍只 0.63 秒，说明是 decode 被挤压而非准入。
- 生产状态与回滚：未重启、未改部署；engine `cbed3c31`、server `de09cffd`、config `6630c920`，health 200。
- 证据：L-077；P2 JSONL/summary、三块 bench、T-probe 与工具源码哈希。
- 学到什么：吞吐、准入和已完成请求的尾延迟必须分开看；长 prefill 共存是下一项主指标。
- 下一步与理由：P3 静态核实发现 `STRATA_PIPED_READ_PIECE` 才是在 piped groups 中每 piece 后让槽 decode
  的开关；单独降 `--prefill` 不等价。下一轮用同 boot A-B-A 验证 32768-token piece + 400/100 ms budget。

## I-20261026-09 · 把已采纳 env 变成运行态部署门禁

- 目标 / 指标：合并后再次丢失 HC_Q8 这类优化时，部署检查必须失败，而不是继续 health 200。
- 假设与预期：固定关键环境 profile，并从 `/proc/<engine-pid>/environ` 精确核对，能低成本发现配置漂移。
- 为什么选这条路线：P1 已证明单个 env 可造成 13.3% 的受控吞吐差，且这次漂移没有被旧 health/hash 检查发现。
- 改动与实验：新增 `production-env-2080ti.json` 和 `--expect-env-file`；缺键、错值均失败；补两正三负回归。
- 结果：专项 6 passed、相关 32 passed、offline preflight PASS；现网门禁精确匹配 7 个关键开关，0 problems；
  engine `cbed3c31`、6×192K、17728 expert slots、health 200。
- 判决：采纳为后续每次部署/实验恢复后的运行态检查；文档、状态和证据台账已同步。
- 预期被推翻：无；门禁只覆盖运行态，模板文件本身仍需由版本控制保护。
- 生产状态与回滚：未重启、未改服务；工具改动可单独回退，不影响现网推理。
- 证据：L-076；`/opt/ai/logs/T-probe-P1-env-gate-final.json`
  sha256 `3413a47b9669c1905c799986c691a384f746b209af0fa1636251ae7928d75cf1`。
- 学到什么：性能优化证据必须同时收口到可执行门禁，否则下一次合并仍可能静默回退。
- 下一步与理由：进入 P2 真实混合流量与受控长上下文 soak，观察 HC_Q8/RoPE 的吞吐、hit、交换和错误。

## I-20261026-07 · 找回合并丢失的 HC_Q8：受控 4 路越过 130，质量门通过

- 目标 / 指标：M1 的 4 路聚合 ≥130，同时确认上游融合没有丢失既有生产优化。
- 假设与预期：现网 env 缺 `STRATA_HC_Q8=1`，但旧台账和旧快照证明它曾在生产；恢复后应回到 +3–7% 区间。
- 为什么选这条路线：这是配置漂移而非新调参，风险可回滚，且直接命中旧的两对独立 A-B 证据。
- 改动与实验：A4→B1→A5→B2→A6 交错，每臂同 6×192K、groups=2、4 路 n=6；T-probe 验证 env 和运行 hash；
  启动日志确认每卡 0.31 GiB Q8 投影；另用相同 13 项自动质量集跑无 HC/HC 两臂。
- 结果：A 四臂聚合 114.5/116.0/128.5/111.7（中位 115.25），B 两臂 132.5/128.7（中位 130.6，+13.3%）。
  单路 A 中位 73.0、B 中位 72.2（−1.1%）；质量两臂都 11/11，全部 `stop`，无格式或循环退化。
  expert slots 18194→17728（−2.6%），6×192K 仍成立。
- 判决：恢复并采纳 HC_Q8；P1 受控目标达到，但 B2 为 128.7，仍标记真实流量待验。
- 预期被推翻：上游合并不一定保留生产 config 的已采纳 env；必须把生产配置漂移纳入回归。
- 生产状态与回滚：HC_Q8、groups=2、split24、6 slots、engine `cbed3c31`、server `de09cffd`，health 200；
  无 HC 配置快照和候选配置均保留。
- 证据：L-075；四轮 n=6 JSON、六份 T-probe、两臂 `responses.json`。
- 学到什么：性能数据必须与生产配置来源一起审计；相同二进制和 1–2 个 env 差就能造成 13% 级别差异。
- 下一步与理由：进入 P2，先为已采纳 env 补回归测试，再做真实流量/长上下文 soak，避免只凭干净 4 路 bench。

## I-20261026-08 · profile 定位双卡长杆；层切分 25/26 再次否决

- 目标 / 指标：把受控 4 路剩余成本拆成每阶段 GPU event 时间，验证是否值得重新配平层切分。
- 假设与预期：stage 2 更重约 15%，把一层从 CUDA1 移到 CUDA0 可能收回 3–5%。
- 为什么选这条路线：这是现有诊断开关和单个配置字段，不需要构建；若成功可直接影响所有并发请求。
- 改动与实验：`STRATA_VERIFY_PROFILE=1` 跑 n=6；随后 A3 split24 → split25 → A4 split24 → split26。
- 结果：stage 1 为 11.4–13.4 ms/window，stage 2 为 13.2–15.4；stage 2 主项是 VRAM experts、waitA、
  head、out-proj 和 q8+qkv。split25 对 A 中位仅 +2.5%，split26 为 −4.5%。
- 判决：profile 采纳为诊断结论；split24 保留，不再扫切分。
- 预期被推翻：stage 2 的 waitA 是流水线交接空档，简单搬层会增加其他成本，不是可回收纯利润。
- 生产状态与回滚：profile env 已移除；split24 和原 config 已恢复。
- 证据：L-074；profile 日志和 split25/26 四臂 JSON/T-probe。
- 学到什么：同类历史结论仍可被新生产形态快速复核，但低于 3% 不做方向性采纳。
- 下一步与理由：不再调层切分，转入 HC_Q8 恢复与质量/真实流量验证。

## I-20261026-06 · batch-MTP 没补回串行窗口，流水线默认进一步坐实

- 目标 / 指标：验证 groups=1 的每槽 MTP 是否能补偿并发吞吐，并保护 6×192K 显存。
- 假设与预期：每槽各生成一个 MTP proposal，若接受率约 80%，可能补回 groups=1 相比流水线损失的 11.6%。
- 为什么选这条路线：它只改配置且代码明确支持 groups=1 + batch-MTP；这是串行路径最后一个高价值开关。
- 改动与实验：生产 groups=2 → groups=1 + `--batch-mtp` n=6；T-probe 验证运行 hash、groups、6 slots，
  启动日志验证 6 个槽 drafter 与显存；结束后恢复 groups=2 并再次 T-probe。
- 结果：聚合仅 91.3 tok/s，比 groups=1 再低 17.7%、比 groups=2 约低 25%；TTFT 392 ms，但 prompt 851.5 ms、
  每流 decode 24.8 tok/s、wall 17.5 s。draft 接受率 80.71%，experts slots 18194→18027。
- 判决：否决；流水线 groups=2 是明确更强的生产默认。
- 预期被推翻：接受率正常不代表窗口吞吐提高；串行双行验证、提交和阶段切换吞掉了 MTP 的收益。
- 生产状态与回滚：groups=2、6×192K、engine `cbed3c31`、server `de09cffd` 已恢复；候选配置与两份 T-probe 保留。
- 证据：L-073；`P1-groups1-batchmtp-B2-n6`。
- 学到什么：性能开关必须按端到端 wall 和窗口吞吐验收；acceptance 只是必要条件，不是吞吐结论。
- 下一步与理由：启用 groups=2 的低采样 batch telemetry，先验证采样无 >3% 回退，再定位流水线内部空隙。

## I-20261026-05 · batch-groups 串行窗口单路略快，但 4 路吞吐回退 11.6%

- 目标 / 指标：M1 的 4 路聚合吞吐和每请求 prompt/decode/TTFT/wall 成本。
- 假设与预期：当前显式 groups=2 走两卡流水线；groups=1 可能靠更完整窗口和更短准入等待提高吞吐。
- 为什么选这条路线：它是 P1 第一个只改配置、可快速回滚的结构变量，且新旧引擎同二进制，不引入代码风险。
- 改动与实验：增强 bench 保存逐请求 metrics/timings；A1 groups=2 n=6 → groups=1 n=6 → A2 groups=2 n=6。
  每臂 T-probe 核对实际 engine/server、slots 和 groups；三臂都是 30 个本基准请求、0 个外来请求。
- 结果：A1/A2 的 4 路聚合 127.2/120.4（合并中位 122.1），groups=1 为 110.9，低 11.6%。groups=1
  的 TTFT 340.5 ms、slot 等待 168.0 ms 更低，但 prompt 686.6 ms（A 约 510）、每流 decode 30.4 tok/s
  （A 为 32.8–33.8）、wall 14.4 s（A 为 12.6/13.3）全面变差；单路 79.7 对 A 的 76.8/76.0。
- 判决：groups=1 否决；生产恢复 groups=2。
- 预期被推翻：更低的准入等待不等于更短任务完成时间；双卡串行窗口在单路略快，但并发损失远大于收益。
- 生产状态与回滚：engine `cbed3c31`、server `de09cffd`、6 slots、192K、RoPE 共享、groups=2；
  `pre-P1-groups1-20261025T173608Z` 快照和 groups=1 候选配置均保留。
- 证据：L-072；`P1-groups2-A1-n6`、`P1-groups1-B1-n6`、`P1-groups2-A2-n6` 三份 JSON 记录及两份 T-probe。
- 学到什么：bench 必须同时看 TTFT 与 wall/decode；只盯准入等待会选错调度。历史 A4000 上的串行收益
  不能外推到 2080 Ti + 本工作负载。
- 下一步与理由：测试 groups=1 + `--batch-mtp`，确认每槽投机能否补回串行窗口损失；若不赢，现有流水线
  已是更强默认，继续做窗口级成本剖析而不是重复同形开关。

## I-20261026-04 · C1 边界收口：满槽损失在新请求等待，不在邻居长静默

- 目标 / 指标：M5 满槽首 token、M8 晚到请求准入，并明确 6/8 槽容量取舍。
- 假设与预期：满槽时新请求会等待一个 slot；邻居解码应继续，若邻居也停顿则优先查调度。
- 为什么选这条路线：C1 是当前最直接的用户等待问题，且不需要改引擎；先测边界能决定默认槽数和下一步。
- 改动与实验：新增 `probe_slot_admission.py`，门控全部 slot 非 idle 后发唯一短请求；同时记录响应 metrics、
  `running/outside_slots`、客户端首/第二 token 和邻居静默。另补 `slot_wait_ms` 并部署 server 补丁。
- 结果：6 槽 3/3 满槽，slot 等待中位 11.422 s、TTFT 11.839 s；8 槽为 13.885/14.375 s。
  邻居静默 6 槽 0.481 s、8 槽 0.552 s；第二 token 都约 0.06 s。默认保持 6 槽。
- 判决：C1 采纳完成；`slot_wait_ms` 采纳并部署；8 槽只保留容量预设。
- 预期被推翻：原 `queue_ms=0` 不是无等待，而是 schema 只统计 server 排队；真实 slot 等待为 11–15 s。
- 生产状态与回滚：恢复 6×192K，server `de09cffd`、engine `cbed3c31`、health 200。server/config 快照及补丁完整。
- 证据：L-070/L-071；C1 6/8 槽 JSON、两臂 T-probe 和 `C1-s2-slot-wait-verify`。
- 学到什么：并发容量增加会拉长新请求准入；延迟归因必须把 server queue、slot wait、prefill、decode 分开。
- 下一步与理由：进入 P1；4 路聚合 113.6 tok/s 仍低于 130 目标，吞吐差距比继续重复满槽测试更有用户价值。

## I-20261026-03 · S0b 负结果与 RoPE 共享采纳：吞吐目标转向准入延迟

- 目标 / 指标：M4 长读邻居停顿、M1/M2 吞吐，以及显存和 expert cache 容量。
- 假设与预期：修复 `staged_source` gate 后，读块会让出解码，邻居静默应显著下降；共享只读 RoPE 应省显存
  且不改变 token 算术。
- 为什么选这条路线：S0b 是当前用户损失最大的候选；RoPE 已有底层接口，接线成本低、生命周期可证明、
  可独立回滚，是同期最高置信度的资源优化。
- 改动与实验：用真实 `concurrent_reads` 和静默统计做 A-B-A；0.1.51 做 n=6 吞吐；在现役 0.1.49.1
  打 opt-in batch slot RoPE 共享，双卡 6×192K 测吞吐、parity、冷 prefill，再用 T-probe 验证运行文件。
- 结果：S0b ON 邻居静默中位 7.09/7.59 s，OFF 7.41 s，旧引擎 7.08 s；路径 3/3 触发但没有用户收益。
  0.1.51 单路 68.0 tok/s、聚合 113.1，低于旧引擎 74.7/115.55。RoPE 双卡省 562.4 MiB，expert slots
  17872→18194；6 槽 4 路单路 75.1、聚合 113.6，差在噪声内；parity 和冷 prefill 通过。
- 判决：S0b 否决并回滚；0.1.51 不替换生产；RoPE 共享采纳为 opt-in 并部署。
- 预期被推翻：并发读“执行”不等于“用户等待下降”；省显存也未自动换来短测 tok/s。
- 生产状态与回滚：现役 engine `cbed3c31`，server `b6b695a1`，health 200；回滚到 `84a0d410` 的快照完整。
  补丁、原始源码和哈希已固化到 `tools/server/patches/rope-share-0.1.49.1.patch`。
- 证据：L-068、L-069；S0b 四份 JSON、RoPE 吞吐/parity/cold 日志和
  `T-probe-rope-share-20261025T170108Z.json`。
- 学到什么：性能实验必须同时回答“机制是否执行”和“用户是否少等”；资源收益与吞吐收益要分开判定。
- 下一步与理由：进入 C1，先用同一生产 boot 测 6 槽满槽的第 7 个请求，明确 queue、TTFT、wall 和邻居静默。

## I-20261026-02 · T-probe 改为读取实际运行文件，消除假部署证据

- 目标 / 指标：版本戳中的 server hash 与 markers 必须来自正在运行的 `serve/server.py`。
- 假设与预期：生产目录和 launcher 实际运行的 port-v1 目录不同；按固定生产路径 hash 会读错对象。
- 为什么选这条路线：S0b 即将部署新引擎，错误的部署证据会让后续性能结论不可信；成本低、风险低。
- 改动与实验：从 `/proc/<pid>/cmdline` 解析 running engine 与 server 路径，再 hash 并提取 markers；
  `check()` 断言 running path 与 hashed path 一致。新增 3 个负例/正例测试。
- 结果：`3 passed`；现网输出从生产目录的旧 server `a3c7df58` 改为实际运行的 port-v1 `b6b695a1`，
  `BCANCEL_DRAIN_S`、strict BRDY 等 markers 均命中。
- 判决：采纳。
- 预期被推翻：无；证实现网 server 并非生产目录文件。
- 生产状态与回滚：只读探针；未重启、未改生产。
- 证据：L-067；`/opt/ai/logs/T-probe-after-20261026T0010Z.json`。
- 学到什么：部署验证必须从 `/proc` 的实际 argv 出发，不能从“默认目录”推断。
- 下一步与理由：继续 S0b；新引擎构建完成后先用 T-probe 证明运行 hash，再做 A-B-A。

## I-20261026-01 · S2 每请求时延指标采纳，长读分析获得统一口径

- 目标 / 指标：确认流式与非流式响应都携带 `queue_ms / ttft_ms / itl_ms_mean / wall_ms / tokens`，且时间关系单调。
- 假设与预期：现有实现已存在；缺的是回归证据，补测成本远低于直接改调度。
- 为什么选这条路线：它是 S0b 的必要观测工装，且 mock 测试不占 GPU、不需重启生产。
- 改动与实验：运行 `tools/server/tests/test_mp_timings.py`，覆盖流式最终 chunk 和非流式 body；断言
  `queue <= ttft <= wall`、token 数一致、多 token 时有正的 ITL。
- 结果：2 passed；另跑 staged cancel 回归 1 passed。
- 判决：采纳。
- 预期被推翻：无；不需要修改 server 实现。
- 生产状态与回滚：未改生产；纯测试验证。
- 证据：L-065；`python3 -m pytest tools/server/tests/test_mp_timings.py -q`。
- 学到什么：S2 已不是工程缺口；继续改 metrics 会挤占真正的用户延迟优化。
- 下一步与理由：进入 S0b，先验证并发读实际触发，再比较邻居停顿与吞吐。

## I-20261025-01 · 规则系统从流程驱动改为自主优化闭环

- 目标 / 指标：让 AI 能独立选择最优路线、执行实验、复盘并继续，而不是逐项填 Gate。
- 假设与预期：当前问题不是规则数量不足，而是同一规则散落在多份文档；合并后决策速度和一致性应提高。
- 为什么选这条路线：规则冲突已经直接影响状态、队列和重启策略，继续增加条款会放大噪声。
- 改动与实验：以 `RULES.md` 为唯一协议；新增 `ROADMAP.md` 和 `ITERATION_LOG.md`；把旧计划、长交接和旧日志移入
  `archive/`；把预检限制收敛到状态/队列一致性。
- 结果：主入口剩下一套规则、一个当前状态、一个路线组合、一个队列、一个复盘入口；历史证据仍可追溯。
  活跃路由由约 800 行压缩为约 150 行；状态/队列预检 PASS；相关单元测试 25 passed；活跃文档链接缺失 0。
- 判决：采纳。
- 预期被推翻：原规则试图靠 11 字段 Gate 和固定轮次保证质量，实际制造了重复、冲突和维护成本。
- 生产状态与回滚：未改生产；文档变更可在 git 中整体回退。
- 证据：`RULES.md`、`ROADMAP.md`、`STATE.md`、`WORK_QUEUE.md`、`archive/`；
  `python3 tools/server/preflight.py --offline` → PASS；
  `python3 -m pytest tools/server/tests/test_mp_preflight.py tools/server/tests/test_mp_round_gate.py -q` → 25 passed。
- 学到什么：约束应放在结果、安全和真实证据上；过程模板只在能降低决策成本时使用。
- 下一步与理由：验证 S2 的每请求 metrics。它成本低、现有实现已存在，并能给 S0b 的长读停顿提供清晰归因。
