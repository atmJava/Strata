#!/usr/bin/env python3
"""Is the fixed ~0.8 s of a small prompt read the expert staging, or per-run fixed work?  Repeat the same tokens.

Why this discriminates.  L-028 put the whole fixed cost inside `Prefill::run` (the batched blob read) and left two
suspects: (a) staging the experts the *new* tokens route to, and (b) per-run fixed work (pinned upload reserve, the
PLE table, chunk planning).  (a) depends on *which experts* the tokens need; (b) does not care what the tokens are.
So: append a block of new tokens, read it, then append **the same text again** and read that.  The second read's
tokens route to the same experts, which the first read and the reply's decode just used - if staging is the cost,
the second read is clearly cheaper; if it is per-run fixed work, the two come out the same.

Each arm runs three times (unique, repeat) inside one conversation and one boot, so the comparison is paired.
Greedy (temperature 0), `reasoning_effort: none`, and the tool checks that the engine line it read belongs to this
request (prompt tokens must equal the context it built) - a concurrent request would otherwise be attributed here.

Usage: stage_repeat_probe.py [context tokens] [block tokens] [pairs]      defaults: 45000 460 3
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

LOG = os.environ.get("TTFT_LOG", "/opt/ai/src/strata-0.2.2/strata-022.log")
CFG = os.environ.get("TTFT_CFG", "/opt/ai/src/strata-0.2.2/strata-022.json")
URL = "http://127.0.0.1:8080/v1/chat/completions"
MODEL = "qwen3.8-flash-next-iq3_xxs-abliterated"
CTX, BLOCK, PAIRS = (int(sys.argv[i]) if len(sys.argv) > i else d for i, d in ((1, 45000), (2, 460), (3, 3)))
RID = str(int(time.time()) % 100000)
GREP_RE = r"prompt [0-9]+ tokens = [0-9]+ reused \+ [0-9]+ read in [0-9]+ ms"   # grep -E does not know \d on this box
PY_RE = r"prompt ([0-9]+) tokens = ([0-9]+) reused \+ ([0-9]+) read in ([0-9]+) ms"


def api_key():
    try:
        return json.load(open(CFG)).get("api_key", "")
    except Exception:
        return os.environ.get("QE_KEY", "")


def grep(pat):
    return subprocess.run(["grep", "-oE", pat, LOG], capture_output=True, text=True, timeout=60).stdout.strip().splitlines()


def post(msgs, mt):
    body = {"model": MODEL, "messages": msgs, "max_tokens": mt, "temperature": 0, "reasoning_effort": "none"}
    hdrs = {"Content-Type": "application/json"}
    if api_key():
        hdrs["Authorization"] = "Bearer " + api_key()
    t0 = time.time()
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers=hdrs)
    with urllib.request.urlopen(req, timeout=3600) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    wall = time.time() - t0
    try:
        reply = d["choices"][0]["message"]["content"] or ""
    except Exception:
        reply = ""
    return wall, reply


def block(n, tag):
    items = max(1, int(n / 12.0))
    return " ".join(f"{RID}{tag}{i:05d}" for i in range(items))


def read_turn(msgs, expect_ctx, label):
    before = len(grep(GREP_RE))
    wall, reply = post(msgs, 300)
    rows = []
    for ln in reversed(grep(GREP_RE)[before:]):
        m = re.search(PY_RE, ln)
        if not m:
            continue
        ctx, reused, read, ms = (int(m.group(i)) for i in (1, 2, 3, 4))
        if expect_ctx is not None and ctx != expect_ctx:   # someone else's request: skip, do not attribute
            print(f"    ({label}: 跳过 ctx={ctx} 不是本请求的 {expect_ctx})")
            continue
        rows.append((reused, read, ms, wall))
        break
    if not rows:
        print(f"    {label}: 没取到自己的引擎行（墙钟 {wall:.2f}s）")
        return None
    reused, read, ms, wall = rows[0]
    print(f"    {label}: ctx={ctx} reused={reused} read={read} 引擎读 {ms} ms  墙钟 {wall:.2f}s")
    return {"read": read, "ms": ms, "wall": wall, "reply": reply}


print(f"  上下文≈{CTX}  每块≈{BLOCK} token  {PAIRS} 对（唯一 + 重复同一块）")
post([{"role": "user", "content": "warm OK"}], 8)
time.sleep(2)

msgs = [{"role": "user", "content": block(CTX, "base") + "\n\n请只回复 OK。"}]
w, reply = post(msgs, 8)
print(f"  建上下文：墙钟 {w:.1f}s")
msgs.append({"role": "assistant", "content": reply})

rows = []
for p in range(1, PAIRS + 1):
    text_u = block(BLOCK, f"u{p}")
    ask = "\n\n请用中文写一篇约 300 字的技术说明，分节论述。"
    msgs.append({"role": "user", "content": "工具输出：\n" + text_u + ask})
    got_u = read_turn(msgs, None, f"对{p} 唯一")
    msgs.append({"role": "assistant", "content": got_u["reply"] if got_u else "OK"})
    msgs.append({"role": "user", "content": "工具输出：\n" + text_u + ask})
    got_r = read_turn(msgs, None, f"对{p} 重复同一块")
    msgs.append({"role": "assistant", "content": got_r["reply"] if got_r else "OK"})
    if got_u and got_r:
        rows.append((p, got_u["ms"], got_r["ms"]))

if rows:
    import statistics
    d = [(r[1] - r[2]) / r[1] * 100 for r in rows]
    print(f"\n  唯一 vs 重复（引擎读 ms）：" + "  ".join(f"对{p}: {u} → {r}（{dd:+.1f}%）" for p, u, r, dd in zip([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], d)))
    print(f"  重复块省下的中位：{statistics.median(d):+.1f}%")
    print("  解读：省得多 → 固定成本在为新 token 路由的专家 staging；省得少（<3%）→ 在每轮固定动作（upload 预留 / PLE 表 / chunk 计划）")
