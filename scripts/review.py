#!/usr/bin/env python3
"""Review orchestrator. Two commands, because the workflow sits between them.

    review.py prepare --repo R [--proposal N | --branch B] --item C
                      --item-file ITEM.md [--proposal-file PROPOSAL.md] [--package P]
        F0 preflight · F1 resolve · F2 package · F3 triage.
        Leaves the package ready and PRINTS the workflow invocation.

    review.py conclude --package P [--findings F] [--scripts-manual S]
                       [--conventions C]
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
import re
import shutil
import subprocess
import sys
import time

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import (MissingConfiguration, dir_repositories, InvalidConfiguration, dir_packages, dir_reports, forge, tracker, UsageError, profile_path, load_profile, write_json,
                   today, read_json, slug, cfg, capabilities_after_preflight, hint_config,
                   report_dir_name)
from i18n import t
from preflight import proof_registry_at_head
from evidence import red_names, progress, authorise, resolve, run_command, time_limit

ROOT = pathlib.Path(__file__).resolve().parent
# Where the package is assembled, when `--package` does not say. It is `paths.packages`
# in `prumo.yml`, which DEFAULTS to `$TMPDIR/task-review` — a package is scratch, and an
# installation that never asked should not find patches in its tree. One that points this
# at its own workspace gets them kept: inspectable after the review, surviving a /tmp
# sweep, and in one place with the reports they produced.


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
#
# The fallback is the INSTALLATION's root — where `prumo.yml` sits — and not the current
# directory, because the current directory is not a fact about anything. The harness sets
# `CLAUDE_PROJECT_DIR` for a hook but not for an ordinary shell command, so a `review.py
# prepare` fired from the scripts directory wrote the marker to
# `scripts/.claude/.task-review-ativo` while the hook went on reading the project root:
# the guard over push, commit and moving the item was off for the whole review, in
# silence. The same loss this function's history already paid for once, reached by
# another road — so the remedy is the same one the test states: a path two pieces share
# is derived from ONE origin, and `prumo.yml` is an origin that does not move with the
# shell. Measured on 2026-11-04.
def marker() -> pathlib.Path:
    base = os.environ.get("CLAUDE_PROJECT_DIR") or _installation_root() or os.getcwd()
    return pathlib.Path(base) / ".claude" / ".task-review-ativo"


def _installation_root() -> str | None:
    """Where `prumo.yml` was read from. `None` when there is no configuration — the
    review will die of that later, with a readable impediment; it must not die HERE,
    inside the guard, where the message would be about the wrong thing."""
    try:
        source = cfg().source
    except Exception:
        return None
    return str(source.parent) if source else None


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


SECRETS_RUN = "run-secrets.txt"
SECRETS_SHOWN = 10


def run_secrets_check(package: pathlib.Path, profile: dict, repo_dir: pathlib.Path) -> dict | None:
    """Runs the profile's `secrets` check over THIS review's change, and lets A9 hear it.

    The check used to be a capability only: preflight asked whether the tool exists, and
    A9 left `not_assessable` the moment it did — with nothing ever scanned. The command
    now runs, with `{base}` and `{head}` filled from the manifest, its output stays in the
    package, and a red turns A9 into `fired` with the scanner's lines as the evidence: a
    secret is a person's decision, never a reviewer's guess. A green adds that the scan
    ran; it never silences what the reviewers read in the diff themselves."""
    check = (profile.get("checks") or {}).get("secrets") or {}
    cap = (read_json(package / "capabilities.json").get("checks") or {}).get("secrets") or {}
    if not check.get("command") or not cap.get("available"):
        return None
    ok, reason = authorise(check["command"], profile)
    if not ok:
        return {"ran": False, "state": "not_run", "reason": reason}
    man = read_json(package / "manifest.json")
    command = (resolve(check["command"], profile, repo_dir)
               .replace("{base}", man.get("merge_base") or man["base"]["sha"])
               .replace("{head}", man["head_sha"]))
    result = run_command(command, repo_dir, timeout=time_limit(check))
    output = result.get("output", result.get("reason", ""))
    (package / SECRETS_RUN).write_text(output, encoding="utf-8")
    if not result.get("ran"):
        return {"ran": False, "state": "not_run", "reason": result.get("reason", "")}
    pattern = check.get("failure_pattern")
    red = result["exit_code"] != 0 or bool(pattern and re.search(pattern, output))
    if red:
        classes = read_json(package / "classes.json")
        lines = [l for l in output.splitlines() if re.match(r"\S+:\d+:", l)][:SECRETS_SHOWN]
        for c in classes:
            if c["id"] == "A9":
                c.update(state="fired", cause="check_red",
                         reason=t("review.secrets_found", run=SECRETS_RUN),
                         evidence=[{"file": l.split(":", 1)[0], "by": l} for l in lines]
                                  or [{"file": SECRETS_RUN, "by": SECRETS_RUN}])
        write_json(package / "classes.json", classes)
    return {"ran": True, "state": "red" if red else "green",
            "reason": t("review.secrets_found", run=SECRETS_RUN) if red
                      else t("review.secrets_clean", run=SECRETS_RUN)}


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


RUNS_DIR = "delivery-runs"


def copy_delivery(source: pathlib.Path, package: pathlib.Path) -> list[str]:
    """The delivery's record AND its captured runs — `run-*.txt`, the suite on the base
    and on the branch, each mutant, the lint — copied beside the review. The record
    names each run by file; a reviewer who doubts a state opens the output instead of
    arguing with a sentence. A verifier once confirmed a contradiction that the run,
    one directory away, refuted. Returns the names copied, for the invocation."""
    record = source / "delivery.json"
    if not record.is_file():
        raise UsageError(t("review.reading_missing", path=record))
    shutil.copy2(record, package / "delivery-record.json")
    runs = sorted(p.name for p in source.glob("run-*.txt"))
    if runs:
        (package / RUNS_DIR).mkdir(exist_ok=True)
        for name in runs:
            shutil.copy2(source / name, package / RUNS_DIR / name)
    return runs


DIGEST_FILE = "delivery-digest.json"
BASE_RUN = "run-suite-base.txt"


def digest_runs(package: pathlib.Path, profile: dict) -> dict | None:
    """Every captured run, read ONCE by the harness with the profile's own patterns, so
    the reviewers get the measurement instead of a pointer to six thousand lines.

    The runs travelled with the record since 2.8.0, and the prompt said "open one when a
    state needs checking". Measured on the next real review: both reviewers spent their
    two reads on the diff slice, never opened a run, and raised — from the record's
    prose — that mutant (a) had turned one refusal test red. Its run held seventeen reds
    beyond the base, the three refusal tests among them. A pointer is not evidence; the
    reds by name are. `beyond_base` is the set a mutant killed: the run's reds minus the
    base run's, the same subtraction `deliver.py` makes, done here for a record that was
    written before it recorded the whole set."""
    runs_dir = package / RUNS_DIR
    if not runs_dir.is_dir():
        return None
    checks = profile.get("checks") or {}
    suite, lint = checks.get("suite") or {}, checks.get("lint") or {}
    digest: dict = {"measured_by": "the harness, from each run file, with the profile's patterns",
                    "base_run": BASE_RUN if (runs_dir / BASE_RUN).is_file() else None, "runs": {}}
    base_reds = None
    if digest["base_run"]:
        base_reds = red_names((runs_dir / BASE_RUN).read_text(encoding="utf-8", errors="replace"),
                              suite.get("red_name_pattern"))
    for path in sorted(runs_dir.glob("run-*.txt")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.name.startswith("run-lint"):
            pattern = lint.get("failure_pattern")
            failed = None
            if pattern:
                try:
                    failed = re.search(pattern, text) is not None
                except re.error:
                    failed = None
            digest["runs"][path.name] = {"role": "lint", "failed": failed}
            continue
        reds = red_names(text, suite.get("red_name_pattern"))
        executed, predicted = progress(text, suite.get("progress_pattern"))
        entry = {"role": "suite", "red_count": None if reds is None else len(reds),
                 "executed": executed, "predicted": predicted}
        # The base run carries its reds by name once; every other run says only what
        # changed against it — `beyond_base`, what went red, and `back_to_green`, what
        # stopped being red. Listing the base's twenty-two names under every mutant
        # tripled the digest and said nothing the subtraction does not.
        if path.name == BASE_RUN or digest["base_run"] is None:
            entry["reds"] = reds
        else:
            known = reds is not None and base_reds is not None
            entry["beyond_base"] = sorted(set(reds) - set(base_reds)) if known else None
            entry["back_to_green"] = sorted(set(base_reds) - set(reds)) if known else None
        digest["runs"][path.name] = entry
    write_json(package / DIGEST_FILE, digest)
    return digest


CANDIDATES_FILE = "criteria-candidates.json"
LIST_LINE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+?)\s*$")
HEADING_LINE = re.compile(r"^\s*(?:#{1,6}\s+(.+?)|\*\*([^*]+)\*\*:?)\s*$")


def criteria_candidates(item_text: str) -> list[dict]:
    """Every list line of the item, numbered in document order, with the heading it sits
    under. The extractor used to cut the item into criteria by itself and cut it
    differently each time — nine over one reading, five over the next, the same text —
    because "the five tests above" can be one criterion or the five. Now the HARNESS
    names the lines; the extractor only labels them; and `build_criteria` applies the
    one rule that was wobbling: a line that summarises other lines IS those lines."""
    out: list[dict] = []
    heading = None
    for n, line in enumerate(item_text.splitlines(), 1):
        h = HEADING_LINE.match(line)
        if h:
            heading = (h.group(1) or h.group(2) or "").strip()
            continue
        m = LIST_LINE.match(line)
        if m:
            out.append({"id": f"L-{len(out) + 1}", "line": n, "under": heading, "text": m.group(1)})
    return out


def build_criteria(candidates: list[dict], labels: list[dict]) -> tuple[list[dict], list[str]]:
    """The criteria list from the harness's candidates and the extractor's labels —
    deterministic, in document order, ids `AC-n`. Roles: `criterion`, `out_of_scope`,
    `context`, and `summary` with `of`: the ids it restates. A summary is replaced by
    its parts whatever the parts were labelled, and never kept beside them. The same
    function runs in the workflow for the reviewers' prompt and here for the file."""
    by_id = {c["id"]: c for c in candidates}
    role = {l["id"]: l for l in labels or [] if isinstance(l, dict) and l.get("id") in by_id}
    chosen: list[str] = []
    for c in candidates:
        l = role.get(c["id"])
        if not l:
            continue
        if l.get("role") == "criterion":
            chosen.append(c["id"])
        elif l.get("role") == "summary":
            parts = [x for x in (l.get("of") or []) if x in by_id and x != c["id"]]
            chosen.extend(parts or [c["id"]])
    ordered: list[str] = []
    for i in sorted(chosen, key=lambda i: by_id[i]["line"]):
        if i not in ordered:
            ordered.append(i)
    criteria = [{"id": f"AC-{k}", "text": by_id[i]["text"], "state": "not_verifiable",
                 "record": None, "evidence": [], "candidate": i}
                for k, i in enumerate(ordered, 1)]
    out_of_scope = [c["text"] for c in candidates if role.get(c["id"], {}).get("role") == "out_of_scope"]
    return criteria, out_of_scope


WORKFLOW_FILES = (("findings", "findings.json"), ("scripts_manual", "scripts_manual.json"),
                  ("conventions_observed", "conventions_observed.json"), ("coverage", "coverage.json"))


def workflow_result_from(path: pathlib.Path) -> dict:
    """The object the workflow returned — bare, or inside the envelope the harness writes
    to the task's output file (`summary`, `logs`, `result`, `totalTokens`). The first
    real run of 2.8.0 saved that file as it came and `conclude` split nothing: the keys
    it looks for sat one level down. The envelope is what a person has at hand; it is
    accepted as is, and unwrapping is said."""
    raw = read_json(path)
    inner = raw.get("result") if isinstance(raw, dict) else None
    if isinstance(inner, dict) and not any(k in raw for k, _ in WORKFLOW_FILES):
        print(t("review.workflow_envelope_unwrapped", path=path))
        return inner
    return raw


def absorb_workflow_result(package: pathlib.Path, result: dict) -> list[dict]:
    """The object the workflow returned, split into the files the judge reads. Five
    files used to be written by hand between the workflow and `conclude`, one per
    key, on every review; the one forgotten was always `coverage`, and a slice nobody
    read then passed unnoticed. The extractor's list lands as `criteria.json` only when
    the package has none — a list a person already confirmed is never overwritten.
    Returns the reviewers' criterion readings, for `merge_criteria`."""
    for key, name in WORKFLOW_FILES:
        if key in result:
            write_json(package / name, result[key])
    labels = result.get("criteria_labels")
    if labels is not None and (package / CANDIDATES_FILE).exists() \
            and not (package / "criteria.json").exists():
        criteria, out = build_criteria(read_json(package / CANDIDATES_FILE), labels)
        write_json(package / "criteria.json", criteria)
        write_json(package / "out-of-scope.json", {"out_of_scope": out})
    extracted = result.get("criteria_extracted") or {}
    if extracted.get("criteria") is not None and not (package / "criteria.json").exists():
        write_json(package / "criteria.json", extracted["criteria"])
        write_json(package / "out-of-scope.json",
                   {"out_of_scope": extracted.get("out_of_scope") or []})
    return list(result.get("criteria") or [])


def merge_criteria(current: list[dict], readings: list[dict]) -> tuple[list[dict], list[str]]:
    """The reviewers' reading of each criterion over the extractor's list.

    `met` and `not_met` travel with their record. `not_verifiable` travels as a NOTE:
    the reviewer's reason is kept for the reader, and `record` stays empty, because a
    record is where a PERSON's evidence sits. A review once approved four criteria the
    reviewer had marked "a person has to confirm" — the sentence sat in `record`, the
    judge saw a record, and gate 3 passed on a reviewer's shrug.

    An id the extractor never gave is returned, not dropped in silence: the reviewers
    once answered `c1-five-tests` to an extractor that said `AC-1`, every reading fell
    on the floor, and the review came out with eight criteria nobody had read."""
    verdicts = {c["id"]: c for c in readings or [] if isinstance(c, dict) and c.get("id")}
    known = {c["id"] for c in current}
    for c in current:
        v = verdicts.get(c["id"])
        if not v or v.get("state") not in ("met", "not_met", "not_verifiable"):
            continue
        if v["state"] == "not_verifiable":
            c["state"] = "not_verifiable"
            if v.get("record"):
                c["note"] = v["record"]
            # Only the running system decides it: QA's, after the approval (2026-11-19).
            # A `decided_by` a person already wrote on the list is never overwritten.
            if v.get("decided_by") == "qa" and not c.get("decided_by"):
                c["decided_by"] = "qa"
        elif v.get("record"):
            c["state"], c["record"] = v["state"], v["record"]
    return current, sorted(set(verdicts) - known)


def delivery_record_for(package: pathlib.Path, head_sha: str) -> dict | None:
    """The record a delivery left, when it is about THIS head. A record about another
    commit is somebody else's proof: it is kept in the package for the reader and
    counts for nothing here."""
    path = package / "delivery-record.json"
    if not path.exists():
        return None
    record = read_json(path)
    return record if (record.get("branch") or {}).get("sha") == head_sha else None


def record_stands(package: pathlib.Path) -> bool:
    """The delivery record in the package speaks for the reviewed head: it is about that
    head, or every commit after it touches no source or test root. No record: True —
    there is nothing to set aside."""
    path = package / "delivery-record.json"
    if not path.exists() or not (package / "manifest.json").exists():
        return True
    man = read_json(package / "manifest.json")
    sha = (read_json(path).get("branch") or {}).get("sha")
    if sha == man.get("head_sha"):
        return True
    profile_file = package / "profile.yml"
    profile = (yaml.safe_load(profile_file.read_text(encoding="utf-8")) or {}
               if profile_file.exists() else {})
    return bool(man.get("repo")) and code_free_since(
        dir_repositories() / man["repo"], profile.get("delivery") or {}, sha, man.get("head_sha")) is not None


def code_free_since(repo_dir: pathlib.Path, block: dict, since: str, head: str) -> list[str] | None:
    """The files changed from `since` to `head` when NONE of them is under the profile's
    source or test roots, else None. Commits that touch only documentation cannot change
    what the suite measures — the method itself asks for the spec and the plan to be
    reconciled after the code, and the first delivery that did so came to its review with
    a record one docs commit behind the head and a suite gate that read "not run".
    No roots declared, `since` not an ancestor of `head`, or git failing: None, and the
    record stays somebody else's proof."""
    roots = [r.rstrip("/") for r in (block.get("source_roots") or []) + (block.get("test_roots") or [])]
    if not roots or not since or not head:
        return None
    def git(*a):
        return subprocess.run(("git", "-C", str(repo_dir), *a), capture_output=True, text=True)
    if git("merge-base", "--is-ancestor", since, head).returncode != 0:
        return None
    diff = git("diff", "--name-only", since, head)
    if diff.returncode != 0:
        return None
    files = [f for f in diff.stdout.splitlines() if f]
    if any(f == r or f.startswith(r + "/") for f in files for r in roots):
        return None
    return files


def suite_from_record(record: dict) -> tuple[str, str] | None:
    """The suite as the delivery measured it on this head: green when it finished and
    turned nothing red by name; red when it did. A record that did not read the names
    says nothing, and the gate goes on waiting for a run of its own."""
    b = record.get("baseline") or {}
    if b.get("new_reds") is None or b.get("executed") is None:
        return None
    if b.get("predicted") is not None and b["executed"] < b["predicted"]:
        return None
    if b["new_reds"]:
        return "red_attributable", t("review.suite_from_delivery_red", names=", ".join(b["new_reds"]))
    if b.get("scope") == "focused":
        # A small delivery is checked by the test files it touched, and the gate says
        # so in words: a reader must never take those for the whole suite.
        return "green", t("review.suite_from_delivery_focused", executed=b["executed"])
    return "green", t("review.suite_from_delivery", executed=b["executed"],
                      reds=len(b.get("base_reds") or []))


def initial_evidence(package: pathlib.Path) -> dict:
    """What the gate needs and does not have yet. Phase F6 may fill it; without F6 it
    stays like this — unless a delivery already measured this very head."""
    man = read_json(package / "manifest.json")
    cap = read_json(package / "capabilities.json")
    suite = cap["checks"].get("suite") or {}
    record = delivery_record_for(package, man["head_sha"])
    path = package / "delivery-record.json"
    other = read_json(path) if path.exists() and not record else None
    other_sha = ((other or {}).get("branch") or {}).get("sha")
    docs_after = None
    if other_sha:
        profile_file = package / "profile.yml"
        profile = (yaml.safe_load(profile_file.read_text(encoding="utf-8")) or {}
                   if profile_file.exists() else {})
        docs_after = (code_free_since(dir_repositories() / man["repo"],
                                      profile.get("delivery") or {}, other_sha, man["head_sha"])
                      if man.get("repo") else None)
        if docs_after is not None:
            record = other
    measured = suite_from_record(record) if record else None
    if measured and docs_after is not None:
        measured = (measured[0], measured[1] + t("review.suite_record_docs_after",
                                                 sha=other_sha[:12], files=", ".join(docs_after)))
    lint = (record or {}).get("lint") or {}
    reason = (measured[1] if measured
              else t("review.suite_record_other_head", sha=other_sha[:12]) if other_sha
              else suite.get("reason") or t("review.suite_not_run"))
    return {
        "suite": measured[0] if measured else "not_run",
        "suite_reason": reason,
        "delivery_record": bool(record),
        **({"delivery_record_sha": other_sha[:12]} if other_sha else {}),
        **({"checks": {"lint": {"state": lint["state"], "reason": lint.get("reason", "")}}}
           if lint.get("state") in ("green", "red") else {}),
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
                           or (dir_packages() / f"{args.repo}-{int(time.time())}"))
    package.mkdir(parents=True, exist_ok=True)
    mark(package)

    # The mandatory reading is copied into the package BEFORE the preflight, because it is
    # the file's existence — not the caller's intention — that counts as read.
    reading = copy_mandatory_reading(args, package)
    if (package / "item.md").is_file():
        write_json(package / CANDIDATES_FILE,
                   criteria_candidates((package / "item.md").read_text(encoding="utf-8")))
    # A delivery's record, when the branch was delivered by the prumo: the proof its
    # own run measured — mutations, the suite by name, lint — travels with the review.
    if getattr(args, "delivery", None):
        copy_delivery(pathlib.Path(args.delivery), package)
        digest = digest_runs(package, load_profile(args.repo))
        if digest:
            killed = {n: len(r["beyond_base"]) for n, r in digest["runs"].items()
                      if r.get("beyond_base")}
            print(t("review.runs_digested", n=len(digest["runs"]), path=package / DIGEST_FILE,
                    killed=", ".join(f"{n}: {k}" for n, k in killed.items()) or "-"))

    pre = ["--repo", args.repo, "--package", str(package), "--origin", reading["origin"]]
    if reading["item"]:
        pre.append("--item-read")
    if reading["proposal_description"]:
        pre.append("--proposal-read")
    _py("preflight.py", *pre, check=False)

    cap = capabilities_after_preflight(package)
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
    if getattr(args, "base", None):
        target += ["--base", args.base]
    _py("resolver.py", *target, "--package", str(package))
    _py("package.py", "--package", str(package))
    cap = read_json(package / "capabilities.json")
    registry = proof_registry_at_head(cap["proof_registry"], dir_repositories() / args.repo,
                                      read_json(package / "manifest.json")["head_sha"])
    if registry is not cap["proof_registry"]:
        cap["proof_registry"] = registry
        write_json(package / "capabilities.json", cap)
    reviewed = read_json(package / "manifest.json")
    own = subprocess.run(("git", "-C", str(dir_repositories() / args.repo), "rev-list",
                          f"{reviewed.get('merge_base')}..{reviewed.get('head_sha')}"),
                         capture_output=True, text=True).stdout.split()
    stale = stale_numbers((package / "diff.patch").read_text(encoding="utf-8")
                          if (package / "diff.patch").exists() else "", own)
    if stale:
        cap.setdefault("warnings", []).extend(
            t("review.stale_number", file=s["file"], line=s["line"], kind=t("review.stale_kind_" + s["kind"]),
              text=s["text"]) for s in stale)
        write_json(package / "capabilities.json", cap)
        for s in stale:
            print("  " + t("review.stale_number", file=s["file"], line=s["line"],
                           kind=t("review.stale_kind_" + s["kind"]), text=s["text"]))

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
    evidence = initial_evidence(package)
    secrets = run_secrets_check(package, load_profile(args.repo), dir_repositories() / args.repo)
    if secrets:
        evidence.setdefault("checks", {})["secrets"] = secrets
        print(t("review.secrets_line", state=secrets["state"], reason=secrets["reason"]))
    write_json(package / "evidence.json", evidence)

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
    if not record_stands(package):
        sha = (read_json(package / "delivery-record.json").get("branch") or {}).get("sha") or "?"
        print("  " + t("review.delivery_record_set_aside", sha=sha[:12],
                       head=read_json(package / "manifest.json")["head_sha"][:12]))
    workflow_args = {
        "package": str(package),
        "plan": plan,
        "profile": {"status": profile.get("status"),
                    "conventions": profile.get("conventions") or [],
                    "anchors": anchors_without_history(profile)},
        "classes": read_json(package / "classes.json"),
        "inline": inline_payload(package),
    }
    write_json(package / "workflow-args.json", workflow_args)

    inline_kb = sum(len(v.encode("utf-8")) for v in workflow_args["inline"].values()) // 1024
    print(t("review.workflow_args_at", path=package / "workflow-args.json",
            files=len(workflow_args["inline"]), kb=inline_kb))
    from triage import workflow_name
    print("\nWorkflow({name: '" + workflow_name("task-review") + "', args: <the object in that file>})")
    print("\n" + t("review.then_conclude", path=package))
    return 0


def anchors_without_history(profile: dict) -> dict:
    """The profile's anchors as the manual planner gets them: where and against what to
    verify, without the `cards` that taught the profile each class.

    Those cards are the project's history — earlier items — and never the item under
    review. On 2026-11-18 the planner of a review copied an anchor's card into its
    script as the item, and provenance stopped the conclude: the id was nowhere in the
    manifest. What is not handed over cannot be copied; a note keeps its text, because
    it says what to look at, and the workflow says what an id inside it is."""
    return {cid: {k: v for k, v in anchor.items() if k != "cards"}
            for cid, anchor in (profile.get("anchors") or {}).items()
            if isinstance(anchor, dict)}


# Prose a change adds — a spec, a plan, a changelog — that names a number only a run or
# the history knows. On 2026-11-18 a delivery's tech plan said "8 tests" where 6 stood and
# "711 of 711" where 713 ran, and an item comment said 709: each true once, each written by
# hand, each stale one commit later, and one review spent a finding on them. A SHA that
# the range made and a count of the form N/N or N of N are the two shapes measured; nothing
# here blocks — the report says it, and whoever wrote it decides. Only the range's own
# commits count: the base a spec says it was cut from is a fact that does not move.
PROSE_SUFFIXES = (".md", ".markdown", ".rst", ".adoc", ".txt")
_HEX = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{7,40}(?![0-9A-Za-z])")
_RUN = r"(?<![\d/])(\d{{2,}})(?:\s*/\s*|\s+(?:{words})\s+)\1(?![\d/])"


def _run_count() -> re.Pattern:
    """N/N, and N of N in the method's language and in the installation's."""
    words = {"of", *(w.strip() for w in t("review.run_count_word").split("|") if w.strip())}
    return re.compile(_RUN.format(words="|".join(sorted(map(re.escape, words)))))


def stale_numbers(patch: str, own_commits: list[str]) -> list[dict]:
    """The added prose lines of `patch` that cite one of `own_commits` — the commits the
    reviewed range itself made — or a run count N/N. Returns [{file, line, kind:
    commit|run_count, text}]. The base's SHA a spec names is a fixed fact and stays; a
    hex word that is no commit of the range — a UUID's group, a price id — stays too."""
    found, file, line = [], None, 0
    own = [c.lower() for c in own_commits if c]
    run_count = _run_count()
    for raw in patch.splitlines():
        if raw.startswith("+++ "):
            name = raw[4:].strip()
            file = name[2:] if name.startswith("b/") else None if name == "/dev/null" else name
            continue
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line = int(m.group(1)) if m else 0
            continue
        if raw.startswith("+") and file and file.lower().endswith(PROSE_SUFFIXES):
            text = raw[1:]
            for word in _HEX.findall(text):
                if any(c.isalpha() for c in word) and any(c.startswith(word) for c in own):
                    found.append({"file": file, "line": line, "kind": "commit", "text": word})
            for m in run_count.finditer(text):
                found.append({"file": file, "line": line, "kind": "run_count", "text": m.group(0)})
        if not raw.startswith("-"):
            line += 1
    return found


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
#
# The set is deliberately THE SAME for every reviewer, even though `proposal.md` and
# `out-of-scope.json` serve mostly scope-coherence (measured on 2026-11-04, when
# proportional effort asked whether anything here is single-reader): the workflow builds
# one invariant prefix so the cache carries across agents, and excluding a file per
# reviewer would split that prefix into variants — re-paying cache creation per agent to
# save at most `INLINE_CEILING` bytes carried once. The ceiling is what bounds the cost;
# a file too big to share is already pointed at, not carried.
INLINE_FILES = ("manifest.json", "capabilities.json", "profile.yml", "classes.json",
                "criteria.json", "item.md", "proposal.md", "out-of-scope.json",
                "delivery-record.json", DIGEST_FILE, CANDIDATES_FILE)


def inline_payload(package: pathlib.Path) -> dict:
    """What every reviewer would otherwise read one by one, read once here.

    A file that is missing is simply absent from the payload — the workflow says so in
    the prompt, which is the same thing the agent would have discovered by reading a
    path that is not there, minus the round trip."""
    out = {}
    # A record about an earlier head with code changed since is no proof of this one, and
    # the agents are not handed it: on 2026-11-18 a review prepared with the record of a
    # head two hand commits behind had its reviewers cite that head's SHA, and provenance
    # stopped the conclude. The record stays in the package; the report says whose it is.
    stale = set() if record_stands(package) else {"delivery-record.json", DIGEST_FILE}
    # The slices too, under the same ceiling: a reviewer handed a path to an 85-line
    # patch spent two of its turns reading it — twice, measured on two reviews of the
    # same delivery. A slice that fits is carried like any other package file, and the
    # prompt tells the reviewer not to read it again. One that does not fit stays a path.
    # Only the slices the plan hands to a reviewer: five travelled on a one-file delivery,
    # four of them the same bytes, for two reviewers.
    plan = read_json(package / "plan.json") if (package / "plan.json").exists() else {}
    wanted = {r.get("slice") for r in plan.get("reviewers") or [] if r.get("slice")}
    slices = [package / "slices" / f"{name}.patch" for name in sorted(wanted)]
    for f in [package / name for name in INLINE_FILES if name not in stale] + slices:
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        if len(text.encode("utf-8")) > INLINE_CEILING:
            continue
        out[str(f.relative_to(package))] = text
    return out


def colliding_ids(findings: list[dict]) -> dict[str, int]:
    """Ids given to more than one finding, with how many findings each one covers.

    An id is the only handle the verification map, `judge.py`'s blocking lists and
    `render.py`'s `by_finding` have on a finding. Each of those keys a dict by it, so two
    findings sharing an id do not come out as two rows: one silently replaces the other,
    and the survivor is then printed once per collision. Measured on 2026-11-04 — four
    reviewers, three of them numbering from `F1` in their own context, two findings gone
    and a third printed three times.

    Returns a mapping rather than a bool because the caller has to NAME the ids: `two
    findings collided` is not something anyone can act on."""
    counts: dict[str, int] = {}
    for f in findings:
        fid = f.get("id")
        if fid is None:
            continue
        counts[fid] = counts.get(fid, 0) + 1
    return {fid: n for fid, n in counts.items() if n > 1}


def _agent_tokens(cost_per_agent: dict, suffix: str):
    """What ONE agent type cost in a run, read from the telemetry aggregate.

    The keys carry the installation's prefix (`prumo:finding-verifier`), so the match is
    by suffix. Absence stays `None`, never zero: a run with no verifier is cheaper than a
    run whose verifier was not measured, and a zero would make the two identical."""
    rows = [v["billable"] if isinstance(v, dict) else v
            for k, v in (cost_per_agent or {}).items() if k.endswith(suffix)]
    return sum(rows) if rows else None


def conclude(args) -> int:
    package = pathlib.Path(args.package)
    readings: list[dict] = []
    if getattr(args, "workflow_result", None):
        readings = absorb_workflow_result(package, workflow_result_from(pathlib.Path(args.workflow_result)))
    for name, source in (("findings.json", args.findings),
                         ("scripts_manual.json", args.scripts_manual),
                         ("conventions_observed.json", args.conventions),
                         ("coverage.json", args.coverage)):
        if source:
            write_json(package / name, read_json(pathlib.Path(source)))
    if getattr(args, "criteria", None):
        readings = read_json(pathlib.Path(args.criteria)) or []
    # The reviewers' reading of each criterion, with its record, over the extractor's
    # list: a state travels only with a record, and an id the item never had is reported.
    if readings:
        path = package / "criteria.json"
        current = read_json(path) if path.exists() else []
        if isinstance(current, dict):
            current = current.get("criteria") or []
        current, unmatched = merge_criteria(current, readings)
        if unmatched:
            print(t("review.criteria_unmatched", n=len(unmatched), ids=", ".join(unmatched)))
        write_json(path, current)

    # A review relates to its own item and its own proposal, and to nothing else. The
    # agents can read the whole workspace, the reports root is in it, and one of them
    # really did anchor on a sibling review and return fifteen scripts about another
    # delivery. This STOPS rather than warns: a report that mixes two deliveries is
    # shaped exactly like a correct one, which makes it the worst thing this method can
    # produce. Checked here because this is the last gate before the judge reads them.
    import provenance
    contaminated = provenance.check(package, [package / "findings.json",
                                              package / "scripts_manual.json",
                                              package / "conventions_observed.json"])
    if contaminated:
        for line in contaminated:
            print(t("review.impediment", reason=line))
        print(t("provenance.remedy"))
        return 1

    # Two findings under one id means findings were LOST on the way here, and what
    # arrives is shaped exactly like a complete result. It STOPS for the same reason
    # provenance does: nobody reading the report could tell.
    collisions = colliding_ids(read_json(package / "findings.json") or [])
    if collisions:
        named = ", ".join(f"{fid} ({n})" for fid, n in sorted(collisions.items()))
        print(t("review.impediment",
                reason=t("findings.colliding_ids", n=len(collisions), ids=named)))
        print(t("findings.colliding_remedy"))
        return 1

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
        changed = ev.get("mandatory_reading_empty") != empty
        ev["mandatory_reading_empty"] = empty
        # A reviewer the triage ACTIVATED and that never answered leaves a slice nobody
        # read. Until 2026-11-04 that fact stopped at the report, and the verdict went
        # on claiming gate 5 while a quarter of the review had not happened — measured
        # over two runs of the same delivery. It belongs beside the suite, which is
        # where the judge already reads what was and was not measured.
        cov_path = package / "coverage.json"
        if cov_path.exists():
            cov = read_json(cov_path)
            missing = cov.get("reviewers_without_return") or 0
            changed = changed or ev.get("reviewers_without_return") != missing
            ev["reviewers_without_return"] = missing
            ev["slices_unread"] = list(cov.get("slices_unread") or [])
        if changed:
            write_json(ev_path, ev)

    _py("judge.py", "--package", str(package))
    _py("render.py", "--package", str(package))

    man, ver = read_json(package / "manifest.json"), read_json(package / "verdict.json")
    profile = load_profile(man["repo"])
    # A directory name, not a message: it stays the same in every language, or the same
    # review filed twice lands in two folders depending on who ran it.
    item = man["item"]["id"] or "no-item"
    destination = dir_reports() / man["repo"] / report_dir_name(man["item"]["id"], man["origin"])
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

    # What this review learned about the REPOSITORY, accrued outside it. A by-product,
    # and deliberately the last thing that happens to a package: it decides nothing, so a
    # failure here must never cost the review the report it already filed.
    try:
        from knowledge import accrue
        learned = accrue(package, str(package / "conventions_observed.json"))
        print(t("review.knowledge_at", path=learned["reading"],
                n=learned["entries"], reviews=learned["reviews"]))
        print("  " + t("review.knowledge_harvested", detail=" · ".join(
            f"{kind} {n}" for kind, n in (learned.get("harvested") or {}).items())))
    except Exception as e:                                  # noqa: BLE001 — see above
        print(t("review.knowledge_failed", error=e))

    # F11: one line per run. It is the basis for precision, cost and coverage per
    # repository.
    findings = read_json(package / "findings.json") \
        if (package / "findings.json").exists() else []
    blocking = [f["id"] for f in findings
                if f.get("severity") in ("critical", "required")
                and f.get("status") != "refuted" and f.get("attributable", True)]
    # The effort dimension travels with the run so that cost can be aggregated BY LEVEL:
    # a global tokens-per-line median mixes a docs-only delivery with a migration and
    # answers nothing about either. `effort.json` is written by the triage; a package
    # from before it exists simply records `null`, which is "not measured", not "minimal".
    effort = read_json(package / "effort.json") \
        if (package / "effort.json").exists() else {}
    plan = read_json(package / "plan.json") \
        if (package / "plan.json").exists() else {}
    cost_per_agent = tel.get("cost_per_agent") or {}
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
        "shape": (effort.get("shape") or {}).get("shape"),
        "effort": effort.get("effort"),
        "reviewers_activated": [r["agent"] for r in plan.get("reviewers") or []],
        "published_findings": len(findings),
        "open_blocking_findings": blocking,
        "relatorio": str(target.relative_to(dir_reports().parent.parent)),
        "tokens": tel.get("tokens"),
        "minutes": tel.get("minutes"),
        "harness_tokens": tel.get("harness_tokens"),
        "tokens_not_measured_because": tel.get("reason"),
        "cost_usd": tel.get("cost_usd"),
        "cost_not_measured_because": tel.get("cost_not_measured_because") or tel.get("reason"),
        "telemetry_source": tel.get("source") or ("workflow" if tel.get("tokens") is not None else None),
        "run_id": tel.get("run_id"),
        "agents": tel.get("agents"),
        "cost_per_agent": cost_per_agent,
        # The two agents whose cost scales with something other than the diff: the
        # verifier with the number of blocking findings, the planner with the number of
        # unassessable classes. Broken out so a change to either lever is measurable.
        "verifier_tokens": _agent_tokens(cost_per_agent, "finding-verifier"),
        "planner_tokens": _agent_tokens(cost_per_agent, "manual-verification-planner"),
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
        if any(r.get("type") == "author_fix" for r in ver["routes"]):
            print("\n" + t("review.way_back_fix", package=package))
    print(t("review.freshness_note", head=man["head_sha"][:12], repo=man["repo"],
            origin=man["origin"]))
    print(t("review.never", **vocab))
    unmark()
    return 0


def freshness(args) -> int:
    """Whether the newest concluded review of an origin still speaks for its tip.

    Deterministic: `runs.jsonl` says what the last review measured and concluded; git
    says what the branch points at now. Exit 0 only when the two agree AND the verdict
    was APPROVED. Born from a delivery where two commits — a batch fix and a refactor —
    landed after the only review and carried four defects into the proposal: a verdict
    speaks for the commits it measured, never for the ones that came after it.
    """
    newest = None
    runs = dir_reports() / "runs.jsonl"
    if runs.exists():
        for raw in runs.read_text(encoding="utf-8").splitlines():
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if line.get("repo") == args.repo and line.get("origin") == args.origin:
                newest = line     # append-only file: the last match is the newest
    if newest is None:
        print(t("review.freshness_none", repo=args.repo, origin=args.origin))
        return 1

    repo_dir = dir_repositories() / args.repo
    head = None
    for ref in (args.origin, f"refs/remotes/origin/{args.origin}"):
        probe = subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "--verify",
                                "--quiet", ref], capture_output=True, text=True)
        if probe.returncode == 0:
            head = probe.stdout.strip()
            break
    if head is None:
        print(t("review.freshness_no_branch", origin=args.origin, repo=str(repo_dir)))
        return 1

    if head != newest["head_sha"]:
        count = subprocess.run(["git", "-C", str(repo_dir), "rev-list", "--count",
                                f"{newest['head_sha']}..{head}"],
                               capture_output=True, text=True)
        n = count.stdout.strip() if count.returncode == 0 else "?"
        print(t("review.freshness_stale", n=n, reviewed=newest["head_sha"][:12],
                head=head[:12], date=newest["date"]))
        return 1
    if newest.get("verdict") != "APPROVED":
        print(t("review.freshness_open", verdict=newest.get("verdict"),
                date=newest["date"]))
        return 1
    print(t("review.freshness_ok", head=head[:12], date=newest["date"]))
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
    a.add_argument("--delivery", help=t("review.help_delivery"))
    a.add_argument("--base", help=t("review.help_base"))

    b = sub.add_parser("conclude")
    b.add_argument("--package", required=True)
    b.add_argument("--workflow-result", help=t("review.help_workflow_result"))
    b.add_argument("--findings")
    b.add_argument("--scripts-manual")
    b.add_argument("--conventions", help=t("review.help_conventions"))
    b.add_argument("--coverage", help=t("review.help_coverage"))
    b.add_argument("--criteria", help=t("review.help_criteria"))
    b.add_argument("--advisory", action="store_true")

    sub.add_parser("release", help=t("review.help_release"))

    c = sub.add_parser("freshness", help=t("review.help_freshness"))
    c.add_argument("--repo", required=True)
    c.add_argument("--origin", required=True)

    args = p.parse_args()
    hint_config(getattr(args, "package", None), getattr(args, "item_file", None),
                getattr(args, "delivery", None), getattr(args, "workflow_result", None))
    if args.command == "release":
        unmark()
        print(t("review.marker_removed", path=marker()))
        return 0
    try:
        if args.command == "freshness":
            return freshness(args)
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
