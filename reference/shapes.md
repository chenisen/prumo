# Shapes

A **shape** is what kind of item a delivery is asked for — a debt, a bug, a hotfix, an
incident, a refactor, a story, an epic — and it decides, before anything is read, how deep the plan
goes, whether a person approves it before a line is written, which kinds may deliver it,
how much diff it may buy, and how hard the review reads it. The shape is the item's,
never inferred from its text: it comes from the tracker's type, mapped in `prumo.yml`
(`tracker.shapes`), or from `--shape` on the invocation. An item with no shape is a stop.

The method knows seven shapes and nothing else. A tracker's type that maps to none of them
is the installation's to map, not the prumo's to guess.

| Shape | Plan depth | Person gate before writing | Kinds | Budget | Review effort | What the plan holds |
|---|---|---|---|---|---|---|
| `debt` | **measure** | no | `coverage-gap`, `behaviour-change` | ×1 | minimal | premises with an address, the files, the mutations and what stays green |
| `bug` | **reproduce** | no | `behaviour-change` | ×1 | standard | the measure, plus the proving tests that reproduce the bug — red on the base before anything is fixed |
| `hotfix` | **reproduce** | **yes** | `behaviour-change` | ×½ | full | as a bug, on the profile's `hotfix_base`; the smallest diff, approved by a person because it goes where a bug does not |
| `incident` | **root-cause** | no | `behaviour-change` | ×½ | one reader, inside the delivery | the symptom, the line it is born at, the test that reproduces it — on the profile's `hotfix_base`; nothing is planned before the first line |
| `refactor` | **preserve** | **yes** | `refactor` | ×2 | standard | the files, and the tests that guard the behaviour being preserved; no new behaviour, no red before green |
| `story` | **design** | **yes** | `behaviour-change`, per task | ×3 | full | the tasks the story decomposes into, each with files, criteria and proving tests, in order; written one by one in a single workflow call, each committed by its writer, checked once and reviewed once |
| `epic` | **decompose** | **yes** | none | — | — | the stories it decomposes into, as items for the tracker; nothing is delivered |

**The plan is a file**, `plan.md`, rendered by `deliver.py plan` from the measurement
in the voice of the report: one fact per line, with an address. Beside it, `plan.json`
carries the shape, the depth, the gate and a digest of the measurement it was rendered
from.

**The gate is a file too.** `deliver.py approve --package P --by NAME` writes
`approval.json` with the digest it approves; `/prumo:approve` is the skill that runs it,
closed to the model so the name is a person's. A plan whose measurement changed after
the approval is unapproved again — the digest says so — and the guard refuses every
write while a required approval is missing. Where the shape asks no gate, the plan is
still rendered and still read: it is what the review's scope reads against.

**The budget is the profile's, scaled by the shape — when the profile declares one.** A
hotfix buys half of what the profile allows; a refactor twice; a story three times, over
all its tasks. It is optional and never defaulted, and it stops nothing: it is the size
the agents aim for, and a delivery over it is declared in the report as a warning. It is
read as the profile stands when `conclude` runs.

## The size

The shape says what was **asked**; the size says how much was **touched**, and it is the
size that decides what a delivery pays. `shape.size_of` is a pure function over the
files and lines of the change, the shape, and two things the profile declares:
`delivery.small` — up to where a change is small in that repository, 3 files and 80
lines when absent — and `delivery.wide_paths` — globs of what everything else leans on.

| Size | When | Sceptic | What `conclude` runs, once |
|---|---|---|---|
| `small` | a shape with no gate, inside `delivery.small`, touching no wide path | no | the test files the change touched, plus the `related_tests` the measurement named |
| `standard` | any other single item — past a limit, on a wide path, or a shape a person approves | once | the whole suite |
| `large` | a story | no; the review reads the branch | the whole suite, over all its tasks |

It is read three times: over the files the measurement named, before anything is
written; over what the writer says landed — its files and its `lines_changed` — to decide
the sceptic; and over the diff, to decide the suite. A delivery that still reaches
`conclude` as a standard item no sceptic read says so in its report. The base is measured in neither case unless
the branch turns something red. A profile with no `checks.suite.files_command` cannot
run named test files, and every delivery there pays the whole suite — the remedy is one
line in the profile, never a flag on the run.

## An incident

Production is failing now. The other shapes spend time to be sure before they write: a
measurement, a plan, a person's approval, a whole suite, a review in rounds. An incident
spends none of it before the fix exists, and keeps the three things that make a fix
worth shipping.

| | Every other shape | `incident` |
|---|---|---|
| Before the first line | a surveyor measures the item's premises; a gated shape waits for a person | nothing: the fixer goes from the symptom to the line it is born at |
| Who writes | the `change-writer`, from the measurement | the `incident-fixer`: diagnoses, reproduces and fixes in one pass, with a shell |
| What must be there | the premises, with addresses | the **root cause, with an address** — a fix that names none does not stand |
| Proof | red on the base, green on the branch | the same: the test that reproduces the incident |
| Suite | by the size: the touched tests, or the whole of it | **the tests it touched and the ones beside them, whatever its size**; the whole suite is the proposal's pipeline's, and the report says it did not run |
| Who reads it | a sceptic, then `task-review`, in rounds | the `incident-reader`, **once**, inside the same workflow call, blind to the fixer's reasoning |
| What holds it | a finding with a basis | **two answers only**, each with an address: the symptom survives the fix, or something that worked no longer does |
| Everything else seen | a finding, a suggestion, a round | a line under *after the incident*, signed by who saw it — an item of its own |
| Where a person decides | before the write (a gated shape) and at the proposal | **once, at the end**: symptom, cause, fix and reading on top of one report, then the push |

Three steps: `deliver.py prepare --shape incident` → `Workflow P/workflow-incident.js` →
`deliver.py conclude`. The delivery ends at a person's push, not at a review: `task-review`
may read the branch afterwards, beside the proposal, and nothing it finds stands between
the fix and production.

A fixer that finds the cause outside the repository — a configuration, a row of data,
another service — writes nothing and returns `blocked` with the diagnosis: in an
incident that is often the answer a person needs first. An `uncertain` reading is shown
on top of the report and holds nothing. A reading that holds hands the fixer its reason
and one more call of the same stage.

Measured on the first incident the method delivered, as a `hotfix` (2026-11-26): two
hours from the first `prepare` to the approval, twenty-two minutes of them worked by an
agent. Forty-one waited at the plan's gate; forty-five went to a base whose whole suite
did not load, with the delivery's own tests green; twenty-seven to three review rounds
whose seven blocking findings said a test could prove more or the new code could be
sturdier — none that the fix missed the cause or broke what worked.

**What a shape never changes:** who writes does not approve; the suite is read by name;
what could not be measured is declared; a line outside the named files is a defect.
