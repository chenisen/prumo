"""Which round a review is, what a later round carries, and which of its findings land
on code already read."""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import basis
import delta
import package as packager


def git(repo, *args):
    return subprocess.run(("git", "-C", str(repo), *args), capture_output=True, text=True,
                          check=True).stdout.strip()


def commit(repo, path, text):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"touch {path}")
    return git(repo, "rev-parse", "HEAD")


def new_repo():
    repo = pathlib.Path(tempfile.mkdtemp())
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    return repo


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def previous_package(findings=(), held=(), criteria=(), scripts=None):
    pkg = pathlib.Path(tempfile.mkdtemp())
    write(pkg / "findings.json", list(findings))
    write(pkg / "verdict.json", {"gates": [{"passed": not held, "items": list(held)}]})
    write(pkg / "criteria.json", list(criteria))
    if scripts is not None:
        write(pkg / "scripts_manual.json", scripts)
    return pkg


class TestWhichRoundItIs(unittest.TestCase):
    def setUp(self):
        self.repo = new_repo()
        self.first = commit(self.repo, "src/a.txt", "a")
        self.head = commit(self.repo, "src/a.txt", "b")
        self.pkg = previous_package()

    def plan(self, earlier, full=False, head=None):
        with mock.patch.object(delta, "history", return_value=earlier):
            return delta.plan_round(self.repo, "r", "branch", head or self.head, full=full)

    def line(self, **kw):
        return {"repo": "r", "origin": "branch", "head_sha": self.first, "package": str(self.pkg), **kw}

    def test_the_first_review_reads_the_whole_delivery(self):
        self.assertEqual(self.plan([]), {"n": 1, "mode": "full"})

    def test_the_second_reads_what_the_push_changed(self):
        plan = self.plan([self.line()])
        self.assertEqual((plan["n"], plan["mode"], plan["since"]), (2, "delta", self.first))

    def test_from_the_third_on_no_reviewer_runs(self):
        self.assertEqual(self.plan([self.line(), self.line()])["mode"], "recheck")

    def test_a_person_can_ask_for_the_whole_delivery_again(self):
        plan = self.plan([self.line()], full=True)
        self.assertEqual((plan["n"], plan["mode"]), (2, "full"))

    def test_a_round_whose_package_is_gone_cannot_be_built_on(self):
        self.assertEqual(self.plan([self.line(package="/nowhere")])["mode"], "full")
        self.assertEqual(self.plan([self.line(package=None)])["mode"], "full")

    def test_a_reviewed_head_the_repository_no_longer_has_cannot_be_built_on(self):
        self.assertEqual(self.plan([self.line(head_sha="0" * 40)])["mode"], "full")

    def test_the_same_head_is_said_so_the_caller_can_stop(self):
        self.assertTrue(self.plan([self.line()], head=self.first)["same_head"])


class TestWhatALaterRoundCarries(unittest.TestCase):
    def test_what_held_a_gate_is_open_and_the_rest_is_not(self):
        prev = previous_package(
            findings=[{"id": "COR-1", "claim": "x"}, {"id": "COR-4", "claim": "y"}, {"id": "SCO-2"}],
            held=["COR-1", "AC-2"],
            criteria=[{"id": "AC-1", "state": "met"}, {"id": "AC-2", "state": "not_met", "text": "t"},
                      {"id": "AC-3", "state": "not_verifiable", "decided_by": "qa"}],
            scripts=[{"goal": "g"}])
        pkg = pathlib.Path(tempfile.mkdtemp())
        carried = delta.carry(pkg, {"previous_package": str(prev)})
        self.assertEqual([f["id"] for f in carried["open"]], ["COR-1"])
        self.assertEqual([c["id"] for c in carried["open_criteria"]], ["AC-2"])
        self.assertEqual(carried["taken"], {"COR": 4, "SCO": 2})
        self.assertTrue(carried["scripts_carried"])
        self.assertTrue((pkg / "criteria.json").exists() and (pkg / "scripts_manual.json").exists())

    def test_a_round_that_wrote_no_script_carries_none(self):
        pkg = pathlib.Path(tempfile.mkdtemp())
        self.assertFalse(delta.carry(pkg, {"previous_package": str(previous_package())})["scripts_carried"])


PATCH = """diff --git a/src/a.php b/src/a.php
--- a/src/a.php
+++ b/src/a.php
@@ -10,3 +10,4 @@ class A
 context line
+    $domain->setTimeout(30);
 another context
-removed
"""


class TestAFindingOnCodeAlreadyRead(unittest.TestCase):
    def finding(self, **evidence):
        return {"id": "COR-3", "severity": "required", "evidence": [{"file": "src/a.php", **evidence}]}

    def flagged(self, *findings):
        return delta.mark_already_reviewed(list(findings), PATCH)

    def test_a_line_inside_a_hunk_of_the_push_is_on_the_push(self):
        self.assertEqual(self.flagged(self.finding(line=11)), [])

    def test_an_excerpt_the_push_added_is_on_the_push_whatever_the_line_says(self):
        self.assertEqual(self.flagged(self.finding(line=5028, excerpt="$domain->setTimeout( 30 );")), [])

    def test_a_line_the_push_did_not_touch_was_already_read(self):
        f = self.finding(line=400, excerpt="return $this->old();")
        self.assertEqual(self.flagged(f), ["COR-3"])
        self.assertTrue(f["already_reviewed"])

    def test_a_file_the_push_did_not_touch_was_already_read(self):
        f = {"id": "COR-3", "severity": "required", "evidence": [{"file": "src/b.php", "line": 11}]}
        self.assertEqual(self.flagged(f), ["COR-3"])

    def test_a_carried_finding_and_a_suggestion_are_left_alone(self):
        carried = {**self.finding(line=400), "carried_from": "pkg"}
        nit = {**self.finding(line=400), "id": "COR-5", "severity": "nit"}
        self.assertEqual(self.flagged(carried, nit), [])


class TestWhereADemandComesFrom(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        (self.root / "CLAUDE.md").write_text(
            "## Logs\n\n**Never** write a customer's document\nto a log.\n", encoding="utf-8")

    def ground(self, **kw):
        f = {"id": "SEC-1", "criterion": None, "rule": None, **kw}
        return f, basis.ground([f], {"AC-1"}, [self.root])

    def test_a_quote_that_is_in_the_file_is_a_rule(self):
        f, _ = self.ground(rule={"file": "CLAUDE.md", "quote": "never write a customer's document to a log"})
        self.assertTrue(f["rule"]["verified"])

    def test_a_quote_nobody_wrote_is_not(self):
        f, found = self.ground(rule={"file": "CLAUDE.md", "quote": "every argument of a call must be asserted"})
        self.assertFalse(f["rule"]["verified"])
        self.assertEqual(found["rules_not_found"], ["SEC-1 → CLAUDE.md"])

    def test_a_file_that_is_not_there_and_a_quote_too_short_to_mean_anything_are_not(self):
        self.assertFalse(self.ground(rule={"file": "docs/x.md", "quote": "never write a customer's document"})[0]["rule"]["verified"])
        self.assertFalse(self.ground(rule={"file": "CLAUDE.md", "quote": "Never"})[0]["rule"]["verified"])

    def test_a_criterion_the_item_never_had_is_dropped(self):
        f, found = self.ground(criterion="AC-9")
        self.assertIsNone(f["criterion"])
        self.assertEqual(found["unknown_criteria"], ["SEC-1 → AC-9"])
        self.assertEqual(self.ground(criterion="AC-1")[0]["criterion"], "AC-1")


class TestALaterRoundsPackage(unittest.TestCase):
    """The diff of a later round is what changed since the reviewed head, in the files
    the delivery owns — a base merged in between rounds brings files that are not a push."""

    def test_only_the_delivery_s_own_files_since_the_reviewed_head(self):
        repo = new_repo()
        commit(repo, "src/a.txt", "a\n")
        git(repo, "checkout", "-qb", "feature")
        reviewed = commit(repo, "src/feature.txt", "one\n")
        git(repo, "checkout", "-q", "-")
        base = commit(repo, "src/elsewhere.txt", "base moved\n")
        git(repo, "checkout", "-q", "feature")
        git(repo, "merge", "-q", "--no-edit", "-")
        head = commit(repo, "src/feature.txt", "one\ntwo\n")
        pkg = pathlib.Path(tempfile.mkdtemp())
        merge_base = git(repo, "merge-base", head, base)
        write(pkg / "manifest.json", {"repo": repo.name, "merge_base": merge_base, "head_sha": head,
                                      "round": {"n": 2, "mode": "delta", "since": reviewed}})
        with mock.patch.object(packager, "dir_repositories", return_value=repo.parent), \
                mock.patch.object(packager, "load_profile", return_value={}):
            change = packager.build(pkg)
        self.assertEqual(change["files"], ["src/feature.txt"])
        self.assertEqual(change["lines_changed"], 1)


if __name__ == "__main__":
    unittest.main()
