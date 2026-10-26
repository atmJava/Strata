# 自主迭代复盘

> 每次形成判决后，在最上方追加一条复盘。格式见 [`RULES.md`](RULES.md) §6。
> 完整证据在 [`EVIDENCE_LEDGER.md`](EVIDENCE_LEDGER.md)；当前断点在 [`STATE.md`](STATE.md)。

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
