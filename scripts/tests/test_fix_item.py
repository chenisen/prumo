"""What `deliver.py prepare --fix` takes from a review, and the package's own item handed back.

Two refusals of 2026-11-18: a person asked to fix a finding the review left uncertain
(`confirm_finding`), beside two nits they chose, and `--fix` said "no author_fix route —
nothing to fix"; the round went in through `--continue` with the item written by hand.
And `prepare --item-file <package>/item.md` on that same package raised SameFileError.
"""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import deliver
from i18n import t


class Review(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.review = pathlib.Path(self.tmp.name)
        (self.review / "manifest.json").write_text(json.dumps(
            {"item": {"id": "52468"}, "origin": "52468-branch", "merge_base": "abc"}))
        self.findings = [
            {"id": "COR-1", "severity": "required", "claim": "the catch marks lines outside the send",
             "evidence": [{"file": "app/C.php", "line": 221}], "consequence": "c1", "form": None},
            {"id": "COR-2", "severity": "nit", "claim": "the mock ignores the party id",
             "evidence": [{"file": "tests/T.php", "line": 9}], "consequence": "c2", "form": None},
            {"id": "COR-3", "severity": "nit", "claim": "mixed lines give 500",
             "evidence": [{"file": "app/R.php", "line": 3}], "consequence": "c3", "form": None},
        ]
        (self.review / "findings.json").write_text(json.dumps(self.findings))

    def tearDown(self):
        self.tmp.cleanup()

    def verdict(self, *routes):
        (self.review / "verdict.json").write_text(json.dumps({"routes": list(routes)}))


class FixItem(Review):

    def test_an_uncertain_finding_is_fixed_when_a_person_asks(self):
        self.verdict({"type": "confirm_finding", "items": ["COR-1"]})
        item, origin, text = deliver.fix_item(self.review)
        self.assertEqual((item, origin), ("52468", "52468-branch"))
        self.assertIn("the catch marks lines outside the send", text)
        self.assertIn(t("deliver.cli.fix_confirmed_by_asking"), text)

    def test_the_findings_a_person_chose_are_the_item_and_no_more(self):
        self.verdict({"type": "confirm_finding", "items": ["COR-1"]})
        _, _, text = deliver.fix_item(self.review, ["COR-1", "COR-2"])
        self.assertIn("the mock ignores the party id", text)
        self.assertNotIn("mixed lines give 500", text)
        self.assertEqual(text.count(t("deliver.cli.fix_confirmed_by_asking")), 1)

    def test_an_id_the_review_does_not_hold_is_refused(self):
        self.verdict({"type": "author_fix", "items": ["COR-1"]})
        with self.assertRaises(deliver.UsageError):
            deliver.fix_item(self.review, ["COR-9"])

    def test_a_review_with_nothing_to_fix_is_still_refused(self):
        self.verdict({"type": "manual_check", "items": ["AC-7"]})
        with self.assertRaises(deliver.UsageError):
            deliver.fix_item(self.review)


class ThePackagesOwnItem(unittest.TestCase):
    def test_handing_back_the_package_item_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = pathlib.Path(tmp)
            (package / "item.md").write_text("# the item\n", encoding="utf-8")
            deliver.read_item(str(package / "item.md"), package)
            self.assertEqual((package / "item.md").read_text(encoding="utf-8"), "# the item\n")

    def test_another_file_is_still_copied(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = pathlib.Path(tmp)
            (package / "item.md").write_text("# old\n", encoding="utf-8")
            (package / "new.md").write_text("# new\n", encoding="utf-8")
            deliver.read_item(str(package / "new.md"), package)
            self.assertEqual((package / "item.md").read_text(encoding="utf-8"), "# new\n")


if __name__ == "__main__":
    unittest.main()


class AFixOfProofOnly(Review):
    """Twice on 2026-11-18 a `--fix` of findings about tests that did not prove came back
    BLOCKED as a behaviour change — nothing goes red on a base whose behaviour the round
    never meant to change — and was prepared again by hand as debt · coverage-gap."""

    def test_every_finding_read_as_proof_makes_a_proof_round(self):
        for f in self.findings:
            f["consequence_kind"] = "proof"
        (self.review / "findings.json").write_text(json.dumps(self.findings))
        self.verdict({"type": "author_fix", "items": ["COR-1", "COR-2"]})
        self.assertTrue(deliver.fix_is_proof_only(self.review))

    def test_one_behaviour_finding_keeps_it_a_behaviour_change(self):
        self.findings[0]["consequence_kind"] = "proof"
        self.findings[1]["consequence_kind"] = "behaviour"
        (self.review / "findings.json").write_text(json.dumps(self.findings))
        self.verdict({"type": "author_fix", "items": ["COR-1", "COR-2"]})
        self.assertFalse(deliver.fix_is_proof_only(self.review))
        self.assertTrue(deliver.fix_is_proof_only(self.review, ["COR-1"]))

    def test_a_finding_nobody_read_twice_is_not_presumed_proof(self):
        self.verdict({"type": "author_fix", "items": ["COR-1"]})
        self.assertFalse(deliver.fix_is_proof_only(self.review))
