// Pure rules for the run guard, kept apart from the hooks so tests can reach them.

export type RunMarker = {
  run_id: string
  branch: string
  protected_branches: string[]
  protected_paths: string[]
}

// A glob segment `*` matches within one path segment, `**` across segments.
export function globToRegExp(glob: string): RegExp {
  let out = ''
  for (let i = 0; i < glob.length; i++) {
    const c = glob.charAt(i)
    if (c === '*' && glob[i + 1] === '*') {
      out += '.*'
      i++
    } else if (c === '*') {
      out += '[^/]*'
    } else if ('\\^$+?.()|{}[]'.includes(c)) {
      out += `\\${c}`
    } else {
      out += c
    }
  }
  return new RegExp(`^${out}$`)
}

export function relativeTo(root: string, path: string): string {
  const base = root.endsWith('/') ? root : `${root}/`
  return path.startsWith(base) ? path.slice(base.length) : path.replace(/^\.\//, '')
}

export function protectedPath(marker: RunMarker, root: string, path: string): string | undefined {
  const rel = relativeTo(root, path)
  return marker.protected_paths.find(glob => globToRegExp(glob).test(rel))
}

// `git [-C dir] [-c k=v] [--flag] <subcommand> <rest>`: the subcommand is the first bare word.
const GIT = String.raw`\bgit\s+(?:(?:-[Cc]\s+\S+|--?[\w-]+(?:=\S+)?)\s+)*`
const REST = String.raw`[^;&|\n]*`
const git = (sub: string, rest = '') => new RegExp(`${GIT}(?:${sub})\\b${rest ? REST + rest : ''}`)

const DESTRUCTIVE_GIT: [RegExp, string][] = [
  [git('push'), 'pushing'],
  [git('reset', String.raw`--hard\b`), 'git reset --hard'],
  [git('rebase'), 'rebasing'],
  [git('commit', String.raw`--amend\b`), 'amending commits'],
  [git('branch', String.raw`\s-[a-zA-Z]*D\b`), 'deleting branches'],
  [git('clean', String.raw`\s-[a-zA-Z]*f`), 'git clean -f'],
  [git('checkout|switch'), 'switching branches'],
  [/--no-verify\b/, 'skipping git hooks'],
]
const GIT_COMMIT = git('commit')
const GIT_RESTORE = new RegExp(`${GIT}checkout\\s+--\\s`)

// Shell writes: redirection targets, and the file arguments of commands that write files.
const SEGMENT_SPLIT = /\|\||&&|[;|\n]/
const REDIRECT = /(?:^|[^<>&\d])\d*>>?\s*(?!&)([^\s;&|<>]+)/g
const WRITERS = new Set(['tee', 'rm', 'mv', 'cp', 'truncate', 'touch', 'dd', 'install'])

const unquote = (word: string) => word.replace(/^['"]|['"]$/g, '')

export function shellWriteTargets(command: string): string[] {
  const targets: string[] = []
  for (const segment of command.split(SEGMENT_SPLIT)) {
    for (const m of segment.matchAll(REDIRECT)) {
      const target = unquote(m[1] ?? '')
      if (target && target !== '/dev/null' && !target.startsWith('/dev/fd/')) targets.push(target)
    }
    const words = segment.trim().split(/\s+/).map(unquote).filter(w => !/^\w+=/.test(w))
    const [cmd = '', ...rest] = words
    const inPlace = (cmd === 'sed' || cmd === 'perl') && rest.some(w => /^-[a-zA-Z]*i/.test(w))
    if (WRITERS.has(cmd) || inPlace) {
      targets.push(...rest.filter(w => w && !w.startsWith('-') && !/^s[/|#]/.test(w) && !w.includes('>')))
    }
  }
  return targets
}

export const isGitCommit = (command: string) => GIT_COMMIT.test(command)

// Returns why a shell command is refused during a run, or undefined.
export function bashRefusal(marker: RunMarker, command: string, branch: string, root = ''): string | undefined {
  for (const [pattern, what] of DESTRUCTIVE_GIT) {
    // `git checkout -- <file>` restores a file; only branch moves are refused.
    if (what === 'switching branches' && GIT_RESTORE.test(command)) continue
    if (pattern.test(command)) return `${what} is not allowed during sdd run ${marker.run_id}`
  }
  if (GIT_COMMIT.test(command) && marker.protected_branches.includes(branch)) {
    return `committing on protected branch ${branch} is not allowed; the run works on ${marker.branch}`
  }
  for (const target of shellWriteTargets(command)) {
    const glob = protectedPath(marker, root, target)
    if (glob) return `${glob} is protected during sdd run ${marker.run_id}`
  }
  return undefined
}
