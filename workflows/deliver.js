export const meta = {
  name: 'deliver',
  description: 'Delivers a prepared item in stages: design (a story or an epic), measure the premises, then write inside the named files and try to refute the proof',
  whenToUse: 'Called by the deliver skill after `deliver.py prepare`: `stage: "design"` for a story or an epic, `stage: "measure"`, then — once measure.json is on disk — `stage: "write"`. Do not invoke it directly: with no package there is nothing to deliver.',
  phases: [
    { title: 'Design', detail: 'a story cut into tasks, an epic into stories, before anything is written' },
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
for (const field of ['package', 'repo', 'item', 'delivery', 'stage']) {
  if (input[field] === undefined) {
    throw new Error(`deliver was given arguments with no \`${field}\` — pass the object ` +
                    '`deliver.py prepare` wrote to workflow-args.json, plus `stage`.')
  }
}
const { package: bundle, repo, item, delivery, stage } = input
const workdir = input.workdir ?? '(no working directory: nothing is written at this stage)'
const PREFIX = input.agent_prefix ?? 'prumo:'
// The agents travel with the plugin and answer by `${PREFIX}${name}`. A checkout that is
// not the installed plugin — a branch under development, a Codex-style harness with no
// named subagents — hands their prompts in `agent_prompts` instead, and each runs as a
// plain subagent with that prompt in front. The tool set is then the harness's default,
// wider than the agent file declares, and the guard is what still holds the writer.
const prompts = input.agent_prompts ?? {}
const spawn = (name, opts) => prompts[name]
  ? { ...opts, prompt: `${prompts[name]}\n\n---\n\n` }
  : { ...opts, agentType: `${PREFIX}${name}`, prompt: '' }
const run = (name, body, opts) => {
  const o = spawn(name, opts)
  const { prompt, ...rest } = o
  return agent(`${prompt}${body}`, rest)
}
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
          stays_green: { type: 'array', items: { type: 'string' } },
        },
      },
    },
    proving_tests: { type: 'array', items: { type: 'string' } },
    guarding_tests: { type: 'array', items: { type: 'string' } },
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
  `Item ${item.id} · shape ${input.shape ?? '?'} · plan depth ${input.plan_depth ?? '?'} · kind ${item.kind}`,
  `Delivery block of the profile: ${JSON.stringify(delivery)}`,
  embed('item.md', 'The item'),
  input.task
    ? `This item is task ${input.task.id} of a story the person approved. Its files and proving tests are ` +
      `the design's: confirm them against the code, replace nothing. ` +
      // The first story task ever measured (2026-11-12) came back blocked before a line
      // was written: the surveyor read the task's criteria — the TARGET state — as premises
      // of today's code, marked "the query still groups by client" as a central premise
      // that FAILS, and named a mutation on the very line the writer was about to replace,
      // which `conclude` would then have reported as a mutation it could not apply.
      `The task's bullets are its CRITERIA — the state the code must reach — and never ` +
      `premises: a premise is a fact about the code TODAY that the design assumed (the line it ` +
      `points at exists, the behaviour it replaces is where it says, the callers it names are ` +
      `the callers, no test covers it yet), and \`fails\` means today's code is NOT as the design ` +
      `assumed. "The behaviour is wrong today" is the reason the task exists, not a failing premise. ` +
      `For a \`behaviour-change\` task name NO mutation: the writer replaces the lines you would ` +
      `point at, and the proof is the proving tests red on the base and green after. ` +
      `Task: ${JSON.stringify(input.task)}`
    : '',
  embed('profile.yml', 'Repository profile'),
  `Conventions of this repository, to read BEFORE writing or judging shape: ${JSON.stringify(input.conventions ?? [])}`,
  (input.conventions ?? []).length === 0
    ? 'The conventions list is EMPTY: follow the neighbouring tests and say so in your return.'
    : '',
  'Everything above is already here: do not read those files again.',
  `Read inside ${workdir} only. Paths you return are relative to it.`,
]

const DESIGN_SCHEMA = {
  type: 'object',
  required: ['summary', 'tasks', 'stories', 'checked'],
  additionalProperties: false,
  properties: {
    summary: { type: 'string' },
    tasks: {
      type: 'array',
      items: {
        type: 'object', required: ['id', 'title', 'criteria', 'files', 'proving_tests', 'depends_on'],
        additionalProperties: false,
        properties: {
          id: { type: 'string' }, title: { type: 'string' },
          criteria: { type: 'array', items: { type: 'string' } },
          files: { type: 'array', items: { type: 'string' } },
          proving_tests: { type: 'array', items: { type: 'string' } },
          depends_on: { type: 'array', items: { type: 'string' } },
        },
      },
    },
    stories: {
      type: 'array',
      items: {
        type: 'object', required: ['title', 'criteria', 'out_of_scope'], additionalProperties: false,
        properties: { title: { type: 'string' }, criteria: { type: 'array', items: { type: 'string' } },
                      out_of_scope: { type: 'string' } },
      },
    },
    checked: { type: 'array', items: { type: 'string' } },
  },
}

if (stage === 'design') {
  // A story is cut into tasks, an epic into stories, before anything is measured or
  // written. The cut is read by a person at the gate; nothing here decides what a task
  // implements beyond naming its files and the tests that will prove it.
  phase('Design')
  const design = await run('task-planner', [
    ...common,
    `Cut this ${input.shape} into ${input.shape === 'epic' ? 'stories' : 'tasks'}, by the four tests. ` +
    'Read the code the item touches first. Name files under the roots and the proving tests by name.',
  ].join('\n'), { label: 'task-planner', phase: 'Design', schema: DESIGN_SCHEMA })
  if (!design) throw new Error('task-planner returned nothing — nothing was cut')
  log(`${design.tasks.length} task(s) · ${design.stories.length} stor(y/ies)`)
  return { stage, design }
}

if (stage === 'measure') {
  phase('Measure')
  const measure = await run('premise-surveyor', [
    ...common,
    'Measure every premise of the item against the code. Name the files a writer may ' +
    'touch for this kind, and the mutation that will prove the delivery. Propose no fix.',
  ].join('\n'), { label: 'premise-surveyor', phase: 'Measure', schema: MEASURE_SCHEMA })
  if (!measure) throw new Error('premise-surveyor returned nothing — nothing was measured')
  const failed = measure.premises.filter((p) => p.central && p.state === 'fails')
  if (failed.length) {
    log(`${failed.length} central premise(s) fail — the delivery stops before anything is written`)
  }
  log(`${measure.premises.length} premises · ${measure.files.length} file(s) named · ` +
      `${measure.mutations.length} mutation(s)`)
  return { stage, measure, written: null, refutation: null }
}

if (stage !== 'write') throw new Error(`unknown stage \`${stage}\` — design, measure or write`)
if (!input.measure) {
  throw new Error('stage `write` needs `measure` — the object the measure stage returned, ' +
                  'already written to <package>/measure.json so the guard can read it.')
}
const measure = input.measure

phase('Write')
const written = await run('change-writer', [
  ...common,
  `Measurement: ${JSON.stringify(measure)}`,
  `You may write ONLY these files: ${measure.files.join(', ')}. The hook refuses any other.`,
  measure.mutations.length
    ? `Name the test exactly: ${measure.mutations.map((m) => m.test).join(', ')}.`
    : '',
  'Write the smallest change that delivers the item, by the ladder. Run nothing.',
].filter(Boolean).join('\n'), { label: 'change-writer', phase: 'Write', schema: WRITTEN_SCHEMA })
if (!written) throw new Error('change-writer returned nothing — nothing was written')
log(`${written.files.length} file(s) written · ${written.skipped.length} skipped item(s) declared`)

phase('Refute')
// The sceptic sees the measurement and the files, never the writer's return: the one who
// writes does not confirm, and the blindness is what makes the reading worth anything.
const refutation = await run('proof-skeptic', [
  ...common,
  `Measurement: ${JSON.stringify(measure)}`,
  `Files written: ${written.files.join(', ')}`,
  'Try to show the test does NOT prove the guard. When in doubt, `uncertain`.',
].join('\n'), { label: 'proof-skeptic', phase: 'Refute', schema: REFUTATION_SCHEMA })
log(refutation ? `sceptic: ${refutation.verdict} — ${refutation.reason}`
               : 'sceptic returned nothing — recorded as uncertain')

return { stage, measure, written,
         refutation: refutation ?? { verdict: 'uncertain', reason: 'the sceptic did not answer', evidence: [] } }
