"""A review relates only to its own item and its own proposal.

Found on 2026-11-04. The agents hold `Read`, `Grep` and `Glob` over the whole workspace,
and the reports root lives there: asked for a manual script per class, a planner anchored
on the report of a SIBLING review sitting in the same repository and wrote fifteen scripts
for the previous item's SHAs. Plausible line by line, and about the wrong delivery.

The first attempt was a sentence in the prompt asking the agent not to. This file exists
because that is not a guarantee — the same reason escaping is done in code.
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from provenance import foreign_references, check

OWN = {
    "repo": "repo-x",
    "origin": "refs/merge-requests/821/head",
    "base": {"sha": "f6d9e1047e20ad0b89030511f54f2ff42cdb69d9"},
    "merge_base": "23cc14f1350ef45c0932a4cead3b42a7f42e4770",
    "head_sha": "cbf771e0ef693a44e8b1e07ec923128a2a52f381",
    "item": {"id": "50275"},
}

ITEM = ("# Card 50275\n\nSegundo de 2 cards; segue o padrão de testes definido no "
        "card 50273.\n")


def package(manifest=None, item=ITEM):
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "manifest.json").write_text(json.dumps(manifest or OWN), encoding="utf-8")
    if item is not None:
        (d / "item.md").write_text(item, encoding="utf-8")
    return d


class TestItSpeaksOnlyAboutThisReview(unittest.TestCase):
    def test_its_own_shas_are_not_foreign(self):
        text = ("rodada contra o diff 23cc14f1350e..cbf771e0ef69, base "
                "f6d9e1047e20ad0b89030511f54f2ff42cdb69d9")
        self.assertEqual([], foreign_references(text, package()))

    def test_a_sha_from_another_delivery_is_caught(self):
        """The real signal: the contaminated run carried f6d9e104..0f4642d6, the SHAs of
        the neighbouring review."""
        found = foreign_references("git diff f6d9e1047e20..0f4642d600e4", package())
        self.assertEqual([{"kind": "sha", "value": "0f4642d600e4"}], found)

    def test_another_proposal_number_is_caught_in_this_forges_ref_shape(self):
        found = foreign_references("refs/merge-requests/820/head", package())
        self.assertIn({"kind": "proposal", "value": "820"}, found)

    def test_the_forges_ref_shape_comes_from_the_origin_not_from_a_guess(self):
        """Another forge publishes proposals under another ref path. The shape is read
        from the origin the resolver wrote, so the check follows whatever this
        installation declared instead of a path this method hard-codes."""
        other_shape = dict(OWN, origin="refs/some-other-shape/821/from")
        found = foreign_references("refs/some-other-shape/820/from",
                                   package(other_shape))
        self.assertIn({"kind": "proposal", "value": "820"}, found)

    def test_an_item_the_ITEM_ITSELF_names_is_not_foreign(self):
        """The asymmetry that keeps this from crying wolf: card 50275 really does say
        'segue o padrão do card 50273', and a finding may quote it."""
        found = foreign_references("segue o padrão de testes do card 50273", package())
        self.assertEqual([], found)

    def test_an_item_that_appears_from_nowhere_is_caught(self):
        found = foreign_references("revisão do card 49945", package())
        self.assertEqual([{"kind": "item", "value": "49945"}], found)

    def test_a_plain_number_is_not_a_sha(self):
        """`188523` is a pipeline id and `42` a count. Hex with no letter is a number."""
        self.assertEqual([], foreign_references(
            "pipeline 188523, 67 testes, 135 asserções, 42/43 linhas", package()))

    def test_short_hex_is_not_a_sha(self):
        """Below eight characters it collides with ordinary hex in code."""
        self.assertEqual([], foreign_references("mask 0xff, colour #a1b2c3", package()))


class TestTheFileLevelCheck(unittest.TestCase):
    def test_a_clean_file_passes(self):
        d = package()
        f = d / "scripts_manual.json"
        f.write_text(json.dumps([{"goal": "diff 23cc14f1350e..cbf771e0ef69"}]),
                     encoding="utf-8")
        self.assertEqual([], check(d, [f]))

    def test_a_contaminated_file_is_named_with_what_gave_it_away(self):
        d = package()
        f = d / "scripts_manual.json"
        f.write_text(json.dumps([{"goal": "diff f6d9e1047e20..0f4642d600e4"}]),
                     encoding="utf-8")
        problems = check(d, [f])
        self.assertEqual(1, len(problems))
        self.assertIn("scripts_manual.json", problems[0])
        self.assertIn("0f4642d600e4", problems[0])

    def test_a_file_that_is_not_there_is_not_a_problem(self):
        d = package()
        self.assertEqual([], check(d, [d / "absent.json"]))


if __name__ == "__main__":
    unittest.main()
