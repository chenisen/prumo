---
name: five-axis-review
description: The five axes a reviewer reads a diff along — correctness, readability, architecture, security and performance — plus change size and dependencies, with the severity scale and the rule that a project's own conventions decide what counts. Neutral of language, framework and project. Use when reading a diff looking for defects.
---

# Five axes for reading a diff

Knowledge common to every reviewer. Each reviewer answers **one** question, but all of
them read along the same axes and use the same scale.

## The two rules that hold for every finding

**Literal about what you read.** Every finding names a file and a line, and states the
concrete consequence **in this code** — not in theory, not "could cause problems". If you
cannot describe what breaks and when, it is not a finding: it is an opinion.

**Contextual about what you demand.** SOLID, KISS, DRY and YAGNI hold **as this project
reads them**. The absence of a pattern the project does not adopt is **never** a finding.
You do not know what this project's pattern is: read the convention documents the request
points you at. If there are none, say so and judge only what is universal.

## The axes

### 1. Correctness
Does the code do what it says? Boundary conditions, the empty case, null, concurrency,
ordering of operations, a swallowed error, an ignored return value, state left half-mutated
when something fails midway.

### 2. Readability
Can someone who did not write this understand it in one reading? A name that lies, a
function doing two things, deep nesting, a comment explaining the what instead of the why,
inconsistency with the rest of the file.

### 3. Architecture
Does the change respect the boundaries the project chose? A layer skipped, a dependency
inverted for no reason, business rules leaking out to the edge, a second home for a
decision that already had one. **Duplication can be deliberate** — before flagging it, look
for the precedent in the project.

### 4. Security
See the `owasp-asvs` skill. Here, only the axis: trusted input, authorisation missing or
applied too late, secrets, injection, an error that leaks.

### 5. Performance
What does the cost grow with? A query inside a loop, eager loading that drags in the world,
an expensive operation on a hot path, missing pagination, allocation in a tight loop.
**A green test over three rows says nothing about real volume.**

## Change size

| Band | What to do |
|---|---|
| up to ~100 lines | read it all, findings point by point |
| ~100 to ~1,000 | read slice by slice, prioritising what triage flagged |
| above ~1,000 | **ask for it to be split**; review whole only in a declared emergency |

The concrete thresholds come from the repository profile. A file that was **already** over
the ceiling and was fattened further by this change is a finding in itself: extract before
adding.

## Dependencies

A new package is a dimension of its own: the package exists, it is maintained, it is the
one intended (and not a lookalike name), the licence serves, and the lockfile was generated
rather than hand-edited.

## Severity

| Label | When | Blocks? |
|---|---|---|
| **critical** | data loss, exploitable security failure, contract broken in production | yes |
| **required** | a real defect with a demonstrable consequence in this code | yes |
| **nit** | a small improvement, no functional consequence | no |
| **optional** | a legitimate alternative, a matter of informed taste | no |
| **fyi** | context the author should know, with no action requested | no |

Two cut-offs written two ways diverge. **This table is the only scale**: if a finding does
not fit it, it is a nit.

## Attribution

A defect the change **did not cause** is not this delivery's author's debt. Mark it
`attributable: false`: it appears in the report and does not hold the proposal. Confusing
the two is the fastest way for a review to lose its credibility.
