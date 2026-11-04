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
from common import (CLASSES, PRUMO_ROOT, BLOCKING_SEVERITIES, dir_reports, load_profile, forge, tracker,
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


def routes_table(verdict: dict, origin: str = "delivery") -> str:
    rows = []
    target = [r for r in verdict["routes"] if r.get("origin", "delivery") == origin]
    for i, r in enumerate(target, 1):
        items = ", ".join(f"`{x}`" for x in r["items"])
        detail = " ".join(r.get("details") or []) or r["closes_when"]
        rows.append(f"| {i} | {t(ROUTE_READABLE[r['type']])} | {r['owner']} | "
                    f"{items} — {detail} |")
    return "\n".join(rows) or f"| — | — | — | {t('report.no_open_routes')} |"


def routes_header() -> str:
    return (f"| # | {t('report.col_route')} | {t('report.col_owner')} | "
            f"{t('report.col_what')} |\n|---|---|---|---|\n")


def project_pending_table(verdict: dict) -> str:
    """Debt of the repository and of the harness: always declared, always charged to
    whoever created it.

    It sits in a table of its own so that nobody reads it as a pending item of THIS
    delivery — the same reason `attributable` exists on a finding."""
    if not any(r.get("origin") == "project" for r in verdict["routes"]):
        return t("report.project_pending_none")
    return (t("report.project_pending_note", **vocabulary()) + "\n\n"
            + routes_header() + routes_table(verdict, origin="project"))


def class_list(classes: list[dict], section: str, state: str) -> str:
    target = [c for c in classes if c["section"] == section and c["state"] == state]
    if not target:
        return t("report.none")
    return "\n".join(
        f"- **{c['id']}** — {CLASSES[c['id']]['question']}  \n  {c['reason']}" for c in target)


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
            body.append(f"| **{c['id']}** | {CLASSES[c['id']]['question']} | {c['reason']} |")
        body.append("\n" + t("report.two_exits", **vocabulary()))
        return "\n".join(body)

    def sec_findings():
        if not findings:
            return t("report.no_findings")
        parts = []
        for a in sorted(findings, key=lambda x: ("critico obrigatorio nit opcional fyi"
                                                 .split().index(x["severity"]))):
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
        rows = [f"| | {t('report.col_condition')} | |", "|---|---|---|"]
        for gate in verd.get("gates", []):
            mark = "✅" if gate["passed"] else "❌"
            detail = ("" if gate["passed"]
                      else "  →  " + ", ".join(f"`{i}`" for i in gate["items"]))
            rows.append(f"| {mark} | **{gate['number']}.** {escape(gate['condition'])} "
                        f"| {detail} |")
        baseline = verd["summary"].get("repo_baseline")
        footer = "\n\n> " + t("report.rule_note")
        if baseline:
            footer += "\n>\n> " + t("report.baseline_note", baseline=escape(baseline))
        return "\n".join(rows) + footer

    def sec_routes():
        if verd["verdict"] == "APPROVED":
            return (t("report.nothing_open") + "\n\n"
                    + "> " + t("report.approve_not_merge", **vocabulary()))
        return routes_header() + routes_table(verd)

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
        section_project_pending=project_pending_table(verd),
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
    """The item comment is a RECORD, not a report.

    It states what was measured and what holds the verdict up, in a fixed shape. No
    narrative, no suggestion, no pending item in prose: whoever needs the reasoning opens
    the report, whose path is on the last line. The shape is the same on every run, so
    that two reviews of the same repository can be compared by reading top to bottom.
    """
    def optional(name, default):
        path = package / name
        return read_json(path) if path.exists() else default

    man, verd = read_json(package / "manifest.json"), optional("verdict.json", {})
    summary = verd.get("summary", {})
    evidence, criteria = optional("evidence.json", {}), optional("criteria.json", [])
    findings, size = optional("findings.json", []), optional("size.json", {})

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
        measured.append(
            f"<li><strong>{t('comment.label_risk_classes')}</strong> "
            + t("comment.classes_value",
                assessed=escape(str(summary.get("profile_coverage", "?"))),
                unassessed=len(unassessed))
            + (f" ({escape(', '.join(unassessed))})" if unassessed else "") + "</li>")
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

    body = f"<p><strong>{t('comment.measured')}</strong></p><ul>" + "".join(measured) + "</ul>"
    if verd.get("verdict") != "APPROVED":
        items = "".join(
            f"<li><strong>{escape(t(ROUTE_READABLE[r['type']]))}, {escape(r['owner'])}:"
            f"</strong> {escape(', '.join(r['items']))}</li>" for r in verd.get("routes", []))
        body += f"<p><strong>{t('comment.open_routes')}</strong></p><ul>{items}</ul>"

    template = (DIR_TEMPLATES / "item-comment.html").read_text(encoding="utf-8")
    return template.format(

        title=tracker().heading_for(t("comment.title", verdict=t(READABLE[verd["verdict"]]))),
        proposal_label=escape(forge().label(re.sub(r"\D", "", man["origin"]) or "?")),
        item=escape(tracker().item),
        sha_initial=escape(man["merge_base"][:12]), sha_final=escape(man["head_sha"][:12]),
        body=body,
        report_label=t("comment.report_label"),
        not_merge=escape(t("comment.not_merge", item=tracker().item,
                          item_with_article=tracker().with_article())),
        report_path=escape(report_path(man)))


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
