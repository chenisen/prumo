export const meta = {
  name: 'task-review',
  description: 'Reviews a delivery already triaged: reviewers in parallel, a sceptical verifier and a manual plan',
  whenToUse: 'Called by the task-review skill after F0–F3. Do not invoke it directly: with no package on disk there is nothing to review.',
  phases: [
    { title: 'Criteria', detail: "the item's acceptance criteria, extracted once with stable ids, when the package carries none" },
    { title: 'Review', detail: 'the reviewers triage activated, each over its own slice' },
    { title: 'Verification', detail: 'the same cause raised twice becomes one finding; one sceptic per blocking finding, in a clean context; the findings still open from an earlier round are re-checked at the head' },
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
// The code at the REVIEWED HEAD, checked out on its own by `review.py prepare`. Without
// it the agents read the repository's checkout, which may be on another branch or behind:
// a `required` finding about a method "that does not exist" was once confirmed by the
// verifier — the head had it, only the checkout did not.
const HEAD_TREE = input.head_tree
  ? `The repository AT THE REVIEWED HEAD is checked out at ${input.head_tree}. Read every ` +
    'repository file there — never in the repository\'s own checkout, which may be on another ' +
    'branch or behind. Only dependencies (vendor, node_modules) are not there; for those, ' +
    'the checkout.'
  : ''

// Which round this is. `full` reads the whole delivery; `delta` reads only what the push
// changed since the head the round before reviewed, and re-checks what that round left
// open; `recheck` runs no reviewer at all — from the third round on the only question is
// whether what is open was fixed, so the review converges instead of finding anew.
const ROUND = input.round ?? { mode: 'full', n: 1, open: [] }
const OPEN = ROUND.open ?? []
// The prose a person reads is written in the installation's language; identifiers,
// paths and code stay as they are in the code.
const LANGUAGE = input.language
  ? `Write every sentence a person will read — \`claim\`, \`consequence\`, \`form\`, ` +
    `\`record\`, \`reason\`, a script's fields — in the language \`${input.language}\`. ` +
    'Identifiers, paths and code stay exactly as they are in the code.'
  : ''

// One bar for proof, the same one the delivery's own sceptic reads by. Two readers with
// two bars made every delivery pay a round: the sceptic said "proves" and the review
// then asked for more.
const PROOF_BAR =
  'THE PROOF BAR: a criterion is proven when at least one test fails if the behaviour the ' +
  'criterion states is removed. That is all a `required` finding may ask of a test. A test ' +
  'that could pin more — an argument, the value of a constant, a timeout, one more branch — ' +
  'is a `nit`, unless the criterion states that detail in its own words. A mutant the ' +
  "delivery's digest shows killed is proven: never ask for it again."

// Where a blocking demand comes from. A reviewer that charges a rule nobody wrote down
// is the one failure a person cannot check from the report.
const BASIS_RULE =
  'A `required` or `critical` finding says where its demand comes from, one of three: ' +
  '`criterion` — the id of the criterion of the item it breaks; `rule` — `{file, quote}`, ' +
  "the project's own sentence copied VERBATIM from the profile, the repository's " +
  'instructions or a convention file (the harness looks the quote up in that file, and a ' +
  'quote it does not find is no rule); or a DEFECT — the code doing the wrong thing on a ' +
  'real path, with the input and the wrong result stated in `consequence`. A demand with ' +
  'none of the three — "should also", "would be safer", "is not asserted" — is a `nit` at ' +
  'most. Never charge a rule the project did not write, and never treat as missing what ' +
  'the item did not ask for.'

// A class the triage fired matched a path. Whether the change went beyond the item is a
// reading, and it is the reviewer's.
const CLASS_RULE =
  'A §A class marked `fired` in the triaged classes is a QUESTION put to you, not a ' +
  'finding: the triage matched a path or a pattern, nothing more. Answer it by reading. ' +
  'Raise a finding with that `class` (and `form: null`) only when, on that surface, the ' +
  'change does something the item did not ask for, or settles something only a person ' +
  'can settle. A change the item itself asks for is a decision already taken: raise nothing.'

// The agents TRAVEL WITH THE METHOD: they are files of this very repository, registered
// by the plugin, not something whoever installs has to copy into `.claude/agents/`. The
// harness exposes them with the plugin's name in front, so the prefix is part of the
// address — and it comes from the plan, not hard-coded here, so that a checkout outside a
// plugin (agents loose in `.claude/agents/`) keeps working with `agent_prefix: ""`.
const PREFIX = plan.agent_prefix ?? 'prumo:'
// The model follows the task's demand. The spine resolved each reviewer's tier into
// `plan.reviewers[].model`; what remains here are the choices only the workflow can
// make at runtime, picked from `plan.models` — the installation's name per tier. An
// absent map or tier spreads nothing, and the agent inherits the session's model.
const MODELS = plan.models ?? {}
const tiered = (tier) => (MODELS[tier] ? { model: MODELS[tier] } : {})
const agentFor = (name) => `${PREFIX}${name}`

// How to hand the result back. Measured over 42 workflow runs (2026-11-12): 40 of 263
// StructuredOutput calls failed, 250k output tokens — the costliest component — spent on
// retries; 29 of the 31 unparseable returns began at `"checked": [` with long free text
// carrying double quotes, parentheses and PHP namespace backslashes the model did not
// escape, and the other failures wrapped the whole JSON as a string under `input`. Size
// does not predict it (median of the failures equals the median of the successes); the
// shape of the strings does. The rule travels with every agent prompt of this workflow.
const RETURN_RULE =
  'HOW TO RETURN: call StructuredOutput once, with the object itself as the tool input — ' +
  'never a JSON string, never wrapped under `input`. Put `findings` first. Every string in ' +
  '`checked` and `not_applicable` is one short plain phrase: no double quotes inside it, no ' +
  'backslashes (write a PHP namespace with slashes or by its short name), no code excerpt. ' +
  'Excerpts belong in `evidence.excerpt`, short.'

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
          criterion: { type: ['string', 'null'] },
          rule: {
            type: ['object', 'null'], required: ['file', 'quote'], additionalProperties: false,
            properties: { file: { type: 'string' }, quote: { type: 'string' } },
          },
          // The consequence lands on a consumer the item does not name — a caller, a route,
          // another command — reached because the change widened something it shares.
          beyond_item: { type: ['boolean', 'null'] },
        },
      },
    },
    // The reading of each criterion the item states, with its record — a fact, never a
    // finding id. Optional: a reviewer that read no criterion returns none. Decided
    // against the delivery's record when one travels with the package.
    criteria: {
      type: 'array',
      items: {
        type: 'object', required: ['id', 'state', 'record'], additionalProperties: false,
        properties: { id: { type: 'string' }, state: { enum: ['met', 'not_met', 'not_verifiable'] },
                      record: { type: ['string', 'null'] },
                      // `qa` when only running the delivered system decides the criterion:
                      // it goes to QA, after the approval, and holds nothing here.
                      decided_by: { type: ['string', 'null'], enum: ['qa', 'suite', null] } },
      },
    },
    // Silence does not count as "nothing found": every reviewer says what it looked at.
    checked: { type: 'array', items: { type: 'string' } },
    not_applicable: { type: 'array', items: { type: 'string' } },
    // What the REPOSITORY does, as opposed to what this delivery did. Optional, and not
    // part of the review: it never becomes a finding, never closes a gate and never
    // reaches the verdict. `knowledge.py` accrues it across reviews so the next reviewer
    // does not rediscover the same shape from zero — which is the biggest single line of
    // this harness's bill. A reviewer that has nothing to say here says nothing.
    conventions_observed: {
      type: 'array',
      items: {
        type: 'object',
        required: ['statement', 'evidence'],
        additionalProperties: false,
        properties: {
          statement: { type: 'string' },
          evidence: {
            type: 'array', minItems: 1,
            items: {
              type: 'object', required: ['file'], additionalProperties: false,
              properties: { file: { type: 'string' }, line: { type: ['integer', 'null'] } },
            },
          },
        },
      },
    },
  },
}

const VERIFICATION_SCHEMA = {
  type: 'object',
  required: ['verdict', 'reason', 'evidence', 'attributable', 'consequence_kind'],
  additionalProperties: false,
  properties: {
    verdict: { enum: ['confirmed', 'refuted', 'uncertain'] },
    reason: { type: 'string' },
    evidence: { type: 'array', items: { type: 'object' } },
    attributable: { type: 'boolean' },
    // The second reading of severity. `judge.py` charges a blocking label whose
    // consequence is `record` or `convention` as a nit — see SEVERITY_READ_DOWN there.
    consequence_kind: { enum: ['behaviour', 'proof', 'proof_extra', 'record', 'convention'] },
  },
}

// The same cause raised by more than one reviewer: groups of finding ids, nothing else.
const GROUPS_SCHEMA = {
  type: 'object',
  required: ['groups'],
  additionalProperties: false,
  properties: {
    groups: { type: 'array', items: { type: 'array', items: { type: 'string' } } },
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
// F2b lives HERE, not in the harness's memory. `criteria.json` cannot exist when
// `prepare` runs — it is an agent's output — and for a while it was the person driving
// the review who had to remember to run the extractor between `prepare` and this
// workflow. Twice it was forgotten and the judge ran with ledger 1 empty; once it was
// run in parallel with the reviewers, who then numbered the criteria their own way and
// every reading fell on the floor. The extractor runs first, its list goes into every
// reviewer's context with the ids the readings must use, and the list is returned so
// that `conclude --workflow-result` writes it beside the package.
const EXTRACT_SCHEMA = {
  type: 'object',
  required: ['criteria', 'out_of_scope'],
  additionalProperties: false,
  properties: {
    criteria: {
      type: 'array',
      items: {
        type: 'object', required: ['id', 'text', 'state', 'record'], additionalProperties: false,
        properties: { id: { type: 'string' }, text: { type: 'string' },
                      state: { enum: ['not_verifiable'] }, record: { type: 'null' },
                      evidence: { type: 'array' } },
      },
    },
    out_of_scope: { type: 'array', items: { type: 'string' } },
    note: { type: ['string', 'null'] },
  },
}

// The harness names the item's list lines (`criteria-candidates.json`, ids `L-n`); the
// extractor only LABELS them; and the one rule that wobbled between runs — "the five
// tests above" as one criterion or as five — is code: a summary IS its parts. Mirrored
// in `review.py build_criteria`, which writes the file at `conclude` from the labels.
const LABEL_SCHEMA = {
  type: 'object',
  required: ['labels'],
  additionalProperties: false,
  properties: {
    labels: {
      type: 'array',
      items: {
        type: 'object', required: ['id', 'role'], additionalProperties: false,
        properties: { id: { type: 'string' },
                      role: { enum: ['criterion', 'out_of_scope', 'context', 'summary'] },
                      of: { type: 'array', items: { type: 'string' } } },
      },
    },
    note: { type: ['string', 'null'] },
  },
}
const buildCriteria = (candidates, labels) => {
  const byId = new Map(candidates.map((c) => [c.id, c]))
  const role = new Map((labels || []).filter((l) => l && byId.has(l.id)).map((l) => [l.id, l]))
  const chosen = []
  for (const c of candidates) {
    const l = role.get(c.id)
    if (!l) continue
    if (l.role === 'criterion') chosen.push(c.id)
    else if (l.role === 'summary') {
      const parts = (l.of || []).filter((x) => byId.has(x) && x !== c.id)
      chosen.push(...(parts.length ? parts : [c.id]))
    }
  }
  const ordered = [...new Set(chosen.sort((a, b) => byId.get(a).line - byId.get(b).line))]
  return {
    criteria: ordered.map((i, k) => ({ id: `AC-${k + 1}`, text: byId.get(i).text,
                                       state: 'not_verifiable', record: null, evidence: [], candidate: i })),
    out_of_scope: candidates.filter((c) => role.get(c.id)?.role === 'out_of_scope').map((c) => c.text),
  }
}

phase('Criteria')
let extracted = null
let labels = null
const candidates = inlined['criteria-candidates.json'] ? JSON.parse(inlined['criteria-candidates.json']) : []
if (inlined['criteria.json']) {
  log('criteria already in the package — the extractor is not paid twice')
} else if (!inlined['item.md']) {
  log('WARNING: no item in the package — nothing to extract criteria from; ledger 1 stays empty')
} else if (candidates.length) {
  const labelled = await agent([
    'LABEL each candidate line of the item below. You do not cut the item: the harness',
    'already named its list lines, `L-1`, `L-2`, … Return one label per id:',
    '`criterion` — a condition the delivery must satisfy; `out_of_scope` — what the item',
    'says is NOT part of this delivery; `context` — background, origin, suggestion, a',
    'premise; `summary` with `of: [ids]` — a line that restates other lines as a whole',
    '("the five tests above", "all of the above"). Never decide whether a criterion was',
    'met. Label every id; an id you leave out is treated as context.',
    `Candidates: ${inlined['criteria-candidates.json']}`,
    embed('item.md', 'Normalised item (description + comments)'),
    RETURN_RULE,
  ].join('\n'), { agentType: agentFor('criteria-extractor'), label: 'criteria-extractor',
                  phase: 'Criteria', schema: LABEL_SCHEMA, ...tiered('light') })
  if (labelled) {
    labels = labelled.labels
    extracted = buildCriteria(candidates, labels)
    inlined['criteria.json'] = JSON.stringify(extracted.criteria, null, 2)
    inlined['out-of-scope.json'] = JSON.stringify({ out_of_scope: extracted.out_of_scope }, null, 2)
    log(`${candidates.length} candidate lines labelled → ${extracted.criteria.length} criteria; ${extracted.out_of_scope.length} out of scope`)
  } else {
    log('WARNING: the extractor returned no labels — the reviewers read the item without a criteria list')
  }
} else {
  extracted = await agent([
    'Extract the acceptance criteria and the out-of-scope list of the item below.',
    `THE ITEM UNDER REVIEW, and no other: ${bundle}/manifest.json names it. Do not read`,
    'any other package, report or item on disk, and do not read code.',
    'One criterion per bullet or sentence the item states as a condition, in the order',
    'they appear, ids `AC-1`, `AC-2`, … Never split a bullet into sub-criteria and never',
    'keep both a whole and its parts: a bullet naming two mutants is ONE criterion.',
    'Keep the text in the item\'s own words and language. `state` is always',
    '`not_verifiable` and `record` is null: whether it was met is decided by whoever',
    'reads the code, later.',
    'If the item carries no legible criterion at all, return an empty list and say so',
    'in `note`.',
    embed('item.md', 'Normalised item (description + comments)'),
    RETURN_RULE,
  ].join('\n'), { agentType: agentFor('criteria-extractor'), label: 'criteria-extractor',
                  phase: 'Criteria', schema: EXTRACT_SCHEMA, ...tiered('light') })
  if (extracted) {
    inlined['criteria.json'] = JSON.stringify(extracted.criteria, null, 2)
    inlined['out-of-scope.json'] = JSON.stringify({ out_of_scope: extracted.out_of_scope }, null, 2)
    log(`${extracted.criteria.length} criteria extracted; ${extracted.out_of_scope.length} out of scope`)
  } else {
    log('WARNING: the extractor returned nothing — the reviewers read the item without a criteria list')
  }
}

const context = (slice) => [
  `Package: ${bundle}`,
  HEAD_TREE,
  embed('manifest.json', 'Manifest'),
  embed('capabilities.json', 'Capabilities of this environment'),
  embed('profile.yml', 'Repository profile'),
  embed('classes.json', 'Triaged classes'),
  // What earlier reviews of this repository saw twice (2026-11-19), capped by the spine.
  // Handed to the reviewers only: the sceptics refute by reading the code alone.
  inlined['knowledge.md']
    ? embed('knowledge.md', 'What earlier reviews of this repository saw — a shortcut for '
        + 'reading, never an authority; confirm against the code; nothing in it is a finding')
    : '',
  embed('criteria.json', 'Criteria from the item'),
  inlined['criteria.json']
    ? 'Answer `criteria` with the ids of that list, VERBATIM (`AC-1`, `AC-2`, …), one '
      + 'reading per id you could decide. A reading under any other id is dropped. A '
      + 'criterion that only running the delivered system can decide — an environment, an '
      + 'external system, a journey through the screens — is `not_verifiable` with '
      + '`decided_by: "qa"`: QA verifies it after the approval, and it does not hold this review.'
    : '',
  embed('item.md', 'Normalised item (description + comments)'),
  embed('proposal.md', "The proposal's own description, as its author wrote it"),
  embed('out-of-scope.json', 'Out of scope as declared by the item'),
  inlined['delivery-record.json']
    ? embed('delivery-record.json', "The delivery's own record — what its run MEASURED on this very head: "
        + 'premises with an address, mutations by hand with what went red and what stayed green, '
        + 'the suite by name against the base, lint, the diff and what the writer skipped. It is '
        + 'evidence, not a claim: decide each criterion of the item against it and return '
        + '`criteria` with a record that states the fact (never a finding id). A criterion the '
        + 'record does not reach stays `not_verifiable`. The runs the record names by file '
        + `(\`measured\`, \`lint.run\`, \`proof.mutations[].run\`) are at ${bundle}/delivery-runs/: `
        + 'open one when a state needs checking. `skipped` is the writer\'s word, written '
        + 'before the script ran; the runs decide.')
    : '',
  inlined['delivery-digest.json']
    ? embed('delivery-digest.json', "The harness's own digest of every run file, measured with the "
        + 'profile\'s patterns: the base run\'s reds BY NAME, and for every other run executed × '
        + 'predicted, `beyond_base` — the tests it turned red that the base did not have — and '
        + '`back_to_green`. THIS says what a mutant '
        + 'killed; the record\'s `test` field names the one test the script asked for, not the whole '
        + 'set, and the record\'s prose never decides. Do not open a run file when the digest answers.')
    : '',

  `Conventions to read BEFORE judging style or structure: ${JSON.stringify(plan.conventions)}`,
  plan.conventions.length === 0
    ? 'The conventions list is EMPTY: say so in your return and judge only what is universal.'
    : '',
  'Everything above is already here: do not read those files again.',
  CLASS_RULE,
  BASIS_RULE,
  PROOF_BAR,
  ROUND.mode === 'delta'
    ? `THIS IS ROUND ${ROUND.n}. An earlier round reviewed this delivery up to ${ROUND.since}; ` +
      'your slice is ONLY what the push changed since. Judge what the push changed. Code the ' +
      'push did not touch was already read: a finding on it is shown and holds nothing. The ' +
      'criteria list carries the earlier reading — re-read against the head each criterion ' +
      'that is not `met`, and any the push touches. These findings are still open from the ' +
      'earlier round and are being re-checked apart; do not raise them again:\n' +
      (OPEN.map((f) => `- ${f.id}: ${f.claim}`).join('\n') || '- none')
    : '',
  LANGUAGE,
  // 2026-11-18, eleven reviews of one item: the change widened a domain class the item's
  // worker shares with an HTTP route the item never names. Three findings in a row were
  // about that route, each answered by adapting the route — mixed lines, plan count,
  // documentation — and each answer opened the next. The fix that closed it was to give
  // the worker its own entry and leave the route as the base had it.
  'When a consequence lands on a consumer the ITEM DOES NOT NAME — another caller, a route, '
    + 'a command, a second family of the same class — reached because this change widened '
    + 'something they share (an input, a validation, a class, a query), set `beyond_item: true`. '
    + 'Its `form` is then to give the item\'s own path its own entry and leave that consumer '
    + 'exactly as the base has it — never to adapt the consumer to the widening. A consumer '
    + 'the item names is `beyond_item: false`.',
  'Optional, and NOT part of your review: if reading this repository taught you '
    + 'something true of IT rather than of this change — a convention the code holds to, '
    + 'where a kind of rule lives, how the tests are shaped — return it in '
    + '`conventions_observed`, each with the file that shows it. It is kept outside the '
    + 'repository so the next review starts less blind. Never phrase it in terms of this '
    + 'delivery, and never put an identifier in it. Nothing to say there is a valid answer.',
  RETURN_RULE,
  inlined[`slices/${slice}.patch`]
    ? embed(`slices/${slice}.patch`, 'Your slice of the diff — it is HERE, do not read the file')
    : `Slice to read: ${bundle}/slices/${slice}.patch`,
].filter(Boolean).join('\n')

phase('Review')
log(`${plan.reviewers.length} reviewers activated; ${plan.skipped.length} skipped by triage`)
for (const s of plan.skipped) log(`skipped: ${s.agent} — ${s.reason}`)

// What a reviewer is told when its first attempt came back empty. The cause measured on
// 2026-11-04 was a turn budget spent entirely on reading: the agent read the change and
// ran out before it could answer. Repeating the same prompt would exhaust it the same
// way, so the retry changes the instruction, not just the dice.
const RAN_OUT = [
  'A PREVIOUS ATTEMPT AT THIS REVIEW RETURNED NOTHING — it spent its whole turn budget',
  'reading and never answered. Read LESS this time: open only what you cannot judge',
  'without, and return your structured answer well before you run out. A partial answer',
  'that names in `checked` what you did not reach is worth far more than no answer at',
  'all — a reviewer that returns nothing leaves a slice nobody read, and the review has',
  'no way to tell that apart from a slice where there was nothing to find.',
].join(' ')

// A reviewer that returns nothing has already cost a whole context AND bought a slice
// nobody read. Measured: two attempts of the same reviewer over the same change, 1.7M
// tokens spent reading and never answering, while the run carried on as though three
// reviewers had been the plan all along. One retry, and it is `coverage` that records
// whether the retry worked — silence is never allowed to pass as "nothing to find".
// How much review this delivery bought, decided by the triage and carried in the plan.
// A plan from before the effort dimension carries no level and is treated as `full` —
// the behaviour it was built under.
const EFFORT = plan.effort ?? 'full'

const review = async (r) => {
  const opts = {
    agentType: agentFor(r.agent),
    label: r.agent,
    phase: 'Review',
    schema: FINDINGS_SCHEMA,
    ...(r.model ? { model: r.model } : {}),
    // `max_turns` travels in the plan for the record, but the harness takes no per-call
    // turn cap (agent() accepts label/phase/schema/model/effort/isolation/agentType, and
    // nothing else) — so the hard ceiling stays the agent frontmatter's, and the
    // per-level budget is enforced as REASONING effort: a minimal delivery buys a low
    // gear, not a shorter leash.
    ...(EFFORT === 'minimal' ? { effort: 'low' } : {}),
  }
  const first = await agent(context(r.slice), opts)
  if (first) return first
  log(`${r.agent} returned nothing — one retry, told to read less and answer`)
  return agent(`${context(r.slice)}\n\n${RAN_OUT}`, { ...opts, label: `${r.agent} (retry)` })
}

if (ROUND.mode === 'recheck') {
  log(`round ${ROUND.n}: no reviewer runs — only the ${OPEN.length} finding(s) still open are re-checked`)
  plan.reviewers = []
}
const reviews = await parallel(plan.reviewers.map((r) => () => review(r)))

// WHICH reviewer went silent, not just how many. The judge closes gate 5 on this, and
// "one reviewer did not return" is not actionable — "nobody read the architecture
// slice" is. Read by index, because `filter(Boolean)` throws away the only thing that
// says who is missing.
const silent = plan.reviewers.filter((_, i) => !reviews[i]).map((r) => r.agent)
const alive = reviews.filter(Boolean)
if (silent.length) {
  log(`WARNING: ${silent.length} reviewer(s) did not return even after the retry — ` +
      `the slice nobody read: ${silent.join(', ')}`)
}
// A finding's id is the METHOD's to give, never the reviewer's. Each agent numbers from
// `F1` inside its own context and has no idea what the others called theirs, so three
// reviewers return three different `F1`s — and everything downstream keys by id: the
// verification map here, `by_finding` in `render.py`, the blocking lists in `judge.py`.
// Measured on 2026-11-04, on a review with four reviewers: the architecture reviewer's
// finding and the scope reviewer's finding both VANISHED, each overwritten by a copy of
// the correctness reviewer's `F1`, which then appeared three times in one report. Silent,
// and shaped exactly like a correct result — a lost finding leaves no hole to notice.
//
// `reviewer` is taken from the plan for the same reason: nothing tells the agents what to
// put there, so they wrote the name of a SKILL they had loaded, identically, which is
// what made the collision invisible to anyone reading the output.
const tagFor = (agent) =>
  (agent.replace(/-reviewer$/, '').split('-')[0] || 'rev').slice(0, 3).toUpperCase()
// Uniqueness is CONSTRUCTED, not hoped for: a profile may name a local reviewer whose tag
// collides with one of the method's own, and a collision must show as `SEC` and `SEC2`,
// never as two findings quietly becoming one.
const tags = (() => {
  const seen = new Map()
  return plan.reviewers.map((r) => {
    const base = tagFor(r.agent)
    const n = (seen.get(base) || 0) + 1
    seen.set(base, n)
    return n === 1 ? base : `${base}${n}`
  })
})()
// A later round numbers after every id the round before it gave, so a carried `COR-1`,
// a decision recorded on a `COR-2` and a new finding never share a name.
const taken = (tag) => Math.max(0, Number((ROUND.taken ?? {})[tag] ?? 0), ...OPEN
  .map((f) => String(f.id).match(/^([A-Z0-9]+)-(\d+)$/))
  .filter((m) => m && m[1] === tag).map((m) => Number(m[2])))
const raised = plan.reviewers.flatMap((r, i) =>
  (reviews[i]?.findings || []).map((f, n) => ({ ...f, reviewer: r.agent, rule: f.rule ?? null,
                                                id: `${tags[i]}-${taken(tags[i]) + n + 1}` })))
const conventionsObserved = alive.flatMap((r) => r.conventions_observed || [])
// One reading per criterion: `not_met` from anyone wins, then `met` with a record,
// else what the extractor said. A `met` with no record is a claim and is dropped.
const criteriaVerdicts = (() => {
  const byId = new Map()
  for (const r of alive) for (const c of (r.criteria || [])) {
    const have = byId.get(c.id)
    if (c.state === 'not_met') byId.set(c.id, c)
    else if (c.state === 'met' && c.record && (!have || have.state !== 'not_met')) byId.set(c.id, c)
    else if (!have && c.state === 'not_verifiable') byId.set(c.id, c)
  }
  return [...byId.values()]
})()
if (criteriaVerdicts.length) log(`${criteriaVerdicts.length} criteria read against the record`)
const coverage = {
  checked: alive.flatMap((r) => r.checked || []),
  not_applicable: alive.flatMap((r) => r.not_applicable || []),
  reviewers_without_return: silent.length,
  slices_unread: silent,
}

phase('Verification')
// The same cause raised by several reviewers is ONE finding. Each reviewer reads in its
// own context, so a fact that sits where two grounds meet comes back once per ground —
// four copies of one sentence on a measured review — and a person reads four problems.
// One light call over the claims alone, no code read; the merge itself is code.
const SEVERITY_RANK = ['critical', 'required', 'nit', 'optional', 'fyi']
const mergeGroups = (all, groups) => {
  const known = new Map(all.map((f) => [f.id, f]))
  const gone = new Set()
  const heads = new Map()
  for (const group of groups || []) {
    const members = [...new Set(group)]
      .filter((id) => known.has(id) && !gone.has(id) && !heads.has(id)).map((id) => known.get(id))
    if (members.length < 2) continue
    const head = members.reduce((x, y) =>
      SEVERITY_RANK.indexOf(y.severity) < SEVERITY_RANK.indexOf(x.severity) ? y : x)
    const rest = members.filter((m) => m !== head)
    for (const m of rest) gone.add(m.id)
    heads.set(head.id, { ...head,
      also_raised_by: [...new Set(rest.map((m) => m.reviewer))].filter((r) => r !== head.reviewer),
      criterion: head.criterion ?? rest.map((m) => m.criterion).find(Boolean) ?? null,
      rule: head.rule ?? rest.map((m) => m.rule).find(Boolean) ?? null })
  }
  return all.filter((f) => !gone.has(f.id)).map((f) => heads.get(f.id) ?? f)
}
const grouping = raised.length > 1
  ? await agent([
      'Several reviewers read the same change, each in its own context. Return the groups of',
      'finding ids that state THE SAME CAUSE — the same fact about the same code, whatever',
      'the words or the severity. Two findings on the same lines with different causes are',
      'not a group, and a finding alone is in no group. Read nothing: decide from this text.',
      ...raised.map((f) => `- ${f.id} [${f.severity}] ` +
        `${[...new Set((f.evidence || []).map((e) => e.file))].join(', ')}: ${f.claim}`),
      'HOW TO RETURN: call StructuredOutput once, with the object itself as the tool input.',
    ].join('\n'), { label: 'group:same-cause', phase: 'Verification', schema: GROUPS_SCHEMA,
                    effort: 'low', ...tiered('light') })
  : null
const findings = mergeGroups(raised, grouping && grouping.groups)
if (findings.length !== raised.length) {
  log(`${raised.length} findings raised, ${findings.length} after the same cause was merged`)
}

// Only what blocks comes through here. A nit or an FYI does not pay for an agent, and a
// finding the reviewer already marked `attributable: false` never holds the proposal, so
// refuting it buys nothing.
const blocking = findings.filter((f) => ['critical', 'required'].includes(f.severity)
                                     && f.attributable !== false)
const preExistingSevere = findings.filter((f) => ['critical', 'required'].includes(f.severity)
                                             && f.attributable === false)
log(`${blocking.length} of ${findings.length} findings block and go to the verifier`)
if (preExistingSevere.length) {
  log(`${preExistingSevere.length} severe finding(s) NOT verified — ` +
      `marked attributable:false, so they never hold the proposal: ` +
      preExistingSevere.map((f) => f.id).join(', '))
}

// In `full` the sceptic keeps its widest mandate — the complete patch and the working
// repository. Below it, it reads the SLICE the finding came from: the reviewer that
// raised it never saw more than that. `pipeline` runs every item concurrently, capped by
// the harness's own scheduler.
const sliceOf = new Map(plan.reviewers.map((r) => [r.agent, r.slice]))
const NO_VERIFIER =
  "the verifier did not return — the reviewer's word stands, not read a second time"

const verifyBlocking = () => pipeline(blocking, (f) =>
  agent([
    'Try to REFUTE this finding. You have not seen the reasoning of whoever raised it.',
    HEAD_TREE,
    `Claim: ${f.claim}`,
    `Alleged consequence: ${f.consequence}`,
    `Evidence pointed at: ${JSON.stringify(f.evidence)}`,
    EFFORT === 'full'
      ? `Read the code at: ${bundle}/slices/complete.patch and in the working repository.`
      : `Read the code at: ${bundle}/slices/${sliceOf.get(f.reviewer) ?? 'complete'}.patch ` +
        '— the slice the finding came from. Do not read beyond it: open the repository ' +
        'only if that slice alone cannot decide, and say so in your reason if you did.',
    inlined['delivery-record.json']
      ? `The delivery's record is at ${bundle}/delivery-record.json and the runs it names ` +
        `by file are at ${bundle}/delivery-runs/, digested at ${bundle}/delivery-digest.json ` +
        '(reds by name per run, and `beyond_base`: what a mutant killed). A claim about what ' +
        'that record says was or was not executed is decided by the digest or the run file, ' +
        'never by the record\'s prose.'
      : '',
    'Also say what KIND of consequence you read, in `consequence_kind`: `behaviour` (this ' +
    'code does the wrong thing on a real path), `proof` (the behaviour a criterion states ' +
    'has NO test that fails when it is removed), `proof_extra` (a test could pin more — an ' +
    'argument, a constant, a timeout, one more branch — but that behaviour already has a ' +
    'test that goes red without it), `record` (the claim is about what a document says — ' +
    'the record, the item, the digest — not about what the code does), or `convention` ' +
    '(naming, style, documentation; nothing functional). `required` means a demonstrable ' +
    'consequence in this code: a consequence of `proof_extra`, `record` or `convention` is ' +
    'charged as a nit by the judge, whatever the label, and your reading is shown beside it.',
    PROOF_BAR,
    'When in doubt, return `uncertain` — never `confirmed`.',
    LANGUAGE,
    RETURN_RULE,
  ].filter(Boolean).join('\n'), {
    agentType: agentFor('finding-verifier'),
    label: `verify:${f.id}`,
    phase: 'Verification',
    schema: VERIFICATION_SCHEMA,
    ...tiered('heavy'),
  }).then((v) => ({ ...f, status: v ? v.verdict : 'confirmed',
                    attributable: v ? v.attributable : f.attributable,
                    consequence_kind: v ? v.consequence_kind : null,
                    verifier_reason: v ? v.reason : NO_VERIFIER })))

// What an earlier round left open, read again at the head. It stood once: only a reading
// that shows it gone lets it go, so a doubt keeps it open.
const recheckOpen = () => pipeline(OPEN, (f) =>
  agent([
    'This finding was CONFIRMED on an earlier head of this delivery, and the branch has ' +
    'moved since. Decide whether it STILL STANDS at the reviewed head.',
    HEAD_TREE,
    `Claim: ${f.claim}`,
    `Consequence: ${f.consequence}`,
    `Evidence it pointed at, on the earlier head: ${JSON.stringify(f.evidence)}`,
    `What the push changed since that head: ${bundle}/slices/complete.patch`,
    'Read the code at the head, not only the patch. `refuted` — the head no longer has ' +
    'it: it was fixed, or what it pointed at is gone; say in `reason` what closed it. ' +
    '`confirmed` — it is still there. When you cannot tell, `confirmed`.',
    PROOF_BAR,
    LANGUAGE,
    RETURN_RULE,
  ].filter(Boolean).join('\n'), {
    agentType: agentFor('finding-verifier'),
    label: `recheck:${f.id}`,
    phase: 'Verification',
    schema: VERIFICATION_SCHEMA,
    ...tiered('heavy'),
  }).then((v) => ({ id: f.id, v })))

// A criterion an earlier round did not read as met, on a round where no reviewer runs.
const CRITERION_SCHEMA = {
  type: 'object',
  required: ['state', 'record'],
  additionalProperties: false,
  properties: { state: { enum: ['met', 'not_met', 'not_verifiable'] },
                record: { type: ['string', 'null'] } },
}
const OPEN_CRITERIA = ROUND.mode === 'recheck' ? (ROUND.open_criteria ?? []) : []
const recheckCriteria = () => pipeline(OPEN_CRITERIA, (c) =>
  agent([
    'An earlier round of this review did not read this criterion of the item as met, and ' +
    'the branch has moved since. Read it against the code at the reviewed head.',
    HEAD_TREE,
    `Criterion ${c.id}: ${c.text}`,
    c.record || c.note ? `What the earlier round wrote: ${c.record || c.note}` : '',
    `What the push changed since: ${bundle}/slices/complete.patch`,
    '`met` with a `record` that states the fact — the code and the test that show it; ' +
    '`not_met` with what is missing; `not_verifiable` when the code cannot decide it.',
    PROOF_BAR,
    LANGUAGE,
    RETURN_RULE,
  ].filter(Boolean).join('\n'), {
    agentType: agentFor('finding-verifier'),
    label: `recheck:${c.id}`,
    phase: 'Verification',
    schema: CRITERION_SCHEMA,
    ...tiered('heavy'),
  }).then((v) => (v ? { id: c.id, state: v.state, record: v.record } : null)))

const [verified, rechecked, reread] = await parallel([verifyBlocking, recheckOpen, recheckCriteria])
for (const c of (reread ?? []).filter(Boolean)) criteriaVerdicts.push(c)
const recheckOf = new Map((rechecked ?? []).filter(Boolean).map((r) => [r.id, r.v]))
const closed = []
const stillOpen = []
for (const f of OPEN) {
  const v = recheckOf.get(f.id)
  if (v && v.verdict === 'refuted') closed.push({ id: f.id, claim: f.claim, reason: v.reason })
  else stillOpen.push({ ...f, status: 'confirmed', carried_from: ROUND.previous_package ?? null,
                        verifier_reason: v ? v.reason
                          : 'the re-check did not return — it stood once and stays open' })
}
if (OPEN.length) log(`${closed.length} of ${OPEN.length} open finding(s) closed by the push`)

const byId = new Map((verified ?? []).filter(Boolean).map((f) => [f.id, f]))
const BELOW_BLOCKING =
  "below the blocking severities a finding decides no gate — the reviewer's word, read once"
const SKIPPED_PRE_EXISTING =
  'not verified: marked attributable:false, and a finding the change did not cause never ' +
  'holds the proposal — the verifier would decide nothing'
const final = [...stillOpen, ...findings.map((f) => byId.get(f.id)
  || (['critical', 'required'].includes(f.severity) && f.attributable !== false
      ? { ...f, status: 'confirmed', verifier_reason: NO_VERIFIER }
      : { ...f, status: 'unverified',
          verifier_reason: f.attributable === false ? SKIPPED_PRE_EXISTING : BELOW_BLOCKING }))]

phase('Manual plan')
const manualCandidates = classes.filter(
  (c) => (c.section === 'B' && c.state === 'fired')
      || (c.state === 'not_assessable' && !c.not_applicable))

// Classes that went quiet for the SAME reason get ONE script, not one each. When a
// profile declares no anchors, every unassessable class carries the identical cause —
// "this repository never said where to look" — and the honest answer to all of them is
// the same act: version the anchors. Writing nine bespoke walkthroughs for one missing
// section of one YAML file cost 64k tokens on a review whose whole delivery was tests,
// and the nine read as nine problems when they are one.
//
// Grouping is by `cause`, the stable id the triage stamps — never by `reason`, which is
// a translated sentence. A class with no cause (a §B that FIRED, which is a real finding
// about this change) is never grouped: it gets its own script.
const grouped = new Map()
const needsScript = []
for (const c of manualCandidates) {
  if (c.cause && c.state === 'not_assessable') {
    if (!grouped.has(c.cause)) grouped.set(c.cause, [])
    grouped.get(c.cause).push(c)
  } else {
    needsScript.push(c)
  }
}
for (const [cause, members] of grouped) {
  needsScript.push(members.length === 1 ? members[0] : {
    id: members.map((m) => m.id).join(', '),
    reason: `${members[0].reason}  — ${members.length} classes share this one cause ` +
            `(${cause}); write ONE script that closes it for all of them, naming each.`,
    cause,
  })
}
if (manualCandidates.length !== needsScript.length) {
  log(`${manualCandidates.length} classes need manual work, collapsed into ${needsScript.length} ` +
      `script(s): classes sharing a cause are answered by one act, not one each`)
}
// A criterion only the running system decides goes to QA after the approval, and QA's
// input is a script (rule of 2026-11-19). With no §B class fired, the planner would not
// run at all and the judge would close gate 4 for a script nobody was asked to write.
for (const c of criteriaVerdicts) {
  if (c.state === 'not_verifiable' && c.decided_by === 'qa') {
    needsScript.push({ id: c.id, reason: `acceptance criterion only the running system decides: ${c.id}` })
  }
}

// WHICH delivery these scripts are for comes first, and it is not optional. Without it
// the planner knows only a list of class ids and a package path, and it has the whole
// workspace to read: on a repository that already holds a report from a SIBLING review,
// it anchored on that one and wrote fifteen scripts for the previous item's SHAs. Every
// line of them was plausible and none of them was about the delivery under review.
// In a minimal delivery whose whole manual plan is `no_anchor`/`no_tool` groups, the
// honest answer is one sentence the triage already rendered, translated, in
// `plan.manual_fallback` — "version the anchors" — and paying a planner context to
// write it cost 64k tokens on a review whose delivery was tests. A §B class that FIRED
// has no fallback entry: that is a real finding about this change, and it keeps the
// agent.
const fallback = plan.manual_fallback ?? {}
// A grouped entry carries several ids joined by `, ` — each keeps its own anchor.
const anchorsFor = (entries) => Object.fromEntries(entries
  .flatMap((c) => String(c.id).split(', '))
  .filter((id) => (profile?.anchors ?? {})[id])
  .map((id) => [id, profile.anchors[id]]))
// A later round keeps the scripts the first one wrote: QA's input does not change with
// a correction, and the spine carried them into this package.
const carried = ROUND.mode !== 'full' && ROUND.scripts_carried
if (carried) needsScript.length = 0
const templatable = EFFORT === 'minimal' && needsScript.length > 0
  && needsScript.every((c) => c.cause && fallback[c.cause])
if (templatable) {
  log('manual plan rendered from the triage template — no planner context paid: '
      + [...new Set(needsScript.map((c) => c.cause))].join(', '))
}
const scripts = templatable
  ? { scripts_manual: [...new Set(needsScript.map((c) => c.cause))]
        .map((cause) => fallback[cause]) }
  : needsScript.length
  ? await agent([
      'Write a manual verification script for each item below.',
      `THE DELIVERY UNDER REVIEW, and no other: ${bundle}/manifest.json — it names the`,
      'item, the origin and the two SHAs. Every script must verify THAT delivery. Any',
      'report, package or item you find elsewhere on disk belongs to a different review:',
      'do not read it, and never take its item id, its SHAs or its findings as yours.',
      HEAD_TREE,
      `Classes: ${JSON.stringify(needsScript.map((c) => ({ id: c.id, reason: c.reason })))}`,
      // The anchors come inline and without `cards`: an anchor's cards are the items that
      // taught the profile the class, and a planner once wrote one into its script as the
      // item under review — provenance stopped the conclude on an id the manifest lacks.
      'The anchors of these classes (where and against what to verify): ' +
        JSON.stringify(anchorsFor(needsScript)),
      'An item id or a commit SHA inside an anchor\'s note is the project\'s HISTORY — an ' +
      'earlier item that taught the profile this class. It is never the item under review: ' +
      'never write it in a script. The only item and SHAs a script names are the manifest\'s.',
      `Anything else of the profile is at ${bundle}/profile.yml, under the same rule.`,
      `Criteria from the item: ${bundle}/criteria.json`,
      `The item itself: ${bundle}/item.md`,
      'The expected result is written BEFORE executing.',
      LANGUAGE,
      RETURN_RULE,
    ].join('\n'), { agentType: agentFor('manual-verification-planner'), phase: 'Manual plan',
                    schema: SCRIPT_SCHEMA, ...tiered('standard') })
  : null

return { findings: final, coverage, conventions_observed: conventionsObserved,
         criteria: criteriaVerdicts, criteria_extracted: extracted, criteria_labels: labels,
         closed_findings: closed,
         ...(carried ? {} : { scripts_manual: scripts ? scripts.scripts_manual : [] }) }
