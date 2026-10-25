#!/usr/bin/env python3
"""probe-version-stamp.py - what is the box running, measured, not quoted from a document.

Read-only: no restart, no config edit, no write on the box.  It records, in one JSON object:

  - md5/sha256/size/mtime of the four deployed files (engine binary, serve/server.py, launcher, config);
  - the *running* engine and server processes, and whether the engine path in `/proc/<pid>/cmdline`
    is the binary that was hashed (a hash of the wrong file is worse than no hash);
  - the STRATA_* environment of the running engine (the switch actually in force);
  - feature markers in the deployed files: strings in the binary, source lines in server.py.  These
    answer "does this build have the staged read / the 46d12fa9 fixes", not "is it fast";
  - /health and the engine block of /metrics.

`docs/STATE.md` and `docs/MULTIPATH_RULES.md` disagreed about the production hashes on 2026-10-25 (and
the R11 server hash turned out to be the staging tree's, not production's), so the numbers below are the
ones to cite: this script reads the files the processes were started from.

Usage: probe-version-stamp.py [--host atm@192.168.3.14 | --local] [--base-url http://127.0.0.1:8080]
                              [--out FILE] [--quiet]
Exit: 0 every item found and health 200; 1 a check failed; 2 ssh or read failed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

REMOTE_PROGRAM = r'''
import hashlib, json, os, subprocess, sys, time, urllib.request

S = "/opt/ai/src/strata-0.2.2"
BASE_URL = sys.argv[1]
API_KEY = sys.argv[2]
DEFAULT_FILES = {
    "engine": S + "/build/strata",
    "server": S + "/serve/server.py",
    "launcher": S + "/run-022.sh",
    "config": S + "/strata-022.json",
}
SERVER_MARKERS = [
    "STRATA_MP_STAGED_READ",
    "STRATA_MP_STAGED_READ_MIN_TOKENS",
    "BCANCEL_DRAIN_S = 10.0",
    "malformed staged-read reply",
    'yield "ping", None',
    "request_started = time.time()",
    "slot_wait_ms",
]
ENGINE_MARKERS = ["STRATA_BATCH_STAGED_READ", "BREAD", "BRDY", "BADOPT", "BCANCEL"]


def digest(path):
    md5 = hashlib.md5()
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            md5.update(chunk)
            sha.update(chunk)
    st = os.stat(path)
    return {"path": path, "md5": md5.hexdigest(), "sha256": sha.hexdigest(),
            "size": st.st_size, "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime))}


out = {"collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "files": {}, "running": {}, "markers": {}, "health": None, "engine": None, "errors": []}

# the engine is a child of the python server; match the binary, not the systemd MainPID
engine_pid = subprocess.run(["pgrep", "-f", "build/strata --serve"], capture_output=True, text=True).stdout.split()
server_pid = subprocess.run(["pgrep", "-f", "serve/server.py --engine"], capture_output=True, text=True).stdout.split()
agent_script = None
if server_pid:
    pid = server_pid[0]
    try:
        argv = [part for part in open("/proc/%s/cmdline" % pid, "rb").read().split(b"\0") if part]
        for part in argv:
            text = part.decode("utf-8", "replace")
            if text.endswith("/serve/server.py") or text.endswith("serve/server.py"):
                agent_script = text
                break
        out["running"]["server"] = {
            "pid": int(pid),
            "cmdline": b" ".join(argv).decode("utf-8", "replace").strip(),
            "script": agent_script,
            "started": subprocess.run(["ps", "-o", "lstart=", "-p", pid], capture_output=True, text=True).stdout.strip(),
        }
    except OSError as exc:
        out["errors"].append("server pid %s: %s" % (pid, exc))
engine_binary = None
if engine_pid:
    pid = engine_pid[0]
    try:
        raw_argv = [part for part in open("/proc/%s/cmdline" % pid, "rb").read().split(b"\0") if part]
        argv = b" ".join(raw_argv).decode("utf-8", "replace").strip()
        engine_binary = raw_argv[0].decode("utf-8", "replace") if raw_argv else None
        env = open("/proc/%s/environ" % pid, "rb").read().replace(b"\0", b"\n").decode("utf-8", "replace")
        out["running"]["engine"] = {
            "pid": int(pid),
            "cmdline": argv,
            "binary": engine_binary,
            "started": subprocess.run(["ps", "-o", "lstart=", "-p", pid], capture_output=True, text=True).stdout.strip(),
            "env": sorted(line for line in env.splitlines() if line.startswith("STRATA_")),
        }
    except OSError as exc:
        out["errors"].append("engine pid %s: %s" % (pid, exc))

# Hash the files the processes actually run.  The production directory has kept stale copies while the
# launcher ran serve/server.py from an experiment tree; hashing that directory's path reports the wrong code.
FILES = dict(DEFAULT_FILES)
if engine_binary:
    FILES["engine"] = engine_binary
if agent_script:
    FILES["server"] = agent_script
out["default_paths"] = dict(DEFAULT_FILES)
for name, path in FILES.items():
    try:
        out["files"][name] = digest(path)
    except OSError as exc:
        out["errors"].append("cannot read %s: %s" % (path, exc))

# markers: strings in the 79 MB binary (grep -a, no `strings` dependency), source lines in server.py
binary = FILES["engine"]
out["markers"]["engine"] = {}
for marker in ENGINE_MARKERS:
    found = subprocess.run(["grep", "-a", "-c", marker, binary], capture_output=True, text=True)
    out["markers"]["engine"][marker] = found.returncode == 0 and int(found.stdout.strip() or 0) > 0
try:
    server_text = open(FILES["server"], encoding="utf-8", errors="replace").read()
    out["markers"]["server"] = {marker: server_text.count(marker) for marker in SERVER_MARKERS}
except OSError as exc:
    out["errors"].append("cannot read server markers: %s" % exc)

def fetch(path):
    req = urllib.request.Request(BASE_URL + path, headers={"Authorization": "Bearer " + API_KEY})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)

try:
    out["health"] = fetch("/health")
except Exception as exc:                                    # noqa: BLE001 - report, do not crash the probe
    out["errors"].append("health: %s" % exc)
try:
    metrics = fetch("/metrics")
    out["engine"] = {k: metrics.get("engine", {}).get(k) for k in (
        "version", "batch_slots", "expert_slots", "max_context", "kv", "parallel")
        if k in metrics.get("engine", {})} or metrics.get("engine")
except Exception as exc:                                    # noqa: BLE001
    out["errors"].append("metrics: %s" % exc)

print(json.dumps(out))
'''


def run_remote(host: str, base_url: str, api_key: str) -> dict:
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host,
               "python3", "-", base_url, api_key]
    done = subprocess.run(command, input=REMOTE_PROGRAM, capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise RuntimeError(f"ssh probe failed (rc={done.returncode}): {done.stderr.strip()[:500]}")
    return json.loads(done.stdout)


def check(stamp: dict) -> list[str]:
    problems = []
    for name in ("engine", "server", "launcher", "config"):
        if name not in stamp.get("files", {}):
            problems.append(f"{name}: not readable")
    running = stamp.get("running", {}).get("engine")
    engine_file = stamp.get("files", {}).get("engine")
    if not running:
        problems.append("running engine: not found")
    elif engine_file and running.get("binary") != engine_file["path"]:
        problems.append(f"running engine binary {running.get('binary')} is not the hashed {engine_file['path']}")
    running_server = stamp.get("running", {}).get("server")
    server_file = stamp.get("files", {}).get("server")
    if not running_server:
        problems.append("running server: not found")
    elif not running_server.get("script"):
        problems.append("running server: cannot identify its serve/server.py")
    elif server_file and running_server.get("script") != server_file["path"]:
        problems.append(f"running server script {running_server.get('script')} is not the hashed {server_file['path']}")
    health = stamp.get("health")
    if not isinstance(health, dict):
        problems.append("health: no JSON")
    elif health.get("loaded") is False:
        problems.append("health: loaded=false")
    if stamp.get("errors"):
        problems.extend(stamp["errors"])
    return problems


def check_expected_env(stamp: dict, expected: dict[str, str]) -> list[str]:
    """Fail when the running engine lacks a required deployment switch or has another value."""
    problems = []
    running = stamp.get("running", {}).get("engine")
    if not expected:
        return problems
    if not running:
        problems.append("running engine: cannot check expected environment")
        return problems
    actual: dict[str, str] = {}
    for item in running.get("env") or []:
        key, separator, value = item.partition("=")
        if separator:
            actual[key] = value
    for key, value in expected.items():
        if key not in actual:
            problems.append(f"running engine env: missing {key}={value}")
        elif actual[key] != value:
            problems.append(f"running engine env: {key}={actual[key]} != expected {value}")
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("MP_HOST", "atm@192.168.3.14"))
    parser.add_argument("--local", action="store_true", help="run on this machine (the box itself)")
    parser.add_argument("--base-url", default=os.environ.get("MP_BASE_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--api-key", default=os.environ.get("MP_API_KEY", "111"))
    parser.add_argument("--out", default=None)
    parser.add_argument("--expect-engine-md5", default=None,
                        help="fail unless the engine file's md5 is this (the V2 window's install check)")
    parser.add_argument("--expect-env-file", default=None,
                        help="JSON file with {\"env\": {KEY: VALUE}} required in the running engine")
    parser.add_argument("--quiet", action="store_true")
    options = parser.parse_args(argv)

    expected_env: dict[str, str] = {}
    if options.expect_env_file:
        try:
            profile = json.load(open(options.expect_env_file, encoding="utf-8"))
            expected_env = profile.get("env")
            if (not isinstance(expected_env, dict) or not expected_env or
                    not all(isinstance(key, str) and isinstance(value, str)
                            for key, value in expected_env.items())):
                raise ValueError('"env" must be a non-empty object of string values')
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"version-stamp: cannot read expected env file: {exc}", file=sys.stderr)
            return 2

    try:
        if options.local:
            done = subprocess.run([sys.executable, "-", options.base_url, options.api_key],
                                  input=REMOTE_PROGRAM, capture_output=True, text=True, timeout=120)
            if done.returncode != 0:
                raise RuntimeError(f"local probe failed: {done.stderr.strip()[:500]}")
            stamp = json.loads(done.stdout)
        else:
            stamp = run_remote(options.host, options.base_url, options.api_key)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"version-stamp: {exc}", file=sys.stderr)
        return 2

    problems = check(stamp)
    problems.extend(check_expected_env(stamp, expected_env))
    if options.expect_engine_md5:
        got = stamp.get("files", {}).get("engine", {}).get("md5")
        if got != options.expect_engine_md5:
            problems.append(f"engine md5 {got} != expected {options.expect_engine_md5}")
    stamp["problems"] = problems
    out = options.out or time.strftime("/tmp/version-stamp-%Y%m%dT%H%M%SZ.json", time.gmtime())
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(stamp, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    if not options.quiet:
        for name, item in stamp.get("files", {}).items():
            print(f"{name:9s} md5 {item['md5']}  {item['size']:>10d} B  {item['mtime']}  {item['path']}")
        running = stamp.get("running", {})
        if "engine" in running:
            print(f"engine pid {running['engine']['pid']} started {running['engine']['started']}")
            for line in running["engine"]["env"]:
                print(f"  env {line}")
        if "server" in running:
            print(f"server pid {running['server']['pid']} started {running['server']['started']}")
        print("server markers:", json.dumps(stamp.get("markers", {}).get("server", {}), ensure_ascii=False))
        print("engine markers:", json.dumps(stamp.get("markers", {}).get("engine", {}), ensure_ascii=False))
        print("engine version:", json.dumps(stamp.get("engine"), ensure_ascii=False))
        print(f"json: {out}")
        for problem in problems:
            print(f"PROBLEM: {problem}", file=sys.stderr)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
