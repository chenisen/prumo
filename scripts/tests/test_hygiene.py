import pathlib, subprocess, sys, tempfile, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import hygiene
from providers import InvalidConfiguration, build, looks_like_a_secret


# The fake secrets this suite needs are BUILT, never written down: a literal here would
# be a secret shape in a tracked file, and hygiene.py is right to fail it. Splitting them
# is the same discipline the module asks of everybody else, applied to its own test.
FAKE_TOKEN = "glpat-" + "A1b2C3d4E5f6G7h8I9"
FAKE_KEY = "-----BEGIN " + "RSA PRIVATE KEY-----"
FAKE_URL = "https://bot:" + "hunter2secret" + "@forge/x.git"


def repo_with(files: dict[str, str]) -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp())
    subprocess.run(("git", "init", "-q"), cwd=d, check=True)
    for name, text in files.items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")
    subprocess.run(("git", "add", "-A"), cwd=d, check=True)
    return d


class TestTheRepositoryCarriesNoSecret(unittest.TestCase):
    def test_the_prumo_itself_is_clean(self):
        self.assertEqual(hygiene.check(), [])

    def test_a_token_in_a_tracked_file_fails(self):
        d = repo_with({"notes.md": f"export GITLAB_TOKEN={FAKE_TOKEN}\n"})
        self.assertTrue(any("looks like a secret" in p for p in hygiene.check(d)))

    def test_a_private_key_fails(self):
        d = repo_with({"k.pem": FAKE_KEY + "\nx\n"})
        self.assertTrue(hygiene.check(d))

    def test_a_password_in_a_url_fails(self):
        d = repo_with({"doc.md": f"clone {FAKE_URL}\n"})
        self.assertTrue(hygiene.check(d))

    def test_the_name_of_a_variable_is_not_a_secret(self):
        d = repo_with({"doc.md": "export GITLAB_TOKEN=... # the name, not the value\n"})
        self.assertEqual(hygiene.check(d), [])

    def test_a_declared_fixture_is_not_a_finding(self):
        d = repo_with({"t.py": 'TOKEN = "glpat-ABCdef123456789"\n'})
        self.assertEqual(hygiene.check(d), [])


class TestTheRepositoryCarriesOnlyItsOwnRules(unittest.TestCase):
    def test_an_installations_configuration_fails(self):
        d = repo_with({"prumo.yml": "prumo: 1\n"})
        self.assertTrue(any("installation" in p for p in hygiene.check(d)))

    def test_a_repository_profile_fails(self):
        d = repo_with({"profiles/some-service.yml": "profile: some-service\n"})
        self.assertTrue(hygiene.check(d))

    def test_a_report_and_its_run_record_fail(self):
        d = repo_with({"reports/r/review-2026-01-01.md": "x\n", "runs.jsonl": "{}\n"})
        self.assertEqual(len(hygiene.check(d)), 2)

    def test_another_projects_harness_instructions_fail(self):
        d = repo_with({"CLAUDE.md": "rules of a project that is not this one\n"})
        self.assertTrue(hygiene.check(d))

    def test_the_example_is_the_contract_and_stays(self):
        d = repo_with({"prumo.yml.example": "prumo: 1\n"})
        self.assertEqual(hygiene.check(d), [])


class TestAConfigurationNeverHoldsTheValue(unittest.TestCase):
    BASE = {"prumo": 1, "language": "en",
            "paths": {"repositories": ".", "profiles": ".", "reports": "."},
            "tracker": {"type": "generic"}}

    def test_a_token_written_into_the_configuration_is_refused(self):
        data = {**self.BASE,
                "forge": {"type": "gitlab", "tokens": [FAKE_TOKEN]}}
        with self.assertRaises(InvalidConfiguration) as e:
            build(data)
        self.assertIn("never a token", str(e.exception))

    def test_the_name_of_the_variable_is_what_it_takes(self):
        data = {**self.BASE, "forge": {"type": "gitlab", "tokens": ["GITLAB_TOKEN"]}}
        self.assertEqual(build(data).forge.tokens, ("GITLAB_TOKEN",))

    def test_an_unknown_connection_is_refused(self):
        data = {**self.BASE, "forge": {"type": "gitlab", "connection": "carrier-pigeon"}}
        with self.assertRaises(InvalidConfiguration):
            build(data)

    def test_the_three_connections_are_accepted_and_none_is_the_default(self):
        for c in ("mcp", "token", "none"):
            data = {**self.BASE, "forge": {"type": "github", "connection": c}}
            self.assertEqual(build(data).forge.connection, c)
        data = {**self.BASE, "forge": {"type": "github"}}
        self.assertEqual(build(data).forge.connection, "none")


if __name__ == "__main__":
    unittest.main()
