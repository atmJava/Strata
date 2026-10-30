#!/usr/bin/env python3
"""Run the real Strata engine under Nsight Systems or Nsight Compute.

The server starts its engine from the config's `exe` field. Point that field at this
wrapper to make the profiler the engine's direct parent, without changing the server,
model, flags, stdin/stdout protocol, or runtime environment.

Required environment:

  STRATA_PROFILE_REAL_ENGINE   absolute path to the engine binary
  STRATA_PROFILE_OUTPUT        report path without an extension
  STRATA_PROFILE_MODE          `nsys` or `ncu`

Optional NCU environment:

  STRATA_NCU_KERNEL_REGEX      demangled kernel regex; omit only for an explicit trace-all run
  STRATA_NCU_LAUNCH_SKIP       launches to skip before the count, default 1
  STRATA_NCU_LAUNCH_COUNT      launches to collect, default 1
"""
from __future__ import annotations

import os
import pathlib
import sys


NSYS = "/usr/local/bin/nsys"
NCU = "/usr/local/cuda-13.0/bin/ncu"


def engine_command(environment: dict[str, str], args: list[str]) -> list[str]:
    engine = pathlib.Path(environment.get("STRATA_PROFILE_REAL_ENGINE", ""))
    output = environment.get("STRATA_PROFILE_OUTPUT", "").strip()
    mode = environment.get("STRATA_PROFILE_MODE", "").strip().lower()
    if not engine.is_absolute() or not engine.is_file() or not os.access(engine, os.X_OK):
        raise ValueError("STRATA_PROFILE_REAL_ENGINE must name an executable absolute path")
    if not output or not pathlib.Path(output).is_absolute():
        raise ValueError("STRATA_PROFILE_OUTPUT must be an absolute path")
    if mode == "nsys":
        return [
            NSYS,
            "profile",
            "--trace=cuda,nvtx,osrt",
            f"--output={output}",
            "--force-overwrite=true",
            str(engine),
            *args,
        ]
    if mode == "ncu":
        skip = environment.get("STRATA_NCU_LAUNCH_SKIP", "1")
        count = environment.get("STRATA_NCU_LAUNCH_COUNT", "1")
        if not skip.isdecimal() or not count.isdecimal() or int(count) < 1:
            raise ValueError("NCU launch skip/count must be whole numbers and count must be positive")
        command = [
            NCU,
            "--target-processes",
            "all",
            "--replay-mode",
            "kernel",
            "--clock-control",
            "none",
            "--cache-control",
            "none",
            "--section",
            "SpeedOfLight",
            "--section",
            "Occupancy",
            "--section",
            "WarpStateStats",
            "--section",
            "MemoryWorkloadAnalysis",
            "--kernel-name-base",
            "demangled",
        ]
        regex = environment.get("STRATA_NCU_KERNEL_REGEX", "").strip()
        if regex:
            command.extend(["--kernel-name", f"regex:{regex}"])
        command.extend(
            [
                "--launch-skip",
                skip,
                "--launch-count",
                count,
                "--export",
                output,
                "--force-overwrite",
                str(engine),
                *args,
            ]
        )
        return command
    raise ValueError("STRATA_PROFILE_MODE must be nsys or ncu")


def main(args: list[str]) -> int:
    try:
        command = engine_command(dict(os.environ), args)
    except ValueError as exc:
        print(f"ncu-engine-wrapper: {exc}", file=sys.stderr)
        return 2
    os.execvpe(command[0], command, dict(os.environ))
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
