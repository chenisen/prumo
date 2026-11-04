#!/usr/bin/env python3
"""Validates a review profile against the contract, and against the rules the schema
cannot reach.

Usage:
    validate_profile.py <repo> [<repo> ...]
    validate_profile.py --all
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import yaml
import jsonschema

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import (CLASSES, PRUMO_ROOT, dir_profiles, CHECK_ROLES, OWNER_TOKEN,
                   profile_path, valid_date, waiver_in_force, owner,
                   unresolved_tokens)
from i18n import t


def schema_path():
    """The profile's contract belongs to the METHOD, not to the installation: it lives
    inside the prumo itself.

    It used to sit beside the profiles, in the tree of whoever installed it — which made
    the contract user-versioned and let it drift between workspaces. A profile that
    validates here and fails there is the same problem `prumo.yml` solves for provider
    and path."""
    return PRUMO_ROOT / "schemas" / "profile.schema.yml"


def load_schema() -> dict:
    return yaml.safe_load(schema_path().read_text(encoding="utf-8"))


def validate(profile: dict, schema: dict | None = None) -> tuple[list[str], list[str]]:
    """Returns (errors, warnings). An error fails the profile; a warning shows and does
    not fail it."""
    schema = schema or load_schema()
    errors: list[str] = []
    warnings: list[str] = []

    for e in sorted(jsonschema.Draft202012Validator(schema).iter_errors(profile),
                    key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in e.path) or t("validate.schema_root")
        errors.append(f"{path}: {e.message}")
    if errors:
        return errors, warnings  # with no valid shape, the rules below have nothing to read

    # --- confirming is a human act, and it needs a name and a date
    if profile["status"] == "confirmed":
        for field in ("confirmed_by", "confirmed_at"):
            if not profile.get(field):
                errors.append(t("validate.confirmation_requires", field=field))
        if profile.get("confirmed_at") and not valid_date(profile["confirmed_at"]):
            errors.append(t("validate.confirmed_at_date"))

    # --- whoever signs comes from the environment, never from the versioned file
    if unresolved_tokens(profile) and not owner():
        warnings.append(t("validate.owner_unresolved", token=OWNER_TOKEN))

    # --- anchors: known class, §B with a verification, complete nao_se_aplica
    for klass, anchor in (profile.get("anchors") or {}).items():
        if klass not in CLASSES:
            errors.append(t("validate.unknown_class", klass=klass, classes=sorted(CLASSES)))
            continue
        if "not_applicable" in anchor:
            if not valid_date(anchor["not_applicable"].get("date", "")):
                errors.append(t("validate.not_applicable_date", klass=klass))
            continue
        empty = not (anchor.get("paths") or anchor.get("patterns"))
        if empty:
            errors.append(t("validate.anchor_empty", klass=klass))
        if CLASSES[klass]["section"] == "B" and not anchor.get("verification"):
            errors.append(t("validate.anchor_needs_verification", klass=klass))

    # --- checks: waiver complete and not expired; required role with no command
    for role in CHECK_ROLES:
        c = (profile.get("checks") or {}).get(role) or {}
        waiver = c.get("waiver")
        if waiver and not valid_date(waiver.get("expires", "")):
            errors.append(t("validate.waiver_date", role=role))
        if c.get("command") is None and c.get("required"):
            if not waiver:
                warnings.append(t("validate.check_without_tool", role=role))
            elif not waiver_in_force(waiver):
                warnings.append(t("validate.waiver_expired", role=role,
                                  date=waiver.get("expires")))

    # --- evals pointing at a class with no anchor measure nothing
    for i, ev in enumerate(profile.get("evals") or []):
        klass = ev["class"]
        if klass == "nenhuma":
            continue
        if klass not in CLASSES:
            errors.append(t("validate.eval_unknown_class", i=i, klass=klass))
        elif ev["expects"] == "fire" and klass not in (profile.get("anchors") or {}):
            warnings.append(t("validate.eval_without_anchor", i=i, klass=klass))

    # --- coverage, so the report knows how much of the repo is under review
    if profile["status"] == "confirmed":
        mapped = len([c for c in (profile.get("anchors") or {}) if c in CLASSES])
        if mapped < 4:
            warnings.append(t("validate.thin_coverage", mapped=mapped))
    return errors, warnings


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("repos", nargs="*")
    p.add_argument("--all", action="store_true", help="validate every profile in review-profiles/")
    args = p.parse_args()

    targets = args.repos
    if args.all:
        targets = sorted(f.stem for f in dir_profiles().glob("*.yml") if not f.stem.startswith("_"))
    if not targets:
        p.error(t("cli.name_repo_or_all"))

    schema = load_schema()
    had_error = False
    for repo in targets:
        path = profile_path(repo)
        if not path.exists():
            print("✗ " + t("validate.profile_missing", repo=repo, path=path))
            had_error = True
            continue
        profile = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        errors, warnings = validate(profile, schema)
        mark = "✗" if errors else "✓"
        mapped = len([c for c in (profile.get("anchors") or {}) if c in CLASSES])
        print(f"{mark} " + t("validate.line", repo=repo, status=profile.get("status"),
                             mapped=mapped))
        for e in errors:
            print("    " + t("validate.error", text=e))
        for a in warnings:
            print("    " + t("validate.warning", text=a))
        had_error = had_error or bool(errors)
    return 1 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
