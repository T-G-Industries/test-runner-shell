#!/usr/bin/env bash
# Start the check runs that are missing. Reads open pull requests (targeting main) and
# merge-queue branches of the PRIVATE repository, looks at the marker statuses already on
# each head commit, and starts live-suite.yml in THIS repository for the codes that need
# a run (the decision is scripts/poll_decide.py). The public log shows two integers only:
# the number of heads seen and the number of runs started. HTTP failures print only the
# status code. Nothing from any response is ever printed.
set -uo pipefail
: "${PRIVATE_REPO:?}" "${PRIVATE_TOKEN:?}" "${GITHUB_TOKEN:?}" "${GITHUB_REPOSITORY:?}"
here="$(cd "$(dirname "$0")" && pwd)"
api="https://api.github.com/repos/${PRIVATE_REPO}"
dispatch_url="https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/workflows/live-suite.yml/dispatches"
max_runs=12
max_pages=10
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

get() {  # $1 path (under the private repo), $2 output file; 0 on a 2xx answer
  local code
  code=$(curl -sS -o "$2" -w '%{http_code}' -H "Authorization: Bearer $PRIVATE_TOKEN" \
    -H "Accept: application/vnd.github+json" "$api/$1" 2>/dev/null) || code=curl-error
  case "$code" in 2*) return 0;; *) echo "::warning::http $code"; return 1;; esac
}

get_all() {  # $1 path with a query string, $2 output file (one JSON array); 0 if every page was read
  local n=1 len
  echo '[]' > "$2"
  while :; do
    get "$1&page=$n" "$tmp/page.json" || return 1
    jq -e 'type == "array"' "$tmp/page.json" >/dev/null 2>&1 || { echo "::warning::http not-a-list"; return 1; }
    jq -s '.[0] + .[1]' "$2" "$tmp/page.json" > "$tmp/acc.json" 2>/dev/null || return 1
    mv "$tmp/acc.json" "$2"
    len=$(jq 'length' "$tmp/page.json" 2>/dev/null) || return 1
    [ "$len" -ge 100 ] || return 0
    n=$((n + 1))
    if [ "$n" -gt "$max_pages" ]; then echo "::warning::http too-many-pages"; return 1; fi
  done
}

mark() {  # $1 sha, $2 code; write the pending marker; 0 on a 2xx answer
  local res
  jq -n --arg c "ci/$2" '{state:"pending",description:"queued",context:$c}' > "$tmp/m.json" || return 1
  res=$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $PRIVATE_TOKEN" \
    -H "Accept: application/vnd.github+json" --data-binary "@$tmp/m.json" "$api/statuses/$1" 2>/dev/null) || res=curl-error
  case "$res" in 2*) return 0;; *) echo "::warning::http $res"; return 1;; esac
}

ok_lists=0
: > "$tmp/heads"
if get_all "pulls?state=open&base=main&per_page=100" "$tmp/prs.json"; then
  ok_lists=$((ok_lists + 1))
  jq -r '.[] | select((.head.sha // "" | test("^[0-9a-f]{40}$")) and ((.number | type) == "number")
         and (.number >= 0) and (.number < 1000000) and (.number == (.number | floor)))
         | "\(.head.sha) \(.number | floor)"' "$tmp/prs.json" >> "$tmp/heads" 2>/dev/null
fi
if get_all "git/matching-refs/heads/gh-readonly-queue/main/?per_page=100" "$tmp/queue.json"; then
  ok_lists=$((ok_lists + 1))
  jq -r '.[] | .object.sha // "" | select(test("^[0-9a-f]{40}$")) | "\(.) -"' "$tmp/queue.json" >> "$tmp/heads" 2>/dev/null
fi
if [ "$ok_lists" = 0 ]; then exit 1; fi
awk '!seen[$1]++' "$tmp/heads" > "$tmp/uniq"   # first entry wins, so a pull request number is kept

heads=0; started=0
while read -r sha pr; do
  [[ $sha =~ ^[0-9a-f]{40}$ ]] || continue
  heads=$((heads + 1))
  left=$((max_runs - started))
  [ "$left" -gt 0 ] || continue
  get_all "commits/$sha/statuses?per_page=100" "$tmp/st.json" || continue
  codes=$(python3 -I "$here/poll_decide.py" "$tmp/st.json" "$(date +%s)" "$left" 2>/dev/null) || continue
  for code in $codes; do
    [[ $code =~ ^c0[1-6]$ ]] || continue
    if [[ $pr =~ ^[0-9]{1,6}$ ]]; then
      jq -n --arg s "$sha" --arg c "$code" --arg p "$pr" \
        '{ref:"main",inputs:{sha:$s,suite_code:$c,pr:$p}}' > "$tmp/d.json"
    else
      jq -n --arg s "$sha" --arg c "$code" \
        '{ref:"main",inputs:{sha:$s,suite_code:$c}}' > "$tmp/d.json"
    fi
    res=$(curl -sS -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $GITHUB_TOKEN" \
      -H "Accept: application/vnd.github+json" --data-binary "@$tmp/d.json" "$dispatch_url" 2>/dev/null) || res=curl-error
    case "$res" in
      2*) started=$((started + 1))
          mark "$sha" "$code" || break;;   # hard stop for this commit
      *) echo "::warning::http $res";;
    esac
  done
done < "$tmp/uniq"
echo "heads seen: $heads"
echo "runs dispatched: $started"
exit 0
