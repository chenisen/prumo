"""What a change adds that looks like a secret — a scanner the prumo carries, so that the
`secrets` check of a profile has a tool even where none is installed.

It reads the lines a change ADDS (`git diff base...head`), never the lines it removes:
a secret taken out is not a secret let in, and the history is a question for whoever
rotates it. With `--all` it reads every tracked file at `head` instead.

Two kinds of shape:

* a VALUE with a shape of its own — a forge or cloud token, a private key block, a
  password inside a URL. These need no context: the value is the evidence.
* a value ASSIGNED to a name that says secret (`password`, `token`, `api_key`,
  `client_secret`…) that is long and varied enough to be random. A short or plain word
  (`changeme`, `<your key>`, `${VAR}`) is a placeholder, not a secret.

A line that carries `prumo: allow-secret` is skipped, and so is any match listed, one
regular expression per line, in `.prumo-secrets-allow` at the repository root — the way
a test fixture says "this one is fake" in the open, where a reviewer sees it.

What it prints never carries the value: the first four characters and a fingerprint.
Exit 1 when something was found, 0 when nothing was, 2 when it could not read the
change.

Usage:
    secrets_scan.py --repo DIR [--base REF] [--head REF] [--all]
"""
from __future__ import annotations

import argparse
import hashlib
import math
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from providers import SECRET_SHAPES

ALLOW_FILE = ".prumo-secrets-allow"
ALLOW_MARK = "prumo: allow-secret"

# Values with a shape of their own. Labels say what the value is, never whose.
VALUE_SHAPES = tuple(("token with a known prefix", s) for s in SECRET_SHAPES) + (
    ("cloud access key id", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("chat workspace token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    ("live payment key", r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}"),
    ("browser API key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("model provider key", r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{24,}"),
    ("package registry token", r"\bnpm_[A-Za-z0-9]{36}\b"),
    ("signed web token", r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
)

SECRET_NAME = r"(?:pass(?:word|wd)?|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret|auth)"
ASSIGNED = re.compile(
    r"(?i)\b[\w.-]*" + SECRET_NAME + r"[\w.-]*\b\s*[:=]\s*[\"'`]([^\"'`\s]{16,})[\"'`]")
PLACEHOLDER = re.compile(r"(?i)^(?:<.*>|\$\{.*\}|\{\{.*\}\}|x{4,}|\*{4,}|.*(?:example|changeme|placeholder|dummy|your[_-]).*)$")


def entropy(s: str) -> float:
    counts = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values())


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:10]


def find(text: str, allow: list[re.Pattern]) -> list[tuple[str, str]]:
    """(label, value) for every secret-looking value in one line of text."""
    if ALLOW_MARK in text:
        return []
    out = []
    for label, shape in VALUE_SHAPES:
        for m in re.finditer(shape, text):
            out.append((label, m.group(0)))
    for m in ASSIGNED.finditer(text):
        value = m.group(1)
        if PLACEHOLDER.match(value) or entropy(value) < 3.5:
            continue
        if any(value in v or v in value for _, v in out):
            continue
        out.append(("random value assigned to a secret name", value))
    return [(l, v) for l, v in out if not any(a.search(v) for a in allow)]


def git(repo: pathlib.Path, *args: str) -> str:
    r = subprocess.run(("git", *args), cwd=repo, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"git {' '.join(args)} failed")
    return r.stdout


def added_lines(repo: pathlib.Path, base: str, head: str):
    """(file, line number at head, text) for every line the change adds."""
    name, line = None, 0
    for raw in git(repo, "diff", "--unified=0", "--no-color", "--no-ext-diff", f"{base}...{head}").splitlines():
        if raw.startswith("+++ "):
            name = raw[6:] if raw.startswith("+++ b/") else None
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line = int(m.group(1)) if m else 0
        elif raw.startswith("+") and name:
            yield name, line, raw[1:]
            line += 1


def tracked_lines(repo: pathlib.Path, head: str):
    for name in git(repo, "ls-tree", "-r", "--name-only", head).splitlines():
        try:
            text = git(repo, "show", f"{head}:{name}")
        except (RuntimeError, UnicodeDecodeError):
            continue
        for i, l in enumerate(text.splitlines(), 1):
            yield name, i, l


def scan(repo: pathlib.Path, base: str | None, head: str, everything: bool) -> list[str]:
    allow_path = repo / ALLOW_FILE
    allow = [re.compile(l.strip()) for l in allow_path.read_text().splitlines()
             if l.strip() and not l.startswith("#")] if allow_path.exists() else []
    lines = tracked_lines(repo, head) if everything else added_lines(repo, base, head)
    found = []
    for name, n, text in lines:
        for label, value in find(text, allow):
            found.append(f"{name}:{n}: {label} — {value[:4]}… (sha256 {fingerprint(value)})")
    return found


def default_base(repo: pathlib.Path) -> str:
    for ref in ("origin/HEAD", "origin/main", "origin/master", "main", "master"):
        r = subprocess.run(("git", "rev-parse", "--verify", "--quiet", ref), cwd=repo, capture_output=True)
        if r.returncode == 0:
            return ref
    raise RuntimeError("no base to compare against: pass --base")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", default=".")
    p.add_argument("--base")
    p.add_argument("--head", default="HEAD")
    p.add_argument("--all", action="store_true", help="every tracked file at head, not only what the change adds")
    a = p.parse_args()
    repo = pathlib.Path(a.repo)
    try:
        base = None if a.all else (a.base or default_base(repo))
        found = scan(repo, base, a.head, a.all)
    except RuntimeError as e:
        print(f"secrets_scan: {e}")
        return 2
    scope = f"tracked files at {a.head}" if a.all else f"lines added by {base}...{a.head}"
    for f in found:
        print(f)
    print(f"{len(found)} secret(s) found in the {scope}" if found else f"no secret found in the {scope}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
