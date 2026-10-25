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
//   checks: ["<shell command>", ...], max_retries, allow, commit_trailer,
//   resume,                                                // optional: `sdd-tasks.py checkpoint` of an interrupted run
//   eventsDir,                                             // .sdd/runs/<run id> for the event feed
//   taskAllow,                                             // { "<task id>": { dependencies, downloads } } per-task overrides
//   isolation,                                             // 'branch' | 'worktree'
// }
// An agent that comes back null was cut off (usage limit, crash, stop), so its task is
// `interrupted`, never `failed`: the run stops without recording anything, and the next run
// resumes from the checkpoint, skipping the steps that already finished.
// Held tasks (tagged manual, decision, …) never taint a phase: they are a person's work.
// The main loop sometimes passes args as a JSON string instead of the object (run #20, 2026-10-18).
const A = typeof args === 'string' ? JSON.parse(args) : args
if (!A?.plan) throw new Error('sdd-run needs the plan object as args; start it with the /sdd-run skill')
const MAX_RETRIES = A.max_retries ?? 2
const RESERVE = 150_000
const RESUME = A.resume?.tasks ?? {}
const EVENTS_DIR = A.eventsDir
const TASK_ALLOW = A.taskAllow ?? {}
const ISOLATION = A.isolation ?? 'branch'

const fs = await import('node:fs')
const path = await import('node:path')

// Event feed: one JSON line per observable step, written to .sdd/runs/<run id>/events.jsonl.
// A monitor, phone notification or dashboard can follow this without reading agent transcripts.
let eventsStream = null
function event(kind, payload) {
  const line = JSON.stringify({ ts: Date.now(), kind, ...payload }) + '\n'
  if (EVENTS_DIR) {
    try {
      fs.mkdirSync(EVENTS_DIR, { recursive: true })
      fs.appendFileSync(path.join(EVENTS_DIR, 'events.jsonl'), line)
    } catch { /* best effort; the run must not fail because monitoring is unavailable */ }
  }
}
function closeEvents() {}

// Worktree isolation: when [run] isolation = "worktree", each task of a parallel group runs in
// its own git worktree so half-written sibling tasks never leak into each other's test runs.
// The bookkeeper merges each worktree onto the run branch in task order.
const { execFileSync } = await import('node:child_process')
function git(args, cwd) {
  return execFileSync('git', args, { cwd, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] })
}
function worktreeFor(taskId) {
  return path.join(A.root, '.sdd', 'worktrees', `${A.run_id}-${taskId}`)
}
async function createWorktree(taskId) {
  const wt = worktreeFor(taskId)
  fs.mkdirSync(path.dirname(wt), { recursive: true })
  try { git(['worktree', 'add', '--detach', wt], A.root) } catch { /* already exists */ }
  return wt
}
async function mergeWorktree(taskId) {
  const wt = worktreeFor(taskId)
  const files = git(['diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD~1', 'HEAD'], wt).trim().split('\n').filter(Boolean)
  git(['checkout', wt, '--', ...files], A.root)
  git(['worktree', 'remove', '--force', wt], A.root)
}

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
    checks_ran: { type: 'boolean' },
    checks_passed: { type: 'boolean' },
    failing_output: { type: 'string' },
    commits: { type: 'array', items: { type: 'string' } },
    blocked_recorded: { type: 'array', items: { type: 'string' } },
  },
  required: ['checks_ran', 'checks_passed', 'failing_output', 'commits', 'blocked_recorded'],
}

const outOfBudget = () => budget.total !== null && budget.remaining() < RESERVE
// Don't start a new agent when we're close to the limit: a cut-off agent leaves work in the tree
// and the next run resumes it. Stop before starting, not mid-agent.
const budgetOK = () => budget.total === null || budget.remaining() >= RESERVE

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

async function implement(task, feedback, attempt, continuing) {
  const evalTask = task.tags.includes('eval')
  const prompt = [
    context,
    '',
    taskBrief(task),
    '',
    feedback
      ? `Attempt ${attempt + 1}. The verifier rejected the previous attempt. Fix these problems and keep what was right:\n- ${feedback.join('\n- ')}`
      : 'Implement this task completely.',
    continuing
      ? '\nAn earlier attempt at this task was cut off before it reported (usage limit or stop). Its edits may already be in the tree: check `git status` and `git diff`, keep what is right and finish the task. Report every file the task changed, including the earlier edits.'
      : '',
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
  event('task.started', { id: task.id, tags: task.tags, files: task.files, deps: task.deps })
  let feedback = null
  let lastFiles = []
  // Every file any attempt touched: a later attempt reports only what it changed itself, and
  // the commit must still hold the earlier attempts' edits (T240, T247 on the language app).
  const allFiles = new Set()
  const touched = files => { for (const f of files ?? []) allFiles.add(f); return [...allFiles] }
  // Resume what an interrupted run finished (args.resume, from its journal).
  const cp = RESUME[task.id]
  let start = 0
  let carried = null
  let continuing = false
  if (cp?.impl) {
    if (cp.verdict?.verdict === 'approve') {
      event('task.resumed', { id: task.id, step: 'commit', from: 'approved' })
      return { id: task.id, outcome: 'approved', summary: cp.impl.summary, files: touched([...(cp.files ?? []), ...(cp.impl.files_changed ?? [])]), resumed: true }
    }
    touched(cp.files)
    if (cp.verdict) { feedback = cp.verdict.problems; start = cp.attempt + 1; lastFiles = touched(cp.impl.files_changed) }
    else { carried = cp.impl; start = cp.attempt }
  } else if (cp?.interrupted === 'implement') {
    start = cp.attempt
    continuing = true
    event('task.resumed', { id: task.id, step: 'implement', from: 'interrupted' })
  }
  for (let attempt = start; attempt <= MAX_RETRIES; attempt++) {
    if (!budgetOK()) return { id: task.id, outcome: 'skipped', reason: 'token budget reached before starting' }
    if (outOfBudget()) return { id: task.id, outcome: 'skipped', reason: 'token budget reached' }
    const result = carried ?? await implement(task, feedback, attempt, continuing)
    carried = null
    continuing = false
    if (!result) {
      event('task.interrupted', { id: task.id, step: 'implement', attempt })
      return { id: task.id, outcome: 'interrupted', step: 'implement', attempt }
    }
    event('task.implemented', { id: task.id, attempt, summary: result.summary, files: result.files_changed })
    if (result.status === 'blocked') {
      const b = result.blocker ?? { tag: 'decision', reason: result.summary, question: 'See the reason.' }
      event('task.blocked', { id: task.id, tag: b.tag, reason: b.reason })
      return { id: task.id, outcome: 'blocked', tag: b.tag, reason: b.reason, question: b.question, files: touched(result.files_changed) }
    }
    lastFiles = touched(result.files_changed)
    if (!budgetOK()) return { id: task.id, outcome: 'skipped', reason: 'token budget reached before verify' }
    const verdict = await verify(task, result)
    if (!verdict) {
      event('task.interrupted', { id: task.id, step: 'verify', attempt })
      return { id: task.id, outcome: 'interrupted', step: 'verify', attempt }
    }
    event('task.verified', { id: task.id, verdict: verdict.verdict, problems: verdict.problems })
    if (verdict.verdict === 'approve') {
      event('task.approved', { id: task.id, files: lastFiles })
      return { id: task.id, outcome: 'approved', summary: result.summary, files: lastFiles }
    }
    feedback = verdict.problems
  }
  event('task.failed', { id: task.id, reason: (feedback ?? []).join('; ') })
  return {
    id: task.id, outcome: 'blocked', tag: 'failed',
    reason: `rejected ${MAX_RETRIES + 1} times: ${(feedback ?? []).join('; ')}`.slice(0, 1500),
    question: 'The verifier kept rejecting this task. Is the task wrong, or does it need a different approach?',
    files: lastFiles, stash: true,
  }
}

async function bookkeep(outcomes, mode, failing, note) {
  const approved = outcomes.filter(o => o.outcome === 'approved')
  const blocked = outcomes.filter(o => o.outcome === 'blocked' || o.outcome === 'failed')
  const prompt = [
    `Project root: ${A.root}. Helper: python3 ${A.helper}`,
    `Run id: ${A.run_id}. Tasks file: ${A.tasks_file}. Blocked file: ${A.blocked_file}.`,
    `Project checks. Run each from the project root as its own Bash call, exactly as written: no loops, pipes, \`;\`, \`&&\` or \`cd\`. ${A.checks.length ? A.checks.map(c => `\n  ${c}`).join('') : '(none configured: report checks_passed true)'}`,
    `Commit trailer, end every commit message with it: ${A.commit_trailer ?? '(none)'}`,
    '',
    mode === 'park'
      ? `MODE park. No task in this group was approved, and the checks fail with the blocked tasks' partial work in the tree:\n${failing}\nStash the files of every blocked task with \`git stash push -u -m "sdd-run ${A.run_id}: partial ${blocked.map(o => o.id).join(' ')}" -- <their files>\`, record each task as blocked with the stash message added to its reason, commit BLOCKED.md, then run the checks again and report them.`
      : mode === 'discard'
      ? `MODE discard. The checks still fail after a repair attempt:\n${failing}\nStash the uncommitted work of these tasks with \`git stash push -u -m "sdd-run ${A.run_id}: ${approved.map(o => o.id).join(' ')}"\`, record each of them as blocked (tag failed, reason: the failing checks, question: what to do), commit BLOCKED.md, and report checks_passed false.`
      : 'MODE commit.',
    '',
    `Approved tasks (JSON): ${JSON.stringify(approved.map(o => ({ id: o.id, summary: o.summary, files: o.files })))}`,
    `Blocked tasks to record (JSON): ${JSON.stringify(blocked.map(o => ({ id: o.id, tag: o.tag ?? 'failed', reason: o.reason, question: o.question ?? '', files: o.files ?? [], stash: !!o.stash })))}`,
    note ? `\n${note}` : '',
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
    event('phase.skipped', { id: phaseInfo.id, title: phaseInfo.title, reason: `depends on ${unmet.join(', ')}` })
    for (const id of Object.keys(phaseInfo.tasks)) results[id] = { id, outcome: 'skipped', reason: `phase ${unmet.join(', ')} is not complete` }
    log(`Phase ${phaseInfo.id} skipped: depends on ${unmet.join(', ')}`)
    continue
  }
  event('phase.started', { id: phaseInfo.id, title: phaseInfo.title, taskCount: Object.keys(phaseInfo.tasks).length })
  log(`Phase ${phaseInfo.id}: ${phaseInfo.title} (${Object.keys(phaseInfo.tasks).length} tasks)`)
  let tainted = false
  // Convergence phases (from /speckit-converge) hold independent gap fixes, so a task that did not
  // pass holds back only later tasks that name it. Other phases build in order: any failure holds the rest.
  const independent = /^convergence/i.test(phaseInfo.title ?? '')
  const notPassed = new Set()
  const namesNotPassed = id => [...notPassed].some(t => new RegExp(`\\b${t}\\b`).test(phaseInfo.tasks[id].text))

  for (const group of phaseInfo.groups) {
    if (!budgetOK()) { stopped = 'token budget reached'; break }
    if (outOfBudget()) { stopped = 'token budget reached'; break }
    if (independent && group.some(namesNotPassed)) {
      for (const id of group) {
        results[id] = { id, outcome: 'skipped', reason: `it builds on ${[...notPassed].filter(t => new RegExp(`\\b${t}\\b`).test(phaseInfo.tasks[id].text)).join(', ') || 'a task in its group'}, which did not pass` }
        notPassed.add(id)
      }
      continue
    }
    if (!independent && tainted && !group.every(id => phaseInfo.tasks[id].parallel)) {
      for (const id of group) results[id] = { id, outcome: 'skipped', reason: 'an earlier task in this phase did not pass' }
      continue
    }
    // Tasks in a group touch different files, so they build at the same time.
    // With worktree isolation, each task runs in its own git worktree.
    const worktrees = ISOLATION === 'worktree'
      ? await Promise.all(group.map(id => createWorktree(id)))
      : null
    const outcomes = (await parallel(group.map((id, i) => async () => {
      const wt = worktrees?.[i]
      const result = await runTask(phaseInfo.tasks[id])
      if (wt && result.outcome === 'approved') {
        try { git(['add', '--', ...result.files], wt) } catch {}
        try { git(['commit', '-m', `feat(${id}): wip`], wt) } catch {}
      }
      return result
    })))
      .map((o, i) => o ?? { id: group[i], outcome: 'failed', reason: 'task run crashed', question: 'Re-run the task.' })
    event('group.finished', { phase: phaseInfo.id, group, outcomes: outcomes.map(o => ({ id: o.id, outcome: o.outcome })), isolation: ISOLATION })
    // Merge worktrees onto the run branch in task order before the bookkeeper runs checks.
    if (ISOLATION === 'worktree') {
      for (const id of group) {
        try { mergeWorktree(id) } catch { /* worktree may have no changes */ }
      }
    }
    // Cut off (usage limit, crash, stop): commit nothing and record nothing. The work stays in the
    // tree and in the journal, and the next run resumes it.
    const cut = outcomes.filter(o => o.outcome === 'interrupted')
    if (cut.length) {
      for (const o of outcomes) results[o.id] = o
      stopped = `interrupted: ${cut.map(o => `${o.id} during ${o.step}`).join(', ')}; the next /sdd-run resumes from the checkpoint`
      log(stopped)
      break
    }

    let book = await bookkeep(outcomes, 'commit')
    // A refused command is not a failing check: never repair or stash for it.
    if (book && !book.checks_ran) {
      log(`Bookkeeper could not run the checks for ${group.join(', ')}; retrying once`)
      book = await bookkeep(outcomes, 'commit', null,
        `Your previous attempt could not run commands: ${book.failing_output}\nRun each check and each git or helper command as its own Bash call, with nothing around it.`)
      if (book && !book.checks_ran) {
        stopped = `bookkeeper could not run the checks: ${book.failing_output.slice(0, 500)}; the group's work is uncommitted in the tree`
        for (const o of outcomes) results[o.id] = o
        break
      }
    }
    // Partial work of blocked tasks must not leave the tree failing for the tasks after them.
    if (book && book.checks_ran && !book.checks_passed && !outcomes.some(o => o.outcome === 'approved')) {
      log(`Checks fail with the partial work of ${group.join(', ')}; stashing it`)
      book = await bookkeep(outcomes, 'park', book.failing_output)
      if (book && !book.checks_passed) { stopped = `checks still fail after stashing the partial work of ${group.join(', ')}: ${book.failing_output.slice(0, 500)}`; for (const o of outcomes) results[o.id] = o; break }
    }
    if (book && !book.checks_passed && outcomes.some(o => o.outcome === 'approved')) {
      log(`Checks failed after ${group.join(', ')}; trying one repair`)
      await repair(outcomes, book.failing_output)
      book = await bookkeep(outcomes, 'commit')
      if (book && book.checks_ran && !book.checks_passed) {
        book = await bookkeep(outcomes, 'discard', book.failing_output)
        for (const o of outcomes) if (o.outcome === 'approved') Object.assign(o, { outcome: 'blocked', tag: 'failed', reason: 'project checks failed after the group; work stashed' })
      }
      if (book && !book.checks_ran) {
        stopped = `bookkeeper could not run the checks after the repair: ${book.failing_output.slice(0, 500)}; the group's work is uncommitted in the tree`
        for (const o of outcomes) results[o.id] = o
        break
      }
    }
    if (!book) {
      for (const o of outcomes) results[o.id] = o
      stopped = `interrupted: the bookkeeper for ${group.join(', ')} was cut off; the work is uncommitted in the tree and the next /sdd-run resumes from the checkpoint`
      break
    }

    for (const o of outcomes) {
      results[o.id] = o
      if (o.outcome !== 'approved') { tainted = true; notPassed.add(o.id) }
    }
    log(`${group.join(', ')}: ${outcomes.map(o => `${o.id} ${o.outcome}`).join(', ')}`)
  }
  phaseState[phaseInfo.id] = stopped ? 'tainted' : tainted ? 'tainted' : 'complete'
}

phase('Report')
const all = Object.values(results)
const by = kind => all.filter(o => o.outcome === kind).map(o => o.id)
closeEvents()
event('run.finished', { run_id: A.run_id, stopped, approved: by('approved').length, blocked: by('blocked').length, skipped: by('skipped').length })
return {
  run_id: A.run_id,
  stopped: stopped,
  approved: by('approved'),
  blocked: all.filter(o => o.outcome === 'blocked' || o.outcome === 'failed').map(o => ({ id: o.id, tag: o.tag ?? 'failed', reason: o.reason, question: o.question })),
  skipped: all.filter(o => o.outcome === 'skipped').map(o => ({ id: o.id, reason: o.reason })),
  held: A.plan.held,
  interrupted: by('interrupted'),
  resumed: all.filter(o => o.resumed).map(o => o.id),
  phases: phaseState,
}
