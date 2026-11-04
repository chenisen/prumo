#!/usr/bin/env python3
"""F9 — report and comments built from the judge's JSON. No model writes anything here.

Escaping `<` and `>` is done IN CODE, never by instructing a model. A marker that reaches
the HTML raw disappears from the comment and takes the formatting with it — it has
happened in production — and asking a model to be careful is not a guarantee.

Usage:
    render.py --pacote DIR [--saida DIR]
"""
from __future__ import annotations

import argparse
import html
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import (CLASSES, PRUMO_ROOT, BLOCKING_SEVERITIES, SEVERITY_ORDER, dir_reports, load_profile, forge, tracker,
                   today, read_json, slug)
from i18n import t

VERSION = "2.0"
DIR_TEMPLATES = PRUMO_ROOT / "templates"

# The board accepts a subset of HTML. Anything not listed here is escaped.
ALLOWED_TAGS = ("p", "strong", "em", "code", "ul", "ol", "li", "span", "br", "a")

READABLE = {"APPROVED": "verdict.approved", "INTERVENTION_REQUIRED": "verdict.intervention"}

# Route type → catalogue key. The ids on the left are DATA, produced by the judge; only
# the label a person reads is translated.
ROUTE_READABLE = {
    "author_fix": "route_label.author_fix",
    "human_decision": "route_label.human_decision",
    "manual_check": "route_label.manual_check",
    "confirm_finding": "route_label.confirm_finding",
    "baseline": "route_label.baseline",
    "missing_profile": "route_label.missing_profile",
    "missing_capability": "route_label.missing_capability",
}


def class_question(class_id: str) -> str:
    """What a class ASKS, in the reader's language.

    It used to be an English literal in `common.py`, which meant a Portuguese report
    printed eighteen English questions — the same bug the catalogue exists to prevent.
    `CLASSES` keeps the structure (section, anchor, combination); the sentence a person
    reads lives in the catalogue."""
    return t(f"class_question.{class_id}")


# Items a route or a gate can carry that are neither a class, a finding nor a criterion:
# mechanical facts the judge names in a single word. The keys on the left are DATA,
# written by the judge; only the sentence a person reads is translated.
MECHANICAL = {
    "suite": "item_meaning.suite",
    "delivery_untested": "item_meaning.delivery_untested",
    "conflict": "item_meaning.conflict",
    "mandatory_reading": "item_meaning.mandatory_reading",
}

# A criterion's text and a finding's claim are written to be read on their own line, not
# inside a table cell. Past this many characters they are cut, because a cell that wraps
# six times hides the three other routes under it.
MEANING_CEILING = 90


def meanings_for(classes: list[dict], criteria: list[dict],
                 findings: list[dict]):
    """Resolves a route's or a gate's item to what it MEANS, in the reader's language.

    `A8`, `AC-3`, `SEC-01` are the harness talking to itself. The person reading the
    report has never seen the class catalogue, did not choose the criterion ids and
    cannot look either of them up from where they are standing — so an id on its own
    says only that something is owed, never what. This is the rule the item comment has
    followed since 2026-11-04 (`No class id travels alone`), applied to the REPORT,
    which is the artefact a reviewer actually opens.

    Returns `None` for an id nothing explains — an out-of-scope file path and a
    repository name already say what they are, and inventing a gloss for them would be
    worse than the bare string."""
    del classes  # the question comes from the catalogue, not from the triaged state
    by_criterion = {c["id"]: c.get("text") for c in criteria}
    by_finding = {f["id"]: f.get("claim") for f in findings}

    def meaning(item: str) -> str | None:
        if item in CLASSES:
            return class_question(item)
        if item in MECHANICAL:
            return t(MECHANICAL[item])
        text = by_criterion.get(item) or by_finding.get(item)
        if not text:
            return None
        text = " ".join(text.split())
        return text if len(text) <= MEANING_CEILING else text[:MEANING_CEILING - 1] + "…"

    return meaning


def named(items: list[str], meaning) -> str:
    """Every id with what it means beside it, and the bare id only when nothing explains
    it. The separator is `;` and not `,` because a question carries its own commas."""
    return "; ".join(
        f"`{i}` ({m})" if (m := meaning(i)) else f"`{i}`" for i in items)


def gate_of(verdict: dict, items: list[str]) -> str:
    """Which of the five conditions these items close, named and not just numbered.

    The judge records the gate per item, so the route does not have to carry it: a route
    serves more than one gate — `author_fix` takes a finding (gate 1) and an unmet
    criterion (gate 3) — and the number alone is one more code the reader never agreed
    to learn."""
    hit = [g for g in verdict.get("gates", [])
           if not g["passed"] and set(g["items"]) & set(items)]
    return ", ".join(f"**{g['number']}.** {t('gate_name.' + g['name'])}" for g in hit)


def vocabulary() -> dict:
    """Both forms of every vocabulary word, so each catalogue can take the one its
    language needs: English takes the bare noun, Portuguese the one carrying the article.
    A form a message does not use is harmless; a form it wants and never gets raises."""
    return {"item": tracker().item, "item_with_article": tracker().with_article(),
            "proposal": forge().proposal, "proposal_with_article": forge().with_article()}


def escape(text) -> str:
    """Every text coming from an item, a proposal, code or an agent passes through here
    before it becomes HTML."""
    return html.escape(str(text if text is not None else ""), quote=False)


def safe_html(fragment: str) -> str:
    """Escape everything, then give back only the tags in the subset the board reads."""
    escaped = escape(fragment)
    for tag in ALLOWED_TAGS:
        escaped = re.sub(rf"&lt;({tag})&gt;", r"<\1>", escaped)
        escaped = re.sub(rf"&lt;/({tag})&gt;", r"</\1>", escaped)
        escaped = re.sub(rf"&lt;({tag}) ([^&]*?)&gt;", r"<\1 \2>", escaped)
    return escaped


def routes_table(verdict: dict, origin: str = "delivery", meaning=None) -> str:
    """One row per route. `meaning` resolves each item to what it asks, so the cell
    reads as a sentence instead of as a code the reader has to decode elsewhere; the
    gate column says WHICH of the five conditions the row closed, by name."""
    meaning = meaning or (lambda _: None)
    rows = []
    target = [r for r in verdict["routes"] if r.get("origin", "delivery") == origin]
    for i, r in enumerate(target, 1):
        detail = " ".join(r.get("details") or []) or r["closes_when"]
        # A project pending item closes no gate — that is the whole point of its
        # origin — so that table has no gate column at all. An always-empty column
        # reads as "nobody looked", which is the opposite of what it would mean.
        gate = f" {gate_of(verdict, r['items'])} |" if origin == "delivery" else ""
        rows.append(f"| {i} | {t(ROUTE_READABLE[r['type']])} |{gate} {r['owner']} | "
                    f"{named(r['items'], meaning)} — {detail} |")
    # One dash per column but the last, which carries the sentence. The delivery table
    # has five columns, the project one four — a row that does not match its header
    # renders as a broken table, not as a missing cell.
    empty = "| — " * (4 if origin == "delivery" else 3)
    return "\n".join(rows) or f"{empty}| {t('report.no_open_routes')} |"


def routes_header(gate: bool = True) -> str:
    gate_col = f" {t('report.col_gate')} |" if gate else ""
    return (f"| # | {t('report.col_route')} |{gate_col} {t('report.col_owner')} | "
            f"{t('report.col_what')} |"
            "\n|---|---|" + ("---|" if gate else "") + "---|---|\n")


def project_pending_table(verdict: dict, meaning=None) -> str:
    """Debt of the repository and of the harness: always declared, always charged to
    whoever created it.

    It sits in a table of its own so that nobody reads it as a pending item of THIS
    delivery — the same reason `attributable` exists on a finding."""
    if not any(r.get("origin") == "project" for r in verdict["routes"]):
        return t("report.project_pending_none")
    return (t("report.project_pending_note", **vocabulary()) + "\n\n"
            + routes_header(gate=False)
            + routes_table(verdict, origin="project", meaning=meaning))


def class_list(classes: list[dict], section: str, state: str) -> str:
    target = [c for c in classes if c["section"] == section and c["state"] == state]
    if not target:
        return t("report.none")
    return "\n".join(
        f"- **{c['id']}** — {class_question(c['id'])}  \n  {c['reason']}" for c in target)


def build_report(package: pathlib.Path) -> str:
    man = read_json(package / "manifest.json")
    verd = read_json(package / "verdict.json")
    classes = read_json(package / "classes.json")
    cap = read_json(package / "capabilities.json")
    size = read_json(package / "size.json")
    profile = load_profile(man["repo"])
    optional = lambda n, v: read_json(package / n) if (package / n).exists() else v
    criteria, findings = optional("criteria.json", []), optional("findings.json", [])
    observations = optional("observations.json", [])
    # A route may point at a finding the `attributable` split moved out of `findings`,
    # so the resolver reads both files — an id whose text sits in the other list would
    # otherwise come out bare, which is the defect this resolver exists to remove.
    meaning = meanings_for(classes, criteria, findings + observations)

    def sec_criteria():
        if not criteria:
            return (t("report.criteria_none", **vocabulary()) + "\n\n"
                    + "> " + t("report.reading_empty_stop"))
        rows = [f"| {t('report.col_criterion')} | {t('report.col_state')} | "
                f"{t('report.col_record')} |", "|---|---|---|"]
        for c in criteria:
            rows.append(f"| {escape(c.get('text', c['id']))[:90]} | `{c['state']}` | "
                        f"{c.get('record') or '—'} |")
        return "\n".join(rows)

    def sec_proof():
        r = cap["proof_registry"]
        if r["type"] == "absent":
            return t("report.no_proof_registry")
        state = (t("report.proof_resolves") if r["resolve"]
                 else t("report.proof_does_not_resolve", reason=r.get("reason") or ""))
        return (t("report.proof_registry_line", type=r["type"], root=r["root"], state=state)
                + f"\n\n{r.get('note') or ''}")

    def sec_conventions():
        if not cap["conventions"]:
            return t("report.no_conventions")
        return "\n".join(
            f"- `{c['caminho']}` — "
            f"{t('report.convention_read') if c['resolve'] else t('report.does_not_resolve')}"
            for c in cap["conventions"])

    def sec_not_assessable():
        target = [c for c in classes if c["state"] == "not_assessable"]
        if not target:
            return t("report.all_assessed")
        body = [t("report.not_assessable_note") + "\n",
                f"| {t('report.col_class')} | {t('report.col_question')} | "
                f"{t('report.col_why_not_assessed')} |", "|---|---|---|"]
        for c in target:
            body.append(f"| **{c['id']}** | {class_question(c['id'])} | {c['reason']} |")
        body.append("\n" + t("report.two_exits", **vocabulary()))
        return "\n".join(body)

    def sec_findings():
        if not findings:
            return t("report.no_findings")
        parts = []
        for a in sorted(findings, key=lambda x: SEVERITY_ORDER.index(x["severity"])):
            ev = "; ".join(f"`{e['file']}`:{e.get('line') or '?'}" for e in a["evidence"])
            mark = "" if a.get("attributable", True) else "  " + t("report.finding_from_project")
            parts.append(
                f"### {a['id']} — `{a['severity']}`{mark}\n\n"
                f"{escape(a['claim'])}\n\n"
                f"- {t('report.evidence')}: {ev}\n"
                f"- {t('report.consequence')}: {escape(a['consequence'])}\n"
                f"- {t('report.status')}: `{a['status']}`\n"
                + (f"- {t('report.suggested_form')}: {escape(a['form'])}\n" if a.get("form")
                   else f"- {t('report.form_not_proposed')}\n"))
        return "\n".join(parts)

    def sec_observations():
        """What the change did NOT cause. Outside the count, outside the verdict, and
        outside what the author is asked for — but not erased: somebody chose to look
        and saw it."""
        if not observations:
            return t("report.no_observations")
        body = [t("report.pre_existing_note", **vocabulary()) + "\n"]
        for a in observations:
            ev = "; ".join(f"`{e['file']}`:{e.get('line') or '?'}" for e in a["evidence"])
            body.append(f"- **{a['id']}** (`{a['severity']}`) — {escape(a['claim'])}\n"
                        f"  - {t('report.where')}: {ev}")
        return "\n".join(body)

    def sec_capabilities():
        rows = [f"| {t('report.col_role')} | {t('report.col_command')} | "
                f"{t('report.col_available')} | {t('report.col_note')} |", "|---|---|---|---|"]
        for role, c in cap["checks"].items():
            rows.append(f"| `{role}` | `{c['command'] or '—'}` | "
                        f"{t('report.available_yes') if c['available'] else '**' + t('report.available_no') + '**'}"
                        f" | {c['reason'] or ''} |")
        if cap.get("warnings"):
            rows.append("\n" + "\n".join(f"- {escape(a)}" for a in cap["warnings"]))
        return "\n".join(rows)

    def sec_gates():
        """The rule, before any route. Five lines; all of them have to pass."""
        rows = [f"| | {t('report.col_condition')} | {t('report.col_what_closed_it')} |",
                "|---|---|---|"]
        for gate in verd.get("gates", []):
            mark = "✅" if gate["passed"] else "❌"
            # The id of whatever closed the gate, WITH the question it asks. A bare
            # `A8` here is the one place a reader cannot skip, and it was the one place
            # that printed a code and nothing else.
            detail = "" if gate["passed"] else named(gate["items"], meaning)
            rows.append(f"| {mark} | **{gate['number']}.** {escape(gate['condition'])} "
                        f"| {detail} |")
        baseline = verd["summary"].get("repo_baseline")
        footer = "\n\n> " + t("report.rule_note")
        if baseline:
            footer += "\n>\n> " + t("report.baseline_note", baseline=escape(baseline))
        stale = verd["summary"].get("stale_base")
        if stale:
            footer += "\n>\n> " + escape(stale)
        return "\n".join(rows) + footer

    def sec_routes():
        if verd["verdict"] == "APPROVED":
            return (t("report.nothing_open") + "\n\n"
                    + "> " + t("report.approve_not_merge", **vocabulary()))
        return routes_header() + routes_table(verd, meaning=meaning)

    template = (DIR_TEMPLATES / "report.md").read_text(encoding="utf-8")
    return template.format(
        repo=man["repo"], item_id=man["item"]["id"] or t("report.unresolved"),
        profile_status=profile.get("status", "absent"),
        profile_version=profile.get("version", 0),
        coverage=verd["summary"]["profile_coverage"], origin=man["origin"],
        base_ref=man["base"]["ref"], base_sha_short=man["base"]["sha"][:12],
        base_source=man["base"]["source"], sha_initial=man["merge_base"][:12],
        sha_final=man["head_sha"][:12], lines=size["lines"], band=size["band"],
        date=today(), version=VERSION,
        # The tracker's vocabulary runs through the template: `card`, `issue`, `ticket`.
        # It lives here and not in the template because a report on disk has to say the
        # name THAT installation uses, and there is one template for all of them.
        item=tracker().item, item_with_article=tracker().with_article(),
        item_title=tracker().item.capitalize(),
        section_gates=sec_gates(), section_routes=sec_routes(),
        section_criteria=sec_criteria(), section_proof=sec_proof(),
        section_conventions=sec_conventions(),
        section_a=class_list(classes, "A", "fired") + "\n\n" + t("report.did_not_fire")
                  + "\n\n" + class_list(classes, "A", "did_not_fire"),
        section_b=class_list(classes, "B", "fired") + "\n\n" + t("report.did_not_fire")
                  + "\n\n" + class_list(classes, "B", "did_not_fire"),
        section_not_assessable=sec_not_assessable(), section_findings=sec_findings(),
        section_observations=sec_observations(),
        section_capabilities=sec_capabilities(), verdict=t(READABLE[verd["verdict"]]),
        section_project_pending=project_pending_table(verd, meaning=meaning),
        verdict_detail=(t("report.verdict_clean") if verd["verdict"] == "APPROVED"
                        else t("report.verdict_routes",
                               n=len([r for r in verd["routes"]
                                      if r.get("origin", "delivery") == "delivery"]))))


# How the suite ended, in one line. The full reason lives in the report; the item comment
# states the STATE, because that is what decides, and the long sentence would drown the
# rest. The keys on the left are DATA, written by the evidence step.
SUITE_READABLE = {
    "green": "suite.green",
    "red_not_attributable": "suite.red_baseline",
    "red_attributable": "suite.red_delivery",
    "not_run": "suite.not_run",
}


def build_item_comment(package: pathlib.Path) -> str:
    """The item comment is a RECORD of fixed shape, and it STANDS ON ITS OWN.

    It states what was measured and what holds the verdict up. No narrative, no
    suggestion, no pending item in prose. The shape is the same on every run, so that two
    reviews of the same repository can be compared by reading top to bottom.

    **It never points at a file.** Until 2026-11-04 the last line carried the report's
    path, and that path is meaningless to everybody except whoever ran the review: the
    reader of the item is on another machine, and a local path tells them a report exists
    without letting them read a line of it. So what decides now travels INSIDE the
    comment — the five gates, the acceptance criteria with their records, and the
    findings with their evidence.

    **It carries only what THIS delivery caused.** A finding the change did not cause
    (`attributable: false`) does not appear: the comment is the record of a verdict on a
    delivery, and the item it hangs on belongs to whoever wrote that delivery. Listing
    the repository's pre-existing defects beside theirs reads as a list of things they
    have to answer for, which is exactly what `attributable` exists to prevent. The
    observations are not erased — somebody chose to look and saw them — they live in the
    report, where the repository's owner reads them.

    What stays out for the same reason is what is an instruction for somebody to execute
    later — the manual scripts and this environment's capabilities. Those are work
    orders, not the record of a verdict.

    The project's pending items DO stay, and they are not findings: they are the declared
    limit of this review's own reach, with owners. They are what the `Measured` block's
    "nine not assessable" points at, and dropping them would leave that number unexplained
    on the very comment that states the verdict.
    """
    def optional(name, default):
        path = package / name
        return read_json(path) if path.exists() else default

    man, verd = read_json(package / "manifest.json"), optional("verdict.json", {})
    summary = verd.get("summary", {})
    evidence, criteria = optional("evidence.json", {}), optional("criteria.json", [])
    findings, size = optional("findings.json", []), optional("size.json", {})
    meaning = meanings_for(optional("classes.json", []), criteria,
                           findings + optional("observations.json", []))

    # Class ids whose question this comment has already spelled out. A second mention
    # prints the bare id: the reader met it a few lines above.
    explained: set[str] = set()

    measured = []
    suite = evidence.get("suite")
    if suite:
        readable = t(SUITE_READABLE[suite]) if suite in SUITE_READABLE else suite
        measured.append(f"<li><strong>{t('comment.label_suite')}</strong> "
                        f"{escape(readable)}</li>")
    for role, c in (evidence.get("checks") or {}).items():
        state = t("comment.ran") if c.get("ran") else t("comment.not_ran")
        measured.append(f"<li><strong>{escape(role.capitalize())}:</strong> {state}</li>")
    if summary:
        unassessed = summary.get("classes_not_assessable") or []
        # `cobertura_de_perfil` arrives already formatted as "N/18" — the judge builds it.
        # The ids ALONE say nothing to the person reading the item: `A4, A7, A9` is the
        # harness talking to itself. Each one goes in with the question it asks, and is
        # recorded as explained so that a route below does not repeat it.
        unassessed_detail = "".join(
            f"<li>{escape(cid)} — {escape(class_question(cid))}</li>" for cid in unassessed)
        explained.update(c for c in unassessed if c in CLASSES)
        measured.append(
            f"<li><strong>{t('comment.label_risk_classes')}</strong> "
            + t("comment.classes_value",
                assessed=escape(str(summary.get("profile_coverage", "?"))),
                unassessed=len(unassessed))
            + (f"<ul>{unassessed_detail}</ul>" if unassessed else "") + "</li>")
    if criteria:
        met = sum(1 for c in criteria if c.get("state") == "met")
        measured.append(f"<li><strong>{t('comment.label_acceptance')}</strong> "
                        + t("comment.acceptance_value", met=met, total=len(criteria))
                        + "</li>")
    if size.get("lines") is not None:
        measured.append(f"<li><strong>{t('comment.label_size')}</strong> "
                        + t("comment.size_value", lines=size["lines"],
                            band=escape(str(size.get("band", "?")))) + "</li>")
    blocking = [a["id"] for a in findings
                if a.get("severity") in BLOCKING_SEVERITIES
                and a.get("status") != "refuted" and a.get("attributable", True)]
    measured.append(
        f"<li><strong>{t('comment.label_findings')}</strong> "
        + (t("comment.findings_value", total=len(findings), blocking=len(blocking),
             ids=escape(', '.join(blocking))) if blocking
           else t("comment.findings_none_blocking", total=len(findings))) + "</li>")

    def section(label_key: str, items: list[str]) -> str:
        """A titled list, or nothing at all. An empty section is noise: a reader who sees
        a heading with no body cannot tell 'there was none' from 'it was not looked
        at'."""
        return (f"<p><strong>{t(label_key)}</strong></p><ul>" + "".join(items) + "</ul>"
                if items else "")

    def rule() -> str:
        """The five gates, verbatim from the judge. This is what decides, so it opens the
        comment — before the measurements that feed it."""
        lines = []
        for gate in verd.get("gates", []):
            mark = "✅" if gate["passed"] else "❌"
            # The same rule the routes below follow: an id goes in with what it asks,
            # unless `Measured` spelled it out a few lines above. This block used to be
            # the one place in the comment that still printed `A8` and nothing else —
            # and it is the line that states WHY the verdict is what it is.
            fresh = [i for i in gate["items"] if i in CLASSES and i not in explained]
            explained.update(fresh)
            detail = ("" if gate["passed"]
                      else " → " + "; ".join(
                          f"{escape(i)} ({escape(m)})"
                          if (m := meaning(i)) and (i in fresh or i not in CLASSES)
                          else escape(i) for i in gate["items"]))
            lines.append(f"<li>{mark} <strong>{gate['number']}.</strong> "
                         f"{escape(gate['condition'])}{detail}</li>")
        block = section("comment.the_rule", lines)
        # Measured facts that decide nothing but explain the verdict: the repository's
        # own red baseline, and a proposal sitting behind its base. Both sit next to the
        # rule because that is where a reader asks "and what about…".
        for note in (summary.get("repo_baseline") and
                     f"{t('comment.baseline_note')} {escape(summary['repo_baseline'])}",
                     summary.get("stale_base") and escape(summary["stale_base"])):
            if block and note:
                block += f"<p><em>{note}</em></p>"
        return block

    def route_items(origin: str) -> list[str]:
        """A route's items are class ids as often as not, and a bare `A4` is the harness
        talking to itself. A class gets its question here UNLESS `Measured` already
        explained it a few lines above — repeating nine long questions inside the same
        comment doubles its length and teaches the reader nothing twice."""
        def listed(items):
            fresh = [i for i in items if i in CLASSES and i not in explained]
            if not fresh:
                return escape(", ".join(items))
            explained.update(fresh)
            return "<ul>" + "".join(
                f"<li>{escape(i)}" + (f" — {escape(class_question(i))}" if i in CLASSES else "")
                + "</li>" for i in items) + "</ul>"

        out = []
        for r in verd.get("routes", []):
            if r.get("origin", "delivery") != origin:
                continue
            items = listed(r["items"])
            # A list closes with `</ul>`; a comma-separated run does not. The separator
            # before the detail has to know which one it just printed.
            detail = (" ".join(r.get("details") or []) or r["closes_when"]
                      if origin == "project" else "")
            joiner = "" if items.endswith("</ul>") else " — "
            out.append(f"<li><strong>{escape(t(ROUTE_READABLE[r['type']]))}, "
                       f"{escape(r['owner'])}:</strong> {items}"
                       + (f"{joiner}{escape(detail)}" if detail else "") + "</li>")
        return out

    def criteria_items() -> list[str]:
        """Every criterion with its state AND its record. The record is the whole point:
        `met` with no evidence behind it is an opinion."""
        return [f"<li><strong>{escape(c.get('text', c['id']))}</strong> — "
                f"<code>{escape(c['state'])}</code>"
                + (f"<br>{escape(c['record'])}" if c.get("record") else "") + "</li>"
                for c in criteria]

    body = f"<p><strong>{t('comment.measured')}</strong></p><ul>" + "".join(measured) + "</ul>"
    body = rule() + body
    body += section("comment.open_routes", route_items("delivery"))
    body += section("comment.section_criteria", criteria_items())
    body += section("comment.section_project_pending", route_items("project"))

    template = (DIR_TEMPLATES / "item-comment.html").read_text(encoding="utf-8")
    return template.format(

        title=tracker().heading_for(t("comment.title", verdict=t(READABLE[verd["verdict"]]))),
        proposal_label=escape(forge().label(re.sub(r"\D", "", man["origin"]) or "?")),
        item=escape(tracker().item),
        sha_initial=escape(man["merge_base"][:12]), sha_final=escape(man["head_sha"][:12]),
        body=body,
        not_merge=escape(t("comment.not_merge", item=tracker().item,
                          item_with_article=tracker().with_article())))


def report_path(man: dict) -> str:
    item_id = man["item"]["id"] or "no-item"
    # Relative to the configured reports root, never a hard-coded prefix: `paths.reports`
    # is an installation fact, and printing a path that contradicts it sends the reader
    # looking in a directory that does not exist on their machine.
    return str((dir_reports() / man["repo"] /
                f"{item_id}-{slug(man['origin'])}" / f"review-{today()}.md"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--package", "--pacote", dest="pacote", required=True)
    p.add_argument("--output", "--saida", dest="saida")
    args = p.parse_args()

    package = pathlib.Path(args.pacote)
    output = pathlib.Path(args.saida or package)
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.md").write_text(build_report(package), encoding="utf-8")
    (output / "item-comment.html").write_text(build_item_comment(package), encoding="utf-8")
    man = read_json(package / "manifest.json")
    print(t("review.rendered", path=f"{output}/report.md"))
    print("        " + t("review.final_destination", path=report_path(man)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
