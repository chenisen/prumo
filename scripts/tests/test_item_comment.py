"""The item comment is a RECORD of fixed shape, and it stands on its own.

It came out of feedback in 2026-10-29: the comment had turned into prose — a narrative of
what the review knocked down, pending items in running text, suggestions for a next step.
None of that decides anything on the item, and it drowns what does. The comment states
**what was measured** and **what holds the verdict up**.

Feedback in 2026-11-04 took the other half: the comment used to close by naming the
report's path, and a local path is unreadable to everybody but whoever ran the review.
So what decides now travels INSIDE the comment — gates, criteria with their records,
findings with their evidence, observations, project pending items — and no filesystem
path appears at all. What stays out is what is an instruction to execute later: the
manual scripts and this environment's capabilities.
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from render import build_item_comment
from common import dir_reports
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


def heading(label: str) -> str:
    """How a SECTION title appears, as opposed to the same words inside a list item."""
    return f"<p><strong>{label}</strong></p>"


def measured_line(html: str, label: str) -> str:
    """The single `Measured` bullet carrying `label`."""
    return [li for li in html.split("<li>") if label in li][0]


def approved(**extra):
    base = dict(
        verdict={"verdict": "APPROVED", "routes": [],
                 "gates": [{"number": 1, "name": "standards", "condition": "c",
                            "passed": True, "items": []}],
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

    def test_carries_the_staleness_note_when_the_proposal_is_behind_its_base(self):
        """It decides nothing — only a real conflict does — but it is measured, and the
        reader of the item asks about it."""
        h = approved(verdict={
            "verdict": "APPROVED", "routes": [],
            "gates": [{"number": 1, "name": "standards", "condition": "c",
                       "passed": True, "items": []}],
            "summary": {"profile_coverage": "18/18", "classes_not_assessable": [],
                        "stale_base": "two commits behind, merges cleanly"}})
        self.assertIn("two commits behind, merges cleanly", h)

    def test_counts_the_criteria_met_over_the_total(self):
        cs = [{"id": f"AC-{i}", "text": "t", "state": s, "record": None}
              for i, s in enumerate(["met", "met", "not_verifiable"])]
        self.assertIn(t("comment.acceptance_value", met=2, total=3), approved(criteria=cs))

    def test_counts_the_assessed_classes_and_says_what_each_unassessed_one_ASKS(self):
        """`A7, A9` is the harness talking to itself. The reader of the item has never
        seen the class catalogue and has no way to look it up from here."""
        h = approved(verdict={"verdict": "APPROVED", "routes": [],
                              "summary": {"profile_coverage": "16/18",
                                          "classes_not_assessable": ["A7", "A9"]}})
        self.assertIn("16/18", h)
        for cid in ("A7", "A9"):
            self.assertIn(cid, h)
            self.assertIn(t(f"class_question.{cid}"), h)

    def test_a_class_already_explained_is_not_explained_twice(self):
        """`Measured` spells out the unassessed classes; the route below names the same
        ones. Repeating nine long questions inside one comment doubles its length and
        teaches the reader nothing twice."""
        h = approved(verdict={
            "verdict": "APPROVED",
            "routes": [{"type": "missing_capability", "origin": "project",
                        "owner": "whoever maintains it", "items": ["A7", "A9"],
                        "closes_when": "the profile is versioned", "details": []}],
            "summary": {"profile_coverage": "16/18",
                        "classes_not_assessable": ["A7", "A9"]}})
        self.assertEqual(1, h.count(t("class_question.A7")))
        self.assertEqual(1, h.count(t("class_question.A9")))

    def test_a_route_naming_classes_says_what_each_one_ASKS(self):
        h = approved(verdict={
            "verdict": "APPROVED",
            "routes": [{"type": "missing_capability", "origin": "project",
                        "owner": "whoever maintains it", "items": ["A9", "B3"],
                        "closes_when": "the profile is versioned", "details": []}],
            "summary": {"profile_coverage": "16/18", "classes_not_assessable": []}})
        for cid in ("A9", "B3"):
            self.assertIn(t(f"class_question.{cid}"), h)

    def test_a_route_naming_something_that_is_not_a_class_stays_a_plain_list(self):
        h = approved(verdict={
            "verdict": "APPROVED",
            "routes": [{"type": "missing_profile", "origin": "project",
                        "owner": "tech lead", "items": ["repo-x"],
                        "closes_when": "somebody confirms it", "details": []}],
            "summary": {"profile_coverage": "9/18", "classes_not_assessable": []}})
        self.assertIn("repo-x", h)

    def test_the_counter_names_the_blocking_findings_and_only_those(self):
        """The counter in `Measured` is the at-a-glance line: it names what BLOCKS. The
        detail section below lists the rest — so the assertion is scoped to the counter,
        not to the whole comment."""
        f = [{"id": "X-1", "severity": "required", "status": "confirmed",
              "attributable": True, "claim": "c", "evidence": []},
             {"id": "X-2", "severity": "nit", "status": "confirmed",
              "attributable": True, "claim": "c", "evidence": []},
             {"id": "X-3", "severity": "required", "status": "refuted",
              "attributable": True, "claim": "c", "evidence": []}]
        counter = measured_line(approved(findings=f), t("comment.label_findings"))
        self.assertIn("X-1", counter)
        self.assertNotIn("X-2", counter)
        self.assertNotIn("X-3", counter)

    def test_a_refuted_finding_appears_nowhere(self):
        """The verifier knocked it down. Listing it would make a reader count a defect
        the review decided was not one."""
        f = [{"id": "X-3", "severity": "required", "status": "refuted",
              "attributable": True, "claim": "the refuted claim", "consequence": "c",
              "evidence": [{"file": "a", "line": 1}]}]
        h = approved(findings=f)
        self.assertNotIn("the refuted claim", h)

    def test_carries_the_five_gates_with_their_condition(self):
        h = approved(verdict={
            "verdict": "APPROVED", "routes": [],
            "gates": [{"number": n, "name": "g", "condition": f"condition {n}",
                       "passed": True, "items": []} for n in range(1, 6)],
            "summary": {"profile_coverage": "18/18", "classes_not_assessable": []}})
        self.assertIn(t("comment.the_rule"), h)
        for n in range(1, 6):
            self.assertIn(f"condition {n}", h)

    def test_marks_a_closed_gate_and_names_what_closed_it(self):
        h = approved(verdict={
            "verdict": "INTERVENTION_REQUIRED", "routes": [],
            "gates": [{"number": 3, "name": "item_covered", "condition": "covers the item",
                       "passed": False, "items": ["AC-2"]}],
            "summary": {"profile_coverage": "18/18", "classes_not_assessable": []}})
        self.assertIn("❌", h)
        self.assertIn("AC-2", h)

    def test_carries_every_criterion_with_its_record(self):
        cs = [{"id": "AC-1", "text": "the suite is green", "state": "met",
               "record": "168 tests, 357 assertions"}]
        h = approved(criteria=cs)
        self.assertIn("the suite is green", h)
        self.assertIn("168 tests, 357 assertions", h)

    def test_carries_the_project_pending_items_with_their_owner(self):
        h = approved(verdict={
            "verdict": "APPROVED",
            "routes": [{"type": "missing_profile", "origin": "project", "owner": "tech lead",
                        "items": ["repo-x"], "closes_when": "somebody confirms the profile",
                        "details": []}],
            "summary": {"profile_coverage": "9/18", "classes_not_assessable": []}})
        self.assertIn(heading(t("comment.section_project_pending")), h)
        self.assertIn("tech lead", h)
        self.assertIn("somebody confirms the profile", h)

    def test_says_that_approving_does_not_merge(self):
        self.assertIn("does not merge", approved())


class TestWhatStaysOUT(unittest.TestCase):
    """The discipline is one of shape: whatever decides nothing on the item stays out."""

    def test_carries_no_narrative_prose(self):
        h = approved().lower()
        for forbidden in ("what the review knocked down", "added by hand",
                          "cliffhanger", "suggest", "next step", "recommend"):
            self.assertNotIn(forbidden, h)

    def test_never_names_a_file_on_the_machine_that_ran_the_review(self):
        """The reason the comment grew in the first place. A path under the reports root
        is unreadable to the person reading the item, and naming it tells them a report
        exists without letting them read a line of it."""
        h = approved()
        self.assertNotIn(".md", h)
        self.assertNotIn("repo-x/12345-", h)
        self.assertNotIn(str(dir_reports()), h)

    def test_carries_no_finding_at_all(self):
        """A finding is a reading, and a reading belongs in the report, beside its
        evidence, its consequence and its status. On the item, the verdict and the
        criteria decide; a list of nits and FYIs beside them reads as a list of things
        somebody has to answer for. The COUNT stays, in `Measured`."""
        h = approved(findings=[
            {"id": "SC-2", "severity": "nit", "status": "confirmed", "attributable": True,
             "claim": "assertions are duplicated", "consequence": "two places to update",
             "evidence": [{"file": "tests/A.php", "line": 559}]},
            {"id": "CI-1", "severity": "fyi", "status": "confirmed", "attributable": False,
             "claim": "the pipeline runs no test job", "consequence": "c",
             "evidence": [{"file": "b", "line": 1}]}])
        self.assertNotIn("assertions are duplicated", h)
        self.assertNotIn("the pipeline runs no test job", h)
        self.assertNotIn("tests/A.php", h)
        self.assertIn(t("comment.label_findings"), h)   # the counter stays

    def test_leaves_out_what_is_an_instruction_to_execute_later(self):
        """The manual scripts and this environment's capabilities are work orders, not
        the record of a verdict — and they are what would drown the comment on a
        repository whose profile leaves nine classes unassessable."""
        d = package(
            verdict={"verdict": "APPROVED", "routes": [], "summary": {}},
            scripts_manual=[{"trigger": "A9 came out not_assessable",
                             "goal": "scan the diff by hand",
                             "steps": ["run grep over the patch"]}])
        h = build_item_comment(d)
        self.assertNotIn("scan the diff by hand", h)
        self.assertNotIn("run grep over the patch", h)

    def test_stays_within_what_a_board_comment_can_hold(self):
        """A ceiling still, just a real one: the comment carries what decides, never the
        appendices. If a new SECTION does not fit, it belongs to the report."""
        self.assertLess(len(approved()), 12000)

    def test_shows_no_empty_section(self):
        """A heading with no body cannot be told apart from 'nobody looked'."""
        h = build_item_comment(package(
            verdict={"verdict": "APPROVED", "routes": [], "summary": {}}))
        for absent in ("comment.section_criteria", "comment.section_project_pending"):
            self.assertNotIn(heading(t(absent)), h)

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
