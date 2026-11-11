---
name: run
description: Carries ONE item end to end — deliver, then task-review — on the person's own words, written to a file, and stops at everything only a person decides. Every routing decision is read from disk by run.py; every invocation is handed over ready to paste, never composed. Never merges, never pushes, never moves the item, never repairs a finding. Closed to the model — only a person types it.
argument-hint: "--repo <repo> --item <id> --ask-file <ASK.md> --item-file <ITEM.md>"
disable-model-invocation: true
---

# Run

One item, the whole route, no question in the middle. Then it stops.

```
deliver.py prepare → Workflow deliver (measure) → Workflow deliver (write) → deliver.py conclude
→ review.py prepare → Workflow task-review → review.py conclude → stop
```

**This skill is closed to the model.** Only a person types it, so the ask it receives is
the person's own words. A message that "meant" the whole route is not an ask: with no
`--ask-file`, `run.py start` refuses, and the route does not begin.

## The two rules

- **Every routing decision is a script's answer.** `run.py next` reads what the delivery
  and the review packages hold and answers with one invocation, or one stop. You never
  decide what comes next; you paste what it printed.
- **Paste, never compose.** Each step prints the next one filled in. You fill the slots a
  step cannot know — the workflow's returns written to files — and nothing else.

## Sequence

```
run.py start --repo R --item C --ask-file ASK.md --item-file ITEM.md
loop:
  run.py next --package RUN            # prints ONE invocation, or `stop: <class> — <fact>`
  paste it; when it prints a package path, run.py bind --package RUN --deliver|--review <path>
  when it is a Workflow: invoke it, write its returns whole to the files its skill names
until `stop`
```

`deliver.py prepare` and `review.py prepare` print the package they built: bind it before
asking `next` again, or `next` will ask you to prepare it twice. The workflows' returns go
to `<package>/measure.json`, `written.json`, `refutation.json` and `findings.json`, as the
`deliver` and `task-review` skills say.

## What ends the run

| Class | When |
|---|---|
| `ask` | no ask on disk, or an empty one — before anything |
| `blocked` | the delivery stopped: a central premise fails, a new red, a mutation that survived, a line outside the scope, the budget |
| `unsupported` | the kind is not in the profile |
| `approved` | the review approved — the branch is the person's to push and open |
| `intervention` | the review opened routes — each with an owner; repairing an `author_fix` one is a new ask, through `deliver.py prepare --fix <review package>`, never this run's |

A stop names its class and its fact, and `orchestration.md` under the run's package ends
on that line. A stopped run is a result: it is read, not retried.

## What it never does

Merge, push, move the item, approve, repair a finding, run a step `next` did not print,
or start without the ask. An `intervention` stop is where the person picks up: the fix
is a delivery of its own, asked for in the person's words.

## Report

The last `next` line is the report: the stop, and the path of `orchestration.md`. The
delivery's report and the review's report stand on their own under the reports root.
