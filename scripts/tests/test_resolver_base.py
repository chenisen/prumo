"""A base named for one review, when the branch was built on another line.

Twice on 2026-11-17/18 a branch cut from one line and merged with the work it needed had
two common ancestors with the profile's base: the diff dragged in commits that were not
the item's. The review was measured by a temporary ref and the profile's `base.branch`
edited for the length of a prepare, then restored by hand. `--base` names it instead.
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import common
import resolver


def git(cwd, *args):
    return subprocess.run(("git", *args), cwd=cwd, capture_output=True, text=True,
                          check=True).stdout.strip()


def commit(repo, name):
    (repo / name).write_text(name, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-qm", name)
    return git(repo, "rev-parse", "HEAD")


class ABaseNamedForTheReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name).resolve()
        self.repo = root / "repositories" / "repo-x"
        self.repo.mkdir(parents=True)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        commit(self.repo, "root")
        git(self.repo, "checkout", "-qb", "other-line")
        self.built_on = commit(self.repo, "work-it-needs")
        git(self.repo, "checkout", "-qb", "item")
        self.item_commit = commit(self.repo, "the-item")
        git(self.repo, "checkout", "-q", "main")
        commit(self.repo, "main-moves")
        (root / "profiles").mkdir()
        (root / "profiles" / "repo-x.yml").write_text("profile: repo-x\nbase:\n  branch: main\n",
                                                      encoding="utf-8")
        (root / "prumo.yml").write_text(textwrap.dedent('''\
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
            '''), encoding="utf-8")
        self.env = dict(os.environ)
        os.environ["PRUMO_CONFIG"] = str(root / "prumo.yml")
        common.cfg.cache_clear()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        common.cfg.cache_clear()
        self.tmp.cleanup()

    def test_the_profile_base_drags_in_the_other_line(self):
        r = resolver.resolve("repo-x", None, "item", "1")
        subjects = git(self.repo, "log", "--format=%s", f"{r['merge_base']}..{r['head_sha']}").splitlines()
        self.assertEqual(sorted(subjects), ["the-item", "work-it-needs"])

    def test_a_named_base_measures_only_the_item(self):
        r = resolver.resolve("repo-x", None, "item", "1", base=self.built_on)
        self.assertEqual(r["merge_base"], self.built_on)
        subjects = git(self.repo, "log", "--format=%s", f"{r['merge_base']}..{r['head_sha']}").splitlines()
        self.assertEqual(subjects, ["the-item"])
        from i18n import t
        self.assertEqual(r["base"]["source"], t("resolver.base_given"))

    def test_a_branch_name_is_a_base_too(self):
        r = resolver.resolve("repo-x", None, "item", "1", base="other-line")
        self.assertEqual(r["base"]["sha"], self.built_on)

    def test_a_base_that_resolves_to_nothing_is_refused(self):
        with self.assertRaises(common.UsageError):
            resolver.resolve("repo-x", None, "item", "1", base="no-such-ref")


if __name__ == "__main__":
    unittest.main()
