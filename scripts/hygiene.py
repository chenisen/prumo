#!/usr/bin/env python3
"""What the prumo must never carry: a secret, and a rule that is not its own.

Two rules, both mechanical, both over what git actually TRACKS — not over what happens
to sit in the working tree. A file nobody committed harms nobody; the damage of a secret
and the coupling of a foreign rule both begin at the commit.

**No secret.** Not in a configuration file, not in a profile, not in a report, not in a
test. The prumo asks for the NAME of an environment variable and reads the value from
the environment, so a token in a tracked file is never a requirement — it is an
accident, and one that a public repository turns into a disclosure. The fake tokens the
suite needs are declared here, by value: adding a real one to that list is a conscious
act, and anything outside it fails.

**No rule that is not the prumo's own.** The method knows no repository — that is what
`agnosticism_check.py` proves about the TEXT. This proves it about the FILES: an
installation's `prumo.yml`, a repository's profile, a report, a run record and a marker
are facts about whoever installed the tool, and in the tool's own repository they are
somebody else's rules shipped to everybody. The contract is `prumo.yml.example`; the
installation is the user's.

Usage:
    hygiene.py [--verbose]
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import PRUMO_ROOT
from providers import SECRET_SHAPES

# The fake tokens the suite asserts over. They are here, by value, so that the scan can
# stay literal: a pattern loose enough to spare "obviously fake" is a pattern loose
# enough to spare a real one.
FIXTURE_SECRETS = (
    "glpat-ABCdef123456789",
)

# Files that belong to an INSTALLATION, never to the method. Matched against the path as
# git reports it, from the repository root.
FOREIGN_RULES = (
    (r"^prumo\.ya?ml$", "an installation's configuration — the contract is prumo.yml.example"),
    (r"^profiles/", "a repository's profile — it belongs to whoever reviews that repository"),
    (r"^reports/", "a review report — it belongs to the installation that produced it"),
    (r"(^|/)runs\.jsonl$", "a run record — one installation's history"),
    (r"(^|/)\.task-review-ativo$", "run state, not source"),
    (r"(^|/)\.env(\.|$)", "an environment file — it carries values, not names"),
    (r"(^|/)CLAUDE\.md$", "another project's instructions to its harness"),
    (r"(^|/)settings\.local\.json$", "one machine's harness settings"),
)


def tracked_files(root: pathlib.Path) -> list[str]:
    r = subprocess.run(("git", "ls-files", "-z"), cwd=root, capture_output=True, text=True)
    return [f for f in r.stdout.split("\0") if f]


def secrets(root: pathlib.Path, files: list[str]) -> list[str]:
    bad = []
    for name in files:
        path = root / name
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for shape in SECRET_SHAPES:
            for m in re.finditer(shape, text):
                if m.group(0) in FIXTURE_SECRETS:
                    continue
                line = text[:m.start()].count("\n") + 1
                bad.append(f"{name}:{line}: {m.group(0)[:12]}… looks like a secret")
    return bad


def foreign(files: list[str]) -> list[str]:
    bad = []
    for name in files:
        for shape, why in FOREIGN_RULES:
            if re.search(shape, name):
                bad.append(f"{name}: {why}")
                break
    return bad


def check(root: pathlib.Path | None = None) -> list[str]:
    root = root or PRUMO_ROOT
    files = tracked_files(root)
    return secrets(root, files) + foreign(files)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    files = tracked_files(PRUMO_ROOT)
    problems = check()
    if args.verbose:
        print(f"{len(files)} tracked files · {len(SECRET_SHAPES)} secret shapes · "
              f"{len(FOREIGN_RULES)} foreign-rule shapes")
    if problems:
        print("✗ what the prumo must never carry, and does:")
        for b in problems:
            print(f"    {b}")
        return 1
    print("✓ no secret, and no rule that is not the prumo's own")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
