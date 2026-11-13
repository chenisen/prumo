# Shapes

A **shape** is what kind of item a delivery is asked for — a debt, a bug, a hotfix, a
refactor, a story, an epic — and it decides, before anything is read, how deep the plan
goes, whether a person approves it before a line is written, which kinds may deliver it,
how much diff it may buy, and how hard the review reads it. The shape is the item's,
never inferred from its text: it comes from the tracker's type, mapped in `prumo.yml`
(`tracker.shapes`), or from `--shape` on the invocation. An item with no shape is a stop.

The method knows six shapes and nothing else. A tracker's type that maps to none of them
is the installation's to map, not the prumo's to guess.

| Shape | Plan depth | Person gate before writing | Kinds | Budget | Review effort | What the plan holds |
|---|---|---|---|---|---|---|
| `debt` | **measure** | no | `coverage-gap`, `behaviour-change` | ×1 | minimal | premises with an address, the files, the mutations and what stays green |
| `bug` | **reproduce** | no | `behaviour-change` | ×1 | standard | the measure, plus the proving tests that reproduce the bug — red on the base before anything is fixed |
| `hotfix` | **reproduce** | **yes** | `behaviour-change` | ×½ | full | as a bug, on the profile's `hotfix_base`; the smallest diff, approved by a person because it goes where a bug does not |
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

**The budget is the profile's, scaled by the shape.** A hotfix buys half of what the
profile allows; a refactor twice; a story three times, over all its tasks.

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

It is read twice: over the files the measurement named, to decide the sceptic, and over
the diff that landed, to decide the suite. The base is measured in neither case unless
the branch turns something red. A profile with no `checks.suite.files_command` cannot
run named test files, and every delivery there pays the whole suite — the remedy is one
line in the profile, never a flag on the run.

**What a shape never changes:** who writes does not approve; the suite is read by name;
what could not be measured is declared; a line outside the named files is a defect.
