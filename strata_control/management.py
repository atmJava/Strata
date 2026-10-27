"""Owner-local management protocol for the dedicated server runtime.

The protocol is intentionally limited to starting, checking, stopping and restarting
the saved production profile. It is not an HTTP API and carries no installer,
benchmarking, calibration or model-market operations.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import re
import sys
from typing import Any

from strata_control import journal, profiles, runtime
from strata_control.profiles import ProfileError


MAX_REQUEST_BYTES = 64 * 1024
_ACTIONS = {
    "runtime.start.plan": {"shutdown_timeout_s"},
    "runtime.start": {
        "wait_s",
        "shutdown_timeout_s",
        "if_profile_revision",
        "if_source_sha256",
    },
    "runtime.status": set(),
    "runtime.verify.plan": {"load"},
    "runtime.verify": {
        "wait_s",
        "load",
        "if_owner_id",
        "if_instance_id",
    },
    "runtime.stop.plan": set(),
    "runtime.stop": {"wait_s", "if_owner_id", "if_instance_id"},
    "runtime.restart.plan": {"allow_source_switch", "allow_source_migration"},
    "runtime.restart": {
        "wait_s",
        "startup_wait_s",
        "if_profile_revision",
        "if_owner_id",
        "if_instance_id",
        "allow_source_switch",
        "allow_source_migration",
        "if_source_sha256",
    },
}


def _seconds(value: object, low: float, high: float, label: str) -> float:
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise ProfileError(
            f"{label} must be finite seconds in {low:g}..{high:g}"
        )
    return float(value)


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ProfileError(f"{label} must be true or false")
    return value


def _optional_revision(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ProfileError(
            f"{label} must be a 64-character lowercase SHA-256"
        )
    return value


def _profile_revision(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{16}", value):
        raise ProfileError(
            "if_profile_revision must be a 16-character lowercase revision"
        )
    return value


def _optional_identity(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ProfileError(f"{label} must be a 32-character lowercase ID")
    return value


def runtime_action(
    action: str,
    profile: str,
    *,
    plan: bool = False,
    wait: float = 300,
    shutdown_timeout: float = 30,
    startup_wait: float = 300,
    load: bool = False,
    if_profile_revision: str | None = None,
    if_owner_id: str | None = None,
    if_instance_id: str | None = None,
    allow_source_switch: bool = False,
    allow_source_migration: bool = False,
    if_source_sha256: str | None = None,
    die_with_pid: int | None = None,
) -> dict:
    """Execute one validated owner operation and return its result."""
    runtime._name(profile)
    if type(plan) is not bool:
        raise ProfileError("plan must be true or false")
    if action == "start":
        wait = _seconds(wait, 0, 600, "start wait")
        shutdown_timeout = _seconds(
            shutdown_timeout, 0, 300, "shutdown timeout"
        )
        if_profile_revision = _profile_revision(if_profile_revision)
        if_source_sha256 = _optional_revision(
            if_source_sha256, "if_source_sha256"
        )
        if plan:
            if (
                if_profile_revision is not None
                or if_source_sha256 is not None
            ):
                raise ProfileError(
                    "start plan cannot include execution preconditions"
                )
            runtime._supported()
            profiles.load(profile)
            launch, source_sha256 = runtime._start_candidate(profile)
            repeated_launch, repeated_digest = runtime._start_candidate(profile)
            if launch != repeated_launch or source_sha256 != repeated_digest:
                raise ProfileError(
                    "start candidate changed during preview; retry", 6
                )
            return {
                "ok": True,
                "plan": {
                    "profile": profile,
                    "launch": launch,
                    "source_sha256": source_sha256,
                    "shutdown_timeout": shutdown_timeout,
                    "would_start": False,
                },
            }
        revision = profiles.load(profile)[1]
        if if_profile_revision is not None or if_source_sha256 is not None:
            runtime._check_start_candidate(
                profile, if_profile_revision, if_source_sha256
            )
        operation_id, result = journal.run_direct(
            profile,
            "runtime.start",
            revision,
            {
                "wait_s": wait,
                "shutdown_timeout_s": shutdown_timeout,
                "expected_profile_revision": if_profile_revision,
                "expected_source_sha256": if_source_sha256,
            },
            lambda: runtime.start(
                profile,
                wait=wait,
                shutdown_timeout=shutdown_timeout,
                expected_profile_revision=if_profile_revision,
                expected_source_sha256=if_source_sha256,
                die_with_pid=die_with_pid,
            ),
        )
    elif action == "status":
        if plan:
            raise ProfileError("status has no plan")
        return runtime.status(profile)
    elif action == "verify":
        load = _boolean(load, "load")
        if_owner_id = _optional_identity(if_owner_id, "if_owner_id")
        if_instance_id = _optional_identity(
            if_instance_id, "if_instance_id"
        )
        if plan:
            if if_owner_id is not None or if_instance_id is not None:
                raise ProfileError(
                    "verify plan cannot include execution preconditions"
                )
            current = runtime.status(profile)
            if not current.get("ok") or current.get("state") != "ready":
                return {
                    "ok": False,
                    "state": current.get("state", "unavailable"),
                    "error": "runtime_not_ready",
                    "exit_code": 6,
                }
            owner = current.get("owner_id")
            instance = current.get("instance_id")
            if (
                _optional_identity(owner, "owner_id") is None
                or _optional_identity(instance, "instance_id") is None
            ):
                raise ProfileError(
                    "runtime verification needs an owned ready instance", 6
                )
            return {
                "ok": True,
                "plan": {
                    "profile": profile,
                    "state": "ready",
                    "owner_id": owner,
                    "instance_id": instance,
                    "model_state": current.get("model_state"),
                    "load": load,
                    "would_load": (
                        load and current.get("model_state") == "unloaded"
                    ),
                    "would_verify": False,
                    "probe": "one_token_generation",
                },
            }
        wait = _seconds(wait, 1, 600, "verify wait")
        if (if_owner_id is None) != (if_instance_id is None):
            raise ProfileError(
                "verify requires both reviewed owner and instance IDs"
            )
        if if_owner_id is not None:
            current = runtime.status(profile)
            if current.get("owner_id") != if_owner_id:
                raise ProfileError("owner_changed_after_verify_plan", 6)
            if current.get("instance_id") != if_instance_id:
                raise ProfileError("instance_changed_after_verify_plan", 6)
        revision = profiles.load(profile)[1]
        operation_id, result = journal.run_direct(
            profile,
            "runtime.verify",
            revision,
            {
                "wait_s": wait,
                "load": load,
                "expected_owner_id": if_owner_id,
                "expected_instance_id": if_instance_id,
            },
            lambda: runtime.verify(
                profile,
                timeout=wait,
                load=load,
                expected_owner_id=if_owner_id,
                expected_instance_id=if_instance_id,
            ),
        )
    elif action == "stop":
        wait = _seconds(wait, 1, 600, "stop wait")
        if_owner_id = _optional_identity(if_owner_id, "if_owner_id")
        if_instance_id = _optional_identity(
            if_instance_id, "if_instance_id"
        )
        if plan:
            if if_owner_id is not None or if_instance_id is not None:
                raise ProfileError(
                    "stop plan cannot include execution preconditions"
                )
            return runtime.plan_stop(profile)
        if if_owner_id is not None:
            current = runtime.status(profile)
            if current.get("owner_id") != if_owner_id:
                raise ProfileError("owner_changed_after_stop_plan", 6)
            if current.get("instance_id") != if_instance_id:
                raise ProfileError("instance_changed_after_stop_plan", 6)
        revision = profiles.load(profile)[1]
        operation_id, result = journal.run_direct(
            profile,
            "runtime.stop",
            revision,
            {
                "wait_s": wait,
                "expected_owner_id": if_owner_id,
                "expected_instance_id": if_instance_id,
            },
            lambda: runtime.stop(
                profile,
                timeout=wait,
                expected_owner_id=if_owner_id,
                expected_instance_id=if_instance_id,
            ),
        )
    elif action == "restart":
        allow_source_switch = _boolean(
            allow_source_switch, "allow_source_switch"
        )
        allow_source_migration = _boolean(
            allow_source_migration, "allow_source_migration"
        )
        if_source_sha256 = _optional_revision(
            if_source_sha256, "if_source_sha256"
        )
        if_profile_revision = _profile_revision(if_profile_revision)
        if_owner_id = _optional_identity(if_owner_id, "if_owner_id")
        if_instance_id = _optional_identity(
            if_instance_id, "if_instance_id"
        )
        if plan:
            if any(
                value is not None
                for value in (
                    if_profile_revision,
                    if_source_sha256,
                    if_owner_id,
                    if_instance_id,
                )
            ):
                raise ProfileError(
                    "restart plan cannot include execution preconditions"
                )
            return runtime.plan_restart(
                profile,
                allow_source_switch=allow_source_switch,
                allow_source_migration=allow_source_migration,
            )
        wait = _seconds(wait, 1, 1800, "restart wait")
        startup_wait = _seconds(startup_wait, 1, 600, "startup wait")
        if (if_owner_id is None) != (if_instance_id is None):
            raise ProfileError(
                "restart requires both reviewed owner and instance IDs"
            )
        if (
            allow_source_switch or allow_source_migration
        ) and not if_source_sha256:
            raise ProfileError(
                "source change needs --if-source-sha256 from --plan"
            )
        if if_owner_id is not None:
            current = runtime.status(profile)
            if current.get("owner_id") != if_owner_id:
                raise ProfileError("owner_changed_after_restart_plan", 6)
            if current.get("instance_id") != if_instance_id:
                raise ProfileError("instance_changed_after_restart_plan", 6)
        revision = profiles.load(profile)[1]
        expected = if_profile_revision or revision
        operation_id, result = journal.run_direct(
            profile,
            "runtime.restart",
            revision,
            {
                "wait_s": wait,
                "startup_wait_s": startup_wait,
                "expected_profile_revision": expected,
                "expected_owner_id": if_owner_id,
                "expected_instance_id": if_instance_id,
                "allow_source_switch": allow_source_switch,
                "allow_source_migration": allow_source_migration,
                "expected_source_sha256": if_source_sha256,
            },
            lambda: runtime.restart(
                profile,
                timeout=wait,
                startup_wait=startup_wait,
                expected_profile_revision=expected,
                expected_owner_id=if_owner_id,
                expected_instance_id=if_instance_id,
                allow_source_switch=allow_source_switch,
                allow_source_migration=allow_source_migration,
                expected_source_sha256=if_source_sha256,
            ),
        )
    else:
        raise ProfileError("unsupported runtime action")
    return {**result, "operation_id": operation_id}


def dispatch(request: object) -> dict:
    """Validate one runtime request from the owner host."""
    if (
        not isinstance(request, dict)
        or set(request) != {"schema_version", "action", "profile", "params"}
        or type(request.get("schema_version")) is not int
        or request["schema_version"] != 1
    ):
        raise ProfileError(
            "management request needs schema_version 1, action, profile and params"
        )
    action, profile, params = (
        request["action"],
        request["profile"],
        request["params"],
    )
    if not isinstance(action, str) or action not in _ACTIONS:
        raise ProfileError("management action is unsupported")
    if (
        not isinstance(profile, str)
        or not profiles.PROFILE_ID.fullmatch(profile)
    ):
        raise ProfileError("management profile name is invalid")
    if not isinstance(params, dict) or set(params) != _ACTIONS[action]:
        raise ProfileError("management action parameters are invalid")

    if action == "runtime.start.plan":
        result = runtime_action(
            "start",
            profile,
            plan=True,
            shutdown_timeout=params["shutdown_timeout_s"],
        )
    elif action == "runtime.start":
        if (
            params["if_profile_revision"] is None
            or params["if_source_sha256"] is None
        ):
            raise ProfileError(
                "start needs the profile revision and source digest from its plan"
            )
        result = runtime_action(
            "start",
            profile,
            wait=params["wait_s"],
            shutdown_timeout=params["shutdown_timeout_s"],
            if_profile_revision=params["if_profile_revision"],
            if_source_sha256=params["if_source_sha256"],
        )
    elif action == "runtime.status":
        result = runtime_action("status", profile)
    elif action == "runtime.verify.plan":
        result = runtime_action(
            "verify", profile, plan=True, load=params["load"]
        )
    elif action == "runtime.verify":
        if (
            params["if_owner_id"] is None
            or params["if_instance_id"] is None
        ):
            raise ProfileError(
                "verify needs the owner and instance IDs from its plan"
            )
        result = runtime_action(
            "verify",
            profile,
            wait=params["wait_s"],
            load=params["load"],
            if_owner_id=params["if_owner_id"],
            if_instance_id=params["if_instance_id"],
        )
    elif action == "runtime.stop.plan":
        result = runtime_action("stop", profile, plan=True)
    elif action == "runtime.stop":
        if params["if_owner_id"] is None:
            raise ProfileError("stop needs the owner ID from its plan")
        result = runtime_action(
            "stop",
            profile,
            wait=params["wait_s"],
            if_owner_id=params["if_owner_id"],
            if_instance_id=params["if_instance_id"],
        )
    elif action == "runtime.restart.plan":
        result = runtime_action(
            "restart",
            profile,
            plan=True,
            allow_source_switch=params["allow_source_switch"],
            allow_source_migration=params["allow_source_migration"],
        )
    else:
        if any(
            params[key] is None
            for key in (
                "if_profile_revision",
                "if_source_sha256",
                "if_owner_id",
                "if_instance_id",
            )
        ):
            raise ProfileError(
                "restart needs profile, source, owner and instance IDs "
                "from its plan"
            )
        result = runtime_action(
            "restart",
            profile,
            wait=params["wait_s"],
            startup_wait=params["startup_wait_s"],
            if_profile_revision=params["if_profile_revision"],
            if_owner_id=params["if_owner_id"],
            if_instance_id=params["if_instance_id"],
            allow_source_switch=params["allow_source_switch"],
            allow_source_migration=params["allow_source_migration"],
            if_source_sha256=params["if_source_sha256"],
        )
    return {"schema_version": 1, **result}


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProfileError(
                "management request has a duplicate field"
            )
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ProfileError("management request has a nonfinite number")


def main() -> int:
    """Handle one owner-local JSON request on stdin."""
    try:
        payload = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if not payload or len(payload) > MAX_REQUEST_BYTES:
            raise ProfileError(
                "management request must be 1..65536 bytes"
            )
        request = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
        with contextlib.redirect_stdout(sys.stderr):
            response = dispatch(request)
        code = 0 if response.get("ok") else response.get("exit_code", 6)
        if code != 0 and "exit_code" not in response:
            response = {**response, "exit_code": code}
    except (ProfileError, ValueError, UnicodeError) as exc:
        code = getattr(exc, "exit_code", 2)
        response = {
            "schema_version": 1,
            "ok": False,
            "error": str(exc),
            "exit_code": code,
        }
    except OSError as exc:
        code = 6
        response = {
            "schema_version": 1,
            "ok": False,
            "error": f"local operation failed ({type(exc).__name__})",
            "exit_code": code,
        }
    except KeyboardInterrupt:
        code = 130
        response = {
            "schema_version": 1,
            "ok": False,
            "error": (
                "operation interrupted; completion must be checked in "
                "the journal"
            ),
            "exit_code": code,
        }
    print(json.dumps(response, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
