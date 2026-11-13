---
name: run
description: Carries ONE item end to end — deliver, then task-review, then the evidence — on the person's own words, written to a file, and stops at everything only a person decides. Every routing decision is read from disk by run.py; every invocation is handed over ready to paste, never composed. Never merges, never pushes, never moves the item, never repairs a finding. Closed to the model — only a person types it.
argument-hint: "--repo <repo> --item <id> --ask-file <ASK.md> --item-file <ITEM.md> [--shape <shape>]"
disable-model-invocation: true
---

# Run

One item, the whole route, no question in the middle. Then it stops.

```
an item with no gate:
  deliver.py prepare → Workflow deliver (stage 'deliver') → deliver.py conclude
  → review.py prepare → Workflow task-review → review.py conclude
  → [approved: the evidence, once] → deliver.py release → stop

a shape a person approves:
  prepare → Workflow (stage 'measure') → deliver.py plan → [a person approves]
  → Workflow (stage 'write') → conclude → the review → …

a story:  prepare → Workflow (stage 'design') → deliver.py plan --design → [a person approves]
          → Workflow (stage 'tasks'): every task written and committed, in order
          → deliver.py conclude, once → the review of the branch, once → …
an epic:  prepare → Workflow (stage 'design') → deliver.py plan --design → [a person approves] → stop: decomposed
```

**This skill is closed to the model.** Only a person types it, so the ask it receives is
the person's own words. A message that "meant" the whole route is not an ask: with no
`--ask-file`, `run.py start` refuses, and the route does not begin.

## The two rules

- **Every routing decision is a script's answer.** `run.py next` reads what the delivery
  and the review packages hold and answers with one invocation, or one stop. You never
  decide what comes next; you paste what it printed.
- **Paste, never compose.** A workflow step is the package's `workflow-args.json` plus the
  stage `next` named — the scripts keep that file filled. Its return is saved, whole, at
  the path the step names. Nothing else is yours to write.

## Sequence

```
run.py start --repo R --item C --ask-file ASK.md --item-file ITEM.md [--shape S]
loop:
  run.py next --package RUN            # prints ONE invocation, or `stop: <class> — <fact>`
  paste it; when it prints a package path, run.py bind --package RUN --deliver|--review <path>
  when it is a Workflow: invoke it, save its return at the path the step names
until `stop`
```

`deliver.py prepare` and `review.py prepare` print the package they built: bind it before
asking `next` again, or `next` will ask you to prepare it twice.

## What ends the run

| Class | When |
|---|---|
| `ask` | no ask on disk, or an empty one — before anything |
| `decomposed` | an epic: the plan holds its stories, for a person to open as items; nothing is delivered |
| `plan` | the shape asks a person's approval of the plan and has none that matches it as it stands — `/prumo:approve <package>`, then `next` again |
| `blocked` | the delivery stopped: a central premise fails, the writer stopped, a task was not written, a new red, a line outside the scope, the budget |
| `unsupported` | the kind is not in the profile |
| `approved` | the review approved, the evidence is written — the branch is the person's to push and open |
| `intervention` | the review opened routes — each with an owner; repairing an `author_fix` one is a new ask, through `deliver.py prepare --fix <review package>`, never this run's |

A stop names its class and its fact, and `orchestration.md` under the run's package ends
on that line. A stopped run is a result: it is read, not retried.

## The evidence, on this route

Once, after the review approved, while the worktree still stands: the `evidence` stage
when the profile declares how a change is shown, then `deliver.py evidence`. An
intervention asks for none — there is nothing finished to illustrate. Nothing before
the verdict raises an environment to show the change.

## What it never does

Merge, push, move the item, approve, repair a finding, run a step `next` did not print,
or start without the ask. An `intervention` stop is where the person picks up: the fix
is a delivery of its own, asked for in the person's words.

## Report

The last `next` line is the report: the stop, and the path of `orchestration.md`. The
delivery's report, its evidence and the review's report stand on their own under the
reports root.
