#!/usr/bin/env bash
# Run the private repository's own entry script. ALL of its output goes to
# $OUT/run.log (never to the public log). It writes $OUT/result.json itself.
#
# Check codes (cNN) run code from the commit under test, so:
#  - the entry script gets NO token and none of the runner's file-command or runtime
#    variables (no RELEASE_TOKEN, no PRIVATE_TOKEN, no PRIVATE_REPO, no GITHUB_ENV, ...);
#  - the runner's RUNNER_TEMP is unset like the rest (its _temp holds the file-command files), and
#    then RUNNER_TEMP is set again to a fresh private directory (mktemp -d under /tmp, mode 700,
#    empty, removed when this script exits), because the entry script and its checks library write
#    their step logs, plant and SBOM files there and refuse to start without it;
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

pin_entry() {  # overwrite the pinned files with $1's (default origin/main); 0 only if they now match exactly
  local f src="${1:-origin/main}"
  cd "$work" 2>/dev/null || return 1
  for f in $pinned; do
    [ "$(git cat-file -t "$src:$f" 2>/dev/null)" = blob ] || return 1
  done
  git -c core.hooksPath=/dev/null checkout "$src" -- $pinned >/dev/null 2>&1 || return 1
  [ ! -L scripts ] || return 1
  for f in $pinned; do
    [ -f "$f" ] && [ ! -L "$f" ] || return 1
    git show "$src:$f" 2>/dev/null | cmp -s - "$f" || return 1
  done
}

case "$code" in
  c*)
    where="the main branch"
    ( pin_entry origin/main ); ok=$?
    if [ "$ok" != 0 ]; then
      echo '{"passed":0,"failed":0,"skipped":0,"errors":0,"counted":false,"reason":"setup"}' > "$OUT/result.json"
      echo 1 > "$OUT/exit_code"
      echo "entry scripts not available from $where (setup)" > "$OUT/run.log"
      echo "setup failure: entry scripts not available from $where"
      exit 0
    fi
    ;;
esac
entry="$work/scripts/public-runner.sh"
if [ ! -f "$entry" ]; then echo 127 > "$OUT/exit_code"; echo "entry script not in the commit"; exit 1; fi
start=$(date +%s)
ptmp=""
trap '[ -z "$ptmp" ] || rm -rf -- "$ptmp"' EXIT
case "$code" in
  c*)
    ptmp="$(mktemp -d /tmp/trs-private.XXXXXX)" && chmod 700 "$ptmp" || {
      echo '{"passed":0,"failed":0,"skipped":0,"errors":0,"counted":false,"reason":"setup"}' > "$OUT/result.json"
      echo 1 > "$OUT/exit_code"; echo "no private temp directory (setup)" > "$OUT/run.log"
      echo "setup failure: no private temp directory"; exit 0
    }
    ( cd "$work" && env -u PRIVATE_TOKEN -u PRIVATE_REPO -u RELEASE_TOKEN -u GITHUB_TOKEN \
        -u GITHUB_ENV -u GITHUB_PATH -u GITHUB_OUTPUT -u GITHUB_STATE -u GITHUB_STEP_SUMMARY \
        -u RUNNER_TEMP -u RUNNER_WORKSPACE -u GITHUB_WORKSPACE \
        -u ACTIONS_RUNTIME_TOKEN -u ACTIONS_ID_TOKEN_REQUEST_TOKEN -u ACTIONS_ID_TOKEN_REQUEST_URL \
        -u ACTIONS_CACHE_URL -u ACTIONS_RESULTS_URL \
        RUNNER_TEMP="$ptmp" RESULT_JSON="$OUT/result.json" bash scripts/public-runner.sh "$code" ) >"$OUT/run.log" 2>&1
    rc=$?
    rm -rf -- "$ptmp"; ptmp=""
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
