// Runs a workflow script with its hooks stubbed: `agent` answers from a table keyed by
// label, everything else is the plain semantics the harness documents. Prints
// {result, calls} as JSON. Usage: node run_workflow.mjs <workflow.js> <case.json>
import { readFileSync } from 'node:fs'

const [, , workflowPath, casePath] = process.argv
const { args, answers } = JSON.parse(readFileSync(casePath, 'utf8'))
const source = readFileSync(workflowPath, 'utf8').replace('export const meta', 'const meta')
const calls = []
const logs = []
const agent = async (prompt, opts = {}) => {
  const label = opts.label ?? opts.agentType ?? 'agent'
  calls.push({ label, prompt, model: opts.model ?? null })
  return label in answers ? answers[label] : null
}
const parallel = (thunks) => Promise.all(thunks.map((f) => f().catch(() => null)))
const pipeline = (items, ...stages) => Promise.all(items.map(async (item, i) => {
  let value = item
  try {
    for (const stage of stages) value = await stage(value, item, i)
    return value
  } catch { return null }
}))
const run = new Function('args', 'agent', 'parallel', 'pipeline', 'phase', 'log',
  `return (async () => {\n${source}\n})()`)
const result = await run(args, agent, parallel, pipeline, () => {}, (m) => logs.push(m))
process.stdout.write(JSON.stringify({ result, calls, logs }))
