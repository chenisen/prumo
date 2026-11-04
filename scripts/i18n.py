#!/usr/bin/env python3
"""Message catalogues: everything a person reads, in the language they chose.

The rule that governs this module is the same one that governs the profile and
`prumo.yml`: **what belongs to an installation does not live in the method.** A language
is an installation fact. Hard-coding Portuguese in a `print` is the same bug as
hard-coding GitLab in the resolver — it works for whoever wrote it and lies to everybody
else.

Two consequences, and both are enforced by tests:

  * **No user-facing string is a literal in code.** It is a key here, resolved at call
    time. A literal that slips through is invisible to the translator and to the reader
    who set `language: pt-BR`.
  * **A catalogue that is missing a key is a defect, not a fallback.** Silently falling
    back to English would make a half-translated release look finished. We raise, and a
    test compares the catalogues key by key.

Usage:
    from i18n import t
    t("preflight.no_token", forge="PR")
"""
from __future__ import annotations

import functools
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIR_MESSAGES = ROOT / "messages"

# The language a clone speaks before anyone configures it. English, because this is a
# public tool: whoever finds it should be able to read it without first learning what
# `caminhos` means. An installation says `language: pt-BR` and gets Portuguese.
DEFAULT = "en"


class MissingMessage(KeyError):
    """A key the catalogue does not carry. Never degrade to another language silently:
    a half-translated report reads as finished, and the reader cannot tell."""


class UnknownLanguage(ValueError):
    """A language with no catalogue. Naming the known ones is part of the error —
    guessing what the user meant is how `pt` and `pt_BR` become undebuggable."""


def available() -> list[str]:
    return sorted(p.stem for p in DIR_MESSAGES.glob("*.yml"))


@functools.cache
def catalogue(language: str) -> dict:
    """Flat `section.key` → text. Cached: the file is read once per process."""
    path = DIR_MESSAGES / f"{language}.yml"
    if not path.exists():
        raise UnknownLanguage(
            f"no catalogue for language `{language}` — known: {', '.join(available())}. "
            f"Add `messages/{language}.yml` to teach the prumo a new one.")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _flatten(data)


def _flatten(data: dict, prefix: str = "") -> dict:
    """Nested YAML reads better than a thousand flat keys; code wants one lookup."""
    out = {}
    for key, value in data.items():
        whole = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, f"{whole}."))
        else:
            out[whole] = value
    return out


def current_language() -> str:
    """The configured language, or the default when there is no configuration.

    This module must work BEFORE the configuration is readable — the message that says
    `prumo.yml` is missing is itself a message. Hence the guarded import and the
    fallback: a broken installation still gets a sentence it can act on, in English."""
    try:
        from providers import load_config
        return load_config().language
    except Exception:
        return DEFAULT


def t(key: str, language: str | None = None, **fields) -> str:
    """Resolve a key, filling `{placeholders}` from the keyword arguments."""
    lang = language or current_language()
    cat = catalogue(lang)
    if key not in cat:
        raise MissingMessage(
            f"`{key}` is missing from `messages/{lang}.yml`. Every user-facing string "
            f"is a key: add it to EVERY catalogue, not only this one.")
    try:
        return str(cat[key]).format(**fields)
    except KeyError as e:
        raise MissingMessage(
            f"`{key}` in `messages/{lang}.yml` wants placeholder {e}, which the caller "
            f"did not pass. A placeholder that never arrives prints as literal braces.") from e


def main() -> int:
    """Compare the catalogues. Run it before releasing a translation."""
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true", help="compare every catalogue to the default")
    args = p.parse_args()

    langs = available()
    print(f"catalogues: {', '.join(langs)}  (default: {DEFAULT})")
    base = catalogue(DEFAULT)
    print(f"  {DEFAULT}: {len(base)} keys")
    failed = False
    for lang in langs:
        if lang == DEFAULT:
            continue
        other = catalogue(lang)
        missing = sorted(set(base) - set(other))
        extra = sorted(set(other) - set(base))
        print(f"  {lang}: {len(other)} keys"
              + (f" · MISSING {len(missing)}" if missing else "")
              + (f" · EXTRA {len(extra)}" if extra else ""))
        for k in missing[:10]:
            print(f"      missing: {k}")
        for k in extra[:10]:
            print(f"      extra:   {k}")
        failed = failed or bool(missing or extra)
    if args.check and failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
