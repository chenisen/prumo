"""The model follows the task's demand — `models:` in prumo.yml maps tiers to names.

The method chooses TIERS (light|standard|heavy) from what it measured; which model
answers a tier is an installation fact. No section → every agent inherits the session's
model, exactly the behaviour before tiers existed.
"""
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import common
from providers import InvalidConfiguration, load_config
from triage import activation_plan

BASE = """prumo: 1
language: en
paths:
  repositories: ./repositories
  profiles: ./profiles
  reports: ./reports
tracker:
  type: businessmap
  url: "https://example.invalid/cards/{id}"
forge:
  type: gitlab
"""

FULL_EFFORT = {"effort": "full", "reasons": ["test"],
               "shape": {"shape": "mixed"}}


def _classes(*fired):
    return [{"id": c, "state": "fired"} for c in fired]


class TestTheInstallationNamesTheTiers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        for d in ("repositories", "profiles", "reports"):
            (self.root / d).mkdir()
        self.env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        common.cfg.cache_clear()
        self.tmp.cleanup()

    def configure(self, extra=""):
        (self.root / "prumo.yml").write_text(BASE + extra, encoding="utf-8")
        os.environ["PRUMO_CONFIG"] = str(self.root / "prumo.yml")
        common.cfg.cache_clear()

    def test_no_section_means_inherit(self):
        self.configure()
        self.assertIsNone(common.model_for("heavy"))
        self.assertEqual(common.models_map(), {})

    def test_a_named_tier_resolves_and_a_missing_one_inherits(self):
        self.configure("models:\n  heavy: big-model\n")
        self.assertEqual(common.model_for("heavy"), "big-model")
        self.assertIsNone(common.model_for("light"))
        self.assertEqual(common.models_map(), {"heavy": "big-model"})

    def test_an_unknown_tier_is_refused_at_load(self):
        self.configure("models:\n  turbo: x\n")
        with self.assertRaises(InvalidConfiguration):
            load_config(self.root)

    def test_the_plan_resolves_tiers_and_carries_the_map(self):
        self.configure("models:\n  light: s\n  standard: m\n  heavy: l\n")
        plan = activation_plan({"profile": "x"}, {}, _classes("A5"),
                               {"topology": "t"}, FULL_EFFORT)
        by_agent = {r["agent"]: r for r in plan["reviewers"]}
        # full effort: the verdict-deciding readings take the heavy tier
        self.assertEqual(by_agent["correctness-proof-reviewer"]["model"], "l")
        self.assertEqual(by_agent["domain-rules-reviewer"]["model"], "l")
        # comparative readings stay standard
        self.assertEqual(by_agent["scope-coherence-reviewer"]["model"], "m")
        self.assertEqual(plan["models"], {"light": "s", "standard": "m", "heavy": "l"})

    def test_without_a_map_the_plan_carries_no_name(self):
        self.configure()
        plan = activation_plan({"profile": "x"}, {}, _classes("A5"),
                               {"topology": "t"}, FULL_EFFORT)
        by_agent = {r["agent"]: r for r in plan["reviewers"]}
        self.assertIsNone(by_agent["correctness-proof-reviewer"]["model"])
        self.assertEqual(by_agent["correctness-proof-reviewer"]["tier"], "heavy")
        self.assertEqual(plan["models"], {})

    def test_a_profile_override_wins_and_a_literal_name_passes_through(self):
        self.configure("models:\n  heavy: l\n")
        plan = activation_plan({"profile": "x",
                                "models": {"correctness-proof-reviewer": "my-exact-model",
                                           "domain-rules-reviewer": "heavy"}},
                               {}, _classes("A5"), {"topology": "t"}, FULL_EFFORT)
        by_agent = {r["agent"]: r for r in plan["reviewers"]}
        self.assertEqual(by_agent["correctness-proof-reviewer"]["model"], "my-exact-model")
        self.assertEqual(by_agent["domain-rules-reviewer"]["model"], "l")


if __name__ == "__main__":
    unittest.main()
