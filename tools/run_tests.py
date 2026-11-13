#!/usr/bin/env python3
"""Run every suite this fork has, from one command, and say what happened.

Four groups, the ones that exist here:

  engine    the standalone conversation tests (cache, memory, file, split-failure, save-reclaim)
  serve     the Python server's tests
  tools     the tools' own tests (the fixed-load client, the pack helpers)
  terminal  the terminal's tests (pytest)

engine is compiled here rather than through ctest on purpose: this checkout leaves out
tests/CMakeLists.txt, which is what turns STRATA_BUILD_TESTS on, and CMakeLists.txt:65 calls
enable_language(CUDA) before any option is read, so a test-only configure still needs a CUDA
compiler. The five tests below have their sources in src/, so they build with g++ and take about
a minute in total.

Usage:
  python tools/run_tests.py                     # everything
  python tools/run_tests.py --group serve       # one group
  python tools/run_tests.py --group serve --filter cache_capacity
  python tools/run_tests.py --python /opt/ai/venvs/test-027/bin/python
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# test -> the sources it needs beyond its own file
ENGINE_TESTS = {
    "conversation_cache_test": [],
    "conversation_split_failure_test": [],
    "conversation_memory_test": ["src/core/conversation_memory.cpp"],
    "conversation_file_test": ["src/core/conversation_file.cpp"],
    "session_save_reclaim_test": ["src/core/conversation_file.cpp"],
}


def say(text=""):
    print(text, flush=True)


def run(command, cwd, timeout):
    """Returns (exit code, output). A timeout is exit 124."""
    try:
        done = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "timed out after %.0fs" % timeout
    except FileNotFoundError as error:
        return 127, str(error)
    return done.returncode, (done.stdout + done.stderr).strip()


def tail(text, lines=12):
    kept = (text or "").strip().splitlines()
    return "\n".join(kept[-lines:]) if kept else "(no output)"


def engine_group(timeout, ggml):
    """Compiles and runs each standalone conversation test. Returns [(name, code, output, seconds)]."""
    results = []
    build = Path(tempfile.mkdtemp(prefix="strata-engine-tests-"))
    includes = ["-I", str(ROOT / "include")]
    if ggml is not None:
        includes += ["-I", str(ggml)]
    try:
        for name, extra in ENGINE_TESTS.items():
            started = time.monotonic()
            sources = [str(ROOT / "src" / "core" / (name + ".cpp"))] + [str(ROOT / item) for item in extra]
            binary = build / name
            code, output = run(["g++", "-std=c++20", "-O1"] + includes + sources + ["-o", str(binary)], ROOT, timeout)
            if code == 0:
                code, output = run([str(binary)], ROOT, timeout)
            results.append((name, code, output, time.monotonic() - started))
            if code != 0:
                break   # the first failure is the one to read; the rest would repeat it
    finally:
        shutil.rmtree(build, ignore_errors=True)
    return results


def python_group(module, python, timeout, extra):
    """serve is a package, so discovery works; tools is not (no __init__.py), so its modules are named."""
    started = time.monotonic()
    if (ROOT / module / "__init__.py").exists():
        command = [python, "-m", "unittest", "discover", "-s", module, "-t", ".", "-p", "test_*.py"]
    else:
        names = sorted(p.stem for p in (ROOT / module).glob("test_*.py"))
        command = [python, "-m", "unittest"] + [module + "." + name for name in names]
    code, output = run(command + extra, ROOT, timeout)
    return code, output, time.monotonic() - started


def pytest_group(python, timeout, path, extra):
    started = time.monotonic()
    code, output = run([python, "-m", "pytest", path, "-q"] + extra, ROOT, timeout)
    return code, output, time.monotonic() - started


def main():
    parser = argparse.ArgumentParser(description="Run this fork's test suites.")
    parser.add_argument("--group", action="append", choices=["engine", "serve", "tools", "terminal", "all"],
                        help="a group to run; repeatable (default: all)")
    parser.add_argument("--python", default=sys.executable, help="interpreter for the Python suites")
    parser.add_argument("--filter", default="", help="unittest -k / pytest -k pattern")
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds per test or per suite")
    parser.add_argument("--ggml", default=os.environ.get("STRATA_GGML_DIR", ""),
                        help="llama.cpp checkout for the engine tests' includes (STRATA_GGML_DIR)")
    args = parser.parse_args()
    groups = args.group or ["all"]
    if "all" in groups:
        groups = ["engine", "serve", "tools", "terminal"]
    ggml = Path(args.ggml) if args.ggml else None
    extra = ["-k", args.filter] if args.filter else []

    failures = 0
    table = []
    say("strata tests: " + ", ".join(groups) + " (python " + args.python + ")")

    if "engine" in groups:
        for name, code, output, seconds in engine_group(args.timeout, ggml):
            table.append(("engine", name, code, seconds, tail(output, 4)))
            failures += code != 0

    for group in ("serve", "tools"):
        if group in groups:
            code, output, seconds = python_group(group, args.python, args.timeout, extra)
            table.append((group, group + ".*", code, seconds, tail(output)))
            failures += code != 0

    if "terminal" in groups:
        code, output, seconds = pytest_group(args.python, args.timeout, "tests/terminal", extra)
        table.append(("terminal", "tests/terminal", code, seconds, tail(output)))
        failures += code != 0

    say()
    say("%-9s %-34s %-8s %7s" % ("group", "suite", "result", "time"))
    for group, name, code, seconds, _ in table:
        verdict = "ok" if code == 0 else ("timeout" if code == 124 else "FAILED")
        say("%-9s %-34s %-8s %6.1fs" % (group, name, verdict, seconds))
    for group, name, code, seconds, output in table:
        if code != 0:
            say("\n--- %s / %s ---\n%s" % (group, name, output))
    say()
    say("all passed" if not failures else "%d of %d groups failed" % (failures, len(table)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
