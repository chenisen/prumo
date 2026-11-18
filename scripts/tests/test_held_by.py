"""A block names the marker that holds it, whose run it is, how old, and how it is let go.

On 2026-11-18 a commit was held by a review marker another session had left, with only
"a review does not alter the target repository's history" to go on: telling a live run
from a leftover meant opening `.claude/` by hand.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import branch_guard

PRUMO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
HOOK = PRUMO_ROOT / "hooks" / "review-guard.sh"


class HeldBy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = pathlib.Path(self.tmp.name)
        (self.project / ".claude").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_review_marker_names_its_package_and_review_release(self):
        m = self.project / ".claude" / ".task-review-ativo"
        m.write_text(json.dumps({"package": "/pk/api-review-52468-r9", "started_at": "2026-11-18"}))
        text = branch_guard.held_by([m])
        self.assertIn("/pk/api-review-52468-r9", text)
        self.assertIn("review.py release", text)
        self.assertIn("0 min", text)

    def test_a_delivery_marker_names_its_own_release(self):
        m = self.project / "x.json"
        m.write_text(json.dumps({"package": "/pk/deliver-1", "worktree": "/w"}))
        self.assertIn("deliver.py release --package /pk/deliver-1", branch_guard.held_by([m]))

    def test_an_unreadable_marker_adds_nothing(self):
        m = self.project / "broken.json"
        m.write_text("{not json")
        self.assertEqual(branch_guard.held_by([m]), "")

    def test_a_delivery_block_names_the_delivery_that_holds_it(self):
        markers = self.project / "markers"; markers.mkdir()
        (markers / "pkg-1.json").write_text(json.dumps(
            {"package": "/pk/deliver-52610", "worktree": str(self.project), "branch": "b"}))
        reason = branch_guard.judge_delivery(markers, "mcp__tracker__move_card_batch", "", "", str(self.project))
        self.assertIn("deliver.py release --package /pk/deliver-52610", reason)
        self.assertIsNone(branch_guard.judge_delivery(markers, "Read", "", "", str(self.project)))

    def test_the_hook_says_it_when_a_review_blocks_a_push(self):
        (self.project / ".claude" / ".task-review-ativo").write_text(
            json.dumps({"package": "/pk/review-of-52468", "started_at": "2026-11-18"}))
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push origin x"}})
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.project),
               "PRUMO_CONFIG": str(self.project / "no-prumo.yml")}
        r = subprocess.run(["bash", str(HOOK)], input=payload, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("/pk/review-of-52468", r.stderr)
        self.assertIn("review.py release", r.stderr)


if __name__ == "__main__":
    unittest.main()
