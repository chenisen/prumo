---
name: task-review
description: Reviews a whole delivery as one package — the item that asked for it, the context it was built in and the code it produced — and closes with a two-valued verdict, APPROVED or INTERVENTION REQUIRED. The method is agnostic of repository, of stack and of tool: everything that is a fact about a project comes from its profile under the configured profiles directory. Approving is the ceiling: it never merges and it never moves the item. Use when the request names a delivery — item, proposal, branch or diff — and asks for a review, an analysis or a check against what was asked for.
---

# Delivery review

One question: **is what the item asked for delivered in this proposal?** Scripts resolve,
triage and judge; read-only agents read, each on one ground; a sceptic reads again whatever
would hold the approval; the judge is code.

The method knows no repository. A path, a table, a tool or a convention is a fact of a
project and lives in its profile, `<profiles>/<repo>.yml` — never in this file.

## Conducting a review

Three steps. Scripts are under `${CLAUDE_PLUGIN_ROOT}/scripts/`.

```
review.py prepare --repo R [--proposal N | --branch B] --item C --item-file ITEM.md
                  [--proposal-file DESC.md] [--delivery PACKAGE] [--base REF] [--full]
Workflow({scriptPath: '<package>/workflow.js'})      # printed by prepare, ready to run
review.py conclude --package P --workflow-result W
```

- **Before `prepare`, have the item as a file** — description and comments. No script
  reaches the tracker or the forge: whoever conducts fetches the text and hands it over.
  An origin that is a proposal also needs its description (`--proposal-file`). A missing or
  empty file is a stop, not a guess: with no item there is nothing to judge.
- **What the request does not say and the disk does not show, ask — once, everything
  together, then proceed.** Which repository, which proposal, which item, which base when
  none resolves. Never search the filesystem for a repository that "looks close", and
  never translate a URL of a forge the installation did not declare.
- **`prepare`** refreshes the base from the remote, checks the reviewed head out on its own
  under the repository's `.worktrees/` (the agents read the code there), triages, and
  writes the workflow into the package with its arguments already inside. Run that file;
  there is nothing to paste.
- **`W`** is what the workflow returned, saved as a file — the harness's own output file
  for the task is accepted as it comes.
- **`--delivery PACKAGE`** when the prumo delivered the branch: the delivery's record and
  its captured runs travel with the review. The suite it measured on this head stands as
  the suite evidence, and the reviewers read what each mutant killed from the digest.
- **`--base REF`** when the branch was built on another line than the profile's base.
  Never edit the profile for one review.
- **`--advisory`** runs everything and issues no verdict: what an author runs before
  opening the proposal.

## Rounds

A review converges. The round is read from what was already concluded over the same
origin; nobody chooses it.

| Round | Who reads | What |
|---|---|---|
| 1 | every reviewer the triage activates | the whole delivery |
| 2 | the reviewers the push's own shape activates | only what the push changed since the reviewed head; what round 1 left open is re-checked at the new head |
| 3 on | no reviewer | only whether what is still open was fixed |

A finding a later round raises on code the push did not change is shown and holds
nothing: that code was read once, and a second reading that disagrees is the harness
varying, not the delivery changing. `--full` reads the whole delivery again at any round,
when a person asks for it. A `prepare` over a head already reviewed stops and says so.

## What holds the approval

> **APPROVED ⟺** (1) the code follows the project's standards and good practice;
> (2) it breaks no business rule; (3) it covers what the item asked for, with the
> necessary evidence; (4) what only QA can verify is handed to QA, with a script;
> (5) the four above were actually measured.

What closes a gate, and nothing else does:

- a finding that is blocking, attributable to the change, **confirmed** by the sceptic,
  and **with a basis** — see below;
- such a finding on a §A class: a person's decision, never a proposed fix;
- a criterion of the item not met, or with no record;
- a suite red by fault of the delivery, a suite that does not run at all, delivered
  behaviour with no test over it, a real conflict with the base;
- something for QA with no script written for it, an empty mandatory reading, a reviewer
  the triage activated that never answered.

What is declared and holds nothing: a class the triage fired with no finding on it, a
doubt the sceptic could not settle, a blocking label with no stated basis, a finding on
code already read, a pre-existing defect, a draft profile, a class with no anchor, a tool
this environment lacks, a red the base already carries, being behind the base. What only
the running system shows — a §B class, a criterion marked `decided_by: qa` — goes to QA,
after the approval, with the script the review wrote.

## What the reviewers are held to

Four rules travel in every reviewer's prompt, and the judge enforces what code can:

- **A blocking finding says where its demand comes from.** A criterion of the item, a rule
  the project wrote down — quoted, with its file; `conclude` looks the quote up, and a
  quote that is not there is no rule — or a defect: the code doing the wrong thing on a
  real path, as the sceptic read it. A demand with none of the three is a suggestion.
- **A class the triage fired is a question, not a finding.** The triage matched a path. It
  becomes a finding only when the change does, on that surface, what the item did not ask
  for. What the item itself asks for is a decision already taken. The one class that
  holds on its own is the one a tool measured — the secrets scan.
- **One bar for proof**, the same the delivery's own sceptic reads by: a criterion is
  proven when a test fails if the behaviour it states is removed. A test that could pin
  more is a suggestion.
- **The prose is written in the installation's language.**

The workflow then merges the same cause raised by more than one reviewer into one
finding, sends only the blocking ones to the sceptic, and returns everything else as
`unverified` — the reviewer's word, read once.

## Telling the person

The report opens with one screen: what holds the approval — each with its address, where
its demand comes from and what to do — what goes to QA, and the questions and suggestions
that hold nothing. Everything else is the annex. Say that screen to the person, in plain
words: never an internal id on its own, never a class or gate number as if they knew it.

Publishing is not part of the review. The item comment, the proposal comment and the
Approve are three external acts, each with its own explicit yes (`reference/publication.md`),
and each only after `review.py freshness` says the verdict still speaks for the branch.
A decision a person takes on a finding goes to `resolutions.json` in the package —
`closure.py` reads the item's comments for it — and is applied by running `judge.py` and
`render.py` again, never by running the review again.

## While a review runs

- `prepare` writes `.claude/.task-review-ativo`; the hook uses it to hold push, commit
  and moving the item until `conclude`. A marker left by a crash expires in four hours;
  `review.py release` removes it by hand.
- **A review speaks about its own item and nothing else.** `provenance.py` checks what the
  agents returned at `conclude`: another review's commit, another proposal or an item its
  own item never names stops the run. Do not hand-edit the ids — run that agent again.
- An empty diff is a stop, not a clean review.

## The profile and what the reviews learn

| Profile | What happens |
|---|---|
| `status: confirmed` | normal operation |
| `draft` or absent | the review runs in full; the gap is declared as the project's, outside the verdict |

No profile: `derive_profile.py <repo>` drafts one; confirming it is a person's act. Whoever
runs the review is named by the environment (`TASK_REVIEW_RESPONSAVEL`), never written
into the profile.

`knowledge.py` keeps, per repository and outside it, what two reviews saw: conventions the
code holds to, the files reviewers keep opening, declared debt, defects no delivery caused.
The reviewers get a capped reading of it as a shortcut; the sceptics do not; nothing in it
closes a gate, and no commit, proposal or item id survives into it.

## Cost

Measured, never estimated: `conclude` reads the workflow's own record and writes `tokens`,
`minutes` and the cost per agent to `telemetry.json` and `runs.jsonl`; `metrics.py`
aggregates. A cost that comes out `null` has a reason beside it — and is a bug to look
into, never a zero.

## What this skill never does

Merge. Move the item. Write into the target repository. Publish without a yes for that
act. Publish a verdict the branch has moved past. Fix what it found. Approve what it could
not assess.

## References

- `reference/classes.md` — the eighteen classes
- `reference/publication.md` — the three external acts and the tracker's HTML subset
- `reference/playbooks.md` — manual verification scripts
- `reference/forge-connection.md` — how the proposal's description reaches the review
- `reference/ci-gitlab.md` — running this on a merge request event
- `schemas/` — finding, criterion, verdict, manual script, profile

Tests: `cd ${CLAUDE_PLUGIN_ROOT}/scripts && python3 -m unittest discover -s tests`.
