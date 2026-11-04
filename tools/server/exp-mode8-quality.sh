#!/bin/bash
# usage: exp-mode8-quality.sh [--go]
# Does mode 8 hold up on the metric this project puts first: an agent session that has to compute things?
#
# exp-mode-ab.sh answered "is it faster" - on this box, yes: single-stream decode 67.2 / 67.7 (mode 4, the
# bracket) against 73.5 (mode 8), +8%, with the answer hash identical in all 18 bitwise runs.  What that
# does not answer is whether the faster kernel is also the same *thinker*.  Mode 8 changes which CUDA
# kernel multiplies the expert weights, so the risk is numeric drift that shows up as a wrong arithmetic
# answer or an empty one, not as a different hash on a 25-character lookup answer.
#
# So this installs mode 8, runs the reasoning-shaped harness (14 tasks, 8 of which no tool can answer -
# multi-step arithmetic, a divisibility count, a remainder, a popcount, an equation, a speed conversion,
# an elimination puzzle) three times, and runs the bitwise gate three times; then it puts the production
# config back and runs both again, so the comparison is A-B-A inside one boot.
#
# Cost: two restarts, about 300 s of this box being unavailable.  Without --go nothing is touched.
# Read the seconds from the harness with care: this session's own tokens come from the same service, so
# while I am awake and writing, the wall clock is contaminated.  Tokens and pass counts are not.
set -u
SRC=/opt/ai/src/strata-0.2.2/strata-022.json
A=/opt/ai/src/strata-0.2.2/exp-mode-A-default4.json
B=/opt/ai/src/strata-0.2.2/exp-mode-B-mode8.json
[ -f "$B" ] || { echo "no $B - run exp-mode-ab.sh first"; exit 1; }

if [ "${1:-}" != "--go" ]; then
  echo "DRY RUN - nothing touched.  To run it: exp-mode8-quality.sh --go"
  echo "Plan: install B (restart ~165s), 3x bitwise + 3x reasoning harness, install A (restart ~165s),"
  echo "      3x bitwise + 3x reasoning harness.  The box ends on the production config either way."
  exit 0
fi

measure() {  # one arm: bitwise gate three times, then the reasoning harness three interleaved reps
  local label=$1
  echo "--- bitwise gate ($label)  $(date -u +%FT%TZ)"
  for i in 1 2 3; do python3 /opt/ai/bin/parity_window.py "q-$label-$i" 200 512; done
  echo "--- reasoning harness ($label)  $(date -u +%FT%TZ)"
  AB_TAG=-q$label python3 -u /opt/ai/bin/agent_budget_ab.py reason 3 "0@high"
}

echo "### mode 8  $(date -u +%FT%TZ)"
/opt/ai/bin/use-config.sh "$B" || { echo "install B failed - nothing measured"; exit 1; }
measure mode8

echo "### mode 4 (production, bracket)  $(date -u +%FT%TZ)"
/opt/ai/bin/use-config.sh "$A" || echo "WARNING: restore failed - restore by hand from /opt/ai/backups"
measure mode4

echo "### done $(date -u +%FT%TZ)"
echo "harness data: /opt/ai/runs/agent-budget-ab-reason-q{mode8,mode4}.json (logs beside them)"
echo "compare: ok/N and completion tokens per arm; the seconds column is contaminated by this session."
