#!/usr/bin/env python3
"""F0 — measures what THIS environment can actually do and writes `capabilities.json`.

It replaces every sentence of the form "today this is blocked" inside the method: what
is true about the environment is measured on every run, never written down in prose that
rots quietly while the environment moves.

A missing or draft profile does NOT stop the run: it becomes the `missing_profile` route
at the judge, declared in the report and outside the verdict.

Usage:
    preflight.py --repo <repo> [--package DIR]
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import (MissingConfiguration, InvalidConfiguration, cfg as common_cfg, dir_repositories, CHECK_ROLES, profile_path, load_profile,
                   waiver_in_force, write_json, today, unresolved_tokens)
from i18n import t

# External tools the harness would reach for. Absent is not an error: it is a measured
# capability, and the report says which ones this environment happens to have.
TOOLS = ("git", "node", "jq", "docker")


def tools(cfg):
    """The fixed ones, plus the configured forge's CLI — `glab`, `gh`, whatever it is. A
    provider with no official CLI (Bitbucket) simply adds no line."""
    all_tools = {name: t(f"preflight.tool_for.{name}") for name in TOOLS}
    if cfg.forge.cli:
        all_tools[cfg.forge.cli] = t("preflight.tool_for.forge_cli",
                                     proposal=cfg.forge.proposal,
                                     proposal_with_article=cfg.forge.with_article())
    return all_tools


def has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def first_token(command: str | None) -> str | None:
    if not command or command.strip().startswith("<"):
        return None
    return command.strip().split()[0]


def measure_checks(profile: dict) -> dict:
    """By ROLE, never by tool. The profile names the command; here we measure whether it exists."""
    out = {}
    for role in CHECK_ROLES:
        c = (profile.get("checks") or {}).get(role) or {}
        command = c.get("command")
        token = first_token(command)
        if command is None:
            available, reason = False, t("preflight.no_command")
        elif token is None:
            available, reason = False, t("preflight.placeholder", value=command)
        elif not has(token):
            available, reason = False, t("preflight.tool_missing", tool=token)
        else:
            available, reason = True, None
        waiver = c.get("waiver")
        out[role] = {
            "command": command, "available": available, "reason": reason,
            "required": bool(c.get("required")),
            "waiver_in_force": waiver_in_force(waiver),
            "preexisting_reds": c.get("preexisting_reds"),
        }
    return out


def measure_proof_registry(profile: dict, repo_dir: pathlib.Path) -> dict:
    reg = profile.get("proof_registry") or {"type": "absent", "root": None}
    if reg.get("type") == "absent":
        return {**reg, "resolve": False,
                "reason": t("preflight.proof_registry_absent")}
    root = reg.get("root")
    resolve = bool(root) and (repo_dir / root).exists()
    return {**reg, "resolve": resolve,
            "reason": None if resolve else
                      t("preflight.proof_registry_unresolved", root=root, repo_dir=repo_dir)}


def measure_conventions(profile: dict, repo_dir: pathlib.Path) -> list[dict]:
    """A broken path raises nothing: it reads empty. So it is checked, never assumed."""
    return [{"caminho": c, "resolve": (repo_dir / c).exists()} for c in (profile.get("conventions") or [])]


def measure_local_reviewer(profile: dict, repo_dir: pathlib.Path) -> dict | None:
    lr = profile.get("local_reviewer")
    if not lr:
        return None
    suffix = "agents" if lr["type"] == "agent" else "skills"
    candidates = [repo_dir / ".claude" / suffix / lr["name"],
                  repo_dir / ".claude" / suffix / f"{lr['name']}.md"]
    resolve = any(p.exists() for p in candidates)
    return {**lr, "resolve": resolve,
            "reason": None if resolve else
                      t("preflight.local_reviewer_unresolved", name=lr["name"],
                        path=f"{repo_dir}/.claude/{suffix}")}


# --- Mandatory reading, and why it is a gate rather than a warning ------------------
#
# A review reads three ledgers. The first two are not on this repository's disk: what was
# asked for lives in the item, and what the delivery SAYS about itself lives in the
# proposal's description. No Python script reaches either one — the harness does, through
# the board's connector and the forge's token. So the reading is DECLARED by whoever did
# it.
#
# A missing declaration is a stop. Reviewing a diff without the item is guessing what was
# asked for; and judging whether the three stories agree while two of them are missing
# produces a verdict that looks complete and is not. The cost of stopping is one
# connection; the cost of carrying on is false confidence in a report.

# The remedy names the route THIS installation declared, and the three routes differ in
# how the harness gets the text — never in what it then does with it. Whatever fetches
# the description, it is handed over as a file: no script here reaches a forge API.
#
# Printing the token route to someone whose `prumo.yml` says `connection: mcp` is how a
# person ends up hunting for a personal access token they do not need, next to a
# connector that already answers. A stop that names the wrong way out costs more than
# the stop itself.
PROPOSAL_REMEDY = {
    "mcp": "preflight.proposal_remedy_mcp",
    "token": "preflight.proposal_remedy_token",
    "none": "preflight.proposal_remedy_none",
}


def requests(cfg):
    """The gate's wording speaks the installation's language: `card`/`MR` in one place,
    `issue`/`PR` in another. The gate is the same; the name of what is missing is not."""
    item = {"item": cfg.tracker.item,
            "item_with_article": cfg.tracker.with_article()}
    proposal = {"proposal": cfg.forge.proposal,
                "proposal_with_article": cfg.forge.with_article()}
    tokens = ", ".join(f"`{tk}`" for tk in cfg.forge.tokens)
    key = PROPOSAL_REMEDY.get(cfg.forge.connection, PROPOSAL_REMEDY["none"])
    return {
        "item": (t("preflight.item_not_read", **item),
                 t("preflight.item_remedy", tracker=cfg.tracker.type, **item)),
        "proposal_description": (t("preflight.proposal_not_read", **proposal),
                         t(key, tokens=tokens, forge=cfg.forge.type, **proposal)),
    }


def reading_impediments(reading: dict, cfg) -> list[str]:
    """What is still unread before the review may begin.

    The proposal's description is only demanded when the origin IS a proposal: reviewing
    a loose branch or a bare diff has no description to demand, and asking for it there
    would be asking for something that does not exist.
    """
    asked = requests(cfg)
    missing = []
    if not reading.get("item"):
        missing.append("item")
    if reading.get("origin") == "mr" and not reading.get("proposal_description"):
        missing.append("proposal_description")
    return [t("preflight.reading_gate", what=asked[k][0], remedy=asked[k][1])
            for k in missing]


def preflight(repo: str, origin: str | None = None, item_read: bool = False,
              proposal_description_read: bool = False) -> dict:
    """`origin` is 'mr', 'branch' or 'diff'. `item_read` and `proposal_description_read`
    are what THE CALLER actually managed to read — no Python script reaches the tracker
    or the forge's API, so mandatory reading is declared by whoever did it, and measured
    here."""
    cfg = common_cfg()
    repo_dir = dir_repositories() / repo
    profile = load_profile(repo)

    profile_errors: list[str] = []
    if profile_path(repo).exists():
        from validate_profile import validate
        profile_errors, _ = validate(profile)

    cap = {
        "repo": repo,
        "medido_em": today(),
        "repo_resolve": repo_dir.is_dir(),
        "profile": {
            "caminho": str(profile_path(repo)),
            "existe": profile_path(repo).exists(),
            "status": profile.get("status", "absent"),
            "version": profile.get("version", 0),
            "valido": not profile_errors,
            "erros": profile_errors,
            "coverage": f"{len(profile.get('anchors') or {})}/18",
        },
        "tools": {name: {"present": has(name), "purpose": purpose}
                        for name, purpose in tools(cfg).items()},
        "checks": measure_checks(profile),
        "proof_registry": measure_proof_registry(profile, repo_dir),
        "conventions": measure_conventions(profile, repo_dir),
        "local_reviewer": measure_local_reviewer(profile, repo_dir),
        "token_mr": bool(cfg.forge.token_in_environment()),
        # What the installation DECLARED, beside what was measured. The two disagreeing
        # is itself information: `connection: token` with no token in the environment is
        # a configuration that has drifted from its shell.
        "forge_connection": cfg.forge.connection,
        "leitura_obrigatoria": {
            "origin": origin,
            "item": item_read,
            "proposal_description": proposal_description_read,
        },
    }

    impediments = []
    if not cap["repo_resolve"]:
        impediments.append(t("preflight.repo_missing", repo=repo, root=dir_repositories()))
    if not cap["tools"]["git"]["present"]:
        impediments.append(t("preflight.no_git"))
    if profile_errors:
        impediments.append(t("preflight.profile_invalid"))
    if profile.get("status") == "confirmed" and unresolved_tokens(profile):
        impediments.append(t("preflight.warn_unresolved_owner"))
    impediments += reading_impediments(cap["leitura_obrigatoria"], cfg)
    cap["impediments"] = impediments

    warnings = []
    if cap["profile"]["status"] != "confirmed":
        warnings.append(t("preflight.warn_draft_profile"))
    for role, c in cap["checks"].items():
        if not c["available"] and c["required"] and not c["waiver_in_force"]:
            warnings.append(t("preflight.warn_check_unavailable", role=role, reason=c["reason"]))
    for c in cap["conventions"]:
        if not c["resolve"]:
            warnings.append(t("preflight.warn_convention_unresolved", path=c["caminho"]))
    if cap["proof_registry"].get("reason") and cap["proof_registry"]["type"] != "absent":
        warnings.append(cap["proof_registry"]["reason"])
    if cap["local_reviewer"] and not cap["local_reviewer"]["resolve"]:
        warnings.append(cap["local_reviewer"]["reason"])
    # Reading and publishing are two different capabilities, and one warning used to
    # speak for both. With `connection: mcp` the connector reads the proposal perfectly
    # well; it is only the WRITE that stays a human act. Saying "no token" flat told the
    # reader their reading was broken when it was not.
    if not cap["token_mr"]:
        warn = ("preflight.warn_mcp_publishing_manual"
                if cfg.forge.connection == "mcp" else "preflight.warn_no_token")
        warnings.append(t(warn, proposal=cfg.forge.proposal,
                          proposal_with_article=cfg.forge.with_article()))
    cap["warnings"] = warnings
    return cap


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--package")
    p.add_argument("--origin", choices=("mr", "branch", "diff"),
                   help="where the change comes from; only `mr` demands a description")
    p.add_argument("--item-read", action="store_true",
                   help="the caller declares having read the item")
    p.add_argument("--proposal-read", action="store_true",
                   help="the caller declares having read the proposal's description")
    args = p.parse_args()

    try:
        cap = preflight(args.repo, origin=args.origin, item_read=args.item_read,
                        proposal_description_read=args.proposal_read)
    except (MissingConfiguration, InvalidConfiguration) as e:
        print(t("config.impediment", reason=e))
        return 2
    if args.package:
        write_json(pathlib.Path(args.package) / "capabilities.json", cap)

    print(t("preflight.header", repo=cap["repo"], status=cap["profile"]["status"],
            coverage=cap["profile"]["coverage"]))
    for name, f in cap["tools"].items():
        print(f"  {'✓' if f['present'] else '·'} {name:8} {f['purpose']}")
    for role, c in cap["checks"].items():
        print(f"  {'✓' if c['available'] else '·'} {role:12} {c['reason'] or c['command']}")
    for w in cap["warnings"]:
        print(f"  {t('preflight.warning', text=w)}")
    for i in cap["impediments"]:
        print(f"  {t('preflight.impediment', text=i)}")
    return 1 if cap["impediments"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
