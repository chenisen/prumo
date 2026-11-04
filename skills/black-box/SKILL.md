---
name: black-box
description: Techniques for designing test cases from a written criterion — equivalence partitioning, boundary values, decision tables and state transitions — to build scripts a person executes when no automated test reaches the behaviour. Use when writing a manual verification plan.
---

# Designing cases from a criterion

Used when the harness cannot prove something and has to hand a script to a person to
execute. The script is not "test it manually": it is a set of cases, each with an input
and a **predicted result written before anyone runs anything**.

## The rule that separates verification from observation

**The predicted result is written before anyone executes.** Whoever runs first and
describes afterwards is observing, not verifying — and will rationalise whatever number
turns up. Write the expected number, then measure.

## The shape of every script

1. **Trigger** — the class that asked for it.
2. **Objective** — the property the script establishes, in one sentence.
3. **Environment** — where it runs, and why that is not production.
4. **Input data** — what has to exist beforehand.
5. **Steps** — numbered, executable by someone who did not write the code.
6. **Predicted result** — a value, a state or a count, written now.
7. **Evidence to collect** — what remains as proof.
8. **Acceptance criterion** — what makes this pass or fail.

## The four techniques

### Equivalence partitioning
Split the input into classes the system treats alike; test **one** from each. Always
include at least one **invalid** partition — the error path is part of the behaviour.

### Boundary values
Where there is a boundary, that is where the defect lives. For a limit `n`, test
**n-1, n, n+1**. This is the technique that catches the inverted comparison, which no
partition catches.

### Decision table
When the outcome depends on a **combination** of conditions, enumerate the combinations.
Three boolean conditions give eight rows; strike the ones the project declares impossible
and test the rest.

### State transition
When the entity has a life cycle, test the **permitted** transitions, and at least one
**forbidden** one — the defect is usually accepting a transition that should not exist.

## Example

Criterion: *"the discount applies to contracts with a minimum term of 12 months or more"*.

| Case | Technique | Input | Predicted result |
|---|---|---|---|
| B1 | boundary value | minimum term of 11 months | no discount |
| B2 | boundary value | minimum term of 12 months | discount applied |
| B3 | boundary value | minimum term of 13 months | discount applied |
| P1 | valid partition | contract with no minimum term | no discount |
| P2 | invalid partition | minimum term missing or negative | validation error, nothing calculated |

Five cases, and B1/B2 on their own already catch the `>` written where `>=` belonged.

## Script types by trigger

| Trigger | Central technique | Evidence |
|---|---|---|
| criterion the diff cannot verify | partitioning + boundary values derived from the text of the criterion | table of cases with the output obtained |
| derived artefact | produce the artefact in dev and read it | predicted fields and values; dump of the artefact |
| aggregate across two stores | query the source and confront it with the artefact | predicted number of divergences (zero) and the query used |
| authorisation scope | two subjects, A and B: with A's credential, request a resource of B's by identifier, then list and filter | the denial, and a list with none of B's items; requests and responses saved |
| contract with a consumer | compare the response before and after in dev | diff of the payload and each consumer's sign-off |
| data migration | run it against a copy of the real schema and test the rollback | count predicted beforehand, checked afterwards |
| behaviour under volume | measure with realistic volume | p95, query count and peak memory, before and after |
| external integration | the supplier's sandbox, including timeout, server error and invalid payload | log of the calls and the behaviour on each failure |

## What makes a script useless

- A predicted result written as "it works" or "it does not break".
- A step only the person who wrote the code can execute.
- Environment "production".
- No evidence to collect — so nobody can check afterwards that it was done.
