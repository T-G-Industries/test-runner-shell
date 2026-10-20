"""Test helper: a stand-in `curl` that answers from a routes file and records calls.

make_stub(dir) writes an executable `curl` into dir. The stub reads $STUB_DIR/routes.json
(a list of [method, url-substring, http-code, body] with an optional fifth element, a
substring that must occur in the request body) and answers with the first match (404 if none). Every call is appended to
$STUB_DIR/calls.jsonl.
"""
import json
import os
import stat
import sys

STUB = r'''#!__PY__
import json, os, sys
d = os.environ["STUB_DIR"]
a = sys.argv[1:]
out = None; method = "GET"; data = None; url = None; auth = None
i = 0
while i < len(a):
    x = a[i]
    if x in ("-o", "-w", "-X", "-H", "--data-binary", "-d", "--data", "--max-time", "--retry"):
        v = a[i + 1]
        if x == "-o": out = v
        elif x == "-X": method = v
        elif x == "-H" and v.lower().startswith("authorization:"): auth = v.split(":", 1)[1].strip()
        elif x in ("--data-binary", "-d", "--data"):
            data = open(v[1:]).read() if v.startswith("@") else v
            if method == "GET": method = "POST"
        i += 2
        continue
    if x.startswith("http"): url = x
    i += 1
routes = json.load(open(os.path.join(d, "routes.json")))
code, body = 404, {}
for route in routes:
    m, sub, c, b = route[:4]
    if m != method or sub not in url:
        continue
    if len(route) > 4 and route[4] not in (data or ""):
        continue
    code, body = c, b
    break
with open(os.path.join(d, "calls.jsonl"), "a") as f:
    f.write(json.dumps({"method": method, "url": url, "auth": auth, "data": data}) + "\n")
if out and out != "/dev/null":
    with open(out, "w") as f:
        f.write(body if isinstance(body, str) else json.dumps(body))
sys.stdout.write(str(code))
'''


def make_stub(directory):
    path = os.path.join(directory, "curl")
    with open(path, "w") as f:
        f.write(STUB.replace("__PY__", sys.executable))
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


def set_routes(directory, routes):
    with open(os.path.join(directory, "routes.json"), "w") as f:
        json.dump(routes, f)


def calls(directory):
    p = os.path.join(directory, "calls.jsonl")
    if not os.path.exists(p):
        return []
    with open(p) as f:
        return [json.loads(line) for line in f if line.strip()]
