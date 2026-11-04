"""The profile contract: what the schema holds, and what only a reading can hold.

The assertions compare against the catalogue (`t(...)`), never against a fragment of the
sentence. A pinned fragment turns every rewording — and every translation — into a
broken-looking rule, while saying nothing about WHICH rule fired. The key says it.
"""
import pathlib, sys, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from i18n import t
from validate_profile import validate


def minimal_profile(**override):
    base = {
        "profile": "repo-x", "version": 1, "status": "draft",
        "stack": {"family": "other", "manifests": []},
        "conventions": [],
        "proof_registry": {"type": "absent", "root": None, "note": None},
        "base": {"branch": "main"},
        "living_docs": [],
        "checks": {r: {"command": None, "required": False}
                   for r in ("secrets", "static", "lint", "dependencies", "suite")},
        "sensitive_data": {"fields": [], "forbidden_destinations": []},
        "size": {"ideal": 100, "acceptable": 300, "split": 1000, "file_ceiling": 1000},
        "anchors": {}, "evals": [],
    }
    base.update(override)
    return base


class TestContract(unittest.TestCase):
    def test_minimal_profile_is_valid(self):
        errors, _ = validate(minimal_profile())
        self.assertEqual(errors, [])

    def test_a_missing_key_is_an_error(self):
        p = minimal_profile(); del p["conventions"]
        errors, _ = validate(p)
        self.assertTrue(any("conventions" in e for e in errors), errors)

    def test_declared_absence_is_valid(self):
        # an empty list, a null command and an `absent` type are VALUES, not omissions
        errors, _ = validate(minimal_profile(conventions=[]))
        self.assertEqual(errors, [])

    def test_confirmed_requires_a_person_and_a_date(self):
        errors, _ = validate(minimal_profile(status="confirmed"))
        self.assertIn(t("validate.confirmation_requires", field="confirmed_by"), errors)
        self.assertIn(t("validate.confirmation_requires", field="confirmed_at"), errors)

    def test_confirmed_with_a_person_and_a_date_passes(self):
        errors, _ = validate(minimal_profile(
            status="confirmed", confirmed_by="someone", confirmed_at="2026-10-28",
            anchors={"A1": {"paths": ["db/**"]}, "A2": {"paths": ["x/**"]},
                     "A4": {"paths": ["y/**"]}, "A10": {"paths": ["z/**"]}}))
        self.assertEqual(errors, [])


class TestAnchors(unittest.TestCase):
    def test_unknown_class(self):
        errors, _ = validate(minimal_profile(anchors={"A99": {"paths": ["x"]}}))
        self.assertTrue(errors)

    def test_an_empty_anchor_ties_nothing_down(self):
        errors, _ = validate(minimal_profile(anchors={"A1": {"cards": [1]}}))
        self.assertIn(t("validate.anchor_empty", klass="A1"), errors)

    def test_a_section_B_class_requires_a_verification(self):
        errors, _ = validate(minimal_profile(anchors={"B4": {"paths": ["x/**"]}}))
        self.assertIn(t("validate.anchor_needs_verification", klass="B4"), errors)

    def test_a_section_B_class_with_a_verification_passes(self):
        errors, _ = validate(minimal_profile(
            anchors={"B4": {"paths": ["x/**"], "verification": "aggregate audit"}}))
        self.assertEqual(errors, [])

    def test_not_applicable_requires_reason_approver_and_date(self):
        errors, _ = validate(minimal_profile(
            anchors={"B5": {"not_applicable": {"reason": "no interface"}}}))
        self.assertTrue(errors)

    def test_a_complete_not_applicable_passes(self):
        errors, _ = validate(minimal_profile(anchors={"B5": {"not_applicable": {
            "reason": "the repository has no interface", "approver": "someone",
            "date": "2026-10-28"}}}))
        self.assertEqual(errors, [])


class TestWarnings(unittest.TestCase):
    def test_a_required_check_with_no_tool_warns(self):
        p = minimal_profile()
        p["checks"]["secrets"] = {"command": None, "required": True}
        errors, warnings = validate(p)
        self.assertEqual(errors, [])
        self.assertIn(t("validate.check_without_tool", role="secrets"), warnings)

    def test_an_expired_waiver_warns(self):
        p = minimal_profile()
        p["checks"]["secrets"] = {"command": None, "required": True, "waiver": {
            "reason": "no tool", "approver": "someone", "expires": "2020-01-01"}}
        _, warnings = validate(p)
        self.assertIn(t("validate.waiver_expired", role="secrets", date="2020-01-01"),
                      warnings)

    def test_an_eval_with_no_anchor_warns(self):
        p = minimal_profile(evals=[{"item": 1, "class": "B1", "expects": "fire"}])
        _, warnings = validate(p)
        self.assertIn(t("validate.eval_without_anchor", i=0, klass="B1"), warnings)

    def test_an_almost_empty_confirmed_profile_warns(self):
        _, warnings = validate(minimal_profile(
            status="confirmed", confirmed_by="x", confirmed_at="2026-10-28"))
        self.assertIn(t("validate.thin_coverage", mapped=0), warnings)


if __name__ == "__main__":
    unittest.main(verbosity=2)
