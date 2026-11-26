"""The shape of an item decides how deep the plan goes, and it is never guessed."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from shape import SHAPES, resolve, budget_for, UnknownShape, size_of, small_limits
from providers import config_for_tests, InvalidConfiguration


class TestTheSevenShapes(unittest.TestCase):
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

    def test_a_shape_outside_the_seven_is_refused(self):
        with self.assertRaises(UnknownShape):
            resolve("chore", None, {})


class TestAnIncidentIsTheShapeThatDoesNotWait(unittest.TestCase):
    """The first incident delivered by the method went as a hotfix (2026-11-26): two hours,
    twenty-two minutes of them worked by an agent. The rest was a plan waiting for a person,
    a whole suite that did not load on the base, and three review rounds that held nothing
    about the fix itself."""

    def test_nobody_approves_a_plan_before_the_first_line(self):
        self.assertFalse(SHAPES["incident"]["gate"])
        self.assertEqual(SHAPES["incident"]["plan"], "root-cause")

    def test_it_is_cut_from_where_production_runs_like_a_hotfix(self):
        self.assertEqual(SHAPES["incident"]["base"], SHAPES["hotfix"]["base"])

    def test_it_changes_behaviour_and_nothing_else(self):
        self.assertEqual(SHAPES["incident"]["kinds"], ("behaviour-change",))

    def test_it_has_a_stage_of_its_own_and_every_other_ungated_shape_the_common_one(self):
        from shape import stage_of
        self.assertEqual(stage_of("incident"), "incident")
        for shape in ("debt", "bug"):
            self.assertEqual(stage_of(shape), "deliver", shape)

    def test_it_alone_is_checked_by_the_tests_it_touched_whatever_its_size(self):
        from shape import checked_by_touched_tests
        self.assertEqual([s for s in SHAPES if checked_by_touched_tests(s)], ["incident"])
        self.assertEqual(size_of("incident", ["a", "b", "c", "d", "e"], 400, {})["size"], "standard",
                         "the size stays what was touched: it is the suite that the shape decides")

    def test_the_tracker_can_map_a_type_to_it(self):
        self.assertEqual(resolve(None, "Production incident", {"Production incident": "incident"}), "incident")


class TestTheBudgetScalesWithTheShape(unittest.TestCase):
    BLOCK = {"budget": {"max_files": 3, "max_lines": 100}}

    def test_a_hotfix_buys_half_rounded_up(self):
        self.assertEqual(budget_for(self.BLOCK, "hotfix"), {"max_files": 2, "max_lines": 50})

    def test_a_story_buys_three_times(self):
        self.assertEqual(budget_for(self.BLOCK, "story"), {"max_files": 9, "max_lines": 300})

    def test_an_epic_buys_nothing(self):
        self.assertEqual(budget_for(self.BLOCK, "epic"), {"max_files": 0, "max_lines": 0})

    def test_a_profile_that_declares_none_has_none_in_every_shape(self):
        for shape in SHAPES:
            self.assertIsNone(budget_for({}, shape), shape)
            self.assertIsNone(budget_for({"budget": None}, shape), shape)


class TestTheMappingLivesInTheConfiguration(unittest.TestCase):
    def test_the_tracker_carries_its_types_mapped(self):
        cfg = config_for_tests(tracker={"type": "businessmap", "shapes": {"Bug": "bug", "História": "story"}})
        self.assertEqual(cfg.tracker.shape_of("Bug"), "bug")
        self.assertIsNone(cfg.tracker.shape_of("Melhoria"))

    def test_a_type_mapped_outside_the_seven_is_refused_at_load(self):
        with self.assertRaises(InvalidConfiguration):
            config_for_tests(tracker={"type": "businessmap", "shapes": {"Bug": "chore"}})


if __name__ == "__main__":
    unittest.main()


class TestTheSizeIsWhatWasTouched(unittest.TestCase):
    """The shape says what was asked; the size says how much was touched, and it is the
    size that decides what a delivery pays: the test files it touched, or the whole
    suite; a sceptic, or none. A two-file fix once paid three whole suites."""

    BLOCK = {"wide_paths": ["src/kernel/**", "*.lock"]}

    def test_a_handful_of_files_on_a_shape_with_no_gate_is_small(self):
        for shape in ("debt", "bug"):
            self.assertEqual(size_of(shape, ["src/a.x", "tests/a.x"], 16, self.BLOCK)["size"], "small", shape)

    def test_before_anything_is_written_the_files_alone_decide(self):
        self.assertEqual(size_of("bug", ["src/a.x"], None, {})["size"], "small")

    def test_past_either_limit_it_is_standard(self):
        self.assertEqual(size_of("bug", ["a", "b", "c", "d"], 10, {})["size"], "standard")
        self.assertEqual(size_of("bug", ["a"], 81, {})["size"], "standard")

    def test_the_limits_are_the_profile_s(self):
        self.assertEqual(small_limits({}), {"max_files": 3, "max_lines": 80})
        block = {"small": {"max_files": 6, "max_lines": 200}}
        self.assertEqual(size_of("bug", ["a", "b", "c", "d"], 150, block)["size"], "small")

    def test_a_wide_path_is_never_small_however_few_its_lines(self):
        out = size_of("bug", ["src/kernel/base.x"], 1, self.BLOCK)
        self.assertEqual(out["size"], "standard")
        self.assertIn("src/kernel/base.x", out["reason"])
        self.assertEqual(size_of("debt", ["deps.lock"], 1, self.BLOCK)["size"], "standard")

    def test_a_shape_a_person_approves_is_never_small_and_a_story_is_large(self):
        self.assertEqual(size_of("hotfix", ["a"], 1, {})["size"], "standard")
        self.assertEqual(size_of("refactor", ["a"], 1, {})["size"], "standard")
        self.assertEqual(size_of("story", ["a"], 1, {})["size"], "large")

    def test_same_change_same_size(self):
        answers = {size_of("bug", ["a", "b"], 30, self.BLOCK)["size"] for _ in range(50)}
        self.assertEqual(answers, {"small"})

