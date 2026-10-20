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
 'README\.md|SECURITY\.md|\.gitleaks\.toml|\.github/workflows/(live-suite|poll)\.yml|scripts/(fetch-private|run-private|report|poll)\.sh|scripts/(sanitize|poll_decide)\.py|scripts/pre-publish-check\.sh|tests/(test_sanitize|test_poll_decide|test_poll|test_report|test_fetch_run|test_workflows|stubcurl)\.py' || true)
[ -z "$extra" ] && echo ok || { echo "FAIL: unexpected files:"; echo "$extra"; fail=1; }
echo "3. triggers: workflow_dispatch; schedule only in poll.yml (at most one cron entry)"
bad=$(grep -nE '^\s*(pull_request|pull_request_target|push|issue_comment|workflow_run|workflow_call|repository_dispatch|fork|issues)\s*:' .github/workflows/*.yml || true)
bad="$bad$(grep -nE '^\s*schedule\s*:' .github/workflows/*.yml | grep -v '^\.github/workflows/poll\.yml:' || true)"
n=$(grep -cE '^\s*-?\s*cron\s*:' .github/workflows/poll.yml); [ "$n" -le 1 ] || bad="$bad poll.yml has $n cron entries (need at most 1)"
for f in .github/workflows/*.yml; do grep -qE '^\s*workflow_dispatch\s*:' "$f" || bad="$bad $f lacks workflow_dispatch"; done
[ -z "$bad" ] && echo ok || { echo "FAIL: $bad"; fail=1; }
echo "4. no artifact upload, no cache, no step that prints a private path"
bad=$(grep -nE 'upload-artifact|actions/cache|set -x|bash -x|ACTIONS_STEP_DEBUG' -r --exclude=pre-publish-check.sh .github scripts || true)
[ -z "$bad" ] && echo ok || { echo "FAIL: $bad"; fail=1; }
echo "5. workflows parse, no secret at workflow or job level, locked checks; tests"
if python3 -I -c "import yaml" 2>/dev/null; then
  python3 -I - <<'PY' && echo ok || { echo FAIL; fail=1; }
import glob, sys, yaml
bad = 0
for p in sorted(glob.glob(".github/workflows/*.yml")):
    with open(p) as f:
        d = yaml.safe_load(f)
    if "secrets." in str(d.get("env", "")):
        print("secret in workflow-level env:", p)
        bad = 1
    for name, job in d["jobs"].items():
        if "secrets." in str(job.get("env", "")):
            print("secret in job-level env:", p, name)
            bad = 1
sys.exit(bad)
PY
else echo "FAIL: python3 yaml module missing (install PyYAML); the workflows cannot be checked"; fail=1; fi
bad=""
grep -qF "startsWith(inputs.suite_code, 'c')" .github/workflows/live-suite.yml || bad="$bad live-suite.yml lacks the check-code step condition;"
grep -qF "steps.validate.outcome == 'success'" .github/workflows/live-suite.yml || bad="$bad the report step is not gated on validate;"
grep -qF "/opt/trs/scripts/" .github/workflows/live-suite.yml || bad="$bad live-suite.yml does not use the locked script copy;"
for v in PRIVATE_TOKEN PRIVATE_REPO RELEASE_TOKEN GITHUB_TOKEN RUNNER_TEMP RUNNER_WORKSPACE GITHUB_WORKSPACE GITHUB_ENV GITHUB_PATH GITHUB_OUTPUT; do
  grep -qE -- "-u $v([[:space:]]|\\\\|$)" scripts/run-private.sh || bad="$bad run-private.sh does not unset $v;"
done
grep -qF "origin/main" scripts/run-private.sh || bad="$bad run-private.sh does not pin the entry scripts;"
[ -z "$bad" ] && echo ok || { echo "FAIL: $bad"; fail=1; }
for t in tests/test_*.py; do python3 -B -I "$t" >/dev/null 2>&1 && echo "ok $t" || { echo "FAIL $t"; fail=1; }; done
echo "6. gitleaks"
if command -v gitleaks >/dev/null; then gitleaks detect --no-git --source . --config .gitleaks.toml -v && echo ok || fail=1; else echo "FAIL: gitleaks not installed"; fail=1; fi
[ "$fail" = 0 ] && echo "ALL CLEAR" || { echo "NOT SAFE TO PUBLISH"; exit 1; }
