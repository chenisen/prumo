import pathlib, sys, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from render import escape, safe_html


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
