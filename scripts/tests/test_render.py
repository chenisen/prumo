import pathlib, sys, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from render import (class_question, escape, gate_of, meanings_for, named,
                    routes_header, routes_table, safe_html)


class TestEscape(unittest.TestCase):
    """Replay of the incident: an item comment lost its formatting because a marker went
    raw into the HTML (item 10001). Escaping is done IN CODE, never by instructing the
    model."""

    def test_markers_are_escaped(self):
        self.assertEqual(escape("<route>"), "&lt;route&gt;")

    def test_item_text_carrying_html_does_not_break_the_comment(self):
        raw = "the <id> field vanished from <ProductResource>"
        output = escape(raw)
        self.assertNotIn("<id>", output)
        self.assertNotIn("<ProductResource>", output)

    def test_an_injected_script_is_neutralised(self):
        self.assertNotIn("<script>", escape("<script>alert(1)</script>"))

    def test_quotation_marks_survive(self):
        # quote=False: quotation marks in running prose must not become &quot; for nothing
        self.assertEqual(escape('the "final" price'), 'the "final" price')

    def test_accented_characters_survive(self):
        self.assertEqual(escape("decisão, verificação"), "decisão, verificação")

    def test_none_becomes_the_empty_string(self):
        self.assertEqual(escape(None), "")


class TestHtmlSubset(unittest.TestCase):
    def test_allowed_tags_survive(self):
        self.assertIn("<strong>", safe_html("<p><strong>x</strong></p>"))

    def test_a_tag_outside_the_subset_is_escaped(self):
        output = safe_html("<table><tr><td>x</td></tr></table>")
        self.assertNotIn("<table>", output)
        self.assertIn("&lt;table&gt;", output)

    def test_a_mixture_keeps_only_what_the_subset_allows(self):
        output = safe_html("<p>field <id> in <code>x</code></p>")
        self.assertIn("<p>", output)
        self.assertIn("<code>", output)
        self.assertIn("&lt;id&gt;", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestNoIdTravelsAlone(unittest.TestCase):
    """`A8`, `AC-3`, `SEC-01` are the harness talking to itself. The item comment has
    resolved them since 2026-11-04; the REPORT did not, and the report is what a
    reviewer opens. Asked for on 2026-11-04, over a verdict held by a single `A8` that
    said nothing about what it asked."""

    GATES = [{"number": 1, "name": "standards", "condition": "standards", "passed": True,
              "items": []},
             {"number": 2, "name": "business_rules", "condition": "business rules",
              "passed": False, "items": ["A8"]}]

    def meaning(self, criteria=(), findings=()):
        return meanings_for([], list(criteria), list(findings))

    def test_a_class_id_carries_the_question_it_asks(self):
        out = named(["A8"], self.meaning())
        self.assertIn("`A8`", out)
        self.assertIn(class_question("A8"), out)

    def test_a_criterion_id_carries_the_criterion(self):
        out = named(["AC-3"], self.meaning(
            criteria=[{"id": "AC-3", "text": "no production code is changed"}]))
        self.assertIn("no production code is changed", out)

    def test_a_finding_id_carries_its_claim(self):
        out = named(["SEC-01"], self.meaning(
            findings=[{"id": "SEC-01", "claim": "masking is case sensitive"}]))
        self.assertIn("masking is case sensitive", out)

    def test_a_long_claim_is_cut_rather_than_wrapping_the_whole_table(self):
        out = named(["SEC-01"], self.meaning(
            findings=[{"id": "SEC-01", "claim": "x" * 400}]))
        self.assertLess(len(out), 140)
        self.assertIn("…", out)

    def test_a_mechanical_item_is_a_sentence_and_not_a_token(self):
        self.assertNotEqual(named(["suite"], self.meaning()), "`suite`")

    def test_an_id_nothing_explains_stays_bare_instead_of_being_invented(self):
        # A repository name and an out-of-scope file path already say what they are.
        self.assertEqual(named(["bat-whatever"], self.meaning()), "`bat-whatever`")

    def test_the_separator_is_not_the_comma_a_question_already_carries(self):
        out = named(["A8", "A9"], self.meaning())
        self.assertIn(";", out)

    def test_a_closed_gate_is_found_from_the_items_that_closed_it(self):
        # The route does not carry its gate: one route serves more than one, so the gate
        # is read back from the items the judge recorded under it.
        self.assertTrue(gate_of({"gates": self.GATES}, ["A8"]))

    def test_a_gate_is_named_and_not_only_numbered(self):
        out = gate_of({"gates": self.GATES}, ["A8"])
        self.assertIn("2.", out)
        self.assertNotEqual(out.strip(), "**2.**")

    def test_a_passing_gate_claims_nothing(self):
        self.assertEqual(gate_of({"gates": self.GATES}, ["A1"]), "")


class TestRouteTableShape(unittest.TestCase):
    """A row that does not match its header renders as a broken table, not as a missing
    cell — and the project table has no gate column at all, because a project pending
    item closes no gate and an always-empty column reads as 'nobody looked'."""

    EMPTY = {"routes": [], "gates": []}

    def columns(self, line):
        return len(line.strip().strip("|").split("|"))

    def test_the_delivery_table_row_matches_its_header(self):
        header = routes_header().splitlines()[0]
        self.assertEqual(self.columns(routes_table(self.EMPTY)),
                         self.columns(header))

    def test_the_project_table_row_matches_its_header(self):
        header = routes_header(gate=False).splitlines()[0]
        self.assertEqual(self.columns(routes_table(self.EMPTY, origin="project")),
                         self.columns(header))

    def test_the_project_table_has_no_gate_column(self):
        self.assertLess(self.columns(routes_header(gate=False).splitlines()[0]),
                        self.columns(routes_header().splitlines()[0]))
