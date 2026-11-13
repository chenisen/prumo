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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pwd", required=True)
    p.add_argument("--command", required=True)
    p.add_argument("--inside-repositories", action="store_true",
                   help="print yes/no: does the command's git run under paths.repositories?")
    p.add_argument("--merge", action="store_true",
                   help="the command merges a proposal: exit 0 only when the installation delegated it")
    a = p.parse_args()
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
