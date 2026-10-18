#!/usr/bin/env bash
# Write the result back to the PRIVATE repository: a commit status on the exact
# SHA, and (optional) one comment with the log tail on the pull request. The
# private repository is the only place the detail goes. HTTP failures print the
# status code only, never the response body.
set -uo pipefail
mode="$1"
: "${PRIVATE_REPO:?}" "${PRIVATE_TOKEN:?}" "${SHA:?}" "${SUITE_CODE:?}" "${OUT:?}"
api="https://api.github.com/repos/${PRIVATE_REPO}"
url="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
post() {  # $1 path, $2 json file
  code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $PRIVATE_TOKEN" \
    -H "Accept: application/vnd.github+json" --data-binary "@$2" "$api/$1" 2>/dev/null) || code=curl-error
  case "$code" in 2*) echo "reported ($1 ok)";; *) echo "::warning::report failed, http $code";; esac
}
mkdir -p "$OUT"
if [ "$mode" = pending ]; then
  jq -n --arg u "$url" --arg c "live/$SUITE_CODE" \
    '{state:"pending",description:"running",context:$c,target_url:$u}' > "$OUT/p.json"
  post "statuses/$SHA" "$OUT/p.json"; exit 0
fi
state=$(jq -r .state "$OUT/status.json" 2>/dev/null || echo error)
desc=$(jq -r .description "$OUT/status.json" 2>/dev/null || echo "NOT A RESULT")
jq -n --arg s "$state" --arg d "$desc" --arg u "$url" --arg c "live/$SUITE_CODE" \
  '{state:$s,description:$d,context:$c,target_url:$u}' > "$OUT/s.json"
post "statuses/$SHA" "$OUT/s.json"
if [ -n "${PR:-}" ] && [ -f "$OUT/run.log" ]; then
  { printf 'Suite %s on `%s`: **%s** ([run](%s))\n\nLast 60 KB of the log:\n\n```\n' "$SUITE_CODE" "$SHA" "$desc" "$url"
    tail -c 60000 "$OUT/run.log" | tr -d '\000' | sed 's/```/` ` `/g'
    printf '\n```\n'; } > "$OUT/comment.md"
  jq -Rs '{body: .}' "$OUT/comment.md" > "$OUT/c.json"
  post "issues/$PR/comments" "$OUT/c.json"
fi
exit 0
