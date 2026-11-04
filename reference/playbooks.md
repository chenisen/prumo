# Manual verification playbooks

The techniques live in the `caixa-preta` skill, which `manual-verification-planner`
loads. This file is the index by trigger — **where** and **against what** comes from the
profile.

## Mandatory structure

Trigger · objective · environment · data · steps · **predicted result** · evidence to
collect · acceptance criterion.

**The predicted result is written before executing.** That is what separates verification
from observation. Whoever measures first and describes afterwards can rationalise any
number.

## By trigger

| Class | Core technique | Evidence | What the profile supplies |
|---|---|---|---|
| criterion not verifiable | equivalence partitioning and boundary values, derived from the text of the criterion | table of cases with the output obtained | the dev environment |
| **B1** derived artefact | produce the artefact in dev and read it | predicted fields and values; dump of the artefact | what the artefact is and how to produce it |
| **B2** legacy internal system | black box against the system's dev environment | log of the calls and the behaviour on each failure | which systems, and why the suite does not cover them |
| **B3** external supplier | the supplier's sandbox, with timeout, server error and invalid payload | log of the calls and the behaviour on each failure | which suppliers, and where |
| **B4** aggregate across two stores | query on the source confronted with the published artefact | predicted number of divergences (zero) and the query used | the two stores and the query |
| **B5** interface | exploratory session with a mission charter and a fixed time box, on top of the end-to-end test | session notes and open problems | whether there is an interface, and where |
| **B6** data migration | run against a copy of the real dev schema, and test the rollback | predicted count before, checked after | the dev schema |
| **B7** volume | measure in dev with real volume, not with three rows | p95, number of queries and peak memory, before and after | which paths are hot |
| **B8** formal process | the organisation's process checklist | recorded sign-off | the name of the process |
| **A3** authorisation and scope | two subjects, A and B: with A's credential, request a resource of B's by identifier, list, and filter | denial, and a list with none of B's items; requests and responses saved | the dev credentials |
| **A2** consumer contract | compare the response before and after in dev | diff of the payload and the sign-off of each consumer | the list of consumers |
| dynamic security | basic scan in dev and manual injection into the new fields | scan report and the cases tested | the dev URL |

## A class that came out `not_assessable`

The playbook says so out loud. The person needs to know they are covering a gap in the
profile, and that the permanent way out is to **version the anchor** — not to repeat the
check by hand on every proposal.

## Closing

All evidence goes back to the item:

```
#evidence <class>  <result obtained>  <link or attachment>
```

`closure.py` reads this. The next step is to **re-judge**, not to re-review: no reviewer
runs again when all that arrived was a piece of evidence.
