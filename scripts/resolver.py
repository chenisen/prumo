#!/usr/bin/env python3
"""F1 — resolves repository, base, branch, SHAs and item. Nothing is assumed.

The base comes from the profile (`base.branch`), not from a convention written into the
method: `homolog` in some repositories, `main` or `master` in others. With no token for
the proposal, the diff comes out through `refs/merge-requests/<iid>/head`, which the
server publishes over SSH.

Usage:
    resolver.py --repo <repo> [--mr IID | --branch NAME] [--item ID] [--package DIR]
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import MissingConfiguration, InvalidConfiguration, dir_repositories, forge, tracker, UsageError, load_profile, write_json
from i18n import t


def item_of_subject(subject: str, commit_format: str | None) -> str | None:
    """The item a commit subject names, read by the repository's OWN commit convention —
    `delivery.commit_format` in its profile — and by nothing else. The method knows no
    shape for an item's id: every tracker numbers its items as it likes (`1234`,
    `ABC-12`, `#7`), and the pattern that used to live here — four to six digits, after
    the word one tracker uses — was one platform's. A repository that declares no
    convention has no item to be read from a subject: the person names it."""
    if not commit_format or "{item}" not in commit_format:
        return None
    pattern = re.escape(commit_format).replace(r"\{message\}", r".+")
    pattern = pattern.replace(r"\{item\}", r"(?P<item>\S+)", 1).replace(r"\{item\}", r"(?P=item)")
    m = re.fullmatch(pattern, subject.strip())
    return m.group("item") if m else None


def git(repo_dir: pathlib.Path, *args: str, allow_failure: bool = True) -> str:
    r = subprocess.run(("git", *args), cwd=repo_dir, capture_output=True, text=True)
    if r.returncode != 0 and not allow_failure:
        raise UsageError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def fetch_proposal_ref(repo_dir: pathlib.Path, iid: int) -> str | None:
    """The proposal's ref comes from the server; the SHA is recorded. With no token, this
    is the way in.

    The ref's SHAPE belongs to the provider, not to the method: `refs/merge-requests/<n>/head`
    on GitLab, `refs/pull/<n>/head` on GitHub. It comes from `prumo.yml`, which is why no
    provider's name appears in this function. Bitbucket Cloud publishes no such ref — there
    the origin has to be `--branch`, and the preflight says so."""
    ref = forge().proposal_ref(iid)
    r = subprocess.run(("git", "fetch", "-q", "origin", f"+{ref}:refs/prumo/proposta-{iid}"),
                       cwd=repo_dir, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return git(repo_dir, "rev-parse", "--verify", "-q", f"refs/prumo/proposta-{iid}") or None


# Long enough for a large repository over a slow link; short enough that a hung remote
# does not hold the review. Past it the base is measured as it is, and the report says so.
FETCH_TIMEOUT_SECONDS = 120


def refresh_remote_branch(repo_dir: pathlib.Path, name: str) -> tuple[bool | None, str | None]:
    """Brings `origin/<name>` up to the remote before anything is measured against it.

    Until 2026-11-19 nothing here fetched: a base 35 commits behind put the deliveries
    already merged into the package (92 files and 4,955 lines against the 10 files and
    432 lines of the proposal), and a reviewer reading a stale checkout raised a
    `required` finding about a method the head had. The remedy was a fetch by hand before
    every prepare, which is exactly the kind of step that is forgotten.

    Returns `(True, None)` refreshed, `(False, reason)` tried and failed — no network, no
    such branch on the remote — and `(None, None)` when there is no remote to ask. A
    failure never stops the review: it measures what it has and SAYS it is not fresh."""
    if not git(repo_dir, "remote", "get-url", "origin"):
        return None, None
    try:
        r = subprocess.run(("git", "fetch", "-q", "origin",
                            f"+refs/heads/{name}:refs/remotes/origin/{name}"),
                           cwd=repo_dir, capture_output=True, text=True,
                           timeout=FETCH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return False, f"git fetch > {FETCH_TIMEOUT_SECONDS}s"
    if r.returncode != 0:
        return False, ((r.stderr or "").strip().splitlines() or ["git fetch failed"])[-1][:200]
    return True, None


def resolve(repo: str, mr: int | None, branch: str | None, item: str | None,
            base: str | None = None) -> dict:
    repo_dir = dir_repositories() / repo
    if not repo_dir.is_dir():
        raise UsageError(t("resolver.repo_missing", repo=repo, root=dir_repositories()))
    profile = load_profile(repo)

    # The base is RESOLVED, never assumed, in three steps. The first two are remote, and
    # that is the point: measure against what the team actually has.
    #
    # The third is local, and it exists because refusing a repository with no remote made
    # it impossible to review a brand-new project, a clone without `origin`, or a machine
    # with no network — and the message only said it did not resolve, offering no remedy.
    # But a local base is WEAKER proof: it may be behind the remote with nothing here
    # noticing. So that step is taken with a `source` of its own, which the report prints
    # — by the same principle as the guardrail's three states: what is worth less does not
    # get to dress up as what is worth more.
    base_name = (profile.get("base") or {}).get("branch") or "main"
    base_ref = f"origin/{base_name}"
    base_local = False
    fetched, fetch_reason = (None, None) if base else refresh_remote_branch(repo_dir, base_name)
    # A base a person names for THIS review — a ref or a commit — when the branch has more
    # than one common ancestor with the profile's: a branch cut from another line and
    # merged with the work it needs measures against what it was built on. Twice on
    # 2026-11-17/18 that took a temporary ref and the profile's `base.branch` edited for
    # the length of a prepare, then restored by hand.
    if base:
        if not git(repo_dir, "rev-parse", "--verify", "-q", f"{base}^{{commit}}"):
            raise UsageError(t("resolver.base_unresolved", base=base, repo=repo))
        base_ref, base_name = base, base
    elif not git(repo_dir, "rev-parse", "--verify", "-q", base_ref):
        alternative = git(repo_dir, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        if alternative:
            base_ref, base_name = alternative, alternative.split("/", 1)[-1]
        elif git(repo_dir, "rev-parse", "--verify", "-q", base_name):
            base_ref, base_local = base_name, True
        else:
            raise UsageError(t("resolver.base_unresolved", base=base_name, repo=repo))

    if mr:
        head = fetch_proposal_ref(repo_dir, mr)
        origin, origin_kind = forge().proposal_ref(mr), "proposal"
        if not head:
            raise UsageError(t("resolver.proposal_fetch_failed", ref=origin,
                               proposal=forge().proposal,
                               proposal_with_article=forge().with_article()))
    elif branch:
        # A branch that lives only here (a delivery not pushed yet) fails the fetch and is
        # read locally below; that failure says nothing about the review, so it is not kept.
        refresh_remote_branch(repo_dir, branch)
        # `--verify -q` is not fussiness: without it, `rev-parse` ECHOES its argument when
        # the ref does not exist, and a branch name walks on pretending to be a SHA. The
        # manifest comes out with `head_sha: "origin/my-branch"`, the diff comes back
        # empty, and the review reports "nothing to review" about a delivery that exists.
        head = (git(repo_dir, "rev-parse", "--verify", "-q", f"origin/{branch}")
                or git(repo_dir, "rev-parse", "--verify", "-q", branch))
        origin, origin_kind = branch, "branch"
        if not head:
            raise UsageError(t("resolver.branch_unresolved", branch=branch))
    else:
        head, origin = git(repo_dir, "rev-parse", "--verify", "-q", "HEAD"), "HEAD"
        origin_kind = "head"

    base_sha = git(repo_dir, "rev-parse", "--verify", "-q", base_ref)
    merge_base = git(repo_dir, "merge-base", base_sha, head) or base_sha
    behind = git(repo_dir, "rev-list", "--count", f"{merge_base}..{base_sha}")

    # The item: from the argument, or from the commits' subjects read by the repository's
    # own commit convention. Never invented, and never guessed from the look of a number.
    subjects = git(repo_dir, "log", "--format=%s", f"{merge_base}..{head}").splitlines()
    commit_format = (profile.get("delivery") or {}).get("commit_format")
    derived = next((found for found in (item_of_subject(s, commit_format) for s in subjects) if found), None)

    source = (t("resolver.base_given") if base
              else (t("resolver.base_local") if base_local
                    else t("resolver.base_from_profile"))
              if base_name == (profile.get("base") or {}).get("branch")
              else t("resolver.base_from_head"))
    if fetched is False and not base_local:
        source = t("resolver.base_not_refreshed", source=source, reason=fetch_reason)
    return {
        "repo": repo,
        "origin": origin,
        "origin_kind": origin_kind,
        "base": {"name": base_name, "ref": base_ref, "sha": base_sha,
                 "local": base_local, "fetched": fetched,
                 "source": source},
        "head_sha": head,
        "merge_base": merge_base,
        "commits_behind_base": int(behind or 0),
        "behind_base": int(behind or 0) > 0,
        **conflicts_with_base(repo_dir, base_sha, head),
        "item": {"id": item or derived,
                 "source": t("resolver.source_argument") if item else
                           (t("resolver.source_commit_subject") if derived else None),
                 "confirmed": bool(item)},
        "commits": subjects,
        "perfil_status": profile.get("status", "absent"),
    }


def conflicts_with_base(repo_dir: pathlib.Path, base_sha: str, head: str) -> dict:
    """Would merging this head into the base conflict?

    Being BEHIND the base and CONFLICTING with it are different facts, and conflating
    them made every slightly stale proposal look like a defect. A branch two commits
    behind that merges cleanly has been measured against code identical to what it will
    meet; one that conflicts has not, and nobody can say what the review proved.

    `git merge-tree --write-tree` answers it without a provider, without a checkout and
    without touching the working tree or any ref: it writes objects to the odb and exits
    non-zero when the merge does not resolve, naming the files. That matters because no
    script here reaches the forge's API — the same reason the item arrives as a file.

    Older git has no `--write-tree`. There the honest answer is `None`: not measured, and
    the judge is told so rather than handed a `False` it would read as "merges cleanly".
    """
    r = subprocess.run(
        ("git", "merge-tree", "--write-tree", "--name-only", base_sha, head),
        cwd=repo_dir, capture_output=True, text=True)
    if r.returncode not in (0, 1):
        return {"conflicts": None, "conflicted_files": [],
                "conflicts_reason": (r.stderr or "").strip()[:200]
                                    or "git merge-tree unavailable"}
    # On a conflict the first line is the tree oid and the rest are the paths.
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    return {"conflicts": r.returncode == 1,
            "conflicted_files": lines[1:] if r.returncode == 1 else [],
            "conflicts_reason": None}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--proposal", "--mr", dest="mr", type=int)
    p.add_argument("--branch")
    p.add_argument("--item")
    p.add_argument("--base")
    p.add_argument("--package")
    args = p.parse_args()

    try:
        r = resolve(args.repo, args.mr, args.branch, args.item, args.base)
    except (MissingConfiguration, InvalidConfiguration) as e:
        print(t("config.impediment", reason=e))
        return 2
    except UsageError as e:
        print(t("review.stop", reason=e))
        return 1

    if args.package:
        write_json(pathlib.Path(args.package) / "manifest.json", r)
    print(t("resolver.summary", repo=r["repo"], origin=r["origin"],
            base=r["base"]["ref"], source=r["base"]["source"]))
    print(f"  {t('resolver.head'):8} {r['head_sha'][:12]}")
    print(f"  {t('resolver.base'):8} {r['base']['sha'][:12]}  "
          f"{t('resolver.commits_ahead', n=r['commits_behind_base'])}")
    print(f"  {t('resolver.item_via', item=tracker().item, id=r['item']['id'], source=r['item']['source'], item_with_article=tracker().with_article())}"
          f"{'' if r['item']['confirmed'] else '  ' + t('resolver.item_derived')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
