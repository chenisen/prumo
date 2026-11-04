"""What the reviews learn about a repository, accrued outside it.

Two things these tests exist to hold, and both of them are the kind that fail silently:

  * **no delivery identifier reaches the reading.** The file is read by EVERY later
    review, so an id that is legitimately this review's own today is foreign tomorrow —
    which is the contamination `provenance.py` was written for, moved one review into
    the future.
  * **one sighting is not a fact.** A single reviewer's guess printed as "what this
    repository does" would be indistinguishable from a corroborated convention, and the
    next reviewer would read it as settled.
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import knowledge
from provenance import foreign_references

MANIFEST = {
    "repo": "repo-x",
    "origin": "refs/merge-requests/821/head",
    "base": {"sha": "aaaa1111bbbb2222cccc3333dddd4444eeee5555"},
    "merge_base": "bbbb2222cccc3333dddd4444eeee5555ffff6666",
    "head_sha": "cccc3333dddd4444eeee5555ffff6666aaaa7777",
    "item": {"id": "12346"},
}


def package(**files):
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    for name, payload in files.items():
        (d / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    return d


def convention(statement, file="app/Rules/Guard.php"):
    return {"statement": statement, "evidence": [{"file": file}]}


class TestSanitising(unittest.TestCase):

    def test_this_reviews_own_identifiers_are_redacted_too(self):
        """`provenance.check` lets its own through, because it asks "is this somebody
        else's review?". Here the question is "will this still be true next month?", and
        a sha never is. Letting its own through is how the file becomes a sibling report
        for the review that reads it next."""
        text = (f"In {MANIFEST['head_sha']} the guard moved; item {MANIFEST['item']['id']} "
                "asked for it, see merge request 821.")
        clean, redacted = knowledge.sanitise(text, MANIFEST)
        self.assertNotIn(MANIFEST["head_sha"], clean)
        self.assertNotIn("12346", clean)
        self.assertEqual(redacted, 3)
        self.assertIn("<sha>", clean)

    def test_an_id_at_the_end_of_a_sentence_is_still_an_id(self):
        """The most ordinary way a sentence ends, and the shape `provenance.py` declines
        to see: its `(?![\\w.])` guard — there to keep `1.821.3` whole — also refuses the
        full stop. A redactor with that blind spot publishes the id it was written for."""
        clean, redacted = knowledge.sanitise(
            "The listing was already unscoped before item 12346.", MANIFEST)
        self.assertNotIn("12346", clean)
        self.assertEqual(redacted, 1)

    def test_a_version_number_is_not_an_identifier(self):
        """And the guard must not swing the other way: redacting the middle of a version
        would corrupt the very conventions this file exists to carry."""
        clean, redacted = knowledge.sanitise("Pinned at 1.821.3 by the manifest", MANIFEST)
        self.assertIn("1.821.3", clean)
        self.assertEqual(redacted, 0)

    def test_redacting_keeps_the_knowledge_instead_of_dropping_it(self):
        """The sentence stays useful once the sha is gone. Dropping the entry would lose
        a real convention to protect against a risk redaction already removed."""
        clean, _ = knowledge.sanitise(
            f"Guards are returned from the query builder ({MANIFEST['merge_base']})",
            MANIFEST)
        self.assertIn("Guards are returned from the query builder", clean)


class TestAccrual(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def accrue(self, pkg, conventions):
        path = pkg / "conv.json"
        path.write_text(json.dumps(conventions), encoding="utf-8")
        return knowledge.accrue(pkg, str(path), self.out)

    def reading(self):
        return (self.out / "repo-x.md").read_text(encoding="utf-8")

    def test_an_observation_seen_once_is_held_back(self):
        self.accrue(package(), [convention("Guards return from the query builder")])
        self.assertNotIn("Guards return from the query builder", self.reading())
        self.assertIn("one review", self.reading())

    def test_the_same_observation_from_a_second_review_is_published(self):
        """Corroboration is the whole difference between a pattern and a guess."""
        self.accrue(package(), [convention("Guards return from the query builder")])
        self.accrue(package(), [convention("Guards  return from the  QUERY builder")])
        reading = self.reading()
        self.assertIn("Guards return from the query builder", reading)
        self.assertIn("`app/Rules/Guard.php`", reading)

    def test_the_second_sighting_does_not_create_a_second_entry(self):
        self.accrue(package(), [convention("Guards return from the query builder")])
        result = self.accrue(package(), [convention("Guards return from the query builder")])
        self.assertEqual(result["entries"], 1)
        self.assertEqual(result["reviews"], 2)

    def test_nothing_is_deleted_because_a_review_did_not_see_it(self):
        """A delivery that touches another corner of the repository says nothing about a
        convention it never went near. Staleness is read from `last_seen`, not from
        absence."""
        self.accrue(package(), [convention("Guards return from the query builder")])
        self.accrue(package(), [convention("Guards return from the query builder")])
        self.accrue(package(), [convention("Tests are named after the rule")])
        self.assertIn("Guards return from the query builder", self.reading())

    def test_no_identifier_of_any_review_survives_into_the_reading(self):
        """The guarantee, stated as the check that would catch its absence."""
        claim = (f"Since {MANIFEST['base']['sha']} the listing is unscoped; "
                 f"item {MANIFEST['item']['id']} did not cause it")
        pkg = package(observations=[
            {"claim": claim, "severity": "required", "status": "confirmed",
             "evidence": [{"file": "app/Repo.php"}]}])
        self.accrue(pkg, [])
        self.accrue(pkg, [])
        reading = self.reading()
        self.assertIn("the listing is unscoped", reading)
        self.assertEqual(foreign_references(reading, pkg), [])
        for identifier in (MANIFEST["base"]["sha"], MANIFEST["head_sha"], "12346", "821"):
            self.assertNotIn(identifier, reading)

    def test_a_refuted_pre_existing_finding_never_becomes_knowledge(self):
        """The verifier knocked it down, so it was never a fact about the repository."""
        pkg = package(observations=[
            {"claim": "The listing is unscoped", "status": "refuted",
             "evidence": [{"file": "app/Repo.php"}]}])
        self.accrue(pkg, [])
        self.accrue(pkg, [])
        self.assertNotIn("The listing is unscoped", self.reading())

    def test_the_repositorys_declared_debt_carries_its_owner(self):
        """It is the one thing in a report that is not the delivery's and still matters:
        today it is reprinted every review and dies with the report."""
        pkg = package(verdict={"verdict": "APPROVED", "project_pending": [
            {"reason": "nine classes have no anchor in the profile",
             "owner": "whoever maintains the repository"}]})
        self.accrue(pkg, [])
        self.accrue(pkg, [])
        reading = self.reading()
        self.assertIn("nine classes have no anchor", reading)
        self.assertIn("owner: whoever maintains the repository", reading)

    def test_the_reading_says_it_decides_nothing(self):
        """A machine-accrued fact that reads as an authority is how the fifth gate starts
        certifying itself."""
        self.accrue(package(), [convention("Guards return from the query builder")])
        self.assertIn("nothing here decides a verdict", self.reading())


if __name__ == "__main__":
    unittest.main(verbosity=2)
