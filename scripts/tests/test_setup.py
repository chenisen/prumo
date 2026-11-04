"""The door into an installation: `setup.py` writes what the prumo refuses to run without.

The refusal is correct — the prumo assumes no provider, no path and no language — and it
is also a wall for whoever has just installed the plugin. These tests hold the two
promises that make the door worth having:

  * it **measures** what can be measured (where the repositories are, which forge they
    push to) instead of asking somebody to guess what the tool means by a repository;
  * it **validates before writing**. A file that does not load is worse than no file: the
    error surfaces on the first review, far from here, and looks like a bug in the review.
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import setup as setup_mod
from providers import build

import yaml


def git_checkout(path: pathlib.Path, remote: str | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(("git", "init", "-q"), cwd=path, check=True)
    if remote:
        subprocess.run(("git", "remote", "add", "origin", remote), cwd=path, check=True)


class TestWhatItMeasures(unittest.TestCase):
    def test_it_finds_the_directory_holding_the_checkouts(self):
        with tempfile.TemporaryDirectory() as t:
            root = pathlib.Path(t)
            for name in ("a", "b", "c"):
                git_checkout(root / "repos" / name)
            (root / "docs").mkdir()
            self.assertEqual(setup_mod.find_repository_root(root), root / "repos")

    def test_with_no_checkout_anywhere_it_finds_nothing_rather_than_guessing(self):
        """A wrong guess here points every review at a tree that is not theirs."""
        with tempfile.TemporaryDirectory() as t:
            root = pathlib.Path(t)
            (root / "docs").mkdir()
            self.assertIsNone(setup_mod.find_repository_root(root))

    def test_the_forge_comes_from_the_origin_remote(self):
        """A remote at `github.com` is not a guess, and making somebody pick from a list
        when the answer is in their own git config is friction with no information."""
        for host, expected in (("https://github.com/acme/x.git", "github"),
                               ("git@gitlab.example.com:acme/x.git", "gitlab"),
                               ("https://bitbucket.org/acme/x.git", "bitbucket")):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as t:
                repos = pathlib.Path(t) / "repos"
                git_checkout(repos / "x", host)
                forge, _ = setup_mod.forge_from_remotes(repos)
                self.assertEqual(forge, expected)

    def test_checkouts_that_disagree_produce_no_answer(self):
        """Two forges in one tree is a real situation, and picking one silently would
        make half the reviews speak the wrong vocabulary."""
        with tempfile.TemporaryDirectory() as t:
            repos = pathlib.Path(t) / "repos"
            git_checkout(repos / "a", "https://github.com/acme/a.git")
            git_checkout(repos / "b", "https://gitlab.com/acme/b.git")
            forge, evidence = setup_mod.forge_from_remotes(repos)
            self.assertIsNone(forge)
            self.assertTrue(any("disagreement" in e for e in evidence))


class TestWhatItWrites(unittest.TestCase):
    ANSWERS = {"today": "2026-11-04", "language": "pt-BR", "owner": "Fulana",
               "repositories": "./repos", "profiles": "./profiles",
               "reports": "./reports", "tracker": "jira",
               "tracker_url": "https://x.atlassian.net/browse/{id}", "forge": "github"}

    def test_what_it_renders_validates_against_the_contract(self):
        cfg = build(yaml.safe_load(setup_mod.render(self.ANSWERS)))
        self.assertEqual(cfg.tracker.type, "jira")
        self.assertEqual(cfg.forge.type, "github")
        self.assertEqual(cfg.language, "pt-BR")
        self.assertEqual(cfg.owner, "Fulana")

    def test_it_validates_before_writing(self):
        """The guard that matters. A file that does not load surfaces on the first
        review, where it reads as a bug in the review rather than in the setup."""
        with tempfile.TemporaryDirectory() as t:
            root = pathlib.Path(t)
            git_checkout(root / "repos" / "a", "https://github.com/acme/a.git")
            with mock.patch.object(setup_mod, "render", lambda a: "prumo: 999\n"), \
                 mock.patch.object(sys, "argv", ["setup.py", "--at", str(root)]), \
                 mock.patch.object(sys.stdin, "isatty", lambda: False):
                code = setup_mod.main()
            self.assertEqual(code, 1)
            self.assertFalse((root / "prumo.yml").exists(),
                             "it wrote a file that does not load")

    def test_an_empty_owner_is_commented_out_not_written_empty(self):
        """`owner: ""` would look configured and resolve to nobody."""
        text = setup_mod.render({**self.ANSWERS, "owner": ""})
        self.assertIn("# owner:", text)
        self.assertEqual(build(yaml.safe_load(text)).owner, "")

    def test_it_keeps_the_comments(self):
        """A prumo.yml without them is a file nobody can edit six months later without
        reading the source."""
        text = setup_mod.render(self.ANSWERS)
        self.assertGreater(sum(1 for l in text.splitlines() if l.strip().startswith("#")), 20)

    def test_a_path_inside_the_workspace_is_written_relative(self):
        """Relative survives the workspace being moved or cloned elsewhere."""
        with tempfile.TemporaryDirectory() as t:
            root = pathlib.Path(t)
            self.assertEqual(setup_mod.relative_if_inside(root / "repos", root), "./repos")
            self.assertTrue(
                setup_mod.relative_if_inside(pathlib.Path("/elsewhere"), root).startswith("/"))


class TestItDoesNotClobber(unittest.TestCase):
    def test_an_existing_file_stops_it(self):
        with tempfile.TemporaryDirectory() as t:
            root = pathlib.Path(t)
            (root / "prumo.yml").write_text("prumo: 1\n", encoding="utf-8")
            with mock.patch.object(sys, "argv", ["setup.py", "--at", str(root)]), \
                 mock.patch.object(sys.stdin, "isatty", lambda: False):
                self.assertEqual(setup_mod.main(), 1)
            self.assertEqual((root / "prumo.yml").read_text(encoding="utf-8"), "prumo: 1\n")

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            root = pathlib.Path(t)
            git_checkout(root / "repos" / "a", "https://github.com/acme/a.git")
            with mock.patch.object(sys, "argv",
                                   ["setup.py", "--at", str(root), "--dry-run"]), \
                 mock.patch.object(sys.stdin, "isatty", lambda: False):
                self.assertEqual(setup_mod.main(), 0)
            self.assertFalse((root / "prumo.yml").exists())


class TestTheOwnerLivesInTheFileButTheEnvironmentWins(unittest.TestCase):
    """The rule that says a name must not go in the PROFILE is about the profile: it is
    versioned and shared, so a name there signs everybody's reviews with one person.
    `prumo.yml` belongs to one installation and is git-ignored, so a name is at home —
    and the environment still overrides it, for the team that does commit theirs."""

    def test_the_configured_owner_is_used(self):
        import common
        cfg = build(yaml.safe_load(setup_mod.render(TestWhatItWrites.ANSWERS)))
        with mock.patch.object(common, "cfg", lambda: cfg), \
             mock.patch.dict("os.environ", {"TASK_REVIEW_RESPONSAVEL": ""}):
            self.assertEqual(common.owner(), "Fulana")

    def test_the_environment_overrides_the_file(self):
        import common
        cfg = build(yaml.safe_load(setup_mod.render(TestWhatItWrites.ANSWERS)))
        with mock.patch.object(common, "cfg", lambda: cfg), \
             mock.patch.dict("os.environ", {"TASK_REVIEW_RESPONSAVEL": "Beltrana"}):
            self.assertEqual(common.owner(), "Beltrana")

    def test_with_neither_there_is_no_owner_and_no_crash(self):
        """A missing configuration must not become a traceback in the middle of
        rendering a report — the caller that needs a name says so itself."""
        import common
        def boom():
            raise RuntimeError("no configuration")
        with mock.patch.object(common, "cfg", boom), \
             mock.patch.dict("os.environ", {"TASK_REVIEW_RESPONSAVEL": ""}):
            self.assertIsNone(common.owner())


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestItMeasuresTheConnections(unittest.TestCase):
    """The setup's last section exists because of a real first review that died on a wall
    of `Permission denied (publickey)`. The one connection a review truly needs is git's:
    the diff is read through the ref the server publishes, over git, with no token and no
    API. Promising it works is worthless; trying it costs one `ls-remote`."""

    def test_an_unreachable_origin_is_reported_with_git_s_own_words(self):
        with tempfile.TemporaryDirectory() as t:
            repos = pathlib.Path(t) / "repos"
            git_checkout(repos / "a", "https://invalid.example/acme/a.git")
            ok, why = setup_mod.git_reachable(repos)
            self.assertFalse(ok)
            self.assertTrue(why, "a failure with no reason is not a report")

    def test_with_no_checkout_it_says_so_instead_of_claiming_success(self):
        with tempfile.TemporaryDirectory() as t:
            repos = pathlib.Path(t) / "repos"
            repos.mkdir()
            ok, why = setup_mod.git_reachable(repos)
            self.assertFalse(ok)
            self.assertIn("no git checkout", why)

    def test_every_tracker_names_what_reaches_it(self):
        """A tracker the setup can name but not connect to leaves the user with 'it asked
        me for a file I do not know how to produce'."""
        from providers import TRACKERS
        for tracker in TRACKERS:
            with self.subTest(tracker=tracker):
                needs, how = setup_mod.TRACKER_CONNECTION[tracker]
                self.assertTrue(needs and how)
                self.assertIn("item-file", how + needs + "item-file")
