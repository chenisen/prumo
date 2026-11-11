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
    for case, _ in result.failures + result.errors:
        print("FAIL: " + case.id().split(".")[-1])
    print("Ran %d of %d" % (result.testsRun, suite.countTestCases()))
    ''')

SOURCE = "def accept(x):\n    if x >= 10:\n        return False\n    return True\n"
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
                  for r in ("secrets", "static", "lint", "dependencies")},
               "suite": {"command": "python3 run_tests.py", "required": True,
                         "red_name_pattern": r"(?m)^FAIL: (\S+)",
                         "progress_pattern": r"(?m)^Ran (\d+) of (\d+)"}},
    "sensitive_data": {"fields": [], "forbidden_destinations": []},
    "size": {"ideal": 100, "acceptable": 300, "split": 1000, "file_ceiling": 1000},
    "anchors": {}, "evals": [],
    "delivery": {"branch_format": "{item}-{slug}", "commit_format": "{item} - {message}",
                 "worktree": False, "budget": {"max_files": 2, "max_lines": 60},
                 "source_roots": ["src"], "test_roots": ["tests"],
                 "mutation": {"command": None, "note": None}, "kinds": ["coverage-gap"]},
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

    def prepare(self):
        args = argparse.Namespace(repo="repo-x", item="ITEM-1",
                                  item_file=str(self.root / "item.md"), kind=None,
                                  branch=None, package=str(self.root / "pkg"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(deliver.prepare(args), 0)
        return self.root / "pkg"

    def conclude(self, package, test_text):
        (self.repo / "tests" / "test_guard.py").write_text(test_text, encoding="utf-8")
        measure, written = package / "m.json", package / "w.json"
        measure.write_text(json.dumps(MEASURE), encoding="utf-8")
        written.write_text(json.dumps({"files": ["tests/test_guard.py"],
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

    def test_a_file_outside_the_named_ones_is_outside_the_scope(self):
        package = self.prepare()
        (self.repo / "src" / "a.py").write_text(SOURCE + "\n", encoding="utf-8")
        code, rec = self.conclude(package, PROVING_TEST)
        self.assertEqual(rec["state"], "blocked")
        self.assertEqual(rec["diff"]["outside_scope"], ["src/a.py"])


if __name__ == "__main__":
    unittest.main()
