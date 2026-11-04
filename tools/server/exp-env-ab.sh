#!/bin/bash
# One env-switch A-B-A, reusable: snapshot -> arm A -> add env keys -> arm B -> restore -> compare.
#
# Every round of this goal needs the same shape (RULES.md §2), and the box's switches are read at engine start, so
# each arm is a restart.  This driver keeps the snapshot/restore/`cmp -s`/gate boilerplate in one place and takes the
# env keys on the command line, so a switch is one line to test and the log names it.
#
# MEASURE=ttft switches the arm from bench-strata (decode) to turn-ttft (time to first token).
# Usage: exp-env-ab.sh <label> [reps] KEY=VAL|cg:KEY=VAL [KEY=VAL ...]   ("cfg:key=value" for a top-level key)
#   MEASURE=ttft with SIZES=300,4500 TTFT_REPS=3 picks the prompt sizes (default 300,3000 x2).
#   e.g. exp-env-ab.sh hc-fuse 3 STRATA_QFUSE=1 STRATA_HC_UPMIX=1
#        exp-env-ab.sh pipewin 3 "arg:--pipeline-windows=2"
#        exp-env-ab.sh split25 3 cfg:layer_split=25
# Output: /opt/ai/logs/L0xx-env-<label>-<UTC ts>.log   (L-number as printed in the first line; rename at will)
set -u
S=/opt/ai/src/strata-0.2.2
CFG=$S/strata-022.json
BIN=/opt/ai/bin
LOGS=/opt/ai/logs
TMP=/opt/ai/tmp
LABEL=${1:?label}; shift
REPS=3
if [ $# -gt 0 ] && [[ ${1} =~ ^[0-9]+$ ]]; then REPS=$1; shift; fi
[ $# -gt 0 ] || { echo "no KEY=VAL given"; exit 2; }
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT=$LOGS/env-$LABEL-$TS.log
GATE=$TMP/env-gate-$LABEL.json
ELOG=$S/strata-022.log

say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$OUT"; }

arm() {
  local tag=$1
  if [ "${MEASURE:-bench}" = "ttft" ]; then
    say "--- turn-ttft $tag (sizes ${SIZES:-300,3000} x${TTFT_REPS:-2} @45k, DSH shape) ---"
    # the tag must name the experiment: turn-ttft appends to ONE jsonl, so a bare "A"/"B" makes every run's
    # rows indistinguishable afterwards (found 2026-10-24: seven runs, all tagged A/B)
    python3 "$BIN/turn-ttft.py" "$LABEL-$tag" "${SIZES:-300,3000}" "${TTFT_REPS:-2}" 45000 300 >>"$OUT" 2>&1
  else
    say "--- bench-strata $tag ($REPS reps) ---"
    python3 "$BIN/bench-strata.py" "$tag" "$REPS" >>"$OUT" 2>&1
  fi
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

BASE=""
restore_on_exit() {
  local rc=$?
  if [ -n "$BASE" ] && [ -f "$BASE" ] && ! cmp -s "$CFG" "$BASE"; then
    echo "[$(date -u +%FT%TZ)] TRAP: the config is not the snapshot - restoring before exit" | tee -a "$OUT"
    "$BIN/use-config.sh" "$BASE" >>"$OUT" 2>&1 || echo "TRAP: restore failed; the box is left on a variant" | tee -a "$OUT"
  fi
  exit $rc
}
trap restore_on_exit EXIT INT TERM
say "=== env A-B-A: $LABEL  (env: $*) ; $REPS reps ==="
cp -p "$CFG" "$TMP/env-$LABEL-base-$TS.json"
BASE=$TMP/env-$LABEL-base-$TS.json
say "config snapshotted -> $BASE"
say "ARM A: deployed env (no restart)"
arm "A"

python3 - "$CFG" "$TMP/env-$LABEL-on-$TS.json" "$@" <<'PY' || { say "FAILED to build the variant"; exit 1; }
import json, sys
cfg = json.load(open(sys.argv[1]))
env = cfg.setdefault("env", {})
changes = []
args = cfg.get("args") or []
for pair in sys.argv[3:]:
    k, _, v = pair.partition("=")
    if k.startswith("arg:"):
        flag, _, val = k[4:].partition("=")
        if flag in args:
            i = args.index(flag)
            old = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("--") else None
            if old is not None:
                args[i + 1] = val
            else:
                args.insert(i + 1, val)
            changes.append("%s %r -> %r (args)" % (flag, old, val))
        else:
            args += [flag, val]
            changes.append("%s (absent) -> %r (args)" % (flag, val))
    elif k.startswith("cfg:"):
        key = k[4:]
        changes.append("config %s %r -> %r" % (key, cfg.get(key), v))
        cfg[key] = v
        flag = "--" + key.replace("_", "-")
        if flag in args:
            i = args.index(flag)
            if i + 1 < len(args):
                changes.append("%s %r -> %r (args)" % (flag, args[i + 1], v))
                args[i + 1] = v
    else:
        changes.append("%s %r -> %r" % (k, env.get(k), v))
        env[k] = v
json.dump(cfg, open(sys.argv[2], "w"), indent=1, ensure_ascii=False)
print("variant B: " + "; ".join(changes) + " (env now %d keys)" % len(env))
PY
say "--- restart with the variant ---"
"$BIN/use-config.sh" "$TMP/env-$LABEL-on-$TS.json" >>"$OUT" 2>&1 \
  || { say "FAILED to install the variant - restoring"; "$BIN/use-config.sh" "$TMP/env-$LABEL-base-$TS.json" >>"$OUT" 2>&1; exit 1; }
say "engine lines about ignored switches: $(grep -a "is ignored\|has no pointer-table" "$ELOG" | tail -1 | cut -c1-160)"
arm "B"

say "--- restore the deployed config ---"
"$BIN/use-config.sh" "$TMP/env-$LABEL-base-$TS.json" >>"$OUT" 2>&1 || { say "FAILED to restore"; exit 1; }
cmp -s "$CFG" "$TMP/env-$LABEL-base-$TS.json" && say "config is byte-identical to the snapshot" || say "WARNING: config differs from the snapshot"

say "=== quality gate: A vs B ==="
python3 - "$TMP" "$LABEL" <<'PY' | tee -a "$OUT" || say "gate comparison failed (missing json?) - see the arm outputs above"
import json, sys
tmp, label = sys.argv[1], sys.argv[2]
a = json.load(open("%s/env-gate-%s.json.A" % (tmp, label)))
b = json.load(open("%s/env-gate-%s.json.B" % (tmp, label)))
for i in range(len(a)):
    print("  prompt %d: A=%s  B=%s  %s" % (i + 1, a[i]["sha"], b[i]["sha"],
                                           "identical" if a[i]["answer"] == b[i]["answer"] else "DIFFERS"))
PY
say "=== done (exit codes: last command $?) ==="
