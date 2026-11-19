#!/usr/bin/env python3
"""What the reviews learned about a repository, accrued across reviews and kept OUTSIDE it.

A review is thrown away every time. The reviewers rediscover the same repository in five
separate contexts, every run — measured over eleven reviews, that rediscovery is the
single biggest line of the bill: `cache_read` is 82% of what is spent, and it is produced
by agents opening files somebody already opened last week.

This module is the other half of that. It writes, per repository, what the reviews keep
finding out: the conventions the code exhibits, the files the reviewers keep opening, the
debt the repository has declared, and the defects no delivery caused. It is the
unconfirmed sibling of the profile — the profile is what a PERSON signed, this is what
the machine noticed.

**Nothing here closes a gate, and that is deliberate.** Only a confirmed profile anchor
decides anything, because the fifth gate measures whether the review actually measured,
and a machine-accrued fact certifying itself is exactly the hole that gate exists to
cover. An entry's road into the verdict runs through a person: `derive_profile.py` reads
this file, somebody confirms it, and it becomes an anchor.

**It carries no sha, no proposal number and no item id — by construction, not by asking.**
The agents hold `Read`, `Grep` and `Glob` over the whole workspace, and a file that names
another review's delivery is the one contamination this method has already paid for
(see `provenance.py`). A knowledge file is read by EVERY later review, so an identifier
that is legitimately this review's own today is foreign tomorrow: the sanitiser is
therefore stricter than `provenance.check` — it redacts every identifier, this review's
included, and counts what it redacted.

The machine-readable ledger (`<repo>.json`) carries dates and counts for the tooling; the
accompanying `<repo>.md` is the distillation. Only the `.md` is ever meant to reach an
agent.

Usage:
    knowledge.py --package DIR [--conventions FILE] [--output DIR]
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import dir_knowledge, dir_packages, dir_repositories, read_json, write_json, today
from provenance import RE_SHA, _proposal_number

KINDS = ("convention", "hot_file", "project_debt", "preexisting")

# A bare number, not one inside a version or an identifier. The trailing guard refuses
# `.`-then-digit (so `1.821.3` keeps its middle) while allowing a sentence's full stop:
# `provenance.py` writes this as `(?![\w.])`, which silently declines to see the id in
# "the listing was already unscoped before item 12346." — the most ordinary way a
# sentence ends.
_NUMBER = r"(?<![\w.])%s(?!\.?\d)(?!\w)"

# How many reviews an entry has to survive before the distillation prints it as something
# the repository DOES, rather than something one review thought it saw once. One sighting
# is an observation; the same sighting from independent reviews is a pattern.
CORROBORATED = 2


# ----------------------------------------------------------------- sanitising

def _identifiers(manifest: dict) -> list[tuple[re.Pattern, str]]:
    """Every identifier that ties text to ONE delivery, as redaction rules.

    This review's own included. `provenance.check` lets its own through because it is
    asking "does this text speak about somebody else's review?"; here the question is
    "will this text still be true next month?", and a sha never is.
    """
    rules: list[tuple[re.Pattern, str]] = [(RE_SHA, "<sha>")]
    for value, marker in ((( manifest.get("item") or {}).get("id"), "<item>"),
                          (_proposal_number(manifest.get("origin") or ""), "<proposal>")):
        if value:
            rules.append((re.compile(_NUMBER % re.escape(value)), marker))
    return rules


def sanitise(text: str, manifest: dict) -> tuple[str, int]:
    """The text with every delivery identifier redacted, and how many were redacted.

    Redacting rather than dropping the entry: the sentence "the guard clause pattern is
    returned from a query builder" stays useful with the sha gone, and dropping it would
    lose the knowledge to protect against a risk that redaction already removed. The count
    is reported so a section that redacted a lot can be read with suspicion.
    """
    redacted = 0
    for pattern, marker in _identifiers(manifest):
        text, n = pattern.subn(marker, text)
        redacted += n
    return text, redacted


# ----------------------------------------------------------------- harvesting

def _key(kind: str, text: str) -> str:
    """Stable dedupe key. Lowercased and stripped of runs of whitespace, so the same
    observation phrased with a different line break does not accrue twice."""
    return f"{kind}:{re.sub(r'\s+', ' ', text).strip().lower()[:160]}"


def head_tree_folder(package: pathlib.Path):
    """Since 2026-11-19 the agents read the reviewed head in a worktree of its own, whose
    path changes with every review. A path read there is folded back onto the
    repository's, or the same file would count once per review under forty different
    names and never be "hot"."""
    tree_file = package / "head-tree.json"
    if not tree_file.exists():
        return lambda path: path
    tree = json.loads(tree_file.read_text(encoding="utf-8"))
    return lambda path: (tree["repo"] + path[len(tree["path"]):]
                         if path.startswith(tree["path"] + "/") else path)


def hot_files(package: str) -> list[dict]:
    """Which repository files the reviewers of this run actually opened.

    Read from the harness's own transcripts, so it costs nothing and cannot be wrong
    about what was read. Package files are excluded: they are this review's scaffolding,
    not the repository's shape.
    """
    try:
        from telemetry import find_run, projects_root
    except ImportError:
        return []
    run = find_run(package, None)
    if not run:
        return []
    agents_dir = (run["_path"].parent.parent / "subagents" / "workflows" / run["runId"])
    if not agents_dir.is_dir():
        return []

    fold = head_tree_folder(pathlib.Path(package))

    counts: collections.Counter = collections.Counter()
    for transcript in agents_dir.glob("agent-*.jsonl"):
        seen_here = set()
        for line in transcript.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = (event.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                path = (block.get("input") or {}).get("file_path")
                if not path or package in str(path):
                    continue
                # Another review's package is not the repository's shape either.
                folded = portable_where(fold(str(path)), None)
                if folded:
                    seen_here.add(folded)
        # One count per agent, not per read: an agent that reread a file three times says
        # nothing more about the repository than one that read it once.
        counts.update(seen_here)
    return [{"text": path, "where": [path], "weight": n}
            for path, n in counts.most_common(40)]


def harvest(package: pathlib.Path, conventions: list[dict] | None) -> dict[str, list[dict]]:
    """Everything this review learned about the repository, by kind.

    All of it is already on disk when `conclude` runs — nothing here calls an LLM, and
    nothing here reads a sibling review.
    """
    found: dict[str, list[dict]] = {k: [] for k in KINDS}

    for c in conventions or []:
        statement = (c.get("statement") or "").strip()
        if statement:
            found["convention"].append({
                "text": statement,
                "where": [e.get("file") for e in (c.get("evidence") or []) if e.get("file")],
            })

    found["hot_file"] = hot_files(str(package))

    verdict_path = package / "verdict.json"
    if verdict_path.exists():
        for pending in read_json(verdict_path).get("project_pending") or []:
            text = pending.get("reason") or pending.get("route") or ""
            if text:
                found["project_debt"].append({"text": text, "where": [],
                                              "owner": pending.get("owner")})

    # The defects no delivery caused. Today they go into the report's observations and
    # die with it; accrued, they are the repository's own debt list — which is what a
    # technical-debt item is opened from.
    observations = package / "observations.json"
    if observations.exists():
        for f in read_json(observations):
            if f.get("status") == "refuted":
                continue          # the verifier knocked it down; it was never a fact
            found["preexisting"].append({
                "text": f.get("claim") or "",
                "where": [e.get("file") for e in (f.get("evidence") or []) if e.get("file")],
                "severity": f.get("severity"),
            })
    return found


# ----------------------------------------------------------------- accrual

def merge(ledger: dict, found: dict[str, list[dict]], manifest: dict,
          when: str) -> dict:
    """Folds this review's harvest into the standing ledger.

    An entry already there has its `last_seen` moved and its sighting count raised; a new
    one starts at one sighting and `status: candidate`. Nothing is ever deleted here —
    staleness is a reading of `last_seen`, and deleting on absence would erase a
    convention merely because this delivery did not touch that corner of the repository.
    """
    ledger.setdefault("repo", manifest.get("repo"))
    ledger.setdefault("entries", [])
    ledger["updated_at"] = when
    ledger["reviews"] = ledger.get("reviews", 0) + 1
    ledger["redacted"] = ledger.get("redacted", 0)

    by_key = {e["key"]: e for e in ledger["entries"]}
    for kind, items in found.items():
        for item in items:
            text, redacted = sanitise(item.get("text") or "", manifest)
            text = text.strip()
            if not text:
                continue
            ledger["redacted"] += redacted
            key = _key(kind, text)
            entry = by_key.get(key)
            if entry is None:
                entry = {"kind": kind, "key": key, "statement": text,
                         "where": [], "first_seen": when, "last_seen": when,
                         "reviews": 0, "status": "candidate"}
                ledger["entries"].append(entry)
                by_key[key] = entry
            entry["last_seen"] = when
            entry["reviews"] += 1
            for where in item.get("where") or []:
                w, _ = sanitise(str(where), manifest)
                w = portable_where(w, manifest.get("repo"))
                if w and w not in entry["where"]:
                    entry["where"].append(w)
            for extra in ("owner", "severity", "weight"):
                if item.get(extra) is not None:
                    entry[extra] = item[extra]
    return ledger


def portable_where(where: str, repo: str | None) -> str | None:
    """Where to look, as a fact of the REPOSITORY. A path inside a review package — a
    slice, the record — is that review's scaffolding: measured on 2026-11-19, the reading
    of one repository pointed at `<packages>/<another review>/slices/complete.patch`, which
    handed to a reviewer is an address into another review, the one thing a review must
    never read. It is dropped. A path under the repository's own checkout becomes relative
    to it, so the same file is one entry whether a reviewer wrote it whole or not."""
    w = where.strip()
    if not w:
        return None
    try:
        packages = dir_packages()
    except Exception:  # an installation with no packages root still has repositories
        packages = None
    # Whole or relative to wherever it was written from: `.prumo/packages/<run>/…` came
    # in both shapes. The root's last two parts are matched anywhere in the path.
    marks = [f"{packages}/", "/".join(packages.parts[-2:]) + "/"] if packages else []
    if any(m in w for m in marks) or w.startswith("slices/") or "/slices/" in w:
        return None
    if repo:
        try:
            root = f"{dir_repositories() / repo}/"
        except Exception:
            root = None
        if root and w.startswith(root):
            w = w[len(root):]
        # A worktree the review read the head in (3.7.0) folds the same way.
        w = re.sub(r"^\.worktrees/[^/]+/", "", w)
    return w


# ----------------------------------------------------------------- distilling

HEADINGS = {
    "convention": "What this repository does, as the code shows it",
    "hot_file": "Where the reviews keep having to look",
    "project_debt": "What this repository owes itself",
    "preexisting": "Defects no delivery caused",
}


def distil(ledger: dict) -> str:
    """The agent-facing reading. No identifier, no date, no finding id, no verdict.

    Dates stay in the ledger: a date beside a statement invites an agent to reason about
    WHEN, which is one step from reasoning about WHICH delivery — the thing this file
    must never do. What reaches the reading is the statement and where to look.
    """
    out = [f"# {ledger.get('repo')} — what the reviews have learned",
           "",
           "Accrued by the review harness across "
           f"{ledger.get('reviews', 0)} review(s). **Nothing here has been confirmed by a "
           "person, and nothing here decides a verdict.** It is a shortcut for reading, "
           "not an authority: check it against the code before relying on it, and when "
           "an entry proves itself, graduate it into the repository's profile.",
           ""]
    entries = ledger.get("entries") or []
    # A hot file accrued before 2026-11-19 may be a package path in its own statement.
    entries = [e for e in entries
               if e["kind"] != "hot_file" or portable_where(e["statement"], ledger.get("repo"))]
    for kind in KINDS:
        mine = [e for e in entries if e["kind"] == kind and e["reviews"] >= CORROBORATED]
        once = [e for e in entries if e["kind"] == kind and e["reviews"] < CORROBORATED]
        if not mine and not once:
            continue
        out.append(f"## {HEADINGS[kind]}")
        out.append("")
        for e in sorted(mine, key=lambda x: -x["reviews"]):
            # Entries accrued before 2026-11-19 may still carry a package path.
            places = list(dict.fromkeys(filter(None, (portable_where(w, ledger.get("repo"))
                                                      for w in e["where"]))))
            where = f" — `{'`, `'.join(places[:3])}`" if places else ""
            owner = f" (owner: {e['owner']})" if e.get("owner") else ""
            out.append(f"- {e['statement']}{where}{owner}")
        if once:
            # Seen once is not the same as established, and collapsing the two would let a
            # single reviewer's guess read as the repository's shape.
            out.append(f"- _{len(once)} further observation(s) seen in only one review, "
                       "held back until a second review sees them too._")
        out.append("")
    if ledger.get("redacted"):
        out.append(f"_{ledger['redacted']} delivery identifier(s) were redacted on the way "
                   "in: this file must stay true of the repository, not of one change._")
        out.append("")
    return "\n".join(out)


# ----------------------------------------------------------------- entry point

def accrue(package: pathlib.Path, conventions_file: str | None,
           output: pathlib.Path | None = None) -> dict:
    manifest = read_json(package / "manifest.json")
    repo = manifest.get("repo") or "unknown"
    root = output or dir_knowledge()
    root.mkdir(parents=True, exist_ok=True)

    conventions = None
    if conventions_file and pathlib.Path(conventions_file).is_file():
        conventions = read_json(pathlib.Path(conventions_file))

    ledger_path, reading_path = root / f"{repo}.json", root / f"{repo}.md"
    ledger = read_json(ledger_path) if ledger_path.exists() else {}
    found = harvest(package, conventions)
    ledger = merge(ledger, found, manifest, today())
    write_json(ledger_path, ledger)
    reading_path.write_text(distil(ledger), encoding="utf-8")
    # What THIS review brought, by kind — so "0 entries over 4 reviews" reads as what it
    # is. Measured on four reviews of one delivery: the reviewers at `minimal` effort
    # opened no repository file, returned no convention, and the package held no
    # observation and no pending route. Zero was the measurement, not a defect; without
    # this line it looked like one.
    return {"repo": repo, "entries": len(ledger["entries"]),
            "reviews": ledger["reviews"], "reading": str(reading_path),
            "harvested": {kind: len(items) for kind, items in found.items()}}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--package", required=True)
    p.add_argument("--conventions", help="what the reviewers observed, if they returned any")
    p.add_argument("--output", help="knowledge root; defaults to the configured one")
    args = p.parse_args()
    result = accrue(pathlib.Path(args.package), args.conventions,
                    pathlib.Path(args.output) if args.output else None)
    print(f"knowledge · {result['repo']}: {result['entries']} entries over "
          f"{result['reviews']} review(s) → {result['reading']}")
    print("  this review harvested: " + " · ".join(
        f"{kind} {n}" for kind, n in result["harvested"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
