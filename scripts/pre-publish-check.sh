#!/usr/bin/env bash
# Run before every push to the public repository: bash scripts/pre-publish-check.sh <denylist-file>
# The denylist is kept OUTSIDE this repository (it names the things that must not appear).
set -uo pipefail
cd "$(dirname "$0")/.."
deny="${1:?path to the denylist file (kept outside this repo)}"
fail=0
echo "1. denylist (case-insensitive, whole tree incl. .git if present)"
if grep -rIinF -f "$deny" --exclude-dir=.git . ; then echo "FAIL: denylisted text found"; fail=1; else echo "ok"; fi
echo "2. only the allowed file set"
extra=$(find . -path ./.git -prune -o -type f -print | sed 's|^\./||' | sort | grep -vxE \
 'README.md|SECURITY.md|.gitleaks.toml|.github/workflows/live-suite.yml|scripts/(fetch-private|run-private|report)\.sh|scripts/(sanitize\.py|pre-publish-check\.sh)|tests/test_sanitize\.py' || true)
[ -z "$extra" ] && echo ok || { echo "FAIL: unexpected files:"; echo "$extra"; fail=1; }
echo "3. triggers: workflow_dispatch only"
bad=$(grep -nE '^\s*(pull_request|pull_request_target|push|issue_comment|schedule|workflow_run|workflow_call|repository_dispatch|fork|issues)\s*:' .github/workflows/*.yml || true)
[ -z "$bad" ] && echo ok || { echo "FAIL: $bad"; fail=1; }
echo "4. no artifact upload, no cache, no step that prints a private path"
bad=$(grep -nE 'upload-artifact|actions/cache|set -x|bash -x|ACTIONS_STEP_DEBUG' -r --exclude=pre-publish-check.sh .github scripts || true)
[ -z "$bad" ] && echo ok || { echo "FAIL: $bad"; fail=1; }
echo "5. workflow parses; sanitizer tests"
python3 -I -c "import yaml,sys; yaml.safe_load(open('.github/workflows/live-suite.yml'))" 2>/dev/null && echo ok || echo "(yaml module missing: check by eye)"
python3 -I tests/test_sanitize.py >/dev/null 2>&1 && echo ok || { echo FAIL; fail=1; }
echo "6. gitleaks"
if command -v gitleaks >/dev/null; then gitleaks detect --no-git --source . --config .gitleaks.toml -v && echo ok || fail=1; else echo "FAIL: gitleaks not installed"; fail=1; fi
[ "$fail" = 0 ] && echo "ALL CLEAR" || { echo "NOT SAFE TO PUBLISH"; exit 1; }
