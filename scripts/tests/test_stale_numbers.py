"""Prose a change adds that names a number only a run or the history knows.

Replay of 2026-11-18: a delivery's tech plan said "8 tests" where 6 stood and "711 of
711" where 713 ran; an item comment said 709. Each was true once and stale one commit
later, and a review spent a finding on them. The prepare now names, in the report's
warnings, every added prose line that cites a commit the reviewed range made, or a run N/N.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import review


def patch(file, *added, context=()):
    body = [f" {c}" for c in context] + [f"+{a}" for a in added]
    return "\n".join([f"diff --git a/{file} b/{file}", f"--- a/{file}", f"+++ b/{file}",
                      f"@@ -1,{len(context)} +1,{len(body)} @@", *body]) + "\n"


class TestStaleNumbers(unittest.TestCase):
    sha = "741e634a9b0c1d2e3f405162738495a6b7c8d9e0"
    base = "4c2ddffee0b1c2d3e4f5061728394a5b6c7d8e9f"
    repo = [sha]

    def test_a_commit_of_the_repository_cited_in_a_spec_is_named(self):
        found = review.stale_numbers(patch("specs/x/tech-plan.md", f"delivered in {self.sha[:9]}"),
                                     self.repo)
        self.assertEqual(found, [{"file": "specs/x/tech-plan.md", "line": 1, "kind": "commit",
                                  "text": self.sha[:9]}])

    def test_a_run_count_is_named_with_its_line(self):
        found = review.stale_numbers(
            patch("plan.md", "suite: 713/713 green", context=("# Plan",)), self.repo)
        self.assertEqual(found, [{"file": "plan.md", "line": 2, "kind": "run_count",
                                  "text": "713/713"}])

    def test_a_run_count_in_words_is_named(self):
        found = review.stale_numbers(patch("plan.md", "Whole suite: 711 of 711 run"), self.repo)
        self.assertEqual([f["text"] for f in found], ["711 of 711"])

    def test_the_base_a_spec_was_cut_from_stays(self):
        """A fixed fact: the base does not move when the branch does."""
        self.assertEqual(review.stale_numbers(patch("a.md", f"cut from {self.base[:9]}"),
                                              self.repo), [])

    def test_a_hex_word_that_is_no_commit_stays(self):
        self.assertEqual(review.stale_numbers(patch("a.md", "price id c267f920 and deadbeefcafe"),
                                              self.repo), [])

    def test_a_date_and_an_uneven_fraction_stay(self):
        self.assertEqual(review.stale_numbers(
            patch("a.md", "on 11/11/2026, 14 of 16 criteria, ratio 3/4, 15/16"), self.repo), [])

    def test_code_is_not_prose(self):
        self.assertEqual(review.stale_numbers(
            patch("src/a.py", f"# see {self.sha[:9]}, 10/10"), self.repo), [])

    def test_a_removed_line_is_not_cited(self):
        text = "\n".join(["--- a/a.md", "+++ b/a.md", "@@ -1,1 +1,1 @@",
                          f"-done in {self.sha[:9]} with 10/10", "+done"]) + "\n"
        self.assertEqual(review.stale_numbers(text, self.repo), [])

    def test_an_empty_patch_names_nothing(self):
        self.assertEqual(review.stale_numbers("", self.repo), [])


if __name__ == "__main__":
    unittest.main()
