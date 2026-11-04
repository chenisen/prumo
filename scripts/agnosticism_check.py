#!/usr/bin/env python3
"""Mechanical proof that the method knows no repository.

Agnosticism that no machine checks is lost again at the first edit. There are four
rules; the first three run here, and the fourth needs a real proposal.

Usage:
    agnosticism_check.py [--verbose]
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import CLASSES, cfg, dir_profiles, dir_repositories, PRUMO_ROOT
from i18n import t

# THE METHOD: everything that has to hold in any repository.
METHOD_TARGETS = [
    PRUMO_ROOT / "skills",
    PRUMO_ROOT / "reference",
    PRUMO_ROOT / "schemas",
    PRUMO_ROOT / "templates",
    PRUMO_ROOT / "scripts",
    PRUMO_ROOT / "workflows" / "task-review.js",
    PRUMO_ROOT / "hooks" / "review-guard.sh",
]
AGENTS = ["criteria-extractor", "scope-coherence-reviewer", "correctness-proof-reviewer",
          "domain-rules-reviewer", "contract-compat-reviewer", "security-privacy-reviewer",
          "architecture-reviewer", "performance-ops-reviewer", "finding-verifier",
          "manual-verification-planner"]

# Patterns that give coupling away. A repository name is discovered from disk, never
# written here. The UNIVERSAL ones hold in any installation: they name technology, not a
# company. A stack tool or a database nailed into the method couples the prumo to one
# ecosystem, whichever it happens to be.
UNIVERSAL_FORBIDDEN = [
    ("stack tool", r"(?i)\b(larastan|phpstan|phpunit|pest|artisan|eloquent|composer|"
                   r"vitest|playwright|eslint|tsconfig|next\.config|prisma)\b"),
    ("assumed language", r"(?i)\b(flat[- ]laravel|laravel|symfony|django|rails|express)\b"),
    ("named storage", r"(?i)\b(mysql|postgres|postgresql|mongo|mongodb|redis)\b"),
]

# The rest belongs to the INSTALLATION, and comes from `prumo.yml`. This very guardrail
# was born carrying ONE company's list of vendors, tables and id range written inside it
# — the same bug it exists to catch, committed by it. A public prumo cannot carry the
# names of the internal systems of whoever wrote it first.
#
#   agnosticism:
#     forbidden:
#       - label: "named vendor"
#         pattern: "(?i)\\b(acme|someone-erp)\\b"
#
# Without the section only the universal patterns hold — and the report says how many
# patterns ran.
def installation_forbidden():
    try:
        extras = cfg().agnosticism
    except Exception:
        return []
    return [(e["label"], e["pattern"]) for e in extras]


def forbidden():
    return UNIVERSAL_FORBIDDEN + installation_forbidden()

# Justified exceptions. Each entry is a deliberate decision and carries its why: an
# exemption with no written reason is coupling waiting to come back.
# "repo" exempts a repository name; the other labels come from forbidden().
ALL_LABELS = [c for c, _ in UNIVERSAL_FORBIDDEN] + ["repo", "item id",
              "project collection/table", "named vendor"]
EXEMPT = {
    # RECOGNITION tables: to classify a repo you have to know what to look for.
    # Adding a stack or a proof registry is editing the table, not the method.
    "derive_profile.py": ["stack tool", "assumed language", "named vendor"],
    "test_derive_profile.py": ["stack tool", "assumed language", "named vendor"],
    # REPLAY of real reviews: the case only proves something if it names what actually
    # happened.
    "test_replay_revisoes.py": ["item id", "named vendor", "repo"],
    "test_render.py": ["item id"],
    # fixtures that exercise triage's universal floor
    "test_triage.py": ["stack tool"],
    # this file itself lists the patterns it looks for
    "agnosticism_check.py": ALL_LABELS,
}


def method_files() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for target in METHOD_TARGETS:
        if target.is_file():
            out.append(target)
        elif target.is_dir():
            out += [p for p in target.rglob("*")
                    if p.is_file() and p.suffix in (".md", ".py", ".js", ".sh", ".json", ".html", ".yml")
                    and "__pycache__" not in p.parts]
    base = PRUMO_ROOT
    out += [base / "agents" / f"{a}.md" for a in AGENTS]
    return [p for p in out if p.exists()]


def rule_1_no_repository_name() -> list[str]:
    repos = sorted((d.name for d in dir_repositories().iterdir() if d.is_dir()), key=len, reverse=True)
    failures = []
    for p in method_files():
        text = p.read_text(encoding="utf-8", errors="ignore")
        exempt = EXEMPT.get(p.name, [])
        for n, line in enumerate(text.splitlines(), 1):
            if "repo" not in exempt:
                for repo in repos:
                    if re.search(rf"(?<![\w-]){re.escape(repo)}(?![\w-])", line):
                        failures.append(t("agnosticism.failure_repo_name",
                                          file=p.relative_to(PRUMO_ROOT), line=n, repo=repo))
            for label, pattern in forbidden():
                if label in exempt:
                    continue
                m = re.search(pattern, line)
                if m:
                    failures.append(t("agnosticism.failure_forbidden",
                                      file=p.relative_to(PRUMO_ROOT), line=n,
                                      label=label, match=m.group(0)))
    return failures


def rule_2_profiles_validate() -> list[str]:
    from validate_profile import load_schema, validate
    import yaml
    schema = load_schema()
    failures = []
    profiles = [f for f in dir_profiles().glob("*.yml") if not f.stem.startswith("_")]
    if not profiles:
        return [t("agnosticism.no_profiles")]
    for f in profiles:
        errors, _ = validate(yaml.safe_load(f.read_text(encoding="utf-8")) or {}, schema)
        failures += [t("agnosticism.profile_error", file=f.name, error=e) for e in errors]
    return failures


def rule_3_suite_passes() -> list[str]:
    r = subprocess.run((sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"),
                       cwd=pathlib.Path(__file__).resolve().parent,
                       capture_output=True, text=True)
    return [] if r.returncode == 0 else [r.stdout.strip() or r.stderr.strip()]


def rule_4_two_families() -> tuple[list[str], str]:
    """Agnosticism never exercised on two stacks is a claim, not a property."""
    import yaml
    families = {}
    for f in dir_profiles().glob("*.yml"):
        if f.stem.startswith("_"):
            continue
        d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        families.setdefault((d.get("stack") or {}).get("family", "?"), []).append(f.stem)
    if len(families) < 2:
        return ([t("agnosticism.single_family", families=families)], "")
    return ([], t("agnosticism.families_note", n=len(families),
                  detail="; ".join(f"{k} ({len(v)})" for k, v in sorted(families.items()))))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    rules = [
        (f"1. {t('agnosticism.rule_1')}", rule_1_no_repository_name()),
        (f"2. {t('agnosticism.rule_2')}", rule_2_profiles_validate()),
        (f"3. {t('agnosticism.rule_3')}", rule_3_suite_passes()),
    ]
    failures_4, note_4 = rule_4_two_families()
    rules.append((f"4. {t('agnosticism.rule_4')}", failures_4))

    any_failed = False
    for title, failures in rules:
        print(f"{'✗' if failures else '✓'} {title}")
        for f in failures[: (None if args.verbose else 12)]:
            print(f"    {f}")
        if failures and not args.verbose and len(failures) > 12:
            print(f"    {t('agnosticism.more_failures', n=len(failures) - 12)}")
        any_failed = any_failed or bool(failures)
    if note_4:
        print(f"    {note_4}")

    print("\n" + t("agnosticism.header", n=len(method_files()), classes=len(CLASSES)))
    print("\n" + t("agnosticism.rule_4_note"))
    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
