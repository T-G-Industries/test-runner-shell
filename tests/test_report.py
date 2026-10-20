import json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import stubcurl  # noqa: E402

SCRIPT = os.path.join(HERE, "..", "scripts", "report.sh")
SHA = "a" * 40
CTX = "Some Check (part 1/2), v1.0: x+y_z-w"
PRIVATE_URL = "https://api.github.com/repos/owner-x/repo-y/"


def run_report(mode, code, status=None, pr="", log=None, stub_code=201, routes=None, sha=SHA):
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "out")
        os.makedirs(out)
        stubcurl.make_stub(d)
        stubcurl.set_routes(d, (routes or []) + [["POST", PRIVATE_URL, stub_code, ""]])
        if status is not None:
            with open(os.path.join(out, "status.json"), "w") as f:
                json.dump(status, f)
        if log is not None:
            with open(os.path.join(out, "run.log"), "w") as f:
                f.write(log)
        env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], STUB_DIR=d,
                   PRIVATE_REPO="owner-x/repo-y", PRIVATE_TOKEN="tok-synthetic", SHA=sha,
                   SUITE_CODE=code, OUT=out, PR=pr, GITHUB_SERVER_URL="https://example.invalid",
                   GITHUB_REPOSITORY="pub/pub", GITHUB_RUN_ID="42")
        r = subprocess.run(["bash", SCRIPT, mode], capture_output=True, text=True, env=env)
        posts = []
        for c in stubcurl.calls(d):
            posts.append((c["url"].replace(PRIVATE_URL, ""), json.loads(c["data"])))
        return r, posts


def statuses(posts):
    return [b for u, b in posts if u == "statuses/" + SHA]


@unittest.skipUnless(shutil.which("jq"), "jq needed")
class Report(unittest.TestCase):
    def test_pending_suite_code_unchanged(self):
        r, posts = run_report("pending", "s01")
        s = statuses(posts)
        self.assertEqual(len(s), 1)
        self.assertEqual((s[0]["context"], s[0]["state"]), ("live/s01", "pending"))

    def test_pending_check_code_posts_only_the_marker(self):
        r, posts = run_report("pending", "c03")
        s = statuses(posts)
        self.assertEqual(len(s), 1)
        self.assertEqual((s[0]["context"], s[0]["state"]), ("ci/c03", "pending"))

    def test_final_suite_code_without_context(self):
        r, posts = run_report("final", "s02", {"state": "success", "description": "PASS: passed 3"})
        s = statuses(posts)
        self.assertEqual([(x["context"], x["state"], x["description"]) for x in s],
                         [("live/s02", "success", "PASS: passed 3")])

    def test_final_suite_code_with_context(self):
        r, posts = run_report("final", "s02", {"state": "failure", "description": "FAIL", "context": CTX})
        self.assertEqual([(x["context"], x["state"]) for x in statuses(posts)], [(CTX, "failure")])

    def test_final_check_code_posts_context_and_marker(self):
        r, posts = run_report("final", "c04", {"state": "success", "description": "PASS", "context": CTX})
        self.assertEqual([(x["context"], x["state"]) for x in statuses(posts)],
                         [(CTX, "success"), ("ci/c04", "success")])

    def test_final_check_code_without_context_uses_default_and_marker(self):
        r, posts = run_report("final", "c04", {"state": "error", "description": "NOT A RESULT (setup)"})
        self.assertEqual([(x["context"], x["state"]) for x in statuses(posts)],
                         [("live/c04", "error"), ("ci/c04", "error")])

    def test_missing_status_file_is_an_error_marker(self):
        r, posts = run_report("final", "c01")
        self.assertEqual([(x["context"], x["state"], x["description"]) for x in statuses(posts)],
                         [("live/c01", "error", "NOT A RESULT"), ("ci/c01", "error", "NOT A RESULT")])

    def test_odd_context_in_status_file_is_ignored(self):
        for bad in ("bad\nname", "trailing\n", "x" * 101, "semi;colon", "", "<b>", "tick`", 5):
            r, posts = run_report("final", "c01", {"state": "success", "description": "PASS", "context": bad})
            self.assertEqual([x["context"] for x in statuses(posts)], ["live/c01", "ci/c01"], repr(bad))

    def test_context_text_never_printed(self):
        r, posts = run_report("final", "c04", {"state": "success", "description": "PASS", "context": CTX})
        self.assertNotIn("Some Check", r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0)

    def test_http_failure_prints_code_only(self):
        r, posts = run_report("final", "s01", {"state": "success", "description": "PASS"}, stub_code=403)
        self.assertIn("http 403", r.stdout + r.stderr)
        self.assertNotEqual(r.returncode, 0)  # a result that was not recorded is a red step

    def test_pending_failure_is_only_a_warning(self):
        r, posts = run_report("pending", "c01", stub_code=500)
        self.assertIn("http 500", r.stdout + r.stderr)
        self.assertEqual(r.returncode, 0)

    # M2: the generic marker is the poller's "done" signal. It must not be posted when the
    # status the private side actually requires was not recorded.
    def test_marker_not_posted_when_real_post_fails(self):
        routes = [["POST", PRIVATE_URL, 500, "", CTX]]
        r, posts = run_report("final", "c04", {"state": "success", "description": "PASS", "context": CTX},
                              routes=routes)
        self.assertEqual([x["context"] for x in statuses(posts)], [CTX])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("http 500", r.stdout + r.stderr)

    def test_marker_not_posted_when_default_context_post_fails(self):
        routes = [["POST", PRIVATE_URL, 502, "", "live/c04"]]
        r, posts = run_report("final", "c04", {"state": "error", "description": "NOT A RESULT"}, routes=routes)
        self.assertEqual([x["context"] for x in statuses(posts)], ["live/c04"])
        self.assertNotEqual(r.returncode, 0)

    def test_failed_marker_post_is_a_red_step(self):
        routes = [["POST", PRIVATE_URL, 500, "", "ci/c04"]]
        r, posts = run_report("final", "c04", {"state": "success", "description": "PASS", "context": CTX},
                              routes=routes)
        self.assertEqual([x["context"] for x in statuses(posts)], [CTX, "ci/c04"])
        self.assertNotEqual(r.returncode, 0)

    def test_real_post_comes_before_marker(self):
        r, posts = run_report("final", "c01", {"state": "success", "description": "PASS", "context": CTX})
        self.assertEqual([x["context"] for x in statuses(posts)], [CTX, "ci/c01"])
        self.assertEqual(r.returncode, 0)

    def test_comment_failure_does_not_fail_the_step(self):
        routes = [["POST", "issues/12/comments", 403, ""]]
        r, posts = run_report("final", "c01", {"state": "success", "description": "PASS"}, pr="12",
                              log="x", routes=routes)
        self.assertEqual(r.returncode, 0)
        self.assertIn("http 403", r.stdout + r.stderr)

    # M4: only fixed words and status codes reach the public log.
    def test_output_has_fixed_words_only(self):
        for mode, code in (("pending", "c03"), ("pending", "s01"), ("final", "c03"), ("final", "s01")):
            r, posts = run_report(mode, code, {"state": "success", "description": "PASS", "context": CTX},
                                  pr="12", log="tail text")
            text = r.stdout + r.stderr
            for line in text.splitlines():
                self.assertRegex(line, r"^(reported: (status|marker|comment)|::warning::report failed, http [0-9a-z-]+)$")
            for needle in (SHA, "12", "owner-x", "repo-y", "tok-synthetic", "42", "statuses", "Some Check"):
                self.assertNotIn(needle, text, needle)

    def test_failure_output_has_no_sha_or_name_either(self):
        r, posts = run_report("final", "c03", {"state": "success", "description": "PASS", "context": CTX},
                              pr="12", log="x", stub_code=500)
        text = r.stdout + r.stderr
        for needle in (SHA, "owner-x", "repo-y", "tok-synthetic", "statuses", "Some Check"):
            self.assertNotIn(needle, text, needle)

    # M5 (second line of defence): report.sh drops reserved names too.
    def test_reserved_context_in_status_file_is_ignored(self):
        for bad in ("ci/c01", "CI/c09", "live/s01", "Live/x", " ci/c01"):
            r, posts = run_report("final", "c01", {"state": "success", "description": "PASS", "context": bad})
            self.assertEqual([x["context"] for x in statuses(posts)], ["live/c01", "ci/c01"], repr(bad))

    def test_star_and_underscore_in_context_are_kept(self):
        name = "Docs generated files up to date (build-*.py --check)"
        r, posts = run_report("final", "c04", {"state": "success", "description": "PASS", "context": name})
        self.assertEqual([x["context"] for x in statuses(posts)], [name, "ci/c04"])

    # M6: the inputs are checked here as well, not only in the workflow.
    def test_bad_inputs_post_nothing(self):
        cases = [dict(sha="A" * 40), dict(sha="a" * 39), dict(sha="a" * 41), dict(sha="a" * 40 + "\n"),
                 dict(sha="../" + "a" * 37), dict(code="c1"), dict(code="x01"), dict(code="c001"),
                 dict(code="c01\n"), dict(code="c01;x"), dict(pr="1234567"), dict(pr="12a"), dict(pr="-1"),
                 dict(pr="1\n2"), dict(pr="../x")]
        for case in cases:
            for mode in ("pending", "final"):
                r, posts = run_report(mode, case.get("code", "c01"), {"state": "success", "description": "PASS"},
                                      pr=case.get("pr", ""), log="x", sha=case.get("sha", SHA))
                self.assertNotEqual(r.returncode, 0, (mode, case))
                self.assertEqual(posts, [], (mode, case))
                self.assertNotIn("owner-x", r.stdout + r.stderr)

    def test_bad_mode_posts_nothing(self):
        for mode in ("", "FINAL", "pending ", "done"):
            r, posts = run_report(mode, "c01", {"state": "success", "description": "PASS"})
            self.assertNotEqual(r.returncode, 0, repr(mode))
            self.assertEqual(posts, [])

    def test_good_boundary_inputs_are_accepted(self):
        for code in ("s00", "s99", "c00", "c99"):
            r, posts = run_report("pending", code)
            self.assertEqual(r.returncode, 0, code)
            self.assertEqual(len(posts), 1)
        r, posts = run_report("final", "c01", {"state": "success", "description": "PASS"}, pr="999999", log="x")
        self.assertIn("issues/999999/comments", [u for u, _ in posts])

    def test_comment_posted_when_pr_and_log(self):
        r, posts = run_report("final", "c01", {"state": "success", "description": "PASS"}, pr="12", log="tail text")
        self.assertIn("issues/12/comments", [u for u, _ in posts])


if __name__ == "__main__":
    unittest.main()
