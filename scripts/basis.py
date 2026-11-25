#!/usr/bin/env python3
"""Where a finding's demand comes from, checked against the files.

A reviewer says which criterion of the item a finding breaks, or quotes the project's
own rule. Both are claims a model made, and both are looked up here: a criterion id the
item never had is dropped, and a quote is searched for in the file it names. A quote that
is not there is a rule nobody wrote — the judge then charges nothing for it.
"""
from __future__ import annotations

import pathlib
import re

QUOTE_FLOOR = 12


def _plain(text: str) -> str:
    """Lower case, with whitespace, emphasis marks and quotation marks taken out: a
    quote copied from rendered prose still matches its source."""
    return re.sub(r"[\s*_`\"'“”‘’«»]+", "", text or "").lower()


def quote_found(rule: dict, roots: list[pathlib.Path]) -> bool:
    """Whether `rule.quote` sits in `rule.file`, looked for under each root in turn."""
    quote = _plain(rule.get("quote"))
    name = str(rule.get("file") or "").lstrip("/")
    if len(quote) < QUOTE_FLOOR or not name:
        return False
    for root in roots:
        path = root / name
        if path.is_file() and quote in _plain(path.read_text(encoding="utf-8", errors="ignore")):
            return True
    return False


def ground(findings: list[dict], criteria_ids: set[str], roots: list[pathlib.Path]) -> dict:
    """Marks each finding's rule `verified` and drops a criterion id the item never had.
    Returns what was not found, for the caller to say."""
    unknown, unquoted = [], []
    for f in findings:
        if f.get("criterion") and f["criterion"] not in criteria_ids:
            unknown.append(f"{f['id']} → {f['criterion']}")
            f["criterion"] = None
        rule = f.get("rule")
        if isinstance(rule, dict):
            rule["verified"] = quote_found(rule, roots)
            if not rule["verified"]:
                unquoted.append(f"{f['id']} → {rule.get('file')}")
    return {"unknown_criteria": unknown, "rules_not_found": unquoted}
