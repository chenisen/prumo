#!/usr/bin/env python3
"""Providers, paths and language: everything the prumo cannot guess.

The method does not know repositories — that already came from the profile. This module
takes away the other half of what belongs to an *installation* rather than to a review:

  * **where things live** — the root of the target repositories, of the profiles and of
    the reports;
  * **which tracker** — BusinessMap, Jira, Acelerato, or a generic one;
  * **which forge** — GitLab, GitHub, Bitbucket;
  * **which language** the prumo answers in.

The rule is the same one that governs an unconfirmed profile and the mandatory reading:
**what the prumo does not know, it does not assume.** Without `prumo.yml` there is no
review. A silent default would have someone review a GitHub PR with GitLab vocabulary,
publish an HTML comment into a tracker that only renders Markdown, or resolve `--repo x`
against a tree that is not theirs — three errors that surface later, in text already
published.

Presets exist so the configuration can be short, not so it can be optional: you declare
`type: github` and inherit ref, prefix, tokens and vocabulary; you declare one loose
field and override only that one.

Usage:
    from providers import load_config, test_config
    cfg = load_config()                 # finds prumo.yml, validates, resolves presets
    cfg.forge.proposal_ref(820)         # 'refs/merge-requests/820/head'
"""
from __future__ import annotations

import dataclasses
import os
import pathlib
import re

import yaml

CONTRACT_VERSION = 1

# Looked up in this order. The environment variable wins over everything, so CI and a
# test checkout can point elsewhere without editing a versioned file.
FILE_NAME = "prumo.yml"
ENV_CONFIG = "PRUMO_CONFIG"

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class MissingConfiguration(Exception):
    """No configuration, no review. An installation error, not a review error."""


class InvalidConfiguration(Exception):
    """The configuration exists but does not hold against the contract."""


# --- PRESETS -------------------------------------------------------------------------
#
# Each preset is what that provider does DIFFERENTLY, and nothing else. A field every
# provider handles the same way is not a preset: it is a constant in the code.

# How the harness reaches a forge's API. Reviewing needs none of them.
FORGE_CONNECTIONS = {
    "mcp":   "a connector the harness already carries",
    "token": "an environment variable holding a personal access token",
    "none":  "no API: the diff comes over git, and publishing stays manual",
}

# What a secret looks like, so that one never reaches a configuration file, a profile or
# a report. These are the public prefixes of the forges this file knows, plus the two
# shapes that carry a credential without a prefix: a key block and a URL with a password
# in it.
SECRET_SHAPES = (
    r"glpat-[A-Za-z0-9_-]{16,}",
    r"gh[pousr]_[A-Za-z0-9]{16,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"ATBB[A-Za-z0-9]{16,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]{6,}@",
)


def looks_like_a_secret(text: str) -> bool:
    """True when the text carries something that must never be written down.

    It answers about a VALUE, not about a name: `GITLAB_TOKEN` is a name and passes;
    `glpat-…` is a value and does not."""
    return any(re.search(shape, text) for shape in SECRET_SHAPES)


FORGES = {
    "gitlab": {
        "proposal": "MR",
        # Portuguese has grammatical gender, and the provider's is not the method's
        # choice: one says "a MR" and "o PR". Without this the report disagrees with
        # itself in half the installations — small, but it is text that reaches an
        # item and a proposal real people read.
        "article": "a",
        "proposal_plural": "MRs",
        "prefix": "!",
        # The server publishes each proposal's head under a read-only ref. The pattern
        # differs per provider, and it is the only thing the resolver needs to know.
        "ref": "refs/merge-requests/{n}/head",
        "tokens": ["GITLAB_TOKEN", "GLAB_TOKEN"],
        "cli": "glab",
        "approve": True,          # the provider has an Approve act of its own
    },
    "github": {
        "proposal": "PR",
        "article": "o",
        "proposal_plural": "PRs",
        "prefix": "#",
        "ref": "refs/pull/{n}/head",
        "tokens": ["GITHUB_TOKEN", "GH_TOKEN"],
        "cli": "gh",
        "approve": True,
    },
    "bitbucket": {
        "proposal": "PR",
        "article": "o",
        "proposal_plural": "PRs",
        "prefix": "#",
        # Bitbucket Server publishes the ref; Cloud publishes none, and there the origin
        # has to be the BRANCH. The preflight says so instead of failing in the dark.
        "ref": "refs/pull-requests/{n}/from",
        "tokens": ["BITBUCKET_TOKEN", "BITBUCKET_APP_PASSWORD"],
        "cli": "",
        "approve": True,
    },
}

TRACKERS = {
    "businessmap": {
        "article": "o",
        "item": "card",
        "item_plural": "cards",
        # The board renders a subset of HTML. `<span class="text-big">` is what it reads
        # as a heading; Markdown there comes out as raw text.
        "format": "html",
        "heading": '<span class="text-big">{text}</span>',
    },
    "jira": {
        "article": "a",
        "item": "issue",
        "item_plural": "issues",
        # Jira Cloud's v3 API speaks ADF. Wiki markup is still accepted in many fields,
        # and it is what a simple comment can use without building a node tree.
        "format": "wiki",
        "heading": "h3. {text}",
    },
    "acelerato": {
        "article": "o",
        "item": "ticket",
        "item_plural": "tickets",
        "format": "html",
        "heading": "<h3>{text}</h3>",
    },
    "generic": {
        "article": "o",
        "item": "item",
        "item_plural": "items",
        "format": "markdown",
        "heading": "### {text}",
    },
}

FORMATS = ("html", "markdown", "wiki")

# --- LEGACY KEYS ---------------------------------------------------------------------
#
# The prumo was written in Portuguese and its configuration carried Portuguese keys. The
# rewrite into English renames them, and renaming a contract breaks every file already
# written against it.
#
# So the loader accepts both, FOR ONE CYCLE, and says so each time. Accepting silently
# would be worse than breaking: an installation would drift for months and only find out
# when support is removed, with nobody left who remembers why the file looks like that.
# `scripts/migrate_config.py` rewrites a file in place.
LEGACY_SECTIONS = {"caminhos": "paths", "rastreador": "tracker",
                   "deteccao": "detection", "agnosticismo": "agnosticism"}
# The Portuguese spelling is the KEY, because it is what we are recognising; the English
# one is the value. A blanket rename over this file has already turned the keys into
# their own values twice — `{"paths": "paths"}` — which silently stops matching every
# configuration written before the rewrite. `test_providers.py` pins it.
LEGACY_FIELDS = {
    "paths": {"repositorios": "repositories", "perfis": "profiles",
              "relatorios": "reports"},
    "tracker": {"tipo": "type", "formato": "format", "titulo": "heading",
                "artigo": "article"},
    "forge": {"tipo": "type", "proposta": "proposal", "prefixo": "prefix",
              "artigo": "article", "aprovar": "approve"},
    "detection": {"registros_de_prova": "proof_registries", "tipo": "type",
                  "marcador": "marker", "observacao": "note"},
    "agnosticism": {"proibidos": "forbidden", "rotulo": "label", "padrao": "pattern"},
}
LEGACY_TRACKER_TYPES = {"generico": "generic"}


@dataclasses.dataclass(frozen=True)
class Forge:
    type: str
    proposal: str
    proposal_plural: str
    article: str
    prefix: str
    _ref: str
    tokens: tuple[str, ...]
    cli: str
    approve: bool
    # How the HARNESS reaches this forge's API, when it reaches it at all: `mcp` (a
    # connector the harness already carries), `token` (an environment variable named
    # here, never a value), or `none`. Reviewing needs neither — the diff comes over
    # git, through the ref the server publishes. Reading a proposal's description
    # without a file, and publishing, need one of the two.
    connection: str = "none"

    def proposal_ref(self, n) -> str:
        return self._ref.format(n=n)

    def token_in_environment(self) -> str | None:
        """The first token present, or None. Deciding what to do without one belongs to
        the preflight — here it is only measured."""
        for name in self.tokens:
            if os.environ.get(name):
                return name
        return None

    def label(self, n) -> str:
        """How this proposal is written in a sentence: `MR !820`, `PR #820`."""
        return f"{self.proposal} {self.prefix}{n}"

    def with_article(self, capital: bool = False) -> str:
        """`a MR`, `o PR` — so Portuguese prose agrees. English catalogues ignore it."""
        art = self.article.capitalize() if capital else self.article
        return f"{art} {self.proposal}"


@dataclasses.dataclass(frozen=True)
class Tracker:
    type: str
    article: str
    item: str
    item_plural: str
    format: str
    heading: str
    url: str

    def item_url(self, id_) -> str:
        return self.url.format(id=id_) if self.url else ""

    def heading_for(self, text: str) -> str:
        return self.heading.format(text=text)

    def with_article(self, capital: bool = False) -> str:
        """`o card`, `a issue` — same reason as the forge's."""
        art = self.article.capitalize() if capital else self.article
        return f"{art} {self.item}"


@dataclasses.dataclass(frozen=True)
class Paths:
    repositories: pathlib.Path
    profiles: pathlib.Path
    reports: pathlib.Path
    # What the reviews learned about a repository, accrued across reviews and kept
    # OUTSIDE it — the same reason the profile lives outside: a fact about a project is
    # not a file of that project, and nothing this method writes belongs in a proposal.
    # Optional, because an installation that does not want the accrual should not have
    # to declare a path to switch it off.
    knowledge: pathlib.Path
    # Where a review's working package is assembled — the diff, the slices, the manifest,
    # everything the agents read. Optional, and it defaults OUTSIDE the workspace
    # (`$TMPDIR/task-review`) because a package is scratch, not a deliverable, and an
    # installation that never says otherwise should not have its tree filled with patches.
    # An installation that wants them kept — inspectable after the fact, surviving a /tmp
    # sweep, in one place with the reports — points this somewhere of its own.
    packages: pathlib.Path

    def repo(self, name: str) -> pathlib.Path:
        return self.repositories / name

    def profile(self, name: str) -> pathlib.Path:
        return self.profiles / f"{name}.yml"


# Proof registry: a marker in the repository decides the type. The default carries only
# what is PUBLIC and recognisable anywhere; an internal specification framework belongs
# to the installation and comes in through `prumo.yml`, never here — that is how one
# private framework's name ended up in everybody's contract.
DEFAULT_REGISTRIES = (
    {"type": "speckit", "marker": ".claude/skills/speckit-specify",
     "note": "Spec Kit: constitution, spec and tasks before the code"},
    {"type": "specs-in-repo", "marker": ".claude/specs",
     "note": "spec and plan versioned per item, inside the repo"},
)


@dataclasses.dataclass(frozen=True)
class Config:
    forge: Forge
    tracker: Tracker
    paths: Paths
    source: pathlib.Path | None   # where this configuration was read from
    language: str = "en"
    # Who signs the human acts a profile records. It lives HERE, and not in the profile,
    # for the reason the profile states: a profile is versioned and shared between
    # everyone who reviews that repository, so a name written into it would sign
    # everybody's reviews with the first person who confirmed it. `prumo.yml` belongs to
    # one installation and is git-ignored, so a name is at home.
    #
    # `TASK_REVIEW_RESPONSAVEL` still WINS over this. A team that does commit its
    # `prumo.yml` gets the same problem back, and the environment is the escape hatch:
    # the file carries the common default, each person overrides it for their own run.
    owner: str = ""
    # Patterns `agnosticism_check.py` adds to the universal ones: the system, table and
    # item-id shapes of THIS company, which must not leak into the method. Optional —
    # without the section, only the universal ones apply.
    agnosticism: tuple = ()
    # Markers `derive_profile.py` looks for to discover a repository's proof registry.
    # Adds to the public ones; does not replace them.
    proof_registries: tuple = DEFAULT_REGISTRIES
    # Legacy keys found while loading. The caller prints them once; silence would let an
    # installation drift until support is removed.
    legacy_keys: tuple = ()


def _find_file(start: pathlib.Path | None = None) -> pathlib.Path | None:
    """Env var, then the prumo's own root, then walking up from the cwd.

    Walking up from the cwd is what allows a per-workspace installation without touching
    the prumo checkout: `prumo.yml` lives at the root of whoever uses it."""
    if (env := os.environ.get(ENV_CONFIG)):
        p = pathlib.Path(env).expanduser()
        return p if p.exists() else None
    if (here := REPO_ROOT / FILE_NAME).exists():
        return here
    current = (start or pathlib.Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        if (candidate := directory / FILE_NAME).exists():
            return candidate
    return None


def _translate_legacy(data: dict) -> tuple[dict, list[str]]:
    """Rename the Portuguese keys and report which ones were found.

    Shallow by design: it renames the sections, their direct fields, and the fields of
    the two list-valued sections. A deep generic rename would also hit VALUES that happen
    to match a key name, which is how a tracker called `tipo` would silently become
    `type`."""
    found: list[str] = []
    out = dict(data)

    for old, new in LEGACY_SECTIONS.items():
        if old in out:
            found.append(old)
            out[new] = out.pop(old)

    for section, mapping in LEGACY_FIELDS.items():
        block = out.get(section)
        if not isinstance(block, dict):
            continue
        novo = {}
        for k, v in block.items():
            if k in mapping and mapping[k] != k:
                found.append(f"{section}.{k}")
                novo[mapping[k]] = v
            else:
                novo[k] = v
        out[section] = novo

    for section, lista in (("detection", "proof_registries"),
                           ("agnosticism", "forbidden")):
        block = out.get(section)
        if not isinstance(block, dict) or not isinstance(block.get(lista), list):
            continue
        mapping = LEGACY_FIELDS[section]
        novos = []
        for entry in block[lista]:
            if not isinstance(entry, dict):
                novos.append(entry)
                continue
            n = {}
            for k, v in entry.items():
                if k in mapping and mapping[k] != k:
                    found.append(f"{section}.{lista}[].{k}")
                    n[mapping[k]] = v
                else:
                    n[k] = v
            novos.append(n)
        block[lista] = novos

    tracker = out.get("tracker")
    if isinstance(tracker, dict) and tracker.get("type") in LEGACY_TRACKER_TYPES:
        found.append(f"tracker.type: {tracker['type']}")
        tracker["type"] = LEGACY_TRACKER_TYPES[tracker["type"]]

    return out, sorted(set(found))


def _require(mapping: dict, key: str, where: str):
    if key not in mapping or mapping[key] in (None, ""):
        raise InvalidConfiguration(f"`{where}.{key}` is required and is empty")
    return mapping[key]


def _resolve_path(value: str, base: pathlib.Path) -> pathlib.Path:
    p = pathlib.Path(str(value)).expanduser()
    if not p.is_absolute():
        p = (base / p).resolve()
    return p


def build(data: dict, source: pathlib.Path | None = None) -> Config:
    """Validate the mapping and resolve the presets. Pure: touches no disk beyond
    `expanduser`."""
    if not isinstance(data, dict):
        raise InvalidConfiguration("the file does not contain a YAML mapping")

    version = data.get("prumo")
    if version != CONTRACT_VERSION:
        raise InvalidConfiguration(
            f"`prumo: {CONTRACT_VERSION}` is required on the first line "
            f"(found: {version!r}) — it is what says which contract this file follows")

    data, legacy = _translate_legacy(data)
    base = source.parent if source else pathlib.Path.cwd()

    # --- language
    language = data.get("language") or "en"
    if not isinstance(language, str):
        raise InvalidConfiguration("`language` must be a string, e.g. `en` or `pt-BR`")
    known = sorted(p.stem for p in (REPO_ROOT / "messages").glob("*.yml"))
    if known and language not in known:
        raise InvalidConfiguration(
            f"unknown language `{language}` — known: {', '.join(known)}. "
            f"Add `messages/{language}.yml` to teach the prumo a new one.")

    # --- owner
    owner = data.get("owner") or ""
    if not isinstance(owner, str):
        raise InvalidConfiguration("`owner` must be a string — the name that signs")

    # --- paths
    c = data.get("paths") or {}
    if not isinstance(c, dict):
        raise InvalidConfiguration("`paths` must be a mapping")
    paths = Paths(
        repositories=_resolve_path(_require(c, "repositories", "paths"), base),
        profiles=_resolve_path(_require(c, "profiles", "paths"), base),
        reports=_resolve_path(_require(c, "reports", "paths"), base),
        packages=(_resolve_path(c["packages"], base) if c.get("packages")
                  else pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "task-review"),
        knowledge=(_resolve_path(c["knowledge"], base) if c.get("knowledge")
                   else _resolve_path(_require(c, "reports", "paths"), base)
                   / "_knowledge"),
    )

    # --- forge
    f = data.get("forge") or {}
    if not isinstance(f, dict):
        raise InvalidConfiguration("`forge` must be a mapping")
    forge_type = _require(f, "type", "forge")
    if forge_type not in FORGES:
        raise InvalidConfiguration(
            f"unknown forge `{forge_type}` — known: {', '.join(sorted(FORGES))}. "
            "For a new provider, declare the `type` of a similar one and override "
            "`ref`, `proposal`, `prefix` and `tokens`.")
    pf = {**FORGES[forge_type], **{k: v for k, v in f.items() if k != "type" and v is not None}}
    tokens = pf["tokens"]
    if isinstance(tokens, str):
        tokens = [tokens]
    if "{n}" not in str(pf["ref"]):
        raise InvalidConfiguration("`forge.ref` must contain `{n}`, the proposal number")
    connection = pf.get("connection") or "none"
    if connection not in FORGE_CONNECTIONS:
        raise InvalidConfiguration(
            f"unknown `forge.connection` `{connection}` — known: "
            f"{', '.join(sorted(FORGE_CONNECTIONS))}. `none` is a valid answer: the diff "
            "is read over git, and publishing stays manual.")
    # A configuration file is versioned, shared and pasted into messages. The NAME of an
    # environment variable belongs in it; the value never does, and the difference is one
    # keystroke in a terminal. Refusing it here is the only moment where the refusal is
    # cheap: afterwards the secret is already on disk, and probably already in a commit.
    for name in tokens:
        if looks_like_a_secret(name):
            raise InvalidConfiguration(
                "`forge.tokens` takes the NAME of an environment variable, never a "
                f"token: `{name[:7]}…` looks like a secret. Write `GITLAB_TOKEN` and "
                "export the value in your environment.")
    forge = Forge(type=forge_type, proposal=pf["proposal"],
                  proposal_plural=pf["proposal_plural"], article=pf["article"],
                  prefix=pf["prefix"], _ref=pf["ref"], tokens=tuple(tokens),
                  cli=pf["cli"] or "", approve=bool(pf["approve"]),
                  connection=connection)

    # --- tracker
    r = data.get("tracker") or {}
    if not isinstance(r, dict):
        raise InvalidConfiguration("`tracker` must be a mapping")
    tracker_type = _require(r, "type", "tracker")
    if tracker_type not in TRACKERS:
        raise InvalidConfiguration(
            f"unknown tracker `{tracker_type}` — known: {', '.join(sorted(TRACKERS))}. "
            "Use `generic` and override `item`, `format` and `heading` for a provider "
            "outside the list.")
    pr = {**TRACKERS[tracker_type],
          **{k: v for k, v in r.items() if k != "type" and v is not None}}
    if pr["format"] not in FORMATS:
        raise InvalidConfiguration(
            f"`tracker.format` must be one of {', '.join(FORMATS)} "
            f"(found: {pr['format']!r})")
    tracker = Tracker(type=tracker_type, article=pr["article"], item=pr["item"],
                      item_plural=pr["item_plural"], format=pr["format"],
                      heading=pr["heading"], url=pr.get("url") or "")

    # --- agnosticism (optional)
    ag = data.get("agnosticism") or {}
    if not isinstance(ag, dict):
        raise InvalidConfiguration("`agnosticism` must be a mapping")
    extras = []
    for i, entry in enumerate(ag.get("forbidden") or []):
        if not isinstance(entry, dict) or not entry.get("label") or not entry.get("pattern"):
            raise InvalidConfiguration(
                f"`agnosticism.forbidden[{i}]` needs `label` and `pattern`")
        try:
            re.compile(entry["pattern"])
        except re.error as e:
            raise InvalidConfiguration(
                f"`agnosticism.forbidden[{i}].pattern` is not a valid regex: {e}") from e
        extras.append({"label": entry["label"], "pattern": entry["pattern"]})

    # --- detection (optional)
    det = data.get("detection") or {}
    if not isinstance(det, dict):
        raise InvalidConfiguration("`detection` must be a mapping")
    registries = list(DEFAULT_REGISTRIES)
    for i, entry in enumerate(det.get("proof_registries") or []):
        if not isinstance(entry, dict) or not entry.get("type") or not entry.get("marker"):
            raise InvalidConfiguration(
                f"`detection.proof_registries[{i}]` needs `type` and `marker`")
        registries.insert(0, {"type": entry["type"], "marker": entry["marker"],
                              "note": entry.get("note") or ""})

    return Config(forge=forge, tracker=tracker, paths=paths, source=source,
                  language=language, owner=owner.strip(), agnosticism=tuple(extras),
                  proof_registries=tuple(registries), legacy_keys=tuple(legacy))


def load_config(start: pathlib.Path | None = None) -> Config:
    """Find, read and validate. Raises `MissingConfiguration` when there is no file.

    The caller must NOT swallow the exception behind a default: the installation is a
    precondition, not a preference."""
    path = _find_file(start)
    if path is None:
        raise MissingConfiguration(
            f"`{FILE_NAME}` not found. The prumo assumes neither provider nor path: "
            f"copy `{FILE_NAME}.example` from the prumo root, fill it in, and put it at "
            f"the root of your workspace — or point `{ENV_CONFIG}` at it.")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise InvalidConfiguration(f"{path}: invalid YAML — {e}") from e
    try:
        return build(data, source=path)
    except InvalidConfiguration as e:
        raise InvalidConfiguration(f"{path}: {e}") from e


def test_config(**overrides) -> Config:
    """A minimal config for tests, touching no disk. Not for production: the paths point
    at /tmp on purpose, so a test that writes in the wrong place fails visibly instead of
    littering the workspace."""
    base = {
        "prumo": CONTRACT_VERSION,
        "paths": {"repositories": "/tmp/prumo-test/repos",
                  "profiles": "/tmp/prumo-test/profiles",
                  "reports": "/tmp/prumo-test/reports"},
        "forge": {"type": "gitlab"},
        "tracker": {"type": "businessmap"},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return build(base)


def main() -> int:
    """`providers.py` on its own says what this installation understood. It is the first
    command to run after writing `prumo.yml`, and the one to ask for in a bug report."""
    import argparse
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        cfg = load_config()
    except (MissingConfiguration, InvalidConfiguration) as e:
        print(f"configuration: {e}")
        return 1
    print(f"configuration: {cfg.source}")
    print(f"  language    {cfg.language}")
    env_owner = (os.environ.get("TASK_REVIEW_RESPONSAVEL") or "").strip()
    if env_owner:
        where = "environment" + (f", overriding `{cfg.owner}`" if cfg.owner else "")
        print(f"  owner       {env_owner}  ({where})")
    elif cfg.owner:
        print(f"  owner       {cfg.owner}  (prumo.yml)")
    else:
        print("  owner       NOT SET — a profile's `${RESPONSAVEL}` will not resolve")
    print(f"  forge       {cfg.forge.type} · {cfg.forge.label(123)} · "
          f"{cfg.forge.proposal_ref(123)}")
    # Declared beside measured, on one line, because the interesting case is the two
    # disagreeing: `connection: token` with nothing in the shell is a configuration that
    # has drifted, and the README has shown this line since before it existed.
    tok = cfg.forge.token_in_environment()
    measured = (f"`{tok}` set in this environment" if tok
                else "no token in the environment ("
                     + ", ".join(cfg.forge.tokens) + ")")
    print(f"              connection: {cfg.forge.connection} · {measured}")
    if cfg.forge.connection == "none":
        print("              reviewing needs none; see reference/forge-connection.md")
    print(f"  tracker     {cfg.tracker.type} · item `{cfg.tracker.item}` · "
          f"comment in {cfg.tracker.format}")
    for name, p in (("repositories", cfg.paths.repositories),
                    ("profiles", cfg.paths.profiles),
                    ("reports", cfg.paths.reports)):
        print(f"  {name:12} {p}{'' if p.exists() else '   ← DOES NOT EXIST'}")
    # No "DOES NOT EXIST" on this one: it is written on the first `conclude` and its
    # absence before that is the normal state, not a misconfiguration.
    print(f"  {'knowledge':12} {cfg.paths.knowledge}")
    print(f"  {'packages':12} {cfg.paths.packages}")
    if cfg.legacy_keys:
        print("\n  legacy Portuguese keys, still accepted — run "
              "`scripts/migrate_config.py` to rewrite the file:")
        for k in cfg.legacy_keys:
            print(f"    {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
