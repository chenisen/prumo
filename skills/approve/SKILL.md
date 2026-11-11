---
name: approve
description: Approves a delivery's plan — the file deliver.py plan rendered from the measurement — so the writer may start. Writes approval.json with the person's name and the digest of the plan it approves; a plan whose measurement changes afterwards is unapproved again. Closed to the model — only a person types it, so the name is a person's. Not for approving a review's verdict, which is the person's act on the proposal.
argument-hint: "<delivery package> [--by <name>]"
disable-model-invocation: true
---

# Approve a plan

One command, one file:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/deliver.py approve --package <delivery package> [--by <name>]
```

`--by` defaults to `TASK_REVIEW_RESPONSAVEL` in the environment, then `owner` in
`prumo.yml`. With neither, the command refuses: an approval with no name is trust with
no owner.

What it writes: `approval.json` beside `plan.json`, carrying the name, the date and the
digest of the measurement the plan was rendered from. What reads it: the guard, which
refuses every write while a required approval is missing; `deliver.py conclude`, which
blocks a delivery whose shape asks a gate and has none; and `run.py next`, which turns
the `plan` stop into the write stage.

Read `plan.md` first. It is short on purpose.
