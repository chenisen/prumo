"""Whoever signs a human act comes from the ENVIRONMENT, never from the versioned file.

A profile is shared by everyone who uses the workspace. Writing a name into it would make
every review by everybody come out signed by the first person who confirmed the profile —
which is exactly the ownerless confidence the method exists to refuse.
"""
import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from common import (OWNER_TOKEN, resolve_owner, owner,
                    unresolved_tokens)
from i18n import t
from validate_profile import validate
# Import tolerant of both discovery modes: `discover -s tests` imports the modules as
# top level, `-t .` imports them as a package. Without this the suite passes in one mode
# and breaks in the other — which is how 41 tests went missing without a sound.
try:
    from tests.test_validate_profile import minimal_profile
except ImportError:
    from test_validate_profile import minimal_profile


def without_environment():
    return mock.patch.dict(os.environ, {"TASK_REVIEW_RESPONSAVEL": ""}, clear=False)


def with_environment(who="Jane Doe"):
    return mock.patch.dict(os.environ, {"TASK_REVIEW_RESPONSAVEL": who}, clear=False)


class TestReadingTheEnvironment(unittest.TestCase):
    def test_with_no_variable_there_is_no_owner(self):
        with without_environment():
            self.assertIsNone(owner())

    def test_whitespace_alone_is_not_a_name(self):
        with with_environment("   "):
            self.assertIsNone(owner())

    def test_the_name_comes_trimmed(self):
        with with_environment("  Jane Doe  "):
            self.assertEqual(owner(), "Jane Doe")


class TestSubstitution(unittest.TestCase):
    def test_substitution_reaches_every_depth(self):
        profile = {"confirmed_by": OWNER_TOKEN,
                   "anchors": {"B5": {"not_applicable": {"approver": OWNER_TOKEN}}},
                   "list": [{"approver": OWNER_TOKEN}]}
        with with_environment():
            r = resolve_owner(profile)
        self.assertEqual(r["confirmed_by"], "Jane Doe")
        self.assertEqual(r["anchors"]["B5"]["not_applicable"]["approver"], "Jane Doe")
        self.assertEqual(r["list"][0]["approver"], "Jane Doe")

    def test_with_no_name_the_token_STAYS(self):
        """Leaving the token is deliberate: absence has to stay visible, not turn into an
        empty string."""
        profile = {"confirmed_by": OWNER_TOKEN}
        with without_environment():
            r = resolve_owner(profile)
        self.assertEqual(r["confirmed_by"], OWNER_TOKEN)
        self.assertTrue(unresolved_tokens(r))

    def test_it_leaves_alone_what_carries_no_token(self):
        profile = {"profile": "repo-x", "version": 2, "active": True, "nothing": None}
        with with_environment():
            self.assertEqual(resolve_owner(profile), profile)

    def test_unresolved_tokens_walks_lists_and_dictionaries(self):
        self.assertFalse(unresolved_tokens({"a": ["b", {"c": 1}]}))
        self.assertTrue(unresolved_tokens({"a": ["b", {"c": OWNER_TOKEN}]}))


class TestValidation(unittest.TestCase):
    def test_a_token_with_no_environment_warns_and_does_not_fail(self):
        p = minimal_profile(status="confirmed", confirmed_by=OWNER_TOKEN,
                            confirmed_at="2026-10-29")
        with without_environment():
            errors, warnings = validate(p)
        self.assertEqual(errors, [])
        self.assertIn(t("validate.owner_unresolved", token=OWNER_TOKEN), warnings)

    def test_with_the_environment_set_it_does_not_warn(self):
        p = minimal_profile(status="confirmed", confirmed_by=OWNER_TOKEN,
                            confirmed_at="2026-10-29")
        with with_environment():
            _, warnings = validate(p)
        self.assertNotIn(t("validate.owner_unresolved", token=OWNER_TOKEN), warnings)


if __name__ == "__main__":
    unittest.main()
