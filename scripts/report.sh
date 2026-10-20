#!/usr/bin/env bash
# Write the result back to the PRIVATE repository: a commit status on the exact
# SHA, and (optional) one comment with the log tail on the pull request. The
# private repository is the only place the detail goes. HTTP failures print the
# status code only, never the response body.
#
# Status names: a suite code (sNN) reports under live/sNN. A check code (cNN) reports
# under the context name found in status.json (already validated by sanitize.py and
# checked again here) or live/cNN when there is none, and always also under the generic
# marker ci/cNN (pending at the start, the final state at the end), which the poller reads.
# The context name is never printed.
#
# Public log: fixed words and HTTP status codes only (no sha, no pull request number, no
# run id, no URL, no name). The inputs are checked again here (the workflow checks them too).
# The generic marker is the poller's "finished" signal, so it is posted only after the
# real status was recorded (2xx); if either post fails the step exits non-zero.
set -uo pipefail
mode="${1:-}"
: "${PRIVATE_REPO:?}" "${PRIVATE_TOKEN:?}" "${SHA:?}" "${SUITE_CODE:?}" "${OUT:?}"
bad() { echo "::error::bad input"; exit 1; }
case "$mode" in pending|final) ;; *) bad;; esac
[[ $SHA =~ ^[0-9a-f]{40}$ ]] || bad
[[ $SUITE_CODE =~ ^[sc][0-9]{2}$ ]] || bad
[[ -z ${PR:-} || ${PR:-} =~ ^[0-9]{1,6}$ ]] || bad
api="https://api.github.com/repos/${PRIVATE_REPO}"
url="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
post() {  # $1 path, $2 json file, $3 fixed label; returns 0 on a 2xx answer
  local code
  code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $PRIVATE_TOKEN" \
    -H "Accept: application/vnd.github+json" --data-binary "@$2" "$api/$1" 2>/dev/null) || code=curl-error
  case "$code" in 2*) echo "reported: $3"; return 0;; *) echo "::warning::report failed, http $code"; return 1;; esac
}
mkdir -p "$OUT"
if [ "$mode" = pending ]; then
  case "$SUITE_CODE" in c*) pctx="ci/$SUITE_CODE";; *) pctx="live/$SUITE_CODE";; esac
  jq -n --arg u "$url" --arg c "$pctx" \
    '{state:"pending",description:"running",context:$c,target_url:$u}' > "$OUT/p.json"
  post "statuses/$SHA" "$OUT/p.json" status || true   # advisory: the poller posts its own marker
  exit 0
fi
state=$(jq -r .state "$OUT/status.json" 2>/dev/null || echo error)
case "$state" in success|failure|error) ;; *) state=error;; esac
desc=$(jq -r .description "$OUT/status.json" 2>/dev/null || echo "NOT A RESULT")
[ -n "$desc" ] || desc="NOT A RESULT"
ctx=$(jq -r 'if (.context | type) == "string" and (.context | test("\\A[A-Za-z0-9 ()/,.:+_*-]{1,100}\\z"))
                and ((.context | ascii_downcase | startswith("ci/") or startswith("live/")) | not)
                and (.context == (.context | gsub("^ +| +$"; "")))
             then .context else empty end' "$OUT/status.json" 2>/dev/null || true)
[ -n "$ctx" ] || ctx="live/$SUITE_CODE"
rc=0
jq -n --arg s "$state" --arg d "$desc" --arg u "$url" --arg c "$ctx" \
  '{state:$s,description:$d,context:$c,target_url:$u}' > "$OUT/s.json"
if post "statuses/$SHA" "$OUT/s.json" status; then
  case "$SUITE_CODE" in c*)
    jq -n --arg s "$state" --arg d "$desc" --arg u "$url" --arg c "ci/$SUITE_CODE" \
      '{state:$s,description:$d,context:$c,target_url:$u}' > "$OUT/m.json"
    post "statuses/$SHA" "$OUT/m.json" marker || rc=1;;
  esac
else
  rc=1   # no marker: the poller must not treat an unrecorded result as finished
fi
if [ -n "${PR:-}" ] && [ -f "$OUT/run.log" ]; then
  { printf 'Suite %s on `%s`: **%s** ([run](%s))\n\nLast 60 KB of the log:\n\n```\n' "$SUITE_CODE" "$SHA" "$desc" "$url"
    tail -c 60000 "$OUT/run.log" | tr -d '\000' | sed 's/```/` ` `/g'
    printf '\n```\n'; } > "$OUT/comment.md"
  jq -Rs '{body: .}' "$OUT/comment.md" > "$OUT/c.json"
  post "issues/$PR/comments" "$OUT/c.json" comment || true
fi
exit "$rc"
