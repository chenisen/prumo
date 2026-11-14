#!/usr/bin/env python3
"""Reads `runs.jsonl` and reports the harness's own metrics, per repository.

The metric none of the surveyed tools carries is profile coverage: how much of a
repository is in fact under review. Without it, "0 findings" is ambiguous.

Usage:
    metrics.py --runs FILE [--repo <repo>] [--json]

`--json` is the public, versioned form (METRICS_VERSION) a client reads instead of
importing this module.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from i18n import t

METRICS_VERSION = 1


def aggregate(runs: list[dict]) -> dict:
    # One bucket per repository AND capability: a delivery's tokens per line and a
    # review's are two different questions, and a median over both answers neither. A
    # line from before the second capability existed carries no `capability` and is a
    # review — the only thing the record could be then.
    by_repo = collections.defaultdict(list)
    for r in runs:
        capability = r.get("capability") or "task-review"
        repo = r.get("repo", "(no repo)")
        key = repo if capability == "task-review" else f"{repo} [{capability}]"
        by_repo[key].append(r)

    out = {}
    for repo, rs in sorted(by_repo.items()):
        verdicts = collections.Counter(r.get("verdict") for r in rs)
        # Precision only exists once A PERSON has closed some route. Before that the
        # numerator is not zero, it is unknown — and publishing 0.0 would make the
        # harness look useless at exactly the moment it has not been measured yet.
        judged = [r for r in rs if r.get("findings_confirmed_by_person") is not None]
        published = sum(r.get("published_findings", 0) for r in judged)
        confirmed = sum(r["findings_confirmed_by_person"] for r in judged)
        precision = round(confirmed / published, 3) if (judged and published) else None
        precision_basis = t("metrics.precision_basis", judged=len(judged), total=len(rs))
        tokens = [r["tokens"] for r in rs if isinstance(r.get("tokens"), (int, float))]
        # Cost per changed line: it is what compares two reviews of different sizes, and
        # what says whether a change to the harness actually made it cheaper or just
        # happened to catch a smaller proposal.
        per_line = [r["tokens"] / r["lines_changed"] for r in rs
                    if isinstance(r.get("tokens"), (int, float))
                    and isinstance(r.get("lines_changed"), (int, float))
                    and r["lines_changed"] > 0]
        # The same ratio, BY EFFORT LEVEL. The global median mixes a docs-only delivery
        # with a migration, so it cannot say whether proportional effort worked: the
        # claim under test is "minimal costs less per line than full", and only this
        # breakdown can confirm or refute it. Runs from before the effort dimension
        # carry no level and land under `(no effort)` — absence stays visible, it does
        # not contaminate a level.
        per_line_by_effort = collections.defaultdict(list)
        shapes = collections.Counter(r["shape"] for r in rs if r.get("shape"))
        for r in rs:
            if (isinstance(r.get("tokens"), (int, float))
                    and isinstance(r.get("lines_changed"), (int, float))
                    and r["lines_changed"] > 0):
                per_line_by_effort[r.get("effort") or "(no effort)"].append(
                    r["tokens"] / r["lines_changed"])
        # A delivery's cost BY SIZE. The claim under test since 3.0 is "a small delivery
        # pays less than a standard one" — no sceptic, the touched test files instead of
        # the whole suite — and the global median cannot confirm or refute it. A delivery
        # recorded before the size existed lands under `(no size)`, visible and apart.
        by_size = collections.defaultdict(list)
        for r in rs:
            if r.get("capability") == "deliver":
                by_size[r.get("size") or "(no size)"].append(r)
        size_cost = {}
        for size, group in sorted(by_size.items()):
            spent = [r["tokens"] for r in group if isinstance(r.get("tokens"), (int, float))]
            took = [r["minutes"] for r in group if isinstance(r.get("minutes"), (int, float))]
            size_cost[size] = {"runs": len(group),
                               "delivered": sum(1 for r in group if r.get("state") == "delivered"),
                               "tokens_median": round(statistics.median(spent)) if spent else None,
                               "minutes_median": round(statistics.median(took), 1) if took else None}
        # Who spends, agent by agent, across the history. It is the list that says where
        # to cut.
        spend = collections.defaultdict(list)
        instances = collections.Counter()
        for r in rs:
            for agent, v in (r.get("cost_per_agent") or {}).items():
                # The record keeps {instances, billable, messages} per agent type.
                spend[agent].append(v["billable"] if isinstance(v, dict) else v)
                instances[agent] += v.get("instances", 1) if isinstance(v, dict) else 1
        agent_cost = {a: {"runs": len(v), "instances": instances[a],
                          "median": round(statistics.median(v))}
                      for a, v in sorted(spend.items(),
                                         key=lambda kv: -statistics.median(kv[1]))}
        # A run without measurement is not a cheap run — it is an unmeasured one, and it
        # has to appear beside the median so that the median is not read as the whole.
        unmeasured = sum(1 for r in rs if r.get("tokens") is None)
        # Money, where the installation priced every model a run used. A run whose cost
        # is null is counted apart, for the same reason as the tokens: not cheap, unpriced.
        usd = [r["cost_usd"] for r in rs if isinstance(r.get("cost_usd"), (int, float))]
        usd_per_line = [r["cost_usd"] / r["lines_changed"] for r in rs
                        if isinstance(r.get("cost_usd"), (int, float))
                        and isinstance(r.get("lines_changed"), (int, float)) and r["lines_changed"] > 0]
        minutes = [r["minutes"] for r in rs if isinstance(r.get("minutes"), (int, float))]
        coverages = [r["profile_coverage"] for r in rs if isinstance(r.get("profile_coverage"), (int, float))]
        not_assessable = [len(r.get("classes_not_assessable") or []) for r in rs]
        # Deliveries per ISO week, and items whose FIRST review was approved: what a
        # person can watch grow without rewarding volume over a clean first pass.
        weeks = collections.Counter(_week(r["date"]) for r in rs
                                    if r.get("state") == "delivered" and _week(r.get("date")))
        first = {}
        for r in rs:
            if r.get("verdict") and r.get("item") is not None:
                first.setdefault(str(r["item"]), r["verdict"])
        out[repo] = {
            "runs": len(rs),
            "delivered": sum(1 for r in rs if r.get("state") == "delivered"),
            "delivered_by_week": dict(sorted(weeks.items())),
            "items_reviewed": len(first),
            "approved_first_pass": sum(1 for v in first.values() if v == "APPROVED"),
            "verdicts": dict(verdicts),
            "precision": precision,
            "precision_basis": precision_basis,
            "disagreements": sum(1 for r in rs if r.get("verdict_contested")),
            "tokens_median": round(statistics.median(tokens)) if tokens else None,
            "tokens_per_line_median": round(statistics.median(per_line), 1) if per_line else None,
            "tokens_per_line_median_by_effort": {
                e: round(statistics.median(v), 1)
                for e, v in sorted(per_line_by_effort.items())},
            "shapes": dict(shapes),
            "by_size": size_cost,
            "runs_without_measurement": unmeasured,
            "cost_usd_total": round(sum(usd), 4) if usd else None,
            "cost_usd_median": round(statistics.median(usd), 4) if usd else None,
            "cost_usd_per_line_median": round(statistics.median(usd_per_line), 5) if usd_per_line else None,
            "runs_without_cost": len(rs) - len(usd),
            "cost_per_agent": agent_cost,
            "minutes_median": round(statistics.median(minutes), 1) if minutes else None,
            "profile_coverage_mean": round(statistics.mean(coverages), 2) if coverages else None,
            "not_assessable_classes_mean": round(statistics.mean(not_assessable), 2) if not_assessable else None,
            "escapes": sum(1 for r in rs if r.get("escape")),
        }
    return out


def _week(date) -> str | None:
    """`2026-11-12` → `2026-W46`; anything else is no week, never a guessed one."""
    import datetime
    try:
        y, w, _ = datetime.date.fromisoformat(str(date)[:10]).isocalendar()
    except ValueError:
        return None
    return f"{y}-W{w:02d}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runs", required=True)
    p.add_argument("--repo")
    p.add_argument("--json", action="store_true", help="the versioned JSON form, for clients")
    args = p.parse_args()

    path = pathlib.Path(args.runs)
    runs = ([json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
            if path.exists() else [])
    if args.repo:
        runs = [r for r in runs if r.get("repo") == args.repo]
    if args.json:
        # no history is an empty answer, not an error: a new installation has none yet
        print(json.dumps({"version": METRICS_VERSION, "repos": aggregate(runs)}, ensure_ascii=False))
        return 0
    if not path.exists():
        print(t("metrics.no_history", path=path))
        return 0

    for repo, m in aggregate(runs).items():
        print("\n" + t("metrics.repo_header", repo=repo, runs=m["runs"]))
        for k, v in m.items():
            if k != "runs":
                print(f"  {k:32} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
