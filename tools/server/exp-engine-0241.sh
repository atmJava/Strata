#!/bin/bash
# Does upstream 0.2.4.1 (engine 0.1.49.1) decode faster on this box than the deployed 0.2.2 (engine 0.1.47)?
#
# Why this is the first code-level question: the v0.2.4.1 range rewrote the CPU expert path - src/kernels/cpu and
# include/strata/kernels/cpu, 13 files, +920/-86 (AVX2/AVX512 IQ and TQ kernels, expert_layout, native_expert) - and
# that is exactly what --native and the CPU expert pool run on.  Qwen's GPU engine files did not change at all in the
# range, so any difference here is the CPU side (or nothing).
#
# The 0.2.4.1 tree has the S1 concurrent-read patch re-applied (generate.cpp is byte-identical between 0.2.2 and
# 0.2.4.1 in the range, so it applied with an offset), otherwise the comparison would confound "new upstream" with
# "no concurrent read".
#
# Arms: A (deployed 0.2.2, this boot) -> B (0.2.4.1 + S1) -> A2 (restored), each bench-strata 3 reps (single stream
# + 4 concurrent, fixed prompts, temperature 0) plus three fixed greedy prompts as the quality gate.
#
# Usage: exp-engine-0241.sh                 (no arguments)
# Output: /opt/ai/logs/L034-engine-0241-<UTC ts>.log
set -u
S=/opt/ai/src/strata-0.2.2
CFG=$S/strata-022.json
BIN=/opt/ai/bin
LOGS=/opt/ai/logs
TMP=/opt/ai/tmp
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT=$LOGS/L034-engine-0241-$TS.log
NEW_EXE=/opt/ai/src/strata-0.2.4.1/build/strata
GATE=$TMP/L034-gate.json

say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$OUT"; }

[ -x "$NEW_EXE" ] || { say "no 0.2.4.1 binary at $NEW_EXE - build it first"; exit 1; }
say "0.2.4.1 binary: $("$NEW_EXE" --version | head -1)  |  deployed: $(/opt/ai/src/strata-0.2.2/build/strata --version | head -1)"

arm() {
  local tag=$1
  say "--- bench-strata $tag (single + 4 concurrent, 3 reps) ---"
  python3 "$BIN/bench-strata.py" "$tag" 3 >>"$OUT" 2>&1
  say "--- quality gate $tag ---"
  python3 - "$GATE" "$tag" <<'PY' | tee -a "$OUT"
import hashlib, json, sys, urllib.request
CFG = "/opt/ai/src/strata-0.2.2/strata-022.json"
key = json.load(open(CFG)).get("api_key", "")
prompts = ["用三句话说明为什么混合专家模型在显存受限时会把专家放在内存里。",
           "计算 17×23 并只用一句话说明步骤。",
           "写一个 Python 函数把嵌套字典拍平成键路径列表，并给出一个调用例子。"]
out = []
for p in prompts:
    body = {"model": "qwen3.8-flash-next-iq3_xxs-abliterated", "messages": [{"role": "user", "content": p}],
            "max_tokens": 300, "temperature": 0, "reasoning_effort": "none"}
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = "Bearer " + key
    req = urllib.request.Request("http://127.0.0.1:8080/v1/chat/completions", data=json.dumps(body).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=1800) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    txt = d["choices"][0]["message"].get("content") or ""
    out.append({"sha": hashlib.sha256(txt.encode()).hexdigest()[:16], "answer": txt})
    print("  gate %s  %s" % (out[-1]["sha"], txt[:80].replace("\n", " ")))
json.dump(out, open(sys.argv[1] + "." + sys.argv[2], "w"), ensure_ascii=False, indent=1)
PY
}

say "=== L034: upstream 0.2.4.1 (engine 0.1.49.1) vs deployed 0.2.2 (0.1.47) ==="
cp -p "$CFG" "$TMP/engine-0241-base-$TS.json"
say "config snapshotted -> $TMP/engine-0241-base-$TS.json"
say "ARM A: the deployed engine, this boot (no restart)"
arm "A-0241"

python3 - "$CFG" "$TMP/engine-0241-new-$TS.json" "$NEW_EXE" <<'PY' || { say "FAILED to build the B variant"; exit 1; }
import json, sys
cfg, dst, exe = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
print("variant B: exe %s -> %s" % (cfg.get("exe"), exe))
cfg["exe"] = exe
json.dump(cfg, open(dst, "w"), indent=1, ensure_ascii=False)
PY
say "--- restart on 0.2.4.1 + S1 ---"
"$BIN/use-config.sh" "$TMP/engine-0241-new-$TS.json" >>"$OUT" 2>&1 \
  || { say "FAILED to install variant B - restoring"; "$BIN/use-config.sh" "$TMP/engine-0241-base-$TS.json" >>"$OUT" 2>&1; exit 1; }
arm "B-0241"

say "--- restore the deployed engine and re-run, so boot drift is measured ---"
"$BIN/use-config.sh" "$TMP/engine-0241-base-$TS.json" >>"$OUT" 2>&1 || { say "FAILED to restore"; exit 1; }
cmp -s "$CFG" "$TMP/engine-0241-base-$TS.json" && say "config is byte-identical to the snapshot" || say "WARNING: config differs from the snapshot"
arm "A2-0241"

say "=== quality gate, arm by arm ==="
python3 - "$TMP" "$TS" <<'PY' | tee -a "$OUT"
import glob, json, sys
tmp, ts = sys.argv[1], sys.argv[2]
arms = {a: (json.load(open("%s/L034-gate.json.%s" % (tmp, a))) if glob.glob("%s/L034-gate.json.%s" % (tmp, a)) else None)
        for a in ("A-0241", "B-0241", "A2-0241")}
for i in range(3):
    row = "  prompt %d: " % (i + 1) + "  ".join("%s=%s" % (a.split("-")[0], arms[a][i]["sha"] if arms[a] else "?") for a in arms)
    same = arms["A-0241"] and arms["B-0241"] and arms["A-0241"][i]["answer"] == arms["B-0241"][i]["answer"]
    print(row + "   A vs B: " + ("identical" if same else "DIFFERS"))
PY
say "=== done ==="
