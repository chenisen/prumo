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

The mutation is written to the record with the file, the line, what was changed into
what, which test went red, and that it was reverted. A mutation the script could not
apply — the line moved, the pattern did not match — is `not_assessable`, declared, and
the run stops: a coverage delivery without its mutation is a test nobody proved.

## Kinds not yet here

Named so the gap is visible, each waiting for its own recipe and its own tests:
`behaviour-change` (red test on the base, green on the branch), `dead-code`,
`static-analysis-debt`, `duplicated-rule`, `stale-dependency`. None runs today.
