#!/bin/bash
# usage: exp-mode-ab.sh [--go]
# A-B-A for STRATA_EXP_MODE on this box: production default (Turing takes mode 4) against mode 8.
#
# Why this experiment.  Upstream gives Turing mode 4 by default (src/kernels/cuda/iq_kernels.cu:4999
# -5004, and sm75_down_em() drops the down projection back to mode 0 in 4+-token windows), and peers
# on this same card class and pack family report forcing mode 8 at +14% decode, text-neutral
# (upstream #2197, yusheng227507 on 2x2080 Ti IQ3_XXS; Adamyno on IQ3_S +4.9% over mode 4).  Our
# production env sets STRATA_SM75_PERF=1 and no STRATA_EXP_MODE, so we are on the mode-4 default.
# Their numbers are not ours: this script exists to find out whether they hold here.
#
# Cost, stated before running it: two service restarts, about 165 s each, so roughly 330 s of this
# box being unavailable - and this session's own tokens come from it.  Without --go nothing is
# touched; the script only writes the two candidate configs and prints what it would do.
#
# The last step installs arm A again, so the box ends where it started even on a mid-run failure
# (use-config.sh keeps a timestamped backup of whatever was live before each swap).
set -u
# Arm A is built from the LIVE config, not from strata-022.prod.json.  That snapshot was taken before
# the --vram-reserve-mib change (it still says 960 where production now says 1280), so building arm A
# from it would have rolled L-001 back without saying so, and the comparison would have been
# mode-4-at-960 against mode-8-at-1280 instead of the one change this script is about.
SRC=/opt/ai/src/strata-0.2.2/strata-022.json
[ -f "$SRC" ] || { echo "no live config at $SRC"; exit 1; }
A=/opt/ai/src/strata-0.2.2/exp-mode-A-default4.json
B=/opt/ai/src/strata-0.2.2/exp-mode-B-mode8.json

python3 - "$SRC" "$A" "$B" <<'PY'
import json, sys
src, a, b = sys.argv[1:4]
c = json.load(open(src))
base_args = c["args"]
assert isinstance(base_args, list) and all(isinstance(x, str) for x in base_args), \
    "args is not a list of strings in %s" % src
env = dict(c.get("env") or {})
json.dump(c, open(a, "w"), indent=2, ensure_ascii=False)                    # arm A: today's config
c2 = dict(c); c2["env"] = dict(env, STRATA_EXP_MODE="8")
json.dump(c2, open(b, "w"), indent=2, ensure_ascii=False)                   # arm B: same + mode 8
for path in (a, b):
    d = json.load(open(path))
    assert d["args"] == base_args, "args changed while writing %s" % path
print("source      : %s  (%d args)" % (src, len(base_args)))
print("arm A env   : STRATA_EXP_MODE=%s" % json.load(open(a))["env"].get("STRATA_EXP_MODE", "<unset -> mode-4 default>"))
print("arm B env   : STRATA_EXP_MODE=%s" % json.load(open(b))["env"].get("STRATA_EXP_MODE"))
print("everything else in the two configs is identical, including args and sampling")
PY
[ $? -eq 0 ] || { echo "config build failed"; exit 1; }

# Is the service already running exactly arm A?  If so the first restart is wasted and we skip it.
# Both halves matter: the live config matching arm A says what would start, and the running engine's
# environment says what did start - a config edited on disk but never restarted matches only the first.
EPID=$(pgrep -f 'build/strata --serve' | head -1)
same=""
if [ -n "${EPID:-}" ]; then
  same=$(python3 - "$A" <<'PY'
import json, sys
live = json.load(open("/opt/ai/src/strata-0.2.2/strata-022.json"))
print("yes" if live == json.load(open(sys.argv[1])) else "no")
PY
)
  if [ "$same" = "yes" ] && tr '\0' '\n' < "/proc/$EPID/environ" | grep -q '^STRATA_EXP_MODE='; then
    same="no (the config matches arm A but the running engine has STRATA_EXP_MODE set: the live config was never started)"
  fi
fi

if [ "${1:-}" != "--go" ]; then
  echo
  echo "DRY RUN - nothing restarted.  To run it: exp-mode-ab.sh --go"
  echo "Live service is already on the arm A config: ${same:-no (no engine process found)}"
  if [ "$same" = "yes" ]; then
    echo "Plan: measure A, install B (restart), measure B twice, install A (restart), measure A."
    echo "That is 2 restarts, about 330 s of downtime plus measurement time."
  else
    echo "Plan: install A (restart), measure, install B (restart), measure, install A (restart), measure."
    echo "That is 3 restarts, about 495 s of downtime plus measurement time."
  fi
  echo "Measured readiness on this box is ~165 s per restart, and this session's own tokens come from"
  echo "that service, so expect one of my requests to fail while it is loading."
  exit 0
fi

t0=$(date +%s)
run() {  # one arm: full harness plus two extra bitwise runs so each arm has three
  local label=$1
  /opt/ai/bin/run-arm.sh "$label" 200
  for i in 2 3; do python3 /opt/ai/bin/parity_window.py "$label-rep$i" 200 512; done
}

echo "### arm A (production default, mode 4)  $(date -u +%FT%TZ)"
if [ "$same" = "yes" ]; then
  echo "already running the arm A config (engine pid $EPID): measuring without restarting"
else
  echo "$same"
  /opt/ai/bin/use-config.sh "$A" || { echo "install A failed - aborting before any measurement"; exit 1; }
fi
run expA-mode4

echo "### arm B (STRATA_EXP_MODE=8)  $(date -u +%FT%TZ)"
if ! /opt/ai/bin/use-config.sh "$B"; then
  echo "install B failed - restoring A"
  /opt/ai/bin/use-config.sh "$A"
  exit 1
fi
run expB-mode8

echo "### arm A again (A-B-A bracket)  $(date -u +%FT%TZ)"
/opt/ai/bin/use-config.sh "$A" || echo "WARNING: final restore failed - restore by hand from /opt/ai/backups"
run expA2-mode4

echo "### total elapsed including restarts: $(( $(date +%s) - t0 ))s"
echo "logs: /opt/ai/logs/arm-exp{A-mode4,B-mode8,A2-mode4}.log"
echo "compare: bench medians and the parity text_sha per arm; a difference under 3% is not a result,"
echo "and in the production shape the answer hash is not reproducible arm to arm anyway - count runs."
