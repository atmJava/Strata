#!/usr/bin/env python3
"""Passive per-engine-session numbers from the two logs the box already writes.

A restart splits the logs into engine sessions.  Between two starts the box measured itself: no
benchmark ran, no load was added, and the only thing that differs is what the config said.  This
reads those sessions side by side and reports, per session, what the primary metrics looked like on
real traffic - including the two numbers the L-011 bench did not report:

  - speculative-draft acceptance (the engine writes "drafts accepted A of B" for every request, and
    a separate "suffix drafts: W windows, A of B" line; neither the bench nor the parity gate ever
    read either one, so L-011 left that column empty);
  - truncation and empty answers (the server writes "done: ... (length, ...)" when a reply hit
    max_tokens, and "the reply reached max tokens while still thinking, so it has no answer" when
    the thinking ate the whole window).

It also reports the expert-cache hit rate and the per-request decode rate, so a window can be
judged on the same columns as hitrate_before_after.py, plus how big the traffic was (prompt tokens
read per request), because a window of 300-token prompts and a window of 100K prompts are not the
same workload and the difference is not controlled here.

Read this with care, and say so out loud when you quote it:
  - the windows do not have the same traffic: mix, prompt length, how many slots were busy at the
    same time, and who the clients were - none of it is controlled.  This is a hypothesis generator,
    not an A/B.  Nothing here passes the "<3% is not a finding" bar by itself.
  - a second stream on this box halves a single stream's rate, so the rate column mixes load with
    any real change.  Acceptance and hit rate are the less contaminated columns.
  - a window that overlaps an experiment (bench, harness, parity probes) contains that load.  The
    window boundaries are printed so you can see which ones do.

Usage: passive_windows.py [log ...] [--label "TS=TEXT"] [--since TS] [--full] [--band MIN:MAX]
  logs   given in chronological order; a session that starts in one file and continues in the next
         is merged (the rotation at 00:00 does not end a session)
  label  annotate a window, e.g. --label "2026-10-23 19:10:57=mode8-prod" (repeatable)
  since  drop windows that started before this timestamp (local to the log's own clock)
  full   print every window instead of the last 12
  band   MIN:MAX - (repeatable; one BAND line per band per window) also report the numbers for the requests whose prompt tokens fall in that range.
         A fixed-prompt bench (bench-strata.py uses ~293-token prompts, parity_window.py ~7.7K,
         cold-prefill.py ~13.3K) lands in a narrow band, so two windows can be compared on the same
         prompts: that is the only column here where the workload really is the same.
  marker SUBSTRING - cut windows at lines containing this text instead of the engine-log
         "engine started" line.  The service log has no timestamps on its restart lines
         ("starting the engine: reading the model's weights ..."), so pair --marker with --starts.
  starts TS,TS,... - names for the newest windows, right-aligned; use it with --marker to give the
         service-log windows the timestamps you read from the engine log.
"""
import pathlib
import re
import statistics
import sys

START = re.compile(r"\[strata\] (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) engine started:")
REQ = re.compile(
    r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms .*?, (\d+) generated in (\d+) ms"
    r" \((\d+(?:\.\d+)?) tok/s\), drafts accepted (\d+) of (\d+)")
SUFFIX = re.compile(r"suffix drafts: (\d+) windows, (\d+) of (\d+) drafts accepted")
DONE = re.compile(r"done: (\d+) tokens in (\d+) s \((\d+(?:\.\d+)?) tok/s\) \((\w+), cancel=(\w+)\)"
                  r".*?expert cache (\d+(?:\.\d+)?)% hit", re.S)
NOANSWER = "reached max tokens while still thinking, so it has no answer"
CAPPED = "thinking capped at"
BUDGET = "thinking budget reached"
WARN = re.compile(r"WARNING: only (\d+) MiB of VRAM free")
KVSTREAM = re.compile(r"KV streaming: (\d+(?:\.\d+)?)% of (\d+) block reads hit VRAM")


def pct(xs, q):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[i]


def stats(name, xs, fmt="%6.2f"):
    if not xs:
        return ""
    return ("  %s median " + fmt + "  p10 " + fmt + "  min " + fmt + "  max " + fmt + "  n=%d") % (
        name, statistics.median(xs), pct(xs, 0.10), min(xs), max(xs), len(xs))

class Window:
    def __init__(self, start):
        self.start = start
        self.label = ""
        self.offered = self.accepted = 0
        self.suf_offered = self.suf_accepted = self.suf_windows = 0
        self.recs = []                      # (prompt_tokens, read_tokens, decode_tok_s, accepted, offered)
        self.engine_gen = 0
        self.done = self.stops = self.length = 0
        self.hit = []
        self.srv_rate = []
        self.noanswer = self.capped = self.budget = 0
        self.warn_min = None
        self.kv_hit = []
        self.lines = 0

    def add(self, line):
        self.lines += 1
        m = REQ.search(line)
        if m:
            self.recs.append((int(m.group(1)), int(m.group(3)), float(m.group(7)),
                              int(m.group(8)), int(m.group(9))))
            self.engine_gen += int(m.group(5))
            self.accepted += int(m.group(8))
            self.offered += int(m.group(9))
            return
        m = SUFFIX.search(line)
        if m:
            self.suf_windows += int(m.group(1))
            self.suf_accepted += int(m.group(2))
            self.suf_offered += int(m.group(3))
            return
        m = DONE.search(line)
        if m:
            self.done += 1
            self.srv_rate.append(float(m.group(3)))
            self.hit.append(float(m.group(6)))
            if m.group(4) == "stop":
                self.stops += 1
            else:
                self.length += 1
            return
        m = WARN.search(line)
        if m:
            v = int(m.group(1))
            self.warn_min = v if self.warn_min is None else min(self.warn_min, v)
            return
        m = KVSTREAM.search(line)
        if m:
            self.kv_hit.append(float(m.group(1)))
            return
        if NOANSWER in line:
            self.noanswer += 1
        elif CAPPED in line:
            self.capped += 1
        elif BUDGET in line:
            self.budget += 1

    def acceptance(self, recs=None):
        recs = self.recs if recs is None else recs
        offered = sum(r[4] for r in recs)
        accepted = sum(r[3] for r in recs)
        return (100.0 * accepted / offered if offered else float("nan")), offered, accepted


def report(w, nxt, bands):
    span = "%s -> %s" % (w.start, nxt or "now")
    acc, offered, accepted = w.acceptance()
    sacc = 100.0 * w.suf_accepted / w.suf_offered if w.suf_offered else float("nan")
    print("%-22s %-41s lines %6d  reqs %4d  done %4d  gen %8d"
          % (w.label or "(unlabeled)", span, w.lines, len(w.recs), w.done, w.engine_gen))
    print("%22s %41s  drafts accepted %5.2f%% of %7d (%d accepted)   suffix %5.2f%% of %6d in %d windows"
          % ("", "", acc, offered, accepted, sacc, w.suf_offered, w.suf_windows))
    pad = " " * 66
    if w.recs:
        print(pad + stats("prompt tok", [r[0] for r in w.recs], "%8.0f"))
        print(pad + stats("prompt read tok", [r[1] for r in w.recs], "%8.0f"))
        print(pad + stats("engine decode tok/s", [r[2] for r in w.recs]))
    if w.srv_rate:
        print(pad + stats("server done tok/s", w.srv_rate))
    if w.hit:
        print(pad + stats("expert hit%", w.hit))
    if w.kv_hit:
        print(pad + stats("kv resident%", w.kv_hit))
    fin = w.stops + w.length
    if fin:
        print("%22s %41s  finish stop %4d  length %4d (%4.1f%%)  no-answer %d  capped %d  budget-hit %d"
              % ("", "", w.stops, w.length, 100.0 * w.length / fin, w.noanswer, w.capped, w.budget))
    if w.warn_min is not None:
        print("%22s %41s  #2065 warnings: lowest free seen %d MiB" % ("", "", w.warn_min))
    for lo, hi in bands:
        sub = [r for r in w.recs if lo <= r[0] <= hi]
        if sub:
            bacc, boff, _ = w.acceptance(sub)
            print("%22s %41s  BAND %d-%d tok: reqs %3d  drafts accepted %5.2f%% of %6d  decode%s"
                  % ("", "", lo, hi, len(sub), bacc, boff, stats("", [r[2] for r in sub])))
        else:
            print("%22s %41s  BAND %d-%d tok: no requests in this window" % ("", "", lo, hi))
    print()


def main():
    logs, labels, since, full = [], {}, None, False
    bands = []
    marker, starts = None, []
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--label":
            i += 1
            ts, text = args[i].split("=", 1)
            labels[ts.replace("T", " ")] = text
        elif a == "--marker":
            i += 1
            marker = args[i]
        elif a == "--starts":
            i += 1
            starts = [s.strip().replace("T", " ") for s in args[i].split(",")]
        elif a == "--since":
            i += 1
            since = args[i].replace("T", " ")
        elif a == "--band":
            i += 1
            lo, hi = args[i].split(":")
            bands.append((int(lo), int(hi)))
        elif a == "--full":
            full = True
        else:
            logs.append(pathlib.Path(a))
        i += 1
    windows = []
    for path in logs:
        with path.open(errors="replace") as handle:
            for line in handle:
                if marker:
                    if marker in line:
                        windows.append(Window(""))
                else:
                    m = START.search(line)
                    if m:
                        windows.append(Window(m.group(1)))
                if windows:
                    windows[-1].add(line)
    if starts:
        # right-align: the starts are the newest boundaries, which is what you know from the engine
        # log when the log you are reading has no timestamps on its own restart lines
        n = min(len(starts), len(windows))
        for k in range(n):
            windows[len(windows) - n + k].start = starts[len(starts) - n + k]
    if not marker:
        for w in windows:
            if not w.start:
                w.start = "(no timestamp on this log's start lines)"
    else:
        for w in windows:
            if not w.start:
                w.start = "(undated start)"
    if since:
        windows = [w for w in windows if w.start >= since]
    shown = windows if full else windows[-12:]
    if len(shown) != len(windows):
        print("(showing the last %d of %d windows; --full for all)\n" % (len(shown), len(windows)))
    for idx, w in enumerate(shown):
        w.label = labels.get(w.start, "")
        nxt = shown[idx + 1].start if idx + 1 < len(shown) else None
        report(w, nxt, bands)


if __name__ == "__main__":
    main()
