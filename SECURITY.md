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
     sha, the suite code, the pull request number and, during the trial of rule 9, the pin alias.
     The run title shows the code. The run list shows when runs happen and how long they take.
   - The step names, the order of the steps, which steps ran, were skipped or failed, and the
     fixed words and numbers the scripts print (rule 4) are visible. So is the fact that a
     secret named `PIN_MAP` exists (its value prints as `***`).
   - A sha gives no content, and the cadence of runs is visible from timing anyway. This is
     accepted.
   - Not shown: the private repository's name, branch names, titles, file or test names, and
     the branch behind a pin alias (rule 9). Nothing of these is in an input, a run title, a
     concurrency group, a step name or a printed line; where one has to pass through a step it
     is held in a masked value (rule 9). Masking is done by the runner on the text of the log:
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
9. TRIAL-ONLY switch, to be removed once the private main branch carries
   `scripts/public-runner.sh` and `scripts/public-checks.sh` (delete the `pin` input, the `PIN`
   and `PIN_MAP` env and the pin block of the validate step, the `PIN_REF`/`PIN_ASKED` env of the
   check step, the pin code in `scripts/run-private.sh`, the secret `PIN_MAP`, and the tests
   marked trial-only; then close this rule).
   - What it does: `live-suite.yml` has an optional input `pin` (default empty, which changes
     nothing): a short alias, 1 to 20 characters from `a-z 0-9`. It must be meaningless on its
     own. Use something like `t1`, never a word that says what it is for, because the alias is
     printed on the run page. For a check code (`cNN`) only, a non-empty alias makes the
     validate step look the alias up in the repository secret `PIN_MAP` and makes
     `run-private.sh` take the two entry scripts from the remote-tracking branch
     `origin/<that branch>` of the fetched checkout instead of `origin/main`, with the same
     checks as for main (both files exist as regular files on that branch, none is a symlink,
     and the copies on disk are byte-identical to the branch's).
   - The map: `PIN_MAP` is a secret (set it in the `private-code` environment, with the other
     secrets), one line of JSON, alias to branch name, for example `{"t1":"<branch>"}`. It is
     the whole allow-list: only a branch written in it can be pinned, so there is no prefix to
     keep and no repository variable. Keep it on one line:
     the runner masks a multi-line secret line by line, which blanks common characters in
     the log. An unset or malformed map, an unknown alias and a value that is not a plain
     string all give the same fixed words ("pin not available") and no pin.
   - Why a secret and not a repository variable: the runner prints a step's `env:` block in
     its header before the step runs, so a variable put into the validate step's `env:` would
     appear in plain text in the log, and a mask added by the step's own script is too late
     for that header. A secret is masked from the start, in every header and every step. A
     secret in the validate step breaks no rule: that step already holds secrets (rule 6) and
     runs no code from the commit under test.
   - Limits on the resolved name, checked twice (the validate step, then `run-private.sh`):
     1 to 128 characters from `A-Z a-z 0-9 . _ / -`, starting with a letter, digit or `_`, no
     `..`, no `//`, no trailing `/`, no `@{`, and not `main`, `HEAD`, `refs/...` or
     `origin/...`. The validate step also rejects an alias for a suite code (`sNN`) and
     `run-private.sh` never reads the pin for one.
   - Failure is never a fall back: an invalid alias or name, a missing branch, a branch that
     lacks a file, or a pin that was asked for but whose name did not arrive at the check step
     (`PIN_ASKED` is set and `PIN_REF` is empty) gives NOT A RESULT (setup). Nothing from the
     commit under test or from main runs.
   - How the name is kept out of the public log: (1) the validate step's first line masks the
     map; (2) the name is resolved inside a command substitution that prints nothing (the map
     is read from the environment by a small python helper, never put on a command line); (3)
     the name and, if it has a slash and the part before it is at least 3 characters, that
     part are masked by `::add-mask::` the moment they are known, before any check on them;
     (4) no script echoes the alias, the map or the name, and every error is a fixed word;
     (5) the name leaves the step only by writing `branch=` to `GITHUB_OUTPUT` (with
     `asked=1`), and the check step receives it as `env: PIN_REF`, so the step header prints
     `PIN_REF: ***`. It is never in a `run:` text, a run name, a concurrency group, an `if:`,
     a `with:` or a job output (job outputs are printed in the next job's header). The check
     step references no secret (rule 6): the output is not a secret reference, but its value
     comes from one, which is why it must stay masked. `run-private.sh` unsets `PIN_REF` and
     `PIN_ASKED` before the code under test starts and prints only "the pinned branch".
   - What is not covered: the runner's masking was not exercised in a real run when this was
     written (the tests run the validate step's script under bash and apply the same masking
     rule the runner applies; the workflow text is parsed). Confirm it on the first trial run:
     the validate and check step headers must show `***` for `PIN_MAP` and `PIN_REF`, and the
     setup message must say "the pinned branch". A masked value is hidden only where the exact
     text appears: do not pick a name that is also a common word or that other log lines print.
     Code that runs in the job can read the name from the step output file or the process
     environment (rule 6). Whoever can edit the secret decides what is allowed, and
     whoever can dispatch `live-suite.yml` (write access to this repository) can use any alias
     in it.
   - Exposure: a dispatcher can make a run execute the entry scripts of any branch in the
     map. That is the same trust as dispatching an arbitrary sha, since the commit under test is
     already arbitrary code; the pin changes which scripts judge it, so a pinned run is only as
     trustworthy as that branch. Do not use a pin for a result you rely on to merge.
