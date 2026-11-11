"""Rule 5 — the whole route runs only on the person's own words, once.

RED until phase 4. Two things hold it: the skill is closed to the model's invocation, so
every argument is typed by a person; and the script refuses to start without the ask
written to a file, so a resumed or compacted session reads the authorisation back
instead of remembering it.
"""
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

PRUMO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SKILL = PRUMO_ROOT / "skills" / "run" / "SKILL.md"


class TestTheRouteIsThePersons(unittest.TestCase):
    def test_the_skill_is_closed_to_the_model(self):
        self.assertTrue(SKILL.exists(), "skills/run/SKILL.md")
        head = SKILL.read_text(encoding="utf-8").split("---")[1]
        self.assertRegex(head, re.compile(r"^disable-model-invocation:\s*true\s*$", re.M))

    def test_run_refuses_without_the_ask_on_disk(self):
        from run import start, AskMissing
        with self.assertRaises(AskMissing):
            start(repo="r", item="ITEM-1", ask_file=None)

    def test_run_refuses_an_empty_ask(self):
        """A file that is there and empty does not count as read — the same rule the
        review holds over the item."""
        import tempfile
        from run import start, AskMissing
        with tempfile.NamedTemporaryFile("w", suffix=".md") as f:
            f.flush()
            with self.assertRaises(AskMissing):
                start(repo="r", item="ITEM-1", ask_file=f.name)


if __name__ == "__main__":
    unittest.main()
