"""Update a Codex JSON model catalog for a locally served Strata model."""

from __future__ import annotations

import copy
import json
import os
import pathlib
import shutil
import tempfile
import time
from typing import Any


DEFAULT_SLUG = "qwen3.8-flash-next-iq3_xxs-abliterated"
DEFAULT_DISPLAY_NAME = "Qwen3.8-Flash-Next IQ3_XXS"
DEFAULT_TEMPLATE = "deepseek-flash"
DEFAULT_CONTEXT_WINDOW = 262144
DEFAULT_AUTO_COMPACT_TOKEN_LIMIT = 210000


class CatalogError(ValueError):
    """The catalog cannot be updated without guessing."""


def update_catalog(
    catalog: dict[str, Any],
    *,
    template_slug: str,
    slug: str,
    display_name: str,
    context_window: int,
    auto_compact_token_limit: int,
) -> tuple[dict[str, Any], str]:
    """Return a catalog with one Strata entry and an added/updated/unchanged result."""
    models = catalog.get("models")
    if not isinstance(models, list):
        raise CatalogError("catalog must contain a models list")
    template = next(
        (
            model
            for model in models
            if isinstance(model, dict) and model.get("slug") == template_slug
        ),
        None,
    )
    if template is None:
        raise CatalogError(f"template model {template_slug!r} is not in the catalog")
    if not isinstance(template.get("model_messages"), dict):
        raise CatalogError(f"template model {template_slug!r} has no model_messages")
    if template.get("apply_patch_tool_type") != "freeform":
        raise CatalogError(
            f"template model {template_slug!r} does not declare freeform apply_patch"
        )
    if context_window <= 0 or auto_compact_token_limit <= 0:
        raise CatalogError("context limits must be positive")
    if auto_compact_token_limit >= context_window:
        raise CatalogError("auto_compact_token_limit must be below context_window")

    entry = copy.deepcopy(template)
    entry.update(
        {
            "slug": slug,
            "display_name": display_name,
            "context_window": context_window,
            "max_context_window": context_window,
            "auto_compact_token_limit": auto_compact_token_limit,
            "priority": 100,
            "availability_nux": None,
        }
    )
    positions = [
        index
        for index, model in enumerate(models)
        if isinstance(model, dict) and model.get("slug") == slug
    ]
    if not positions:
        updated = copy.deepcopy(catalog)
        updated["models"].append(entry)
        return updated, "added"
    current = [models[index] for index in positions]
    if len(current) == 1 and current[0] == entry:
        return catalog, "unchanged"

    updated = copy.deepcopy(catalog)
    first = positions[0]
    updated["models"][first] = entry
    for index in reversed(positions[1:]):
        del updated["models"][index]
    return updated, "updated"


def load_catalog(path: pathlib.Path) -> dict[str, Any]:
    """Read a catalog object from *path* without accepting non-object JSON."""
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict):
        raise CatalogError("catalog must be a JSON object")
    return catalog


def write_catalog(path: pathlib.Path, catalog: dict[str, Any]) -> pathlib.Path:
    """Write atomically and return the timestamped backup."""
    path = path.expanduser().resolve()
    stamp = time.strftime("%Y%m%dT%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(catalog, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return backup
