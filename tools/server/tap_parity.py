"""Compare (or merge) the engine's DFlash2 tap dumps, so the tap wiring can be checked without a model run.

The engine writes one file per verifier when `STRATA_TAP_FILE` is set (src/core/verify.cpp, tap_write; a process
that runs two Verifiers - the pipeline parity pair - would share one path, so give a layer-split run the `%d` spelling
below, and compare a merge of those files with a single-stage run): a 16-byte header `int32 magic 'STAP', int32 version 1, int32 n_embd, int32 n_taps`, then `n_taps` int32 layer
numbers, then one record per row of every window: `int32 position, int32 input token`, then `n_taps * n_embd`
float32 values (the taps in header order).

Three uses:

  compare  two files of the SAME layers (CLI run vs serve window vs batch window): every record must agree
           within the tolerance.  Equal tokens are what the tap wiring is for - a mismatch is a wiring bug, not
           noise, because the taps are a mean of the same layer outputs on the same rows.
  merge    files of DISJOINT layers (a layer split writes one file per stage): the union is the whole window's
           taps, and it is what a single-stage run's file can then be compared against.
  info     header and record count of one file (what layers, how many rows, which positions).

Examples:

  python tools/server/tap_parity.py compare cli.stap serve.stap
  python tools/server/tap_parity.py merge stage0.stap stage1.stap -o whole.stap
  python tools/server/tap_parity.py compare whole.stap batch.stap --tol 1e-6
"""
from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

MAGIC = 0x50415453  # 'STAP'
VERSION = 1


class FormatError(ValueError):
    """The file is not a tap dump of this version."""


@dataclass
class Dump:
    n_embd: int
    layers: list[int]
    rows: dict[tuple[int, int], list[float]]  # (position, token) -> n_taps * n_embd floats

    @property
    def n_taps(self) -> int:
        return len(self.layers)


def read_dump(path: str | Path) -> Dump:
    raw = Path(path).read_bytes()
    if len(raw) < 16:
        raise FormatError(f"{path}: {len(raw)} bytes is shorter than a header")
    magic, version, n_embd, n_taps = struct.unpack("<4i", raw[:16])
    if magic != MAGIC:
        raise FormatError(f"{path}: not a tap dump (magic 0x{magic:08x}, want 0x{MAGIC:08x})")
    if version != VERSION:
        raise FormatError(f"{path}: version {version}, this tool reads {VERSION}")
    if n_embd <= 0 or n_taps <= 0:
        raise FormatError(f"{path}: n_embd {n_embd} / n_taps {n_taps} is not a dump")
    off = 16
    if len(raw) < off + 4 * n_taps:
        raise FormatError(f"{path}: the layer list is cut short")
    layers = list(struct.unpack(f"<{n_taps}i", raw[off:off + 4 * n_taps]))
    off += 4 * n_taps
    per = n_taps * n_embd
    stride = 8 + 4 * per
    rows: dict[tuple[int, int], list[float]] = {}
    while off + 8 <= len(raw):
        if off + stride > len(raw):
            raise FormatError(f"{path}: a record at byte {off} is cut short")
        pos, tok = struct.unpack("<2i", raw[off:off + 8])
        vals = list(struct.unpack(f"<{per}f", raw[off + 8:off + stride]))
        key = (pos, tok)
        if key in rows and rows[key] != vals:
            raise FormatError(f"{path}: two records for position {pos} token {tok} disagree")
        rows[key] = vals
        off += stride
    return Dump(n_embd=n_embd, layers=layers, rows=rows)


def write_dump(path: str | Path, n_embd: int, layers: list[int], rows: dict[tuple[int, int], list[float]]) -> None:
    per = len(layers) * n_embd
    out = bytearray(struct.pack("<4i", MAGIC, VERSION, n_embd, len(layers)))
    out += struct.pack(f"<{len(layers)}i", *layers)
    for (pos, tok), vals in sorted(rows.items()):
        if len(vals) != per:
            raise FormatError(f"position {pos} token {tok}: {len(vals)} floats, want {per}")
        out += struct.pack("<2i", pos, tok)
        out += struct.pack(f"<{per}f", *vals)
    Path(path).write_bytes(bytes(out))


def merge(dumps: list[Dump], paths: list[str]) -> Dump:
    """The union of files whose layer sets are disjoint, in ascending layer order."""
    n_embd = dumps[0].n_embd
    seen: dict[int, int] = {}
    for d, p in zip(dumps, paths):
        if d.n_embd != n_embd:
            raise FormatError(f"{p}: n_embd {d.n_embd}, the first file has {n_embd}")
        for layer in d.layers:
            if layer in seen:
                raise FormatError(f"{p}: layer {layer} is also in the file before (merge wants disjoint layers)")
            seen[layer] = len(seen)
    order = sorted(seen)   # the taps need not be contiguous layers; the merged header keeps the sorted numbers
    rows: dict[tuple[int, int], list[float]] = {}
    keys = set().union(*(d.rows.keys() for d in dumps))
    for key in sorted(keys):
        for d, p in zip(dumps, paths):
            if key not in d.rows:
                raise FormatError(f"{p}: position {key[0]} token {key[1]} is in another file but not this one - "
                                  "the stages must cover the same rows")
    for key in keys:
        merged: list[float] = []
        for layer in order:
            for d in dumps:
                if layer in d.layers:
                    merged.extend(d.rows[key][d.layers.index(layer) * n_embd:(d.layers.index(layer) + 1) * n_embd])
                    break
        rows[key] = merged
    return Dump(n_embd=n_embd, layers=order, rows=rows)


def compare(a: Dump, b: Dump, tol: float, allow_missing: bool = False,
            limit: int = 5) -> tuple[int, int, float, list[str]]:
    """(compared rows, differing/missing rows, worst absolute difference, the first `limit` notes).

    Two files of different widths cannot be the same measurement, and a row in one file only is not a pass either
    (a truncated or partly written dump must not compare equal): both are failures unless `allow_missing`."""
    if a.n_embd != b.n_embd:
        raise FormatError(f"n_embd differs: {a.n_embd} vs {b.n_embd} - not the same measurement")
    common = sorted(set(a.layers) & set(b.layers))
    if not common:
        return 0, 0, 0.0, ["no common tap layers: compare a single-stage file with a merge, or check --tap-layers"]
    only_a = sorted(set(a.layers) - set(b.layers))
    only_b = sorted(set(b.layers) - set(a.layers))
    ia = [a.layers.index(layer) for layer in common]
    ib = [b.layers.index(layer) for layer in common]
    n_embd = a.n_embd
    keys = sorted(set(a.rows) & set(b.rows))
    notes: list[str] = []
    if only_a:
        notes.append(f"only in the first file: layers {only_a}")
    if only_b:
        notes.append(f"only in the second file: layers {only_b}")
    missing = len(a.rows) + len(b.rows) - 2 * len(keys)
    if missing:
        notes.append(f"{missing} rows are in one file only (not compared)")
    if missing and not allow_missing:
        notes.append("one-sided rows are a failure here: pass --allow-missing to compare only the common rows")
    compared = differing = 1 if (missing and not allow_missing) else 0
    worst = 0.0
    detail: list[str] = []
    for pos, tok in keys:
        ra, rb = a.rows[(pos, tok)], b.rows[(pos, tok)]
        bad = False
        for la, lb in zip(ia, ib):
            for j in range(n_embd):
                x, y = ra[la * n_embd + j], rb[lb * n_embd + j]
                d = abs(x - y)
                if d > worst:
                    worst = d
                if d > tol:
                    bad = True
                    if len(detail) < limit:
                        detail.append(f"position {pos} token {tok}: layer {common[ia.index(la)]} element {j}: "
                                  f"{x!r} vs {y!r} (|d| {d:.3e})")
        compared += 1
        differing += int(bad)
    return compared, differing, worst, notes + detail


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="compare or merge the engine's DFlash2 tap dumps (STRATA_TAP_FILE)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare", help="two files of the same layers must agree")
    c.add_argument("first")
    c.add_argument("second")
    c.add_argument("--tol", type=float, default=1e-6, help="largest absolute difference allowed (default 1e-6)")
    c.add_argument("--limit", type=int, default=5, help="how many differing rows to name (default 5)")
    c.add_argument("--allow-missing", action="store_true",
                   help="compare only the rows both files have (a partial dump then passes)")
    m = sub.add_parser("merge", help="files of disjoint layers into one")
    m.add_argument("files", nargs="+")
    m.add_argument("-o", "--out", required=True)
    i = sub.add_parser("info", help="header and record count of one file")
    i.add_argument("file")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "info":
            d = read_dump(args.file)
            pos = sorted(p for p, _ in d.rows)
            print(f"{args.file}: {d.n_taps} taps {d.layers}, n_embd {d.n_embd}, {len(d.rows)} rows"
                  f"{f', positions {pos[0]}..{pos[-1]}' if pos else ''}")
            return 0
        if args.cmd == "merge":
            dumps = [read_dump(p) for p in args.files]
            merged = merge(dumps, args.files)
            write_dump(args.out, merged.n_embd, merged.layers, merged.rows)
            print(f"merged {len(args.files)} files -> {args.out}: layers {merged.layers}, {len(merged.rows)} rows")
            return 0
        a, b = read_dump(args.first), read_dump(args.second)
        compared, differing, worst, notes = compare(a, b, args.tol, args.allow_missing, args.limit)
        for note in notes:
            print(note)
        print(f"{compared} rows compared, {differing} differ (tolerance {args.tol:g}), worst |diff| {worst:.3e}")
        return 0 if compared and not differing else (1 if compared else 2)
    except (FormatError, OSError, KeyError, IndexError) as err:
        print(f"tap_parity: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
