---
name: owasp-asvs
description: Map of the OWASP Top 10:2025 and OWASP API Security Top 10 categories, with the verifiable ASVS 5.0 list, for reading a code change in search of abuse. Neutral of language and framework. Use when reviewing the security of a diff, classifying a vulnerability, or deciding whether a finding is real.
---

# OWASP for diff review

Knowledge, not method: what to do with a finding is decided by the harness that loads
this skill. Nothing here names a project, a language or a tool.

## How to use it in a review

1. For each file in the slice, walk the categories below and ask **whether this change
   opens the way in**, not whether the category exists in the world.
2. Every finding names **file and line** and describes the **abuse scenario in one
   sentence**: who abuses it, what they get, through where.
3. **List the categories you checked and the ones that do not apply to this slice.**
   Silence does not count as "nothing found" — a reviewer who does not say what they
   looked at cannot be measured.
4. A finding that touches authorisation scope or a secret is a **human decision**:
   state the question and the options, and leave the fix shape null.

## OWASP Top 10:2025

| Code | Category | What to look for in a diff |
|---|---|---|
| A01 | Broken Access Control | a permission check removed, weakened, or applied after the effect; a client-supplied identifier used directly |
| A02 | Security Misconfiguration | an insecure default switched on, debug mode, open CORS, a security header removed |
| A03 | Software Supply Chain | a new dependency, a lockfile edited by hand, a swapped origin, a post-install script |
| A04 | Cryptographic Failures | a weak algorithm, a key in code, a fixed IV, non-cryptographic randomness, TLS switched off |
| A05 | Injection | concatenation into a query, a command, a template, a file path or a header |
| A06 | Insecure Design | a flow that trusts the client, no rate limit where there is cost, a derivable secret |
| A07 | Authentication Failures | a session that never expires, a token whose signature or audience goes unverified, a weak password reset |
| A08 | Software and Data Integrity Failures | deserialisation of external data, an update applied without an integrity check |
| A09 | Logging and Alerting Failures | a security event that stopped being recorded, a log with no correlation, sensitive data inside the log |
| A10 | Mishandling of Exceptional Conditions | an empty `catch`, a failure that turns into a success, an error that leaks internal detail, a failure path with no test |

## OWASP API Security Top 10

| Code | Category | What to look for |
|---|---|---|
| API1 | **BOLA** — Broken Object Level Authorization | an object fetched by identifier without checking owner or scope. **It is the same risk the A3 class watches.** |
| API2 | Broken Authentication | credential issuing or validation altered |
| API3 | Broken Object Property Level Authorization | a sensitive field exposed in the response, or accepted on input with no allow-list |
| API4 | Unrestricted Resource Consumption | pagination removed, a missing limit, an expensive operation with no ceiling |
| API5 | Broken Function Level Authorization | an administrative route reachable by an ordinary role |
| API6 | Unrestricted Access to Sensitive Business Flows | a business flow that can be automated with no friction |
| API7 | Server Side Request Forgery | a client-supplied URL used in a server-side request |
| API8 | Security Misconfiguration | the same as A02, on the API surface |
| API9 | Improper Inventory Management | a new route left undocumented, an old version still alive |
| API10 | Unsafe Consumption of APIs | a third-party response consumed without validation |

## ASVS 5.0 — the verifiable list

Use it as a checklist of what to **ask the diff**, chapter by chapter:

- **V1 Encoding** — input validated at the edge, output encoded for the right destination.
- **V2 Authentication** — factor, credential lifecycle, recovery.
- **V3 Session** — creation, rotation, expiry, invalidation on logout.
- **V4 Authorisation** — the decision happens on the server, per object and per function.
- **V5 Validation and sanitisation** — by type, by range, by allow-list.
- **V6 Cryptography** — algorithm, key, randomness, storage.
- **V7 Error and log** — what it records, what it hides, what it alerts on.
- **V8 Data protection** — at rest, in transit, in cache, in log.
- **V9 Communication** — TLS, pinning, certificate verification.
- **V10 Configuration** — defaults, secrets, dependencies.

## Severity

- **critical** — remote exploitation without authentication, or direct exposure of a
  third party's data.
- **required** — exploitation with authentication, escalation, or a conditional leak.
- **nit / fyi** — hardening worth doing, with no demonstrable abuse path in this code.

A finding with no concrete abuse scenario **in this code** is neither critical nor
required.
