#!/usr/bin/env python3
"""Carry one item from the base to a reviewed branch, with every routing decision read
from disk and every act handed over ready to paste.

    run.py start --repo R --item C --ask-file ASK.md --item-file ITEM.md [--package P]
    run.py bind  --package P (--deliver DIR | --review DIR)
    run.py next  --package P
    run.py status [--package P] [--json]   where a run stands, read-only — the contract
                                           a runner or an app reads; it logs nothing
    run.py log   --package P --line TEXT

`start` refuses to begin without the ask — the person's own words, in a file: a run that
is resumed or compacted reads its authorisation back instead of remembering it. `next`
reads what the delivery and the review packages hold and answers with ONE thing: the
invocation that comes next, or the stop that ends the run. It composes nothing a step
did not already hand over, and it decides nothing the disk does not show.

The route is fixed: deliver (prepare · ONE workflow call · conclude) → task-review
(prepare · workflow · conclude) → evidence, once, when the review approved → stop. A
shape a person approves waits at its plan, between a measure (or a design) and the
write. What the review decides ends the run. Nothing here merges, pushes, moves the
item, or repairs a finding.
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
                    read_json, today, write_json, hint_config)
from i18n import t


class AskMissing(UsageError):
    """No ask, or an empty one. The route does not start on an inference."""


STOP_CLASSES = ("blocked", "unsupported", "approved", "intervention", "ask", "plan", "decomposed")


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
          package: str | None = None, shape: str | None = None,
          kind: str | None = None) -> pathlib.Path:
    from shape import SHAPES
    kinds = (SHAPES.get(shape) or {}).get("kinds")
    if shape and kind and kinds is not None and kind not in kinds:
        # refused here, where it is typed, not at the prepare the run would repeat
        raise UsageError(t("deliver.cli.kind_not_for_shape", kind=kind, shape=shape,
                           kinds=", ".join(kinds) or "-"))
    ask = read_ask(ask_file)
    root = pathlib.Path(package or (dir_packages() / f"{repo}-run-{int(time.time())}"))
    root.mkdir(parents=True, exist_ok=True)
    (root / "ask.md").write_text(ask, encoding="utf-8")
    if item_file:
        shutil.copy2(item_file, root / "item.md")
    write_json(root / "run.json", {
        "repo": repo, "item": item, "shape": shape, "kind": kind, "started_at": today(),
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


def bind(root: pathlib.Path, kind: str, target: str, task: str | None = None) -> None:
    run = read_json(root / "run.json")
    if task:
        run.setdefault("tasks", {})[task] = str(pathlib.Path(target).resolve())
    else:
        run[f"{kind}_package"] = str(pathlib.Path(target).resolve())
    write_json(root / "run.json", run)
    log(root, t("run.bound", kind=f"{kind} {task}" if task else kind, path=target))


def _wf(name: str) -> str:
    """A plugin's workflow answers by the plugin's name in front — the same prefix its
    agents carry, read by `triage.workflow_name` from the same three sources."""
    from triage import workflow_name
    return workflow_name(name)


def _exists(base: str | None, name: str) -> bool:
    return bool(base) and (pathlib.Path(base) / name).is_file()


def decide(run: dict, root: pathlib.Path) -> dict:
    """The next move, from disk alone. Returns {"kind": "invoke"|"stop", "text", "class"}."""
    repo, item = run["repo"], run["item"]
    d, r = run.get("deliver_package"), run.get("review_package")
    item_file = root / "item.md"

    def invoke(text): return {"kind": "invoke", "text": text, "class": None}
    def stop(klass, fact): return {"kind": "stop", "text": fact, "class": klass}

    # --- a story or an epic: the design first, then the tasks one by one
    if _exists(d, "deliver-manifest.json"):
        man = read_json(pathlib.Path(d) / "deliver-manifest.json")
        if man.get("shape") in ("story", "epic"):
            return decide_decomposed(run, root, d, man, invoke, stop)

    # --- the delivery
    if not _exists(d, "deliver-manifest.json"):
        return invoke(f"deliver.py prepare --repo {repo} --item {item} --item-file {item_file}"
                      + (f" --shape {run['shape']}" if run.get("shape") else "")
                      + (f" --kind {run['kind']}" if run.get("kind") else ""))
    man = read_json(pathlib.Path(d) / "deliver-manifest.json")
    result = f"{d}/workflow-result.json"
    if _exists(d, "delivery.json"):
        rec = read_json(pathlib.Path(d) / "delivery.json")
        if rec.get("state") != "delivered":
            return stop(rec.get("state", "blocked"), rec.get("reason", "?"))
        branch = rec["branch"]["name"]
    elif not man.get("gate"):
        # No gate: measured, sized and written in ONE workflow call, checked once.
        if not _exists(d, "workflow-result.json"):
            return invoke(_stage(d, "deliver") + f" → {t('run.save_result', path=result)}")
        return invoke(f"deliver.py conclude --package {d} --result {result}")
    elif not _exists(d, "measure.json"):
        if not _exists(d, "measure-result.json"):
            return invoke(_stage(d, "measure") + f" → {t('run.save_result', path=f'{d}/measure-result.json')}")
        return invoke(f"deliver.py plan --package {d} --measure {d}/measure-result.json")
    else:
        measure = read_json(pathlib.Path(d) / "measure.json")
        central_failed = any(p.get("central") and p.get("state") == "fails"
                             for p in measure.get("premises") or [])
        # The plan is rendered from the measurement, and the gate waits here for a
        # person's file — the route never writes it.
        if not _exists(d, "plan.json"):
            return invoke(f"deliver.py plan --package {d} --measure {d}/measure.json")
        plan = read_json(pathlib.Path(d) / "plan.json")
        if not central_failed:
            ok = read_json(pathlib.Path(d) / "approval.json") if _exists(d, "approval.json") else {}
            if ok.get("digest") != plan.get("digest"):
                return stop("plan", t("run.plan_stop", path=f"{d}/plan.md",
                                      command=f"/prumo:approve {d}"))
            if not _exists(d, "workflow-result.json"):
                return invoke(_stage(d, "write") + f" → {t('run.save_result', path=result)}")
        return invoke(f"deliver.py conclude --package {d}"
                      + (f" --result {result}" if _exists(d, "workflow-result.json") else ""))

    # --- the review. The door out of an intervention is handed over, never taken: a
    # fix is a new delivery on the person's own ask, and the invocation is what the
    # person pastes.
    if not _exists(r, "manifest.json") or _review_read_another_head(r, rec):
        return invoke(f"review.py prepare --repo {repo} --branch {branch} --item {item} --item-file {item_file} --delivery {d}")
    return _review_step(run, r, repo, stop, invoke)


def _stage(d: str, stage: str) -> str:
    """One stage of the deliver workflow, over the object `prepare` (and `plan`) wrote:
    pasted with the stage's name, nothing composed."""
    return f"Workflow({{name: '{_wf('deliver')}', args: <{d}/workflow-args.json + stage: '{stage}'>}})"


def _review_read_another_head(r: str, rec: dict) -> bool:
    """A fix delivered after the verdict moves the branch: the bound review judged the
    head before it, so the fix asks for a review of its own head, not the old stop."""
    head = read_json(pathlib.Path(r) / "manifest.json").get("head_sha")
    delivered = (rec.get("branch") or {}).get("sha")
    return bool(head and delivered and head != delivered)


def decide_decomposed(run: dict, root: pathlib.Path, d: str, man: dict, invoke, stop) -> dict:
    """A story: design → plan → a person's approval → its tasks written one after another
    in ONE workflow call, each committed by its writer → one conclude over the whole
    branch → the review, once. An epic: design → plan → stop, with the stories for a
    person to open."""
    repo, item = run["repo"], run["item"]
    if not _exists(d, "plan.json"):
        if not _exists(d, "design-result.json"):
            return invoke(_stage(d, "design") + f" → {t('run.save_result', path=f'{d}/design-result.json')}")
        return invoke(f"deliver.py plan --package {d} --design {d}/design-result.json")
    plan = read_json(pathlib.Path(d) / "plan.json")
    if plan.get("gate"):
        ok = read_json(pathlib.Path(d) / "approval.json") if _exists(d, "approval.json") else {}
        if ok.get("digest") != plan.get("digest"):
            return stop("plan", t("run.plan_stop", path=f"{d}/plan.md", command=f"/prumo:approve {d}"))
    if man.get("shape") == "epic" or not man.get("deliverable", True):
        return stop("decomposed", t("deliver.cli.decomposed", shape=man["shape"]) + f" — {d}/plan.md")
    result = f"{d}/workflow-result.json"
    if not _exists(d, "delivery.json"):
        if not _exists(d, "workflow-result.json"):
            return invoke(_stage(d, "tasks") + f" → {t('run.save_result', path=result)}")
        return invoke(f"deliver.py conclude --package {d} --result {result}")
    rec = read_json(pathlib.Path(d) / "delivery.json")
    if rec.get("state") != "delivered":
        return stop(rec.get("state", "blocked"), rec.get("reason", "?"))
    r = run.get("review_package")
    if not _exists(r, "manifest.json") or _review_read_another_head(r, rec):
        return invoke(f"review.py prepare --repo {repo} --branch {rec['branch']['name']} --item {item} --item-file {root / 'item.md'} --delivery {d}")
    return _review_step(run, r, repo, stop, invoke)


def _worktree_standing(d: str | None) -> bool:
    """A delivery cut in a worktree leaves it standing until the review read the branch;
    once the verdict is in, the worktree is released and the branch stays."""
    if not _exists(d, "deliver-manifest.json"):
        return False
    man = read_json(pathlib.Path(d) / "deliver-manifest.json")
    return bool(man.get("in_worktree")) and pathlib.Path(man.get("workdir") or "/nonexistent").exists()


def _tree_standing(d: str | None) -> bool:
    """The tree the delivery was written in is still there to be shown from."""
    man = read_json(pathlib.Path(d) / "deliver-manifest.json") if _exists(d, "deliver-manifest.json") else {}
    return bool(man.get("workdir")) and pathlib.Path(man["workdir"]).exists()


def _review_step(run: dict, r: str, repo: str, stop, invoke) -> dict:
    if _exists(r, "verdict.json"):
        d = run.get("deliver_package")
        v = read_json(pathlib.Path(r) / "verdict.json")
        verdict = v.get("verdict")
        # The evidence, ONCE, after the whole item is delivered and approved, while the
        # worktree still stands: it illustrates what was done and decides nothing.
        if verdict == "APPROVED" and _exists(d, "delivery.json") and not _exists(d, "evidence.json"):
            declared = (read_json(pathlib.Path(d) / "deliver-manifest.json").get("delivery") or {}).get("evidence")
            collected = f"{d}/evidence-result.json"
            if declared and _tree_standing(d) and not _exists(d, "evidence-result.json"):
                return invoke(_stage(d, "evidence") + f" → {t('run.save_result', path=collected)}")
            return invoke(f"deliver.py evidence --package {d}"
                          + (f" --collected {collected}" if _exists(d, "evidence-result.json") else ""))
        if _worktree_standing(d):
            return invoke(f"deliver.py release --package {d}")
        if verdict == "APPROVED":
            return stop("approved", str(verdict))
        fact = str(verdict)
        if any(x.get("type") == "author_fix" for x in v.get("routes") or []):
            fact += f" · {t('run.fix_door', text=f'deliver.py prepare --repo {repo} --fix {r}')}"
        return stop("intervention", fact)
    if _exists(r, "workflow-result.json"):
        return invoke(f"review.py conclude --package {r} --workflow-result {r}/workflow-result.json")
    if not _exists(r, "findings.json"):
        return invoke(f"Workflow({{name: '{_wf('task-review')}', args: <{r}/workflow-args.json>}})"
                      + f" → {t('run.save_result', path=f'{r}/workflow-result.json')}")
    return invoke(f"review.py conclude --package {r} --findings {r}/findings.json"
                  + (f" --criteria {r}/criteria-verdicts.json" if _exists(r, "criteria-verdicts.json") else ""))


STATUS_VERSION = 1


def status(root: pathlib.Path) -> dict:
    """Where one run stands, for another program to read — the public contract between
    the method and anything that conducts or shows it (a runner, an app). Read-only: unlike
    `next`, it appends nothing to orchestration.md, so asking twice changes nothing.

    `kind` is `invoke` (`text` is the step to run) or `stop` (`class` and `text` say why).
    A field added later never changes the meaning of one already here; a change that would
    raises `version`."""
    run = read_json(root / "run.json")
    out = decide(run, root)
    return {"version": STATUS_VERSION, "package": str(root),
            "repo": run.get("repo"), "item": None if run.get("item") is None else str(run["item"]),
            "shape": run.get("shape"), "started_at": run.get("started_at"),
            "deliver_package": run.get("deliver_package"), "review_package": run.get("review_package"),
            "step": {"kind": out["kind"], "class": out.get("class"), "text": out.get("text", "")}}


def status_all(packages: pathlib.Path) -> list[dict]:
    """Every run under the packages directory, each read on its own: one broken run is a
    `blocked` stop with its error, never a reason to lose the others."""
    out = []
    for d in sorted(p for p in packages.iterdir() if (p / "run.json").is_file()) if packages.is_dir() else []:
        try:
            out.append(status(d))
        except Exception as e:                               # noqa: BLE001 — reported, not raised
            out.append({"version": STATUS_VERSION, "package": str(d), "repo": None, "item": None,
                        "shape": None, "started_at": None, "deliver_package": None, "review_package": None,
                        "step": {"kind": "stop", "class": "blocked", "text": f"{type(e).__name__}: {e}"}})
    return out


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
    a.add_argument("--shape", help="the item's shape, as prepare takes it")
    a.add_argument("--kind", help="the item's kind (coverage-gap, behaviour-change, refactor…), as prepare takes it")
    b = sub.add_parser("bind")
    b.add_argument("--package", required=True)
    b.add_argument("--deliver")
    b.add_argument("--review")
    b.add_argument("--task", help="with --deliver: the task id this package delivers")
    c = sub.add_parser("next")
    c.add_argument("--package", required=True)
    s_ = sub.add_parser("status", help="where a run stands, read-only (`--json` for programs)")
    s_.add_argument("--package", help="one run; without it, every run in paths.packages")
    s_.add_argument("--json", action="store_true")
    d = sub.add_parser("log")
    d.add_argument("--package", required=True)
    d.add_argument("--line", required=True)
    args = p.parse_args()
    hint_config(*(getattr(args, a, None) for a in ("package", "item_file", "ask_file", "deliver", "review")))
    try:
        if args.command == "start":
            root = start(args.repo, args.item, args.ask_file, args.item_file, args.package,
                         shape=args.shape, kind=args.kind)
            print(t("run.started", item=args.item, repo=args.repo, path=root))
            print(t("run.then", path=root))
            return 0
        if args.command == "status":
            import json as _json
            if args.package:
                out = status(pathlib.Path(args.package))
            else:
                from common import cfg
                out = status_all(pathlib.Path(cfg().paths.packages))
            if args.json:
                print(_json.dumps(out, ensure_ascii=False))
            else:
                for o in (out if isinstance(out, list) else [out]):
                    st = o["step"]
                    print(f"{o['package']}: " + (t("run.stop", klass=st["class"], fact=st["text"])
                                                 if st["kind"] == "stop" else t("run.next", text=st["text"])))
            return 0
        root = pathlib.Path(args.package)
        if args.command == "bind":
            if args.deliver:
                bind(root, "deliver", args.deliver, task=args.task)
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
