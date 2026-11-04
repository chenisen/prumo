# The eighteen guardrail classes

The **class** belongs to the method and holds in any stack. The **anchor** — paths,
patterns, commands, items — comes from the repository's profile. Nothing here names a
repository, a table, a collection or a tool: if it did, the class would work in one
project and lie in all the others.

Every class leaves triage with **one of three states**, and all three appear in the
report:

| State | Means | In the judge |
|---|---|---|
| `fired` | the anchor matched what the change touches | human decision route (§A) or manual check (§B) |
| `did_not_fire` | the anchor exists and did not match | nothing — but it is **named** in the report |
| `not_assessable` | there is no anchor, or the capability to measure it is missing | `missing_capability` route, until someone records `not_applicable` or waives it |

Silence never counts as absence of risk. It is the difference between "there is no risk
here" and "nobody looked", and a report that does not separate the two lies.

## §A — human decision

No §A finding proposes a fix. How to resolve it is a person's call; the harness states
the question and the doors available. The finding schema **refuses** a non-null `form`
when the class starts with A.

| Class | Question | Anchor the profile supplies | Without an anchor |
|---|---|---|---|
| **A1** | was a schema artefact that has **already been applied** edited? | the paths of the schema artefacts | not assessable |
| **A2** | did the shape of an outbound contract change — route response, published document, event, payload? | contract surfaces and the consumers outside the repo | not assessable |
| **A3** | was an authorisation-scope or tenant clause touched? | optional: where the scope is enforced, to raise recall | **holds without an anchor** |
| **A4** | did an integration surface change — endpoint, base-URL environment variable, shared table? | integration paths and which documentation is a deliverable | not assessable |
| **A5** | did a business rule change — what the customer may buy, see or pay? | optional: where the rules live | **holds without an anchor** |
| **A6** | was a layer or a boundary introduced, or a dependency inverted? | the layer map, when the project has one | **holds without an anchor**, reading the conventions it points at |
| **A7** | does the code assert a fact the specification does not hold, or contradict one it does? | `proof_registry` | not assessable when the type is `absent` |
| **A8** | was a test removed **or weakened**? | optional: where the tests live | **holds without an anchor** |
| **A9** | did a secret, a credential or key material get in? | the `secrets` check, plus any extra patterns from the repo | not assessable without the check |
| **A10** | was a hot path touched with an effect that scales with volume? | the repo's hot paths | not assessable |

**A9 reads content, not file names.** A secret pasted inside a documentation `.md` is
exactly as exposed as a versioned `.env` — that is precisely the case this rule came
from.

## §B — manual check

§B is not "a test is missing". It is "no automated test reaches this, and here is the
script for a person to prove it". Every script carries the **expected result written
down before it is executed** — that is what separates a check from an observation.

| Class | Question | Anchor the profile supplies | Without an anchor |
|---|---|---|---|
| **B1** | was a derived artefact that is only observable by running it affected? | what the artefact is and how to produce it in dev | not assessable |
| **B2** | was an integration with an internal legacy system touched? | which systems, and why the suite does not cover them | not assessable |
| **B3** | was an external provider touched? | which providers and where the sandbox is | not assessable |
| **B4** | did an aggregate spanning two stores change — price, total, composition? | which aggregates and what the audit is | not assessable |
| **B5** | did a screen, a flow or interface copy change? | whether the repo has an interface, and where | not assessable |
| **B6** | is there a **data** migration, not just a schema one? | how to run it against a real dev schema and how to roll it back | **holds without an anchor** for detection |
| **B7** | is there behaviour that only shows up at volume? | optional: what to measure | **holds without an anchor** |
| **B8** | does the delivery reach production through a formal process? | the name of that process in the organisation | **holds without an anchor** |

## The eight that need no profile

`A3`, `A5`, `A6`, `A8`, `A9`, `B6`, `B7` and `B8` work in any repository with no anchor
at all — by the content of the diff, not by path. That is the review floor for a
repository that does not yet have a confirmed profile. The other ten depend on the
profile, and **say so out loud** when it is missing.

## How a class stops being not assessable

Two ways out, both versioned in the profile and reviewed in a proposal — never through a
comment on an item, because what will hold for every future review of that repo needs
peer review:

1. **Supply the anchor.** `anchors.<class>` with `paths` or `patterns`; a §B class also
   needs `verification`, stating how a person proves it.
2. **Record that it does not apply.** `anchors.<class>.not_applicable` with `reason`,
   `approver` and `date`. A repository with no interface does not need B5 — but it does
   need to say so, with a name and a date.
