#!/usr/bin/env python3
"""probe-version-stamp.py - what is the box running, measured, not quoted from a document.

Read-only: no restart, no config edit, no write on the box.  It records, in one JSON object:

  - md5/sha256/size/mtime of the four deployed files (engine binary, serve/server.py, launcher, config);
  - the exact bytes of the running JSON config and the exact argv/environment of the server and engine;
  - the *running* engine and server processes, and whether the engine path in `/proc/<pid>/cmdline`
    is the binary that was hashed (a hash of the wrong file is worse than no hash);
  - stable kernel start tokens, parent/child relationships and cgroups for the process tree;
  - the STRATA_* environment of the running engine (the switch actually in force);
  - feature markers in the deployed files: strings in the binary, source lines in server.py.  These
    answer "does this build have the staged read / the 46d12fa9 fixes", not "is it fast";
  - /health and the engine block of /metrics.

The result is an input for a future last-known-good record.  It is explicitly not a rollback mechanism:
it only observes the running instance and does not prove that a restart, health check or recovery works.

`docs/STATE.md` and `docs/MULTIPATH_RULES.md` disagreed about the production hashes on 2026-10-25 (and
the R11 server hash turned out to be the staging tree's, not production's), so the numbers below are the
ones to cite: this script reads the files the processes were started from.

Usage: probe-version-stamp.py [--host atm@192.168.9.103 | --local] [--base-url http://127.0.0.1:8080]
                              [--out FILE] [--quiet]
Exit: 0 every item found and health 200; 1 a check failed; 2 ssh or read failed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

ENGINE_PROCESS_PATTERN = "[s]trata --serve"
SERVER_PROCESS_PATTERN = "serve/[s]erver.py --engine"
SNAPSHOT_CONTRACT = {
    "kind": "read_only_future_lkg_input",
    "rollback_verified": False,
    "restart_verified": False,
    "health_smoke_gate_verified": False,
    "warning": "this snapshot is evidence only; it is not an executable rollback target",
}

REMOTE_PROGRAM = r'''
import base64, hashlib, json, os, subprocess, sys, time, urllib.request

S = "/opt/ai/src/strata-0.2.2"
BASE_URL = sys.argv[1]
API_KEY = sys.argv[2]
SNAPSHOT_CONTRACT = __SNAPSHOT_CONTRACT__
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


def stable_stat(path):
    st = os.stat(path, follow_symlinks=True)
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def digest(path, keep_bytes=False):
    md5 = hashlib.md5()
    sha = hashlib.sha256()
    content = bytearray()
    before = stable_stat(path)
    with open(path, "rb") as fh:
        opened = os.fstat(fh.fileno())
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            md5.update(chunk)
            sha.update(chunk)
            if keep_bytes:
                content.extend(chunk)
        after = os.fstat(fh.fileno())
    current = stable_stat(path)
    opened_identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
    finished_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before != opened_identity or opened_identity != finished_identity or finished_identity != current:
        raise RuntimeError("file changed while the snapshot was being read: " + path)
    result = {"path": path, "md5": md5.hexdigest(), "sha256": sha.hexdigest(),
              "size": opened.st_size,
              "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(opened.st_mtime))}
    if keep_bytes:
        result["content_b64"] = base64.b64encode(content).decode("ascii")
        result["encoding"] = "base64"
    return result


def proc_bytes(pid, name):
    with open("/proc/%s/%s" % (pid, name), "rb") as fh:
        return fh.read()


def split_env(raw):
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def parse_status(raw):
    out = {}
    for line in raw.decode("utf-8", "replace").splitlines():
        key, separator, value = line.partition(":")
        if separator:
            out[key] = value.strip()
    return out


def process_identity(pid):
    stat_raw = proc_bytes(pid, "stat").decode("utf-8", "replace")
    close = stat_raw.rfind(")")
    fields = stat_raw[close + 2:].split()
    cmdline_raw = proc_bytes(pid, "cmdline")
    environ_raw = proc_bytes(pid, "environ")
    return {
        "pid": int(pid),
        "ppid": int(fields[1]),
        "state": fields[0],
        "kernel_start_token": fields[19],
        "cmdline": [part.decode("utf-8", "replace") for part in cmdline_raw.split(b"\0") if part],
        "cmdline_b64": base64.b64encode(cmdline_raw).decode("ascii"),
        "environment": split_env(environ_raw),
        "environment_b64": base64.b64encode(environ_raw).decode("ascii"),
        "status": parse_status(proc_bytes(pid, "status")),
        "cgroup": proc_bytes(pid, "cgroup").decode("utf-8", "replace").strip().splitlines(),
        "maps_sha256": hashlib.sha256(proc_bytes(pid, "maps")).hexdigest(),
        "smaps_rollup_sha256": hashlib.sha256(proc_bytes(pid, "smaps_rollup")).hexdigest(),
    }


def all_processes():
    table = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            stat_raw = proc_bytes(name, "stat").decode("utf-8", "replace")
            close = stat_raw.rfind(")")
            fields = stat_raw[close + 2:].split()
            table[int(name)] = {"pid": int(name), "ppid": int(fields[1]), "state": fields[0],
                                "kernel_start_token": fields[19]}
        except OSError:
            continue
    return table


def descendants(root):
    table = all_processes()
    selected = {root}
    changed = True
    while changed:
        changed = False
        for pid, item in table.items():
            if item["ppid"] in selected and pid not in selected:
                selected.add(pid)
                changed = True
    return [table[pid] for pid in sorted(selected) if pid in table]


def resolve_process_path(pid, path):
    """Resolve a relative argv path against the process working directory."""
    if not path or os.path.isabs(path):
        return path
    cwd = os.readlink("/proc/%s/cwd" % pid)
    return os.path.normpath(os.path.join(cwd, path))


out = {"collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "snapshot": SNAPSHOT_CONTRACT,
       "files": {}, "running": {}, "process_tree": [], "identity_stable": None,
       "markers": {}, "health": None, "engine": None, "errors": []}

# the engine is a child of the python server; match the binary, not the systemd MainPID
engine_pid = subprocess.run(["pgrep", "-f", "__ENGINE_PROCESS_PATTERN__"],
                            capture_output=True, text=True).stdout.split()
server_pid = subprocess.run(["pgrep", "-f", "__SERVER_PROCESS_PATTERN__"],
                            capture_output=True, text=True).stdout.split()
agent_script = None
agent_config = None
if server_pid:
    pid = server_pid[0]
    try:
        argv = [part for part in open("/proc/%s/cmdline" % pid, "rb").read().split(b"\0") if part]
        text_argv = [part.decode("utf-8", "replace") for part in argv]
        for index, text in enumerate(text_argv):
            if text.endswith("/serve/server.py") or text.endswith("serve/server.py"):
                agent_script = resolve_process_path(pid, text)
            elif text == "--config" and index + 1 < len(text_argv):
                agent_config = resolve_process_path(pid, text_argv[index + 1])
            elif text.startswith("--config="):
                agent_config = resolve_process_path(pid, text.partition("=")[2])
        out["running"]["server"] = {
            "pid": int(pid),
            "cmdline": b" ".join(argv).decode("utf-8", "replace").strip(),
            "script": agent_script,
            "config": agent_config,
            "started": subprocess.run(["ps", "-o", "lstart=", "-p", pid], capture_output=True, text=True).stdout.strip(),
        }
        out["running"]["server"].update(process_identity(pid))
    except OSError as exc:
        out["errors"].append("server pid %s: %s" % (pid, exc))
engine_binary = None
if engine_pid:
    pid = engine_pid[0]
    try:
        raw_argv = [part for part in open("/proc/%s/cmdline" % pid, "rb").read().split(b"\0") if part]
        argv = b" ".join(raw_argv).decode("utf-8", "replace").strip()
        engine_binary = resolve_process_path(pid, raw_argv[0].decode("utf-8", "replace")) if raw_argv else None
        env = open("/proc/%s/environ" % pid, "rb").read().replace(b"\0", b"\n").decode("utf-8", "replace")
        out["running"]["engine"] = {
            "pid": int(pid),
            "cmdline": argv,
            "binary": engine_binary,
            "started": subprocess.run(["ps", "-o", "lstart=", "-p", pid], capture_output=True, text=True).stdout.strip(),
            "env": sorted(line for line in env.splitlines() if line.startswith("STRATA_")),
        }
        out["running"]["engine"].update(process_identity(pid))
    except OSError as exc:
        out["errors"].append("engine pid %s: %s" % (pid, exc))

identity_before = {}
for role in ("server", "engine"):
    item = out["running"].get(role) or {}
    if isinstance(item.get("pid"), int) and item.get("kernel_start_token"):
        identity_before[item["pid"]] = item["kernel_start_token"]
if server_pid:
    try:
        out["process_tree"] = descendants(int(server_pid[0]))
    except OSError as exc:
        out["errors"].append("process tree: %s" % exc)

# Hash the files the processes actually run.  The production directory has kept stale copies while the
# launcher ran serve/server.py from an experiment tree; hashing that directory's path reports the wrong code.
FILES = dict(DEFAULT_FILES)
if engine_binary:
    FILES["engine"] = engine_binary
if agent_script:
    FILES["server"] = agent_script
if agent_config:
    FILES["config"] = agent_config
out["default_paths"] = dict(DEFAULT_FILES)
for name, path in FILES.items():
    try:
        out["files"][name] = digest(path, keep_bytes=(name == "config"))
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

identity_after = {}
for pid in identity_before:
    try:
        identity_after[pid] = process_identity(pid)["kernel_start_token"]
    except OSError:
        identity_after[pid] = None
out["identity_stable"] = identity_before == identity_after
if not out["identity_stable"]:
    out["errors"].append("a recorded process changed or exited during the snapshot")

print(json.dumps(out))
'''.replace("__ENGINE_PROCESS_PATTERN__", ENGINE_PROCESS_PATTERN).replace(
    "__SERVER_PROCESS_PATTERN__", SERVER_PROCESS_PATTERN
).replace("__SNAPSHOT_CONTRACT__", repr(SNAPSHOT_CONTRACT))


def run_remote(host: str, base_url: str, api_key: str) -> dict:
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host,
               "python3", "-", base_url, api_key]
    done = subprocess.run(command, input=REMOTE_PROGRAM, capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise RuntimeError(f"ssh probe failed (rc={done.returncode}): {done.stderr.strip()[:500]}")
    return json.loads(done.stdout)


def default_output_path() -> str:
    """Return a cross-platform timestamped output path in the system temp directory."""
    name = time.strftime("version-stamp-%Y%m%dT%H%M%SZ.json", time.gmtime())
    return os.path.join(tempfile.gettempdir(), name)


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
    config_file = stamp.get("files", {}).get("config")
    if not running_server:
        problems.append("running server: cannot identify its config")
    elif not running_server.get("config"):
        problems.append("running server: cannot identify its --config")
    elif config_file and running_server.get("config") != config_file["path"]:
        problems.append(
            f"running server config {running_server.get('config')} is not the hashed {config_file['path']}"
        )
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
    parser.add_argument("--host", default=os.environ.get("MP_HOST", "atm@192.168.9.103"))
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
    out = options.out or default_output_path()
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
        print("snapshot:", json.dumps(stamp.get("snapshot"), ensure_ascii=False))
        print("identity stable:", stamp.get("identity_stable"))
        print(f"json: {out}")
        for problem in problems:
            print(f"PROBLEM: {problem}", file=sys.stderr)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
