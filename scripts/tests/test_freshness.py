"""`review.py freshness` — a verdict speaks for the head it measured, never for what
came after it.

Born from a measured failure: a delivery shipped four defects to its proposal inside two
commits — a batch fix and a refactor — made AFTER its only review, because nothing at
publication time asked whether the verdict still spoke for the branch.
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
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import common
import review


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


class TestAVerdictSpeaksOnlyForTheHeadItMeasured(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        (root / "repositories").mkdir()
        (root / "reports").mkdir()
        (root / "profiles").mkdir()
        self.repo = root / "repositories" / "repo-x"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "user.email", "t@t")
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "one")
        git(self.repo, "checkout", "-q", "-b", "feature")
        (root / "prumo.yml").write_text(
            "prumo: 1\n"
            "language: en\n"
            "paths:\n"
            "  repositories: ./repositories\n"
            "  profiles: ./profiles\n"
            "  reports: ./reports\n"
            "tracker:\n"
            "  type: businessmap\n"
            "  url: \"https://example.invalid/cards/{id}\"\n"
            "forge:\n"
            "  type: gitlab\n", encoding="utf-8")
        self.root = root
        self.env = dict(os.environ)
        os.environ["PRUMO_CONFIG"] = str(root / "prumo.yml")
        common.cfg.cache_clear()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        common.cfg.cache_clear()
        self.tmp.cleanup()

    def head(self) -> str:
        return subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()

    def record(self, head: str, verdict: str):
        line = {"date": "2026-01-01", "repo": "repo-x", "origin": "feature",
                "head_sha": head, "verdict": verdict}
        with (self.root / "reports" / "runs.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")

    def freshness(self):
        args = argparse.Namespace(repo="repo-x", origin="feature")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = review.freshness(args)
        return code, out.getvalue()

    def test_a_branch_nobody_reviewed_is_not_fresh(self):
        code, out = self.freshness()
        self.assertEqual(code, 1)
        self.assertIn("no concluded review", out)

    def test_the_reviewed_head_with_an_approved_verdict_is_fresh(self):
        self.record(self.head(), "APPROVED")
        code, out = self.freshness()
        self.assertEqual(code, 0)
        self.assertIn("FRESH", out)

    def test_a_commit_after_the_review_voids_even_an_approved_verdict(self):
        self.record(self.head(), "APPROVED")
        (self.repo / "a.txt").write_text("two\n", encoding="utf-8")
        git(self.repo, "commit", "-aqm", "a batch fix nobody read")
        code, out = self.freshness()
        self.assertEqual(code, 1)
        self.assertIn("STALE", out)
        self.assertIn("1 commit(s)", out)

    def test_an_open_verdict_on_the_same_head_is_not_publishable_as_approval(self):
        self.record(self.head(), "INTERVENTION REQUIRED")
        code, out = self.freshness()
        self.assertEqual(code, 1)
        self.assertIn("OPEN", out)

    def test_the_newest_record_decides_not_the_first(self):
        self.record("0" * 40, "INTERVENTION REQUIRED")
        self.record(self.head(), "APPROVED")
        code, out = self.freshness()
        self.assertEqual(code, 0)

    def test_an_unknown_branch_is_an_impediment_not_a_pass(self):
        line = {"date": "2026-01-01", "repo": "repo-x", "origin": "no-such-branch",
                "head_sha": self.head(), "verdict": "APPROVED"}
        with (self.root / "reports" / "runs.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
        args = argparse.Namespace(repo="repo-x", origin="no-such-branch")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = review.freshness(args)
        self.assertEqual(code, 1)
        self.assertIn("not found", out.getvalue())


if __name__ == "__main__":
    unittest.main()
