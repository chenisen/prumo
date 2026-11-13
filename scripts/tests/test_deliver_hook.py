"""The guard while deliveries run: rule 6, held by the hook and not by the prompt.

One marker per delivery in `.claude/.prumo-deliver-ativo.d/` — the worktree the writer
may touch, its branch and the files the measurement named. A write or a git command is
judged by the delivery whose worktree it lands in, and by nobody outside every worktree:
several agents deliver several items in one repository at once, and the guard must not
take one delivery's rules for the whole workspace.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
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
        self.project = pathlib.Path(self.tmp.name).resolve()  # macOS: /var is /private/var
        # hermetic: an installation that delegated merging must not leak in from the shell
        self.env = dict(os.environ)
        os.environ["PRUMO_CONFIG"] = str(self.project / "no-prumo.yml")
        self.markers = self.project / ".claude" / ".prumo-deliver-ativo.d"
        self.markers.mkdir(parents=True)
        self.repo = self.project / "repo"
        self.worktree = self.deliver("item-1", self.repo / ".worktrees" / "item-1", ["tests/test_a.py"])

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        self.tmp.cleanup()

    def deliver(self, branch, worktree, allowed, package=None):
        worktree.mkdir(parents=True, exist_ok=True)
        (self.markers / f"{branch}.json").write_text(json.dumps({
            "worktree": str(worktree), "branch": branch, "allowed": allowed,
            "package": str(package) if package else None}), encoding="utf-8")
        return worktree

    def bash(self, command):
        return run_hook(self.project, "Bash", command=command)

    def write(self, path):
        return run_hook(self.project, "Write", file_path=str(path), content="x")

    # ---- inside the delivery's worktree
    def test_push_from_the_worktree_is_blocked(self):
        self.assertEqual(self.bash(f"git -C {self.worktree} push origin item-1"), 2)
        self.assertEqual(self.bash(f"cd {self.worktree} && git push"), 2)

    def test_pushing_the_delivery_s_branch_from_anywhere_is_blocked(self):
        self.assertEqual(self.bash(f"git -C {self.repo} push origin item-1"), 2)
        self.assertEqual(self.bash(f"git -C {self.repo} push origin HEAD:item-1"), 2)

    def test_add_all_is_blocked(self):
        self.assertEqual(self.bash(f"git -C {self.worktree} add -A"), 2)
        self.assertEqual(self.bash(f"cd {self.worktree} && git add ."), 2)

    def test_rewriting_history_or_leaving_the_branch_is_blocked(self):
        for cmd in ("reset --hard origin/main", "rebase main", "checkout main", "checkout -B main",
                    "switch main", "merge main"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(f"git -C {self.worktree} {cmd}"), 2)

    def test_moving_the_item_is_blocked(self):
        self.assertEqual(run_hook(self.project, "mcp__tracker__move_card_batch"), 2)

    def test_a_write_inside_the_worktree_but_outside_the_named_files_is_blocked(self):
        self.assertEqual(self.write(self.worktree / "src" / "a.py"), 2)

    def test_before_the_measurement_names_a_file_no_write_passes(self):
        """The marker is on from `prepare`; the files come with `measure.json`. In
        between, the surveyor reads and nobody writes."""
        self.deliver("item-1", self.worktree, [], package=self.project / "no-such-pkg")
        self.assertEqual(self.write(self.worktree / "tests" / "test_a.py"), 2)

    def test_a_plan_that_asks_a_gate_admits_no_write_until_the_approval_matches(self):
        package = self.project / "pkg"
        package.mkdir()
        (package / "measure.json").write_text(json.dumps({"files": ["tests/test_b.py"]}), encoding="utf-8")
        (package / "plan.json").write_text(json.dumps({"gate": True, "digest": "d1"}), encoding="utf-8")
        self.deliver("item-1", self.worktree, [], package=package)
        target = self.worktree / "tests" / "test_b.py"
        self.assertEqual(self.write(target), 2)
        (package / "approval.json").write_text(json.dumps({"by": "x", "digest": "old"}), encoding="utf-8")
        self.assertEqual(self.write(target), 2)
        (package / "approval.json").write_text(json.dumps({"by": "x", "digest": "d1"}), encoding="utf-8")
        self.assertEqual(self.write(target), 0)

    def test_the_named_files_are_read_from_the_package_s_measure(self):
        package = self.project / "pkg"
        package.mkdir()
        (package / "measure.json").write_text(json.dumps({"files": ["tests/test_b.py"]}), encoding="utf-8")
        self.deliver("item-1", self.worktree, [], package=package)
        self.assertEqual(self.write(self.worktree / "tests" / "test_b.py"), 0)
        self.assertEqual(self.write(self.worktree / "tests" / "test_c.py"), 2)

    def test_a_commit_with_a_pathspec_and_adding_a_named_path_pass(self):
        self.assertEqual(self.bash(f"git -C {self.worktree} commit -m 'ITEM-1 - prove' -- tests/test_a.py"), 0)
        self.assertEqual(self.bash(f"git -C {self.worktree} add tests/test_a.py"), 0)

    def test_a_write_inside_the_named_files_passes(self):
        self.assertEqual(self.write(self.worktree / "tests" / "test_a.py"), 0)

    # ---- outside every delivery's worktree nothing of this applies
    def test_outside_the_worktree_writes_and_git_pass(self):
        """The checkout beside the worktree, the workspace, a package: another agent's
        task, or the conductor filing an agent's return."""
        for path in (self.repo / "src" / "a.py", self.project / "packages" / "p" / "measure-return.json",
                     self.project / "elsewhere" / "x.py"):
            with self.subTest(path=path):
                self.assertEqual(self.write(path), 0)
        for cmd in ("checkout -b other", "add -A", "push origin other", "rebase main"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(f"git -C {self.repo} {cmd}"), 0)

    def test_two_deliveries_in_one_repository_judge_each_its_own_worktree(self):
        other = self.deliver("item-2", self.repo / ".worktrees" / "item-2", ["src/b.py"])
        self.assertEqual(self.write(other / "src" / "b.py"), 0)
        self.assertEqual(self.write(self.worktree / "tests" / "test_a.py"), 0)
        self.assertEqual(self.write(self.worktree / "src" / "b.py"), 2, "item-2's file is not item-1's")
        self.assertEqual(self.write(other / "tests" / "test_a.py"), 2, "item-1's file is not item-2's")

    def test_a_delivery_made_in_place_holds_the_checkout_but_not_another_s_worktree(self):
        self.deliver("item-3", self.repo, ["src/c.py"])
        self.assertEqual(self.write(self.repo / "src" / "c.py"), 0)
        self.assertEqual(self.write(self.repo / "src" / "d.py"), 2)
        self.assertEqual(self.write(self.worktree / "tests" / "test_a.py"), 0, "the worktree is item-1's")

    def test_a_stale_marker_judges_nothing(self):
        old = time.time() - 5 * 3600
        os.utime(self.markers / "item-1.json", (old, old))
        self.assertEqual(self.write(self.worktree / "src" / "a.py"), 0)
        self.assertEqual(self.bash(f"git -C {self.worktree} push"), 0)

    def test_without_any_marker_push_and_writes_pass(self):
        (self.markers / "item-1.json").unlink()
        self.assertEqual(self.bash(f"git -C {self.worktree} push origin item-1"), 0)
        self.assertEqual(self.write(self.project / "anything.py"), 0)

    def test_merge_is_blocked_with_or_without_a_delivery(self):
        self.assertEqual(self.bash("glab mr merge 1"), 2)
        (self.markers / "item-1.json").unlink()
        self.assertEqual(self.bash("glab mr merge 1"), 2)


if __name__ == "__main__":
    unittest.main()
