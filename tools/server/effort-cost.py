"""思考档位 A/B（服务端侧，不改客户端配置）：同一问题在 none/low/medium 下的墙钟与生成量。

目的：给用户一个可直接用的数字——把 DSH 的 reasoning_effort 降一档，在这台机器上能省多少墙钟。
做法：3 个真实形状的问题（代码/中文技术/工具调用式），每个档位各跑一次，temperature 0，max_tokens 1500。
      指标取客户端墙钟（用户感受）、生成 token 数、以及"思考"占比（reasoning_content 的字符数）。
用法: effort-cost.py [tag]
结果追加到 /opt/ai/logs/effort-cost.jsonl
"""
import json
import statistics
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8080/v1/chat/completions"
MODEL = "qwen3.8-flash-next-iq3_xxs-abliterated"
TAG = sys.argv[1] if len(sys.argv) > 1 else "run"
OUT = "/opt/ai/logs/effort-cost.jsonl"

PROMPTS = [
    ("code", "用 Python 写一个函数，把嵌套的 list 展平（任意深度），要求保留原顺序、支持生成器，并给出两个边界用例。"),
    ("zh-tech", "解释一下混合专家模型里 top-k 路由的负载均衡损失为什么必要，300 字左右，分点。"),
    ("agent", "你现在要做一次代码审查：下面函数在并发下有问题，请指出问题并给出修改后的版本。\n\n"
              "def get(cache, k):\n    if k not in cache:\n        cache[k] = load(k)\n    return cache[k]"),
]
EFFORTS = ["none", "low", "medium"]


def call(prompt, effort):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1500, "temperature": 0, "reasoning_effort": effort}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    wall = time.time() - t0
    msg = (d.get("choices") or [{}])[0].get("message") or {}
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    u = d.get("usage") or {}
    ti = d.get("timings") or {}
    return {"wall_s": wall, "out_tok": u.get("completion_tokens"),
            "reason_chars": len(reasoning), "content_chars": len(content),
            "prompt_n": ti.get("prompt_n"), "decode_tok_s": ti.get("predicted_per_second"),
            "finish": (d.get("choices") or [{}])[0].get("finish_reason"), "content": content}


print(f"  标签 {TAG}")
rows = []
for name, prompt in PROMPTS:
    print(f"\n  --- {name} ---")
    for eff in EFFORTS:
        r = call(prompt, eff)
        r.update({"tag": TAG, "task": name, "effort": eff})
        rows.append(r)
        print(f"    {eff:>6}: 墙钟 {r['wall_s']:>6.1f}s  生成 {str(r['out_tok']):>5} tok  "
              f"思考 {r['reason_chars']:>5} 字符  回答 {r['content_chars']:>5} 字符  "
              f"decode {r['decode_tok_s'] or 0:>5.1f} tok/s  finish={r['finish']}")

print("\n  === 各档位汇总（3 个任务的中位）===")
for eff in EFFORTS:
    sub = [r for r in rows if r["effort"] == eff]
    if not sub:
        continue
    print(f"    {eff:>6}: 墙钟中位 {statistics.median(r['wall_s'] for r in sub):>6.1f}s  "
          f"生成中位 {statistics.median(r['out_tok'] or 0 for r in sub):>6.0f} tok  "
          f"思考字符中位 {statistics.median(r['reason_chars'] for r in sub):>6.0f}")

base = [r for r in rows if r["effort"] == "medium"]
for eff in ("low", "none"):
    sub = [r for r in rows if r["effort"] == eff]
    if base and sub:
        bm = statistics.median(r["wall_s"] for r in base)
        sm = statistics.median(r["wall_s"] for r in sub)
        print(f"    medium → {eff}: 墙钟中位 {bm:.1f}s → {sm:.1f}s（{(sm/bm-1)*100:+.1f}%）")

with open(OUT, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"tag": TAG, "rows": [{k: v for k, v in r.items() if k != "content"} for r in rows]},
                        ensure_ascii=False) + "\n")
print(f"\n  结果已追加到 {OUT}")
