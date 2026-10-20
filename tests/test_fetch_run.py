import json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
TOKEN = "tok-synthetic-value"
GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def base_env():
    return {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/")}


def sh(args, cwd=None, env=None, check=True):
    e = dict(base_env(), **GIT)
    e.update(env or {})
    return subprocess.run(args, cwd=cwd, env=e, capture_output=True, text=True, check=check)


def make_origin(d):
    """A bare repo with three commits on main and one more commit on a side branch."""
    work = os.path.join(d, "w")
    bare = os.path.join(d, "origin.git")
    sh(["git", "init", "-q", "-b", "main", work])
    for n in range(3):
        with open(os.path.join(work, "f.txt"), "w") as f:
            f.write(str(n))
        sh(["git", "add", "."], cwd=work)
        sh(["git", "commit", "-q", "-m", "c%d" % n], cwd=work)
    sh(["git", "checkout", "-q", "-b", "side"], cwd=work)
    with open(os.path.join(work, "g.txt"), "w") as f:
        f.write("side")
    sh(["git", "add", "."], cwd=work)
    sh(["git", "commit", "-q", "-m", "side"], cwd=work)
    side = sh(["git", "rev-parse", "HEAD"], cwd=work).stdout.strip()
    main = sh(["git", "rev-parse", "main"], cwd=work).stdout.strip()
    sh(["git", "clone", "-q", "--bare", work, bare])
    return bare, main, side


def fetch(code, sha, bare, d):
    dest = os.path.join(d, "dest")
    out = os.path.join(d, "out")
    env = {"PRIVATE_REPO": "owner-x/repo-y", "PRIVATE_TOKEN": TOKEN, "SHA": sha, "OUT": out,
           "SUITE_CODE": code,
           "GIT_CONFIG_COUNT": "1",
           "GIT_CONFIG_KEY_0": "url.file://%s.insteadOf" % bare,
           "GIT_CONFIG_VALUE_0": "https://github.com/owner-x/repo-y.git"}
    r = sh(["bash", os.path.join(SCRIPTS, "fetch-private.sh"), dest], env=env, check=False)
    return r, dest


class Fetch(unittest.TestCase):
    def test_check_code_gets_all_branches_and_full_history(self):
        with tempfile.TemporaryDirectory() as d:
            bare, main, side = make_origin(d)
            r, dest = fetch("c05", side, bare, d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "commit fetched")
            self.assertEqual(sh(["git", "rev-parse", "HEAD"], cwd=dest).stdout.strip(), side)
            self.assertEqual(sh(["git", "rev-list", "--count", "--all"], cwd=dest).stdout.strip(), "4")
            refs = sh(["git", "for-each-ref", "--format=%(refname)"], cwd=dest).stdout.split()
            self.assertIn("refs/remotes/origin/main", refs)
            self.assertIn("refs/remotes/origin/side", refs)
            self.assertFalse(os.path.exists(os.path.join(dest, ".git", "shallow")))

    def test_every_check_code_does_the_same(self):
        for code in ("c01", "c02", "c03", "c04", "c05", "c06"):
            with tempfile.TemporaryDirectory() as d:
                bare, main, side = make_origin(d)
                r, dest = fetch(code, main, bare, d)
                self.assertEqual(r.returncode, 0, code)
                self.assertEqual(sh(["git", "rev-list", "--count", "--all"], cwd=dest).stdout.strip(), "4", code)

    def test_suite_code_stays_shallow(self):
        with tempfile.TemporaryDirectory() as d:
            bare, main, side = make_origin(d)
            r, dest = fetch("s01", main, bare, d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(sh(["git", "rev-parse", "HEAD"], cwd=dest).stdout.strip(), main)
            self.assertEqual(sh(["git", "rev-list", "--count", "--all"], cwd=dest).stdout.strip(), "1")

    def test_missing_commit_fails_without_details(self):
        with tempfile.TemporaryDirectory() as d:
            bare, main, side = make_origin(d)
            r, dest = fetch("c01", "f" * 40, bare, d)
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn(bare, r.stdout + r.stderr)
            self.assertNotIn("owner-x", r.stdout + r.stderr)
            self.assertNotIn(TOKEN, r.stdout + r.stderr)


def make_work(d, pr_files, main_files=None, extra_refs=None):
    """A checkout of 'the commit under test' (pr_files) whose origin/main has main_files.

    Files are {relative path: text}; a value ("link", target) makes a symlink. main_files None
    means there is no origin/main at all. extra_refs is {branch name: files}: more
    remote-tracking branches (refs/remotes/origin/<name>), each one commit on top of origin/main.
    """
    work = os.path.join(d, "w")
    sh(["git", "init", "-q", "-b", "main", work])

    def commit(files, msg):
        for name, text in files.items():
            path = os.path.join(work, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.lexists(path):
                os.remove(path)
            if isinstance(text, tuple):
                os.symlink(text[1], path)
                continue
            with open(path, "w") as f:
                f.write(text)
        sh(["git", "add", "-A"], cwd=work)
        sh(["git", "commit", "-q", "-m", msg, "--allow-empty"], cwd=work)
        return sh(["git", "rev-parse", "HEAD"], cwd=work).stdout.strip()

    if main_files is not None:
        base = commit(main_files, "main")
        sh(["git", "update-ref", "refs/remotes/origin/main", base], cwd=work)
        for name, files in (extra_refs or {}).items():
            sh(["git", "checkout", "-q", "--detach", base], cwd=work)
            tip = commit(files, "pin")
            sh(["git", "update-ref", "refs/remotes/origin/" + name, tip], cwd=work)
            sh(["git", "checkout", "-q", "main"], cwd=work)
    commit(pr_files, "pr")
    return work


GOOD_ENTRY = ('#!/usr/bin/env bash\necho main-entry > "$ENVDUMP.who"\ncp scripts/public-checks.sh "$ENVDUMP.checks"\n'
              'env | sort > "$ENVDUMP"\necho "$1" > "$ENVDUMP.code"\necho \'{}\' > "$RESULT_JSON"\n')
GOOD_CHECKS = "# checks from main\n"
EVIL_ENTRY = '#!/usr/bin/env bash\necho pr-entry > "$ENVDUMP.who"\necho \'{}\' > "$RESULT_JSON"\n'
MAIN = {"scripts/public-runner.sh": GOOD_ENTRY, "scripts/public-checks.sh": GOOD_CHECKS}
SECRET_ENV = ("RELEASE_TOKEN", "PRIVATE_TOKEN", "PRIVATE_REPO", "GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT",
              "GITHUB_STATE", "GITHUB_STEP_SUMMARY", "RUNNER_TEMP", "RUNNER_WORKSPACE", "GITHUB_WORKSPACE",
              "GITHUB_TOKEN", "ACTIONS_RUNTIME_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
              "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_CACHE_URL", "ACTIONS_RESULTS_URL")


class Helpers:
    def run_script(self, d, work, code, extra=None, drop_token=False):
        out = os.path.join(d, "out")
        env = {"PRIVATE_TOKEN": TOKEN, "PRIVATE_REPO": "owner-x/repo-y", "OUT": out,
               "ENVDUMP": os.path.join(d, "env.txt"), "GITHUB_ENV": "/nonexistent/env",
               "GITHUB_PATH": "/nonexistent/path", "GITHUB_OUTPUT": "/nonexistent/o",
               "GITHUB_STATE": "/nonexistent/s", "GITHUB_STEP_SUMMARY": "/nonexistent/sum",
               "RUNNER_TEMP": "/nonexistent/tmp", "RUNNER_WORKSPACE": "/nonexistent/rw",
               "GITHUB_WORKSPACE": "/nonexistent/gw", "GITHUB_TOKEN": "gh-" + TOKEN,
               "ACTIONS_RUNTIME_TOKEN": "rt-" + TOKEN, "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "id-" + TOKEN,
               "ACTIONS_ID_TOKEN_REQUEST_URL": "https://example.invalid/id",
               "ACTIONS_CACHE_URL": "https://example.invalid/c", "ACTIONS_RESULTS_URL": "https://example.invalid/r"}
        env.update(extra or {})
        return sh(["bash", os.path.join(SCRIPTS, "run-private.sh"), work, code], env=env, check=False), out

    def read(self, d, name):
        with open(os.path.join(d, name)) as f:
            return f.read()

    def assert_setup_not_a_result(self, d, r, out):
        self.assertEqual(r.returncode, 0)
        self.assertNotEqual(self.read(d, "out/exit_code").strip(), "0")
        self.assertFalse(os.path.exists(os.path.join(d, "env.txt.who")), "no entry script may run")
        status = os.path.join(d, "status.json")
        r2 = subprocess.run([sys.executable, "-I", os.path.join(SCRIPTS, "sanitize.py"),
                             os.path.join(out, "result.json"), os.path.join(out, "exit_code"), status],
                            capture_output=True, text=True)
        self.assertEqual(r2.returncode, 0)
        with open(status) as f:
            st = json.load(f)
        self.assertEqual((st["state"], st["description"]), ("error", "NOT A RESULT (setup)"))
        self.assertNotIn(TOKEN, r.stdout + r.stderr)


class Run(Helpers, unittest.TestCase):
    def run_entry(self, code):
        with tempfile.TemporaryDirectory() as d:
            work = make_work(d, MAIN, MAIN)
            r, out = self.run_script(d, work, code)
            return (r, self.read(d, "env.txt"), self.read(d, "env.txt.code").strip(),
                    self.read(d, "out/exit_code").strip())

    def test_check_code_entry_gets_no_token_at_all(self):
        r, dumped, got, rc = self.run_entry("c03")
        self.assertEqual((r.returncode, got, rc), (0, "c03", "0"))
        self.assertFalse(TOKEN in dumped)
        for name in SECRET_ENV:
            self.assertFalse(("\n" + name + "=") in ("\n" + dumped), name)
        self.assertTrue("RESULT_JSON=" in dumped)
        self.assertFalse(TOKEN in r.stdout + r.stderr)

    def test_suite_code_entry_still_gets_the_release_token(self):
        r, dumped, got, rc = self.run_entry("s01")
        self.assertEqual((r.returncode, got, rc), (0, "s01", "0"))
        self.assertTrue(("\nRELEASE_TOKEN=" + TOKEN + "\n") in ("\n" + dumped))

    def test_check_code_does_not_need_the_token_in_the_environment(self):
        with tempfile.TemporaryDirectory() as d:
            work = make_work(d, MAIN, MAIN)
            env = dict(base_env(), OUT=os.path.join(d, "out"), ENVDUMP=os.path.join(d, "env.txt"))
            r = subprocess.run(["bash", os.path.join(SCRIPTS, "run-private.sh"), work, "c01"],
                               env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            r = subprocess.run(["bash", os.path.join(SCRIPTS, "run-private.sh"), work, "s01"],
                               env=env, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)  # a suite code still needs it

    def test_missing_entry_script_for_a_suite(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(base_env(), PRIVATE_TOKEN=TOKEN, OUT=os.path.join(d, "out"))
            r = subprocess.run(["bash", os.path.join(SCRIPTS, "run-private.sh"), d, "s01"],
                               env=env, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(self.read(d, "out/exit_code").strip(), "127")

    # Pinned entry scripts: the commit under test cannot supply the script that runs it.
    def test_check_code_runs_the_origin_main_entry_not_the_checkouts(self):
        with tempfile.TemporaryDirectory() as d:
            pr = {"scripts/public-runner.sh": EVIL_ENTRY, "scripts/public-checks.sh": "# evil\n"}
            work = make_work(d, pr, MAIN)
            r, out = self.run_script(d, work, "c02")
            self.assertEqual(self.read(d, "env.txt.who").strip(), "main-entry")
            self.assertEqual(self.read(d, "env.txt.checks"), GOOD_CHECKS)
            self.assertEqual(self.read(d, "env.txt.code").strip(), "c02")
            self.assertEqual(self.read(d, "out/exit_code").strip(), "0")

    def test_every_check_code_is_pinned(self):
        for code in ("c01", "c02", "c03", "c04", "c05", "c06"):
            with tempfile.TemporaryDirectory() as d:
                work = make_work(d, {"scripts/public-runner.sh": EVIL_ENTRY, "scripts/public-checks.sh": "x"}, MAIN)
                self.run_script(d, work, code)
                self.assertEqual(self.read(d, "env.txt.who").strip(), "main-entry", code)

    def test_pin_supplies_scripts_the_commit_does_not_have(self):
        with tempfile.TemporaryDirectory() as d:
            work = make_work(d, {"other.txt": "x"}, MAIN)
            self.run_script(d, work, "c01")
            self.assertEqual(self.read(d, "env.txt.who").strip(), "main-entry")

    def test_dirty_worktree_changes_are_overwritten_too(self):
        with tempfile.TemporaryDirectory() as d:
            work = make_work(d, MAIN, MAIN)
            with open(os.path.join(work, "scripts", "public-runner.sh"), "w") as f:
                f.write(EVIL_ENTRY)
            self.run_script(d, work, "c01")
            self.assertEqual(self.read(d, "env.txt.who").strip(), "main-entry")

    def test_symlinked_scripts_cannot_redirect_the_pin(self):
        with tempfile.TemporaryDirectory() as d:
            elsewhere = os.path.join(d, "elsewhere")
            os.makedirs(elsewhere)
            with open(os.path.join(elsewhere, "public-runner.sh"), "w") as f:
                f.write(EVIL_ENTRY)
            with open(os.path.join(elsewhere, "public-checks.sh"), "w") as f:
                f.write("# evil\n")
            work = make_work(d, {"x": "1"}, MAIN)
            sh(["git", "rm", "-rq", "scripts"], cwd=work)
            os.symlink(elsewhere, os.path.join(work, "scripts"))
            sh(["git", "add", "-A"], cwd=work)
            sh(["git", "commit", "-q", "-m", "symlink"], cwd=work)
            self.run_script(d, work, "c01")
            who = os.path.join(d, "env.txt.who")
            self.assertNotEqual(self.read(d, "env.txt.who").strip() if os.path.exists(who) else "", "pr-entry")
            with open(os.path.join(elsewhere, "public-runner.sh")) as f:
                self.assertEqual(f.read(), EVIL_ENTRY)  # nothing was written through the link

    def test_symlinked_entry_file_cannot_redirect_the_pin(self):
        with tempfile.TemporaryDirectory() as d:
            work = make_work(d, {"x": "1"}, MAIN)
            target = os.path.join(d, "evil.sh")
            with open(target, "w") as f:
                f.write(EVIL_ENTRY)
            os.remove(os.path.join(work, "scripts", "public-runner.sh"))
            os.symlink(target, os.path.join(work, "scripts", "public-runner.sh"))
            self.run_script(d, work, "c01")
            self.assertEqual(self.read(d, "env.txt.who").strip(), "main-entry")
            with open(target) as f:
                self.assertEqual(f.read(), EVIL_ENTRY)

    def test_origin_main_without_the_scripts_is_a_setup_failure(self):
        with tempfile.TemporaryDirectory() as d:
            # The commit under test has working-looking scripts; origin/main has none yet.
            work = make_work(d, {"scripts/public-runner.sh": EVIL_ENTRY, "scripts/public-checks.sh": "x"},
                             {"README": "x"})
            r, out = self.run_script(d, work, "c01")
            self.assert_setup_not_a_result(d, r, out)

    def test_origin_main_with_only_one_of_the_scripts_is_a_setup_failure(self):
        for only in ("scripts/public-runner.sh", "scripts/public-checks.sh"):
            with tempfile.TemporaryDirectory() as d:
                work = make_work(d, {"scripts/public-runner.sh": EVIL_ENTRY, "scripts/public-checks.sh": "x"},
                                 {only: MAIN[only]})
                r, out = self.run_script(d, work, "c04")
                self.assert_setup_not_a_result(d, r, out)

    def test_no_origin_main_at_all_is_a_setup_failure(self):
        with tempfile.TemporaryDirectory() as d:
            work = make_work(d, {"scripts/public-runner.sh": EVIL_ENTRY, "scripts/public-checks.sh": "x"})
            r, out = self.run_script(d, work, "c06")
            self.assert_setup_not_a_result(d, r, out)

    def test_not_a_git_checkout_is_a_setup_failure(self):
        with tempfile.TemporaryDirectory() as d:
            work = os.path.join(d, "w")
            os.makedirs(os.path.join(work, "scripts"))
            for n in ("public-runner.sh", "public-checks.sh"):
                with open(os.path.join(work, "scripts", n), "w") as f:
                    f.write(EVIL_ENTRY)
            r, out = self.run_script(d, work, "c01")
            self.assert_setup_not_a_result(d, r, out)

    def test_stale_result_from_earlier_is_removed(self):
        with tempfile.TemporaryDirectory() as d:
            work = make_work(d, MAIN, {"README": "x"})
            os.makedirs(os.path.join(d, "out"))
            with open(os.path.join(d, "out", "result.json"), "w") as f:
                json.dump({"passed": 9, "failed": 0, "skipped": 0, "errors": 0, "counted": True, "reason": "ok"}, f)
            r, out = self.run_script(d, work, "c01")
            self.assert_setup_not_a_result(d, r, out)

    def test_suite_codes_are_not_pinned(self):
        with tempfile.TemporaryDirectory() as d:
            work = make_work(d, {"scripts/public-runner.sh": EVIL_ENTRY}, {"README": "x"})
            self.run_script(d, work, "s05")
            self.assertEqual(self.read(d, "env.txt.who").strip(), "pr-entry")


PREFIX = "pin/"  # stands in for the repository variable that holds the allowed branch prefix
PIN_ENTRY = ('#!/usr/bin/env bash\necho pinned-entry > "$ENVDUMP.who"\ncp scripts/public-checks.sh "$ENVDUMP.checks"\n'
             'env | sort > "$ENVDUMP"\necho "$1" > "$ENVDUMP.code"\necho \'{}\' > "$RESULT_JSON"\n')
PIN_CHECKS = "# checks from the pinned branch\n"
PIN = {"scripts/public-runner.sh": PIN_ENTRY, "scripts/public-checks.sh": PIN_CHECKS}
BAD_REFS = ["pin/../main", "pin/a/../b", "pin/a..b", "pin/a b", "pin/a\tb", "pin/a\nb", "main", "origin/main",
            "refs/heads/pin/x", "refs/remotes/origin/pin/x", "other/x", "Pin/x", "pin", "pin/", "/pin/x",
            "pin/x/", "pin//x", "pin/a//b", "pin/x//", "pin/x@{1}", "pin/x@{u}", "pin/x;true", "pin/$(true)", "pin/x*",
            "pin/x?", "pin/x~1", "pin/x^", "pin/x:y", "pin/x\\y", "pin/" + "a" * 101, "origin/pin/x", "../pin/x",
            "pin/\u00e9"]


class PinnedBranch(Helpers, unittest.TestCase):
    """TRIAL-ONLY: check codes may take their entry scripts from a named private branch."""

    def run_pinned(self, code, ref, extra_env=None, refs=None, pr=None, main=MAIN, prefix=PREFIX):
        with tempfile.TemporaryDirectory() as d:
            work = make_work(d, pr or {"scripts/public-runner.sh": EVIL_ENTRY, "scripts/public-checks.sh": "x"},
                             main, refs if refs is not None else {"pin/trial-1": PIN})
            env = {} if ref is None else {"PIN_REF": ref}
            if prefix is not None:
                env["PIN_PREFIX"] = prefix
            env.update(extra_env or {})
            r, out = self.run_script(d, work, code, extra=env)
            seen = {n: self.read(d, n) if os.path.exists(os.path.join(d, n)) else None
                    for n in ("env.txt", "env.txt.who", "env.txt.checks", "env.txt.code")}
            seen["exit_code"] = self.read(d, "out/exit_code").strip()
            runner = os.path.join(work, "scripts", "public-runner.sh")
            seen["runner"] = self.read(work, "scripts/public-runner.sh") if os.path.exists(runner) else None
            logs = r.stdout + r.stderr + self.read(d, "out/run.log") + self.read(d, "out/exit_code")
            if os.path.exists(os.path.join(out, "result.json")):
                logs += self.read(d, "out/result.json")
            return d, work, r, out, seen, logs

    def setup_failure(self, code, ref, **kw):
        with tempfile.TemporaryDirectory() as d:
            work = make_work(d, {"scripts/public-runner.sh": EVIL_ENTRY, "scripts/public-checks.sh": "x"},
                             kw.get("main", MAIN), kw.get("refs", {"pin/trial-1": PIN}))
            env = {} if ref is None else {"PIN_REF": ref}
            if kw.get("prefix", PREFIX) is not None:
                env["PIN_PREFIX"] = kw.get("prefix", PREFIX)
            r, out = self.run_script(d, work, code, extra=env)
            self.assert_setup_not_a_result(d, r, out)
            logs = r.stdout + r.stderr + self.read(d, "out/run.log")
            if ref and ref not in ("pin", "pin/"):  # those are substrings of the fixed words
                self.assertNotIn(ref, logs)
            self.assertFalse(os.path.exists(os.path.join(d, "env.txt.checks")))
            if kw.get("why"):  # which check stopped it: the ref itself, or the branch lookup
                self.assertIn(kw["why"], logs)

    def test_default_without_a_pin_is_unchanged(self):
        for ref in (None, ""):
            _, _, r, out, seen, _ = self.run_pinned("c02", ref)
            self.assertEqual(seen["env.txt.who"].strip(), "main-entry", ref)
            self.assertEqual(seen["env.txt.checks"], GOOD_CHECKS)

    def test_empty_pin_with_no_prefix_configured_is_unchanged(self):
        _, _, r, out, seen, _ = self.run_pinned("c02", "", prefix=None)
        self.assertEqual(seen["env.txt.who"].strip(), "main-entry")
        _, _, r, out, seen, _ = self.run_pinned("c02", None, prefix="")
        self.assertEqual(seen["env.txt.who"].strip(), "main-entry")

    def test_valid_pin_runs_the_pinned_branch_scripts(self):
        _, _, r, out, seen, _ = self.run_pinned("c03", "pin/trial-1")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(seen["env.txt.who"].strip(), "pinned-entry")
        self.assertEqual(seen["env.txt.checks"], PIN_CHECKS)
        self.assertEqual(seen["env.txt.code"].strip(), "c03")

    def test_every_check_code_honours_the_pin(self):
        for code in ("c01", "c02", "c03", "c04", "c05", "c06"):
            _, _, r, out, seen, _ = self.run_pinned(code, "pin/trial-1")
            self.assertEqual(seen["env.txt.who"].strip(), "pinned-entry", code)

    def test_pin_names_with_dots_dashes_slashes_are_accepted(self):
        for name in ("pin/a.b-c_d", "pin/feature/x-1.2", "pin/" + "a" * 100):
            _, _, r, out, seen, _ = self.run_pinned("c01", name, refs={name: PIN})
            self.assertEqual(seen["env.txt.who"].strip(), "pinned-entry", name)

    def test_pin_does_not_fall_back_to_main_or_the_commit(self):
        # main has different scripts and the commit has evil ones; only the pinned ones may run.
        _, work, r, out, seen, _ = self.run_pinned("c01", "pin/trial-1")
        self.assertEqual(seen["env.txt.who"].strip(), "pinned-entry")
        self.assertEqual(seen["runner"], PIN_ENTRY)

    def test_pinned_entry_gets_no_token_and_no_pin_variables(self):
        _, _, r, out, seen, logs = self.run_pinned("c01", "pin/trial-1")
        dumped = seen["env.txt"]
        self.assertNotIn(TOKEN, dumped)
        for name in SECRET_ENV + ("PIN_REF", "PIN_PREFIX"):
            self.assertFalse(("\n" + name + "=") in ("\n" + dumped), name)

    def test_ref_is_never_printed(self):
        _, _, r, out, seen, logs = self.run_pinned("c01", "pin/trial-1")
        self.assertNotIn("trial-1", logs)
        self.assertEqual(r.returncode, 0)
        self.setup_failure("c01", "pin/missing-branch")
        self.setup_failure("c01", "pin/../main")

    def test_invalid_refs_are_rejected_as_setup_failures(self):
        for ref in BAD_REFS:
            # make the dangerous branches exist where git allows, so only the check can stop them
            self.setup_failure("c01", ref, refs={"pin/trial-1": PIN}, why="from a valid pinned branch")

    def test_invalid_ref_never_falls_back_to_main(self):
        d, work, r, out, seen, _ = self.run_pinned("c05", "main")
        self.assertIsNone(seen["env.txt.who"])
        self.assertEqual(seen["exit_code"], "1")

    def test_bare_main_is_rejected_even_if_the_prefix_is_empty(self):
        self.setup_failure("c01", "main", prefix="", why="from a valid pinned branch")
        self.setup_failure("c01", "main", prefix=None, why="from a valid pinned branch")

    def test_missing_branch_is_a_setup_failure(self):
        self.setup_failure("c01", "pin/not-there", why="from the pinned branch")

    def test_unset_or_bad_prefix_rejects_every_ref(self):
        for prefix in (None, "", "pin", "/", "pin/../", "../", "pin//", "a/b/", "-x/", "pin/ ", " pin/", "pin/\n",
                       "p" * 31 + "/", "pi n/", "pin/*"):
            self.setup_failure("c01", "pin/trial-1", prefix=prefix, why="from a valid pinned branch")

    def test_pinned_branch_missing_a_script_is_a_setup_failure(self):
        for only in ("scripts/public-runner.sh", "scripts/public-checks.sh"):
            self.setup_failure("c01", "pin/trial-1", refs={"pin/trial-1": {only: PIN[only]}},
                               main={only: PIN[only]})

    def test_pinned_branch_that_deleted_a_script_is_a_setup_failure(self):
        with tempfile.TemporaryDirectory() as d:
            work = make_work(d, MAIN, MAIN, {"pin/gone": {"other.txt": "x"}})
            sh(["git", "checkout", "-q", "--detach", "refs/remotes/origin/pin/gone"], cwd=work)
            sh(["git", "rm", "-q", "scripts/public-checks.sh"], cwd=work)
            sh(["git", "commit", "-q", "-m", "del"], cwd=work)
            sh(["git", "update-ref", "refs/remotes/origin/pin/gone", "HEAD"], cwd=work)
            sh(["git", "checkout", "-q", "main"], cwd=work)
            r, out = self.run_script(d, work, "c01", extra={"PIN_REF": "pin/gone", "PIN_PREFIX": PREFIX})
            self.assert_setup_not_a_result(d, r, out)

    def test_symlinked_script_on_the_pinned_branch_is_a_setup_failure(self):
        evil = {"scripts/public-runner.sh": ("link", "/nonexistent/evil.sh"), "scripts/public-checks.sh": "x"}
        self.setup_failure("c01", "pin/trial-1", refs={"pin/trial-1": evil})

    def test_suite_codes_ignore_the_pin(self):
        for ref in ("pin/trial-1", "pin/missing", "main", "pin/../x", ""):
            _, _, r, out, seen, logs = self.run_pinned(
                "s05", ref, pr={"scripts/public-runner.sh": EVIL_ENTRY}, main={"README": "x"})
            self.assertEqual(seen["env.txt.who"].strip(), "pr-entry", ref)
            self.assertEqual(r.returncode, 0)

    def test_pin_without_the_pin_variables_from_the_environment_uses_main(self):
        _, _, r, out, seen, _ = self.run_pinned("c01", None, prefix=None)
        self.assertEqual(seen["env.txt.who"].strip(), "main-entry")


if __name__ == "__main__":
    unittest.main()
