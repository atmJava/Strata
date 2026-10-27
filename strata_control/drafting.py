"""Choose the server's drafter: MTP, DFlash2, or none.

DFlash2 remains available for Qwen3.8-Flash-Next. Auto keeps MTP because no sm_75 measurement in
this tree shows DFlash2 winning on every tested prompt. A missing or incompatible DFlash2 pack
falls back to MTP with a precise note rather than preventing the server from starting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODES = ("auto", "mtp", "dflash2", "off")
DEFAULT_MODE = "auto"
LABELS = {"auto": "Auto", "mtp": "MTP", "dflash2": "DFlash2", "off": "Off"}

REPO = "incoai/Qwen3.8-Flash-Next-DFlash2"
REVISION = "a22a1744e85c17364dcc54a6a420af7135d4c7f6"
PACK_NAME = "qwen3.8-flash-next-dflash2.q8.pack"
FOLDER = "dflash2"
DOWNLOAD_GB = 1.2
PACK_GB = 0.76
VRAM_GB = 1.2

ENGINE_SERVES_DFLASH2 = True

DIDNT_FIT = "DFlash2 didn't fit"
NOT_SUPPORTED = "DFlash2 is for Qwen3.8-Flash-Next only"
NOT_DOWNLOADED = "the DFlash2 drafter isn't downloaded yet"
REASONS = (DIDNT_FIT, NOT_SUPPORTED, NOT_DOWNLOADED)

SUPPORTED_FAMILIES = ("qwen", "gyro")
_SUPPORTED_NAME = re.compile(
    r"^qwen3\.8-flash-next(-gyro-[a-z0-9._-]+|-(q|iq|tq)\d[a-z0-9_.]*)?$",
    re.IGNORECASE,
)
_ON = re.compile(r"DFlash2 drafter on\b")
_pending_note = ""


@dataclass(frozen=True)
class Decision:
    asked: str
    drafter: str
    note: str = ""
    flags: tuple[str, ...] = ()


def card_class(archs: Any) -> str:
    values = [archs] if isinstance(archs, str) else list(archs or [])
    return "sm75" if any(str(item).strip().lower() in ("sm75", "sm_75", "75") for item in values) else "other"


def suggest(_klass: str = "other") -> str:
    """Auto stays on MTP until DFlash2 has an sm_75 sweep where it wins every prompt class."""
    return "mtp"


def normalize(value: Any) -> str:
    text = str(value if value is not None else DEFAULT_MODE).strip().lower()
    return text if text in MODES else DEFAULT_MODE


def supported_model(model_name: str | None = None, family: str | None = None) -> bool:
    if family is not None:
        return family.lower() in SUPPORTED_FAMILIES
    return bool(model_name and _SUPPORTED_NAME.match(str(model_name)))


def pack_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / FOLDER / PACK_NAME


def pack_ready(data_dir: str | Path | None) -> bool:
    try:
        path = pack_path(data_dir) if data_dir is not None else None
        return bool(path and path.is_file() and path.stat().st_size > (1 << 20))
    except OSError:
        return False


def data_dir_of(args: list[Any], *, cwd: str | Path | None = None) -> Path | None:
    """Infer the model data folder from the existing pack or MTP arguments.

    Setup normally writes ``--mtp <data-dir>/mtp/rt`` and ``--pack <data-dir>/packs/<tag>``.  A
    hand-written ``--mtp <data-dir>/mtp`` is accepted too.  Relative paths belong to the run
    config's ``cwd``, as the engine's paths do.
    """
    items = [str(item) for item in args]
    root = None
    for flag in ("--pack", "--mtp"):
        if flag not in items:
            continue
        at = items.index(flag)
        if at + 1 >= len(items):
            continue
        path = Path(items[at + 1])
        if flag == "--mtp" and path.name.isdigit():
            continue
        if flag == "--pack" and path.parent.name == "packs":
            root = path.parent.parent
        elif flag == "--mtp":
            if path.name == "mtp":
                root = path.parent
            elif path.parent.name == "mtp":
                root = path.parent.parent
        if root is not None:
            break
    if root is None:
        return None
    if cwd is not None and not root.is_absolute():
        root = Path(cwd) / root
    return root


def decide(mode: Any, *, klass: str, model_name: str | None = None,
           family: str | None = None, data_dir: str | Path | None = None) -> Decision:
    asked = normalize(mode)
    if asked == "off":
        return Decision(asked, "off")
    wanted = suggest(klass) if asked == "auto" else asked
    if wanted != "dflash2":
        return Decision(asked, "mtp")
    if not supported_model(model_name, family):
        return Decision(asked, "mtp", NOT_SUPPORTED if asked == "dflash2" else "")
    if not ENGINE_SERVES_DFLASH2:
        return Decision(asked, "mtp", NOT_SUPPORTED if asked == "dflash2" else "")
    if not pack_ready(data_dir):
        return Decision(asked, "mtp", NOT_DOWNLOADED)
    return Decision(asked, "dflash2", flags=(
        "--draft", "dflash2", "--draft-model", str(pack_path(data_dir)),
    ))


def needs_pack(mode: Any, *, klass: str, model_name: str | None = None,
               family: str | None = None, data_dir: str | Path | None = None) -> bool:
    asked = normalize(mode)
    wanted = suggest(klass) if asked == "auto" else asked
    return (wanted == "dflash2" and ENGINE_SERVES_DFLASH2
            and supported_model(model_name, family) and not pack_ready(data_dir))


DRAFT_FLAGS = frozenset(("--draft", "--draft-model"))


def strip_draft_flags(args: list[Any]) -> list[str]:
    out: list[str] = []
    items = [str(item) for item in args]
    index = 0
    while index < len(items):
        if items[index] in DRAFT_FLAGS:
            index += 2 if index + 1 < len(items) and not items[index + 1].startswith("--") else 1
            continue
        out.append(items[index])
        index += 1
    return out


def apply(cfg: dict[str, Any], *, klass: str, data_dir: str | Path | None = None,
          family: str | None = None) -> Decision:
    from strata_control import draft_layer

    decision = decide(cfg.get("drafting"), klass=klass, model_name=cfg.get("model_name"),
                      family=family, data_dir=data_dir)
    if cfg.get("drafting") is None and decision.drafter == "mtp":
        return decision
    args = strip_draft_flags(cfg.get("args") or [])
    if decision.drafter == "off":
        cfg["mtp"] = False
        cfg["args"] = args
        draft_layer.apply(cfg)
    else:
        if normalize(cfg.get("drafting")) != "off" and cfg.get("mtp") is False:
            cfg["mtp"] = True
        cfg["args"] = [*args, *decision.flags]
    return decision


def mtp_line(reason: str) -> str:
    return f"Drafting: MTP ({reason})"


def fallback_note(log_text: str) -> str:
    text = str(log_text or "")
    if _ON.search(text) or "--draft dflash2" not in text:
        return ""
    reason = DIDNT_FIT
    return mtp_line(reason)


def summary_line(decision: Decision, engine_note: str = "") -> str:
    if engine_note:
        return engine_note
    if decision.note:
        return mtp_line(decision.note)
    return f"Drafting: {LABELS.get(decision.drafter, decision.drafter)}"


def remember_note(note: str) -> None:
    global _pending_note
    _pending_note = note or ""


def take_note() -> str:
    global _pending_note
    note, _pending_note = _pending_note, ""
    return note


def engine_info(args: list[Any], log_text: str = "", note: str = "") -> dict[str, str]:
    items = [str(item) for item in args]
    asked_dflash2 = any(
        items[index] == "--draft" and index + 1 < len(items) and items[index + 1] == "dflash2"
        for index in range(len(items))
    )
    fell_back = fallback_note(log_text) if asked_dflash2 else ""
    if asked_dflash2 and not fell_back:
        return {"drafting": "dflash2", "drafting_note": ""}
    drafter = "mtp" if "--mtp" in items else "off"
    text = fell_back or (mtp_line(note) if note and drafter == "mtp" else "")
    return {"drafting": drafter, "drafting_note": text}
