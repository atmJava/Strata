"""Command-line clients for the retained Strata server."""

import re
from pathlib import Path

SCHEMA_VERSION = 1

#: The command-line product version. ``version()`` below is the files'
#: engine build number, which compatibility checks compare.
PRODUCT_VERSION = "0.2.6"


def version() -> str:
    """The terminal's version: the VERSION file of an archive, else the source tree's CMake project version."""
    root = Path(__file__).resolve().parents[1]
    try:
        text = (root / "VERSION").read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    try:
        m = re.search(r"^\s*project\([^)]*?VERSION\s+([0-9]+(?:\.[0-9]+)+)",
                      (root / "CMakeLists.txt").read_text(encoding="utf-8", errors="replace"), re.MULTILINE)
        if m:
            return m.group(1)
    except OSError:
        pass
    return "unknown"
