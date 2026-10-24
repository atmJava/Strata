#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A/B: does reasoning_effort 'high' vs 'medium' change the OUTPUT LANGUAGE?

Both go to the same running Strata. Strata's serve/frontend.py maps
  high   -> template 'xhigh'  -> chat_template sets a reasoning instruction
  medium -> template 'medium' -> chat_template has NO branch -> emits nothing
Everything else is identical, so any language difference is the template text.
"""
import json
import re
import sys
import urllib.request

URL = "http://127.0.0.1:8080/v1/chat/completions"
MODEL = "qwen3.8-flash-next-coder-iq1_m"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 10

TOOLS = [{
    "type": "function",
    "function": {
        "name": "grep",
        "description": "Search file contents for a regular expression.",
        "parameters": {"type": "object",
                       "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
                       "required": ["pattern"]},
    },
}]

MESSAGES = [
    {"role": "system",
     "content": "You are a coding agent working in a Java Spring Boot repository named ruoyi. "
                "The user speaks Chinese. Report findings concisely."},
    {"role": "user",
     "content": "我的需求是改造 DealerPurchaseOrderControllerMerchant 的查询对象为专用 QueryVO，"
                "支持订单号、交易方式、采购类型、评估师、状态、车辆 VIN、多车系、"
                "carType（new=新车 used=二手车）、能源类型、车身形式（suv,mpv）。"
                "你先梳理核实需求的实现可行性，是否存在问题和阻碍。"},
    {"role": "assistant",
     "content": "## 结论先行\n\n**可行，无硬阻碍**。已确认四项口径：快照冻结来源、车身形式多选、"
                "VIN 模糊 like、full 整改 + 新测试。"},
    {"role": "user",
     "content": "继续。先用一两句话说明你接下来要做什么，然后调用工具检查现状。"},
]

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def strip_code(s: str) -> str:
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


def body(effort: str) -> dict:
    return {"model": MODEL, "messages": MESSAGES, "tools": TOOLS, "temperature": 1.0,
            "max_tokens": 16384, "reasoning_effort": effort, "stream": False}


def call(effort: str):
    req = urllib.request.Request(URL, data=json.dumps(body(effort)).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read().decode())


def main():
    results = {}
    for effort in ("high", "medium"):
        rows = []
        for i in range(N):
            try:
                d = call(effort)
            except Exception as exc:                      # noqa: BLE001
                rows.append((None, None, f"ERROR {exc}"))
                continue
            msg = d["choices"][0]["message"]
            content = msg.get("content") or ""
            reason = msg.get("reasoning_content") or msg.get("reasoning") or ""
            prose = strip_code(content)
            cjk = len(CJK.findall(prose))
            lat = len(re.findall(r"[A-Za-z]", prose))
            ratio = cjk / (cjk + lat) if (cjk + lat) else None
            usage = d.get("usage", {})
            rows.append((ratio, usage.get("completion_tokens"),
                         content.replace("\n", " ")[:70]))
        results[effort] = rows

    for effort, rows in results.items():
        print(f"\n================ reasoning_effort = {effort} ================")
        ok = []
        for ratio, ct, head in rows:
            tag = "  --  " if ratio is None else f"{100 * ratio:5.1f}%"
            print(f"  proseZh={tag}  ct={str(ct):>6}  | {head}")
            if ratio is not None:
                ok.append(ratio)
        if ok:
            zh = sum(1 for r in ok if r > 0.5)
            print(f"  -> n={len(ok)}  Chinese={zh}  English={len(ok) - zh}  "
                  f"mean_proseZh={100 * sum(ok) / len(ok):.1f}%")


if __name__ == "__main__":
    main()
