# prumo

[![tests](https://img.shields.io/badge/tests-275%20green-brightgreen)](#running-the-checks)
[![licence](https://img.shields.io/badge/licence-MIT-blue)](LICENSE)

Reviews a **whole delivery** as one package — the item that asked for it, the context it
was built in, and the code it produced — and closes with a two-valued verdict:
**APPROVED** or **INTERVENTION REQUIRED**.

There is one question only:

> **Is what the item asked for delivered in this change proposal?**

A plumb line measures against a reference. The item is the reference; the proposal is the
wall.

**Approving is the ceiling.** The prumo never merges, never moves an item between
columns, never writes into the repository under review, and never publishes without
explicit authorisation.

---

## Contents

- [The shape of it](#the-shape-of-it)
- [Repository structure](#repository-structure)
- [Setup](#setup)
  - [1. Install the plugin](#1-install-the-plugin-claude-code)
  - [2. Write the `prumo.yml`](#2-write-the-prumoyml)
  - [3. Derive a repository profile](#3-derive-a-repository-profile)
  - [4. Say who you are](#4-say-who-you-are)
  - [Setup on Codex](#setup-on-codex)
  - [Coming from a Portuguese install](#coming-from-a-portuguese-install)
- [Using it](#using-it)
- [The method, briefly](#the-method-briefly)
- [What it never does](#what-it-never-does)
- [Running the checks](#running-the-checks)

---

## The shape of it

**Deterministic backbone, agentic leaves.** Code decides what is rule; the LLM decides
what is judgement.

| | |
|---|---|
| **20 Python scripts** | Resolve the base, assemble the package, triage the risk, judge. No dependency beyond `yaml` and `jsonschema`. The judge is a pure function: same input, same verdict. |
| **10 read-only agents** | One question each. Seven reviewers of one axis, a criteria extractor, a manual-verification planner, and a **sceptical verifier** that tries to *refute* every finding without having seen the reasoning behind it. |
| **3 knowledge skills** | What the reviewers load to read a diff: five axes, OWASP/ASVS, personal data. Neutral of language, framework and project. |
| **18 risk classes** | A guardrail has three states — `fired`, `did_not_fire`, `not_assessable`. |
| **2 message catalogues** | 313 keys each. Everything a person reads, in the configured language. |

Three rules govern everything, and each has a test that fails when it is broken:

1. **The method knows no repository.** Every path, table, tool and convention comes from
   the profile at `<profiles>/<repo>.yml`.
2. **The method knows no provider.** GitLab, GitHub, Bitbucket, BusinessMap, Jira,
   Acelerato, and the language — all of it comes from `prumo.yml`.
3. **What could not be assessed is declared.** Silence does not count as absence of risk.

---

## Repository structure

```
prumo/
├── .claude-plugin/
│   ├── plugin.json              what the harness registers: skill, agents, hook
│   └── marketplace.json         the storefront `/plugin marketplace add` reads
│
├── scripts/                     THE BACKBONE — 20 modules, no model runs here
│   ├── providers.py             prumo.yml: paths, tracker, forge, language
│   ├── i18n.py                  message catalogues; a missing key raises
│   ├── common.py                the 18 classes, shared helpers
│   ├── preflight.py             F0 — what THIS environment can do
│   ├── resolver.py              F1 — base, SHAs, the item
│   ├── package.py               F2 — diff, slices, change.json
│   ├── triage.py                F3 — which classes fire, which reviewers run
│   ├── evidence.py              F6 — runs only the profile's allowlist, one round
│   ├── judge.py                 F7 — pure function: gates, routes, verdict
│   ├── render.py                F9 — report and comments
│   ├── review.py                the two entry points: `prepare` and `conclude`
│   ├── delta.py, closure.py     the loops: re-judgement and item resolutions
│   ├── telemetry.py, metrics.py measured cost, never estimated
│   ├── derive_profile.py        bootstraps a profile for a new repository
│   ├── validate_profile.py      a profile against its contract
│   ├── migrate_config.py        rewrites a Portuguese prumo.yml or profile
│   ├── agnosticism_check.py     fails the method when it names a technology
│   ├── hygiene.py               fails a secret, or a rule that is not the prumo's own
│   ├── setup.py                 writes a prumo.yml: asks five things, measures the rest
│   └── tests/                   297 tests
│
├── agents/                      THE LEAVES — 10 read-only prompts
├── skills/
│   ├── task-review/SKILL.md     what Claude Code loads as `/prumo:task-review`
│   ├── five-axis-review/        correctness, readability, architecture, security, performance
│   ├── owasp-asvs/              OWASP Top 10:2025 + API Top 10 against ASVS 5.0
│   ├── personal-data/           what is personal data and which destinations are barred
│
├── workflows/task-review.js     the reviewer fan-out, the verifier, the manual plan
├── hooks/
│   ├── review-guard.sh          blocks merge always; push/commit while a review runs
│   └── hooks.json               registers the hook — you edit no settings.json
│
├── messages/{en,pt-BR}.yml      every user-facing string
├── schemas/                     finding, criterion, triaged-class, verdict,
│                                manual-script, and the profile contract
├── templates/                   report and the three comments
├── reference/                   the 18 classes, publication, playbooks, CI
└── prumo.yml.example            the installation contract, commented
```

**What is NOT here, by design:** the reports, the `runs.jsonl`, the repository profiles
and your `prumo.yml`. Those belong to an installation, live where `paths` points, and
never enter this repository.

---

## Setup

Four steps, and the second writes itself.

### 1. Install the plugin (Claude Code)

The ten agents, the four knowledge skills, the workflow and the hook travel with the
plugin. You copy nothing into `.claude/agents/`, you edit no `settings.json`, you
register no hook by hand:

**One at a time** — the second is not an argument to the first:

```
/plugin marketplace add chenisen/prumo
```

```
/plugin install prumo@prumo
```

What that registers:

| What | How it shows up |
|---|---|
| the skill | `/prumo:task-review <item url>` |
| the 10 agents | `prumo:scope-coherence-reviewer`, `prumo:finding-verifier`, … |
| the knowledge skills | loaded by the reviewers, not invoked by hand |
| the fan-out workflow | invoked by the skill |
| the `PreToolUse` hook | from `hooks/hooks.json` |

> **If you already had the prumo as a loose skill**, remove the old copies from
> `.claude/agents/`, `.claude/workflows/` and `.claude/hooks/` — otherwise both sets
> register at once, the old ones unprefixed and the plugin's as `prumo:<name>`, and you
> end up editing one while testing the other.

**Python 3.11+ and two packages** are the only runtime dependency:

```bash
pip install pyyaml jsonschema
```

Optional, measured by the preflight and never required: `git` (required in practice —
without a diff there is no review), `docker` (running a repo's suite in its container),
and your forge's CLI (`glab`, `gh`).

### 2. Write the `prumo.yml`

**Let the setup write it:**

```bash
python3 <prumo>/scripts/setup.py --at <your-workspace>
```

It asks what nothing can answer for you — the tracker and its link template, the
language, who signs, and **how the harness reaches your forge** — and **measures** the
rest: where your repositories are (by counting git checkouts), which forge they push to
(by reading their `origin`), whether git can actually fetch from it, and whether this
shell already holds a forge token or an authenticated CLI. It validates before writing,
never overwrites without `--force`, and `--dry-run` prints the file instead. At the end
it names what is left: which repositories have no profile yet, and what each connection
buys.

**The forge connection** is `mcp`, `token` or `none`, and **`none` is a valid answer**:
reviewing needs no API, because the diff is read over git through the ref the server
publishes. A connection buys the proposal's description without a file, its pipeline's
state, and publishing. Whichever you pick, the configuration holds **the NAME of an
environment variable, never a token** — `providers.py` refuses a value that looks like a
secret, and `scripts/hygiene.py` fails any secret, or any rule belonging to somebody's
installation, that reaches a commit in this repository.

**[`reference/forge-connection.md`](reference/forge-connection.md) has the steps**, per
provider: which connector, from where, and what it needs to be allowed to do. The one
invariant across all three routes is that **the text always arrives as a file** —
`--proposal-file`. A connection does not remove that argument; it removes the copying and
pasting that would otherwise produce it. A token sitting in your environment is not a
reading, and does not open the gate.

Answer it up front and it asks nothing:

```bash
python3 scripts/setup.py --tracker jira --forge github --language pt-BR --owner "A B"
```

The rest of this section is what that file means, for when you edit it by hand.

**Without this file the prumo refuses to run.** That is deliberate: it assumes neither
provider, nor path, nor language. A silent default would have you review a GitHub PR with
GitLab vocabulary, or publish HTML into a tracker that only renders Markdown — errors
that surface later, in text already published.

Put it at the root of the workspace you review, or point `PRUMO_CONFIG` at it:

```yaml
prumo: 1
language: en                              # en | pt-BR
owner: "Your Name"                        # who signs; the environment overrides it

paths:
  repositories: ./repositories            # where the repos to review live
  profiles:     ./profiles                # one .yml per repository
  reports:      ./reports                 # reports + runs.jsonl

tracker:
  type: businessmap                       # businessmap | jira | acelerato | generic
  url: "https://YOUR-SPACE.example.com/cards/{id}"

forge:
  type: gitlab                            # gitlab | github | bitbucket
```

Declaring a `type` brings a whole preset; declare a single field only to override it.
Check what was understood — this is also the first thing to paste into a bug report:

```bash
python3 scripts/providers.py
```

```
configuration: /my-workspace/prumo.yml
  language    en
  forge       gitlab · MR !123 · refs/merge-requests/123/head
              connection: token · `GITLAB_TOKEN` set in this environment
  tracker     businessmap · item `card` · comment in html
  repositories /my-workspace/repositories
  profiles     /my-workspace/profiles
  reports      /my-workspace/reports
```

**What each provider changes:**

| | GitLab | GitHub | Bitbucket |
|---|---|---|---|
| Proposal | `MR !820` | `PR #820` | `PR #820` |
| Diff ref | `refs/merge-requests/{n}/head` | `refs/pull/{n}/head` | `refs/pull-requests/{n}/from` |
| Tokens | `GITLAB_TOKEN`, `GLAB_TOKEN` | `GITHUB_TOKEN`, `GH_TOKEN` | `BITBUCKET_TOKEN` |
| CLI | `glab` | `gh` | — |

| | BusinessMap | Jira | Acelerato | generic |
|---|---|---|---|---|
| Item | `card` | `issue` | `ticket` | `item` |
| Comment | HTML | wiki markup | HTML | Markdown |

> **Bitbucket Cloud publishes no pull request ref.** The preset serves Server/Data
> Centre. On Cloud, review by branch (`--branch`); the description gate does not apply to
> a `branch` origin.

A provider outside the list: use the closest one and override the fields. No preset is
magic — each is five or six values.

**Two optional sections** finish the installation, and neither puts a company name inside
the prumo:

- **`agnosticism.forbidden`** — the names of *your* systems, tables and item-id shape,
  which `agnosticism_check.py` fails if they leak into the method. The universal patterns
  (stack tool, language, database) already belong to the prumo.
- **`detection.proof_registries`** — the marker of your specification framework, if you
  have one. The public ones (Spec Kit, `.claude/specs`) are already known; a declared one
  is tested before them.

`prumo.yml.example` carries all of it, commented.

### 3. Derive a repository profile

Each repository under review needs a profile. Without one the review still runs and says
so, but it sees less: half the classes come out `not_assessable` for want of an anchor.

```bash
python3 scripts/derive_profile.py my-service
```

It fills in what it can measure and lists what it could not. **Confirming is a person's
act**, once per repository: fill in the rest, set `status: confirmed`, and name who
confirmed it.

```bash
python3 scripts/validate_profile.py my-service   # against the contract
```

### 4. Say who you are

Someone has to sign the human acts a profile records — a waiver, a `not_applicable`.
Two places, and which one you use depends on how your `prumo.yml` is shared:

```yaml
owner: "Your Name"                    # in prumo.yml
```

```bash
export TASK_REVIEW_RESPONSAVEL="Your Name"    # in the environment — WINS over the file
```

The name must **never** go in the *profile*: a profile is versioned and shared between
everyone who reviews that repository, so a name there would sign everybody's reviews with
the first person who confirmed it. The profile writes the token `${RESPONSAVEL}` and the
name is resolved at run time.

`prumo.yml` is a different file with different sharing: it belongs to one installation
and is git-ignored, so a name is at home there. If your team *does* commit its
`prumo.yml`, the environment variable is the way out — the file carries the common
default, each person overrides it for their own run.

### Setup on Codex

Codex has no skills and no named subagents, so the prumo runs in two parts: the
**backbone** through the CLI, which is deterministic and needs no model at all, and the
**leaves** as prompts you hand to Codex. Put this in an `AGENTS.md` at your workspace
root:

```markdown
When the request names a delivery (item, proposal, branch or diff) and asks for a review:

1. Read the item from the tracker into /tmp/item.md. If the origin is a proposal, read
   its description into /tmp/proposal.md. An empty file does NOT count as read — if you
   cannot get them, STOP and ask for the connection.
2. python3 <prumo>/scripts/review.py prepare --repo R --proposal N --item C \
      --item-file /tmp/item.md --proposal-file /tmp/proposal.md
3. For each reviewer in the printed plan, open a clean context with the prompt from
   <prumo>/agents/<name>.md and the package paths. Collect findings as JSON per
   <prumo>/schemas/finding.schema.json.
4. For each blocking finding, run finding-verifier in a context that has NOT seen the
   reasoning behind it. That blindness is what makes the verification worth anything.
5. python3 <prumo>/scripts/review.py conclude --package P --findings A
6. Publishing is an external act and wants its own explicit yes. Never merge, never move
   the item.
```

Step 3 is what the workflow does by itself on Claude Code. The agent prompts and the
schemas are the same files either way.

To run the backbone alone — useful in CI — pass an empty findings list. You get the
report with triaged classes, criteria and gates, without the agentic findings, and the
report says so.

### Coming from a Portuguese install

The old keys (`caminhos`, `rastreador`, `deteccao`, `agnosticismo` and their fields) are
still accepted for one cycle, and every run names the ones it found. To rewrite a file —
comments and all, because in a `prumo.yml` the comments are half the documentation:

```bash
python3 scripts/migrate_config.py prumo.yml --write
python3 scripts/migrate_config.py profiles/*.yml --write
```

---

## Using it

```
/prumo:task-review https://your-tracker/item/50273
```

Or the backbone directly — two commands, because the workflow sits between them and a
script cannot invoke it:

```
review.py prepare --repo R [--proposal N | --branch B] --item C
                  --item-file ITEM.md [--proposal-file PROPOSAL.md]
Workflow({name: 'task-review', args: {...}})          # prepare prints this, filled in
review.py conclude --package P --findings A [--scripts-manual S]
```

**The mandatory reading is a gate, and it comes before everything.** The review does not
start without the item, and — when the origin is a proposal — without its description. No
Python script reaches the tracker or the forge API: whoever conducts the review does, so
the reading is *declared by the person who did it*, by handing over the file. A file that
is absent, or present and empty, does not count as read — the difference between "there
are no criteria" and "I could not read them" is the whole point.

---

## The method, briefly

### The verdict

> **APPROVED ⟺**
> 1. the code follows the project's standards and good practice;
> 2. it breaks no business rule;
> 3. it covers what the item asked for, with the necessary evidence;
> 4. no manual verification is left to do;
> 5. and the four above were actually **measured**.

Any closed gate is INTERVENTION REQUIRED. The report opens with that five-line table and
only then with the routes — the route says *who does what*, the gate says *which
condition failed*.

### Whose debt is it

**The review judges what the delivery DID.** Three applications of one principle:

- a **finding** on code the change did not touch carries `attributable: false`: it reaches
  the report, outside the count and outside the verdict;
- a **red suite** the delivery did not cause is the repository's baseline: it reaches the
  summary and does not hold the proposal;
- a **route** with `origin: project` — an unconfirmed profile, a class with no anchor, a
  missing tool — is declared with an owner, and stays outside the verdict.

Without this, a repository whose profile nobody finished would never approve any delivery,
and the verdict would stop discriminating good work from bad.

### Cost

Measured, never estimated. `telemetry.py` reads the workflow record and the per-agent
transcripts and sums what the API reported. **With no locatable transcript, `tokens`
comes out `null` with a reason** — a zero would make an expensive review look cheap.

---

## What it never does

- **Merge.** The hook blocks it, always.
- **Move an item between columns.**
- **Write into the repository under review.** Only into the reports directory.
- **Publish without authorisation.** The item comment, the proposal comment and the
  Approve are three external acts, each with its own yes.
- **Fix.** The author fixes.
- **Approve what it could not assess.** See the third guardrail state.

---

## Running the checks

```bash
cd scripts
python3 -m unittest discover -s tests          # 275 tests
python3 -m unittest discover -s tests -t .     # must collect the SAME number
python3 i18n.py --check                        # catalogues, key by key
python3 agnosticism_check.py                   # the four rules
```

> The two discovery modes must collect **the same number**. They once diverged by 41
> without a word, because one module imported another as a top-level name. If they
> diverge again, that is the bug — do not run only one of them.

`metrics.py` aggregates cost across `runs.jsonl`: median per run, **tokens per changed
line** — the only figure that compares reviews of different sizes — and spend per agent
type.
