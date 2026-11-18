#!/usr/bin/env python3
"""Deliver one item: cut its worktree, let an agent write and run its tests there, check
what landed once, hand the branch to task-review, and illustrate it at the very end.

    deliver.py prepare  --repo R --item C --item-file ITEM.md (--type T | --shape S)
                        [--kind K] [--branch B] [--package P]
        F0 preflight · F1 resolve the shape and the base · F4 cut the branch in its own
        worktree and put the marker on. Runs no suite. Prints the workflow invocation.

    deliver.py plan     --package P (--measure M | --design D)
        the plan a person approves, for the shapes that ask a gate.

    deliver.py conclude --package P [--result R.json] [--measure M] [--written W]
                        [--refutation F]
        F6 check, ONCE per item: the scope and the budget over the diff, the proof over
        the test files the change touched, the suite its size asks for — those files, or
        the whole of it — and the lint · F8 judge, a pure function · F9 record, report,
        the next command. Removes the marker.

    deliver.py evidence --package P [--collected E.json]
        after the item is delivered: what was done, shown. Illustrates; decides nothing.

    deliver.py release  --package P
        the worktree goes, the branch stays.

Who writes does not approve: nothing here reads a finding or issues a verdict. The end of
a delivery is the invocation of `task-review`, printed and never run.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import (MissingConfiguration, InvalidConfiguration, UsageError, cfg,
                    dir_packages, dir_repositories, dir_reports, load_profile,
                    profile_path, read_json, slug, today, waiver_in_force, write_json,
                    casa_algum, capabilities_after_preflight, hint_config, models_map)
from i18n import t
from shape import (SHAPES, resolve as resolve_shape, budget_for, UnknownShape, size_of,
                   small_limits)
import hashlib

ROOT = pathlib.Path(__file__).resolve().parent
DELIVERED, BLOCKED, UNSUPPORTED = "delivered", "blocked", "unsupported"
NEXT = "/prumo:task-review --repo {repo} --branch {branch} --item {item}"
DERIVE = "derive_profile.py {repo} --delivery"


class DeliveryNotAssessable(UsageError):
    """The profile does not describe how this repository is delivered into. Nothing is
    assumed in its place: the message carries the derivation command."""


# --- F8: the judge ---------------------------------------------------------------------
#
# A function over the record and the profile. It reads no disk, runs nothing, calls no
# model. Same record, same answer — `test_deliver.py` runs it a hundred times to hold
# that. Every stop names its reason from the catalogue and the owner of the next move.

def delivery_block(profile: dict, repo: str | None = None) -> dict:
    block = profile.get("delivery")
    if not isinstance(block, dict):
        name = repo or profile.get("profile") or "?"
        raise DeliveryNotAssessable(t("deliver.not_assessable.no_delivery_block",
                                      repo=name, command=DERIVE.format(repo=name)))
    return block


def deliver_judge(record: dict, profile: dict, reference: str | None = None) -> dict:
    block = delivery_block(profile, record.get("repo"))
    repo = record.get("repo") or profile.get("profile") or "?"
    out = {"state": DELIVERED, "reason": t("deliver.state.delivered"), "owner": None,
           "next": NEXT.format(repo=repo, branch=record["branch"]["name"],
                               item=record["item"]["id"])}

    def stop(state, reason, owner):
        out.update(state=state, reason=reason, owner=owner)
        return out

    kinds = list(block.get("kinds") or [])
    kind = record["item"]["kind"]
    if kind not in kinds:
        return stop(UNSUPPORTED, t("deliver.unsupported.kind", kind=kind,
                                   kinds=", ".join(kinds) or "-"), "profile")

    for p in record.get("premises") or []:
        if p["central"] and p["state"] == "fails":
            return stop(BLOCKED, t("deliver.blocked.premise_failed", premise=p["text"],
                                   evidence=p.get("evidence") or "?"), "person")

    plan = record.get("plan") or {}
    if plan.get("gate") and not plan.get("approved_by"):
        return stop(BLOCKED, t("deliver.blocked.plan_not_approved",
                               command="deliver.py approve --package <package> --by <name>"), "person")

    # The writer said it could not deliver — a premise the code denies, a test it could
    # not turn green. Its word is the reason; nothing was checked after it.
    if record.get("writer_blocked"):
        return stop(BLOCKED, t("deliver.blocked.writer_blocked", reason=record["writer_blocked"]), "person")
    waiting = [k for k, v in (record.get("tasks") or {}).items() if v != DELIVERED]
    if waiting:
        return stop(BLOCKED, t("deliver.blocked.tasks_pending", tasks=", ".join(waiting)), "author")

    # The diff's own stops come before every stop a run could give: nothing ran after them.
    diff = record["diff"]
    stopped_by_diff = diff_stop(diff, record.get("waiver"), reference)
    if stopped_by_diff == "outside_scope":
        return stop(BLOCKED, t("deliver.blocked.outside_scope",
                               paths=", ".join(diff["outside_scope"])), "author")
    if stopped_by_diff == "budget":
        budget = plan.get("budget") or block.get("budget") or {}
        return stop(BLOCKED, t("deliver.blocked.budget_exceeded",
                               files=len(diff.get("files") or []),
                               lines=diff.get("added", 0) + diff.get("removed", 0),
                               max_files=budget.get("max_files", "?"),
                               max_lines=budget.get("max_lines", "?")), "person")

    # A refactor with nothing standing guard has no proof to preserve, and the suite
    # never ran for it: the stop is the person's, before "the suite did not run".
    proof = record["proof"]
    if kind == "refactor" and not ((proof.get("guarding") or {}).get("tests")):
        return stop(BLOCKED, t("deliver.blocked.no_guarding_tests"), "person")

    base = record["baseline"]
    if base.get("executed") is None:
        # The suite ran and still nobody counted it: the profile names no way to read
        # how many tests ran. Saying "did not run" would send the author after a fault
        # that is the profile's.
        suite_check = (profile.get("checks") or {}).get("suite") or {}
        if (record.get("measured") or {}).get("suite") and not suite_check.get("progress_pattern"):
            return stop(BLOCKED, t("deliver.blocked.progress_not_declared"), "profile")
        return stop(BLOCKED, t("deliver.blocked.suite_not_run",
                               reason=t("review.suite_not_run")), "author")
    if base.get("predicted") is not None and base["executed"] < base["predicted"]:
        return stop(BLOCKED, t("deliver.blocked.suite_incomplete",
                               executed=base["executed"], predicted=base["predicted"]),
                    "author")
    if str(base.get("base") or "").startswith("not_run"):
        return stop(BLOCKED, t("deliver.blocked.base_not_measured",
                               reason=base["base"].split(":", 1)[-1].strip()), "author")
    if base.get("new_reds") is None:
        return stop(BLOCKED, t("deliver.blocked.baseline_not_assessable"), "profile")
    if base["new_reds"]:
        return stop(BLOCKED, t("deliver.blocked.new_red",
                               names=", ".join(base["new_reds"])), "author")

    proof = record["proof"]
    # `partial` passes: something went red on the base and one named test was not read
    # among the reds — the name is the runner's spelling, and the report says which.
    red = proof.get("red_on_base")
    if red and red.get("result") not in ("red", "partial"):
        return stop(BLOCKED, t("deliver.blocked.test_not_red_on_base", test=red["test"]),
                    "author")
    if kind == "behaviour-change" and not red:
        return stop(BLOCKED, t("deliver.blocked.no_red_on_base"), "author")
    guarding = proof.get("guarding")
    if kind == "refactor":
        if guarding.get("result") == "red":
            return stop(BLOCKED, t("deliver.blocked.guard_went_red",
                                   names=", ".join(guarding.get("tests") or [])), "author")
    for m in proof.get("mutations") or []:
        if m.get("stays_green_result") == "red":
            return stop(BLOCKED, t("deliver.blocked.mutation_too_wide", file=m["file"],
                                   line=m["line"], test=", ".join(m.get("stays_green") or [])),
                        "author")
        if m["result"] == "not_run":
            return stop(BLOCKED, t("deliver.not_assessable.mutation_not_applied",
                                   file=m["file"], line=m["line"],
                                   reason=t("evidence.did_not_run")), "author")
        if m["result"] != "red":
            return stop(BLOCKED, t("deliver.blocked.mutation_survived",
                                   file=m["file"], line=m["line"]), "author")

    refutation = proof.get("refutation") or {}
    if refutation.get("verdict") == "does_not_prove":
        return stop(BLOCKED, t("deliver.blocked.refuted", reason=refutation.get("reason", "")),
                    "author")

    lint = record.get("lint") or {}
    if lint.get("state") == "red":
        return stop(BLOCKED, t("deliver.blocked.lint_red", reason=lint.get("reason", "")), "author")
    return out


def diff_stop(diff: dict, waiver: dict | None, reference: str | None = None) -> str | None:
    """What the diff alone decides, before anything is run: `outside_scope`, `budget`, or
    None. A line outside the scope, or a budget exceeded with no waiver in force, blocks
    whatever the tests say — so the judge reads it first and `conclude` runs nothing after
    it. The first real delivery on 3.0 paid three mutants and two whole suites, six and a
    half minutes, for a stop its diff had already decided."""
    if diff.get("outside_scope"):
        return "outside_scope"
    if not diff.get("within_budget") and not waiver_in_force(waiver, reference):
        return "budget"
    return None


# --- the marker ------------------------------------------------------------------------
#
# Same origin as `review.marker()`: `CLAUDE_PROJECT_DIR`, else where `prumo.yml` sits,
# never the shell's directory. ONE marker PER DELIVERY, in one directory the hook reads:
# several agents deliver several items in the same repository at once, each in its own
# worktree, and the hook judges a write or a git command by the delivery whose worktree
# it lands in — never the whole workspace by whichever delivery started last. JSON on
# purpose: the worktree, the branch and the files the writer may touch travel in it.

MARKERS_DIR = ".prumo-deliver-ativo.d"


def marker(package: pathlib.Path) -> pathlib.Path:
    base = os.environ.get("CLAUDE_PROJECT_DIR") or _installation_root() or os.getcwd()
    return pathlib.Path(base) / ".claude" / MARKERS_DIR / f"{pathlib.Path(package).name}.json"


def markers() -> list[pathlib.Path]:
    """The deliveries running now in this workspace."""
    return sorted(marker(pathlib.Path("x")).parent.glob("*.json"))


def _installation_root() -> str | None:
    try:
        source = cfg().source
    except Exception:
        return None
    return str(source.parent) if source else None


def mark(package: pathlib.Path, worktree: pathlib.Path, allowed: list[str],
         branch: str | None = None, roots: list[str] | None = None) -> None:
    """`roots` is where this delivery's kind may write before a measurement names its
    files: an item with no gate is measured and written in one workflow call, and the
    guard holds the writer to the roots until `conclude` holds the diff to the scope."""
    target = marker(package)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"package": str(package), "worktree": str(worktree),
                                  "branch": branch, "allowed": allowed, "roots": roots or [],
                                  "started_at": today()}) + "\n",
                      encoding="utf-8")


def unmark(package: pathlib.Path | None = None) -> None:
    """This delivery's marker; with no package, every one — the `release` of a stuck
    workspace, which a person runs knowing what else is running."""
    for m in ([marker(package)] if package else markers()):
        m.unlink(missing_ok=True)


# --- git, suite, mutation ----------------------------------------------------------------

def git(cwd: pathlib.Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(("git", *args), cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise UsageError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def _py(script: str, *args: str, check: bool = True) -> None:
    """One step of the spine. Its output is kept, not shown: the report is what a person
    reads, and a step's own lines matter only when it fails."""
    r = subprocess.run((sys.executable, str(ROOT / script), *args), text=True,
                       capture_output=True)
    if check and r.returncode != 0:
        sys.stdout.write(r.stdout or "")
        sys.stderr.write(r.stderr or "")
        raise UsageError(t("review.step_failed", path=script, n=r.returncode))


def run_suite(profile: dict, cwd: pathlib.Path, package: pathlib.Path, label: str,
              tests: list[str] | None = None) -> dict:
    """The suite, through the profile's allowlist, output kept beside the package. The
    template is what the allowlist judges; `{workdir}` is filled in after, with `cwd`
    as the executor sees it. With `tests` and a `files_command` in the profile, only
    those test files run."""
    from evidence import authorise, resolve, run_command, time_limit, with_tests
    check = (profile.get("checks") or {}).get("suite") or {}
    by_files = bool(tests and check.get("files_command"))
    template = check["files_command"] if by_files else check.get("command")
    ok, reason = authorise(template or "", profile)
    if not ok:
        return {"ran": False, "reason": reason, "output": ""}
    command = resolve(template, profile, cwd)
    if by_files:
        command = with_tests(command, tests)
    result = run_command(command, cwd, limit=None, timeout=time_limit(check))
    (package / f"run-suite-{label}.txt").write_text(
        result.get("output", result.get("reason", "")), encoding="utf-8")
    return result


def run_lint(profile: dict, cwd: pathlib.Path, package: pathlib.Path) -> dict:
    """The profile's lint check, read like the suite: the output first, the exit code
    second. No command declared is `not_run`, said in words — never green."""
    from evidence import authorise, resolve, run_command, time_limit
    check = (profile.get("checks") or {}).get("lint") or {}
    if not check.get("command"):
        return {"state": "not_run", "reason": t("evidence.not_in_allowlist"), "run": None}
    ok, reason = authorise(check["command"], profile)
    if not ok:
        return {"state": "not_run", "reason": reason, "run": None}
    r = run_command(resolve(check["command"], profile, cwd), cwd, limit=None,
                    timeout=time_limit(check))
    (package / "run-lint.txt").write_text(r.get("output", r.get("reason", "")), encoding="utf-8")
    if not r.get("ran"):
        return {"state": "not_run", "reason": r.get("reason", ""), "run": None}
    pattern = check.get("failure_pattern")
    failed_in_text = bool(pattern and re.search(pattern, r.get("output") or ""))
    # The run file is named in the record: a reader who doubts the state opens the
    # output, instead of trusting a sentence — or the writer's note that it never ran.
    if r["exit_code"] == 0 and not failed_in_text:
        return {"state": "green", "reason": t("evidence.lint_green_output_and_code") if pattern
                else t("evidence.lint_green_exit_code_only"), "run": "run-lint.txt"}
    return {"state": "red", "reason": (r.get("output") or "").strip().splitlines()[-1][:200]
            if (r.get("output") or "").strip() else t("evidence.red_no_baseline"),
            "run": "run-lint.txt"}


def with_base_versions(workdir: pathlib.Path, base_sha: str, files: list[str]):
    """Context: the base's version of `files` in the tree, what was there put back after.
    A file the base does not hold is removed for the duration — a behaviour change
    that adds a file is proven red by the test failing to import it. Bytes in, bytes
    out, and the index is never touched: a story's tasks are already committed when
    this runs, and a `checkout` of the base's paths would stage their reversal."""
    import contextlib

    def at_base(f: str) -> bytes | None:
        r = subprocess.run(("git", "show", f"{base_sha}:{f}"), cwd=workdir, capture_output=True)
        return r.stdout if r.returncode == 0 else None

    @contextlib.contextmanager
    def _ctx():
        saved = {}
        for f in files:
            path = workdir / f
            saved[f] = path.read_bytes() if path.is_file() else None
            base = at_base(f)
            if base is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(base)
            elif path.exists():
                path.unlink()
        try:
            yield
        finally:
            for f, data in saved.items():
                path = workdir / f
                if data is None:
                    if path.exists():
                        path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
    return _ctx()


def production_files(files: list[str], block: dict) -> list[str]:
    """The changed files the base's version replaces under the new test: under a source
    root and under no test root. A repository that keeps its tests beside the code
    (`src/**/__tests__`) has its test roots inside a source root, and a test file counted
    as production went back to the base with the code — the proving test vanished, and
    the red it owed could never show."""
    def under(f: str, roots: list[str]) -> bool:
        return casa_algum(f, [r.rstrip("/") + "/**" for r in roots or []])
    return [f for f in files
            if under(f, block.get("source_roots")) and not under(f, block.get("test_roots"))]


def apply_mutation(workdir: pathlib.Path, m: dict) -> str | None:
    """Turn the guarded line wrong. Returns None when applied, else why not — a line
    that moved or a text that is not there is declared, never approximated."""
    path = workdir / m["file"]
    if not path.is_file():
        return t("deliver.cli.mutation_file_missing", file=m["file"])
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    idx = m["line"] - 1
    if idx < 0 or idx >= len(lines) or m["from"] not in lines[idx]:
        return t("deliver.cli.mutation_text_missing", file=m["file"], line=m["line"],
                 text=m["from"])
    lines[idx] = lines[idx].replace(m["from"], m["to"], 1)
    path.write_text("".join(lines), encoding="utf-8")
    return None


def revert(workdir: pathlib.Path, file: str) -> None:
    git(workdir, "checkout", "--", file)


# --- F0 F1 F3 F4: prepare ----------------------------------------------------------------

def read_item(path: str, package: pathlib.Path) -> None:
    source = pathlib.Path(path)
    if not source.is_file():
        raise UsageError(t("review.reading_missing", path=path))
    if not source.read_text(encoding="utf-8").strip():
        raise UsageError(t("review.reading_empty", path=path))
    target = package / "item.md"
    # The package's own item, handed back to prepare (`--continue` on the same package),
    # is already where it goes: copying a file onto itself raised SameFileError.
    if not (target.exists() and source.resolve() == target.resolve()):
        shutil.copy2(source, target)


def read_related(path: str | None, package: pathlib.Path) -> str | None:
    """What was already decided on the items around this one — its parent, its siblings,
    its predecessors — as the conductor read it from the tracker. The design of a story
    (2026-11-18) put back to a person a question a sibling's comment had answered the day
    before, and the person had to say so. Kept in the package; carried to every agent."""
    if not path:
        stored = package / "related.md"
        return stored.read_text(encoding="utf-8") if stored.exists() else None
    source = pathlib.Path(path)
    if not source.is_file():
        raise UsageError(t("review.reading_missing", path=path))
    text = source.read_text(encoding="utf-8")
    if not text.strip():
        raise UsageError(t("review.reading_empty", path=path))
    (package / "related.md").write_text(text, encoding="utf-8")
    return text


def branch_name(block: dict, item: str, item_text: str, given: str | None) -> str:
    """The invocation's branch; else the one the item names on a `Branch:` line — a
    tracker's field the harness writes into the item; else the profile's format over
    the item's title. The item's name is the team's, and a slug is a guess at it."""
    if given:
        return given
    for line in item_text.splitlines():
        m = re.match(r"^\s*branch\s*:\s*(\S+)\s*$", line, re.I)
        if m:
            return m.group(1)
    return block["branch_format"].format(item=item, slug=slug(item_title(item_text, item), 40))


def item_title(item_text: str, item: str) -> str:
    """The item's first line, without the heading mark and without the item's own id in
    front of it: `# ITEM-7 — Title` under `{item}-{slug}` named a branch `ITEM-7-item-7-…`."""
    first = item_text.strip().splitlines()[0].lstrip("# ").strip() if item_text.strip() else ""
    return re.sub(rf"^{re.escape(str(item))}\s*[-—–:·]?\s*", "", first) if item else first


def fix_selection(review: pathlib.Path, chosen: list[str] | None) -> tuple[dict, list[dict], dict]:
    """The review's manifest, the routes a fix delivers, and the findings by id."""
    man = read_json(review / "manifest.json")
    verdict = read_json(review / "verdict.json")
    findings = {f["id"]: f for f in (read_json(review / "findings.json")
                                     if (review / "findings.json").exists() else [])}
    routes = [r for r in verdict.get("routes") or [] if r.get("type") in ("author_fix", "confirm_finding")]
    if chosen:
        unknown = [i for i in chosen if i not in findings]
        if unknown:
            raise UsageError(t("deliver.cli.fix_unknown_findings", ids=", ".join(unknown), path=review))
        confirmed = {i for r in routes if r["type"] == "confirm_finding" for i in r.get("items") or []}
        routes = [{"type": "confirm_finding" if i in confirmed else "author_fix", "items": [i]}
                  for i in chosen]
    if not routes:
        raise UsageError(t("deliver.cli.fix_no_routes", path=review))
    return man, routes, findings


def fix_is_proof_only(review: pathlib.Path, chosen: list[str] | None = None) -> bool:
    """Every finding the fix delivers is about a test that does not prove what it claims —
    the verifier read its consequence as `proof`. Such a round changes tests only, and as a
    `behaviour-change` it is blocked: nothing goes red on a base whose behaviour it never
    meant to change. Twice on 2026-11-18 a `--fix` came back BLOCKED for exactly that and
    was prepared again by hand as `--shape debt --kind coverage-gap`."""
    _, routes, findings = fix_selection(review, chosen)
    ids = [i for r in routes for i in r.get("items") or []]
    return bool(ids) and all((findings.get(i) or {}).get("consequence_kind") == "proof" for i in ids)


def fix_item(review: pathlib.Path, chosen: list[str] | None = None) -> tuple[str, str, str]:
    """The item a fix delivers. Returns (item id, origin, item text).

    By default, the findings behind the review's `author_fix` routes AND its
    `confirm_finding` ones: a person who asks for the fix of an uncertain finding has
    confirmed it — on 2026-11-18 that ask was refused, and the round went in through
    `--continue` with the item written by hand. With `chosen`, exactly those finding ids,
    whatever their severity: the same person chose two nits beside the uncertain finding,
    and a fix takes what a person chose, never more."""
    man, routes, findings = fix_selection(review, chosen)
    item = str(man["item"]["id"] or "no-item")
    lines = [f"# {t('deliver.cli.fix_item', item=item)}", ""]
    for r in routes:
        for fid in r.get("items") or []:
            f = findings.get(fid)
            if f:
                where = "; ".join(f"{e.get('file')}:{e.get('line')}" for e in f.get("evidence") or [])
                lines += [f"- **{fid}** {f['claim']} — {where}",
                          f"  consequence: {f.get('consequence', '')}"]
                if f.get("form"):
                    lines.append(f"  form: {f['form']}")
                if f.get("beyond_item"):
                    lines.append("  " + t("deliver.cli.fix_beyond_item"))
                if r["type"] == "confirm_finding":
                    lines.append("  " + t("deliver.cli.fix_confirmed_by_asking"))
            else:
                lines.append(f"- **{fid}** {r.get('details') or r.get('closes_when') or ''}")
    return item, man.get("origin") or "", "\n".join(lines) + "\n"


def setup_worktree(repo_dir: pathlib.Path, workdir: pathlib.Path, block: dict) -> None:
    """What a fresh worktree needs before a suite can run in it — a dependency tree git
    does not hold, a cache directory the framework wants writable — is a fact of the
    repository, and it comes from the profile's `worktree_setup`: one command per
    line, by `shlex`, no shell, `{repo}` and `{worktree}` filled in. Nothing runs that
    is not declared there. A command that fails takes the worktree down with it, so
    the next prepare does not meet a half-built tree."""
    from evidence import COMPOSITION
    for template in block.get("worktree_setup") or []:
        if COMPOSITION.search(template):
            raise UsageError(t("deliver.cli.setup_composed", command=template))
        command = template.replace("{repo}", str(repo_dir)).replace("{worktree}", str(workdir))
        r = subprocess.run(shlex.split(command), cwd=workdir, capture_output=True, text=True)
        if r.returncode != 0:
            output = ((r.stdout or "") + (r.stderr or "")).strip()[-500:]
            raise UsageError(t("deliver.cli.setup_failed", command=template, n=r.returncode,
                               output=output))


def same_test(expected: str, name: str) -> bool:
    """A test named by the measure is the red the runner printed when one contains the
    other's words, or when both read `<file> > <title>` with the same title and one
    file path ending the other — the measure counts from the repository root, the
    runner from where it ran (`packages/kit/test/a.test.ts` and `test/a.test.ts`)."""
    if expected in name:
        return True
    if "::" in expected and "::" in name:
        return _same_unit_and_case(expected, name)
    sep = " > "
    if sep not in expected or sep not in name:
        return False
    (f1, t1), (f2, t2) = expected.split(sep, 1), name.split(sep, 1)
    return t1 == t2 and (f1 == f2 or f1.endswith("/" + f2) or f2.endswith("/" + f1))


def _same_unit_and_case(expected: str, name: str) -> bool:
    """`<unit>::<case>` on both sides, where the measure may name the unit by its file
    (`tests/Unit/GuardTest.x`) and the runner by its class (`Tests\\Unit\\GuardTest`), and
    may add a data set to the case (`refuses with data set "empty"`). The case must match
    whole; the unit's last part must match without its extension, and the parts before it
    must agree as far as the shorter side goes."""
    (u1, c1), (u2, c2) = expected.split("::", 1), name.split("::", 1)
    if not (c2 == c1 or c2.startswith(c1 + " ")):
        return False
    p1, p2 = _unit_parts(u1), _unit_parts(u2)
    if not p1 or not p2 or p1[-1] != p2[-1]:
        return False
    n = min(len(p1), len(p2))
    return p1[-n:] == p2[-n:]


def _unit_parts(unit: str) -> list[str]:
    parts = [p for p in re.split(r"[\\/]", unit.strip()) if p]
    if parts and "." in parts[-1]:
        parts[-1] = parts[-1].rsplit(".", 1)[0]
    return [p.lower() for p in parts]


def file_stands_for(name: str, test_files: list[str]) -> bool:
    """A red read under a test file's name — the file could not load — stands for every
    test in it. Runners name that file by module (`pkg.test_a`, `test_a`) or by a path
    relative to where they ran (`test/a.test.ts` for `apps/web/test/a.test.ts`)."""
    for f in test_files:
        module = pathlib.Path(f).stem
        if name in (f, module) or name.endswith(("." + module, "\\" + module, "/" + module)):
            return True
        if "/" in name and f.endswith("/" + name):
            return True
    return False


def cut_branch(repo_dir: pathlib.Path, block: dict, name: str, base_sha: str,
               fresh: bool = True) -> pathlib.Path:
    """A clean tree is a precondition, not a courtesy: a pending change nobody made here
    is somebody's work in progress. In a worktree the branch lives beside the checkout
    and the checkout is never touched — not even its branch; without one, the branch
    is checked out in place and the report says where HEAD stood. A branch that already
    exists is never reset under a worktree, nor in place while it holds commits the
    base lacks: it may carry somebody's commits, and the person names another, asks
    for a fix, or deletes it. A worktree that already holds the branch is
    continued, not recut — that is a story's second task, or a fix on a reviewed branch."""
    if uses_worktree(block):
        workdir = repo_dir / ".worktrees" / name
        exclude = repo_dir / ".git" / "info" / "exclude"
        if exclude.parent.is_dir():
            text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
            if ".worktrees/" not in text:
                exclude.write_text(text.rstrip("\n") + "\n.worktrees/\n", encoding="utf-8")
        if workdir.exists():
            if not fresh and git(workdir, "branch", "--show-current", check=False) == name:
                return workdir
            raise UsageError(t("deliver.cli.worktree_exists", path=workdir))
        if fresh:
            if git(repo_dir, "rev-parse", "--verify", "-q", f"refs/heads/{name}", check=False):
                raise UsageError(t("deliver.cli.branch_exists", branch=name, path=repo_dir))
            git(repo_dir, "worktree", "add", "-b", name, str(workdir), base_sha)
        else:
            git(repo_dir, "worktree", "add", str(workdir), name)
        try:
            setup_worktree(repo_dir, workdir, block)
        except UsageError:
            git(repo_dir, "worktree", "remove", "--force", str(workdir), check=False)
            if fresh:
                git(repo_dir, "branch", "-D", name, check=False)
            raise
        return workdir
    if git(repo_dir, "status", "--porcelain"):
        raise UsageError(t("deliver.cli.tree_dirty", path=repo_dir))
    if fresh:
        # recutting in place is safe only when the branch holds nothing the base lacks
        if git(repo_dir, "rev-parse", "--verify", "-q", f"refs/heads/{name}", check=False) \
                and git(repo_dir, "rev-list", "--count", f"{base_sha}..{name}") != "0":
            raise UsageError(t("deliver.cli.branch_ahead", branch=name, path=repo_dir))
        git(repo_dir, "checkout", "-q", "-B", name, base_sha)
    else:
        git(repo_dir, "checkout", "-q", name)
    return repo_dir


def stage_return(path: str, key: str, optional: bool = False):
    """One return of the deliver workflow, read from whatever file a person has at hand:
    the bare object, the workflow's `{stage, measure, written, refutation}`, or that inside
    the harness's task output (`summary`, `logs`, `result`). The first front-end delivery
    passed the harness file to `plan`, which read no key and rendered an empty plan.
    `optional` is a return a stage may leave empty — a small item has no sceptic."""
    data = read_json(pathlib.Path(path))
    if isinstance(data, dict) and isinstance(data.get("result"), dict) and "summary" in data:
        data = data["result"]
    if isinstance(data, dict) and "stage" in data:
        if not isinstance(data.get(key), (dict, list)):
            if optional:
                return None
            raise UsageError(t("deliver.cli.stage_return_missing", field=key, path=path))
        return data[key]
    return data


def uses_worktree(block: dict) -> bool:
    """Every item is delivered in a worktree of its own: the person's checkout is never
    touched, and several items are delivered in one repository at once. A profile opts
    out by saying `worktree: false`, in words — a suite that cannot reach a worktree."""
    return block.get("worktree", True) is not False


def agent_commands(profile: dict, workdir: pathlib.Path) -> dict:
    """What the writer may run while it works, resolved for this tree: the suite over the
    test files it names, and the lint. Both are the profile's, judged by the allowlist
    as templates. The whole suite is never among them — that is `conclude`'s, once, and
    only for an item whose size asks it."""
    from evidence import authorise, resolve
    checks = profile.get("checks") or {}
    suite, lint = checks.get("suite") or {}, checks.get("lint") or {}
    out = {"tests": None, "lint": None,
           "red_name_pattern": suite.get("red_name_pattern"),
           "failure_pattern": suite.get("failure_pattern")}
    if suite.get("files_command") and authorise(suite["files_command"], profile)[0]:
        out["tests"] = resolve(suite["files_command"], profile, workdir)
    if lint.get("command") and authorise(lint["command"], profile)[0]:
        out["lint"] = resolve(lint["command"], profile, workdir)
    return out


def scope_roots(block: dict, kind: str) -> list[str]:
    """Where a delivery of this kind may write. Documentation the change makes stale
    travels with it, where the profile says where documentation lives — except in a
    coverage gap, which only adds tests."""
    generated = list(block.get("generated") or [])
    if kind == "coverage-gap":
        return list(block["test_roots"]) + generated
    return list(block["source_roots"]) + list(block["test_roots"]) + list(block.get("doc_roots") or []) + generated


def profile_extract(profile: dict, package: pathlib.Path) -> str:
    """What a delivery's agents read of the profile, and nothing else: the stack, the
    conventions, the base, and how the suite and the lint are run and read. The `delivery`
    block travels beside it, already resolved. The whole profile — anchors, evals, the
    review's own roles, every comment — used to be pasted into the workflow's arguments
    and repeated in every message of every agent: eighteen thousand characters on the
    first real delivery on 3.0, for a surveyor that needed the suite's red-name pattern."""
    import yaml
    checks = profile.get("checks") or {}
    kept = {"profile": profile.get("profile"), "stack": profile.get("stack"),
            "conventions": profile.get("conventions"), "base": profile.get("base"),
            "checks": {role: checks[role] for role in ("suite", "lint") if checks.get(role)}}
    return (f"# An extract: what a delivery reads. The whole profile is {package / 'profile.yml'}\n"
            + yaml.safe_dump({k: v for k, v in kept.items() if v}, allow_unicode=True, sort_keys=False, width=100))


def measured_run(repo: str, tree: str, command: str | None, package: pathlib.Path) -> str | None:
    """A suite run already taken over this exact tree by this exact command, copied into
    `package` as its base run. The tree is git's, so a commit whose message alone changed
    — or a branch continued from a delivered head — is the same tree. Returns the run's
    path, or None."""
    if not tree or not command:
        return None
    repo_dir = dir_repositories() / repo
    for other in sorted(dir_packages().glob(f"{repo}-*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if other == package or not (other / "profile.yml").exists():
            continue
        try:
            import yaml
            prof = yaml.safe_load((other / "profile.yml").read_text(encoding="utf-8")) or {}
        except Exception:                                     # noqa: BLE001
            continue
        if ((prof.get("checks") or {}).get("suite") or {}).get("command") != command:
            continue
        candidates = []
        man = other / "deliver-manifest.json"
        if man.exists():
            candidates.append((read_json(man).get("base", {}).get("sha"), other / "run-suite-base.txt"))
        rec = other / "delivery.json"
        if rec.exists() and read_json(rec).get("state") == DELIVERED:
            candidates.append((read_json(rec).get("branch", {}).get("sha"), other / "run-suite-branch.txt"))
        for sha, run in candidates:
            if sha and run.exists() and git(repo_dir, "rev-parse", f"{sha}^{{tree}}", check=False) == tree:
                shutil.copy2(run, package / "run-suite-base.txt")
                return str(run)
    return None


def release_worktree(package: pathlib.Path) -> None:
    """The worktree a delivery was cut in goes away with the marker; the branch stays,
    because the branch is the delivery and the worktree was only where it was written.
    A package that was not cut in a worktree has nothing to remove."""
    path = package / "deliver-manifest.json"
    if not path.exists():
        return
    man = read_json(path)
    if not man.get("in_worktree"):
        return
    workdir = pathlib.Path(man["workdir"])
    if workdir.exists():
        git(dir_repositories() / man["repo"], "worktree", "remove", "--force", str(workdir))
    print(t("deliver.cli.worktree_removed", path=workdir, branch=man["branch"]["name"]))


def prepare(args) -> int:
    package = pathlib.Path(args.package or (dir_packages() / f"{args.repo}-deliver-{int(time.time())}"))
    package.mkdir(parents=True, exist_ok=True)
    fix = pathlib.Path(args.fix) if getattr(args, "fix", None) else None
    # A person widened an item already delivered on its branch: the new ask is written on
    # that branch, and its head is the base, as a fix's is — measured on the first front-end
    # delivery, whose owner added two rules of the same screen after the review approved it.
    continuing = bool(getattr(args, "continue_branch", False))
    if continuing and not args.branch:
        raise UsageError(t("deliver.cli.continue_needs_branch"))
    if fix:
        # A fix delivers the routes a review opened, on the review's own branch: the
        # item is written from the findings, and the base is that branch's head, so the
        # red before the green is measured against what the review read.
        chosen = [i.strip() for i in (getattr(args, "findings", None) or "").split(",") if i.strip()]
        fix_id, fix_origin, text = fix_item(fix, chosen or None)
        args.item = args.item or fix_id
        proof_only = not args.kind and not getattr(args, "shape", None) and fix_is_proof_only(fix, chosen or None)
        args.kind = args.kind or ("coverage-gap" if proof_only else "behaviour-change")
        args.shape = getattr(args, "shape", None) or ("debt" if proof_only else "bug")
        if proof_only:
            print(t("deliver.cli.fix_proof_only"))
        args.branch = args.branch or (fix_origin if fix_origin and not fix_origin.startswith("refs/")
                                      and fix_origin != "HEAD" else None)
        if not args.branch:
            raise UsageError(t("deliver.cli.fix_needs_branch", origin=fix_origin))
        (package / "item.md").write_text(text, encoding="utf-8")
    else:
        read_item(args.item_file, package)
    related = read_related(getattr(args, "related_file", None), package)

    _py("preflight.py", "--repo", args.repo, "--package", str(package),
        "--origin", "branch", "--item-read", check=False)
    cap = capabilities_after_preflight(package)
    if cap["impediments"]:
        for impediment in cap["impediments"]:
            print(t("review.impediment", reason=impediment))
        raise UsageError(t("review.impediment_open"))

    profile = load_profile(args.repo)
    block = delivery_block(profile, args.repo)
    shutil.copy2(profile_path(args.repo), package / "profile.yml")

    try:
        shape = resolve_shape(getattr(args, "shape", None), getattr(args, "type", None),
                              dict(cfg().tracker.shapes))
    except UnknownShape as e:
        raise UsageError(str(e))
    rules = SHAPES[shape]
    item_text = (package / "item.md").read_text(encoding="utf-8")
    common_args = {"package": str(package), "repo": args.repo, "shape": shape,
                   "plan_depth": rules["plan"], "gate": rules["gate"],
                   "delivery": block_as_the_agents_read_it(block, shape),
                   # The repository's own conventions, as pointers: the writer reads them
                   # before the first line, the way the reviewers read them before judging.
                   "conventions": list(profile.get("conventions") or []),
                   # The installation's model name per demand tier (prumo.yml `models:`);
                   # the workflow assigns each agent its tier. Empty map → every agent
                   # inherits the session's model, the behaviour before tiers existed.
                   "models": models_map(),
                   "agent_prefix": _agent_prefix(),
                   # A round that answers a review: its proof is what the review doubted,
                   # so the sceptic reads it whatever its size.
                   "answers_review": bool(fix),
                   # Where the item began: the base the review measured. A round that
                   # closes a cause reads every earlier round from here, for what it left dead.
                   "item_base": (read_json(fix / "manifest.json").get("merge_base")
                                 if fix else None)}
    if not rules["deliverable"]:
        # An epic is cut into stories and stops there: no branch, no suite, no marker.
        manifest = {"repo": args.repo, "item": {"id": args.item, "kind": "none", "source": "tracker"},
                    "shape": shape, "plan_depth": rules["plan"], "gate": rules["gate"],
                    "budget": budget_for(block, shape), "deliverable": False}
        write_json(package / "deliver-manifest.json", manifest)
        write_json(package / "workflow-args.json", {
            **common_args, "item": manifest["item"],
            "inline": {"item.md": item_text, **({"related.md": related} if related else {})}})
        print("\n" + t("review.package_ready", path=package))
        print(_invocation(package, "design"))
        print(f"deliver.py plan --package {package} --design <the file its return was saved to>")
        return 0
    kind = args.kind or (rules["kinds"][0] if rules["kinds"] else "coverage-gap")
    if not args.kind and kind == "behaviour-change" and continuing:
        # A widened item that only adds tests is no behaviour change: nothing goes red on
        # its base, and the round comes back BLOCKED after the writer's whole context.
        print(t("deliver.cli.kind_presumed", kind=kind))
    if kind not in rules["kinds"]:
        raise UsageError(t("deliver.cli.kind_not_for_shape", kind=kind, shape=shape,
                           kinds=", ".join(rules["kinds"])))
    if kind not in (block.get("kinds") or []):
        raise UsageError(t("deliver.unsupported.kind", kind=kind,
                           kinds=", ".join(block.get("kinds") or []) or "-"))

    # F1 — the base, resolved the way the review resolves it: remote first.
    _py("resolver.py", "--repo", args.repo, "--item", args.item, "--package", str(package))
    man = read_json(package / "manifest.json")
    repo_dir = dir_repositories() / args.repo
    git(repo_dir, "fetch", "-q", "origin", check=False)
    base_sha = man["base"]["sha"]
    base_name = man["base"]["name"]

    # F4 — the branch, cut from the base before anything is written. A fix continues
    # the branch it was asked about, and that branch's head is its base.
    name = branch_name(block, args.item, item_text, args.branch)
    if rules.get("base") and not fix:
        hotfix_base = block.get(rules["base"])
        if not hotfix_base:
            raise UsageError(t("deliver.cli.hotfix_base_missing"))
        base_sha = (git(repo_dir, "rev-parse", "--verify", "-q", f"origin/{hotfix_base}", check=False)
                    or git(repo_dir, "rev-parse", "--verify", "-q", hotfix_base, check=False))
        if not base_sha:
            raise UsageError(t("resolver.branch_unresolved", branch=hotfix_base))
        base_name = hotfix_base
    if fix or continuing:
        base_sha = (git(repo_dir, "rev-parse", "--verify", "-q", name, check=False)
                    or git(repo_dir, "rev-parse", "--verify", "-q", f"origin/{name}", check=False))
        if not base_sha:
            raise UsageError(t("resolver.branch_unresolved", branch=name))
        base_name = name
    if name in cfg().forge.protected_branches:
        raise UsageError(t("deliver.cli.branch_protected", branch=name))
    fresh = not (fix or continuing)
    if not uses_worktree(block):
        print(t("deliver.cli.no_worktree", repo=args.repo))
    workdir = cut_branch(repo_dir, block, name, base_sha, fresh=fresh)

    # No suite runs here. The base used to be measured before anything was written —
    # twenty minutes of a whole suite, twice when it timed out, ahead of a two-file fix.
    # `conclude` measures the base only when the branch turns something red, and reuses
    # a run already taken over the same tree.
    manifest = {
        "repo": args.repo, "item": {"id": args.item, "kind": kind, "source": "tracker"},
        "shape": shape, "plan_depth": rules["plan"], "gate": rules["gate"],
        "budget": budget_for(block, shape),
        "base": {"name": base_name, "sha": base_sha},
        "branch": {"name": name, "sha": git(workdir, "rev-parse", "HEAD")},
        "workdir": str(workdir), "in_worktree": uses_worktree(block),
        "previous_head": man["head_sha"],
        "delivery": block,
    }
    write_json(package / "deliver-manifest.json", manifest)

    mark(package, workdir, [], branch=name, roots=scope_roots(block, kind))
    from common import glob_to_regex
    write_json(package / "workflow-args.json", {
        **common_args, "item": manifest["item"], "workdir": str(workdir),
        "base": manifest["base"], "branch": name,
        # What the writer may run, already resolved for this tree; the size's two numbers
        # and the wide paths as patterns, so the workflow sizes the item the way
        # `shape.size_of` will size the diff.
        "commands": agent_commands(profile, workdir),
        "commit_format": block["commit_format"],
        "small": small_limits(block),
        "wide_patterns": [glob_to_regex(p).pattern for p in block.get("wide_paths") or []],
        "evidence": block.get("evidence"),
        "evidence_dir": str(dir_reports() / args.repo / f"{args.item}-deliver" / "evidence"),
        "inline": {"item.md": item_text, "profile.yml": profile_extract(profile, package),
                   **({"related.md": related} if related else {})},
    })
    print("\n" + t("review.package_ready", path=package))
    print(t("deliver.cli.branch_at", branch=name, path=workdir, base=base_sha[:12]))
    print(f"  {t('deliver.report.shape').lower()}: {shape} · {rules['plan']}"
          + (t("deliver.cli.plan_gate") if rules["gate"] else ""))
    print("\n" + t("review.invoke_workflow") + "\n")
    if shape == "story":
        print(_invocation(package, "design"))
        print(f"deliver.py plan --package {package} --design <the file its return was saved to>")
    elif rules["gate"]:
        print(_invocation(package, "measure"))
        print(f"deliver.py plan --package {package} --measure <the file its return was saved to>")
    else:
        print(_invocation(package, "deliver"))
        print("\n" + t("deliver.cli.then_conclude", path=package))
    return 0


def _invocation(package: pathlib.Path, stage: str) -> str:
    return ("Workflow({name: '" + _workflow_name("deliver") + "', args: <the object in "
            + str(package / "workflow-args.json") + f"> + stage: '{stage}'}})")


# --- the plan and its gate ---------------------------------------------------------------

def digest(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def without_mutations(kind: str, measure: dict) -> tuple[dict, list[dict]]:
    """A behaviour change names no mutation: the writer replaces the very lines a
    mutant would point at, so the mutant's `from` text is gone by the time `conclude`
    applies it. A fix round of the first story (2026-11-12) carried two such mutants on
    pre-fix code. Returns the measurement without them and the ones dropped, which the
    plan declares instead of silently losing."""
    if kind != "behaviour-change" or not measure.get("mutations"):
        return measure, []
    return dict(measure, mutations=[]), list(measure["mutations"])


def report_name(package: pathlib.Path) -> str:
    """The report's file under the item's directory: one per package and day. It was one
    per day, and on 2026-11-17/18 every conclude of a fix or a block overwrote the
    delivery's own report — twelve rounds of one item left one file. A package concluded
    again the same day still replaces its own."""
    return f"deliver-{today()}-{package.name}.md"


def block_as_the_agents_read_it(block: dict, shape: str) -> dict:
    """The profile's delivery block with its budget already resolved for the shape.

    The raw block carries the budget of one item; a story's is scaled from it. The design
    of a story (2026-11-18) read the raw `budget` in its prompt and wrote "8 files, above
    the ceiling of 7, waiver expired" into the summary a person approves — the story's
    budget was 21 files. A number an agent restates is one nobody computed: the agents get
    the number the spine will hold the diff to, and the plan prints it."""
    return {**block, "budget": budget_for(block, shape)}


def budget_line(man: dict) -> str:
    """What the plan says about the budget — the numbers, or that the profile declares
    none. Said either way: a stop that cannot happen is not a silence."""
    budget = man.get("budget")
    if not budget:
        return "budget none — the profile declares no `delivery.budget`"
    return f"budget {budget['max_files']} files · {budget['max_lines']} lines"


def render_plan(man: dict, measure: dict, dropped: list[dict] | None = None) -> str:
    """The plan a person reads before a line is written: what was measured, what will
    be touched, what will prove it. One fact per line, each with an address."""
    L = lambda key: t(f"deliver.report.{key}")  # noqa: E731
    rules = SHAPES[man["shape"]]
    lines = [f"{L('plan'):9}{man['item']['id']} · {man['shape']} · {man['plan_depth']}"
             + (f" · {t('deliver.report.gate_pending')}" if rules["gate"] else ""),
             f"{L('base'):9}{man['base']['name']}@{man['base']['sha'][:7]}   "
             f"{L('branch')} {man['branch']['name']}",
             f"{'':9}{budget_line(man)}", ""]
    lines.append(L("premises"))
    for p in measure.get("premises") or []:
        lines.append(f"{SYMBOL[p['state']]} {p['text']} — {p.get('evidence') or '?'}"
                     + (" · central" if p.get("central") else ""))
    if measure.get("existing"):
        lines += ["", L("existing")]
        for e in measure["existing"]:
            lines.append(f"  {e['kind']}: {e['what']} — {e.get('where') or t('deliver.report.existing_none')}")
    lines += ["", f"{L('diff'):9}" + ", ".join(measure.get("files") or []) or "?"]
    lines += ["", L("proof")]
    for m in dropped or []:
        lines.append("  " + t("deliver.cli.mutation_dropped_behaviour_change",
                              file=m.get("file", "?"), line=m.get("line", "?"), test=m.get("test", "?")))
    for m in measure.get("mutations") or []:
        lines.append(f"  {m['file']}:{m['line']} `{m['from']}`→`{m['to']}` → {m['test']} red"
                     + (f" · stays green: {', '.join(m['stays_green'])}" if m.get("stays_green") else ""))
    # Only what this kind proves: a surveyor that filled every field of the shape it
    # was given would otherwise have the plan promise a red-before-green nobody runs.
    kind = man["item"]["kind"]
    if kind == "behaviour-change" and measure.get("proving_tests"):
        lines.append(f"  red on base, green on branch: {', '.join(measure['proving_tests'])}")
    if kind == "refactor":
        lines.append(f"  stays green: {', '.join(measure.get('guarding_tests') or []) or '? (none — the delivery stops)'}")
    lines.append("")
    return "\n".join(lines)


def render_design(man: dict, design: dict) -> str:
    L = lambda key: t(f"deliver.report.{key}")  # noqa: E731
    rules = SHAPES[man["shape"]]
    lines = [f"{L('plan'):9}{man['item']['id']} · {man['shape']} · {man['plan_depth']}"
             + (f" · {t('deliver.report.gate_pending')}" if rules["gate"] else ""),
             f"{'':9}{design.get('summary', '')}"]
    if man.get("deliverable", True):
        lines.append(f"{L('base'):9}{man['base']['name']}@{man['base']['sha'][:7]}   "
                     f"{L('branch')} {man['branch']['name']}")
    lines.append(f"{'':9}{budget_line(man)}"
                 + (" · over every task" if design.get("tasks") and man.get("budget") else ""))
    for x in design.get("tasks") or []:
        lines += ["", f"{x['id']}  {x['title']}"
                      + (f"  ← {', '.join(x['depends_on'])}" if x.get("depends_on") else "")]
        lines += [f"  ✔ {c}" for c in x["criteria"]]
        lines.append(f"  files: {', '.join(x['files'])}")
        lines.append(f"  red on base, green after: {', '.join(x['proving_tests'])}")
    for st in design.get("stories") or []:
        lines += ["", f"story  {st['title']}"]
        lines += [f"  ✔ {c}" for c in st["criteria"]]
        lines.append(f"  not: {st['out_of_scope']}")
    lines.append("")
    return "\n".join(lines)


def plan(args) -> int:
    package = pathlib.Path(args.package)
    man = read_json(package / "deliver-manifest.json")
    rules = SHAPES[man["shape"]]
    import jsonschema
    if getattr(args, "design", None):
        design = stage_return(args.design, "design")
        jsonschema.validate(design, read_json(ROOT.parent / "schemas" / "design.schema.json"))
        write_json(package / "design.json", design)
        subject, text = design, render_design(man, design)
    else:
        measure, dropped = without_mutations(man["item"]["kind"], stage_return(args.measure, "measure"))
        write_json(package / "measure.json", measure)
        subject, text = measure, render_plan(man, measure, dropped)
    record = {"shape": man["shape"], "depth": man["plan_depth"], "gate": rules["gate"],
              "digest": digest(subject), "rendered_at": today()}
    write_json(package / "plan.json", record)
    # The next stage reads what was planned from workflow-args.json: the conductor
    # pastes that object and a stage name, and composes nothing.
    carry(package, **({"design": subject} if getattr(args, "design", None) else {"measure": subject}))
    (package / "plan.md").write_text(text, encoding="utf-8")
    print((package / "plan.md").read_text(encoding="utf-8"))
    print(t("deliver.cli.plan_at", path=package / "plan.md", shape=man["shape"],
            depth=man["plan_depth"],
            gate=t("deliver.cli.plan_gate") if rules["gate"] else t("deliver.cli.plan_no_gate")))
    return 0


def carry(package: pathlib.Path, **fields) -> None:
    """Put `fields` into the package's workflow-args.json — what the next workflow stage
    needs and a person would otherwise have to paste by hand. None removes a field."""
    path = package / "workflow-args.json"
    if not path.exists():
        return
    data = read_json(path)
    for key, value in fields.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    write_json(path, data)


def red_excerpt(output: str, names: list[str], each: int = 1500, total: int = 6000) -> str:
    """What the run printed about each red, from the first line that names it — the
    part a second write needs, without the thousands of lines around it."""
    parts = []
    for name in names:
        at = output.find(name)
        if at >= 0:
            parts.append(output[max(0, at - 200):at + each])
    return "\n…\n".join(parts)[:total] if parts else output[-total:]


def approve(args) -> int:
    from common import owner
    package = pathlib.Path(args.package)
    who = (args.by or owner() or "").strip()
    if not who:
        raise UsageError(t("deliver.cli.approval_needs_name"))
    plan_record = read_json(package / "plan.json")
    write_json(package / "approval.json", {"by": who, "at": today(), "digest": plan_record["digest"]})
    print(t("deliver.cli.approved", by=who, path=package / "approval.json"))
    return 0


def approval_of(package: pathlib.Path) -> str | None:
    """Who approved the plan as it stands — None when nobody did, or when the
    measurement changed after the approval."""
    plan_path, ok_path = package / "plan.json", package / "approval.json"
    if not (plan_path.exists() and ok_path.exists()):
        return None
    plan_record, ok = read_json(plan_path), read_json(ok_path)
    return ok.get("by") if ok.get("digest") == plan_record.get("digest") else None


def _agent_prefix() -> str:
    from triage import agent_prefix
    return agent_prefix()


def _workflow_name(name: str) -> str:
    from triage import workflow_name
    return workflow_name(name)


# --- F6 F8 F9: conclude ------------------------------------------------------------------

def changed_files(workdir: pathlib.Path, base_sha: str,
                  generated: list[str] | None = None) -> tuple[list[str], int, int]:
    """Every file the tree differs from the base in — tracked or new — with the lines.
    A file the profile declares generated is listed, and its lines are not counted."""
    untracked = [l for l in git(workdir, "ls-files", "--others", "--exclude-standard").splitlines() if l]
    for f in untracked:
        git(workdir, "add", "-N", "--", f)
    files, added, removed = [], 0, 0
    for line in git(workdir, "diff", "--numstat", base_sha).splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        a, r, f = parts
        files.append(f)
        if casa_algum(f, list(generated or [])):
            continue
        added += int(a) if a.isdigit() else 0
        removed += int(r) if r.isdigit() else 0
    return files, added, removed


def scope_guard(files: list[str], allowed: list[str], roots: list[str]) -> list[str]:
    """A file outside what the measurement named, or outside the roots the kind allows,
    is outside the scope. The hook holds this while the writer works; this holds it
    over what actually landed."""
    patterns = [p for r in roots for p in (r.rstrip("/"), r.rstrip("/") + "/**")]
    return [f for f in files if f not in allowed or not casa_algum(f, patterns)]


def measure_of_design(design: dict) -> dict:
    """A story is measured by its design: the files its tasks named and the tests that
    prove them. Nobody measures each task again before writing it — the person approved
    the cut, and the writer reads the code it is about to change."""
    tasks = design.get("tasks") or []
    return {"premises": [], "mutations": [], "checked": list(design.get("checked") or []),
            "files": sorted({f for x in tasks for f in x.get("files") or []}),
            "proving_tests": [p for x in tasks for p in x.get("proving_tests") or []]}


def tasks_written(design: dict, returned: list | None) -> tuple[dict, dict]:
    """A story's tasks, as the workflow returned them, folded into one `written` and one
    state per task: `delivered` when its writer wrote it, `blocked` when the writer said
    why it could not, `pending` when the workflow never reached it."""
    by_id = {x.get("id"): (x.get("written") or {}) for x in returned or []}
    written = {"files": [], "skipped": [], "reused": [], "blocked": None}
    states = {}
    # a fix written after the story's check found something is no task of the design:
    # its files join the story's, its state is nobody's
    known = {task["id"] for task in design.get("tasks") or []}
    for extra, w in by_id.items():
        if extra not in known and not w.get("blocked"):
            written["files"] += [f for f in w.get("files") or [] if f not in written["files"]]
    for task in design.get("tasks") or []:
        w = by_id.get(task["id"])
        if not w:
            states[task["id"]] = "pending"
            continue
        states[task["id"]] = BLOCKED if w.get("blocked") else DELIVERED
        if w.get("blocked") and not written["blocked"]:
            written["blocked"] = f"{task['id']}: {w['blocked']}"
        written["files"] += [f for f in w.get("files") or [] if f not in written["files"]]
        written["skipped"] += [f"{task['id']}: {s}" for s in w.get("skipped") or []]
        written["reused"] += [r for r in w.get("reused") or [] if r not in written["reused"]]
    return written, states


def runnable_tests(files: list[str], block: dict, check: dict, workdir: pathlib.Path) -> list[str]:
    """The files of `files` a runner can be handed: under a test root, present in the
    tree, and — when the profile says what a test file is called — called that. A
    fixture changed beside the tests is not a test, and a runner handed one stops."""
    under = [r.rstrip("/") + "/**" for r in block.get("test_roots") or []]
    named = list(check.get("test_files") or [])
    return [f for f in files if casa_algum(f, under) and (workdir / f).is_file()
            and (not named or casa_algum(f, named))]


def own_base_run(package: pathlib.Path, check: dict) -> str | None:
    """The whole suite on the base, already taken by an earlier `conclude` of THIS package.
    A package has one base, so the run is the same measurement — when the command is the
    one the package was prepared with and the run finished: a run nobody can count, or one
    cut short, is measured again. The first real delivery on 3.0 concluded twice, blocked
    and then delivered, and paid the base's whole suite both times."""
    from evidence import progress
    run, snapshot = package / "run-suite-base.txt", package / "profile.yml"
    if not run.exists() or not snapshot.exists() or not check.get("command"):
        return None
    import yaml
    prepared = ((yaml.safe_load(snapshot.read_text(encoding="utf-8")) or {}).get("checks") or {}).get("suite") or {}
    if prepared.get("command") != check.get("command"):
        return None
    output = run.read_text(encoding="utf-8")
    executed, predicted = progress(output, check.get("progress_pattern"))
    if executed is None or predicted is None or executed < predicted:
        return None
    return output


def base_run(profile: dict, man: dict, workdir: pathlib.Path, package: pathlib.Path,
             files: list[str], tests: list[str] | None) -> tuple[str | None, str]:
    """The suite on the base, taken only because the branch turned something red — a
    green branch owes no baseline. Returns (output, how): `reused` when an earlier
    package measured this very tree with this very command, `measured` when it ran
    here over the base's version of every changed file, and (None, reason) when it did
    not run. `tests` narrows it to the files the branch run was narrowed to."""
    check = (profile.get("checks") or {}).get("suite") or {}
    if tests is None:
        own = own_base_run(package, check)
        if own is not None:
            print(t("deliver.cli.baseline_reused", run=str(package / "run-suite-base.txt")))
            return own, "reused"
        tree = git(workdir, "rev-parse", f"{man['base']['sha']}^{{tree}}", check=False)
        reused = measured_run(man["repo"], tree, check.get("command"), package)
        if reused:
            print(t("deliver.cli.baseline_reused", run=reused))
            return (package / "run-suite-base.txt").read_text(encoding="utf-8"), "reused"
    with with_base_versions(workdir, man["base"]["sha"], files):
        on_base = None if tests is None else [f for f in tests if (workdir / f).is_file()]
        if tests is not None and not on_base:
            return "", "measured"                 # none of those test files exists on the base
        r = run_suite(profile, workdir, package, "base" if tests is None else "base-focused",
                      tests=on_base)
    if not r.get("ran"):
        return None, r.get("reason") or t("evidence.did_not_run")
    return r.get("output", ""), "measured"


def conclude(args) -> int:
    package = pathlib.Path(args.package)
    man = read_json(package / "deliver-manifest.json")
    profile = load_profile(man["repo"])
    block = delivery_block(profile, man["repo"])
    workdir = pathlib.Path(man["workdir"])
    kind = man["item"]["kind"]
    shape = man.get("shape") or "debt"
    result = getattr(args, "result", None)
    story = shape == "story"
    task_states = None
    if story:
        design = read_json(package / "design.json")
        measure = measure_of_design(design)
        kept = read_json(package / "tasks.json") if (package / "tasks.json").exists() else {}
        for x in (stage_return(result, "tasks") if result else []) or []:
            if x.get("written"):
                kept[x["id"]] = x["written"]
        write_json(package / "tasks.json", kept)
        written, task_states = tasks_written(design, [{"id": k, "written": v} for k, v in kept.items()])
        refutation = None
    else:
        source = getattr(args, "measure", None) or result or str(package / "measure.json")
        measure, _ = without_mutations(kind, stage_return(source, "measure"))
        if getattr(args, "written", None):
            written = stage_return(args.written, "written")
        else:
            written = (stage_return(result, "written", optional=True) if result else None) \
                or {"files": [], "skipped": []}
        if getattr(args, "refutation", None):
            refutation = stage_return(args.refutation, "refutation")
        else:
            refutation = stage_return(result, "refutation", optional=True) if result else None
        write_json(package / "measure.json", measure)

    premises = measure.get("premises") or []
    allowed = list(measure.get("files") or []) + list(written.get("files") or [])
    generated = list(block.get("generated") or [])
    roots = scope_roots(block, kind)

    files, added, removed = changed_files(workdir, man["base"]["sha"], generated)
    # What was written is what the tree holds, not what the writer remembered. A second
    # attempt on the same tree declared only the files it touched in that pass (three
    # tasks of the first story, 2026-11-12), and the record read a partial delivery. The
    # writer's list is kept, and every file the measurement named that differs from the
    # base joins it; anything else stays outside the scope below.
    declared = list(written.get("files") or [])
    named = set(measure.get("files") or [])
    written = dict(written, files=declared + [f for f in files if f in named and f not in declared])
    write_json(package / "written.json", written)
    # The budget is the profile's AS IT STANDS, like the waiver beside it: a person who
    # raises it, or removes it, after a `blocked` concludes the same package again and is
    # heard. The manifest keeps the one the plan was rendered with. No budget declared,
    # no stop: the size of a change is a repository's decision, never the method's.
    budget = budget_for(block, shape) or {}
    # The plan is rendered here too when nobody did, so the record always holds one;
    # the gate reads the approval against the measurement as it stands.
    subject = design if story else measure
    if not (package / "plan.json").exists():
        write_json(package / "plan.json", {"shape": shape, "depth": man.get("plan_depth", "measure"),
                                           "gate": SHAPES[shape]["gate"], "digest": digest(subject),
                                           "rendered_at": today()})
        (package / "plan.md").write_text(render_plan(man, measure), encoding="utf-8")
    elif read_json(package / "plan.json").get("digest") != digest(subject):
        write_json(package / "plan.json", dict(read_json(package / "plan.json"), digest=digest(subject)))
    plan_record = {"shape": shape, "depth": man.get("plan_depth", "measure"),
                   "gate": SHAPES[shape]["gate"], "approved_by": approval_of(package),
                   "budget": {"max_files": int(budget["max_files"]),
                              "max_lines": int(budget["max_lines"])} if budget else None}
    counted = [f for f in files if not casa_algum(f, generated)]
    diff = {"files": files, "added": added, "removed": removed,
            "outside_scope": scope_guard(files, allowed, roots),
            "within_budget": len(counted) <= budget.get("max_files", 10 ** 9)
                             and added + removed <= budget.get("max_lines", 10 ** 9)}

    # The size of what LANDED decides what is run, once: the test files the change
    # touched, or the whole suite. Measured on the first front-end delivery: three whole
    # suites of twenty minutes for a change of two files and sixteen lines.
    size = size_of(shape, counted, added + removed, block)
    check = (profile.get("checks") or {}).get("suite") or {}
    changed_tests = runnable_tests(files, block, check, workdir)
    related = [f for f in runnable_tests(list(measure.get("related_tests") or []), block, check, workdir)
               if f not in changed_tests]
    focused = changed_tests + related
    by_files = bool(check.get("files_command") and focused)
    scope = "focused" if size["size"] == "small" and by_files else "whole"

    from evidence import compare_reds, red_names, progress
    central_failed = any(p["central"] and p["state"] == "fails" for p in premises)
    # a refactor with nothing standing guard runs nothing: the stop is the person's
    if kind == "refactor" and not (measure.get("guarding_tests") or []):
        central_failed = True

    mutations, suite_out, suite_ran, suite_file = [], "", False, None
    red_on_base = green_on_branch = guarding = None
    base_out, base_how, base_file = "", "not_needed", None
    lint = {"state": "not_run", "reason": t("review.suite_not_run"), "run": None}
    gate_shut = plan_record["gate"] and not plan_record["approved_by"]
    stopped_early = bool(written.get("blocked")) or (story and any(s != DELIVERED for s in task_states.values()))
    stopped_by_diff = diff_stop(diff, block.get("waiver"))
    if not central_failed and files and not gate_shut and not stopped_early and not stopped_by_diff:
        # The mutation by hand: wrong at the named line, the tests, the named test red,
        # the line put back. One run per DISTINCT mutation, over the test files the
        # change touched when the profile can run files — a mutant is judged by the
        # tests the item names, never by the whole suite once more.
        groups: dict[tuple, list[dict]] = {}
        for m in measure.get("mutations") or []:
            groups.setdefault((m["file"], m["line"], m["from"], m["to"]), []).append(m)
        for n, ((file, line, src, dst), wanted) in enumerate(groups.items(), 1):
            why = apply_mutation(workdir, {"file": file, "line": line, "from": src, "to": dst})
            reds, ran, reverted, run_file = [], False, False, None
            if why is None:
                r = run_suite(profile, workdir, package, f"mutation-{n}", tests=focused if by_files else None)
                # A mutation run that did not finish proves nothing either way: a fatal
                # in the mutated file ends the run before the named test is reached,
                # and "no red" is then not "green".
                executed, predicted = progress(r.get("output", ""), check.get("progress_pattern"))
                finished = predicted is None or (executed is not None and executed >= predicted)
                ran = bool(r.get("ran")) and finished
                run_file = f"run-suite-mutation-{n}.txt" if r.get("ran") else None
                reds = red_names(r.get("output", ""), check.get("red_name_pattern")) or []
                revert(workdir, file)
                reverted = not git(workdir, "diff", "--name-only", "--", file)
            for m in wanted:
                entry = {"file": file, "line": line, "from": src, "to": dst, "test": m["test"],
                         "result": "not_run", "reverted": reverted,
                         "stays_green": list(m.get("stays_green") or []),
                         "stays_green_result": None, "run": run_file, "reds": sorted(set(reds)) if ran else []}
                if why is None:
                    entry["result"] = ("red" if any(same_test(m["test"], name) for name in reds)
                                       else "green") if ran else "not_run"
                    if ran and entry["stays_green"]:
                        entry["stays_green_result"] = ("red" if any(
                            same_test(g, name) for g in entry["stays_green"] for name in reds) else "green")
                else:
                    entry["why"] = why
                mutations.append(entry)
        # Red before green: the base's production files under the tests the change wrote,
        # and only those test files run. Red when every proving test the measurement
        # named was read red; partial when something went red and a name was not read —
        # a name is how the runner prints it, and a paraphrase is not a missing proof;
        # green when nothing did, which is a test that passes without the change.
        proving = list(measure.get("proving_tests") or [])
        production = production_files(files, block)
        if kind == "behaviour-change" and production and changed_tests:
            with with_base_versions(workdir, man["base"]["sha"], production):
                r = run_suite(profile, workdir, package, "base-with-test",
                              tests=changed_tests if check.get("files_command") else None)
            on_base = red_names(r.get("output", ""), check.get("red_name_pattern")) or []
            # A test file that cannot even load against the base — it imports what the
            # change adds — is reported by the runner under the file's name, not the
            # test's. That is red on the base too: the module's name stands for its tests.
            read_red = [p for p in proving
                        if any(same_test(p, name) or file_stands_for(name, changed_tests) for name in on_base)]
            missing = [p for p in proving if p not in read_red]
            red_on_base = {"test": ", ".join(proving) or ", ".join(changed_tests),
                           "result": "not_run" if not r.get("ran")
                           else "green" if not on_base
                           else "red" if not missing else "partial"}
            if red_on_base["result"] == "partial":
                red_on_base["missing"] = missing

        suite = run_suite(profile, workdir, package, "branch" if scope == "whole" else "branch-focused",
                          tests=focused if scope == "focused" else None)
        suite_out, suite_ran = suite.get("output", ""), bool(suite.get("ran"))
        suite_file = f"run-suite-{'branch' if scope == 'whole' else 'branch-focused'}.txt" if suite_ran else None
        on_branch = red_names(suite_out, check.get("red_name_pattern")) or [] if suite_ran else []
        if proving and suite_ran:
            green_on_branch = {"test": ", ".join(proving),
                               "result": "red" if any(same_test(p, name) for p in proving for name in on_branch) else "green"}
        guards = list(measure.get("guarding_tests") or [])
        if kind == "refactor":
            guarding = {"tests": guards,
                        "result": ("red" if any(same_test(g, name) for g in guards for name in on_branch)
                                   else "green") if suite_ran else "not_run"}
        # The base is measured only when the branch has a red to explain.
        if suite_ran and on_branch:
            out, base_how = base_run(profile, man, workdir, package, files,
                                     None if scope == "whole" else focused)
            if out is None:
                base_out, base_how = "", f"not_run: {base_how}"
            else:
                base_out = out
                name = "run-suite-base.txt" if scope == "whole" else "run-suite-base-focused.txt"
                base_file = name if (package / name).exists() else None
        lint = run_lint(profile, workdir, package)

    reads = compare_reds(base_out, suite_out, check) if suite_ran else {
        "base_reds": [], "branch_reds": None, "new_reds": None,
        "vanished_reds": None, "predicted": None, "executed": None}
    if base_how.startswith("not_run"):
        reads = dict(reads, new_reds=None, vanished_reds=None)
    baseline = {"base_reds": reads["base_reds"] or [], "branch_reds": reads["branch_reds"] or [],
                "new_reds": reads["new_reds"], "vanished_reds": reads["vanished_reds"] or [],
                "predicted": reads["predicted"], "executed": reads["executed"],
                "scope": scope, "base": base_how}

    record = {
        "repo": man["repo"], "item": man["item"], "base": man["base"],
        "branch": {"name": man["branch"]["name"], "sha": git(workdir, "rev-parse", "HEAD")},
        "premises": premises,
        "size": size,
        "lint": lint,
        "plan": plan_record,
        "proof": {"red_on_base": red_on_base, "green_on_branch": green_on_branch,
                  "guarding": guarding,
                  "mutations": [{k: v for k, v in m.items() if k != "why"} for m in mutations],
                  "refutation": ({"verdict": refutation["verdict"], "reason": refutation["reason"]}
                                 if refutation else None)},
        "baseline": baseline, "diff": diff,
        "writer_blocked": written.get("blocked") or None,
        # The writer's word, verbatim. `measured` names every run the script made, as a
        # file beside the package, so the note and the proof never compete for the same
        # sentence.
        "skipped": list(written.get("skipped") or []),
        "measured": {"note": t("deliver.record.measured_note"),
                     "base": base_file, "suite": suite_file,
                     "lint": lint.get("run"),
                     "mutations": [m["run"] for m in mutations if m.get("run")]},
        "waiver": block.get("waiver"),
    }
    if story:
        record["tasks"] = task_states
    import jsonschema
    jsonschema.validate(record, read_json(ROOT.parent / "schemas" / "delivery.schema.json"))
    record.update(deliver_judge(record, profile))

    # A commit with a pathspec, in the repository's own convention — and only for a
    # delivery that stands. A story's tasks were committed one by one as they were
    # written; what is left uncommitted here, if anything, closes the branch. A blocked
    # tree stays as it is, for a person to read.
    if record["state"] == DELIVERED and files and git(workdir, "status", "--porcelain", "--", *files):
        git(workdir, "add", "--", *files)
        # The message is the writer's, written in the repository's own convention (it read
        # the conventions); without one, the item's title verbatim — a slug drops the
        # accents and cuts the sentence. Either way the item's id is not said twice.
        message = (written.get("commit_message") or "").strip() or item_title(
            (package / "item.md").read_text(encoding="utf-8"), man["item"]["id"])
        git(workdir, "commit", "-q", "-m",
            block["commit_format"].format(item=man["item"]["id"], message=message))
    record["branch"]["sha"] = git(workdir, "rev-parse", "HEAD")

    write_json(package / "delivery.json", record)
    if record["state"] == BLOCKED and record.get("owner") == "author":
        reds = list(baseline["new_reds"] or [])
        carry(package, measure=None if story else measure,
              done=[k for k, v in task_states.items() if v == DELIVERED] if story else None,
              previous={"reason": record["reason"],
                        "output": red_excerpt(suite_out, reds) if reds else "",
                        "hint": ""})
    else:
        carry(package, previous=None)
    report = render(record, mutations)
    (package / "report.md").write_text(report, encoding="utf-8")
    destination = dir_reports() / man["repo"] / f"{man['item']['id']}-deliver"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(package / "report.md", destination / report_name(package))
    unmark(package)
    print(report)
    print(t("review.report_at", path=destination / report_name(package)))
    if man.get("in_worktree"):
        print(t("deliver.cli.release_hint", path=package))

    # What this delivery cost, read from the harness's transcripts — every workflow run
    # over the package. A by-product: a failure here never costs the delivery its
    # record, and an unmeasured run is `null`, never zero.
    try:
        from telemetry import measure as measure_cost, summarise
        tel = measure_cost(str(package), None, every_run=True)
        write_json(package / "telemetry.json", tel)
        print("\n" + summarise(tel))
        record_run(package, record, tel)
    except Exception as e:                                   # noqa: BLE001 — see above
        print(t("review.knowledge_failed", error=e))
    return 0 if record["state"] == DELIVERED else 1


# --- the evidence ------------------------------------------------------------------------
#
# Evidence illustrates a delivery that is already done; it does not make it done. The
# tests the change wrote and the checks `conclude` ran are what hold the quality. So the
# evidence is produced once per item, after its last task is delivered, and nothing it
# finds changes the state: no environment is raised, no screen is captured and no
# exchange is recorded while the work is still being written.

def evidence(args) -> int:
    package = pathlib.Path(args.package)
    record_path = package / "delivery.json"
    if not record_path.exists() or read_json(record_path).get("state") != DELIVERED:
        raise UsageError(t("deliver.cli.evidence_needs_delivered", path=package))
    record = read_json(record_path)
    man = read_json(package / "deliver-manifest.json")
    collected = stage_return(args.collected, "evidence", optional=True) if getattr(args, "collected", None) else None
    repo_dir = dir_repositories() / man["repo"]
    commits = git(repo_dir, "log", "--format=%h %s", f"{man['base']['sha']}..{record['branch']['sha']}",
                  check=False).splitlines()
    L = lambda key: t(f"deliver.report.{key}")  # noqa: E731
    b, d, proof = record["baseline"], record["diff"], record["proof"]
    lines = [f"{L('evidence'):9}{record['item']['id']} · {record['branch']['name']}@{record['branch']['sha'][:7]}",
             "", L("commits")] + [f"  {c}" for c in commits]
    lines += ["", f"{L('diff'):9}{len(d['files'])} files · +{d['added']} −{d['removed']}"]
    lines += [f"  {f}" for f in d["files"]]
    lines += ["", L("proof")]
    if proof.get("red_on_base"):
        lines.append(f"  red on base, green on branch: {proof['red_on_base']['test']} — {proof['red_on_base']['result']}")
    for m in proof.get("mutations") or []:
        lines.append(f"  {m['file']}:{m['line']} `{m['from']}`→`{m['to']}` → {m['test']} {m['result']}")
    lines.append(f"  suite ({b.get('scope', 'whole')}) {b['executed']}/{b['predicted']} · new reds {len(b['new_reds'] or [])}")
    if collected:
        lines += ["", L("shown")]
        for c in collected.get("captures") or []:
            lines.append(f"✔ {c.get('criterion', '?')} — {c.get('observed', '')}"
                         + (f" · {c['artifact']}" if c.get("artifact") else ""))
        for c in collected.get("not_collected") or []:
            lines.append(f"? {c.get('criterion', '?')} — {c.get('reason', '')}")
    else:
        lines += ["", t("deliver.cli.evidence_is_the_tests")]
    lines.append("")
    text = "\n".join(lines)
    (package / "evidence.md").write_text(text, encoding="utf-8")
    write_json(package / "evidence.json", {"at": today(), "head": record["branch"]["sha"],
                                           "collected": collected})
    destination = dir_reports() / man["repo"] / f"{man['item']['id']}-deliver"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(package / "evidence.md", destination / f"evidence-{today()}.md")
    print(text)
    print(t("review.report_at", path=destination / f"evidence-{today()}.md"))
    return 0


def record_run(package: pathlib.Path, record: dict, tel: dict) -> None:
    """One line in `runs.jsonl`, beside the reviews', with the capability named so that
    `metrics.py` never mixes a delivery's cost per line into a review's median."""
    mutations = record["proof"].get("mutations") or []
    line = {
        "date": today(), "capability": "deliver", "repo": record["repo"],
        "item": record["item"]["id"], "kind": record["item"]["kind"],
        "size": (record.get("size") or {}).get("size"),
        "suite_scope": record["baseline"].get("scope"),
        "state": record["state"], "owner": record.get("owner"),
        "branch": record["branch"]["name"], "head_sha": record["branch"]["sha"],
        "files": len(record["diff"]["files"]),
        "lines_changed": record["diff"]["added"] + record["diff"]["removed"],
        "mutations_red": sum(1 for m in mutations if m["result"] == "red"),
        "mutations_total": len(mutations),
        "new_reds": len(record["baseline"].get("new_reds") or []),
        "premises_failed": sum(1 for p in record["premises"] if p["state"] == "fails"),
        "skipped": len(record["skipped"]),
        "tokens": tel.get("tokens"), "minutes": tel.get("minutes"),
        "cost_usd": tel.get("cost_usd"),
        "cost_not_measured_because": tel.get("cost_not_measured_because") or tel.get("reason"),
        "telemetry_source": tel.get("source") or ("workflow" if tel.get("tokens") is not None else None),
        "agents": tel.get("agents"), "runs": tel.get("runs"),
        "cost_per_agent": tel.get("cost_per_agent") or {},
    }
    runs = dir_reports() / "runs.jsonl"
    runs.parent.mkdir(parents=True, exist_ok=True)
    with runs.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


SYMBOL = {"holds": "✔", "fails": "✘", "not_assessable": "?", "red": "✔", "green": "✘",
          "not_run": "?", "partial": "✔"}


def sceptic_owed(record: dict) -> bool:
    """A delivered item whose diff is `standard` and that no sceptic read. A story is
    `large` and read by the review; a blocked write was never handed to a sceptic."""
    return (record.get("state") == DELIVERED
            and (record.get("size") or {}).get("size") == "standard"
            and not (record.get("proof") or {}).get("refutation"))


def render(record: dict, mutations: list[dict]) -> str:
    """One fact per line, each with an address. No prose around it."""
    L = lambda key: t(f"deliver.report.{key}")  # noqa: E731
    pl = record.get("plan") or {}
    lines = [f"{L('item'):9}{record['item']['id']} · {pl.get('shape', '?')} · {record['item']['kind']} · "
             f"{t('deliver.state.' + record['state'])}"]
    if pl.get("gate"):
        lines.append(f"{'':9}{t('deliver.report.gate_open', by=pl['approved_by']) if pl.get('approved_by') else t('deliver.report.gate_pending')}")
    if record["state"] != DELIVERED:
        lines.append(f"{'':9}{record['reason']} → {t('deliver.owner.' + record['owner'])}")
    lines += [f"{L('base'):9}{record['base']['name']}@{record['base']['sha'][:7]}   "
              f"{L('branch')} {record['branch']['name']}@{record['branch']['sha'][:7]}"]
    size = record.get("size") or {}
    if size:
        lines.append(f"{L('size'):9}{size['size']} — {size['reason']}")
    for task, state in (record.get("tasks") or {}).items():
        lines.append(f"{'':9}{SYMBOL.get({'delivered': 'holds', 'blocked': 'fails'}.get(state), '?')} {task} {state}")
    lines += ["", L("premises")]
    for p in record["premises"] or []:
        lines.append(f"{SYMBOL[p['state']]} {p['text']} — {p.get('evidence') or '?'}")
    lines += ["", L("proof")]
    rb = record["proof"].get("red_on_base")
    if rb:
        lines.append(f"{SYMBOL[rb['result']]} red on base: {rb['test']} {rb['result']}"
                     + (f" · not read among the reds: {', '.join(rb['missing'])}" if rb.get("missing") else ""))
    gd = record["proof"].get("guarding")
    if gd:
        lines.append(f"{SYMBOL['holds' if gd['result'] == 'green' else 'fails' if gd['result'] == 'red' else 'not_assessable']} "
                     f"guarding: {', '.join(gd['tests']) or '?'} {gd['result']}")
    for m in mutations:
        lines.append(f"{SYMBOL[m['result']]} {m['file']}:{m['line']} `{m['from']}`→`{m['to']}` "
                     f"→ {m['test']} {m['result']}" + (" · reverted" if m["reverted"] else "")
                     + (f" · stays green: {', '.join(m['stays_green'])} {m['stays_green_result']}"
                        if m.get("stays_green") else "")
                     + (f" · {len(m['reds'])} red beyond the base" if m.get("reds") else "")
                     + (f" · {m['why']}" if m.get("why") else ""))
    lint = record.get("lint") or {}
    if lint.get("state") != "not_run":
        lines.append(f"{SYMBOL['holds' if lint['state'] == 'green' else 'fails']} lint {lint['state']} — {lint['reason']}")
    ref = record["proof"].get("refutation")
    if ref:
        lines.append(f"{SYMBOL.get({'proves': 'holds', 'does_not_prove': 'fails'}.get(ref['verdict'], 'not_assessable'))} "
                     f"{t('deliver.report.sceptic')}: {ref['verdict']} — {ref['reason']}")
    elif sceptic_owed(record):
        # A standard item is read by the sceptic once. One that was not is said, never left
        # looking like a small one: the workflow sizes again after the write, and this is
        # what remains when the writer's count and the diff disagree.
        lines.append(f"{SYMBOL['not_assessable']} {t('deliver.report.sceptic')}: "
                     + t("deliver.report.sceptic_not_read", reason=(record.get("size") or {}).get("reason", "")))
    b = record["baseline"]
    lines.append(f"  suite ({b.get('scope', 'whole')}) {b['executed']}/{b['predicted']} · reds "
                 f"branch={len(b['branch_reds'])} new={len(b['new_reds'] or [])}"
                 + (f" base={len(b['base_reds'])} ({b['base']})" if b.get("base") not in (None, "not_needed") else ""))
    d = record["diff"]
    lines += ["", f"{L('diff'):9}{len(d['files'])} files · +{d['added']} −{d['removed']}"
                  + (f" · outside: {', '.join(d['outside_scope'])}" if d["outside_scope"] else "")]
    measured = record.get("measured") or {}
    runs = [r for r in (measured.get("base"), measured.get("suite"), measured.get("lint"),
                        *(measured.get("mutations") or [])) if r]
    if runs:
        lines += ["", f"{L('measured'):9}{', '.join(runs)}"]
    if record["skipped"]:
        lines += ["", L("skipped") + " — " + measured.get("note", "")] + [f"? {s}" for s in record["skipped"]]
    lines += ["", f"{L('next'):9}{record['next']}", ""]
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("prepare")
    a.add_argument("--repo", required=True)
    a.add_argument("--item")
    a.add_argument("--item-file", help=t("review.help_item_file"))
    a.add_argument("--related-file", help=t("deliver.cli.help_related_file"))
    a.add_argument("--kind")
    a.add_argument("--branch")
    a.add_argument("--package")
    a.add_argument("--fix", help="a review package whose author_fix and confirm_finding routes become the item")
    a.add_argument("--findings", help=t("deliver.cli.help_findings"))
    a.add_argument("--continue", dest="continue_branch", action="store_true",
                   help="write the item on --branch, which already holds a delivery; its head is the base")
    a.add_argument("--shape", choices=sorted(SHAPES), help="the item's shape; or --type")
    a.add_argument("--type", help="the tracker's type name, mapped by prumo.yml `tracker.shapes`")
    pl = sub.add_parser("plan")
    pl.add_argument("--package", required=True)
    pl.add_argument("--measure", help="the measure stage's return, for a measured plan")
    pl.add_argument("--design", help="the design stage's return, for a story or an epic")
    ap = sub.add_parser("approve")
    ap.add_argument("--package", required=True)
    ap.add_argument("--by")
    b = sub.add_parser("conclude")
    b.add_argument("--package", required=True)
    b.add_argument("--result", help="the file the workflow's return was saved to — measure, written and refutation are read from it")
    b.add_argument("--measure")
    b.add_argument("--written")
    b.add_argument("--refutation")
    ev = sub.add_parser("evidence", help="after the item is delivered: what was done, shown")
    ev.add_argument("--package", required=True)
    ev.add_argument("--collected", help="the evidence stage's return, when the profile declares how to show a change")
    c = sub.add_parser("release", help=t("review.help_release"))
    c.add_argument("--package", help="the delivery's package: its worktree is removed too; the branch stays")
    args = p.parse_args()
    hint_config(*(getattr(args, a, None) for a in ("package", "item_file", "fix", "review")))
    try:
        if args.command == "prepare":
            if not args.fix and not (args.item and args.item_file):
                p.error("--item and --item-file, or --fix")
            return prepare(args)
        if args.command == "conclude":
            return conclude(args)
        if args.command == "evidence":
            return evidence(args)
        if args.command == "plan":
            if not (args.measure or args.design):
                p.error("--measure or --design")
            return plan(args)
        if args.command == "approve":
            return approve(args)
        package = pathlib.Path(args.package) if args.package else None
        if package:
            release_worktree(package)
        unmark(package)
        print(t("review.marker_removed", path=marker(package) if package else marker(pathlib.Path("x")).parent))
        return 0
    except (MissingConfiguration, InvalidConfiguration) as e:
        print(t("config.impediment", reason=e))
        return 2
    except UsageError as e:
        print(t("review.stop", reason=e))
        if getattr(args, "package", None) and args.command == "prepare":
            unmark(pathlib.Path(args.package))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
