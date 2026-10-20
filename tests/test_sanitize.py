import json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "sanitize.py")
LEAK = "SECRET-TEXT-must-never-appear"


def run(result, rc="0"):
    with tempfile.TemporaryDirectory() as d:
        r, e, s = (os.path.join(d, n) for n in ("r.json", "e", "s.json"))
        if result is not None:
            open(r, "w").write(result if isinstance(result, str) else json.dumps(result))
        open(e, "w").write(rc)
        out = subprocess.run([sys.executable, "-I", SCRIPT, r, e, s], capture_output=True, text=True).stdout
        return out, json.load(open(s))


OK = {"passed": 4, "failed": 0, "skipped": 0, "errors": 0, "counted": True, "reason": "ok"}
CTX = "Some Check (part 1/2), v1.0: x+y_z-w"


class Context(unittest.TestCase):
    def test_valid_context_goes_to_status_json(self):
        out, st = run(dict(OK, context=CTX))
        self.assertEqual(st["context"], CTX)
        self.assertEqual(st["state"], "success")

    def test_context_is_never_printed(self):
        out_with, _ = run(dict(OK, context=CTX))
        out_without, st = run(OK)
        self.assertNotIn("Some Check", out_with)
        self.assertEqual(out_with, out_without)
        self.assertNotIn("context", st)

    def test_length_boundaries(self):
        self.assertEqual(run(dict(OK, context="a" * 100))[1]["context"], "a" * 100)
        self.assertEqual(run(dict(OK, context="a"))[1]["context"], "a")
        self.assertNotIn("context", run(dict(OK, context="a" * 101))[1])
        self.assertNotIn("context", run(dict(OK, context=""))[1])

    def test_odd_contexts_are_dropped(self):
        odd = ["bad\nname", "trailing\n", "semi;colon", "pipe|x", "quote'x", 'dq"x', "tick`x", "$(x)", "<b>",
               "caf\u00e9", "tab\tx", "back\\slash", "a" * 5000, "{x}", "x#y", "x=y", "x@y"]
        for c in odd:
            out, st = run(dict(OK, context=c))
            self.assertNotIn("context", st, repr(c))
            self.assertEqual(st["state"], "success")
            if len(c) > 3:
                self.assertNotIn(c, out)

    def test_names_of_the_private_checks_are_accepted(self):
        for name in ("Lint / type check / dependency scan",
                     "Deployment config tests (tests/test_bundle.py)",
                     "Docs generated files up to date (build-*.py --check)",
                     "Screens (build, unit tests, boundary check)",
                     "Secret and real-data scan (gitleaks, scan-real-data.py)",
                     "Dependency vulnerability scan and SBOM (Trivy, Syft)"):
            self.assertEqual(run(dict(OK, context=name))[1].get("context"), name, name)

    def test_marker_and_suite_prefixes_are_rejected(self):
        # A commit under test must not be able to write the poller's marker or a suite status.
        for name in ("ci/c01", "ci/anything", "CI/c01", "Ci/c02", "live/s01", "LIVE/s01", "live/x y",
                     " ci/c01", " live/s01", "ci/", "live/"):
            out, st = run(dict(OK, context=name))
            self.assertNotIn("context", st, repr(name))
            self.assertEqual(st["state"], "success")
        # Only the prefix is reserved; the words may appear later in a name.
        for name in ("Check ci/c01", "alive/x", "ci", "live", "cix/c01"):
            self.assertEqual(run(dict(OK, context=name))[1].get("context"), name, name)

    def test_leading_or_trailing_space_is_rejected(self):
        for name in (" Lint", "Lint ", " "):
            self.assertNotIn("context", run(dict(OK, context=name))[1], repr(name))

    def test_non_string_contexts_are_dropped(self):
        for c in (5, None, True, ["x"], {"a": "b"}, 1.5):
            self.assertNotIn("context", run(dict(OK, context=c))[1], repr(c))

    def test_context_is_kept_on_a_not_a_result(self):
        _, st = run({"passed": 0, "failed": 0, "skipped": 2, "errors": 0, "counted": False,
                     "reason": "skips", "context": CTX})
        self.assertEqual((st["state"], st["context"]), ("error", CTX))
        _, st = run({"passed": "x", "context": CTX})
        self.assertEqual((st["state"], st["context"]), ("error", CTX))

    def test_context_alone_is_not_enough_for_a_pass(self):
        _, st = run({"context": CTX})
        self.assertEqual(st["state"], "error")

    def test_leak_in_context_position_is_not_copied(self):
        out, st = run(dict(OK, context=LEAK + "!"))
        self.assertNotIn(LEAK, out + json.dumps(st))


class Robust(unittest.TestCase):
    def test_deeply_nested_json_is_not_a_result_not_a_crash(self):
        for text in ("[" * 200000, '{"a":' * 100000, "[" * 5000 + "]" * 5000):
            out, st = run(text)
            self.assertEqual(st["state"], "error", text[:10])
            self.assertIn("NOT A RESULT", out)

    def test_unreadable_inputs_are_not_a_result(self):
        with tempfile.TemporaryDirectory() as d:
            s = os.path.join(d, "s.json")
            # result.json and exit_code are directories: reading them raises OSError subclasses.
            r = subprocess.run([sys.executable, "-I", SCRIPT, d, d, s], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.load(open(s))["state"], "error")

    def test_invalid_utf8_is_not_a_result(self):
        with tempfile.TemporaryDirectory() as d:
            r_, e, s = (os.path.join(d, n) for n in ("r.json", "e", "s.json"))
            open(r_, "wb").write(b'{"context": "\xff\xfe"}')
            open(e, "w").write("0")
            r = subprocess.run([sys.executable, "-I", SCRIPT, r_, e, s], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.load(open(s))["state"], "error")


class T(unittest.TestCase):
    def test_pass(self):
        out, st = run({"passed": 11, "failed": 0, "skipped": 0, "errors": 0, "counted": True, "reason": "ok"})
        self.assertEqual(st["state"], "success")
        self.assertIn("PASS: passed 11", out)

    def test_fail(self):
        _, st = run({"passed": 3, "failed": 1, "skipped": 0, "errors": 0, "counted": True, "reason": "fail"}, "1")
        self.assertEqual(st["state"], "failure")

    def test_not_a_result_when_skips(self):
        _, st = run({"passed": 3, "failed": 0, "skipped": 2, "errors": 0, "counted": False, "reason": "skips"})
        self.assertEqual(st["state"], "error")

    def test_missing_and_garbage(self):
        self.assertEqual(run(None)[1]["state"], "error")
        self.assertEqual(run("not json")[1]["state"], "error")

    def test_free_text_never_passes_through(self):
        out, st = run({"passed": LEAK, "failed": 0, "skipped": 0, "errors": 0, "counted": True,
                       "reason": LEAK, "note": LEAK})
        self.assertNotIn(LEAK, out + json.dumps(st))
        self.assertEqual(st["state"], "error")
        out, st = run({"passed": 1, "failed": 0, "skipped": 0, "errors": 0, "counted": True,
                       "reason": "ok", "note": LEAK, "names": [LEAK]})
        self.assertNotIn(LEAK, out + json.dumps(st))

    def test_nonzero_exit_is_not_a_pass(self):
        _, st = run({"passed": 5, "failed": 0, "skipped": 0, "errors": 0, "counted": True, "reason": "ok"}, "1")
        self.assertEqual(st["state"], "failure")


if __name__ == "__main__":
    unittest.main()
