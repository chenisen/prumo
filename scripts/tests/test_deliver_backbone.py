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

    def test_a_file_path_names_the_test_a_runner_prints_by_its_class(self):
        # First real block (2026-11-17): the measure wrote `tests/Unit/GuardTest.x::refuses`,
        # the runner printed `Tests\Unit\GuardTest::refuses`, and a killed mutant read green.
        self.assertTrue(deliver.same_test("tests/Unit/GuardTest.x::refuses", "Tests\\Unit\\GuardTest::refuses"))
        self.assertTrue(deliver.same_test("tests/Unit/GuardTest.x::refuses",
                                          'Tests\\Unit\\GuardTest::refuses with data set "empty"'))
        self.assertTrue(deliver.same_test("tests/test_guard.py::test_refuses", "test_guard.py::test_refuses"))

    def test_another_method_or_another_class_is_still_another_test(self):
        self.assertFalse(deliver.same_test("tests/Unit/GuardTest.x::refuses", "Tests\\Unit\\GuardTest::refuses_twice"))
        self.assertFalse(deliver.same_test("tests/Unit/GuardTest.x::refuses", "Tests\\Unit\\OtherGuardTest::refuses"))
        self.assertFalse(deliver.same_test("tests/Unit/GuardTest.x::refuses", "Tests\\Unit\\GuardTestCase::refuses"))


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

    def conclude_result(self, package, result):
        """The way the route concludes: one file, the workflow's whole return."""
        (package / "workflow-result.json").write_text(json.dumps(result), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            code = deliver.conclude(argparse.Namespace(package=str(package),
                                                       result=str(package / "workflow-result.json")))
        return code, json.loads((package / "delivery.json").read_text(encoding="utf-8"))

    def profile_with(self, **changes):
        """The profile, with `checks.suite` or `delivery` keys changed for one test."""
        import yaml
        data = json.loads(json.dumps(PROFILE))
        for key, value in changes.items():
            section, _, name = key.partition("__")
            target = data["checks"]["suite"] if section == "suite" else data["delivery"]
            if value is None:
                target.pop(name, None)
            else:
                target[name] = value
        (self.root / "profiles" / "repo-x.yml").write_text(yaml.safe_dump(data), encoding="utf-8")

    def tree_of(self, package):
        return pathlib.Path(json.loads((package / "deliver-manifest.json")
                                       .read_text(encoding="utf-8"))["workdir"])



class Spine(Fixture):
    def test_prepare_cuts_the_branch_runs_no_suite_and_marks(self):
        """The base used to be measured here — a whole suite before a line was written.
        Now prepare cuts and marks; the base is measured by `conclude`, and only when
        the branch turns something red."""
        package = self.prepare()
        man = json.loads((package / "deliver-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(man["branch"]["name"], "ITEM-1-prove-the-refusal-above-ten")
        self.assertNotIn("baseline", man)
        self.assertEqual(list(package.glob("run-suite-*.txt")), [])
        head = subprocess.run(("git", "branch", "--show-current"), cwd=self.repo,
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(head, man["branch"]["name"])
        self.assertTrue(deliver.markers())
        mark = json.loads(deliver.markers()[0].read_text())
        self.assertEqual(mark["worktree"], str(self.repo))
        self.assertEqual(mark["roots"], ["tests"], "a coverage gap may write tests, and nothing else")
        args = json.loads((package / "workflow-args.json").read_text(encoding="utf-8"))
        self.assertEqual(args["small"], {"max_files": 3, "max_lines": 80})
        self.assertEqual(args["commands"]["lint"], "python3 lint.py")
        self.assertIsNone(args["commands"]["tests"], "no files_command declared: the writer runs no test")
        self.assertFalse(args["gate"])

    def test_the_arguments_carry_an_extract_of_the_profile_not_the_whole_of_it(self):
        """The whole profile was pasted into the workflow's arguments and repeated in every
        message of every agent. A delivery reads the stack, the base and how the suite and
        the lint are run and read; the review's anchors, evals and roles are not its
        business, and the package still holds the whole file."""
        import yaml
        package = self.prepare()
        args = json.loads((package / "workflow-args.json").read_text(encoding="utf-8"))
        extract = yaml.safe_load(args["inline"]["profile.yml"])
        self.assertEqual(sorted(extract), ["base", "checks", "profile", "stack"])
        self.assertEqual(sorted(extract["checks"]), ["lint", "suite"])
        self.assertEqual(extract["checks"]["suite"]["red_name_pattern"], r"(?m)^FAIL: (\S+)")
        self.assertIn(str(package / "profile.yml"), args["inline"]["profile.yml"])
        whole = yaml.safe_load((package / "profile.yml").read_text(encoding="utf-8"))
        self.assertIn("anchors", whole, "the package keeps the profile as it was read")
        self.assertEqual(args["delivery"], whole["delivery"], "the delivery block travels beside it, whole")

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

    def test_a_profile_with_no_budget_stops_no_delivery_for_its_size(self):
        """A budget is a repository's decision. Where nobody declared one the method
        defaults none: the plan says so, and a change of any size is judged by its proof."""
        self.profile_with(delivery__budget=None)
        package = self.prepare()
        man = json.loads((package / "deliver-manifest.json").read_text(encoding="utf-8"))
        self.assertIsNone(man["budget"])
        code, rec = self.conclude(package, PROVING_TEST + "# a line of proof\n" * 400)
        self.assertEqual((code, rec["state"]), (0, "delivered"), rec)
        self.assertTrue(rec["diff"]["within_budget"])
        self.assertIsNone(rec["plan"]["budget"])
        self.assertIn("budget none", (package / "plan.md").read_text(encoding="utf-8"))

    def test_a_budget_raised_after_a_block_is_heard_by_the_next_conclude(self):
        """The budget is read as the profile stands, like the waiver beside it. A person
        who answers `blocked` by raising it — or removing it — concludes the same package
        again; the manifest's number, frozen at prepare, used to keep the stop standing."""
        package = self.prepare()
        long_test = PROVING_TEST + "# a line of proof\n" * 400
        code, rec = self.conclude(package, long_test)
        self.assertEqual((code, rec["state"], rec["owner"]), (1, "blocked", "person"), rec)
        self.profile_with(delivery__budget={"max_files": 2, "max_lines": 600})
        code, rec = self.conclude(package, long_test)
        self.assertEqual((code, rec["state"]), (0, "delivered"), rec)
        self.assertEqual(rec["plan"]["budget"], {"max_files": 2, "max_lines": 600})

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
        """What the writer declared travels beside what the script ran. A reviewer once
        read the two side by side as a contradiction and blocked a sound delivery, so
        the record names each run as a file, and says in words which of the two decides.
        The branch came out green, so no base was measured."""
        package = self.prepare()
        code, rec = self.conclude(package, PROVING_TEST)
        self.assertEqual(code, 0, rec)
        self.assertEqual(rec["skipped"], ["the boundary at 9"])
        self.assertEqual(rec["proof"]["mutations"][0]["run"], "run-suite-mutation-1.txt")
        self.assertEqual(rec["proof"]["mutations"][0]["reds"], ["test_guard_refuses"])
        self.assertEqual(rec["lint"]["run"], "run-lint.txt")
        self.assertEqual(rec["measured"]["suite"], "run-suite-branch.txt")
        self.assertIsNone(rec["measured"]["base"])
        self.assertEqual((rec["baseline"]["base"], rec["baseline"]["scope"]), ("not_needed", "whole"))
        self.assertFalse((package / "run-suite-base.txt").exists())
        self.assertEqual(rec["size"]["size"], "small")
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

    def story(self):
        (self.repo / "src" / "a.py").write_text(BUGGY, encoding="utf-8")
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-q", "-m", "buggy base")
        story = self.prepare(kind="behaviour-change", shape="story")
        design = {"summary": "a limit, then the guard uses it", "stories": [], "checked": [],
                  "tasks": [{"id": "T1", "title": "a limit module", "criteria": ["limit() is 10"],
                             "files": ["src/b.py", "tests/test_b.py"], "proving_tests": ["test_limit_is_ten"],
                             "depends_on": []},
                            {"id": "T2", "title": "the guard refuses the limit", "criteria": ["ten is refused"],
                             "files": ["src/a.py", "tests/test_guard.py"], "proving_tests": ["test_guard_refuses"],
                             "depends_on": ["T1"]}]}
        (story / "design.in").write_text(json.dumps({"stage": "design", "design": design}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            deliver.plan(argparse.Namespace(package=str(story), measure=None, design=str(story / "design.in")))
            deliver.approve(argparse.Namespace(package=str(story), by="someone"))
        return story

    TASKS = {
        "T1": {"src/b.py": "def limit():\n    return 10\n",
               "tests/test_b.py": "import sys, unittest\nsys.path.insert(0, 'src')\nfrom b import limit\n\nclass B(unittest.TestCase):\n    def test_limit_is_ten(self):\n        self.assertEqual(limit(), 10)\n"},
        "T2": {"src/a.py": "from b import limit\n\n\ndef accept(x):\n    if x >= limit():\n        return False\n    return True\n",
               "tests/test_guard.py": PROVING_TEST},
    }

    def write_task(self, task_id):
        """What the writer of one task does: writes its files and commits them."""
        for path, text in self.TASKS[task_id].items():
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(text, encoding="utf-8")
        git(self.repo, "add", "--", *self.TASKS[task_id])
        git(self.repo, "commit", "-q", "-m", f"ITEM-1 - {task_id}")
        return {"id": task_id, "written": {"files": list(self.TASKS[task_id]), "skipped": [], "reused": [],
                                           "tests_run": {"command": None, "red_first": True, "green": True},
                                           "blocked": None, "commit_message": task_id, "commit": "abc"}}

    def test_a_story_is_written_task_by_task_and_checked_once(self):
        """Two tasks, each written and committed by its writer on the story's branch;
        nothing is measured, proven or reported per task. One conclude over the whole
        branch: the scope, the budget, the red on the base, the suite — once."""
        story = self.story()
        man = json.loads((story / "deliver-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(man["budget"], {"max_files": 6, "max_lines": 180})
        args = json.loads((story / "workflow-args.json").read_text(encoding="utf-8"))
        self.assertEqual([x["id"] for x in args["design"]["tasks"]], ["T1", "T2"],
                         "the tasks stage reads the design from workflow-args.json")
        self.assertFalse((story / "tasks").exists(), "a task has no package of its own")
        code, rec = self.conclude_result(story, {"stage": "tasks", "tasks": [self.write_task("T1"), self.write_task("T2")]})
        self.assertEqual((code, rec["state"], rec["tasks"]), (0, "delivered", {"T1": "delivered", "T2": "delivered"}), rec)
        self.assertEqual(rec["size"]["size"], "large")
        self.assertEqual(sorted(rec["diff"]["files"]), ["src/a.py", "src/b.py", "tests/test_b.py", "tests/test_guard.py"])
        log = subprocess.run(("git", "log", "--format=%s", "main..HEAD"), cwd=self.repo,
                             capture_output=True, text=True).stdout.strip().splitlines()
        self.assertEqual(log, ["ITEM-1 - T2", "ITEM-1 - T1"], "one commit per task, and none added by the check")
        self.assertEqual(rec["baseline"]["scope"], "whole")
        self.assertEqual(rec["baseline"]["new_reds"], [])
        self.assertEqual(rec["measured"]["suite"], "run-suite-branch.txt")
        self.assertEqual(len(list(story.glob("run-suite-branch*.txt"))), 1, "the suite ran once for the whole story")
        self.assertEqual(rec["proof"]["red_on_base"]["result"], "red")
        self.assertEqual(rec["proof"]["green_on_branch"]["result"], "green")
        self.assertEqual(subprocess.run(("git", "status", "--porcelain"), cwd=self.repo, capture_output=True,
                                        text=True).stdout.strip(), "", "the red on the base left the tree as it was")
        jsonschema.validate(rec, json.loads((PRUMO_ROOT / "schemas" / "delivery.schema.json").read_text(encoding="utf-8")))

    def test_a_story_whose_task_stopped_is_blocked_and_resumes_from_it(self):
        story = self.story()
        stopped = {"id": "T2", "written": {"files": [], "skipped": [], "reused": [], "blocked": "src/a.py:2 is not as the design assumed",
                                           "tests_run": {"command": None, "red_first": None, "green": None}}}
        code, rec = self.conclude_result(story, {"stage": "tasks", "tasks": [self.write_task("T1"), stopped]})
        self.assertEqual((code, rec["state"], rec["owner"]), (1, "blocked", "person"))
        self.assertIn("T2: src/a.py:2", rec["reason"])
        self.assertEqual(rec["tasks"], {"T1": "delivered", "T2": "blocked"})
        self.assertEqual(list(story.glob("run-suite-*.txt")), [], "nothing is run over a story that did not finish")
        # the next call of the tasks stage returns only what it wrote; the earlier task is kept
        code, rec = self.conclude_result(story, {"stage": "tasks", "tasks": [self.write_task("T2")]})
        self.assertEqual((code, rec["state"], rec["tasks"]), (0, "delivered", {"T1": "delivered", "T2": "delivered"}), rec)

    def test_a_story_over_its_budget_waits_for_a_person_or_a_waiver(self):
        story = self.story()
        tight = {"max_files": 1, "max_lines": 1}
        self.profile_with(delivery__budget=tight)
        result = {"stage": "tasks", "tasks": [self.write_task("T1"), self.write_task("T2")]}
        code, over = self.conclude_result(story, result)
        self.assertEqual((code, over["state"], over["owner"]), (1, "blocked", "person"))
        self.profile_with(delivery__budget=tight,
                          delivery__waiver={"approver": "someone", "reason": "a person said so", "expires": "2999-12-31"})
        code, waived = self.conclude_result(story, result)
        self.assertEqual((code, waived["state"]), (0, "delivered"), waived["reason"])
        self.profile_with(delivery__budget=tight,
                          delivery__waiver={"approver": "someone", "reason": "a person said so", "expires": "2000-01-01"})
        code, expired = self.conclude_result(story, result)
        self.assertEqual((code, expired["state"]), (1, "blocked"))

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
        self.assertNothingRan(package, rec)

    def assertNothingRan(self, package, rec):
        self.assertEqual(list(package.glob("run-*.txt")), [], "a stop the diff decides is paid by no run")
        self.assertEqual(rec["proof"]["mutations"], [])
        self.assertEqual((rec["lint"]["state"], rec["measured"]["suite"]), ("not_run", None))

    def test_a_stop_the_diff_decides_runs_no_mutant_no_suite_and_no_lint(self):
        """The first real delivery on 3.0 was 146 lines against a budget of 120: its writer
        had said so, and `conclude` still ran three mutants and two whole suites before
        answering `blocked`. What the diff alone decides is read first."""
        package = self.prepare()
        long_test = PROVING_TEST + "# a line of proof\n" * 400
        code, rec = self.conclude(package, long_test)
        self.assertEqual((code, rec["state"], rec["owner"]), (1, "blocked", "person"), rec)
        self.assertNothingRan(package, rec)
        # the stop lifted, the same package is concluded again and everything runs — once
        self.profile_with(delivery__budget=None)
        code, rec = self.conclude(package, long_test)
        self.assertEqual((code, rec["state"]), (0, "delivered"), rec)
        self.assertEqual(rec["proof"]["mutations"][0]["result"], "red")
        self.assertTrue((package / "run-suite-branch.txt").exists())

    def test_the_diff_s_stop_is_named_ahead_of_any_stop_a_run_could_give(self):
        diff = {"files": ["a"], "added": 1, "removed": 0, "outside_scope": ["src/z.py"], "within_budget": False}
        self.assertEqual(deliver.diff_stop(diff, None), "outside_scope")
        self.assertEqual(deliver.diff_stop(dict(diff, outside_scope=[]), None), "budget")
        waiver = {"approver": "someone", "reason": "a person said so", "expires": "2999-12-31"}
        self.assertIsNone(deliver.diff_stop(dict(diff, outside_scope=[]), waiver))
        self.assertEqual(deliver.diff_stop(dict(diff, outside_scope=[]), dict(waiver, expires="2000-01-01")), "budget")
        self.assertIsNone(deliver.diff_stop(dict(diff, outside_scope=[], within_budget=True), None))


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
        data["delivery"].pop("worktree")      # the default: every item in its own worktree
        data["delivery"].update({"worktree_setup": setup,
                                 "path_map": path_map or {"host": str(self.repo),
                                                          "container": str(self.repo)}})
        (self.root / "profiles" / "repo-x.yml").write_text(yaml.safe_dump(data), encoding="utf-8")

    def branch_of(self, cwd):
        return subprocess.run(("git", "branch", "--show-current"), cwd=cwd,
                              capture_output=True, text=True).stdout.strip()

    def test_prepare_cuts_the_branch_in_its_own_worktree_and_marks(self):
        package = self.prepare()
        man = json.loads((package / "deliver-manifest.json").read_text(encoding="utf-8"))
        workdir = self.repo / ".worktrees" / man["branch"]["name"]
        self.assertEqual((man["in_worktree"], man["workdir"]), (True, str(workdir)))
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


class TheItemSaysItsIdOnce(unittest.TestCase):
    """An item titled `# ITEM-7 — Title` named its branch `ITEM-7-item-7-…` and its commit
    `ITEM-7 - ITEM-7 — Title`: the id the format adds is not read from the title again."""

    BLOCK = {"branch_format": "{item}-{slug}"}

    def test_the_title_loses_its_leading_id(self):
        self.assertEqual(deliver.item_title("# ITEM-7 — Hide the split\n", "ITEM-7"), "Hide the split")
        self.assertEqual(deliver.item_title("# ITEM-7: Hide the split\n", "ITEM-7"), "Hide the split")
        self.assertEqual(deliver.item_title("# Hide ITEM-7 split\n", "ITEM-7"), "Hide ITEM-7 split")

    def test_the_branch_names_the_id_once(self):
        self.assertEqual(deliver.branch_name(self.BLOCK, "ITEM-7", "# ITEM-7 — Hide the split\n", None),
                         "ITEM-7-hide-the-split")


RUN_FILES = textwrap.dedent('''\
    import sys, unittest, importlib.util
    suite = unittest.TestSuite()
    for path in sys.argv[1:]:
        spec = importlib.util.spec_from_file_location(path.replace("/", "_")[:-3], path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    result = unittest.TextTestRunner(stream=open("/dev/null", "w")).run(suite)
    for case, _ in result.failures + result.errors:
        print("FAIL: " + case.id().split(".")[-1])
    print("Ran %d of %d" % (result.testsRun, suite.countTestCases()))
    ''')


BEHAVIOUR = {"premises": [], "files": ["src/a.py", "tests/test_guard.py"], "mutations": [],
             "proving_tests": ["test_guard_refuses"]}


class TheSizeDecidesWhatRuns(Fixture):
    """What the first front-end delivery paid for a change of two files: a whole suite on
    the base before a line was written, one more to read a single red, one more on the
    branch. The size of what landed now decides: the test files the change touched, or
    the whole suite — once."""

    def setUp(self):
        super().setUp()
        (self.repo / "run_files.py").write_text(RUN_FILES, encoding="utf-8")
        (self.repo / "src" / "a.py").write_text(BUGGY, encoding="utf-8")
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-q", "-m", "buggy base")
        self.profile_with(suite__files_command="python3 run_files.py {tests}")

    def test_a_small_delivery_runs_only_the_test_files_it_touched(self):
        package = self.prepare(kind="behaviour-change")
        args = json.loads((package / "workflow-args.json").read_text(encoding="utf-8"))
        self.assertEqual(args["commands"]["tests"], "python3 run_files.py {tests}",
                         "the writer is handed the command that runs named test files")
        code, rec = self.conclude(package, PROVING_TEST, measure_data=BEHAVIOUR, source=SOURCE)
        self.assertEqual((rec["state"], rec["size"]["size"], rec["baseline"]["scope"]),
                         ("delivered", "small", "focused"), rec)
        self.assertEqual(rec["proof"]["red_on_base"]["result"], "red")
        self.assertIn("Ran 1 of 1", (package / "run-suite-base-with-test.txt").read_text(encoding="utf-8"))
        self.assertIn("Ran 1 of 1", (package / "run-suite-branch-focused.txt").read_text(encoding="utf-8"))
        self.assertFalse((package / "run-suite-branch.txt").exists(), "the whole suite never ran")
        self.assertEqual(rec["measured"]["suite"], "run-suite-branch-focused.txt")

    def test_the_related_test_files_the_measure_named_run_with_them(self):
        package = self.prepare(kind="behaviour-change")
        measure = dict(BEHAVIOUR, related_tests=["tests/test_a.py", "tests/gone.py"])
        code, rec = self.conclude(package, PROVING_TEST, measure_data=measure, source=SOURCE)
        self.assertEqual(rec["state"], "delivered", rec)
        self.assertIn("Ran 2 of 2", (package / "run-suite-branch-focused.txt").read_text(encoding="utf-8"))

    def test_the_plan_names_what_already_stands_with_its_address(self):
        package = self.prepare(kind="behaviour-change")
        man = json.loads((package / "deliver-manifest.json").read_text(encoding="utf-8"))
        heading = deliver.t("deliver.report.existing")
        measure = dict(BEHAVIOUR, existing=[
            {"kind": "definition", "what": "active line", "where": "src/a.py:12"},
            {"kind": "switch", "what": "release flag", "where": None}])
        plan = deliver.render_plan(man, measure)
        self.assertIn(heading, plan)
        self.assertIn("definition: active line — src/a.py:12", plan)
        self.assertIn(f"switch: release flag — {deliver.t('deliver.report.existing_none')}", plan)
        self.assertNotIn(heading, deliver.render_plan(man, BEHAVIOUR))

    def test_past_the_small_limit_the_whole_suite_runs_once(self):
        self.profile_with(suite__files_command="python3 run_files.py {tests}", delivery__small={"max_files": 1})
        package = self.prepare(kind="behaviour-change")
        code, rec = self.conclude(package, PROVING_TEST, measure_data=BEHAVIOUR, source=SOURCE)
        self.assertEqual((rec["state"], rec["size"]["size"], rec["baseline"]["scope"]),
                         ("delivered", "standard", "whole"), rec)
        self.assertIn("Ran 2 of 2", (package / "run-suite-branch.txt").read_text(encoding="utf-8"))

    def test_a_standard_delivery_no_sceptic_read_says_so_in_its_report(self):
        """The first real delivery on 3.0 was sized small by its one file, landed past the
        small limit and was delivered as a standard item no sceptic had read — and its
        report read like a small one's. The gap is said; a small delivery owes no line."""
        self.profile_with(suite__files_command="python3 run_files.py {tests}", delivery__small={"max_files": 1})
        package = self.prepare(kind="behaviour-change")
        code, rec = self.conclude(package, PROVING_TEST, measure_data=BEHAVIOUR, source=SOURCE)
        self.assertEqual((rec["state"], rec["size"]["size"], rec["proof"]["refutation"]),
                         ("delivered", "standard", None), rec)
        self.assertTrue(deliver.sceptic_owed(rec))
        self.assertIn(deliver.t("deliver.report.sceptic_not_read", reason=rec["size"]["reason"]),
                      (package / "report.md").read_text(encoding="utf-8"))

    def test_a_small_delivery_owes_no_sceptic(self):
        package = self.prepare(kind="behaviour-change")
        code, rec = self.conclude(package, PROVING_TEST, measure_data=BEHAVIOUR, source=SOURCE)
        self.assertEqual((rec["state"], rec["size"]["size"]), ("delivered", "small"), rec)
        self.assertFalse(deliver.sceptic_owed(rec))
        self.assertNotIn(deliver.t("deliver.report.sceptic"), (package / "report.md").read_text(encoding="utf-8"))
        self.assertIn("Ran 1 of 1", (package / "run-suite-base-with-test.txt").read_text(encoding="utf-8"),
                      "the red on the base still runs only the changed test files")

    def test_a_wide_path_is_never_small(self):
        self.profile_with(suite__files_command="python3 run_files.py {tests}", delivery__wide_paths=["src/a.py"])
        package = self.prepare(kind="behaviour-change")
        code, rec = self.conclude(package, PROVING_TEST, measure_data=BEHAVIOUR, source=SOURCE)
        self.assertEqual((rec["size"]["size"], rec["baseline"]["scope"]), ("standard", "whole"))
        self.assertIn("src/a.py", rec["size"]["reason"])

    def test_a_test_green_without_the_change_is_blocked(self):
        package = self.prepare(kind="behaviour-change")
        code, rec = self.conclude(package, WEAK_TEST, measure_data=BEHAVIOUR, source=SOURCE)
        self.assertEqual((rec["state"], rec["proof"]["red_on_base"]["result"]), ("blocked", "green"))

    def test_a_red_on_the_base_under_another_name_is_partial_and_passes(self):
        """The measurement names a test the way a person would; the runner prints it its
        own way. Something went red on the base: the proof stands, and the report says
        which name was not read."""
        package = self.prepare(kind="behaviour-change")
        measure = dict(BEHAVIOUR, proving_tests=["the guard refuses ten"])
        code, rec = self.conclude(package, PROVING_TEST, measure_data=measure, source=SOURCE)
        red = rec["proof"]["red_on_base"]
        self.assertEqual((rec["state"], red["result"], red["missing"]), ("delivered", "partial", ["the guard refuses ten"]), rec)
        self.assertIn("not read among the reds", (package / "report.md").read_text(encoding="utf-8"))

    def test_a_fixture_beside_the_tests_is_not_handed_to_the_runner(self):
        self.profile_with(suite__files_command="python3 run_files.py {tests}", suite__test_files=["tests/test_*.py"],
                          delivery__budget={"max_files": 3, "max_lines": 60})
        package = self.prepare(kind="behaviour-change")
        (self.tree_of(package) / "tests" / "helper.py").write_text("raise SystemExit('a helper is not a test')\n", encoding="utf-8")
        measure = dict(BEHAVIOUR, files=BEHAVIOUR["files"] + ["tests/helper.py"])
        code, rec = self.conclude(package, PROVING_TEST, measure_data=measure, source=SOURCE)
        self.assertEqual(rec["state"], "delivered", rec)

    def test_the_writer_s_commit_message_is_the_subject(self):
        package = self.prepare(kind="behaviour-change")
        tree = self.tree_of(package)
        (tree / "tests" / "test_guard.py").write_text(PROVING_TEST, encoding="utf-8")
        (tree / "src" / "a.py").write_text(SOURCE, encoding="utf-8")
        code, rec = self.conclude_result(package, {"stage": "deliver", "size": "small", "measure": BEHAVIOUR, "refutation": None,
                                                   "written": {"files": BEHAVIOUR["files"], "skipped": [], "reused": [], "blocked": None,
                                                               "tests_run": {"command": "x", "red_first": True, "green": True},
                                                               "commit_message": "refusing ten at the guard"}})
        self.assertEqual(rec["state"], "delivered", rec)
        subject = subprocess.run(("git", "log", "-1", "--format=%s"), cwd=tree,
                                 capture_output=True, text=True).stdout.strip()
        self.assertEqual(subject, "ITEM-1 - refusing ten at the guard")
        self.assertEqual(rec["branch"]["sha"], subprocess.run(("git", "rev-parse", "HEAD"), cwd=tree,
                                                              capture_output=True, text=True).stdout.strip())

    def test_a_writer_that_stopped_blocks_for_a_person_and_nothing_runs(self):
        package = self.prepare(kind="behaviour-change")
        code, rec = self.conclude_result(package, {"stage": "deliver", "size": "small", "measure": BEHAVIOUR, "refutation": None,
                                                   "written": {"files": [], "skipped": [], "reused": [],
                                                               "blocked": "src/a.py:2 already refuses ten",
                                                               "tests_run": {"command": None, "red_first": None, "green": None}}})
        self.assertEqual((code, rec["state"], rec["owner"]), (1, "blocked", "person"))
        self.assertIn("already refuses ten", rec["reason"])
        self.assertEqual(list(package.glob("run-suite-*.txt")), [])


class TheBaseIsMeasuredOnlyWhenTheBranchIsRed(Fixture):
    """A green branch owes no baseline. A red one is compared by name against the base,
    measured then — over the base's version of every changed file — or reused from a
    package that measured the same tree with the same command."""

    OLD_RED = "import unittest\n\nclass Old(unittest.TestCase):\n    def test_was_always_red(self):\n        self.fail('inherited')\n"

    def inherit_a_red(self):
        (self.repo / "tests" / "test_old.py").write_text(self.OLD_RED, encoding="utf-8")
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-q", "-m", "an inherited red")

    def test_a_green_branch_measures_no_base(self):
        package = self.prepare()
        code, rec = self.conclude(package, PROVING_TEST)
        self.assertEqual((rec["state"], rec["baseline"]["base"], rec["baseline"]["base_reds"]),
                         ("delivered", "not_needed", []))
        self.assertFalse((package / "run-suite-base.txt").exists())

    def test_a_red_the_base_already_had_is_measured_then_and_is_not_new(self):
        self.inherit_a_red()
        package = self.prepare()
        code, rec = self.conclude(package, PROVING_TEST)
        self.assertEqual(rec["state"], "delivered", rec)
        b = rec["baseline"]
        self.assertEqual((b["base"], b["base_reds"], b["new_reds"]), ("measured", ["test_was_always_red"], []))
        self.assertEqual(rec["measured"]["base"], "run-suite-base.txt")
        self.assertIn("Ran 2 of 2", (package / "run-suite-base.txt").read_text(encoding="utf-8"),
                      "the base ran without the test the delivery wrote")
        self.assertTrue((self.tree_of(package) / "tests" / "test_guard.py").exists(), "and the delivery's file is back")

    def test_a_base_already_measured_is_reused(self):
        self.inherit_a_red()
        (self.root / "packages").mkdir(exist_ok=True)
        first = self.prepare(package="packages/repo-x-first")
        self.conclude(first, PROVING_TEST)
        git(self.repo, "checkout", "-q", "main")
        second = self.root / "packages" / "repo-x-second"
        with contextlib.redirect_stdout(io.StringIO()):
            deliver.prepare(argparse.Namespace(repo="repo-x", item="ITEM-2", item_file=str(self.root / "item.md"),
                                               kind=None, branch="ITEM-2-another", package=str(second),
                                               fix=None, shape="debt", type=None))
        out = io.StringIO()
        (self.repo / "tests" / "test_guard.py").write_text(PROVING_TEST, encoding="utf-8")
        (second / "m.json").write_text(json.dumps(MEASURE), encoding="utf-8")
        with contextlib.redirect_stdout(out):
            deliver.conclude(argparse.Namespace(package=str(second), measure=str(second / "m.json")))
        rec = json.loads((second / "delivery.json").read_text(encoding="utf-8"))
        self.assertEqual((rec["state"], rec["baseline"]["base"], rec["baseline"]["new_reds"]),
                         ("delivered", "reused", []), rec)
        self.assertIn(str(first / "run-suite-base.txt"), out.getvalue())

    def test_a_package_concluded_again_reuses_the_base_it_measured(self):
        """One package, one base. The first real delivery on 3.0 was concluded twice —
        blocked, then delivered — and the base's whole suite ran both times."""
        self.inherit_a_red()
        package = self.prepare()
        code, rec = self.conclude(package, PROVING_TEST)
        self.assertEqual(rec["baseline"]["base"], "measured")
        base = package / "run-suite-base.txt"
        base.write_text(base.read_text(encoding="utf-8") + "the first conclude's own run\n", encoding="utf-8")
        code, rec = self.conclude(package, PROVING_TEST)
        b = rec["baseline"]
        self.assertEqual((rec["state"], b["base"], b["base_reds"], b["new_reds"]),
                         ("delivered", "reused", ["test_was_always_red"], []), rec)
        self.assertIn("the first conclude's own run", base.read_text(encoding="utf-8"), "it was not run again")

    def test_a_base_run_that_did_not_finish_or_was_taken_by_another_command_is_measured_again(self):
        self.inherit_a_red()
        package = self.prepare()
        self.conclude(package, PROVING_TEST)
        base = package / "run-suite-base.txt"
        base.write_text("FAIL: test_was_always_red\nRan 1 of 2\n", encoding="utf-8")
        code, rec = self.conclude(package, PROVING_TEST)
        self.assertEqual(rec["baseline"]["base"], "measured", "a run cut short is not a measurement")
        self.assertIn("Ran 2 of 2", base.read_text(encoding="utf-8"))
        base.write_text(base.read_text(encoding="utf-8") + "the first conclude's own run\n", encoding="utf-8")
        self.profile_with(suite__command="python3 ./run_tests.py")
        code, rec = self.conclude(package, PROVING_TEST)
        self.assertEqual(rec["baseline"]["base"], "measured", "another command is another measurement")
        self.assertNotIn("the first conclude's own run", base.read_text(encoding="utf-8"))

    def test_a_new_red_blocks_and_leaves_the_next_write_what_it_needs(self):
        package = self.prepare(kind="behaviour-change")
        broken = "def accept(x):\n    return False\n"
        code, rec = self.conclude(package, PROVING_TEST, measure_data=BEHAVIOUR, source=broken)
        self.assertEqual((code, rec["state"], rec["owner"]), (1, "blocked", "author"))
        self.assertEqual(rec["baseline"]["new_reds"], ["test_accept_small"])
        args = json.loads((package / "workflow-args.json").read_text(encoding="utf-8"))
        self.assertIn("test_accept_small", args["previous"]["reason"])
        self.assertIn("FAIL: test_accept_small", args["previous"]["output"])
        self.assertEqual(args["measure"]["files"], BEHAVIOUR["files"], "the write stage is pasted, not composed")


class TheEvidenceComesLast(Fixture):
    """Evidence illustrates a delivery that is done. It is produced once, after the
    item is delivered, and what it holds changes nothing."""

    def test_an_undelivered_package_has_no_evidence(self):
        package = self.prepare()
        with self.assertRaises(common.UsageError), contextlib.redirect_stdout(io.StringIO()):
            deliver.evidence(argparse.Namespace(package=str(package), collected=None))

    def test_without_a_declared_way_of_showing_the_tests_are_the_evidence(self):
        package = self.prepare()
        self.conclude(package, PROVING_TEST)
        before = (package / "delivery.json").read_text(encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(deliver.evidence(argparse.Namespace(package=str(package), collected=None)), 0)
        text = (package / "evidence.md").read_text(encoding="utf-8")
        self.assertIn("ITEM-1 - ", text)                       # the commit
        self.assertIn("tests/test_guard.py", text)
        self.assertIn("test_guard_refuses red", text)          # the mutant it killed
        self.assertIn("the tests above are the evidence", text)
        self.assertEqual((package / "delivery.json").read_text(encoding="utf-8"), before)
        self.assertTrue(list((self.root / "reports" / "repo-x" / "ITEM-1-deliver").glob("evidence-*.md")))

    def test_what_the_collector_showed_and_could_not_show_are_both_written(self):
        package = self.prepare()
        self.conclude(package, PROVING_TEST)
        collected = package / "evidence-result.json"
        collected.write_text(json.dumps({"stage": "evidence", "evidence": {
            "captures": [{"criterion": "ten is refused", "observed": "the screen shows the refusal", "artifact": "shots/ten.png"}],
            "not_collected": [{"criterion": "nine passes", "reason": "the environment did not come up"}],
            "environment": {"raised": ["up"], "taken_down": True}}}), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(deliver.evidence(argparse.Namespace(package=str(package), collected=str(collected))), 0)
        text = (package / "evidence.md").read_text(encoding="utf-8")
        self.assertIn("✔ ten is refused — the screen shows the refusal · shots/ten.png", text)
        self.assertIn("? nine passes — the environment did not come up", text)
        self.assertEqual(json.loads((package / "delivery.json").read_text(encoding="utf-8"))["state"], "delivered")


class AStageReturnReadsFromAnyFileAtHand(unittest.TestCase):
    """`plan` was handed the harness's task output and rendered an empty plan in silence."""

    MEASURE = {"premises": [], "files": ["src/a.py"], "mutations": [], "checked": []}

    def write(self, data):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(data, f); f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_bare_workflow_and_harness_envelope_give_the_same_return(self):
        workflow = {"stage": "measure", "measure": self.MEASURE, "written": None, "refutation": None}
        for data in (self.MEASURE, workflow, {"summary": "s", "logs": [], "result": workflow}):
            self.assertEqual(deliver.stage_return(self.write(data), "measure"), self.MEASURE)

    def test_a_return_a_stage_may_leave_empty_reads_as_none(self):
        """A small item has no sceptic: `refutation` is null in its return, and that is
        not a wrong file."""
        path = self.write({"stage": "deliver", "measure": {"files": []}, "written": {"files": []}, "refutation": None})
        self.assertIsNone(deliver.stage_return(path, "refutation", optional=True))

    def test_a_stage_without_the_key_asked_is_refused(self):
        workflow = {"stage": "measure", "measure": self.MEASURE, "written": None, "refutation": None}
        with self.assertRaises(deliver.UsageError):
            deliver.stage_return(self.write(workflow), "written")


if __name__ == "__main__":
    unittest.main()
