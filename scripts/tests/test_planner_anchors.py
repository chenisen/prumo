"""The manual planner gets the anchors without the cards that taught them.

Replay of 2026-11-18: the planner of a review copied an anchor's `cards` entry (an earlier
item, the one that taught the profile class A10) into its script as the item under
review. Provenance stopped the conclude — the id was nowhere in the manifest — and only
the planner had to be run again, by hand. What is not handed over cannot be copied.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import review

WORKFLOW = (pathlib.Path(__file__).resolve().parent.parent.parent
            / "workflows" / "task-review.js")


class TestAnchorsWithoutHistory(unittest.TestCase):
    PROFILE = {"anchors": {
        "A10": {"paths": ["app/Handler.php"], "cards": [46372],
                "nota": "an earlier item moved the read out of the loop"},
        "A8": {"paths": ["tests/**"], "cards": []},
        "B1": {"paths": ["app/Formatter.php"], "verification": "take an order to acceptance"},
    }}

    def test_no_anchor_carries_its_cards(self):
        for cid, anchor in review.anchors_without_history(self.PROFILE).items():
            self.assertNotIn("cards", anchor, cid)

    def test_where_and_against_what_survive(self):
        view = review.anchors_without_history(self.PROFILE)
        self.assertEqual(view["A10"]["paths"], ["app/Handler.php"])
        self.assertEqual(view["A10"]["nota"], "an earlier item moved the read out of the loop")
        self.assertEqual(view["B1"]["verification"], "take an order to acceptance")

    def test_a_profile_with_no_anchors_hands_over_none(self):
        self.assertEqual(review.anchors_without_history({}), {})
        self.assertEqual(review.anchors_without_history({"anchors": None}), {})

    def test_the_profile_is_not_changed(self):
        review.anchors_without_history(self.PROFILE)
        self.assertEqual(self.PROFILE["anchors"]["A10"]["cards"], [46372])


class TestTheWorkflowHandsTheViewToThePlanner(unittest.TestCase):
    def setUp(self):
        self.source = WORKFLOW.read_text(encoding="utf-8")

    def test_the_planner_gets_the_anchors_inline(self):
        self.assertIn("JSON.stringify(anchorsFor(needsScript))", self.source)

    def test_a_grouped_entry_keeps_each_class_anchor(self):
        self.assertIn(".flatMap((c) => String(c.id).split(', '))", self.source)

    def test_the_planner_is_told_an_id_in_a_note_is_history(self):
        self.assertIn("is the project\\'s HISTORY", self.source)


if __name__ == "__main__":
    unittest.main()
