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
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import common
import deliver

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


class Spine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
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

    def conclude(self, package, test_text, measure_data=MEASURE, source=None):
        (self.repo / "tests" / "test_guard.py").write_text(test_text, encoding="utf-8")
        if source is not None:
            (self.repo / "src" / "a.py").write_text(source, encoding="utf-8")
        measure, written = package / "m.json", package / "w.json"
        measure.write_text(json.dumps(measure_data), encoding="utf-8")
        written.write_text(json.dumps({"files": list(measure_data["files"]),
                                       "skipped": ["the boundary at 9"]}), encoding="utf-8")
        args = argparse.Namespace(package=str(package), measure=str(measure),
                                  written=str(written), refutation=None)
        with contextlib.redirect_stdout(io.StringIO()):
            code = deliver.conclude(args)
        return code, json.loads((package / "delivery.json").read_text(encoding="utf-8"))

    def test_prepare_cuts_the_branch_reads_the_baseline_and_marks(self):
        package = self.prepare()
        man = json.loads((package / "deliver-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(man["branch"]["name"], "ITEM-1-prove-the-refusal-above-ten")
        self.assertEqual(man["baseline"], {"base_reds": [], "predicted": 1, "executed": 1})
        head = subprocess.run(("git", "branch", "--show-current"), cwd=self.repo,
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(head, man["branch"]["name"])
        self.assertTrue(deliver.marker().exists())
        self.assertEqual(json.loads(deliver.marker().read_text())["worktree"], str(self.repo))

    def test_a_dirty_tree_is_a_stop(self):
        (self.repo / "src" / "a.py").write_text(SOURCE + "# pending\n", encoding="utf-8")
        with self.assertRaises(common.UsageError), contextlib.redirect_stdout(io.StringIO()):
            self.prepare()

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
        self.assertFalse(deliver.marker().exists())
        self.assertTrue((self.root / "reports" / "repo-x" / "ITEM-1-deliver").is_dir())

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
        self.assertFalse(deliver.marker().exists())

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

    def test_an_epic_is_not_delivered(self):
        with self.assertRaises(common.UsageError):
            self.prepare(shape="epic")

    def test_a_kind_the_shape_does_not_allow_is_refused(self):
        with self.assertRaises(common.UsageError):
            self.prepare(kind="coverage-gap", shape="bug")

    def test_a_file_outside_the_named_ones_is_outside_the_scope(self):
        package = self.prepare()
        (self.repo / "src" / "a.py").write_text(SOURCE + "\n", encoding="utf-8")
        code, rec = self.conclude(package, PROVING_TEST)
        self.assertEqual(rec["state"], "blocked")
        self.assertEqual(rec["diff"]["outside_scope"], ["src/a.py"])


if __name__ == "__main__":
    unittest.main()
