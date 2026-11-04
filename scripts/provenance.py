#!/usr/bin/env python3
"""Does this text speak about THIS review, or about somebody else's?

A review answers one question about one delivery, and it must relate to nothing but its
own item and its own proposal. A review done earlier must not bias a new one.

That is not a style preference. The agents hold `Read`, `Grep` and `Glob` over the whole
workspace, and the reports root is in that workspace: on a repository that already holds
a report from a sibling review, a planner asked for "a manual script per class" anchored
on the neighbouring report and wrote fifteen scripts for the previous item's SHAs. Every
line was plausible and none was about the delivery under review — the worst output this
method can give, because the reader cannot tell it from the real thing.

The first fix was a sentence in the prompt telling the agent not to do that. This module
exists because that is not a guarantee: the same rule that governs escaping governs this
one — **it is done in code, never by instructing a model.**

What counts as foreign, and why each rule is shaped the way it is:

  * **a commit sha.** Unambiguous. Eight to forty hex characters with at least one letter
    (a pure-digit run is a number, not a sha), and foreign when it is not a prefix of —
    nor prefixed by — any of this review's three: head, base and merge base. This is the
    signal that caught the real contamination.
  * **a proposal number.** Read from the origin ref's own shape, so it follows the forge
    the installation declared instead of assuming one.
  * **an item id that this review's own item never mentions.** The asymmetry matters: an
    item may legitimately name a sibling — "follows the test pattern of card 50273" is a
    sentence card 50275 really contains — and flagging that would make the check cry wolf
    on the item's own words. An id that appears in the OUTPUT and nowhere in the item came
    from somewhere else.

Usage:
    provenance.py --package DIR FILE...
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import read_json
from i18n import t

# Eight is the floor on purpose: shorter runs collide with ordinary hex in code, and git
# itself needs about that many to stay unambiguous in a real repository.
RE_SHA = re.compile(r"\b(?=[0-9a-f]*[a-f])[0-9a-f]{8,40}\b")


def _own_shas(manifest: dict) -> set[str]:
    return {s for s in (manifest.get("head_sha"), manifest.get("merge_base"),
                        (manifest.get("base") or {}).get("sha")) if s}


def _is_own(sha: str, own: set[str]) -> bool:
    """A sha matches one of ours when either is a prefix of the other — the diff prints
    twelve characters, the manifest keeps forty, and both name the same commit."""
    return any(o.startswith(sha) or sha.startswith(o) for o in own)


def _proposal_number(origin: str) -> str | None:
    """The number inside the origin ref, whatever shape this forge publishes it in.

    The shape is read, never assumed: the resolver already wrote the real ref into the
    manifest, and the presets in `providers.py` are the only place that knows what each
    forge calls its proposals."""
    m = re.search(r"refs/[\w-]+/(\d+)/", origin or "")
    return m.group(1) if m else None


def _proposal_shape(origin: str) -> re.Pattern | None:
    """The same ref path, with the number left open — so a sibling's ref is recognised by
    the shape this installation actually uses and not by a guessed one."""
    n = _proposal_number(origin)
    if not n:
        return None
    return re.compile(re.escape(origin).replace(re.escape(n), r"(\d+)", 1))


def foreign_references(text: str, package: pathlib.Path) -> list[dict]:
    """Everything in `text` that belongs to another review. Empty means it speaks only
    about this one."""
    manifest = read_json(package / "manifest.json")
    own = _own_shas(manifest)
    out: list[dict] = []

    for sha in dict.fromkeys(RE_SHA.findall(text)):
        if not _is_own(sha, own):
            out.append({"kind": "sha", "value": sha})

    origin = manifest.get("origin") or ""
    mine, shape = _proposal_number(origin), _proposal_shape(origin)
    if shape:
        for n in dict.fromkeys(shape.findall(text)):
            if n != mine:
                out.append({"kind": "proposal", "value": n})

    item_id = (manifest.get("item") or {}).get("id")
    item_path = package / "item.md"
    if item_id and item_path.exists():
        # Only ids shaped like ours, and only those the item itself never names.
        said_by_the_item = item_path.read_text(encoding="utf-8")
        same_shape = re.compile(r"(?<![\w.])\d{%d}(?![\w.])" % len(item_id))
        for n in dict.fromkeys(same_shape.findall(text)):
            if n != item_id and n not in said_by_the_item:
                out.append({"kind": "item", "value": n})
    return out


def check(package: pathlib.Path, files: list[pathlib.Path]) -> list[str]:
    """One readable line per file that speaks about another review."""
    problems = []
    for f in files:
        if not f.exists():
            continue
        foreign = foreign_references(f.read_text(encoding="utf-8"), package)
        if foreign:
            problems.append(t("provenance.contaminated", file=f.name,
                              refs=", ".join(f"{r['kind']} {r['value']}" for r in foreign[:6])))
    return problems


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--package", required=True)
    p.add_argument("files", nargs="+")
    args = p.parse_args()
    problems = check(pathlib.Path(args.package), [pathlib.Path(f) for f in args.files])
    for line in problems:
        print(line)
    if problems:
        print(t("provenance.remedy"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
