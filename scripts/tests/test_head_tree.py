"""The agents read the reviewed HEAD, checked out on its own.

Measured on 2026-11-10: the reviewers read the repository's checkout, which was behind;
an architecture reviewer said a handler called a method that did not exist, and the
verifier confirmed it. The method was in the base and in the head — only the checkout
lacked it. A `required` false positive that closes gate 1 alone, and the sceptic cannot
catch it, because it reads the same wrong tree.
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import review


def git(cwd, *args):
    return subprocess.run(("git", *args), cwd=cwd, capture_output=True, text=True,
                          check=True).stdout.strip()


class TheHeadTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name).resolve()
        self.repo = root / "repo-x"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "handler.php").write_text("old\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        git(self.repo, "checkout", "-qb", "item")
        (self.repo / "handler.php").write_text("calls the new method\n", encoding="utf-8")
        git(self.repo, "commit", "-qam", "the item")
        self.head = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "-q", "main")          # the checkout is NOT the head
        self.package = root / "packages" / "repo-x-123"
        self.package.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_tree_holds_the_head_while_the_checkout_does_not(self):
        tree = review.add_head_tree(self.repo, self.head, self.package)
        self.assertEqual((tree / "handler.php").read_text(), "calls the new method\n")
        self.assertEqual((self.repo / "handler.php").read_text(), "old\n")

    def test_no_branch_is_created_or_moved(self):
        before = git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads")
        review.add_head_tree(self.repo, self.head, self.package)
        self.assertEqual(git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)",
                             "refs/heads"), before)

    def test_the_tree_stays_out_of_git_status(self):
        review.add_head_tree(self.repo, self.head, self.package)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_the_review_end_takes_the_tree_and_only_its_own(self):
        tree = review.add_head_tree(self.repo, self.head, self.package)
        review.remove_head_tree(self.package)
        self.assertFalse(tree.exists())
        self.assertNotIn(str(tree), git(self.repo, "worktree", "list"))
        review.remove_head_tree(self.package)            # twice is not an error
        review.remove_head_tree(None)

    def test_a_head_git_refuses_gives_no_tree_and_the_review_goes_on(self):
        self.assertIsNone(review.add_head_tree(self.repo, "0" * 40, self.package))


class TheWorkflowHandsTheTreeToEveryAgent(unittest.TestCase):
    """Reviewers, both sceptics and the planner: whoever reads code reads the head."""

    def test_every_agent_that_reads_code_gets_the_head_tree(self):
        src = (ROOT.parent / "workflows" / "task-review.js").read_text(encoding="utf-8")
        self.assertIn("input.head_tree", src)
        self.assertGreaterEqual(src.count("HEAD_TREE,"), 4)


class KnowledgeFoldsTheTreeBack(unittest.TestCase):
    def test_a_file_read_in_the_tree_counts_under_the_repository_s_path(self):
        import knowledge
        with tempfile.TemporaryDirectory() as d:
            package = pathlib.Path(d)
            (package / "head-tree.json").write_text(json.dumps(
                {"path": "/r/x/.worktrees/prumo-review-1", "repo": "/r/x", "sha": "a"}))
            fold = knowledge.head_tree_folder(package)
            self.assertEqual(fold("/r/x/.worktrees/prumo-review-1/app/H.php"), "/r/x/app/H.php")
            self.assertEqual(fold("/r/x/app/Other.php"), "/r/x/app/Other.php")
            self.assertEqual(fold("/r/x/.worktrees/prumo-review-10/a"), "/r/x/.worktrees/prumo-review-10/a")

    def test_a_package_with_no_tree_folds_nothing(self):
        import knowledge
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(knowledge.head_tree_folder(pathlib.Path(d))("/a/b"), "/a/b")


if __name__ == "__main__":
    unittest.main()
