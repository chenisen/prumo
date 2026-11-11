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
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import (MissingConfiguration, InvalidConfiguration, UsageError, cfg,
                    dir_packages, dir_repositories, dir_reports, load_profile,
                    profile_path, read_json, slug, today, waiver_in_force, write_json,
                    casa_algum)
from i18n import t

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

    base = record["baseline"]
    if base.get("executed") is None:
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
        budget = block.get("budget") or {}
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
    """The suite, through the profile's allowlist, output kept beside the package."""
    from evidence import authorise, run_command
    check = (profile.get("checks") or {}).get("suite") or {}
    ok, reason = authorise(check.get("command") or "", profile)
    if not ok:
        return {"ran": False, "reason": reason, "output": ""}
    result = run_command(check["command"], cwd, limit=None)
    (package / f"run-suite-{label}.txt").write_text(
        result.get("output", result.get("reason", "")), encoding="utf-8")
    return result


def run_lint(profile: dict, cwd: pathlib.Path, package: pathlib.Path) -> dict:
    """The profile's lint check, read like the suite: the output first, the exit code
    second. No command declared is `not_run`, said in words — never green."""
    from evidence import authorise, run_command
    check = (profile.get("checks") or {}).get("lint") or {}
    if not check.get("command"):
        return {"state": "not_run", "reason": t("evidence.not_in_allowlist")}
    ok, reason = authorise(check["command"], profile)
    if not ok:
        return {"state": "not_run", "reason": reason}
    r = run_command(check["command"], cwd, limit=None)
    (package / "run-lint.txt").write_text(r.get("output", r.get("reason", "")), encoding="utf-8")
    if not r.get("ran"):
        return {"state": "not_run", "reason": r.get("reason", "")}
    pattern = check.get("failure_pattern")
    failed_in_text = bool(pattern and re.search(pattern, r.get("output") or ""))
    if r["exit_code"] == 0 and not failed_in_text:
        return {"state": "green", "reason": t("evidence.green_output_and_code") if pattern
                else t("evidence.green_exit_code_only")}
    return {"state": "red", "reason": (r.get("output") or "").strip().splitlines()[-1][:200]
            if (r.get("output") or "").strip() else t("evidence.red_no_baseline")}


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
    if given:
        return given
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


def cut_branch(repo_dir: pathlib.Path, block: dict, name: str, base_sha: str,
               fresh: bool = True) -> pathlib.Path:
    """A clean tree is a precondition, not a courtesy: a pending change nobody made here
    is somebody's work in progress. In a worktree the branch lives beside the checkout
    and the checkout is never touched; without one, the branch is checked out in place
    and the report says where HEAD stood."""
    if block.get("worktree"):
        workdir = repo_dir / ".worktrees" / name
        exclude = repo_dir / ".git" / "info" / "exclude"
        if exclude.parent.is_dir():
            text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
            if ".worktrees/" not in text:
                exclude.write_text(text.rstrip("\n") + "\n.worktrees/\n", encoding="utf-8")
        if workdir.exists():
            raise UsageError(t("deliver.cli.worktree_exists", path=workdir))
        if fresh:
            git(repo_dir, "worktree", "add", "-b", name, str(workdir), base_sha)
        else:
            git(repo_dir, "worktree", "add", str(workdir), name)
        return workdir
    if git(repo_dir, "status", "--porcelain"):
        raise UsageError(t("deliver.cli.tree_dirty", path=repo_dir))
    if fresh:
        git(repo_dir, "checkout", "-q", "-B", name, base_sha)
    else:
        git(repo_dir, "checkout", "-q", name)
    return repo_dir


def prepare(args) -> int:
    package = pathlib.Path(args.package or (dir_packages() / f"{args.repo}-deliver-{int(time.time())}"))
    package.mkdir(parents=True, exist_ok=True)
    fix = pathlib.Path(args.fix) if getattr(args, "fix", None) else None
    if fix:
        # A fix delivers the routes a review opened, on the review's own branch: the
        # item is written from the findings, and the base is that branch's head, so the
        # red before the green is measured against what the review read.
        fix_id, fix_origin, text = fix_item(fix)
        args.item = args.item or fix_id
        args.kind = args.kind or "behaviour-change"
        args.branch = args.branch or (fix_origin if fix_origin and not fix_origin.startswith("refs/")
                                      and fix_origin != "HEAD" else None)
        if not args.branch:
            raise UsageError(t("deliver.cli.fix_needs_branch", origin=fix_origin))
        (package / "item.md").write_text(text, encoding="utf-8")
    else:
        read_item(args.item_file, package)

    _py("preflight.py", "--repo", args.repo, "--package", str(package),
        "--origin", "branch", "--item-read", check=False)
    cap = read_json(package / "capabilities.json")
    if cap["impediments"]:
        for impediment in cap["impediments"]:
            print(t("review.impediment", reason=impediment))
        raise UsageError(t("review.impediment_open"))

    profile = load_profile(args.repo)
    block = delivery_block(profile, args.repo)
    shutil.copy2(profile_path(args.repo), package / "profile.yml")

    kind = args.kind or "coverage-gap"
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
    if fix:
        base_sha = (git(repo_dir, "rev-parse", "--verify", "-q", name, check=False)
                    or git(repo_dir, "rev-parse", "--verify", "-q", f"origin/{name}", check=False))
        if not base_sha:
            raise UsageError(t("resolver.branch_unresolved", branch=name))
        base_name = name
    workdir = cut_branch(repo_dir, block, name, base_sha, fresh=not fix)

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
        "workdir": str(workdir), "delivery": block,
        "agent_prefix": _agent_prefix(),
        "inline": {"item.md": (package / "item.md").read_text(encoding="utf-8"),
                   "profile.yml": (package / "profile.yml").read_text(encoding="utf-8")},
    })
    print("\n" + t("review.package_ready", path=package))
    print(t("deliver.cli.branch_at", branch=name, path=workdir, base=base_sha[:12]))
    print(t("deliver.cli.baseline", reds=len(base_reads["base_reds"] or []),
            executed=base_reads["executed"], predicted=base_reads["predicted"]))
    print("\n" + t("review.invoke_workflow") + "\n")
    print("Workflow({name: 'deliver', args: <the object in " + str(package / "workflow-args.json") + ">})")
    print("\n" + t("deliver.cli.then_conclude", path=package))
    return 0


def _agent_prefix() -> str:
    try:
        return cfg().agents.prefix
    except Exception:
        return ""


# --- F6 F8 F9: conclude ------------------------------------------------------------------

def changed_files(workdir: pathlib.Path, base_sha: str) -> tuple[list[str], int, int]:
    """Every file the tree differs from the base in — tracked or new — with the lines."""
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
        added += int(a) if a.isdigit() else 0
        removed += int(r) if r.isdigit() else 0
    return files, added, removed


def scope_guard(files: list[str], allowed: list[str], roots: list[str]) -> list[str]:
    """A file outside what the measurement named, or outside the roots the kind allows,
    is outside the scope. The hook holds this while the writer works; this holds it
    over what actually landed."""
    patterns = [r.rstrip("/") + "/**" for r in roots]
    return [f for f in files if f not in allowed or not casa_algum(f, patterns)]


def conclude(args) -> int:
    package = pathlib.Path(args.package)
    man = read_json(package / "deliver-manifest.json")
    profile = load_profile(man["repo"])
    block = delivery_block(profile, man["repo"])
    workdir = pathlib.Path(man["workdir"])
    measure = read_json(pathlib.Path(args.measure))
    written = read_json(pathlib.Path(args.written)) if args.written else {"files": [], "skipped": []}
    refutation = read_json(pathlib.Path(args.refutation)) if args.refutation else None
    write_json(package / "measure.json", measure)
    write_json(package / "written.json", written)

    premises = measure.get("premises") or []
    allowed = list(measure.get("files") or []) + list(written.get("files") or [])
    kind = man["item"]["kind"]
    roots = block["test_roots"] if kind == "coverage-gap" else block["source_roots"] + block["test_roots"]

    files, added, removed = changed_files(workdir, man["base"]["sha"])
    budget = block.get("budget") or {}
    diff = {"files": files, "added": added, "removed": removed,
            "outside_scope": scope_guard(files, allowed, roots),
            "within_budget": len(files) <= budget.get("max_files", 10 ** 9)
                             and added + removed <= budget.get("max_lines", 10 ** 9)}

    check = (profile.get("checks") or {}).get("suite") or {}
    from evidence import compare_reds, red_names, progress
    central_failed = any(p["central"] and p["state"] == "fails" for p in premises)

    mutations, suite_out = [], ""
    red_on_base = green_on_branch = None
    lint = {"state": "not_run", "reason": t("review.suite_not_run")}
    if not central_failed and files:
        # The mutation by hand: wrong at the named line, the suite, the named test red,
        # the line put back. Kept whole in the record, revert included.
        # One suite run per DISTINCT mutation: an item that wants three tests red under
        # the same mutant names it three times, and the suite is the expensive part.
        groups: dict[tuple, list[dict]] = {}
        for m in measure.get("mutations") or []:
            groups.setdefault((m["file"], m["line"], m["from"], m["to"]), []).append(m)
        for n, ((file, line, src, dst), wanted) in enumerate(groups.items(), 1):
            why = apply_mutation(workdir, {"file": file, "line": line, "from": src, "to": dst})
            reds, ran, reverted = [], False, False
            if why is None:
                r = run_suite(profile, workdir, package, f"mutation-{n}")
                # A mutation suite that did not finish proves nothing either way: a
                # fatal in the mutated file ends the run before the named test is
                # reached, and "no red" is then not "green".
                executed, predicted = progress(r.get("output", ""), check.get("progress_pattern"))
                finished = predicted is None or (executed is not None and executed >= predicted)
                ran = bool(r.get("ran")) and finished
                reds = red_names(r.get("output", ""), check.get("red_name_pattern")) or []
                revert(workdir, file)
                reverted = not git(workdir, "diff", "--name-only", "--", file)
            for m in wanted:
                entry = {"file": file, "line": line, "from": src, "to": dst, "test": m["test"],
                         "result": "not_run", "reverted": reverted,
                         "stays_green": list(m.get("stays_green") or []),
                         "stays_green_result": None}
                if why is None:
                    entry["result"] = ("red" if any(m["test"] in name for name in reds)
                                       else "green") if ran else "not_run"
                    if ran and entry["stays_green"]:
                        entry["stays_green_result"] = ("red" if any(
                            g in name for g in entry["stays_green"] for name in reds) else "green")
                else:
                    entry["why"] = why
                mutations.append(entry)
        # Red before green: the base's production files under the new test.
        proving = list(measure.get("proving_tests") or [])
        production = [f for f in files if casa_algum(f, [r.rstrip("/") + "/**" for r in block["source_roots"]])]
        if kind == "behaviour-change" and proving and production:
            with with_base_versions(workdir, man["base"]["sha"], production):
                r = run_suite(profile, workdir, package, "base-with-test")
            on_base = red_names(r.get("output", ""), check.get("red_name_pattern")) or []
            red_on_base = {"test": ", ".join(proving),
                           "result": ("red" if all(any(p in name for name in on_base) for p in proving)
                                      else "green") if r.get("ran") else "not_run"}
        suite = run_suite(profile, workdir, package, "branch")
        suite_out = suite.get("output", "")
        if proving and suite.get("ran"):
            on_branch = red_names(suite_out, check.get("red_name_pattern")) or []
            green_on_branch = {"test": ", ".join(proving),
                               "result": "red" if any(p in name for p in proving for name in on_branch) else "green"}
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
        "proof": {"red_on_base": red_on_base, "green_on_branch": green_on_branch,
                  "mutations": [{k: v for k, v in m.items() if k != "why"} for m in mutations],
                  "refutation": ({"verdict": refutation["verdict"], "reason": refutation["reason"]}
                                 if refutation else None)},
        "baseline": baseline, "diff": diff,
        "skipped": list(written.get("skipped") or []),
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
    lines = [f"{L('item'):9}{record['item']['id']} · {record['item']['kind']} · "
             f"{t('deliver.state.' + record['state'])}"]
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
    for m in mutations:
        lines.append(f"{SYMBOL[m['result']]} {m['file']}:{m['line']} `{m['from']}`→`{m['to']}` "
                     f"→ {m['test']} {m['result']}" + (" · reverted" if m["reverted"] else "")
                     + (f" · stays green: {', '.join(m['stays_green'])} {m['stays_green_result']}"
                        if m.get("stays_green") else "")
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
    if record["skipped"]:
        lines += ["", L("skipped")] + [f"? {s}" for s in record["skipped"]]
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
    b = sub.add_parser("conclude")
    b.add_argument("--package", required=True)
    b.add_argument("--measure", required=True)
    b.add_argument("--written")
    b.add_argument("--refutation")
    c = sub.add_parser("release", help=t("review.help_release"))
    args = p.parse_args()
    try:
        if args.command == "prepare":
            if not args.fix and not (args.item and args.item_file):
                p.error("--item and --item-file, or --fix")
            return prepare(args)
        if args.command == "conclude":
            return conclude(args)
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
