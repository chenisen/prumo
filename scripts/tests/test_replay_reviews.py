"""Replay of the reviews already done by hand, against the judge.

It is the exit criterion of Phase 0: the judge has to reproduce the verdict a person
wrote, for the SAME reasons. Every fixture cites the report it came from.

Source: resources/reviews/<item>-<slug>/review-<date>.md
"""
import pathlib, sys, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from judge import judge_delivery, APPROVED, INTERVENTION
from common import CLASSES

PROFILE = {"profile": "example-repo", "status": "confirmed"}


def classes(**states):
    return [{"id": c, "section": m["section"], "state": states.get(c, "did_not_fire"),
             "reason": "x", "not_applicable": False, "waiver": None, "evidence": []}
            for c, m in CLASSES.items()]


def judge(**kw):
    base = dict(profile=PROFILE, classes=classes(), findings=[], criteria=[],
                evidence={"suite": "green"}, scope={}, resolutions=[], today="2026-10-28")
    base.update(kw)
    return judge_delivery(**base)


def route_types(v):
    return {r["type"] for r in v["routes"]}


class TestItem10001(unittest.TestCase):
    """review-2026-10-27.md — INTERVENTION REQUIRED.
    R1 the proposal is 15 commits behind the base; A7 `contradicts` left open; AC-6
    undecidable because two measurements of the baseline disagree."""

    def setUp(self):
        self.v = judge(
            classes=classes(A7="fired"),
            criteria=[{"id": "AC-6", "state": "not_verifiable", "record": None}],
            evidence={"suite": "green", "behind_base": True})

    def test_verdict(self):
        self.assertEqual(self.v["verdict"], INTERVENTION)

    def test_being_behind_the_base_is_annotated_and_not_charged(self):
        """It used to open an `author_fix / rebase` route and close gate 1. Since
        2026-11-04 only a real conflict does that; staleness is declared in the summary."""
        self.assertIsNotNone(self.v["summary"]["stale_base"])
        author = [r for r in self.v["routes"] if r["type"] == "author_fix"]
        self.assertEqual([], [r for r in author if "rebase" in r["items"]])

    def test_A7_is_a_human_decision_and_not_the_authors(self):
        self.assertIn("human_decision", route_types(self.v))
        decision = [r for r in self.v["routes"] if r["type"] == "human_decision"][0]
        self.assertEqual(decision["items"], ["A7"])

    def test_an_undecidable_criterion_becomes_a_manual_check(self):
        check = [r for r in self.v["routes"] if r["type"] == "manual_check"][0]
        self.assertIn("AC-6", check["items"])


class TestItem10002(unittest.TestCase):
    """review-2026-10-27-apos-entrega.md — APPROVED.
    Nine criteria met, suite green at the baseline, no guardrail fired.
    F1/F3/F5 are FYI; F2 is `Required` but OF THE PROJECT, outside this delivery."""

    def setUp(self):
        self.v = judge(findings=[
            {"id": "F1", "severity": "fyi", "status": "confirmed"},
            {"id": "F2", "severity": "required", "status": "confirmed", "attributable": False},
            {"id": "F3", "severity": "fyi", "status": "confirmed"},
            {"id": "F4", "severity": "required", "status": "refuted"},
            {"id": "F5", "severity": "fyi", "status": "confirmed"},
        ], criteria=[{"id": f"AC-{i}", "state": "met"} for i in range(1, 10)])

    def test_verdict(self):
        self.assertEqual(self.v["verdict"], APPROVED)

    def test_withdrawn_F4_does_not_show_up(self):
        self.assertEqual(self.v["summary"]["refuted_findings"], 1)

    def test_F2_of_the_project_does_not_block_but_stays_on_record(self):
        self.assertEqual(self.v["summary"]["non_attributable_findings"], ["F2"])


class TestItem10003Round2(unittest.TestCase):
    """review-2026-10-28-rodada-2.md — INTERVENTION REQUIRED.
    No §A, no §B. What holds it is ONE Required finding, the author's (F2): the proofs
    do not discriminate the tenant's value."""

    def setUp(self):
        self.v = judge(findings=[
            {"id": "F2", "severity": "required", "status": "confirmed"}])

    def test_verdict(self):
        self.assertEqual(self.v["verdict"], INTERVENTION)

    def test_it_is_the_authors_and_nothing_else(self):
        self.assertEqual(route_types(self.v), {"author_fix"})
        self.assertEqual(self.v["routes"][0]["items"], ["F2"])

    def test_nothing_is_a_human_decision(self):
        self.assertNotIn("human_decision", route_types(self.v))


class TestItem10003Round3(unittest.TestCase):
    """review-2026-10-28-rodada-3.md — APPROVED.
    No §A, no §B, no Critical or Required left open."""

    def test_verdict(self):
        v = judge(findings=[{"id": "F2", "severity": "required", "status": "confirmed"}],
                  resolutions=[{"type": "fix", "item": "F2"}])
        self.assertEqual(v["verdict"], APPROVED)
        self.assertEqual(v["routes"], [])


class TestDraftProfileChangesAllThree(unittest.TestCase):
    """The same three cases, with the profile still in draft.

    Until 2026-11-03 none of them could approve: the draft was the repository's cost of
    entry. The rule changed — the review judges whether the item was delivered IN THIS
    PROPOSAL, and an unfinished profile is the project's debt. The draft stays DECLARED
    on every run, with an owner, and leaves the verdict. What it stops doing is punishing
    a clean delivery for something pending that it did not cause."""

    def test_draft_is_declared_in_all_of_them_and_holds_none(self):
        draft = {"profile": "example-repo", "status": "draft"}
        for name, kw in (("10002", dict(criteria=[{"id": "AC-1", "state": "met"}])),
                         ("10003r3", dict())):
            with self.subTest(case=name):
                v = judge(profile=draft, **kw)
                self.assertEqual(v["verdict"], APPROVED)
                self.assertIn("missing_profile", route_types(v))
                self.assertIn("example-repo", v["summary"]["project_pending"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
