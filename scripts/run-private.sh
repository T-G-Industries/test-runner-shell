#!/usr/bin/env bash
# Run the private repository's own entry script. ALL of its output goes to
# $OUT/run.log (never to the public log). It writes $OUT/result.json itself.
#
# Check codes (cNN) run code from the commit under test, so:
#  - the entry script gets NO token and none of the runner's file-command or runtime
#    variables (no RELEASE_TOKEN, no PRIVATE_TOKEN, no PRIVATE_REPO, no GITHUB_ENV, ...);
#  - the entry script and its checks library (scripts/public-runner.sh and
#    scripts/public-checks.sh) are first overwritten with the versions on origin/main of the
#    fetched checkout, so the commit under test cannot supply the script that judges it.
#    If origin/main does not have both files (the private side is not on main yet), nothing
#    runs and the result is a setup failure (NOT A RESULT), never a pass. Other files the
#    entry script reads from the checkout are still the commit's own.
# Suite codes (sNN) pass RELEASE_TOKEN for image loading and run the commit's entry script.
set -uo pipefail
work="$1"; code="$2"
: "${OUT:?}"
mkdir -p "$OUT"; rm -f "$OUT/result.json"
pinned="scripts/public-runner.sh scripts/public-checks.sh"

pin_entry() {  # overwrite the pinned files with origin/main's; 0 only if they now match exactly
  local f
  cd "$work" 2>/dev/null || return 1
  for f in $pinned; do
    [ "$(git cat-file -t "origin/main:$f" 2>/dev/null)" = blob ] || return 1
  done
  git -c core.hooksPath=/dev/null checkout origin/main -- $pinned >/dev/null 2>&1 || return 1
  [ ! -L scripts ] || return 1
  for f in $pinned; do
    [ -f "$f" ] && [ ! -L "$f" ] || return 1
    git show "origin/main:$f" 2>/dev/null | cmp -s - "$f" || return 1
  done
}

case "$code" in
  c*)
    if ! ( pin_entry ); then
      echo '{"passed":0,"failed":0,"skipped":0,"errors":0,"counted":false,"reason":"setup"}' > "$OUT/result.json"
      echo 1 > "$OUT/exit_code"
      echo "entry scripts not available from the main branch (setup)" > "$OUT/run.log"
      echo "setup failure: entry scripts not available from the main branch"
      exit 0
    fi
    ;;
esac
entry="$work/scripts/public-runner.sh"
if [ ! -f "$entry" ]; then echo 127 > "$OUT/exit_code"; echo "entry script not in the commit"; exit 1; fi
start=$(date +%s)
case "$code" in
  c*)
    ( cd "$work" && env -u PRIVATE_TOKEN -u PRIVATE_REPO -u RELEASE_TOKEN -u GITHUB_TOKEN \
        -u GITHUB_ENV -u GITHUB_PATH -u GITHUB_OUTPUT -u GITHUB_STATE -u GITHUB_STEP_SUMMARY \
        -u RUNNER_TEMP -u RUNNER_WORKSPACE -u GITHUB_WORKSPACE \
        -u ACTIONS_RUNTIME_TOKEN -u ACTIONS_ID_TOKEN_REQUEST_TOKEN -u ACTIONS_ID_TOKEN_REQUEST_URL \
        -u ACTIONS_CACHE_URL -u ACTIONS_RESULTS_URL \
        RESULT_JSON="$OUT/result.json" bash scripts/public-runner.sh "$code" ) >"$OUT/run.log" 2>&1
    rc=$?
    ;;
  *)
    : "${PRIVATE_TOKEN:?}"
    ( cd "$work" && RELEASE_TOKEN="$PRIVATE_TOKEN" RESULT_JSON="$OUT/result.json" \
      bash scripts/public-runner.sh "$code" ) >"$OUT/run.log" 2>&1
    rc=$?
    ;;
esac
echo "$rc" > "$OUT/exit_code"
echo "$(( $(date +%s) - start )) seconds, exit $rc"
exit 0   # the verdict is reported by the next step, not by this step's colour
