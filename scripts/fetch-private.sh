#!/usr/bin/env bash
# Fetch one commit of the private repository into $1. Every git message goes to a
# file that is never printed: git echoes remote URLs, which would name the repository.
set -euo pipefail
dest="$1"
: "${PRIVATE_REPO:?}" "${PRIVATE_TOKEN:?}" "${SHA:?}" "${OUT:?}"
mkdir -p "$OUT"
log="$OUT/fetch.log"
rm -rf "$dest"; mkdir -p "$dest"; cd "$dest"
hdr="Authorization: basic $(printf 'x-access-token:%s' "$PRIVATE_TOKEN" | base64 -w0)"
{
  git init -q
  git remote add origin "https://github.com/${PRIVATE_REPO}.git"
  git -c "http.extraheader=$hdr" fetch -q --depth 1 origin "$SHA"
  git checkout -q FETCH_HEAD
} >"$log" 2>&1 || { echo "::error::could not fetch the commit (details withheld)"; exit 1; }
echo "commit fetched"
