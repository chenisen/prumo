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
    return {"files": list(files), "diff": diff,
            "removed": list(removed), "lines_changed": lines}


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


class TestThePackageKeysTriageReads(unittest.TestCase):
    """`triage.py` reads `change.json`. A key the package never writes is not a nil-safe
    default: it is a rule that can never fire, and it fails in SILENCE — the class simply
    reports "nothing matched the anchor" forever.

    Found on 2026-11-04: `change.json` has called the list `files` since the method was
    written in English, and triage still asked for `arquivos`. Every anchored class was
    dead, and the three always-on reviewers never activated.
    """

    def test_an_anchored_class_fires_when_a_file_matches_its_anchor(self):
        """A1 takes the path alone — no `combination` — so an anchor match IS the
        evidence. Under the defect this was unreachable: `files` came back empty, so
        `by_path` was empty, so no anchored class could ever fire."""
        profile = {"profile": "repo-x", "anchors": {"A1": {"paths": ["database/migrations/**"]}}}
        change = {"files": ["database/migrations/2026_01_01_add_column.php"],
                  "removed": [], "diff": "+ $table->string('x');\n"}
        state = assess_class("A1", CLASSES["A1"], profile, change, CAP_ALL)
        self.assertEqual("fired", state["state"], state["reason"])

    def test_A8_still_needs_a_removal_and_not_merely_a_test_path(self):
        """A8 combines path AND pattern. Adding tests under an anchored `tests/**` is not
        removing or weakening one, and the fix above must not turn every test delivery
        into a human decision."""
        profile = {"profile": "repo-x", "anchors": {"A8": {"paths": ["tests/**"]}}}
        change = {"files": ["tests/Module/XTest.php"], "removed": [],
                  "diff": "+ $this->assertSame(1, 1);\n"}
        state = assess_class("A8", CLASSES["A8"], profile, change, CAP_ALL)
        self.assertEqual("did_not_fire", state["state"])

    def test_the_always_on_reviewers_activate_when_the_change_has_files(self):
        from triage import REVIEWERS
        change = {"files": ["tests/Module/XTest.php"], "diff": ""}
        always_on = [r["agent"] for r in REVIEWERS
                     if not r.get("always") and r["when"](change, [])]
        for agent in ("correctness-proof-reviewer", "security-privacy-reviewer",
                      "architecture-reviewer"):
            self.assertIn(agent, always_on)

    def test_no_reader_asks_for_a_key_the_package_does_not_write(self):
        """The general form of the same defect, so the next rename is caught here."""
        import re
        written = {"files", "removed", "added", "lines_changed", "diff", "slices",
                   "over_ceiling"}  # exactly what package.py writes
        source = pathlib.Path(__file__).resolve().parent.parent / "triage.py"
        asked = set(re.findall(r'change\.get\("([a-z_]+)"\)', source.read_text(encoding="utf-8")))
        self.assertEqual(set(), asked - written, f"triage.py reads keys change.json never writes")


class TestA8DoesNotFireOnProse(unittest.TestCase):
    """`only` is an ordinary English word, and A8 used to match it bare. A comment
    reading `// Known current behavior, not the desired one: only the exact spelling …`
    fired A8 on a delivery that ADDED 217 tests and removed none — and a fired §A class
    closes gate 2, so a comment cost a human decision and an INTERVENTION. Measured
    twice on 2026-11-04, over the same delivery."""

    def fires(self, added_line):
        profile = minimal_profile()
        profile["anchors"] = {"A8": {"paths": ["tests/**"], "cards": []}}
        diff = ("diff --git a/tests/FooTest.php b/tests/FooTest.php\n"
                "--- a/tests/FooTest.php\n+++ b/tests/FooTest.php\n@@ -1 +1,2 @@\n"
                f"{added_line}\n")
        return assess("A8", profile,
                      change(["tests/FooTest.php"], diff=diff))["state"] == "fired"

    def test_the_english_word_only_in_a_comment_does_not_fire(self):
        self.assertFalse(self.fires(
            "+        // Known current behavior, not the desired one: only the exact "
            "spelling content-type replaces the GET default."))

    def test_the_english_word_skip_in_prose_does_not_fire(self):
        self.assertFalse(self.fires("+        // we skip the second branch on purpose"))

    def test_a_method_named_after_the_word_does_not_fire(self):
        self.assertFalse(self.fires(
            "+    public function it_sends_json_when_form_is_asked_for_known_current_behavior()"))

    def test_a_real_javascript_narrowing_still_fires(self):
        self.assertTrue(self.fires("+  it.only('does the thing', () => {"))

    def test_a_real_phpunit_skip_still_fires(self):
        self.assertTrue(self.fires("+        $this->markTestSkipped('flaky');"))

    def test_a_real_pytest_skip_still_fires(self):
        self.assertTrue(self.fires("+@pytest.mark.skip(reason='broken')"))

    def test_a_real_go_skip_still_fires(self):
        self.assertTrue(self.fires("+	t.Skip(\"needs a database\")"))

    def test_a_removed_test_still_fires_whatever_the_wording(self):
        self.assertTrue(self.fires("-    it('does the thing', () => {"))


class TestAgentPrefix(unittest.TestCase):
    """How the harness addresses the agents, and in which order the three sources speak.

    The order matters because the only one that is DETECTED is the one that goes quiet
    the moment a package is prepared outside the plugin's own skill — and a plan carrying
    the wrong prefix kills the workflow on its first agent, after the package is built."""

    def setUp(self):
        import os, triage
        self.triage = triage
        self.saved = {k: os.environ.get(k)
                      for k in ("PRUMO_PREFIXO_AGENTE", "CLAUDE_PLUGIN_ROOT")}
        for k in self.saved:
            os.environ.pop(k, None)
        self.addCleanup(self._restore)

    def _restore(self):
        import os
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _with_config(self, prefix):
        """Stand `cfg()` in for one call, the way the installation would answer."""
        import unittest.mock as mock
        from providers import test_config
        return mock.patch.object(self.triage, "cfg",
                                 lambda: test_config(agents={"prefix": prefix})
                                 if prefix is not None else test_config())

    def test_the_configuration_decides_when_it_declares_a_prefix(self):
        with self._with_config("prumo:"):
            self.assertEqual(self.triage.agent_prefix(), "prumo:")

    def test_an_empty_string_in_the_configuration_is_an_answer_not_a_silence(self):
        # agents loose in `.claude/agents/` answer to the bare name, and declaring that
        # must not fall through to the detection
        with self._with_config(""):
            self.assertEqual(self.triage.agent_prefix(), "")

    def test_the_environment_overrides_the_configuration(self):
        import os
        os.environ["PRUMO_PREFIXO_AGENTE"] = "other:"
        with self._with_config("prumo:"):
            self.assertEqual(self.triage.agent_prefix(), "other:")

    def test_undeclared_falls_back_to_the_plugin_variable(self):
        import os
        with self._with_config(None):
            self.assertEqual(self.triage.agent_prefix(), "")
            os.environ["CLAUDE_PLUGIN_ROOT"] = "/somewhere/prumo"
            self.assertEqual(self.triage.agent_prefix(), "prumo:")

    def test_a_configuration_that_cannot_be_read_does_not_break_the_triage(self):
        # `prumo.yml` is a precondition of the REVIEW, not of this function: a missing
        # file must come out as the readable impediment `load_config` raises elsewhere,
        # never as a traceback inside the activation plan.
        import unittest.mock as mock
        from providers import MissingConfiguration

        def boom():
            raise MissingConfiguration("no prumo.yml")

        with mock.patch.object(self.triage, "cfg", boom):
            self.assertEqual(self.triage.agent_prefix(), "")
