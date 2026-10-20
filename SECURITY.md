# Rules for this repository

1. Triggers: `workflow_dispatch` in both workflows. `schedule` only in
   `.github/workflows/poll.yml`, with exactly one cron entry, and that workflow only starts
   runs of the other one. Never add `pull_request`, `pull_request_target`, `push`,
   `issue_comment` or `workflow_run`, and never `schedule` anywhere else.
2. No application or test source, no names, no data, no secrets are ever committed.
3. Nothing from a fetched checkout may be printed, uploaded as an artifact, or cached.
4. Only `scripts/sanitize.py` writes run-specific text to the log of a run. `scripts/report.sh`
   prints fixed words and HTTP status codes only (never a sha, pull request number, run id,
   URL or name). `scripts/poll.sh` prints two integers (heads seen, runs started) and HTTP
   status codes.
5. Every change to this repository is checked with `scripts/pre-publish-check.sh`.
6. Secrets and the code under test (what is and is not protected).
   What the workflow does:
   - A secret is listed in `env:` only on the steps that need it (validate, mark pending,
     fetch, report, and the run step for suite codes `sNN`), never at workflow or job level.
   - The step that runs check codes (`cNN`) references no secret at all, and
     `scripts/run-private.sh` unsets the token, the repository name, the runner's
     file-command files (`GITHUB_ENV`, `GITHUB_PATH`, `GITHUB_OUTPUT`, `GITHUB_STATE`,
     `GITHUB_STEP_SUMMARY`), `RUNNER_TEMP`, `RUNNER_WORKSPACE`, `GITHUB_WORKSPACE`, and the
     `ACTIONS_*` runtime variables before the code under test starts.
   - The public scripts are copied to a root-owned, read-only directory (`/opt/trs`) before
     the fetch, the writable copy is removed, and every later step runs the copy.
   - For check codes, `run-private.sh` overwrites `scripts/public-runner.sh` and
     `scripts/public-checks.sh` of the checkout with the `origin/main` versions before
     running them (the commit under test cannot supply the script that judges it). If
     `origin/main` lacks either file the result is NOT A RESULT (setup), never a pass. Other
     files the entry script reads from the checkout are still the commit's own.
   - The report step runs only if the validate and lock steps succeeded. `report.sh` checks
     sha, code and pull request number again, and `sanitize.py` refuses status names that
     start with `ci/` or `live/` (the poller's marker and the suite status).
   What is NOT protected: the fetch needs `PRIVATE_TOKEN`, so the job that runs the code
   under test has held it in an earlier step, and everything runs as the same user on one
   machine with passwordless `sudo`. Step-level scoping keeps the token out of the code's
   environment, but code that runs in the job and is willing to use `sudo` can read the
   runner process's memory, replace tools such as `curl`, `jq` and `python3` that the report
   step later uses, or edit the locked copy as root. A split into a job that fetches and a
   job that runs would need a way to hand the code across, and artifacts and caches are
   forbidden (rule 3); job outputs are limited in size and are printed in the next job's
   step header. So there is no clean split here, and the real protection is organisational:
   only collaborators with write access to the private repository can get code run (the
   poller runs head commits of open pull requests and merge-queue branches), the
   `private-code` environment is restricted to `main`, and the token has the smallest
   scope (rule 8). Treat the token as exposed to anyone who can push a branch there. A
   compromised run can forge any status the token may write.
7. What is visible in the public repository. Stated plainly, so nobody assumes more:
   - Run inputs are shown on each run's page to anyone who can see the repository: the commit
     sha, the suite code, the pull request number.
     The run title shows the code. The run list shows when runs happen and how long they take.
   - The step names, the order of the steps, which steps ran, were skipped or failed, and the
     fixed words and numbers the scripts print (rule 4) are visible. So is the fact that a
     secret named `PRIVATE_TOKEN` exists (its value prints as `***`).
   - A sha gives no content, and the cadence of runs is visible from timing anyway. This is
     accepted.
   - Not shown: the private repository's name, branch names, titles, file or test names, and
     anything else. Nothing of these is in an input, a run title, a
     concurrency group, a step name or a printed line; where one has to pass through a step it
     is held in a masked value. Masking is done by the runner on the text of the log:
     it is not a guarantee against code that is run in the job (rule 6), and it does not cover
     what the code under test sends elsewhere.
   - Logs and run pages written before this rule was in force may still show what it now
     hides. They are removed by deleting the runs (Actions API: delete a workflow run), not by
     deleting log files only, since the inputs stay on the run page.
8. Operation requirements (neither is checked by code):
   - The `private-code` environment must have no required reviewers and no wait timer.
     Otherwise every poller run and every dispatched run waits for an approval that nobody
     sees, and merges stop.
   - GitHub disables scheduled workflows in a public repository after 60 days without
     repository activity, which silently stops the poller. Keep it alive: a commit, or
     re-enable it in the Actions tab, at least every 50 days (a calendar reminder), and
     check that a poll run exists whenever merges are expected. Manual dispatch of
     `live-suite.yml` remains the fallback.
   - `PRIVATE_TOKEN`: fine-grained, private repository only; Contents read, Commit
     statuses write, Pull requests/Issues write (comments). Nothing else. It expires; a
     reminder is needed before then.
9. (Removed.) The trial-only branch pin was taken out once the check scripts reached the
   private main branch; check codes always take their entry scripts from `origin/main`.
