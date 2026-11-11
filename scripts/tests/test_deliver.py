"""The contract of `deliver`, written before the code: each rule of
`skills/deliver/SKILL.md` has a case here that fails when it is broken.

Until phase 1 lands, this module is RED on purpose: `deliver.py` does not exist yet, and
these cases are what it has to satisfy. The record under test is the one
`schemas/delivery.schema.json` holds; the judge is a pure function over it.
"""
import json
import pathlib
import re
import sys
import unittest

import jsonschema

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from i18n import t, catalogue
import i18n

PRUMO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SCHEMA = json.loads((PRUMO_ROOT / "schemas" / "delivery.schema.json").read_text(encoding="utf-8"))

# The invocation a delivery ends with. A person pastes it; the prumo never runs it.
NEXT = re.compile(r"^/prumo:task-review --repo \S+ --branch \S+ --item \S+$")


def record(**override):
    """A delivery with everything in order — the clean path."""
    base = {
        "item": {"id": "ITEM-1", "kind": "coverage-gap", "source": "tracker"},
        "base": {"name": "main", "sha": "0123abc"},
        "branch": {"name": "ITEM-1-guard", "sha": "4567def"},
        "premises": [{"text": "the guard exists", "state": "holds",
                      "evidence": "src/a.py:41", "central": True}],
        "proof": {"red_on_base": None, "green_on_branch": {"test": "t_guard", "result": "green"},
                  "mutations": [{"file": "src/a.py", "line": 41, "from": ">=", "to": ">",
                                 "test": "t_guard", "result": "red", "reverted": True}]},
        "baseline": {"base_reds": ["t_old"], "branch_reds": ["t_old"], "new_reds": [],
                     "vanished_reds": [], "predicted": 120, "executed": 120},
        "diff": {"files": ["tests/test_a.py"], "added": 30, "removed": 0,
                 "outside_scope": [], "within_budget": True},
        "skipped": [],
        "measured": {"note": "n", "base": None, "suite": None, "lint": None, "mutations": []},
        "waiver": None,
    }
    base.update(override)
    return base


PROFILE = {"profile": "repo-x",
           "delivery": {"kinds": ["coverage-gap"],
                        "budget": {"max_files": 4, "max_lines": 120}}}


def judge(rec=None, profile=PROFILE, today="2026-11-11"):
    from deliver import deliver_judge
    return deliver_judge(rec or record(), profile, today)


class TestTheRecord(unittest.TestCase):
    def test_the_clean_record_validates(self):
        jsonschema.validate(record(), SCHEMA)

    def test_skipped_is_mandatory_even_when_empty(self):
        """Rule 10 — what was not checked is declared, never omitted."""
        rec = record(); del rec["skipped"]
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(rec, SCHEMA)

    def test_the_record_never_carries_a_verdict(self):
        """Rule 1 — who writes does not approve. The word belongs to task-review."""
        self.assertNotIn("verdict", SCHEMA["properties"])
        self.assertNotIn("APPROVED", json.dumps(SCHEMA))


class TestCleanPath(unittest.TestCase):
    def test_everything_in_order_is_delivered(self):
        self.assertEqual(judge()["state"], "delivered")

    def test_next_is_task_review_and_nothing_else(self):
        """Rule 1 and rule 3 — the end of a delivery is the review's beginning, handed
        over ready to paste, never run."""
        out = judge()
        self.assertRegex(out["next"], NEXT)

    def test_same_record_same_answer(self):
        """Rule 2 — routing is a function, not a judgement. A hundred runs, one answer."""
        answers = {json.dumps(judge(), sort_keys=True) for _ in range(100)}
        self.assertEqual(len(answers), 1)


class TestStops(unittest.TestCase):
    """Rule 4 — every stop has a reason from the catalogue and an owner."""

    def assert_blocked(self, rec, key, owner, **fields):
        out = judge(rec)
        self.assertEqual(out["state"], "blocked", out)
        self.assertEqual(out["reason"], t(key, **fields))
        self.assertEqual(out["owner"], owner)

    def test_a_central_premise_that_fails_blocks_before_anything_is_written(self):
        """Rule 7 — measured before touched. The record carries no diff at all."""
        rec = record(premises=[{"text": "only point of control", "state": "fails",
                                "evidence": "src/b.py:88", "central": True}],
                     diff={"files": [], "added": 0, "removed": 0,
                           "outside_scope": [], "within_budget": True})
        self.assert_blocked(rec, "deliver.blocked.premise_failed", "person",
                            premise="only point of control", evidence="src/b.py:88")

    def test_a_non_central_premise_that_fails_is_recorded_and_does_not_block(self):
        rec = record(premises=[{"text": "aside", "state": "fails",
                                "evidence": "src/b.py:88", "central": False}])
        self.assertEqual(judge(rec)["state"], "delivered")

    def test_a_new_red_by_name_blocks(self):
        """Rule 8 — baseline by name."""
        rec = record(baseline={"base_reds": ["t_old"], "branch_reds": ["t_old", "t_new"],
                               "new_reds": ["t_new"], "vanished_reds": [],
                               "predicted": 120, "executed": 120})
        self.assert_blocked(rec, "deliver.blocked.new_red", "author", names="t_new")

    def test_a_suite_that_did_not_finish_blocks(self):
        """Rule 8 — predicted versus executed. Fewer reds mean nothing if fewer tests ran."""
        rec = record(baseline={"base_reds": [], "branch_reds": [], "new_reds": [],
                               "vanished_reds": [], "predicted": 120, "executed": 97})
        self.assert_blocked(rec, "deliver.blocked.suite_incomplete", "author",
                            executed=97, predicted=120)

    def test_a_mutation_that_survived_blocks(self):
        rec = record(proof={"red_on_base": None,
                            "green_on_branch": {"test": "t_guard", "result": "green"},
                            "mutations": [{"file": "src/a.py", "line": 41, "from": ">=",
                                           "to": ">", "test": "t_guard", "result": "green",
                                           "reverted": True}]})
        self.assert_blocked(rec, "deliver.blocked.mutation_survived", "author",
                            file="src/a.py", line=41)

    def test_a_proof_the_sceptic_refuted_blocks(self):
        """The mutation reads the suite; the sceptic reads the test. Either alone can be
        fooled — a test on a mock goes red on a mutation the mock does not see."""
        proof = dict(record()["proof"], refutation={"verdict": "does_not_prove",
                                                    "reason": "asserts on the fake"})
        self.assert_blocked(record(proof=proof), "deliver.blocked.refuted", "author",
                            reason="asserts on the fake")

    def test_an_uncertain_sceptic_is_recorded_and_does_not_block(self):
        proof = dict(record()["proof"], refutation={"verdict": "uncertain", "reason": "could not trace"})
        self.assertEqual(judge(record(proof=proof))["state"], "delivered")

    def test_a_plan_that_asks_a_gate_and_has_no_approval_blocks_for_a_person(self):
        plan = {"shape": "hotfix", "depth": "reproduce", "gate": True, "approved_by": None,
                "budget": {"max_files": 2, "max_lines": 60}}
        out = judge(record(plan=plan))
        self.assertEqual((out["state"], out["owner"]), ("blocked", "person"))
        self.assertIn("approve", out["reason"])

    def test_an_approved_plan_lets_the_delivery_through(self):
        plan = {"shape": "hotfix", "depth": "reproduce", "gate": True, "approved_by": "someone",
                "budget": {"max_files": 2, "max_lines": 60}}
        self.assertEqual(judge(record(plan=plan))["state"], "delivered")

    def test_the_budget_the_shape_scaled_is_the_one_the_reason_names(self):
        plan = {"shape": "hotfix", "depth": "reproduce", "gate": True, "approved_by": "someone",
                "budget": {"max_files": 2, "max_lines": 60}}
        rec = record(plan=plan, diff={"files": ["a", "b", "c"], "added": 10, "removed": 0,
                                       "outside_scope": [], "within_budget": False})
        out = judge(rec)
        self.assertEqual(out["state"], "blocked")
        self.assertEqual(out["reason"], t("deliver.blocked.budget_exceeded", files=3, lines=10,
                                          max_files=2, max_lines=60))

    def test_a_red_lint_blocks(self):
        """The profile's lint check runs over the tree the writer left; a red there
        would cost a whole review cycle to find."""
        self.assert_blocked(record(lint={"state": "red", "reason": "FAIL tests/test_a.py"}),
                            "deliver.blocked.lint_red", "author", reason="FAIL tests/test_a.py")

    def test_a_mutant_that_turns_a_stays_green_test_red_is_too_wide(self):
        proof = dict(record()["proof"])
        proof["mutations"] = [dict(proof["mutations"][0], stays_green=["t_passage"],
                                   stays_green_result="red")]
        self.assert_blocked(record(proof=proof), "deliver.blocked.mutation_too_wide", "author",
                            file="src/a.py", line=41, test="t_passage")

    def test_a_behaviour_change_without_a_red_on_the_base_is_not_proven(self):
        rec = record(item={"id": "ITEM-1", "kind": "behaviour-change", "source": "tracker"})
        profile = {"profile": "repo-x", "delivery": {"kinds": ["coverage-gap", "behaviour-change"],
                                                   "budget": {"max_files": 4, "max_lines": 120}}}
        out = judge(rec, profile=profile)
        self.assertEqual(out["state"], "blocked")
        self.assertEqual(out["reason"], t("deliver.blocked.no_red_on_base"))

    def test_a_behaviour_change_red_on_base_and_green_on_branch_is_delivered(self):
        proof = dict(record()["proof"], red_on_base={"test": "t_x", "result": "red"},
                     green_on_branch={"test": "t_x", "result": "green"}, mutations=[])
        rec = record(item={"id": "ITEM-1", "kind": "behaviour-change", "source": "tracker"}, proof=proof)
        profile = {"profile": "repo-x", "delivery": {"kinds": ["behaviour-change"],
                                                   "budget": {"max_files": 4, "max_lines": 120}}}
        self.assertEqual(judge(rec, profile=profile)["state"], "delivered")

    def test_a_refactor_with_no_guarding_test_stops_for_a_person(self):
        rec = record(item={"id": "ITEM-1", "kind": "refactor", "source": "tracker"},
                     proof={"red_on_base": None, "green_on_branch": None, "mutations": [],
                            "guarding": {"tests": [], "result": "not_run"}})
        profile = {"profile": "repo-x", "delivery": {"kinds": ["refactor"], "budget": {"max_files": 8, "max_lines": 240}}}
        out = judge(rec, profile=profile)
        self.assertEqual((out["state"], out["owner"]), ("blocked", "person"))

    def test_a_refactor_whose_guard_went_red_is_the_author_s(self):
        rec = record(item={"id": "ITEM-1", "kind": "refactor", "source": "tracker"},
                     proof={"red_on_base": None, "green_on_branch": None, "mutations": [],
                            "guarding": {"tests": ["t_old"], "result": "red"}})
        profile = {"profile": "repo-x", "delivery": {"kinds": ["refactor"], "budget": {"max_files": 8, "max_lines": 240}}}
        out = judge(rec, profile=profile)
        self.assertEqual((out["state"], out["owner"]), ("blocked", "author"))

    def test_a_refactor_with_its_guards_green_is_delivered(self):
        rec = record(item={"id": "ITEM-1", "kind": "refactor", "source": "tracker"},
                     proof={"red_on_base": None, "green_on_branch": None, "mutations": [],
                            "guarding": {"tests": ["t_old"], "result": "green"}})
        profile = {"profile": "repo-x", "delivery": {"kinds": ["refactor"], "budget": {"max_files": 8, "max_lines": 240}}}
        self.assertEqual(judge(rec, profile=profile)["state"], "delivered")

    def test_a_line_outside_the_scope_blocks(self):
        rec = record(diff={"files": ["tests/test_a.py", "src/z.py"], "added": 31,
                           "removed": 0, "outside_scope": ["src/z.py"], "within_budget": True})
        self.assert_blocked(rec, "deliver.blocked.outside_scope", "author", paths="src/z.py")

    def test_over_budget_with_no_waiver_blocks(self):
        rec = record(diff={"files": ["a", "b", "c", "d", "e"], "added": 200, "removed": 0,
                           "outside_scope": [], "within_budget": False})
        self.assert_blocked(rec, "deliver.blocked.budget_exceeded", "person",
                            files=5, lines=200, max_files=4, max_lines=120)

    def test_over_budget_with_a_waiver_in_force_is_delivered(self):
        """The waiver is the profile's — a name and an expiry — never the run's."""
        rec = record(diff={"files": ["a", "b", "c", "d", "e"], "added": 200, "removed": 0,
                           "outside_scope": [], "within_budget": False},
                     waiver={"reason": "agreed split", "approver": "someone",
                             "expires": "2027-01-01"})
        self.assertEqual(judge(rec)["state"], "delivered")

    def test_an_expired_waiver_does_not_lift_the_budget(self):
        rec = record(diff={"files": ["a", "b", "c", "d", "e"], "added": 200, "removed": 0,
                           "outside_scope": [], "within_budget": False},
                     waiver={"reason": "agreed split", "approver": "someone",
                             "expires": "2026-01-01"})
        self.assertEqual(judge(rec)["state"], "blocked")

    def test_a_kind_the_profile_does_not_list_is_unsupported(self):
        rec = record(item={"id": "ITEM-1", "kind": "dead-code", "source": "tracker"})
        out = judge(rec)
        self.assertEqual(out["state"], "unsupported")
        self.assertEqual(out["reason"], t("deliver.unsupported.kind", kind="dead-code",
                                          kinds="coverage-gap"))

    def test_a_profile_with_no_delivery_block_is_not_assessable_never_a_default(self):
        """Rule 10 — nothing is assumed about a repository the profile did not describe."""
        from deliver import DeliveryNotAssessable
        with self.assertRaises(DeliveryNotAssessable):
            judge(record(), profile={})


class TestBaselineByName(unittest.TestCase):
    """Rule 8 — the suite is read by NAME and by COUNT OF EXECUTION, from the output,
    through the patterns the profile declares. Which runner printed it does not matter."""

    BASE = "FAIL: t_old (suite.A)\nRan 120 of 120\n"
    BRANCH = "FAIL: t_old (suite.A)\nFAIL: t_new (suite.B)\nRan 120 of 120\n"
    SHORT = "FAIL: t_old (suite.A)\nRan 97 of 120\n"
    CHECK = {"red_name_pattern": r"(?m)^FAIL: (\S+)",
             "progress_pattern": r"(?m)^Ran (\d+) of (\d+)"}

    def test_a_new_red_is_the_set_difference_by_name(self):
        from evidence import compare_reds
        out = compare_reds(self.BASE, self.BRANCH, self.CHECK)
        self.assertEqual(out["new_reds"], ["t_new"])
        self.assertEqual(out["vanished_reds"], [])

    def test_predicted_and_executed_come_from_the_output(self):
        from evidence import compare_reds
        out = compare_reds(self.BASE, self.SHORT, self.CHECK)
        self.assertEqual((out["executed"], out["predicted"]), (97, 120))

    def test_without_a_name_pattern_the_baseline_is_not_assessable(self):
        """A count is not a baseline. No pattern, no names — declared, not guessed."""
        from evidence import compare_reds
        out = compare_reds(self.BASE, self.BRANCH, {})
        self.assertIsNone(out["new_reds"])


class TestTheVoice(unittest.TestCase):
    """Rule 7 of the voice: one idea per sentence, twenty words at most. Held over the
    catalogue, where every sentence a person reads from this capability lives."""

    def test_every_deliver_message_is_twenty_words_or_fewer(self):
        for lang in i18n.available():
            for key, text in catalogue(lang).items():
                if not key.startswith(("deliver.", "run.")):
                    continue
                with self.subTest(language=lang, key=key):
                    for sentence in re.split(r"(?<=[.!?])\s+", str(text)):
                        words = [w for w in sentence.split() if w]
                        self.assertLessEqual(len(words), 20, sentence)


if __name__ == "__main__":
    unittest.main()


class TestTheBranchName(unittest.TestCase):
    """The team names the branch on the item; a slug is only what stands in when
    nobody did."""
    BLOCK = {"branch_format": "{item}-{slug}"}

    def test_the_invocation_s_branch_wins(self):
        from deliver import branch_name
        self.assertEqual(branch_name(self.BLOCK, "I-1", "# t\nBranch: named\n", "given"), "given")

    def test_the_item_s_branch_line_is_read(self):
        from deliver import branch_name
        self.assertEqual(branch_name(self.BLOCK, "I-1", "# Title\n\nCard I-1\nBranch: I-1-prove-the-guard\n", None),
                         "I-1-prove-the-guard")

    def test_without_one_the_title_is_slugged(self):
        from deliver import branch_name
        self.assertEqual(branch_name(self.BLOCK, "I-1", "# Prove the guard\n", None), "I-1-prove-the-guard")
