"""Static checks of the two workflow files (parsed with PyYAML, which must be installed).

These read the YAML, they do not run it: they fix the properties SECURITY.md promises about
where secrets appear, which step may run code from the fetched commit, and what the final
report depends on. A real run on GitHub is the only runtime proof.
"""
import glob, os, re, subprocess, unittest

import yaml  # a missing module is a failure here, never a skip

HERE = os.path.dirname(os.path.abspath(__file__))
WF = os.path.join(HERE, "..", ".github", "workflows")


def load(name):
    with open(os.path.join(WF, name)) as f:
        text = f.read()
    return yaml.safe_load(text), text


def steps_of(doc, job):
    return doc["jobs"][job]["steps"]


def by_id(steps, sid):
    found = [s for s in steps if s.get("id") == sid]
    assert len(found) == 1, "step id %s: %d found" % (sid, len(found))
    return found[0]


def has_secret(step):
    return "secrets." in str(step)


def suite_code_pattern(text):
    # The regex of the `[[ $SUITE_CODE =~ ... ]]` check, as written in the file.
    m = re.search(r"\[\[ \$SUITE_CODE =~ (\^\S+\$) \]\]", text)
    assert m, "no SUITE_CODE check found"
    return m.group(1)


def run_validate(**env):
    # Runs the validate step of live-suite.yml with synthetic inputs; returns its exit code.
    doc, _ = load("live-suite.yml")
    base = {"PATH": os.environ["PATH"], "SHA": "a" * 40, "SUITE_CODE": "c01", "PR": "",
            "PRIVATE_REPO": "owner-x/repo-y", "PRIVATE_TOKEN": "tok-synthetic-value"}
    return subprocess.run(["bash", "-eo", "pipefail", "-c", steps_of(doc, "run")[0]["run"]],
                          env=dict(base, **env), capture_output=True, text=True).returncode


def bash_matches(pattern, code):
    return subprocess.run(["bash", "-c", '[[ $1 =~ $2 ]]', "x", code, pattern]).returncode == 0


class Live(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc, cls.text = load("live-suite.yml")
        cls.job = cls.doc["jobs"]["run"]
        cls.steps = steps_of(cls.doc, "run")

    def idx(self, sid):
        return self.steps.index(by_id(self.steps, sid))

    def test_only_trigger_is_workflow_dispatch(self):
        on = self.doc.get("on", self.doc.get(True))  # PyYAML reads a bare `on` as True
        self.assertEqual(list(on), ["workflow_dispatch"])

    def test_no_secret_at_workflow_or_job_level(self):
        self.assertNotIn("secrets.", str(self.doc.get("env", "")))
        self.assertNotIn("secrets.", str(self.job.get("env", "")))
        for other in self.doc["jobs"].values():
            self.assertNotIn("secrets.", str(other.get("env", "")))

    def test_permissions_are_read_only(self):
        self.assertEqual(self.doc["permissions"], {"contents": "read"})

    def test_actions_are_pinned_by_sha(self):
        for s in self.steps:
            if "uses" in s:
                self.assertRegex(s["uses"], r"@[0-9a-f]{40}( |$)")

    def test_secrets_only_on_the_steps_that_need_them(self):
        names = [s["id"] for s in self.steps if has_secret(s)]
        self.assertEqual(names, ["validate", "pending", "fetch", "run_suite", "report"])

    def test_check_code_step_has_no_secret_reference_at_all(self):
        s = by_id(self.steps, "run_check")
        self.assertFalse(has_secret(s))
        self.assertIn("startsWith(inputs.suite_code, 'c')", s["if"])
        self.assertIn("run-private.sh", s["run"])

    def test_suite_code_step_is_the_only_other_runner_of_commit_code(self):
        s = by_id(self.steps, "run_suite")
        self.assertIn("startsWith(inputs.suite_code, 's')", s["if"])
        runners = [x["id"] for x in self.steps if "run-private.sh" in x.get("run", "")]
        self.assertEqual(runners, ["run_suite", "run_check"])

    def test_nothing_that_runs_commit_code_gets_a_secret_after_it_started_for_checks(self):
        # Between the fetch and the report only run_suite (suite codes) may mention a secret.
        between = self.steps[self.idx("fetch") + 1:self.idx("report")]
        self.assertEqual([s.get("id") for s in between if has_secret(s)], ["run_suite"])

    def test_public_scripts_are_locked_aside_before_the_fetch(self):
        lock = by_id(self.steps, "lock")
        self.assertLess(self.idx("lock"), self.idx("pending"))
        self.assertLess(self.idx("lock"), self.idx("fetch"))
        self.assertFalse(has_secret(lock))
        run = lock["run"]
        for needle in ("sudo", "/opt/trs", "chown -R root:root /opt/trs", "chmod"):
            self.assertIn(needle, run)
        self.assertRegex(run, r"chmod -R [^\n]*a-w|chmod -R [^\n]*go-w")

    def test_later_steps_use_only_the_locked_copy_of_the_scripts(self):
        after = self.steps[self.idx("lock") + 1:]
        for s in after:
            for m in re.finditer(r"(\S*)scripts/", s.get("run", "")):
                self.assertTrue(m.group(1).endswith("/opt/trs/"), (s.get("name"), m.group(0)))
        for sid in ("pending", "fetch", "run_suite", "run_check", "report"):
            self.assertIn("/opt/trs/scripts/", by_id(self.steps, sid)["run"], sid)

    def test_workspace_copy_of_the_scripts_is_removed(self):
        self.assertRegex(by_id(self.steps, "lock")["run"], r"rm -rf [^\n]*scripts")

    def test_report_runs_only_after_a_successful_validate_and_lock(self):
        cond = by_id(self.steps, "report")["if"]
        self.assertIn("always()", cond)
        self.assertIn("steps.validate.outcome == 'success'", cond)
        self.assertIn("steps.lock.outcome == 'success'", cond)

    def test_validate_is_first_and_checks_all_three_inputs(self):
        self.assertEqual(self.steps[0].get("id"), "validate")
        run = self.steps[0]["run"]
        for needle in ("[0-9a-f]{40}", "^(s[0-9]{2}|s62[a-d]|c[0-9]{2})$", "[0-9]{1,6}"):
            self.assertIn(needle, run)

    def test_suite_code_accepts_s_nn_the_s62_shards_and_c_nn_only(self):
        # The same pattern is checked in scripts/report.sh (see Repo.test_report_sh_suite_code_pattern_matches).
        for code in ("s01", "s62", "s62a", "s62b", "s62c", "s62d", "c01", "c06"):
            self.assertEqual(run_validate(SUITE_CODE=code), 0, code)
        for code in ("s62e", "s62ab", "s62A", "c01a", "s6", "x01", "s620"):
            self.assertNotEqual(run_validate(SUITE_CODE=code), 0, code)

    def test_report_step_has_a_clean_path(self):
        run = by_id(self.steps, "report")["run"]
        self.assertIn("PATH=/usr/bin:/bin", run)

    def test_step_order(self):
        for a, b in (("validate", "lock"), ("lock", "pending"), ("pending", "fetch"), ("fetch", "run_suite"),
                     ("fetch", "run_check"), ("run_suite", "report"), ("run_check", "report")):
            self.assertLess(self.idx(a), self.idx(b), (a, b))

    def test_environment_and_concurrency(self):
        self.assertEqual(self.job["environment"], "private-code")
        self.assertIn("inputs.suite_code", self.doc["concurrency"]["group"])
        self.assertIn("inputs.sha", self.doc["concurrency"]["group"])
        self.assertIs(self.doc["concurrency"]["cancel-in-progress"], False)

    def test_run_name_shows_only_the_code(self):
        self.assertEqual(self.doc["run-name"], "run ${{ inputs.suite_code }}")

    def test_no_forbidden_constructs(self):
        for bad in ("upload-artifact", "actions/cache", "set -x", "bash -x", "ACTIONS_STEP_DEBUG"):
            self.assertNotIn(bad, self.text)

    def test_there_is_no_pin_switch(self):
        self.assertEqual(sorted((self.doc.get("on") or self.doc[True])["workflow_dispatch"]["inputs"]),
                         ["pr", "sha", "suite_code"])
        for gone in ("PIN", "pin", "secrets.PIN_MAP"):
            self.assertNotIn(gone, self.text)

    def test_no_run_block_interpolates_an_expression(self):
        for s in self.steps:
            self.assertNotIn("${{", s.get("run", ""), s.get("name"))

    def test_validate_step_rejects_bad_inputs(self):
        import tempfile
        base = {"PATH": os.environ["PATH"], "SHA": "a" * 40, "SUITE_CODE": "c01", "PR": "",
                "PRIVATE_REPO": "owner-x/repo-y", "PRIVATE_TOKEN": "tok-synthetic-value"}
        def run(**env):
            e = dict(base, **env)
            return subprocess.run(["bash", "-eo", "pipefail", "-c", self.steps[0]["run"]], env=e,
                                  capture_output=True, text=True).returncode
        self.assertEqual(run(), 0)
        self.assertNotEqual(run(SHA="g" * 40), 0)
        self.assertNotEqual(run(SUITE_CODE="x01"), 0)
        self.assertNotEqual(run(PR="12x"), 0)
        self.assertNotEqual(run(PRIVATE_TOKEN=""), 0)
        self.assertNotEqual(run(PRIVATE_REPO=""), 0)


def json_map(d):
    import json
    return json.dumps(d)


class Poll(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc, cls.text = load("poll.yml")

    def test_triggers(self):
        on = self.doc.get("on", self.doc.get(True))
        self.assertIn("workflow_dispatch", on)
        self.assertLessEqual(set(on), {"schedule", "workflow_dispatch"})
        self.assertLessEqual(len(on.get("schedule", [])), 1)

    def test_secrets_only_on_the_poll_step(self):
        for job in self.doc["jobs"].values():
            self.assertNotIn("secrets.", str(job.get("env", "")))
            holders = [s.get("name") for s in job["steps"] if has_secret(s)]
            self.assertEqual(holders, ["Start missing runs (numbers only)"])

    def test_permissions(self):
        self.assertEqual(self.doc["permissions"], {"contents": "read", "actions": "write"})


class Repo(unittest.TestCase):
    def test_report_sh_suite_code_pattern_matches(self):
        with open(os.path.join(HERE, "..", "scripts", "report.sh")) as f:
            report = f.read()
        with open(os.path.join(WF, "live-suite.yml")) as f:
            live = f.read()
        pattern = suite_code_pattern(report)
        self.assertEqual(pattern, suite_code_pattern(live))
        for code in ("s01", "s62", "s62a", "s62d", "c01", "c06"):
            self.assertTrue(bash_matches(pattern, code), code)
        for code in ("s62e", "s62ab", "s62A", "c01a", "s6", "x01"):
            self.assertFalse(bash_matches(pattern, code), code)

    def test_every_workflow_is_covered_here(self):
        self.assertEqual(sorted(os.path.basename(p) for p in glob.glob(os.path.join(WF, "*.yml"))),
                         ["live-suite.yml", "poll.yml"])


if __name__ == "__main__":
    unittest.main()
