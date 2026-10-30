"""Local owner-controlled supervisor for one saved Linux serving profile.

The supervisor holds the Popen handle for the server it started.  Its control
channel is a mode-0600 Unix socket in a mode-0700 directory, and its leases use
POSIX ``flock``.  A PID in a state file is compared with its kernel start token
before it can be treated as the server this host started.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from strata_control import atomic, owner_transport, paths, posix_owner, procs, profiles, readiness
from strata_control.private_files import private_mkdir
from serve import launch_identity
from strata_control.profiles import ProfileError


MAX_MESSAGE = 64 * 1024
ROOT = Path(__file__).resolve().parents[1]
_STARTED_HOSTS: dict[str, subprocess.Popen] = {}


def _is_private(details: os.stat_result, path: Path) -> bool:
    """Owner-only: this UID and no group/other access bits."""
    return not posix_owner.owner_only_wrong(path, details)


def _lock_file(descriptor: int, *, exclusive: bool) -> bool:
    """Take a lease without waiting: True when taken, False when another holder has it."""
    try:
        fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            return False
        raise
    return True


def _unlock_file(descriptor: int) -> None:
    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _supported() -> None:
    """Refuse a host that cannot provide a private Unix socket."""
    if hasattr(socket, "AF_UNIX"):
        return
    raise ProfileError("managed runtime has no qualified local control transport on this platform", 5)


def _name(value: str) -> str:
    if not profiles.PROFILE_ID.fullmatch(value):
        raise ProfileError("runtime commands require a saved profile name")
    return value


def runtime_dir() -> Path:
    _supported()
    override = os.environ.get("STRATA_RUNTIME_DIR")
    if override:
        base = Path(override).expanduser()
    else:
        base = paths.state_base() / "runtime"
    if not base.is_absolute():
        raise ProfileError("STRATA_RUNTIME_DIR must be absolute")
    private_mkdir(base, parents=True, exist_ok=True)
    details = base.lstat()
    if not stat.S_ISDIR(details.st_mode) or not _is_private(details, base):
        raise ProfileError(_not_private_message(base), 5)
    return base


def _not_private_message(base: Path) -> str:
    return "runtime directory must be owner-only (mode 0700)"


def acquire_activity_lease(*, shared: bool) -> int:
    """Let managed hosts coexist while excluding a local calibration run."""
    directory = runtime_dir()
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(directory / ".activity.lock", flags, 0o600)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or not _is_private(details, directory / ".activity.lock"):
            raise ProfileError("runtime activity lock must be owner-only", 5)
        if not _lock_file(descriptor, exclusive=not shared):
            raise ProfileError("a managed runtime or calibration owns the local engine lease", 6)
        return descriptor
    except BaseException:
        if "descriptor" in locals():
            os.close(descriptor)
        raise


def active_managed_instances() -> list[dict]:
    """Inspect private owner records, including stale or uncertain instances."""
    directory = runtime_dir()
    names = [path.stem for path in directory.glob("*.json") if profiles.PROFILE_ID.fullmatch(path.stem)]
    if len(names) > 64:
        raise ProfileError("too many managed runtime records to verify exclusive access", 6)
    active = []
    for name in names:
        current = status(name)
        if current.get("state") != "stopped":
            active.append({"profile": name, "state": current.get("state", "unknown")})
    return active


def _paths(name: str) -> tuple[Path, Path, Path]:
    directory = runtime_dir()
    digest = hashlib.sha256((str(directory) + "\0" + name).encode("utf-8")).hexdigest()[:16]
    address = owner_transport.address_for(directory, name)
    return directory / f"{name}.json", address, directory / f"{name}.host.log"


def _read_state(name: str) -> dict | None:
    path, socket_path, _ = _paths(_name(name))
    try:
        details = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(details.st_mode) or not _is_private(details, path) or details.st_size > 16 * 1024:
        raise ProfileError("runtime state file is not private or is invalid", 5)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ProfileError("runtime state file is unreadable", 5) from None
    address_ok = isinstance(state, dict) and state.get("socket") == socket_path
    if not isinstance(state, dict) or state.get("schema_version") != 1 or state.get("profile") != name or \
            not address_ok or not isinstance(state.get("token"), str) or \
            not re.fullmatch(r"[0-9a-f]{64}", state["token"]):
        raise ProfileError("runtime state file has an unsupported shape", 5)
    return state


def _child_marker(name: str, token: str) -> Path:
    return runtime_dir() / f"{name}-{token}.child.json"


def _pid_exists(pid: int) -> bool:
    """Whether the recorded child is still there. This is a liveness check only."""
    try:
        os.kill(pid, 0)  # Liveness check only; never signal a PID from saved state.
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


_MARKER_SLACK = 2.0   # seconds: the child starts before its marker is written; this much of clock rounding is allowed


def _child_present(child: dict, written: float | None = None) -> bool:
    """Whether the child recorded in an owner marker is still there.

    A process created after the marker was written cannot be the recorded child;
    this clears a marker whose PID now belongs to a newer process.
    """
    started = child.get("started") if type(child.get("started")) is int else None
    if not _pid_exists(child["pid"]):
        return False
    if started is None and written is not None:
        created = procs.started_epoch(child["pid"])
        if created is not None and created > written + _MARKER_SLACK:
            return False
    return True


def _host_lease_active(name: str) -> bool:
    path, _, _ = _paths(name)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not _lock_file(descriptor, exclusive=True):
            return True
        _unlock_file(descriptor)
        return False
    finally:
        os.close(descriptor)


def _recover_stale(name: str, *, wait: float) -> bool:
    """Reclaim private state only after the host lease and owned child are gone."""
    state = _read_state(name)
    if state is None:
        return False
    state_path, socket_path, _ = _paths(name)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(state_path, flags)
    try:
        if os.fstat(descriptor).st_ino != state_path.lstat().st_ino:
            raise ProfileError("runtime state changed during recovery", 6)
        if not _lock_file(descriptor, exclusive=True):
            raise ProfileError("this profile already has a live managed runtime", 6)
        marker = _child_marker(name, state["token"])
        try:
            details = marker.lstat()
            if not stat.S_ISREG(details.st_mode) or not _is_private(details, marker) or details.st_size > 4096:
                raise ValueError("invalid owner marker")
            child = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ProfileError("lost host has no valid child ownership record; inspect before recovery", 6) from None
        if not isinstance(child, dict) or child.get("host_token") != state["token"] or \
                child.get("phase") != "owned" or type(child.get("pid")) is not int or child["pid"] <= 0:
            raise ProfileError("lost host child launch is unverified; inspect before recovery", 6)
        deadline = time.monotonic() + min(45.0, max(5.0, wait))
        written = details.st_mtime
        while _child_present(child, written) and time.monotonic() < deadline:
            time.sleep(0.1)
        if _child_present(child, written):
            raise ProfileError("lost host child is still present; retry after it exits", 6)
        if not isinstance(child.get("ready"), str):
            raise ProfileError("lost host ready path is invalid; inspect before recovery", 6)
        ready = Path(child["ready"])
        if ready.parent != runtime_dir() or not ready.name.startswith(name + "-") or \
                not ready.name.endswith(".ready.json"):
            raise ProfileError("lost host ready path is invalid; inspect before recovery", 6)
        try:
            details = ready.lstat()
            if not stat.S_ISREG(details.st_mode) or not _is_private(details, ready):
                raise ProfileError("lost host ready file is invalid; inspect before recovery", 6)
            ready.unlink()
        except FileNotFoundError:
            pass
        # A socket that still accepts means a live owner; nothing may be
        # recovered over it.
        try:
            details = Path(socket_path).lstat()
            if not stat.S_ISSOCK(details.st_mode) or details.st_uid != os.getuid():
                raise ProfileError("lost host socket is invalid; inspect before recovery", 6)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                try:
                    probe.connect(str(socket_path))
                except OSError as exc:
                    if exc.errno not in (errno.ECONNREFUSED, errno.ENOENT):
                        raise ProfileError(
                            "lost host socket cannot be verified; inspect before recovery", 6
                        ) from None
                else:
                    raise ProfileError(
                        "a control listener still owns this socket; inspect before recovery", 6
                    )
            Path(socket_path).unlink()
        except FileNotFoundError:
            pass
        snapshots = list(runtime_dir().glob(f"{name}-{state['token']}-*.rollback.json"))
        if len(snapshots) > 64:
            raise ProfileError("too many lost-host config snapshots; inspect before recovery", 6)
        for snapshot in snapshots:
            details = snapshot.lstat()
            if not stat.S_ISREG(details.st_mode) or not _is_private(details, snapshot):
                raise ProfileError("lost-host config snapshot is not private; inspect before recovery", 6)
            snapshot.unlink()
        marker.unlink()
        state_path.unlink()
        return True
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_new(path: Path, data: bytes) -> int:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        # Keep this lease open in the owner host. The kernel releases it even
        # after SIGKILL, unlike a PID file or a stale Unix socket.
        if not _lock_file(descriptor, exclusive=True):
            raise OSError(errno.EWOULDBLOCK, "the runtime lease is held")
        with atomic.fdopen_owned(os.dup(descriptor), "wb") as stream:
            stream.write(data)
            stream.flush()
        os.fsync(descriptor)
        return descriptor
    except BaseException:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise


def _rpc(name: str, command: str, *, timeout: float = 5, params: dict | None = None) -> dict:
    state = _read_state(name)
    if state is None:
        raise ProfileError("runtime is not managed or is stopped", 3)
    request = {"token": state["token"], "command": command}
    if params:
        request.update(params)
    raw = (json.dumps(request) + "\n").encode("utf-8")
    try:
        client = owner_transport.connect(state["socket"], timeout=timeout)
    except OSError:      # nothing listens there any more (a lost owner), or it did not answer in time
        raise ProfileError("runtime control is unavailable; inspect the private host log", 3) from None
    try:
        try:
            client.sendall(raw)
            answer = bytearray()
            while len(answer) <= MAX_MESSAGE:
                chunk = client.recv(4096)
                if not chunk:
                    break
                answer.extend(chunk)
                if b"\n" in chunk:
                    break
        except socket.timeout:
            raise ProfileError("runtime operation wait expired; inspect runtime status", 8) from None
        except OSError:
            raise ProfileError("runtime control is unavailable; inspect the private host log", 3) from None
    finally:
        client.close()
    if len(answer) > MAX_MESSAGE:
        raise ProfileError("runtime control response is too large", 6)
    try:
        result = json.loads(answer.split(b"\n", 1)[0])
    except (ValueError, UnicodeError):
        raise ProfileError("runtime control returned an invalid response", 6) from None
    if not isinstance(result, dict) or type(result.get("ok")) is not bool:
        raise ProfileError("runtime control returned an invalid response", 6)
    return result


def status(name: str) -> dict:
    state = _read_state(name)
    if state is None:
        return {"ok": True, "profile": name, "state": "stopped"}
    try:
        active = _host_lease_active(name)
    except FileNotFoundError:
        return {"ok": True, "profile": name, "state": "stopped"}
    if not active:
        return {"ok": False, "profile": name, "state": "stale", "error": "owner_host_lost",
                "next_action": "runtime start will recover after the owned child exits", "exit_code": 6}
    return _rpc(name, "status")


def _start_candidate(name: str) -> tuple[dict, str]:
    from strata_cli.cli import _launch_plan
    plan, _ = _launch_plan(name)
    digest = hashlib.sha256(Path(plan["source_config"]).read_bytes()).hexdigest()
    return plan, digest


def _engine_preflight(plan: dict) -> None:
    """Refuse a start before any process exists when the run config names its engine as a component and the package for this
    PC is missing or fails its integrity check (every file's SHA-256). The server checks again when it starts the engine; this
    check makes the refusal the first thing the user sees, with the file named, instead of a log line behind 'server_exited'."""
    try:
        cfg = json.loads(Path(plan["source_config"]).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, KeyError, TypeError):
        return
    if not isinstance(cfg, dict):
        return
    from strata_control import engine_component
    if not engine_component.is_reference(cfg.get("exe")):
        return
    try:
        engine_component.expand(cfg["exe"], cfg)
    except engine_component.EngineMissing as exc:
        raise ProfileError(str(exc), exc.exit_code) from None


def _ready_pid_is_child(ready_pid, child_pid: int) -> bool:
    """Return whether the ready file names the exact server process we started."""
    if type(ready_pid) is not int or ready_pid <= 0:
        return False
    return ready_pid == child_pid


def _check_start_candidate(name: str, expected_profile_revision: str | None,
                           expected_source_sha256: str | None) -> tuple[dict, str]:
    plan, digest = _start_candidate(name)
    if expected_profile_revision is not None and plan["profile_revision"] != expected_profile_revision:
        raise ProfileError("profile_changed_after_start_plan", 6)
    if expected_source_sha256 is not None and digest != expected_source_sha256:
        raise ProfileError("source_changed_after_start_plan", 6)
    return plan, digest


MAX_START_WAIT = 1800     # seconds a start may wait for the model: a 250 GB DeepSeek set on a slow disk needs more than 10 minutes (#3949)


def start(name: str, *, wait: float = 300, shutdown_timeout: float = 30,
          expected_profile_revision: str | None = None,
          expected_source_sha256: str | None = None,
          die_with_pid: int | None = None, on_status=None) -> dict:
    """Start the saved profile under a new owner host.

    ``on_status`` (optional) is called with each status answer while the start waits (about every 0.2 s; the caller throttles), so a
    caller can show progress. The result of a wait that ran out names the last phase (``loading``, ``verifying_generation``,
    ``ready_file_not_matched``).

    The owner host is a background service: it is not tied to the terminal or the process that ran this command and
    keeps serving after they end. It stops on `runtime stop`, when its server ends, or - only if `die_with_pid`
    names a process - when that process is gone."""
    name = _name(name)
    die_with = None
    if die_with_pid is not None:
        token = procs.start_token(die_with_pid) if type(die_with_pid) is int else None
        if token is None:
            raise ProfileError("the process to follow with --die-with-pid is not running")
        die_with = (die_with_pid, token)
    if not 0 <= wait <= MAX_START_WAIT or not 0 <= shutdown_timeout <= 300:
        raise ProfileError(f"start wait must be 0..{MAX_START_WAIT} and shutdown timeout 0..300 seconds")
    if expected_profile_revision is not None and (not isinstance(expected_profile_revision, str) or
            not re.fullmatch(r"[0-9a-f]{16}", expected_profile_revision)):
        raise ProfileError("expected profile revision must be 16 lowercase hex characters")
    if expected_source_sha256 is not None and (not isinstance(expected_source_sha256, str) or
            not re.fullmatch(r"[0-9a-f]{64}", expected_source_sha256)):
        raise ProfileError("expected source digest must be 64 lowercase hex characters")
    plan, _ = _check_start_candidate(name, expected_profile_revision, expected_source_sha256)
    _engine_preflight(plan)
    _, _, log_path = _paths(name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    lock_name = "rt" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]
    with profiles._lock(lock_name):
        if expected_profile_revision is not None or expected_source_sha256 is not None:
            _check_start_candidate(name, expected_profile_revision, expected_source_sha256)
        procs.end_leftovers(profile=name)               # a server or engine a lost owner left behind (verified one by one)
        if _read_state(name) is not None:
            _recover_stale(name, wait=wait)
        descriptor = os.open(log_path, flags, 0o600)
        with atomic.fdopen_owned(descriptor, "ab") as stream:
            command = [sys.executable, "-m", "strata_control.runtime", "--host", name,
                       "--shutdown-timeout", str(shutdown_timeout),
                       "--startup-wait", str(wait if wait else 300)]
            if expected_profile_revision is not None:
                command += ["--expected-profile-revision", expected_profile_revision]
            if expected_source_sha256 is not None:
                command += ["--expected-source-sha256", expected_source_sha256]
            if die_with is not None:
                command += ["--die-with-pid", str(die_with[0]), "--die-with-token", die_with[1]]
            proc = subprocess.Popen(command,
                                    cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stream,
                                    stderr=subprocess.STDOUT, close_fds=True,
                                    start_new_session=True)
        _STARTED_HOSTS[name] = proc
        deadline = time.monotonic() + max(5.0, wait)
        while _read_state(name) is None and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if _read_state(name) is None:
            if proc.poll() is not None:
                _STARTED_HOSTS.pop(name, None)
            raise ProfileError("runtime host failed to start; inspect " + str(log_path), 6)
    if wait == 0:
        return {"ok": True, "profile": name, "state": "starting", "plan": plan}
    deadline = time.monotonic() + wait
    result: dict = {}
    while time.monotonic() < deadline:
        result = _rpc(name, "status")
        if on_status is not None:
            try:
                on_status(result)
            except Exception:                                   # noqa: BLE001 - progress must never break a start
                on_status = None
        if result.get("state") in ("ready", "failed"):
            return {**result, "ok": result["state"] == "ready",
                    "exit_code": 6 if result["state"] == "failed" else 0}
        time.sleep(0.2)
    return {"ok": False, "profile": name, "state": "starting", "error": "startup_wait_expired",
            "phase": result.get("phase"), "exit_code": 8}


def plan_stop(name: str) -> dict:
    current = status(_name(name))
    if current.get("state") not in ("ready", "starting", "failed") or not current.get("owner_id"):
        return {"ok": False, "profile": name, "state": current.get("state", "unknown"),
                "error": "runtime_not_owned", "exit_code": 6}
    return {"ok": True, "plan": {"profile": name, "state": current["state"],
                                 "owner_id": current["owner_id"],
                                 "instance_id": current.get("instance_id"),
                                 "profile_revision": current.get("profile_revision"),
                                 "would_drain": False, "would_stop": False}}


def stop(name: str, *, timeout: float = 330,
         expected_owner_id: str | None = None,
         expected_instance_id: str | None = None) -> dict:
    if not 1 <= timeout <= 600:
        raise ProfileError("stop wait must be 1..600 seconds")
    name = _name(name)
    if expected_owner_id is not None and (not isinstance(expected_owner_id, str) or
            not re.fullmatch(r"[0-9a-f]{32}", expected_owner_id)):
        raise ProfileError("expected owner ID must be 32 lowercase hex characters")
    if expected_instance_id is not None and (not isinstance(expected_instance_id, str) or
            not re.fullmatch(r"[0-9a-f]{32}", expected_instance_id)):
        raise ProfileError("expected instance ID must be 32 lowercase hex characters")
    params = {"expected_owner_id": expected_owner_id,
              "expected_instance_id": expected_instance_id}
    lost = _stop_lost(name)
    if lost is not None:
        return lost
    result = _rpc(name, "stop", timeout=timeout, params=params)
    if not result["ok"]:
        return result
    owned = _STARTED_HOSTS.pop(name, None)
    if owned is not None:
        try:
            owned.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _STARTED_HOSTS[name] = owned
    return result


def _stop_lost(name: str) -> dict | None:
    """`stop` when there is no live owner host: the host was killed or crashed, so nobody can be asked to stop the
    server it started. End what it left behind - the recorded server and engine, each re-checked by PID, start time
    and command line - and clear the private state. None when an owner is running (the normal stop applies)."""
    try:
        state = _read_state(name)
    except ProfileError:
        state = None
    if state is not None:
        try:
            if _host_lease_active(name):
                return None
        except FileNotFoundError:
            state = None
    outcome = procs.end_leftovers(profile=name)
    recovered = False
    if state is not None:
        recovered = _recover_stale(name, wait=20)
    if state is None and not any(outcome[key] for key in ("ended", "stuck", "unverifiable")):
        return None                                   # nothing recorded: the normal "not managed or is stopped"
    stuck = bool(outcome["stuck"] or outcome["unverifiable"])
    return {"ok": not stuck, "profile": name, "state": "failed" if stuck else "stopped", "recovered": recovered,
            "ended": outcome["ended"], "leftover_unverified": outcome["unverifiable"],
            "exit_code": 6 if stuck else 0,
            **({"error": "leftover_process_not_ended"} if stuck else {})}


def cleanup(profile: str | None = None) -> dict:
    """End the servers and engines Strata started whose owner is gone (`strata runtime cleanup`). Owned instances
    that are running are not touched, and neither is any process that is not exactly what Strata recorded."""
    if profile is not None:
        profile = _name(profile)
    outcome = procs.end_leftovers(profile=profile)
    if profile is not None:
        try:
            if _read_state(profile) is not None and not _host_lease_active(profile):
                outcome["recovered"] = _recover_stale(profile, wait=20)
        except (ProfileError, FileNotFoundError):
            pass
    stuck = bool(outcome["stuck"] or outcome["unverifiable"])
    return {"ok": not stuck, "state": "cleaned" if not stuck else "failed", **outcome,
            "exit_code": 6 if stuck else 0}


def verify(name: str, *, timeout: float = 300, load: bool = False,
           expected_owner_id: str | None = None,
           expected_instance_id: str | None = None) -> dict:
    if not 1 <= timeout <= 600 or type(load) is not bool:
        raise ProfileError("verify wait must be 1..600 seconds and load must be true or false")
    for label, identity in (("owner", expected_owner_id), ("instance", expected_instance_id)):
        if identity is not None and (not isinstance(identity, str) or
                                     not re.fullmatch(r"[0-9a-f]{32}", identity)):
            raise ProfileError(f"expected {label} ID must be 32 lowercase hex characters")
    return _rpc(_name(name), "verify", timeout=timeout + 5,
                params={"wait": timeout, "load": load,
                        "expected_owner_id": expected_owner_id,
                        "expected_instance_id": expected_instance_id})


def restart(name: str, *, timeout: float = 1000, startup_wait: float = 300,
            expected_profile_revision: str | None = None,
            expected_owner_id: str | None = None,
            expected_instance_id: str | None = None,
            allow_source_switch: bool = False,
            allow_source_migration: bool = False,
            expected_source_sha256: str | None = None) -> dict:
    if (not 1 <= timeout <= 1800 or not 1 <= startup_wait <= MAX_START_WAIT or
            type(allow_source_switch) is not bool or type(allow_source_migration) is not bool):
        raise ProfileError(f"restart wait must be 1..1800 and startup wait 1..{MAX_START_WAIT} seconds")
    if expected_source_sha256 is not None and (not isinstance(expected_source_sha256, str) or
                                               not re.fullmatch(r"[0-9a-f]{64}", expected_source_sha256)):
        raise ProfileError("expected source SHA-256 must be 64 lowercase hex characters")
    for label, identity in (("owner", expected_owner_id), ("instance", expected_instance_id)):
        if identity is not None and (not isinstance(identity, str) or
                                     not re.fullmatch(r"[0-9a-f]{32}", identity)):
            raise ProfileError(f"expected {label} ID must be 32 lowercase hex characters")
    if (allow_source_switch or allow_source_migration) and expected_source_sha256 is None:
        raise ProfileError("source change needs the candidate SHA-256 from runtime restart --plan")
    params = {"startup_wait": startup_wait, "allow_source_switch": allow_source_switch,
              "allow_source_migration": allow_source_migration}
    if expected_source_sha256 is not None:
        params["expected_source_sha256"] = expected_source_sha256
    if expected_profile_revision is not None:
        params["expected_profile_revision"] = expected_profile_revision
    if expected_owner_id is not None:
        params["expected_owner_id"] = expected_owner_id
    if expected_instance_id is not None:
        params["expected_instance_id"] = expected_instance_id
    return _rpc(_name(name), "restart", timeout=timeout, params=params)


def plan_restart(name: str, *, allow_source_switch: bool = False,
                 allow_source_migration: bool = False) -> dict:
    if type(allow_source_switch) is not bool or type(allow_source_migration) is not bool:
        raise ProfileError("source change options must be true or false")
    return _rpc(_name(name), "plan_restart", timeout=10,
                params={"allow_source_switch": allow_source_switch,
                        "allow_source_migration": allow_source_migration})


class Host:
    def __init__(self, name: str, shutdown_timeout: float, startup_wait: float = 300,
                 expected_profile_revision: str | None = None,
                 expected_source_sha256: str | None = None,
                 die_with: tuple[int, str] | None = None):
        self.name = _name(name)
        self.die_with = die_with           # (pid, start token): stop the server when that process is gone
        self.shutdown_timeout = shutdown_timeout
        self.expected_profile_revision = expected_profile_revision
        self.expected_source_sha256 = expected_source_sha256
        self.state_path, self.socket_path, self.log_path = _paths(name)
        self.token = secrets.token_hex(32)
        self.owner_id = uuid.uuid4().hex
        self.child: subprocess.Popen | None = None
        self.ready_path: Path | None = None
        self.args: list[str] | None = None
        self.source: Path | None = None
        self.source_bytes: bytes | None = None
        self.expected_attestation: str | None = None
        self.revision: str | None = None
        self.last_error: str | None = None
        self.running = True
        self.state_fd: int | None = None
        self.owner_write_fd: int | None = None
        self.probe_timeout = startup_wait
        self.probe_deadline = 0.0
        self.probe_key: tuple[int, str] | None = None
        self.probe_result: dict | None = None
        self.rollback_snapshots: set[Path] = set()

    def _mark_child(self, phase: str, *, pid: int | None = None, ready: Path | None = None) -> None:
        payload = {"host_token": self.token, "phase": phase}
        if pid is not None and ready is not None:
            payload.update(pid=pid, ready=str(ready))
        profiles._atomic(_child_marker(self.name, self.token), (json.dumps(payload) + "\n").encode())

    def _plan(self) -> tuple[dict, list[str], Path, bytes]:
        from strata_cli.cli import _launch_plan
        plan, args = _launch_plan(self.name)
        source = Path(plan["source_config"])
        return plan, args, source, source.read_bytes()

    def _source_change_check(self, source: Path, source_bytes: bytes,
                             allow_source_switch: bool,
                             allow_source_migration: bool) -> tuple[bool, bool, str | None]:
        """Identify when rollback needs an owner-private copy of the running config."""
        if source == self.source:
            migrated = source_bytes != self.source_bytes
            return False, migrated, ("source_config_changed_restart_requires_migration"
                                     if migrated and not allow_source_migration else None)
        if not allow_source_switch:
            return True, False, "source_switch_requires_opt_in"
        try:
            old_stat, new_stat = self.source.lstat(), source.lstat()
            if (not stat.S_ISREG(old_stat.st_mode) or not stat.S_ISREG(new_stat.st_mode) or
                    (old_stat.st_dev, old_stat.st_ino) == (new_stat.st_dev, new_stat.st_ino)):
                return True, False, "source_switch_requires_distinct_regular_files"
            migrated = self.source.read_bytes() != self.source_bytes
        except OSError:
            return True, False, "rollback_source_unavailable"
        return True, migrated, ("rollback_source_changed" if migrated and not allow_source_migration else None)

    def _snapshot_running_config(self) -> Path:
        """Save launch bytes before drain; never overwrite the operator's edited file."""
        path = self.state_path.parent / f"{self.name}-{self.token}-{uuid.uuid4().hex}.rollback.json"
        descriptor = _write_new(path, self.source_bytes)
        os.close(descriptor)
        self.rollback_snapshots.add(path)
        return path

    def _retire_snapshot(self, path: Path) -> bool:
        if path in self.rollback_snapshots:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                return False
            self.rollback_snapshots.discard(path)
        return True

    @staticmethod
    def _config_args(args: list[str], source: Path) -> list[str]:
        result = list(args)
        try:
            index = result.index("--config")
            result[index + 1] = str(source)
        except (ValueError, IndexError):
            raise ProfileError("owned launch has no run config argument", 6) from None
        return result

    def _launch(self, args: list[str], source: Path, source_bytes: bytes,
                revision: str | None) -> None:
        ready = self.state_path.parent / f"{self.name}-{uuid.uuid4().hex}.ready.json"
        self.ready_path = ready
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        log = self.state_path.parent / f"{self.name}.server.log"
        self._mark_child("launching")
        descriptor = os.open(log, flags, 0o600)
        try:
            reader, writer = os.pipe()
        except BaseException:
            os.close(descriptor)
            raise
        server_args = [*args, "--ready-file", str(ready),
                       "--shutdown-timeout", str(self.shutdown_timeout),
                       "--owner-fd", str(reader)]
        attestation_key = secrets.token_bytes(32)
        expected_attestation = launch_identity.attestation(attestation_key, source_bytes, server_args)
        child_env = os.environ.copy()
        child_env["STRATA_OWNER_ATTEST_KEY"] = attestation_key.hex()
        child_env["STRATA_NO_ACQUIRE"] = "1"      # a managed server never installs or downloads (acquisition.py)
        child_env["STRATA_RUNTIME_PROFILE"] = self.name   # the records of its engine say which profile they belong to
        child_env["STRATA_OWNER_ID"] = self.owner_id
        stream = None
        try:
            stream = atomic.fdopen_owned(descriptor, "ab")
            with stream:
                self.child = subprocess.Popen([sys.executable, "-m", "serve.server", *server_args],
                                              cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stream,
                                              stderr=subprocess.STDOUT, close_fds=True,
                                              pass_fds=(reader,), start_new_session=True, env=child_env)
        except BaseException:
            try:
                os.close(writer)
            finally:
                try:
                    self._clear_ready()
                except OSError:
                    pass
            raise
        finally:
            os.close(reader)
        self.owner_write_fd = writer
        try:
            procs.write_record(self.child.pid, "server", profile=self.name)
            self._mark_child("owned", pid=self.child.pid, ready=ready)
        except BaseException:
            # No ownership record means a future start cannot safely reclaim
            # this instance. Stop this child immediately instead.
            self._abort_launch()
            raise
        self.args, self.source, self.source_bytes, self.revision = list(args), source, source_bytes, revision
        self.expected_attestation = expected_attestation
        self.last_error = None
        self.probe_deadline = time.monotonic() + self.probe_timeout
        self.probe_key = None
        self.probe_result = None

    def _verify_ready(self, ready: dict, key: tuple[int, str], host: str, source_bytes: bytes,
                      expected_attestation: str, timeout: float) -> None:
        try:
            result = readiness.verify(ready, source_bytes, expected_host=host,
                                      expected_instance=key[1],
                                      expected_attestation=expected_attestation, timeout=timeout)
        except readiness.ReadinessError as exc:
            result = {"error": str(exc)}
        except Exception:
            result = {"error": "probe_internal_error"}
        if self.probe_key == key:
            self.probe_result = result
            if "error" in result:
                self.last_error = result["error"]

    def _log_tail(self, limit: int = 4096) -> str | None:
        """The last lines the server wrote (a failed start is explained by them), or None. Bounded read, never raises."""
        try:
            with open(self.state_path.parent / f"{self.name}.server.log", "rb") as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - limit))
                lines = [x.rstrip() for x in stream.read().decode("utf-8", "replace").splitlines() if x.strip()]
        except OSError:
            return None
        # not the indented frames of a Python traceback: the engine's own lines and the final exception line say why
        plain = [x for x in lines if not x[0].isspace() and not x.startswith("Traceback")]
        chosen = plain[-3:] or lines[-1:]
        return " | ".join(x[:200] for x in chosen) if chosen else None

    def _status(self) -> dict:
        child = self.child
        base = {"ok": True, "profile": self.name, "host_pid": os.getpid(),
                "owner_id": self.owner_id,
                "child_pid": child.pid if child else None, "profile_revision": self.revision,
                "shutdown_timeout": self.shutdown_timeout}
        if child is None:
            if self.last_error is not None:
                return {**base, "ok": False, "state": "failed", "error": self.last_error, "exit_code": 6}
            return {**base, "state": "stopped"}
        if child.poll() is not None:
            # `exit_code` is Control's own; the server's code also travels under `engine_exit_code`.
            return {**base, "ok": False, "state": "failed", "exit_code": child.returncode, "engine_exit_code": child.returncode,
                    "error": self.last_error or "server_exited", "log_tail": self._log_tail()}
        if self.ready_path is not None:
            try:
                ready = json.loads(self.ready_path.read_text(encoding="utf-8"))
                if _ready_pid_is_child(ready.get("pid"), child.pid) and isinstance(ready.get("instance_id"), str):
                    key = (child.pid, ready["instance_id"])
                    if self.probe_key != key:
                        self.probe_key, self.probe_result = key, None
                        host = self.args[self.args.index("--host") + 1]
                        probe = threading.Thread(
                            target=self._verify_ready,
                            args=(ready, key, host, self.source_bytes,
                                  self.expected_attestation,
                                  max(0.1, self.probe_deadline - time.monotonic())),
                            name="strata-generation-probe", daemon=True)
                        try:
                            probe.start()
                        except RuntimeError:
                            # Readiness probing is auxiliary. A temporary
                            # process thread limit must not tear down the host
                            # and the serving child; retry on the next status.
                            self.probe_key = None
                            self.probe_result = None
                    if self.probe_result is None:
                        return {**base, "state": "starting", "phase": "verifying_generation",
                                "instance_id": ready["instance_id"]}
                    if "error" in self.probe_result:
                        return {**base, "ok": False, "state": "failed", "error": self.probe_result["error"],
                                "exit_code": 6, "instance_id": ready["instance_id"]}
                    return {**base, "state": "ready", "instance_id": ready["instance_id"],
                            "model_state": ready.get("model_state"),
                            **self.probe_result}
                if isinstance(ready, dict):
                    # the server published its ready file but it is not the process this host started: say so, so a wait that
                    # ends can name it (before #3683 this looked exactly like a model that was still loading)
                    return {**base, "state": "starting", "phase": "ready_file_not_matched"}
            except (OSError, ValueError):
                pass
        return {**base, "state": "starting", "phase": "loading"}

    def _wait_ready(self, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self._status()
            if current["state"] in ("ready", "failed"):
                return current
            time.sleep(0.15)
        current = self._status()
        if current["state"] in ("ready", "failed"):
            return current
        return {**current, "ok": False, "error": "startup_wait_expired", "exit_code": 8}

    def _clear_ready(self) -> None:
        if self.ready_path is not None:
            self.ready_path.unlink(missing_ok=True)
            self.ready_path = None

    def _stop_child(self) -> dict:
        child = self.child
        if child is None or child.poll() is not None:
            self._close_owner_pipe()
            self._clear_ready()
            return {"ok": True, "state": "stopped", "exit_code": child.returncode if child else 0}
        try:
            child.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            pass        # it exited between poll() and the signal; wait() below still collects the code
        try:
            code = child.wait(timeout=self.shutdown_timeout + 8)
        except (OSError, subprocess.TimeoutExpired):
            self._kill_child_tree()
            try:
                child.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                self._close_owner_pipe()
                self._clear_ready()
                return {"ok": False, "state": "failed", "error": "server_stop_deadline_expired", "exit_code": 8}
            self._close_owner_pipe()
            self._clear_ready()
            return {"ok": False, "state": "failed", "error": "server_stop_deadline_expired", "exit_code": 8}
        self._close_owner_pipe()
        self._clear_ready()
        return {"ok": code == 0, "state": "stopped" if code == 0 else "failed",
                "exit_code": code, "error": None if code == 0 else "server_stop_failed"}

    def _close_owner_pipe(self) -> None:
        if self.owner_write_fd is not None:
            descriptor = self.owner_write_fd
            self.owner_write_fd = None
            try:
                os.close(descriptor)
            except OSError:
                pass

    def _abort_launch(self) -> None:
        """Best-effort teardown after Popen succeeded but ownership was not published."""
        child = self.child
        try:
            self._kill_child_tree()
        except OSError:
            pass
        if child is not None:
            try:
                child.wait(timeout=10)
            except (subprocess.TimeoutExpired, OSError):
                pass
        self._close_owner_pipe()
        try:
            self._clear_ready()
        except OSError:
            pass

    def _kill_child_tree(self) -> None:
        """Kill the server this host started and its dedicated process group."""
        child = self.child
        if child is None or child.poll() is not None:
            return
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            try:
                child.kill()
            except OSError:
                pass

    def _restart(self, startup_wait: float, expected_profile_revision: str | None = None,
                 allow_source_switch: bool = False,
                 allow_source_migration: bool = False,
                 expected_source_sha256: str | None = None,
                 expected_owner_id: str | None = None,
                 expected_instance_id: str | None = None) -> dict:
        old = self._status()
        if expected_owner_id is not None and old.get("owner_id") != expected_owner_id:
            return {"ok": False, "error": "owner_changed_after_restart_plan", "exit_code": 6}
        if expected_instance_id is not None and old.get("instance_id") != expected_instance_id:
            return {"ok": False, "error": "instance_changed_after_restart_plan", "exit_code": 6}
        if old["state"] != "ready":
            return {"ok": False, "state": old["state"], "error": "runtime_not_ready", "exit_code": 6}
        try:
            plan, new_args, source, source_bytes = self._plan()
        except (ProfileError, OSError):
            return {"ok": False, "state": "ready", "error": "new_profile_preflight_failed", "exit_code": 5}
        switched, migrated, source_error = self._source_change_check(
            source, source_bytes, allow_source_switch, allow_source_migration)
        if source_error:
            return {"ok": False, "state": "ready", "error": source_error, "exit_code": 6}
        if expected_source_sha256 is not None and \
                hashlib.sha256(source_bytes).hexdigest() != expected_source_sha256:
            return {"ok": False, "state": "ready", "error": "source_changed_after_preview", "exit_code": 6}
        if expected_profile_revision is not None and plan["profile_revision"] != expected_profile_revision:
            return {"ok": False, "state": "ready", "error": "profile_changed_after_preview", "exit_code": 6}
        current = self._status()
        if expected_instance_id is not None and current.get("instance_id") != expected_instance_id:
            return {"ok": False, "error": "instance_changed_after_restart_plan", "exit_code": 6}
        if current["state"] != "ready":
            return {"ok": False, "state": current["state"], "error": "runtime_not_ready", "exit_code": 6}
        old_args, old_source, old_bytes, old_revision = self.args, self.source, self.source_bytes, self.revision
        rollback_source = old_source
        rollback_args = old_args
        if migrated:
            try:
                rollback_source = self._snapshot_running_config()
                rollback_args = self._config_args(old_args, rollback_source)
            except (OSError, ProfileError):
                if rollback_source != old_source:
                    self._retire_snapshot(rollback_source)
                return {"ok": False, "state": "ready", "error": "rollback_snapshot_failed", "exit_code": 6}
        self.probe_timeout = startup_wait
        stopped = self._stop_child()
        if not stopped["ok"]:
            return {**stopped, "profile": self.name, "previous_instance_id": old.get("instance_id")}
        try:
            if source.read_bytes() != source_bytes:
                raise ProfileError("source changed during drain", 6)
            self._launch(new_args, source, source_bytes, plan["profile_revision"])
            result = self._wait_ready(startup_wait)
            if result.get("ok") and result["state"] == "ready":
                cleanup_ok = True
                if rollback_source != source:
                    cleanup_ok = self._retire_snapshot(rollback_source) and cleanup_ok
                if old_source != source:
                    cleanup_ok = self._retire_snapshot(old_source) and cleanup_ok
                return {**result, "previous_instance_id": old.get("instance_id"), "restarted": True,
                        "source_switched": switched, "source_migrated": migrated,
                        "rollback_snapshot_cleanup_pending": not cleanup_ok}
            if self.child is not None and self.child.poll() is None:
                self._stop_child()
        except (ProfileError, OSError):
            result = {"state": "failed", "error": "new_instance_launch_failed", "exit_code": 6}
        # The old launch needs its original bytes, even if the operator edited
        # the same file. A private snapshot is used only for explicit migration.
        try:
            old_source_unchanged = rollback_source.read_bytes() == old_bytes
        except OSError:
            old_source_unchanged = False
        if old_source_unchanged:
            try:
                self._launch(rollback_args, rollback_source, old_bytes, old_revision)
                rollback = self._wait_ready(startup_wait)
            except (OSError, ProfileError):
                rollback = {"state": "failed"}
            if rollback.get("ok") and rollback.get("state") == "ready":
                cleanup_ok = True
                if old_source != rollback_source:
                    cleanup_ok = self._retire_snapshot(old_source)
                return {"ok": False, "state": "ready", "error": result.get("error", "new_instance_failed"),
                        "exit_code": 6, "rolled_back": True,
                        "rollback_snapshot_cleanup_pending": not cleanup_ok,
                        "rollback_source_config": str(rollback_source),
                        "instance_id": rollback.get("instance_id"),
                        "previous_instance_id": old.get("instance_id")}
        return {"ok": False, "state": "failed", "error": result.get("error", "restart_failed"),
                "exit_code": 6, "rolled_back": False}

    def _plan_restart(self, allow_source_switch: bool = False,
                      allow_source_migration: bool = False) -> dict:
        current = self._status()
        if current["state"] != "ready":
            return {"ok": False, "state": current["state"], "error": "runtime_not_ready", "exit_code": 6}
        try:
            plan, candidate_args, source, source_bytes = self._plan()
        except (ProfileError, OSError):
            return {"ok": False, "state": "ready", "error": "new_profile_preflight_failed", "exit_code": 5}
        switched, migrated, source_error = self._source_change_check(
            source, source_bytes, allow_source_switch, allow_source_migration)
        if source_error:
            return {"ok": False, "state": "ready", "error": source_error, "exit_code": 6,
                    "source_switch": switched, "source_migration": migrated,
                    "requires_allow_source_switch": switched and not allow_source_switch,
                    "requires_allow_source_migration": migrated and not allow_source_migration}
        from strata_cli.cli import _redact_argv
        return {"ok": True, "state": "ready", "profile": self.name,
                "current_owner_id": current["owner_id"],
                "current_instance_id": current["instance_id"],
                "current_profile_revision": self.revision,
                "candidate_profile_revision": plan["profile_revision"],
                "current_source_config": str(self.source), "candidate_source_config": str(source),
                "current_source_sha256": hashlib.sha256(self.source_bytes).hexdigest(),
                "candidate_source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "source_switch": switched, "source_migration": migrated,
                "rollback_snapshot_required": migrated,
                "current_server_argv": _redact_argv(self.args),
                "candidate_server_argv": _redact_argv(candidate_args),
                "changed": switched or migrated or self.args != candidate_args or
                           self.revision != plan["profile_revision"],
                "shutdown_timeout": self.shutdown_timeout,
                "would_drain": False, "would_start": False}

    def _handle(self, request: dict) -> dict:
        if not secrets.compare_digest(str(request.get("token", "")), self.token):
            return {"ok": False, "error": "unauthorized", "exit_code": 4}
        command = request.get("command")
        if command == "status":
            return self._status()
        if command == "verify":
            timeout, force_load = request.get("wait", 300), request.get("load", False)
            if type(timeout) not in (int, float) or not 1 <= timeout <= 600 or type(force_load) is not bool:
                return {"ok": False, "error": "invalid_verification_options", "exit_code": 2}
            owner = request.get("expected_owner_id")
            instance = request.get("expected_instance_id")
            for label, identity in (("owner", owner), ("instance", instance)):
                if identity is not None and (not isinstance(identity, str) or
                                             not re.fullmatch(r"[0-9a-f]{32}", identity)):
                    return {"ok": False, "error": f"invalid_expected_{label}_id", "exit_code": 2}
            if (owner is None) != (instance is None):
                return {"ok": False, "error": "verification_requires_both_identities", "exit_code": 2}
            current = self._status()
            if owner is not None and owner != self.owner_id:
                return {"ok": False, "error": "owner_changed_after_verify_plan", "exit_code": 6}
            if instance is not None and current.get("instance_id") != instance:
                return {"ok": False, "error": "instance_changed_after_verify_plan", "exit_code": 6}
            if current["state"] != "ready" or self.ready_path is None or self.child is None:
                return {"ok": False, "state": current["state"], "error": "runtime_not_ready", "exit_code": 6}
            try:
                ready = json.loads(self.ready_path.read_text(encoding="utf-8"))
                if not _ready_pid_is_child(ready.get("pid"), self.child.pid) or ready.get("instance_id") != current["instance_id"]:
                    raise readiness.ReadinessError("probe_instance_mismatch")
                host = self.args[self.args.index("--host") + 1]
                result = readiness.verify(ready, self.source_bytes, expected_host=host,
                                          expected_instance=current["instance_id"],
                                          expected_attestation=self.expected_attestation, timeout=timeout,
                                          force_load=force_load)
            except readiness.ReadinessError as exc:
                return {"ok": False, "state": current["state"], "instance_id": current["instance_id"],
                        "error": str(exc), "exit_code": 6}
            except (OSError, ValueError):
                return {"ok": False, "state": current["state"], "instance_id": current["instance_id"],
                        "error": "probe_invalid_ready_file", "exit_code": 6}
            if self.probe_key == (self.child.pid, current["instance_id"]):
                self.probe_result = result
            return {**self._status(), "verified_now": True}
        if command == "stop":
            owner = request.get("expected_owner_id")
            instance = request.get("expected_instance_id")
            if owner is not None and (not isinstance(owner, str) or
                    not re.fullmatch(r"[0-9a-f]{32}", owner)):
                return {"ok": False, "error": "invalid_expected_owner_id", "exit_code": 2}
            if instance is not None and (not isinstance(instance, str) or
                    not re.fullmatch(r"[0-9a-f]{32}", instance)):
                return {"ok": False, "error": "invalid_expected_instance_id", "exit_code": 2}
            if owner is not None and owner != self.owner_id:
                return {"ok": False, "error": "owner_changed_after_stop_plan", "exit_code": 6}
            if instance is not None and self._status().get("instance_id") != instance:
                return {"ok": False, "error": "instance_changed_after_stop_plan", "exit_code": 6}
            result = self._stop_child()
            self.running = False
            return {**result, "profile": self.name}
        if command == "restart":
            startup_wait = request.get("startup_wait", 300)
            if type(startup_wait) not in (int, float) or not 1 <= startup_wait <= MAX_START_WAIT:
                return {"ok": False, "error": "invalid_startup_wait", "exit_code": 2}
            revision = request.get("expected_profile_revision")
            if revision is not None and (not isinstance(revision, str) or
                                         not re.fullmatch(r"[0-9a-f]{16}", revision)):
                return {"ok": False, "error": "invalid_profile_revision", "exit_code": 2}
            allow_source_switch = request.get("allow_source_switch", False)
            if type(allow_source_switch) is not bool:
                return {"ok": False, "error": "invalid_source_switch_option", "exit_code": 2}
            allow_source_migration = request.get("allow_source_migration", False)
            if type(allow_source_migration) is not bool:
                return {"ok": False, "error": "invalid_source_migration_option", "exit_code": 2}
            expected_source_sha256 = request.get("expected_source_sha256")
            if (allow_source_switch or allow_source_migration) and \
                    (not isinstance(expected_source_sha256, str) or
                     not re.fullmatch(r"[0-9a-f]{64}", expected_source_sha256)):
                return {"ok": False, "error": "source_change_needs_candidate_digest", "exit_code": 2}
            if expected_source_sha256 is not None and (not isinstance(expected_source_sha256, str) or
                                                       not re.fullmatch(r"[0-9a-f]{64}", expected_source_sha256)):
                return {"ok": False, "error": "invalid_source_digest", "exit_code": 2}
            expected_owner_id = request.get("expected_owner_id")
            expected_instance_id = request.get("expected_instance_id")
            if expected_owner_id is not None and (not isinstance(expected_owner_id, str) or
                                                  not re.fullmatch(r"[0-9a-f]{32}", expected_owner_id)):
                return {"ok": False, "error": "invalid_expected_owner_id", "exit_code": 2}
            if expected_instance_id is not None and (not isinstance(expected_instance_id, str) or
                                                     not re.fullmatch(r"[0-9a-f]{32}", expected_instance_id)):
                return {"ok": False, "error": "invalid_expected_instance_id", "exit_code": 2}
            return self._restart(startup_wait, revision, allow_source_switch,
                                 allow_source_migration, expected_source_sha256,
                                 expected_owner_id, expected_instance_id)
        if command == "plan_restart":
            allow_source_switch = request.get("allow_source_switch", False)
            allow_source_migration = request.get("allow_source_migration", False)
            if type(allow_source_switch) is not bool or type(allow_source_migration) is not bool:
                return {"ok": False, "error": "invalid_source_change_options", "exit_code": 2}
            return self._plan_restart(allow_source_switch, allow_source_migration)
        return {"ok": False, "error": "unsupported_runtime_command", "exit_code": 2}

    def serve(self) -> int:
        activity_lease = acquire_activity_lease(shared=True)
        listener = owner_transport.Listener(self.socket_path, timeout=1.0)
        try:
            bound_address = listener.bind()
            def shutdown(signum, frame):
                self.running = False
            signal.signal(signal.SIGTERM, shutdown)
            signal.signal(signal.SIGINT, shutdown)
            self.state_fd = _write_new(self.state_path, (json.dumps({"schema_version": 1, "profile": self.name,
                                                                     "socket": bound_address, "token": self.token,
                                                                     "host_pid": os.getpid()}) + "\n").encode())
            try:
                plan, args, source, source_bytes = self._plan()
                if (self.expected_profile_revision is not None and
                        plan["profile_revision"] != self.expected_profile_revision):
                    raise ProfileError("profile_changed_after_start_plan", 6)
                if (self.expected_source_sha256 is not None and
                        hashlib.sha256(source_bytes).hexdigest() != self.expected_source_sha256):
                    raise ProfileError("source_changed_after_start_plan", 6)
                self._launch(args, source, source_bytes, plan["profile_revision"])
            except (ProfileError, OSError) as exc:
                self.last_error = str(exc) if isinstance(exc, ProfileError) else type(exc).__name__
            while self.running:
                if self.die_with is not None and procs.start_token(self.die_with[0]) != self.die_with[1]:
                    self.running = False         # the process this instance lives for is gone: stop (drained) and leave
                    break
                if self.probe_result is not None and "error" in self.probe_result and \
                        self.child is not None and self.child.poll() is None:
                    self._stop_child()
                try:
                    connection = listener.accept()
                except socket.timeout:
                    continue
                if connection is None:
                    # A quiet timeout: the owner loops so it can still service its child.
                    continue
                with connection:
                    connection.settimeout(5)
                    raw = bytearray()
                    try:
                        while len(raw) <= MAX_MESSAGE and b"\n" not in raw:
                            chunk = connection.recv(4096)
                            if not chunk:
                                break
                            raw.extend(chunk)
                        if len(raw) > MAX_MESSAGE:
                            raise ValueError("message too large")
                        request = json.loads(raw.split(b"\n", 1)[0])
                        if not isinstance(request, dict):
                            raise ValueError("request must be an object")
                        response = self._handle(request)
                    except (OSError, ValueError):
                        response = {"ok": False, "error": "invalid_control_request", "exit_code": 2}
                    try:
                        connection.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode())
                    except OSError:
                        pass
        finally:
            if self.child is not None and self.child.poll() is None:
                self._stop_child()
            self._close_owner_pipe()
            listener.close()
            try:
                current = _read_state(self.name)
                if current and current.get("token") == self.token:
                    self.state_path.unlink(missing_ok=True)
            except ProfileError:
                pass
            Path(self.socket_path).unlink(missing_ok=True)
            if self.ready_path is not None:
                self.ready_path.unlink(missing_ok=True)
            _child_marker(self.name, self.token).unlink(missing_ok=True)
            for snapshot in tuple(self.rollback_snapshots):
                self._retire_snapshot(snapshot)
            if self.state_fd is not None:
                os.close(self.state_fd)
            _unlock_file(activity_lease)
            os.close(activity_lease)
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="strata-runtime-host")
    parser.add_argument("--host", required=True)
    parser.add_argument("--shutdown-timeout", type=float, default=30)
    parser.add_argument("--startup-wait", type=float, default=300)
    parser.add_argument("--expected-profile-revision")
    parser.add_argument("--expected-source-sha256")
    parser.add_argument("--die-with-pid", type=int)
    parser.add_argument("--die-with-token")
    args = parser.parse_args(argv)
    if not 0 <= args.shutdown_timeout <= 300 or not 1 <= args.startup_wait <= MAX_START_WAIT:
        parser.error("shutdown timeout must be 0..300")
    if args.expected_profile_revision is not None and not re.fullmatch(r"[0-9a-f]{16}", args.expected_profile_revision):
        parser.error("expected profile revision must be 16 lowercase hex characters")
    if args.expected_source_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", args.expected_source_sha256):
        parser.error("expected source digest must be 64 lowercase hex characters")
    try:
        die_with = (args.die_with_pid, args.die_with_token) if args.die_with_pid and args.die_with_token else None
        return Host(args.host, args.shutdown_timeout, args.startup_wait,
                    args.expected_profile_revision, args.expected_source_sha256, die_with).serve()
    except (ProfileError, OSError) as exc:
        print(f"[strata runtime] host failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 6


if __name__ == "__main__":
    raise SystemExit(main())
