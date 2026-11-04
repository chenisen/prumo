#!/usr/bin/env python3
"""Writes a `prumo.yml` by asking, and by looking around first.

The prumo refuses to run without a configuration, on purpose: it assumes neither
provider nor path nor language. That refusal is correct and it is also, for whoever has
just installed the plugin, a wall. This is the door — it asks the four questions the
prumo cannot answer for itself, and it *measures* everything else.

What it measures rather than asks:

  * the workspace root, from where you ran it;
  * which directory holds the repositories, by looking for a parent of several git
    checkouts instead of making you name one;
  * the forge, from the `origin` remote of those repositories — a remote at
    `github.com` is not a guess;
  * which repositories have no profile yet.

What it asks, because no amount of looking answers it: the tracker and its URL, the
language, and who signs.

It never overwrites without saying so, and `--dry-run` prints the file instead of
writing it. A tool that silently rewrites the one file where an installation says where
its repositories live is a tool nobody runs twice.

Usage:
    setup.py                          # ask, in the current directory
    setup.py --at ~/work              # ask, writing ~/work/prumo.yml
    setup.py --dry-run                # print, write nothing
    setup.py --tracker jira --forge github --language pt-BR --owner "A B"
                                      # answer everything up front; asks nothing
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from providers import (CONTRACT_VERSION, FILE_NAME, FORGE_CONNECTIONS, FORGES,
                       TRACKERS, InvalidConfiguration, build, looks_like_a_secret)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# A tracker needs a URL to turn an id into a link. The shape differs per provider, and
# guessing it wrong produces a report full of dead links — so each one carries its
# template with the part the user has to replace in capitals.
TRACKER_URL_HINT = {
    "businessmap": "https://YOUR-SPACE.kanbanize.com/ctrl_board/BOARD/cards/{id}/details",
    "jira": "https://YOUR-SPACE.atlassian.net/browse/{id}",
    "acelerato": "https://YOUR-SPACE.acelerato.com/ticket/{id}",
    "generic": "",
}

LANGUAGES_HINT = {"en": "English", "pt-BR": "português do Brasil"}

# What each tracker needs to be REACHABLE, as opposed to merely named. The prumo never
# reaches a tracker itself — the item is read by whoever conducts the review and handed
# over in `--item-file` — so this is about the HARNESS's connection, not the prumo's.
# Naming it here is the difference between "it asked me for a file I do not know how to
# produce" and "I know which connector to add".
TRACKER_CONNECTION = {
    "businessmap": ("an MCP connector for BusinessMap/Kanbanize",
                    "Add it in your Claude settings under Connectors. The review then "
                    "reads the item through it and writes the normalised file itself."),
    "jira": ("an MCP connector for Atlassian/Jira",
             "Add it in your Claude settings under Connectors, or paste the issue into "
             "a file and pass it with `--item-file`."),
    "acelerato": ("no connector is published for Acelerato",
                  "Paste the ticket into a file and pass it with `--item-file`. An empty "
                  "file does not count as read."),
    "generic": ("whatever reaches your tracker",
                "With no connector, paste the item into a file and pass it with "
                "`--item-file`."),
}


# What reaches each forge's API, as opposed to its git. The prumo reviews without any
# of it — the diff comes over git, through the ref the server publishes. What a
# connection buys is the proposal's DESCRIPTION without a file, the pipeline's state,
# and publishing. Named here so that "it asked me for a description I cannot produce"
# becomes "I know which connector to add".
FORGE_CONNECTION_HINT = {
    "gitlab": ("an MCP connector for GitLab, or a token in the environment",
               "The connector reads the proposal and its pipeline; the token also "
               "publishes. Neither is needed to review."),
    "github": ("an MCP connector for GitHub, or a token in the environment",
               "The connector reads the proposal and its checks; the token also "
               "publishes. Neither is needed to review."),
    "bitbucket": ("an app password in the environment",
                  "No connector is published for Bitbucket. Without one, pass the "
                  "description with `--proposal-file` and publish by hand."),
}

# Naming the capability is half the help; the other half is the command that gets it.
# Whoever has just installed the prumo does not know which connector, from where, or
# what it needs to be allowed to do — and "add an MCP connector" sends them to a search
# engine at the exact moment they wanted to review something.
FORGE_CONNECTION_DOC = "reference/forge-connection.md"


def forge_reachable(cli: str, tokens: list[str]) -> tuple[str, str]:
    """Which connection this environment ALREADY has, measured rather than promised.

    Returns the connection to write and the sentence that says why. An MCP connector
    cannot be measured from here — it belongs to the harness, not to the shell — so it
    is the one answer that is asked for instead of measured."""
    for name in tokens:
        if os.environ.get(name):
            return "token", f"`{name}` is set in this environment"
    if cli:
        try:
            r = subprocess.run((cli, "auth", "status"), capture_output=True, text=True,
                               timeout=20,
                               env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        except (OSError, subprocess.SubprocessError):
            r = None
        if r is not None and r.returncode == 0:
            return "token", f"`{cli} auth status` answers: this shell is authenticated"
    return "none", "no token in the environment and no authenticated CLI"


def is_git_checkout(path: pathlib.Path) -> bool:
    return (path / ".git").exists()


def find_repository_root(start: pathlib.Path) -> pathlib.Path | None:
    """The directory holding the most git checkouts, looking only one level down.

    Asking "where are your repositories?" to someone who has just installed a tool is
    asking them to guess what the tool means by a repository. Counting checkouts answers
    it, and when the count is zero we fall back to asking — a wrong guess here sends
    every review at a tree that is not theirs."""
    candidates: list[tuple[int, pathlib.Path]] = []
    for directory in [start, *[d for d in start.iterdir() if d.is_dir()]]:
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        try:
            count = sum(1 for d in directory.iterdir() if d.is_dir() and is_git_checkout(d))
        except PermissionError:
            continue
        if count:
            candidates.append((count, directory))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (-c[0], len(str(c[1]))))
    return candidates[0][1]


def forge_from_remotes(repositories: pathlib.Path) -> tuple[str | None, list[str]]:
    """Read `origin` from each checkout and map the host to a forge.

    A remote at `github.com` is not a guess, and making somebody choose from a list when
    the answer is written in their own git config is friction with no information in it.
    When the checkouts disagree, we say so and ask."""
    hosts: dict[str, int] = {}
    evidence: list[str] = []
    if not repositories.is_dir():
        return None, evidence
    for d in sorted(repositories.iterdir()):
        if not (d.is_dir() and is_git_checkout(d)):
            continue
        r = subprocess.run(("git", "remote", "get-url", "origin"), cwd=d,
                           capture_output=True, text=True)
        url = r.stdout.strip()
        if not url:
            continue
        for forge, needle in (("github", "github.com"), ("gitlab", "gitlab"),
                              ("bitbucket", "bitbucket")):
            if needle in url:
                hosts[forge] = hosts.get(forge, 0) + 1
                evidence.append(f"{d.name}: {url}")
                break
    if not hosts:
        return None, evidence
    if len(hosts) > 1:
        return None, evidence + [f"disagreement: {', '.join(sorted(hosts))}"]
    return next(iter(hosts)), evidence


def git_reachable(repositories: pathlib.Path) -> tuple[bool, str]:
    """Can git actually fetch from `origin`? Measured, not assumed.

    The prumo reads a diff through the ref the server publishes, over git. No token, no
    API. So the one connection it truly needs is the one `git fetch` uses — and finding
    out it is missing on the first review, as a wall of `Permission denied (publickey)`,
    is finding out too late."""
    if not repositories.is_dir():
        return False, "no repositories to try"
    for d in sorted(repositories.iterdir()):
        if not (d.is_dir() and (d / ".git").exists()):
            continue
        r = subprocess.run(("git", "ls-remote", "--exit-code", "origin", "HEAD"),
                           cwd=d, capture_output=True, text=True, timeout=45,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if r.returncode == 0:
            return True, f"`git ls-remote` works in {d.name}"
        return False, (r.stderr.strip().splitlines() or ["git could not reach origin"])[-1]
    return False, "no git checkout to try"


def ask(prompt: str, options: list[str] | None = None, default: str = "") -> str:
    """One question. Empty input takes the default; the default is always shown."""
    if options:
        print(f"\n{prompt}")
        for i, o in enumerate(options, 1):
            mark = "  ←" if o == default else ""
            print(f"  {i}) {o}{mark}")
        raw = input(f"  [{default}] > ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        return raw
    raw = input(f"\n{prompt}\n  [{default}] > ").strip()
    return raw or default


def relative_if_inside(path: pathlib.Path, base: pathlib.Path) -> str:
    """A relative path inside the workspace survives the workspace being moved or
    cloned somewhere else; an absolute one does not."""
    try:
        return "./" + str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path.resolve())


def render(answers: dict) -> str:
    """The file, with the comments. A `prumo.yml` without them is a file nobody can
    edit six months later without reading the source."""
    a = answers
    url_line = (f'  url: "{a["tracker_url"]}"' if a["tracker_url"]
                else "  # url: \"https://YOUR-TRACKER/item/{id}\"   # optional: links the report")
    # The NAME of the variable, never its value. A prumo.yml is versioned, shared and
    # pasted into messages; a token written here is a token published.
    # `none` is the honest default: a render with no answer about the connection must
    # not invent one that publishes.
    connection = a.get("forge_connection") or "none"
    connection_note = ("\n  # tokens: [" + ", ".join(FORGES[a["forge"]]["tokens"])
                       + "]   # the NAME of the variable; never the token itself"
                       if connection == "token" else "")
    owner_line = (f'owner: "{a["owner"]}"' if a["owner"] else
                  '# owner: "Your Name"   # who signs; TASK_REVIEW_RESPONSAVEL overrides it')
    return f"""# prumo — installation configuration
#
# Written by `setup.py` on {a['today']}. Everything here is a fact about THIS
# installation, never about the method: the prumo assumes no provider, no path and no
# language, and refuses to run without this file.
#
# Check what it understood:  python3 <prumo>/scripts/providers.py

prumo: {CONTRACT_VERSION}

# What the prumo answers in: reports, comments, command-line output.
# Catalogues live in `messages/<lang>.yml`.
language: {a['language']}

# Who signs the human acts a profile records — a waiver, a `not_applicable`.
# It lives here and not in the profile because a profile is versioned and shared between
# everyone who reviews that repository; this file belongs to one installation.
# `TASK_REVIEW_RESPONSAVEL` in the environment WINS over this, which is how a team that
# does commit its prumo.yml still gets one name per person.
{owner_line}

paths:
  # The tree holding the repositories to review. `--repo my-service` becomes
  # `<repositories>/my-service`.
  repositories: {a['repositories']}

  # One `.yml` per repository: that project's anchors, checks and conventions. This is
  # what makes the method agnostic — no path or tool lives inside the prumo.
  # A repository with no profile: `derive_profile.py <repo>`.
  profiles: {a['profiles']}

  # Reports, plus `runs.jsonl` with one line per run.
  reports: {a['reports']}

# Where the item that asked for the delivery lives. The prumo never reaches the tracker
# on its own — the item is read by whoever conducts the review and handed over in
# `--item-file`. What is here governs the vocabulary and the comment format.
tracker:
  type: {a['tracker']}
{url_line}

# Where the change proposal lives. The diff is read through the ref the server
# publishes, over git — no API needed to review. A connection is only required to READ
# the proposal's description without a file, to read its pipeline, and to PUBLISH.
forge:
  type: {a['forge']}

  # How the harness reaches this forge's API: mcp | token | none.
  # `none` is a valid answer — the review runs, the description comes from
  # `--proposal-file`, and publishing stays a human act.
  connection: {connection}{connection_note}

# --- Optional, and neither puts a company name inside the prumo -----------------------
#
# agnosticism:
#   forbidden:                     # names of YOUR systems, which must not leak into the
#     - label: "named vendor"      # method. `agnosticism_check.py` fails them.
#       pattern: "(?i)\\\\b(acme|some-erp)\\\\b"
#
# detection:
#   proof_registries:              # your specification framework's marker, if you have
#     - type: my-framework         # one. The public ones are already known.
#       marker: my-framework.json
"""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--at", default=".", help="where to write prumo.yml (default: here)")
    p.add_argument("--dry-run", action="store_true", help="print the file, write nothing")
    p.add_argument("--force", action="store_true", help="overwrite an existing prumo.yml")
    for flag, choices in (("tracker", sorted(TRACKERS)), ("forge", sorted(FORGES))):
        p.add_argument(f"--{flag}", choices=choices, help=f"answer the {flag} up front")
    p.add_argument("--language", help="en | pt-BR")
    p.add_argument("--owner", help="who signs")
    p.add_argument("--tracker-url", help="link template, with {id}")
    p.add_argument("--forge-connection", choices=sorted(FORGE_CONNECTIONS),
                   help="how the harness reaches the forge API: mcp | token | none")
    args = p.parse_args()

    workspace = pathlib.Path(args.at).resolve()
    target = workspace / FILE_NAME
    answered = {args.tracker, args.forge, args.language, args.owner} != {None}
    interactive = sys.stdin.isatty() and not all(
        (args.tracker, args.forge, args.language))

    if target.exists() and not (args.force or args.dry_run):
        print(f"`{target}` already exists.")
        print("  --force overwrites it · --dry-run prints what would be written")
        print("  To translate an older Portuguese one: migrate_config.py prumo.yml --write")
        return 1

    # A mistyped `--at` used to come out as a FileNotFoundError traceback from inside
    # the measuring step — on the FIRST command anybody runs, which is the worst place
    # to look like a crash. We do not create it either: scattering directories at a path
    # somebody typed wrong is how a `prumo.yml` ends up somewhere nobody looks again.
    if not workspace.is_dir():
        print(f"`{workspace}` is not a directory.")
        print("  --at takes the workspace to write `prumo.yml` into; create it first, "
              "or point at an existing one.")
        return 1

    print(f"prumo setup · {workspace}")

    # --- what we can measure
    repositories = find_repository_root(workspace)
    if repositories:
        found = sorted(d.name for d in repositories.iterdir()
                       if d.is_dir() and is_git_checkout(d))
        print(f"\n  repositories: {relative_if_inside(repositories, workspace)} "
              f"— {len(found)} checkouts: {', '.join(found[:6])}"
              + (" …" if len(found) > 6 else ""))
    else:
        print("\n  repositories: none found — I will ask")

    forge, evidence = forge_from_remotes(repositories) if repositories else (None, [])
    if forge:
        print(f"  forge:        {forge}, from the `origin` of those checkouts")
    elif evidence:
        print("  forge:        the checkouts disagree — I will ask")
        for e in evidence[:4]:
            print(f"                  {e}")

    # --- what only a person knows
    if interactive:
        if not repositories:
            repositories = pathlib.Path(ask(
                "Which directory holds the repositories to review?",
                default=str(workspace / "repositories"))).expanduser()
        tracker = args.tracker or ask(
            "Which tracker holds the item that asks for a delivery?",
            sorted(TRACKERS), default="generic")
        tracker_url = args.tracker_url or ask(
            f"Link template for an item (leave empty to skip). `{{id}}` is substituted:",
            default=TRACKER_URL_HINT.get(tracker, ""))
        if "YOUR-SPACE" in tracker_url:
            print("  note: the template still says YOUR-SPACE — edit it before publishing")
        forge = args.forge or forge or ask(
            "Which forge holds the change proposal?", sorted(FORGES), default="github")
        language = args.language or ask(
            "Which language should the prumo answer in?",
            [f"{k}  ({v})" for k, v in LANGUAGES_HINT.items()], default="en").split()[0]
        owner = args.owner if args.owner is not None else ask(
            "Who signs the human acts a profile records? (empty: set "
            "TASK_REVIEW_RESPONSAVEL instead)", default="")
        measured, why = forge_reachable(FORGES[forge]["cli"], FORGES[forge]["tokens"])
        print(f"\n  forge connection: {measured} — {why}")
        forge_connection = args.forge_connection or ask(
            f"How does the harness reach {forge}'s API? (not needed to review)",
            [f"{k}  ({v})" for k, v in FORGE_CONNECTIONS.items()],
            default=measured).split()[0]
    else:
        if not repositories:
            repositories = workspace / "repositories"
        tracker = args.tracker or "generic"
        tracker_url = args.tracker_url or ""
        forge = args.forge or forge or "github"
        language = args.language or "en"
        owner = args.owner or ""
        forge_connection = args.forge_connection or forge_reachable(
            FORGES[forge]["cli"], FORGES[forge]["tokens"])[0]
        if not answered:
            print("\n  not a terminal and nothing was answered — writing the defaults. "
                  "Pass --tracker/--forge/--language to decide them.")

    from common import today
    answers = {
        "today": today(), "language": language, "owner": owner,
        "repositories": relative_if_inside(repositories, workspace),
        "profiles": "./profiles", "reports": "./reports",
        "tracker": tracker, "tracker_url": tracker_url, "forge": forge,
        "forge_connection": forge_connection,
    }
    text = render(answers)

    # Validate BEFORE writing. A file that does not load is worse than no file: the
    # error surfaces on the first review, far from here.
    import yaml
    try:
        build(yaml.safe_load(text), source=target)
    except InvalidConfiguration as e:
        print(f"\n✗ what I was about to write does not validate: {e}")
        print("  This is a bug in setup.py — please report it with the answers above.")
        return 1

    if args.dry_run:
        print("\n" + "-" * 78)
        print(text, end="")
        print("-" * 78)
        print("\nNothing was written. Drop --dry-run to write it.")
        return 0

    target.write_text(text, encoding="utf-8")
    print(f"\n✓ {target}")

    for d in ("profiles", "reports"):
        (workspace / d).mkdir(exist_ok=True)

    # --- what is left, named
    print("\nWhat is left:")
    missing = sorted(d.name for d in repositories.iterdir()
                     if d.is_dir() and is_git_checkout(d)
                     and not (workspace / "profiles" / f"{d.name}.yml").exists()
                     ) if repositories.is_dir() else []
    if missing:
        print(f"  · {len(missing)} repositories have no profile. Derive them:")
        print(f"      python3 {relative_if_inside(REPO_ROOT, workspace)}/scripts/"
              f"derive_profile.py --all")
        print("    Each comes out a `draft` and lists what it could not fill in. "
              "Confirming is a person's act, once per repository.")
    if not owner:
        print("  · nobody signs yet: export TASK_REVIEW_RESPONSAVEL=\"Your Name\", "
              "or put `owner:` in the file")
    # --- the three connections, measured rather than promised
    print("\nConnections:")

    ok, why = git_reachable(repositories)
    if ok:
        print(f"  \u2713 git reaches `origin` \u2014 {why}")
    else:
        print(f"  \u2717 git cannot reach `origin`: {why}")
        print("      This is the one connection a review truly needs: the diff is read")
        print("      through the ref the server publishes, over git. Fix it before the")
        print("      first review \u2014 an SSH key on your account, or an HTTPS helper.")

    needs, how = TRACKER_CONNECTION.get(tracker, TRACKER_CONNECTION["generic"])
    print(f"  \u00b7 tracker: {needs}")
    print(f"      {how}")
    print("      The prumo never reaches the tracker itself \u2014 whoever conducts the")
    print("      review does, and hands the item over in `--item-file`.")

    needs, how = FORGE_CONNECTION_HINT.get(
        forge, ("a token in the environment", "Without one, publishing stays manual."))
    measured, why = forge_reachable(FORGES[forge]["cli"], FORGES[forge]["tokens"])
    mark = "\u2713" if measured != "none" or forge_connection == "mcp" else "\u00b7"
    print(f"  {mark} forge: `connection: {forge_connection}` \u2014 {why}")
    print(f"      {needs}")
    print(f"      {how}")
    if forge_connection == "token":
        print("      The prumo stores the NAME of the variable, never the token. A "
              "secret")
        print("      written into a configuration file is a secret published.")
    print(f"      Step by step, per provider: {relative_if_inside(REPO_ROOT, workspace)}"
          f"/{FORGE_CONNECTION_DOC}")

    print("\nCheck it any time with:  python3 scripts/providers.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
