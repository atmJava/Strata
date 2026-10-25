#!/usr/bin/env python3
"""Two arm files -> medians, spread, and the >=3% rule (EVERGREEN §5 / RULES §10).

Reads the JSON of two runs of the same probe under two arms and prints, for one field:
  arm A: n, median, spread (max-min)/median
  arm B: same
  diff: (median B - median A) / median A, and the verdict: >=3% -> B differs from A; <3% -> no conclusion.

Understands the two probe shapes: {"rows": [...]} (exp-v2-prefill-ab.py) and {"reads": [...]}
(exp-v2-warmup.py, where the field is neighbour_gap_s or wall_s).

Usage: exp-v2-ab-summary.py A.json B.json [--field prompt_ms]
"""
import argparse
import json
import sys


def values(path: str, field: str) -> list[float]:
    data = json.load(open(path))
    rows = data.get("rows") or data.get("reads") or []
    out = []
    for row in rows:
        value = row.get(field)
        if isinstance(value, (int, float)):
            out.append(float(value))
    return out


def stats(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    ordered = sorted(xs)
    median = ordered[len(ordered) // 2] if len(ordered) % 2 else (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2
    return {"n": len(xs), "median": round(median, 3), "min": round(ordered[0], 3), "max": round(ordered[-1], 3),
            "spread_pct": round((ordered[-1] - ordered[0]) / median * 100, 1) if median else None}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--field", default="prompt_ms")
    opts = ap.parse_args(argv)
    a, b = values(opts.a, opts.field), values(opts.b, opts.field)
    sa, sb = stats(a), stats(b)
    print(json.dumps({"field": opts.field, "A": sa, "B": sb}, indent=1))
    if not a or not b:
        print("no conclusion: an arm has no numbers for this field", file=sys.stderr)
        return 2
    diff = (sb["median"] - sa["median"]) / sa["median"] * 100
    verdict = (f"B - A = {diff:+.2f}% (>=3% -> a real difference)" if abs(diff) >= 3
               else f"B - A = {diff:+.2f}% (<3% -> no conclusion at this sample size)")
    print(f"{verdict}; A n={sa['n']} spread {sa['spread_pct']}%, B n={sb['n']} spread {sb['spread_pct']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
