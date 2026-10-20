#!/usr/bin/env bash
# Fetch one commit of the private repository into $1. Every git message goes to a
# file that is never printed: git echoes remote URLs, which would name the repository.
# Suite codes (sNN) fetch the one commit (depth 1). Check codes (cNN) fetch all branches
# with full history, then check out the commit, so history scans can read every commit.
set -euo pipefail
dest="$1"
: "${PRIVATE_REPO:?}" "${PRIVATE_TOKEN:?}" "${SHA:?}" "${OUT:?}" "${SUITE_CODE:?}"
mkdir -p "$OUT"
log="$OUT/fetch.log"
rm -rf "$dest"; mkdir -p "$dest"; cd "$dest"
hdr="Authorization: basic $(printf 'x-access-token:%s' "$PRIVATE_TOKEN" | base64 -w0)"
{
  git init -q
  git remote add origin "https://github.com/${PRIVATE_REPO}.git"
  case "$SUITE_CODE" in
    c*)
      git -c "http.extraheader=$hdr" fetch -q origin '+refs/heads/*:refs/remotes/origin/*'
      git checkout -q --detach "$SHA"
      ;;
    *)
      git -c "http.extraheader=$hdr" fetch -q --depth 1 origin "$SHA"
      git checkout -q FETCH_HEAD
      ;;
  esac
} >"$log" 2>&1 || { echo "::error::could not fetch the commit (details withheld)"; exit 1; }
echo "commit fetched"
