"""The guard's marker: script and hook have to resolve to the SAME file.

This test exists because of a bug that cost the whole protection without failing
anything. The marker's path was derived from the script's position
(`ROOT.parent.parent.parent`), which was correct while the method lived inside
`.claude/skills/`. Once it became a repository of its own, that same expression started
pointing at the FILESYSTEM ROOT: `review.py` wrote to `/.task-review-ativo`, the hook
read `$CLAUDE_PROJECT_DIR/.claude/.task-review-ativo`, and the guard over push, commit
and moving the item stopped firing — in silence, because nothing fails when a protection
disappears.

The lesson, and what this test pins down: a path two pieces have to share is not derived
twice. It is derived from one common origin — here, `CLAUDE_PROJECT_DIR`.
"""
import os
import pathlib
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import review

PRUMO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
HOOK = PRUMO_ROOT / "hooks" / "review-guard.sh"


class TestMarker(unittest.TestCase):
    def test_the_marker_lives_in_the_reviewed_project(self):
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/proj"}):
            self.assertEqual(review.marker(),
                             pathlib.Path("/proj/.claude/.task-review-ativo"))

    def test_with_no_declared_project_it_falls_back_to_the_current_directory(self):
        """Never the filesystem root, and never the prumo's own tree: one installation
        serves several projects, and the state 'a review is running' belongs to one of
        them at a time."""
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        with mock.patch.dict(os.environ, env, clear=True):
            m = review.marker()
        self.assertNotEqual(m, pathlib.Path("/.claude/.task-review-ativo"))
        self.assertEqual(m, pathlib.Path(os.getcwd()) / ".claude" / ".task-review-ativo")

    def test_script_and_hook_agree(self):
        """The end the bug broke. The hook is shell, so the comparison is textual: both
        have to use `CLAUDE_PROJECT_DIR` and the same suffix."""
        source = HOOK.read_text(encoding="utf-8")
        line = next((l for l in source.splitlines() if l.startswith("MARKER=")), None)
        self.assertIsNotNone(line, "the hook does not define MARKER")
        self.assertIn("CLAUDE_PROJECT_DIR", line)
        self.assertIn(".claude/.task-review-ativo", line)
        with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/proj"}):
            from_script = review.marker()
        suffix = re.search(r'\.claude/\.task-review-ativo', line).group(0)
        self.assertTrue(str(from_script).endswith(suffix))

    def test_mark_and_unmark_use_the_same_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            with mock.patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": t}):
                review.mark(pathlib.Path("/tmp/package-x"))
                self.assertTrue(review.marker().exists())
                review.unmark()
                self.assertFalse(review.marker().exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
