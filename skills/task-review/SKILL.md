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
file erases it. With no way to read the description and no `--proposal-file`, the
preflight raises an impediment and `prepare` stops, naming the remedy **for the route
this installation declared** — `reference/forge-connection.md` has the steps per
provider. **Ask for the connection, do not work around it.** A token in the environment
is not a reading: it is how the harness FETCHES the text, which still arrives here as a
file. Reviewing a diff without the item is guessing at what
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
review.py conclude --package P --findings A [--scripts-manual R]
                   [--conventions C]                              # F7 F9 F11 F12
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
| **F12** knowledge | `knowledge.py` | `<knowledge>/<repo>.md` and `.json` — what the reviews keep learning about the REPOSITORY |
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

**The item comment is a RECORD of fixed shape, and it STANDS ON ITS OWN.** Generated by
`render.py` out of the package, identical on every run — verdict, proposal, the two SHAs,
**The rule** (the five gates, with what closed any that is shut), a **Measured** block
(suite, risk classes, acceptance criteria, size, findings), **Open routes** when the
verdict is INTERVENTION, the **acceptance criteria each with its record**, the
**project's pending items** with owners, and the line saying that approving does not
merge.

**No class id travels alone.** `A4, A7, A9` is the harness talking to itself: the person
reading the item has never seen the class catalogue and cannot look it up from there.
Every id goes in with the question it asks, resolved by `class_question()` in the
reader's language — the questions live in the catalogues, not as literals in
`common.py`, which is what used to print eighteen English sentences into a Portuguese
report.

**It never names a file.** Until 2026-11-04 it closed by printing the report's path, and
a path under the reports root is unreadable to everybody except whoever ran the review:
the person reading the item is on another machine, and naming it told them a report
existed without letting them read a line of it. So what decides travels inside the
comment.

**No finding appears in it, of any kind.** Not the pre-existing ones, which were never
this delivery's, and not this delivery's own. A finding is a *reading*, and a reading
belongs in the report, where it sits beside its evidence, its consequence and its
status. On the item, the **verdict and the criteria** are what decide; a list of nits
and FYIs beside them reads as a list of things somebody has to answer for. What the
reader still gets is the **count**, in `Measured`, with the blocking ones named — enough
to know whether anything is owed without turning the item into a review thread. Asked
for on 2026-11-04, in two steps: first the pre-existing ones, then all of them.

A consequence worth knowing when you write a criterion's **record**: it must state its
fact, never point at a finding id. A record reading "the pipeline runs no test job (see
CI-1)" travels into the comment while `CI-1` does not, and a dangling cross-reference in
a published comment is the same defect as the path that used to close it.

What stays out for the same reason is what is an **instruction to execute later** — the
manual scripts and this environment's capabilities. Those are work orders, not the record
of a verdict, and they are what would drown the comment on a repository whose profile
leaves nine classes unassessable. Narrative, prose pending work and a suggestion for the
next step stay out too: they **decide nothing on the item** and drown what does. An empty
section is never printed, because a heading with no body cannot be told apart from
"nobody looked"; and a **refuted** finding appears nowhere, because the verifier knocked
it down.

The **project's pending items** are the one thing that is not this delivery's and stays,
because they are not findings: they are the declared limit of the review's own reach,
with owners, and they are what the `Measured` block's "nine not assessable" points at.
Dropping them would leave that number unexplained on the comment that states the verdict.

All of it is pinned in `tests/test_item_comment.py`; a new SECTION that does not fit
belongs to the report.

**An empty diff is a stop**, not a clean review: with no file there is nothing to review.

## A review relates to its own item and its own proposal, and to nothing else

A review done earlier must not bias a new one. The reviewers hold `Read`, `Grep` and
`Glob` over the whole workspace, and the reports root is in that workspace — so a
repository that already carries a sibling review carries a trap. It sprang on 2026-11-04:
asked for a manual script per class, a planner anchored on the neighbouring report and
returned fifteen scripts about the **previous** item, naming its SHAs and its proposal.
Plausible line by line, and about the wrong delivery.

The first attempt at a fix was a sentence in the prompt asking the agent not to do that.
That is not a guarantee, and the method already knows it — the same rule that puts
escaping in code puts this in code. **`provenance.py` decides it**, and `conclude` is the
gate: before the judge reads anything, every file the agents produced is checked against
this review's own identity, and a file that names another review's sha, another
proposal, or an item its own item never mentions **stops the run**. It stops rather than
warns because a report that mixes two deliveries is shaped exactly like a correct one.

The three rules, and why each is shaped that way:

- **a commit sha** — unambiguous, so it is the strong signal: eight to forty hex
  characters with at least one letter, foreign unless it is a prefix of (or prefixed by)
  this review's head, base or merge base;
- **a proposal number** — read from the origin ref's own shape, so it follows whatever
  forge the installation declared;
- **an item id the review's own item never mentions** — the asymmetry is the point. An
  item may legitimately name a sibling ("follows the test pattern of card X"), and
  flagging that would make the check cry wolf on the item's own words.

The remedy is never to hand-edit the ids: whatever read the wrong review had a reason,
and the next run would do it again. Re-run that agent with this review's manifest named
first.

While the review runs, `review.py` creates `.claude/.task-review-ativo`, and the hook uses
that marker to block push, commit and moving the item **only while the review is
running** — outside it the workspace carries on as normal, and the skills that legitimately
push or move an item keep working. Merge is blocked always. A marker leaked by a crash
expires in 4h, and `review.py release` removes it by hand.

Across loops, `delta.py` decides the mode before F2 — **re-judgement** when only evidence
arrived (no reviewer runs), **delta** when a push arrived, **full** the first time round.
`closure.py` reads the item's comments and produces `resolutions.json`.

## What the reviews learn about the repository

A review is thrown away every time, and the reviewers rediscover the same repository in
five separate contexts on every run. Measured over eleven reviews, that rediscovery is
the single biggest line of the bill: `cache_read` is 82% of what is spent, and output is
1%. The cost is not what the agents write — it is **requests × accumulated context**, and
the context is accumulated by opening files somebody already opened last week.

`knowledge.py` writes the other half. Per repository, outside it, under
`<knowledge>/<repo>.md`: the conventions the code exhibits, the files the reviewers keep
opening, the debt the repository has declared, and the defects no delivery caused. It is
the **profile's unconfirmed sibling** — the profile is what a person signed, this is what
the machine noticed.

Four rules, and each one closes a hole the method has already paid for:

- **Nothing here closes a gate.** The fifth gate measures whether the review actually
  measured; a machine-accrued fact certifying itself is exactly the hole that gate exists
  to cover. An entry's road into a verdict runs through a person — `derive_profile.py`
  reads the file, somebody confirms it, it becomes an anchor.
- **No sha, no proposal number, no item id — redacted in code, not asked for in a
  prompt.** This file is read by EVERY later review, so an identifier that is
  legitimately this review's own today is foreign tomorrow. The sanitiser is therefore
  stricter than `provenance.check`: it redacts its own too, and counts what it redacted.
  It redacts rather than drops, because a sentence stays useful once the sha is gone.
- **One sighting is not a fact.** An entry is printed only once a SECOND review has seen
  it; before that it is counted and held back, with the count stated. A single reviewer's
  guess printed as "what this repository does" is indistinguishable from a corroborated
  convention.
- **Nothing is deleted because a review did not see it.** A delivery that touches another
  corner of the repository says nothing about a convention it never went near. Staleness
  is read from `last_seen` in the ledger, never from absence.

The reviewers feed it through an OPTIONAL `conventions_observed` in their return — what
is true of the repository rather than of this change. It never becomes a finding. A
reviewer with nothing to say there says nothing, and the accrual is a **by-product**: it
runs last, after the report is filed, and a failure in it never costs the review its
report.

**Written now, read later.** Nothing consumes the file yet. Handing it to the reviewers
is the cut that attacks the ~60% spent on rediscovery, and it waits until the file has
mass and the sanitiser has a review's worth of real text behind it — a shortcut that is
wrong is worse than no shortcut, because the reviewer cannot tell.

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
- `reference/forge-connection.md` — connecting a forge so the description reads itself
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
