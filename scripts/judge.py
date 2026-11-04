#!/usr/bin/env python3
"""The judge: a function over validated JSON. Same input, same verdict.

It reads no package, calls no model and knows no repository. Text planted in a proposal
or in an item does not move the result, because nothing here reads prose — only states,
severities and resolutions.

The verdict is two-valued, as it has always been. What changed is that INTERVENTION
REQUIRED comes out typed into routes, each with an owner and with the event that closes
it.

Usage:
    judge.py --package DIR [--output DIR]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import (BLOCKING_SEVERITIES, forge, read_json, tracker, waiver_in_force,
                    write_json)
from i18n import t

APPROVED = "APPROVED"
INTERVENTION = "INTERVENTION_REQUIRED"


def _words() -> dict:
    """Both forms of every vocabulary word, on every message. English wants the bare
    `MR`; a language with grammatical gender wants `a MR` — and only the catalogue knows
    which of the two its own sentence needs. Sending one form alone is how a report ends
    up saying "the a MR"."""
    return {"proposal": forge().proposal,
            "proposal_with_article": forge().with_article(),
            "item": tracker().item,
            "item_with_article": tracker().with_article()}


# Every route has an owner and a closing event. That is what turns a verdict into an
# actionable request instead of a label. Both texts are catalogue keys named after the
# route itself — `verdict.routes.<route>` and `verdict.closes.<route>`.
SUITE_ROUTE = {
    "red_attributable":     "author_fix",
    # A red the delivery did not cause is not the author's debt: it is the repository's,
    # and it enters the report through the summary, not through the verdict. Decided on
    # 2026-10-29, over a repository whose base carries credential reds: they travel with
    # the base branch and would make every APPROVED impossible there. Which repository
    # that is, the profile says — this file does not know, and must not.
    "red_not_attributable": None,
    # Nothing executed at all: no red is attributable because no test ran. A repository
    # where the suite cannot run proves nothing about any delivery, so this one holds the
    # proposal even though the delivery did not cause it. A suite that runs in parts —
    # one selection at a time, the default invocation aborting on stale configuration —
    # is NOT this: that is `red_not_attributable` plus a note. Decided on 2026-11-04.
    "broken":               "author_fix",
    "not_run":           "manual_check",
    "green":                   None,
}

# The suite states that hold a proposal. Everything else about the suite is an
# annotation: it is named in the report, with an owner when it has one, and the verdict
# goes on being decided by whether the item's ask was delivered.
SUITE_BLOCKING = {"red_attributable", "broken"}

# --- THE RULE -----------------------------------------------------------------------
#
#   APPROVED  ⟺  1. the code follows the project's standards and good practice
#                2. it does not break a business rule
#                3. it covers what the item asked for, with the necessary evidence
#                4. no manual verification is left to do
#                5. and the four above were actually MEASURED
#
# Any gate that fails is INTERVENTION REQUIRED. The fifth gate exists because "not
# assessed" is not "no risk": an APPROVED over ground nobody measured is the one output
# of this method that is worth less than no review at all.
#
# Each route belongs to exactly one gate. The route is still what makes the verdict
# actionable — who does what, and what closes it. The gate is what makes THE RULE
# legible. Each gate's condition is a catalogue key: `verdict.gates.<name>`.
GATES = {
    1: "standards",
    2: "business_rules",
    3: "item_covered",
    4: "no_manual",
    5: "measured",
}

ROUTE_GATE = {
    "author_fix":         1,
    "confirm_finding":    1,
    "human_decision":     2,
    "manual_check":       4,
    "missing_profile":    5,
    "missing_capability": 5,
    "review_incomplete":  5,
}

# --- WHOSE DEBT IT IS ----------------------------------------------------------------
#
# The review judges ONE question: is what the item asked for delivered in this proposal?
# Something pending that the delivery did not cause — a profile nobody confirmed, a
# class with no anchor, a tool this environment lacks — is the PROJECT's debt. It is
# declared, never deleted, but it closes no gate and becomes no INTERVENTION: otherwise a
# repository whose profile nobody finished would never approve any delivery at all, and
# the verdict would stop telling a good delivery from a bad one.
#
# It is the same principle that already governs `attributable` on a finding and
# `vermelha_nao_atribuivel` on the suite, now applied to the route. Decided 2026-11-03.
#
# What remains the DELIVERY's debt, and goes on blocking:
#   * an attributable, blocking finding                     (gate 1)
#   * a §A class fired over what the diff changed           (gate 2)
#   * a criterion of the item unmet, or with no record      (gate 3)
#   * a §B class fired                                      (gate 4)
#   * mandatory reading empty — with no item there is       (gate 5)
#     nothing to judge
ORIGIN_DELIVERY = "delivery"
ORIGIN_PROJECT = "project"

ROUTE_ORIGIN = {
    "missing_profile":    ORIGIN_PROJECT,
    "missing_capability": ORIGIN_PROJECT,
}


def judge_delivery(profile: dict, classes: list[dict], findings: list[dict],
                   criteria: list[dict], evidence: dict, scope: dict,
                   resolutions: list[dict], today: str | None = None) -> dict:
    """Verdict and routes. Deterministic: it reads no package, and depends on no clock
    beyond the `today` it is handed, which only serves to measure whether a waiver has
    expired. The catalogue and the installation's vocabulary are read for the WORDS of a
    route; they never move the decision."""
    routes: list[dict] = []

    resolved = {r["item"] for r in (resolutions or [])
                if r.get("type") != "waiver" or waiver_in_force(r.get("waiver"), today)}

    by_gate: dict[int, list[str]] = {n: [] for n in GATES}
    project_pending: list[str] = []

    def route(kind: str, items: list[str], detail: dict | None = None,
              gate: int | None = None, origin: str | None = None):
        """Accumulates into the route of that kind, never opens a second one. A person
        reads a single block.

        `gate` is which of the five conditions of THE RULE this item closes. Two routes
        serve more than one gate — `author_fix` takes both a finding (gate 1) and an
        unmet criterion (gate 3) — so the gate is declared by the caller, not deduced
        from the kind of route.

        `origin` says whose debt it is. `project` is declared and closes no gate; one
        kind of route serves both origins — `manual_check` takes both a §B class
        (delivery) and a suite the profile cannot run (project) — so the origin is also
        declared by the caller whenever it departs from the kind's default.
        """
        items = [i for i in items if i not in resolved]
        if not items:
            return
        origin = origin or ROUTE_ORIGIN.get(kind, ORIGIN_DELIVERY)
        if origin == ORIGIN_DELIVERY:
            by_gate[gate if gate is not None else ROUTE_GATE[kind]] += items
        else:
            project_pending.extend(items)
        existing = next((r for r in routes
                         if r["type"] == kind and r["origin"] == origin), None)
        if existing is None:
            existing = {"type": kind, "origin": origin, "items": [],
                        "owner": t(f"verdict.routes.{kind}", **_words()),
                        "closes_when": t(f"verdict.closes.{kind}", **_words()),
                        "details": []}
            routes.append(existing)
        existing["items"] = sorted(set(existing["items"]) | set(items))
        if detail and detail.get("detail"):
            existing["details"].append(detail["detail"])

    # --- the profile. Draft or absent does not stop the review; it stops APPROVING.
    if (profile.get("status") or "absent") != "confirmed":
        route("missing_profile", [profile.get("profile", t("verdict.unknown_repo"))],
              {"detail": t("verdict.detail_profile",
                           status=profile.get("status") or "absent", **_words())})

    live = lambda c: (not c.get("not_applicable")
                      and not waiver_in_force(c.get("waiver"), today))

    route("human_decision",
          [c["id"] for c in classes
           if c["section"] == "A" and c["state"] == "fired" and live(c)])
    route("manual_check",
          [c["id"] for c in classes
           if c["section"] == "B" and c["state"] == "fired" and live(c)])
    route("missing_capability",
          [c["id"] for c in classes if c["state"] == "not_assessable" and live(c)])

    # --- findings. A refuted one disappears; an uncertain one goes to a person, not to
    #     the author; and a finding the change did not cause is not the debt of whoever
    #     wrote this proposal.
    severe = lambda f: f.get("severity") in BLOCKING_SEVERITIES
    blocks = lambda f: severe(f) and f.get("attributable", True)
    route("author_fix",
          [f["id"] for f in findings if blocks(f) and f.get("status") == "confirmed"])
    route("confirm_finding",
          [f["id"] for f in findings if blocks(f) and f.get("status") == "uncertain"])
    non_attributable = [f["id"] for f in findings
                        if severe(f) and not f.get("attributable", True)
                        and f.get("status") != "refuted"]

    # --- criteria of the item
    route("author_fix", [c["id"] for c in criteria if c.get("state") == "not_met"],
          {"detail": t("verdict.detail_criterion_unmet", **_words())}, gate=3)
    # A criterion the SUITE decides is decided by the suite's state, never by the
    # absence of a human record: charging it again at gate 3 would make every repository
    # with a red base, or with no runner in this environment, unapprovable — which is the
    # double charge the suite rule already refuses. It is the criterion's own `decided_by`
    # that says so, because only whoever read the item knows what the criterion asks of.
    suite_blocks = evidence.get("suite") in SUITE_BLOCKING
    route("manual_check",
          [c["id"] for c in criteria
           if c.get("state") == "not_verifiable" and not c.get("record")
           and not (c.get("decided_by") == "suite" and not suite_blocks)],
          {"detail": t("verdict.detail_criterion_unrecorded", **_words())}, gate=3)

    # --- what the delivery proves about itself
    #
    # Delivered behaviour that no test covers holds the proposal: the proof is part of
    # what the item asked for, not a favour. The one exception is an item whose whole
    # subject IS the proof — a coverage or technical-debt item, which delivers tests over
    # code that was already there and would otherwise be charged for the absence it was
    # opened to fix. Whoever read the item says which case it is. Decided on 2026-11-04.
    if evidence.get("delivery_untested") and not evidence.get("item_is_coverage_debt"):
        route("author_fix", ["delivery_untested"],
              {"detail": t("verdict.detail_delivery_untested", **_words())}, gate=3)

    # --- mechanical evidence
    destination = SUITE_ROUTE.get(evidence.get("suite"))
    if destination:
        # A suite red BY FAULT of the delivery is the delivery's debt. A suite this
        # environment did not know how to run is the project's: the delivery did not
        # cause the missing tool. The criterion of the item that depends on it is still
        # charged at gate 3 — what disappears is the double charge, not the charge.
        suite_origin = ORIGIN_DELIVERY if suite_blocks else ORIGIN_PROJECT
        route(destination, ["suite"],
              {"detail": t("verdict.detail_suite", value=evidence["suite"], **_words())},
              gate=3 if suite_blocks else None, origin=suite_origin)
    # Being BEHIND the base and CONFLICTING with it are different facts, and only the
    # second one holds the proposal. A branch a few commits behind that merges cleanly was
    # measured against code identical to what it will meet; one that conflicts was not, and
    # nobody can say what the review proved. Decided on 2026-11-04: staleness is an
    # annotation in the summary, a conflict is gate 1.
    if evidence.get("conflicts") is True:
        route("author_fix", ["conflict"],
              {"detail": t("verdict.detail_conflict",
                           files=", ".join(evidence.get("conflicted_files") or []) or "—",
                           **_words())}, gate=1)
    if evidence.get("mandatory_reading_empty"):
        route("manual_check", ["mandatory_reading"],
              {"detail": t("verdict.detail_mandatory_reading", **_words())}, gate=5)

    # A reviewer the triage ACTIVATED and that never answered leaves a slice nobody
    # read. Whose debt is it? Not the delivery's — the author did not break the agent —
    # and not the repository's. It is the REVIEW's own, and it closes gate 5 anyway,
    # for the same reason empty mandatory reading does: whose fault it was does not
    # change that there is nothing to judge with. "Approve on its own when it could not
    # assess" is the one thing this method never does, and until 2026-11-04 it did
    # exactly that whenever an agent ran out of turns — silently, because a reviewer
    # that returns nothing is indistinguishable from a reviewer that found nothing.
    if evidence.get("reviewers_without_return"):
        route("review_incomplete",
              sorted(evidence.get("slices_unread") or ["review"]),
              {"detail": t("verdict.detail_review_incomplete",
                           n=evidence["reviewers_without_return"], **_words())}, gate=5)

    route("author_fix", list(scope.get("out_of_scope_touched") or []),
          {"detail": t("verdict.detail_out_of_scope", **_words())}, gate=3)

    gates = [{"number": n, "name": GATES[n],
              "condition": t(f"verdict.gates.{GATES[n]}", **_words()),
              "passed": not by_gate[n], "items": sorted(set(by_gate[n]))}
             for n in sorted(GATES)]

    # Only the DELIVERY's debt decides the verdict. The project's is declared and goes to
    # whoever maintains the repository, without holding a proposal that delivered what
    # the item asked for.
    delivery_routes = [r for r in routes if r["origin"] == ORIGIN_DELIVERY]

    return {
        "verdict": INTERVENTION if delivery_routes else APPROVED,
        # THE RULE, legible before any route: five gates, and all of them must pass.
        "gates": gates,
        "routes": routes,
        "summary": {
            "repo_baseline": (
                evidence.get("suite_reason") or "")
                if evidence.get("suite") == "red_not_attributable" else None,
            # Measured and declared, charged to nobody: the branch is behind, and the
            # merge either resolves, does not, or could not be measured here.
            "stale_base": (
                t("verdict.stale_base",
                  n=evidence.get("commits_behind_base") or 0,
                  merge=(t("verdict.stale_conflicts") if evidence.get("conflicts") is True
                         else t("verdict.stale_clean") if evidence.get("conflicts") is False
                         else t("verdict.stale_unmeasured",
                                reason=evidence.get("conflicts_reason") or "")))
                if evidence.get("behind_base") else None),
            "classes_fired": [c["id"] for c in classes if c["state"] == "fired"],
            "classes_did_not_fire": [c["id"] for c in classes if c["state"] == "did_not_fire"],
            "classes_not_assessable": [c["id"] for c in classes if c["state"] == "not_assessable"],
            "profile_coverage": f"{len([c for c in classes if c['state'] != 'not_assessable'])}/18",
            "blocking_findings": len([f for f in findings if blocks(f)]),
            "refuted_findings": len([f for f in findings if f.get("status") == "refuted"]),
            "non_attributable_findings": non_attributable,
            # Declared, never deleted: what this environment could not measure is still
            # said, it just is not charged to whoever wrote the delivery.
            "project_pending": sorted(set(project_pending)),
        },
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--package", required=True)
    p.add_argument("--output")
    args = p.parse_args()

    package = pathlib.Path(args.package)
    output = pathlib.Path(args.output or package)
    optional = lambda name, empty: (read_json(package / name)
                                    if (package / name).exists() else empty)

    from common import load_profile
    verdict = judge_delivery(
        profile=load_profile(read_json(package / "manifest.json")["repo"]),
        classes=read_json(package / "classes.json"),
        findings=optional("findings.json", []),
        criteria=optional("criteria.json", []),
        evidence=optional("evidence.json", {}),
        scope=optional("scope.json", {}),
        resolutions=optional("resolutions.json", []),
    )
    write_json(output / "verdict.json", verdict)
    print(verdict["verdict"])
    for r in verdict["routes"]:
        if r["origin"] != ORIGIN_DELIVERY:
            continue
        print(f"  {r['type']:20} {', '.join(r['items'])}  → {r['owner']}")
    project_routes = [r for r in verdict["routes"] if r["origin"] == ORIGIN_PROJECT]
    if project_routes:
        print("  " + t("verdict.project_pending", **_words()))
        for r in project_routes:
            print(f"  {r['type']:20} {', '.join(r['items'])}  → {r['owner']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
