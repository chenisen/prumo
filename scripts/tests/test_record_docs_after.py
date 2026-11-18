"""A delivery's record stands for a later head when every commit after it touches only
documentation.

The method asks for the spec and the plan to be reconciled after the code. The first
delivery that did so (2026-11-17) reached its review one docs commit past its record, and
the suite gate read "not run" — although nothing the suite measures had changed.
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import json
from unittest import mock

import review
from review import code_free_since

BLOCK = {"source_roots": ["src"], "test_roots": ["tests"]}


def git(repo, *a):
    return subprocess.run(("git", "-C", str(repo), *a), capture_output=True, text=True,
                          check=True).stdout.strip()


def commit(repo, path, text):
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    git(repo, "add", path)
    git(repo, "commit", "-qm", path)
    return git(repo, "rev-parse", "HEAD")


class CodeFreeSince(unittest.TestCase):
    def setUp(self):
        self.repo = pathlib.Path(tempfile.mkdtemp())
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        self.record = commit(self.repo, "src/a.txt", "a")

    def test_only_documentation_after_the_record_keeps_it(self):
        head = commit(self.repo, "docs/plan.md", "plan")
        self.assertEqual(code_free_since(self.repo, BLOCK, self.record, head), ["docs/plan.md"])

    def test_a_test_or_a_source_file_after_the_record_voids_it(self):
        head = commit(self.repo, "tests/a_test.txt", "t")
        self.assertIsNone(code_free_since(self.repo, BLOCK, self.record, head))
        head = commit(self.repo, "src/b.txt", "b")
        self.assertIsNone(code_free_since(self.repo, BLOCK, self.record, head))

    def test_no_roots_declared_decides_nothing(self):
        head = commit(self.repo, "docs/plan.md", "plan")
        self.assertIsNone(code_free_since(self.repo, {}, self.record, head))

    def test_a_record_that_is_not_an_ancestor_of_the_head_decides_nothing(self):
        git(self.repo, "checkout", "-qb", "other", self.record + "~0")
        side = commit(self.repo, "docs/side.md", "side")
        git(self.repo, "checkout", "-q", "-")
        head = commit(self.repo, "docs/plan.md", "plan")
        self.assertIsNone(code_free_since(self.repo, BLOCK, side, head))

    def test_a_root_named_as_a_prefix_is_not_a_neighbour_s_prefix(self):
        head = commit(self.repo, "srcdocs/note.md", "n")
        self.assertEqual(code_free_since(self.repo, BLOCK, self.record, head), ["srcdocs/note.md"])


class TheGateReadsIt(unittest.TestCase):
    """Wired into the suite gate: green with the record's numbers when only docs came
    after it, `not_run` naming the record's commit when code did."""

    def evidence(self, after_path):
        repos = pathlib.Path(tempfile.mkdtemp())
        repo = repos / "r"
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "t@example.com")
        git(repo, "config", "user.name", "t")
        record = commit(repo, "src/a.txt", "a")
        head = commit(repo, after_path, "x")
        pkg = pathlib.Path(tempfile.mkdtemp())
        (pkg / "manifest.json").write_text(json.dumps({"repo": "r", "head_sha": head}))
        (pkg / "capabilities.json").write_text(json.dumps({"checks": {"suite": {}}}))
        (pkg / "profile.yml").write_text("delivery:\n  source_roots: [src]\n  test_roots: [tests]\n")
        (pkg / "delivery-record.json").write_text(json.dumps({
            "branch": {"sha": record},
            "baseline": {"base_reds": ["old"], "new_reds": [], "predicted": 9, "executed": 9}}))
        with mock.patch.object(review, "dir_repositories", return_value=repos):
            return review.initial_evidence(pkg), record

    def test_docs_after_the_record_keep_the_suite_green(self):
        ev, record = self.evidence("docs/plan.md")
        self.assertEqual(ev["suite"], "green")
        self.assertIn("docs/plan.md", ev["suite_reason"])
        self.assertTrue(ev["delivery_record"])

    def test_code_after_the_record_leaves_it_not_run_and_named(self):
        ev, record = self.evidence("tests/a_test.txt")
        self.assertEqual(ev["suite"], "not_run")
        self.assertEqual(ev["delivery_record_sha"], record[:12])
        self.assertFalse(ev["delivery_record"])


if __name__ == "__main__":
    unittest.main()
