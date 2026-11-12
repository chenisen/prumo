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

**A run the Workflow did not record still counts.** Agents run by another harness — a
runner with any provider, a Codex-style harness, a person replaying the workflow — leave
a ledger in the package instead: `usage.jsonl`, one line per agent call, with the agent,
the model and what the provider reported. It is read when no Workflow record is.

**Money is measured, never estimated.** Tokens become USD only through `prices` in
`prumo.yml`, the installation's own list. A model with no price, or a call whose tokens
came without the input/output split, leaves the cost `null` with the reason.

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


def find_runs(package: str | None, run_id: str | None) -> list[dict]:
    """Every run over this package, oldest first. A delivery is two workflow runs —
    measure, then write — and a cost that reads only the last one reads half."""
    candidates = []
    for path in workflow_records():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if run_id and data.get("runId") != run_id:
            continue
        if package and not any(
                package in str(data.get(field) or "") for field in ("args", "script")):
            continue
        data["_path"] = path
        candidates.append(data)
    return sorted(candidates, key=lambda d: d.get("timestamp") or "")


def find_run(package: str | None, run_id: str | None) -> dict | None:
    """The run that reviewed THIS package.

    Where the package path lives in the record has MOVED, and the lookup followed it
    one step too late. It used to be inlined in the workflow's `script`, back when the
    harness was handed a script built per review. Since the method travels as a plugin
    there is ONE generic `task-review.js` for every run and the package arrives in
    `args` — so `package in script` became permanently false and every review since
    printed `tokens: null`, "no workflow record found". The measurement did not get
    worse; it went silent, which is harder to notice, and it is the number every
    efficiency decision is judged against. Both fields are read now: `args` for how the
    harness records a run today, `script` so a record written before the move still
    resolves.

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
        if package and not any(
                package in str(data.get(field) or "") for field in ("args", "script")):
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


LEDGER = "usage.jsonl"


def ledger_agents(package: str | None) -> list[dict]:
    """The agent calls a harness wrote to `<package>/usage.jsonl`, in the shape
    `usage_per_agent` returns. A line that names only `total_tokens` keeps its total and
    says the split is unknown — the components are never invented as zero."""
    path = pathlib.Path(package) / LEDGER if package else None
    if not path or not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        known = {k: o[k] for k in COMPONENTS if isinstance(o.get(k), int)}
        split = "input_tokens" in known and "output_tokens" in known
        total = sum(known.values()) if split else o.get("total_tokens")
        if not isinstance(total, int):
            continue
        out.append({"agent": o.get("agent") or "?", "model": o.get("model"),
                    "messages": int(o.get("requests") or 1),
                    **{k: known.get(k, 0) for k in COMPONENTS},
                    "billable": total, "split": split, "source": o.get("source")})
    return sorted(out, key=lambda a: -a["billable"])


def price_of(agent: dict, prices: dict) -> tuple[float | None, str | None]:
    """USD for one agent call, from the installation's price list — or None and why."""
    if not agent.get("split", True):
        return None, t("telemetry.cost_no_split", agent=agent["agent"])
    p = prices.get(agent.get("model") or "")
    if not p:
        return None, t("telemetry.cost_no_price", model=agent.get("model") or "?")
    usd = (agent["input_tokens"] * p["input"] + agent["output_tokens"] * p["output"]
           + agent["cache_creation_input_tokens"] * p.get("cache_write", p["input"])
           + agent["cache_read_input_tokens"] * p.get("cache_read", p["input"])) / 1_000_000
    return round(usd, 6), None


def installation_prices() -> dict:
    try:
        from common import cfg
        return dict(cfg().prices)
    except Exception:                                        # noqa: BLE001 — no config, no prices
        return {}


def with_cost(m: dict, prices: dict | None = None) -> dict:
    """Adds `cost_usd` (all agents priced, or null with the first reasons) and the cost
    of each agent type."""
    prices = installation_prices() if prices is None else prices
    reasons, total = [], 0.0
    for a in m["per_agent"]:
        a["cost_usd"], why = price_of(a, prices)
        if why:
            reasons.append(why)
        else:
            total += a["cost_usd"]
            entry = m["cost_per_agent"].get(a["agent"])
            if entry is not None:
                entry["cost_usd"] = round(entry.get("cost_usd", 0) + a["cost_usd"], 6)
    reasons = list(dict.fromkeys(reasons))
    m["cost_usd"] = None if reasons or not m["per_agent"] else round(total, 4)
    m["cost_not_measured_because"] = "; ".join(reasons[:3]) or (
        None if m["per_agent"] else t("telemetry.cost_nothing"))
    m["cost_priced_usd"] = round(total, 4)
    return m


def from_ledger(package: str) -> dict | None:
    agents = ledger_agents(package)
    if not agents:
        return None
    total = collections.Counter()
    for a in agents:
        for k in COMPONENTS:
            total[k] += a[k]
    aggregate: dict[str, dict] = {}
    for a in agents:
        e = aggregate.setdefault(a["agent"], {"instances": 0, "billable": 0, "messages": 0})
        e["instances"] += 1
        e["billable"] += a["billable"]
        e["messages"] += a["messages"]
    billable = sum(a["billable"] for a in agents)
    return {"run_id": None, "runs": [], "session": None, "source": LEDGER,
            "scope": t("telemetry.scope_ledger"),
            "tokens": billable, "billable_tokens": billable, "harness_tokens": None,
            "components": {k: total[k] for k in COMPONENTS},
            "components_complete": all(a["split"] for a in agents),
            "minutes": None, "agents": len(agents), "declared_agents": None,
            "tool_calls": None, "default_model": None, "status": None,
            "per_agent": agents,
            "cost_per_agent": dict(sorted(aggregate.items(), key=lambda kv: -kv[1]["billable"]))}


def measure(package: str | None, run_id: str | None, every_run: bool = False) -> dict:
    """What the agents cost: the Workflow's own records when there are any, else the
    ledger a harness left in the package; tokens always, USD when every model is priced."""
    m = measure_tokens(package, run_id, every_run)
    if m.get("tokens") is None and package:
        m = from_ledger(package) or m
    return with_cost(m) if m.get("tokens") is not None else m


def measure_tokens(package: str | None, run_id: str | None, every_run: bool = False) -> dict:
    """One run — the most recent, which is the one whose findings a review judges — or,
    with `every_run`, all of them summed: a delivery spends across two workflow runs."""
    runs = find_runs(package, run_id) if every_run else [find_run(package, run_id)]
    runs = [r for r in runs if r]
    if not runs:
        target = run_id or package
        return {"tokens": None, "minutes": None,
                "reason": t("telemetry.no_record", target=target,
                            root=projects_root())}

    run = runs[-1]
    session = run["_path"].parent.parent
    agents = []
    for r in runs:
        agents += usage_per_agent(r["_path"].parent.parent, r["runId"])
    total = collections.Counter()
    for a in agents:
        for k in COMPONENTS:
            total[k] += a[k]
    billable = sum(total[k] for k in COMPONENTS)
    ms = sum((r.get("durationMs") or 0) for r in runs)

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
        "runs": [r["runId"] for r in runs],
        "session": session.name,
        "scope": t("telemetry.scope"),
        "tokens": billable,
        "billable_tokens": billable,
        "harness_tokens": sum((r.get("totalTokens") or 0) for r in runs),
        "components": {k: total[k] for k in COMPONENTS},
        "minutes": round(ms / 60000, 2),
        # Still spelled in Portuguese because `review.py` reads it to write `runs.jsonl`:
        # renaming it here alone would turn the field into a silent `null` in the record.
        "agents": len(agents),
        "declared_agents": run.get("agentCount"),
        "tool_calls": sum((r.get("totalToolCalls") or 0) for r in runs),
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
          agents=m["agents"], tool_calls=m["tool_calls"]) if m.get("source") != LEDGER
        else t("telemetry.summary_ledger", tokens=f"{m['tokens']:,}", agents=m["agents"]),
        t("telemetry.components", output=f"{c['output_tokens']:,}",
          cache_creation=f"{c['cache_creation_input_tokens']:,}",
          cache_read=f"{c['cache_read_input_tokens']:,}")
        if m.get("components_complete", True) else t("telemetry.components_unknown"),
    ]
    if m.get("harness_tokens") is not None:
        lines.append(t("telemetry.harness_total", harness=f"{m['harness_tokens']:,}"))
    if "cost_usd" in m:
        lines.append(t("telemetry.cost", usd=f"{m['cost_usd']:.4f}") if m["cost_usd"] is not None
                     else t("telemetry.cost_unmeasured", priced=f"{m['cost_priced_usd']:.4f}",
                            reason=m["cost_not_measured_because"]))
    lines.append(t("telemetry.per_agent"))
    for a in m["per_agent"]:
        cost = f" · US$ {a['cost_usd']:.4f}" if a.get("cost_usd") is not None else ""
        lines.append(t("telemetry.agent_row", agent=f"{a['agent']:30}",
                       billable=f"{a['billable']:>11,}",
                       messages=f"{a['messages']:>3}", model=a["model"]) + cost)
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--package")
    p.add_argument("--run-id")
    p.add_argument("--output", help=t("telemetry.output_help"))
    p.add_argument("--every-run", action="store_true", help=t("telemetry.every_run_help"))
    args = p.parse_args()
    if not (args.package or args.run_id):
        p.error(t("telemetry.need_target"))

    m = measure(args.package, args.run_id, every_run=args.every_run)
    destination = pathlib.Path(args.output) if args.output else (
        pathlib.Path(args.package) / "telemetry.json" if args.package else None)
    if destination:
        write_json(destination, m)
    print(summarise(m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
