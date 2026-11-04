"""A class that asks about PRODUCTION does not fire over a proof file.

Straight from production, 2026-10-29: a delivery that only added tests made A5 ("did a
business rule change?") fire, because the name of a domain enum appeared inside a test
file. A §A class that fires opens a human-decision route and closes a gate — the cost of
the false positive is somebody having to decide something that never happened.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from common import CLASSES
from triage import assess_class

PROFILE = {"profile": "repo-x", "anchors": {}}
CAP = {"checks": {}}

DIFF_TEST = """diff --git a/tests/Unit/Promotion/RuleTest.php b/tests/Unit/Promotion/RuleTest.php
+++ b/tests/Unit/Promotion/RuleTest.php
+        'type' => EligibilityRuleTypeEnum::SERVICE_CATEGORY->value,
+        $this->assertTrue($price >= 10);
"""
DIFF_PRODUCTION = """diff --git a/app/Rules/Rule.php b/app/Rules/Rule.php
+++ b/app/Rules/Rule.php
+        if ($eligibility >= 10) {
"""


def change(files, diff):
    return {"arquivos": files, "removidos": [], "diff": diff, "corpo": diff}


def assess(class_id, files, diff):
    return assess_class(class_id, CLASSES[class_id], PROFILE, change(files, diff), CAP)


class TestA5DoesNotFireOverProof(unittest.TestCase):
    def test_a_test_only_change_does_not_fire_A5(self):
        c = assess("A5", ["tests/Unit/Promotion/RuleTest.php"], DIFF_TEST)
        self.assertEqual(c["state"], "did_not_fire", c)

    def test_and_the_reason_SAYS_it_was_because_it_is_proof(self):
        c = assess("A5", ["tests/Unit/Promotion/RuleTest.php"], DIFF_TEST)
        self.assertIn("proof", c["reason"])
        self.assertIn("production", c["reason"])

    def test_the_same_shape_in_production_STILL_fires(self):
        """The guard must not blind the class: production is still read."""
        c = assess("A5", ["app/Rules/Rule.php"], DIFF_PRODUCTION)
        self.assertEqual(c["state"], "fired", c)

    def test_production_alongside_a_test_fires_on_the_production(self):
        c = assess("A5", ["app/Rules/Rule.php", "tests/Unit/Promotion/RuleTest.php"],
                   DIFF_PRODUCTION + DIFF_TEST)
        self.assertEqual(c["state"], "fired", c)


class TestA8StillLooksAtTests(unittest.TestCase):
    """The class that asks ABOUT tests cannot be hit by the guard."""

    def test_A8_is_not_production_only(self):
        self.assertFalse(CLASSES["A8"].get("production_only", False))

    def test_A8_fires_when_an_assert_is_deleted_from_a_test(self):
        """A8 carries `combination: e` — it needs the path AND the pattern. The production
        guard must not take its path away, and its path is precisely `tests/**`."""
        diff = ("diff --git a/tests/Unit/Promotion/RuleTest.php b/tests/Unit/Promotion/RuleTest.php\n"
                "--- a/tests/Unit/Promotion/RuleTest.php\n"
                "+++ b/tests/Unit/Promotion/RuleTest.php\n"
                "-        $this->assertTrue($refused);\n")
        m = {"arquivos": ["tests/Unit/Promotion/RuleTest.php"], "removidos": [],
             "diff": diff, "corpo": diff}
        c = assess_class("A8", CLASSES["A8"], {"profile": "repo-x",
                         "anchors": {"A8": {"paths": ["tests/**"]}}}, m, CAP)
        self.assertEqual(c["state"], "fired", c)


class TestWhichClassesAreAboutProduction(unittest.TestCase):
    def test_production_only_implies_code_only(self):
        """Prose is already excluded by code_only; production_only narrows, never widens."""
        for class_id, meta in CLASSES.items():
            if meta.get("production_only"):
                self.assertTrue(meta.get("code_only"), class_id)


if __name__ == "__main__":
    unittest.main()
