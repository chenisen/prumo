"""A finding's id is the METHOD's to give, and it has to be unique.

The defect this file exists for, measured on 2026-11-04 over a four-reviewer review:
nothing tells a reviewer agent how to number what it finds, so each one numbers from
`F1` inside its own context. Three of the four returned an `F1`. Everything downstream
keys by id — the verification map in the workflow, `render.py`'s `by_finding`,
`judge.py`'s blocking lists — so the three collapsed into one: the architecture
reviewer's finding and the scope reviewer's finding disappeared, and the correctness
reviewer's `F1` was printed three times in their place.

What makes it worth a gate rather than a comment: a lost finding leaves no hole. The
report came out the right shape, the right length, with the right number of sections. The
only way to notice was to read the per-agent journal and count.

The workflow now stamps a unique id per reviewer. This file holds the other end — the
spine refusing to judge a findings file where the stamping did not happen.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import review

WORKFLOW = (pathlib.Path(__file__).resolve().parent.parent.parent
            / "workflows" / "task-review.js")


class TestCollidingIds(unittest.TestCase):
    def test_distinct_ids_pass(self):
        self.assertEqual(
            review.colliding_ids([{"id": "SCO-1"}, {"id": "COR-1"}, {"id": "SEC-1"}]), {})

    def test_it_names_the_id_and_how_many_findings_it_covers(self):
        """`two findings collided` is not actionable; `F1 (3)` is."""
        self.assertEqual(
            review.colliding_ids([{"id": "F1"}, {"id": "F1"}, {"id": "F2"}, {"id": "F1"}]),
            {"F1": 3})

    def test_several_collisions_are_all_reported(self):
        self.assertEqual(
            review.colliding_ids([{"id": "F1"}, {"id": "F1"},
                                  {"id": "SEC-01"}, {"id": "SEC-01"}]),
            {"F1": 2, "SEC-01": 2})

    def test_an_empty_set_of_findings_is_not_a_collision(self):
        self.assertEqual(review.colliding_ids([]), {})

    def test_a_finding_with_no_id_is_not_counted_as_a_collision(self):
        """A missing id is the schema's business. Counting several of them as one
        collision would report the wrong defect and send a person looking for the wrong
        thing."""
        self.assertEqual(review.colliding_ids([{"claim": "x"}, {"claim": "y"}]), {})


class TestTheWorkflowStampsTheId(unittest.TestCase):
    """There is no JS runtime in this environment, so this end is read as text. It pins
    the two decisions, not the spelling: the id comes from the plan's reviewer list, and
    uniqueness is constructed rather than hoped for."""

    def setUp(self):
        self.source = WORKFLOW.read_text(encoding="utf-8")

    def test_findings_are_collected_per_reviewer_of_the_plan(self):
        # keyed off `plan.reviewers`, which is what knows WHICH agent returned what —
        # the agents' own `reviewer` field held the name of a skill they had loaded
        self.assertIn("plan.reviewers.flatMap", self.source)

    def test_the_reviewer_is_taken_from_the_plan_not_from_the_agent(self):
        self.assertIn("reviewer: r.agent", self.source)

    def test_the_id_is_stamped_rather_than_passed_through(self):
        self.assertIn("id: `${tags[i]}-${n + 1}`", self.source)

    def test_colliding_tags_are_disambiguated_instead_of_merged(self):
        """A profile may name a local reviewer whose tag matches one of the method's."""
        self.assertIn("`${base}${n}`", self.source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
