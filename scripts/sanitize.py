"""Turn the private runner's result into public-safe text.

Allowlist only: the input is parsed as JSON and every field is checked for type
and range; the output is built from those checked values and fixed words. No byte
of any log is ever copied through. Anything unexpected becomes NOT A RESULT.
"""
import json
import sys

REASONS = {"ok", "skips", "setup", "timeout", "missing", "fail"}


def load(result_path, exit_path):
    try:
        with open(exit_path) as f:
            rc = int(f.read().strip())
    except (OSError, ValueError):
        rc = -1
    try:
        with open(result_path) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = None
    if not isinstance(raw, dict):
        return rc, {"passed": 0, "failed": 0, "skipped": 0, "errors": 0, "counted": False, "reason": "missing"}
    res = {}
    for k in ("passed", "failed", "skipped", "errors"):
        v = raw.get(k)
        res[k] = v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 1_000_000 else None
    res["counted"] = raw.get("counted") is True
    res["reason"] = raw.get("reason") if raw.get("reason") in REASONS else "missing"
    if any(res[k] is None for k in ("passed", "failed", "skipped", "errors")):
        res.update({"passed": 0, "failed": 0, "skipped": 0, "errors": 0, "counted": False, "reason": "missing"})
    return rc, res


def verdict(rc, res):
    if not res["counted"] or res["reason"] not in ("ok", "fail"):
        return "error", "NOT A RESULT"
    if rc == 0 and res["failed"] == 0 and res["errors"] == 0:
        return "success", "PASS"
    return "failure", "FAIL"


def main(result_path, exit_path, status_path):
    rc, res = load(result_path, exit_path)
    state, word = verdict(rc, res)
    desc = "%s: passed %d, failed %d, errors %d, skipped %d" % (
        word, res["passed"], res["failed"], res["errors"], res["skipped"])
    if word == "NOT A RESULT":
        desc = "NOT A RESULT (%s)" % res["reason"]
    with open(status_path, "w") as f:
        json.dump({"state": state, "description": desc[:140]}, f)
    print("### " + desc)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
