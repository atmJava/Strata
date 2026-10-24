#!/usr/bin/env python3
"""Print the conversation-cache and memory fields of the server metrics."""
import json
import urllib.request

req = urllib.request.Request("http://127.0.0.1:8080/metrics", headers={"Authorization": "Bearer 111"})
with urllib.request.urlopen(req, timeout=10) as r:
    d = json.load(r)

print("top-level:", list(d))
print("conversation_cache:", json.dumps(d.get("conversation_cache"), indent=1)[:800])
print("totals:", json.dumps(d.get("totals"), indent=1)[:800])
print("requests_kept:", json.dumps(d.get("requests_kept"), indent=1)[:400])
engine = d.get("engine") or {}
live = d.get("live") or {}
print("engine sample:", json.dumps({k: engine[k] for k in list(engine)[:10]}, indent=1))
print("live sample:", json.dumps({k: live[k] for k in list(live)[:10]}, indent=1))
