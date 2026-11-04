"""Foundations shared by the prumo's scripts.

Nothing here knows a repository: whatever varies per project arrives through the profile.
Nothing here knows an installation: whatever varies per workspace arrives through
`prumo.yml`.
"""
from __future__ import annotations

import datetime as _dt
import functools
import json
import os
import pathlib
import re

from providers import (MissingConfiguration, InvalidConfiguration,  # noqa: F401
                        load_config)

PRUMO_ROOT = pathlib.Path(__file__).resolve().parent.parent


# The paths are FUNCTIONS, not constants, for one reason: the configuration is a
# precondition of use, not of import. As a constant, `from common import DIR_REPOS` would
# fire the read of `prumo.yml` on the import of any script, and a workspace with no
# configuration would die with a traceback before `main()` — far from the cause and with
# no remedy written down. As a function, the caller decides where to catch it, and the
# error comes out as a readable impediment.
@functools.cache
def cfg():
    """This installation's configuration. Cached: the file is read once per process."""
    return load_config()


def dir_repositories() -> pathlib.Path:
    return cfg().paths.repositories


def dir_profiles() -> pathlib.Path:
    return cfg().paths.profiles


def dir_reports() -> pathlib.Path:
    return cfg().paths.reports


def forge():
    """Vocabulary and refs of the proposal provider (GitLab, GitHub, Bitbucket…)."""
    return cfg().forge


def tracker():
    """Vocabulary and comment format of the tracker (BusinessMap, Jira…)."""
    return cfg().tracker

# The eighteen classes. The class belongs to the method; the anchor belongs to the
# profile.
# `anchor_required` = False means the class holds even when the profile declares nothing.
# `combination` says how path and pattern add up when BOTH exist:
#   "ou" (or)  — touching the path is signal already (the surface itself is the risk)
#   "e"  (and) — the path says WHERE to look and the pattern says WHAT; without both
#                there is no signal
# `production_only` excludes PROOF files from the search: a delivery that only adds tests
#   changes no business rule and inverts no dependency, and matching an enum's name inside
#   a test file is a false positive. The class that asks about tests — A8 — does not use it.
# `code_only` restricts the search to code files: looking for a shape of code inside a .md
# finds the word and not the fact. A9 deliberately does NOT carry it — a secret pasted into
# a document is every bit as exposed as one in a .env.
CLASSES = {
    "A1":  dict(section="A", anchor_required=True,
                question="was an already-applied schema artefact edited?"),
    "A2":  dict(section="A", anchor_required=True,
                question="did the shape of an outbound contract change?"),
    # A3 asks what the SYSTEM authorises, so proof is not where it happens: a test that
    # asserts a 401 or names a tenant touches no authorisation scope, and matching the
    # word `tenant` inside a test file cost a human decision about something that did not
    # occur. Same argument as A5 and A6, applied here on 2026-11-04 after a test-only
    # delivery fired it on one word.
    "A3":  dict(section="A", anchor_required=False, code_only=True, production_only=True,
                combination="e",
                question="was an authorisation-scope or tenant clause touched?"),
    "A4":  dict(section="A", anchor_required=True,
                question="did an integration surface change?"),
    "A5":  dict(section="A", anchor_required=False, code_only=True, production_only=True, combination="e",
                question="did a business rule change?"),
    "A6":  dict(section="A", anchor_required=False, code_only=True, production_only=True, combination="e",
                question="was a layer or boundary introduced, or a dependency inverted?"),
    "A7":  dict(section="A", anchor_required=True,
                question="does the code state a fact the specification does not hold, or contradict one it does?"),
    "A8":  dict(section="A", anchor_required=False, combination="e",
                question="was a test removed or weakened?"),
    "A9":  dict(section="A", anchor_required=False, check="secrets",
                question="did a secret, a credential or key material get in?"),
    "A10": dict(section="A", anchor_required=True,
                question="was a hot path touched with an effect at volume?"),
    "B1":  dict(section="B", anchor_required=True,
                question="was a derived artefact, observable only by running, affected?"),
    "B2":  dict(section="B", anchor_required=True,
                question="was an integration with a legacy internal system touched?"),
    "B3":  dict(section="B", anchor_required=True, combination="e",
                question="was an external provider touched?"),
    "B4":  dict(section="B", anchor_required=True,
                question="did an aggregate spanning two stores change?"),
    "B5":  dict(section="B", anchor_required=True,
                question="did a screen, a flow or interface text change?"),
    "B6":  dict(section="B", anchor_required=False, code_only=True, production_only=True, combination="e",
                question="is there a data migration, not only a schema one?"),
    "B7":  dict(section="B", anchor_required=False, code_only=True, production_only=True,
                question="is there behaviour that only shows up at volume?"),
    "B8":  dict(section="B", anchor_required=False,
                question="does the delivery reach production through a formal process?"),
}

DOCUMENT_EXTENSIONS = (".md", ".markdown", ".txt", ".rst", ".adoc", ".mdx")


def is_document(path: str) -> bool:
    return path.lower().endswith(DOCUMENT_EXTENSIONS)


CHECK_ROLES = ("secrets", "static", "lint", "dependencies", "suite")

BLOCKING_SEVERITIES = ("critical", "required")

# Worst first. These are the DATA values of `finding.schema.json`'s severity enum, so
# anything that sorts findings reads them from here. Spelling the list out a second time
# inside a sort key is how `render.py` came to sort on five words that stopped being the
# enum: the id changed, the copy did not, and the report raised ValueError on the first
# `critical` it ever saw.
SEVERITY_ORDER = ("critical", "required", "nit", "optional", "fyi")


class UsageError(Exception):
    """Malformed input: the message goes to the person, not into a stack trace."""


def today() -> str:
    return os.environ.get("TASK_REVIEW_HOJE") or _dt.date.today().isoformat()


# The profile is versioned and shared by everyone who uses the workspace; the name of
# whoever signs a human act is NOT. The profile stores the token, and the environment says
# who that is. Same convention as `TASK_REVIEW_HOJE`.
OWNER_TOKEN = "${RESPONSAVEL}"


def owner() -> str | None:
    """Who signs, in this run, the human acts the profile records.

    The environment wins over the configuration, and that order is the point. A solo
    installation writes the name once in `prumo.yml` and forgets it; a team that commits
    its `prumo.yml` would otherwise sign every review with one person's name, and the
    environment variable is each person's way out of that without editing a shared file.
    """
    from_env = (os.environ.get("TASK_REVIEW_RESPONSAVEL") or "").strip()
    if from_env:
        return from_env
    try:
        return cfg().owner or None
    except Exception:
        # No configuration is not the same as no owner: the caller that needs a name
        # will say so. Raising here would turn a missing `prumo.yml` into a traceback in
        # the middle of rendering a report.
        return None


def resolve_owner(value, who: str | None = None):
    """Swap the token for the name of whoever is conducting. With no name in the
    environment, leave the token standing — so the absence stays VISIBLE downstream
    instead of collapsing into an empty string."""
    who = who or owner()
    if not who:
        return value
    if isinstance(value, str):
        return value.replace(OWNER_TOKEN, who)
    if isinstance(value, dict):
        return {k: resolve_owner(v, who) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_owner(v, who) for v in value]
    return value


def unresolved_tokens(value) -> bool:
    """True when an owner token is left anywhere in the structure."""
    if isinstance(value, str):
        return OWNER_TOKEN in value
    if isinstance(value, dict):
        return any(unresolved_tokens(v) for v in value.values())
    if isinstance(value, list):
        return any(unresolved_tokens(v) for v in value)
    return False


def valid_date(value: str) -> bool:
    try:
        _dt.date.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def waiver_in_force(waiver: dict | None, reference: str | None = None) -> bool:
    """A waiver holds only until it expires. An expired one is as if it never existed."""
    if not waiver:
        return False
    expires = waiver.get("expires")
    if not valid_date(expires or ""):
        return False
    return _dt.date.fromisoformat(expires) >= _dt.date.fromisoformat(reference or today())


def profile_path(repo: str) -> pathlib.Path:
    return dir_profiles() / f"{repo}.yml"


def load_profile(repo: str) -> dict:
    """Load the repo's profile. Absent returns a skeleton with status `ausente`.

    An absent profile NEVER stops the review: it becomes the judge's `perfil_ausente`
    route.
    """
    import yaml

    path = profile_path(repo)
    if not path.exists():
        return {"profile": repo, "version": 0, "status": "absent", "anchors": {},
                "conventions": [], "checks": {}, "evals": [], "pending": []}
    profile = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profile.setdefault("profile", repo)
    return resolve_owner(profile)


def write_json(path: pathlib.Path, data) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_json(path: pathlib.Path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def glob_to_regex(pattern: str) -> re.Pattern:
    """Turn a path glob into a regex. `**` crosses directories, `*` does not."""
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def casa_algum(path: str, patterns) -> bool:
    return any(glob_to_regex(p).search(path) for p in (patterns or []))


def slug(text: str, limit: int = 60) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t[:limit] or "untitled"
