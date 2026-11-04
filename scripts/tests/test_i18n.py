"""Catalogues: a missing key is a defect, and a literal in code is invisible.

Two properties, and the second is the one that rots quietly. A string hard-coded in a
`print` looks fine to whoever wrote it and is unreachable to everyone who set
`language: pt-BR` — it is the same bug as hard-coding a provider, with the same shape:
it works for the author and lies to the rest.
"""
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import i18n
from providers import test_config

RAIZ = pathlib.Path(__file__).resolve().parent.parent.parent

# Placeholders are DERIVED from each message, not listed here. A fixed dictionary falls
# behind the moment someone adds a key, and then the guard fails for the wrong reason —
# it stops saying "this message is broken" and starts saying "this test is stale".
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def campos_de(texto: str) -> dict:
    return {nome: f"<{nome}>" for nome in PLACEHOLDER.findall(texto)}


class TestCatalogues(unittest.TestCase):
    def test_every_catalogue_has_the_same_keys(self):
        """Falling back to another language would make a half-translated release look
        finished, and the reader could not tell."""
        base = i18n.catalogue(i18n.DEFAULT)
        for lang in i18n.available():
            with self.subTest(language=lang):
                outro = i18n.catalogue(lang)
                self.assertEqual(sorted(base), sorted(outro),
                                 f"`{lang}` does not match `{i18n.DEFAULT}` key for key")

    def test_every_message_renders_in_every_language(self):
        """A placeholder that never arrives prints as literal braces in a report."""
        for lang in i18n.available():
            for chave in i18n.catalogue(lang):
                with self.subTest(language=lang, key=chave):
                    bruto = i18n.catalogue(lang)[chave]
                    texto = i18n.t(chave, language=lang, **campos_de(str(bruto)))
                    # Against the PLACEHOLDER pattern, not a bare `{`: a message may
                    # carry literal braces on purpose (`{{a, b}}` renders as `{a, b}`),
                    # and a bare check calls that a defect.
                    self.assertEqual(PLACEHOLDER.findall(texto), [],
                                     "unfilled placeholder")

    def test_unknown_language_is_refused_and_names_the_known_ones(self):
        with self.assertRaises(i18n.UnknownLanguage) as ctx:
            i18n.catalogue("klingon")
        for conhecido in i18n.available():
            self.assertIn(conhecido, str(ctx.exception))

    def test_missing_key_raises_instead_of_degrading(self):
        with self.assertRaises(i18n.MissingMessage):
            i18n.t("nao.existe", language="en")

    def test_the_default_is_english(self):
        """A public clone that nobody configured answers in English."""
        self.assertEqual(i18n.DEFAULT, "en")
        self.assertEqual(test_config().language, "en")

    def test_the_language_is_configurable(self):
        self.assertEqual(test_config(language="pt-BR").language, "pt-BR")

    def test_an_unknown_language_in_the_config_is_refused(self):
        from providers import InvalidConfiguration
        with self.assertRaises(InvalidConfiguration):
            test_config(language="klingon")


class TestNoLiteralsInCode(unittest.TestCase):
    """The guard for the regression this module exists to prevent."""

    # Files that legitimately carry prose: the catalogue resolver and this test.
    #
    # `setup.py` joins them for a reason worth writing down: it runs BEFORE a language
    # is chosen. Its output cannot be in the configured language, because there is no
    # configuration yet — it is the thing that writes one. And its language picker shows
    # each option as an ENDONYM, the name a language gives itself ("português do
    # Brasil"), which is how a person recognises their own language in a list. Routing
    # that through the catalogue would print every option in the language they have not
    # chosen yet, which is exactly backwards.
    ISENTOS = {"i18n.py", "test_i18n.py", "setup.py"}

    # The ratchet. The rewrite into English externalises one file at a time, and a test
    # that goes red for the whole journey teaches people to ignore it. So the files not
    # yet converted are listed HERE, explicitly, and the test asserts two things: nothing
    # outside the list carries a literal, and the list never grows.
    #
    # Delete a name from this list when you externalise that file. The day the list is
    # empty, delete the list.
    # Empty: every production module has been externalised. The set and the test below
    # stay, so the next person who adds a literal has somewhere it will be caught
    # instead of somewhere to hide it.
    POR_CONVERTER: set[str] = set()

    def test_no_accented_prose_left_in_a_user_facing_string(self):
        """A crude but decisive signal: Portuguese text in a Python string literal, in a
        codebase whose language is English, is almost certainly an un-externalised
        message. Comments and docstrings are exempt — they are for the reader of the
        code, not for the user."""
        import io
        import token as _t
        import tokenize
        acentos = re.compile(r"[áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ]")
        falhas = []
        # Only production modules. A fixture's prose is for whoever reads the test,
        # not for the user — the suite gets translated in its own pass.
        for py in sorted((RAIZ / "scripts").glob("*.py")):
            if py.name in self.ISENTOS or "__pycache__" in str(py):
                continue
            if py.name in self.POR_CONVERTER:
                continue
            fonte = py.read_text(encoding="utf-8")
            anterior = None
            for tok in tokenize.generate_tokens(io.StringIO(fonte).readline):
                if tok.type == _t.STRING and acentos.search(tok.string):
                    # docstring: a STRING alone on a logical line, right after INDENT,
                    # NEWLINE or the start of a block. Those are prose for the reader.
                    if anterior is None or anterior.type in (_t.INDENT, _t.NEWLINE, _t.NL,
                                                             _t.DEDENT, _t.ENCODING):
                        continue
                    falhas.append(f"{py.relative_to(RAIZ)}:{tok.start[0]}")
                if tok.type not in (_t.COMMENT,):
                    anterior = tok
        self.assertEqual(falhas, [],
                         "Portuguese literal outside a docstring — externalise it into "
                         "`messages/`:\n  " + "\n  ".join(falhas))

    def test_the_ratchet_only_turns_one_way(self):
        """A file listed as pending that no longer carries a literal must leave the list.
        Otherwise the list becomes decoration and stops meaning anything."""
        import io
        import token as _t
        import tokenize
        acentos = re.compile(r"[áàâãéêíóôõúçÁÀÂÃÉÊÍÓÔÕÚÇ]")
        limpos = []
        for nome in sorted(self.POR_CONVERTER):
            py = RAIZ / "scripts" / nome
            if not py.exists():
                continue
            fonte = py.read_text(encoding="utf-8")
            anterior, tem = None, False
            for tok in tokenize.generate_tokens(io.StringIO(fonte).readline):
                if tok.type == _t.STRING and acentos.search(tok.string):
                    if anterior is not None and anterior.type not in (
                            _t.INDENT, _t.NEWLINE, _t.NL, _t.DEDENT, _t.ENCODING):
                        tem = True
                        break
                if tok.type not in (_t.COMMENT,):
                    anterior = tok
            if not tem:
                limpos.append(nome)
        self.assertEqual(limpos, [],
                         "already externalised — remove from POR_CONVERTER:\n  "
                         + "\n  ".join(limpos))


if __name__ == "__main__":
    unittest.main(verbosity=2)
