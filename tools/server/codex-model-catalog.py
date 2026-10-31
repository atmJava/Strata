#!/usr/bin/env python3
"""Add this Strata model's tool metadata to Codex's local model catalog.

Codex falls back to a reduced tool set when a model slug is absent from its
catalog. For Strata that omits the freeform `apply_patch` tool even though the
agent instructions still name it. This updater clones a known Codex model entry
and changes only the model identity and context limits.
"""
from __future__ import annotations

import argparse
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
        raise ValueError("catalog must contain a models list")
    template = next(
        (
            model
            for model in models
            if isinstance(model, dict) and model.get("slug") == template_slug
        ),
        None,
    )
    if template is None:
        raise ValueError(f"template model {template_slug!r} is not in the catalog")
    if not isinstance(template.get("model_messages"), dict):
        raise ValueError(f"template model {template_slug!r} has no model_messages")
    if context_window <= 0 or auto_compact_token_limit <= 0:
        raise ValueError("context limits must be positive")
    if auto_compact_token_limit >= context_window:
        raise ValueError("auto_compact_token_limit must be below context_window")

    updated = copy.deepcopy(catalog)
    current = next(
        (
            model
            for model in updated["models"]
            if isinstance(model, dict) and model.get("slug") == slug
        ),
        None,
    )
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
    if current is None:
        updated["models"].append(entry)
        return updated, "added"
    if current == entry:
        return catalog, "unchanged"
    updated["models"][updated["models"].index(current)] = entry
    return updated, "updated"


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--catalog",
        type=pathlib.Path,
        default=pathlib.Path.home() / ".codex" / "models.json",
    )
    parser.add_argument("--template", default="deepseek-flash")
    parser.add_argument("--slug", default=DEFAULT_SLUG)
    parser.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME)
    parser.add_argument("--context-window", type=int, default=262144)
    parser.add_argument("--auto-compact-token-limit", type=int, default=210000)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the catalog; without this flag, only check whether it would change",
    )
    options = parser.parse_args(argv)
    path = options.catalog.expanduser().resolve()
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
        updated, action = update_catalog(
            catalog,
            template_slug=options.template,
            slug=options.slug,
            display_name=options.display_name,
            context_window=options.context_window,
            auto_compact_token_limit=options.auto_compact_token_limit,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    backup = write_catalog(path, updated) if options.apply and action != "unchanged" else None
    print(
        json.dumps(
            {
                "catalog": str(path),
                "slug": options.slug,
                "action": action,
                "applied": bool(backup),
                "backup": str(backup) if backup else None,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
