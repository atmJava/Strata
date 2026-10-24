#!/bin/bash
# Does STRATA_MMQ_DIRECT pay off on this box?  A-B-A with the MMQ tab build, and a quality gate on the way through.
#
# L-030 measured the prompt path's phase map: for a 461-token re-read, stage 1 spends 185 ms of its 610 ms in
# "wait copy" - the copy of every expert the chunk routes to into the buffer MMQ wants (resident ones too) - while a
# 4,524-token read spends 2.3% there.  STRATA_MMQ_DIRECT=1 reads resident experts in place instead, but the deployed
# binary has no pointer-table path at all (STRATA_MMQ_TAB was off), so this needs the separate build the previous
# round made in build-mmqtab with -DSTRATA_MMQ_TAB=ON.
#
# Arms, all on the same DSH-shaped probe (turn-ttft 300,3000 3 @45000):
#   A  old binary, no MMQ_DIRECT      (the boot already running - no restart)
#   B  build-mmqtab, STRATA_MMQ_DIRECT=1
#   A2 build-mmqtab, no MMQ_DIRECT    (isolates the switch from the binary change)
# then the base config is restored byte for byte.  The quality gate is three fixed greedy prompts per arm: the switch
# is documented as bitwise-equal to the copied path (prefill_mmq_tab_test, 874 products, nine types), and identical
# answers on this box would say so end to end; where they differ the diff is printed rather than hidden.
#
# Usage: mmq_direct_ab.sh            (no arguments; sizes and context are the ones L-030 used)
# Output: /opt/ai/logs/L031-mmq-direct-<UTC ts>.log
set -u
S=/opt/ai/src/strata-0.2.2
CFG=$S/strata-022.json
BIN=/opt/ai/bin
LOGS=/opt/ai/logs
TMP=/opt/ai/tmp
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT=$LOGS/L031-mmq-direct-$TS.log
OLD_EXE=$S/build/strata
# use-config.sh finds the engine with `pgrep -f "build/strata --serve"`, so the MMQ build lives under a path that
# matches it; build-mmqtab/strata itself does not, and the install would report "RESTART NOT VISIBLE" after 120 s.
NEW_EXE=/opt/ai/engines/mmq-tab/build/strata
GATE=$TMP/L031-gate.json

say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$OUT"; }

if [ ! -x "$NEW_EXE" ]; then
  say "the MMQ tab build is missing at $NEW_EXE - build it first (cmake --build build-mmqtab --target strata)"; exit 1
fi

gate_run() {   # three fixed greedy prompts; answers compared word for word across arms
  python3 - "$GATE" <<'PY' | tee -a "$OUT"
import hashlib, json, sys, time, urllib.request, os
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
    req = urllib.request.Request("http://127.0.0.1:8080/v1/chat/completions",
                                 data=json.dumps(body).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=1800) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    txt = (d["choices"][0]["message"].get("content") or "")
    out.append({"prompt": p, "answer": txt, "sha": hashlib.sha256(txt.encode()).hexdigest()[:16]})
    print("  gate %-6s %s" % (out[-1]["sha"], txt[:90].replace("\n", " ")))
json.dump(out, open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
print("  gate answers ->", sys.argv[1])
PY
  cp -p "$GATE" "$TMP/L031-gate-$2.json" 2>/dev/null || true
}

turns() {
  local tag=$1 before
  before=$(wc -l < "$S/strata-022.log")
  say "--- turn-ttft $tag (engine log was at line $before) ---"
  python3 "$BIN/turn-ttft.py" "$tag" 300,3000 3 45000 300 >>"$OUT" 2>&1
  say "--- reads the engine reported for $tag ---"
  tail -n +"$((before + 1))" "$S/strata-022.log" | grep -aE "strata serve: prompt [0-9]+ tokens|reads resident experts in place|MMQ_DIRECT" \
    | cut -c1-230 | tee -a "$OUT"
}

say "=== L031: STRATA_MMQ_DIRECT=1 (MMQ tab build) vs the deployed binary ==="
cp -p "$CFG" "$TMP/mmq-ab-base-$TS.json"
say "config snapshotted -> $TMP/mmq-ab-base-$TS.json"
say "ARM A (old binary, no MMQ_DIRECT) is the boot running now"
turns "A" A
gate_run "$GATE" A

python3 - "$CFG" "$TMP/mmq-on-$TS.json" "$NEW_EXE" <<'PY' || { say "FAILED to build the MMQ variant"; exit 1; }
import json, sys
cfg, dst, exe = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
before = {"exe": cfg.get("exe"), "direct": (cfg.get("env") or {}).get("STRATA_MMQ_DIRECT")}
cfg["exe"] = exe
cfg.setdefault("env", {})["STRATA_MMQ_DIRECT"] = "1"
json.dump(cfg, open(dst, "w"), indent=1, ensure_ascii=False)
print("variant B: exe %s -> %s, STRATA_MMQ_DIRECT %r -> 1" % (before["exe"], exe, before["direct"]))
PY
say "--- restart: MMQ tab binary + STRATA_MMQ_DIRECT=1 ---"
"$BIN/use-config.sh" "$TMP/mmq-on-$TS.json" >>"$OUT" 2>&1 || { say "FAILED to install variant B - restoring the deployed config"; "$BIN/use-config.sh" "$TMP/mmq-ab-base-$TS.json" >>"$OUT" 2>&1; exit 1; }
turns "B" B
gate_run "$GATE" B

python3 - "$CFG" "$TMP/mmq-off-$TS.json" "$NEW_EXE" <<'PY' || { say "FAILED to build the A2 variant"; exit 1; }
import json, sys
cfg, dst, exe = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
cfg["exe"] = exe
cfg.setdefault("env", {}).pop("STRATA_MMQ_DIRECT", None)
json.dump(cfg, open(dst, "w"), indent=1, ensure_ascii=False)
print("variant A2: the MMQ tab binary, STRATA_MMQ_DIRECT unset")
PY
say "--- restart: MMQ tab binary, switch off (isolates the switch from the binary) ---"
"$BIN/use-config.sh" "$TMP/mmq-off-$TS.json" >>"$OUT" 2>&1 || { say "FAILED to install variant A2 - restoring the deployed config"; "$BIN/use-config.sh" "$TMP/mmq-ab-base-$TS.json" >>"$OUT" 2>&1; exit 1; }
turns "A2" A2
gate_run "$GATE" A2

say "--- restore the deployed binary and config byte for byte ---"
"$BIN/use-config.sh" "$TMP/mmq-ab-base-$TS.json" >>"$OUT" 2>&1 || { say "FAILED to restore"; exit 1; }
cmp -s "$CFG" "$TMP/mmq-ab-base-$TS.json" && say "config is byte-identical to the snapshot" || say "WARNING: config differs from the snapshot"

say "--- quality gate: the three answers, arm by arm ---"
python3 - "$TMP" "$TS" <<'PY' | tee -a "$OUT"
import glob, json, sys
tmp, ts = sys.argv[1], sys.argv[2]
arms = {}
for arm in ("A", "B", "A2"):
    f = "%s/L031-gate-%s.json" % (tmp, arm)
    arms[arm] = json.load(open(f)) if glob.glob(f) else None
for i in range(3):
    row = "  prompt %d: " % (i + 1) + "  ".join("%s=%s" % (a, arms[a][i]["sha"] if arms[a] else "?") for a in ("A", "B", "A2"))
    same = arms["A"] and arms["B"] and arms["A"][i]["answer"] == arms["B"][i]["answer"]
    print(row + "   A vs B: " + ("identical" if same else "DIFFERS"))
PY
say "=== done ==="
