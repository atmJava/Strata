#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Real-context A/B on reasoning_effort, using the runtime /settings hook.

The engine currently runs WITH the config's presence_penalty=1.0, so both arms
share that; this isolates the effort variable against the real 80-message
session context (which is what actually went English at 03:33 UTC).

A final "继续 + narrate" user turn forces the same short narration + tool call
shape that turned English in the session, so the visible text always exists.
"""
import json
import re
import sys
import time
import urllib.request

CHAT = "http://127.0.0.1:8080/v1/chat/completions"
SETTINGS = "http://127.0.0.1:8080/settings"

BASE = json.load(open("/opt/ai/bin/replay_ctx.json"))
BASE["model"] = "qwen3.8-flash-next-coder-iq1_m"
NARRATE = {"role": "user",
           "content": "继续。先用一两句话说明你接下来要做什么，然后调用工具检查现状。"}

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def strip_code(s):
    s = re.sub(r"```[\s\S]*?```", " ", s)
    s = re.sub(r"`[^`]*`", " ", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"https?://\S+", " ", s)

    def tok(m):
        w = m.group(0)
        if re.search(r"[/\\.]", w) or "_" in w or re.search(r"[a-z][A-Z]", w) or len(w) > 24:
            return " "
        return w

    return re.sub(r"[A-Za-z0-9_./\\-]*[A-Za-z_][A-Za-z0-9_./\\-]*", tok, s)


def ratio(s):
    p = strip_code(s or "")
    c = len(CJK.findall(p))
    l = len(re.findall(r"[A-Za-z]", p))
    return (c / (c + l)) if (c + l) else None


def set_effort(level):
    body = json.dumps({"defaults": {"reasoning_effort": level}}).encode() if level else b'{"defaults":null}'
    req = urllib.request.Request(SETTINGS, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    for level in ("high", "medium"):
        print(f"\n########## /settings reasoning_effort={level} -> {set_effort(level)} ##########", flush=True)
        got = []
        for i in range(1, runs + 1):
            body = dict(BASE)
            body["messages"] = BASE["messages"] + [NARRATE]
            req = urllib.request.Request(CHAT, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            t0 = time.time()
            try:
                with urllib.request.urlopen(req, timeout=2400) as r:
                    d = json.load(r)
            except Exception as exc:  # noqa: BLE001
                print(f"  r{i}: ERR {exc}", flush=True)
                continue
            msg = d["choices"][0]["message"]
            content = msg.get("content") or ""
            reason = msg.get("reasoning_content") or ""
            rr, lr = ratio(content), ratio(reason)
            if rr is not None:
                got.append(rr)
            u = d.get("usage", {})
            print(f"  r{i}: text_zh={('  --  ' if rr is None else f'{100*rr:5.1f}%')} "
                  f"reason_zh={('  --  ' if lr is None else f'{100*lr:5.1f}%')} len={len(content):<5} "
                  f"ct={u.get('completion_tokens')} finish={d['choices'][0].get('finish_reason')} "
                  f"{time.time()-t0:.0f}s", flush=True)
            print(f"       {json.dumps(content.replace(chr(10),' ')[:130], ensure_ascii=False)}", flush=True)
        if got:
            zh = sum(1 for r in got if r > 0.5)
            print(f"  ==> effort={level}: n={len(got)} Chinese={zh} English={len(got)-zh} "
                  f"mean_text_zh={100*sum(got)/len(got):.1f}%", flush=True)
    print(f"\n########## reset shared settings -> {set_effort(None)} ##########", flush=True)


if __name__ == "__main__":
    main()
