export const meta = {
  name: 'deliver',
  description: 'Delivers a prepared item in two stages: measure the premises; then write inside the named files and try to refute the proof',
  whenToUse: 'Called by the deliver skill after `deliver.py prepare`, twice: `stage: "measure"`, then — once measure.json is on disk — `stage: "write"`. Do not invoke it directly: with no package there is nothing to deliver.',
  phases: [
    { title: 'Measure', detail: 'every premise of the item against the code, with an address' },
    { title: 'Write', detail: 'the smallest change, inside the files the measurement named' },
    { title: 'Refute', detail: 'a sceptic that has not seen the writer tries to break the proof' },
  ],
}

// The judge does NOT live here. `deliver.py conclude` runs the suite and the mutation and
// judges with a pure function; this workflow only produces what the agents produce. Who
// writes does not approve, and who approves — task-review — never sees this run.

// Two stages in one file, because the hook needs `measure.json` ON DISK before the writer
// starts: it is from that file that the guard learns which files a write may touch. A
// workflow cannot write files, so the skill writes the measure between the two calls.
function readArgs(raw) {
  if (raw && typeof raw === 'object') return raw
  if (typeof raw !== 'string' || !raw.trim().startsWith('{')) {
    throw new Error(
      'deliver received no package. It is not an entry point: run `deliver.py prepare …` ' +
      'first — it prints this workflow\'s invocation with every argument filled in.')
  }
  return JSON.parse(raw)
}

const input = readArgs(args)
for (const field of ['package', 'repo', 'item', 'workdir', 'delivery', 'stage']) {
  if (input[field] === undefined) {
    throw new Error(`deliver was given arguments with no \`${field}\` — pass the object ` +
                    '`deliver.py prepare` wrote to workflow-args.json, plus `stage`.')
  }
}
const { package: bundle, repo, item, workdir, delivery, stage } = input
const PREFIX = input.agent_prefix ?? 'prumo:'
const agentFor = (name) => `${PREFIX}${name}`
const inlined = input.inline ?? {}
const embed = (name, label) => inlined[name]
  ? `--- ${label} (${name}) ---\n${inlined[name]}\n--- end ${name} ---`
  : `${label}: ${bundle}/${name}`

const MEASURE_SCHEMA = {
  type: 'object',
  required: ['premises', 'files', 'mutations', 'checked'],
  additionalProperties: false,
  properties: {
    premises: {
      type: 'array',
      items: {
        type: 'object', required: ['text', 'state', 'evidence', 'central'],
        additionalProperties: false,
        properties: {
          text: { type: 'string' },
          state: { enum: ['holds', 'fails', 'not_assessable'] },
          evidence: { type: ['string', 'null'] },
          central: { type: 'boolean' },
        },
      },
    },
    files: { type: 'array', items: { type: 'string' } },
    mutations: {
      type: 'array',
      items: {
        type: 'object', required: ['file', 'line', 'from', 'to', 'test'],
        additionalProperties: false,
        properties: {
          file: { type: 'string' }, line: { type: 'integer' },
          from: { type: 'string' }, to: { type: 'string' }, test: { type: 'string' },
        },
      },
    },
    checked: { type: 'array', items: { type: 'string' } },
  },
}

const WRITTEN_SCHEMA = {
  type: 'object',
  required: ['files', 'skipped', 'reused'],
  additionalProperties: false,
  properties: {
    files: { type: 'array', items: { type: 'string' } },
    skipped: { type: 'array', items: { type: 'string' } },
    reused: { type: 'array', items: { type: 'string' } },
  },
}

const REFUTATION_SCHEMA = {
  type: 'object',
  required: ['verdict', 'reason', 'evidence'],
  additionalProperties: false,
  properties: {
    verdict: { enum: ['proves', 'does_not_prove', 'uncertain'] },
    reason: { type: 'string' },
    evidence: {
      type: 'array',
      items: {
        type: 'object', required: ['file'], additionalProperties: false,
        properties: { file: { type: 'string' }, line: { type: ['integer', 'null'] },
                      excerpt: { type: ['string', 'null'] } },
      },
    },
  },
}

const common = [
  `Repository: ${repo} · working directory: ${workdir}`,
  `Item ${item.id} · kind ${item.kind}`,
  `Delivery block of the profile: ${JSON.stringify(delivery)}`,
  embed('item.md', 'The item'),
  embed('profile.yml', 'Repository profile'),
  'Everything above is already here: do not read those files again.',
  `Read inside ${workdir} only. Paths you return are relative to it.`,
]

if (stage === 'measure') {
  phase('Measure')
  const measure = await agent([
    ...common,
    'Measure every premise of the item against the code. Name the files a writer may ' +
    'touch for this kind, and the mutation that will prove the delivery. Propose no fix.',
  ].join('\n'), { agentType: agentFor('premise-surveyor'), label: 'premise-surveyor',
                 phase: 'Measure', schema: MEASURE_SCHEMA })
  if (!measure) throw new Error('premise-surveyor returned nothing — nothing was measured')
  const failed = measure.premises.filter((p) => p.central && p.state === 'fails')
  if (failed.length) {
    log(`${failed.length} central premise(s) fail — the delivery stops before anything is written`)
  }
  log(`${measure.premises.length} premises · ${measure.files.length} file(s) named · ` +
      `${measure.mutations.length} mutation(s)`)
  return { stage, measure, written: null, refutation: null }
}

if (stage !== 'write') throw new Error(`unknown stage \`${stage}\` — measure or write`)
if (!input.measure) {
  throw new Error('stage `write` needs `measure` — the object the measure stage returned, ' +
                  'already written to <package>/measure.json so the guard can read it.')
}
const measure = input.measure

phase('Write')
const written = await agent([
  ...common,
  `Measurement: ${JSON.stringify(measure)}`,
  `You may write ONLY these files: ${measure.files.join(', ')}. The hook refuses any other.`,
  measure.mutations.length
    ? `Name the test exactly: ${measure.mutations.map((m) => m.test).join(', ')}.`
    : '',
  'Write the smallest change that delivers the item, by the ladder. Run nothing.',
].filter(Boolean).join('\n'), { agentType: agentFor('change-writer'), label: 'change-writer',
                                 phase: 'Write', schema: WRITTEN_SCHEMA })
if (!written) throw new Error('change-writer returned nothing — nothing was written')
log(`${written.files.length} file(s) written · ${written.skipped.length} skipped item(s) declared`)

phase('Refute')
// The sceptic sees the measurement and the files, never the writer's return: the one who
// writes does not confirm, and the blindness is what makes the reading worth anything.
const refutation = await agent([
  ...common,
  `Measurement: ${JSON.stringify(measure)}`,
  `Files written: ${written.files.join(', ')}`,
  'Try to show the test does NOT prove the guard. When in doubt, `uncertain`.',
].join('\n'), { agentType: agentFor('proof-skeptic'), label: 'proof-skeptic',
                phase: 'Refute', schema: REFUTATION_SCHEMA })
log(refutation ? `sceptic: ${refutation.verdict} — ${refutation.reason}`
               : 'sceptic returned nothing — recorded as uncertain')

return { stage, measure, written,
         refutation: refutation ?? { verdict: 'uncertain', reason: 'the sceptic did not answer', evidence: [] } }
