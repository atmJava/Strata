"""Locate llama.cpp's gguf-py for retained model-pack tools.

Order: ``STRATA_GGUF_PY``, the repository's third_party checkout, then
``STRATA_INSTALL_DIR`` and the development checkout.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]


def gguf_py() -> str:
    cands = [os.environ.get("STRATA_GGUF_PY"), ENGINE / "third_party" / "llama.cpp" / "gguf-py",
             ENGINE.parents[min(1, len(ENGINE.parents) - 1)] / ".ref" / "llama.cpp" / "gguf-py"]   # the dev tree; a shallow install (G:\Strata) has no grandparent
    install = os.environ.get("STRATA_INSTALL_DIR")
    if install:
        cands.append(Path(install).expanduser() / "third_party" / "llama.cpp" / "gguf-py")
    for c in cands:
        if c and (Path(c) / "gguf").is_dir():
            return str(c)
    sys.exit("llama.cpp's gguf-py was not found. Set STRATA_GGUF_PY to the gguf-py folder of a llama.cpp checkout, "
             "place it under third_party/llama.cpp/gguf-py, or set STRATA_INSTALL_DIR.")


def add_gguf_py() -> None:
    p = gguf_py()
    if p not in sys.path:
        sys.path.insert(0, p)
