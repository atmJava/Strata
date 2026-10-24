#!/usr/bin/env python3
"""A1：每轮（每次请求）从主机/Host 走了多少专家与 KV 字节 —— 只用引擎日志里**已经在打印**的计数器，零重启。

为什么先算这个：NVLink 只在"字节要跨卡/跨主机"时才有意义。先把"每窗口/每轮到底搬了多少字节"钉住，
才知道 4.25× 的链路差最多能换回多少毫秒；算不清就不该写代码。

读的行（引擎 0.1.47 的每请求摘要）：
  strata serve: expert tiers: GPU <n> hits this request; since the start RAM <b> blobs, files 0 blobs <MB> MB read
  strata serve: KV streaming: <p>% of <n> block reads hit VRAM, <MiB> read from RAM
  strata serve: prompt <n> tokens = <r> reused + <k> read in <ms> ms (...)

输出：相邻两次摘要之间的增量（= 一轮的量），中位与 p90，并按 11.0 / 46.8 GB/s 折算成毫秒。
"""
import argparse
import re
import statistics
import sys

RAM_RE = re.compile(r"since the start RAM (\d+) blobs, files 0 blobs ([\d.]+) MB read")
KV_RE = re.compile(r"KV streaming: ([\d.]+)% of (\d+) block reads hit VRAM, ([\d.]+) MiB read from RAM")
PROMPT_RE = re.compile(r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", nargs="?", default="/opt/ai/src/strata-0.2.2/strata-022.log")
    ap.add_argument("--tail", type=int, default=40000, help="只读最后这么多行")
    args = ap.parse_args()

    with open(args.log, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 8 * 1024 * 1024))
        lines = fh.read().decode("utf-8", "replace").splitlines()[-args.tail:]

    # 计数器是"since the start"（每个进程重置），所以**按引擎启动切段**再取增量：
    # 跨进程做差会得到负数（这一版第一版就是这么错的：均值 −0.3 MB/轮）。
    segs, cur = [], []
    for line in lines:
        if "engine started:" in line:
            if cur:
                segs.append(cur)
            cur = []
            continue
        m = RAM_RE.search(line)
        if not m:
            continue
        kv = KV_RE.search(line)
        cur.append((float(m.group(2)), float(kv.group(3)) if kv else None))
    if cur:
        segs.append(cur)
    if not segs:
        sys.exit("no request summaries in the tail")
    marks = [x for seg in segs for x in seg]
    d_mb, d_kv = [], []
    for seg in segs:
        for (m0, _), (m1, _) in zip(seg, seg[1:]):
            d_mb.append(m1 - m0)

    d_blobs, tok = [], []

    def ms(mb, gbps):
        return mb / (gbps * 1e3) * 1e3   # MB / (GB/s) -> ms

    print(f"请求摘要 {len(marks)} 条，增量 {len(d_mb)} 个（同进程内相邻两次的差 = 一轮的量），分 {len(segs)} 段")
    for name, v in (("RAM 专家 blob/轮", d_blobs), ("RAM 专家 MB/轮", d_mb), ("KV MiB/轮", d_kv)):
        if not v:
            continue
        print(f"  {name}: 中位 {statistics.median(v):.1f}  均值 {statistics.fmean(v):.1f}  "
              f"p90 {sorted(v)[int(0.9 * (len(v) - 1))]:.1f}  max {max(v):.1f}")
    if tok:
        print(f"  提示词 token/轮: 中位 {statistics.median(tok):.0f}")

    med = statistics.median(d_mb)
    tot = med + (statistics.median(d_kv) / 1.048576 if d_kv else 0.0)
    print()
    print(f"每轮 host 字节（中位）≈ {tot:.1f} MB")
    print(f"  按 host 11.0 GB/s : {ms(tot, 11.0):.1f} ms")
    print(f"  按 NVLink 46.8 GB/s: {ms(tot, 46.8):.1f} ms   （省 {ms(tot, 11.0) - ms(tot, 46.8):.1f} ms/轮）")
    print()
    print("判据（CODE_PLAN §10 A1）：host < 15 MB/轮 → decode 侧 NVLink 上限 < 1 ms/窗口，只做 prefill 线。")


if __name__ == "__main__":
    main()
