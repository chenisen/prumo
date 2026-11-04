---
name: task-review
description: Reviews a whole delivery as one package — the item that asked for it, the context it was built in and the code it produced — and closes with a two-valued verdict, APPROVED or INTERVENTION REQUIRED. The method is agnostic of repository, of stack and of tool: everything that is a fact about a project comes from its profile under the configured profiles directory. Approving is the ceiling: it never merges and it never moves the item. Use when the request names a delivery — item, proposal, branch or diff — and asks for a review, an analysis or a check against what was asked for.
---

# Delivery review

Deterministic backbone, agentic leaves. **Code decides what is a rule; the LLM decides what
is a judgement.** Scripts resolve, triage and judge; read-only agents give an opinion, each
one on a single question; a sceptical verifier confirms before the judge decides.

## The rule that governs this skill

**The method knows no repository.** No path, table, collection, tool, item or convention
appears here. All of that comes from the **profile** under `<profiles>/<repo>.yml`. If you
are about to write the name of a repository in this file, stop: the place for it is the
profile.

And **what could not be assessed is declared.** A guardrail has three states — `fired`,
`did_not_fire`, `not_assessable` — because silence does not count as the absence of risk.

## The three ledgers

| Ledger | What it is | Where |
|---|---|---|
| **1. What was asked for** | acceptance criteria and what the item put out of scope | the item |
| **2. What was specified and proven** | the proof registry the repo declares | `proof_registry` in the profile; `type: absent` is a valid answer, and it appears in the report |
| **3. What the project has already decided about itself** | conventions, architecture, precedents | `conventions` in the profile — **pointers, read at runtime** |

**Mandatory reading is a gate, and it sits BEFORE everything else.** The review does not
start without the **item** and — when the origin is a proposal — without the **proposal's
description**. No Python script reaches the tracker or the forge's API: the harness is
what reaches them, so the reading is *declared by whoever did it*, handing over the file:

```
review.py prepare --repo R --proposal N --item C
                  --item-file ITEM.md [--proposal-file DESC.md]
```

A file that is missing, or present and empty, **does not count as read** — the difference
between "there is no criterion" and "I could not read it" is the whole point, and a blank
file erases it. With no forge token in the environment and no `--proposal-file`, the
preflight raises an impediment and `prepare` stops, naming the remedy: **ask for the
connection, do not work around it.** Reviewing a diff without the item is guessing at what
was asked for; judging the coherence of the three stories with two of them missing produces
a report shaped like a complete one and partial in content — the worst result this method
can give.

A loose branch or a stray diff has no proposal description, and demanding one there would
be charging for something that does not exist: the description gate applies only to origin
`mr`. The item gate applies to every origin.

**Whoever is running the review declares themselves through the environment, not through
the profile.** A profile is versioned and shared; writing a name into it would make every
review by everybody come out signed by the first person who confirmed it. That is why the
profile writes the token `${RESPONSAVEL}` into `confirmed_by` and into every `approver`,
and the harness swaps it for the value of **`TASK_REVIEW_RESPONSAVEL`** — the same
convention as `TASK_REVIEW_HOJE`. A `confirmed` profile with the token still unresolved is
an **impediment** at preflight: a `not_applicable` with no owner is trust with no owner.

If the item is readable but **carries no criterion at all**, say so; do not invent what it
asked for.

## What you do not know, you ask

The prumo refuses to assume a provider, a path or a language. The same rule governs the
person conducting the review: **what the request does not say and the disk does not
show, you ask — once, plainly, before starting.**

A review built on a guess produces a report shaped like a finished one. That is the worst
output this method has, because the reader cannot tell it apart from the real thing.

Ask when:

| You do not know | Ask for |
|---|---|
| which repository the delivery lives in | the repository name, as it appears under `paths.repositories` |
| which proposal | the proposal URL or its number |
| which item asked for it | the item URL or id — the `--item-file` has to come from somewhere |
| the base, when the profile does not name one and `origin/HEAD` does not resolve | the branch the delivery should be measured against |
| whether an unknown URL is a proposal or an item | which of the two it is |

Two cases deserve their own sentence:

**The project root.** `--repo R` resolves to `<repositories>/R`, and `paths.repositories`
comes from `prumo.yml`. If the name does not resolve, do **not** search the filesystem for
something that looks close: say which root was configured, list what is actually there,
and ask which one. A review of the wrong tree is indistinguishable from a review of the
right one until somebody reads the diff.

**A provider the configuration does not describe.** If the URL points at a host that is
not the configured forge — a GitHub URL on a GitLab installation — stop. Do not translate
it. Say what `prumo.yml` declares, say what the URL points at, and ask which is wrong.
Reviewing it anyway means the report speaks the wrong vocabulary and the ref pattern
fetches nothing.

**Ask once, with everything you need, and then proceed.** A review that stops four times
to ask one thing each costs more attention than it saves. Gather the gaps, ask them
together, and carry on.

## Sequence

Scripts under `${CLAUDE_PLUGIN_ROOT}/scripts/`, all Python 3 with no dependency beyond
`yaml` and `jsonschema`. The **ten agents travel with the method** — they are files of the
plugin itself, registered as `prumo:<name>`, and whoever installs it copies nothing into
`.claude/agents/`.

Two commands, because the workflow sits in the middle — a script does not invoke the
Workflow.

```
review.py prepare --repo R [--proposal N | --branch B] --item C
                  --item-file ITEM.md [--proposal-file DESC.md]   # F0 F1 F2 F3
Workflow({name: 'task-review', args: {...}})                      # F4 F5 F8
review.py conclude --package P --findings A [--scripts-manual R]  # F7 F9 F11
```

`prepare` prints the workflow invocation already assembled, with the activation plan.

| Phase | Part | What it produces |
|---|---|---|
| **F0** preflight | `preflight.py` | `capabilities.json` — what THIS environment can do |
| **F1** resolve | `resolver.py` | `manifest.json` — base, SHAs, item |
| **F2** package | `package.py` | `diff.patch`, `slices/`, `change.json`, `profile.yml` |
| **F2b** criteria | agent `criteria-extractor` | `criteria.json` |
| **F3** triage | `triage.py` | `classes.json`, `size.json`, `plan.json` |
| **F4, F5, F8** | the workflow | verified findings and manual scripts |
| **F6** evidence | `evidence.py --role suite --run` | `evidence.json` — **one round, only the profile's allowlist** |
| **F7** judge | `judge.py` | `verdict.json` |
| **F9** render | `render.py` | `report.md`, `item-comment.html` |
| **F10** publication | `reference/publication.md` | three acts, each with its own yes |
| **F11** record | `review.py conclude` + `telemetry.py` | the report under `<reports>/<repo>/`, `telemetry.json` in the package, and one line in `runs.jsonl` **with the measured cost** |

**The review judges what the delivery DID.** Two consequences, both of them in the harness:

- **A finding about code the change did not touch is not a finding of this review.** It
  leaves the count and the verdict, and goes to the *Observations on the pre-existing*
  section — neither erased, nor charged to the author. The reviewers already get the
  instruction not to raise it; `conclude` separates whatever slips through, by
  `attributable`. A gap in what the diff DELIVERED counts as this delivery's: a branch the
  new file ought to have covered and did not is the delivery's debt, because the delivery
  is the proof.
- **A class that asks about production does not fire on a proof file.** `production_only`
  marks A5, A6, B6 and B7: a delivery that only adds a test changes no business rule and
  inverts no dependency, and matching the name of an enum inside a test was costing a human
  decision about something that did not happen. A8 — the class that asks *about* tests — is
  not marked, deliberately. When a class goes quiet for that reason, the report **says
  the reason**.

**The item comment is a RECORD, not a report.** Fixed shape, generated by `render.py` out
of the package, identical on every run — verdict, proposal, the two SHAs, a **Measured**
block (suite, risk classes, acceptance criteria, size, findings), the path to the report,
and the line saying that approving does not merge. When the verdict is INTERVENTION,
**Open routes** goes in as well, with owners. Nothing else: a narrative of what the review
knocked down, pending work in prose and a suggestion for the next step **decide nothing on
the item** and drown what does decide — whoever wants the reasoning opens the report. The
format has a size ceiling and is pinned in `tests/test_item_comment.py`; a new field that
does not fit belongs to the report, not to the comment.

**An empty diff is a stop**, not a clean review: with no file there is nothing to review.

While the review runs, `review.py` creates `.claude/.task-review-ativo`, and the hook uses
that marker to block push, commit and moving the item **only while the review is
running** — outside it the workspace carries on as normal, and the skills that legitimately
push or move an item keep working. Merge is blocked always. A marker leaked by a crash
expires in 4h, and `review.py release` removes it by hand.

Across loops, `delta.py` decides the mode before F2 — **re-judgement** when only evidence
arrived (no reviewer runs), **delta** when a push arrived, **full** the first time round.
`closure.py` reads the item's comments and produces `resolutions.json`.

## Profile

| Situation | What happens |
|---|---|
| `status: confirmed` | normal operation |
| `draft` or absent | the review **runs in full**; the judge opens `missing_profile` with origin `project` — declared in the report, **outside the verdict** |
| advisory mode | runs with no profile, **issues no verdict**, publishes nothing |

Repository with no profile: run `derive_profile.py <repo>`, hand over the draft and the list
of what it could not fill in. **Confirming is a person's act**, once per repository.

Confirming the profile is still worth a lot: with no anchor, half the classes come out
`not_assessable` and the review sees less. What changed on 2026-11-03 is that this cost
falls on whoever maintains the repository, not on whoever wrote the delivery.

## The verdict — the rule

Binary, and there is only one rule:

> **APPROVED ⟺**
> 1. the code follows the project's **standards and good practice**;
> 2. it breaks no **business rule**;
> 3. it **covers what the item asked for**, with the necessary evidence;
> 4. no **manual verification** is left to do;
> 5. and the four above were actually **measured**.

Any gate closed means INTERVENTION REQUIRED. The report opens with that five-line table, and
only then with the routes — the route says *who does what*, the gate says *which condition
failed*. Two routes serve more than one gate (`author_fix` takes both a finding and an unmet
criterion), so the gate is declared in `judge.py` at the point of use, never deduced from
the route's type.

**The fifth gate measures the REVIEW, not the repository.** "Not assessed" still does not
mean "no risk" — but whose debt it is decides whether it holds the proposal. Missing
mandatory reading brings the verdict down, because with no item there is nothing to judge. A
draft profile and a guardrail with no tool **do not bring it down**: the delivery did not
cause them. They come out declared in `project_pending` and in the *PROJECT pending items*
section of the report, with owners. Decided on 2026-11-03.

**This skill asks a single question: is what the item asked for delivered in this
proposal?** Every route carries `origin` — `delivery` or `project` — and only one of
origin `delivery` closes a gate and becomes INTERVENTION. It is the same principle that already
governs `attributable` on a finding and `red_not_attributable` on the suite, now applied to
the route: a repository whose profile nobody finished would never approve any delivery at
all, and the verdict would stop telling a good delivery from a bad one.

What still holds the proposal: an attributable, blocking finding, a §A class fired, an item
criterion unmet or with no record, a §B class fired, and empty mandatory reading.

**What does NOT go into the rule:** a suite red the delivery did not cause. A pre-existing
baseline is the repository's debt, goes into the report's summary and does not hold the
proposal — otherwise a repo whose trunk carries reds would never approve any delivery at
all, and the verdict would stop discriminating. Decided on 2026-10-29.

**The suite holds a proposal in three cases, and annotates in every other.** Extended on
2026-11-04, after a delivery of green tests was held by a base whose environment was not
set up:

1. **it does not run at all** (`broken`) — a repository where nothing executes proves
   nothing about any delivery. A suite that runs *in parts* is not this: a default
   invocation that aborts on a stale testsuite entry, while every selection runs, is
   `red_not_attributable` plus a note;
2. **a test over what this delivery wrote is red** (`red_attributable`);
3. **what the delivery wrote has no test over it** (`delivery_untested`) — unless the
   item's whole subject IS the proof, which `item_is_coverage_debt` declares: a coverage
   or technical-debt item delivers tests over code that was already there, and charging
   it for the absence it was opened to fix is charging it twice.

Everything else — a red baseline, a runner this environment lacks, a pipeline nobody
read — is an annotation with an owner, and **the proposal is approved if what the item
asked for is delivered**. A criterion of the item that only the suite decides follows the
suite's state, never the absence of a human record: whoever reads the item marks it
`decided_by: suite`, and the judge stops charging it a second time at gate 3.

The routes and their owners are in `judge.py`; the classes, in `reference/classes.md`.

**No §A finding proposes a fix** — the schema refuses a non-null `form`. How to resolve it
is a person's call.

**A defect the change did not cause** goes out with `attributable: false`: it appears in the
report and does not hold the proposal.

## What this skill never does

- **Merge.** The `review-guard.sh` hook blocks it, and the bot account does not have the
  right.
- **Move the item.** A `deny` rule in `permissions`.
- **Write into the target repository.** Only under `<reports>/<repo>/`.
- **Publish without authorisation.** The item comment, the proposal comment and the Approve
  are three external acts, each with **its own** explicit yes.
- **Fix.** Whoever fixes is the author.
- **Approve on its own when it could not assess.** See the third state.

## References

- `reference/classes.md` — the 18 classes, with no anchors
- `reference/publication.md` — the three external acts and the tracker's HTML subset
- `reference/playbooks.md` — manual verification scripts
- `reference/ci-gitlab.md` — the job that runs this on `merge_request_event`
- `schemas/` — finding, triaged class, criterion, verdict, manual script
- `schemas/profile.schema.yml` — the profile's contract

Tests: `cd ${CLAUDE_PLUGIN_ROOT}/scripts && python3 -m unittest discover -s tests`.

## Cost

The cost of each review is **measured, never estimated**: `telemetry.py` reads the record
the Workflow writes and the per-agent transcripts, and sums what the API reported in
`usage`. `conclude` calls it by itself, and `runs.jsonl` then carries `tokens`, `minutes`,
`lines_changed` and `cost_per_agent`. `metrics.py` aggregates: the median per run, **tokens
per changed line** — the only number that compares reviews of different sizes — and the
spend per agent type across the history.

Two totals, on purpose. `harness_tokens` is the Workflow's `totalTokens`;
`billable_tokens` is the sum of every `usage` component of every request. They diverge
widely (835k against 10.8M on the first measurement), and it is the breakdown —
`cache_read` dominates — that says where to cut.

**With no locatable transcript, `tokens` comes out `null` with a reason.** A zero would make
an expensive review look cheap, and `metrics.py` publishes `runs_without_measurement`
alongside the median so that the median is not read as the whole.

The two levers that rule the cost, measured:

- **the slice**, which is the input payload of every new context. Only `scope-coherence`
  reads `complete`; the rest read `code-and-tests` or `code-and-config`. A reviewer's slice
  that `package.py` does not write is silent empty reading — there is a test holding that
  coupling.
- **the number of requests per agent**, which is what multiplies `cache_read`. On the second
  measurement, four `finding-verifier` runs cost 10.4M of the run's 30M: verification
  fan-out is the axis that grows most on a bad delivery.
