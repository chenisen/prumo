export const meta = {
  name: 'deliver',
  description: 'Delivers a prepared item: measures it, and a writer with a shell writes it and runs its tests in the item\'s own worktree; a story is written task by task; the evidence is collected once, at the end',
  whenToUse: 'Called by the deliver skill after `deliver.py prepare`, with the stage that script printed: `deliver` (an item with no gate: measure and write in one call), `design` (a story or an epic), `measure` then `write` (a shape a person approves in between), `tasks` (an approved story), `evidence` (a delivered item). Do not invoke it directly: with no package there is nothing to deliver.',
  phases: [
    { title: 'Design', detail: 'a story cut into tasks, an epic into stories, before anything is written' },
    { title: 'Measure', detail: 'every premise of the item against the code, with an address' },
    { title: 'Write', detail: 'the smallest change, written and run by one agent in the item\'s worktree' },
    { title: 'Refute', detail: 'a sceptic that has not seen the writer tries to break the proof — never on a small item' },
    { title: 'Evidence', detail: 'what was done, shown — once, after the item is delivered' },
  ],
}

// The judge does NOT live here. `deliver.py conclude` checks what landed, once, and
// judges with a pure function; this workflow only produces what the agents produce. Who
// writes does not approve, and who approves — task-review — never sees this run.
//
// The agents EXECUTE. Until 3.0 the writer had no shell: it wrote blind, a script ran the
// whole suite after it, a red sent the item back for a second write with the first one's
// output pasted in — six of the first story's thirteen tasks were written twice that
// way, and the conductor filled the gaps with throwaway scripts. Now the writer runs the
// tests it touches, in the worktree, until they are green, and the script checks once.
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
// not the installed plugin — a branch under development, a harness with no named
// subagents — hands their prompts in `agent_prompts` instead, and each runs as a plain
// subagent with that prompt in front.
const prompts = input.agent_prompts ?? {}
const spawn = (name, opts) => prompts[name]
  ? { ...opts, prompt: `${prompts[name]}\n\n---\n\n` }
  : { ...opts, agentType: `${PREFIX}${name}`, prompt: '' }
// The model follows the task's demand. `input.models` is the installation's name per
// tier (resolved by the spine from `prumo.yml`); each agent's tier is the method's
// reading of how hard its question is: planning a story and refuting a proof decide
// what ships (`heavy`); measuring and writing are ordinary readings (`standard`);
// collecting evidence illustrates and decides nothing (`light`). An absent map or tier
// spreads nothing, and the agent inherits the session's model.
const MODELS = input.models ?? {}
const TIER = {
  'premise-surveyor': 'standard',
  'change-writer': 'standard',
  'task-planner': 'heavy',
  'proof-skeptic': 'heavy',
  'evidence-collector': 'light',
}
const run = (name, body, opts) => {
  const o = spawn(name, opts)
  const { prompt, ...rest } = o
  const model = MODELS[TIER[name]]
  return agent(`${prompt}${body}`, model ? { ...rest, model } : rest)
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
    related_tests: { type: 'array', items: { type: 'string' } },
    // What already stands and the writer builds on: the definition of a word the item
    // uses, the switch the behaviour sits behind, the readers of what will be written.
    existing: {
      type: 'array',
      items: {
        type: 'object', required: ['kind', 'what', 'where'],
        additionalProperties: false,
        properties: {
          kind: { enum: ['definition', 'switch', 'reader'] },
          what: { type: 'string' },
          where: { type: ['string', 'null'] },
        },
      },
    },
    checked: { type: 'array', items: { type: 'string' } },
  },
}

const WRITTEN_SCHEMA = {
  type: 'object',
  required: ['files', 'skipped', 'reused', 'tests_run', 'blocked'],
  additionalProperties: false,
  properties: {
    files: { type: 'array', items: { type: 'string' } },
    skipped: { type: 'array', items: { type: 'string' } },
    reused: { type: 'array', items: { type: 'string' } },
    tests_run: {
      type: 'object', required: ['command', 'red_first', 'green'], additionalProperties: false,
      properties: {
        command: { type: ['string', 'null'] },
        red_first: { type: ['boolean', 'null'] },
        green: { type: ['boolean', 'null'] },
      },
    },
    blocked: { type: ['string', 'null'] },
    commit_message: { type: 'string' },
    commit: { type: ['string', 'null'] },
    lines_changed: { type: ['integer', 'null'] },
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

const EVIDENCE_SCHEMA = {
  type: 'object',
  required: ['captures', 'not_collected', 'environment'],
  additionalProperties: false,
  properties: {
    captures: {
      type: 'array',
      items: {
        type: 'object', required: ['criterion', 'observed'], additionalProperties: false,
        properties: { criterion: { type: 'string' }, observed: { type: 'string' },
                      artifact: { type: ['string', 'null'] } },
      },
    },
    not_collected: {
      type: 'array',
      items: {
        type: 'object', required: ['criterion', 'reason'], additionalProperties: false,
        properties: { criterion: { type: 'string' }, reason: { type: 'string' } },
      },
    },
    environment: {
      type: 'object', required: ['raised', 'taken_down'], additionalProperties: false,
      properties: { raised: { type: 'array', items: { type: 'string' } }, taken_down: { type: 'boolean' } },
    },
  },
}

const common = [
  `Repository: ${repo} · working directory: ${workdir}`,
  `Item ${item.id} · shape ${input.shape ?? '?'} · plan depth ${input.plan_depth ?? '?'} · kind ${item.kind}`,
  `Delivery block of the profile: ${JSON.stringify(delivery)}`,
  embed('item.md', 'The item'),
  embed('profile.yml', 'Repository profile'),
  `Conventions of this repository, to read BEFORE writing or judging shape: ${JSON.stringify(input.conventions ?? [])}`,
  (input.conventions ?? []).length === 0
    ? 'The conventions list is EMPTY: follow the neighbouring tests and say so in your return.'
    : '',
  'Everything above is already here: do not read those files again.',
  `Work inside ${workdir} only. Paths you return are relative to it.`,
]

// The size, before anything is written: the measurement names files, not lines. The two
// numbers and the wide paths are the profile's, handed over by `deliver.py prepare` as it
// will read them again over the diff that lands — `shape.size_of` is the rule, this is
// its first reading. A small item buys no sceptic; the review that follows reads it.
const smallLimit = input.small ?? { max_files: 3, max_lines: 80 }
const widePatterns = (input.wide_patterns ?? []).map((p) => new RegExp(p))
const sizeOf = (files) => {
  if (input.gate) return 'standard'
  if (files.some((f) => widePatterns.some((re) => re.test(f)))) return 'standard'
  return files.length <= smallLimit.max_files ? 'small' : 'standard'
}
// The size AFTER the write. The measurement names files, not lines, so its reading is
// provisional: the first real delivery on 3.0 (2026-11-13) was sized small by its one
// file, landed 146 lines against a small of 120, and reached `conclude` as a standard
// item no sceptic had read. The writer has a shell and says how many lines landed.
const sizeWritten = (measure, written) => {
  const files = [...new Set([...measure.files, ...(written.files ?? [])])]
  if (sizeOf(files) !== 'small') return 'standard'
  return (written.lines_changed ?? 0) > smallLimit.max_lines ? 'standard' : 'small'
}

// What the writer may run, resolved by `deliver.py prepare` from the profile's allowlist
// for this very tree. The whole suite is never among them: `conclude` runs it once, and
// only for an item whose size asks it.
const commands = input.commands ?? {}
const shell = [
  commands.tests
    ? `You have a shell, and you use it. Run the tests you touch with exactly this command, the test files in place of {tests}: ${commands.tests}`
    : 'The profile declares no command that runs named test files: run no test, and say so in `skipped`.',
  commands.lint ? `Before you return, run the lint once: ${commands.lint}` : '',
  'Read a result in the OUTPUT, never in the exit code — a wrapped executor swallows it.' +
  (commands.red_name_pattern ? ` A red test is a line matching ${JSON.stringify(commands.red_name_pattern)}.` : '') +
  (commands.failure_pattern ? ` A failed run matches ${JSON.stringify(commands.failure_pattern)}.` : ''),
  'Never the whole suite, never an environment for showing the change, never a push, never another branch. ' +
  'No script that writes code: you edit the files yourself.',
].filter(Boolean)

async function measureItem() {
  phase('Measure')
  const measure = await run('premise-surveyor', [
    ...common,
    // A fix round's measure (2026-11-12) named two mutants on the pre-fix lines the fix
    // itself rewrote. `deliver.py` drops them; the brief keeps the surveyor off them.
    item.kind === 'behaviour-change'
      ? 'This item changes behaviour: name NO mutation. The writer replaces the lines a mutant ' +
        'would point at; the proof is the proving tests red on the base and green after.'
      : '',
    'Measure every premise of the item against the code. Name the files a writer may ' +
    'touch for this kind, the tests that will prove the delivery, and in `related_tests` ' +
    'the existing test FILES that exercise the code those files hold. Propose no fix.',
    // The review threads of 2026-11-15 sent deliveries back for what the code already
    // held and the delivery never read: a second definition of a standing
    // concept, a switch that gated half of the new paths, a write that emptied a queue
    // filtering on the column it filled.
    'In `existing`, give the address of what already stands: the definition of each word of ' +
    'the domain the item leans on, the switch the behaviour sits behind when the item names ' +
    'one, and the selections that filter on each column or status the delivery will write.',
  ].filter(Boolean).join('\n'), { label: 'premise-surveyor', phase: 'Measure', schema: MEASURE_SCHEMA })
  if (!measure) throw new Error('premise-surveyor returned nothing — nothing was measured')
  if (item.kind === 'behaviour-change') measure.mutations = []
  log(`${measure.premises.length} premises · ${measure.files.length} file(s) named · ` +
      `${measure.mutations.length} mutation(s)`)
  return measure
}

const centralFails = (measure) => measure.premises.filter((p) => p.central && p.state === 'fails')

// One writer, one task of work: it reads, writes the test and sees it red, writes the
// code and sees it green, runs the lint, and says what it left out. `task` is a story's
// task; `earlier` the tasks already written on this branch.
async function writeItem(measure, { task = null, earlier = [], previous = null } = {}) {
  phase('Write')
  // A blocked delivery is written again on the same tree, and the previous verdict
  // travels with the prompt — `deliver.py conclude` writes it into workflow-args.json.
  const again = previous
    ? 'A PREVIOUS ATTEMPT AT THIS ITEM WAS BLOCKED on this same tree — the files below ' +
      'already hold it. Fix the cause before anything else, inside the same files. ' +
      `Reason: ${previous.reason}` +
      (previous.output ? `\nWhat the run printed:\n${previous.output}` : '') +
      (previous.hint ? `\n${previous.hint}` : '')
    : ''
  // What the story already holds on this branch. A write in the tenth task of the first
  // story (2026-11-12) removed a field before the call the ninth task had made read it.
  const storyContext = earlier.length
    ? 'EARLIER TASKS OF THIS STORY, already written on this branch — every criterion below ' +
      `must still hold after this change: ${JSON.stringify(earlier)}\n` +
      'Do not undo any of them: read how this change meets their files before writing.'
    : ''
  const subject = (delivery.commit_format ?? '{item} - {message}').replace('{item}', String(item.id))
  const written = await run('change-writer', [
    ...common,
    again,
    task
      ? `This is task ${task.id} of a story a person approved: ${JSON.stringify(task)}\n` +
        'The task\'s bullets are its CRITERIA — the state the code must reach. Its files and ' +
        'proving tests are the design\'s: confirm them against the code as you read, and when the ' +
        'code is not as the design assumed, write nothing and return `blocked` with the address.'
      : '',
    storyContext,
    `Measurement: ${JSON.stringify(measure)}`,
    `The files named for this change: ${measure.files.join(', ')}. A file outside them, that the change ` +
    'cannot be delivered without, is declared in `files` with the reason in `skipped`; anything else is a defect.',
    measure.mutations.length
      ? `Name the test exactly: ${measure.mutations.map((m) => m.test).join(', ')}. Turn the guard wrong ` +
        'yourself at the named line, see that test red, and put the line back before you return.'
      : '',
    ...shell,
    'Write the smallest change that delivers the item, by the ladder: the test first, red; ' +
    'the code, green; the lint. `tests_run` says what you ran and what you saw.',
    // Twice in the first story a test double of a method with a declared return type
    // returned nothing; the runtime threw inside a try, the catch ran, and new reds came
    // from the double, not from the change.
    'A test double of a method with a declared return type returns a value of that type, ' +
    'explicitly — never the double\'s default.',
    // The first story's refactor round (2026-11-12): the person reviewing the merge request
    // found the worker the writer had grown — loops and branches nested three deep, one method
    // doing five things — and a construction convention changed in files no test needed it in.
    'The code you write is clean on the first pass: one purpose per method, named for it; ' +
    'one level of nesting in what you add; nothing written twice; no comment explaining code. ' +
    'Keep the file\'s conventions — how it builds collaborators, how it names — and change one ' +
    'only where a test you write depends on it.',
    // The same review threads: an exclusion that named one member of an
    // enumeration and let its sibling through, a write keyed by the owner where the event
    // was one order, a validation that ran with its flag off, a match tested only on
    // identifiers picked to match.
    'Four edges, each read before the code and proven by a test of its own: a branch on a ' +
    'closed set decides every member; a write is keyed as narrowly as its event, and the readers ' +
    'of the column it fills are searched for; behind a switch, the switch off is the base; the ' +
    'data in a test looks like the data — the empty and the older value included.',
    // The first front-end delivery (2026-11-13) committed the item's title with its id
    // twice, against a log written in another voice: the script cannot know the
    // repository's voice, the writer read it.
    '`commit_message` in your return: what the change does, in the voice of this repository\'s ' +
    'recent commit subjects and its conventions — without the item\'s id, which the format adds.',
    '`lines_changed` in your return: the lines your change added plus the lines it removed, read ' +
    'from `git diff --numstat HEAD` in the working directory before you return, plus every line of a ' +
    'file you created (a new file is not in that diff). It decides whether a sceptic reads the ' +
    'proof: a number, never a guess.',
    task
      ? 'When this task\'s tests are green, commit it yourself, with a pathspec and never `git add -A`: ' +
        `\`git add -- <the files you wrote>\`, then \`git commit -m "${subject.replace('{message}', '<your commit_message>')}"\`. ` +
        'Return the commit\'s short hash in `commit`. A task whose tests you could not turn green is not committed: return `blocked`.'
      : 'Do NOT commit: the script commits a delivery that stands.',
    previous
      ? '`files` in your return lists EVERY file changed since the task base — the earlier ' +
        'attempt\'s included — not only the ones you touched in this pass.'
      : '',
  ].filter(Boolean).join('\n'), {
    label: task ? `change-writer:${task.id}` : 'change-writer', phase: 'Write', schema: WRITTEN_SCHEMA,
  })
  if (!written) throw new Error('change-writer returned nothing — nothing was written')
  log(written.blocked
    ? `writer stopped: ${written.blocked}`
    : `${written.files.length} file(s) written · tests ${written.tests_run.green ? 'green' : 'not green'} · ` +
      `${written.skipped.length} skipped item(s) declared`)
  return written
}

// The sceptic sees the measurement and the files, never the writer's return: the one who
// writes does not confirm, and the blindness is what makes the reading worth anything.
async function refute(measure, written, previous = null) {
  phase('Refute')
  const refutation = await run('proof-skeptic', [
    ...common,
    `Measurement: ${JSON.stringify(measure)}`,
    `Files written: ${written.files.join(', ')}`,
    previous
      ? 'This is a SECOND attempt on the same tree: the change under review is every file of ' +
        'the measurement that differs from the base, not only the files listed above.'
      : '',
    'Try to show the test does NOT prove the guard — the values it seeds and the members ' +
    'of a closed set it feeds included. When in doubt, `uncertain`.',
  ].filter(Boolean).join('\n'), { label: 'proof-skeptic', phase: 'Refute', schema: REFUTATION_SCHEMA })
  log(refutation ? `sceptic: ${refutation.verdict} — ${refutation.reason}`
                 : 'sceptic returned nothing — recorded as uncertain')
  return refutation ?? { verdict: 'uncertain', reason: 'the sceptic did not answer', evidence: [] }
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
  const measure = await measureItem()
  const failed = centralFails(measure)
  if (failed.length) {
    log(`${failed.length} central premise(s) fail — the delivery stops before anything is written`)
  }
  return { stage, measure, written: null, refutation: null }
}

if (stage === 'deliver') {
  // An item with no gate, in ONE call: measured, sized, written and — unless it is
  // small — read by the sceptic. Nothing waits on the conductor in between.
  if (input.gate) {
    throw new Error(`a ${input.shape} waits for a person between the measure and the write — ` +
                    'stage `measure`, then `deliver.py plan`, then stage `write`')
  }
  const measure = await measureItem()
  if (centralFails(measure).length) {
    log('a central premise fails — the delivery stops before anything is written')
    return { stage, size: null, measure, written: null, refutation: null }
  }
  const written = await writeItem(measure)
  const size = written.blocked ? sizeOf(measure.files) : sizeWritten(measure, written)
  log(`size: ${size}`)
  const refutation = size === 'small' || written.blocked ? null : await refute(measure, written)
  return { stage, size, measure, written, refutation }
}

if (stage === 'write') {
  // After a person's approval, or again after a blocked attempt: the measurement is on
  // disk, and `deliver.py plan` (or `conclude`) put it in workflow-args.json.
  if (!input.measure) {
    throw new Error('stage `write` needs `measure` — `deliver.py plan` writes it into ' +
                    'workflow-args.json; pass that object, plus `stage`.')
  }
  const measure = input.measure
  const written = await writeItem(measure, { previous: input.previous ?? null })
  const refutation = written.blocked || sizeWritten(measure, written) === 'small'
    ? null : await refute(measure, written, input.previous ?? null)
  return { stage, measure, written, refutation }
}

if (stage === 'tasks') {
  // An approved story: its tasks, in the design's order, each written, run and
  // committed by one writer before the next begins. No surveyor per task, no sceptic
  // per task, no suite per task: the story is checked once, by `conclude`, and read
  // once, by the review. The first story (2026-11-12) paid a prepare, a measure, a plan,
  // a write, a sceptic and a whole suite for each of thirteen tasks.
  if (!input.design) {
    throw new Error('stage `tasks` needs `design` — `deliver.py plan --design` writes it into ' +
                    'workflow-args.json; pass that object, plus `stage`.')
  }
  const done = new Set(input.done ?? [])
  const earlier = []
  const tasks = []
  for (const task of input.design.tasks) {
    const brief = { id: task.id, title: task.title, criteria: task.criteria, files: task.files }
    if (done.has(task.id)) { earlier.push(brief); continue }
    const measure = { premises: [], files: task.files, mutations: [], proving_tests: task.proving_tests, checked: [] }
    const written = await writeItem(measure, { task, earlier: [...earlier],
                                                previous: input.previous?.task === task.id ? input.previous : null })
    tasks.push({ id: task.id, written })
    if (written.blocked) {
      log(`task ${task.id} stopped — the tasks after it wait`)
      break
    }
    earlier.push(brief)
  }
  // Every task is written and the story's check found something: one more write, over
  // the whole story, with what the check printed. It is no task of the design.
  if (!tasks.length && input.previous) {
    const whole = { premises: [], mutations: [], checked: [],
                    files: [...new Set(input.design.tasks.flatMap((t) => t.files))],
                    proving_tests: input.design.tasks.flatMap((t) => t.proving_tests) }
    const fix = { id: 'FIX', title: 'repair what the story\'s check found', criteria: [input.previous.reason],
                  files: whole.files, proving_tests: [], depends_on: [] }
    tasks.push({ id: fix.id, written: await writeItem(whole, { task: fix, earlier, previous: input.previous }) })
  }
  return { stage, tasks }
}

if (stage === 'evidence') {
  // Evidence illustrates a delivery that is already done. It runs once per item, after
  // its last task; nothing it finds changes the delivery's state; and it is the only
  // place where an environment for showing the change is raised — the first front-end
  // delivery raised one, and rewrote its capture scripts, while the work was still open.
  if (!input.evidence) {
    log('the profile declares no `delivery.evidence` — the tests are the evidence')
    return { stage, evidence: null }
  }
  phase('Evidence')
  const evidence = await run('evidence-collector', [
    ...common,
    `The delivery's record: ${bundle}/delivery.json · its report: ${bundle}/report.md`,
    `How this repository shows a change to a person: ${input.evidence.how}`,
    `What raises the environment, in order: ${JSON.stringify(input.evidence.setup ?? [])}`,
    `What takes it down: ${JSON.stringify(input.evidence.teardown ?? [])}`,
    `Keep every artifact under ${input.evidence_dir}, and name it in \`artifact\`.`,
    'Show each acceptance criterion of the item once. Change no file of the repository. ' +
    'What you could not show is `not_collected`, with the reason — never a blocker.',
  ].join('\n'), { label: 'evidence-collector', phase: 'Evidence', schema: EVIDENCE_SCHEMA })
  log(evidence ? `${evidence.captures.length} shown · ${evidence.not_collected.length} not collected`
               : 'the collector returned nothing — recorded as not collected')
  return { stage, evidence: evidence ?? { captures: [], not_collected: [{ criterion: '*', reason: 'the collector did not answer' }],
                                           environment: { raised: [], taken_down: true } } }
}

throw new Error(`unknown stage \`${stage}\` — deliver, design, measure, write, tasks or evidence`)
