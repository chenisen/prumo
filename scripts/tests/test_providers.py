"""A configuração é pré-condição de uso, e estes testes seguram as três promessas dela.

1. **Nada é presumido.** Sem arquivo, o prumo recusa — não cai num padrão. Um padrão
   silencioso faria alguém revisar uma PR do GitHub com vocabulário de GitLab, ou
   publicar HTML num tracker que só lê Markdown. Os dois erros só aparecem depois, no
   text já publicado.
2. **O preset encurta, não decide.** Declarar `type` herda tudo; declarar um campo solto
   sobrescreve só aquele.
3. **O método não sabe o name de fornecedor nenhum.** Trocar o `prumo.yml` troca ref,
   vocabulário, token e format — without tocar uma line de código.
"""
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from providers import (MissingConfiguration, InvalidConfiguration, FORGES, TRACKERS,
                        CONTRACT_VERSION, load_config, test_config, build)

MINIMO = {
    "prumo": CONTRACT_VERSION,
    "paths": {"repositories": "/tmp/r", "profiles": "/tmp/p", "reports": "/tmp/rel"},
    "forge": {"type": "gitlab"},
    "tracker": {"type": "businessmap"},
}


def without(chave, de=None):
    d = {k: v for k, v in (de or MINIMO).items() if k != chave}
    return d


class TestNothingIsAssumed(unittest.TestCase):
    def test_no_file_is_refused(self):
        with tempfile.TemporaryDirectory() as t, \
             mock.patch.dict("os.environ", {"PRUMO_CONFIG": str(pathlib.Path(t) / "nao-existe")}):
            with self.assertRaises(MissingConfiguration):
                load_config()

    def test_the_refusal_names_the_remedy(self):
        with tempfile.TemporaryDirectory() as t, \
             mock.patch.dict("os.environ", {"PRUMO_CONFIG": str(pathlib.Path(t) / "x")}):
            try:
                load_config()
            except MissingConfiguration as e:
                self.assertIn("prumo.yml.example", str(e))
                self.assertIn("PRUMO_CONFIG", str(e))

    def test_the_contract_version_is_required(self):
        with self.assertRaises(InvalidConfiguration):
            build(without("prumo"))

    def test_a_wrong_version_is_refused(self):
        with self.assertRaises(InvalidConfiguration):
            build({**MINIMO, "prumo": 999})

    def test_every_path_is_required(self):
        for faltante in ("repositories", "profiles", "reports"):
            with self.subTest(caminho=faltante):
                dados = {**MINIMO, "paths": without(faltante, MINIMO["paths"])}
                with self.assertRaises(InvalidConfiguration):
                    build(dados)

    def test_forge_and_tracker_are_required(self):
        for secao in ("forge", "tracker"):
            with self.subTest(secao=secao):
                with self.assertRaises(InvalidConfiguration):
                    build(without(secao))


class TestPresetsShortenWithoutDeciding(unittest.TestCase):
    def test_every_known_forge_builds_from_type_alone(self):
        for type in FORGES:
            with self.subTest(forge=type):
                c = test_config(forge={"type": type})
                self.assertTrue(c.forge.proposal and c.forge.tokens)
                self.assertIn("{n}", c.forge._ref)

    def test_every_known_tracker_builds_from_type_alone(self):
        for type in TRACKERS:
            with self.subTest(tracker=type):
                c = test_config(tracker={"type": type})
                self.assertTrue(c.tracker.item and c.tracker.heading)

    def test_a_loose_field_overrides_only_itself(self):
        c = test_config(forge={"type": "github", "proposal": "Pull"})
        self.assertEqual(c.forge.proposal, "Pull")
        self.assertEqual(c.forge.prefix, "#")                 # veio do preset
        self.assertEqual(c.forge.proposal_ref(7), "refs/pull/7/head")

    def test_an_unknown_provider_is_refused_and_names_the_known_ones(self):
        with self.assertRaises(InvalidConfiguration) as ctx:
            test_config(forge={"type": "gerrit"})
        for known in FORGES:
            self.assertIn(known, str(ctx.exception))

    def test_a_ref_without_a_placeholder_is_refused(self):
        """Uma ref without `{n}` buscaria sempre a mesma proposal, em silêncio."""
        with self.assertRaises(InvalidConfiguration):
            test_config(forge={"type": "github", "ref": "refs/pull/head"})

    def test_a_comment_format_outside_the_list_is_refused(self):
        with self.assertRaises(InvalidConfiguration):
            test_config(tracker={"type": "generic", "format": "latex"})


class TestSwappingTheFileSwapsEverything(unittest.TestCase):
    """O eval que de fato importa: o método não conhece fornecedor."""

    def test_the_ref_changes_with_the_forge(self):
        self.assertEqual(test_config(forge={"type": "gitlab"}).forge.proposal_ref(820),
                         "refs/merge-requests/820/head")
        self.assertEqual(test_config(forge={"type": "github"}).forge.proposal_ref(820),
                         "refs/pull/820/head")

    def test_the_label_changes_with_the_forge(self):
        self.assertEqual(test_config(forge={"type": "gitlab"}).forge.label(820), "MR !820")
        self.assertEqual(test_config(forge={"type": "github"}).forge.label(820), "PR #820")

    def test_the_article_agrees(self):
        """`a MR` e `o PR`. Pequeno, mas vai para text que pessoas leem."""
        self.assertEqual(test_config(forge={"type": "gitlab"}).forge.with_article(), "a MR")
        self.assertEqual(test_config(forge={"type": "github"}).forge.with_article(), "o PR")

    def test_the_item_vocabulary_changes_with_the_tracker(self):
        self.assertEqual(test_config(tracker={"type": "businessmap"}).tracker.item, "card")
        self.assertEqual(test_config(tracker={"type": "jira"}).tracker.item, "issue")
        self.assertEqual(test_config(tracker={"type": "acelerato"}).tracker.item, "ticket")

    def test_the_heading_respects_the_tracker_format(self):
        self.assertIn("text-big",
                      test_config(tracker={"type": "businessmap"}).tracker.heading_for("X"))
        self.assertEqual(test_config(tracker={"type": "jira"}).tracker.heading_for("X"),
                         "h3. X")
        self.assertEqual(test_config(tracker={"type": "generic"}).tracker.heading_for("X"),
                         "### X")

    def test_the_token_read_is_the_configured_forge_one(self):
        c = test_config(forge={"type": "github"})
        with mock.patch.dict("os.environ", {"GITLAB_TOKEN": "x"}, clear=True):
            self.assertIsNone(c.forge.token_in_environment())   # token do fornecedor errado
        with mock.patch.dict("os.environ", {"GH_TOKEN": "x"}, clear=True):
            self.assertEqual(c.forge.token_in_environment(), "GH_TOKEN")


class TestPaths(unittest.TestCase):
    def test_a_relative_path_resolves_from_the_file(self):
        with tempfile.TemporaryDirectory() as t:
            base = pathlib.Path(t)
            (base / "prumo.yml").write_text(
                f"prumo: {CONTRACT_VERSION}\n"
                "paths: {repositories: ./repos, profiles: ./profiles, reports: ./rel}\n"
                "forge: {type: gitlab}\nrastreador: {type: businessmap}\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"PRUMO_CONFIG": str(base / "prumo.yml")}):
                c = load_config()
            self.assertEqual(c.paths.repositories, (base / "repos").resolve())

    def test_the_package_root_defaults_outside_the_workspace(self):
        """A package is scratch — the diff, the slices, every file the agents read. An
        installation that never asked for them should not find patches in its tree."""
        with mock.patch.dict("os.environ", {"TMPDIR": "/tmp/elsewhere"}):
            c = build(MINIMO)
        self.assertEqual(c.paths.packages, pathlib.Path("/tmp/elsewhere/task-review"))

    def test_an_installation_can_keep_its_packages(self):
        """Pointed at its own tree, the packages survive a /tmp sweep and stay readable
        after the review that produced them."""
        c = build({**MINIMO, "paths": {**MINIMO["paths"], "packages": "/tmp/r/.packages"}})
        self.assertEqual(c.paths.packages, pathlib.Path("/tmp/r/.packages"))

    def test_knowledge_defaults_beside_the_reports(self):
        c = build(MINIMO)
        self.assertEqual(c.paths.knowledge, pathlib.Path("/tmp/rel/_knowledge"))

    def test_repo_and_profile_derive_from_the_root(self):
        c = test_config()
        self.assertEqual(c.paths.repo("x").name, "x")
        self.assertEqual(c.paths.profile("x").name, "x.yml")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestNoProviderInTheMethod(unittest.TestCase):
    """O guarda-corpo da regressão que este módulo existe para impedir.

    Nome de fornecedor dentro de um script é o mesmo bug que name de repositório dentro
    da skill: funciona na instalação de quem escreveu e mente em todas as outras. O lugar
    de `gitlab`, `refs/merge-requests` e `kanbanize` é o preset ou o `prumo.yml` — nunca
    a lógica.

    Prosa é exceção declarada: um comentário pode citar o GitLab para explicar POR QUE o
    preset existe, e um docstring pode dar exemplo. O que não pode é a execução depender
    do name.
    """

    FORBIDDEN = ("gitlab", "github", "bitbucket", "kanbanize", "businessmap",
                 "jira", "acelerato", "merge-requests", "refs/pull")
    # providers.py É o lugar dos nomes. teste_agnosticismo e este módulo os citam para
    # verificá-los.
    EXEMPT = {"providers.py", "test_provedores.py", "agnosticism_check.py"}

    def test_no_provider_name_outside_the_presets(self):
        """Usa `tokenize` em vez de varrer linhas: só CÓDIGO é inspecionado.

        A primeira versão deste teste olhava line a line e pulava as que *começavam*
        com `#` ou aspas — e por isso acusava prosa dentro de docstring de várias linhas.
        Falso positivo em guarda-corpo é pior que guarda-corpo nenhum: ensina a ignorá-lo.
        """
        import io
        import re
        import token as _t
        import tokenize

        root = pathlib.Path(__file__).resolve().parent.parent
        failures = []
        for py in sorted(root.rglob("*.py")):
            if py.name in self.EXEMPT or "__pycache__" in str(py):
                continue
            fonte = py.read_text(encoding="utf-8")
            for tok in tokenize.generate_tokens(io.StringIO(fonte).readline):
                if tok.type in (_t.STRING, _t.COMMENT, _t.NL, _t.NEWLINE):
                    continue      # prosa pode explicar POR QUE o preset existe
                for name in self.FORBIDDEN:
                    if re.search(rf"\b{re.escape(name)}\b", tok.string, re.I):
                        failures.append(f"{py.relative_to(root)}:{tok.start[0]}: "
                                      f"`{name}` em código")
        self.assertEqual(failures, [],
                         "fornecedor cravado no método:\n  " + "\n  ".join(failures))


class TestVocabularyReachesPublishedText(unittest.TestCase):
    """O vocabulário tem de atravessar até o TEXTO, não parar na configuração.

    Esta classe nasceu de um setup limpo feito como um cliente novo: a configuração dizia
    `jira`/`github` e o prumo respondia corretamente em `providers.py` — mas o relatório
    saía "item", o preflight avisava sobre "token de MR", e o rodapé prometia não mover
    "a coluna do card". Agnosticismo que para antes da saída não é agnosticism: é um
    campo de configuração que ninguém lê.
    """

    TEMPLATES = ("report.md", "item-comment.html",
                 "proposal-comment-approved.md", "proposal-comment-intervention.md")

    def test_no_published_template_names_a_local_file(self):
        """A path under the reports root is local to whoever ran the review. Naming it in
        something that gets published tells the reader a report exists without letting
        them read a line of it — so what decides travels inside the comment instead.
        Added 2026-11-04, after the feedback that `report_path` was meaningless to anyone
        but the machine that produced it."""
        root = pathlib.Path(__file__).resolve().parent.parent.parent / "templates"
        published = [n for n in self.TEMPLATES if n != "report.md"]
        for name in published:
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("report_path", text, name)
            self.assertNotIn(".md`", text, name)

    def test_no_template_hard_codes_the_item_or_proposal_name(self):
        import re
        root = pathlib.Path(__file__).resolve().parent.parent.parent / "templates"
        forbidden = (r"\bcard\b", r"\bMR\b", r"\bPR\b", r"\bissue\b", r"\bticket\b")
        failures = []
        for name in self.TEMPLATES:
            text = (root / name).read_text(encoding="utf-8")
            for linha_n, line in enumerate(text.splitlines(), 1):
                # `{...}` é campo preenchido em runtime: é exatamente a saída correta.
                without_fields = re.sub(r"\{[^}]*\}", "", line)
                for pattern in forbidden:
                    if re.search(pattern, without_fields, re.I):
                        failures.append(f"{name}:{linha_n}: {pattern} em text fixo")
        self.assertEqual(failures, [],
                         "vocabulário de fornecedor cravado em gabarito:\n  "
                         + "\n  ".join(failures))

    def test_the_tracker_has_gender_too(self):
        """`o card`, `a issue`, `o ticket`."""
        for type, expected in (("businessmap", "o card"), ("jira", "a issue"),
                               ("acelerato", "o ticket"), ("generic", "o item")):
            with self.subTest(tracker=type):
                self.assertEqual(
                    test_config(tracker={"type": type}).tracker.with_article(),
                    expected)


class TestLegacyPortugueseKeys(unittest.TestCase):
    """A Portuguese configuration keeps working for one cycle, and says so.

    This class exists because the promise broke twice during the rewrite, both times the
    same way: a blanket rename swept over `LEGACY_SECTIONS` and replaced the Portuguese
    key — which is DATA, the thing being recognised — with its English destination. The
    map then read `{"paths": "paths"}` and silently stopped recognising `caminhos:`, so
    every existing installation failed with "`paths.repositories` is required".

    A map whose keys are the old spelling cannot be renamed by a tool that renames old
    spellings. Hence a test that reads a real Portuguese file end to end.
    """

    LEGACY = """
prumo: 1
caminhos:
  repositorios: ./repos
  perfis: ./perfis
  relatorios: ./relatorios
rastreador:
  tipo: generico
forge:
  tipo: gitlab
agnosticismo:
  proibidos:
    - rotulo: "fornecedor"
      padrao: "(?i)\\\\bacme\\\\b"
deteccao:
  registros_de_prova:
    - tipo: meu-framework
      marcador: meu.json
"""

    def config(self):
        with tempfile.TemporaryDirectory() as t:
            f = pathlib.Path(t) / "prumo.yml"
            f.write_text(self.LEGACY, encoding="utf-8")
            with mock.patch.dict("os.environ", {"PRUMO_CONFIG": str(f)}):
                return load_config()

    def test_every_legacy_section_is_recognised(self):
        c = self.config()
        self.assertTrue(str(c.paths.repositories).endswith("repos"))
        self.assertTrue(str(c.paths.profiles).endswith("perfis"))
        self.assertTrue(str(c.paths.reports).endswith("relatorios"))
        self.assertEqual(c.tracker.type, "generic")
        self.assertEqual(c.forge.type, "gitlab")
        self.assertEqual(len(c.agnosticism), 1)
        self.assertEqual(c.agnosticism[0]["label"], "fornecedor")
        self.assertEqual(c.proof_registries[0]["type"], "meu-framework")

    def test_the_legacy_keys_are_declared_not_swallowed(self):
        """Accepting in silence would be worse than breaking: the installation would
        drift for months and only find out when support is removed."""
        found = self.config().legacy_keys
        for expected in ("caminhos", "rastreador", "deteccao", "agnosticismo"):
            self.assertIn(expected, found)

    def test_the_map_still_keys_on_the_portuguese_spelling(self):
        """The regression guard proper: if a rename ever sweeps this map again, the keys
        become their own values and the whole thing silently stops matching."""
        from providers import LEGACY_SECTIONS
        for old, new in LEGACY_SECTIONS.items():
            self.assertNotEqual(old, new, f"`{old}` maps to itself — swept by a rename")

    def test_no_legacy_field_maps_to_itself(self):
        """Same guard, one level down: a sweep that hits `LEGACY_FIELDS` turns `tipo`
        into `type` on BOTH sides, and `forge.tipo:` stops being recognised."""
        from providers import LEGACY_FIELDS
        for section, mapping in LEGACY_FIELDS.items():
            for old, new in mapping.items():
                self.assertNotEqual(old, new,
                                    f"`{section}.{old}` maps to itself — swept by a rename")
