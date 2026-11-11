# Delivery kinds

A **kind** is a closed recipe: a trigger, the steps, and a deterministic proof of done.
`deliver` runs a kind the profile lists under `delivery.kinds`; anything else is
`unsupported`, and the run stops instead of improvising. Adding a kind is a change to
this file, to the schema's enum, and to the tests — never a decision made during a run.

The kind belongs to the method and holds in any stack. Where the tests live, how the
suite runs, how a red is named — all of it comes from the profile.

## `coverage-gap` — a guard with no test

| | |
|---|---|
| **Trigger** | the item names a guard (a condition, a validation, a refusal) and says no test proves it |
| **Premises the surveyor measures** | the guard exists at `file:line`; no test in `test_roots` exercises it; the guard is the only point of control the item claims it is |
| **What the writer may touch** | the test file only — a new one, or the existing one for that unit. Production code is out of scope for this kind |
| **Proof** | **mutation by hand**: the script turns the guarded code wrong at the named line (the operator, the boundary, the returned value), runs the suite, requires the new test **red**, and reverts the mutation. Then the suite on the branch, green by name against the baseline |
| **Done when** | the mutation went red, the revert left the tree clean, no new red by name, the diff is inside `test_roots` and inside the budget |
| **Blocked when** | the guard is not where the item says; a test already covers it (the item's premise fails); the mutation survived — the test does not prove the guard |

A mutant has two halves. `test` is what must go red; `stays_green` is what the item says
must not — the passage test under a guard switched off. A mutant that turns a
`stays_green` test red is wider than the guard it was meant to kill, and the delivery is
blocked for it.

The mutation is written to the record with the file, the line, what was changed into
what, which test went red, which stayed green, and that it was reverted. A mutation the script could not
apply — the line moved, the pattern did not match — is `not_assessable`, declared, and
the run stops: a coverage delivery without its mutation is a test nobody proved.

## `behaviour-change` — the code does the wrong thing, or not the thing

| | |
|---|---|
| **Trigger** | the item names a behaviour the code lacks or gets wrong, with the input and the expected answer |
| **Premises the surveyor measures** | where the behaviour lives at `file:line`; what the code answers today; which test file proves it; `proving_tests` — the names the writer must use |
| **What the writer may touch** | the production files the measurement named, and the test file |
| **Proof** | **red before green**: the script restores the base's version of the production files, runs the suite with the new test in place, and requires every proving test **red**; then restores the writer's files and runs the suite again, requiring them green by name against the baseline |
| **Done when** | red on the base, green on the branch, no new red by name, the diff inside the named files and the budget, the profile's lint green |
| **Blocked when** | the proving test is green on the base — it does not prove the change; a line outside the named files; a new red elsewhere |

An `author_fix` route a review opened is this kind: `deliver.py prepare --fix <review
package>` writes the item from the routes' findings, stays on the review's branch, and
takes the branch's head as the base — the red before the green is measured against what
the review read.

## `refactor` — the shape changes, the behaviour does not

| | |
|---|---|
| **Trigger** | the item names code to reshape — a duplication, a dependency to invert, a module to split — and says the observable behaviour stays |
| **Premises the surveyor measures** | the files being reshaped at `file:line`; `guarding_tests` — the existing tests, by name, that exercise them today |
| **What the writer may touch** | the files the measurement named, production and tests alike |
| **Proof** | **preservation**: every guarding test green on the branch, by name; the baseline by name; the diff inside the named files and the budget; the profile's lint green. No red before green — nothing new is proven, something old is kept |
| **Done when** | all of the above, and at least one guarding test named |
| **Blocked when** | no guarding test exists — the behaviour has no proof to preserve, and the guard comes first, as a `coverage-gap`; a guarding test went red; a line outside the named files |

The shape that asks for this kind, `refactor`, buys twice the profile's budget and
waits for a person's approval of the plan: the plan is where the person reads which
tests stand guard, and decides whether that is enough.

## Every kind

The profile's lint check, when it declares one, runs over the tree the writer left and
blocks on red. A kind not listed here — `dead-code`, `static-analysis-debt`,
`duplicated-rule`, `stale-dependency` — is `unsupported` until it has a recipe and its
tests.
