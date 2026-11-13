"""The spine of `deliver`, end to end over a throwaway repository: prepare cuts the
branch and reads the baseline; the writer's work is simulated on disk; conclude proves
by mutation, compares the suite by name, judges, commits with a pathspec and hands the
next command. A second run shows the other side: a test that does not prove the guard
survives the mutation, and the delivery is blocked with nothing committed.

The repository, its suite and its profile are built here, in a temporary directory, with
a configuration of their own — the way an installation would have them. Nothing in this
file names a stack: the runner is three lines of Python so the output can carry the two
lines the profile's patterns read.
"""
import argparse
import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

import jsonschema

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import common
import deliver

PRUMO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

RUNNER = textwrap.dedent('''\
    import sys, unittest
    suite = unittest.defaultTestLoader.discover("tests")
    result = unittest.TextTestRunner(stream=open("/dev/null", "w")).run(suite)
    print("%d / %d (100%%)" % (result.testsRun, suite.countTestCases()))
    for case, _ in result.failures + result.errors:
        print("FAIL: " + case.id().split(".")[-1])
        print("trace " * 4000)
    print("Ran %d of %d" % (result.testsRun, suite.countTestCases()))
    ''')

SOURCE = "def accept(x):\n    if x >= 10:\n        return False\n    return True\n"
BUGGY = "def accept(x):\n    if x > 10:\n        return False\n    return True\n"
LINT = "import sys, pathlib\nbad = [p for p in pathlib.Path('tests').glob('*.py') if 'LINT_BAD' in p.read_text()]\nprint('FAIL ' + ', '.join(map(str, bad)) if bad else 'PASS')\nsys.exit(1 if bad else 0)\n"
EXISTING_TEST = textwrap.dedent('''\
    import sys, unittest
    sys.path.insert(0, "src")
    from a import accept

    class T(unittest.TestCase):
        def test_accept_small(self):
            self.assertTrue(accept(1))
    ''')
PROVING_TEST = textwrap.dedent('''\
    import sys, unittest
    sys.path.insert(0, "src")
    from a import accept

    class G(unittest.TestCase):
        def test_guard_refuses(self):
            self.assertFalse(accept(10))
    ''')
WEAK_TEST = PROVING_TEST.replace("self.assertFalse(accept(10))", "self.assertTrue(accept(1))")

PROFILE = {
    "profile": "repo-x", "version": 1, "status": "draft",
    "stack": {"family": "other", "manifests": []}, "conventions": [],
    "proof_registry": {"type": "absent", "root": None, "note": None},
    "base": {"branch": "main"}, "living_docs": [],
    "checks": {**{r: {"command": None, "required": False}
                  for r in ("secrets", "static", "dependencies")},
               "lint": {"command": "python3 lint.py", "required": True,
                        "failure_pattern": r"(?m)^FAIL"},
               "suite": {"command": "python3 run_tests.py", "required": True,
                         "red_name_pattern": r"(?m)^FAIL: (\S+)",
                         "progress_pattern": r"(?m)^Ran (\d+) of (\d+)"}},
    "sensitive_data": {"fields": [], "forbidden_destinations": []},
    "size": {"ideal": 100, "acceptable": 300, "split": 1000, "file_ceiling": 1000},
    "anchors": {}, "evals": [],
    "delivery": {"branch_format": "{item}-{slug}", "commit_format": "{item} - {message}",
                 "worktree": False, "budget": {"max_files": 2, "max_lines": 60},
                 "source_roots": ["src"], "test_roots": ["tests"],
                 "mutation": {"command": None, "note": None}, "hotfix_base": "main",
                 "kinds": ["coverage-gap", "behaviour-change", "refactor"]},
}

MEASURE = {
    "premises": [{"text": "the guard exists", "state": "holds",
                  "evidence": "src/a.py:2", "central": True}],
    "files": ["tests/test_guard.py"],
    "mutations": [{"file": "src/a.py", "line": 2, "from": ">=", "to": ">",
                   "test": "test_guard_refuses"}],
}


def git(cwd, *args):
    subprocess.run(("git", *args), cwd=cwd, check=True, capture_output=True, text=True)


class FileStandsFor(unittest.TestCase):
    """A test file that cannot load is reported under the file's name, in each runner's
    own spelling; every spelling stands for the tests inside it."""

    FILES = ["apps/web/test/app.test.ts", "tests/test_guard.py"]

    def test_a_module_name_or_a_relative_path_stands_for_the_file(self):
        for name in ("test_guard", "tests.test_guard", "test/app.test.ts", "apps/web/test/app.test.ts"):
            self.assertTrue(deliver.file_stands_for(name, self.FILES), name)

    def test_another_file_or_a_test_inside_it_does_not(self):
        for name in ("test/other.test.ts", "pp.test.ts", "test_guard_refuses", "test/app.test.ts > mural > x"):
            self.assertFalse(deliver.file_stands_for(name, self.FILES), name)


class SameTest(unittest.TestCase):
    """The measure names a test from the repository root; the runner prints it from
    where it ran. The title must match whole; only the file's root may differ."""

    def test_a_path_root_does_not_hide_the_same_test(self):
        self.assertTrue(deliver.same_test("packages/kit/test/a.test.ts > g > t", "test/a.test.ts > g > t"))
        self.assertTrue(deliver.same_test("a.test.ts > g > t", "test/a.test.ts > g > t"))
        self.assertTrue(deliver.same_test("test_guard_refuses", "test_guard_refuses"))

    def test_another_title_or_another_file_is_another_test(self):
        self.assertFalse(deliver.same_test("packages/kit/test/a.test.ts > g > t", "test/a.test.ts > g > u"))
        self.assertFalse(deliver.same_test("packages/kit/test/a.test.ts > g > t", "test/b.test.ts > g > t"))
        self.assertFalse(deliver.same_test("packages/kit/test/a.test.ts > g > t", "pa.test.ts > g > t"))


class Fixture(unittest.TestCase):
    """The throwaway repository, its profile and the two commands, shared by the
    spine in the checkout and the spine in a worktree."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name).resolve()  # macOS: /var is /private/var
        self.repo = root / "repositories" / "repo-x"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "tests").mkdir()
        (self.repo / "src" / "a.py").write_text(SOURCE, encoding="utf-8")
        (self.repo / "tests" / "test_a.py").write_text(EXISTING_TEST, encoding="utf-8")
        (self.repo / "run_tests.py").write_text(RUNNER, encoding="utf-8")
        (self.repo / "lint.py").write_text(LINT, encoding="utf-8")
        (self.repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "user.email", "t@t")
        (root / "profiles").mkdir()
        import yaml
        (root / "profiles" / "repo-x.yml").write_text(yaml.safe_dump(PROFILE), encoding="utf-8")
        (root / "prumo.yml").write_text(textwrap.dedent('''\
            prumo: 1
            language: en
            paths:
              repositories: ./repositories
              profiles: ./profiles
              reports: ./reports
              packages: ./packages
            tracker:
              type: businessmap
              url: "https://example.invalid/cards/{id}"
            forge:
              type: gitlab
            '''), encoding="utf-8")
        (root / "item.md").write_text("# prove the refusal above ten\n\n`accept` refuses 10 and nobody tests it.\n",
                                      encoding="utf-8")
        self.root = root
        self.env = dict(os.environ)
        os.environ["PRUMO_CONFIG"] = str(root / "prumo.yml")
        os.environ["CLAUDE_PROJECT_DIR"] = str(root)
        common.cfg.cache_clear()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        common.cfg.cache_clear()
        self.tmp.cleanup()

    def prepare(self, kind=None, fix=None, package="pkg", shape="debt"):
        args = argparse.Namespace(repo="repo-x", item=None if fix else "ITEM-1",
                                  item_file=str(self.root / "item.md"), kind=kind,
                                  branch=None, package=str(self.root / package), fix=fix,
                                  shape=None if fix else shape, type=None)
        with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(deliver.prepare(args), 0)
        return self.root / package

    def conclude(self, package, test_text, measure_data=MEASURE, source=None, declared=None):
        # the writer writes where the branch was cut: the checkout, or its worktree
        tree = pathlib.Path(json.loads((package / "deliver-manifest.json")
                                       .read_text(encoding="utf-8"))["workdir"])
        (tree / "tests" / "test_guard.py").write_text(test_text, encoding="utf-8")
        if source is not None:
            (tree / "src" / "a.py").write_text(source, encoding="utf-8")
        measure, written = package / "m.json", package / "w.json"
        measure.write_text(json.dumps(measure_data), encoding="utf-8")
        written.write_text(json.dumps({"files": list(measure_data["files"]) if declared is None else declared,
                                       "skipped": ["the boundary at 9"]}), encoding="utf-8")
        args = argparse.Namespace(package=str(package), measure=str(measure),
                                  written=str(written), refutation=None)
        with contextlib.redirect_stdout(io.StringIO()):
            code = deliver.conclude(args)
        return code, json.loads((package / "delivery.json").read_text(encoding="utf-8"))



class Spine(Fixture):
    def test_prepare_cuts_the_branch_reads_the_baseline_and_marks(self):
        package = self.prepare()
        man = json.loads((package / "deliver-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(man["branch"]["name"], "ITEM-1-prove-the-refusal-above-ten")
        self.assertEqual(man["baseline"], {"base_reds": [], "predicted": 1, "executed": 1})
        head = subprocess.run(("git", "branch", "--show-current"), cwd=self.repo,
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(head, man["branch"]["name"])
        self.assertTrue(deliver.markers())
        self.assertEqual(json.loads(deliver.markers()[0].read_text())["worktree"], str(self.repo))

    def test_a_dirty_tree_is_a_stop(self):
        (self.repo / "src" / "a.py").write_text(SOURCE + "# pending\n", encoding="utf-8")
        with self.assertRaises(common.UsageError), contextlib.redirect_stdout(io.StringIO()):
            self.prepare()

    def test_a_second_prepare_never_discards_the_commits_a_branch_holds(self):
        package = self.prepare()
        code, rec = self.conclude(package, PROVING_TEST)
        self.assertEqual(code, 0, rec)
        name = json.loads((package / "deliver-manifest.json").read_text(encoding="utf-8"))["branch"]["name"]
        before = subprocess.run(("git", "rev-parse", name), cwd=self.repo,
                                capture_output=True, text=True).stdout.strip()
        git(self.repo, "checkout", "-q", "main")
        deliver.unmark()
        with self.assertRaises(common.UsageError) as cm, contextlib.redirect_stdout(io.StringIO()):
            self.prepare(package="pkg2")
        self.assertIn(name, str(cm.exception))
        after = subprocess.run(("git", "rev-parse", name), cwd=self.repo,
                               capture_output=True, text=True).stdout.strip()
        self.assertEqual(after, before)

    def test_a_generated_file_is_in_scope_and_outside_the_budget(self):
        import yaml
        path = self.root / "profiles" / "repo-x.yml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["delivery"]["generated"] = ["*.lock"]
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        package = self.prepare()
        (self.repo / "deps.lock").write_text("pin\n" * 120, encoding="utf-8")
        measure = dict(MEASURE, files=["tests/test_guard.py", "deps.lock"])
        code, rec = self.conclude(package, PROVING_TEST, measure_data=measure)
        self.assertEqual(code, 0, rec)
        self.assertIn("deps.lock", rec["diff"]["files"])
        self.assertEqual(rec["diff"]["outside_scope"], [])
        self.assertTrue(rec["diff"]["within_budget"])

    # ---- see also: the shape tests above

    def test_a_proving_test_is_delivered_committed_and_handed_to_review(self):
        package = self.prepare()
        code, rec = self.conclude(package, PROVING_TEST)
        self.assertEqual(code, 0, rec)
        self.assertEqual(rec["state"], "delivered")
        self.assertEqual(rec["proof"]["mutations"][0]["result"], "red")
        self.assertTrue(rec["proof"]["mutations"][0]["reverted"])
        self.assertEqual(rec["baseline"]["new_reds"], [])
        self.assertEqual((rec["baseline"]["executed"], rec["baseline"]["predicted"]), (2, 2))
        self.assertEqual(rec["skipped"], ["the boundary at 9"])
        self.assertEqual(rec["next"], "/prumo:task-review --repo repo-x --branch "
                                      "ITEM-1-prove-the-refusal-above-ten --item ITEM-1")
        # the source is exactly what the base had: the mutation left no trace
        self.assertEqual((self.repo / "src" / "a.py").read_text(encoding="utf-8"), SOURCE)
        log = subprocess.run(("git", "log", "--format=%s", "main..HEAD"), cwd=self.repo,
                             capture_output=True, text=True).stdout.strip().splitlines()
        self.assertEqual(log, ["ITEM-1 - prove the refusal above ten"])
        self.assertFalse(deliver.markers())
        self.assertTrue((self.root / "reports" / "repo-x" / "ITEM-1-deliver").is_dir())

    def test_the_record_names_every_run_the_script_made_beside_what_the_writer_declared(self):
        """The writer has no shell and says so in `skipped`; the script runs the suite, the
        lint and the mutants afterwards. A reviewer once read the two side by side as a
        contradiction and blocked a sound delivery, so the record names each run as a
        file, and says in words which of the two decides."""
        package = self.prepare()
        code, rec = self.conclude(package, PROVING_TEST)
        self.assertEqual(code, 0, rec)
        self.assertEqual(rec["skipped"], ["the boundary at 9"])
        self.assertEqual(rec["proof"]["mutations"][0]["run"], "run-suite-mutation-1.txt")
        self.assertEqual(rec["proof"]["mutations"][0]["reds"], ["test_guard_refuses"])
        self.assertEqual(rec["lint"]["run"], "run-lint.txt")
        self.assertEqual(rec["measured"]["suite"], "run-suite-branch.txt")
        self.assertEqual(rec["measured"]["base"], "run-suite-base.txt")
        self.assertEqual(rec["measured"]["lint"], "run-lint.txt")
        self.assertEqual(rec["measured"]["mutations"], ["run-suite-mutation-1.txt"])
        self.assertIn("measured", rec["measured"]["note"])
        for name in ("run-suite-mutation-1.txt", "run-lint.txt", "run-suite-branch.txt"):
            self.assertTrue((package / name).is_file(), name)
        report = (package / "report.md").read_text(encoding="utf-8")
        self.assertIn("run-suite-mutation-1.txt", report)
        self.assertIn(rec["measured"]["note"], report)
        self.assertNotIn("suite finished", rec["lint"]["reason"])

    def test_a_test_that_does_not_prove_the_guard_is_blocked_and_nothing_is_committed(self):
        package = self.prepare()
        code, rec = self.conclude(package, WEAK_TEST)
        self.assertEqual(code, 1)
        self.assertEqual(rec["state"], "blocked")
        self.assertEqual(rec["proof"]["mutations"][0]["result"], "green")
        self.assertEqual(rec["owner"], "author")
        log = subprocess.run(("git", "log", "--format=%s", "main..HEAD"), cwd=self.repo,
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(log, "")
        self.assertTrue((self.repo / "tests" / "test_guard.py").exists())
        self.assertFalse(deliver.markers())

    def test_a_red_lint_blocks_the_delivery(self):
        package = self.prepare()
        code, rec = self.conclude(package, PROVING_TEST + "# LINT_BAD\n")
        self.assertEqual(rec["state"], "blocked")
        self.assertEqual(rec["lint"]["state"], "red")
        self.assertEqual(rec["proof"]["mutations"][0]["result"], "red", "the proof still ran")

    def test_a_mutant_that_also_kills_the_passage_test_is_too_wide(self):
        """The item says the passage test must stay green under the mutant. A mutant
        that turns the guard into `if True` refuses everything: red where it should,
        and red where it must not."""
        package = self.prepare()
        wide = dict(MEASURE, mutations=[{"file": "src/a.py", "line": 2, "from": "x >= 10",
                                         "to": "True", "test": "test_guard_refuses",
                                         "stays_green": ["test_accept_small"]}])
        code, rec = self.conclude(package, PROVING_TEST, measure_data=wide)
        self.assertEqual(rec["state"], "blocked")
        m = rec["proof"]["mutations"][0]
        # refusing everything keeps the refusal test green and turns the passage red
        self.assertEqual((m["result"], m["stays_green_result"]), ("green", "red"))
        self.assertIn("test_accept_small", rec["reason"])

    def test_a_behaviour_change_is_proven_red_on_the_base_and_green_on_the_branch(self):
        """The base refuses only above ten; the item wants ten refused too. The test is
        written for the wanted behaviour, goes red against the base's file, green against
        the writer's, and the source the base had is put back in between — exactly."""
        (self.repo / "src" / "a.py").write_text(BUGGY, encoding="utf-8")
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-q", "-m", "buggy base")
        package = self.prepare(kind="behaviour-change")
        measure = {"premises": [{"text": "ten is accepted today", "state": "holds",
                                 "evidence": "src/a.py:2", "central": True}],
                   "files": ["src/a.py", "tests/test_guard.py"], "mutations": [],
                   "proving_tests": ["test_guard_refuses"]}
        code, rec = self.conclude(package, PROVING_TEST, measure_data=measure, source=SOURCE)
        self.assertEqual(code, 0, rec)
        self.assertEqual(rec["proof"]["red_on_base"]["result"], "red")
        self.assertEqual(rec["proof"]["green_on_branch"]["result"], "green")
        self.assertEqual((self.repo / "src" / "a.py").read_text(encoding="utf-8"), SOURCE)
        self.assertEqual(rec["diff"]["outside_scope"], [])

    def test_a_second_attempt_that_declares_one_file_is_recorded_with_every_file_it_left(self):
        """A rewrite on the same tree declared only the file it touched in that pass
        (2026-11-12, three tasks of the first story): the record holds what the tree
        holds inside the measured files, not what the writer remembered."""
        (self.repo / "src" / "a.py").write_text(BUGGY, encoding="utf-8")
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-q", "-m", "buggy base")
        package = self.prepare(kind="behaviour-change")
        measure = {"premises": [], "files": ["src/a.py", "tests/test_guard.py"], "mutations": [],
                   "proving_tests": ["test_guard_refuses"]}
        code, rec = self.conclude(package, PROVING_TEST, measure_data=measure, source=SOURCE,
                                  declared=["tests/test_guard.py"])
        self.assertEqual(code, 0, rec)
        written = json.loads((package / "w.json").read_text(encoding="utf-8"))
        self.assertEqual(written["files"], ["tests/test_guard.py"], "the writer's own file stays as it was")
        kept = json.loads((package / "written.json").read_text(encoding="utf-8"))
        self.assertEqual(kept["files"], ["tests/test_guard.py", "src/a.py"])
        self.assertEqual(rec["proof"]["red_on_base"]["result"], "red")

    def test_a_behaviour_change_drops_the_mutants_its_measure_named_and_says_so(self):
        """A fix round's measure (2026-11-12) named mutants on the pre-fix lines the fix
        itself rewrote. A behaviour change is proven red on the base, green after."""
        (self.repo / "src" / "a.py").write_text(BUGGY, encoding="utf-8")
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-q", "-m", "buggy base")
        package = self.prepare(kind="behaviour-change")
        measure = {"premises": [], "files": ["src/a.py", "tests/test_guard.py"],
                   "mutations": [{"file": "src/a.py", "line": 2, "from": "x > 10", "to": "x > 11",
                                  "test": "test_guard_refuses"}],
                   "proving_tests": ["test_guard_refuses"]}
        (package / "m.in").write_text(json.dumps(measure), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            deliver.plan(argparse.Namespace(package=str(package), measure=str(package / "m.in"), design=None))
        kept = json.loads((package / "measure.json").read_text(encoding="utf-8"))
        self.assertEqual(kept["mutations"], [])
        self.assertIn("src/a.py:2", (package / "plan.md").read_text(encoding="utf-8"))
        code, rec = self.conclude(package, PROVING_TEST, measure_data=measure, source=SOURCE)
        self.assertEqual((code, rec["proof"]["mutations"]), (0, []), rec)

    def test_a_behaviour_change_whose_test_is_green_on_the_base_is_blocked(self):
        package = self.prepare(kind="behaviour-change")
        measure = {"premises": [], "files": ["src/a.py", "tests/test_guard.py"], "mutations": [],
                   "proving_tests": ["test_accept_small"]}
        code, rec = self.conclude(package, PROVING_TEST, measure_data=measure, source=SOURCE + "\n")
        self.assertEqual(rec["state"], "blocked")
        self.assertEqual(rec["proof"]["red_on_base"]["result"], "green")

    def test_a_fix_continues_the_reviewed_branch_and_takes_its_head_as_the_base(self):
        """A review opened an `author_fix` route on the delivered branch. The fix is a
        delivery whose item is the finding, whose branch is the reviewed one, and whose
        base is that branch's head — never a new branch cut from the base."""
        package = self.prepare()
        code, rec = self.conclude(package, PROVING_TEST)
        self.assertEqual(rec["state"], "delivered")
        branch = rec["branch"]["name"]
        head = subprocess.run(("git", "rev-parse", "HEAD"), cwd=self.repo,
                              capture_output=True, text=True).stdout.strip()
        review = self.root / "review"; review.mkdir()
        (review / "manifest.json").write_text(json.dumps({"item": {"id": "ITEM-1"}, "origin": branch}))
        (review / "verdict.json").write_text(json.dumps({"verdict": "INTERVENTION_REQUIRED", "routes": [
            {"type": "author_fix", "items": ["COR-1"], "owner": "author", "closes_when": "push"}]}))
        (review / "findings.json").write_text(json.dumps([{"id": "COR-1", "claim": "nine is not refused",
            "evidence": [{"file": "src/a.py", "line": 2}], "consequence": "x", "form": None}]))
        fix_pkg = self.prepare(fix=str(review), package="pkg-fix")
        man = json.loads((fix_pkg / "deliver-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(man["base"], {"name": branch, "sha": head})
        self.assertEqual(man["branch"]["name"], branch)
        self.assertEqual(man["item"]["kind"], "behaviour-change")
        self.assertIn("nine is not refused", (fix_pkg / "item.md").read_text(encoding="utf-8"))
        current = subprocess.run(("git", "branch", "--show-current"), cwd=self.repo,
                                 capture_output=True, text=True).stdout.strip()
        self.assertEqual(current, branch)

    def test_a_widened_item_continues_its_delivered_branch_from_its_head(self):
        """The person widened an item after its branch was delivered. `--continue` writes
        the new ask on that branch, with its head as the base — never a branch recut from
        the base, which would drop what was already delivered."""
        package = self.prepare()
        code, rec = self.conclude(package, PROVING_TEST)
        branch = rec["branch"]["name"]
        head = subprocess.run(("git", "rev-parse", "HEAD"), cwd=self.repo,
                              capture_output=True, text=True).stdout.strip()
        args = argparse.Namespace(repo="repo-x", item="ITEM-1", item_file=str(self.root / "item.md"),
                                  kind="behaviour-change", branch=branch, package=str(self.root / "pkg-more"),
                                  fix=None, shape="bug", type=None, continue_branch=True)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(deliver.prepare(args), 0)
        man = json.loads((self.root / "pkg-more" / "deliver-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(man["base"], {"name": branch, "sha": head})
        self.assertEqual(man["branch"], {"name": branch, "sha": head})

    def test_continue_without_a_branch_is_refused(self):
        args = argparse.Namespace(repo="repo-x", item="ITEM-1", item_file=str(self.root / "item.md"),
                                  kind=None, branch=None, package=str(self.root / "pkg-x"),
                                  fix=None, shape="bug", type=None, continue_branch=True)
        with self.assertRaises(deliver.UsageError):
            deliver.prepare(args)

    def test_a_hotfix_waits_for_a_person_and_runs_nothing_until_then(self):
        """The shape asks a gate. Without the approval file the suite and the mutation
        do not run and the record says who is owed; with it, the delivery goes through,
        on the base the profile names for a hotfix, with half the budget."""
        import contextlib, io
        (self.repo / "src" / "a.py").write_text(BUGGY, encoding="utf-8")
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-q", "-m", "buggy base")
        package = self.prepare(kind="behaviour-change", shape="hotfix")
        man = json.loads((package / "deliver-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((man["shape"], man["gate"], man["base"]["name"]), ("hotfix", True, "main"))
        self.assertEqual(man["budget"], {"max_files": 1, "max_lines": 30})
        measure = {"premises": [], "files": ["src/a.py", "tests/test_guard.py"], "mutations": [],
                   "proving_tests": ["test_guard_refuses"]}
        (package / "m.json").write_text(json.dumps(measure), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            deliver.plan(argparse.Namespace(package=str(package), measure=str(package / "m.json")))
        self.assertIn("hotfix", (package / "plan.md").read_text(encoding="utf-8"))
        code, rec = self.conclude(package, PROVING_TEST, measure_data=measure, source=SOURCE)
        self.assertEqual((rec["state"], rec["owner"]), ("blocked", "person"))
        self.assertIsNone(rec["proof"]["red_on_base"], "nothing ran behind a shut gate")
        self.assertFalse((package / "run-suite-branch.txt").exists())
        with contextlib.redirect_stdout(io.StringIO()):
            deliver.approve(argparse.Namespace(package=str(package), by="someone"))
        code, rec = self.conclude(package, PROVING_TEST, measure_data=measure, source=SOURCE)
        self.assertEqual(rec["plan"]["approved_by"], "someone")
        # two files against a budget of one: the shape's half, not the profile's whole
        self.assertEqual(rec["state"], "blocked")
        self.assertIn("1", rec["reason"])

    def test_a_refactor_is_proven_by_the_guards_that_stay_green(self):
        """The shape is `refactor`: gated, twice the budget, the kind `refactor`. The
        source is reshaped without changing what `test_accept_small` asserts; that test
        is the guard, and it stays green."""
        import contextlib, io
        package = self.prepare(kind="refactor", shape="refactor")
        measure = {"premises": [], "files": ["src/a.py"], "mutations": [],
                   "guarding_tests": ["test_accept_small"]}
        (package / "m.json").write_text(json.dumps(measure), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            deliver.plan(argparse.Namespace(package=str(package), measure=str(package / "m.json")))
            deliver.approve(argparse.Namespace(package=str(package), by="someone"))
        reshaped = "LIMIT = 10\n\n\ndef accept(x):\n    return x < LIMIT\n"
        (self.repo / "src" / "a.py").write_text(reshaped, encoding="utf-8")
        measure_path, written = package / "m.json", package / "w.json"
        written.write_text(json.dumps({"files": ["src/a.py"], "skipped": []}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            code = deliver.conclude(argparse.Namespace(package=str(package), measure=str(measure_path),
                                                       written=str(written), refutation=None))
        rec = json.loads((package / "delivery.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 0, rec)
        self.assertEqual(rec["proof"]["guarding"], {"tests": ["test_accept_small"], "result": "green"})
        self.assertEqual(rec["plan"]["budget"], {"max_files": 4, "max_lines": 120})

    def test_a_refactor_with_nothing_standing_guard_runs_nothing_and_stops_for_a_person(self):
        import contextlib, io
        package = self.prepare(kind="refactor", shape="refactor")
        measure = {"premises": [], "files": ["src/a.py"], "mutations": [], "guarding_tests": []}
        (package / "m.json").write_text(json.dumps(measure), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            deliver.plan(argparse.Namespace(package=str(package), measure=str(package / "m.json")))
            deliver.approve(argparse.Namespace(package=str(package), by="someone"))
        (self.repo / "src" / "a.py").write_text(SOURCE + "\n", encoding="utf-8")
        written = package / "w.json"; written.write_text(json.dumps({"files": ["src/a.py"], "skipped": []}))
        with contextlib.redirect_stdout(io.StringIO()):
            deliver.conclude(argparse.Namespace(package=str(package), measure=str(package / "m.json"),
                                                written=str(written), refutation=None))
        rec = json.loads((package / "delivery.json").read_text(encoding="utf-8"))
        self.assertEqual((rec["state"], rec["owner"]), ("blocked", "person"))
        self.assertFalse((package / "run-suite-branch.txt").exists())

    def test_an_epic_is_designed_and_never_cut(self):
        """No branch, no suite, no marker: an epic becomes stories in a plan, and the
        plan is where it ends."""
        import contextlib, io
        package = self.prepare(shape="epic")
        man = json.loads((package / "deliver-manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(man["deliverable"])
        self.assertFalse(deliver.markers())
        head = subprocess.run(("git", "branch", "--show-current"), cwd=self.repo,
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(head, "main", "no branch was cut for an epic")
        design = {"summary": "two stories", "tasks": [], "checked": [],
                  "stories": [{"title": "refuse ten", "criteria": ["ten is refused"], "out_of_scope": "nine"},
                              {"title": "refuse nine", "criteria": ["nine is refused"], "out_of_scope": "eight"}]}
        (package / "design.json.in").write_text(json.dumps(design), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            deliver.plan(argparse.Namespace(package=str(package), measure=None, design=str(package / "design.json.in")))
        text = (package / "plan.md").read_text(encoding="utf-8")
        self.assertIn("story  refuse ten", text); self.assertIn("not: eight", text)
        self.assertTrue(json.loads((package / "plan.json").read_text())["gate"])

    def test_a_story_is_delivered_task_by_task_on_one_branch_and_judged_as_a_whole(self):
        """Two tasks, the second building on the first: each is a child delivery with
        its own measure, proof and commit on the story's branch, its base the branch's
        head; the story's record sums them inside the shape's budget."""
        import contextlib, io
        (self.repo / "src" / "a.py").write_text(BUGGY, encoding="utf-8")
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-q", "-m", "buggy base")
        story = self.prepare(kind="behaviour-change", shape="story")
        man = json.loads((story / "deliver-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(man["budget"], {"max_files": 6, "max_lines": 180})
        design = {"summary": "a limit, then the guard uses it", "stories": [], "checked": [],
                  "tasks": [{"id": "T1", "title": "a limit module", "criteria": ["limit() is 10"],
                             "files": ["src/b.py", "tests/test_b.py"], "proving_tests": ["test_limit_is_ten"],
                             "depends_on": []},
                            {"id": "T2", "title": "the guard refuses the limit", "criteria": ["ten is refused"],
                             "files": ["src/a.py", "tests/test_guard.py"], "proving_tests": ["test_guard_refuses"],
                             "depends_on": ["T1"]}]}
        (story / "design.in").write_text(json.dumps(design), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            deliver.plan(argparse.Namespace(package=str(story), measure=None, design=str(story / "design.in")))
            deliver.approve(argparse.Namespace(package=str(story), by="someone"))
        branch = man["branch"]["name"]

        def child(task_id, writes, measure):
            pkg = story / "tasks" / task_id
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(deliver.prepare(argparse.Namespace(
                    repo="repo-x", item=None, item_file=None, kind=None, branch=None, package=str(pkg),
                    fix=None, shape=None, type=None, story=str(story), task=task_id)), 0)
            cm = json.loads((pkg / "deliver-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual((cm["shape"], cm["branch"]["name"], cm["base"]["name"]), ("bug", branch, branch))
            for path, text in writes.items():
                (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
                (self.repo / path).write_text(text, encoding="utf-8")
            (pkg / "m.json").write_text(json.dumps(measure), encoding="utf-8")
            (pkg / "w.json").write_text(json.dumps({"files": list(writes), "skipped": []}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                deliver.plan(argparse.Namespace(package=str(pkg), measure=str(pkg / "m.json"), design=None))
                deliver.conclude(argparse.Namespace(package=str(pkg), measure=str(pkg / "m.json"),
                                                    written=str(pkg / "w.json"), refutation=None))
            return json.loads((pkg / "delivery.json").read_text(encoding="utf-8"))

        t1 = child("T1", {"src/b.py": "def limit():\n    return 10\n",
                          "tests/test_b.py": "import sys, unittest\nsys.path.insert(0, 'src')\nfrom b import limit\n\nclass B(unittest.TestCase):\n    def test_limit_is_ten(self):\n        self.assertEqual(limit(), 10)\n"},
                   {"premises": [], "files": ["src/b.py", "tests/test_b.py"], "mutations": [],
                    "proving_tests": ["test_limit_is_ten"]})
        self.assertEqual(t1["state"], "delivered", t1)
        self.assertEqual(t1["proof"]["red_on_base"]["result"], "red")
        t2 = child("T2", {"src/a.py": "from b import limit\n\n\ndef accept(x):\n    if x >= limit():\n        return False\n    return True\n",
                          "tests/test_guard.py": PROVING_TEST},
                   {"premises": [], "files": ["src/a.py", "tests/test_guard.py"], "mutations": [],
                    "proving_tests": ["test_guard_refuses"]})
        self.assertEqual(t2["state"], "delivered", t2)
        self.assertEqual(t2["base"]["sha"], t1["branch"]["sha"], "the second task's base is the first's head")
        with contextlib.redirect_stdout(io.StringIO()):
            code = deliver.story_status(argparse.Namespace(package=str(story)))
        rec = json.loads((story / "delivery.json").read_text(encoding="utf-8"))
        self.assertEqual((code, rec["state"], rec["tasks"]), (0, "delivered", {"T1": "delivered", "T2": "delivered"}))
        self.assertEqual(sorted(rec["diff"]["files"]), ["src/a.py", "src/b.py", "tests/test_b.py", "tests/test_guard.py"])
        log = subprocess.run(("git", "log", "--format=%s", "main..HEAD"), cwd=self.repo,
                             capture_output=True, text=True).stdout.strip().splitlines()
        self.assertEqual(len(log), 2, log)

        # The story carries the proof its tasks measured (2026-11-12: the first real
        # story wrote `baseline` all null, and the review read "the suite never ran at
        # the head" of a branch whose last task had run it on that very commit).
        self.assertEqual(rec["branch"]["sha"], t2["branch"]["sha"])
        self.assertIsNotNone(rec["baseline"]["executed"], rec["baseline"])
        self.assertEqual(rec["baseline"]["new_reds"], [])
        self.assertEqual(rec["measured"]["suite"], "run-suite-branch.txt")
        self.assertTrue((story / "run-suite-branch.txt").is_file())
        self.assertEqual(rec["proof"]["red_on_base"]["result"], "red")
        self.assertEqual(rec["proof"]["green_on_branch"]["result"], "green")
        for task_id in ("T1", "T2"):
            self.assertTrue((story / f"run-suite-base-with-test-{task_id}.txt").is_file(), task_id)
        jsonschema.validate(rec, json.loads((PRUMO_ROOT / "schemas" / "delivery.schema.json").read_text(encoding="utf-8")))

        # The second task was told what the first delivered, so neither the writer nor
        # the sceptic can undo it unseen (2026-11-12: a write in task 10 defeated task 9).
        args_t2 = json.loads((story / "tasks" / "T2" / "workflow-args.json").read_text(encoding="utf-8"))
        self.assertEqual([x["id"] for x in args_t2["story_context"]], ["T1"])
        self.assertEqual(args_t2["story_context"][0]["criteria"], ["limit() is 10"])
        self.assertEqual(args_t2["story_context"][0]["files"], ["src/b.py", "tests/test_b.py"])
        args_t1 = json.loads((story / "tasks" / "T1" / "workflow-args.json").read_text(encoding="utf-8"))
        self.assertEqual(args_t1["story_context"], [])

        # Over the story's budget: blocked for a person — unless the person's waiver in
        # the profile is in force. An expired waiver is as if it never existed.
        man["budget"] = {"max_files": 1, "max_lines": 1}
        (story / "deliver-manifest.json").write_text(json.dumps(man), encoding="utf-8")
        import yaml
        profile_file = self.root / "profiles" / "repo-x.yml"
        original = yaml.safe_load(profile_file.read_text(encoding="utf-8"))

        def status(waiver):
            profile = json.loads(json.dumps(original))
            if waiver:
                profile["delivery"]["waiver"] = waiver
            profile_file.write_text(yaml.safe_dump(profile), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                code = deliver.story_status(argparse.Namespace(package=str(story)))
            return code, json.loads((story / "delivery.json").read_text(encoding="utf-8"))

        code, over = status(None)
        self.assertEqual((code, over["state"], over["owner"]), (1, "blocked", "person"))
        code, waived = status({"approver": "someone", "reason": "a person said so", "expires": "2999-12-31"})
        self.assertEqual((code, waived["state"]), (0, "delivered"), waived["reason"])
        self.assertEqual(waived["waiver"]["approver"], "someone")
        code, expired = status({"approver": "someone", "reason": "a person said so", "expires": "2000-01-01"})
        self.assertEqual((code, expired["state"]), (1, "blocked"))
        profile_file.write_text(yaml.safe_dump(original), encoding="utf-8")

        # A fix round after a review (`prepare --fix`) lands on the story's branch from a
        # package of its own and moves the head past the last task. The story counts its
        # diff and takes its suite as the head's (2026-11-12: the story read "no task at
        # the head" right after the fix round of the first real story was delivered).
        man["budget"] = {"max_files": 60, "max_lines": 6000}
        (story / "deliver-manifest.json").write_text(json.dumps(man), encoding="utf-8")
        (self.repo / "src" / "b.py").write_text("def limit():\n    return 10  # fixed\n", encoding="utf-8")
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-q", "-m", "fix round")
        fix_head = subprocess.run(("git", "rev-parse", "HEAD"), cwd=self.repo,
                                  capture_output=True, text=True).stdout.strip()
        fix_pkg = story.parent / "the-fix-round"
        fix_pkg.mkdir()
        fix_rec = json.loads(json.dumps(t2))
        fix_rec["branch"]["sha"] = fix_head
        fix_rec["item"] = dict(man["item"])
        fix_rec["diff"] = {"files": ["src/b.py", "src/c.py"], "added": 7, "removed": 3,
                           "outside_scope": [], "within_budget": True}
        (fix_pkg / "delivery.json").write_text(json.dumps(fix_rec), encoding="utf-8")
        shutil.copy2(story / "tasks" / "T2" / "run-suite-branch.txt", fix_pkg / "run-suite-branch.txt")
        with contextlib.redirect_stdout(io.StringIO()):
            code = deliver.story_status(argparse.Namespace(package=str(story)))
        fixed = json.loads((story / "delivery.json").read_text(encoding="utf-8"))
        self.assertEqual((code, fixed["state"]), (0, "delivered"), fixed["reason"])
        self.assertEqual(fixed["branch"]["sha"], fix_head)
        self.assertIn("src/c.py", fixed["diff"]["files"])
        self.assertEqual(fixed["diff"]["added"], rec["diff"]["added"] + 10)
        self.assertIn("the-fix-round", fixed["measured"]["note"])
        # Another item's delivery on the same branch is not this story's fix.
        fix_rec["item"]["id"] = "SOMETHING-ELSE"
        (fix_pkg / "delivery.json").write_text(json.dumps(fix_rec), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            code = deliver.story_status(argparse.Namespace(package=str(story)))
        self.assertEqual(code, 1)

    def test_a_kind_the_shape_does_not_allow_is_refused(self):
        with self.assertRaises(common.UsageError):
            self.prepare(kind="coverage-gap", shape="bug")

    def test_a_delivery_is_never_cut_on_a_protected_branch(self):
        args = argparse.Namespace(repo="repo-x", item="ITEM-1", item_file=str(self.root / "item.md"),
                                  kind=None, branch="main", package=str(self.root / "pkg"),
                                  fix=None, shape="debt", type=None)
        with self.assertRaises(common.UsageError) as cm, contextlib.redirect_stdout(io.StringIO()):
            deliver.prepare(args)
        self.assertIn("main", str(cm.exception))
        head = subprocess.run(("git", "log", "--format=%s", "-1"), cwd=self.repo,
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(head, "base")

    def test_a_file_outside_the_named_ones_is_outside_the_scope(self):
        package = self.prepare()
        (self.repo / "src" / "a.py").write_text(SOURCE + "\n", encoding="utf-8")
        code, rec = self.conclude(package, PROVING_TEST)
        self.assertEqual(rec["state"], "blocked")
        self.assertEqual(rec["diff"]["outside_scope"], ["src/a.py"])


    def test_a_named_document_under_doc_roots_is_inside_the_scope(self):
        """Measured on 2026-11-12: a README the change made stale could not travel with
        it, and the review asked for it four rounds running."""
        roots = ["src", "tests", "README.md", "docs/"]
        self.assertEqual(deliver.scope_guard(["README.md", "docs/a.md", "src/x.py"],
                                             ["README.md", "docs/a.md", "src/x.py"], roots), [])
        self.assertEqual(deliver.scope_guard(["README.md"], [], roots), ["README.md"],
                         "a document the measurement did not name stays outside")
        self.assertEqual(deliver.scope_guard(["NOTES.md"], ["NOTES.md"], roots), ["NOTES.md"])

class InAWorktree(Fixture):
    """The same spine, with the profile asking for a worktree: the person's checkout
    never changes branch, the suite runs over the worktree through `{workdir}`, what
    git does not hold arrives by `worktree_setup`, and `release` takes the worktree
    away and leaves the branch. The runner sits outside git — the way a dependency
    tree does — so a suite that passes in the worktree proves the setup ran there."""

    SETUP = ["cp -r {repo}/runner {worktree}/runner"]

    def setUp(self):
        super().setUp()
        (self.repo / "runner").mkdir()
        (self.repo / "run_tests.py").rename(self.repo / "runner" / "run_tests.py")
        (self.repo / ".gitignore").write_text("__pycache__/\nrunner/\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "the runner lives outside git")
        self.profile(self.SETUP)

    def profile(self, setup, path_map=None):
        import yaml
        data = json.loads(json.dumps(PROFILE))
        data["checks"]["suite"]["command"] = "python3 {workdir}/runner/run_tests.py"
        data["delivery"].update({"worktree": True, "worktree_setup": setup,
                                 "path_map": path_map or {"host": str(self.repo),
                                                          "container": str(self.repo)}})
        (self.root / "profiles" / "repo-x.yml").write_text(yaml.safe_dump(data), encoding="utf-8")

    def branch_of(self, cwd):
        return subprocess.run(("git", "branch", "--show-current"), cwd=cwd,
                              capture_output=True, text=True).stdout.strip()

    def test_prepare_cuts_the_branch_reads_the_baseline_and_marks(self):
        package = self.prepare()
        man = json.loads((package / "deliver-manifest.json").read_text(encoding="utf-8"))
        workdir = self.repo / ".worktrees" / man["branch"]["name"]
        self.assertEqual((man["in_worktree"], man["workdir"]), (True, str(workdir)))
        self.assertEqual(man["baseline"], {"base_reds": [], "predicted": 1, "executed": 1})
        self.assertEqual(self.branch_of(self.repo), "main")
        self.assertEqual(self.branch_of(workdir), man["branch"]["name"])
        self.assertTrue((workdir / "runner" / "run_tests.py").exists())
        self.assertEqual(json.loads(deliver.markers()[0].read_text())["worktree"], str(workdir))
        self.assertIn(".worktrees/", (self.repo / ".git" / "info" / "exclude").read_text())

    def test_a_dirty_tree_is_a_stop(self):
        # a worktree is cut beside the checkout: pending work there is nobody's business
        (self.repo / "src" / "a.py").write_text(SOURCE + "# pending\n", encoding="utf-8")
        package = self.prepare()
        self.assertTrue(pathlib.Path(json.loads((package / "deliver-manifest.json")
                                               .read_text(encoding="utf-8"))["workdir"]).is_dir())

    def test_a_proving_test_is_delivered_committed_and_handed_to_review(self):
        package = self.prepare()
        code, rec = self.conclude(package, PROVING_TEST)
        self.assertEqual((code, rec["state"]), (0, "delivered"), rec)
        self.assertEqual(rec["proof"]["mutations"][0]["result"], "red")
        self.assertEqual((rec["baseline"]["executed"], rec["baseline"]["predicted"]), (2, 2))
        branch = rec["branch"]["name"]
        log = subprocess.run(("git", "log", "--format=%s", f"main..{branch}"), cwd=self.repo,
                             capture_output=True, text=True).stdout.strip().splitlines()
        self.assertEqual(log, ["ITEM-1 - prove the refusal above ten"])
        # the checkout: still on main, still clean, never saw the test file
        self.assertEqual(self.branch_of(self.repo), "main")
        self.assertEqual(subprocess.run(("git", "status", "--porcelain"), cwd=self.repo,
                                        capture_output=True, text=True).stdout, "")
        self.assertFalse((self.repo / "tests" / "test_guard.py").exists())
        self.assertFalse(deliver.markers())

    def test_release_removes_the_worktree_and_keeps_the_branch(self):
        package = self.prepare()
        workdir = pathlib.Path(json.loads((package / "deliver-manifest.json")
                                          .read_text(encoding="utf-8"))["workdir"])
        with contextlib.redirect_stdout(io.StringIO()):
            deliver.release_worktree(package)
        self.assertFalse(workdir.exists())
        listed = subprocess.run(("git", "worktree", "list"), cwd=self.repo,
                                capture_output=True, text=True).stdout
        self.assertNotIn(".worktrees", listed)
        branches = subprocess.run(("git", "branch", "--list", "ITEM-1-*"), cwd=self.repo,
                                  capture_output=True, text=True).stdout
        self.assertIn("ITEM-1-prove-the-refusal-above-ten", branches)

    def test_a_setup_that_fails_takes_the_worktree_and_the_branch_down(self):
        self.profile(["cp -r {repo}/runner {worktree}/runner", "false"])
        with self.assertRaises(common.UsageError) as cm, contextlib.redirect_stdout(io.StringIO()):
            self.prepare()
        self.assertIn("worktree_setup", str(cm.exception))
        self.assertFalse((self.repo / ".worktrees").exists() and any((self.repo / ".worktrees").iterdir()))
        branches = subprocess.run(("git", "branch", "--list", "ITEM-1-*"), cwd=self.repo,
                                  capture_output=True, text=True).stdout
        self.assertEqual(branches.strip(), "")

    def test_a_composed_setup_never_runs(self):
        self.profile(["cp -r {repo}/runner {worktree}/runner && rm -rf {repo}"])
        with self.assertRaises(common.UsageError) as cm, contextlib.redirect_stdout(io.StringIO()):
            self.prepare()
        self.assertIn("worktree_setup", str(cm.exception))
        self.assertTrue((self.repo / "src" / "a.py").exists())

    def test_an_existing_branch_is_never_reset_under_a_worktree(self):
        git(self.repo, "branch", "ITEM-1-prove-the-refusal-above-ten", "main")
        with self.assertRaises(common.UsageError) as cm, contextlib.redirect_stdout(io.StringIO()):
            self.prepare()
        self.assertIn("ITEM-1-prove-the-refusal-above-ten", str(cm.exception))

    def test_the_workdir_is_translated_through_the_path_map(self):
        import evidence
        profile = {"delivery": {"path_map": {"host": str(self.repo), "container": "/var/www/html"}}}
        self.assertEqual(evidence.mapped_workdir(profile, self.repo), "/var/www/html")
        self.assertEqual(evidence.mapped_workdir(profile, self.repo / ".worktrees" / "b"),
                         "/var/www/html/.worktrees/b")
        self.assertEqual(evidence.resolve("docker exec -w {workdir} c runner", profile,
                                          self.repo / ".worktrees" / "b"),
                         "docker exec -w /var/www/html/.worktrees/b c runner")
        self.assertEqual(evidence.mapped_workdir({"delivery": {}}, self.repo / "x"),
                         str(self.repo / "x"))
        with self.assertRaises(common.UsageError):
            evidence.mapped_workdir(profile, self.root)

    def test_a_test_that_does_not_prove_the_guard_is_blocked_and_nothing_is_committed(self):
        package = self.prepare()
        code, rec = self.conclude(package, WEAK_TEST)
        self.assertEqual((code, rec["state"]), (1, "blocked"))
        branch = rec["branch"]["name"]
        log = subprocess.run(("git", "log", "--format=%s", f"main..{branch}"), cwd=self.repo,
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(log, "")
        self.assertEqual(self.branch_of(self.repo), "main")


if __name__ == "__main__":
    unittest.main()
