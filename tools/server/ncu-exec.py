#!/usr/bin/env python3
"""受控 NCU 启动器：把先前抓下来的 server argv/env 原样重启在一个 profiler 之下。

定位：`docs/superpowers/plans/2026-10-27-resource-utilization-optimization.md` 的 Task 4。
NCU 不能附加到现役生产进程，所以先在生产还跑着时把 `/proc/<pid>/cmdline` 与
`/proc/<pid>/environ` 存成 JSON，停服后用本脚本 `execvpe` 出同一个进程形状，
让 profiler 从进程起点就开始采集。调用方是 `ncu` 或 `nsys profile`，它们把自己的
参数留在 argv 前面，因此这里只接受一个参数：runtime JSON 的路径。

维护者需要先知道的两件事：
1. runtime JSON 里含服务环境变量（可能有 API key），必须是 0600 或更严，否则拒绝启动；
   这个检查只在 POSIX 上有意义，Windows 上没有权限位。
2. 本脚本用 `execvpe` 替换自身，不 fork。任何"启动后再做点什么"的想法都会让 profiler
   丢掉进程起点，不要加。
"""
from __future__ import annotations

import json
import os
import pathlib
import stat
import sys


def load_runtime(path: pathlib.Path) -> tuple[list[str], dict[str, str]]:
    """读取抓下来的启动参数与环境。

    参数与权限要求来自 Task 2 Step 3：那份 JSON 由 `/proc` 直接 dump，含服务环境，
    组或其他用户可读就意味着凭据泄漏给了同机的其它账号。

    @param path runtime JSON 路径，内容形如 `{"argv": [...], "env": {...}}`。
    @return (argv, env) 二元组，argv 至少一项且全为字符串，env 为字符串到字符串的映射。
    @throws ValueError 权限过松、argv 为空或含非字符串、env 不是字符串映射时抛出。
    """
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError("runtime file permissions must be 0600 or stricter")
    data = json.loads(path.read_text(encoding="utf-8"))
    argv = data.get("argv")
    env = data.get("env")
    if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
        raise ValueError("argv must be a non-empty string list")
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise ValueError("env must be a string mapping")
    return argv, env


def main(argv: list[str]) -> int:
    """替换当前进程为被抓下来的 server 命令。

    @param argv 传入本脚本的 argv（含脚本名）。多一个或少一个参数都算用法错误，
        因为 profiler 会把它自己的参数留在前面，静默忽略会换来一个"看起来跑起来了"
        但其实没被采集的 server。
    @return 只有 execvpe 失败才会走到返回值，正常路径不返回。
    """
    if len(argv) != 1:
        print("usage: ncu-exec.py RUNTIME.json", file=sys.stderr)
        return 2
    command, environment = load_runtime(pathlib.Path(argv[0]))
    os.execvpe(command[0], command, environment)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
