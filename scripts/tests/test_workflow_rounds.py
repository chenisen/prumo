"""The review workflow, run with its agents stubbed: what each round asks, of whom, and
what it hands back. The script has no filesystem and no clock, so a table of answers
keyed by agent label is the whole world it sees."""
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
WORKFLOW = HERE.parent.parent / "workflows" / "task-review.js"
RUNNER = HERE / "fixtures" / "run_workflow.mjs"

VERIFIED = {"verdict": "confirmed", "reason": "r", "evidence": [], "attributable": True,
            "consequence_kind": "behaviour"}


def finding(**kw):
    base = {"id": "F1", "reviewer": "x", "severity": "nit", "class": None,
            "claim": "the claim, in a sentence", "evidence": [{"file": "a.php", "line": 1}],
            "consequence": "what happens in this code", "form": None, "diff_verifiable": True,
            "status": "proposed", "attributable": True}
    return {**base, **kw}


def answer(*findings, criteria=()):
    return {"findings": list(findings), "checked": [], "not_applicable": [],
            "criteria": list(criteria)}


def arguments(round_=None, reviewers=("correctness-proof-reviewer", "scope-coherence-reviewer")):
    args = {
        "package": "/pkg", "language": "pt-BR", "profile": {},
        "plan": {"reviewers": [{"agent": a, "slice": "complete", "model": "m"} for a in reviewers],
                 "skipped": [], "models": {"light": "l", "standard": "s", "heavy": "h"},
                 "effort": "full", "conventions": [], "agent_prefix": "prumo:"},
        "classes": [{"id": "A2", "section": "A", "state": "fired"}],
        "inline": {"item.md": "- do x", "criteria.json": '[{"id": "AC-1", "text": "do x"}]'},
    }
    if round_:
        args["round"] = round_
    return args


@unittest.skipUnless(shutil.which("node"), "the workflow is JavaScript: no node, no run")
class WorkflowCase(unittest.TestCase):
    def run_workflow(self, args, answers):
        with tempfile.TemporaryDirectory() as tmp:
            case = pathlib.Path(tmp) / "case.json"
            case.write_text(json.dumps({"args": args, "answers": answers}), encoding="utf-8")
            done = subprocess.run(["node", str(RUNNER), str(WORKFLOW), str(case)],
                                  capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        out = json.loads(done.stdout)
        self.labels = [c["label"] for c in out["calls"]]
        self.prompts = {c["label"]: c["prompt"] for c in out["calls"]}
        return out["result"]

    def by_id(self, result):
        return {f["id"]: f for f in result["findings"]}


class TestTheFirstRound(WorkflowCase):
    def test_every_reviewer_is_told_the_three_rules_and_the_language(self):
        self.run_workflow(arguments(), {})
        prompt = self.prompts["correctness-proof-reviewer"]
        for needle in ("THE PROOF BAR", "is a QUESTION put to you", "where its demand comes from",
                       "`pt-BR`"):
            self.assertIn(needle, prompt)

    def test_the_same_cause_raised_twice_is_one_finding(self):
        result = self.run_workflow(arguments(), {
            "correctness-proof-reviewer": answer(finding(severity="nit")),
            "scope-coherence-reviewer": answer(finding(severity="required", criterion="AC-1")),
            "group:same-cause": {"groups": [["COR-1", "SCO-1"]]},
            "verify:SCO-1": VERIFIED,
        })
        self.assertEqual(list(self.by_id(result)), ["SCO-1"])
        self.assertEqual(result["findings"][0]["also_raised_by"], ["correctness-proof-reviewer"])

    def test_only_a_blocking_finding_is_read_a_second_time(self):
        result = self.run_workflow(arguments(), {
            "correctness-proof-reviewer": answer(finding(severity="required"), finding()),
            "verify:COR-1": VERIFIED,
        })
        self.assertEqual([l for l in self.labels if l.startswith("verify:")], ["verify:COR-1"])
        self.assertEqual(self.by_id(result)["COR-2"]["status"], "unverified")

    def test_a_verifier_that_does_not_return_leaves_the_reviewer_s_word_standing(self):
        result = self.run_workflow(arguments(), {
            "correctness-proof-reviewer": answer(finding(severity="required"))})
        self.assertEqual(self.by_id(result)["COR-1"]["status"], "confirmed")

    def test_every_finding_carries_a_rule_field_even_when_the_reviewer_gave_none(self):
        result = self.run_workflow(arguments(), {
            "correctness-proof-reviewer": answer(finding())})
        self.assertIn("rule", result["findings"][0])


class TestADeltaRound(WorkflowCase):
    ROUND = {"mode": "delta", "n": 2, "since": "abc123", "previous_package": "/prev",
             "scripts_carried": True,
             "open": [finding(id="COR-1", severity="required", status="confirmed"),
                      finding(id="COR-2", severity="required", status="confirmed")]}

    def run_round(self, answers):
        return self.run_workflow(arguments(self.ROUND), answers)

    def test_the_reviewers_are_told_which_round_it_is_and_what_is_already_open(self):
        self.run_round({})
        prompt = self.prompts["correctness-proof-reviewer"]
        self.assertIn("THIS IS ROUND 2", prompt)
        self.assertIn("- COR-1: the claim, in a sentence", prompt)

    def test_what_the_push_fixed_is_closed_and_what_it_did_not_stays_open(self):
        result = self.run_round({"recheck:COR-1": {**VERIFIED, "verdict": "refuted", "reason": "a test pins it"},
                                 "recheck:COR-2": VERIFIED})
        self.assertEqual([c["id"] for c in result["closed_findings"]], ["COR-1"])
        still = self.by_id(result)["COR-2"]
        self.assertEqual((still["status"], still["carried_from"]), ("confirmed", "/prev"))

    def test_a_recheck_in_doubt_or_silent_keeps_the_finding_open(self):
        result = self.run_round({"recheck:COR-1": {**VERIFIED, "verdict": "uncertain"}})
        self.assertEqual({i: f["status"] for i, f in self.by_id(result).items()},
                         {"COR-1": "confirmed", "COR-2": "confirmed"})

    def test_a_new_finding_is_numbered_after_the_ones_carried(self):
        result = self.run_round({"correctness-proof-reviewer": answer(finding())})
        self.assertIn("COR-3", self.by_id(result))

    def test_a_new_finding_never_takes_an_id_the_round_before_already_gave(self):
        result = self.run_workflow(arguments({**self.ROUND, "taken": {"COR": 7}}),
                                   {"correctness-proof-reviewer": answer(finding())})
        self.assertIn("COR-8", self.by_id(result))

    def test_the_scripts_the_first_round_wrote_are_not_written_again(self):
        result = self.run_round({})
        self.assertNotIn("scripts_manual", result)
        self.assertNotIn("manual-verification-planner", " ".join(self.labels))


class TestARecheckRound(WorkflowCase):
    ROUND = {"mode": "recheck", "n": 3, "since": "abc123", "previous_package": "/prev",
             "scripts_carried": True,
             "open": [finding(id="COR-1", severity="required", status="confirmed")],
             "open_criteria": [{"id": "AC-1", "text": "do x", "record": "no test"}]}

    def test_no_reviewer_runs_and_only_what_is_open_is_read(self):
        result = self.run_workflow(arguments(self.ROUND), {
            "recheck:COR-1": {**VERIFIED, "verdict": "refuted"},
            "recheck:AC-1": {"state": "met", "record": "the test goes red without it"}})
        self.assertEqual(sorted(self.labels), ["recheck:AC-1", "recheck:COR-1"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["criteria"], [{"id": "AC-1", "state": "met",
                                               "record": "the test goes red without it"}])


if __name__ == "__main__":
    unittest.main()
