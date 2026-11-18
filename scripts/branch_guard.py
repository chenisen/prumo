#!/usr/bin/env python3
"""The branch guardrail: nothing of the prumo lands directly on a protected branch.

Called by the hook on every `git` command the model is about to run. It answers one
question — does this command push to, commit on, or rewrite a protected branch of a
repository the prumo works on? — and prints the reason when it does. The hook turns a
printed reason into a block; silence lets the command through.

What is protected is an installation fact: `forge.protected_branches` in `prumo.yml`,
with `homolog`, `main`, `master`, `prod` and `develop` as the default. Which repositories
are the prumo's is `paths.repositories`: a repository outside it — the prumo's own
checkout, a scratch clone — is not the guard's business. Without a configuration there is
no root to scope by, so every repository is guarded with the default list: a guard that
fails open for want of a file is no guard.

Merging a proposal is a person's act too, unless the installation delegated it —
`forge.delegated_merge: true`. `--merge` answers that one: silence and 0 when it was
delegated, the reason and 2 otherwise. A configuration that cannot be read is NOT a
delegation: that answer fails closed.

Usage:
    branch_guard.py --pwd DIR --command "git push origin homolog"
    branch_guard.py --pwd DIR --command "<merge command>" --merge
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from i18n import t

SEGMENTS = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")
WRITES_HISTORY = ("commit", "merge", "rebase", "cherry-pick", "revert", "am")


def settings() -> tuple[tuple[str, ...], pathlib.Path | None]:
    try:
        from common import cfg
        c = cfg()
        return tuple(c.forge.protected_branches), pathlib.Path(os.path.realpath(c.paths.repositories))
    except Exception:
        from providers import DEFAULT_PROTECTED_BRANCHES
        return DEFAULT_PROTECTED_BRANCHES, None


def git_out(cwd: pathlib.Path, *args: str) -> str:
    try:
        r = subprocess.run(("git", "-C", str(cwd), *args), capture_output=True, text=True, timeout=10)
    except Exception:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def short(ref: str) -> str:
    ref = ref.lstrip("+")
    for prefix in ("refs/heads/", "refs/remotes/origin/"):
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return ref


def push_targets(args: list[str], current: str) -> list[str]:
    """The branches a `git push` lands on. A refspec names its destination after the
    colon, or is the destination itself; with no refspec the current branch goes."""
    positional = [a for a in args if not a.startswith("-")]
    # options with a value swallow the next token
    cleaned, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a in ("-o", "--push-option", "--receive-pack", "--exec", "--repo"):
            skip = True
            continue
        if not a.startswith("-"):
            cleaned.append(a)
    positional = cleaned
    refspecs = positional[1:] if positional else []
    deleting = any(a in ("-d", "--delete") for a in args)
    if not refspecs:
        return [current] if current else []
    out = []
    for spec in refspecs:
        spec = spec.lstrip("+")
        if deleting:
            out.append(short(spec))
            continue
        if ":" in spec:
            dst = spec.split(":", 1)[1]
            out.append(short(dst) if dst else "")
        elif spec == "HEAD":
            out.append(current)
        else:
            out.append(short(spec))
    return [b for b in out if b]


def judge_git(tokens: list[str], cwd: pathlib.Path, protected: tuple[str, ...],
              root: pathlib.Path | None) -> str | None:
    # global options before the subcommand: -C <dir>, -c k=v, --git-dir=…
    i, work = 1, cwd
    while i < len(tokens):
        tk = tokens[i]
        if tk == "-C" and i + 1 < len(tokens):
            work = (work / tokens[i + 1]).resolve()
            i += 2
        elif tk == "-c" and i + 1 < len(tokens):
            i += 2
        elif tk.startswith("-"):
            i += 1
        else:
            break
    if i >= len(tokens):
        return None
    sub, args = tokens[i], tokens[i + 1:]
    top = git_out(work, "rev-parse", "--show-toplevel")
    if not top:
        return None
    top_real = pathlib.Path(os.path.realpath(top))
    if root is not None and root not in top_real.parents and top_real != root:
        return None
    current = git_out(work, "symbolic-ref", "--short", "-q", "HEAD")
    repo = top_real.name

    if sub == "push":
        for branch in push_targets(args, current):
            if branch in protected:
                return t("guard.push_protected", branch=branch, repo=repo)
        return None
    if sub in ("checkout", "switch", "branch"):
        # creating or moving a protected branch by force: -B/-C/-f <name>
        for n, a in enumerate(args):
            if a in ("-B", "-C", "-f", "--force") and n + 1 < len(args) and args[n + 1] in protected:
                return t("guard.write_protected", action=f"{sub} {a}", branch=args[n + 1], repo=repo)
        return None
    if current in protected:
        if sub in WRITES_HISTORY:
            return t("guard.write_protected", action=sub, branch=current, repo=repo)
        if sub == "reset" and any(a in ("--hard", "--mixed", "--soft") for a in args):
            return t("guard.write_protected", action="reset", branch=current, repo=repo)
    return None


def judge(command: str, pwd: str) -> str | None:
    protected, root = settings()
    cwd = pathlib.Path(pwd)
    for segment in SEGMENTS.split(command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        # leading environment assignments
        while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
            tokens = tokens[1:]
        if not tokens:
            continue
        if tokens[0] == "cd" and len(tokens) > 1:
            cwd = (cwd / os.path.expanduser(tokens[1])).resolve()
            continue
        if tokens[0] != "git":
            continue
        reason = judge_git(tokens, cwd, protected, root)
        if reason:
            return reason
    return None


def effective_cwd(command: str, pwd: str) -> pathlib.Path:
    """Where the command's git runs: `pwd`, moved by every `cd` the command makes."""
    cwd = pathlib.Path(pwd)
    for segment in SEGMENTS.split(command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        if tokens and tokens[0] == "cd" and len(tokens) > 1:
            cwd = (cwd / os.path.expanduser(tokens[1])).resolve()
    return cwd


def inside_repositories(command: str, pwd: str) -> bool:
    """Whether the command's git runs in a repository under `paths.repositories`. The
    review-time guard blocks commit, push and rewriting ONLY there: a review alters no
    history of the repository it reads, and the prumo's own checkout — where a commit was
    blocked mid-review, six reviews into a day of its own development — is not that
    repository. With no root configured the answer is yes: a guard that cannot tell
    where it is keeps blocking, as it always did."""
    _, root = settings()
    if root is None:
        return True
    cwd = os.path.realpath(effective_cwd(command, pwd))
    return cwd == str(root) or cwd.startswith(str(root).rstrip("/") + "/")


def merge_delegated() -> bool:
    """Whether this installation handed merging to the agent. Unreadable → no."""
    try:
        from common import cfg
        return cfg().forge.delegated_merge is True
    except Exception:
        return False


# --- while deliveries run ---------------------------------------------------------------
#
# One marker per delivery (`deliver.py mark`). A write or a git command is judged by the
# delivery whose worktree it lands in — the most specific one, when a delivery made in
# place holds the checkout and another one a worktree inside it — and by nobody when it
# lands outside every worktree: another agent's task, the workspace, a package. Several
# deliveries in the same repository, each in its own worktree, do not see each other.

MARKER_VALIDITY_SECONDS = 4 * 3600   # a marker leaked by a crash must not wedge anything
HISTORY_REWRITES = ("rebase", "cherry-pick", "revert", "merge")


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def live_deliveries(directory: pathlib.Path) -> list[dict]:
    import time
    out = []
    for f in sorted(directory.glob("*.json")):
        try:
            if time.time() - f.stat().st_mtime >= MARKER_VALIDITY_SECONDS:
                continue
            m = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if m.get("worktree"):
            m["worktree"] = os.path.realpath(m["worktree"])
            m["_marker"] = str(f)
            out.append(m)
    return out


def owner(path: str, deliveries: list[dict]) -> dict | None:
    real = os.path.realpath(path)
    holding = [d for d in deliveries if _inside(real, d["worktree"])]
    return max(holding, key=lambda d: len(d["worktree"])) if holding else None


def judge_write(path: str, deliveries: list[dict]) -> str | None:
    d = owner(path, deliveries)
    if d is None:
        return None
    rel = os.path.relpath(os.path.realpath(path), d["worktree"])
    package = pathlib.Path(d.get("package") or "")
    # A plan whose shape asks a person's approval, and has none that matches it, admits
    # no write: the gate is a file, and the guard reads it.
    try:
        plan = json.loads((package / "plan.json").read_text(encoding="utf-8"))
        if plan.get("gate"):
            try:
                ok = json.loads((package / "approval.json").read_text(encoding="utf-8"))
            except Exception:
                ok = {}
            if ok.get("digest") != plan.get("digest"):
                return t("guard.delivery_write_before_approval", file=rel)
    except Exception:
        pass
    # The files a write may touch are what the measurement named — written to the
    # package between the two stages, so the guard reads them from disk and asks nobody.
    allowed = list(d.get("allowed") or [])
    try:
        allowed += list(json.loads((package / "measure.json").read_text(encoding="utf-8")).get("files") or [])
    except Exception:
        pass
    if not allowed:
        # No measurement on disk yet. A delivery with no gate is measured and written in
        # ONE workflow call, and a story's tasks are written from the approved design:
        # the writer is held to the roots its kind may touch, and `conclude` holds the
        # diff to the scope. A marker that names no roots keeps the old rule.
        roots = [r.rstrip("/") for r in d.get("roots") or []]
        if not roots:
            return t("guard.delivery_write_unmeasured", file=rel)
        if not any(_under_root(rel, r) for r in roots):
            return t("guard.delivery_write_unnamed", file=rel)
        return None
    if rel not in allowed:
        return t("guard.delivery_write_unnamed", file=rel)
    return None


def _under_root(rel: str, root: str) -> bool:
    """`rel` sits under `root` — a directory, a single file, or a glob of either."""
    from common import casa_algum
    return rel == root or rel.startswith(root + "/") or casa_algum(rel, [root, root + "/**"])


def judge_delivery_git(tokens: list[str], cwd: pathlib.Path, deliveries: list[dict]) -> str | None:
    i, work = 1, cwd
    while i < len(tokens):
        tk = tokens[i]
        if tk == "-C" and i + 1 < len(tokens):
            work = (work / tokens[i + 1]).resolve()
            i += 2
        elif tk == "-c" and i + 1 < len(tokens):
            i += 2
        elif tk.startswith("-"):
            i += 1
        else:
            break
    if i >= len(tokens):
        return None
    sub, args = tokens[i], tokens[i + 1:]
    d = owner(str(work), deliveries)
    if sub == "push":
        # a delivery's branch leaves the machine by a person's hand, from wherever
        current = git_out(work, "symbolic-ref", "--short", "-q", "HEAD")
        branches = {x.get("branch") for x in deliveries if x.get("branch")}
        if d is not None or branches & set(push_targets(args, current)):
            return t("guard.delivery_push")
        return None
    if d is None:
        return None
    if sub == "add" and any(a in ("-A", "--all", ".") for a in args):
        return t("guard.delivery_add_all")
    if sub in HISTORY_REWRITES or (sub == "reset" and "--hard" in args):
        return t("guard.delivery_rewrite", action=sub)
    if sub in ("checkout", "switch") and "--" not in args:
        return t("guard.delivery_checkout")
    return None


def held_by(markers: list[pathlib.Path]) -> str:
    """Which marker holds the guard, from which package, how old, and how it is let go.

    A marker leaked by a crash, or left by another session, held a commit on 2026-11-18
    with only "a review does not alter the target repository's history" to go on: nobody
    could tell a live run from a leftover without opening `.claude/` by hand."""
    import time
    parts = []
    for f in markers:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            minutes = int((time.time() - f.stat().st_mtime) // 60)
        except Exception:
            continue
        package = data.get("package") or "?"
        release = ("review.py release" if f.name == ".task-review-ativo"
                   else f"deliver.py release --package {package}")
        parts.append(t("guard.held_by", marker=f, package=package, minutes=minutes, release=release))
    return (" — " + "; ".join(parts)) if parts else ""


def judge_delivery(directory: pathlib.Path, tool: str, path: str, command: str, pwd: str) -> str | None:
    reason = _judge_delivery(directory, tool, path, command, pwd)
    if not reason:
        return None
    live = {pathlib.Path(d["_marker"]) for d in live_deliveries(directory) if d.get("_marker")}
    return reason + held_by(sorted(live))


def _judge_delivery(directory: pathlib.Path, tool: str, path: str, command: str, pwd: str) -> str | None:
    deliveries = live_deliveries(directory)
    if not deliveries:
        return None
    if any(k in tool for k in ("move_card", "move_issue", "move_ticket", "transition",
                               "update_status", "set_status")):
        return t("guard.delivery_move_item")
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit") and path:
        return judge_write(path, deliveries)
    if not command:
        return None
    cwd = pathlib.Path(pwd)
    for segment in SEGMENTS.split(command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
            tokens = tokens[1:]
        if not tokens:
            continue
        if tokens[0] == "cd" and len(tokens) > 1:
            cwd = (cwd / os.path.expanduser(tokens[1])).resolve()
            continue
        if tokens[0] == "git":
            reason = judge_delivery_git(tokens, cwd, deliveries)
            if reason:
                return reason
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pwd", required=True)
    p.add_argument("--command", required=True)
    p.add_argument("--inside-repositories", action="store_true",
                   help="print yes/no: does the command's git run under paths.repositories?")
    p.add_argument("--merge", action="store_true",
                   help="the command merges a proposal: exit 0 only when the installation delegated it")
    p.add_argument("--delivery", metavar="MARKERS_DIR",
                   help="judge the call against the deliveries running now; prints the reason")
    p.add_argument("--held-by", metavar="MARKER",
                   help="print which run a marker belongs to, its age and how it is released")
    p.add_argument("--tool", default="")
    p.add_argument("--path", default="")
    a = p.parse_args()
    if a.held_by:
        print(held_by([pathlib.Path(a.held_by)]))
        return 0
    if a.delivery:
        reason = judge_delivery(pathlib.Path(a.delivery), a.tool, a.path, a.command, a.pwd)
        if reason:
            print(reason)
            return 2
        return 0
    if a.merge:
        if merge_delegated():
            return 0
        print(t("guard.merge_not_delegated"))
        return 2
    if a.inside_repositories:
        print("yes" if inside_repositories(a.command, a.pwd) else "no")
        return 0
    reason = judge(a.command, a.pwd)
    if reason:
        print(reason)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
