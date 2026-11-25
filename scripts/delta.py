#!/usr/bin/env python3
"""Which round of review this is, and what a later round reads.

  * round 1 — `full`: the whole delivery, every reviewer the triage activates.
  * round 2 — `delta`: the reviewers read only what the push changed since the head the
    first round reviewed, and what that round left open is re-checked at the new head.
  * round 3 on — `recheck`: no reviewer runs. The only question left is whether what is
    open was fixed, so a review converges instead of finding anew on every pass.

A finding a later round raises on code the push did not touch is shown and holds
nothing: that code was read once already, and a second reading that disagrees with the
first is the harness varying, not the delivery changing. `prepare --full` asks for a
whole review again, at any round.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import BLOCKING_SEVERITIES, dir_reports, read_json
from i18n import t

FULL, DELTA, RECHECK = "full", "delta", "recheck"
CARRIED = ("criteria.json", "out-of-scope.json", "resolutions.json", "scripts_manual.json")


def history(repo: str, origin: str) -> list[dict]:
    """The reviews already concluded over this origin, oldest first."""
    runs = dir_reports() / "runs.jsonl"
    if not runs.exists():
        return []
    out = []
    for raw in runs.read_text(encoding="utf-8").splitlines():
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if line.get("repo") == repo and line.get("origin") == origin:
            out.append(line)
    return out


def plan_round(repo_dir: pathlib.Path, repo: str, origin: str, head: str,
               full: bool = False) -> dict:
    """The round this review is, from what was concluded before it. `full` whenever the
    earlier round cannot be built on — it left no package, or its head is gone."""
    earlier = history(repo, origin)
    round_ = {"n": len(earlier) + 1, "mode": FULL}
    if not earlier:
        return round_
    if full:
        return {**round_, "reason": t("delta.full_asked")}
    last = earlier[-1]
    previous = pathlib.Path(last.get("package") or "")
    if not last.get("package") or not (previous / "findings.json").exists() \
            or not (previous / "verdict.json").exists():
        return {**round_, "reason": t("delta.no_package")}
    since = last.get("head_sha") or ""
    known = subprocess.run(("git", "-C", str(repo_dir), "cat-file", "-e", f"{since}^{{commit}}"),
                           capture_output=True, text=True).returncode == 0
    if not since or not known:
        return {**round_, "reason": t("delta.head_gone", sha=since[:12])}
    return {"n": round_["n"], "mode": DELTA if len(earlier) == 1 else RECHECK,
            "since": since, "same_head": since == head,
            "previous_package": str(previous)}


def carry(package: pathlib.Path, round_: dict) -> dict:
    """What the earlier round settled, brought into this package, and what it left open,
    returned for the workflow: the findings that held a gate, and — for a round with no
    reviewer — the criteria it did not read as met."""
    previous = pathlib.Path(round_["previous_package"])
    for name in CARRIED:
        if (previous / name).exists() and not (package / name).exists():
            shutil.copy2(previous / name, package / name)
    findings = read_json(previous / "findings.json") or []
    verdict = read_json(previous / "verdict.json")
    held = {i for g in verdict.get("gates") or [] if not g.get("passed") for i in g.get("items") or []}
    taken: dict[str, int] = {}
    for f in findings:
        m = re.match(r"^([A-Z0-9]+)-(\d+)$", str(f.get("id") or ""))
        if m:
            taken[m.group(1)] = max(taken.get(m.group(1), 0), int(m.group(2)))
    criteria = read_json(package / "criteria.json") if (package / "criteria.json").exists() else []
    if isinstance(criteria, dict):
        criteria = criteria.get("criteria") or []
    scripts = package / "scripts_manual.json"
    return {
        "open": [f for f in findings if f.get("id") in held],
        "open_criteria": [{"id": c["id"], "text": c.get("text"), "record": c.get("record"),
                           "note": c.get("note")}
                          for c in criteria
                          if c.get("state") != "met" and c.get("decided_by") not in ("qa", "suite")],
        "scripts_carried": scripts.exists() and bool(read_json(scripts)),
        "taken": taken,
    }


def _plain(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def changed(patch: str) -> dict[str, dict]:
    """Per file of a patch: the new-side line numbers its hunks cover, and the text it
    adds with the whitespace taken out — the two ways a finding points at a line."""
    out: dict[str, dict] = {}
    current, line = None, 0
    for raw in patch.splitlines():
        if raw.startswith("+++ "):
            name = raw[4:].strip()
            current = None if name == "/dev/null" else out.setdefault(
                name[2:] if name.startswith("b/") else name, {"lines": set(), "added": []})
            continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line = int(m.group(1)) if m else 0
            continue
        if current is None or raw.startswith(("diff ", "index ", "--- ")):
            continue
        if raw.startswith(("-", "\\")):
            continue
        current["lines"].add(line)
        if raw.startswith("+"):
            current["added"].append(_plain(raw[1:]))
        line += 1
    return {name: {"lines": c["lines"], "added": "".join(c["added"])} for name, c in out.items()}


def on_the_push(finding: dict, touched: dict[str, dict]) -> bool:
    """Whether any evidence of the finding points at something the push changed: a line
    inside one of its hunks, or an excerpt the push added."""
    for e in finding.get("evidence") or []:
        name = str(e.get("file") or "")
        hit = next((c for f, c in touched.items() if name == f or name.endswith("/" + f)
                    or f.endswith("/" + name)), None)
        if hit is None:
            continue
        excerpt = _plain(e.get("excerpt") or e.get("snippet") or "")
        if e.get("line") in hit["lines"] or (len(excerpt) >= 8 and excerpt in hit["added"]):
            return True
    return False


def mark_already_reviewed(findings: list[dict], patch: str) -> list[str]:
    """On a later round, flags each blocking finding this round raised that lands only on
    code the push did not change. Returns the ids flagged."""
    touched = changed(patch)
    flagged = []
    for f in findings:
        if f.get("carried_from") or f.get("severity") not in BLOCKING_SEVERITIES:
            continue
        if not on_the_push(f, touched):
            f["already_reviewed"] = True
            flagged.append(f["id"])
    return flagged
