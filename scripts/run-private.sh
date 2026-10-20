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
#  - TRIAL-ONLY switch (remove once the private main branch carries both scripts, SECURITY.md
#    rule 9): if PIN_REF is set, a check code takes those two files from the remote-tracking
#    branch origin/$PIN_REF instead of origin/main. PIN_REF must be PIN_PREFIX (a fixed word and
#    a slash, from a repository variable) plus a plain branch name; anything else, or a branch
#    that is missing or lacks a file, is a setup failure (NOT A RESULT), never a fall back to
#    main. The name is never printed. Suite codes never read PIN_REF.
# Suite codes (sNN) pass RELEASE_TOKEN for image loading and run the commit's entry script.
set -uo pipefail
work="$1"; code="$2"
: "${OUT:?}"
mkdir -p "$OUT"; rm -f "$OUT/result.json"
pinned="scripts/public-runner.sh scripts/public-checks.sh"
PIN_REF="${PIN_REF:-}"; PIN_PREFIX="${PIN_PREFIX:-}"

pin_ref_ok() {  # 0 only for PIN_PREFIX + a plain branch name (same rule as the workflow's validate step)
  local rest
  [[ $PIN_PREFIX =~ ^[A-Za-z0-9_][A-Za-z0-9_-]{0,29}/$ ]] || return 1
  [[ $PIN_REF == "$PIN_PREFIX"?* ]] || return 1
  rest=${PIN_REF#"$PIN_PREFIX"}
  [[ $rest =~ ^[A-Za-z0-9._/-]{1,100}$ ]] || return 1
  [[ $rest != /* && $rest != */ && $rest != *..* && $rest != *//* && $rest != *'@{'* ]] || return 1
}

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
    if [ -z "$PIN_REF" ]; then
      ( pin_entry origin/main ); ok=$?
    elif pin_ref_ok; then
      where="the pinned branch"
      ( pin_entry "refs/remotes/origin/$PIN_REF" ); ok=$?
    else
      where="a valid pinned branch"
      ok=1
    fi
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
        -u ACTIONS_CACHE_URL -u ACTIONS_RESULTS_URL -u PIN_REF -u PIN_PREFIX \
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
