"""Process ownership and leftover detection for the Linux server runtime.

Every child leaves an owner-private record containing its PID, kernel start
time and command line.  A PID is signalled only while all three still match,
and only after its recorded parent is gone.
"""

from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from strata_control import atomic
from strata_control import paths
from strata_control.private_files import private_mkdir

SCHEMA = 1


def _stat_fields(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        return raw[raw.rindex(")") + 2:].split()
    except (OSError, ValueError):
        return None


def start_token(pid: int) -> str | None:
    """Return a token unique to this PID's current process, or None."""
    if type(pid) is not int or pid <= 0:
        return None
    fields = _stat_fields(pid)
    if fields is None or len(fields) < 20 or fields[0] == "Z":
        return None
    return "l:" + fields[19]


def started_epoch(pid: int) -> float | None:
    """Return the process creation time in seconds since the Unix epoch."""
    token = start_token(pid)
    if token is None:
        return None
    try:
        boot = next(int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines()
                    if line.startswith("btime "))
        return boot + int(token[2:]) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, StopIteration):
        return None


def command_line(pid: int) -> list[str] | None:
    """Return the process argv, or None when it cannot be read."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part] or None


def parent_of(pid: int) -> int | None:
    fields = _stat_fields(pid)
    if fields is None or len(fields) < 2:
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


def exe_path(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def same_file(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.realpath(a) == os.path.realpath(b)


@dataclass(frozen=True)
class Ident:
    pid: int
    token: str
    cmd: tuple[str, ...]


def identify(pid: int) -> Ident | None:
    token = start_token(pid)
    cmd = command_line(pid)
    if token is None or not cmd:
        return None
    return Ident(pid, token, tuple(cmd))


def is_same(pid: int, token: str, cmd: list[str] | tuple[str, ...] | None) -> bool:
    return start_token(pid) == token and command_line(pid) == list(cmd or [])


def procs_dir(create: bool = True) -> Path:
    base = paths.state_base() / "procs"
    if create and not base.exists():
        private_mkdir(base, parents=True, exist_ok=True)
    return base


def write_record(pid: int, role: str, *, profile: str | None = None, cmd: list[str] | None = None,
                 parent_pid: int | None = None) -> str | None:
    """Write a best-effort ownership record and return the child's start token."""
    try:
        token = start_token(pid)
        command = list(cmd) if cmd else command_line(pid)
        parent_pid = os.getpid() if parent_pid is None else parent_pid
        parent_token = start_token(parent_pid)
        if token is None or not command or parent_token is None:
            return None
        record = {
            "schema": SCHEMA,
            "pid": pid,
            "token": token,
            "cmd": command,
            "role": role,
            "profile": profile,
            "parent": {"pid": parent_pid, "token": parent_token},
            "written_at": time.time(),
        }
        path = procs_dir() / f"{pid}.json"
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        atomic.replace(tmp, path)
        return token
    except (OSError, ValueError):
        return None


def _load(path: Path) -> dict | None:
    try:
        if path.stat().st_size > 16 * 1024:
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = (
        isinstance(record, dict)
        and record.get("schema") == SCHEMA
        and type(record.get("pid")) is int
        and isinstance(record.get("token"), str)
        and isinstance(record.get("cmd"), list)
        and all(isinstance(part, str) for part in record["cmd"])
        and isinstance(record.get("parent"), dict)
        and type(record["parent"].get("pid")) is int
        and isinstance(record["parent"].get("token"), str)
    )
    return record if ok else None


def drop_record(pid: int, token: str | None = None) -> None:
    path = procs_dir(create=False) / f"{pid}.json"
    try:
        if token is not None:
            record = _load(path)
            if record is None or record["token"] != token:
                return
        path.unlink(missing_ok=True)
    except OSError:
        pass


def records(profile: str | None = None) -> list[dict]:
    base = procs_dir(create=False)
    if not base.is_dir():
        return []
    found = []
    for path in sorted(base.glob("*.json")):
        record = _load(path)
        if record is not None and (profile is None or record.get("profile") == profile):
            found.append(record)
    return found


def leftovers(profile: str | None = None, *, forget_stale: bool = True) -> tuple[list[dict], list[dict]]:
    """Return verified leftovers and records whose argv cannot be inspected."""
    to_end: list[dict] = []
    unverifiable: list[dict] = []
    for record in records(profile):
        pid = record["pid"]
        if start_token(pid) != record["token"]:
            if forget_stale:
                drop_record(pid)
            continue
        parent = record["parent"]
        if start_token(parent["pid"]) == parent["token"]:
            continue
        current = command_line(pid)
        if current is None:
            unverifiable.append(record)
        elif current == record["cmd"]:
            to_end.append(record)
        elif forget_stale:
            drop_record(pid)
    return to_end, unverifiable


def _signal_verified(record: dict, sig: int) -> bool:
    if not is_same(record["pid"], record["token"], record["cmd"]):
        return False
    try:
        os.kill(record["pid"], sig)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def end_one(record: dict, *, grace: float = 8.0) -> str:
    pid = record["pid"]
    if not is_same(pid, record["token"], record["cmd"]):
        drop_record(pid)
        return "gone"
    _signal_verified(record, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and start_token(pid) == record["token"]:
        time.sleep(0.1)
    if start_token(pid) == record["token"]:
        _signal_verified(record, signal.SIGKILL)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and start_token(pid) == record["token"]:
        time.sleep(0.1)
    if start_token(pid) == record["token"]:
        return "stuck"
    drop_record(pid)
    return "ended"


def end_leftovers(profile: str | None = None, *, grace: float = 8.0, say=None) -> dict:
    to_end, unverifiable = leftovers(profile)
    result = {
        "ended": [],
        "gone": [],
        "stuck": [],
        "unverifiable": [{"pid": r["pid"], "role": r.get("role")} for r in unverifiable],
    }
    for record in sorted(to_end, key=lambda item: item["written_at"], reverse=True):
        outcome = end_one(record, grace=grace)
        result[outcome].append(
            {"pid": record["pid"], "role": record.get("role"), "profile": record.get("profile")}
        )
        if say is not None and outcome != "gone":
            say(f"[strata] leftover {record.get('role', 'process')} {record['pid']} {outcome}")
    return result


def orphans_of(engine_exe: str) -> list[Ident]:
    """Find an engine left running by an earlier Strata version under PID 1."""
    if not engine_exe:
        return []
    mine = {os.getpid()}
    current = os.getpid()
    for _ in range(16):
        parent = parent_of(current)
        if parent is None or parent in (0, 1) or parent in mine:
            break
        mine.add(parent)
        current = parent
    found: list[Ident] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in mine:
            continue
        command = command_line(pid)
        if not command or "--serve" not in command or not same_file(exe_path(pid), engine_exe):
            continue
        parent = parent_of(pid)
        if parent in mine:
            continue
        if parent not in (None, 0, 1) and start_token(parent) is not None:
            continue
        ident = identify(pid)
        if ident is not None:
            found.append(ident)
    return found


def end_orphans(engine_exe: str, *, grace: float = 8.0, say=None) -> list[dict]:
    ended = []
    for ident in orphans_of(engine_exe):
        record = {
            "pid": ident.pid,
            "token": ident.token,
            "cmd": list(ident.cmd),
            "role": "engine",
            "written_at": 0,
        }
        outcome = end_one(record, grace=grace)
        ended.append({"pid": ident.pid, "outcome": outcome})
        if say is not None and outcome != "gone":
            say(f"[strata] an engine left behind by an earlier run (pid {ident.pid}) {outcome}")
    return ended
