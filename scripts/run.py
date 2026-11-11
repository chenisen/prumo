#!/usr/bin/env python3
"""Carry one item from the base to a reviewed branch, with every routing decision read
from disk and every act handed over ready to paste.

    run.py start --repo R --item C --ask-file ASK.md --item-file ITEM.md [--package P]
    run.py bind  --package P (--deliver DIR | --review DIR)
    run.py next  --package P
    run.py log   --package P --line TEXT

`start` refuses to begin without the ask — the person's own words, in a file: a run that
is resumed or compacted reads its authorisation back instead of remembering it. `next`
reads what the delivery and the review packages hold and answers with ONE thing: the
invocation that comes next, or the stop that ends the run. It composes nothing a step
did not already hand over, and it decides nothing the disk does not show.

The route is fixed: deliver (prepare · measure · write · conclude) → task-review
(prepare · workflow · conclude) → stop. What the review decides ends the run. Nothing
here merges, pushes, moves the item, or repairs a finding.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import (MissingConfiguration, InvalidConfiguration, UsageError, dir_packages,
                    read_json, today, write_json)
from i18n import t


class AskMissing(UsageError):
    """No ask, or an empty one. The route does not start on an inference."""


STOP_CLASSES = ("blocked", "unsupported", "approved", "intervention", "ask")


def read_ask(path: str | None) -> str:
    if not path:
        raise AskMissing(t("run.ask_missing"))
    source = pathlib.Path(path)
    if not source.is_file():
        raise AskMissing(t("review.reading_missing", path=path))
    text = source.read_text(encoding="utf-8")
    if not text.strip():
        raise AskMissing(t("run.ask_empty", path=path))
    return text


def start(repo: str, item: str, ask_file: str | None, item_file: str | None = None,
          package: str | None = None) -> pathlib.Path:
    ask = read_ask(ask_file)
    root = pathlib.Path(package or (dir_packages() / f"{repo}-run-{int(time.time())}"))
    root.mkdir(parents=True, exist_ok=True)
    (root / "ask.md").write_text(ask, encoding="utf-8")
    if item_file:
        shutil.copy2(item_file, root / "item.md")
    write_json(root / "run.json", {
        "repo": repo, "item": item, "started_at": today(),
        "ask_digest": hashlib.sha256(ask.encode("utf-8")).hexdigest(),
        "deliver_package": None, "review_package": None,
    })
    log(root, t("run.started", item=item, repo=repo, path=root))
    return root


def log(root: pathlib.Path, line: str) -> None:
    """One line per step. The same line twice in a row is one step asked twice, not two
    steps, and is written once."""
    path = root / "orchestration.md"
    previous = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if previous and previous[-1] == line:
        return
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def bind(root: pathlib.Path, kind: str, target: str) -> None:
    run = read_json(root / "run.json")
    run[f"{kind}_package"] = str(pathlib.Path(target).resolve())
    write_json(root / "run.json", run)
    log(root, t("run.bound", kind=kind, path=target))


def _exists(base: str | None, name: str) -> bool:
    return bool(base) and (pathlib.Path(base) / name).is_file()


def decide(run: dict, root: pathlib.Path) -> dict:
    """The next move, from disk alone. Returns {"kind": "invoke"|"stop", "text", "class"}."""
    repo, item = run["repo"], run["item"]
    d, r = run.get("deliver_package"), run.get("review_package")
    item_file = root / "item.md"

    def invoke(text): return {"kind": "invoke", "text": text, "class": None}
    def stop(klass, fact): return {"kind": "stop", "text": fact, "class": klass}

    # --- the delivery
    if not _exists(d, "deliver-manifest.json"):
        return invoke(f"deliver.py prepare --repo {repo} --item {item} --item-file {item_file}")
    if _exists(d, "delivery.json"):
        rec = read_json(pathlib.Path(d) / "delivery.json")
        if rec.get("state") != "delivered":
            return stop(rec.get("state", "blocked"), rec.get("reason", "?"))
        branch = rec["branch"]["name"]
    elif not _exists(d, "measure.json"):
        return invoke(f"Workflow({{name: 'deliver', args: <{d}/workflow-args.json + stage: 'measure'>}})")
    else:
        measure = read_json(pathlib.Path(d) / "measure.json")
        central_failed = any(p.get("central") and p.get("state") == "fails"
                             for p in measure.get("premises") or [])
        if not central_failed and not _exists(d, "written.json"):
            return invoke(f"Workflow({{name: 'deliver', args: <{d}/workflow-args.json + stage: 'write', measure>}})")
        return invoke(f"deliver.py conclude --package {d} --measure {d}/measure.json"
                      + (f" --written {d}/written.json" if _exists(d, "written.json") else "")
                      + (f" --refutation {d}/refutation.json" if _exists(d, "refutation.json") else ""))

    # --- the review
    if not _exists(r, "manifest.json"):
        return invoke(f"review.py prepare --repo {repo} --branch {branch} --item {item} --item-file {item_file}")
    if _exists(r, "verdict.json"):
        verdict = read_json(pathlib.Path(r) / "verdict.json").get("verdict")
        klass = "approved" if verdict == "APPROVED" else "intervention"
        return stop(klass, str(verdict))
    if not _exists(r, "findings.json"):
        return invoke(f"Workflow({{name: 'task-review', args: <{r}/workflow-args.json>}})")
    return invoke(f"review.py conclude --package {r} --findings {r}/findings.json")


def next_step(root: pathlib.Path) -> dict:
    run = read_json(root / "run.json")
    out = decide(run, root)
    if out["kind"] == "stop":
        log(root, t("run.stop", klass=out["class"], fact=out["text"]))
    else:
        log(root, t("run.next", text=out["text"]))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("start")
    a.add_argument("--repo", required=True)
    a.add_argument("--item", required=True)
    a.add_argument("--ask-file", required=True)
    a.add_argument("--item-file", required=True, help=t("review.help_item_file"))
    a.add_argument("--package")
    b = sub.add_parser("bind")
    b.add_argument("--package", required=True)
    b.add_argument("--deliver")
    b.add_argument("--review")
    c = sub.add_parser("next")
    c.add_argument("--package", required=True)
    d = sub.add_parser("log")
    d.add_argument("--package", required=True)
    d.add_argument("--line", required=True)
    args = p.parse_args()
    try:
        if args.command == "start":
            root = start(args.repo, args.item, args.ask_file, args.item_file, args.package)
            print(t("run.started", item=args.item, repo=args.repo, path=root))
            print(t("run.then", path=root))
            return 0
        root = pathlib.Path(args.package)
        if args.command == "bind":
            if args.deliver:
                bind(root, "deliver", args.deliver)
            if args.review:
                bind(root, "review", args.review)
            print(t("run.then", path=root))
            return 0
        if args.command == "log":
            log(root, args.line)
            return 0
        out = next_step(root)
        if out["kind"] == "stop":
            print(t("run.stop", klass=out["class"], fact=out["text"]))
            return 1
        print(t("run.next", text=out["text"]))
        return 0
    except (MissingConfiguration, InvalidConfiguration) as e:
        print(t("config.impediment", reason=e))
        return 2
    except UsageError as e:
        print(t("review.stop", reason=e))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
