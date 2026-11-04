#!/usr/bin/env python3
"""Route-closure parser. Structured text in a comment on the item, with no LLM.

    #evidence B4   <result obtained> [link]
    #decision A2   <door chosen>, decided by <person>
    #waiver SEC-3  <reason>, approver <person>, expires YYYY-MM-DD

The `missing_profile` and `missing_capability` routes do NOT close here, on purpose:
what holds for every future review of a repository closes by a proposal against the
profile, which goes through peer review.

Usage:
    closure.py --comments FILE [--package DIR]
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import valid_date, write_json
from i18n import t

# The marker kinds ARE the canonical values: there is no normalisation table any more.
# The Portuguese version needed one because `evidencia` and `evidência` are the same
# marker typed two ways; English spares us the accent-folding.
RE_MARKER = re.compile(
    r"#(?P<kind>evidence|decision|waiver)\s+(?P<item>[A-Za-z0-9_-]+)\s*(?P<rest>.*)",
    re.I)
# `decided by` comes before the bare `by` on purpose: alternation is first-match, and the
# bare one would otherwise swallow the longer form and capture the wrong word.
RE_APPROVER = re.compile(r"(?:approver|approved by|decided by|by)\s*[:=]?\s*(@?[\w.\-]+)",
                         re.I)
RE_EXPIRES = re.compile(r"(?:expires|expiry|until)\s*[:=]?\s*(\d{4}-\d{2}-\d{2})", re.I)


def parse(text: str) -> tuple[list[dict], list[str]]:
    """Returns (resolutions, refusals). A malformed marker is REFUSED, never half-taken."""
    resolutions, refusals = [], []
    for line in (text or "").splitlines():
        m = RE_MARKER.search(line)
        if not m:
            continue
        kind = m.group("kind").lower()
        item, rest = m.group("item"), (m.group("rest") or "").strip()
        if not rest:
            refusals.append(t("closure.missing_content", kind=kind, item=item))
            continue
        r = {"type": kind, "item": item, "text": rest, "line": line.strip()}
        if kind == "waiver":
            approver = RE_APPROVER.search(rest)
            expires = RE_EXPIRES.search(rest)
            if not approver or not expires:
                refusals.append(t("closure.waiver_incomplete", item=item))
                continue
            if not valid_date(expires.group(1)):
                refusals.append(t("closure.waiver_bad_date", item=item))
                continue
            r["waiver"] = {"reason": rest, "approver": approver.group(1),
                           "expires": expires.group(1)}
        if kind == "decision":
            who = RE_APPROVER.search(rest)
            if not who:
                refusals.append(t("closure.decision_without_decider", item=item))
                continue
            r["decided_by"] = who.group(1)
        resolutions.append(r)
    return resolutions, refusals


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--comments", required=True)
    p.add_argument("--package")
    args = p.parse_args()

    text = pathlib.Path(args.comments).read_text(encoding="utf-8")
    resolutions, refusals = parse(text)
    if args.package:
        write_json(pathlib.Path(args.package) / "resolutions.json", resolutions)
    for r in resolutions:
        print(t("closure.accepted", kind=f"{r['type']:10}", item=r["item"]))
    for x in refusals:
        print(t("closure.refused", reason=x))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
