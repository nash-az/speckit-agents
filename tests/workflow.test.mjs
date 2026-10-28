// Tests for workflows/sdd-run.js with stubbed agents: node --test tests/
// The script runs as the Workflow engine runs it: an async body with agent(), parallel(), log(),
// phase(), args and budget as globals.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const SOURCE = readFileSync(new URL('../workflows/sdd-run.js', import.meta.url), 'utf8')
  .replace('export const meta =', 'const meta =')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const body = new AsyncFunction('agent', 'parallel', 'pipeline', 'phase', 'log', 'args', 'budget', SOURCE)

function task(id, text = `Do ${id}`) {
  return { id, text, done: false, parallel: false, story: null, tags: [], files: [`${id}.py`], line: 1 }
}

function argsFor(ids, resume) {
  const tasks = Object.fromEntries(ids.map(id => [id, task(id)]))
  return {
    run_id: 'test', root: '/p', tasks_file: '/p/tasks.md', blocked_file: '/p/BLOCKED.md', helper: '/h',
    plan: { phases: [{ id: '15', title: 'Convergence', groups: ids.map(id => [id]), tasks }], held: [] },
    phase_deps: {}, checks: ['true'], max_retries: 2, allow: {}, commit_trailer: '', resume,
  }
}

// script: label → result, or a function (prompt) → result. Unlisted labels get a default.
async function run(ids, script = {}, resume, asArgs = a => a, budget = { total: null, spent: () => 0, remaining: () => Infinity }) {
  const calls = []
  const agent = async (prompt, opts) => {
    calls.push({ label: opts.label, prompt, opts })
    const answer = script[opts.label]
    if (typeof answer === 'function') return answer(prompt)
    if (answer !== undefined) return answer
    const [kind] = opts.label.split(':')
    if (kind === 'impl' || kind === 'eval') return { status: 'done', summary: 'done', files_changed: ['x.py'] }
    if (kind === 'verify') return { verdict: 'approve', problems: [], checks_run: [] }
    if (kind === 'commit') return { checks_ran: true, checks_passed: true, failing_output: '', commits: ['c1'], blocked_recorded: [] }
    return null
  }
  const parallel = async thunks => Promise.all(thunks.map(t => t().catch(() => null)))
  const out = await body(agent, parallel, null, () => {}, () => {}, asArgs(argsFor(ids, resume)), budget)
  return { out, labels: calls.map(c => c.label), calls }
}

test('a normal task is implemented, verified and committed', async () => {
  const { out, labels } = await run(['T1'])
  assert.deepEqual(labels, ['impl:T1', 'verify:T1', 'commit:T1'])
  assert.deepEqual(out.approved, ['T1'])
  assert.equal(out.stopped, null)
})

test('args passed as a JSON string work like the object', async () => {
  const { out, labels } = await run(['T1'], {}, undefined, JSON.stringify)
  assert.deepEqual(labels, ['impl:T1', 'verify:T1', 'commit:T1'])
  assert.deepEqual(out.approved, ['T1'])
})

test('a planned task with an answer puts the question and the answer in the implementer prompt', async () => {
  const withAnswer = a => {
    Object.assign(a.plan.phases[0].tasks.T1, { question: 'Which port?', answer: 'Use 8123.' })
    return a
  }
  const { calls } = await run(['T1'], {}, undefined, withAnswer)
  const impl = calls.find(c => c.label === 'impl:T1')
  assert.match(impl.prompt, /^Earlier question: Which port\?$/m)
  assert.match(impl.prompt, /^Answer from the person \(follow it\): Use 8123\.$/m)
})

test('a planned task without an answer has no answer lines in the implementer prompt', async () => {
  const { calls } = await run(['T1'])
  assert.doesNotMatch(calls.find(c => c.label === 'impl:T1').prompt, /Earlier question|Answer from the person/)
})

test('a cut-off implementer stops the run as interrupted, records nothing and commits nothing', async () => {
  const { out, labels } = await run(['T1', 'T2'], { 'impl:T1': null })
  assert.deepEqual(labels, ['impl:T1'])
  assert.deepEqual(out.interrupted, ['T1'])
  assert.deepEqual(out.blocked, [])
  assert.match(out.stopped, /^interrupted: T1 during implement/)
})

test('a cut-off verifier is interrupted, not a rejection', async () => {
  const { out, labels } = await run(['T1'], { 'verify:T1': null })
  assert.deepEqual(labels, ['impl:T1', 'verify:T1'])
  assert.deepEqual(out.interrupted, ['T1'])
  assert.ok(!labels.includes('impl:T1#2'))
})

test('a cut-off bookkeeper stops the run as interrupted', async () => {
  const { out } = await run(['T1', 'T2'], { 'commit:T1': null })
  assert.match(out.stopped, /^interrupted: the bookkeeper for T1/)
})

test('resume: an approved task goes straight to the commit', async () => {
  const resume = { tasks: { T1: { attempt: 0, impl: { status: 'done', summary: 's', files_changed: ['a.py'] }, verdict: { verdict: 'approve', problems: [], checks_run: [] } } } }
  const { out, labels, calls } = await run(['T1'], {}, resume)
  assert.deepEqual(labels, ['commit:T1'])
  assert.match(calls[0].prompt, /"files":\["a.py"\]/)
  assert.deepEqual(out.resumed, ['T1'])
})

test('resume: an implemented task is only verified', async () => {
  const resume = { tasks: { T1: { attempt: 1, impl: { status: 'done', summary: 'from before', files_changed: ['a.py'] } } } }
  const { labels, calls } = await run(['T1'], {}, resume)
  assert.deepEqual(labels, ['verify:T1', 'commit:T1'])
  assert.match(calls[0].prompt, /from before/)
})

test('resume: a rejected attempt continues with the next attempt and the feedback', async () => {
  const resume = { tasks: { T1: { attempt: 0, impl: { status: 'done', summary: 's', files_changed: ['a.py'] }, verdict: { verdict: 'reject', problems: ['missing test'], checks_run: [] } } } }
  const { labels, calls } = await run(['T1'], {}, resume)
  assert.deepEqual(labels, ['impl:T1#2', 'verify:T1', 'commit:T1'])
  assert.match(calls[0].prompt, /Attempt 2.*\n- missing test/)
})

test('resume: a cut-off attempt is continued, with a note about the edits in the tree', async () => {
  const resume = { tasks: { T1: { attempt: 1, interrupted: 'implement' } } }
  const { labels, calls } = await run(['T1'], {}, resume)
  assert.deepEqual(labels, ['impl:T1#2', 'verify:T1', 'commit:T1'])
  assert.match(calls[0].prompt, /cut off before it reported/)
})

test('resume: the retry limit still counts the attempts from before', async () => {
  const resume = { tasks: { T1: { attempt: 2, impl: { status: 'done', summary: 's', files_changed: ['a.py'] }, verdict: { verdict: 'reject', problems: ['still wrong'], checks_run: [] } } } }
  const { out, labels } = await run(['T1'], {}, resume)
  assert.deepEqual(labels, ['commit:T1'])
  assert.equal(out.blocked[0].id, 'T1')
  assert.match(out.blocked[0].reason, /still wrong/)
})

test('tasks without a checkpoint run normally in a resumed run', async () => {
  const resume = { tasks: { T1: { attempt: 0, impl: { status: 'done', summary: 's', files_changed: [] }, verdict: { verdict: 'approve', problems: [], checks_run: [] } } } }
  const { labels } = await run(['T1', 'T2'], {}, resume)
  assert.deepEqual(labels, ['commit:T1', 'impl:T2', 'verify:T2', 'commit:T2'])
})

test('the commit holds the files of every attempt, not only the last', async () => {
  let calls_seen = 0
  const { calls } = await run(['T1'], {
    'impl:T1': { status: 'done', summary: 'first', files_changed: ['a.py', 'pyproject.toml'] },
    'verify:T1': p => (calls_seen++ === 0 ? { verdict: 'reject', problems: ['no real test'], checks_run: [] } : { verdict: 'approve', problems: [], checks_run: [] }),
    'impl:T1#2': { status: 'done', summary: 'second', files_changed: ['a.py', 'test_a.py'] },
  })
  const commit = calls.find(c => c.label === 'commit:T1')
  assert.match(commit.prompt, /"files":\["a.py","pyproject.toml","test_a.py"\]/)
})

test('the workflow uses nothing the engine refuses: no import(), require(), clock or randomness', () => {
  // Workflow scripts can't import(), have no file system, and Date.now() / Math.random() /
  // argless new Date() throw (research.md D6).
  const raw = readFileSync(new URL('../workflows/sdd-run.js', import.meta.url), 'utf8')
  for (const banned of ['import(', 'require(', 'Date.now(', 'Math.random(', 'new Date()']) {
    assert.ok(!raw.includes(banned), `workflows/sdd-run.js contains ${banned}`)
  }
})

// Role models (data-model.md §5.4, FR-011).
const withModels = agentModels => a => Object.assign(a, { agentModels })

test('agentModels.implementer sets model on the implementer calls only', async () => {
  const { calls } = await run(['T1'], {}, undefined, withModels({ implementer: 'haiku' }))
  for (const c of calls) {
    if (c.label.startsWith('impl:')) assert.equal(c.opts.model, 'haiku', c.label)
    else assert.ok(!('model' in c.opts), `${c.label} has a model key`)
  }
  assert.ok(calls.some(c => c.label === 'impl:T1'))
})

test("agentModels 'inherit' or no entry passes no model key", async () => {
  for (const models of [{ implementer: 'inherit', verifier: 'inherit' }, {}, undefined]) {
    const { calls } = await run(['T1'], {}, undefined, withModels(models))
    for (const c of calls) assert.ok(!('model' in c.opts), `${c.label} has a model key with ${JSON.stringify(models)}`)
  }
})

test('agentModels per role: evaluator for eval tasks, verifier, bookkeeper, implementer for repair', async () => {
  const models = { implementer: 'm-impl', evaluator: 'm-eval', verifier: 'm-ver', bookkeeper: 'm-book' }
  const evalT1 = a => { a.plan.phases[0].tasks.T1.tags = ['eval']; return withModels(models)(a) }
  const { calls } = await run(['T1'], {}, undefined, evalT1)
  const byLabel = Object.fromEntries(calls.map(c => [c.label, c.opts.model]))
  assert.deepEqual(byLabel, { 'eval:T1': 'm-eval', 'verify:T1': 'm-ver', 'commit:T1': 'm-book' })

  let commits = 0
  const failing = { checks_ran: true, checks_passed: false, failing_output: 'boom', commits: [], blocked_recorded: [] }
  const passing = { checks_ran: true, checks_passed: true, failing_output: '', commits: ['c1'], blocked_recorded: [] }
  const r = await run(['T1'], {
    'commit:T1': () => (commits++ === 0 ? failing : passing),
    'repair:T1': { status: 'done', summary: 'fixed', files_changed: ['x.py'] },
  }, undefined, withModels(models))
  const repair = r.calls.find(c => c.label === 'repair:T1')
  assert.ok(repair, `no repair call in ${r.labels}`)
  assert.equal(repair.opts.model, 'm-impl')
})

test('credit-aware pause skips starting a new agent when remaining budget is below RESERVE', async () => {
  const budget = { total: 1_000_000, spent: () => 860_000, remaining: () => 140_000 } // below 150_000 RESERVE
  const { out, labels } = await run(['T1'], {}, undefined, a => a, budget)
  assert.deepEqual(labels, []) // no agent started at all
  assert.equal(out.stopped, 'token budget reached')
  assert.ok(out.approved.length === 0)
})

test('credit-aware pause allows starting when remaining budget is above RESERVE', async () => {
  const budget = { total: 1_000_000, spent: () => 800_000, remaining: () => 200_000 } // above 150_000 RESERVE
  const { out, labels } = await run(['T1'], {}, undefined, a => a, budget)
  assert.ok(labels.includes('impl:T1'))
  assert.deepEqual(out.approved, ['T1'])
})
