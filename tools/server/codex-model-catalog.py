#!/usr/bin/env python3
"""Add this Strata model's tool metadata to Codex's local model catalog.

Codex falls back to a reduced tool set when a model slug is absent from its
catalog. For Strata that omits the freeform `apply_patch` tool even though the
agent instructions still name it. This updater clones a known Codex model entry
and changes only the model identity and context limits.
"""
from __future__ import annotations

import argparse
import json
import pathlib
from strata_control.codex_catalog import (
    DEFAULT_AUTO_COMPACT_TOKEN_LIMIT,
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_DISPLAY_NAME,
    DEFAULT_SLUG,
    DEFAULT_TEMPLATE,
    CatalogError,
    load_catalog,
    update_catalog,
    write_catalog,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--catalog",
        type=pathlib.Path,
        default=pathlib.Path.home() / ".codex" / "models.json",
    )
    parser.add_argument("--template", default=DEFAULT_TEMPLATE)
    parser.add_argument("--slug", default=DEFAULT_SLUG)
    parser.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME)
    parser.add_argument("--context-window", type=int, default=DEFAULT_CONTEXT_WINDOW)
    parser.add_argument(
        "--auto-compact-token-limit",
        type=int,
        default=DEFAULT_AUTO_COMPACT_TOKEN_LIMIT,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the catalog; without this flag, only check whether it would change",
    )
    options = parser.parse_args(argv)
    path = options.catalog.expanduser().resolve()
    try:
        catalog = load_catalog(path)
        updated, action = update_catalog(
            catalog,
            template_slug=options.template,
            slug=options.slug,
            display_name=options.display_name,
            context_window=options.context_window,
            auto_compact_token_limit=options.auto_compact_token_limit,
        )
    except (OSError, CatalogError) as exc:
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
