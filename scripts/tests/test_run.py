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


class TestEveryRoutingDecisionIsReadFromDisk(unittest.TestCase):
    """Rule 2 — `run.py next` composes nothing: each answer is what the two packages
    hold, and the same disk gives the same answer."""

    def setUp(self):
        import json, tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / "item.md").write_text("# x\n", encoding="utf-8")
        self.deliver = self.root / "d"; self.deliver.mkdir()
        self.review = self.root / "r"; self.review.mkdir()
        self.run = {"repo": "repo-x", "item": "ITEM-1",
                    "deliver_package": str(self.deliver), "review_package": str(self.review)}
        self.json = json

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, where, name, data):
        (where / name).write_text(self.json.dumps(data), encoding="utf-8")

    def decide(self):
        from run import decide
        return decide(self.run, self.root)

    def test_nothing_on_disk_asks_for_the_delivery_to_be_prepared(self):
        out = self.decide()
        self.assertEqual(out["kind"], "invoke")
        self.assertTrue(out["text"].startswith("deliver.py prepare --repo repo-x --item ITEM-1"))

    def test_a_prepared_delivery_asks_for_the_measure_stage(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.assertIn("stage: 'measure'", self.decide()["text"])

    def test_a_measured_delivery_asks_for_the_write_stage(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "measure.json", {"premises": [{"central": True, "state": "holds"}]})
        self.assertIn("stage: 'write'", self.decide()["text"])

    def test_a_central_premise_that_fails_skips_the_write_and_concludes(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "measure.json", {"premises": [{"central": True, "state": "fails"}]})
        out = self.decide()
        self.assertTrue(out["text"].startswith("deliver.py conclude"))
        self.assertNotIn("--written", out["text"])

    def test_a_written_delivery_concludes_with_every_return_it_has(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "measure.json", {"premises": []})
        self.put(self.deliver, "written.json", {})
        self.put(self.deliver, "refutation.json", {})
        out = self.decide()["text"]
        self.assertIn("--written", out); self.assertIn("--refutation", out)

    def test_a_blocked_delivery_is_a_stop_with_its_class_and_reason(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "delivery.json", {"state": "blocked", "reason": "new red: t_x",
                                                 "branch": {"name": "b"}})
        out = self.decide()
        self.assertEqual((out["kind"], out["class"], out["text"]), ("stop", "blocked", "new red: t_x"))

    def test_a_delivered_branch_hands_the_review_its_prepare(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "delivery.json", {"state": "delivered", "branch": {"name": "ITEM-1-b"}})
        out = self.decide()["text"]
        self.assertTrue(out.startswith("review.py prepare --repo repo-x --branch ITEM-1-b --item ITEM-1"))

    def test_a_prepared_review_asks_for_the_workflow_then_the_conclude(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "delivery.json", {"state": "delivered", "branch": {"name": "b"}})
        self.put(self.review, "manifest.json", {})
        self.assertIn("name: 'task-review'", self.decide()["text"])
        self.put(self.review, "findings.json", [])
        self.assertTrue(self.decide()["text"].startswith("review.py conclude"))

    def test_a_verdict_ends_the_run(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "delivery.json", {"state": "delivered", "branch": {"name": "b"}})
        self.put(self.review, "manifest.json", {})
        self.put(self.review, "verdict.json", {"verdict": "APPROVED"})
        self.assertEqual(self.decide()["class"], "approved")
        self.put(self.review, "verdict.json", {"verdict": "INTERVENTION_REQUIRED"})
        self.assertEqual(self.decide()["class"], "intervention")

    def test_the_same_disk_gives_the_same_answer(self):
        self.put(self.deliver, "deliver-manifest.json", {})
        answers = {self.decide()["text"] for _ in range(50)}
        self.assertEqual(len(answers), 1)

    def test_the_log_ends_on_the_stop_line_and_never_repeats_a_step(self):
        from run import log, next_step
        self.json.dump(self.run, open(self.root / "run.json", "w"))
        next_step(self.root); next_step(self.root)
        self.put(self.deliver, "deliver-manifest.json", {})
        self.put(self.deliver, "delivery.json", {"state": "unsupported", "reason": "kind",
                                                 "branch": {"name": "b"}})
        next_step(self.root)
        lines = (self.root / "orchestration.md").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("unsupported", lines[-1])
