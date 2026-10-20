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
              "GITHUB_STATE", "GITHUB_STEP_SUMMARY", "RUNNER_WORKSPACE", "GITHUB_WORKSPACE",
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

    # RUNNER_TEMP: the real private entry script requires it (": ${RUNNER_TEMP:?}"). A check code gets a fresh private
    # directory, never the runner's own _temp (which holds the file-command files), and it is removed afterwards.
    def temp_probe_entry(self, rc=0):
        return ('#!/usr/bin/env bash\nset -u\n'
                'echo "$RUNNER_TEMP" > "$ENVDUMP.rt"\n'
                '[ -d "$RUNNER_TEMP" ] && echo yes > "$ENVDUMP.isdir"\n'
                'stat -c %%a "$RUNNER_TEMP" > "$ENVDUMP.mode"\n'
                'ls -A "$RUNNER_TEMP" > "$ENVDUMP.ls"\n'
                'echo scratch > "$RUNNER_TEMP/step1.log"\n'
                'env | sort > "$ENVDUMP"\n'
                'echo \'{}\' > "$RESULT_JSON"\nexit %d\n' % rc)

    def run_probe(self, code, rc=0, extra=None):
        with tempfile.TemporaryDirectory() as d:
            files = {"scripts/public-runner.sh": self.temp_probe_entry(rc), "scripts/public-checks.sh": GOOD_CHECKS}
            work = make_work(d, files, files)
            r, out = self.run_script(d, work, code, extra=extra)
            rt = self.read(d, "env.txt.rt").strip() if os.path.exists(os.path.join(d, "env.txt.rt")) else None
            info = {"r": r, "rt": rt, "exit": self.read(d, "out/exit_code").strip()}
            for k in ("isdir", "mode", "ls"):
                path = os.path.join(d, "env.txt." + k)
                info[k] = self.read(d, "env.txt." + k).strip() if os.path.exists(path) else None
            info["env"] = self.read(d, "env.txt")
            info["after"] = os.path.lexists(rt) if rt else None
            return info

    def test_check_code_gets_a_fresh_private_runner_temp(self):
        for code in ("c01", "c02", "c03", "c04", "c05", "c06"):
            i = self.run_probe(code)
            self.assertEqual((i["r"].returncode, i["exit"]), (0, "0"), code)
            self.assertTrue(i["rt"] and os.path.isabs(i["rt"]), code)
            self.assertNotEqual(i["rt"], "/nonexistent/tmp", code)
            self.assertEqual(i["isdir"], "yes", code)
            self.assertEqual(i["mode"], "700", code)
            self.assertEqual(i["ls"], "", code)  # empty at start: no set_env / add_path / file-command files
            self.assertFalse(i["after"], code + ": removed afterwards")
            self.assertNotIn("RUNNER_TEMP=/nonexistent/tmp", i["env"].split("\n"))
            self.assertNotIn("GITHUB_ENV", i["env"])
            self.assertNotIn("RUNNER_WORKSPACE", i["env"])
            self.assertNotIn("GITHUB_WORKSPACE", i["env"])

    def test_check_code_never_gets_the_real_runner_temp_or_its_file_commands(self):
        with tempfile.TemporaryDirectory() as real:
            os.makedirs(os.path.join(real, "_runner_file_commands"))
            for n in ("set_env_123", "add_path_123", "save_state_123"):
                with open(os.path.join(real, "_runner_file_commands", n), "w") as f:
                    f.write("x=1\n")
            i = self.run_probe("c04", extra={"RUNNER_TEMP": real})
            self.assertNotEqual(i["rt"], real)
            self.assertFalse(i["rt"].startswith(real + os.sep))
            self.assertEqual(i["ls"], "")
            self.assertFalse(i["after"])
            self.assertEqual(sorted(os.listdir(real)), ["_runner_file_commands"])  # untouched
            self.assertEqual(len(os.listdir(os.path.join(real, "_runner_file_commands"))), 3)

    def test_check_code_works_when_the_runner_temp_was_never_set(self):
        with tempfile.TemporaryDirectory() as d:
            files = {"scripts/public-runner.sh": self.temp_probe_entry(), "scripts/public-checks.sh": GOOD_CHECKS}
            work = make_work(d, files, files)
            env = dict(base_env(), OUT=os.path.join(d, "out"), ENVDUMP=os.path.join(d, "env.txt"))
            r = subprocess.run(["bash", os.path.join(SCRIPTS, "run-private.sh"), work, "c04"],
                               env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(self.read(d, "out/exit_code").strip(), "0")
            self.assertFalse(os.path.lexists(self.read(d, "env.txt.rt").strip()))

    def test_runner_temp_is_removed_when_the_entry_fails(self):
        i = self.run_probe("c02", rc=3)
        self.assertEqual(i["exit"], "3")
        self.assertTrue(i["rt"])
        self.assertFalse(i["after"])

    def test_simultaneous_check_codes_get_different_temp_dirs(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            files = {"scripts/public-runner.sh": self.temp_probe_entry(), "scripts/public-checks.sh": GOOD_CHECKS}
            works = [make_work(d1, files, files), make_work(d2, files, files)]
            procs = []
            for d, w in ((d1, works[0]), (d2, works[1])):
                env = dict(base_env(), OUT=os.path.join(d, "out"), ENVDUMP=os.path.join(d, "env.txt"))
                procs.append(subprocess.Popen(["bash", os.path.join(SCRIPTS, "run-private.sh"), w, "c05"],
                                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
            for p in procs:
                p.communicate()
                self.assertEqual(p.returncode, 0)
            a, b = (self.read(d, "env.txt.rt").strip() for d in (d1, d2))
            self.assertNotEqual(a, b)
            self.assertFalse(os.path.lexists(a) or os.path.lexists(b))

    def test_setup_failure_creates_no_temp_dir(self):
        import glob
        before = set(glob.glob("/tmp/trs-private.*"))
        with tempfile.TemporaryDirectory() as d:
            work = make_work(d, MAIN, None)  # no origin/main: setup failure
            r, out = self.run_script(d, work, "c01")
            self.assertFalse(os.path.exists(os.path.join(d, "env.txt.who")))
        self.assertEqual(set(glob.glob("/tmp/trs-private.*")), before)

    def test_suite_code_runner_temp_is_unchanged(self):
        r, dumped, got, rc = self.run_entry("s01")
        self.assertTrue(("\nRUNNER_TEMP=/nonexistent/tmp\n") in ("\n" + dumped))
        self.assertTrue(("\nRUNNER_WORKSPACE=/nonexistent/rw\n") in ("\n" + dumped))
        self.assertTrue(("\nGITHUB_WORKSPACE=/nonexistent/gw\n") in ("\n" + dumped))
        self.assertTrue(("\nGITHUB_ENV=/nonexistent/env\n") in ("\n" + dumped))

    def test_check_code_env_differs_from_suite_code_only_as_documented(self):
        # the variables a check code lacks compared with an s code are exactly the scrubbed ones; RUNNER_TEMP is
        # present for both (a private directory for the check code), and nothing else is added or lost
        _, c_dump, _, _ = self.run_entry("c01")
        _, s_dump, _, _ = self.run_entry("s01")
        names = lambda t: {l.split("=", 1)[0] for l in t.splitlines() if "=" in l}
        missing = names(s_dump) - names(c_dump)
        self.assertEqual(missing, set(SECRET_ENV))
        self.assertIn("RUNNER_TEMP", names(c_dump))
        self.assertEqual(names(c_dump) - names(s_dump), set())

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


if __name__ == "__main__":
    unittest.main()
