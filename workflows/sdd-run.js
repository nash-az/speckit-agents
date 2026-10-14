export const meta = {
  name: 'sdd-run',
  description: 'Implement, verify and commit the open tasks of a Spec Kit tasks.md, parking what needs a person in BLOCKED.md',
  whenToUse: 'Started by the /sdd-run skill, which passes the plan from bin/sdd-tasks.py as args',
  phases: [
    { title: 'Build', detail: 'implementer → verifier per task, retried with the verifier feedback' },
    { title: 'Commit', detail: 'project checks, then one commit per approved task' },
    { title: 'Report', detail: 'run summary' },
  ],
}

// args (built by skills/sdd-run): {
//   run_id, root, tasks_file, blocked_file, helper,       // helper = absolute path of bin/sdd-tasks.py
//   plan,                                                  // output of `sdd-tasks.py plan`
//   phase_deps: { "<phase id>": ["<phase id>", ...] },
//   checks: ["<shell command>", ...], max_retries, allow, commit_trailer
// }
// Held tasks (tagged manual, decision, …) never taint a phase: they are a person's work.
if (!args?.plan) throw new Error('sdd-run needs the plan object as args; start it with the /sdd-run skill')
const A = args
const MAX_RETRIES = A.max_retries ?? 2
const RESERVE = 150_000

const IMPLEMENT_SCHEMA = {
  type: 'object',
  properties: {
    status: { type: 'string', enum: ['done', 'blocked'] },
    summary: { type: 'string' },
    files_changed: { type: 'array', items: { type: 'string' } },
    blocker: {
      type: 'object',
      properties: {
        tag: { type: 'string', enum: ['manual', 'decision', 'dep', 'download', 'failed'] },
        reason: { type: 'string' },
        question: { type: 'string' },
      },
      required: ['tag', 'reason', 'question'],
    },
  },
  required: ['status', 'summary', 'files_changed'],
}

const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['approve', 'reject'] },
    problems: { type: 'array', items: { type: 'string' } },
    checks_run: { type: 'array', items: { type: 'string' } },
  },
  required: ['verdict', 'problems', 'checks_run'],
}

const BOOKKEEP_SCHEMA = {
  type: 'object',
  properties: {
    checks_passed: { type: 'boolean' },
    failing_output: { type: 'string' },
    commits: { type: 'array', items: { type: 'string' } },
    blocked_recorded: { type: 'array', items: { type: 'string' } },
  },
  required: ['checks_passed', 'failing_output', 'commits', 'blocked_recorded'],
}

const outOfBudget = () => budget.total !== null && budget.remaining() < RESERVE

function taskBrief(task) {
  const tags = [task.parallel ? 'P' : null, task.story, ...task.tags].filter(Boolean)
  return [
    `Task ${task.id}${tags.length ? ` [${tags.join('] [')}]` : ''} (tasks.md line ${task.line}):`,
    task.text,
    task.files.length ? `Files named by the task: ${task.files.join(', ')}` : 'The task names no files.',
  ].join('\n')
}

const context = [
  `Project root: ${A.root}`,
  `Tasks file: ${A.tasks_file} (read the plan, spec, data model and contracts next to it as the task needs).`,
  `Allowed this run: new dependencies ${A.allow?.dependencies ? 'yes' : 'no'}, large downloads ${A.allow?.downloads ? 'yes' : 'no'}.`,
].join('\n')

async function implement(task, feedback, attempt) {
  const evalTask = task.tags.includes('eval')
  const prompt = [
    context,
    '',
    taskBrief(task),
    '',
    feedback
      ? `Attempt ${attempt + 1}. The verifier rejected the previous attempt. Fix these problems and keep what was right:\n- ${feedback.join('\n- ')}`
      : 'Implement this task completely.',
  ].join('\n')
  return agent(prompt, {
    label: `${evalTask ? 'eval' : 'impl'}:${task.id}${attempt ? `#${attempt + 1}` : ''}`,
    phase: 'Build',
    agentType: evalTask ? 'speckit-agents:sdd-evaluator' : 'speckit-agents:sdd-implementer',
    schema: IMPLEMENT_SCHEMA,
  })
}

async function verify(task, result) {
  const prompt = [
    context,
    '',
    taskBrief(task),
    '',
    `The implementer reports: ${result.summary}`,
    `Files changed: ${result.files_changed.join(', ') || '(none reported)'}`,
    '',
    'Decide whether this task is really done.',
  ].join('\n')
  return agent(prompt, {
    label: `verify:${task.id}`,
    phase: 'Build',
    agentType: 'speckit-agents:sdd-verifier',
    schema: VERIFY_SCHEMA,
  })
}

// One task: implement → verify, retried with feedback. Resolves to an outcome.
async function runTask(task) {
  let feedback = null
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    if (outOfBudget()) return { id: task.id, outcome: 'skipped', reason: 'token budget reached' }
    const result = await implement(task, feedback, attempt)
    if (!result) return { id: task.id, outcome: 'failed', reason: 'implementer did not finish', question: 'Re-run the task, or look at it by hand.' }
    if (result.status === 'blocked') {
      const b = result.blocker ?? { tag: 'decision', reason: result.summary, question: 'See the reason.' }
      return { id: task.id, outcome: 'blocked', tag: b.tag, reason: b.reason, question: b.question, files: result.files_changed }
    }
    const verdict = await verify(task, result)
    if (verdict && verdict.verdict === 'approve') {
      return { id: task.id, outcome: 'approved', summary: result.summary, files: result.files_changed }
    }
    feedback = verdict ? verdict.problems : ['The verifier did not finish; re-check the work and run the tests yourself.']
  }
  return {
    id: task.id, outcome: 'blocked', tag: 'failed',
    reason: `rejected ${MAX_RETRIES + 1} times: ${(feedback ?? []).join('; ')}`.slice(0, 1500),
    question: 'The verifier kept rejecting this task. Is the task wrong, or does it need a different approach?',
  }
}

async function bookkeep(outcomes, mode, failing) {
  const approved = outcomes.filter(o => o.outcome === 'approved')
  const blocked = outcomes.filter(o => o.outcome === 'blocked' || o.outcome === 'failed')
  const prompt = [
    `Project root: ${A.root}. Helper: python3 ${A.helper}`,
    `Run id: ${A.run_id}. Tasks file: ${A.tasks_file}. Blocked file: ${A.blocked_file}.`,
    `Project checks, run each from the project root: ${A.checks.length ? A.checks.map(c => `\n  ${c}`).join('') : '(none configured: report checks_passed true)'}`,
    `Commit trailer, end every commit message with it: ${A.commit_trailer ?? '(none)'}`,
    '',
    mode === 'discard'
      ? `MODE discard. The checks still fail after a repair attempt:\n${failing}\nStash the uncommitted work of these tasks with \`git stash push -u -m "sdd-run ${A.run_id}: ${approved.map(o => o.id).join(' ')}"\`, record each of them as blocked (tag failed, reason: the failing checks, question: what to do), commit BLOCKED.md, and report checks_passed false.`
      : 'MODE commit.',
    '',
    `Approved tasks (JSON): ${JSON.stringify(approved.map(o => ({ id: o.id, summary: o.summary, files: o.files })))}`,
    `Blocked tasks to record (JSON): ${JSON.stringify(blocked.map(o => ({ id: o.id, tag: o.tag ?? 'failed', reason: o.reason, question: o.question ?? '' })))}`,
  ].join('\n')
  return agent(prompt, { label: `commit:${outcomes.map(o => o.id).join(',')}`, phase: 'Commit', agentType: 'speckit-agents:sdd-bookkeeper', schema: BOOKKEEP_SCHEMA, effort: 'low' })
}

async function repair(outcomes, failing) {
  const ids = outcomes.filter(o => o.outcome === 'approved').map(o => o.id)
  const prompt = [
    context,
    '',
    `Tasks ${ids.join(', ')} were each approved alone, but together the project checks fail:`,
    failing,
    '',
    'Fix the cause without dropping any task\'s behaviour. Do not weaken or skip tests.',
  ].join('\n')
  return agent(prompt, { label: `repair:${ids.join(',')}`, phase: 'Build', agentType: 'speckit-agents:sdd-implementer', schema: IMPLEMENT_SCHEMA })
}

// ---- main loop ----
const results = {}           // task id → outcome
const phaseState = {}        // phase id → 'complete' | 'tainted' | 'skipped'
let stopped = null

for (const phaseInfo of A.plan.phases) {
  if (stopped) break
  const unmet = (A.phase_deps[phaseInfo.id] ?? []).filter(dep => phaseState[dep] && phaseState[dep] !== 'complete')
  if (unmet.length) {
    phaseState[phaseInfo.id] = 'skipped'
    for (const id of Object.keys(phaseInfo.tasks)) results[id] = { id, outcome: 'skipped', reason: `phase ${unmet.join(', ')} is not complete` }
    log(`Phase ${phaseInfo.id} skipped: depends on ${unmet.join(', ')}`)
    continue
  }
  log(`Phase ${phaseInfo.id}: ${phaseInfo.title} (${Object.keys(phaseInfo.tasks).length} tasks)`)
  let tainted = false

  for (const group of phaseInfo.groups) {
    if (outOfBudget()) { stopped = 'token budget reached'; break }
    if (tainted && !group.every(id => phaseInfo.tasks[id].parallel)) {
      for (const id of group) results[id] = { id, outcome: 'skipped', reason: 'an earlier task in this phase did not pass' }
      continue
    }
    // Tasks in a group touch different files, so they build at the same time.
    const outcomes = (await parallel(group.map(id => () => runTask(phaseInfo.tasks[id]))))
      .map((o, i) => o ?? { id: group[i], outcome: 'failed', reason: 'task run crashed', question: 'Re-run the task.' })

    let book = await bookkeep(outcomes, 'commit')
    if (book && !book.checks_passed && outcomes.some(o => o.outcome === 'approved')) {
      log(`Checks failed after ${group.join(', ')}; trying one repair`)
      await repair(outcomes, book.failing_output)
      book = await bookkeep(outcomes, 'commit')
      if (book && !book.checks_passed) {
        book = await bookkeep(outcomes, 'discard', book.failing_output)
        for (const o of outcomes) if (o.outcome === 'approved') Object.assign(o, { outcome: 'blocked', tag: 'failed', reason: 'project checks failed after the group; work stashed' })
      }
    }
    if (!book) { stopped = 'bookkeeper did not finish; tree may hold uncommitted work'; break }

    for (const o of outcomes) {
      results[o.id] = o
      if (o.outcome !== 'approved') tainted = true
    }
    log(`${group.join(', ')}: ${outcomes.map(o => `${o.id} ${o.outcome}`).join(', ')}`)
  }
  phaseState[phaseInfo.id] = stopped ? 'tainted' : tainted ? 'tainted' : 'complete'
}

phase('Report')
const all = Object.values(results)
const by = kind => all.filter(o => o.outcome === kind).map(o => o.id)
return {
  run_id: A.run_id,
  stopped: stopped,
  approved: by('approved'),
  blocked: all.filter(o => o.outcome === 'blocked' || o.outcome === 'failed').map(o => ({ id: o.id, tag: o.tag ?? 'failed', reason: o.reason, question: o.question })),
  skipped: all.filter(o => o.outcome === 'skipped').map(o => ({ id: o.id, reason: o.reason })),
  held: A.plan.held,
  phases: phaseState,
}
