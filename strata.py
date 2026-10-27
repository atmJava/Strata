"""Strata's command-line entry point for the dedicated CUDA sm_75 server."""

import importlib.util
import sys

#: What the API server imports. The remaining command paths use the standard library.
SERVER_PACKAGES = ("jinja2", "yaml", "regex", "psutil", "requests", "numpy", "PIL")


def _preflight(argv):
    """A missing package is a typed, actionable error, never an install: starting does not run pip."""
    command = next((a for a in argv if not a.startswith("-")), "")
    if command not in ("serve", "launch"):
        return 0
    missing = [m for m in SERVER_PACKAGES if importlib.util.find_spec(m) is None]
    if not missing:
        return 0
    sys.stderr.write(
        "strata: the interpreter %s is missing packages the API server needs: %s.\n"
        "        Starting and serving never install anything: python -m pip install -r requirements.txt\n"
        % (sys.executable, ", ".join(missing)))
    return 5


if __name__ == "__main__":
    code = _preflight(sys.argv[1:])
    if code:
        raise SystemExit(code)
    from strata_cli.server_cli import main
    raise SystemExit(main())
