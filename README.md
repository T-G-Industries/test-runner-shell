# test-runner-shell

A thin GitHub Actions shell that runs test jobs on free public-repository minutes.

This repository contains no application source code, no test code, no data and no
configuration of any project. A run fetches code from elsewhere at run time, inside
an ephemeral runner, and prints only numbers to its public log (see
`scripts/sanitize.py`). Detailed output is delivered privately, never here.

Runs are started by hand (`workflow_dispatch`) by the repository owner, or by a small
poller (`.github/workflows/poll.yml`) that runs on a schedule and only starts runs of the
main workflow for work that has no result yet. It prints two numbers and nothing else. No
other trigger exists in this repository, and none may be added.

Codes starting with `s` are suites. Codes starting with `c` are checks: the same run
reports the result to the private side under the name that side chooses, plus a generic
marker (`ci/<code>`) that the poller reads.

## Operation notes

- The `private-code` environment (holds the secrets) must have no required reviewers and no
  wait timer, or every run waits for an approval and the poller stalls.
- GitHub turns scheduled workflows off after 60 days without activity in a public
  repository. The poller therefore needs a keep-alive (a commit or a re-enable in the Actions
  tab at least every 50 days). Manual dispatch of the main workflow is the fallback.
- Run pages show the commit sha, code, pull request number and (trial only) the pin alias as
  inputs, plus step names and the fixed words the scripts print (accepted, see `SECURITY.md`
  rule 7, which lists what stays visible). `SECURITY.md` rule 6 says exactly what is and is not
  protected from the code under test.
- Trial-only pin (`SECURITY.md` rule 9): the optional input `pin` is a short alias such as `t1`
  that means nothing by itself. It is looked up in the repository secret `PIN_MAP` (one line of
  JSON, alias to branch name, set in the `private-code` environment); the branch it points to is
  masked in the log. Leave `pin` empty for normal runs.
