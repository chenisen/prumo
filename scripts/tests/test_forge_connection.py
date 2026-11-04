"""How the proposal's description gets in — the three routes, and the one invariant.

A connection is how the HARNESS fetches that text. It is never how the method gets it:
no script in this repository reaches a forge API, so the text always arrives as a file.
Three things are held here, and all three were once wrong in the same seam.
"""
import argparse
import dataclasses
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from providers import FORGE_CONNECTIONS, test_config
from preflight import PROPOSAL_REMEDY, reading_impediments
from review import INLINE_FILES, copy_mandatory_reading


def missing_description(cfg):
    """The one impediment this module is about, as the gate words it."""
    imp = reading_impediments(
        {"origin": "mr", "item": True, "proposal_description": False}, cfg)
    assert len(imp) == 1, imp
    return imp[0]


class TestTheRemedyNamesTheDeclaredRoute(unittest.TestCase):
    """A stop that names the wrong way out costs more than the stop itself.

    Printing the token remedy to someone whose `prumo.yml` says `connection: mcp` sends
    them hunting for a personal access token they do not need, next to a connector that
    already answers."""

    def test_every_connection_has_a_remedy_of_its_own(self):
        """No connection may fall through to another one's instructions."""
        self.assertEqual(set(PROPOSAL_REMEDY), set(FORGE_CONNECTIONS))

    def test_mcp_is_told_about_the_connector_and_not_about_tokens(self):
        cfg = test_config(forge={"type": "gitlab", "connection": "mcp"})
        remedy = missing_description(cfg)
        self.assertIn("connector", remedy)
        self.assertNotIn("GITLAB_TOKEN", remedy)

    def test_token_is_told_which_variables_this_forge_uses(self):
        cfg = test_config(forge={"type": "github", "connection": "token"})
        remedy = missing_description(cfg)
        self.assertIn("GITHUB_TOKEN", remedy)

    def test_none_is_told_to_paste_and_how_to_stop_pasting(self):
        cfg = test_config(forge={"type": "gitlab", "connection": "none"})
        remedy = missing_description(cfg)
        self.assertIn("--proposal-file", remedy)
        self.assertIn("forge-connection.md", remedy)

    def test_every_route_still_ends_at_a_file(self):
        """The routes differ in how the harness FETCHES. What it then does is the same:
        no script here reaches an API, so the text is handed over as a file."""
        for connection in FORGE_CONNECTIONS:
            with self.subTest(connection=connection):
                cfg = test_config(forge={"type": "gitlab", "connection": connection})
                self.assertIn("--proposal-file", missing_description(cfg))

    def test_an_unknown_connection_still_gets_instructions(self):
        """`providers.py` refuses one at load time, so this is only reachable through a
        hand-built config — and even there the gate must not raise instead of saying
        what to do."""
        cfg = test_config(forge={"type": "gitlab"})
        broken = dataclasses.replace(
            cfg, forge=dataclasses.replace(cfg.forge, connection="carrier-pigeon"))
        self.assertIn("--proposal-file", missing_description(broken))


class TestATokenDoesNotCountAsAReading(unittest.TestCase):
    """The gate opens when the TEXT ARRIVES, never when a variable is set.

    It used to flip on `GITLAB_TOKEN` or `GLAB_TOKEN` being present — two bugs at once.
    The names were literals, so the same environment did nothing on a GitHub
    installation; and nothing fetched anything, so `proposal.md` was never written and
    the review ran with the proposal's story missing from the one judgement that
    compares three stories."""

    def args(self, **over):
        base = {"proposal": 128, "branch": None, "item_file": None,
                "proposal_file": None}
        return argparse.Namespace(**{**base, **over})

    def test_a_forge_token_in_the_environment_does_not_open_the_gate(self):
        for name in ("GITLAB_TOKEN", "GLAB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
            with self.subTest(variable=name), tempfile.TemporaryDirectory() as d:
                with mock.patch.dict("os.environ", {name: "glpat-ABCdef123456789"}):
                    read = copy_mandatory_reading(self.args(), pathlib.Path(d))
                self.assertFalse(read["proposal_description"])
                self.assertFalse((pathlib.Path(d) / "proposal.md").exists())

    def test_the_file_is_what_opens_it(self):
        with tempfile.TemporaryDirectory() as d:
            source = pathlib.Path(d) / "description.md"
            source.write_text("Implements the item. Adds the missing tests.",
                              encoding="utf-8")
            package = pathlib.Path(d) / "package"
            package.mkdir()
            read = copy_mandatory_reading(
                self.args(proposal_file=str(source)), package)
            self.assertTrue(read["proposal_description"])
            self.assertTrue((package / "proposal.md").exists())


class TestTheDescriptionReachesAReviewer(unittest.TestCase):
    """Collected and discarded is worse than never collected: the gate charges for a
    reading that then reaches nobody.

    `proposal.md` was demanded at the gate, copied into the package, and left there.
    `scope-coherence` is asked whether item, proposal and code tell the SAME story, and
    it was being handed two of the three."""

    WORKFLOW = (pathlib.Path(__file__).resolve().parent.parent.parent
                / "workflows" / "task-review.js")

    def test_it_travels_in_the_inline_payload(self):
        self.assertIn("proposal.md", INLINE_FILES)

    def test_the_workflow_embeds_it_in_every_reviewer_context(self):
        text = self.WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("embed('proposal.md'", text)

    def test_it_sits_beside_the_item_it_is_compared_against(self):
        """Both are per-review text read by the same question. Separating them in the
        prompt buys nothing and invites one of them being dropped again."""
        self.assertEqual(INLINE_FILES.index("proposal.md"),
                         INLINE_FILES.index("item.md") + 1)


class TestTheWarningSeparatesReadingFromPublishing(unittest.TestCase):
    """One warning used to speak for both capabilities. With a connector the reading is
    fine and only the WRITE is manual — saying "no token" flat told the reader their
    reading was broken when it was not."""

    def warnings_for(self, connection):
        """With the forge's tokens taken OUT of the environment, so the result does not
        depend on whose shell runs the suite."""
        from preflight import preflight
        cfg = test_config(forge={"type": "gitlab", "connection": connection})
        without_tokens = {name: "" for name in cfg.forge.tokens}
        with mock.patch("preflight.common_cfg", return_value=cfg), \
             mock.patch.dict("os.environ", without_tokens):
            return preflight("whatever", origin="mr", item_read=True,
                             proposal_description_read=True)["warnings"]

    def test_with_a_connector_only_publishing_is_called_manual(self):
        texts = " ".join(self.warnings_for("mcp"))
        self.assertIn("only publishing stays manual", texts)

    def test_without_one_the_blunt_warning_stands(self):
        texts = " ".join(self.warnings_for("none"))
        self.assertIn("publishing stays manual", texts)
        self.assertNotIn("the connector READS", texts)


class TestTheDeclaredConnectionIsRecorded(unittest.TestCase):
    """Declared beside measured: the two disagreeing is itself information. A
    `connection: token` with nothing in the shell is a configuration that has drifted
    away from the machine it runs on."""

    def test_capabilities_carries_it(self):
        from preflight import preflight
        cfg = test_config(forge={"type": "github", "connection": "mcp"})
        with mock.patch("preflight.common_cfg", return_value=cfg):
            cap = preflight("whatever", origin="branch", item_read=True)
        self.assertEqual(cap["forge_connection"], "mcp")


if __name__ == "__main__":
    unittest.main()
