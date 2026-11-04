import pathlib, sys, tempfile, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from derive_profile import derive, detect_family, detect_registry
from i18n import t
from validate_profile import validate


def fake_repo(files: dict[str, str]) -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp())
    for name, content in files.items():
        target = d / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return d


class TestFamily(unittest.TestCase):
    def test_laravel(self):
        d = fake_repo({"composer.json": "{}", "artisan": ""})
        self.assertEqual(detect_family(d)["family"], "php-laravel")

    def test_next(self):
        d = fake_repo({"package.json": "{}", "next.config.js": ""})
        self.assertEqual(detect_family(d)["family"], "node-next")

    def test_php_without_laravel(self):
        d = fake_repo({"composer.json": "{}"})
        self.assertEqual(detect_family(d)["family"], "php")

    def test_no_manifest_is_a_composition(self):
        d = fake_repo({"docker-compose.yml": ""})
        self.assertEqual(detect_family(d)["family"], "composicao")


class TestProofRegistry(unittest.TestCase):
    """Detection belongs to the METHOD; the list of markers belongs to the installation.

    This test once pinned the name of one company's specification framework. A public
    prumo cannot carry that name, and a test demanding it made the coupling mandatory.
    What it proves now is the mechanism: the public markers hold with no configuration,
    a marker declared under `detection` is recognised, and a repository with none comes
    out `absent` — a valid answer, which leaves class A7 `not_assessable`."""

    def test_the_public_markers_hold_without_configuration(self):
        cases = [
            ({".claude/skills/speckit-specify/SKILL.md": ""}, "speckit"),
            ({".claude/specs/1-x/spec.md": ""}, "specs-in-repo"),
            ({"README.md": ""}, "absent"),
        ]
        for files, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(detect_registry(fake_repo(files))["type"], expected)

    def test_a_marker_declared_by_the_installation_is_recognised(self):
        from unittest import mock
        import derive_profile
        from providers import test_config
        # Patch `derive_profile.cfg`, not `common.cfg`: the module imported the name, so
        # it holds its own reference, and patching the origin never reaches it.
        cfg = test_config(detection={"proof_registries": [
            {"type": "my-framework", "marker": "my-framework.json",
             "note": "declared by this installation"}]})
        with mock.patch.object(derive_profile, "cfg", lambda: cfg):
            self.assertEqual(
                detect_registry(fake_repo({"my-framework.json": "{}"}))["type"],
                "my-framework")

    def test_the_declared_one_beats_the_public_one_when_both_match(self):
        """The installation knows its own repository better than the default does."""
        from unittest import mock
        import derive_profile
        from providers import test_config
        cfg = test_config(detection={"proof_registries": [
            {"type": "my-framework", "marker": ".claude/specs"}]})
        with mock.patch.object(derive_profile, "cfg", lambda: cfg):
            self.assertEqual(
                detect_registry(fake_repo({".claude/specs/1-x/spec.md": ""}))["type"],
                "my-framework")


class TestDerivation(unittest.TestCase):
    def test_always_a_draft(self):
        profile, _ = derive("x", fake_repo({"composer.json": "{}", "artisan": ""}))
        self.assertEqual(profile["status"], "draft")
        self.assertIsNone(profile["confirmed_by"])

    def test_the_derived_profile_validates_against_the_contract(self):
        for files in ({"composer.json": "{}", "artisan": "", "database/migrations/a.php": ""},
                      {"package.json": "{}", "next.config.js": ""},
                      {"docker-compose.yml": ""}):
            with self.subTest(files=sorted(files)):
                profile, _ = derive("x", fake_repo(files))
                self.assertEqual(validate(profile)[0], [])

    def test_an_anchor_that_does_not_resolve_stays_out(self):
        # with no database/migrations on disk, A1 gets no invented anchor
        profile, _ = derive("x", fake_repo({"composer.json": "{}", "artisan": ""}))
        self.assertNotIn("A1", profile["anchors"])

    def test_an_anchor_that_resolves_goes_in(self):
        profile, _ = derive("x", fake_repo(
            {"composer.json": "{}", "artisan": "", "database/migrations/a.php": ""}))
        self.assertIn("database/migrations/**", profile["anchors"]["A1"]["paths"])

    def test_the_pending_items_name_what_was_missing(self):
        _, pending = derive("x", fake_repo({"docker-compose.yml": ""}))
        self.assertIn(t("derive.pending_proof_registry"), pending)
        self.assertIn(t("derive.pending_secrets"), pending)

    def test_an_absent_proof_registry_becomes_a_pending_item_for_A7(self):
        _, pending = derive("x", fake_repo({"composer.json": "{}", "artisan": ""}))
        self.assertTrue(any("A7" in p for p in pending), pending)


if __name__ == "__main__":
    unittest.main(verbosity=2)
