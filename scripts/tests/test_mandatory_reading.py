"""The mandatory-reading gate: with no item, and with no proposal description when the
origin is a proposal, the review does not start.

This is a gate and not a warning because the first two ledgers — what was asked for, and
what the delivery says about itself — are not on the repository's disk. Reviewing without
them produces a report with the shape of a complete one and the content of a partial one,
which is the worst possible outcome.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from providers import test_config
from preflight import reading_impediments


CFG = test_config()


class TestMandatoryReading(unittest.TestCase):

    def test_a_proposal_origin_with_nothing_demands_both(self):
        imp = reading_impediments({"origin": "mr", "item": False, "proposal_description": False}, CFG)
        self.assertEqual(len(imp), 2)
        self.assertTrue(any(CFG.tracker.item in i for i in imp))
        self.assertTrue(any(f"DESCRIPTION of the {CFG.forge.proposal}" in i for i in imp))

    def test_a_proposal_origin_with_both_is_let_through(self):
        self.assertEqual(
            reading_impediments({"origin": "mr", "item": True, "proposal_description": True}, CFG), [])

    def test_a_branch_origin_does_not_demand_a_proposal_description(self):
        """A loose branch has no proposal description; demanding it would be demanding
        something that does not exist."""
        imp = reading_impediments({"origin": "branch", "item": True, "proposal_description": False}, CFG)
        self.assertEqual(imp, [])

    def test_a_diff_origin_does_not_demand_a_proposal_description(self):
        imp = reading_impediments({"origin": "diff", "item": True, "proposal_description": False}, CFG)
        self.assertEqual(imp, [])

    def test_the_item_is_demanded_whatever_the_origin(self):
        """The item is ledger 1. No origin excuses not knowing what was asked for."""
        for origin in ("mr", "branch", "diff"):
            with self.subTest(origin=origin):
                imp = reading_impediments(
                    {"origin": origin, "item": False, "proposal_description": True}, CFG)
                self.assertEqual(len(imp), 1)
                self.assertIn(CFG.tracker.item, imp[0])

    def test_every_impediment_says_how_to_get_out_of_it(self):
        """Naming the remedy is part of the contract: the stop says what to do next."""
        imp = reading_impediments({"origin": "mr", "item": False, "proposal_description": False}, CFG)
        for i in imp:
            self.assertIn("The review does NOT start without it.", i)
            self.assertTrue("--item-file" in i or "--proposal-file" in i)


if __name__ == "__main__":
    unittest.main()
