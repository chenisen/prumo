#!/usr/bin/env python3
"""Reads `runs.jsonl` and reports the harness's own metrics, per repository.

The metric none of the surveyed tools carries is profile coverage: how much of a
repository is in fact under review. Without it, "0 findings" is ambiguous.

Usage:
    metrics.py --runs FILE [--repo <repo>]
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


def aggregate(runs: list[dict]) -> dict:
    by_repo = collections.defaultdict(list)
    for r in runs:
        by_repo[r.get("repo", "(no repo)")].append(r)

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
        minutes = [r["minutes"] for r in rs if isinstance(r.get("minutes"), (int, float))]
        coverages = [r["profile_coverage"] for r in rs if isinstance(r.get("profile_coverage"), (int, float))]
        not_assessable = [len(r.get("classes_not_assessable") or []) for r in rs]
        out[repo] = {
            "runs": len(rs),
            "verdicts": dict(verdicts),
            "precision": precision,
            "precision_basis": precision_basis,
            "disagreements": sum(1 for r in rs if r.get("verdict_contested")),
            "tokens_median": round(statistics.median(tokens)) if tokens else None,
            "tokens_per_line_median": round(statistics.median(per_line), 1) if per_line else None,
            "runs_without_measurement": unmeasured,
            "cost_per_agent": agent_cost,
            "minutes_median": round(statistics.median(minutes), 1) if minutes else None,
            "profile_coverage_mean": round(statistics.mean(coverages), 2) if coverages else None,
            "not_assessable_classes_mean": round(statistics.mean(not_assessable), 2) if not_assessable else None,
            "escapes": sum(1 for r in rs if r.get("escape")),
        }
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runs", required=True)
    p.add_argument("--repo")
    args = p.parse_args()

    path = pathlib.Path(args.runs)
    if not path.exists():
        print(t("metrics.no_history", path=path))
        return 0
    runs = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.repo:
        runs = [r for r in runs if r.get("repo") == args.repo]

    for repo, m in aggregate(runs).items():
        print("\n" + t("metrics.repo_header", repo=repo, runs=m["runs"]))
        for k, v in m.items():
            if k != "runs":
                print(f"  {k:32} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
