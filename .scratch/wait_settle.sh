#!/bin/bash
# Wait until the working tree stops being written to by the other agent.
# Robust settle: requires TWO consecutive checks where nothing under
# gateway/ or tests/ (recursive) was modified in the last 3 min AND the
# git status file-count is unchanged. Poll every 45s. Cap at 90 min.
cd /Users/stephenbowman/.hermes/hermes-agent || exit 1
MAXWAIT=7200
elapsed=0
last_count=-1
last_head=""
clean_streak=0
while [ "$elapsed" -lt "$MAXWAIT" ]; do
  recent=$(find gateway tests -name '*.py' -mmin -5 2>/dev/null | wc -l | tr -d ' ')
  count=$(git status --short 2>/dev/null | wc -l | tr -d ' ')
  head=$(git rev-parse HEAD 2>/dev/null)
  if [ "$recent" -eq 0 ] && [ "$count" = "$last_count" ] && [ "$head" = "$last_head" ]; then
    clean_streak=$((clean_streak + 1))
  else
    clean_streak=0
  fi
  last_count="$count"
  last_head="$head"
  if [ "$clean_streak" -ge 2 ]; then
    echo "SETTLED after ${elapsed}s: quiet 5min x2 confirmations; status file-count=${count}"
    echo "=== HEAD ==="; git log --oneline -1
    echo "=== run.py lines ==="; wc -l gateway/run.py | awk '{print $1}'
    exit 0
  fi
  sleep 60
  elapsed=$((elapsed + 60))
done
echo "TIMEOUT after ${MAXWAIT}s; still active. status file-count=$(git status --short | wc -l | tr -d ' ')"
