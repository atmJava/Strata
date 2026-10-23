#!/usr/bin/env python3
"""A small, self-scoring quality eval: run it once per BF16_TC arm and compare the two JSONs.

Every task has a programmatically checkable answer, so the comparison is "how many did it get right"
rather than a judgement call.  The engine is greedy here (no sampling is sent), and the A/B control
showed byte-identical output across restarts, so ONE run per arm is enough.

Each result carries what the scorer needs: content, reasoning, usage, finish_reason.  The Chinese
prose tasks have no expected answer; they are scored on Chinese ratio and on the same 12-word passage
repetition coverage that Strata's own reasoning-loop detector uses (server.py:157).

Usage:  quality_eval.py <outdir>
"""
import json
import os
import pathlib
import re
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8080"
OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/qeval")
OUT.mkdir(parents=True, exist_ok=True)
MAX_TOKENS = 2200
EFFORT = "high"                      # -> xhigh in Strata's template


def facts(n=45):
    return "\n".join("Fact %02d: the reservoir at station %d holds %d cubic metres and was commissioned in %d."
                     % (i, i * 7, i * 1237 + 41, 1900 + i) for i in range(1, n + 1))


BUGGY = """```js
function sumTo(n) {
  let s = 0;
  for (let i = 1; i < n; i++) {
    s += i;
  }
  return s;
}
```"""

# id, prompt, expectation.  Kinds: number | contains | json | prose
TASKS = [
    ("calc-1", "一个数加上 37 等于 91，这个数是多少？只输出数字，不要任何其它文字。", ("number", 54)),
    ("calc-2", "3 个工人 4 天做 72 个零件。照这个效率，6 个工人 5 天能做多少个零件？只输出数字。",
     ("number", 180)),
    ("calc-3", "一个水箱：进水管单独注满要 4 小时，出水管单独排空要 6 小时。两管同时打开，水箱初始为空，"
               "需要多少小时注满？只输出数字（小数保留两位）。", ("number", 12)),
    ("calc-4", "一个班有 45 名学生，其中 3/5 是女生。女生比男生多几人？只输出数字。", ("number", 9)),
    ("logic-1", "已知：所有玫瑰都是花；有些花很快凋谢。能否必然推出「有些玫瑰很快凋谢」？"
                "只回答「能」或「不能」，不要解释。", ("contains", "不能")),
    ("zh-fact-1", "中国的首都是哪座城市？只输出城市名。", ("contains", "北京")),
    ("zh-fact-2", "一年当中哪个月份的天数最少？只输出那个月份的数字。", ("number", 2)),
    ("json-1", '只输出一个 JSON 对象，包含 "a" 和 "b" 两个键，值分别是数字 1 和字符串 "x"。'
               "不要任何其它文字，不要代码块标记。", ("json", {"a": 1, "b": "x"})),
    ("json-2", "只输出一个 JSON 数组，元素依次是 1 到 5 这五个整数。不要任何其它文字，不要代码块标记。",
     ("json", [1, 2, 3, 4, 5])),
    ("extract-1", "逐字输出第 27 条 Fact 的整行内容，不要任何其它文字。\n\n" + facts(),
     ("contains", "Fact 27: the reservoir at station 189 holds 33440 cubic metres and was commissioned in 1927.")),
    ("code-1", "下面这段 JavaScript 有一处 off-by-one 错误。只输出出错的行号数字（从 1 开始计）。\n\n" + BUGGY,
     ("number", 3)),
    ("zh-prose-1", "请用中文写一段约 250 字的说明，讲清楚「如何给一台长期运行的服务器做温度监控」，"
                   "包括要采集哪些读数、多久采一次、什么阈值该告警。写成连贯的段落，不要分点、不要标题。",
     ("prose", None)),
    ("zh-prose-2", "请用中文写一段约 250 字的说明，讲清楚「为什么磁盘的可用空间会突然减少」，"
                   "至少提到三种常见原因。写成连贯的段落，不要分点、不要标题。", ("prose", None)),
]


def post(text, max_tokens=MAX_TOKENS):
    payload = {
        "model": "qwen3.8-flash-next-iq2_xs",
        "messages": [{"role": "user", "content": text}],
        "max_tokens": max_tokens,
        "reasoning_effort": EFFORT,
        "stream": True,
        # no temperature / top_p / top_k / seed: greedy, so the run is reproducible (verified)
    }
    # L-002: a thinking budget sent per request, so the server's own settings and every other
    # client stay untouched.  Unset or 0: send nothing, and the config's default applies.
    budget = int(os.environ.get("QE_BUDGET", "0") or 0)
    if budget:
        payload["reasoning_budget_tokens"] = budget
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    # The box grew an API key after this script was written; without it every task comes back
    # 401 and the score reads 0/11 instead of erroring out.  QE_KEY carries it in.
    key = os.environ.get("QE_KEY", "")
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=body,
                                 headers=headers)
    reasoning, content, finish, usage = [], [], None, None
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=3600) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            try:
                j = json.loads(payload)
            except ValueError:
                continue
            if j.get("usage"):
                usage = j["usage"]
            for ch in j.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("reasoning_content"):
                    reasoning.append(d["reasoning_content"])
                if d.get("content"):
                    content.append(d["content"])
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
    return {"reasoning": "".join(reasoning), "content": "".join(content),
            "finish": finish, "usage": usage, "seconds": round(time.time() - t0, 2)}


results = {}
for tid, prompt, expect in TASKS:
    print("=== %s ===" % tid, flush=True)
    try:
        out = post(prompt)
    except Exception as e:                                   # noqa: BLE001
        print("    FAILED: %r" % (e,), flush=True)
        out = {"error": repr(e)}
    out["prompt"] = prompt
    out["expect"] = expect
    results[tid] = out
    print("    finish=%s  reasoning=%d  content=%d  seconds=%s"
          % (out.get("finish"), len(out.get("reasoning") or ""), len(out.get("content") or ""),
             out.get("seconds")), flush=True)
    print("    answer: %r" % (out.get("content") or "")[:220], flush=True)

(OUT / "responses.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
print("saved", OUT / "responses.json", flush=True)


# --------------------------------------------------------------------------------------------------- scoring
def normalize(s):
    s = (s or "").strip()
    s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s).strip()      # a stray code fence
    return s


def first_json_block(s):
    s = normalize(s)
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = s.find(opener), s.rfind(closer)
        if i >= 0 and j > i:
            try:
                return json.loads(s[i:j + 1])
            except ValueError:
                pass
    try:
        return json.loads(s)
    except ValueError:
        return None


def numbers_in(s):
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", s or "")]


def chinese_ratio(s):
    s = s or ""
    han = len(re.findall(r"[\u4e00-\u9fff]", s))
    return han / max(1, len(s))


def repeat_coverage(text, width=12, window=2000, history=30000):
    """Strata's own detector (serve/server.py reasoning_repeat_coverage), for comparison."""
    words = re.findall(r"\w+|[^\w\s]", (text or "").lower())[-history:]
    if len(words) < window:
        return 0.0
    grams = [tuple(words[i:i + width]) for i in range(len(words) - width + 1)]
    counts = {}
    for g in grams:
        counts[g] = counts.get(g, 0) + 1
    start = max(0, len(words) - window)
    covered = set()
    for i in range(start, len(grams)):
        if counts[grams[i]] >= 3:
            covered.update(range(i, i + width))
    return len(covered) / (len(words) - start)


def check(kind, expect, content):
    got = normalize(content)
    if kind == "number":
        nums = numbers_in(got)
        return bool(nums) and abs(nums[-1] - expect) < 0.01, got[:60]
    if kind == "contains":
        return expect in got, got[:60]
    if kind == "json":
        parsed = first_json_block(got)
        return parsed == expect, json.dumps(parsed)[:60] if parsed is not None else "(unparseable)"
    return None, got[:60]                     # prose: metrics only


print("\n" + "=" * 92)
print(f"{'task':<14} {'ok':>3} {'finish':>7} {'ansCh':>6} {'rsnCh':>6} {'zh%':>6} {'LoopCov':>7}  answer")
n_ok = n_scored = 0
for tid, prompt, expect in TASKS:
    r = results[tid]
    kind, exp = expect
    ok, shown = check(kind, exp, r.get("content"))
    if ok is not None:
        n_scored += 1
        n_ok += bool(ok)
    ratio = chinese_ratio(r.get("content")) if kind == "prose" else chinese_ratio(
        (r.get("content") or "") + (r.get("reasoning") or ""))
    cov = repeat_coverage(r.get("reasoning"))
    print(f"{tid:<14} {('-' if ok is None else ('Y' if ok else 'n')):>3} {str(r.get('finish')):>7} "
          f"{len(r.get('content') or ''):>6} {len(r.get('reasoning') or ''):>6} {ratio * 100:>5.1f}% "
          f"{cov:>7.3f}  {shown!r}")
print("=" * 92)
print(f"SCORED: {n_ok}/{n_scored} correct")
