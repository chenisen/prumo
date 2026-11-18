"""The proof registry is measured where the reviewed head has it.

The preflight runs before the head is known and looks in the checkout. On 2026-11-17 the
checkout sat on the base branch while the delivery's spec was its own first commit, and
the report said the registry did not resolve with the spec right there in the diff.
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from preflight import proof_registry_at_head

UNRESOLVED = {"type": "specs-in-repo", "root": ".claude/specs", "resolve": False, "reason": "x"}


def git(repo, *a):
    return subprocess.run(("git", "-C", str(repo), *a), capture_output=True, text=True,
                          check=True).stdout.strip()


class ProofRegistryAtHead(unittest.TestCase):
    def setUp(self):
        self.repo = pathlib.Path(tempfile.mkdtemp())
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "a.txt").write_text("a")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-qm", "base")
        git(self.repo, "checkout", "-qb", "delivery")
        spec = self.repo / ".claude" / "specs" / "1-x" / "spec.md"
        spec.parent.mkdir(parents=True)
        spec.write_text("spec")
        git(self.repo, "add", ".claude")
        git(self.repo, "commit", "-qm", "spec")
        self.head = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "-q", "main")

    def test_a_registry_only_the_head_holds_resolves_and_says_where(self):
        r = proof_registry_at_head(UNRESOLVED, self.repo, self.head)
        self.assertTrue(r["resolve"])
        self.assertIn(self.head[:12], r["reason"])

    def test_a_registry_the_head_lacks_stays_unresolved(self):
        base = git(self.repo, "rev-parse", "main")
        self.assertIs(proof_registry_at_head(UNRESOLVED, self.repo, base), UNRESOLVED)

    def test_absent_or_already_resolved_is_left_alone(self):
        for reg in ({"type": "absent", "root": None, "resolve": False},
                    {**UNRESOLVED, "resolve": True, "reason": None}):
            self.assertIs(proof_registry_at_head(reg, self.repo, self.head), reg)


if __name__ == "__main__":
    unittest.main()
