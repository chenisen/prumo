#!/usr/bin/env python3
"""Delta review and re-judgement. No round re-reads what did not change.

Two loops, and they cost very different things:

  * re-judgement — evidence or a decision arrived. Runs resolver, judge, render.
    NO reviewer is spawned.
  * delta — a push arrived. Diff since the last reviewed SHA, plus the re-check of the
    findings that are still open.

Three cycles with the same blocking finding escalate to the tech lead: a loop with no
ceiling is not a review, it is attrition.

Usage:
    delta.py --package DIR --history FILE
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import write_json, read_json
from i18n import t

CYCLE_CEILING = 3


def decide(manifest: dict, history: list[dict], resolutions: list[dict]) -> dict:
    """Decides what the next round has to do. Pure."""
    previous = [h for h in history if h.get("repo") == manifest["repo"]
                and h.get("origin") == manifest["origin"]]
    last = previous[-1] if previous else None

    if last is None:
        return {"mode": "full", "reason": t("delta.first_round"),
                "since": None, "findings_to_recheck": [], "escalate": False}

    same_sha = last.get("head_sha") == manifest["head_sha"]
    arrived = {r["item"] for r in resolutions} - set(last.get("resolutions_seen") or [])

    if same_sha and arrived:
        return {"mode": "re-judgement",
                "reason": t("delta.resolutions_arrived", resolutions=sorted(arrived)),
                "since": last["head_sha"], "findings_to_recheck": [], "escalate": False}
    if same_sha:
        return {"mode": "re-judgement", "reason": t("delta.nothing_changed"),
                "since": last["head_sha"], "findings_to_recheck": [], "escalate": False}

    open_findings = [a for a in (last.get("open_blocking_findings") or [])
                     if a not in {r["item"] for r in resolutions}]
    cycles = {}
    for h in previous:
        for a in h.get("open_blocking_findings") or []:
            cycles[a] = cycles.get(a, 0) + 1
    stubborn = sorted(a for a in open_findings if cycles.get(a, 0) + 1 >= CYCLE_CEILING)

    return {"mode": "delta",
            "reason": t("delta.new_push", sha=last["head_sha"][:12]),
            "since": last["head_sha"], "findings_to_recheck": open_findings,
            "escalate": bool(stubborn), "stubborn": stubborn,
            "escalation_detail": t("delta.escalation", n=len(stubborn),
                                   cycle=CYCLE_CEILING, findings=stubborn)
                                if stubborn else None}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--package", required=True)
    p.add_argument("--history", required=True)
    args = p.parse_args()

    package = pathlib.Path(args.package)
    hist_path = pathlib.Path(args.history)
    history = [read_json(hist_path)] if hist_path.suffix == ".json" and hist_path.exists() else []
    if hist_path.suffix == ".jsonl" and hist_path.exists():
        import json
        history = [json.loads(l) for l in hist_path.read_text(encoding="utf-8").splitlines() if l.strip()]

    res = (package / "resolutions.json")
    plan = decide(read_json(package / "manifest.json"), history,
                  read_json(res) if res.exists() else [])
    write_json(package / "delta.json", plan)
    print(t("delta.line", mode=plan["mode"], reason=plan["reason"]))
    if plan.get("escalation_detail"):
        print(t("delta.escalate", detail=plan["escalation_detail"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
