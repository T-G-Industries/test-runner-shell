# Rules for this repository

1. Triggers: `workflow_dispatch` only. Never add `pull_request`, `pull_request_target`,
   `push`, `issue_comment`, `schedule` or `workflow_run`.
2. No application or test source, no names, no data, no secrets are ever committed.
3. Nothing from a fetched checkout may be printed, uploaded as an artifact, or cached.
4. Only `scripts/sanitize.py` writes run-specific text to the log.
5. Every change to this repository is checked with `scripts/pre-publish-check.sh`.
