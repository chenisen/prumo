import pathlib, sys, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from triage import assess_class, triage_classes, size_band, activation_plan
from common import CLASSES
# Tolerant of both discovery forms: `discover -s tests` imports the modules as
# top-level, `-t .` imports them as a package. Without this the suite passes in
# one mode and breaks in the other — and that is how 41 tests vanished unnoticed.
try:
    from tests.test_validate_profile import minimal_profile
except ImportError:
    from test_validate_profile import minimal_profile

CAP_ALL = {"checks": {r: {"available": True} for r in
                      ("secrets", "static", "lint", "dependencies", "suite")}}
CAP_NONE = {"checks": {r: {"available": False, "reason": "tool absent"} for r in
                       ("secrets", "static", "lint", "dependencies", "suite")}}


def change(files=(), diff="", removed=(), lines=10):
    return {"arquivos": list(files), "diff": diff,
            "removidos": list(removed), "lines_changed": lines}


def assess(class_id, profile, chg, cap=CAP_ALL):
    return assess_class(class_id, CLASSES[class_id], profile, chg, cap)


class TestThreeStates(unittest.TestCase):
    def test_a_class_requiring_an_anchor_without_one_is_not_assessable(self):
        r = assess("A1", minimal_profile(), change(["app/x.php"]))
        self.assertEqual(r["state"], "not_assessable")
        self.assertIn("no anchor", r["reason"])

    def test_want_of_an_anchor_never_comes_out_as_did_not_fire(self):
        # the confusion the third state exists to prevent
        p = minimal_profile()
        for class_id, meta in CLASSES.items():
            if not meta["anchor_required"]:
                continue
            r = assess(class_id, p, change(["app/x.php"]))
            self.assertEqual(r["state"], "not_assessable", class_id)

    def test_an_anchor_that_matches_fires(self):
        p = minimal_profile(anchors={"A1": {"paths": ["database/migrations/**"]}})
        r = assess("A1", p, change(["database/migrations/2024_x.php"]))
        self.assertEqual(r["state"], "fired")
        self.assertTrue(r["evidence"])

    def test_an_anchor_that_does_not_match_does_not_fire_and_says_why(self):
        p = minimal_profile(anchors={"A1": {"paths": ["database/migrations/**"]}})
        r = assess("A1", p, change(["app/Foo.php"]))
        self.assertEqual(r["state"], "did_not_fire")
        self.assertTrue(r["reason"])

    def test_a_recorded_not_applicable_steps_aside_leaving_a_trail(self):
        p = minimal_profile(anchors={"B5": {"not_applicable": {
            "reason": "no interface", "approver": "someone", "date": "2026-10-28"}}})
        r = assess("B5", p, change(["app/x.php"]))
        self.assertEqual(r["state"], "did_not_fire")
        self.assertTrue(r["not_applicable"])
        self.assertIn("someone", r["reason"])


class TestUniversalFloor(unittest.TestCase):
    """The eight classes with no required anchor have to work in a repo with no profile."""

    def test_A9_catches_a_secret_by_its_content(self):
        d = "+++ b/docs/guide.md\n+  --token glpat-ABCdef123456789\n"
        r = assess("A9", minimal_profile(), change(["docs/guide.md"], d))
        self.assertEqual(r["state"], "fired")

    def test_A9_is_not_assessable_without_the_check(self):
        r = assess("A9", minimal_profile(), change(["app/x.php"]), CAP_NONE)
        self.assertEqual(r["state"], "not_assessable")

    def test_A3_catches_a_tenant_scope(self):
        d = "--- a/app/R.php\n-        ->where('tenant_id', $tenant)\n"
        self.assertEqual(assess("A3", minimal_profile(), change(["app/R.php"], d))["state"],
                         "fired")

    def test_A8_catches_a_deleted_assert(self):
        d = "--- a/tests/XTest.php\n-        $this->assertSame(3, $r->total);\n"
        self.assertEqual(assess("A8", minimal_profile(), change(["tests/XTest.php"], d))["state"],
                         "fired")

    def test_A8_catches_a_deleted_test_file(self):
        r = assess("A8", minimal_profile(), change([], "", removed=["tests/Feature/XTest.php"]))
        self.assertEqual(r["state"], "fired")

    def test_A8_catches_a_silenced_test(self):
        d = "+++ b/tests/XTest.php\n+        $this->markTestSkipped('later');\n"
        self.assertEqual(assess("A8", minimal_profile(), change(["tests/XTest.php"], d))["state"],
                         "fired")

    def test_the_floor_holds_with_no_profile_at_all(self):
        p = {"profile": "new-repo", "status": "absent", "anchors": {}, "checks": {}}
        d = "+ password = 'leakedsecret123'\n"
        self.assertEqual(assess("A9", p, change(["app/x.php"], d))["state"], "fired")


class TestWholeTriage(unittest.TestCase):
    def test_always_18_classes(self):
        self.assertEqual(len(triage_classes(minimal_profile(), change(), CAP_ALL)), 18)

    def test_every_class_carries_a_reason(self):
        for c in triage_classes(minimal_profile(), change(["app/x.php"]), CAP_ALL):
            self.assertTrue(c["reason"], c["id"])


class TestSizeAndTopology(unittest.TestCase):
    def test_bands(self):
        p = minimal_profile()
        for lines, band in ((50, "ideal"), (250, "acceptable"), (1500, "split")):
            self.assertEqual(size_band(p, change(lines=lines))["band"], band)

    def test_the_ideal_band_does_not_pay_for_fan_out(self):
        p = minimal_profile()
        m = change(["app/x.php"], lines=20)
        cl = triage_classes(p, m, CAP_ALL)
        plan = activation_plan(p, m, cl, size_band(p, m))
        self.assertEqual(len(plan["reviewers"]), 1)
        self.assertTrue(plan["skipped"])

    def test_the_contract_reviewer_runs_only_when_a_contract_class_fired(self):
        p = minimal_profile(anchors={"A2": {"paths": ["routes/**"]}})
        m = change(["routes/api.php"], lines=400)
        cl = triage_classes(p, m, CAP_ALL)
        plan = activation_plan(p, m, cl, size_band(p, m))
        self.assertIn("contract-compat-reviewer", [r["agent"] for r in plan["reviewers"]])

    def test_a_local_reviewer_replaces_the_architecture_one(self):
        p = minimal_profile(local_reviewer={"type": "agent", "name": "repo-reviewer"})
        m = change(["app/x.php"], lines=400)
        cl = triage_classes(p, m, CAP_ALL)
        plan = activation_plan(p, m, cl, size_band(p, m))
        agents = [r["agent"] for r in plan["reviewers"]]
        self.assertIn("repo-reviewer", agents)
        self.assertNotIn("architecture-reviewer", agents)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestCombinationAndOr(unittest.TestCase):
    """The path says WHERE to look; the pattern says WHAT. In some classes one is not enough."""

    def test_A3_demands_both_path_and_pattern(self):
        p = minimal_profile(anchors={"A3": {"paths": ["app/**"], "patterns": [r"\btenant"]}})
        d = "diff --git a/app/Foo.php b/app/Foo.php\n+ $x = 1;\n"
        self.assertEqual(assess("A3", p, change(["app/Foo.php"], d))["state"], "did_not_fire")

    def test_A3_fires_with_both(self):
        p = minimal_profile(anchors={"A3": {"paths": ["app/**"], "patterns": [r"\btenant"]}})
        d = "diff --git a/app/Foo.php b/app/Foo.php\n-  ->where('tenant_id', $t)\n"
        self.assertEqual(assess("A3", p, change(["app/Foo.php"], d))["state"], "fired")

    def test_the_pattern_must_sit_in_the_file_the_path_pointed_at(self):
        p = minimal_profile(anchors={"A3": {"paths": ["app/**"], "patterns": [r"\btenant"]}})
        d = ("diff --git a/app/Foo.php b/app/Foo.php\n+ $x = 1;\n"
             "diff --git a/docs/notes.md b/docs/notes.md\n+ talks about tenant here\n")
        r = assess("A3", p, change(["app/Foo.php", "docs/notes.md"], d))
        self.assertEqual(r["state"], "did_not_fire")

    def test_A9_stays_an_or(self):
        """A secret pasted into a .md is as exposed as one in a .env — the lesson that bore the rule."""
        p = minimal_profile(anchors={"A9": {"paths": [".env*"]}})
        d = "diff --git a/docs/guide.md b/docs/guide.md\n+  --token glpat-ABCdef123456789\n"
        self.assertEqual(assess("A9", p, change(["docs/guide.md"], d))["state"], "fired")

    def test_A10_fires_on_the_path_alone(self):
        p = minimal_profile(anchors={"A10": {"paths": ["app/Route.php"]}})
        self.assertEqual(assess("A10", p, change(["app/Route.php"], ""))["state"], "fired")

    def test_A5_does_not_mistake_matches_for_match(self):
        d = "+        $matches = $promotion->get();\n"
        self.assertEqual(assess("A5", minimal_profile(), change(["app/x.php"], d))["state"],
                         "did_not_fire")

    def test_A5_fires_on_decision_logic(self):
        d = "+        if ($contract->loyalty >= 12) { $price = $price * 0.9; }\n"
        self.assertEqual(assess("A5", minimal_profile(), change(["app/x.php"], d))["state"],
                         "fired")

    def test_A6_ignores_an_ordinary_concrete_class(self):
        d = "+class ProductController extends Controller\n"
        self.assertEqual(assess("A6", minimal_profile(), change(["app/x.php"], d))["state"],
                         "did_not_fire")

    def test_A6_fires_on_a_new_abstraction(self):
        d = "+interface OfferRepositoryContract\n"
        self.assertEqual(assess("A6", minimal_profile(), change(["app/x.php"], d))["state"],
                         "fired")


class TestCodeShapeInsideADocument(unittest.TestCase):
    """Hunting for the SHAPE of code inside a .md finds the WORD, not the fact.
    Measured on a real proposal: `Container` matched Docker's "container", in a markdown."""

    DIFF_MD = ("diff --git a/docs/guide.md b/docs/guide.md\n"
               "+  run it inside the application container\n")

    def test_A6_does_not_fire_on_a_document(self):
        r = assess("A6", minimal_profile(), change(["docs/guide.md"], self.DIFF_MD))
        self.assertEqual(r["state"], "did_not_fire")

    def test_A6_ignores_lowercase_container_even_in_code(self):
        d = "diff --git a/app/x.php b/app/x.php\n+  // bring the container up and run\n"
        self.assertEqual(assess("A6", minimal_profile(), change(["app/x.php"], d))["state"],
                         "did_not_fire")

    def test_A6_still_catches_real_dependency_injection(self):
        d = "diff --git a/app/P.php b/app/P.php\n+        $this->app->bind(Contract::class, Impl::class);\n"
        self.assertEqual(assess("A6", minimal_profile(), change(["app/P.php"], d))["state"],
                         "fired")

    def test_A5_does_not_fire_on_a_document(self):
        d = ("diff --git a/docs/rules.md b/docs/rules.md\n"
             "+  if loyalty is >= 12 months, apply the discount\n")
        self.assertEqual(assess("A5", minimal_profile(), change(["docs/rules.md"], d))["state"],
                         "did_not_fire")

    def test_A9_STILL_reads_a_document(self):
        """The deliberate exception: a secret in a .md is as exposed as one in a .env."""
        d = "diff --git a/docs/guide.md b/docs/guide.md\n+  --token glpat-ABCdef123456789\n"
        self.assertEqual(assess("A9", minimal_profile(), change(["docs/guide.md"], d))["state"],
                         "fired")


class TestSliceWithoutAHeader(unittest.TestCase):
    """A diff with no `diff --git` gives no way to attribute a line to a file. Returning
    nothing would make the class stop firing IN SILENCE — the failure mode this harness fights."""

    def test_a_bare_diff_still_fires(self):
        d = "+        if ($contract->loyalty >= 12) { $price = $price * 0.9; }\n"
        self.assertEqual(assess("A5", minimal_profile(), change(["app/x.php"], d))["state"],
                         "fired")

    def test_a_bare_diff_in_the_combination_still_fires(self):
        p = minimal_profile(anchors={"A3": {"paths": ["app/**"], "patterns": [r"\btenant"]}})
        d = "-  ->where('tenant_id', $t)\n"
        self.assertEqual(assess("A3", p, change(["app/Foo.php"], d))["state"], "fired")


def test_every_reviewer_slice_is_written_by_the_package():
    """A reviewer pointing at a slice that does not exist raises no error: it reads nothing.

    The workflow builds `<package>/slices/<slice>.patch` as text. A name `package.py` never
    writes would have the reviewer read nothing and report "no findings" — the failure mode
    this harness exists not to have. The two lists are coupled and nobody was checking them.
    """
    import package
    import triage

    declared = {"complete", *package.SLICES}
    used = {r["slice"] for r in triage.REVIEWERS}
    assert used <= declared, (
        f"slice with no matching file: {sorted(used - declared)} — "
        f"package.py writes {sorted(declared)}")
