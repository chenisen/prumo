"""The shape of an item decides how deep the plan goes, and it is never guessed."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from shape import SHAPES, resolve, budget_for, UnknownShape
from providers import test_config, InvalidConfiguration


class TestTheSixShapes(unittest.TestCase):
    def test_every_shape_declares_the_whole_contract(self):
        for name, rules in SHAPES.items():
            with self.subTest(shape=name):
                for key in ("plan", "gate", "kinds", "budget", "review", "deliverable", "decomposes"):
                    self.assertIn(key, rules)

    def test_the_gate_and_the_depth_follow_the_table(self):
        self.assertFalse(SHAPES["debt"]["gate"]); self.assertFalse(SHAPES["bug"]["gate"])
        for gated in ("hotfix", "refactor", "story", "epic"):
            self.assertTrue(SHAPES[gated]["gate"], gated)
        self.assertEqual(SHAPES["bug"]["plan"], "reproduce")
        self.assertEqual(SHAPES["story"]["plan"], "design")
        self.assertFalse(SHAPES["epic"]["deliverable"])

    def test_the_invocation_s_shape_wins(self):
        self.assertEqual(resolve("bug", "Débito técnico", {"Débito técnico": "debt"}), "bug")

    def test_the_tracker_s_type_maps_through_the_installation(self):
        self.assertEqual(resolve(None, "Débito técnico", {"Débito técnico": "debt"}), "debt")

    def test_an_unmapped_type_is_a_refusal_not_a_guess(self):
        with self.assertRaises(UnknownShape):
            resolve(None, "Melhoria", {"Débito técnico": "debt"})

    def test_no_shape_at_all_is_a_refusal(self):
        with self.assertRaises(UnknownShape):
            resolve(None, None, {})

    def test_a_shape_outside_the_six_is_refused(self):
        with self.assertRaises(UnknownShape):
            resolve("chore", None, {})


class TestTheBudgetScalesWithTheShape(unittest.TestCase):
    BLOCK = {"budget": {"max_files": 3, "max_lines": 100}}

    def test_a_hotfix_buys_half_rounded_up(self):
        self.assertEqual(budget_for(self.BLOCK, "hotfix"), {"max_files": 2, "max_lines": 50})

    def test_a_story_buys_three_times(self):
        self.assertEqual(budget_for(self.BLOCK, "story"), {"max_files": 9, "max_lines": 300})

    def test_an_epic_buys_nothing(self):
        self.assertEqual(budget_for(self.BLOCK, "epic"), {"max_files": 0, "max_lines": 0})


class TestTheMappingLivesInTheConfiguration(unittest.TestCase):
    def test_the_tracker_carries_its_types_mapped(self):
        cfg = test_config(tracker={"type": "businessmap", "shapes": {"Bug": "bug", "História": "story"}})
        self.assertEqual(cfg.tracker.shape_of("Bug"), "bug")
        self.assertIsNone(cfg.tracker.shape_of("Melhoria"))

    def test_a_type_mapped_outside_the_six_is_refused_at_load(self):
        with self.assertRaises(InvalidConfiguration):
            test_config(tracker={"type": "businessmap", "shapes": {"Bug": "chore"}})


if __name__ == "__main__":
    unittest.main()
