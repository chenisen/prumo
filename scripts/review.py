#!/usr/bin/env python3
"""Review orchestrator. Two commands, because the workflow sits between them.

    review.py prepare --repo R [--proposal N | --branch B] --item C
                      --item-file ITEM.md [--proposal-file PROPOSAL.md] [--package P]
        F0 preflight · F1 resolve · F2 package · F3 triage.
        Leaves the package ready and PRINTS the workflow invocation.

    review.py conclude --package P [--findings F] [--scripts-manual S]
        F7 judge · F9 render · F11 record.

Splitting this in two is not cosmetic: a script does not invoke the Workflow — the model
does, from inside the skill. And what sits in the middle are the agents, which are the
part that judges.

Advisory mode (`--advisory`): runs everything, **issues no verdict** and writes no report
under the reports root. It is what the author runs before opening the proposal.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import (MissingConfiguration, InvalidConfiguration, dir_reports, forge, tracker, UsageError, profile_path, load_profile, write_json,
                   today, read_json, slug)
from i18n import t

ROOT = pathlib.Path(__file__).resolve().parent
DEFAULT_PACKAGE_ROOT = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / "task-review"


def _vocabulary() -> dict:
    """Both forms of every provider word, for any message that names one.

    English takes the bare noun, Portuguese the one with its article, and the catalogue
    — not the call site — decides which. Passing only the form this language happens to
    need is how a translated sentence ends up printing literal braces.
    """
    return {"item": tracker().item, "item_with_article": tracker().with_article(),
            "items": tracker().item_plural,
            "proposal": forge().proposal, "proposal_with_article": forge().with_article()}


# The guard hook reads this file to know a review is under way. A marker on disk, not an
# environment variable, because the hook runs in another process.
#
# The path is the REVIEWED PROJECT's, never derived from this script's position. It used
# to be `ROOT.parent.parent.parent`, correct while the method lived under
# `.claude/skills/task-review/scripts`, and it became the filesystem root once the method
# became a repository of its own — the marker went to `/.task-review-ativo`, the hook read
# somewhere else, and the push and commit guard simply never fired. Silent: nothing
# failed, the protection just stopped existing.
#
# This function and `MARCADOR` in `hooks/review-guard.sh` have to resolve to the SAME
# file. There is a test holding both ends.
def marker() -> pathlib.Path:
    base = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return pathlib.Path(base) / ".claude" / ".task-review-ativo"


def mark(package: pathlib.Path) -> None:
    # `.claude/` may not exist in a project that has never seen the harness, and the
    # review cannot die of that — nor, worse, carry on with no marker and the guard off.
    target = marker()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"package": str(package), "started_at": today()}) + "\n",
                      encoding="utf-8")


def unmark() -> None:
    marker().unlink(missing_ok=True)


def _py(script: str, *args: str, check: bool = True) -> None:
    """Runs one step of the spine. A failed step is a stop, not a warning.

    `check=False` only for the preflight, whose exit code 1 means "there is an impediment"
    and is read by the caller from `capabilities.json` — there the good message is the
    impediment's, not the exit code's.
    """
    r = subprocess.run((sys.executable, str(ROOT / script), *args), text=True)
    if check and r.returncode != 0:
        raise UsageError(t("review.step_failed", path=script, n=r.returncode))


def copy_mandatory_reading(args, package: pathlib.Path) -> dict:
    """Puts the item and the proposal's description inside the package, and returns what
    actually landed there.

    A file that does not exist, or that exists empty, does NOT count as read: the
    difference between "there is no criterion" and "I could not read it" is the whole
    point, and a blank file erases that difference. The `mr` origin is what makes the
    description demandable.
    """
    origin = "mr" if args.proposal else ("branch" if args.branch else "diff")
    read_files = {"origin": origin, "item": False, "proposal_description": False}

    for key, path, destination in (("item", args.item_file, "item.md"),
                                   ("proposal_description", args.proposal_file,
                                    "proposal.md")):
        if not path:
            continue
        source = pathlib.Path(path)
        if not source.is_file():
            raise UsageError(t("review.reading_missing", path=path))
        if not source.read_text(encoding="utf-8").strip():
            raise UsageError(t("review.reading_empty", path=path))
        shutil.copy2(source, package / destination)
        read_files[key] = True

    # A token in the environment does NOT satisfy this gate, and the line that used to
    # say it did was two bugs in three lines.
    #
    # It named `GITLAB_TOKEN` and `GLAB_TOKEN` as literals — the method knowing one
    # provider by name, inside the gate, while `forge.tokens` exists for exactly this.
    # On a GitHub installation the same environment did nothing, and the gate stayed shut
    # with the credential sitting right there.
    #
    # The deeper one: it flipped the flag with no file and no fetch. Nothing in this
    # repository reads a forge API, so `proposal.md` was never written, the preflight
    # passed, and the review ran with the proposal's story missing from the one judgement
    # that compares three stories. A gate that opens on the PRESENCE OF A VARIABLE
    # instead of the arrival of the text produces the result this method calls its worst:
    # a report shaped like a complete one and partial in content.
    #
    # What a connection buys is the HARNESS's ability to fetch that text. However it
    # fetches it — a connector, a token, a CLI, a paste — it then hands it over here, as
    # a file. See `reference/forge-connection.md`.
    return read_files


def short_range(package: pathlib.Path) -> str:
    m = read_json(package / "manifest.json")
    return f"{m['merge_base'][:12]}..{m['head_sha'][:12]}"


def item_scope(package: pathlib.Path) -> dict:
    """A touched file that no criterion asks for is grown scope.

    Without `criteria.json` the out-of-scope list is empty — and that is said, not assumed.
    """
    path = package / "criteria.json"
    if not path.exists():
        return {"out_of_scope_touched": [], "source": t("review.scope_no_criteria")}
    data = read_json(path)
    out_of_scope = (data.get("out_of_scope") or []) if isinstance(data, dict) else []
    files = read_json(package / "change.json")["files"]
    from common import casa_algum
    return {"out_of_scope_touched": [f for f in files if casa_algum(f, out_of_scope)],
            "source": "criteria.json"}


def initial_evidence(package: pathlib.Path) -> dict:
    """What the gate needs and does not have yet. Phase F6 may fill it; without F6 it
    stays like this."""
    man = read_json(package / "manifest.json")
    cap = read_json(package / "capabilities.json")
    suite = cap["checks"].get("suite") or {}
    return {
        "suite": "not_run" if not suite.get("available") else "not_run",
        "suite_reason": suite.get("reason") or t("review.suite_not_run"),
        "behind_base": man.get("behind_base", False),
        "commits_behind_base": man.get("commits_behind_base", 0),
        # Three states on purpose: True conflicts, False merges cleanly, None was not
        # measured. A None collapsed into False would read as "merges cleanly" and the
        # judge would annotate a staleness it never checked.
        "conflicts": man.get("conflicts"),
        "conflicted_files": man.get("conflicted_files") or [],
        "conflicts_reason": man.get("conflicts_reason"),
        "mandatory_reading_empty": not (package / "criteria.json").exists(),
    }


def prepare(args) -> int:
    package = pathlib.Path(args.package
                           or (DEFAULT_PACKAGE_ROOT / f"{args.repo}-{int(time.time())}"))
    package.mkdir(parents=True, exist_ok=True)
    mark(package)

    # The mandatory reading is copied into the package BEFORE the preflight, because it is
    # the file's existence — not the caller's intention — that counts as read.
    reading = copy_mandatory_reading(args, package)

    pre = ["--repo", args.repo, "--package", str(package), "--origin", reading["origin"]]
    if reading["item"]:
        pre.append("--item-read")
    if reading["proposal_description"]:
        pre.append("--proposal-read")
    _py("preflight.py", *pre, check=False)

    cap = read_json(package / "capabilities.json")
    if cap["impediments"]:
        for impediment in cap["impediments"]:
            print(t("review.impediment", reason=impediment))
        raise UsageError(t("review.impediment_open"))

    target = ["--repo", args.repo]
    if args.proposal:
        target += ["--proposal", str(args.proposal)]
    elif args.branch:
        target += ["--branch", args.branch]
    if args.item:
        target += ["--item", args.item]
    _py("resolver.py", *target, "--package", str(package))
    _py("package.py", "--package", str(package))

    # The profile goes INTO the package: that is where the agents read it from, and that
    # is how the version actually used stays recorded next to the run.
    source = profile_path(args.repo)
    if source.exists():
        shutil.copy2(source, package / "profile.yml")
    else:
        (package / "profile.yml").write_text(
            f"# there is no profile for `{args.repo}`.\n"
            f"# run: derive_profile.py {args.repo}\n"
            f"profile: {args.repo}\nstatus: ausente\nanchors: {{}}\nconventions: []\n",
            encoding="utf-8")

    # An empty diff is not a clean review: it is the absence of an object. Spawning a
    # reviewer to read nothing would produce an APPROVED with no content, which is worse
    # than no answer at all.
    change = read_json(package / "change.json")
    if not change["files"]:
        raise UsageError(t("review.empty_diff_at", ref=short_range(package),
                           **_vocabulary()))

    _py("triage.py", "--package", str(package))
    write_json(package / "scope.json", item_scope(package))
    write_json(package / "evidence.json", initial_evidence(package))

    plan = read_json(package / "plan.json")
    profile = load_profile(args.repo)
    print("\n" + t("review.package_ready", path=package))
    print(t("review.profile_status", status=profile.get("status"),
            version=profile.get("version", 0)))
    if profile.get("status") != "confirmed" and not args.advisory:
        print("  " + t("review.profile_draft_note"))
    print("\n" + t("review.invoke_workflow") + "\n")
    # The full arguments go to a FILE, and the terminal gets a readable invocation.
    #
    # They used to be printed whole. Then the small package files moved into them, and
    # the invocation became a wall of JSON that whoever conducts the review has to carry
    # in their context and echo back in the tool call — paying for the payload twice, in
    # the one place that was not the problem. A file costs one read.
    workflow_args = {
        "package": str(package),
        "plan": plan,
        "profile": {"status": profile.get("status"),
                    "conventions": profile.get("conventions") or []},
        "classes": read_json(package / "classes.json"),
        "inline": inline_payload(package),
    }
    write_json(package / "workflow-args.json", workflow_args)

    inline_kb = sum(len(v.encode("utf-8")) for v in workflow_args["inline"].values()) // 1024
    print(t("review.workflow_args_at", path=package / "workflow-args.json",
            files=len(workflow_args["inline"]), kb=inline_kb))
    print("\nWorkflow({name: 'task-review', args: <the object in that file>})")
    print("\n" + t("review.then_conclude", path=package))
    return 0


# The small package files, carried IN the prompt instead of pointed at.
#
# Measured on a real review of a 1,885-line delivery: six agents spent 7.9M tokens, and
# 84% of that was `cache_read` — every request re-reading everything the agent had
# accumulated. The accumulation came from 81 `Read` calls, and the same seven small
# files appeared over and over: the manifest five times between them, the profile six,
# the patch slice eight.
#
# Each of those reads is a round trip: a request to ask, a result to carry, and the whole
# context re-read on the next one. Handing the content over in the prompt turns ~35 round
# trips into zero, and the content enters once, inside the prefix that is already cached.
#
# Only the SMALL ones. The patch stays a path on purpose — it is the big payload, it
# differs per slice, and putting it in the prompt would pay for it in every agent that
# does not need it, which is the opposite of what the slices are for.
INLINE_CEILING = 24_000   # bytes, per file: above this, pointing at it is cheaper

# `proposal.md` belongs here for the same reason `item.md` does, and its absence was a
# silent hole: the description was demanded at the gate, copied into the package, and
# then read by nobody. `scope-coherence` is asked whether item, proposal and code tell
# the SAME story, and it was being handed two of the three.
INLINE_FILES = ("manifest.json", "capabilities.json", "profile.yml", "classes.json",
                "criteria.json", "item.md", "proposal.md", "out-of-scope.json")


def inline_payload(package: pathlib.Path) -> dict:
    """What every reviewer would otherwise read one by one, read once here.

    A file that is missing is simply absent from the payload — the workflow says so in
    the prompt, which is the same thing the agent would have discovered by reading a
    path that is not there, minus the round trip."""
    out = {}
    for name in INLINE_FILES:
        f = package / name
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        if len(text.encode("utf-8")) > INLINE_CEILING:
            continue
        out[name] = text
    return out


def conclude(args) -> int:
    package = pathlib.Path(args.package)
    for name, source in (("findings.json", args.findings),
                         ("scripts_manual.json", args.scripts_manual)):
        if source:
            write_json(package / name, read_json(pathlib.Path(source)))

    if args.advisory:
        print(t("review.advisory_mode"))
        _py("triage.py", "--package", str(package))
        unmark()
        return 0

    # The review judges WHAT THE DELIVERY DID. A defect the change did not cause is not a
    # finding of this review: it comes in as an OBSERVATION, outside the count and outside
    # the verdict. Mixing the two makes the delivery answer for the repository's debt, and
    # buries what genuinely needs fixing inside what nobody asked anyone to look at now.
    findings_path = package / "findings.json"
    if findings_path.exists():
        all_findings = read_json(findings_path)
        own = [f for f in all_findings if f.get("attributable", True)]
        from_project = [f for f in all_findings if not f.get("attributable", True)]
        if from_project:
            write_json(findings_path, own)
            write_json(package / "observations.json", from_project)
            print(t("review.findings_split", own=len(own), project=len(from_project)))

    # `criteria.json` can only arrive AFTER the package exists — F2b runs over it.
    # `initial_evidence` judges it in `prepare`, when there is no way for it to exist yet,
    # so the flag would freeze at True and open a phantom manual-verification route. What
    # decides is the disk, at judge_delivery time.
    ev_path = package / "evidence.json"
    if ev_path.exists():
        ev = read_json(ev_path)
        empty = not (package / "criteria.json").exists()
        if ev.get("mandatory_reading_empty") != empty:
            ev["mandatory_reading_empty"] = empty
            write_json(ev_path, ev)

    _py("judge.py", "--package", str(package))
    _py("render.py", "--package", str(package))

    man, ver = read_json(package / "manifest.json"), read_json(package / "verdict.json")
    profile = load_profile(man["repo"])
    # A directory name, not a message: it stays the same in every language, or the same
    # review filed twice lands in two folders depending on who ran it.
    item = man["item"]["id"] or "no-item"
    destination = dir_reports() / man["repo"] / f"{item}-{slug(man['origin'])}"
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"review-{today()}.md"
    shutil.copy2(package / "report.md", target)
    shutil.copy2(package / "item-comment.html",
                 destination / f"item-comment-{today()}.html")

    # This run's cost, read from the transcripts. It feeds F11 and runs before it because
    # it is what carries `tokens` and `minutes` — the fields `metrics.py` already knew how
    # to read and nobody was filling in. Not measuring is not a review failure: the line
    # goes out with `tokens: null` and the reason beside it, never with a zero.
    from telemetry import measure, summarise
    tel = measure(str(package), None)
    write_json(package / "telemetry.json", tel)
    print("\n" + summarise(tel))

    # F11: one line per run. It is the basis for precision, cost and coverage per
    # repository.
    findings = read_json(package / "findings.json") \
        if (package / "findings.json").exists() else []
    blocking = [f["id"] for f in findings
                if f.get("severity") in ("critical", "required")
                and f.get("status") != "refuted" and f.get("attributable", True)]
    line = {
        "date": today(), "repo": man["repo"], "origin": man["origin"], "item": item,
        "head_sha": man["head_sha"], "verdict": ver["verdict"],
        "routes": [r["type"] for r in ver["routes"]],
        "perfil_status": profile.get("status"), "perfil_versao": profile.get("version", 0),
        "profile_coverage": 18 - len(ver["summary"]["classes_not_assessable"]),
        "classes_not_assessable": ver["summary"]["classes_not_assessable"],
        # Tokens in absolute numbers do not compare two reviews: a big proposal costs more
        # for being big. The size travels with them so that cost PER LINE is measurable,
        # which is the only way to know whether a cut in the harness worked.
        "lines_changed": read_json(package / "change.json").get("lines_changed"),
        "published_findings": len(findings),
        "open_blocking_findings": blocking,
        "relatorio": str(target.relative_to(dir_reports().parent.parent)),
        "tokens": tel.get("tokens"),
        "minutes": tel.get("minutes"),
        "harness_tokens": tel.get("harness_tokens"),
        "tokens_not_measured_because": tel.get("reason"),
        "run_id": tel.get("run_id"),
        "agents": tel.get("agents"),
        "cost_per_agent": tel.get("cost_per_agent") or {},
    }
    runs = dir_reports() / "runs.jsonl"
    runs.parent.mkdir(parents=True, exist_ok=True)
    with runs.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    vocab = _vocabulary()
    print("\n" + t("review.report_at", path=target))
    print(t("review.record_at", path=runs))
    print(t("review.telemetry_at", path=package / "telemetry.json"))
    print("\n" + t("review.three_acts"))
    print("  " + t("review.act_item", **vocab) + "    " + t("review.act_proposal", **vocab)
          + ("    " + t("review.act_approve", **vocab)
             if ver["verdict"] == "APPROVED" else ""))
    if ver["verdict"] != "APPROVED":
        print("     " + t("review.act_no_approve"))
    print(t("review.never", **vocab))
    unmark()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("prepare")
    a.add_argument("--repo", required=True)
    a.add_argument("--proposal", type=int)
    a.add_argument("--branch")
    a.add_argument("--item")
    # Help text is built BEFORE anything reads `prumo.yml`, so it names no provider: a
    # `--help` on a broken installation has to print, not raise. The provider vocabulary
    # starts at `prepare`, inside the try that turns a missing configuration into an
    # impediment with an exit code.
    a.add_argument("--item-file", help=t("review.help_item_file"))
    a.add_argument("--proposal-file", help=t("review.help_proposal_file"))
    a.add_argument("--package")
    a.add_argument("--advisory", action="store_true")

    b = sub.add_parser("conclude")
    b.add_argument("--package", required=True)
    b.add_argument("--findings")
    b.add_argument("--scripts-manual")
    b.add_argument("--advisory", action="store_true")

    sub.add_parser("release", help=t("review.help_release"))

    args = p.parse_args()
    if args.command == "release":
        unmark()
        print(t("review.marker_removed", path=marker()))
        return 0
    try:
        return prepare(args) if args.command == "prepare" else conclude(args)
    except (MissingConfiguration, InvalidConfiguration) as e:
        # An incomplete installation is not a review stop: it is an impediment to use.
        # The prumo assumes neither provider nor path — see `prumo.yml.example`.
        unmark()
        print("\n" + t("config.impediment", reason=e))
        return 2
    except UsageError as e:
        unmark()             # a stop must not leave the workspace locked
        print("\n" + t("review.stop", reason=e))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
