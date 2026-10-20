"""Decide which check codes to start for one commit, from its commit statuses.

Pure logic, no network. Run as: python3 -I poll_decide.py <statuses.json> <now-epoch> <budget>
It prints the codes to start, separated by spaces, and nothing else. Anything it cannot
read or does not understand produces no output (it never starts a run on bad data).
"""
import calendar
import json
import sys
import time

CODES = ("c01", "c02", "c03", "c04", "c05", "c06")
STATES = ("pending", "success", "failure", "error")
FINAL = ("success", "failure")
MAX_ATTEMPTS = 3
STALE_PENDING_SECONDS = 150 * 60
QUEUED = "queued"  # description of the marker the poller writes right after a dispatch


def _epoch(text):
    try:
        return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError, OverflowError):
        return None


def _entries(statuses, code):
    marker = "ci/" + code
    found = []
    for s in statuses:
        if isinstance(s, dict) and s.get("context") == marker and s.get("state") in STATES:
            found.append(s)
    return found


def _order(s):
    t = _epoch(s.get("created_at"))
    i = s.get("id")
    return (t if t is not None else 0, i if isinstance(i, int) and not isinstance(i, bool) else 0)


def needs_run(statuses, code, now):
    entries = _entries(statuses, code)
    if not entries:
        return True
    latest = max(entries, key=_order)
    state = latest["state"]
    if state in FINAL:
        return False
    if state == "error":
        return sum(1 for e in entries if e["state"] == "error") < MAX_ATTEMPTS
    started = _epoch(latest.get("created_at"))
    if started is None or now - started < STALE_PENDING_SECONDS:
        return False  # still running, or its age is unknown
    # A pending marker that never finished counts as one attempt. An attempt leaves up to two
    # pending markers: "queued" (written by the poller at dispatch) and "running" (written by
    # the run itself, or by a hand-started run). Take the larger of the two counts.
    pending = [e for e in entries if e["state"] == "pending"]
    queued = sum(1 for e in pending if e.get("description") == QUEUED)
    return max(queued, len(pending) - queued) < MAX_ATTEMPTS


def all_final(statuses, codes=CODES):
    if not isinstance(statuses, list):
        return False
    for code in codes:
        entries = _entries(statuses, code)
        if not entries or max(entries, key=_order)["state"] not in FINAL:
            return False
    return True


def decide(statuses, now, budget=12, codes=CODES):
    if not isinstance(statuses, list) or budget <= 0 or all_final(statuses, codes):
        return []
    out = [c for c in codes if needs_run(statuses, c, now)]
    return out[:budget]


def main(argv):
    try:
        path, now, budget = argv[0], int(argv[1]), int(argv[2])
        with open(path) as f:
            statuses = json.load(f)
    except (IndexError, ValueError, OSError):
        return 2
    print(" ".join(decide(statuses, now, budget)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
