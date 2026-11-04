#!/usr/bin/env python3
"""Passive before/after from the service log - what a config change did to a live box.

A restart splits the service log into two engine sessions.  Everything the box measured on its own
between the two starts is a same-box, same-model, same-traffic comparison that cost no benchmark
time and added no load: the sessions differ only in what the config said.  This is how L-001 (the
--vram-reserve-mib change, which cost 144 expert-cache slots) gets judged without running anything.

It reads the lines the server already writes for every completed request:

  [strata] done: 989 tokens in 16 s (76.3 tok/s) (stop, cancel=False), expert cache 98.6% hit (+0.1% ...)

and reports, per session, how many such lines there are and the median / p10 / p90 of the hit
percentage and of the per-request rate.

Read this with care, and say so out loud when you quote it:
  - the two sessions do not have the same traffic.  Request mix, prompt length and how many slots were
    busy at the same time all differ, and none of them is controlled here.
  - a second stream on this box halves a single stream's rate, so the rate column mixes load effects
    with any real change.  The hit percentage is the less contaminated of the two.
  - so a difference smaller than the spread it sits in is not a finding.  This tells you whether to
    run a controlled A/B, not what the answer is.

Usage: hitrate_before_after.py [path] [start-marker-substring]
"""
import pathlib
import re
import statistics
import sys

LOG = "/opt/ai/logs/strata-service.log"
MARK = "starting the engine: reading the model's weights"
DONE = re.compile(r"done: .*?\((\d+(?:\.\d+)?) tok/s\).*?expert cache (\d+(?:\.\d+)?)% hit", re.S)
SLOTS = re.compile(r"expert cache[^0-9]*([\d,]+)\s*slots", re.I)


def pct(xs, q):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[i]


def describe(name, rows, slots):
    if not rows:
        print("%-9s no completed requests in this window" % name)
        return
    hits = [r[1] for r in rows]
    rate = [r[0] for r in rows]
    print("%-9s requests %5d   hit%%  median %6.2f  p10 %6.2f  p90 %6.2f  min %6.2f"
          % (name, len(rows), statistics.median(hits), pct(hits, 0.10), pct(hits, 0.90), min(hits)))
    print("%-9s              rate  median %6.1f  p10 %6.1f  p90 %6.1f  max %6.1f   slots %s"
          % ("", statistics.median(rate), pct(rate, 0.10), pct(rate, 0.90), max(rate),
             "/".join(slots) if slots else "not printed"))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else LOG
    mark = sys.argv[2] if len(sys.argv) > 2 else MARK
    lines = pathlib.Path(path).read_text(errors="replace").splitlines()
    starts = [i for i, ln in enumerate(lines) if mark in ln]
    if not starts:
        print("no start marker %r in %s" % (mark, path))
        return 1
    if len(starts) < 2:
        print("only one start marker (line %d): nothing to compare before" % (starts[0] + 1))
        starts = [-1] + starts
    print("file %s  %d lines  start markers at %s"
          % (path, len(lines), [i + 1 for i in starts[-4:]]))
    prev, last = starts[-2], starts[-1]
    for name, lo, hi in (("before", prev + 1, last), ("after", last + 1, len(lines))):
        rows = []
        slots = []
        for ln in lines[lo:hi]:
            m = DONE.search(ln)
            if m:
                rows.append((float(m.group(1)), float(m.group(2))))
            s = SLOTS.search(ln)
            if s:
                slots.append(s.group(1))
        describe(name, rows, slots)
    print("\nlines compared: before %d..%d   after %d..%d" % (prev + 2, last, last + 2, len(lines)))
    print("uncontrolled: traffic mix, prompt length and concurrent slots differ between the windows;")
    print("a second stream halves a single stream here, so read the rate column as load, not as change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
