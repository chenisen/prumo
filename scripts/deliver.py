#!/usr/bin/env python3
"""Deliver one item: measure, write inside the named files, prove, hand to task-review.

Two commands, because the workflow — the surveyor, the writer and the sceptic — sits
between them and a script does not invoke it.

    deliver.py prepare  --repo R --item C --item-file ITEM.md [--kind K] [--branch B]
                        [--package P]
        F0 preflight · F1 resolve the base · F3 baseline on the base, by name ·
        F4 cut the branch (worktree or checkout) and put the marker on.
        Prints the workflow invocation.

    deliver.py conclude --package P --measure M.json --written W.json [--refutation R.json]
        F6 prove: scope guard, mutation by hand, the suite on the branch, baseline by name ·
        F8 judge, a pure function · F9 record, report, the next command. Removes the marker.

    deliver.py release  removes a marker leaked by a crash.

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
                    casa_algum, capabilities_after_preflight, hint_config)
from i18n import t
from shape import SHAPES, resolve as resolve_shape, budget_for, UnknownShape
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
    if base.get("new_reds") is None:
        return stop(BLOCKED, t("deliver.blocked.baseline_not_assessable"), "profile")
    if base["new_reds"]:
        return stop(BLOCKED, t("deliver.blocked.new_red",
                               names=", ".join(base["new_reds"])), "author")

    proof = record["proof"]
    red = proof.get("red_on_base")
    if red and red.get("result") != "red":
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

    diff = record["diff"]
    if diff.get("outside_scope"):
        return stop(BLOCKED, t("deliver.blocked.outside_scope",
                               paths=", ".join(diff["outside_scope"])), "author")
    if not diff.get("within_budget") and not waiver_in_force(record.get("waiver"), reference):
        budget = plan.get("budget") or block.get("budget") or {}
        return stop(BLOCKED, t("deliver.blocked.budget_exceeded",
                               files=len(diff.get("files") or []),
                               lines=diff.get("added", 0) + diff.get("removed", 0),
                               max_files=budget.get("max_files", "?"),
                               max_lines=budget.get("max_lines", "?")), "person")
    return out


# --- the marker ------------------------------------------------------------------------
#
# Same origin as `review.marker()`: `CLAUDE_PROJECT_DIR`, else where `prumo.yml` sits,
# never the shell's directory. The hook reads the same file, and it is JSON on purpose:
# the worktree and the files the writer may touch travel in it, so the hook refuses a
# write outside them without asking the model whether it meant to.

def marker() -> pathlib.Path:
    base = os.environ.get("CLAUDE_PROJECT_DIR") or _installation_root() or os.getcwd()
    return pathlib.Path(base) / ".claude" / ".prumo-deliver-ativo"


def _installation_root() -> str | None:
    try:
        source = cfg().source
    except Exception:
        return None
    return str(source.parent) if source else None


def mark(package: pathlib.Path, worktree: pathlib.Path, allowed: list[str]) -> None:
    target = marker()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"package": str(package), "worktree": str(worktree),
                                  "allowed": allowed, "started_at": today()}) + "\n",
                      encoding="utf-8")


def unmark() -> None:
    marker().unlink(missing_ok=True)


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


def run_suite(profile: dict, cwd: pathlib.Path, package: pathlib.Path, label: str) -> dict:
    """The suite, through the profile's allowlist, output kept beside the package. The
    template is what the allowlist judges; `{workdir}` is filled in after, with `cwd`
    as the executor sees it."""
    from evidence import authorise, resolve, run_command
    check = (profile.get("checks") or {}).get("suite") or {}
    ok, reason = authorise(check.get("command") or "", profile)
    if not ok:
        return {"ran": False, "reason": reason, "output": ""}
    result = run_command(resolve(check["command"], profile, cwd), cwd, limit=None)
    (package / f"run-suite-{label}.txt").write_text(
        result.get("output", result.get("reason", "")), encoding="utf-8")
    return result


def run_lint(profile: dict, cwd: pathlib.Path, package: pathlib.Path) -> dict:
    """The profile's lint check, read like the suite: the output first, the exit code
    second. No command declared is `not_run`, said in words — never green."""
    from evidence import authorise, resolve, run_command
    check = (profile.get("checks") or {}).get("lint") or {}
    if not check.get("command"):
        return {"state": "not_run", "reason": t("evidence.not_in_allowlist"), "run": None}
    ok, reason = authorise(check["command"], profile)
    if not ok:
        return {"state": "not_run", "reason": reason, "run": None}
    r = run_command(resolve(check["command"], profile, cwd), cwd, limit=None)
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
    """Context: the base's version of `files` in the tree, the writer's put back after.
    A file the base does not hold is removed for the duration — a behaviour change
    that adds a file is proven red by the test failing to import it."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        saved = {}
        for f in files:
            path = workdir / f
            saved[f] = path.read_text(encoding="utf-8") if path.is_file() else None
            in_base = bool(git(workdir, "cat-file", "-e", f"{base_sha}:{f}", check=False)
                           or subprocess.run(("git", "cat-file", "-e", f"{base_sha}:{f}"),
                                             cwd=workdir, capture_output=True).returncode == 0)
            if in_base:
                git(workdir, "checkout", base_sha, "--", f)
            elif path.exists():
                path.unlink()
        try:
            yield
        finally:
            for f, text in saved.items():
                path = workdir / f
                if text is None:
                    if path.exists():
                        path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding="utf-8")
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
    shutil.copy2(source, package / "item.md")


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
    title = item_text.strip().splitlines()[0].lstrip("# ").strip() if item_text.strip() else ""
    return block["branch_format"].format(item=item, slug=slug(title, 40))


def fix_item(review: pathlib.Path) -> tuple[str, str, str]:
    """The item a fix delivers: the `author_fix` routes a review opened, each with the
    finding behind it. Returns (item id, origin, item text)."""
    man = read_json(review / "manifest.json")
    verdict = read_json(review / "verdict.json")
    findings = {f["id"]: f for f in (read_json(review / "findings.json")
                                     if (review / "findings.json").exists() else [])}
    routes = [r for r in verdict.get("routes") or [] if r.get("type") == "author_fix"]
    if not routes:
        raise UsageError(t("deliver.cli.fix_no_routes", path=review))
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
    sep = " > "
    if sep not in expected or sep not in name:
        return False
    (f1, t1), (f2, t2) = expected.split(sep, 1), name.split(sep, 1)
    return t1 == t2 and (f1 == f2 or f1.endswith("/" + f2) or f2.endswith("/" + f1))


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
    if block.get("worktree"):
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
    story = pathlib.Path(args.story) if getattr(args, "story", None) else None
    task = None
    story_context = None
    if story:
        # A task of a story is a delivery of its own: its item is the task as the design
        # wrote it, its branch the story's, its base that branch's head, its shape a bug's
        # — reproduce, no gate, because the person approved the whole plan already.
        design = read_json(story / "design.json")
        task = next((x for x in design.get("tasks") or [] if x["id"] == args.task), None)
        if task is None:
            raise UsageError(t("deliver.cli.story_task_missing", task=args.task, path=story))
        story_man = read_json(story / "deliver-manifest.json")
        args.item = f"{story_man['item']['id']}-{task['id']}"
        args.kind = args.kind or "behaviour-change"
        args.shape = "bug"
        args.branch = story_man["branch"]["name"]
        text = [f"# {task['title']}", "", f"Task {task['id']} of {story_man['item']['id']} — {design.get('summary', '')}", ""]
        text += [f"- {c}" for c in task["criteria"]]
        text += ["", f"files: {', '.join(task['files'])}", f"proving tests: {', '.join(task['proving_tests'])}"]
        (package / "item.md").write_text("\n".join(text) + "\n", encoding="utf-8")
        write_json(package / "task.json", {"story": str(story), **task})
        # What the story already delivered on this branch, so neither the writer nor the
        # sceptic undoes it unseen: on the first story (2026-11-12) a write in task 10
        # removed a field before the call task 9 had made read it, and the sceptic, asked
        # only about task 10's own proving tests, said `proves`.
        story_context = []
        for earlier in design.get("tasks") or []:
            if earlier["id"] == task["id"]:
                break
            rec_path = story / "tasks" / earlier["id"] / "delivery.json"
            if rec_path.exists() and read_json(rec_path).get("state") == DELIVERED:
                story_context.append({k: earlier[k] for k in ("id", "title", "criteria", "files")})
    elif fix:
        # A fix delivers the routes a review opened, on the review's own branch: the
        # item is written from the findings, and the base is that branch's head, so the
        # red before the green is measured against what the review read.
        fix_id, fix_origin, text = fix_item(fix)
        args.item = args.item or fix_id
        args.kind = args.kind or "behaviour-change"
        args.shape = getattr(args, "shape", None) or "bug"
        args.branch = args.branch or (fix_origin if fix_origin and not fix_origin.startswith("refs/")
                                      and fix_origin != "HEAD" else None)
        if not args.branch:
            raise UsageError(t("deliver.cli.fix_needs_branch", origin=fix_origin))
        (package / "item.md").write_text(text, encoding="utf-8")
    else:
        read_item(args.item_file, package)

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
    if not rules["deliverable"]:
        # An epic is cut into stories and stops there: no branch, no suite, no marker.
        shutil.copy2(profile_path(args.repo), package / "profile.yml")
        manifest = {"repo": args.repo, "item": {"id": args.item, "kind": "none", "source": "tracker"},
                    "shape": shape, "plan_depth": rules["plan"], "gate": rules["gate"],
                    "budget": budget_for(block, shape), "deliverable": False}
        write_json(package / "deliver-manifest.json", manifest)
        write_json(package / "workflow-args.json", {
            "package": str(package), "repo": args.repo, "item": manifest["item"],
            "shape": shape, "plan_depth": rules["plan"], "delivery": block,
            "conventions": list(profile.get("conventions") or []),
            "agent_prefix": _agent_prefix(),
            "inline": {"item.md": (package / "item.md").read_text(encoding="utf-8")}})
        print("\n" + t("review.package_ready", path=package))
        print("Workflow({name: '" + _workflow_name("deliver") + "', args: <the object in " + str(package / "workflow-args.json") + "> + stage: 'design'})")
        print(f"deliver.py plan --package {package} --design <the design it returned>")
        return 0
    kind = args.kind or (rules["kinds"][0] if rules["kinds"] else "coverage-gap")
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
    name = branch_name(block, args.item, (package / "item.md").read_text(encoding="utf-8"),
                       args.branch)
    if rules.get("base") and not fix:
        hotfix_base = block.get(rules["base"])
        if not hotfix_base:
            raise UsageError(t("deliver.cli.hotfix_base_missing"))
        base_sha = (git(repo_dir, "rev-parse", "--verify", "-q", f"origin/{hotfix_base}", check=False)
                    or git(repo_dir, "rev-parse", "--verify", "-q", hotfix_base, check=False))
        if not base_sha:
            raise UsageError(t("resolver.branch_unresolved", branch=hotfix_base))
        base_name = hotfix_base
    if story:
        # the story's branch exists since the story's own prepare; its head is the base
        base_sha = git(repo_dir, "rev-parse", "--verify", "-q", name, check=False)
        if not base_sha:
            raise UsageError(t("resolver.branch_unresolved", branch=name))
        base_name = name
    if fix:
        base_sha = (git(repo_dir, "rev-parse", "--verify", "-q", name, check=False)
                    or git(repo_dir, "rev-parse", "--verify", "-q", f"origin/{name}", check=False))
        if not base_sha:
            raise UsageError(t("resolver.branch_unresolved", branch=name))
        base_name = name
    if name in cfg().forge.protected_branches:
        raise UsageError(t("deliver.cli.branch_protected", branch=name))
    workdir = cut_branch(repo_dir, block, name, base_sha, fresh=not (fix or story))

    # F3 — the baseline, by name, on the branch while it still IS the base.
    from evidence import compare_reds
    check = (profile.get("checks") or {}).get("suite") or {}
    result = run_suite(profile, workdir, package, "base")
    base_reads = compare_reds(result.get("output", ""), result.get("output", ""), check)
    if not result.get("ran"):
        raise UsageError(t("deliver.blocked.suite_not_run", reason=result.get("reason")))
    if base_reads["predicted"] is not None and base_reads["executed"] is not None \
            and base_reads["executed"] < base_reads["predicted"]:
        raise UsageError(t("deliver.blocked.suite_incomplete",
                           executed=base_reads["executed"], predicted=base_reads["predicted"]))

    manifest = {
        "repo": args.repo, "item": {"id": args.item, "kind": kind, "source": "tracker"},
        "shape": shape, "plan_depth": rules["plan"], "gate": rules["gate"],
        "budget": budget_for(block, shape),
        "base": {"name": base_name, "sha": base_sha},
        "branch": {"name": name, "sha": git(workdir, "rev-parse", "HEAD")},
        "workdir": str(workdir), "in_worktree": bool(block.get("worktree")),
        "previous_head": man["head_sha"],
        "baseline": {"base_reds": base_reads["base_reds"],
                     "predicted": base_reads["predicted"], "executed": base_reads["executed"]},
        "delivery": block,
    }
    write_json(package / "deliver-manifest.json", manifest)

    mark(package, workdir, [])
    write_json(package / "workflow-args.json", {
        "package": str(package), "repo": args.repo, "item": manifest["item"],
        "shape": shape, "plan_depth": rules["plan"],
        "workdir": str(workdir), "delivery": block,
        "task": task,
        "story_context": story_context,
        # The repository's own conventions, as pointers: the writer reads them before
        # the first line, the way the reviewers read them before judging style.
        "conventions": list(profile.get("conventions") or []),
        "agent_prefix": _agent_prefix(),
        "inline": {"item.md": (package / "item.md").read_text(encoding="utf-8"),
                   "profile.yml": (package / "profile.yml").read_text(encoding="utf-8")},
    })
    print("\n" + t("review.package_ready", path=package))
    print(t("deliver.cli.branch_at", branch=name, path=workdir, base=base_sha[:12]))
    print(f"  {t('deliver.report.shape').lower()}: {shape} · {rules['plan']}"
          + (t("deliver.cli.plan_gate") if rules["gate"] else ""))
    print(t("deliver.cli.baseline", reds=len(base_reads["base_reds"] or []),
            executed=base_reads["executed"], predicted=base_reads["predicted"]))
    print("\n" + t("review.invoke_workflow") + "\n")
    print("Workflow({name: '" + _workflow_name("deliver") + "', args: <the object in " + str(package / "workflow-args.json") + "> + stage: 'measure'})")
    print(f"deliver.py plan --package {package} --measure <the measure it returned>")
    print("\n" + t("deliver.cli.then_conclude", path=package))
    return 0


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


def render_plan(man: dict, measure: dict, dropped: list[dict] | None = None) -> str:
    """The plan a person reads before a line is written: what was measured, what will
    be touched, what will prove it. One fact per line, each with an address."""
    L = lambda key: t(f"deliver.report.{key}")  # noqa: E731
    rules = SHAPES[man["shape"]]
    lines = [f"{L('plan'):9}{man['item']['id']} · {man['shape']} · {man['plan_depth']}"
             + (f" · {t('deliver.report.gate_pending')}" if rules["gate"] else ""),
             f"{L('base'):9}{man['base']['name']}@{man['base']['sha'][:7]}   "
             f"{L('branch')} {man['branch']['name']}",
             f"{'':9}budget {man['budget']['max_files']} files · {man['budget']['max_lines']} lines", ""]
    lines.append(L("premises"))
    for p in measure.get("premises") or []:
        lines.append(f"{SYMBOL[p['state']]} {p['text']} — {p.get('evidence') or '?'}"
                     + (" · central" if p.get("central") else ""))
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
    lines.append(f"{'':9}budget {man['budget']['max_files']} files · {man['budget']['max_lines']} lines"
                 + (" · over every task" if design.get("tasks") else ""))
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
        design = read_json(pathlib.Path(args.design))
        jsonschema.validate(design, read_json(ROOT.parent / "schemas" / "design.schema.json"))
        write_json(package / "design.json", design)
        subject, text = design, render_design(man, design)
    else:
        measure, dropped = without_mutations(man["item"]["kind"], read_json(pathlib.Path(args.measure)))
        write_json(package / "measure.json", measure)
        subject, text = measure, render_plan(man, measure, dropped)
    record = {"shape": man["shape"], "depth": man["plan_depth"], "gate": rules["gate"],
              "digest": digest(subject), "rendered_at": today()}
    write_json(package / "plan.json", record)
    (package / "plan.md").write_text(text, encoding="utf-8")
    print((package / "plan.md").read_text(encoding="utf-8"))
    print(t("deliver.cli.plan_at", path=package / "plan.md", shape=man["shape"],
            depth=man["plan_depth"],
            gate=t("deliver.cli.plan_gate") if rules["gate"] else t("deliver.cli.plan_no_gate")))
    return 0


def story_status(args) -> int:
    """The story's record, from its tasks' records: delivered when every task is and
    the sum of their diffs sits inside the story's budget — or a person's waiver in the
    profile covers it; blocked otherwise, naming the next task. Written as
    `delivery.json` so the route and the review read one record per branch.

    The record carries the proof the tasks measured, not a blank. The first story
    (2026-11-12) wrote `baseline` all null and `proof` empty, and the review read "the
    suite never ran at the head" of a branch whose last task had run it on that very
    commit. The suite is the last task's run AT THE STORY'S HEAD, re-read against the
    story's own base, and its file is copied beside the record so `review.py prepare
    --delivery` carries it; the red-on-base runs travel too, one per task."""
    package = pathlib.Path(args.package)
    man = read_json(package / "deliver-manifest.json")
    design = read_json(package / "design.json")
    profile = load_profile(man["repo"])
    block = delivery_block(profile, man["repo"])
    check = (profile.get("checks") or {}).get("suite") or {}
    tasks = design.get("tasks") or []
    records = {}
    for x in tasks:
        path = package / "tasks" / x["id"] / "delivery.json"
        if path.exists():
            records[x["id"]] = read_json(path)
    done = [x["id"] for x in tasks if records.get(x["id"], {}).get("state") == "delivered"]
    pending = [x for x in tasks if x["id"] not in done]
    files = sorted({f for r in records.values() for f in r["diff"]["files"]})
    lines = sum(r["diff"]["added"] + r["diff"]["removed"] for r in records.values())
    budget = man["budget"]
    head = git(dir_repositories() / man["repo"], "rev-parse", "--verify", "-q", man["branch"]["name"], check=False) or man["branch"]["sha"]
    # A fix round on the story's branch (`prepare --fix` after a review) moves the head
    # past the last task. Its record lives in its own package beside the story's; it is
    # the same item on the same branch, so its diff joins the story's, and when it is the
    # one at the head its suite is the head's. Measured on the first story (2026-11-12):
    # without this the story read "no task at the head" right after the fix was delivered.
    repo_dir = dir_repositories() / man["repo"]
    fixes = []
    for rec_path in sorted(package.parent.glob("*/delivery.json")):
        if rec_path.parent == package:
            continue
        rec = read_json(rec_path)
        if (rec.get("state") == DELIVERED
                and str((rec.get("item") or {}).get("id")) == str(man["item"]["id"])
                and (rec.get("branch") or {}).get("name") == man["branch"]["name"]
                and rec["branch"].get("sha")
                and subprocess.run(("git", "merge-base", "--is-ancestor", rec["branch"]["sha"], head),
                                   cwd=repo_dir, capture_output=True).returncode == 0):
            fixes.append((rec_path.parent, rec))
    for _, rec in fixes:
        files = sorted(set(files) | set(rec["diff"]["files"]))
        lines += rec["diff"]["added"] + rec["diff"]["removed"]
    within = len(files) <= budget["max_files"] and lines <= budget["max_lines"]
    waiver = block.get("waiver")

    from evidence import compare_reds, red_names
    base_file = package / "run-suite-base.txt"
    base_out = base_file.read_text(encoding="utf-8") if base_file.exists() else ""
    at_head = next((x["id"] for x in reversed(tasks)
                    if x["id"] in done and records[x["id"]]["branch"]["sha"] == head), None)
    branch_out, lint = "", {"state": "not_run", "reason": t("review.suite_not_run"), "run": None}
    head_fix = None
    if not at_head:
        head_fix = next(((pkg, rec) for pkg, rec in fixes if rec["branch"]["sha"] == head), None)
        if head_fix:
            at_head = head_fix[0].name
    if at_head:
        source = head_fix[0] if head_fix else package / "tasks" / at_head
        run = source / "run-suite-branch.txt"
        if run.exists():
            branch_out = run.read_text(encoding="utf-8")
            shutil.copy2(run, package / "run-suite-branch.txt")
        lint = (head_fix[1] if head_fix else records[at_head]).get("lint") or lint
    if branch_out:
        reads = compare_reds(base_out, branch_out, check)
        baseline = {"base_reds": reads["base_reds"] or [], "branch_reds": reads["branch_reds"] or [],
                    "new_reds": reads["new_reds"], "vanished_reds": reads["vanished_reds"] or [],
                    "predicted": reads["predicted"], "executed": reads["executed"]}
    else:
        baseline = {"base_reds": man["baseline"]["base_reds"] or [], "branch_reds": None,
                    "new_reds": None, "vanished_reds": None, "predicted": None, "executed": None}

    # Red before green, task by task: each task's own base-with-test run travels beside
    # the record under the task's name, and the story's proof is red only when every
    # task that names one was red.
    proving = [p for x in tasks for p in x.get("proving_tests") or []]
    reds_on_base = []
    for x in tasks:
        red = (records.get(x["id"]) or {}).get("proof", {}).get("red_on_base")
        if red:
            reds_on_base.append(red["result"])
            run = package / "tasks" / x["id"] / "run-suite-base-with-test.txt"
            if run.exists():
                shutil.copy2(run, package / f"run-suite-base-with-test-{x['id']}.txt")
    red_on_base = ({"test": ", ".join(proving),
                    "result": "red" if all(r == "red" for r in reds_on_base) and len(reds_on_base) == len(tasks)
                    else ("green" if "green" in reds_on_base else "not_run")}
                   if reds_on_base else None)
    green_on_branch = None
    if branch_out and proving:
        on_branch = red_names(branch_out, check.get("red_name_pattern")) or []
        green_on_branch = {"test": ", ".join(proving),
                           "result": "red" if any(same_test(p, name) for p in proving for name in on_branch) else "green"}

    state, reason, owner = DELIVERED, t("deliver.state.delivered"), None
    if pending:
        nxt = pending[0]["id"]
        state = BLOCKED
        bad = next((x["id"] for x in pending if records.get(x["id"], {}).get("state") in (BLOCKED, UNSUPPORTED)), None)
        reason = (records[bad]["reason"] if bad else
                  t("deliver.cli.story_tasks_pending", done=len(done), total=len(tasks), task=nxt))
        owner = records[bad]["owner"] if bad else "author"
    elif not at_head:
        state, owner = BLOCKED, "author"
        reason = t("deliver.cli.story_no_task_at_head", head=head[:12])
    elif baseline["new_reds"]:
        state, owner = BLOCKED, "author"
        reason = t("deliver.blocked.new_red", names=", ".join(baseline["new_reds"]))
    elif not within and not waiver_in_force(waiver):
        state, owner = BLOCKED, "person"
        reason = t("deliver.cli.story_budget", lines=lines, files=len(files),
                   max_lines=budget["max_lines"], max_files=budget["max_files"])
    measured = {"note": (t("deliver.record.story_measured_note", task=at_head) if at_head
                         else t("deliver.cli.story_no_task_at_head", head=head[:12])),
                "base": "run-suite-base.txt" if base_file.exists() else None,
                "suite": "run-suite-branch.txt" if branch_out else None,
                "lint": None,
                "mutations": []}
    record = {"repo": man["repo"], "item": man["item"], "base": man["base"],
              "branch": {"name": man["branch"]["name"], "sha": head},
              "premises": [], "lint": lint,
              "plan": {"shape": man["shape"], "depth": man["plan_depth"], "gate": SHAPES[man["shape"]]["gate"],
                       "approved_by": approval_of(package), "budget": budget},
              "proof": {"red_on_base": red_on_base, "green_on_branch": green_on_branch, "mutations": [],
                        "guarding": None, "refutation": None},
              "baseline": baseline,
              "diff": {"files": files, "added": lines, "removed": 0, "outside_scope": [],
                       "within_budget": within},
              "skipped": [s for r in list(records.values()) + [rec for _, rec in fixes]
                          for s in r.get("skipped") or []],
              "measured": measured,
              "waiver": waiver, "state": state, "reason": reason, "owner": owner,
              "next": NEXT.format(repo=man["repo"], branch=man["branch"]["name"], item=man["item"]["id"]),
              "tasks": {x["id"]: records.get(x["id"], {}).get("state", "pending") for x in tasks}}
    import jsonschema
    jsonschema.validate(record, read_json(ROOT.parent / "schemas" / "delivery.schema.json"))
    write_json(package / "delivery.json", record)
    print(f"{t('deliver.report.item'):9}{man['item']['id']} · story · {t('deliver.state.' + state)}")
    for x in tasks:
        print(f"  {SYMBOL.get({'delivered': 'holds', 'blocked': 'fails'}.get(record['tasks'][x['id']], 'not_assessable'))} {x['id']}  {x['title']}  {record['tasks'][x['id']]}")
    if branch_out:
        print(f"{'':9}" + t("deliver.cli.story_suite_at_head", task=at_head, executed=baseline["executed"],
                             predicted=baseline["predicted"], reds=len(baseline["base_reds"]),
                             new=len(baseline["new_reds"] or [])))
    if not within and waiver_in_force(waiver):
        print(f"{'':9}" + t("deliver.cli.story_budget_waived", lines=lines, files=len(files),
                             max_lines=budget["max_lines"], max_files=budget["max_files"],
                             approver=waiver.get("approver", "?"), expires=waiver.get("expires", "?")))
    if state != DELIVERED:
        print(f"{'':9}{reason}")
    print(f"{t('deliver.report.next'):9}{record['next']}")
    return 0 if state == DELIVERED else 1


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


def conclude(args) -> int:
    package = pathlib.Path(args.package)
    man = read_json(package / "deliver-manifest.json")
    profile = load_profile(man["repo"])
    block = delivery_block(profile, man["repo"])
    workdir = pathlib.Path(man["workdir"])
    measure, _ = without_mutations(man["item"]["kind"], read_json(pathlib.Path(args.measure)))
    written = read_json(pathlib.Path(args.written)) if args.written else {"files": [], "skipped": []}
    refutation = read_json(pathlib.Path(args.refutation)) if args.refutation else None
    write_json(package / "measure.json", measure)

    premises = measure.get("premises") or []
    allowed = list(measure.get("files") or []) + list(written.get("files") or [])
    kind = man["item"]["kind"]
    generated = list(block.get("generated") or [])
    # Documentation the change makes stale travels with it, where the profile says
    # where documentation lives — except in a coverage gap, which only adds tests.
    roots = (block["test_roots"] if kind == "coverage-gap"
             else block["source_roots"] + block["test_roots"] + list(block.get("doc_roots") or [])
             ) + generated

    files, added, removed = changed_files(workdir, man["base"]["sha"], generated)
    # What was written is what the tree holds, not what the writer remembered. A second
    # attempt on the same tree declared only the files it touched in that pass (three
    # tasks of the first story, 2026-11-12), and the record and the sceptic read a
    # partial delivery. The writer's list is kept, and every file the measurement named
    # that differs from the base joins it; anything else stays outside the scope below.
    declared = list(written.get("files") or [])
    named = set(measure.get("files") or [])
    written = dict(written, files=declared + [f for f in files if f in named and f not in declared])
    write_json(package / "written.json", written)
    budget = man.get("budget") or block.get("budget") or {}
    shape = man.get("shape") or "debt"
    # The plan is rendered here too when nobody did, so the record always holds one;
    # the gate reads the approval against the measurement as it stands.
    if not (package / "plan.json").exists():
        write_json(package / "plan.json", {"shape": shape, "depth": man.get("plan_depth", "measure"),
                                           "gate": SHAPES[shape]["gate"], "digest": digest(measure),
                                           "rendered_at": today()})
        (package / "plan.md").write_text(render_plan(man, measure), encoding="utf-8")
    elif read_json(package / "plan.json").get("digest") != digest(measure):
        write_json(package / "plan.json", dict(read_json(package / "plan.json"), digest=digest(measure)))
    plan_record = {"shape": shape, "depth": man.get("plan_depth", "measure"),
                   "gate": SHAPES[shape]["gate"], "approved_by": approval_of(package),
                   "budget": {"max_files": int(budget.get("max_files", 0)),
                              "max_lines": int(budget.get("max_lines", 0))}}
    diff = {"files": files, "added": added, "removed": removed,
            "outside_scope": scope_guard(files, allowed, roots),
            "within_budget": len([f for f in files if not casa_algum(f, generated)])
                             <= budget.get("max_files", 10 ** 9)
                             and added + removed <= budget.get("max_lines", 10 ** 9)}

    check = (profile.get("checks") or {}).get("suite") or {}
    from evidence import compare_reds, red_names, progress
    central_failed = any(p["central"] and p["state"] == "fails" for p in premises)
    # a refactor with nothing standing guard runs nothing: the stop is the person's
    if kind == "refactor" and not (measure.get("guarding_tests") or []):
        central_failed = True

    mutations, suite_out, suite_ran = [], "", False
    red_on_base = green_on_branch = guarding = None
    lint = {"state": "not_run", "reason": t("review.suite_not_run"), "run": None}
    gate_shut = plan_record["gate"] and not plan_record["approved_by"]
    if not central_failed and files and not gate_shut:
        # The mutation by hand: wrong at the named line, the suite, the named test red,
        # the line put back. Kept whole in the record, revert included.
        # One suite run per DISTINCT mutation: an item that wants three tests red under
        # the same mutant names it three times, and the suite is the expensive part.
        groups: dict[tuple, list[dict]] = {}
        for m in measure.get("mutations") or []:
            groups.setdefault((m["file"], m["line"], m["from"], m["to"]), []).append(m)
        for n, ((file, line, src, dst), wanted) in enumerate(groups.items(), 1):
            why = apply_mutation(workdir, {"file": file, "line": line, "from": src, "to": dst})
            reds, ran, reverted, run_file = [], False, False, None
            if why is None:
                r = run_suite(profile, workdir, package, f"mutation-{n}")
                # A mutation suite that did not finish proves nothing either way: a
                # fatal in the mutated file ends the run before the named test is
                # reached, and "no red" is then not "green".
                executed, predicted = progress(r.get("output", ""), check.get("progress_pattern"))
                finished = predicted is None or (executed is not None and executed >= predicted)
                ran = bool(r.get("ran")) and finished
                run_file = f"run-suite-mutation-{n}.txt" if r.get("ran") else None
                reds = red_names(r.get("output", ""), check.get("red_name_pattern")) or []
                revert(workdir, file)
                reverted = not git(workdir, "diff", "--name-only", "--", file)
            for m in wanted:
                # The whole red set under the mutant, minus the base's, travels too: a
                # mutant that reached eighteen tests the item never named is a fact
                # about the repository — those tests already depended on the guard —
                # and the record used to keep only the one name it was asked about.
                entry = {"file": file, "line": line, "from": src, "to": dst, "test": m["test"],
                         "result": "not_run", "reverted": reverted,
                         "stays_green": list(m.get("stays_green") or []),
                         "stays_green_result": None, "run": run_file,
                         "reds": sorted(set(reds) - set(man["baseline"]["base_reds"] or [])) if ran else []}
                if why is None:
                    entry["result"] = ("red" if any(same_test(m["test"], name) for name in reds)
                                       else "green") if ran else "not_run"
                    if ran and entry["stays_green"]:
                        entry["stays_green_result"] = ("red" if any(
                            same_test(g, name) for g in entry["stays_green"] for name in reds) else "green")
                else:
                    entry["why"] = why
                mutations.append(entry)
        # Red before green: the base's production files under the new test.
        proving = list(measure.get("proving_tests") or [])
        production = production_files(files, block)
        if kind == "behaviour-change" and proving and production:
            with with_base_versions(workdir, man["base"]["sha"], production):
                r = run_suite(profile, workdir, package, "base-with-test")
            on_base = red_names(r.get("output", ""), check.get("red_name_pattern")) or []
            # A test file that cannot even load against the base — it imports what the
            # change adds — is reported by the runner under the file's name, not the
            # test's. That is red on the base too: the module's name stands for its tests.
            test_files = [f for f in files
                          if casa_algum(f, [r_.rstrip("/") + "/**" for r_ in block["test_roots"]])]
            red_on_base = {"test": ", ".join(proving),
                           "result": ("red" if all(any(same_test(p, name) or file_stands_for(name, test_files)
                                                           for name in on_base) for p in proving)
                                      else "green") if r.get("ran") else "not_run"}
        suite = run_suite(profile, workdir, package, "branch")
        suite_out, suite_ran = suite.get("output", ""), bool(suite.get("ran"))
        on_branch = red_names(suite_out, check.get("red_name_pattern")) or [] if suite.get("ran") else []
        if proving and suite.get("ran"):
            green_on_branch = {"test": ", ".join(proving),
                               "result": "red" if any(same_test(p, name) for p in proving for name in on_branch) else "green"}
        guards = list(measure.get("guarding_tests") or [])
        if kind == "refactor":
            guarding = {"tests": guards,
                        "result": ("red" if any(same_test(g, name) for g in guards for name in on_branch)
                                   else "green") if suite.get("ran") else "not_run"}
        lint = run_lint(profile, workdir, package)

    base_out = (package / "run-suite-base.txt").read_text(encoding="utf-8") \
        if (package / "run-suite-base.txt").exists() else ""
    reads = compare_reds(base_out, suite_out, check) if suite_out else {
        "base_reds": man["baseline"]["base_reds"], "branch_reds": None, "new_reds": None,
        "vanished_reds": None, "predicted": None, "executed": None}
    baseline = {"base_reds": reads["base_reds"] or [], "branch_reds": reads["branch_reds"] or [],
                "new_reds": reads["new_reds"], "vanished_reds": reads["vanished_reds"] or [],
                "predicted": reads["predicted"], "executed": reads["executed"]}

    record = {
        "repo": man["repo"], "item": man["item"], "base": man["base"],
        "branch": {"name": man["branch"]["name"], "sha": git(workdir, "rev-parse", "HEAD")},
        "premises": premises,
        "lint": lint,
        "plan": plan_record,
        "proof": {"red_on_base": red_on_base, "green_on_branch": green_on_branch,
                  "guarding": guarding,
                  "mutations": [{k: v for k, v in m.items() if k != "why"} for m in mutations],
                  "refutation": ({"verdict": refutation["verdict"], "reason": refutation["reason"]}
                                 if refutation else None)},
        "baseline": baseline, "diff": diff,
        # The writer's word, verbatim, and written BEFORE the script ran anything. A
        # reviewer once read "mutations were reasoned, not executed" here, beside the
        # mutations the script had executed, and blocked the delivery as contradicting
        # itself. The record now says which is which: `measured` names every run the
        # script made, as a file beside the package, so the note and the proof never
        # compete for the same sentence.
        "skipped": list(written.get("skipped") or []),
        "measured": {"note": t("deliver.record.measured_note"),
                     "base": "run-suite-base.txt" if (package / "run-suite-base.txt").exists() else None,
                     "suite": "run-suite-branch.txt" if suite_ran else None,
                     "lint": lint.get("run"),
                     "mutations": [m["run"] for m in mutations if m.get("run")]},
        "waiver": block.get("waiver"),
    }
    import jsonschema
    jsonschema.validate(record, read_json(ROOT.parent / "schemas" / "delivery.schema.json"))
    record.update(deliver_judge(record, profile))

    # One commit, with a pathspec, in the repository's own convention — and only for a
    # delivery that stands. A blocked tree stays as it is, for a person to read.
    if record["state"] == DELIVERED and files:
        git(workdir, "add", "--", *files)
        # The message is the item's title, verbatim: a slug drops the accents and cuts
        # the sentence, and the person reading the log should find the item by its name.
        title = (package / "item.md").read_text(encoding="utf-8").strip().splitlines()[0].lstrip("# ").strip()
        git(workdir, "commit", "-q", "-m",
            block["commit_format"].format(item=man["item"]["id"], message=title))
        record["branch"]["sha"] = git(workdir, "rev-parse", "HEAD")

    write_json(package / "delivery.json", record)
    report = render(record, mutations)
    (package / "report.md").write_text(report, encoding="utf-8")
    destination = dir_reports() / man["repo"] / f"{man['item']['id']}-deliver"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(package / "report.md", destination / f"deliver-{today()}.md")
    unmark()
    print(report)
    print(t("review.report_at", path=destination / f"deliver-{today()}.md"))
    if man.get("in_worktree"):
        print(t("deliver.cli.release_hint", path=package))

    # What this delivery cost, read from the harness's transcripts — every workflow run
    # over the package, because a delivery is two. A by-product: a failure here never
    # costs the delivery its record, and an unmeasured run is `null`, never zero.
    try:
        from telemetry import measure, summarise
        tel = measure(str(package), None, every_run=True)
        write_json(package / "telemetry.json", tel)
        print("\n" + summarise(tel))
        record_run(package, record, tel)
    except Exception as e:                                   # noqa: BLE001 — see above
        print(t("review.knowledge_failed", error=e))
    return 0 if record["state"] == DELIVERED else 1


def record_run(package: pathlib.Path, record: dict, tel: dict) -> None:
    """One line in `runs.jsonl`, beside the reviews', with the capability named so that
    `metrics.py` never mixes a delivery's cost per line into a review's median."""
    mutations = record["proof"].get("mutations") or []
    line = {
        "date": today(), "capability": "deliver", "repo": record["repo"],
        "item": record["item"]["id"], "kind": record["item"]["kind"],
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
          "not_run": "?"}


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
              f"{L('branch')} {record['branch']['name']}@{record['branch']['sha'][:7]}", ""]
    lines.append(L("premises"))
    for p in record["premises"] or []:
        lines.append(f"{SYMBOL[p['state']]} {p['text']} — {p.get('evidence') or '?'}")
    lines += ["", L("proof")]
    rb = record["proof"].get("red_on_base")
    if rb:
        lines.append(f"{SYMBOL[rb['result']]} red on base: {rb['test']} {rb['result']}")
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
    b = record["baseline"]
    lines.append(f"  suite {b['executed']}/{b['predicted']} · reds base={len(b['base_reds'])} "
                 f"branch={len(b['branch_reds'])} new={len(b['new_reds'] or [])}")
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
    a.add_argument("--kind")
    a.add_argument("--branch")
    a.add_argument("--package")
    a.add_argument("--fix", help="a review package whose author_fix routes become the item")
    a.add_argument("--shape", choices=sorted(SHAPES), help="the item's shape; or --type")
    a.add_argument("--story", help="a story's package: the item is one of its tasks")
    a.add_argument("--task", help="the task id in that story's design")
    a.add_argument("--type", help="the tracker's type name, mapped by prumo.yml `tracker.shapes`")
    pl = sub.add_parser("plan")
    pl.add_argument("--package", required=True)
    pl.add_argument("--measure", help="the measure stage's return, for a measured plan")
    pl.add_argument("--design", help="the design stage's return, for a story or an epic")
    st = sub.add_parser("story")
    st.add_argument("--package", required=True)
    ap = sub.add_parser("approve")
    ap.add_argument("--package", required=True)
    ap.add_argument("--by")
    b = sub.add_parser("conclude")
    b.add_argument("--package", required=True)
    b.add_argument("--measure", required=True)
    b.add_argument("--written")
    b.add_argument("--refutation")
    c = sub.add_parser("release", help=t("review.help_release"))
    c.add_argument("--package", help="the delivery's package: its worktree is removed too; the branch stays")
    args = p.parse_args()
    hint_config(*(getattr(args, a, None) for a in ("package", "item_file", "fix", "story", "review")))
    try:
        if args.command == "prepare":
            if not args.fix and not args.story and not (args.item and args.item_file):
                p.error("--item and --item-file, or --fix, or --story with --task")
            return prepare(args)
        if args.command == "conclude":
            return conclude(args)
        if args.command == "plan":
            if not (args.measure or args.design):
                p.error("--measure or --design")
            return plan(args)
        if args.command == "story":
            return story_status(args)
        if args.command == "approve":
            return approve(args)
        if args.package:
            release_worktree(pathlib.Path(args.package))
        unmark()
        print(t("review.marker_removed", path=marker()))
        return 0
    except (MissingConfiguration, InvalidConfiguration) as e:
        print(t("config.impediment", reason=e))
        return 2
    except UsageError as e:
        print(t("review.stop", reason=e))
        unmark()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
