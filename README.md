# test-runner-shell

A thin GitHub Actions shell that runs test jobs on free public-repository minutes.

This repository contains no application source code, no test code, no data and no
configuration of any project. A run fetches code from elsewhere at run time, inside
an ephemeral runner, and prints only numbers to its public log (see
`scripts/sanitize.py`). Detailed output is delivered privately, never here.

Runs are started by hand (`workflow_dispatch`) by the repository owner only. No
other trigger exists in this repository, and none may be added.
