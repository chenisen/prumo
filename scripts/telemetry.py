#!/usr/bin/env python3
"""Measures what the review cost, by reading the harness's own transcripts.

It feeds the F11 record; it is not a phase of its own. (F10 is the publication.)

It estimates nothing and calls no LLM: it reads the record the Workflow writes per run
and the per-agent transcripts, and adds up what the API reported under `usage`.

**Two totals, and they do not agree — on purpose.** `harness_tokens` is the `totalTokens`
the Workflow records; `billable_tokens` is the sum of every `usage` component of
every request of every agent. They diverge by more than an order of magnitude in every
measurement taken so far. Keeping only one would hide the difference; keeping both lets
the breakdown drive the decision — it is `cache_read` that dominates, and it is agent
requests that generate it.

**Absence does not become zero.** With no locatable transcript, `tokens` comes out
`null` with a reason beside it. A zero here would make an expensive review look cheap,
which is the failure mode this harness exists not to have.

Usage:
    telemetry.py --package DIR [--output FILE]
    telemetry.py --run-id wf_xxx
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import write_json
from i18n import t

COMPONENTS = ("input_tokens", "output_tokens",
              "cache_creation_input_tokens", "cache_read_input_tokens")


def projects_root() -> pathlib.Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(pathlib.Path.home() / ".claude")
    return pathlib.Path(base) / "projects"


def workflow_records() -> list[pathlib.Path]:
    """Every `workflows/*.json` of every session of every project."""
    return [pathlib.Path(p) for p in
            glob.glob(str(projects_root() / "*" / "*" / "workflows" / "*.json"))]


def find_run(package: str | None, run_id: str | None) -> dict | None:
    """The run that reviewed THIS package. The workflow's script carries the path inside.

    More than one run over the same package (one repeated after a failure) resolves to
    the most recent, which is the one that produced the findings `conclude` is judging.
    """
    candidates = []
    for path in workflow_records():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if run_id and data.get("runId") != run_id:
            continue
        if package and package not in (data.get("script") or ""):
            continue
        data["_path"] = path
        candidates.append(data)
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.get("timestamp") or "")


def usage_per_agent(session: pathlib.Path, run_id: str) -> list[dict]:
    """One record per workflow agent: type, model, messages and the four components.

    `messages` counts the agent's REQUESTS, which is **not** its `maxTurns`: an agent
    capped at 25 showed up here with 29. Calling that "turns" would make it look as though
    the cap had been breached. What the count measures is how many requests the agent
    made — and that is what multiplies the cost.

    **One request is one `message.id`, never one line.** The transcript writes a line per
    streaming chunk, and every chunk repeats the same `usage` snapshot of the response it
    belongs to. Summing line by line therefore counted each request two to five times:
    measured on 2026-11-04, one agent had 46 lines carrying `usage` and 17 distinct ids,
    and the eleven reviews on disk added up to 112M against a real 48M. An inflated figure
    is worse than no figure here, because it is the number every optimisation is judged
    against — and it was about to be used to cut a phase that was not the expensive one.

    The LAST snapshot of an id wins: the chunks of one response carry the usage as it was
    known when each was emitted, and only the final one is complete.
    """
    agents_dir = session / "subagents" / "workflows" / run_id
    out = []
    for jsonl in sorted(agents_dir.glob("agent-*.jsonl")):
        meta_path = jsonl.with_suffix("").with_suffix(".meta.json")
        if not meta_path.exists():
            meta_path = pathlib.Path(str(jsonl)[:-len(".jsonl")] + ".meta.json")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        counts, models = collections.Counter(), set()
        # Keyed by `message.id` so the streaming chunks of one response collapse into the
        # single request they are. A line with no id cannot be collapsed with anything —
        # it gets a key of its own rather than being dropped, because an uncountable
        # request is still a request.
        per_request: dict[str, dict] = {}
        for n, line in enumerate(
                jsonl.read_text(encoding="utf-8", errors="ignore").splitlines()):
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("type") != "assistant":
                continue
            msg = o.get("message") or {}
            if msg.get("model"):
                models.add(msg["model"])
            usage = msg.get("usage") or {}
            if not usage:
                continue
            per_request[msg.get("id") or f"line-{n}"] = usage
        for usage in per_request.values():
            for k, v in usage.items():
                if k in COMPONENTS and isinstance(v, int):
                    counts[k] += v
        out.append({
            "agent": meta.get("agentType", jsonl.stem),
            "model": sorted(models)[0] if models else None,
            "messages": len(per_request),
            **{k: counts[k] for k in COMPONENTS},
            "billable": sum(counts[k] for k in COMPONENTS),
        })
    return sorted(out, key=lambda a: -a["billable"])


def measure(package: str | None, run_id: str | None) -> dict:
    run = find_run(package, run_id)
    if run is None:
        target = run_id or package
        return {"tokens": None, "minutes": None,
                "reason": t("telemetry.no_record", target=target,
                            root=projects_root())}

    session = run["_path"].parent.parent
    agents = usage_per_agent(session, run["runId"])
    total = collections.Counter()
    for a in agents:
        for k in COMPONENTS:
            total[k] += a[k]
    billable = sum(total[k] for k in COMPONENTS)
    ms = run.get("durationMs") or 0

    # Aggregated by agent TYPE, summing the instances. One workflow opens N
    # `finding-verifier` agents — one per blocking finding — and a dictionary keyed by
    # name would keep only the last, hiding precisely the axis that grows most on a bad
    # delivery.
    aggregate: dict[str, dict] = {}
    for a in agents:
        entry = aggregate.setdefault(a["agent"], {"instances": 0, "billable": 0,
                                                  "messages": 0})
        entry["instances"] += 1
        entry["billable"] += a["billable"]
        entry["messages"] += a["messages"]

    return {
        "run_id": run["runId"],
        "session": session.name,
        "scope": t("telemetry.scope"),
        "tokens": billable,
        "billable_tokens": billable,
        "harness_tokens": run.get("totalTokens"),
        "components": {k: total[k] for k in COMPONENTS},
        "minutes": round(ms / 60000, 2),
        # Still spelled in Portuguese because `review.py` reads it to write `runs.jsonl`:
        # renaming it here alone would turn the field into a silent `null` in the record.
        "agents": len(agents),
        "declared_agents": run.get("agentCount"),
        "tool_calls": run.get("totalToolCalls"),
        "default_model": run.get("defaultModel"),
        "status": run.get("status"),
        "per_agent": agents,
        "cost_per_agent": dict(sorted(aggregate.items(),
                                      key=lambda kv: -kv[1]["billable"])),
    }


def summarise(m: dict) -> str:
    if m.get("tokens") is None:
        return t("telemetry.unmeasured", reason=m["reason"])
    c = m["components"]
    lines = [
        t("telemetry.summary", tokens=f"{m['tokens']:,}", minutes=m["minutes"],
          agents=m["agents"], tool_calls=m["tool_calls"]),
        t("telemetry.components", output=f"{c['output_tokens']:,}",
          cache_creation=f"{c['cache_creation_input_tokens']:,}",
          cache_read=f"{c['cache_read_input_tokens']:,}"),
        t("telemetry.harness_total", harness=f"{m['harness_tokens']:,}"),
        t("telemetry.per_agent"),
    ]
    for a in m["per_agent"]:
        lines.append(t("telemetry.agent_row", agent=f"{a['agent']:30}",
                       billable=f"{a['billable']:>11,}",
                       messages=f"{a['messages']:>3}", model=a["model"]))
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--package")
    p.add_argument("--run-id")
    p.add_argument("--output", help=t("telemetry.output_help"))
    args = p.parse_args()
    if not (args.package or args.run_id):
        p.error(t("telemetry.need_target"))

    m = measure(args.package, args.run_id)
    destination = pathlib.Path(args.output) if args.output else (
        pathlib.Path(args.package) / "telemetry.json" if args.package else None)
    if destination:
        write_json(destination, m)
    print(summarise(m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
