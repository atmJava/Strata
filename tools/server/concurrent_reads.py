#!/usr/bin/env python3
"""What the concurrent prompt read is actually worth on this box, from the engine's own lines.

The engine writes one line per prompt read that ran on its own thread beside the decoding slots:

  strata batch: read 708 tokens on a thread of its own in 1540 ms (concurrent, to 141824 of 141829);
  the slots decoded 0 rows meanwhile (0.0 rows/s; stage 1: 0 windows, 0.0 ms host each; ...)

The "slots decoded N rows meanwhile" field is the whole point of the change: it says how much the
live sessions moved while the cold prompt was arriving.  A read with 0 rows is a read nobody was
hurt by and nobody benefited from - only one session was alive.  A read with rows > 0 is the case
the feature exists for.

When the gate refuses, the engine says so instead, and no read line appears:

  ... an expert source that stages its blobs (the GGUF in place, unbuffered, STRATA_IO_PREFETCH)

So "read line present" means the gate was open for that read, and a window with a big cold read and
no read lines means the gate was closed.  That is how this tells which engine sessions ran with the
gate open without asking anyone what binary was in place.

Windows are the engine sessions (the "[strata] <ts> engine started:" lines), so a config change can
be read off the windows on either side of it.

Usage: concurrent_reads.py [log ...] [--since TS] [--full]
"""
import pathlib
import re
import statistics
import sys

START = re.compile(r"\[strata\] (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) engine started:")
READ = re.compile(r"read (\d+) tokens on a thread of its own in (\d+) ms \(concurrent, to (\d+) of (\d+)\);"
                  r" the slots decoded (\d+) rows meanwhile \((\d+(?:\.\d+)?) rows/s")
REFUSE = "stages its blobs"


def main():
    logs, since, full = [], None, False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--since":
            i += 1
            since = args[i].replace("T", " ")
        elif args[i] == "--full":
            full = True
        else:
            logs.append(pathlib.Path(args[i]))
        i += 1
    wins = []
    for path in logs:
        lines = path.open(errors="replace").read().splitlines()
        if lines and not any(START.search(line) for line in lines):
            # logrotate cut this file mid-session, so the running session has no start line in it.
            # Without a fallback the tool prints nothing for the window you actually care about.
            wins.append({"start": "unknown (%s: rotated mid-session, this is the session tail)" % path.name,
                         "reads": [], "refusals": 0, "big_read": 0, "partial": True})
        for line in lines:
            m = START.search(line)
            if m:
                wins.append({"start": m.group(1), "reads": [], "refusals": 0, "big_read": 0})
            if not wins:
                continue
            w = wins[-1]
            m = READ.search(line)
            if m:
                w["reads"].append((int(m.group(1)), int(m.group(2)), int(m.group(5))))
            elif REFUSE in line:
                w["refusals"] += 1
    if since:
        # a window with no known start cannot be date-filtered; it is a rotated tail, so it is recent
        wins = [w for w in wins if w.get("partial") or w["start"] >= since]
    shown = wins if full else wins[-12:]
    if len(shown) != len(wins):
        print("(showing the last %d of %d sessions; --full for all)\n" % (len(shown), len(wins)))
    for n, w in enumerate(shown):
        nxt = shown[n + 1]["start"] if n + 1 < len(shown) else "now"
        r = w["reads"]
        rows = [x[2] for x in r]
        live = [x for x in r if x[2] > 0]
        ms = sum(x[1] for x in r)
        print("%s -> %s   reads %4d  refusals %3d   ms %8d   rows meanwhile %7d  (%s per live read)"
              % (w["start"], nxt, len(r), w["refusals"], ms, sum(rows),
                 ("median %d, p10 %d, max %d" % (statistics.median([x[2] for x in live]),
                                                 sorted(x[2] for x in live)[max(0, len(live)//10)],
                                                 max(x[2] for x in live))) if live else "no live read"))
        if r:
            zero = len(r) - len(live)
            print("%s   reads with 0 rows meanwhile: %d of %d (%.0f%%)   largest read %d tokens in %d ms"
                  % (" " * 19, zero, len(r), 100.0 * zero / len(r),
                     max(r, key=lambda x: x[0])[0], max(r, key=lambda x: x[0])[1]))


if __name__ == "__main__":
    main()
