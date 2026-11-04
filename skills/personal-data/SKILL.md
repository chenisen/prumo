---
name: personal-data
description: How to find a personal-data leak in a code change — which fields count as personal, which destinations are forbidden, and what minimisation and stated purpose demand of a change. The concrete fields and destinations come from the repository profile, never from this skill. Use when reviewing the privacy of a diff.
---

# Personal data in a diff review

This skill carries the **how**. The **what** — which fields this project treats as
sensitive and which destinations are forbidden — comes from `sensitive_data` in the
repository profile, because that answer changes from project to project.

Much of the reasoning below is framed by the LGPD (*Lei Geral de Proteção de Dados*),
Brazil's general data protection law, which is close in shape to the GDPR: it defines
personal data, singles out a narrower class of sensitive data, and requires that every
use of personal data have a declared purpose and be limited to what that purpose needs.
You do not need to know the statute to use this skill — the two principles it leans on,
minimisation and purpose, travel to any privacy regime.

## The principle that decides almost everything

**Minimisation.** A piece of personal data may sit somewhere only if there is a declared
purpose for it to be there. The review question is not "is this field secret?", it is
**"why does this field need to be in this response, in this log, in this telemetry?"**.

## The four destinations that leak most

| Destination | What to look for in the diff |
|---|---|
| **API response** | a serialiser that started exposing an attribute; a whole object returned instead of a projection; an eager-loaded relation dragging the other side's fields along |
| **Log** | a person's identifier, government document or contact details inside a log message; a whole object logged; a request body logged on error |
| **Error message** | internal detail handed back to the client; the data subject's own data echoed in validation; a stack trace in production |
| **Telemetry and metrics** | a metric label carrying an identifier; an analytics event carrying a document number; a distributed trace carrying a body |

## Categories of data, by risk

- **Direct identifier** — tax or civil identity document, full name.
- **Contact** — e-mail, telephone, address.
- **Sensitive data in the legal sense** — racial or ethnic origin, religious belief,
  political opinion, trade-union membership, health, sex life, genetic data, biometric
  data. Exposure here is always **critical**.
- **Data about a child or adolescent** — needs a legal basis of its own; treat it as
  sensitive.
- **Indirect identifier** — fine-grained geolocation, device identifier, the pair (date
  of birth + municipality). On its own each looks harmless; combined, they re-identify.

## How to classify the finding

| Situation | Severity |
|---|---|
| Sensitive data in the legal sense exposed to someone who should not see it | **critical** |
| A direct identifier or contact detail in a destination the profile forbids | **required** |
| New personal data in a response with no declared purpose | **required** — and a **human decision**: purpose is product's to decide |
| A new indirect identifier in telemetry | **nit**, with the re-identification risk named |
| A field already exposed before the change and untouched by it | **`attributable: false`** — record it, do not block on it |

## Two questions that uncover the rest

1. **Who reads this destination?** A log shipped to a third-party aggregator has a wider
   audience than a local file. The answer moves the severity.
2. **Does the data leave the subject?** Exposing the authenticated subject's own document
   to themselves is not the same as exposing it to another user — and the second is also
   a BOLA case (API1), which class A3 watches.

## What is not a finding

- Pseudonymisation the project already applies and the change preserves.
- A personal field in a response whose consumer is the authenticated data subject
  themselves, for a purpose the project has already declared.
- The absence of a control **this project does not adopt** — never demand a practice the
  repository did not choose.
