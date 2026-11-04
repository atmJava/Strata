#!/usr/bin/env python3
"""check_preset.py - validate a Strata server preset before it is installed (T7 / F6).

The fork's launcher enforces `N x CTX <= GPU KV tokens` (N = MAX_NUM_SEQS, CTX = MAX_MODEL_LEN).
Strata streams KV beyond `--kv-resident` cells per QSA layer, so that formula has no direct
analog (archived docs/archive/FEATURE_PARITY-2026-10-24.md F6); this tool checks the invariants that do hold, and - when
the server is reachable - that the running engine matches the preset.

Usage:
  check_preset.py <config.json> [--base-url http://127.0.0.1:8080] [--api-key KEY]
Exit code 0 when every check passes (warnings alone still pass).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

MIN_KV_RESIDENT = 20480
MIN_CONVERSATION_CACHE_MIB = 4096
MIN_VRAM_RESERVE_MIB = 1024
MAX_CONTEXT = 262144


def arg_value(args: list[str], flag: str) -> str | None:
    if flag in args:
        index = args.index(flag)
        if index + 1 < len(args):
            return args[index + 1]
    return None


def check(config: dict, base_url: str | None, api_key: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notes: list[str] = []
    args = config.get("args") or []

    parallel = config.get("parallel")
    if not isinstance(parallel, int) or parallel < 1:
        errors.append(f"parallel: expected a positive integer, got {parallel!r}")

    batch = arg_value(args, "--batch")
    if parallel is not None and batch is not None and int(batch) != parallel:
        errors.append(f"--batch {batch} != parallel {parallel} (the server's admission limit is parallel)")

    groups = arg_value(args, "--batch-groups")
    if groups is not None and parallel:
        if int(groups) < 1 or int(groups) > parallel or parallel % int(groups) != 0:
            errors.append(f"--batch-groups {groups} must divide parallel {parallel}")
        else:
            notes.append(f"groups {groups} x {parallel // int(groups)} slots")

    context = arg_value(args, "--max-context")
    if context is not None:
        value = int(context)
        if value > MAX_CONTEXT:
            errors.append(f"--max-context {value} > {MAX_CONTEXT}")
        if value < 8192:
            errors.append(f"--max-context {value} < 8192")
        if parallel:
            notes.append(f"requested capacity N x CTX = {parallel} x {value} = {parallel * value} tokens")
    else:
        notes.append("no --max-context: the engine default applies")

    kv_resident = arg_value(args, "--kv-resident")
    if kv_resident is not None:
        cells = int(kv_resident)
        if 0 < cells < MIN_KV_RESIDENT:
            errors.append(f"--kv-resident {cells} < {MIN_KV_RESIDENT} (the engine's floor)")
        if cells and context and cells < int(context):
            notes.append(f"KV streams beyond {cells} cells per layer (context {context}): the fork's "
                         f"N x CTX <= KV formula does not bound this preset")

    cache = arg_value(args, "--conversation-cache-mib")
    if cache is not None and int(cache) < MIN_CONVERSATION_CACHE_MIB:
        errors.append(f"--conversation-cache-mib {cache} < {MIN_CONVERSATION_CACHE_MIB}")

    reserve = arg_value(args, "--vram-reserve-mib")
    if reserve is not None and int(reserve) < MIN_VRAM_RESERVE_MIB:
        errors.append(f"--vram-reserve-mib {reserve} < {MIN_VRAM_RESERVE_MIB}")

    expected = f"strata-022.mp-{parallel}x{int(context) // 1000}.json" if (parallel and context) else None
    if expected:
        notes.append(f"preset name: {expected}")

    if base_url:
        try:
            request = urllib.request.Request(base_url.rstrip("/") + "/metrics",
                                             headers={"Authorization": f"Bearer {api_key}"})
            with urllib.request.urlopen(request, timeout=15) as response:
                engine = json.load(response)["engine"]
        except Exception as error:  # noqa: BLE001 - a reachability note, not a preset error
            notes.append(f"/metrics not reachable ({type(error).__name__}); static checks only")
        else:
            if parallel is not None and engine.get("batch_slots") != parallel:
                errors.append(f"engine batch_slots {engine.get('batch_slots')} != preset parallel {parallel}")
            if context is not None and engine.get("max_context") != int(context):
                errors.append(f"engine max_context {engine.get('max_context')} != preset {context}")
            if kv_resident is not None and engine.get("kv_resident") != int(kv_resident):
                errors.append(f"engine kv_resident {engine.get('kv_resident')} != preset {kv_resident}")
            notes.append(f"running engine {engine.get('version')} matches the preset's shape")
    return errors, notes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("--base-url", default=os.environ.get("MP_BASE_URL"))
    parser.add_argument("--api-key", default=os.environ.get("MP_API_KEY", "111"))
    options = parser.parse_args()
    config = json.load(open(options.config, encoding="utf-8"))
    errors, notes = check(config, options.base_url, options.api_key)
    for note in notes:
        print(f"note: {note}")
    for error in errors:
        print(f"ERROR: {error}")
    print("OK" if not errors else f"{len(errors)} error(s)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
