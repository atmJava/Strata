#!/bin/sh
# L-002: thinking-budget arms on the self-scoring eval.
#
# Each arm sends its own reasoning_budget_tokens per request (quality_eval_budget.py reads it from
# QE_BUDGET), so the server's own settings and every other client stay untouched and no restart is
# needed - all arms run in one boot, which is what the same-boot rule asks for.
#
# The eval is greedy (no temperature / top_p / top_k / seed is sent) and every task has a
# programmatically checkable answer, so the comparison is "how many did it get right", not a
# judgement call. It also prints reasoning and answer characters and seconds per task, which is
# where the thinking cost and the wall clock come from.
#
# Usage: thinking-budget-ab.sh <budget> [<budget> ...]
#        budget 0 = send no budget (the config's reasoning_budget_tokens applies)
# Arms run in the order given, so "256 1024 0" is aggressive, middle, baseline-last.
#
# Wall clock here is contaminated by whatever else is decoding - on this box a second stream halves
# single-stream speed - so read the token counts as the primary effect and the seconds as a trend.

BIN=/opt/ai/bin/quality_eval_budget.py
RUNS=/opt/ai/runs
CFG=/opt/ai/src/strata-0.2.2/strata-022.json

key=$(python3 -c "import json;print(json.load(open('$CFG')).get('api_key',''))" 2>/dev/null)
if [ -z "$key" ]; then echo "no api_key read from $CFG"; exit 1; fi

for b in "$@"; do
    if [ "$b" = "0" ]; then label=base; unset QE_BUDGET; else label=b$b; QE_BUDGET=$b; export QE_BUDGET; fi
    out=$RUNS/L002-$label
    echo "=== arm $label budget=${QE_BUDGET:-none} -> $out ==="
    QE_KEY=$key python3 "$BIN" "$out" > "$out.log" 2>&1
    echo "    $(grep -E '^SCORED:' "$out.log" | tail -1)   $(grep -c FAILED "$out.log") failed tasks"
    unset QE_BUDGET
done
