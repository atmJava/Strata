#!/usr/bin/env python3
"""两臂的 TTFT 配对比较：同一个提示词（同一 new、同一 turn）在两臂的差，而不是两臂各自的中位数。

为什么需要它：`turn-ttft.py` 每轮发的是**逐字相同的提示词**（`block(n, f"n{n}r{i}")`），但两臂各自的
中位数里混着"这一轮恰好命中多少专家/重读多少 token"的差异 —— 同一臂内部的 `host staging` 会在
130↔286 ms 之间跳。逐条配对把输入差异消掉，剩下的才是臂的效应。

用法：paired_ttft.py <tagA> <tagB> [--jsonl PATH]
输出：每一档的配对数、配对差的中位（ms 与 %）、B 比 A 快的次数、以及判定。
判定规则（与 RULES 一致）：|中位差| < 5% 或 B 胜率不显著（双侧符号检验 p > 0.1）→ 写"不可分辨"。
"""
import argparse
import json
import statistics
import sys

DEFAULT = "/opt/ai/logs/turn-ttft.jsonl"


def load(path):
    recs = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return recs


def newest(recs, tag):
    got = [r for r in recs if r.get("tag") == tag]
    if not got:
        return None
    # 同一个 tag 跑过多次时取最后一次；行的顺序就是时间顺序
    return got[-1]


def sign_test_p(wins, losses):
    """双侧精确符号检验（小样本），wins+losses 为有效配对数。"""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    # P(X <= k) * 2，X ~ Bin(n, 1/2)
    from math import comb

    tail = sum(comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--jsonl", default=DEFAULT)
    args = ap.parse_args()

    recs = load(args.jsonl)
    ra, rb = newest(recs, args.a), newest(recs, args.b)
    if ra is None or rb is None:
        sys.exit(f"tag not found: {args.a if ra is None else args.b} in {args.jsonl}")

    def index(rec):
        return {(r["new"], r["turn"]): r for r in rec.get("rows", [])
                if r.get("new") is not None and r.get("turn") is not None and r.get("ttft_ms")}

    ia, ib = index(ra), index(rb)
    keys = sorted(set(ia) & set(ib))
    if not keys:
        sys.exit("no identical (new, turn) pairs — run both arms with the same sizes and reps")

    print(f"A={args.a}  B={args.b}  配对 {len(keys)} 条")
    print(f"{'新增':>6} {'轮':>3} {'A 首字 ms':>10} {'B 首字 ms':>10} {'差 ms':>8} {'差 %':>7}")
    by_level = {}
    for k in keys:
        a, b = ia[k]["ttft_ms"], ib[k]["ttft_ms"]
        d = b - a
        pct = 100.0 * d / a
        by_level.setdefault(k[0], []).append((d, pct, a, b))
        print(f"{k[0]:>6} {k[1]:>3} {a:>10} {b:>10} {d:>+8} {pct:>+7.1f}")

    print()
    for lvl, rows in sorted(by_level.items()):
        d = [r[0] for r in rows]
        pct = [r[1] for r in rows]
        wins = sum(1 for x in pct if x < 0)      # B faster
        losses = sum(1 for x in pct if x > 0)
        med = statistics.median(pct)
        p = sign_test_p(wins, losses)
        spread = statistics.median(abs(x) for x in pct)
        verdict = "不可分辨" if abs(med) < 5.0 or p > 0.1 else ("B 更快" if med < 0 else "B 更慢")
        print(f"新增 {lvl}: n={len(rows)} 配对差中位 {med:+.1f}%  (|差| 中位 {spread:.1f}%)  "
              f"B 胜 {wins}/{wins + losses}  符号检验 p={p:.3f}  → {verdict}")


if __name__ == "__main__":
    main()
