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
        for needle in ("[0-9a-f]{40}", "[sc][0-9]{2}", "[0-9]{1,6}"):
            self.assertIn(needle, run)

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

    # TRIAL-ONLY pin switch (SECURITY.md rule 9): check codes may take their entry scripts
    # from a named private branch. Remove these tests together with the switch.
    def test_pin_ref_is_an_optional_string_input_defaulting_to_empty(self):
        on = self.doc.get("on", self.doc.get(True))
        spec = on["workflow_dispatch"]["inputs"]["pin_ref"]
        self.assertEqual((spec["type"], spec["required"], spec["default"]), ("string", False, ""))

    def test_pin_values_are_only_ever_passed_through_env_on_two_steps(self):
        for pat in (r"inputs\.pin_ref", r"vars\.PIN_PREFIX"):
            for line in self.text.splitlines():
                if re.search(pat, line):
                    self.assertRegex(line, r"^\s+PIN_(REF|PREFIX): \$\{\{ (inputs\.pin_ref|vars\.PIN_PREFIX) \}\}\s*$")
        holders = [s["id"] for s in self.steps if "PIN_REF" in str(s.get("env", {}))]
        self.assertEqual(holders, ["validate", "run_check"])
        holders = [s["id"] for s in self.steps if "PIN_PREFIX" in str(s.get("env", {}))]
        self.assertEqual(holders, ["validate", "run_check"])
        self.assertNotIn("PIN_", str(self.job.get("env", "")))
        self.assertNotIn("PIN_", str(self.doc.get("env", "")))
        self.assertNotIn("pin_ref", self.doc["concurrency"]["group"])
        self.assertNotIn("pin_ref", self.doc["run-name"])
        for s in self.steps:
            self.assertNotIn("pin_ref", s.get("if", ""))
            if s.get("id") != "validate":
                self.assertNotIn("pin_ref", s.get("run", ""))
            self.assertNotIn("pin_ref", str(s.get("with", {})))

    def test_the_suite_step_gets_no_pin_value(self):
        for sid in ("run_suite", "fetch", "pending", "report"):
            self.assertNotIn("PIN_", str(by_id(self.steps, sid)))

    def test_check_step_still_has_no_secret_with_the_pin_env(self):
        s = by_id(self.steps, "run_check")
        self.assertFalse(has_secret(s))
        self.assertEqual(s["env"], {"PIN_REF": "${{ inputs.pin_ref }}", "PIN_PREFIX": "${{ vars.PIN_PREFIX }}"})

    def validate(self, ref, prefix="pin/", code="c01", **env):
        e = {"PATH": os.environ["PATH"], "SHA": "a" * 40, "SUITE_CODE": code, "PR": "",
             "PRIVATE_REPO": "owner-x/repo-y", "PRIVATE_TOKEN": "tok-synthetic-value"}
        if ref is not None:
            e["PIN_REF"] = ref
        if prefix is not None:
            e["PIN_PREFIX"] = prefix
        e.update(env)
        return subprocess.run(["bash", "-eo", "pipefail", "-c", self.steps[0]["run"]], env=e,
                              capture_output=True, text=True)

    def test_validate_step_default_is_unchanged(self):
        for ref, prefix in ((None, None), ("", None), ("", ""), ("", "pin/"), (None, "pin/")):
            for code in ("c01", "c06", "s01", "s99"):
                r = self.validate(ref, prefix, code)
                self.assertEqual(r.returncode, 0, (ref, prefix, code, r.stdout + r.stderr))

    def test_validate_step_accepts_a_plain_branch_for_check_codes_only(self):
        for ref in ("pin/trial-1", "pin/a.b_c-d", "pin/feature/x", "pin/" + "a" * 100):
            for code in ("c01", "c02", "c03", "c04", "c05", "c06"):
                self.assertEqual(self.validate(ref, code=code).returncode, 0, (ref, code))
            for code in ("s01", "s05"):
                r = self.validate(ref, code=code)
                self.assertNotEqual(r.returncode, 0, (ref, code))
                self.assertNotIn(ref, r.stdout + r.stderr)

    def test_validate_step_rejects_everything_else_without_echoing_it(self):
        bad = ["pin/../main", "pin/a/../b", "pin/a..b", "pin/a b", "pin/a\tb", "pin/a\nb", "main", "origin/main",
               "refs/heads/pin/x", "refs/remotes/origin/pin/x", "other/x", "Pin/x", "pin", "pin/", "/pin/x",
               "pin/x/", "pin//x", "pin/a//b", "pin/x@{1}", "pin/x;true", "pin/$(true)", "pin/x*", "pin/x~1", "pin/x^",
               "pin/x:y", "pin/x\\y", "pin/" + "a" * 101, "origin/pin/x", "../pin/x", "pin/\u00e9"]
        for ref in bad:
            r = self.validate(ref)
            self.assertNotEqual(r.returncode, 0, ref)
            if ref not in ("pin", "pin/"):
                self.assertNotIn(ref, r.stdout + r.stderr)

    def test_validate_step_rejects_a_pin_when_the_prefix_is_unset_or_odd(self):
        for prefix in (None, "", "pin", "/", "../", "pin/../", "pin//", "a/b/", "-x/", "pin/ ", "p" * 31 + "/", "pin/*"):
            self.assertNotEqual(self.validate("pin/trial-1", prefix).returncode, 0, prefix)
            self.assertNotEqual(self.validate("main", prefix).returncode, 0, prefix)

    def test_validate_step_still_rejects_the_old_bad_inputs(self):
        self.assertNotEqual(self.validate("", SHA="g" * 40).returncode, 0)
        self.assertNotEqual(self.validate("", SUITE_CODE="x01").returncode, 0)
        self.assertNotEqual(self.validate("", PR="12x").returncode, 0)
        self.assertNotEqual(self.validate("", PRIVATE_TOKEN="").returncode, 0)


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
    def test_every_workflow_is_covered_here(self):
        self.assertEqual(sorted(os.path.basename(p) for p in glob.glob(os.path.join(WF, "*.yml"))),
                         ["live-suite.yml", "poll.yml"])


if __name__ == "__main__":
    unittest.main()
