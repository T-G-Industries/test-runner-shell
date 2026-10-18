#!/usr/bin/env bash
# Run the private repository's own entry script. ALL of its output goes to
# $OUT/run.log (never to the public log). It writes $OUT/result.json itself.
set -uo pipefail
work="$1"; code="$2"
: "${OUT:?}" "${PRIVATE_TOKEN:?}"
mkdir -p "$OUT"; rm -f "$OUT/result.json"
entry="$work/scripts/public-runner.sh"
if [ ! -f "$entry" ]; then echo 127 > "$OUT/exit_code"; echo "entry script not in the commit"; exit 1; fi
start=$(date +%s)
( cd "$work" && RELEASE_TOKEN="$PRIVATE_TOKEN" RESULT_JSON="$OUT/result.json" \
  bash scripts/public-runner.sh "$code" ) >"$OUT/run.log" 2>&1
rc=$?
echo "$rc" > "$OUT/exit_code"
echo "$(( $(date +%s) - start )) seconds, exit $rc"
exit 0   # the verdict is reported by the next step, not by this step's colour
