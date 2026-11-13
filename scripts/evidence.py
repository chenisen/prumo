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
import os
import pathlib
import re
import shlex
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import (dir_repositories, CHECK_ROLES, UsageError, load_profile, write_json,
                    read_json)
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
    # The suite over named test files only — the red a behaviour change owes on the base
    # is read from the proving tests, not from the whole suite run once more.
    allowed.add(((profile.get("checks") or {}).get("suite") or {}).get("files_command"))
    # A delivery's mutation command is part of the same allowlist: declared in the
    # profile, run by the script, never composed.
    allowed.add(((profile.get("delivery") or {}).get("mutation") or {}).get("command"))
    allowed.discard(None)
    if command not in allowed:
        return False, t("evidence.not_in_allowlist")
    return True, t("evidence.authorised_by_profile")


# The placeholder a profile's command uses to name the tree it runs over. The allowlist
# judges the TEMPLATE — the substitution happens after, so a path can never smuggle a
# command in, and the same template serves the checkout and a worktree.
WORKDIR = "{workdir}"
# The placeholder a suite's `files_command` uses for the test files it runs. Filled after
# the allowlist judged the template, each path quoted, so a path is never a command.
TESTS = "{tests}"


def with_tests(command: str, tests: list[str]) -> str:
    return command.replace(TESTS, " ".join(shlex.quote(t) for t in tests))


def mapped_workdir(profile: dict, workdir: pathlib.Path) -> str:
    """The tree a command runs over, as the EXECUTOR sees it. Without a `path_map` the
    executor is this machine and the path is the host's. With one, the checkout is
    mounted somewhere else — a container, a runner — and a worktree inside the checkout
    is reached at the same relative place under the mount. A tree the map does not
    cover is a stop: the executor could not reach it, and a suite run over the wrong
    tree is a green that measured nothing."""
    path_map = (profile.get("delivery") or {}).get("path_map")
    if not path_map:
        return str(workdir)
    host = pathlib.Path(os.path.realpath(path_map["host"]))
    real = pathlib.Path(os.path.realpath(workdir))
    try:
        relative = real.relative_to(host)
    except ValueError:
        raise UsageError(t("evidence.path_map_outside", path=workdir, host=path_map["host"]))
    container = path_map["container"].rstrip("/")
    return container if str(relative) == "." else f"{container}/{relative.as_posix()}"


def resolve(command: str, profile: dict, workdir: pathlib.Path) -> str:
    """The template with `{workdir}` filled in — after `authorise`, never before."""
    if WORKDIR not in command:
        return command
    return command.replace(WORKDIR, mapped_workdir(profile, workdir))


def time_limit(check: dict | None) -> int:
    """How long a check may run: its own `timeout_seconds` in the profile, else
    TIME_LIMIT. A front-end suite of seven thousand tests in a throwaway container takes
    twenty minutes and was cut at fifteen, every time, on the base itself (2026-11-13);
    the repository knows how long its suite takes, the method does not."""
    declared = (check or {}).get("timeout_seconds")
    return int(declared) if isinstance(declared, int) and declared > 0 else TIME_LIMIT


def run_command(command: str, repo_dir: pathlib.Path, limit: int | None = 20000,
                timeout: int = TIME_LIMIT) -> dict:
    """`limit` keeps the LAST bytes of the output: enough for a review to read the
    summary, and a cap on what a package carries. A delivery passes `None` — it reads
    the reds by NAME, and a runner prints the names before the traces that fill the
    tail, so a cut tail is a list of names cut to nothing. Measured on 2026-11-11: a
    mutation that turned 39 tests red came out `green` because every name sat above
    the cut. `timeout` is the check's `time_limit()`."""
    try:
        r = subprocess.run(shlex.split(command), cwd=repo_dir, capture_output=True,
                           text=True, timeout=timeout)
        output = (r.stdout or "") + (r.stderr or "")
        kept = output if limit is None else output[-limit:]
        return {"ran": True, "exit_code": r.returncode, "output": kept,
                "truncated": len(kept) < len(output)}
    except subprocess.TimeoutExpired:
        return {"ran": False, "reason": t("evidence.timed_out", seconds=timeout)}
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


def red_names(output: str, pattern: str | None) -> list[str] | None:
    """The reds, by NAME, read from the output through the profile's pattern. No pattern
    means no names — `None`, not an empty list: an empty list says "nothing is red", and
    that is a claim nobody measured."""
    if not pattern:
        return None
    try:
        names = re.findall(pattern, output or "")
    except re.error:
        return None
    return sorted({(n[0] if isinstance(n, tuple) else n) for n in names})


def progress(output: str, pattern: str | None) -> tuple[int | None, int | None]:
    """(executed, predicted) from the output's last progress line, or (None, None)."""
    if not pattern:
        return None, None
    try:
        found = re.findall(pattern, output or "")
    except re.error:
        return None, None
    if not found or len(found[-1]) != 2:
        return None, None
    executed, predicted = found[-1]
    return int(executed), int(predicted)


def compare_reds(base_output: str, branch_output: str, check: dict) -> dict:
    """The baseline, compared by name and never by count. `new_reds` is what the branch
    turned red; `vanished_reds` is what it turned green, recorded and never charged.
    Without a name pattern both are `None` — declared, not guessed — and the progress
    line is read on its own, because a suite that did not finish is not green whatever
    its names say."""
    base = red_names(base_output, check.get("red_name_pattern"))
    branch = red_names(branch_output, check.get("red_name_pattern"))
    executed, predicted = progress(branch_output, check.get("progress_pattern"))
    if base is None or branch is None:
        return {"base_reds": base, "branch_reds": branch, "new_reds": None,
                "vanished_reds": None, "executed": executed, "predicted": predicted}
    return {"base_reds": base, "branch_reds": branch,
            "new_reds": sorted(set(branch) - set(base)),
            "vanished_reds": sorted(set(base) - set(branch)),
            "executed": executed, "predicted": predicted}


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

    repo_dir = dir_repositories() / man["repo"]
    result = run_command(resolve(command, profile, repo_dir), repo_dir,
                         timeout=time_limit(check))
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
