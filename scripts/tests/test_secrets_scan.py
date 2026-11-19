"""The scanner the prumo carries for a profile's `secrets` check, and the review running it.

The fake values are assembled at run time, piece by piece: written whole, they would be
exactly what `hygiene.py` exists to refuse in this repository."""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import secrets_scan
from i18n import t

SCANNER = pathlib.Path(secrets_scan.__file__).resolve()
TOKEN = "gh" + "p_" + "Ab3dE6gH9jK2mN5pQ8sT1vW4yZ7cF0hJ"
CLOUD_ID = "AK" + "IA" + "QWERTYUIOPASDFGH"
RANDOM = "q8Zr" + "T2vLx9Wm4Kp7Ns1B"


def git(repo, *args):
    subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True)


def repo_with(base_text: str, branch_text: str) -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp())
    git(d, "init", "-q", "-b", "main")
    git(d, "config", "user.email", "t@t"); git(d, "config", "user.name", "t")
    (d / "app.cfg").write_text(base_text)
    git(d, "add", "app.cfg"); git(d, "commit", "-qm", "base")
    git(d, "checkout", "-qb", "change")
    (d / "app.cfg").write_text(branch_text)
    git(d, "commit", "-qam", "change")
    return d


class TestWhatTheChangeAdds(unittest.TestCase):
    def test_a_token_the_change_adds_is_found_and_never_printed_whole(self):
        d = repo_with("name = 'x'\n", f"name = 'x'\nkey = '{TOKEN}'\n")
        found = secrets_scan.scan(d, "main", "change", False)
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("app.cfg:2: "))
        self.assertNotIn(TOKEN, found[0])

    def test_a_secret_the_change_removes_is_not_its_to_answer_for(self):
        d = repo_with(f"key = '{TOKEN}'\n", "key = ''\n")
        self.assertEqual(secrets_scan.scan(d, "main", "change", False), [])

    def test_a_cloud_key_id_and_a_random_value_under_a_secret_name(self):
        d = repo_with("", f"id = {CLOUD_ID}\nclient_secret = \"{RANDOM}\"\n")
        labels = [f.split(": ", 1)[1].split(" — ")[0] for f in secrets_scan.scan(d, "main", "change", False)]
        self.assertEqual(labels, ["cloud access key id", "random value assigned to a secret name"])

    def test_a_placeholder_and_a_name_are_not_secrets(self):
        d = repo_with("", "password = \"<your password here>\"\napi_key = \"${API_KEY_FROM_ENV}\"\n"
                          "token_env = 'GITHUB_TOKEN'\npassword = 'aaaaaaaaaaaaaaaaaaaa'\n")
        self.assertEqual(secrets_scan.scan(d, "main", "change", False), [])

    def test_a_fake_said_out_loud_is_spared(self):
        d = repo_with("", f"key = '{TOKEN}'  # prumo: allow-secret — fixture\n")
        self.assertEqual(secrets_scan.scan(d, "main", "change", False), [])
        d = repo_with("", f"key = '{TOKEN}'\n")
        (d / secrets_scan.ALLOW_FILE).write_text("# fixtures\n^ghp_Ab3dE6\n")
        self.assertEqual(secrets_scan.scan(d, "main", "change", False), [])

    def test_the_exit_code_says_found_or_not(self):
        dirty = repo_with("", f"key = '{TOKEN}'\n")
        clean = repo_with("", "key = ''\n")
        run = lambda d: subprocess.run((sys.executable, str(SCANNER), "--repo", str(d), "--base", "main",
                                        "--head", "change"), capture_output=True, text=True)
        self.assertEqual(run(dirty).returncode, 1)
        r = run(clean)
        self.assertEqual(r.returncode, 0)
        self.assertIn("no secret found", r.stdout)


class TestTheReviewRunsIt(unittest.TestCase):
    """A scanner that exists and never runs left A9 assessable with nothing scanned."""

    def package_for(self, d: pathlib.Path) -> tuple[pathlib.Path, dict]:
        import review
        sha = lambda ref: subprocess.run(("git", "rev-parse", ref), cwd=d, capture_output=True, text=True).stdout.strip()
        command = f"{sys.executable} {SCANNER} --repo {{workdir}} --base {{base}} --head {{head}}"
        profile = {"checks": {"secrets": {"command": command, "required": True}}}
        pkg = pathlib.Path(tempfile.mkdtemp())
        (pkg / "capabilities.json").write_text(json.dumps({"checks": {"secrets": {"available": True}}}))
        (pkg / "manifest.json").write_text(json.dumps({"merge_base": sha("main"), "head_sha": sha("change"),
                                                       "base": {"sha": sha("main")}}))
        (pkg / "classes.json").write_text(json.dumps([{"id": "A9", "section": "A", "state": "did_not_fire",
                                                        "reason": "x", "evidence": []}]))
        return pkg, profile

    def test_a_red_scan_fires_A9_with_the_scanner_s_lines(self):
        import review
        d = repo_with("", f"key = '{TOKEN}'\n")
        pkg, profile = self.package_for(d)
        out = review.run_secrets_check(pkg, profile, d)
        self.assertEqual((out["ran"], out["state"]), (True, "red"))
        a9 = json.loads((pkg / "classes.json").read_text())[0]
        self.assertEqual((a9["state"], a9["cause"]), ("fired", "check_red"))
        self.assertEqual(a9["evidence"][0]["file"], "app.cfg")
        self.assertTrue((pkg / review.SECRETS_RUN).read_text().startswith("app.cfg:1: "))

    def test_a_green_scan_says_it_ran_and_leaves_the_triage_alone(self):
        import review
        d = repo_with("", "key = ''\n")
        pkg, profile = self.package_for(d)
        out = review.run_secrets_check(pkg, profile, d)
        self.assertEqual((out["state"], out["reason"]),
                         ("green", t("review.secrets_clean_exit_only", run=review.SECRETS_RUN)))
        self.assertEqual(json.loads((pkg / "classes.json").read_text())[0]["state"], "did_not_fire")

    def test_a_green_the_output_proves_says_so(self):
        import review
        d = repo_with("", "key = ''\n")
        pkg, profile = self.package_for(d)
        profile["checks"]["secrets"]["success_pattern"] = "no secret found"
        out = review.run_secrets_check(pkg, profile, d)
        self.assertEqual((out["state"], out["reason"]),
                         ("green", t("review.secrets_clean", run=review.SECRETS_RUN)))

    def test_exit_zero_with_output_that_proves_nothing_is_not_green(self):
        """2026-11-19: seventeen scans green while the container had failed to mount the
        repository — the wrapper returned 0 either way."""
        import review
        d = repo_with("", "key = ''\n")
        pkg, profile = self.package_for(d)
        # exits 0 and prints nothing that shows a scan, as the container did
        profile["checks"]["secrets"]["command"] = f"{sys.executable} -c print(0)"
        profile["checks"]["secrets"]["success_pattern"] = r"INF scanned ~"
        out = review.run_secrets_check(pkg, profile, d)
        self.assertEqual((out["ran"], out["state"]), (False, "not_run"))
        a9 = json.loads((pkg / "classes.json").read_text())[0]
        self.assertEqual((a9["state"], a9["cause"]), ("not_assessable", "check_unproven"))

    def test_the_failure_pattern_wins_over_the_success_pattern(self):
        import review
        d = repo_with("", "key = ''\n")
        pkg, profile = self.package_for(d)
        # exits 0, and its output matches both: the leak is what counts
        profile["checks"]["secrets"]["command"] = f"{sys.executable} -c print(41+1)"
        profile["checks"]["secrets"].update(success_pattern="4", failure_pattern=r"42")
        self.assertEqual(review.run_secrets_check(pkg, profile, d)["state"], "red")

    def test_no_command_no_run(self):
        import review
        d = repo_with("", "key = ''\n")
        pkg, _ = self.package_for(d)
        self.assertIsNone(review.run_secrets_check(pkg, {"checks": {}}, d))


if __name__ == "__main__":
    unittest.main()
