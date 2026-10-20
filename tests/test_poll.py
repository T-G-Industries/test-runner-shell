import json, os, re, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import stubcurl  # noqa: E402

SCRIPT = os.path.join(HERE, "..", "scripts", "poll.sh")
LEAK = "SECRET-TEXT-must-never-appear"
PRIV_TOKEN = "priv-token-synthetic"
GH_TOKEN = "gh-token-synthetic"
A, B, C, D = ("a" * 40, "b" * 40, "c" * 40, "d" * 40)
ALL = ["c01", "c02", "c03", "c04", "c05", "c06"]
PRIV = "https://api.github.com/repos/owner-x/repo-y/"
PUB = "https://api.github.com/repos/pub-owner/pub-repo/actions/workflows/live-suite.yml/dispatches"


def status(code, state, ts="2999-01-01T00:00:00Z", i=1):
    return {"id": i, "context": "ci/" + code, "state": state, "created_at": ts}


def run_poll(routes, check_calls=None):
    with tempfile.TemporaryDirectory() as d:
        stubcurl.make_stub(d)
        stubcurl.set_routes(d, routes)
        env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], STUB_DIR=d,
                   PRIVATE_REPO="owner-x/repo-y", PRIVATE_TOKEN=PRIV_TOKEN, GITHUB_TOKEN=GH_TOKEN,
                   GITHUB_REPOSITORY="pub-owner/pub-repo")
        r = subprocess.run(["bash", SCRIPT], capture_output=True, text=True, env=env)
        return r, stubcurl.calls(d)


def pulls(*items):  # (sha, number)
    return ["GET", "/pulls?", 200, [{"number": n, "head": {"sha": s}, "title": LEAK} for s, n in items]]


def queue(*shas):
    return ["GET", "/git/matching-refs/heads/gh-readonly-queue/main/", 200,
            [{"ref": "refs/heads/gh-readonly-queue/main/pr-9-" + s, "object": {"sha": s}} for s in shas]]


def sts(sha, body, code=200):
    return ["GET", "/commits/%s/statuses" % sha, code, body]


def dispatch_ok():
    return ["POST", PUB, 204, ""]


def dispatches(calls):
    return [c for c in calls if c["method"] == "POST" and c["url"] == PUB]


def markers(calls):
    """(sha, code, state, description) of every marker posted to the private repo."""
    out = []
    for c in calls:
        if c["method"] == "POST" and c["url"].startswith(PRIV + "statuses/"):
            b = json.loads(c["data"])
            out.append((c["url"][len(PRIV + "statuses/"):], b["context"], b["state"], b.get("description")))
    return out


def marker_ok():
    return ["POST", PRIV + "statuses/", 201, ""]


def page(n):
    return "page=%d" % n


def filler(n, code="c01"):  # n unrelated statuses (other contexts), newest first
    return [{"id": 1000 + i, "context": "other/%d" % i, "state": "success",
             "created_at": "2026-01-01T00:00:00Z"} for i in range(n)]


class Poll(unittest.TestCase):
    def assert_only_integers(self, r):
        for line in (r.stdout + r.stderr).splitlines():
            self.assertRegex(line, r"^(heads seen|runs dispatched): [0-9]+$|^::warning::http [0-9a-z-]+$")
        self.assertNotIn(LEAK, r.stdout + r.stderr)

    def test_mixed_heads(self):
        routes = [
            pulls((A, 7), (B, 8)), queue(C),
            sts(A, [status(c, "success") for c in ALL]),
            sts(B, []),
            sts(C, [status("c01", "success")]),
            dispatch_ok(), marker_ok(),
        ]
        r, calls = run_poll(routes)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.splitlines(), ["heads seen: 3", "runs dispatched: 11"])
        self.assert_only_integers(r)
        posts = dispatches(calls)
        self.assertEqual(len(posts), 11)
        bodies = [json.loads(p["data"]) for p in posts]
        for p in posts:
            self.assertEqual(p["url"], PUB)
            self.assertEqual(p["auth"], "Bearer " + GH_TOKEN)
        for b in bodies:
            self.assertEqual(b["ref"], "main")
        b_runs = [b["inputs"] for b in bodies if b["inputs"]["sha"] == B]
        c_runs = [b["inputs"] for b in bodies if b["inputs"]["sha"] == C]
        self.assertEqual([x["suite_code"] for x in b_runs], ALL)
        self.assertTrue(all(x["pr"] == "8" for x in b_runs))
        self.assertEqual([x["suite_code"] for x in c_runs], ALL[1:])
        self.assertTrue(all("pr" not in x for x in c_runs))
        self.assertFalse([b for b in bodies if b["inputs"]["sha"] == A])

    def test_reads_use_the_private_token_only(self):
        r, calls = run_poll([pulls((A, 1)), queue(), sts(A, []), dispatch_ok(), marker_ok()])
        for c in calls:
            if c["method"] == "GET":
                self.assertEqual(c["auth"], "Bearer " + PRIV_TOKEN)
                self.assertTrue(c["url"].startswith(PRIV))
        urls = [c["url"] for c in calls if c["method"] == "GET"]
        self.assertTrue(any("pulls?state=open&base=main&per_page=100&page=1" in u for u in urls))
        self.assertTrue(any("matching-refs/heads/gh-readonly-queue/main/" in u for u in urls))
        self.assertTrue(any(u.endswith("/commits/%s/statuses?per_page=100&page=1" % A) for u in urls))
        for c in calls:  # the marker is a write to the private repo with the private token
            if c["method"] == "POST" and c["url"].startswith(PRIV):
                self.assertEqual(c["auth"], "Bearer " + PRIV_TOKEN)

    def test_cap_is_twelve(self):
        shas = ["1" * 40, "2" * 40, "3" * 40, "4" * 40]
        routes = [pulls(*[(s, i + 1) for i, s in enumerate(shas)]), queue(), dispatch_ok(), marker_ok()]
        routes += [sts(s, []) for s in shas]
        r, calls = run_poll(routes)
        self.assertEqual(r.stdout.splitlines(), ["heads seen: 4", "runs dispatched: 12"])
        self.assertEqual(len(dispatches(calls)), 12)
        self.assertEqual(len(markers(calls)), 12)

    def test_same_sha_in_pr_and_queue_counted_once_keeps_pr(self):
        r, calls = run_poll([pulls((A, 5)), queue(A), sts(A, []), dispatch_ok(), marker_ok()])
        self.assertEqual(r.stdout.splitlines(), ["heads seen: 1", "runs dispatched: 6"])
        self.assertTrue(all(json.loads(p["data"])["inputs"]["pr"] == "5" for p in dispatches(calls)))

    def test_retry_logic_is_applied(self):
        errs = [status("c01", "error", "2026-01-01T00:00:0%dZ" % i, i) for i in (1, 2)]
        three = [status("c02", "error", "2026-01-01T00:00:0%dZ" % i, 10 + i) for i in (1, 2, 3)]
        rest = [status(c, "success", i=20 + n) for n, c in enumerate(ALL[2:])]
        r, calls = run_poll([pulls((A, 1)), queue(), sts(A, errs + three + rest), dispatch_ok(), marker_ok()])
        self.assertEqual([json.loads(p["data"])["inputs"]["suite_code"] for p in dispatches(calls)], ["c01"])

    def test_list_failure_prints_only_the_code(self):
        routes = [["GET", "/pulls?", 403, {"message": LEAK}], queue(B), sts(B, []), dispatch_ok(), marker_ok()]
        r, calls = run_poll(routes)
        out = r.stdout + r.stderr
        self.assertIn("403", out)
        self.assertNotIn(LEAK, out)
        self.assertIn("heads seen: 1", out)
        self.assertEqual(r.returncode, 0)
        for line in out.splitlines():
            self.assertRegex(line, r"^(heads seen|runs dispatched): [0-9]+$|^::warning::http [0-9]+$")

    def test_both_lists_failing_is_a_failed_run(self):
        routes = [["GET", "/pulls?", 401, LEAK], ["GET", "/matching-refs/", 401, LEAK]]
        r, calls = run_poll(routes)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn(LEAK, r.stdout + r.stderr)
        self.assertEqual(dispatches(calls), [])

    def test_status_read_failure_dispatches_nothing_for_that_sha(self):
        r, calls = run_poll([pulls((A, 1)), queue(), sts(A, {"message": LEAK}, 500), dispatch_ok(), marker_ok()])
        self.assertEqual(dispatches(calls), [])
        self.assertNotIn(LEAK, r.stdout + r.stderr)
        self.assertIn("runs dispatched: 0", r.stdout)

    def test_non_list_status_body_dispatches_nothing(self):
        r, calls = run_poll([pulls((A, 1)), queue(), sts(A, {"message": LEAK}), dispatch_ok(), marker_ok()])
        self.assertEqual(dispatches(calls), [])
        self.assertNotIn(LEAK, r.stdout + r.stderr)

    def test_dispatch_failure_is_not_counted_and_prints_code_only(self):
        r, calls = run_poll([pulls((A, 1)), queue(), sts(A, []), ["POST", PUB, 422, {"message": LEAK}]])
        self.assertIn("runs dispatched: 0", r.stdout)
        self.assertIn("422", r.stdout + r.stderr)
        self.assertNotIn(LEAK, r.stdout + r.stderr)

    def test_bad_shas_and_numbers_are_dropped(self):
        routes = [["GET", "/pulls?", 200, [
            {"number": 1, "head": {"sha": "xyz; rm -rf /"}},
            {"number": "1; x", "head": {"sha": D}},
            {"number": 3, "head": {"sha": D.upper()}}]],
            ["GET", "/matching-refs/", 200, [{"ref": "r", "object": {"sha": "nothex"}}]],
            sts(D, []), dispatch_ok(), marker_ok()]
        r, calls = run_poll(routes)
        for p in dispatches(calls):
            i = json.loads(p["data"])["inputs"]
            self.assertRegex(i["sha"], r"^[0-9a-f]{40}$")
            self.assertRegex(i.get("pr", "1"), r"^[0-9]{1,6}$")

    def test_no_heads(self):
        r, calls = run_poll([pulls(), queue()])
        self.assertEqual(r.stdout.splitlines(), ["heads seen: 0", "runs dispatched: 0"])

    def test_required_env(self):
        env = {k: v for k, v in os.environ.items() if k not in ("PRIVATE_TOKEN", "PRIVATE_REPO", "GITHUB_TOKEN")}
        r = subprocess.run(["bash", SCRIPT], capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)


class Marker(unittest.TestCase):
    """M1: the poller posts ci/cNN pending itself, right after a 2xx dispatch."""

    def test_marker_follows_each_successful_dispatch(self):
        r, calls = run_poll([pulls((A, 1)), queue(), sts(A, []), dispatch_ok(), marker_ok()])
        post_calls = [c for c in calls if c["method"] == "POST"]
        self.assertEqual(len(post_calls), 12)
        for i in range(0, 12, 2):  # dispatch, marker, dispatch, marker, ...
            self.assertEqual(post_calls[i]["url"], PUB)
            self.assertTrue(post_calls[i + 1]["url"].startswith(PRIV + "statuses/"))
        self.assertEqual(markers(calls), [(A, "ci/" + c, "pending", "queued") for c in ALL])
        inputs = [json.loads(c["data"])["inputs"]["suite_code"] for c in dispatches(calls)]
        self.assertEqual(inputs, ALL)
        self.assertEqual(r.stdout.splitlines(), ["heads seen: 1", "runs dispatched: 6"])

    def test_marker_url_is_on_the_exact_sha(self):
        r, calls = run_poll([pulls((A, 1)), queue(), sts(A, [status(c, "success") for c in ALL[1:]]),
                             dispatch_ok(), marker_ok()])
        m = [c for c in calls if c["method"] == "POST" and c["url"] != PUB]
        self.assertEqual([c["url"] for c in m], [PRIV + "statuses/" + A])

    def test_no_marker_when_the_dispatch_fails(self):
        r, calls = run_poll([pulls((A, 1)), queue(), sts(A, []), ["POST", PUB, 422, ""], marker_ok()])
        self.assertEqual(markers(calls), [])
        self.assertIn("runs dispatched: 0", r.stdout)

    def test_failed_marker_is_a_hard_stop_for_that_sha(self):
        routes = [pulls((A, 1), (B, 2)), queue(), sts(A, []), sts(B, []), dispatch_ok(),
                  ["POST", PRIV + "statuses/" + A, 500, {"message": LEAK}], marker_ok()]
        r, calls = run_poll(routes)
        by_sha = {}
        for p in dispatches(calls):
            i = json.loads(p["data"])["inputs"]
            by_sha.setdefault(i["sha"], []).append(i["suite_code"])
        self.assertEqual(by_sha[A], ["c01"])        # one dispatch, then stop for this sha
        self.assertEqual(by_sha[B], ALL)            # other shas carry on
        self.assertEqual(r.stdout.splitlines()[-2:], ["heads seen: 2", "runs dispatched: 7"])
        self.assertIn("::warning::http 500", r.stdout + r.stderr)
        self.assertNotIn(LEAK, r.stdout + r.stderr)
        Poll.assert_only_integers(self, r)

    def test_marker_transport_error_is_a_hard_stop_too(self):
        routes = [pulls((A, 1)), queue(), sts(A, []), dispatch_ok(), ["POST", PRIV + "statuses/", 403, ""]]
        r, calls = run_poll(routes)
        self.assertEqual(len(dispatches(calls)), 1)
        self.assertIn("::warning::http 403", r.stdout + r.stderr)

    def test_next_poll_does_not_dispatch_again_after_a_marker(self):
        # "Dispatch gap": the run has been dispatched and has not started yet. The only
        # trace on the commit is the poller's own marker, and that is enough.
        gap = [{"id": 5, "context": "ci/c01", "state": "pending", "description": "queued",
                "created_at": "2999-01-01T00:00:00Z"}]
        r, calls = run_poll([pulls((A, 1)), queue(), sts(A, gap + [status(c, "success", i=10 + n)
                                                                    for n, c in enumerate(ALL[1:])]),
                             dispatch_ok(), marker_ok()])
        self.assertEqual(dispatches(calls), [])
        self.assertEqual(markers(calls), [])

    def test_two_polls_in_a_row_start_each_code_once(self):
        # The second poll sees what the first one wrote (simulated from the first one's markers).
        r1, calls1 = run_poll([pulls((A, 1)), queue(), sts(A, []), dispatch_ok(), marker_ok()])
        seen = [{"id": 50 + n, "context": m[1], "state": m[2], "description": m[3],
                 "created_at": "2999-01-01T00:00:00Z"} for n, m in enumerate(markers(calls1))]
        r2, calls2 = run_poll([pulls((A, 1)), queue(), sts(A, seen), dispatch_ok(), marker_ok()])
        self.assertEqual(len(dispatches(calls1)), 6)
        self.assertEqual(dispatches(calls2), [])

    def test_stale_queued_marker_is_retried_but_bounded(self):
        old = lambda n: {"id": n, "context": "ci/c01", "state": "pending", "description": "queued",
                         "created_at": "2001-01-0%dT00:00:00Z" % n}
        rest = [status(c, "success", i=20 + n) for n, c in enumerate(ALL[1:])]
        r, calls = run_poll([pulls((A, 1)), queue(), sts(A, [old(1), old(2)] + rest), dispatch_ok(), marker_ok()])
        self.assertEqual([json.loads(p["data"])["inputs"]["suite_code"] for p in dispatches(calls)], ["c01"])
        r, calls = run_poll([pulls((A, 1)), queue(), sts(A, [old(1), old(2), old(3)] + rest),
                             dispatch_ok(), marker_ok()])
        self.assertEqual(dispatches(calls), [])


class Pagination(unittest.TestCase):
    """M3: pull requests, queue branches and statuses are read page by page."""

    def test_statuses_on_page_two_are_seen(self):
        older = [status(c, "success", i=n + 1) for n, c in enumerate(ALL)]   # all six, on page 2
        routes = [pulls((A, 1)), queue(), sts(A, older, 200), dispatch_ok(), marker_ok()]
        routes.insert(2, ["GET", "/commits/%s/statuses" % A, 200, filler(100)])  # page 1 (generic)
        routes[2][1] += "?per_page=100&page=1"
        routes[3][1] += "?per_page=100&page=2"
        r, calls = run_poll(routes)
        self.assertEqual(dispatches(calls), [])
        self.assertEqual(r.stdout.splitlines(), ["heads seen: 1", "runs dispatched: 0"])
        urls = [c["url"] for c in calls if "/statuses?" in c["url"]]
        self.assertEqual(len(urls), 2)

    def test_short_page_stops_the_paging(self):
        r, calls = run_poll([pulls((A, 1)), queue(), sts(A, filler(99)), dispatch_ok(), marker_ok()])
        urls = [c["url"] for c in calls if "/statuses?" in c["url"] and c["method"] == "GET"]
        self.assertEqual(len(urls), 1)
        self.assertEqual(len(dispatches(calls)), 6)

    def test_failed_second_status_page_dispatches_nothing_for_that_sha(self):
        routes = [pulls((A, 1)), queue(),
                  ["GET", "/commits/%s/statuses?per_page=100&page=1" % A, 200, filler(100)],
                  ["GET", "/commits/%s/statuses?per_page=100&page=2" % A, 500, {"message": LEAK}],
                  dispatch_ok(), marker_ok()]
        r, calls = run_poll(routes)
        self.assertEqual(dispatches(calls), [])
        self.assertEqual(markers(calls), [])
        self.assertNotIn(LEAK, r.stdout + r.stderr)
        self.assertIn("::warning::http 500", r.stdout + r.stderr)

    def test_endless_status_pages_fail_closed(self):
        routes = [pulls((A, 1)), queue(), sts(A, filler(100)), dispatch_ok(), marker_ok()]
        r, calls = run_poll(routes)
        self.assertEqual(dispatches(calls), [])
        urls = [c["url"] for c in calls if "/statuses?" in c["url"] and c["method"] == "GET"]
        self.assertLessEqual(len(urls), 10)

    def test_pull_requests_on_page_two_are_seen(self):
        def pr(n):
            return {"number": n, "head": {"sha": "%040x" % (0xabc000 + n)}, "title": LEAK}
        p1 = [pr(n) for n in range(1, 101)]
        routes = [["GET", "/pulls?state=open&base=main&per_page=100&page=1", 200, p1],
                  ["GET", "/pulls?state=open&base=main&per_page=100&page=2", 200, [pr(101)]],
                  queue(), ["GET", "/commits/", 200, [status(c, "success") for c in ALL]], dispatch_ok(), marker_ok()]
        r, calls = run_poll(routes)
        self.assertEqual(r.stdout.splitlines(), ["heads seen: 101", "runs dispatched: 0"])

    def test_failed_second_pull_page_is_a_failed_list(self):
        def pr(n):
            return {"number": n, "head": {"sha": "%040x" % (0xabc000 + n)}}
        routes = [["GET", "/pulls?state=open&base=main&per_page=100&page=1", 200, [pr(n) for n in range(1, 101)]],
                  ["GET", "/pulls?state=open&base=main&per_page=100&page=2", 502, LEAK],
                  ["GET", "/matching-refs/", 401, LEAK]]
        r, calls = run_poll(routes)
        self.assertNotEqual(r.returncode, 0)   # no usable list at all
        self.assertEqual(dispatches(calls), [])
        self.assertNotIn(LEAK, r.stdout + r.stderr)

    def test_non_array_page_is_a_failed_read(self):
        r, calls = run_poll([pulls((A, 1)), queue(), sts(A, {"message": LEAK}), dispatch_ok(), marker_ok()])
        self.assertEqual(dispatches(calls), [])


if __name__ == "__main__":
    unittest.main()
