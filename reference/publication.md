# Publication: three external acts, three yeses

Read **only at the end**, once there is a verdict. Nothing here enters the context of
anyone doing the reviewing.

## The distinction most often lost

**Authorising publication is not agreeing with the verdict.** A person may authorise the
comment and disagree with its content — and that disagreement is evaluation data,
recorded in `runs.jsonl` as `verdict_contested`.

## The three acts

| Act | When | Requires |
|---|---|---|
| **Comment on the item** | whenever there is a verdict | an explicit yes |
| **Comment on the proposal** | whenever there is a verdict | an explicit yes, separate from the previous one |
| **Approving the proposal** | **only** on `APPROVED` | an explicit yes, separate from both of the above |

One yes never drags the next one along. Asking for all three at once defeats the point.

**What never happens:** merge, moving the item, push. The hook blocks it; the bot account
does not hold the right; and it is written down here so that nobody reintroduces it by
accident.

## Report

The report is written **always**, without asking for anything, at:

```
<reports>/<repo>/<item>-<slug>/review-<date>.md
```

That is the only place this skill writes. An intervention verdict stays **only** in the
local report until someone authorises publication.

**The path never goes into a published comment.** It is local to whoever ran the review,
and the people reading the item or the proposal are not on that machine — naming it tells
them a report exists without letting them read a line of it. The item comment therefore
carries what decides inside itself (see the skill), and the proposal-comment templates
below do the same.

## The board's HTML

The comment on the item accepts a subset: `p`, `strong`, `em`, `code`, `ul`, `ol`, `li`,
`span`, `br`, `a`. Everything outside that is escaped.

**The escaping is done in code** (`render.py`), never by instructing the model. A
placeholder such as `<id>` that reaches the HTML raw disappears from the comment and
takes the formatting with it — this has happened. `html.escape(..., quote=False)`
preserves quotation marks and accented characters, which are common outside English and
do not need to become entities.

Headings use `<span class="text-big">`, which is what the board understands.

## Reading the proposal without a token

With no token in the environment, **the proposal's metadata cannot be read**. What still
works:

- the **diff** comes out of `refs/merge-requests/<iid>/head`, which the server publishes
  and `resolver.py` fetches over SSH;
- the **item** comes from the argument, or is derived from the subjects of the commits —
  and a derived one is **confirmed by a person** before it counts.

Without a token, publication is manual: the harness writes the report and hands over the
comments ready for someone to paste. That is a capability measured by preflight, not a
failure.
