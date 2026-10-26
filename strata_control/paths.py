"""Per-user state locations for the Linux terminal and server."""

from __future__ import annotations

import os
from pathlib import Path


def state_base(environ=None, *, home: Path | None = None) -> Path:
    """The folder that holds Strata's per-user state: runtime/, tuning/, time-skip/."""
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else home
    return Path(environ.get("XDG_STATE_HOME") or str(home / ".local" / "state")) / "strata"
