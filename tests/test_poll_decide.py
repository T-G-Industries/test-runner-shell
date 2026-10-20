import importlib.util, json, os, subprocess, sys, tempfile, unittest
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "poll_decide.py")
spec = importlib.util.spec_from_file_location("poll_decide", SCRIPT)
pd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pd)

NOW = 1_800_000_000  # fixed "now" (epoch seconds)
ALL = ["c01", "c02", "c03", "c04", "c05", "c06"]


def iso(epoch):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


_id = [0]


def st(code, state, age_min=1, context=None):
    _id[0] += 1
    return {"id": _id[0], "context": context or "ci/" + code, "state": state,
            "created_at": iso(NOW - age_min * 60)}


def finals(codes=ALL, state="success"):
    return [st(c, state) for c in codes]


class Decide(unittest.TestCase):
    def test_nothing_known_dispatches_all_six(self):
        self.assertEqual(pd.decide([], NOW), ALL)

    def test_only_missing_codes(self):
        self.assertEqual(pd.decide(finals(["c01", "c02", "c04"]), NOW), ["c03", "c05", "c06"])

    def test_other_contexts_are_ignored(self):
        other = [st("c01", "success", context="live/c01"), st("c02", "success", context="some other check")]
        self.assertEqual(pd.decide(other, NOW), ALL)

    def test_all_final_is_skipped(self):
        mixed = finals(ALL[:3]) + finals(ALL[3:], "failure")
        self.assertTrue(pd.all_final(mixed))
        self.assertEqual(pd.decide(mixed, NOW), [])

    def test_failure_is_final_not_retried(self):
        self.assertEqual(pd.decide([st("c01", "failure")] + finals(ALL[1:]), NOW), [])

    def test_young_pending_not_dispatched(self):
        s = [st("c01", "pending", age_min=149)] + finals(ALL[1:])
        self.assertEqual(pd.decide(s, NOW), [])
        self.assertFalse(pd.all_final(s))

    def test_old_pending_is_dispatched_again(self):
        s = [st("c01", "pending", age_min=151)] + finals(ALL[1:])
        self.assertEqual(pd.decide(s, NOW), ["c01"])

    def test_old_pending_retry_budget_is_three_attempts(self):
        s = [st("c01", "pending", age_min=400), st("c01", "pending", age_min=300),
             st("c01", "pending", age_min=200)] + finals(ALL[1:])
        self.assertEqual(pd.decide(s, NOW), [])

    def test_queued_plus_running_markers_are_one_attempt(self):
        # An attempt leaves two pending markers: "queued" (poller, at dispatch) and "running"
        # (the run itself). Both together count once, so the budget is still three attempts.
        def attempt(age):
            return [dict(st("c01", "pending", age), description="queued"),
                    dict(st("c01", "pending", age - 1), description="running")]
        rest = finals(ALL[1:])
        self.assertEqual(pd.decide(attempt(900) + attempt(600) + rest, NOW), ["c01"])
        self.assertEqual(pd.decide(attempt(900) + attempt(600) + attempt(300) + rest, NOW), [])

    def test_attempts_that_never_started_or_were_started_by_hand_count_too(self):
        rest = finals(ALL[1:])
        q = lambda age: dict(st("c01", "pending", age), description="queued")
        r = lambda age: dict(st("c01", "pending", age), description="running")
        self.assertEqual(pd.decide([q(900), q(600)] + rest, NOW), ["c01"])
        self.assertEqual(pd.decide([q(900), q(600), q(300)] + rest, NOW), [])
        self.assertEqual(pd.decide([r(900), r(600)] + rest, NOW), ["c01"])
        self.assertEqual(pd.decide([r(900), r(600), r(300)] + rest, NOW), [])

    def test_fresh_queued_marker_blocks_a_second_dispatch(self):
        s = [dict(st("c01", "pending", 1), description="queued")] + finals(ALL[1:])
        self.assertEqual(pd.decide(s, NOW), [])

    def test_unparsable_pending_time_counts_as_young(self):
        bad = {"id": 99, "context": "ci/c01", "state": "pending", "created_at": "garbage"}
        self.assertEqual(pd.decide([bad] + finals(ALL[1:]), NOW), [])

    def test_error_retries_up_to_three(self):
        rest = finals(ALL[1:])
        one = [st("c01", "error", 30)] + rest
        two = [st("c01", "error", 60), st("c01", "error", 30)] + rest
        three = [st("c01", "error", 90), st("c01", "error", 60), st("c01", "error", 30)] + rest
        self.assertEqual(pd.decide(one, NOW), ["c01"])
        self.assertEqual(pd.decide(two, NOW), ["c01"])
        self.assertEqual(pd.decide(three, NOW), [])

    def test_latest_decides_not_first_listed(self):
        # API lists newest first; order in the list must not matter, created_at does.
        old_err = st("c01", "error", 50)
        new_ok = st("c01", "success", 10)
        for order in ([old_err, new_ok], [new_ok, old_err]):
            self.assertEqual(pd.decide(order + finals(ALL[1:]), NOW), [])

    def test_error_then_young_pending_waits(self):
        s = [st("c01", "error", 20), st("c01", "pending", 5)] + finals(ALL[1:])
        self.assertEqual(pd.decide(s, NOW), [])

    def test_same_second_ties_use_id(self):
        a = {"id": 1, "context": "ci/c01", "state": "error", "created_at": iso(NOW - 60)}
        b = {"id": 2, "context": "ci/c01", "state": "success", "created_at": iso(NOW - 60)}
        self.assertEqual(pd.decide([a, b] + finals(ALL[1:]), NOW), [])

    def test_budget_caps_the_result(self):
        self.assertEqual(pd.decide([], NOW, budget=4), ALL[:4])
        self.assertEqual(pd.decide([], NOW, budget=0), [])
        self.assertEqual(pd.decide([], NOW, budget=-3), [])

    def test_garbage_input_dispatches_nothing(self):
        for bad in (None, {}, {"message": "Bad credentials"}, "text", 5):
            self.assertEqual(pd.decide(bad, NOW), [])
            self.assertFalse(pd.all_final(bad))

    def test_garbage_items_are_skipped(self):
        junk = [None, 3, "x", {"context": 5, "state": []}, {"context": "ci/c01"}, {"state": "success"}]
        self.assertEqual(pd.decide(junk, NOW), ALL)

    def test_unknown_state_is_ignored(self):
        s = [{"id": 1, "context": "ci/c01", "state": "weird", "created_at": iso(NOW)}] + finals(ALL[1:])
        self.assertEqual(pd.decide(s, NOW), ["c01"])


class Cli(unittest.TestCase):
    def run_cli(self, content, now=NOW, budget=12):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            if content is not None:
                with open(p, "w") as f:
                    f.write(content if isinstance(content, str) else json.dumps(content))
            r = subprocess.run([sys.executable, "-I", SCRIPT, p, str(now), str(budget)],
                               capture_output=True, text=True)
            return r

    def test_prints_codes_only(self):
        r = self.run_cli(finals(["c01"]))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.split(), ["c02", "c03", "c04", "c05", "c06"])

    def test_budget_argument(self):
        self.assertEqual(self.run_cli([], budget=2).stdout.split(), ["c01", "c02"])

    def test_bad_inputs_print_nothing(self):
        for content in (None, "not json", '{"message": "x"}'):
            r = self.run_cli(content)
            self.assertEqual(r.stdout.strip(), "")
        r = subprocess.run([sys.executable, "-I", SCRIPT], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "")
        r = self.run_cli([], now="abc")
        self.assertEqual(r.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
