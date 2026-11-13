"""Nothing of the prumo lands directly on a protected branch.

The guard is a script the hook calls on every `git` command: push to, commit on, or
rewrite a protected branch of a repository under `paths.repositories` prints a reason
and the hook blocks. Feature branches pass; a repository outside the root passes — the
prumo's own checkout lives there; the list is the installation's, with a default.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import common
import branch_guard
from i18n import t

try:
    from tests.test_deliver_hook import run_hook
except ImportError:
    from test_deliver_hook import run_hook


def git(cwd, *args):
    subprocess.run(("git", "-c", "user.name=t", "-c", "user.email=t@t", *args), cwd=cwd,
                   check=True, capture_output=True, text=True)


class ProtectedBranches(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.repo = self.root / "repositories" / "repo-x"
        self.repo.mkdir(parents=True)
        (self.repo / "a.txt").write_text("a\n", encoding="utf-8")
        git(self.repo, "init", "-q", "-b", "homolog")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-q", "-m", "base")
        self.elsewhere = self.root / "elsewhere" / "tool"
        self.elsewhere.mkdir(parents=True)
        git(self.elsewhere, "init", "-q", "-b", "main")
        self.config(None)
        self.env = dict(os.environ)
        os.environ["PRUMO_CONFIG"] = str(self.root / "prumo.yml")
        os.environ["CLAUDE_PROJECT_DIR"] = str(self.root)
        common.cfg.cache_clear()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        common.cfg.cache_clear()
        self.tmp.cleanup()

    def config(self, protected, delegated=None):
        extra = f"  protected_branches: {json.dumps(protected)}\n" if protected is not None else ""
        extra += f"  delegated_merge: {json.dumps(delegated)}\n" if delegated is not None else ""
        (self.root / "prumo.yml").write_text(textwrap.dedent('''\
            prumo: 1
            language: en
            paths:
              repositories: ./repositories
              profiles: ./profiles
              reports: ./reports
              packages: ./packages
            tracker:
              type: businessmap
              url: "https://example.invalid/cards/{id}"
            forge:
              type: gitlab
            ''') + extra, encoding="utf-8")
        common.cfg.cache_clear()

    def judge(self, command, cwd=None):
        return branch_guard.judge(command, str(cwd or self.repo))

    # ---- push
    def test_a_push_to_a_protected_branch_by_refspec_is_blocked(self):
        for cmd in ("git push origin homolog", "git push -u origin main",
                    "git push origin HEAD:refs/heads/master", "git push --force origin feature:prod",
                    "git push origin --delete develop"):
            self.assertIsNotNone(self.judge(cmd), cmd)

    def test_a_bare_push_while_on_a_protected_branch_is_blocked(self):
        self.assertEqual(self.judge("git push"), t("guard.push_protected", branch="homolog", repo="repo-x"))
        self.assertIsNotNone(self.judge("git push origin HEAD"))

    def test_a_push_of_a_feature_branch_passes(self):
        git(self.repo, "checkout", "-q", "-b", "ITEM-1-prove-it")
        for cmd in ("git push", "git push -u origin ITEM-1-prove-it",
                    "git push origin HEAD:ITEM-1-prove-it", "git push origin homolog:ITEM-1-prove-it"):
            self.assertIsNone(self.judge(cmd), cmd)

    # ---- writing on the branch
    def test_a_commit_on_a_protected_branch_is_blocked_and_on_a_feature_branch_passes(self):
        self.assertEqual(self.judge("git commit -m x"),
                         t("guard.write_protected", action="commit", branch="homolog", repo="repo-x"))
        self.assertIsNotNone(self.judge("git merge feature"))
        self.assertIsNotNone(self.judge("git reset --hard HEAD~1"))
        git(self.repo, "checkout", "-q", "-b", "feature")
        self.assertIsNone(self.judge("git commit -m x"))
        self.assertIsNone(self.judge("git merge homolog"))

    def test_moving_a_protected_branch_by_force_is_blocked(self):
        git(self.repo, "checkout", "-q", "-b", "feature")
        self.assertIsNotNone(self.judge("git checkout -B main HEAD"))
        self.assertIsNotNone(self.judge("git branch -f homolog HEAD"))
        self.assertIsNone(self.judge("git checkout -B feature-2 HEAD"))
        self.assertIsNone(self.judge("git checkout homolog"))

    # ---- scope
    def test_the_directory_comes_from_cd_or_dash_C_in_the_command(self):
        self.assertIsNotNone(self.judge(f"cd {self.repo} && git push origin homolog", cwd=self.root))
        self.assertIsNotNone(self.judge(f"git -C {self.repo} commit -m x", cwd=self.root))
        self.assertIsNone(self.judge("git push origin homolog", cwd=self.root))

    def test_the_review_time_block_asks_whether_the_command_runs_under_the_root(self):
        """A commit in the prumo's own checkout was blocked mid-review, six reviews into
        a day of the prumo's own development. The review-time rule now asks the same
        script that scopes the protected-branch rule: under `paths.repositories`, yes;
        anywhere else, no; the `cd` in the command counts."""
        self.assertTrue(branch_guard.inside_repositories("git commit -m x", str(self.repo)))
        self.assertTrue(branch_guard.inside_repositories(f"cd {self.repo} && git commit -m x", "/"))
        outside = self.root / "elsewhere"; outside.mkdir(exist_ok=True)
        self.assertFalse(branch_guard.inside_repositories("git commit -m x", str(outside)))
        self.assertFalse(branch_guard.inside_repositories(f"cd {outside} && git commit -m x", str(self.repo)))
        out = subprocess.run((sys.executable, str(pathlib.Path(branch_guard.__file__)),
                              "--pwd", str(outside), "--command", "git commit -m x",
                              "--inside-repositories"), capture_output=True, text=True,
                             env={**os.environ, "PRUMO_CONFIG": os.environ.get("PRUMO_CONFIG", "")})
        self.assertIn(out.stdout.strip(), ("yes", "no"))
        hook = (pathlib.Path(branch_guard.__file__).parent.parent / "hooks" / "review-guard.sh").read_text()
        self.assertIn("--inside-repositories", hook)
        self.assertIn('[ "$inside" != "no" ]', hook)

    def test_a_repository_outside_the_root_is_not_the_guards_business(self):
        (self.elsewhere / "f").write_text("x", encoding="utf-8")
        self.assertIsNone(self.judge("git push origin main", cwd=self.elsewhere))
        self.assertIsNone(self.judge("git commit -m x", cwd=self.elsewhere))

    def test_the_list_is_the_installations(self):
        self.config(["release"])
        self.assertIsNone(self.judge("git push origin homolog"))
        self.assertIsNotNone(self.judge("git push origin release"))
        self.assertEqual(common.cfg().forge.protected_branches, ("release",))

    def test_the_default_list(self):
        from providers import DEFAULT_PROTECTED_BRANCHES
        self.assertEqual(DEFAULT_PROTECTED_BRANCHES, ("homolog", "main", "master", "prod", "develop"))
        self.assertEqual(common.cfg().forge.protected_branches, DEFAULT_PROTECTED_BRANCHES)

    # ---- through the hook, no marker anywhere
    def test_the_hook_blocks_the_push_and_lets_the_feature_branch_through(self):
        self.assertEqual(run_hook(self.root, "Bash", command=f"git -C {self.repo} push origin homolog"), 2)
        self.assertEqual(run_hook(self.root, "Bash", command=f"git -C {self.repo} push origin feature"), 0)
        self.assertEqual(run_hook(self.root, "Bash", command=f"git -C {self.repo} status"), 0)


    # ---- merging: a person's act, unless the installation delegated it
    MERGES = ("gh pr merge 25 --merge", "glab mr merge 3",
              "curl -X PUT https://forge.example.invalid/repos/o/r/pulls/25/merge")

    def merges(self):
        return [run_hook(self.root, "Bash", command=c) for c in self.MERGES]

    def test_merge_is_blocked_when_the_installation_did_not_delegate_it(self):
        self.assertFalse(common.cfg().forge.delegated_merge)
        self.assertEqual(self.merges(), [2, 2, 2])
        r = subprocess.run([sys.executable, branch_guard.__file__, "--pwd", str(self.root),
                            "--command", "gh pr merge 25", "--merge"], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (2, t("guard.merge_not_delegated")))

    def test_a_delegated_merge_passes_with_the_configuration_the_environment_names(self):
        self.config(None, delegated=True)
        self.assertTrue(common.cfg().forge.delegated_merge)
        self.assertEqual(self.merges(), [0, 0, 0])

    def test_a_delegated_merge_passes_with_the_configuration_found_by_walking_up(self):
        """No PRUMO_CONFIG: the hook asks from the project's directory, where the
        installation's prumo.yml lives."""
        del os.environ["PRUMO_CONFIG"]
        self.config(None, delegated=True)
        self.assertEqual(self.merges(), [0, 0, 0])
        self.config(None)
        self.assertEqual(self.merges(), [2, 2, 2])

    def test_a_delegated_merge_is_still_blocked_while_a_review_or_a_delivery_runs(self):
        self.config(None, delegated=True)
        (self.root / ".claude").mkdir(exist_ok=True)
        for name in (".task-review-ativo", ".prumo-deliver-ativo"):
            with self.subTest(marker=name):
                marker = self.root / ".claude" / name
                marker.write_text("{}", encoding="utf-8")
                self.assertEqual(run_hook(self.root, "Bash", command="gh pr merge 25"), 2)
                marker.unlink()
        self.assertEqual(run_hook(self.root, "Bash", command="gh pr merge 25"), 0)

    def test_delegation_is_a_boolean_and_a_configuration_that_fails_does_not_delegate(self):
        from providers import InvalidConfiguration
        self.config(None, delegated="yes")
        with self.assertRaises(InvalidConfiguration):
            common.cfg()
        self.assertEqual(self.merges(), [2, 2, 2])


if __name__ == "__main__":
    unittest.main()
