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
