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
from common import dir_knowledge, read_json, write_json, today
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
                seen_here.add(str(path))
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
                if w not in entry["where"]:
                    entry["where"].append(w)
            for extra in ("owner", "severity", "weight"):
                if item.get(extra) is not None:
                    entry[extra] = item[extra]
    return ledger


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
    for kind in KINDS:
        mine = [e for e in entries if e["kind"] == kind and e["reviews"] >= CORROBORATED]
        once = [e for e in entries if e["kind"] == kind and e["reviews"] < CORROBORATED]
        if not mine and not once:
            continue
        out.append(f"## {HEADINGS[kind]}")
        out.append("")
        for e in sorted(mine, key=lambda x: -x["reviews"]):
            where = f" — `{'`, `'.join(e['where'][:3])}`" if e["where"] else ""
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
    ledger = merge(ledger, harvest(package, conventions), manifest, today())
    write_json(ledger_path, ledger)
    reading_path.write_text(distil(ledger), encoding="utf-8")
    return {"repo": repo, "entries": len(ledger["entries"]),
            "reviews": ledger["reviews"], "reading": str(reading_path)}


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
