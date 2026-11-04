"""The item comment is a RECORD of fixed shape, not a report.

It came out of feedback in 2026-10-29: the comment had turned into prose — a narrative of
what the review knocked down, pending items in running text, suggestions for a next step.
None of that decides anything on the item, and it drowns what does. The comment states
**what was measured** and **what holds the verdict up**; whoever wants the reasoning opens
the report.
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from render import build_item_comment
from i18n import t

MANIFEST = {"repo": "repo-x", "origin": "refs/merge-requests/411/head",
            "merge_base": "8c75e543bed3549a", "head_sha": "12ebb32366c0b30b",
            "item": {"id": "12345"}}


def package(**files):
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    for name, data in files.items():
        (d / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    return d


def approved(**extra):
    base = dict(
        verdict={"verdict": "APPROVED", "routes": [],
                 "summary": {"profile_coverage": "18/18", "classes_not_assessable": []}},
        evidence={"suite": "green"},
        criteria=[{"id": "AC-1", "text": "t", "state": "met", "record": "r"}],
        size={"lines": 1704, "band": "split"},
        findings=[],
    )
    base.update(extra)
    return build_item_comment(package(**base))


class TestWhatGoesIn(unittest.TestCase):
    def test_states_the_verdict_the_proposal_and_both_SHAs(self):
        h = approved()
        for expected in ("APPROVED", "!411", "8c75e543bed3", "12ebb32366c0"):
            self.assertIn(expected, h)

    def test_states_the_suite_state_in_a_single_line(self):
        self.assertIn(t("suite.green"), approved())

    def test_translates_a_baseline_red_without_dumping_the_whole_reason(self):
        h = approved(evidence={"suite": "red_not_attributable",
                               "suite_reason": "X" * 900})
        self.assertIn(t("suite.red_baseline"), h)
        self.assertNotIn("X" * 50, h)

    def test_counts_the_criteria_met_over_the_total(self):
        cs = [{"id": f"AC-{i}", "text": "t", "state": s, "record": None}
              for i, s in enumerate(["met", "met", "not_verifiable"])]
        self.assertIn(t("comment.acceptance_value", met=2, total=3), approved(criteria=cs))

    def test_counts_the_assessed_classes_and_names_the_unassessable_ones(self):
        h = approved(verdict={"verdict": "APPROVED", "routes": [],
                              "summary": {"profile_coverage": "16/18",
                                          "classes_not_assessable": ["A7", "A9"]}})
        self.assertIn("16/18", h)
        self.assertIn("A7, A9", h)

    def test_names_the_blocking_findings_and_only_those(self):
        f = [{"id": "X-1", "severity": "required", "status": "confirmed", "attributable": True},
             {"id": "X-2", "severity": "nit", "status": "confirmed", "attributable": True},
             {"id": "X-3", "severity": "required", "status": "refuted", "attributable": True}]
        h = approved(findings=f)
        self.assertIn("X-1", h)
        self.assertNotIn("X-2", h)
        self.assertNotIn("X-3", h)

    def test_always_points_at_the_report(self):
        # The repository, the item and the date — not a hard-coded prefix. The prefix
        # comes from `paths.reports`, which every installation sets differently; pinning
        # it here made the test pass only on the machine that wrote it.
        html = approved()
        self.assertIn("repo-x/12345-", html)
        self.assertIn(".md", html)

    def test_says_that_approving_does_not_merge(self):
        self.assertIn("does not merge", approved())


class TestWhatStaysOUT(unittest.TestCase):
    """The discipline is one of shape: whatever decides nothing on the item stays out."""

    def test_carries_no_narrative_prose(self):
        h = approved().lower()
        for forbidden in ("what the review knocked down", "added by hand",
                          "cliffhanger", "suggest", "next step", "recommend"):
            self.assertNotIn(forbidden, h)

    def test_the_approval_comment_is_short(self):
        """A deliberate ceiling. If a new field does not fit, the field belongs to the
        report."""
        self.assertLess(len(approved()), 1200)

    def test_does_not_invent_a_field_the_package_does_not_carry(self):
        h = build_item_comment(package(
            verdict={"verdict": "APPROVED", "routes": [], "summary": {}}))
        self.assertIn("APPROVED", h)
        self.assertNotIn(t("comment.label_acceptance"), h)
        self.assertNotIn(t("comment.label_suite"), h)


class TestIntervention(unittest.TestCase):
    def test_lists_the_open_routes_with_their_owner(self):
        h = approved(verdict={
            "verdict": "INTERVENTION_REQUIRED",
            "routes": [{"type": "human_decision", "owner": "tech lead", "items": ["A5"]}],
            "summary": {"profile_coverage": "18/18", "classes_not_assessable": []}})
        self.assertIn(t("comment.open_routes"), h)
        self.assertIn("tech lead", h)
        self.assertIn("A5", h)

    def test_the_measured_section_stays_present(self):
        h = approved(verdict={
            "verdict": "INTERVENTION_REQUIRED",
            "routes": [{"type": "human_decision", "owner": "tech lead", "items": ["A5"]}],
            "summary": {"profile_coverage": "18/18", "classes_not_assessable": []}})
        self.assertIn(t("comment.measured"), h)


if __name__ == "__main__":
    unittest.main()
