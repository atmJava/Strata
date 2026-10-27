"""Command line for the dedicated Linux CUDA sm_75 server build.

The public surface is deliberately small: serve a prepared run config, manage saved profiles and
the local owner supervisor, inspect components, validate the production shape, and read telemetry.
Model installation, desktop packaging and multi-vendor setup are not part of this build.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from strata_cli import monitor_commands
from strata_cli.client import ClientError
from strata_cli.cli import _doctor, _error, _profile_command, _runtime, _serve


def _machine_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", choices=("human", "json"), default="human")
    parser.add_argument("--json", action="store_true", help="alias for --output json")
    parser.add_argument("--quiet", action="store_true", default=False)


def _serve_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", "--config", dest="config", required=True, metavar="CONFIG.json")
    parser.add_argument("--engine", choices=("strata", "mock"), default="strata",
                        help="strata: the engine in the run config (default); mock: no GPU and no model")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--parallel", type=int)
    parser.add_argument("--batch-groups")
    parser.add_argument("--max-queue", type=int)
    parser.add_argument("--queue-timeout", type=float)
    parser.add_argument("--batch-telemetry", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--ready-file")
    parser.add_argument("--shutdown-timeout", type=float, default=30)
    parser.add_argument("--plan", action="store_true", help="validate and print the launch without starting")
    parser.add_argument("--output", choices=("human", "json", "jsonl"), default="human")
    parser.add_argument("--json", action="store_true", help="alias for --plan --output json")
    parser.add_argument("--quiet", action="store_true")


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="strata",
        description="Strata server for Linux x86_64, CUDA sm_75 and Qwen3.8-Flash-Next",
    )
    import strata_cli
    ap.add_argument("--version", action="version",
                    version=f"strata {strata_cli.PRODUCT_VERSION} (build {strata_cli.version()}; python {sys.version.split()[0]})")
    commands = ap.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the OpenAI/Anthropic server in the foreground")
    _serve_options(serve)
    launch = commands.add_parser("launch", help="compatibility alias for serve")
    _serve_options(launch)

    profile = commands.add_parser("profile", help="manage saved serving overrides")
    actions = profile.add_subparsers(dest="profile_command", required=True)
    create = actions.add_parser("create")
    create.add_argument("name")
    create.add_argument("--from-config", required=True)
    listing = actions.add_parser("list")
    show = actions.add_parser("show")
    show.add_argument("name")
    show.add_argument("--effective", action="store_true")
    validate = actions.add_parser("validate")
    validate.add_argument("name")
    set_values = actions.add_parser("set")
    set_values.add_argument("name")
    set_values.add_argument("assignments", nargs="+")
    set_values.add_argument("--plan", action="store_true")
    use_config = actions.add_parser("use-config")
    use_config.add_argument("name")
    use_config.add_argument("--from-config", required=True)
    use_config.add_argument("--plan", action="store_true")
    use_config.add_argument("--if-revision")
    edit = actions.add_parser("edit")
    edit.add_argument("name")
    history = actions.add_parser("history")
    history.add_argument("name")
    rollback = actions.add_parser("rollback")
    rollback.add_argument("name")
    rollback.add_argument("--to", required=True)
    for item in (create, listing, show, validate, set_values, use_config, edit, history, rollback):
        _machine_options(item)

    doctor = commands.add_parser("doctor", help="read-only component and GPU check")
    doctor.add_argument("--profile", help="saved profile name or run config JSON path")
    doctor.add_argument("--components", action="store_true", default=True)
    doctor.add_argument("--live", action="store_true")
    _machine_options(doctor)

    check = commands.add_parser(
        "check-production",
        help="validate a run config against the documented 2 x RTX 2080 Ti, 6 x 256K profile",
    )
    check.add_argument("--config", required=True, metavar="CONFIG.json")
    check.add_argument(
        "--env-file",
        default=str(Path(__file__).resolve().parents[1] / "tools" / "server" / "production-env-2080ti.json"),
    )
    _machine_options(check)

    runtime = commands.add_parser("runtime", help="manage a background server owned by this account")
    runtime_actions = runtime.add_subparsers(dest="runtime_command", required=True)
    start = runtime_actions.add_parser("start")
    start.add_argument("--profile", required=True)
    start.add_argument("--die-with-pid", type=int)
    start.add_argument("--plan", action="store_true")
    start.add_argument("--wait", type=float, default=300)
    start.add_argument("--shutdown-timeout", type=float, default=30)
    _machine_options(start)
    status = runtime_actions.add_parser("status")
    status.add_argument("--profile", required=True)
    _machine_options(status)
    verify = runtime_actions.add_parser("verify")
    verify.add_argument("--profile", required=True)
    verify.add_argument("--wait", type=float, default=300)
    verify.add_argument("--load", action="store_true")
    verify.add_argument("--plan", action="store_true")
    verify.add_argument("--if-owner-id")
    verify.add_argument("--if-instance-id")
    _machine_options(verify)
    stop = runtime_actions.add_parser("stop")
    stop.add_argument("--profile", required=True)
    stop.add_argument("--plan", action="store_true")
    stop.add_argument("--wait", type=float, default=330)
    stop.add_argument("--if-owner-id")
    stop.add_argument("--if-instance-id")
    _machine_options(stop)
    cleanup = runtime_actions.add_parser("cleanup")
    cleanup.add_argument("--profile")
    _machine_options(cleanup)
    restart = runtime_actions.add_parser("restart")
    restart.add_argument("--profile", required=True)
    restart.add_argument("--plan", action="store_true")
    restart.add_argument("--allow-source-switch", action="store_true")
    restart.add_argument("--allow-source-migration", action="store_true")
    restart.add_argument("--wait", type=float, default=1000)
    restart.add_argument("--startup-wait", type=float, default=300)
    restart.add_argument("--if-profile-revision")
    restart.add_argument("--if-owner-id")
    restart.add_argument("--if-instance-id")
    restart.add_argument("--if-source-sha256")
    _machine_options(restart)

    monitor_commands.add_status(commands)
    monitor_commands.add_monitor(commands)
    monitor_commands.add_logs(commands)
    monitor_commands.add_connections(commands)
    return ap


def _check_production(a: argparse.Namespace) -> int:
    from strata_control import sm75_profile
    result = sm75_profile.check_path(a.config, env_path=a.env_file)
    machine = a.output == "json" or a.json
    if machine:
        print(json.dumps({"schema_version": 1, **result}, ensure_ascii=False, allow_nan=False))
    else:
        print(f"config: {result['config']}")
        print("observed: " + ", ".join(
            f"{key}={value!r}" for key, value in result["observed"].items()
            if key != "env_sources"
        ))
        for item in result["warnings"]:
            print(f"warning: {item['code']}: {item['message']}")
        for item in result["errors"]:
            print(f"error: {item['code']}: {item['message']}", file=sys.stderr)
        print("production profile: " + ("OK" if result["ok"] else "MISMATCH"))
    return 0 if result["ok"] else 5


def _run(a: argparse.Namespace) -> int:
    if a.command in ("serve", "launch"):
        return _serve(a)
    if a.command == "profile":
        return _profile_command(a)
    if a.command == "doctor":
        return _doctor(a)
    if a.command == "check-production":
        return _check_production(a)
    if a.command == "runtime":
        return _runtime(a)
    return monitor_commands.run(a)


def main(argv: list[str] | None = None) -> int:
    ap = parser()
    a, extras = ap.parse_known_args(argv)
    a.raw_argv = list(sys.argv[1:] if argv is None else argv)
    if extras:
        ap.error("unrecognized arguments: " + " ".join(extras))
    if getattr(a, "json", False):
        if a.command in ("serve", "launch") and not a.plan:
            a.plan = True
        if hasattr(a, "output"):
            a.output = "json"
    try:
        return _run(a)
    except BrokenPipeError:
        return 0
    except ValueError as exc:
        machine = bool(getattr(a, "json", False) or getattr(a, "output", None) in ("json", "jsonl"))
        return _error(str(exc), getattr(exc, "exit_code", 2), machine)
    except ClientError as exc:
        machine = bool(getattr(a, "json", False) or getattr(a, "output", None) in ("json", "jsonl"))
        return _error(str(exc), exc.exit_code, machine)
    except OSError as exc:
        machine = bool(getattr(a, "json", False) or getattr(a, "output", None) == "json")
        return _error(str(exc), 6, machine)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
