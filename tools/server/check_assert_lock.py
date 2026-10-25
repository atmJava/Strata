#!/usr/bin/env python3
"""check-assert-lock.py - V1-4: the decode-attention shape is locked, not a tuning knob.

`src/kernels/cuda/qsa_decode_attn.cu` has `static_assert(CHUNK == 64 && THREADS == 256, ...)`: on this
card 128/256 and 256/256 produced wrong output (rel RMS 13-35x) and 32/128 faulted
(`docs/VLLM_PORT_PLAN.md` §P4), so the only build that may exist is 64/256.  A comment saying so is not
a lock; this script proves the compiler refuses the other shapes.

It does not build the project.  It asks the existing build directory for the exact nvcc command of that
one object (`make -n` on the object rule), runs it with the default shape to /tmp, then again with
`-DSTRATA_QSA_ATTN_CHUNK=128`.  Expected: the default compiles; the override fails **on the
static_assert** (a failure for another reason is a different problem and is reported as such).

Usage: check-assert-lock.py <source-tree> <build-dir> [--chunk 128]
Exit: 0 both halves as expected; 2 cannot extract the command; 3 default failed to compile;
      4 override compiled (the lock is gone); 5 override failed for another reason.
"""
from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile

OBJECT = "CMakeFiles/strata_kernels.dir/src/kernels/cuda/qsa_decode_attn.cu.o"
BUILD_MAKE = "CMakeFiles/strata_kernels.dir/build.make"


def compile_command(build_dir: str) -> list[str]:
    done = subprocess.run(["make", "-f", BUILD_MAKE, OBJECT, "-n", "-B"],
                          cwd=build_dir, capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise SystemExit(f"make -n failed ({done.returncode}): {done.stderr.strip()[:400]}")
    for line in done.stdout.splitlines():
        if "nvcc" in line and "-c " in line:
            tokens = shlex.split(line)
            keep, skip = [], False
            for token in tokens:
                if skip:                     # the argument of -MT / -MF
                    skip = False
                    continue
                if token == "-MD":           # dependency files point into the build tree: not wanted
                    continue
                if token in ("-MT", "-MF"):
                    skip = True
                    continue
                keep.append(token)
            # the output object would land in the build directory: send it to /tmp instead
            for i, token in enumerate(keep):
                if token == "-o":
                    keep[i + 1] = os.path.join(tempfile.gettempdir(), "assert-lock-check.o")
                    break
            return keep
    raise SystemExit("no nvcc compile command found in `make -n` output")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("src")
    parser.add_argument("build")
    parser.add_argument("--chunk", default="128")
    options = parser.parse_args(argv)

    command = compile_command(options.build)
    print("checking the default shape compiles ...", flush=True)
    default = subprocess.run(command, cwd=options.build, capture_output=True, text=True, timeout=600)
    if default.returncode != 0:
        print(default.stdout[-2000:])
        print(default.stderr[-4000:], file=sys.stderr)
        print("FAIL: the default shape does not compile", file=sys.stderr)
        return 3

    print(f"checking -DSTRATA_QSA_ATTN_CHUNK={options.chunk} is rejected ...", flush=True)
    override = subprocess.run(command + [f"-DSTRATA_QSA_ATTN_CHUNK={options.chunk}"],
                              cwd=options.build, capture_output=True, text=True, timeout=600)
    output = override.stdout + override.stderr
    if override.returncode == 0:
        print(f"FAIL: CHUNK={options.chunk} compiled; the static_assert lock is gone", file=sys.stderr)
        return 4
    if "static_assert" not in output:
        print(output[-4000:], file=sys.stderr)
        print("FAIL: the override failed, but not on the static_assert", file=sys.stderr)
        return 5
    line = next((l.strip() for l in output.splitlines() if "static_assert" in l), "")
    print(f"OK: default compiles; CHUNK={options.chunk} rejected: {line[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
