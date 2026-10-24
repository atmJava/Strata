#!/bin/bash
# The decode phase map, from the engine's own profiler.  No new code: STRATA_VERIFY_PROFILE already times a window.
#
# verify.cpp:935 turns prof_on_ on, and verify.cpp:2409 (profile_report) prints, per window and per stage, ms/window
# for each named phase - hc-read0, the q8 gemvs, conv, rec, kv-idx, k/v+norm-rope, kv append, q, scores+topk,
# kv-resolve, attention, gate, out-proj, router+plan+bell, shared+quant, waitA, VRAM hits, waitB, PCIe grp, waitCPU,
# copy+combine, (gap), head - split into GDN and QSA layers.  generate.cpp prints it next to its own per-window
# accounting ("strata batch: N windows, avg ... = run ... + commit ... + emit ... + adapt wait ...") whenever the
# slots go idle.  That line answers "GPU vs CPU pool vs copies vs commit" per window, which is exactly the question
# a code-level optimisation has to answer first.
#
# Arms: A (deployed config, the boot running now - the L-034 numbers are the reference), B (same config plus
# STRATA_VERIFY_PROFILE=1) and the restore.  The instrument's own cost is read from B's bench numbers against A's,
# per the ledger rule that the probe is measured before its readings are believed.
#
# Usage: exp-decode-profile.sh [reps]        default 3
# Output: /opt/ai/logs/L035-decode-profile-<UTC ts>.log
set -u
S=/opt/ai/src/strata-0.2.2
CFG=$S/strata-022.json
BIN=/opt/ai/bin
LOGS=/opt/ai/logs
TMP=/opt/ai/tmp
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT=$LOGS/L035-decode-profile-$TS.log
ELOG=$S/strata-022.log
REPS=${1:-3}

say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$OUT"; }

arm() {
  local tag=$1
  say "--- bench-strata $tag ($REPS reps: single 400 + four concurrent 400) ---"
  python3 "$BIN/bench-strata.py" "$tag" "$REPS" >>"$OUT" 2>&1
}

profile() {   # the engine's own lines from this arm's window
  local tag=$1 before=$2
  say "--- decode accounting the engine printed for $tag ---"
  tail -n +"$((before + 1))" "$ELOG" \
    | grep -aE "windows, avg|GPU stages, stage|strata verify trace" \
    | cut -c1-1200 | tee -a "$OUT"
  say "lines: $(tail -n +"$((before + 1))" "$ELOG" | grep -ac 'GPU stages, stage') stage profile(s), $(tail -n +"$((before + 1))" "$ELOG" | grep -ac 'windows, avg') window accounting(s)"
}

say "=== L035: the decode phase map (STRATA_VERIFY_PROFILE=1; no new code) ==="
cp -p "$CFG" "$TMP/decode-prof-base-$TS.json"
say "config snapshotted -> $TMP/decode-prof-base-$TS.json"
say "reference (L-034): single 73.3 tok/s, four-way 111.2 tok/s on this engine without the profiler"
arm "A-prof"

python3 - "$CFG" "$TMP/decode-prof-on-$TS.json" <<'PY' || { say "FAILED to build the profiler variant"; exit 1; }
import json, sys
cfg = json.load(open(sys.argv[1]))
was = (cfg.get("env") or {}).get("STRATA_VERIFY_PROFILE")
cfg.setdefault("env", {})["STRATA_VERIFY_PROFILE"] = "1"
json.dump(cfg, open(sys.argv[2], "w"), indent=1, ensure_ascii=False)
print("variant B: STRATA_VERIFY_PROFILE was %r -> 1 (env now %d keys)" % (was, len(cfg["env"])))
PY
say "--- restart with STRATA_VERIFY_PROFILE=1 ---"
"$BIN/use-config.sh" "$TMP/decode-prof-on-$TS.json" >>"$OUT" 2>&1 \
  || { say "FAILED to install the profiler variant - restoring"; "$BIN/use-config.sh" "$TMP/decode-prof-base-$TS.json" >>"$OUT" 2>&1; exit 1; }
before=$(wc -l < "$ELOG")
arm "B-prof"
profile "B-prof" "$before"

say "--- restore the deployed config ---"
"$BIN/use-config.sh" "$TMP/decode-prof-base-$TS.json" >>"$OUT" 2>&1 || { say "FAILED to restore"; exit 1; }
cmp -s "$CFG" "$TMP/decode-prof-base-$TS.json" && say "config is byte-identical to the snapshot" || say "WARNING: config differs from the snapshot"
say "=== done ==="
