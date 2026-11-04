export const meta = {
  name: 'task-review',
  description: 'Reviews a delivery already triaged: reviewers in parallel, a sceptical verifier and a manual plan',
  whenToUse: 'Called by the task-review skill after F0–F3. Do not invoke it directly: with no package on disk there is nothing to review.',
  phases: [
    { title: 'Review', detail: 'the reviewers triage activated, each over its own slice' },
    { title: 'Verification', detail: 'one sceptic per blocking finding, in a clean context' },
    { title: 'Manual plan', detail: 'a script for what no test reaches' },
  ],
}

// The judge does NOT live here. It is a pure function in Python, in the skill, for two
// reasons: it is the only way to have tests over it in this environment, and whoever
// judges must not sit in the same context as whoever found. This workflow hands back
// findings; the skill judges.

// `args` arrives as an object when the caller passes real JSON, and as a STRING when it
// passes serialised JSON. Destructuring a string gives undefined for everything and the
// workflow dies on the first read, far from the cause. Tolerating both forms here is
// cheaper than diagnosing `plan.reviewers is undefined` all over again.
//
// The third form is the one that actually happens: somebody invokes this workflow by
// hand with the item's URL, because `/prumo:task-review <url>` is how the SKILL is
// called and the two look alike. `JSON.parse` then fails with `Unexpected identifier
// "https"`, which says nothing about what went wrong or what to do. This workflow is not
// an entry point: it reviews a package `review.py prepare` already built.
function readArgs(raw) {
  if (raw && typeof raw === 'object') return raw
  if (typeof raw !== 'string') {
    throw new Error(
      'task-review received no arguments. It is not an entry point: run ' +
      '`review.py prepare …` first — it prints this workflow\'s invocation, filled in.')
  }
  const text = raw.trim()
  if (!text.startsWith('{')) {
    throw new Error(
      `task-review received \`${text.slice(0, 60)}\` where it expected the package. ` +
      'It is not an entry point — it reviews a package that already exists on disk. ' +
      'To review a delivery, call the SKILL (`/prumo:task-review <url>`), or run ' +
      '`review.py prepare --repo R --proposal N --item C --item-file …`, which prints ' +
      'this workflow\'s invocation with every argument filled in.')
  }
  try {
    return JSON.parse(text)
  } catch (e) {
    throw new Error(
      `task-review could not parse its arguments as JSON (${e.message}). They should be ` +
      'the object `review.py prepare` printed — copy it whole, including `package`, ' +
      '`plan`, `profile` and `classes`.')
  }
}

const input = readArgs(args)
for (const field of ['package', 'plan', 'classes']) {
  if (!input[field]) {
    throw new Error(
      `task-review was given arguments with no \`${field}\`. That object comes from ` +
      '`review.py prepare`, whole — a hand-written subset leaves a reviewer reading a ' +
      'file that is not there, and saying nothing about it.')
  }
}
const { package: bundle, plan, profile, classes } = input

// The agents TRAVEL WITH THE METHOD: they are files of this very repository, registered
// by the plugin, not something whoever installs has to copy into `.claude/agents/`. The
// harness exposes them with the plugin's name in front, so the prefix is part of the
// address — and it comes from the plan, not hard-coded here, so that a checkout outside a
// plugin (agents loose in `.claude/agents/`) keeps working with `agent_prefix: ""`.
const PREFIX = plan.agent_prefix ?? 'prumo:'
const agentFor = (name) => `${PREFIX}${name}`

const FINDINGS_SCHEMA = {
  type: 'object',
  required: ['findings', 'checked', 'not_applicable'],
  additionalProperties: false,
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        required: ['id', 'reviewer', 'severity', 'class', 'claim', 'evidence',
                   'consequence', 'form', 'diff_verifiable', 'status', 'attributable'],
        additionalProperties: false,
        properties: {
          id: { type: 'string' },
          reviewer: { type: 'string' },
          severity: { enum: ['critical', 'required', 'nit', 'optional', 'fyi'] },
          class: { type: ['string', 'null'] },
          claim: { type: 'string' },
          evidence: {
            type: 'array', minItems: 1,
            items: {
              type: 'object', required: ['file'], additionalProperties: false,
              properties: { file: { type: 'string' }, line: { type: ['integer', 'null'] },
                            excerpt: { type: ['string', 'null'] } },
            },
          },
          consequence: { type: 'string' },
          form: { type: ['string', 'null'] },
          diff_verifiable: { type: 'boolean' },
          attributable: { type: 'boolean' },
          status: { enum: ['proposed'] },
          owasp_category: { type: ['string', 'null'] },
        },
      },
    },
    // Silence does not count as "nothing found": every reviewer says what it looked at.
    checked: { type: 'array', items: { type: 'string' } },
    not_applicable: { type: 'array', items: { type: 'string' } },
  },
}

const VERIFICATION_SCHEMA = {
  type: 'object',
  required: ['verdict', 'reason', 'evidence', 'attributable'],
  additionalProperties: false,
  properties: {
    verdict: { enum: ['confirmed', 'refuted', 'uncertain'] },
    reason: { type: 'string' },
    evidence: { type: 'array', items: { type: 'object' } },
    attributable: { type: 'boolean' },
  },
}

const SCRIPT_SCHEMA = {
  type: 'object',
  required: ['scripts_manual'],
  additionalProperties: false,
  properties: {
    scripts_manual: {
      type: 'array',
      items: {
        type: 'object',
        required: ['trigger', 'goal', 'environment', 'data', 'steps',
                   'expected_result', 'evidence_to_collect', 'acceptance'],
        additionalProperties: false,
        properties: {
          trigger: { type: 'string' }, goal: { type: 'string' },
          environment: { type: 'string' }, data: { type: 'array', items: { type: 'string' } },
          steps: { type: 'array', items: { type: 'string' } },
          expected_result: { type: 'string' },
          evidence_to_collect: { type: 'string' }, acceptance: { type: 'string' },
          cases: { type: 'array', items: { type: 'object' } },
        },
      },
    },
  },
}

// The context is THE SAME for every reviewer, so that the prefix cache carries across
// repositories. What changes are the paths — and those are data, not prompt.
// The small package files arrive AS CONTENT, not as paths. `review.py prepare` reads
// them once and puts them in `inline`; without that, every reviewer read the same seven
// files one by one, and each read is a round trip whose cost is the whole accumulated
// context re-read. Measured on one real review: 81 reads across six agents, 84% of 7.9M
// tokens in `cache_read`.
//
// The patch stays a PATH on purpose: it is the big payload, it differs per slice, and
// carrying it here would pay for it in every agent that does not need it — the opposite
// of what the slices are for.
const inlined = (input.inline ?? {})

const embed = (name, label) => inlined[name]
  ? `--- ${label} (${name}) ---\n${inlined[name]}\n--- end ${name} ---`
  : `${label}: ${bundle}/${name}  (not in this package)`

// The INVARIANT part comes first and the varying part last, so the prefix cache has the
// longest possible run to match on. The slice path is the only thing that differs
// between reviewers, so it goes at the end.
const context = (slice) => [
  `Package: ${bundle}`,
  embed('manifest.json', 'Manifest'),
  embed('capabilities.json', 'Capabilities of this environment'),
  embed('profile.yml', 'Repository profile'),
  embed('classes.json', 'Triaged classes'),
  embed('criteria.json', 'Criteria from the item'),
  embed('item.md', 'Normalised item (description + comments)'),
  embed('proposal.md', "The proposal's own description, as its author wrote it"),
  embed('out-of-scope.json', 'Out of scope as declared by the item'),
  `Conventions to read BEFORE judging style or structure: ${JSON.stringify(plan.conventions)}`,
  plan.conventions.length === 0
    ? 'The conventions list is EMPTY: say so in your return and judge only what is universal.'
    : '',
  'Everything above is already here: do not read those files again.',
  `Slice to read: ${bundle}/slices/${slice}.patch`,
].filter(Boolean).join('\n')

phase('Review')
log(`${plan.reviewers.length} reviewers activated; ${plan.skipped.length} skipped by triage`)
for (const s of plan.skipped) log(`skipped: ${s.agent} — ${s.reason}`)

const reviews = await parallel(plan.reviewers.map((r) => () =>
  agent(context(r.slice), {
    agentType: agentFor(r.agent),
    label: r.agent,
    phase: 'Review',
    schema: FINDINGS_SCHEMA,
    ...(r.model ? { model: r.model } : {}),
  })))

const alive = reviews.filter(Boolean)
if (alive.length < plan.reviewers.length) {
  log(`WARNING: ${plan.reviewers.length - alive.length} reviewer(s) did not return — ` +
      'the report will record the slice nobody read')
}
const findings = alive.flatMap((r) => r.findings || [])
const coverage = {
  checked: alive.flatMap((r) => r.checked || []),
  not_applicable: alive.flatMap((r) => r.not_applicable || []),
  reviewers_without_return: plan.reviewers.length - alive.length,
}

phase('Verification')
// Only what blocks comes through here. A nit or an FYI does not pay for an agent.
const blocking = findings.filter((f) => ['critical', 'required'].includes(f.severity))
log(`${blocking.length} of ${findings.length} findings block and go to the verifier`)

const verified = await pipeline(blocking, (f) =>
  agent([
    'Try to REFUTE this finding. You have not seen the reasoning of whoever raised it.',
    `Claim: ${f.claim}`,
    `Alleged consequence: ${f.consequence}`,
    `Evidence pointed at: ${JSON.stringify(f.evidence)}`,
    `Read the code at: ${bundle}/slices/complete.patch and in the working repository.`,
    'When in doubt, return `uncertain` — never `confirmed`.',
  ].join('\n'), {
    agentType: agentFor('finding-verifier'),
    label: `verify:${f.id}`,
    phase: 'Verification',
    schema: VERIFICATION_SCHEMA,
  }).then((v) => ({ ...f, status: v ? v.verdict : 'uncertain',
                    attributable: v ? v.attributable : f.attributable,
                    verifier_reason: v ? v.reason : 'the verifier did not return' })))

const byId = new Map(verified.filter(Boolean).map((f) => [f.id, f]))
const final = findings.map((f) => byId.get(f.id)
  || (['critical', 'required'].includes(f.severity)
      ? { ...f, status: 'uncertain', verifier_reason: 'not verified' }
      : { ...f, status: 'confirmed' }))

phase('Manual plan')
const needsScript = classes.filter(
  (c) => (c.section === 'B' && c.state === 'fired')
      || (c.state === 'not_assessable' && !c.not_applicable))

const scripts = needsScript.length
  ? await agent([
      'Write a manual verification script for each item below.',
      `Classes: ${JSON.stringify(needsScript.map((c) => ({ id: c.id, reason: c.reason })))}`,
      `Profile (the anchor says where and against what to verify): ${bundle}/profile.yml`,
      `Criteria from the item: ${bundle}/criteria.json`,
      'The expected result is written BEFORE executing.',
    ].join('\n'), { agentType: agentFor('manual-verification-planner'), phase: 'Manual plan',
                    schema: SCRIPT_SCHEMA })
  : null

return { findings: final, coverage, scripts_manual: scripts ? scripts.scripts_manual : [] }
