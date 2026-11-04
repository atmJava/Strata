#!/usr/bin/env python3
"""DSH 形状的多轮会话：每轮比上一轮长，测「每轮实际要重读多少」与首字延迟。

为什么需要它：真实负载里 91% 的请求复用 ≥90% 前缀，文档坑 #47 记「58% 的预填时间
花在 324 次重读 >10k token 的请求上」。decode 基准按设计看不到这条路径。本工具
按 DSH 的真实形状走：messages=[...] 累积助手回复 + 新的用户消息，每轮新增一段
**唯一**内容（模拟工具输出），于是「上一轮新增的内容 = 这一轮必须重读的部分」。

用法:
    turn-ttft.py <tag> [新增长度,逗号分隔] [每档轮数] [上下文token] [生成token]
默认: turn-ttft.py run 300,1200,3000 3 45000 300
结果追加到 /opt/ai/logs/turn-ttft.jsonl
引擎日志用 TTFT_LOG 指（默认 /opt/ai/src/strata-0.2.2/strata-022.log）。
    以前这里写死了 0.1.40.1 那套安装的日志路径，换了安装就一行都读不到（2026-10-24 修的）。
"""
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.request

LOG = os.environ.get("TTFT_LOG", "/opt/ai/src/strata-0.2.2/strata-022.log")
CFG = os.environ.get("TTFT_CFG", "/opt/ai/src/strata-0.2.2/strata-022.json")
MODEL = "qwen3.8-flash-next-iq3_xxs-abliterated"
OUT = "/opt/ai/logs/turn-ttft.jsonl"
URL = "http://127.0.0.1:8080/v1/chat/completions"

TAG = sys.argv[1] if len(sys.argv) > 1 else "run"
NEW = [int(x) for x in (sys.argv[2] if len(sys.argv) > 2 else "300,1200,3000").split(",")]
ROUNDS = int(sys.argv[3]) if len(sys.argv) > 3 else 3
CTX = int(sys.argv[4]) if len(sys.argv) > 4 else 45000
GEN = int(sys.argv[5]) if len(sys.argv) > 5 else 300
RID = f"{int(time.time()) % 100000}"

def api_key():
    """这台机器要 key。这套脚本写的时候服务还没 key，0.2.2 之后不发就 401（2026-10-24 修的）。"""
    try:
        return json.load(open(CFG)).get("api_key", "")
    except Exception:
        return os.environ.get("QE_KEY", "")


PROMPT_RE = r"prompt [0-9]+ tokens = [0-9]+ reused \+ [0-9]+ read in [0-9]+ ms \([0-9.]+ tok/s\)"
GEN_RE = r"[0-9]+ generated in [0-9]+ ms \([0-9.]+ tok/s\)"
ASK = "\n\n请用中文写一篇约 300 字的技术说明，分节论述。"


def grep(pat):
    o = subprocess.run(["grep", "-oE", pat, LOG], capture_output=True, text=True, timeout=30).stdout
    return o.strip().splitlines()


def post(msgs, mt):
    body = {"model": MODEL, "messages": msgs, "max_tokens": mt,
            "temperature": 0, "reasoning_effort": "none"}
    hdrs = {"Content-Type": "application/json"}
    key = api_key()
    if key:
        hdrs["Authorization"] = "Bearer " + key
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers=hdrs)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=3600) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    wall = time.time() - t0
    try:
        reply = d["choices"][0]["message"]["content"] or ""
    except Exception:
        reply = ""
    return wall, reply


def last(pat, before, rx):
    for ln in reversed(grep(pat)[before:]):
        m = re.search(rx, ln)
        if m:
            return m
    return None


def block(n, tag):
    """约 n 个 token 的唯一内容（英文/数字混排，避免与历史重合）。

    一个 `RIDtag#####` 串实测约 12 token（2026-10-24 那次按老的 /3 跑，"45000"实际造出 180,018 token，
    标称差 4 倍；跑完还撞到上下文上限吃了 400）。所以除数改成 12，标称才当真。
    """
    items = max(1, int(n / 12.0))
    return " ".join(f"{RID}{tag}{i:05d}" for i in range(items))


print(f"  标签 {TAG}  上下文≈{CTX}  新增 {NEW}  每档 {ROUNDS} 轮  生成 {GEN}")
print("  预热 …")
post([{"role": "user", "content": "warm OK"}], 8)
time.sleep(2)

rows = []
try:
    msgs = [{"role": "user", "content": block(CTX, "base") + "\n\n请只回复 OK。"}]
    p0, g0 = len(grep(PROMPT_RE)), len(grep(GEN_RE))
    wall, reply = post(msgs, 8)
    m = last(PROMPT_RE, p0, r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms \(([\d.]+) tok/s\)")
    print(f"  建上下文：ctx={m.group(1)} token  {int(m.group(4))/1000:.1f}s  墙钟 {wall:.1f}s" if m else "  建上下文失败")
    msgs.append({"role": "assistant", "content": reply})

    for n in NEW:
        print(f"\n  --- 每轮新增 ≈{n} token ---")
        for i in range(1, ROUNDS + 1):
            msgs.append({"role": "user", "content": "工具输出：\n" + block(n, f"n{n}r{i}") + ASK})
            pp, gp = len(grep(PROMPT_RE)), len(grep(GEN_RE))
            try:
                wall, reply = post(msgs, GEN)
            except urllib.error.HTTPError as e:
                print(f"    turn {i}: HTTP {e.code}（{sum(len(x['content']) for x in msgs)} 字符）—— 到上下文上限了，这一档就此停")
                break
            msgs.append({"role": "assistant", "content": reply})
            pm = last(PROMPT_RE, pp, r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms \(([\d.]+) tok/s\)")
            gm = last(GEN_RE, gp, r"(\d+) generated in (\d+) ms \(([\d.]+) tok/s\)")
            if not (pm and gm):
                print(f"    turn {i}: 未取到日志行（墙钟 {wall:.1f}s）")
                continue
            ctx, reused, read, pms = int(pm.group(1)), int(pm.group(2)), int(pm.group(3)), int(pm.group(4))
            dec = float(gm.group(3))
            rows.append({"new": n, "turn": i, "ctx": ctx, "reused": reused, "read": read,
                         "ttft_ms": pms, "decode_tok_s": dec, "wall_s": wall})
            print(f"    turn {i}: ctx={ctx:>6} reused={reused:>6} read={read:>5} "
                  f"首字={pms/1000:>5.2f}s decode={dec:>6.1f} tok/s 墙钟={wall:>5.1f}s")
        sub = [r for r in rows if r["new"] == n]
        if sub:
            print(f"    >>> 新增 {n}: 重读中位 {statistics.median(r['read'] for r in sub):.0f} token，"
                  f"首字中位 {statistics.median(r['ttft_ms'] for r in sub)/1000:.2f}s，"
                  f"decode 中位 {statistics.median(r['decode_tok_s'] for r in sub):.1f} tok/s")
except urllib.error.HTTPError as e:
    print(f"  HTTP {e.code}：到上下文上限，就此收尾（已量的行照写）")
finally:
    with open(OUT, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"tag": TAG, "ctx": CTX, "new": NEW, "rounds": ROUNDS, "rows": rows},
                            ensure_ascii=False) + "\n")
    print(f"\n  结果已追加到 {OUT}")
