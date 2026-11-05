#!/usr/bin/env python3
"""Assesses the 18 classes against the profile's anchors and what the change touches.

Reads no code and calls no LLM: it is glob, regex and the capabilities the preflight
measured. Every class comes out in one of three states, and `nao_disparou` and
`nao_avaliavel` come out with a reason.

Usage:
    triage.py --pacote DIR [--saida DIR]
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from i18n import t
from common import (CLASSES, CHECK_ROLES, UsageError, load_profile, casa_algum,
                   waiver_in_force, is_document, write_json, read_json, cfg)

# Content patterns that hold in any stack. This is the floor under the eight classes with
# no anchor: what is looked for is the SHAPE of the change in the diff, never a path that
# belongs to one project.
UNIVERSAL_PATTERNS = {
    "A3": [r"\btenant\b", r"\bscope[A-Z_]", r"\bwhere(Tenant|Company|Owner|Organization)",
           r"\bauthoriz", r"\bcan\(", r"\bpolicy\b", r"\bGate::", r"\bforbidden\b"],
    # A5: the business WORD alone is not enough; there has to be decision logic over it.
    "A5": [r"(?i)(\b(if|elseif|elif|when|case|match|unless)\b|\?\?|&&|\|\|)[^\n]{0,80}"
           r"\b(discount|desconto|price|preco|preço|elegib|eligib|fidelid|loyalt|promo|tarif)",
           r"(?i)\b(discount|desconto|price|preco|preço|elegib|eligib|fidelid|loyalt)"
           r"[^\n]{0,40}(>=|<=|==|!=|>|<)\s*\w"],
    # A6: introducing an ABSTRACTION or a boundary, not declaring one more concrete class.
    # No `(?i)`: capitalised `Container` is the injection container; lowercase "container"
    # is the ordinary word, and matching it was a false positive measured on a real
    # proposal.
    "A6": [r"^\+\s*(abstract\s+class|interface|trait)\s",
           r"^\+\s*(class|final\s+class)\s+\w+\s+implements\s",
           r"^\+[^\n]*\b(ServiceProvider|->bind\(|->singleton\(|ContainerInterface)"],
    # The third pattern looks for a test DISABLED or NARROWED, and it used to look for
    # the bare words `skip` and `only`. `only` is an ordinary English word: the comment
    # `// Known current behavior, not the desired one: only the exact spelling …` fired
    # A8 on a delivery that added 217 tests and removed none, and a fired §A class
    # closes gate 2 — so a comment cost a human decision and an INTERVENTION. Measured
    # twice on 2026-11-04. What it matches now is the SHAPE of a directive, which prose
    # does not have: the call, the attribute, the decorator.
    "A8": [r"^-\s*.*\b(assert|expect|should|toBe|toEqual)", r"^-\s*.*\b(it|test|def test_)\s*\(",
           r"^\+\s*.*(\.(skip|only|failing|todo)\s*[(\.]"
           r"|\b(markTestSkipped|markTestIncomplete)\s*\("
           r"|\b(xit|xdescribe|fit|fdescribe)\s*\("
           r"|@pytest\.mark\.(skip|skipif|xfail)"
           r"|@(unittest\.)?(skip|skipIf|skipUnless|expectedFailure)\b"
           r"|\bt\.Skip(Now)?\s*\("
           r"|#\[\s*(ignore|Ignore)\s*\]"
           r"|\$this->markTestSkipped)"],
    "A9": [r"(?i)(api[_-]?key|secret|password|passwd|token|bearer)\s*[=:]\s*['\"][^'\"]{8,}",
           r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"glpat-[A-Za-z0-9_\-]{10,}",
           r"gh[pousr]_[A-Za-z0-9]{20,}", r"AKIA[0-9A-Z]{16}"],
    "B6": [r"(?i)\b(update|insert into|delete from)\b.*\b(where|select)\b",
           r"(?i)\bDB::(table|statement|raw)\b", r"(?i)\bchunkById\b", r"(?i)\bbackfill\b"],
    "B7": [r"(?i)\bforeach\b[\s\S]{0,200}?\b(find|where|first|get|query)\(",
           r"(?i)\b(all|get)\(\)\s*->\s*(each|map|filter)", r"(?i)\bN\+1\b"],
    "B8": [],  # decided by where the delivery lands, not by the diff; the resolver reports it
}

# A8 also fires structurally when a test file disappears from the diff altogether.
RE_TEST = re.compile(r"(^|/)(tests?|spec|__tests__|e2e)/|\.(test|spec)\.[tj]sx?$|Test\.php$")

# What counts as configuration when classifying a change's NATURE. Universal formats,
# never a project's paths — the same rule that governs `UNIVERSAL_PATTERNS`.
CONFIG_EXTENSIONS = (".yml", ".yaml", ".json", ".env", ".toml", ".ini")


def _is_config(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.lower().endswith(CONFIG_EXTENSIONS) or name.startswith(".env")


def change_shape(change: dict) -> dict:
    """Classifies the NATURE of the change — what the diff is made of, before any size.

    A pure function over `change.json`: no LLM, no repository knowledge. The partition is
    decided per file, in this order: a document is prose whatever its directory; anything
    under a test path is proof, fixtures included — a `.json` under `tests/` serves the
    tests, not the system; a config format outside a test path is configuration; the rest
    is production.

    The five shapes are exclusive and the cascade decides ties: touching production makes
    the change `production` (or `mixed` when proof moved with it) no matter what rode
    along; with no production, proof wins over config, config over docs — the heavier
    nature names the change."""
    files = change.get("files") or []
    docs = [f for f in files if is_document(f)]
    proof = [f for f in files if not is_document(f) and RE_TEST.search(f)]
    config = [f for f in files
              if not is_document(f) and not RE_TEST.search(f) and _is_config(f)]
    production = [f for f in files
                  if not is_document(f) and not RE_TEST.search(f) and not _is_config(f)]

    if production:
        shape = "mixed" if proof else "production"
    elif proof:
        shape = "proof_only"
    elif config:
        shape = "config_only"
    else:
        shape = "docs_only"

    by_file = change.get("lines_by_file") or {}
    lines_production = sum(by_file.get(f, 0) for f in production)
    lines_proof = sum(by_file.get(f, 0) for f in proof)
    if not by_file:
        # A package from before the per-file measure. The one total is attributed to
        # production whenever production was touched — conservative on purpose: a guess
        # here may only RAISE the effort, never lower it.
        total = int(change.get("lines_changed") or 0)
        lines_production = total if production else 0
        lines_proof = total if (proof and not production) else 0

    return {"shape": shape,
            "production_files": production, "proof_files": proof,
            "config_files": config, "doc_files": docs,
            "lines_production": lines_production, "lines_proof": lines_proof}


def _added_and_removed_lines(diff: str) -> str:
    return "\n".join(l for l in diff.splitlines() if l[:1] in "+-")


def _blocks_for_files(diff: str, files: list[str]) -> str:
    """Cuts from the diff only the blocks belonging to the given files.

    This is what gives the "e" combination its meaning: the pattern has to appear INSIDE
    the file the path pointed at, and not anywhere else in the diff.

    **If the diff carries no `diff --git` header there is no way to attribute a line to a
    file** — and then this returns the whole body, never empty. Returning empty would make
    the class stop firing in silence, which is the failure mode this harness exists to
    avoid.
    """
    if not files:
        return ""
    if "diff --git " not in diff:
        return _added_and_removed_lines(diff)
    targets, blocks, current, keeping = set(files), [], [], False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            if keeping:
                blocks.append("\n".join(current))
            current, keeping = [], any(a in line for a in targets)
        if keeping and line[:1] in "+-":
            current.append(line)
    if keeping:
        blocks.append("\n".join(current))
    return "\n".join(blocks)


def assess_class(class_id: str, meta: dict, profile: dict, change: dict,
                 capabilities: dict, shape: dict | None = None) -> dict:
    """Decides the state of ONE class. A pure function: same input, same state.

    `shape` is the partition `change_shape` already made; computed here only when the
    caller did not pass one, so eighteen classes share one classification instead of
    each cutting the file list again."""
    shape = shape or change_shape(change)
    anchor = (profile.get("anchors") or {}).get(class_id)
    # `change.json` calls this `files`. It was `arquivos` until the method was written
    # in English, and the rename missed here — which made `files` silently EMPTY, so no
    # anchor ever matched and every anchored class reported "nothing matched the anchor".
    # A key the package does not write is not a nil-safe default: it is a dead rule.
    files = change.get("files") or []
    diff = change.get("diff") or ""
    body = _added_and_removed_lines(diff)

    # 1. not_applicable recorded by a person: the class steps aside, leaving a trail
    if anchor and "not_applicable" in anchor:
        na = anchor["not_applicable"]
        return dict(id=class_id, section=meta["section"], state="did_not_fire",
                    reason=t("triage.not_applicable", reason=na["reason"],
                             approver=na["approver"], date=na["date"]),
                    cause="not_applicable",
                    not_applicable=True, waiver=None, evidence=[])

    # 2. capability: a class that leans on a check is not assessable without the tool
    role = meta.get("check")
    if role:
        cap = (capabilities.get("checks") or {}).get(role) or {}
        if not cap.get("available"):
            waiver = ((profile.get("checks") or {}).get(role) or {}).get("waiver")
            return dict(id=class_id, section=meta["section"], state="not_assessable",
                        reason=t("triage.no_tool", check=role,
                                 reason=cap.get("reason") or t("triage.check_not_declared")),
                        cause="no_tool",
                        not_applicable=False,
                        waiver=waiver if waiver_in_force(waiver) else None, evidence=[])

    # 3. no anchor: only assessable if the class has a universal floor
    has_anchor = bool(anchor and (anchor.get("paths") or anchor.get("patterns")))
    if not has_anchor and meta["anchor_required"]:
        return dict(id=class_id, section=meta["section"], state="not_assessable",
                    reason=t("triage.no_anchor", repo=profile.get("profile")),
                    cause="no_anchor",
                    not_applicable=False, waiver=None, evidence=[])

    # 4. assess. The path says where; the pattern says what. `combination` decides whether
    #    one of the two is enough.
    by_path = [{"file": a, "by": t("triage.by_anchor_path")} for a in files
               if has_anchor and casa_algum(a, anchor.get("paths"))]

    patterns = list((anchor or {}).get("patterns") or []) + UNIVERSAL_PATTERNS.get(class_id, [])
    by_pattern: list[dict] = []
    proof_only = False  # every code file in the change is proof, and this class is about production
    if by_path and meta.get("combination") == "e":
        target = _blocks_for_files(diff, [e["file"] for e in by_path])
    elif meta.get("code_only"):
        code = [a for a in files if not is_document(a)]
        if meta.get("production_only"):
            # This class asks what the system DOES, and proof does nothing: adding a test
            # changes no business rule and inverts no dependency. Matching an enum's name
            # inside a test file is a false positive, and a false positive in a §A class
            # costs a human decision nobody needed to make. The partition comes from the
            # shape — config stays IN: a pattern can live in a `.yml` as well as in code.
            production = shape["production_files"] + shape["config_files"]
            proof_only = bool(shape["proof_files"]) and not production
            code = production
        target = _blocks_for_files(diff, code) if code else ""
    else:
        target = body
    for p in patterns:
        try:
            m = re.search(p, target, re.MULTILINE)
        except re.error:
            continue
        if m:
            by_pattern.append({"pattern": p, "snippet": m.group(0)[:120].strip()})
            break

    if class_id == "A8":
        for f in change.get("removed") or []:
            if RE_TEST.search(f):
                by_pattern.append({"file": f, "by": t("triage.by_removed_test")})

    has_paths = bool((anchor or {}).get("paths"))
    if meta.get("combination") == "e" and has_paths and patterns:
        evidence = (by_path[:2] + by_pattern) if (by_path and by_pattern) else []
    else:
        evidence = by_path + by_pattern

    if evidence:
        return dict(id=class_id, section=meta["section"], state="fired",
                    reason=meta["question"], not_applicable=False, waiver=None,
                    evidence=evidence[:5])

    if proof_only:
        # Silence with a declared cause: the class did not fall quiet by accident, it fell
        # quiet because every code file in the change is proof and this class asks about
        # production.
        reason = t("triage.production_only")
    elif has_anchor:
        reason = t("triage.nothing_matched_anchor")
    else:
        reason = t("triage.nothing_matched_universal")
    return dict(id=class_id, section=meta["section"], state="did_not_fire",
                reason=reason, not_applicable=False, waiver=None, evidence=[])


def with_diff(change: dict, package: pathlib.Path) -> dict:
    """The patch, read from disk ONCE and carried in memory under a private key.

    It used to live inside `change.json` itself, which made the file 77KB — the whole
    patch, byte for byte, beside `diff.patch` holding the same thing. Every reader of
    that file then paid for the patch, and in one measured review the reviewers read it
    seven times between them.

    The key stays `diff` IN MEMORY, which is what every triage function reads and what
    the suite builds. What changed is only what reaches disk."""
    patch = package / "diff.patch"
    return {**change, "diff": patch.read_text(encoding="utf-8") if patch.exists() else ""}


def triage_classes(profile: dict, change: dict, capabilities: dict,
                   shape: dict | None = None) -> list[dict]:
    shape = shape or change_shape(change)
    return [assess_class(c, meta, profile, change, capabilities, shape)
            for c, meta in sorted(CLASSES.items(), key=lambda kv: (kv[1]["section"], int(kv[0][1:])))]


def size_band(profile: dict, change: dict) -> dict:
    thresholds = profile.get("size") or {"ideal": 100, "acceptable": 300, "split": 1000}
    n = int(change.get("lines_changed") or 0)
    if n > thresholds["split"]:
        band, topology = "split", t("triage.topology_split")
    elif n > thresholds["ideal"]:
        band, topology = "acceptable", t("triage.topology_acceptable")
    else:
        band, topology = "ideal", t("triage.topology_ideal")
    return {"lines": n, "band": band, "topology": topology, "limiares": thresholds}


# The classes whose firing makes a delivery FULL effort whatever its size: they read the
# system's contract with the outside (A1/A2/A4), its authorisation (A3), its business
# rules (A5) or its data (B6). Method constants, like `UNIVERSAL_PATTERNS` — the profile
# tunes the anchors that make them fire, never this list.
FULL_EFFORT_CLASSES = ("A1", "A2", "A3", "A4", "A5", "B6")

# The shapes that carry no production code. What they still have to catch — a removed
# test, a secret in a config — is caught by the CLASSES, which run whatever the effort;
# a §A firing takes the delivery out of this level.
MINIMAL_SHAPES = ("proof_only", "docs_only", "config_only")


def effort_level(shape: dict, size: dict, classes: list[dict],
                 change: dict | None = None) -> dict:
    """Decides how much review this delivery buys: `minimal`, `standard` or `full`.

    A pure function, derived with no LLM, and it decides WHO READS, never what is
    declared: the 18 classes are always triaged, gate 5 keeps saying what was and was
    not measured, and a skipped reviewer leaves a named trail in the plan. Every verdict
    input stays untouched — `judge.py` never sees this.

    The cascade, worst first:
    - `full` when a contract/authorisation/rule/data class fired, production lines are
      above the acceptable threshold, the band is `split`, or a file is over the ceiling;
    - `minimal` when the change carries no production code AND no §A class fired — a
      removed test (A8) or a secret (A9) is exactly what a cheap level must not absorb;
    - `standard` for everything in between.

    The thresholds come from the profile's `size` (optional `production_acceptable`,
    defaulting to `acceptable`): repository-tuned numbers, never repository names."""
    fired = {c["id"] for c in classes if c["state"] == "fired"}
    sensitive = sorted(f for f in fired if f in FULL_EFFORT_CLASSES)
    section_a = sorted(f for f in fired if f.startswith("A"))
    thresholds = size.get("limiares") or {}
    acceptable = thresholds.get("production_acceptable") or thresholds.get("acceptable", 300)
    over_ceiling = (change or {}).get("over_ceiling") or []

    reasons = []
    if sensitive:
        reasons.append(t("triage.effort_full_class", classes=", ".join(sensitive)))
    if shape["lines_production"] > acceptable:
        reasons.append(t("triage.effort_full_production",
                         lines=shape["lines_production"], limit=acceptable))
    if size["band"] == "split":
        reasons.append(t("triage.effort_full_split"))
    if over_ceiling:
        reasons.append(t("triage.effort_full_ceiling", n=len(over_ceiling)))
    if reasons:
        level = "full"
    elif shape["shape"] in MINIMAL_SHAPES:
        if section_a:
            level = "standard"
            reasons.append(t("triage.effort_not_minimal_class",
                             shape=shape["shape"], classes=", ".join(section_a)))
        else:
            level = "minimal"
            reasons.append(t("triage.effort_minimal", shape=shape["shape"]))
    else:
        level = "standard"
        reasons.append(t("triage.effort_standard", limit=acceptable))

    return {"effort": level, "reasons": reasons, "shape": shape,
            "thresholds": {"production_acceptable": acceptable,
                           "split": thresholds.get("split", 1000)}}


def _fired(classes: list[dict], *ids: str) -> bool:
    return any(c["id"] in ids and c["state"] == "fired" for c in classes)


# Who runs, over which slice, with which model and turn budget, PER EFFORT LEVEL.
#
# This table replaced two implicit rules: `bool(files)` — which activated correctness,
# security and architecture for ANY non-empty diff above the ideal band, making a
# coverage delivery pay the same fan-out as a migration — and `band == ideal` skipping
# everything non-always, which read only size and never nature. Measured before the
# change: the three `bool(files)` reviewers were 78% of the bill.
#
# The slice is the harness's single biggest cost lever: it is the input payload of a
# fresh context, and four reviewers reading `completo` paid for the changelog and the
# lockfile four times over. Only `scope-coherence` reads the whole diff, because its
# question is precisely whether the item, the proposal and the code tell the same story —
# and prose is part of the story.
#
# Two kinds of entry. A LEVELLED reviewer declares, per effort level it runs at, its
# slice, model and turn budget; a level it does not declare is a skip with a named
# reason, and a level may carry its own `when` gate (correctness in minimal reads only a
# proof-only change; security in minimal only answers a fired A9/A3 — unreachable today,
# since those classes raise the effort, and kept as defence in depth should the cascade
# ever change). A CLASS-TRIGGERED reviewer carries a single `when`: the fired class
# decides, at any level, because its ground is already a named risk — and A1/A2/A4/A5
# force `full` anyway, so only performance-ops ever runs below it.
#
# `model` here is the method's choice per level; `profile.models` still wins. `max_turns`
# travels in the plan for the record — the harness does not take a per-call turn cap
# (verified against the Workflow agent() options), so the hard ceiling stays the agent
# frontmatter's and the per-level budget is enforced as reasoning effort instead.
REVIEWERS = [
    dict(agent="scope-coherence-reviewer",
         levels={"minimal":  dict(slice="complete", model="sonnet", max_turns=12),
                 "standard": dict(slice="complete", model="sonnet", max_turns=20),
                 "full":     dict(slice="complete", model="sonnet", max_turns=25)}),
    dict(agent="correctness-proof-reviewer",
         levels={"minimal":  dict(slice="tests", model="sonnet", max_turns=15,
                                  when=lambda shape, classes: shape["shape"] == "proof_only",
                                  skip_key="triage.skip_minimal_correctness"),
                 "standard": dict(slice="code-and-tests", model="sonnet", max_turns=25),
                 "full":     dict(slice="code-and-tests", model="sonnet", max_turns=30)}),
    dict(agent="security-privacy-reviewer",
         levels={"minimal":  dict(slice="code-and-config", model="sonnet", max_turns=15,
                                  when=lambda shape, classes: _fired(classes, "A9", "A3"),
                                  skip_key="triage.skip_minimal_security"),
                 "standard": dict(slice="code-and-config", model="sonnet", max_turns=20),
                 "full":     dict(slice="code-and-config", model="opus", max_turns=30)}),
    dict(agent="architecture-reviewer",
         levels={"standard": dict(slice="code-and-tests", model="sonnet", max_turns=20,
                                  when=lambda shape, classes: _fired(classes, "A6"),
                                  skip_key="triage.skip_standard_architecture"),
                 "full":     dict(slice="code-and-tests", model="sonnet", max_turns=24)}),
    dict(agent="contract-compat-reviewer", slice="contract",
         when=lambda shape, classes: _fired(classes, "A1", "A2", "A4")),
    dict(agent="domain-rules-reviewer", slice="code-and-tests",
         when=lambda shape, classes: _fired(classes, "A5")),
    dict(agent="performance-ops-reviewer", slice="code-and-config",
         when=lambda shape, classes: _fired(classes, "A10", "B7")),
]


def agent_prefix() -> str:
    """`prumo:` when the method is installed as a plugin, empty when the agents sit loose
    in `.claude/agents/`.

    Three sources, in order, and the first two are DECLARED rather than detected:
    `PRUMO_PREFIXO_AGENTE` forces it for one run, `agents.prefix` in `prumo.yml` states it
    for the installation, and only then does `CLAUDE_PLUGIN_ROOT` decide.

    The detection is last because it is the weakest of the three: the harness sets that
    variable only while it is running the plugin's own skill, so preparing a package from
    a plain shell — which is how a review gets re-run by hand — leaves it absent, the plan
    goes out with the bare name, and the workflow dies on its first agent with `agent type
    'scope-coherence-reviewer' not found`. The failure lands after the package is built,
    names no remedy, and says nothing about a prefix. Measured on 2026-11-04, on a review
    that had to be thrown away."""
    forced = os.environ.get("PRUMO_PREFIXO_AGENTE")
    if forced is not None:
        return forced
    try:
        declared = cfg().agent_prefix
    except Exception:
        declared = None
    if declared is not None:
        return declared
    return "prumo:" if os.environ.get("CLAUDE_PLUGIN_ROOT") else ""


def manual_fallback(profile: dict, classes: list[dict]) -> dict:
    """Pre-rendered manual scripts for the causes whose honest answer never varies.

    Nine classes unassessable for want of an anchor are ONE act — version the anchors —
    and paying a planner context to write that sentence cost 64k tokens on a review whose
    whole delivery was tests. The workflow already groups by `cause`; what was missing
    was not paying an agent to state the obvious. Rendered HERE, not in the workflow,
    because the sentence a person reads is translated and the catalogue lives with the
    scripts."""
    out = {}
    for cause in ("no_anchor", "no_tool"):
        members = [c for c in classes if c["state"] == "not_assessable"
                   and c.get("cause") == cause and not c.get("not_applicable")]
        if not members:
            continue
        ids = ", ".join(c["id"] for c in members)
        out[cause] = {
            "trigger": t("triage.manual_fallback_trigger", ids=ids,
                         cause=members[0]["reason"]),
            "goal": t(f"triage.manual_fallback_goal_{cause}"),
            "environment": t("triage.manual_fallback_environment",
                             repo=profile.get("profile")),
            "data": [],
            "steps": [t(f"triage.manual_fallback_steps_{cause}", ids=ids)],
            "expected_result": t("triage.manual_fallback_expected"),
            "evidence_to_collect": t("triage.manual_fallback_evidence"),
            "acceptance": t("triage.manual_fallback_acceptance"),
        }
    return out


def activation_plan(profile: dict, change: dict, classes: list[dict], size: dict,
                    effort: dict | None = None) -> dict:
    """Who reads this delivery, decided by the effort table — never by band alone.

    Skipping is NEVER silent: every reviewer that does not run lands in `skipped` with a
    translated reason, and the report prints the list. And nothing here reaches the
    verdict — a smaller effort reduces what is read, not what is declared."""
    effort = effort or effort_level(change_shape(change), size, classes, change)
    level, shape = effort["effort"], effort["shape"]
    active, skipped = [], []
    for r in REVIEWERS:
        if "when" in r:
            # Class-triggered: the fired class decides, at any level.
            if not r["when"](shape, classes):
                skipped.append({"agent": r["agent"],
                                "reason": t("triage.reviewer_not_triggered")})
                continue
            chosen = dict(slice=r["slice"], model=None, max_turns=None)
        else:
            cfg_level = r["levels"].get(level)
            if cfg_level is None:
                skipped.append({"agent": r["agent"],
                                "reason": t("triage.reviewer_skipped_effort", level=level)})
                continue
            gate = cfg_level.get("when")
            if gate and not gate(shape, classes):
                skipped.append({"agent": r["agent"], "reason": t(cfg_level["skip_key"])})
                continue
            chosen = cfg_level
        agent = r["agent"]
        if agent == "architecture-reviewer" and profile.get("local_reviewer"):
            agent = profile["local_reviewer"]["name"]
        active.append({"agent": agent, "slice": chosen["slice"],
                       # The profile's override wins over the level's choice, as it
                       # always won over the frontmatter's.
                       "model": (profile.get("models") or {}).get(r["agent"])
                                or chosen["model"],
                       "max_turns": chosen["max_turns"]})
    return {"reviewers": active, "skipped": skipped,
            "effort": level,
            "effort_reasons": effort["reasons"],
            "manual_fallback": manual_fallback(profile, classes),
            "conventions": profile.get("conventions") or [],
            # How the agents are addressed in this harness. Installed as a plugin they
            # come with the plugin's name in front; loose in `.claude/agents/`, with
            # nothing. The workflow does not guess — it is told.
            "agent_prefix": agent_prefix(),
            "topology": size["topology"]}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--package", "--pacote", dest="pacote", required=True)
    p.add_argument("--output", "--saida", dest="saida")
    args = p.parse_args()

    package = pathlib.Path(args.pacote)
    out = pathlib.Path(args.saida or package)
    manifest = read_json(package / "manifest.json")
    profile = load_profile(manifest["repo"])
    capabilities = read_json(package / "capabilities.json")
    change = with_diff(read_json(package / "change.json"), package)

    shape = change_shape(change)
    classes = triage_classes(profile, change, capabilities, shape)
    size = size_band(profile, change)
    effort = effort_level(shape, size, classes, change)
    plan = activation_plan(profile, change, classes, size, effort)

    write_json(out / "classes.json", classes)
    write_json(out / "size.json", size)
    write_json(out / "effort.json", effort)
    write_json(out / "plan.json", plan)

    count = {e: sum(1 for c in classes if c["state"] == e)
             for e in ("fired", "did_not_fire", "not_assessable")}
    print(t("triage.summary", fired=count["fired"], quiet=count["did_not_fire"],
            unknown=count["not_assessable"], band=size["band"], lines=size["lines"],
            reviewers=len(plan["reviewers"])))
    print(t("triage.effort_summary", level=effort["effort"], shape=shape["shape"],
            reasons="; ".join(effort["reasons"])))
    for c in classes:
        if c["state"] != "did_not_fire":
            print(f"  {c['state']:14} {c['id']:4} {c['reason'][:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
