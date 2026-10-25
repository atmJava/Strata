#!/usr/bin/env python3
"""run-matrix.py - one batch, one run, one attribution table.

The batch rule (`docs/SELF_ITERATION_PLAN.md` §3) is: all changes done -> **one** build -> **one**
window -> **one** run of the whole test matrix -> attribute item by item afterwards.  This runner is
that one run.  It never changes the box, never restarts anything, and never decides a criterion itself:
each item carries its criterion, timeout and log path, and the summary says PASS/FAIL/NOTRUN per item
with the raw log beside it.

Items are defined below for the batch they belong to.  V1 is fully defined; V2/V3 items are added when
their drivers exist (a stub is marked NOT DEFINED and never reported as a pass).

Usage:
  run-matrix.py --batch V1 --list                 # what would run, and against which paths
  run-matrix.py --batch V1 --dry-run              # print the exact commands
  run-matrix.py --batch V1                        # run everything, write <out>/summary.json
  run-matrix.py --batch V1 --only V1-2,V1-5       # subset (bisection: one flag off, rerun that item)
  run-matrix.py --batch V1 --skip V1-4

Placeholders: {src} {build} {py} {out} {host} {base_url} {api_key}.

Exit: 0 every item PASS; 1 any FAIL; 2 any NOTRUN / bad plan; 3 the runner itself failed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time

DEFAULT_HOST = os.environ.get("MP_HOST", "atm@192.168.3.14")
DEFAULT_SRC = os.environ.get("V1_SRC", "/opt/ai/src/strata-port-v1")
DEFAULT_PY = os.environ.get("V1_PY", "/opt/ai/tmp/venv-v0/bin/python")

# where: "local" runs here; "ssh" runs on the box.  judge: how the result is read (see judge()).
BATCHES: dict[str, list[dict]] = {
    "V1": [
        dict(id="V1-1", what="production version stamp (four files + running processes + markers)",
             criterion="exit 0; all four md5s recorded; the running engine is the hashed binary",
             where="local", timeout=180, judge="exit0",
             cmd=["python3", "tools/server/probe-version-stamp.py", "--out", "{out}/V1-1-version-stamp.json"]),
        dict(id="V1-2", what="the server unit tests on the server source tree",
             criterion="0 failed, 0 errors (skips are printed, never counted as a pass)",
             where="ssh", timeout=900, judge="pytest",
             # a port with nothing on it: the ten live-server cases skip instead of sending real traffic
             # to production.  Their live run is a V2/V3 window item, not this one.
             env={"MP_BASE_URL": "http://127.0.0.1:9"},
             cmd=["{py}", "-m", "pytest", "{src}/tools/server/tests", "-q"]),
        dict(id="V1-3", what="isa_guard_check (CTest)",
             criterion="ctest PASS", where="ssh", timeout=900, judge="exit0",
             cmd=["ctest", "--test-dir", "{build}", "-R", "^isa_guard_check$", "--output-on-failure"]),
        dict(id="V1-4", what="the 64/256 decode-attention shape is locked by static_assert",
             criterion="default shape compiles; STRATA_QSA_ATTN_CHUNK=128 is rejected on the static_assert",
             where="ssh", timeout=600, judge="exit0",
             cmd=["python3", "{src}/tools/server/check_assert_lock.py", "{src}", "{build}"]),
        dict(id="V1-5", what="staged-read wedge protocol cases (server tree, no patch)",
             criterion="pytest exit 0, 0 failed", where="ssh", timeout=600, judge="pytest",
             cmd=["{py}", "-m", "pytest", "{src}/serve/test_staged_wedge.py", "-q"]),
    ],
    # The V2a window: deploy (the ops script, outside the matrix) -> this run.  Order matters: the
    # warmup series is only "the first requests after a restart" while the engine is still cold, so it
    # runs first; the staged drill is the heavy one and runs last.
    "V2": [
        dict(id="V2-0", what="the install is live (window gate: engine md5 + running process), not just copied",
             criterion="exit 0; engine md5 is the V1 build's 84a0d410 and the running engine is that file",
             where="local", timeout=180, judge="exit0",
             cmd=["python3", "tools/server/probe-version-stamp.py", "--expect-engine-md5", "84a0d410367abfb3a8bcb9422a3b10ee",
                  "--out", "{out}/V2-0-version-stamp.json"]),
        dict(id="V2-4", what="warmup: the first long read after the restart vs the second (cold/warm claim)",
             criterion="both ~22K reads answered; per-read wall_s and the neighbour's largest gap printed "
                       "(a measurement, not a threshold)",
             where="local", timeout=900, judge="exit0",
             cmd=["python3", "tools/server/exp-v2-warmup.py", "{base_url}"]),
        dict(id="V1-5", what="the VRAM account under the production shape (six slots, 192K, int8)",
             criterion="both GPUs and the engine's own arena/expert-cache numbers reported; free reserve printed",
             where="local", timeout=180, judge="exit0",
             cmd=["python3", "tools/server/exp-v2-vram.py", "{base_url}"]),
        dict(id="V2-1", what="staged read runs and a cancelled reader leaves the neighbours alone (S0d live; "
                             "S0c's engine kill is a separate, budgeted step)",
             criterion="after the close the six neighbours still decode (gap < 2 s, tokens > 0); health 200; "
                       "a long read then a short one are both served",
             where="local", timeout=1800, judge="exit0",
             cmd=["python3", "tools/server/exp-v2-staged-drill.py", "{base_url}", "6"]),
    ],
    "V3": [],
}

PYTEST_SUMMARY = re.compile(r"(\d+) (passed|failed|skipped|error|errors)")


def expand(argv: list[str], values: dict[str, str]) -> list[str]:
    return [token.format(**values) for token in argv]


def expand_env(env: dict[str, str], values: dict[str, str]) -> dict[str, str]:
    return {key: value.format(**values) for key, value in env.items()}


def judge(kind: str, returncode: int, output: str) -> tuple[str, str]:
    if kind == "exit0":
        if returncode == 0:
            return "PASS", ""
        line = next((l for l in reversed(output.splitlines()) if l.strip()), "")
        return "FAIL", f"rc={returncode}: {line[:160]}"
    if kind == "pytest":
        counts = {name: int(number) for number, name in PYTEST_SUMMARY.findall(output)}
        failed = counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0)
        detail = ", ".join(f"{counts[k]} {k}" for k in ("passed", "failed", "error", "errors", "skipped")
                           if k in counts)
        if returncode == 0 and failed == 0 and counts.get("passed", 0) > 0:
            return "PASS", detail
        if returncode == 5:
            return "FAIL", "no tests collected (rc=5)"
        return "FAIL", f"rc={returncode}; {detail or 'no pytest summary'}"
    raise SystemExit(f"unknown judge {kind}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", default="V1")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--src", default=DEFAULT_SRC)
    parser.add_argument("--build", default=None)
    parser.add_argument("--py", default=DEFAULT_PY)
    parser.add_argument("--base-url", default=os.environ.get("MP_BASE_URL", "http://192.168.3.14:8080"))
    parser.add_argument("--api-key", default=os.environ.get("MP_API_KEY", "111"))
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--only", default=None)
    parser.add_argument("--skip", default=None)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    options = parser.parse_args(argv)

    items = BATCHES.get(options.batch)
    if items is None:
        print(f"unknown batch {options.batch}; known: {', '.join(sorted(BATCHES))}", file=sys.stderr)
        return 3
    out_dir = options.out_dir or time.strftime(f"/tmp/{options.batch}-matrix-%Y%m%dT%H%M%SZ", time.gmtime())
    values = {
        "src": options.src, "build": options.build or options.src + "/build75", "py": options.py,
        "out": out_dir, "host": options.host, "base_url": options.base_url, "api_key": options.api_key,
    }
    only = set(filter(None, (options.only or "").split(",")))
    skip = set(filter(None, (options.skip or "").split(",")))

    if not items:
        print(f"batch {options.batch}: no items defined yet", file=sys.stderr)
        return 2

    selected = [item for item in items if (not only or item["id"] in only) and item["id"] not in skip]
    if not selected:
        print("nothing selected", file=sys.stderr)
        return 2
    if options.list:
        for item in selected:
            print(f"{item['id']}  {item['where']:5s}  {item['criterion']}")
            print(f"       {item['what']}")
            env = expand_env(item.get("env", {}), values)
            prefix = (" ".join(f"{k}={v}" for k, v in env.items()) + " ") if env else ""
            print(f"       cmd: {prefix}{' '.join(expand(item['cmd'], values))}")
        return 0

    os.makedirs(out_dir, exist_ok=True)
    rows, results = [], {"batch": options.batch, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         "host": options.host, "src": values["src"], "build": values["build"],
                         "items": []}
    for item in selected:
        log_path = os.path.join(out_dir, item["id"] + ".log")
        command = expand(item["cmd"], values)
        env = expand_env(item.get("env", {}), values)
        if item["where"] == "ssh":
            if env:
                command = ["env"] + [f"{k}={v}" for k, v in env.items()] + command
            command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", options.host] + command
        printable = " ".join(shlex.quote(token) for token in command)
        if options.dry_run:
            print(f"{item['id']}  DRY-RUN  {printable}")
            continue
        started = time.time()
        try:
            run_env = dict(os.environ, **env) if item["where"] == "local" else None
            done = subprocess.run(command, capture_output=True, text=True, timeout=item["timeout"], env=run_env)
            output = done.stdout + done.stderr
            result, detail = judge(item["judge"], done.returncode, output)
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") + (exc.stderr or "") if isinstance(exc.stdout, str) else ""
            result, detail = "FAIL", f"timeout after {item['timeout']} s"
        except OSError as exc:
            output, result, detail = str(exc), "NOTRUN", f"runner error: {exc}"
        duration = round(time.time() - started, 1)
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(f"# {item['id']} {item['what']}\n# criterion: {item['criterion']}\n")
            fh.write(f"# command: {printable}\n# result: {result} {detail}\n\n{output}")
        rows.append((item["id"], result, duration, log_path))
        results["items"].append({"id": item["id"], "what": item["what"], "criterion": item["criterion"],
                                 "result": result, "detail": detail, "seconds": duration,
                                 "log": log_path, "command": printable})
        print(f"{item['id']}  {result:6s}  {duration:7.1f} s  {detail}")

    if options.dry_run:
        return 0

    results["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"\n{'ID':6s} {'RESULT':7s} {'SECONDS':>8s}  LOG")
    for ident, result, duration, log_path in rows:
        print(f"{ident:6s} {result:7s} {duration:8.1f}  {log_path}")
    print(f"summary: {os.path.join(out_dir, 'summary.json')}")
    fails = [row for row in rows if row[1] != "PASS"]
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
