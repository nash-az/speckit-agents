import type { EngineInterface, Register } from 'claude-code'
import { bashRefusal, isGitCommit, protectedPath, type RunMarker } from './rules.ts'

const MARKER = '.sdd/run.json'

// The guard is active only while /sdd-run has written .sdd/run.json.
async function activeRun($: EngineInterface): Promise<{ marker: RunMarker; root: string } | undefined> {
  const root = await $.session.root()
  let text: string
  try {
    text = await $.fs.read(`${root}/${MARKER}`)
  } catch {
    return undefined
  }
  const marker = JSON.parse(text) as RunMarker
  return { marker, root }
}

// The main loop owns the run: it may write .sdd/ (to end the run); subagents may not.
function forLoop(marker: RunMarker, agentId: string | undefined): RunMarker {
  return agentId ? marker : { ...marker, protected_paths: marker.protected_paths.filter(p => !p.startsWith('.sdd/')) }
}

async function currentBranch($: EngineInterface, root: string): Promise<string> {
  const { exitCode, stdout } = await $.process.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], { cwd: root })
  return exitCode === 0 ? stdout.trim() : ''
}

export const register: Register = on => {
  on('tool.call', { tool: ['Edit', 'Write', 'NotebookEdit'] }, async ($, e, next) => {
    const run = await activeRun($)
    if (!run) return next(e)
    const path = ('file_path' in e ? e.file_path : 'notebook_path' in e ? e.notebook_path : '') ?? ''
    const glob = protectedPath(forLoop(run.marker, e.agentId), run.root, path)
    return glob
      ? { deny: `${$.plugin.name}: ${path} matches protected path ${glob} during sdd run ${run.marker.run_id}.` }
      : next(e)
  }).catch(($, e, next) =>
    next.called ? next(e) : { deny: `${$.plugin.name}: the run guard failed; refusing to be safe.` },
  )

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const run = await activeRun($)
    if (!run) return next(e)
    const branch = isGitCommit(e.command) ? await currentBranch($, run.root) : ''
    const why = bashRefusal(forLoop(run.marker, e.agentId), e.command, branch, run.root)
    return why ? { deny: `${$.plugin.name}: ${why}.` } : next(e)
  }).catch(($, e, next) =>
    next.called ? next(e) : { deny: `${$.plugin.name}: the run guard failed; refusing to be safe.` },
  )
}
