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
    # from a private branch that the secret PIN_MAP maps a short public alias to. Remove these
    # tests together with the switch.
    def validate_run(self):
        return self.steps[0]["run"]

    def test_pin_is_an_optional_string_alias_input_defaulting_to_empty(self):
        on = self.doc.get("on", self.doc.get(True))
        inputs = on["workflow_dispatch"]["inputs"]
        spec = inputs["pin"]
        self.assertEqual((spec["type"], spec["required"], spec["default"]), ("string", False, ""))
        self.assertEqual(sorted(inputs), ["pin", "pr", "sha", "suite_code"])

    def test_the_old_free_text_input_and_the_prefix_variable_are_gone(self):
        for gone in ("pin_ref", "PIN_PREFIX", "inputs.pin_ref"):
            self.assertNotIn(gone, self.text)
        self.assertNotIn("vars.", self.text)  # nothing is read from a plain-text repository variable

    def test_the_documents_describe_the_alias_and_the_secret_not_the_old_input(self):
        root = os.path.join(HERE, "..")
        for name in ("SECURITY.md", "README.md"):
            with open(os.path.join(root, name)) as f:
                doc = f.read()
            for gone in ("pin_ref", "PIN_PREFIX"):
                self.assertNotIn(gone, doc, name)
        with open(os.path.join(root, "SECURITY.md")) as f:
            sec = f.read()
        for needle in ("PIN_MAP", "secret", "alias", "`t1`", "add-mask", "`***`"):
            self.assertIn(needle, sec)

    def test_the_alias_input_description_names_nothing(self):
        on = self.doc.get("on", self.doc.get(True))
        desc = on["workflow_dispatch"]["inputs"]["pin"]["description"].lower()
        self.assertNotIn("branch", desc)
        self.assertNotIn("private", desc)

    def test_alias_is_used_only_on_one_env_line_of_the_validate_step(self):
        for line in self.text.splitlines():
            if re.search(r"inputs\.pin\b", line):
                self.assertRegex(line, r"^\s+PIN: \$\{\{ inputs\.pin \}\}\s*$")
        holders = [s["id"] for s in self.steps if "inputs.pin" in str(s.get("env", {}))]
        self.assertEqual(holders, ["validate"])
        for s in self.steps:
            for key in ("run", "if", "with", "name", "id"):
                self.assertNotIn("inputs.pin", str(s.get(key, "")))
        self.assertNotIn("pin", self.doc["concurrency"]["group"])
        self.assertNotIn("pin", self.doc["run-name"])
        self.assertNotIn("PIN", str(self.job.get("env", "")))
        self.assertNotIn("PIN", str(self.doc.get("env", "")))

    def test_the_map_is_a_secret_on_the_validate_step_only(self):
        for line in self.text.splitlines():
            if "PIN_MAP" in line and not line.lstrip().startswith("#"):
                if "secrets.PIN_MAP" in line:
                    self.assertRegex(line, r"^\s+PIN_MAP: \$\{\{ secrets\.PIN_MAP \}\}\s*$")
        holders = [s["id"] for s in self.steps if "PIN_MAP" in str(s.get("env", {}))]
        self.assertEqual(holders, ["validate"])
        self.assertEqual(self.steps[0]["env"]["PIN_MAP"], "${{ secrets.PIN_MAP }}")
        self.assertEqual(self.text.count("secrets.PIN_MAP"), 1)
        for s in self.steps[1:]:
            self.assertNotIn("PIN_MAP", str(s))

    def test_the_resolved_name_reaches_only_the_check_step_through_a_masked_output(self):
        uses = [l for l in self.text.splitlines() if "steps.validate.outputs" in l]
        self.assertEqual(len(uses), 2, uses)
        self.assertRegex(uses[0], r"^\s+PIN_ASKED: \$\{\{ steps\.validate\.outputs\.asked \}\}\s*$")
        self.assertRegex(uses[1], r"^\s+PIN_REF: \$\{\{ steps\.validate\.outputs\.branch \}\}\s*$")
        s = by_id(self.steps, "run_check")
        # PIN_ASKED is a plain flag: if the masked name were ever lost on its way, the runner script
        # must fail the setup rather than silently judge with main's scripts
        self.assertEqual(s["env"], {"PIN_ASKED": "${{ steps.validate.outputs.asked }}",
                                    "PIN_REF": "${{ steps.validate.outputs.branch }}"})
        self.assertFalse(has_secret(s))
        holders = [x["id"] for x in self.steps if "PIN_REF" in str(x.get("env", {}))]
        self.assertEqual(holders, ["run_check"])
        holders = [x["id"] for x in self.steps if "PIN_ASKED" in str(x.get("env", {}))]
        self.assertEqual(holders, ["run_check"])
        self.assertNotIn("outputs", str(self.doc["concurrency"]))
        self.assertNotIn("outputs", self.doc["run-name"])
        self.assertNotIn("outputs", str(self.job.get("env", "")))
        self.assertNotIn("outputs", str(self.job.get("outputs", "")))  # job outputs are printed in the next job's header
        self.assertNotIn("outputs", str(self.doc.get("env", "")))
        for x in self.steps:
            self.assertNotIn("steps.validate.outputs", x.get("if", ""))
            self.assertNotIn("steps.validate.outputs", str(x.get("with", {})))
            self.assertNotIn("steps.validate.outputs", x.get("run", ""))

    def test_the_other_steps_get_no_pin_value(self):
        for sid in ("run_suite", "fetch", "pending", "report", "lock"):
            st = str(by_id(self.steps, sid))
            for n in ("PIN", "pin", "branch"):
                self.assertNotIn(n, st, (sid, n))

    def test_no_run_block_interpolates_an_expression(self):
        for s in self.steps:
            self.assertNotIn("${{", s.get("run", ""), s.get("name"))

    def test_validate_masks_the_map_on_its_very_first_line(self):
        first = self.validate_run().splitlines()[0]
        self.assertIn("::add-mask::", first)
        self.assertIn("$PIN_MAP", first)

    def test_validate_masks_the_name_and_prefix_before_any_other_use(self):
        lines = self.validate_run().splitlines()
        marks = [i for i, l in enumerate(lines) if re.search(r"echo \"::add-mask::\$branch\"", l)]
        self.assertEqual(len(marks), 1)
        pfx = [i for i, l in enumerate(lines) if re.search(r"echo \"::add-mask::\$prefix\"", l)]
        self.assertEqual(len(pfx), 1)
        # the assignment from the map comes first; every other mention of the name is after the mask
        assign = [i for i, l in enumerate(lines) if re.match(r"\s*branch=\$\(", l)]
        self.assertEqual(len(assign), 1)
        self.assertLess(assign[0], marks[0])
        for i, l in enumerate(lines):
            if i in (assign[0], marks[0]):
                continue
            if re.search(r"\$\{?(branch|prefix)\b", l):
                self.assertGreater(i, marks[0], l)
        # nothing between the assignment and the mask mentions it at all
        for l in lines[assign[0] + 1:marks[0]]:
            self.assertNotRegex(l, r"\$\{?branch\b")

    def test_validate_never_echoes_the_alias_the_name_or_the_map(self):
        allowed = ('echo "::add-mask::${PIN_MAP%%$\'\\n\'*}"', 'echo "::add-mask::$branch"', 'echo "::add-mask::$prefix"')
        run = self.validate_run()
        for m in re.finditer(r"\b(echo|printf)\s+((?:\"[^\"]*\"|'[^']*')[^\n;|&>]*)", run):
            cmd = m.group(0).strip()
            if re.search(r"\$\{?(PIN|PIN_MAP|branch|prefix|rest)\b", cmd):
                self.assertTrue(any(cmd.startswith(a.rstrip('"')) or cmd == a for a in allowed)
                                or cmd.startswith("printf 'asked=1"), cmd)
        # the python helper reads the map from its environment, never from an argument or the shell text
        self.assertNotRegex(run, r"python3[^\n]*\$\{?PIN")
        self.assertNotRegex(run, r"\b(cat|tee|env|printenv|declare|set)\b[^\n]*\$\{?(PIN|branch)")

    def test_validate_writes_the_name_only_to_the_step_output_file(self):
        out = [l for l in self.validate_run().splitlines() if "GITHUB_OUTPUT" in l]
        self.assertEqual(len(out), 1)
        self.assertRegex(out[0], r"^\s*printf 'asked=1\\nbranch=%s\\n' \"\$branch\" >> \"\$GITHUB_OUTPUT\"\s*$")

    def test_validate_is_the_only_step_that_uses_github_output(self):
        for s in self.steps[1:]:
            self.assertNotIn("GITHUB_OUTPUT", s.get("run", ""))

    def test_check_step_still_has_no_secret_with_the_pin_env(self):
        s = by_id(self.steps, "run_check")
        self.assertFalse(has_secret(s))

    # --- the validate step script, executed ---
    MAP = {"t1": "zebra/trial-9", "t2": "plainname", "t3": "ab/short-prefix"}

    def validate(self, alias, pin_map="default", code="c01", **env):
        import json, tempfile
        if pin_map == "default":
            pin_map = json.dumps(self.MAP)
        out = tempfile.NamedTemporaryFile("w+", delete=False)
        out.close()
        e = {"PATH": os.environ["PATH"], "SHA": "a" * 40, "SUITE_CODE": code, "PR": "",
             "PRIVATE_REPO": "owner-x/repo-y", "PRIVATE_TOKEN": "tok-synthetic-value",
             "GITHUB_OUTPUT": out.name}
        if alias is not None:
            e["PIN"] = alias
        if pin_map is not None:
            e["PIN_MAP"] = pin_map
        e.update(env)
        try:
            r = subprocess.run(["bash", "-eo", "pipefail", "-c", self.validate_run()], env=e,
                               capture_output=True, text=True)
            with open(out.name) as f:
                r.output_file = f.read()
        finally:
            os.unlink(out.name)
        return r

    @staticmethod
    def public_text(r):
        """What the public log would show: stdout and stderr minus the ::add-mask:: command lines
        (the runner consumes them), with every masked value replaced by ***."""
        text = r.stdout + r.stderr
        masks = [l[len("::add-mask::"):] for l in text.splitlines() if l.startswith("::add-mask::")]
        shown = "\n".join(l for l in text.splitlines() if not l.startswith("::add-mask::"))
        for m in sorted(masks, key=len, reverse=True):
            if m:
                shown = shown.replace(m, "***")
        return shown, masks

    def assert_clean(self, r, *secrets):
        raw = "\n".join(l for l in (r.stdout + r.stderr).splitlines() if not l.startswith("::add-mask::"))
        for sec in secrets:
            self.assertNotIn(sec, raw)
            self.assertNotIn(sec, self.public_text(r)[0])

    def test_validate_step_default_is_unchanged(self):
        for alias, pin_map in ((None, None), ("", None), ("", ""), ("", "{}"), (None, "{}"), ("", '{"t1": "zebra/trial-9"}')):
            for code in ("c01", "c06", "s01", "s99"):
                r = self.validate(alias, pin_map, code)
                self.assertEqual(r.returncode, 0, (alias, pin_map, code, r.stdout + r.stderr))
                self.assertEqual(r.output_file, "")
                self.assert_clean(r, "zebra", "trial-9")

    def test_resolves_a_known_alias_for_check_codes_and_leaks_nothing(self):
        for alias, name in (("t1", "zebra/trial-9"), ("t2", "plainname")):
            for code in ("c01", "c02", "c03", "c04", "c05", "c06"):
                r = self.validate(alias, code=code)
                self.assertEqual(r.returncode, 0, (alias, code, r.stderr))
                self.assertEqual(r.output_file, "asked=1\nbranch=%s\n" % name)
                shown, masks = self.public_text(r)
                self.assertIn(name, masks)
                self.assertNotIn(name, shown)
                self.assert_clean(r, name)
                if "/" in name:
                    self.assert_clean(r, name.split("/")[0])
                    self.assertIn(name.split("/")[0], masks)

    def test_the_whole_map_and_then_the_name_and_prefix_are_masked_in_that_order(self):
        import json
        m = json.dumps(self.MAP)
        r = self.validate("t1", m)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "::add-mask::" + m)
        masks = [l for l in lines if l.startswith("::add-mask::")]
        self.assertEqual(masks, ["::add-mask::" + m, "::add-mask::zebra/trial-9", "::add-mask::zebra"])
        self.assertEqual(lines, masks)  # and nothing else is printed on success

    def test_no_pin_prints_no_name_mask_and_writes_no_output(self):
        r = self.validate("", code="c01")
        self.assertEqual([l for l in r.stdout.splitlines() if "zebra" in l and "::add-mask::{" not in l], [])
        self.assertNotIn("::add-mask::zebra", r.stdout)
        self.assertEqual(r.output_file, "")

    def test_short_prefix_is_not_masked_because_it_would_blank_the_log(self):
        r = self.validate("t3")
        self.assertEqual(r.returncode, 0)
        self.assertIn("::add-mask::ab/short-prefix", r.stdout)
        self.assertNotIn("::add-mask::ab\n", r.stdout + "\n")

    def test_alias_for_a_suite_code_is_rejected(self):
        for code in ("s01", "s05", "s99"):
            r = self.validate("t1", code=code)
            self.assertNotEqual(r.returncode, 0, code)
            self.assertEqual(r.output_file, "")
            self.assert_clean(r, "zebra", "trial-9")

    def test_bad_aliases_are_rejected(self):
        for alias in ("T1", "t-1", "t_1", "a" * 21, "t 1", "t\n1", "t\t1", "t1;true", "../x", "$(true)", "t1/", "\u00e9",
                      "main", "pin/x", "zebra/trial-9"):
            r = self.validate(alias)
            self.assertNotEqual(r.returncode, 0, repr(alias))
            self.assertEqual(r.output_file, "")
            self.assert_clean(r, "zebra", "trial-9")
        self.assertEqual(self.validate("a" * 20, json_map({"a" * 20: "zebra/x"})).returncode, 0)
        self.assertEqual(self.validate("0", json_map({"0": "zebra/x"})).returncode, 0)

    def test_unknown_alias_and_unusable_maps_are_rejected_with_the_same_words(self):
        import json
        outcomes = set()
        for alias, pin_map in (("zz", "default"), ("t1", None), ("t1", ""), ("t1", "not json"), ("t1", "[]"), ("t1", '"t1"'),
                               ("t1", "null"), ("t1", "{}"), ("t1", '{"t1": 5}'), ("t1", '{"t1": null}'),
                               ("t1", '{"t1": ["zebra/x"]}'), ("t1", '{"t1": {"a": "zebra/x"}}'), ("t1", '{"t1": ""}'),
                               ("t1", '{"t1": "zebra/x\\n"}'), ("t1", '{"t1": "zebra/x%0Aevil"}'),
                               ("t1", '{"t1": "zebra/x y"}')):
            r = self.validate(alias, pin_map)
            self.assertNotEqual(r.returncode, 0, (alias, pin_map))
            self.assertEqual(r.output_file, "")
            shown, _ = self.public_text(r)
            self.assertNotIn("zebra", shown)
            if pin_map in ("default", None, "", "not json", "[]", '"t1"', "null", "{}", '{"t1": 5}', '{"t1": null}',
                           '{"t1": ""}'):
                outcomes.add(shown.strip())
        self.assertEqual(len(outcomes), 1, outcomes)  # no way to tell an unknown alias from a broken map

    def test_resolved_names_are_checked_like_before(self):
        import json
        bad = ["zebra/../main", "zebra/a/../b", "zebra/a..b", "..", "zebra/a b", "zebra/a\tb", "main", "HEAD", "refs/heads/zebra/x",
               "refs/remotes/origin/zebra/x", "origin/main", "origin/zebra/x", "/zebra/x", "zebra/x/", "zebra//x", "zebra/a//b",
               "zebra/x@{1}", "zebra/x;true", "zebra/$(true)", "zebra/x*", "zebra/x~1", "zebra/x^", "zebra/x:y",
               "zebra/x\\y", "zebra/x?", "zebra/x[", "zebra/x%41", "zebra/\u00e9", "-zebra", ".zebra", "zebra/" + "a" * 123,
               "x" * 129, "zebra/x\r", " zebra/x", "zebra/x "]
        for name in bad:
            r = self.validate("t1", json.dumps({"t1": name}))
            self.assertNotEqual(r.returncode, 0, repr(name))
            self.assertEqual(r.output_file, "", repr(name))
            shown, _ = self.public_text(r)
            self.assertNotIn("zebra", shown)
            self.assertNotIn("bad", shown.replace("bad pin", ""))
            for code in ("s01",):
                self.assertNotEqual(self.validate("t1", json.dumps({"t1": name}), code=code).returncode, 0)

    def test_good_resolved_names_are_accepted(self):
        import json
        for name in ("zebra/trial-1", "zebra/a.b_c-d", "zebra/feature/x", "zebra/" + "a" * 100, "x" * 128, "plain", "Zebra/X",
                     "z/y", "_x", "0abc"):
            r = self.validate("t1", json.dumps({"t1": name}))
            self.assertEqual(r.returncode, 0, (name, r.stdout + r.stderr))
            self.assertEqual(r.output_file, "asked=1\nbranch=%s\n" % name)
            self.assert_clean(r, name)

    def test_a_multiline_map_is_masked_on_its_first_line_and_still_leaks_nothing(self):
        r = self.validate("t1", '{\n "t1": "zebra/trial-9"\n}')
        shown, masks = self.public_text(r)
        self.assertNotIn("zebra", shown)
        self.assertNotIn("trial-9", shown)

    def test_validate_step_still_rejects_the_old_bad_inputs(self):
        self.assertNotEqual(self.validate("", SHA="g" * 40).returncode, 0)
        self.assertNotEqual(self.validate("", SUITE_CODE="x01").returncode, 0)
        self.assertNotEqual(self.validate("", PR="12x").returncode, 0)
        self.assertNotEqual(self.validate("", PRIVATE_TOKEN="").returncode, 0)
        self.assertNotEqual(self.validate("", PRIVATE_REPO="").returncode, 0)

    def test_no_pin_value_survives_into_the_environment_of_a_later_step(self):
        # the step output file is the only channel; nothing is exported or written to GITHUB_ENV
        for l in self.validate_run().splitlines():
            self.assertNotIn("GITHUB_ENV", l)
            self.assertNotIn("export", l)


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
    def test_every_workflow_is_covered_here(self):
        self.assertEqual(sorted(os.path.basename(p) for p in glob.glob(os.path.join(WF, "*.yml"))),
                         ["live-suite.yml", "poll.yml"])


if __name__ == "__main__":
    unittest.main()
