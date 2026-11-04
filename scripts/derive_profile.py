#!/usr/bin/env python3
"""Derives the DRAFT of a repository's profile, by reading the repository.

The deriver never confirms what it derived: it hands back `status: rascunho` and the list
of what it could not fill in. Confirming is a human act, once per repository.

Usage:
    derive_profile.py <repo> [...] [--output DIR] [--force]
    derive_profile.py --all
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import CLASSES, cfg, dir_profiles, dir_repositories, profile_path
from i18n import t

# --- Stack families the manifest recognises. Adding a family is editing this table, not
#     the method: no script decides anything from a repository's name.
FAMILIES = [
    dict(family="php-laravel", manifest="composer.json", marker="artisan",
         conventions=["CLAUDE.md", ".claude/docs/"],
         checks=dict(estatico="phpstan.neon", lint="pint.json", suite="phpunit.xml"),
         schema=["database/migrations/**"],
         contract=["routes/**", "app/Http/Resources/**", "app/Http/Controllers/**"],
         tests=["tests/**"]),
    dict(family="node-next", manifest="package.json", marker="next.config.js",
         conventions=["CLAUDE.md", "docs/"],
         checks=dict(estatico="tsconfig.json", lint=".eslintrc.json", suite="vitest.config.ts"),
         schema=["prisma/migrations/**", "drizzle/**"],
         contract=["app/api/**", "src/app/api/**", "pages/api/**"],
         tests=["**/*.test.ts", "**/*.test.tsx", "e2e/**", "tests/**"]),
    dict(family="node", manifest="package.json", marker=None,
         conventions=["CLAUDE.md", "docs/"],
         checks=dict(estatico="tsconfig.json", lint=".eslintrc.json", suite=None),
         schema=[], contract=[], tests=["test/**", "tests/**"]),
    dict(family="php", manifest="composer.json", marker=None,
         conventions=["CLAUDE.md"],
         checks=dict(estatico="phpstan.neon", lint=None, suite="phpunit.xml"),
         schema=[], contract=[], tests=["tests/**"]),
]

FAMILY_COMPOSITE = dict(family="composicao", manifest=None, marker=None,
                        conventions=["CLAUDE.md", "README.md"], checks={},
                        schema=[], contract=[], tests=[])

# How each family fills the check roles, WHEN the tool exists in the repo. A function,
# not a constant, for the same reason the paths in `common` are: these are placeholders a
# person reads, so they are resolved in the configured language — and the catalogue is
# read on use, never on import.
def commands() -> dict:
    return {
        "php-laravel": dict(estatico=t("derive.cmd_static"), lint=t("derive.cmd_lint"),
                            dependencias=t("derive.cmd_audit"),
                            suite=t("derive.cmd_suite_container")),
        "php":         dict(estatico=t("derive.cmd_static"), lint=None,
                            dependencias=t("derive.cmd_audit"),
                            suite=t("derive.cmd_suite_container")),
        "node-next":   dict(estatico=t("derive.cmd_typecheck"), lint=t("derive.cmd_lint"),
                            dependencias=t("derive.cmd_audit"), suite=t("derive.cmd_suite")),
        "node":        dict(estatico=t("derive.cmd_typecheck"), lint=t("derive.cmd_lint"),
                            dependencias=t("derive.cmd_audit"), suite=None),
        "composicao":  dict(estatico=None, lint=None, dependencias=None, suite=None),
    }


# Proof registry: the marker in the repository decides the type. The list comes from the
# configuration — the public ones by default, plus whatever the installation declares
# under `detection`.
def registries():
    return [(r["type"], r["marker"], r["note"]) for r in cfg().proof_registries]


def run_command(repo_dir: pathlib.Path, *args: str) -> str:
    try:
        r = subprocess.run(args, cwd=repo_dir, capture_output=True, text=True, timeout=20)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def detect_family(repo_dir: pathlib.Path) -> dict:
    for f in FAMILIES:
        if not (repo_dir / f["manifest"]).exists():
            continue
        if f["marker"] and not (repo_dir / f["marker"]).exists():
            continue
        return f
    return FAMILY_COMPOSITE


def detect_base(repo_dir: pathlib.Path) -> tuple[str, str | None]:
    """Resolved, never presumed: named base -> origin/HEAD -> whatever exists."""
    head = run_command(repo_dir, "git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    default = head.split("/", 1)[1] if "/" in head else (head or "main")
    has_homolog = bool(run_command(repo_dir, "git", "rev-parse", "--verify", "-q",
                                   "origin/homolog"))
    if has_homolog and default != "homolog":
        return default, t("derive.base_ambiguous", head=default)
    return default, None


def detect_registry(repo_dir: pathlib.Path) -> dict:
    for kind, marker, note in registries():
        if (repo_dir / marker).exists():
            return {"type": kind, "root": marker, "note": note}
    return {"type": "absent", "root": None, "note": t("derive.no_proof_registry")}


def existing(repo_dir: pathlib.Path, patterns: list[str]) -> list[str]:
    """Keeps only the patterns that match something present today — an anchor that does
    not resolve is noise."""
    alive = []
    for p in patterns:
        root = p.split("**")[0].split("*")[0].rstrip("/")
        if root and (repo_dir / root).exists():
            alive.append(p)
        elif not root and list(repo_dir.glob(p.replace("**/", ""))):
            alive.append(p)
    return alive


def derive(repo: str, repo_dir: pathlib.Path) -> tuple[dict, list[str]]:
    fam = detect_family(repo_dir)
    name = fam["family"]
    pending: list[str] = []

    manifests = [fam["manifest"]] if fam["manifest"] and (repo_dir / fam["manifest"]).exists() else []
    conventions = [c for c in fam["conventions"] if (repo_dir / c).exists()]
    if not conventions:
        pending.append(t("derive.pending_conventions"))

    branch, base_warning = detect_base(repo_dir)
    if base_warning:
        pending.append(t("derive.pending_base", reason=base_warning))

    registry = detect_registry(repo_dir)
    if registry["type"] == "absent":
        pending.append(t("derive.pending_proof_registry"))

    checks = {}
    by_family = commands()
    for role in ("secrets", "static", "lint", "dependencies", "suite"):
        if role == "secrets":
            checks[role] = {"command": None, "required": True}
            pending.append(t("derive.pending_secrets"))
            continue
        marker = fam.get("checks", {}).get(role)
        present = bool(marker) and (repo_dir / marker).exists()
        command = by_family.get(name, {}).get(role) if present else None
        checks[role] = {"command": command,
                        "required": role == "suite" and command is not None}
        if role == "suite" and command:
            checks[role]["preexisting_reds"] = None
            pending.append(t("derive.pending_suite_baseline"))
        if command and command.startswith("<"):
            pending.append(t("derive.pending_command", role=role))

    anchors: dict[str, dict] = {}
    for klass, patterns in (("A1", fam["schema"]), ("A2", fam["contract"]), ("A8", fam["tests"])):
        alive = existing(repo_dir, patterns)
        if alive:
            anchors[klass] = {"paths": alive, "cards": []}
    anchors["A9"] = {"paths": [".env*", "**/*.pem", "**/*.key", "**/*.p12"], "cards": []}
    if name == "composicao":
        anchors.pop("A9", None)

    without_anchor = [c for c, m in CLASSES.items()
                      if m["anchor_required"] and c not in anchors]
    if without_anchor:
        pending.append(t("derive.pending_anchors", classes=", ".join(sorted(without_anchor))))

    profile = {
        "profile": repo,
        "version": 1,
        "status": "draft",
        "confirmed_by": None,
        "confirmed_at": None,
        "stack": {"family": name, "manifests": manifests},
        "conventions": conventions,
        "proof_registry": registry,
        "base": {"branch": branch},
        "living_docs": [],
        "checks": checks,
        "sensitive_data": {"fields": [], "forbidden_destinations": []},
        "size": {"ideal": 100, "acceptable": 300, "split": 1000, "file_ceiling": 1000},
        "local_reviewer": None,
        "anchors": anchors,
        "evals": [],
        "pending": [],
    }
    return profile, pending


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("repos", nargs="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--output", default=None)  # resolved later, once the config is loaded
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing profile (without this, a confirmed one is refused)")
    args = p.parse_args()

    targets = args.repos
    if args.all:
        targets = sorted(d.name for d in dir_repositories().iterdir() if d.is_dir())
    if not targets:
        p.error(t("cli.name_repo_or_all"))

    from common import today
    out_dir = pathlib.Path(args.output) if args.output else dir_profiles()
    out_dir.mkdir(parents=True, exist_ok=True)

    for repo in targets:
        repo_dir = dir_repositories() / repo
        if not repo_dir.is_dir():
            print("✗ " + t("preflight.repo_missing", repo=repo, root=dir_repositories()))
            continue
        destination = out_dir / f"{repo}.yml"
        if destination.exists() and not args.force:
            import yaml as _y
            current = _y.safe_load(destination.read_text(encoding="utf-8")) or {}
            print("- " + t("derive.already_exists", repo=repo, status=current.get("status")))
            continue

        profile, pending = derive(repo, repo_dir)
        body = yaml.safe_dump(profile, allow_unicode=True, sort_keys=False, width=100)
        header = t("derive.header", repo=repo, date=today(),
                   pending="\n".join(f"#   - {x}" for x in pending)
                           or ("#   " + t("derive.header_nothing_pending")))
        destination.write_text(header + "\n" + body, encoding="utf-8")
        print("✓ " + t("derive.done", repo=repo, family=profile["stack"]["family"],
                       base=profile["base"]["branch"],
                       proof=profile["proof_registry"]["type"],
                       anchors=len(profile["anchors"]), pending=len(pending)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
