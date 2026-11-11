"""The guard while a delivery runs: rule 6, held by the hook and not by the prompt.

RED until phase 1: the hook does not yet read `.claude/.prumo-deliver-ativo`. The marker
is JSON — the worktree the writer may touch and the files the measurement named — so the
hook can refuse a write outside them without asking the model whether it meant to.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

PRUMO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
HOOK = PRUMO_ROOT / "hooks" / "review-guard.sh"


def run_hook(project: pathlib.Path, tool: str, **tool_input) -> int:
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input})
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project)}
    r = subprocess.run(["bash", str(HOOK)], input=payload, capture_output=True,
                       text=True, env=env)
    return r.returncode


class DeliveryGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = pathlib.Path(self.tmp.name)
        (self.project / ".claude").mkdir()
        self.worktree = self.project / "repo" / ".worktrees" / "item-1"
        self.worktree.mkdir(parents=True)
        self.marker = self.project / ".claude" / ".prumo-deliver-ativo"
        self.marker.write_text(json.dumps({
            "worktree": str(self.worktree),
            "allowed": ["tests/test_a.py"],
        }), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def bash(self, command):
        return run_hook(self.project, "Bash", command=command)

    def write(self, path):
        return run_hook(self.project, "Write", file_path=path, content="x")

    # ---- blocked while the marker exists
    def test_push_is_blocked(self):
        self.assertEqual(self.bash("git push origin item-1"), 2)

    def test_add_all_is_blocked(self):
        self.assertEqual(self.bash("git add -A"), 2)
        self.assertEqual(self.bash("git add ."), 2)

    def test_rewriting_the_base_is_blocked(self):
        for cmd in ("git reset --hard origin/main", "git rebase main",
                    "git checkout main", "git checkout -B main"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), 2)

    def test_moving_the_item_is_blocked(self):
        self.assertEqual(run_hook(self.project, "mcp__tracker__move_card_batch"), 2)

    def test_a_write_outside_the_worktree_is_blocked(self):
        self.assertEqual(self.write(str(self.project / "repo" / "tests" / "test_a.py")), 2)

    def test_a_write_inside_the_worktree_but_outside_the_named_files_is_blocked(self):
        self.assertEqual(self.write(str(self.worktree / "src" / "a.py")), 2)

    def test_the_named_files_are_read_from_the_package_s_measure(self):
        """The marker is written before the measurement exists; the files come later,
        from `measure.json` in the package, and the guard reads them from there."""
        package = self.project / "pkg"
        package.mkdir()
        (package / "measure.json").write_text(json.dumps({"files": ["tests/test_b.py"]}),
                                              encoding="utf-8")
        self.marker.write_text(json.dumps({"worktree": str(self.worktree),
                                           "allowed": [], "package": str(package)}),
                               encoding="utf-8")
        self.assertEqual(self.write(str(self.worktree / "tests" / "test_b.py")), 0)
        self.assertEqual(self.write(str(self.worktree / "tests" / "test_c.py")), 2)

    # ---- allowed while the marker exists
    def test_a_commit_with_a_pathspec_passes(self):
        self.assertEqual(self.bash("git commit -m 'ITEM-1 - prove the guard' -- tests/test_a.py"), 0)

    def test_adding_a_named_path_passes(self):
        self.assertEqual(self.bash("git add tests/test_a.py"), 0)

    def test_a_write_inside_the_named_files_passes(self):
        self.assertEqual(self.write(str(self.worktree / "tests" / "test_a.py")), 0)

    # ---- without the marker nothing but merge is blocked
    def test_without_the_marker_push_and_writes_pass(self):
        self.marker.unlink()
        self.assertEqual(self.bash("git push origin item-1"), 0)
        self.assertEqual(self.write(str(self.project / "anything.py")), 0)

    def test_merge_is_blocked_with_or_without_the_marker(self):
        self.assertEqual(self.bash("glab mr merge 1"), 2)
        self.marker.unlink()
        self.assertEqual(self.bash("glab mr merge 1"), 2)


if __name__ == "__main__":
    unittest.main()
