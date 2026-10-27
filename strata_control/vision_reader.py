"""Picture-reader configuration for the Linux CUDA server.

One program, ``strata-vision``, contains llama.cpp's CPU and CUDA backends.  ``--gpu`` selects CUDA for a picture;
without it the same program reads on the CPU.  The server can therefore retry the same executable without ``--gpu``
when CUDA cannot start, instead of shipping a second vendor-specific reader.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CPU_READER = "strata-vision"
GPU_READER = CPU_READER


def default_mode(vram_gb: float | None = None) -> str:
    """The dedicated server reads pictures on its CUDA card by default."""
    return "gpu"


def wanted(asked: str | None, vram_gb: float | None = None) -> str:
    """Map a user's choice to ``"gpu"``, ``"cpu"`` or ``"none"``."""
    choice = (asked or "").strip().lower()
    if choice in ("no", "none", "off"):
        return "none"
    if choice == "cpu":
        return "cpu"
    return default_mode(vram_gb)


def find(folder: Path | str) -> dict[str, Path | None]:
    """The single CUDA-capable reader in an engine folder."""
    path = Path(folder) / CPU_READER
    path = path if path.is_file() else None
    return {"cpu": path, "gpu": path}


@dataclass
class Reader:
    mode: str                     # "gpu" | "cpu" | "none"
    exe: Path | None = None
    cpu_exe: Path | None = None   # the same program, started without --gpu
    note: str = ""


def choose(folder: Path | str, asked: str) -> Reader:
    """Choose the reader for ``asked`` from ``strata-vision`` only."""
    asked = (asked or "").strip().lower()
    if asked in ("no", "none", "off", ""):
        return Reader("none")
    have = find(folder)
    exe = have["cpu"]
    if exe is None:
        return Reader("none", note="this engine has no picture reader: pictures off")
    if asked == "cpu":
        return Reader("cpu", exe)
    return Reader("gpu", exe, exe)


def vision_section(reader: Reader, base: dict) -> dict:
    """Build a run config's ``vision`` section for ``reader``."""
    out = dict(base)
    out["exe"] = str(reader.exe) if reader.exe else ""
    out["gpu"] = reader.mode == "gpu"
    if reader.mode == "gpu" and reader.cpu_exe:
        out["cpu_exe"] = str(reader.cpu_exe)
    else:
        out.pop("cpu_exe", None)
    return out


def cpu_fallback(vision_cfg: dict) -> dict | None:
    """The same reader without ``--gpu``, when CUDA cannot start."""
    if not vision_cfg.get("gpu") or not vision_cfg.get("cpu_exe"):
        return None
    out = {
        key: value
        for key, value in vision_cfg.items()
        if key not in ("cpu_exe", "elastic", "elastic_mib", "elastic_idle_s")
    }
    out["exe"], out["gpu"] = vision_cfg["cpu_exe"], False
    return out
