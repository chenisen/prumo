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

## Inputs

A missing input is a stop that names everything missing at once:

| You do not know | Ask for |
|---|---|
| which repository | its name under `paths.repositories` |
| which item | the item URL or id, and the `--item-file` read from it |
| the kind, when the item does not say | one of `reference/delivery-kinds.md`, or `unsupported` |
| the base, when the profile names none | the branch to measure against |

A profile without a `delivery:` block is `not_assessable`, and the stop hands
`derive_profile.py <repo> --delivery` ready to paste. Never a default.

## Sequence

Scripts under `${CLAUDE_PLUGIN_ROOT}/scripts/`. Two commands, with the workflow between.

```
deliver.py prepare  --repo R --item C --item-file ITEM.md [--kind K] [--branch B]
    F0 preflight    git, the suite's tool, a clean tree, the profile's delivery block
    F1 resolve      remote base, SHA, the item, the kind
    F2 measure      premises → measure.json                     [agent: premise-surveyor]
    F3 baseline     reds on the base, by name → baseline.json
    F4 worktree     branch <branch_format> from the base, isolated worktree, marker on
Workflow({name: 'deliver', args: {...}})                        # prepare prints it filled
    F5 write        code and test, inside the named files        [agent: change-writer]
    F6 prove        red on base / mutation, suite, baseline      [script]
    F7 refute       tries to knock the proof down                [agent: proof-skeptic]
deliver.py conclude --package P
    F8 judge        delivered | blocked | unsupported            (pure function)
    F9 render       the record, the short report, the next command — publishes nothing
```

`prepare` stops at F2 when a central premise fails, and at F3 when the suite does not
finish on the base. `conclude` removes the marker.

**Who executes is the script**, through the profile's allowlist (`checks.suite.command`,
`delivery.mutation.command`). An agent runs nothing. A wrapper can swallow an exit code,
so the result is read from the output (`failure_pattern`, `red_name_pattern`).

## The record

`delivery.json`, held to `schemas/delivery.schema.json`. `deliver_judge()` reads it and
answers `state`, `reason` and `owner`; same record, same answer. The state is decided by
fixed rule:

- `unsupported` — the kind is not in the profile's `kinds`;
- `blocked` — a central premise fails; a new red by name; a line outside the scope; the
  budget exceeded with no waiver in force; a mutation that survived; a suite that did not
  finish; a test that was not red on the base when the kind demands it;
- `delivered` — otherwise.

`skipped` is mandatory: what the writer did not check is written, even when empty.

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
