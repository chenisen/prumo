#!/usr/bin/env python3
"""F6 — fetches the input the gate is missing. One round, allowlisted commands only.

It is the only phase that executes anything, and for that reason the most constrained:

  * **one round per run.** With no result the item stays `nao_verificado` — and
    `nao_verificado` is an honest state, not a failure to hide;
  * **only what the PROFILE declares.** The allowlist is the set of `checagens` commands.
    A command that is not there does not run, however nicely it is asked for;
  * **no composition.** `;`, `&&`, `|`, `$(...)`, `>` and `` ` `` fail on sight: an
    allowed command chained to another one is a different command.

Usage:
    evidence.py --pacote DIR [--papel suite] [--executar]
"""
from __future__ import annotations

import argparse
import pathlib
import re
import shlex
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import (dir_repositories, CHECK_ROLES, load_profile, write_json, read_json)
from i18n import t

# Shell metacharacters. The presence of any one of them fails the whole command.
COMPOSITION = re.compile(r"[;&|`><]|\$\(")

TIME_LIMIT = 900  # 15 min: a big repository's suite fits; an infinite loop does not


def authorise(command: str, profile: dict) -> tuple[bool, str]:
    """Decide whether a command may run. Pure, and the heart of this phase's guarantee."""
    if not command or not command.strip():
        return False, t("evidence.empty_command")
    if command.strip().startswith("<"):
        return False, t("preflight.placeholder", value=command)
    if COMPOSITION.search(command):
        return False, t("evidence.shell_composition")
    allowed = {(c or {}).get("command") for c in (profile.get("checks") or {}).values()}
    allowed.discard(None)
    if command not in allowed:
        return False, t("evidence.not_in_allowlist")
    return True, t("evidence.authorised_by_profile")


def run_command(command: str, repo_dir: pathlib.Path) -> dict:
    try:
        r = subprocess.run(shlex.split(command), cwd=repo_dir, capture_output=True,
                           text=True, timeout=TIME_LIMIT)
        output = (r.stdout or "") + (r.stderr or "")
        return {"ran": True, "exit_code": r.returncode, "output": output[-20000:],
                "truncated": len(output) > 20000}
    except subprocess.TimeoutExpired:
        return {"ran": False, "reason": t("evidence.timed_out", seconds=TIME_LIMIT)}
    except FileNotFoundError as e:
        return {"ran": False, "reason": t("evidence.executable_missing", error=e)}
    except Exception as e:                                   # noqa: BLE001
        return {"ran": False, "reason": t("evidence.command_error",
                                            type=type(e).__name__, error=e)}


def read_suite(result: dict, check: dict) -> tuple[str, str]:
    """Translate a suite run into the state the judge understands.

    The baseline is compared **by name**, never by count: a repository may carry reds
    that travel with the base, and a count tells no delivery apart from another.
    """
    if not result.get("ran"):
        return "not_run", result.get("reason", t("evidence.did_not_run"))

    # The exit code is NOT enough. An executor that wraps the process — container,
    # runner, wrapper — can return 0 with the suite red, and then the green did not look
    # at the result: it looked at the wrapper. So the output is read first, whenever the
    # profile declares what a failure looks like in this repository.
    pattern = check.get("failure_pattern")
    failed_in_text = False
    if pattern:
        try:
            failed_in_text = bool(re.search(pattern, result.get("output") or ""))
        except re.error as e:
            return "not_run", t("evidence.invalid_failure_pattern", error=e)

    if result["exit_code"] == 0 and not failed_in_text:
        if pattern:
            return "green", t("evidence.green_output_and_code")
        return "green", t("evidence.green_exit_code_only")

    baseline = check.get("preexisting_reds")
    if result.get("truncated"):
        return "not_run", t("evidence.output_truncated")
    if baseline:
        return "red_not_attributable", t("evidence.red_with_baseline", baseline=baseline)
    return "red_attributable", t("evidence.red_no_baseline")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--package", required=True)
    p.add_argument("--role", choices=CHECK_ROLES, default="suite")
    p.add_argument("--run", action="store_true",
                   help=t("evidence.run_flag_help"))
    args = p.parse_args()

    package = pathlib.Path(args.package)
    man = read_json(package / "manifest.json")
    profile = load_profile(man["repo"])
    check = (profile.get("checks") or {}).get(args.role) or {}
    command = check.get("command")

    ok, reason = authorise(command or "", profile)
    print(t("evidence.role_line", role=args.role, command=repr(command),
            decision=t("evidence.authorised") if ok else t("evidence.refused"),
            reason=reason))

    evid = read_json(package / "evidence.json") if (package / "evidence.json").exists() else {}
    if not ok:
        if args.role == "suite":
            evid["suite"] = "not_run"
            evid["suite_reason"] = reason
        evid.setdefault("nao_verificado", []).append({"papel": args.role, "reason": reason})
        write_json(package / "evidence.json", evid)
        return 0

    if not args.run:
        print(t("evidence.not_executed"))
        return 0

    result = run_command(command, dir_repositories() / man["repo"])
    (package / f"run-{args.role}.txt").write_text(
        result.get("output", result.get("reason", "")), encoding="utf-8")
    if args.role == "suite":
        state, reason = read_suite(result, check)
        evid["suite"], evid["suite_reason"] = state, reason
        print(t("evidence.suite_line", state=state, reason=reason))
    else:
        evid.setdefault("checks", {})[args.role] = {
            "exit_code": result.get("exit_code"), "ran": result.get("ran")}
    write_json(package / "evidence.json", evid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
