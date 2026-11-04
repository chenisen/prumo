#!/usr/bin/env python3
"""F2 — materialises the package on disk. Agents read references, never pasted content.

Writes `bundle/` with the full diff, the per-reviewer slices, the file list and
`mudanca.json`, which is what triage reads. Nothing here interprets code.

Usage:
    package.py --pacote DIR
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import dir_repositories, load_profile, write_json, read_json
from i18n import t

# Slices: each reviewer reads only its own, so that the whole diff is not paid for in
# every context.
SLICES = {
    "code-and-tests": lambda a: not a.endswith((".md", ".json", ".lock", ".yml", ".yaml")),
    # Secrets and operational configuration live in `.yml`, `.json` and `.env` — slicing
    # those out would blind exactly A9 and the operability question. This slice cuts only
    # what no reviewer judges: prose and lockfiles.
    "code-and-config": lambda a: not a.endswith((".md", ".lock")),
    "contract": lambda a: bool(re.search(
        r"(^|/)(routes?|api|migrations?|schema|resources|events?|contracts?)(/|$)|"
        r"\.(sql|proto|graphql|openapi\.ya?ml)$|(^|/)\.env", a, re.I)),
    "tests": lambda a: bool(re.search(
        r"(^|/)(tests?|spec|__tests__|e2e)/|\.(test|spec)\.[tj]sx?$|Test\.php$", a)),
}


def git(repo_dir: pathlib.Path, *args: str) -> str:
    return subprocess.run(("git", *args), cwd=repo_dir, capture_output=True,
                          text=True).stdout


def build(package: pathlib.Path) -> dict:
    manifest = read_json(package / "manifest.json")
    repo_dir = dir_repositories() / manifest["repo"]
    rev_range = f"{manifest['merge_base']}..{manifest['head_sha']}"

    diff = git(repo_dir, "diff", "--no-color", rev_range)
    (package / "diff.patch").write_text(diff, encoding="utf-8")

    status = {}
    for line in git(repo_dir, "diff", "--name-status", rev_range).splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            status[parts[-1]] = parts[0][:1]
    files = sorted(status)
    removed = [a for a, s in status.items() if s == "D"]

    numstat = git(repo_dir, "diff", "--numstat", rev_range).splitlines()
    lines = sum(int(x) for l in numstat for x in l.split("\t")[:2] if x.isdigit())

    (package / "slices").mkdir(parents=True, exist_ok=True)
    (package / "slices" / "complete.patch").write_text(diff, encoding="utf-8")
    for name, belongs in SLICES.items():
        targets = [a for a in files if belongs(a)]
        body = git(repo_dir, "diff", "--no-color", rev_range, "--", *targets) if targets else ""
        (package / "slices" / f"{name}.patch").write_text(body, encoding="utf-8")

    change = {
        "files": files,
        "removed": removed,
        "added": [a for a, s in status.items() if s == "A"],
        "lines_changed": lines,
        # The diff is NOT embedded here. It used to be, and `change.json` weighed 77KB —
        # the whole patch, byte for byte, beside `diff.patch` holding the same thing. In
        # one measured review the reviewers read this file seven times between them,
        # paying for the patch twice each time. A JSON that carries a file sitting next
        # to it is a file read twice; `diff` is the path.
        "diff": "diff.patch",
        "slices": {n: str(package / "slices" / f"{n}.patch") for n in ("complete", *SLICES)},
        "over_ceiling": [],
    }

    ceiling = (load_profile(manifest["repo"]).get("size") or {}).get("file_ceiling", 1000)
    for a in files:
        target = repo_dir / a
        if target.exists():
            try:
                n = sum(1 for _ in target.open(encoding="utf-8", errors="ignore"))
                if n > ceiling:
                    change["over_ceiling"].append({"file": a, "lines": n})
            except OSError:
                pass

    # `change` carries the patch IN MEMORY, because the triage reads it from here. What
    # reaches disk carries the PATH — this line used to strip `diff` and then put the
    # whole patch straight back, which is how the file came to weigh 77KB beside a
    # `diff.patch` holding the same bytes.
    (package / "diff.patch").write_text(diff, encoding="utf-8")
    write_json(package / "change.json", {**change, "diff": "diff.patch"})
    return change


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--package", "--pacote", dest="pacote", required=True)
    args = p.parse_args()
    m = build(pathlib.Path(args.pacote))
    print(t("package.summary", files=len(m["files"]), lines=m["lines_changed"],
            removed=len(m["removed"]), over_ceiling=len(m["over_ceiling"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
