"""The review judges WHAT THE DELIVERY DID — not the state of the repository.

A defect the change did not cause is not a finding of this review: it leaves the count,
it leaves the verdict, and it goes to the observations section. Mixing the two makes the
delivery answer for the repository's debt, and buries what needs fixing inside what
nobody asked anyone to look at.
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from judge import judge_delivery, APPROVED, INTERVENTION


def finding(id_, attributable, severity="required", status="confirmed"):
    return {"id": id_, "severity": severity, "status": status,
            "attributable": attributable, "class": None, "evidence": [{"file": "a.php"}]}


def judge(findings):
    return judge_delivery(profile={"profile": "r", "status": "confirmed"}, classes=[],
                          findings=findings, criteria=[], evidence={}, scope={},
                          resolutions=[], today="2026-10-29")


class TestVerdict(unittest.TestCase):
    def test_a_severe_finding_of_the_project_does_not_hold_the_proposal(self):
        self.assertEqual(judge([finding("OBS-1", attributable=False)])["verdict"], APPROVED)

    def test_a_severe_finding_of_the_delivery_holds_it(self):
        self.assertEqual(judge([finding("A-1", attributable=True)])["verdict"], INTERVENTION)

    def test_the_project_one_enters_no_route(self):
        v = judge([finding("OBS-1", attributable=False)])
        items = [i for r in v["routes"] for i in r["items"]]
        self.assertNotIn("OBS-1", items)


class TestPartitionInConclude(unittest.TestCase):
    """`conclude` splits the two files before the judge ever reads them."""

    def test_splits_into_findings_and_observations(self):
        with tempfile.TemporaryDirectory() as d:
            package = pathlib.Path(d)
            everything = [finding("A-1", True), finding("OBS-1", False), finding("A-2", True)]
            (package / "findings.json").write_text(json.dumps(everything), encoding="utf-8")
            (package / "evidence.json").write_text("{}", encoding="utf-8")

            # runs only the partition passage, without invoking judge/render
            code = (ROOT / "review.py").read_text(encoding="utf-8")
            start = code.index("    findings_path = package / \"findings.json\"")
            end = code.index("    # `criteria.json` can only arrive AFTER")
            snippet = "\n".join(l[4:] if l.startswith("    ") else l
                                for l in code[start:end].splitlines())
            exec(compile(snippet, "partition", "exec"),
                 {"package": package, "read_json": lambda p: json.loads(p.read_text()),
                  "write_json": lambda p, d: p.write_text(json.dumps(d)),
                  "print": lambda *a: None, "t": lambda key, **kw: ""})

            self.assertEqual(
                [f["id"] for f in json.loads((package / "findings.json").read_text())],
                ["A-1", "A-2"])
            self.assertEqual(
                [f["id"] for f in json.loads((package / "observations.json").read_text())],
                ["OBS-1"])

    def test_with_nothing_of_the_project_no_observations_file_is_created(self):
        with tempfile.TemporaryDirectory() as d:
            package = pathlib.Path(d)
            (package / "findings.json").write_text(
                json.dumps([finding("A-1", True)]), encoding="utf-8")
            code = (ROOT / "review.py").read_text(encoding="utf-8")
            start = code.index("    findings_path = package / \"findings.json\"")
            end = code.index("    # `criteria.json` can only arrive AFTER")
            snippet = "\n".join(l[4:] if l.startswith("    ") else l
                                for l in code[start:end].splitlines())
            exec(compile(snippet, "partition", "exec"),
                 {"package": package, "read_json": lambda p: json.loads(p.read_text()),
                  "write_json": lambda p, d: p.write_text(json.dumps(d)),
                  "print": lambda *a: None, "t": lambda key, **kw: ""})
            self.assertFalse((package / "observations.json").exists())


class TestInstructionToTheReviewers(unittest.TestCase):
    """The agents' contract has to say it — otherwise the harness filters out what
    should never have been raised at all, and spends a read on what it will discard."""

    def test_every_reviewer_is_told_not_to_raise_the_pre_existing(self):
        agents = sorted((ROOT.parent / "agents").glob("*-reviewer.md"))
        self.assertGreaterEqual(len(agents), 7)
        for f in agents:
            text = f.read_text(encoding="utf-8")
            self.assertIn("not your finding", text, f.name)
            self.assertIn("be here if", text, f.name)


if __name__ == "__main__":
    unittest.main()
