---
name: deliver
description: Delivers ONE item as the smallest change that pays for it, with proof — measures the item's premises against the remote base, writes only inside the files the measurement named, proves red before green, compares the suite by name, commits with a pathspec, and hands the branch to task-review. Never approves its own work, never pushes, never merges, never moves the item. Use when the request names an item and asks to deliver, fix, implement or pay it. Not for reviewing (that is task-review).
---

# Deliver

One item, one branch, one proof. Then `task-review`. **Who writes does not approve.**

The question this capability answers:

> **What is the smallest change that delivers this item, and what proves it?**

## Rules

Each rule has a test that fails when it is broken (`scripts/tests/test_deliver.py`,
`test_deliver_hook.py`, `test_run.py`).

| # | Rule | Held by |
|---|---|---|
| R1 | **One item per run.** An item asking two things stops and asks for the cut. | script |
| R2 | **Measure before touching.** Every premise of the item becomes `holds`, `fails` or `not_assessable`, with `file:line`, against the **remote base** — never the checkout. | script + `premise-surveyor` |
| R3 | **A failed premise records, it does not erase.** Scope is a person's call: a central premise that fails is a stop, before anything is written. | script |
| R4 | **Red before green.** A behaviour change: the new test is red on the base and green on the branch. A coverage gap: a mutation by hand turns the guarded code wrong → the test goes red → the mutation is reverted. No red, no proof. | script runs, record keeps |
| R5 | **Smallest diff that pays the item.** No drive-by refactor, rename or whole-file formatting. A line outside the scope is a defect. | `scope_guard` over the diff |
| R6 | **Observable behaviour does not change** — unless the item says the behaviour IS the change. | the existing tests, by name |
| R7 | **Baseline by name.** The set of reds on the base against the set on the branch. A new red stops. An old red that vanished is recorded. | script |
| R8 | **Predicted versus executed.** A suite that did not finish is not green. | script |
| R9 | **One commit per step**, in the profile's `commit_format`. Never `git add -A`; always a pathspec. | hook + script |
| R10 | **Budget.** `max_files` and `max_lines` from the profile. Over it stops and proposes the split; a waiver in the profile lifts it, with a name and an expiry. | script |
| R11 | **Stopping is a result.** `blocked`, with a reason and the next owner, weighs as much as `delivered`. | judge |

And the three rules every capability of the prumo holds: **the method knows no
repository** (suite, base, container, commit convention, budget — all from the profile);
**the method knows no provider** (`prumo.yml`); **what could not be measured is declared**.

## What it never does

- push, merge, move the item, approve;
- write outside the worktree, or outside the files `measure.json` named plus the test file;
- touch the base branch, rewrite history, `git add -A`;
- invent a premise, a kind or a profile block that is not there.

The hook holds these from outside while `.claude/.prumo-deliver-ativo` exists; the rules
above hold them from inside. Both, because the model can err and an item can carry text
that tries.

## The shape decides the depth

Before anything is read, the item's **shape** — `debt`, `bug`, `hotfix`, `refactor`,
`story`, `epic` — decides how deep the plan goes, whether a person approves it before a
line is written, which kinds may deliver it, how much diff it buys and how hard the
review reads it. `reference/shapes.md` is the table. The shape is the tracker's type
mapped in `prumo.yml` (`--type`), or `--shape`; it is never inferred from the text, and
an item with no shape is a stop.

**The plan is a file.** After the measure stage, `deliver.py plan` writes `measure.json`,
`plan.md` — what was measured, what will be touched, what will prove it — and `plan.json`
with the shape, the depth and a digest of the measurement. A shape that asks a gate
waits there: `/prumo:approve <package>`, closed to the model, writes `approval.json`
with a person's name and that digest. The guard refuses every write and `conclude`
blocks for the person until it matches; a measurement that changes afterwards is
unapproved again.

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

Scripts under `${CLAUDE_PLUGIN_ROOT}/scripts/`. Two commands, with the workflow between.

```
deliver.py prepare  --repo R --item C --item-file ITEM.md (--type T | --shape S) [--kind K] [--branch B]
    F0 preflight    git, the suite's tool, the profile's delivery block
    F1 resolve      the shape, the kind it allows, the base (a hotfix: `delivery.hotfix_base`)
    F4 branch       <branch_format> from the base — a worktree, or the checkout if clean
    F3 baseline     the suite on the branch while it still IS the base; reds by name
    marker on
Workflow({name: 'deliver', args: {...workflow-args.json, stage: 'measure'}})
    F2 measure      premises, the files, the mutation           [agent: premise-surveyor]
deliver.py plan --package P --measure <the measure it returned>
    F2b plan        measure.json, plan.md, plan.json — the hook reads the files from there
    a shape with a gate stops here: /prumo:approve P, by a person
    a central premise that fails: skip the write stage; go to conclude
Workflow({name: 'deliver', args: {...workflow-args.json, stage: 'write', measure: {...}}})
    F5 write        the test (and, by kind, the code) inside the named files [agent: change-writer]
    F7 refute       tries to knock the proof down                       [agent: proof-skeptic]
→ write `written` to <package>/written.json and `refutation` to <package>/refutation.json
deliver.py conclude --package P --measure P/measure.json --written P/written.json
                    --refutation P/refutation.json
    F6 prove        scope guard, mutation by hand, the suite, baseline by name   [script]
    F8 judge        delivered | blocked | unsupported                 (pure function)
    F9 record       delivery.json, the short report, the commit, the next command
    marker off
```

The three agents travel with the plugin and answer by `<prefix><name>`. A checkout that is
not the installed plugin passes their prompts in `agent_prompts: {name: text}` instead, and
each runs as a plain subagent — wider tools than the agent file declares, so the guard is
what holds the writer there.

The three returns are written **as the workflow returned them**, whole: a hand-edited
measurement names files the surveyor never read, and the guard would let a write
through on the strength of an edit nobody measured.

`prepare` stops when the tree is dirty, the kind is not in the profile, or the suite does
not finish on the base. `conclude` commits only a delivery that stands; a blocked tree is
left as it is, for a person to read.

**Who executes is the script**, through the profile's allowlist (`checks.suite.command`,
`delivery.mutation.command`). An agent runs nothing. A wrapper can swallow an exit code,
so the result is read from the output (`failure_pattern`, `red_name_pattern`).

## The record

`delivery.json`, held to `schemas/delivery.schema.json`. `deliver_judge()` reads it and
answers `state`, `reason` and `owner`; same record, same answer. The state is decided by
fixed rule:

- `unsupported` — the kind is not in the profile's `kinds`;
- `blocked` — a central premise fails; the plan's gate has no approval; a new red by name; a line outside the scope; the
  budget exceeded with no waiver in force; a mutation that survived; the sceptic's
  `does_not_prove`; a suite that did not finish; a test that was not red on the base when
  the kind demands it;
- `delivered` — otherwise.

`skipped` is mandatory: what the writer did not check is written, even when empty.

Three more facts the record carries since 2.1: the profile's **lint** check over the
tree the writer left (`red` blocks); for each mutant, the tests the item says must
**stay green** under it (`red` there means the mutant is wider than the guard, and
blocks); and, for a `behaviour-change`, the **red on the base** — the base's production
files restored under the new test, the proving tests required red, the writer's files
put back, the suite run again and required green.

## A story, an epic

A `story` is cut before it is measured: the `design` stage of the workflow runs the
`task-planner`, which returns the tasks — each with its files, its falsifiable criteria,
its proving tests and what it builds on — and `deliver.py plan --design` renders them
as the plan a person approves. Then each task is a delivery of its own:

```
deliver.py prepare --repo R --story <story package> --task T1 --package <story package>/tasks/T1
```

Its item is the task as the design wrote it, its shape a bug's — reproduce, no gate,
because the person approved the whole plan — its branch the story's, its base that
branch's head. The surveyor confirms the design's files and proving tests against the
code and replaces nothing. When every task is delivered, `deliver.py story --package
<story package>` writes the story's own record from its tasks' — delivered when all
are, inside the shape's budget over every task — and the branch goes to the review once.

An `epic` is designed and never cut: no branch, no suite, no marker. Its plan holds the
stories it decomposes into, each an item for the tracker, and the plan is where it ends.

## A fix

```
deliver.py prepare --repo R --fix <review package> [--branch B]
```

The `author_fix` routes a review opened become the item, written from the findings
behind them; the kind is `behaviour-change`; the branch is the review's own, and its
head is the base — the red before the green is measured against what the review read.
The rest of the sequence is the same, and it ends where every delivery ends: in the
review's invocation.

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
