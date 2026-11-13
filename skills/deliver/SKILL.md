---
name: deliver
description: Delivers ONE item as the smallest change that pays for it — cuts the item its own worktree, has an agent with a shell write the change and run the tests it touches until they are green, checks what landed once (by its size: the test files it touched, or the whole suite), commits with a pathspec, and hands the branch to task-review. Evidence is collected once, after the item is done. Never approves its own work, never pushes, never merges, never moves the item. Use when the request names an item and asks to deliver, fix, implement or pay it. Not for reviewing (that is task-review).
---

# Deliver

One item, one worktree, one branch. Then `task-review`. **Who writes does not approve.**

The question this capability answers:

> **What is the smallest change that delivers this item, and what proves it?**

## The four things that keep it fast and honest

1. **The agents execute.** The writer has a shell: it edits the code itself, runs the
   tests it touches, reads what they print and fixes what it wrote, until it is green.
   No script is written to write code. The conductor runs three things per item — one
   prepare, one workflow call, one conclude — and composes nothing in between.
2. **Every item has its own worktree.** The person's checkout is never touched, and
   several items are delivered in one repository at once, each judged by its own marker.
3. **The size decides what is paid.** A small change is checked by the test files it
   touched and read by no sceptic; a larger one runs the whole suite — **once**. The
   base is never measured up front: only when the branch turns something red.
4. **Evidence comes last.** What the tests and the checks hold is the quality. Showing
   the change — an environment raised, a screen captured — happens once per item, after
   its last task is delivered, and decides nothing.

## Rules

Each rule has a test that fails when it is broken (`scripts/tests/test_deliver.py`,
`test_deliver_backbone.py`, `test_deliver_hook.py`, `test_run.py`).

| # | Rule | Held by |
|---|---|---|
| R1 | **One item per run.** An item asking two things stops and asks for the cut. | script |
| R2 | **Measure before touching.** Every premise of the item becomes `holds`, `fails` or `not_assessable`, with `file:line`. A story is measured by its design, which a person approves. | `premise-surveyor`, `task-planner` |
| R3 | **A failed premise records, it does not erase.** A central premise that fails is a stop, before anything is written; a writer that finds the code other than assumed returns `blocked`. | script + writer |
| R4 | **Red before green.** The writer sees its test red, then green. `conclude` reads it once more over the base's production files, the changed test files only: nothing red there is a test that passes without the change, and blocks. A coverage gap is proven by the mutant its test kills. | writer runs, script checks once |
| R5 | **Smallest diff that pays the item.** No drive-by refactor, rename or whole-file formatting. | `scope_guard` over the diff |
| R6 | **The size decides the suite.** Small: the test files the change touched, plus the ones the measurement named beside them. Otherwise: the whole suite, once. A wide path is never small. | `shape.size_of` |
| R7 | **Baseline by name, and only when needed.** A green branch owes no baseline. A red one is compared by name against the base — measured then, or reused from a package that measured the same tree. A new red stops. | script |
| R8 | **Predicted versus executed.** A suite that did not finish is not green. | script |
| R9 | **One commit per step**, in the profile's `commit_format`. Never `git add -A`; always a pathspec. A story's writer commits its own task. | hook + script |
| R10 | **Budget.** `max_files` and `max_lines` from the profile, scaled by the shape. Over it stops; a waiver in the profile lifts it, with a name and an expiry. | script |
| R11 | **Stopping is a result.** `blocked`, with a reason and the next owner, weighs as much as `delivered`. | judge |
| R12 | **Evidence illustrates, once, at the end.** No environment for showing a change is raised while the work is open; nothing the evidence finds changes the state. | route + `evidence-collector` |

And the three rules every capability of the prumo holds: **the method knows no
repository** (suite, base, container, commit convention, budget, sizes — all from the
profile); **the method knows no provider** (`prumo.yml`); **what could not be measured
is declared**.

## What it never does

- push, merge, move the item, approve;
- land on a protected branch (`forge.protected_branches`): the delivery is cut *from* one, never *on* one;
- write outside the worktree, or outside the roots its kind may touch;
- run the whole suite more than once per item, or at all for a small one;
- raise an environment to *show* the change before the item is delivered;
- write a script that writes code;
- invent a premise, a kind or a profile block that is not there.

The hook holds the first three from outside while the delivery's marker exists — one per
delivery, in `.claude/.prumo-deliver-ativo.d/` — and only inside THAT delivery's worktree
(pushing its branch, from anywhere). The rules above hold them from inside. Both,
because the model can err and an item can carry text that tries.

## The shape and the size

The item's **shape** — `debt`, `bug`, `hotfix`, `refactor`, `story`, `epic` — is the
tracker's type mapped in `prumo.yml` (`--type`), or `--shape`; never inferred from the
text. It decides whether a person approves a plan before a line is written, which kinds
may deliver it and how much diff it buys. `reference/shapes.md` is the table.

The **size** is what the change touches, computed — `small`, `standard`, `large` — from
the files and lines of the diff, the profile's `delivery.small` (3 files and 80 lines
when absent) and its `delivery.wide_paths`. It decides the sceptic and the suite:

| Size | When | Sceptic | What `conclude` runs |
|---|---|---|---|
| `small` | a shape with no gate, inside `delivery.small`, no wide path | no | the test files the change touched + `related_tests` |
| `standard` | everything else that is one item | once | the whole suite, once |
| `large` | a story | no — the review reads the branch | the whole suite, once, over all its tasks |

A profile with no `checks.suite.files_command` cannot run named test files: every
delivery there runs the whole suite, and the fix is one line in the profile.

## Inputs

A missing input is a stop that names everything missing at once:

| You do not know | Ask for |
|---|---|
| which repository | its name under `paths.repositories` |
| which item | the item URL or id, and the `--item-file` read from it |
| the shape | `--type <tracker type name>` mapped in `prumo.yml`, or `--shape` |
| the kind, when the item does not say | one of `reference/delivery-kinds.md`, or `unsupported` |
| the base, when the profile names none | the branch to measure against |

A profile without a `delivery:` block is `not_assessable`, and the stop hands
`derive_profile.py <repo> --delivery` ready to paste. Never a default.

## Sequence

Scripts under `${CLAUDE_PLUGIN_ROOT}/scripts/`. Each step prints the next; the object the
workflow takes is always `<package>/workflow-args.json` plus a `stage` — pasted, never
composed. Save each workflow return, whole, to the file the step names.

**An item with no gate** (`debt`, `bug`, a fix) — three steps:

```
deliver.py prepare --repo R --item C --item-file ITEM.md (--type T | --shape S) [--kind K] [--branch B]
    resolves the shape and the base, cuts the branch in its own worktree, marks. Runs no suite.
Workflow({name: 'prumo:deliver', args: {...workflow-args.json, stage: 'deliver'}})
    premise-surveyor measures → the size is read → change-writer writes and runs its tests
    → proof-skeptic, unless the item is small          → save the return to <package>/workflow-result.json
deliver.py conclude --package P --result P/workflow-result.json
    scope and budget over the diff · the proof over the changed test files · the suite
    the size asks · lint · judge · one commit · record and report · marker off
```

**A shape a person approves** (`hotfix`, `refactor`) — the gate sits between two stages:

```
prepare → Workflow stage 'measure' → deliver.py plan --package P --measure <its return>
→ /prumo:approve P, by a person → Workflow stage 'write' → deliver.py conclude --package P --result <its return>
```

**A story** — designed, approved, written task by task in one call, checked once:

```
prepare --shape story → Workflow stage 'design' → deliver.py plan --package P --design <its return>
→ /prumo:approve P → Workflow stage 'tasks' → deliver.py conclude --package P --result <its return>
```

**After the item is delivered** — and, on the `run` route, after the review approved:

```
Workflow stage 'evidence'      only when the profile declares `delivery.evidence`
deliver.py evidence --package P [--collected <its return>]
deliver.py release  --package P      the worktree goes, the branch stays
```

`plan` and `conclude` write what the next stage needs into `workflow-args.json` — the
measurement, the design, the tasks already written, a blocked attempt's reason — so the
next invocation is the same object with another stage.

**What the writer runs is the profile's.** `prepare` resolves `checks.suite.files_command`
and `checks.lint.command` for the worktree and hands them over; nothing else is run by
an agent, and the whole suite is the script's. A wrapper can swallow an exit code, so
every result is read from the output (`failure_pattern`, `red_name_pattern`). A check
runs for 900 s unless the profile declares its own `timeout_seconds`.

**Where it runs is a worktree**, `.worktrees/<branch>` inside the repository. `{workdir}`
in a check's command names that tree as the executor sees it, through
`delivery.path_map` when the suite runs in a container; what git does not hold arrives
by `worktree_setup`, one plain command per line, nothing chained. A profile whose suite
cannot reach a worktree says `worktree: false`, and each prepare says so.

## A blocked attempt, written again

A `blocked` delivery leaves the tree as it is, and `conclude` leaves in
`workflow-args.json` what a second write needs: the measurement and `previous` — the
judge's reason and what the run printed about each new red. Invoke the workflow again
with `stage: 'write'` (a story: `stage: 'tasks'`, which skips the tasks already
written), save the return, and `conclude` again on the same package. With a writer that
runs its own tests this is rare: it is what is left when the whole suite finds a red the
touched tests could not.

What a second attempt wrote is what the tree holds: `conclude` keeps the writer's list
and adds every file the measurement named that differs from the base.

## The record

`delivery.json`, held to `schemas/delivery.schema.json`. `deliver_judge()` reads it and
answers `state`, `reason` and `owner`; same record, same answer:

- `unsupported` — the kind is not in the profile's `kinds`;
- `blocked` — a central premise fails; the plan's gate has no approval; the writer
  stopped; a story's task was not written; a new red by name; a red branch whose base
  could not be measured; a suite that did not finish; nothing red on the base under the
  new test; a mutation that survived, or was too wide; the sceptic's `does_not_prove`;
  a red lint; a line outside the scope; the budget exceeded with no waiver in force;
- `delivered` — otherwise.

The record says its `size`, the `scope` of the suite that ran (`focused` or `whole`),
how the base was read (`not_needed`, `measured`, `reused`), and names every run the
script made as a file beside the package. `skipped` is mandatory: what the writer did
not check is written, even when empty. A red on the base read under another name than
the measurement's is `partial`: the proof stands and the report says which name.

## A story, an epic

A `story` is cut before it is written: the `design` stage runs the `task-planner`, which
returns the tasks — each with its files, its falsifiable criteria, its proving tests and
what it builds on — and `deliver.py plan --design` renders them as the plan a person
approves. Then **one** workflow call, `stage: 'tasks'`, writes them in order: each task
has one writer, which reads the tasks before it, writes, sees its tests green and
**commits its own task** before the next begins. A writer that finds the code other
than the design assumed returns `blocked`, and the tasks after it wait.

Nothing is measured, proven or reported per task. One `conclude` checks the whole
branch — scope, budget, the red on the base, the whole suite, lint — and one review
reads it.

An `epic` is designed and never cut: no branch, no suite, no marker. Its plan holds the
stories it decomposes into, each an item for the tracker, and the plan is where it ends.

## A fix, a widened item

```
deliver.py prepare --repo R --fix <review package> [--branch B]
deliver.py prepare --repo R --item C --item-file ITEM.md --shape S --branch B --continue
```

A fix delivers the `author_fix` routes a review opened, written from the findings behind
them, on the review's own branch. A widened item is a new ask on a branch that already
holds a delivery. In both the branch's head is the base, the shape has no gate, and the
sequence is the three steps above.

## The evidence

`deliver.py evidence` writes `evidence.md` beside the delivery's report: the commits,
the files, the proof, the suite that ran. When the profile declares `delivery.evidence`
— how a change is shown to a person in that repository, what raises the environment,
what takes it down — the `evidence` stage runs the `evidence-collector` first: it raises
the environment **once**, takes one capture per acceptance criterion, takes it down, and
its captures join the file. What it could not show is written with the reason. None of
it changes the delivery's state: it illustrates what was done.

## The report

Short. One fact per line, each with an address. `✔` holds, `✘` fails, `?` not measured.
Code, commands, paths and errors verbatim. Full sentences only for a warning or an
irreversible act. It ends with the next command:

```
/prumo:task-review --repo R --branch <branch> --item C
```

Never the review itself. Reviewing is another step and another decision.

## Where the item comes from

An item of the tracker, or a route of origin `project` declared by an earlier
`task-review` and kept in `<knowledge>/<repo>.md`. In the second case the record says so.
