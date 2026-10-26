"""Server command implementation shared by the CLI and owner runtime.

This module contains only the operations used by the Linux CUDA sm_75 server build:
launch planning, foreground serving, saved profiles, component diagnostics, and the
owner-local background runtime. The public command grammar lives in ``server_cli``.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import sys
import time
from pathlib import Path

from strata_control import profiles
from strata_control.doctor import inspect as inspect_components
from strata_control.profiles import ProfileError


def _error(message: str, code: int, machine: bool = False) -> int:
    if machine:
        print(json.dumps({
            "schema_version": 1,
            "ok": False,
            "error": {"code": code, "message": message},
        }))
    else:
        print(f"strata: {message}", file=sys.stderr)
    return code


def _success(
    payload: dict,
    *,
    machine: bool,
    human: str | None = None,
    quiet: bool = False,
) -> int:
    if machine:
        print(json.dumps(
            {"schema_version": 1, "ok": True, **payload},
            ensure_ascii=False,
            allow_nan=False,
        ))
    elif quiet:
        return 0
    elif human is not None:
        print(human)
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def _source(ref: str) -> tuple[Path, dict | None, str | None, dict]:
    path, saved, revision = profiles.resolve(ref)
    return path, saved, revision, profiles.read_run_config(path)


def _redact_argv(args: list[str]) -> list[str]:
    out = []
    conceal_next = False
    for item in args:
        if conceal_next:
            out.append("<redacted>")
            conceal_next = False
            continue
        if item.startswith("-") and any(
            word in item.lower()
            for word in ("key", "token", "secret", "password", "credential")
        ):
            if "=" in item:
                out.append(item.partition("=")[0] + "=<redacted>")
            else:
                out.append(item)
                conceal_next = True
        else:
            out.append(item)
    return out


def _effective(
    cfg: dict,
    saved: dict | None,
    cli: argparse.Namespace | None = None,
) -> tuple[dict, dict]:
    overrides = saved["overrides"] if saved else {}
    values = {}
    origins = {}
    for key, default in (
        ("host", "127.0.0.1"),
        ("port", 8095),
        ("parallel", None),
        ("batch_groups", None),
        ("max_queue", None),
        ("queue_timeout", None),
        ("offline", False),
        ("batch_telemetry", False),
    ):
        explicit = getattr(cli, key, None) if cli is not None else None
        if key == "offline" and explicit is False:
            explicit = None
        if explicit is not None:
            value, origin = explicit, "command"
        elif key in overrides:
            value, origin = overrides[key], "saved_profile"
        elif key in cfg:
            value, origin = cfg[key], "run_config"
        elif key == "batch_telemetry" and "STRATA_BATCH_TELEMETRY" in os.environ:
            value, origin = (
                os.environ["STRATA_BATCH_TELEMETRY"] == "1",
                "environment",
            )
        else:
            value, origin = default, "default"
        values[key], origins[key] = value, origin

    profiles.validate_overrides({
        key: value
        for key, value in values.items()
        if key in (
            "host",
            "port",
            "parallel",
            "batch_groups",
            "max_queue",
            "queue_timeout",
            "offline",
            "batch_telemetry",
        )
        and value is not None
    })
    if values["queue_timeout"] is not None and values["max_queue"] is None:
        raise ProfileError("queue_timeout requires max_queue to bound pending calls")
    if values["parallel"] is not None and any(
        flag in cfg["args"] for flag in ("--batch", "--slots")
    ):
        if origins["parallel"] != "run_config":
            raise ProfileError(
                "parallel override conflicts with --batch/--slots in run config args"
            )
    if values["batch_groups"] is not None and "--batch-groups" in cfg["args"]:
        raise ProfileError(
            "batch_groups override conflicts with --batch-groups in run config args"
        )
    if values["offline"] and (cfg.get("mcp_servers") or cfg.get("before_load")):
        raise ProfileError(
            "offline serving requires MCP servers and before_load to be absent"
        )
    return values, origins


def _launch_plan(
    ref: str,
    cli: argparse.Namespace | None = None,
) -> tuple[dict, list[str]]:
    path, saved, revision, cfg = _source(ref)
    values, origins = _effective(cfg, saved, cli)

    from serve.server import api_key_of, engine_args, explicit_no_key, key_list

    raw_key = os.environ.get("STRATA_API_KEY") or cfg.get("api_key", "")
    if not explicit_no_key(raw_key):
        try:
            key_list(api_key_of(raw_key))
        except ValueError:
            raise ProfileError("the inference API key is invalid", 5) from None

    server_args = [
        "--engine",
        getattr(cli, "engine", "strata"),
        "--config",
        str(path),
        "--host",
        str(values["host"]),
        "--port",
        str(values["port"]),
    ]
    if cli is not None:
        server_args += ["--shutdown-timeout", str(cli.shutdown_timeout)]
    for key, flag in (
        ("parallel", "--parallel"),
        ("batch_groups", "--batch-groups"),
        ("max_queue", "--max-queue"),
        ("queue_timeout", "--queue-timeout"),
    ):
        if values[key] is not None and origins[key] != "run_config":
            server_args += [flag, str(values[key])]
    if values["offline"]:
        server_args.append("--offline")
    if origins["batch_telemetry"] not in ("run_config", "default"):
        server_args.append(
            "--batch-telemetry"
            if values["batch_telemetry"]
            else "--no-batch-telemetry"
        )

    ready_path = None
    if cli is not None and cli.ready_file:
        from serve.ready import preflight as ready_preflight

        ready_path = str(ready_preflight(cli.ready_file))
        server_args += ["--ready-file", ready_path]

    effective_cfg = {**cfg, "args": list(cfg["args"])}
    if values["parallel"] is not None:
        effective_cfg["parallel"] = values["parallel"]
    if values["batch_groups"] is not None:
        effective_cfg["args"] += ["--batch-groups", str(values["batch_groups"])]
    notes = io.StringIO()
    with contextlib.redirect_stdout(notes):
        engine_argv = engine_args(effective_cfg)
    plan = {
        "profile": saved["id"] if saved else None,
        "profile_revision": revision,
        "source_config": str(path),
        "model_name": cfg.get("model_name"),
        "effective": values,
        "origin": origins,
        "server_argv": server_args,
        "ready_file": ready_path,
        "engine_exe": cfg["exe"],
        "engine_argv": _redact_argv(engine_argv),
        "notes": [line for line in notes.getvalue().splitlines() if line],
    }
    return plan, server_args


def _serve_error(a: argparse.Namespace, message: str, code: int = 2) -> int:
    if a.output == "jsonl":
        print(json.dumps({
            "schema_version": 1,
            "event": "failed",
            "at": time.time(),
            "exit_code": code,
            "error": {"message": message},
        }, ensure_ascii=False, allow_nan=False))
        return code
    return _error(message, code, a.output == "json")


def _serve_jsonl(
    server_main,
    args: list[str],
    plan: dict,
    *,
    quiet: bool = False,
) -> int:
    output = sys.stdout
    output_open = True
    ready = False
    stopped = False
    fields = {
        "starting": ("profile", "model_id", "pid"),
        "ready": (
            "instance_id",
            "pid",
            "api_url",
            "bound_host",
            "bound_port",
            "model_id",
            "model_state",
            "api_state",
        ),
        "stopping": ("instance_id", "reason"),
        "draining": ("instance_id", "pending_requests", "deadline_s"),
        "stopped": (
            "instance_id",
            "reason",
            "drained",
            "cancelled_requests",
            "exit_code",
        ),
        "failed": ("phase", "exit_code"),
    }

    def emit(kind: str, values: dict) -> None:
        nonlocal output_open, ready, stopped
        if kind not in fields:
            return
        ready |= kind == "ready"
        stopped |= kind == "stopped"
        if not output_open:
            return
        record = {
            "schema_version": 1,
            "event": kind,
            "at": time.time(),
            **{key: values[key] for key in fields[kind] if key in values},
        }
        try:
            output.write(json.dumps(
                record, ensure_ascii=False, allow_nan=False
            ) + "\n")
            output.flush()
        except BrokenPipeError:
            output_open = False

    emit("starting", {
        "profile": plan["profile"],
        "model_id": plan["model_name"],
        "pid": os.getpid(),
    })
    try:
        with (
            open(os.devnull, "w", encoding="utf-8")
            if quiet
            else contextlib.nullcontext(sys.stderr)
        ) as human_output:
            with contextlib.redirect_stdout(human_output):
                result = server_main(
                    args,
                    lifecycle_event=emit,
                    interrupt_exit_code=130,
                )
    except SystemExit as exc:
        result = exc.code if type(exc.code) is int else 6
        if result == 0 and not ready:
            result = 6
        if not isinstance(exc.code, int):
            print(
                "strata: foreground service startup failed; inspect the "
                "profile and component checks",
                file=sys.stderr,
            )
        emit("failed", {
            "phase": "startup" if not ready else "running",
            "exit_code": result,
        })
        return result
    except KeyboardInterrupt:
        emit("failed", {
            "phase": "startup" if not ready else "running",
            "exit_code": 130,
        })
        return 130
    except Exception as exc:
        print(
            f"strata: foreground service failed ({type(exc).__name__})",
            file=sys.stderr,
        )
        emit("failed", {
            "phase": "startup" if not ready else "running",
            "exit_code": 6,
        })
        return 6
    if not ready:
        emit("failed", {"phase": "startup", "exit_code": result or 6})
        return result or 6
    if not stopped:
        emit(
            "failed" if result else "stopped",
            {
                "phase": "running",
                "exit_code": result,
                "reason": "normal",
                "drained": result == 0,
            },
        )
    return result


def _serve_foreground(
    server_main,
    args: list[str],
    plan: dict,
    *,
    output: str,
    quiet: bool,
) -> int:
    if output == "jsonl":
        return _serve_jsonl(server_main, args, plan, quiet=quiet)
    if quiet:
        with open(os.devnull, "w", encoding="utf-8") as human_output:
            with contextlib.redirect_stdout(human_output):
                result = server_main(args, interrupt_exit_code=130)
        if result == 8:
            print(
                "strata: shutdown drain deadline expired; owned requests "
                "were cancelled",
                file=sys.stderr,
            )
        elif result:
            print(
                f"strata: foreground service exited with code {result}",
                file=sys.stderr,
            )
        return result
    return server_main(args, interrupt_exit_code=130)


def _serve(a: argparse.Namespace) -> int:
    if a.output == "json" and not a.plan:
        return _serve_error(
            a,
            "--output json requires --plan; use --output jsonl for "
            "foreground events",
        )
    if a.output == "jsonl" and a.plan:
        return _serve_error(
            a,
            "--plan uses --output human or json; jsonl is for foreground "
            "events",
        )
    if a.port is not None and not 0 <= a.port <= 65535:
        return _serve_error(a, "--port must be in 0..65535")
    if a.parallel is not None and not 1 <= a.parallel <= 1024:
        return _serve_error(a, "--parallel must be in 1..1024")
    if a.max_queue is not None and not 0 <= a.max_queue <= 4096:
        return _serve_error(a, "--max-queue must be in 0..4096")
    if a.queue_timeout is not None and (
        not math.isfinite(a.queue_timeout) or not 0.1 <= a.queue_timeout <= 600
    ):
        return _serve_error(
            a, "--queue-timeout must be finite seconds in 0.1..600"
        )
    if not 0 <= a.shutdown_timeout <= 300:
        return _serve_error(a, "--shutdown-timeout must be in 0..300 seconds")

    try:
        if a.batch_groups is not None:
            profiles.validate_overrides({
                "batch_groups": (
                    a.batch_groups
                    if a.batch_groups == "auto"
                    else int(a.batch_groups)
                    if a.batch_groups.isdecimal()
                    else a.batch_groups
                )
            })
        plan, args = _launch_plan(a.config, a)
    except ValueError as exc:
        if a.output == "jsonl":
            return _serve_error(a, str(exc), getattr(exc, "exit_code", 2))
        raise

    if a.plan:
        return _success(
            {"plan": plan},
            machine=a.output == "json",
            quiet=getattr(a, "quiet", False),
        )

    from serve.server import main as server_main

    from strata_control import runtime

    try:
        lease = runtime.acquire_activity_lease(shared=True)
    except ValueError as exc:
        if a.output == "jsonl":
            return _serve_error(a, str(exc), getattr(exc, "exit_code", 6))
        raise
    try:
        return _serve_foreground(
            server_main,
            args,
            plan,
            output=a.output,
            quiet=getattr(a, "quiet", False),
        )
    finally:
        os.close(lease)


def _profile_command(a: argparse.Namespace) -> int:
    machine = a.output == "json"
    if a.profile_command == "create":
        saved, revision = profiles.create(a.name, a.from_config)
        return _success(
            {"profile": saved, "revision": revision},
            machine=machine,
            human=f"Saved profile {a.name} at revision {revision}",
            quiet=getattr(a, "quiet", False),
        )
    if a.profile_command == "list":
        items = profiles.list_profiles()
        human = "\n".join(
            f"{item['id']}  {item.get('revision', item.get('error'))}"
            for item in items
        ) or "No saved profiles"
        return _success(
            {"profiles": items},
            machine=machine,
            human=human,
            quiet=getattr(a, "quiet", False),
        )

    saved, revision = profiles.load(a.name)
    if a.profile_command == "use-config":
        proposed, previous = profiles.propose_source(a.name, a.from_config)
        if previous != revision:
            raise ProfileError(
                "profile changed during source preview; plan it again", 6
            )
        if a.if_revision is not None and a.if_revision != previous:
            raise ProfileError(
                "profile changed after preview; plan it again before "
                "selecting a config",
                6,
            )
        change = {
            "before": saved["source_config"],
            "after": proposed["source_config"],
        }
        if a.plan:
            return _success(
                {
                    "profile": a.name,
                    "revision": previous,
                    "source_config": change,
                    "model_name": profiles.read_run_config(
                        Path(proposed["source_config"])
                    ).get("model_name"),
                    "would_save": change["before"] != change["after"],
                },
                machine=machine,
                human=f"Config: {change['before']} -> {change['after']}",
                quiet=getattr(a, "quiet", False),
            )
        saved, revision = profiles.save(
            a.name, proposed, expected_revision=previous
        )
        return _success(
            {
                "profile": saved,
                "previous_revision": previous,
                "revision": revision,
                "source_config": change,
            },
            machine=machine,
            human=f"Saved source config for {a.name}: "
            f"{change['before']} -> {change['after']}; "
            "running instance unchanged",
            quiet=getattr(a, "quiet", False),
        )
    if a.profile_command == "set":
        proposed, previous = profiles.propose_values(a.name, a.assignments)
        changes = {
            key: {
                "before": saved["overrides"].get(key),
                "after": value,
            }
            for key, value in proposed["overrides"].items()
            if saved["overrides"].get(key) != value
        }
        if a.plan:
            return _success(
                {
                    "profile": a.name,
                    "revision": previous,
                    "changes": changes,
                    "would_save": bool(changes),
                },
                machine=machine,
                quiet=getattr(a, "quiet", False),
            )
        saved, revision = profiles.save(
            a.name, proposed, expected_revision=previous
        )
        return _success(
            {
                "profile": saved,
                "previous_revision": previous,
                "revision": revision,
                "changes": changes,
            },
            machine=machine,
            human=f"Saved profile {a.name}: {previous} -> {revision}\n"
            + "\n".join(
                f"  {key}: {value['before']!r} -> {value['after']!r}"
                for key, value in changes.items()
            ),
            quiet=getattr(a, "quiet", False),
        )
    if a.profile_command == "edit":
        previous_value = saved
        saved, previous, revision = profiles.edit(a.name)
        changes = {
            key: {
                "before": previous_value.get(key),
                "after": saved.get(key),
            }
            for key in saved
            if previous_value.get(key) != saved.get(key)
        }
        return _success(
            {
                "profile": saved,
                "previous_revision": previous,
                "revision": revision,
                "changes": changes,
            },
            machine=machine,
            human=f"Saved profile {a.name}: {previous} -> {revision}",
            quiet=getattr(a, "quiet", False),
        )
    if a.profile_command == "history":
        entries = profiles.history(a.name)
        return _success(
            {"profile": a.name, "history": entries},
            machine=machine,
            human="\n".join(
                f"{item['revision']}  "
                f"{'current' if item.get('current') else 'saved'}"
                for item in entries
            ),
            quiet=getattr(a, "quiet", False),
        )
    if a.profile_command == "rollback":
        saved, previous, revision = profiles.rollback(a.name, a.to)
        return _success(
            {
                "profile": saved,
                "previous_revision": previous,
                "revision": revision,
            },
            machine=machine,
            human=f"Restored profile {a.name}: {previous} -> {revision}; "
            "running instances are unchanged",
            quiet=getattr(a, "quiet", False),
        )

    result = {"profile": saved, "revision": revision}
    if a.profile_command == "validate" or a.effective:
        plan, _ = _launch_plan(a.name)
        result["plan"] = plan
    return _success(
        result,
        machine=machine,
        human=(
            f"Profile {a.name} is valid at revision {revision}"
            if a.profile_command == "validate"
            else None
        ),
        quiet=getattr(a, "quiet", False),
    )


def _doctor(a: argparse.Namespace) -> int:
    machine = a.output == "json"
    ref = getattr(a, "profile", None)
    path, saved, _ = profiles.resolve(ref) if ref else (None, None, None)
    report = inspect_components(
        path,
        profile_id=saved["id"] if saved else None,
        live=getattr(a, "live", False),
    )
    if machine:
        print(json.dumps({
            "schema_version": 1,
            "ok": report["ok"],
            "report": report,
        }, ensure_ascii=False, allow_nan=False))
    elif not getattr(a, "quiet", False):
        for item in report["components"]:
            detail = (
                item.get("path")
                or item.get("reason")
                or (
                    f"version {item['version']}"
                    if item.get("version")
                    else ""
                )
            )
            print(f"{item['status']:>16}  {item['name']}  {detail}")
        for issue in report["issues"]:
            print(f"{issue['severity']}: {issue['code']}: {issue['message']}")
        live_probe = report["live_gpu_probe"]
        if live_probe is not None:
            print(f"Live GPU probe: {live_probe['status']}")
            for card in live_probe["cards"]:
                print(
                    f"  GPU {card['index']}: {card['name']} | "
                    f"{card['memory_mib']} MiB | "
                    f"driver {card['driver_version']} | {card['uuid']}"
                )
    return 0 if report["ok"] else 5


def _runtime(a: argparse.Namespace) -> int:
    from strata_control import management

    machine = a.output == "json"
    if a.runtime_command == "cleanup":
        from strata_control import runtime

        result = runtime.cleanup(a.profile)
        if machine:
            print(json.dumps(
                {"schema_version": 1, **result},
                ensure_ascii=False,
                allow_nan=False,
            ))
        else:
            print(
                f"ended {len(result['ended'])} leftover process(es)"
                + (
                    f"; {len(result['stuck'])} would not end"
                    if result["stuck"]
                    else ""
                )
                + (
                    f"; {len(result['unverifiable'])} could not be identified "
                    "here and were left alone"
                    if result["unverifiable"]
                    else ""
                )
            )
        return 0 if result["ok"] else result["exit_code"]

    result = management.runtime_action(
        a.runtime_command,
        a.profile,
        plan=getattr(a, "plan", False),
        die_with_pid=getattr(a, "die_with_pid", None),
        wait=getattr(a, "wait", 300),
        shutdown_timeout=getattr(a, "shutdown_timeout", 30),
        startup_wait=getattr(a, "startup_wait", 300),
        load=getattr(a, "load", False),
        if_profile_revision=getattr(a, "if_profile_revision", None),
        if_owner_id=getattr(a, "if_owner_id", None),
        if_instance_id=getattr(a, "if_instance_id", None),
        allow_source_switch=getattr(a, "allow_source_switch", False),
        allow_source_migration=getattr(a, "allow_source_migration", False),
        if_source_sha256=getattr(a, "if_source_sha256", None),
    )
    if (
        a.runtime_command in ("start", "stop", "verify")
        and a.plan
        and result.get("ok")
    ):
        return _success(
            {"plan": result["plan"]},
            machine=machine,
            quiet=getattr(a, "quiet", False),
        )
    if machine:
        print(json.dumps(
            {"schema_version": 1, **result},
            ensure_ascii=False,
            allow_nan=False,
        ))
    elif not getattr(a, "quiet", False):
        readiness_label = {
            "generation_ready": "generation checked",
            "unloaded": "API verified; model unloaded",
        }.get(result.get("readiness_state"))
        print(
            f"{a.profile}: {result.get('state', 'unknown')}"
            + (
                f"  instance {result['instance_id']}"
                if result.get("instance_id")
                else ""
            )
            + (f"  {result['api_url']}" if result.get("api_url") else "")
            + (
                "  config/launch verified"
                if result.get("launch_attested")
                else ""
            )
            + (f"  {readiness_label}" if readiness_label else "")
            + (f"  {result['error']}" if result.get("error") else "")
        )
        if not result["ok"] and a.runtime_command in (
            "start",
            "restart",
            "verify",
        ):
            for line in _engine_log_hint(a.profile):
                print(line, file=sys.stderr)
    return 0 if result["ok"] else result.get("exit_code", 6)


def _engine_log_hint(profile: str) -> list[str]:
    try:
        path, _saved, _revision = profiles.resolve(profile)
        log = profiles.read_run_config(path).get("log")
        if not isinstance(log, str) or not log:
            return []
        text = Path(log).expanduser().read_text(
            encoding="utf-8", errors="replace"
        )
    except (ProfileError, OSError, ValueError, KeyError):
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [f"engine log: {log}"] + [
        f"  {line[:300]}" for line in lines[-3:]
    ]


def main(argv: list[str] | None = None) -> int:
    """Compatibility entry point; the server build's grammar is in server_cli."""
    from strata_cli.server_cli import main as server_main

    return server_main(argv)
